"""Приложение бота внутри Telegram (Mini App): веб-сервер на 127.0.0.1 и https-адрес через cloudflared.

Что отдавать и что запускать, решает bot.py: он передаёт в start() обработчики вида
{"/api/…": функция(тело запроса) -> dict}. Здесь только сервер, проверка подписи Telegram и туннель.
Пускает только владельца бота (по подписанным данным Telegram, их не подделать без токена).

Переменные окружения:
  HORO_WEB_PORT  порт на 127.0.0.1 (по умолчанию 8788; у планера 8787)
  HORO_WEB_URL   свой https-адрес; если пусто — бот поднимает туннель cloudflared (адрес …trycloudflare.com)
"""
import hashlib
import hmac
import json
import mimetypes
import os
import platform
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
WEB = os.path.join(HERE, "webapp")
PORT = int(os.environ.get("HORO_WEB_PORT") or 8788)
BIN = os.path.join(os.path.dirname(HERE), "horo", "tmp", "bin", "cloudflared")  # если cloudflared не установлен

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


def version():
    """Метка файлов приложения: меняется при обновлении, чтобы Telegram не показывал старые из кэша."""
    h = hashlib.sha1()
    for name in sorted(os.listdir(WEB)):
        with open(os.path.join(WEB, name), "rb") as f:
            h.update(f.read())
    return h.hexdigest()[:10]


class Web(BaseHTTPRequestHandler):
    server_version = "HDP/1"

    def log_message(self, fmt, *args):  # без шума в логах pm2
        pass

    def reply(self, code, body, ctype="application/json; charset=utf-8", cache="no-store"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/health":
            return self.reply(200, "ok", "text/plain")
        if path.startswith("/api/"):
            return self.api(path, {})
        if path in ("/", "/index.html"):
            with open(os.path.join(WEB, "index.html"), encoding="utf-8") as f:
                page = f.read().replace("{{v}}", _cfg.get("version", "0"))
            return self.reply(200, page, "text/html; charset=utf-8")
        name = os.path.basename(path)  # только файлы из webapp/, без подпапок
        file = os.path.join(WEB, name)
        if name and name != "index.html" and os.path.isfile(file):
            with open(file, "rb") as f:
                ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
                if ctype.startswith("text/") or ctype.endswith("javascript"):
                    ctype += "; charset=utf-8"
                return self.reply(200, f.read(), ctype, "public, max-age=31536000, immutable")
        self.reply(404, {"error": "not found"})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(min(n, 1_000_000)) or b"{}") if n else {}
        except ValueError:
            return self.reply(400, {"error": "bad json"})
        self.api(path, body if isinstance(body, dict) else {})

    def api(self, path, body):
        user = check_init_data(self.headers.get("X-Init-Data") or "", _cfg["token"])
        if not user:
            return self.reply(401, {"error": "Открой приложение через Telegram"})
        if user.get("id") != _cfg["owner"]():
            return self.reply(403, {"error": "Это личный бот"})
        fn = _cfg["routes"].get(path)
        if not fn:
            return self.reply(404, {"error": "нет такого метода"})
        try:
            self.reply(200, fn(body))
        except ValueError as e:  # понятная ошибка для человека
            self.reply(400, {"error": str(e)})
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


def healthy():
    try:
        with urllib.request.urlopen(url + "/health", timeout=20) as r:
            return r.read() == b"ok"
    except Exception:
        return False


def tunnel():
    """cloudflared даёт адрес https://….trycloudflare.com. Он меняется при перезапуске — тогда бот
    обновляет кнопку приложения. Если адрес перестал открываться, туннель перезапускается."""
    global url
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


def start(token, owner, routes, on_url):
    """Запускает сервер и (если нет HORO_WEB_URL) туннель. on_url(адрес) зовётся, когда адрес готов или сменился."""
    _cfg.update(token=token, owner=owner, routes=routes, on_url=on_url, version=version())
    threading.Thread(target=serve, daemon=True).start()
    if url:
        on_url(url)
    else:
        threading.Thread(target=tunnel, daemon=True).start()
