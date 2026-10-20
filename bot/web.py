"""Приложение бота внутри Telegram (Mini App): веб-сервер на 127.0.0.1 и https-адрес через cloudflared.

Что отдавать и что запускать, решает bot.py: он передаёт в start() обработчики вида
{"/api/…": функция(тело запроса) -> dict}. Здесь только сервер, проверка подписи Telegram и туннель.
Пускает только владельца бота (по подписанным данным Telegram, их не подделать без токена).

Сайт в браузере (/site/, bot/site/): то же API, вход без Telegram-браузера — прямо на странице входа в Safari:
«📲 Войти через Telegram» (бот пишет «✅ Войти», страница ждёт и получает cookie) или код из 6 цифр из /site;
ещё — одноразовая ссылка из /site. Код и ссылка живут 15 минут и срабатывают один раз, дальше браузер помнит
вход полгода (cookie hdp_site).
Постоянный адрес — https://hdp.<ip-через-дефисы>.sslip.io (его держит Caddy планера), иначе адрес туннеля.
На постоянном адресе сайт — зеркало настоящего horodigital.ru с кнопками бота (bot/mirror.py): всё, что не API бота,
не /site/ и не /_hdp/, после входа уходит на платформу. Прежний сайт (bot/site/) остаётся по адресу /site/.

Переменные окружения:
  HORO_WEB_PORT  порт на 127.0.0.1 (по умолчанию 8788; у планера 8787)
  HORO_WEB_URL   свой https-адрес; если пусто — бот поднимает туннель cloudflared (адрес …trycloudflare.com)
  HORO_SITE_URL  адрес сайта; если пусто — https://hdp.<ip>.sslip.io, когда он отвечает, иначе адрес приложения
"""
import hashlib
import hmac
import json
import mimetypes
import os
import platform
import secrets
import re
import shutil
import subprocess
import threading
import time
import traceback
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
import mirror  # noqa: E402  (рядом, в bot/)
WEB = os.path.join(HERE, "webapp")
SITE = os.path.join(HERE, "site")
SESSIONS = os.path.join(os.path.dirname(HERE), "horo", "tmp", "site_sessions.json")
COOKIE = "hdp_site"
CODE_TTL = 15 * 60          # одноразовая ссылка входа
SESSION_TTL = 180 * 86400   # сколько браузер помнит вход (полгода)
PORT = int(os.environ.get("HORO_WEB_PORT") or 8788)
BIN = os.path.join(os.path.dirname(HERE), "horo", "tmp", "bin", "cloudflared")  # если cloudflared не установлен

# маршруты, которые что-то делают (решают, сдают, меняют настройки): GET на них не принимаем
WRITE = {"/api/solve", "/api/auto", "/api/human", "/api/mode", "/api/support", "/api/check", "/api/alphabet",
         "/api/draft/send", "/api/draft/drop", "/api/draft/save", "/api/prep/run",
         "/api/site/send", "/api/site/hold", "/api/site/settings/set", "/api/site/prep", "/api/site/check"}

url = os.environ.get("HORO_WEB_URL", "").rstrip("/")  # текущий https-адрес приложения
_cfg = {}


def check_init_data(init_data, token, max_age=2 * 86400):
    """Проверка подписи данных Mini App (core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app).
    Возвращает пользователя (dict) или None."""
    try:
        pairs = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None
    got = pairs.pop("hash", "")
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()

    def sign(d):
        check = "\n".join(f"{k}={v}" for k, v in sorted(d.items()))
        return hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    # поле signature по документации входит в строку проверки; на всякий случай принимаем и без него
    variants = [pairs] + ([{k: v for k, v in pairs.items() if k != "signature"}] if "signature" in pairs else [])
    if not got or not any(hmac.compare_digest(sign(d), got) for d in variants):
        return None
    try:
        if time.time() - int(pairs.get("auth_date", 0)) > max_age:
            return None
        return json.loads(pairs.get("user", "null"))
    except ValueError:
        return None


def version(folder=WEB):
    """Метка файлов приложения: меняется при обновлении, чтобы Telegram не показывал старые из кэша."""
    h = hashlib.sha1()
    for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        if not os.path.isfile(os.path.join(folder, name)):
            continue  # подпапки (bot/site/mirror) — у них своя метка
        with open(os.path.join(folder, name), "rb") as f:
            h.update(f.read())
    return h.hexdigest()[:10]


# ---------- вход на сайт без Telegram ----------
# Храним только sha256 от кодов и сессий: файл не даёт войти, даже если его прочитать.
_slock = threading.Lock()
_digest = lambda x: hashlib.sha256(x.encode()).hexdigest()


def _sessions():
    try:
        with open(SESSIONS) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    now = time.time()
    return {k: v for k, v in data.items() if isinstance(v, dict) and v.get("exp", 0) > now}


def _save_sessions(data):
    os.makedirs(os.path.dirname(SESSIONS), exist_ok=True)
    tmp = SESSIONS + ".part"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(data, f)
    os.replace(tmp, SESSIONS)


def site_code(pin=False):
    """Новый одноразовый код входа: длинный — для ссылки из бота, pin=True — 6 цифр, чтобы ввести на сайте."""
    code = f"{secrets.randbelow(10 ** 6):06d}" if pin else secrets.token_urlsafe(24)
    with _slock:
        data = _sessions()
        data[_digest(code)] = {"kind": "code", "exp": time.time() + CODE_TTL}
        _save_sessions(data)
    return code


_fails = []  # время неверных кодов: 10 за 10 минут — вход по коду закрыт на 10 минут (6 цифр не перебрать)


def site_locked(now=None):
    now = now or time.time()
    with _slock:
        _fails[:] = [t for t in _fails if now - t < 600]
        return len(_fails) >= 10


def site_login(code):
    """Код (ссылки или 6 цифр) -> токен сессии (код сгорает). None — код неверный, просрочен или уже использован."""
    code = re.sub(r"\s", "", str(code or ""))[:64]
    if site_locked():
        return None
    with _slock:
        data = _sessions()
        rec = data.pop(_digest(code), None)
        if not rec or rec.get("kind") != "code":
            _fails.append(time.time())
            return None
        token = secrets.token_urlsafe(32)
        data[_digest(token)] = {"kind": "session", "exp": time.time() + SESSION_TTL, "at": int(time.time())}
        _save_sessions(data)
    return token


# Вход одним нажатием: страница входа просит вход (POST /site/api/login/request) → бот пишет Глебу «✅ Войти» →
# он жмёт → страница, которая ждёт (POST /site/api/login/poll {req}), получает cookie. req знает только эта страница.
_reqs = {}  # req -> {"id": короткий id для кнопки, "exp", "ok", "ua"}
_req_times = []  # не чаще 5 просьб за 10 минут (чужой не засыплет чат)


def site_ua(ua):
    ua = ua or ""
    dev = next((n for k, n in (("iPad", "iPad"), ("iPhone", "iPhone"), ("Android", "Android"), ("Mac OS", "Mac"),
                               ("Windows", "Windows")) if k in ua), "Устройство")
    app = "приложение с экрана «Домой»" if "Safari" not in ua and dev in ("iPad", "iPhone") else "браузер"
    return f"{dev} · {app}"


def site_request(ua="", now=None):
    """Новая просьба войти: бот пишет Глебу карточку с «✅ Войти» и кодом. -> req или PermissionError."""
    now = now or time.time()
    with _slock:
        _req_times[:] = [t for t in _req_times if now - t < 600]
        if len(_req_times) >= 5:
            raise PermissionError("Слишком часто — подожди несколько минут или введи код из /site")
        _req_times.append(now)
        for k in [k for k, v in _reqs.items() if v["exp"] < now]:
            _reqs.pop(k, None)
        req, short = secrets.token_urlsafe(24), secrets.token_hex(4)
        _reqs[req] = {"id": short, "exp": now + CODE_TTL, "ok": False, "ua": site_ua(ua)}
    _cfg["login_ask"](short, _reqs[req]["ua"], site_code(pin=True))
    return req


def site_approve(short, now=None):
    """Глеб нажал «✅ Войти» -> устройство (строкой) или None, если просьба устарела."""
    with _slock:
        for v in _reqs.values():
            if v["id"] == short and v["exp"] > (now or time.time()):
                v["ok"] = True
                return v["ua"]
    return None


def site_poll(req, now=None):
    """-> токен сессии (вход подтверждён, один раз), None — ещё ждём, ValueError — просьбы нет или устарела."""
    now = now or time.time()
    with _slock:
        v = _reqs.get(str(req or ""))
        if not v or v["exp"] < now:
            raise ValueError("Время вышло — нажми «Войти через Telegram» ещё раз")
        if not v["ok"]:
            return None
        _reqs.pop(req, None)
        token = secrets.token_urlsafe(32)
        data = _sessions()
        data[_digest(token)] = {"kind": "session", "exp": now + SESSION_TTL, "at": int(now)}
        _save_sessions(data)
    return token


def site_check(token):
    if not token:
        return False
    rec = _sessions().get(_digest(token))
    return bool(rec and rec.get("kind") == "session")


def site_logout(token):
    with _slock:
        data = _sessions()
        if data.pop(_digest(token or ""), None):
            _save_sessions(data)


def site_url():
    """Адрес сайта для ссылки входа (без / в конце) или пусто, если ещё не готов."""
    return os.environ.get("HORO_SITE_URL", "").rstrip("/") or _cfg.get("site") or url


def site_link():
    base = site_url()
    return f"{base}/site/login?c={site_code()}" if base else ""


def server_ip():
    for src in ("https://api.ipify.org", "https://ipv4.icanhazip.com", "https://ifconfig.me/ip"):
        try:
            with urllib.request.urlopen(src, timeout=15) as r:
                ip = r.read().decode().strip()
            if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", ip):
                return ip
        except Exception:
            pass
    return None


def watch_site():
    """Постоянный адрес сайта hdp.<ip>.sslip.io: им пользуемся, как только Caddy планера на него отвечает."""
    if os.environ.get("HORO_SITE_URL"):
        return
    ip = None
    while True:
        ip = ip or server_ip()
        if ip:
            base = "https://hdp." + ip.replace(".", "-") + ".sslip.io"
            _cfg["site"] = base if healthy(base) else ""
        time.sleep(600 if _cfg.get("site") else 120)


class Web(BaseHTTPRequestHandler):
    server_version = "HDP/1"
    timeout = 60  # медленный или оборванный клиент не держит поток вечно

    def log_message(self, fmt, *args):  # без шума в логах pm2
        pass

    def reply(self, code, body, ctype="application/json; charset=utf-8", cache="no-store", cookie=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")  # код входа из ссылки не уходит в чужие запросы
        if cookie:
            self.send_header("Set-Cookie", f"{COOKIE}={cookie}; Path=/; Max-Age={SESSION_TTL}; HttpOnly; Secure; SameSite=Lax")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):  # клиент закрыл страницу, не дождавшись ответа
            pass

    def cookie(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE:
                return v
        return ""

    def redirect(self, to, cookie=None):
        self.send_response(302)
        self.send_header("Location", to)
        self.send_header("Cache-Control", "no-store")
        if cookie is not None:
            age = SESSION_TTL if cookie else 0
            self.send_header("Set-Cookie", f"{COOKIE}={cookie}; Path=/; Max-Age={age}; HttpOnly; Secure; SameSite=Lax")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def mirror_host(self):
        """Постоянный адрес сайта (hdp.… или HORO_SITE_URL) — там зеркало платформы; на адресе туннеля — приложение."""
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        own = urllib.parse.urlparse(os.environ.get("HORO_SITE_URL", "")).hostname or ""
        return host.startswith("hdp.") or (own and host == own.lower())

    def mirror(self, path):
        """Зеркало платформы: свои файлы /_hdp/, остальное — на horodigital.ru (только после входа)."""
        if path in ("/_hdp/on", "/_hdp/off"):  # «⏻ выключить всё» / включить кнопки бота обратно
            to = (urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("to") or ["/student"])[0]
            to = to if to.startswith("/") and not to.startswith(("//", "/\\")) else "/student"  # только свой сайт
            age = SESSION_TTL if path.endswith("off") else 0
            self.send_response(302)
            self.send_header("Location", to)
            self.send_header("Cache-Control", "no-store")
            # не HttpOnly: guard.js по ней понимает, что кнопки выключены, и рисует ⏻ «включить»
            self.send_header("Set-Cookie", f"{mirror.PLAIN}=1; Path=/; Max-Age={age}; Secure; SameSite=Lax")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/_hdp/sw.js":  # service worker зеркала (bundle задания): область — весь сайт, без долгого кэша
            got = mirror.file("sw.js")
            self.send_response(200)
            self.send_header("Content-Type", got[1])
            self.send_header("Service-Worker-Allowed", "/")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(got[0])))
            self.end_headers()
            self.wfile.write(got[0])
            return
        if path == "/_hdp/bundle":  # sw.js: цепочка данных задания одним ответом (mirror.Ahead.bundle)
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            t, x = (q.get("t") or [""])[0], (q.get("x") or [""])[0]
            if not (site_check(self.cookie()) and re.fullmatch(r"[\w-]{8,64}", t) and re.fullmatch(r"[\w-]{8,64}", x)):
                return self.reply(403, {})
            body = json.dumps(mirror._ahead.bundle(t, x, first_too=q.get("first") == ["1"]), ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/_hdp/ahead":  # палец коснулся карточки задания — сервер берёт её данные заранее (mirror.Ahead)
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            t, x = (q.get("t") or [""])[0], (q.get("x") or [""])[0]
            if site_check(self.cookie()) and re.fullmatch(r"[\w-]{8,64}", t) and re.fullmatch(r"[\w-]{8,64}", x):
                mirror._ahead.want([f"{mirror.EDU}/topics/{t}/tasks?taskUuid={x}"])
            self.send_response(204)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        if path.startswith("/_hdp/"):
            got = mirror.file(path[len("/_hdp/"):])
            if not got:
                return self.reply(404, {"error": "not found"})
            return self.reply(200, got[0], got[1], "public, max-age=31536000, immutable")
        if not site_check(self.cookie()):
            if self.command == "GET" and "text/html" in (self.headers.get("Accept") or ""):
                return self.redirect("/site/")  # там карточка «напиши боту /site»
            return self.reply(401, {"error": "Нужен вход: напиши боту /site — пришлю ссылку"})
        origin = self.headers.get("Origin")
        if self.command not in ("GET", "HEAD", "OPTIONS") and origin and \
                urllib.parse.urlparse(origin).hostname != (self.headers.get("Host") or "").split(":")[0]:
            return self.reply(403, {"error": "Чужой сайт"})  # запросы с других страниц не пускаем
        if self.command == "POST" and re.fullmatch(r"/api/auth/v\d+/logout", path):
            # «Выйти» на платформе: из зеркала выходим, а сессию бота на платформе не трогаем (иначе бот разлогинен)
            n = int(self.headers.get("Content-Length") or 0)
            if n > 0:
                self.rfile.read(min(n, 65536))
            site_logout(self.cookie())
            body = json.dumps({"success": True, "data": None}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Set-Cookie", f"{COOKIE}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax")
            self.end_headers()
            self.wfile.write(body)
            return
        try:
            mirror.proxy(self, _cfg.get("mirror_version", "0"))
        except Exception as e:
            traceback.print_exc()
            self.reply(502, {"error": f"Зеркало: {e}"})

    def site(self, path):
        """Сайт в браузере: /site/ и его файлы, вход по одноразовой ссылке, выход."""
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if path == "/site/login":
            token = site_login((q.get("c") or [""])[0])
            home = "/student" if self.mirror_host() else "/site/"
            return self.redirect(home if token else "/site/#login-expired", token or None)
        if path == "/site/logout":
            site_logout(self.cookie())
            return self.redirect("/site/", "")
        if path in ("/site", "/site/", "/site/index.html"):
            if self.mirror_host() and site_check(self.cookie()):
                return self.redirect("/student")  # вошёл — сразу зеркало платформы, а не прежний самодельный сайт
            with open(os.path.join(SITE, "index.html"), encoding="utf-8") as f:
                page = f.read().replace("{{v}}", _cfg.get("site_version", "0"))
            return self.reply(200, page, "text/html; charset=utf-8")
        return self.static(SITE, path)

    def static(self, folder, path):
        name = os.path.basename(path)  # только файлы из папки, без подпапок
        file = os.path.join(folder, name)
        if name and name != "index.html" and os.path.isfile(file):
            with open(file, "rb") as f:
                ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
                if ctype.startswith("text/") or ctype.endswith("javascript"):
                    ctype += "; charset=utf-8"
                return self.reply(200, f.read(), ctype, "public, max-age=31536000, immutable")
        self.reply(404, {"error": "not found"})

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/health":
            return self.reply(200, "ok", "text/plain")
        if self.mirror_host() and path not in _cfg["routes"] and not (path == "/site" or path.startswith("/site/")):
            return self.redirect("/student") if path == "/" else self.mirror(path)
        if path.startswith("/api/"):
            if path in WRITE:  # действия — только POST (GET могут повторить предпросмотр ссылок, расширения и т. п.)
                return self.reply(405, {"error": "Это действие — только POST"})
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            return self.api(path, {k: v[-1] for k, v in q.items()})  # ?lvl=2 → {"lvl": "2"}
        if path == "/site" or path.startswith("/site/"):
            return self.site(path)
        if path == "/" and (self.headers.get("Host") or "").startswith("hdp."):
            return self.redirect("/site/")  # постоянный адрес — это сайт, мини-приложение живёт по адресу туннеля
        if path in ("/", "/index.html"):
            with open(os.path.join(WEB, "index.html"), encoding="utf-8") as f:
                page = f.read().replace("{{v}}", _cfg.get("version", "0"))
            return self.reply(200, page, "text/html; charset=utf-8")
        self.static(WEB, path)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ("/site/api/login/code", "/site/api/login/request", "/site/api/login/poll"):
            return self.login(path)
        if self.mirror_host() and path not in _cfg["routes"]:
            return self.mirror(path)
        try:
            n = max(0, int(self.headers.get("Content-Length") or 0))  # -1 — не ждать тело до закрытия связи
            limit = 16_000_000 if path == "/api/support" else 1_000_000  # в поддержке — скриншот
            body = json.loads(self.rfile.read(min(n, limit)) or b"{}") if n else {}
        except ValueError:
            return self.reply(400, {"error": "bad json"})
        self.api(path, body if isinstance(body, dict) else {})

    def login(self, path):
        """Вход на странице входа (без cookie): код из 6 цифр, или «Войти через Telegram» — просьба и ожидание."""
        origin = self.headers.get("Origin")
        if origin and urllib.parse.urlparse(origin).hostname != (self.headers.get("Host") or "").split(":")[0]:
            return self.reply(403, {"error": "Чужой сайт"})
        try:
            n = max(0, int(self.headers.get("Content-Length") or 0))
            body = json.loads(self.rfile.read(min(n, 2000)) or b"{}") if n else {}
            body = body if isinstance(body, dict) else {}
        except ValueError:
            return self.reply(400, {"error": "bad json"})
        try:
            if path.endswith("/code"):
                token = site_login(body.get("code"))
                if not token:
                    return self.reply(429 if site_locked() else 400,
                                      {"error": "Много неверных кодов — подожди 10 минут" if site_locked()
                                       else "Код неверный или устарел — напиши боту /site"})
            elif path.endswith("/request"):
                return self.reply(200, {"req": site_request(self.headers.get("User-Agent"))})
            else:
                token = site_poll(body.get("req"))
                if not token:
                    return self.reply(200, {"wait": True})
        except PermissionError as e:
            return self.reply(429, {"error": str(e)})
        except ValueError as e:
            return self.reply(410, {"error": str(e)})
        except Exception:
            traceback.print_exc()
            return self.reply(502, {"error": "Не смог написать в Telegram — введи код из /site"})
        return self.reply(200, {"ok": True, "home": "/student" if self.mirror_host() else "/site/"}, cookie=token)

    def other(self):
        """PUT/PATCH/DELETE/HEAD/OPTIONS — только зеркалу (у API бота их нет)."""
        path = urllib.parse.urlparse(self.path).path
        if self.mirror_host() and path not in _cfg["routes"]:
            return self.mirror(path)
        self.reply(405, {"error": "method not allowed"})

    do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = other

    def api(self, path, body):
        user = check_init_data(self.headers.get("X-Init-Data") or "", _cfg["token"])
        # сайт: вход по cookie. Свой заголовок X-Site браузер не пошлёт с чужого сайта без разрешения (CORS),
        # поэтому чужая страница не сможет нажать «Сделать» от имени Глеба
        if not user and self.headers.get("X-Site") == "1" and site_check(self.cookie()):
            user = {"id": _cfg["owner"]()}
        if not user:
            return self.reply(401, {"error": "Открой приложение через Telegram" if not self.headers.get("X-Site")
                                    else "Нужен вход: напиши боту /site — пришлю ссылку"})
        if user.get("id") != _cfg["owner"]():
            return self.reply(403, {"error": "Это личный бот"})
        fn = _cfg["routes"].get(path)
        if not fn:
            return self.reply(404, {"error": "нет такого метода"})
        if path in WRITE:
            mirror._swr.clear()  # бот что-то меняет на платформе — зеркало берёт свежее
        try:
            self.reply(200, fn(body))
        except ValueError as e:  # понятная ошибка для человека
            self.reply(400, {"error": str(e)})
        except PermissionError as e:
            self.reply(403, {"error": str(e)})
        except Exception as e:
            traceback.print_exc()
            self.reply(500, {"error": f"Ошибка сервера: {e}"})


def serve():
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Web)
    httpd.daemon_threads = True
    print(f"Приложение: http://127.0.0.1:{PORT}")
    httpd.serve_forever()


# ---------- туннель: https-адрес без домена ----------

def cloudflared():
    """Путь к cloudflared. Если его нет, скачивает в horo/tmp/bin (один файл с GitHub Cloudflare)."""
    path = shutil.which("cloudflared") or (BIN if os.access(BIN, os.X_OK) else None)
    if path:
        return path
    arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine().lower())
    if not arch or platform.system() != "Linux":
        return None
    try:
        os.makedirs(os.path.dirname(BIN), exist_ok=True)
        src = f"https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-{arch}"
        with urllib.request.urlopen(src, timeout=300) as r, open(BIN + ".part", "wb") as f:
            shutil.copyfileobj(r, f)
        os.chmod(BIN + ".part", 0o755)
        os.replace(BIN + ".part", BIN)
        print("cloudflared скачан:", BIN)
        return BIN
    except Exception as e:
        print("cloudflared не скачался:", e)
        return None


def healthy(base=None):
    try:
        with urllib.request.urlopen((base or url) + "/health", timeout=20) as r:
            return r.read() == b"ok"
    except Exception:
        return False


def tunnel():
    """cloudflared даёт адрес https://….trycloudflare.com. Он меняется при перезапуске — тогда бот
    обновляет кнопку приложения. Если адрес перестал открываться, туннель перезапускается."""
    exe = cloudflared()
    if not exe:
        print("Нет cloudflared — приложение недоступно, работают кнопки в чате")
        return
    while True:
        proc = subprocess.Popen([exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{PORT}"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        found = threading.Event()

        def read():
            global url
            for line in proc.stderr:
                m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
                if m and not found.is_set():
                    url = m.group(0)
                    found.set()

        threading.Thread(target=read, daemon=True).start()
        if found.wait(60):
            for _ in range(30):  # адрес начинает открываться не сразу
                if healthy():
                    break
                time.sleep(3)
            _cfg["on_url"](url)
            fails = 0
            while proc.poll() is None:
                time.sleep(120)
                fails = 0 if healthy() else fails + 1
                if fails >= 3:
                    print("Туннель не отвечает — перезапускаю")
                    proc.kill()
        else:
            proc.kill()
        proc.wait()
        time.sleep(5)


def start(token, owner, routes, on_url, login_ask=None):
    """Запускает сервер и (если нет HORO_WEB_URL) туннель. on_url(адрес) зовётся, когда адрес готов или сменился.
    login_ask(id, устройство, код) — написать Глебу «✅ Войти» (кнопка с id) для входа на сайт."""
    _cfg.update(token=token, owner=owner, routes=routes, on_url=on_url, login_ask=login_ask, version=version(),
                site_version=version(SITE), mirror_version=version(mirror.FILES))
    threading.Thread(target=serve, daemon=True).start()
    threading.Thread(target=watch_site, daemon=True).start()
    if url:
        on_url(url)
    else:
        threading.Thread(target=tunnel, daemon=True).start()
