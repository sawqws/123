"""Зеркало horodigital.ru: сайт HDP в браузере один в один как настоящая платформа.

Сервер бота отдаёт настоящие страницы платформы (/student/…, /api/<сервис>/v1/…, /store/…, /realtime) со своей
сессией (horo/tmp/state.json — та же, что у horo/*.js), поэтому вид совпадает до пикселя и сам обновляется вместе
с платформой. Поверх в HTML вставляются кнопки бота: /_hdp/overlay.js и overlay.css (bot/site/mirror/).

Что меняется по дороге:
- в браузер не уходит настоящая cookie сессии платформы (вход в зеркало — своя cookie hdp_site, см. web.py);
  Set-Cookie платформы оседает здесь, в памяти (_jar);
- из HTML убирается Яндекс-метрика, guard.js глушит её и Sentry (иначе платформа видела бы чужой адрес);
- абсолютные ссылки https://horodigital.ru в HTML/JSON/CSS становятся относительными (картинки, файлы — через зеркало);
- сессия платформы истекла (401) — бот входит заново (horo/session.js) и повторяет запрос один раз.
"""

import gzip
import http.client
import json
import os
import re
import select
import socket
import ssl
import subprocess
import threading
import time
import urllib.parse
import zlib

UP = "horodigital.ru"
BASE = "https://" + UP
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
STATE = os.path.join(REPO, "horo", "tmp", "state.json")
FILES = os.path.join(HERE, "site", "mirror")
TIMEOUT = 60

# заголовки, которые не пересылаем (соединение, адреса прокси, наши cookie и метки)
HOP = {"connection", "keep-alive", "proxy-connection", "transfer-encoding", "te", "trailer", "upgrade",
       "proxy-authorization", "proxy-authenticate", "host", "cookie", "content-length", "accept-encoding",
       "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "x-real-ip", "forwarded", "via",
       "cf-connecting-ip", "x-site", "x-init-data"}
DROP_RESP = {"connection", "keep-alive", "transfer-encoding", "content-length", "set-cookie", "content-encoding",
             "strict-transport-security", "content-security-policy", "content-security-policy-report-only",
             "alt-svc", "report-to", "nel", "date", "server"}
TEXT = ("text/html", "application/json", "text/css", "application/problem+json")
METRIKA = re.compile(r"<!--\s*Yandex\.Metrika.*?/Yandex\.Metrika counter\s*-->|<noscript><div><img[^>]*mc\.yandex[^>]*>"
                     r"</div></noscript>", re.S | re.I)
ABS = re.compile(r"https?:(?:\\?/){2}horodigital\.ru(?=[/\"'\\?#]|$)")


# ---------- сессия платформы ----------

class Jar:
    """Cookie платформы: из state.json (его пишут horo/*.js после входа) + что платформа прислала через зеркало."""

    def __init__(self, path=STATE):
        self.path, self.mtime, self.c, self.lock = path, None, {}, threading.Lock()

    def _load(self):
        try:
            m = os.path.getmtime(self.path)
        except OSError:
            return
        if m == self.mtime:
            return
        try:
            with open(self.path) as f:
                data = json.load(f)
        except (OSError, ValueError):
            return  # пишется прямо сейчас — прочтём в следующий раз
        self.mtime = m
        now = time.time()
        for c in data.get("cookies", []):
            if c.get("domain", "").lstrip(".") == UP and (c.get("expires", -1) in (-1, None) or c["expires"] > now):
                self.c[c["name"]] = c["value"]

    def header(self):
        with self.lock:
            self._load()
            return "; ".join(f"{k}={v}" for k, v in self.c.items())

    def update(self, set_cookies):
        with self.lock:
            self._load()  # сначала файл — иначе первое чтение затрёт то, что прислала платформа
            for sc in set_cookies:
                name, _, rest = sc.partition("=")
                value = rest.split(";", 1)[0]
                attrs = rest.lower()
                gone = "max-age=0" in attrs or "expires=thu, 01 jan 1970" in attrs or not value
                if gone:
                    self.c.pop(name.strip(), None)
                else:
                    self.c[name.strip()] = value

    def has_session(self):
        with self.lock:
            self._load()
            return any("session" in k for k in self.c)


_jar = Jar()
_relogin = threading.Lock()
_last_login = [0.0]


def relogin():
    """Войти на платформу заново (node horo/session.js пишет state.json). Не чаще раза в минуту."""
    with _relogin:
        if time.time() - _last_login[0] < 60:
            return False
        _last_login[0] = time.time()
        try:
            r = subprocess.run(["node", os.path.join(REPO, "horo", "session.js")], cwd=REPO, capture_output=True,
                               text=True, timeout=180)
            return r.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False


# ---------- соединение с платформой ----------

def _proxy():
    """HTTPS_PROXY (облачная среда для тестов); на сервере его нет — идём напрямую."""
    p = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or ""
    u = urllib.parse.urlparse(p) if p else None
    return (u.hostname, u.port or 80) if u and u.hostname else None


def connect():
    ctx = ssl.create_default_context()
    px = _proxy()
    if px:
        c = http.client.HTTPSConnection(px[0], px[1], timeout=TIMEOUT, context=ctx)
        c.set_tunnel(UP, 443)
    else:
        c = http.client.HTTPSConnection(UP, 443, timeout=TIMEOUT, context=ctx)
    return c


class Pool:
    """Открытые соединения с платформой (TLS-рукопожатие дорогое). Простоявшие дольше IDLE — закрываем."""
    IDLE = 15

    def __init__(self, size=8):
        self.size, self.free, self.lock = size, [], threading.Lock()

    def get(self):
        now = time.time()
        with self.lock:
            while self.free:
                c, at = self.free.pop()
                if now - at < self.IDLE:
                    return c
                c.close()
        return connect()

    def put(self, c):
        with self.lock:
            if len(self.free) < self.size:
                self.free.append((c, time.time()))
                return
        c.close()


_pool = Pool()


def up_headers(h, host):
    """Заголовки запроса к платформе: как у браузера Глеба, но с сессией бота и адресом платформы."""
    out = {}
    for k, v in h.headers.items():
        lk = k.lower()
        if lk in HOP or lk.startswith(("x-forwarded", "cf-", "sec-websocket")):
            continue
        if lk in ("origin", "referer"):
            v = re.sub(r"^https?://[^/]+", BASE, v)
        out[k] = v
    out["Host"] = UP
    out["Accept-Encoding"] = "gzip"
    ck = _jar.header()
    if ck:
        out["Cookie"] = ck
    return out


def read_body(h):
    """Тело запроса браузера (обычное или chunked)."""
    if (h.headers.get("Transfer-Encoding") or "").lower() == "chunked":
        parts = []
        while True:
            size = int(h.rfile.readline().split(b";")[0].strip() or b"0", 16)
            if not size:
                h.rfile.readline()
                break
            parts.append(h.rfile.read(size))
            h.rfile.readline()
        return b"".join(parts)
    n = int(h.headers.get("Content-Length") or 0)
    return h.rfile.read(n) if n > 0 else None


def decode(data, enc):
    enc = (enc or "").lower()
    if enc == "gzip":
        return gzip.decompress(data)
    if enc == "deflate":
        try:
            return zlib.decompress(data)
        except zlib.error:
            return zlib.decompress(data, -zlib.MAX_WBITS)
    return data


PLAIN = "hdp_plain"  # cookie «⏻ выключить всё»: страница — чистая платформа, без кнопок бота (guard.js остаётся)


def plain(h):
    return any(p.strip() == f"{PLAIN}=1" for p in (h.headers.get("Cookie") or "").split(";"))


def inject(html, version="0", bare=False):
    """HTML страницы: без метрики, с guard.js первым и кнопками бота (bare — без кнопок: «⏻ выключить всё»)."""
    html = METRIKA.sub("", html)
    head = (f'<script src="/_hdp/guard.js?v={version}"></script>')
    if bare:
        return re.sub(r"<head[^>]*>", lambda m: m.group(0) + head, html, count=1, flags=re.I)
    tail = (f'<link rel="stylesheet" href="/_hdp/overlay.css?v={version}">'
            f'<script defer src="/_hdp/overlay.js?v={version}"></script>')
    html = re.sub(r"<head[^>]*>", lambda m: m.group(0) + head, html, count=1, flags=re.I)
    if "</head>" in html:
        html = html.replace("</head>", tail + "</head>", 1)
    else:
        html += tail
    return html


def rewrite(text):
    """Абсолютные адреса платформы → относительные (в JSON бывает с экранированными «\\/»)."""
    return ABS.sub("", text)


def request(method, path, headers, body):
    """Запрос к платформе с повтором на новом соединении, если старое закрылось. → (conn, resp)."""
    for attempt in range(2):
        c = _pool.get() if attempt == 0 else connect()  # второй раз — точно новое соединение
        try:
            c.request(method, path, body=body, headers=headers)
            return c, c.getresponse()
        except (http.client.HTTPException, OSError):
            c.close()
            if attempt:
                raise
    raise OSError("нет связи с платформой")


def proxy(h, version="0"):
    """Переслать запрос браузера на платформу и отдать ответ (h — BaseHTTPRequestHandler)."""
    if (h.headers.get("Upgrade") or "").lower() == "websocket":
        return websocket(h)
    method = h.command
    body = read_body(h) if method not in ("GET", "HEAD") else None
    path = h.path if h.path.startswith("/") else "/"
    for attempt in range(2):
        headers = up_headers(h, UP)
        try:
            conn, resp = request(method, path, headers, body)
        except (http.client.HTTPException, OSError) as e:
            return h.reply(502, {"error": f"Платформа не отвечает: {e}"})
        if resp.status == 401 and attempt == 0 and path.startswith("/api/") and method in ("GET", "HEAD"):
            resp.read()
            _pool.put(conn)
            if relogin():
                continue
        break
    _jar.update(resp.headers.get_all("Set-Cookie") or [])
    ctype = (resp.headers.get("Content-Type") or "").lower()
    out = [(k, v) for k, v in resp.headers.items() if k.lower() not in DROP_RESP]
    out = [(k, rewrite(v) if k.lower() == "location" else v) for k, v in out]
    if method == "HEAD" or resp.status in (204, 304):
        resp.read()
        _pool.put(conn)
        return send(h, resp.status, resp.reason, out, b"")
    if ctype.startswith(TEXT):
        data = resp.read()
        _pool.put(conn)
        data = decode(data, resp.headers.get("Content-Encoding"))
        charset = re.search(r"charset=([\w-]+)", ctype)
        text = data.decode(charset.group(1) if charset else "utf-8", "replace")
        text = rewrite(text)
        if ctype.startswith("text/html"):
            text = inject(text, version, plain(h))
            out = [(k, v) for k, v in out if k.lower() not in ("etag", "last-modified")]
            out.append(("Cache-Control", "no-store"))
            out = dedupe(out)
        return send(h, resp.status, resp.reason, out, text.encode("utf-8"))
    # остальное (скрипты, шрифты, картинки, файлы) — как есть, по кускам
    enc = resp.headers.get("Content-Encoding")
    if enc:
        out.append(("Content-Encoding", enc))
    length = resp.headers.get("Content-Length")
    if length and not resp.headers.get("Transfer-Encoding"):
        out.append(("Content-Length", length))
    h.send_response(resp.status, resp.reason)
    for k, v in out:
        h.send_header(k, v)
    h.end_headers()
    ok = True
    try:
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            h.wfile.write(chunk)
    except (BrokenPipeError, ConnectionResetError, OSError):
        ok = False  # браузер ушёл — соединение с платформой недочитано, не переиспользуем
    if ok and resp.isclosed():
        _pool.put(conn)
    else:
        conn.close()
    h.close_connection = True


def dedupe(headers):
    seen, out = set(), []
    for k, v in reversed(headers):
        if k.lower() == "cache-control":
            if "cache-control" in seen:
                continue
            seen.add("cache-control")
        out.append((k, v))
    return list(reversed(out))


def send(h, status, reason, headers, body):
    h.send_response(status, reason)
    for k, v in headers:
        h.send_header(k, v)
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    try:
        if body:
            h.wfile.write(body)
    except (BrokenPipeError, ConnectionResetError):
        pass


# ---------- websocket (/realtime — живые обновления платформы) ----------

def _raw_socket():
    ctx = ssl.create_default_context()
    px = _proxy()
    if px:
        s = socket.create_connection(px, timeout=TIMEOUT)
        s.sendall(f"CONNECT {UP}:443 HTTP/1.1\r\nHost: {UP}:443\r\n\r\n".encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            part = s.recv(4096)
            if not part:
                raise OSError("прокси закрыл соединение")
            buf += part
        if b" 200" not in buf.split(b"\r\n", 1)[0]:
            raise OSError("прокси не пустил: " + buf.split(b"\r\n", 1)[0].decode(errors="replace"))
    else:
        s = socket.create_connection((UP, 443), timeout=TIMEOUT)
    return ctx.wrap_socket(s, server_hostname=UP)


def websocket(h):
    """Прозрачный туннель websocket: заголовки — как у обычного запроса, дальше байты в обе стороны."""
    headers = up_headers(h, UP)
    headers.pop("Accept-Encoding", None)
    for k, v in h.headers.items():
        if k.lower().startswith("sec-websocket"):
            headers[k] = v
    headers["Upgrade"] = "websocket"
    headers["Connection"] = "Upgrade"
    try:
        up = _raw_socket()
        req = f"GET {h.path} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in headers.items()) + "\r\n"
        up.sendall(req.encode())
    except OSError as e:
        return h.reply(502, {"error": f"Платформа не отвечает: {e}"})
    h.close_connection = True
    client = h.connection
    h.wfile.flush()
    pipe(client, up)


def pipe(a, b):
    a.settimeout(None)
    b.settimeout(None)
    try:
        while True:
            # у TLS-сокета данные могут лежать в буфере ssl — select их не видит
            ready = [b] if isinstance(b, ssl.SSLSocket) and b.pending() else select.select([a, b], [], [], 300)[0]
            if not ready:
                return  # 5 минут тишины — закрываем
            for s in ready:
                data = s.recv(65536)
                if not data:
                    return
                (b if s is a else a).sendall(data)
    except OSError:
        return
    finally:
        try:
            b.close()
        except OSError:
            pass


def file(name):
    """Файл из bot/site/mirror/ (только по имени, без подпапок) → (bytes, ctype) или None."""
    import mimetypes
    name = os.path.basename(name)
    path = os.path.join(FILES, name)
    if not name or not os.path.isfile(path):
        return None
    with open(path, "rb") as f:
        data = f.read()
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    if ctype.startswith("text/") or ctype.endswith("javascript"):
        ctype += "; charset=utf-8"
    return data, ctype

