"""Мини-приложение Секретаря: маленький HTTP-сервер внутри процесса агента (asyncio, без зависимостей).

Слушает 127.0.0.1:TG_AGENT_PORT (по умолчанию 8794); снаружи — https://agent.<ip-через-дефисы>.sslip.io
(Caddy проксирует сюда) или свой адрес в TG_AGENT_URL. Страницы — tgbot/webapp/ (без сборки).
Каждый запрос к /api/ несёт подпись Telegram (заголовок X-Init-Data): пускаем только владельца.
Поручения идут тем же путём, что сообщения в чате (Agent.begin/work): живой статус и ответ — и в чате, и здесь;
подтверждения «Да/Нет» — по-прежнему в чате.
"""
import asyncio
import datetime
import hashlib
import hmac
import json
import mimetypes
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import actions
import jobs

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "webapp")
FILES = ("index.html", "app.css", "app.js", "kit.css", "kit.js")
MAX_AGE = 24 * 3600          # initData старше суток не принимаем
MAX_BODY = 256 * 1024
MAX_SUPPORT = 16 * 1024 * 1024  # 🛠 поддержка — со скриншотом
MEMORY_LIMIT = 20000
HUB_ENV = "/etc/hub-bot.env"
HUB_PORT = int(os.environ.get("HUB_PORT") or 8790)  # сервер Главного: в его режиме поддержка идёт через «Доработать»


# ---------- подпись Telegram ----------

def check_init_data(init_data, token, max_age=MAX_AGE, now=None):
    """Пользователь из initData Mini App, если подпись верна и не старше max_age, иначе None.
    core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app"""
    try:
        pairs = dict(urllib.parse.parse_qsl(init_data or "", keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None
    got = pairs.pop("hash", "")
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()

    def sign(d):
        check = "\n".join(f"{k}={v}" for k, v in sorted(d.items()))
        return hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    variants = [pairs] + ([{k: v for k, v in pairs.items() if k != "signature"}] if "signature" in pairs else [])
    if not got or not any(hmac.compare_digest(sign(d), got) for d in variants):
        return None
    try:
        age = (now or time.time()) - int(pairs.get("auth_date", 0))
        user = json.loads(pairs.get("user", "null"))
    except ValueError:
        return None
    if age > max_age or age < -300 or not isinstance(user, dict) or not isinstance(user.get("id"), int):
        return None
    return user


def sign_init_data(token, user, auth_date=None, **extra):
    """Подписанный initData — для тестов и превью."""
    fields = {"auth_date": str(int(auth_date if auth_date is not None else time.time())),
              "user": json.dumps(user, ensure_ascii=False, separators=(",", ":")), **extra}
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(fields)


# ---------- адрес снаружи ----------

def server_ip():
    if os.environ.get("TG_AGENT_IP"):
        return os.environ["TG_AGENT_IP"]
    for url in ("https://api.ipify.org", "https://ipv4.icanhazip.com", "https://ifconfig.me/ip"):
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                ip = r.read().decode().strip()
            if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", ip):
                return ip
        except Exception:
            pass
    return None


def public_url():
    """https-адрес приложения: TG_AGENT_URL или agent.<ip-через-дефисы>.sslip.io (маршрут — в Caddy планера)."""
    if os.environ.get("TG_AGENT_URL"):
        return os.environ["TG_AGENT_URL"].rstrip("/")
    ip = server_ip()
    return f"https://agent.{ip.replace('.', '-')}.sslip.io" if ip else None


def hub_url():
    """Мини-приложение Главного (там вкладка «Секретарь»): HUB_URL или https://hub.<ip>.sslip.io, плюс /app."""
    base = os.environ.get("HUB_URL") or ""
    if not base:
        try:
            with open(HUB_ENV) as f:
                base = dict(re.findall(r"^\s*([A-Z_]+)\s*=\s*(.*?)\s*$", f.read(), re.M)).get("HUB_URL", "")
        except OSError:
            pass
    base = base.strip().rstrip("/")
    if not base:
        ip = server_ip()
        base = f"https://hub.{ip.replace('.', '-')}.sslip.io" if ip else ""
    return base + "/app" if base.startswith("https://") else None


def healthy(url):
    try:
        with urllib.request.urlopen(url + "/health", timeout=15) as r:
            return r.read().strip() == b"ok"
    except Exception:
        return False


def version():
    h = hashlib.sha1()
    for f in FILES:
        try:
            with open(os.path.join(WEB, f), "rb") as fh:
                h.update(fh.read())
        except OSError:
            pass
    return h.hexdigest()[:10]


# ---------- данные для экранов ----------

def initials(name):
    words = [w for w in re.split(r"\s+", name or "") if w and w[0].isalnum()]
    return "".join(w[0] for w in words[:2]).upper() or "✦"


def when(d):
    if not d:
        return ""
    d = d.astimezone(actions.MSK)
    today = datetime.datetime.now(actions.MSK).date()
    if d.date() == today:
        return d.strftime("%H:%M")
    if d.date() == today - datetime.timedelta(days=1):
        return "вчера"
    return d.strftime("%d.%m")


def preview(m):
    if m is None:
        return ""
    text = (getattr(m, "message", "") or "").replace("\n", " ").strip()
    try:
        pre = actions.media(m)
    except Exception:
        pre = ""
    out = ("Вы: " if getattr(m, "out", False) else "") + pre + text
    return out[:160]


def chat_json(d):
    try:
        kind = actions.kind(d.entity)
    except Exception:
        kind = "чат"
    return {"id": d.id, "name": d.name or "?", "initials": initials(d.name), "kind": kind,
            "unread": d.unread_count or 0, "mentions": getattr(d, "unread_mentions_count", 0) or 0,
            "when": when(getattr(d, "date", None)), "preview": preview(getattr(d, "message", None))}


def job_json(j):
    nxt = datetime.datetime.fromisoformat(j["next"]).astimezone(actions.MSK)
    days = j.get("days") or []
    period = (f"один раз {datetime.date.fromisoformat(j['once']):%d.%m}" if j.get("once")
              else "каждый день" if len(days) == 7 else "по будням" if days == list(range(5))
              else "по выходным" if days == [5, 6] else ", ".join(jobs.DAYS[d] for d in days))
    if j.get("until"):
        period += f", до {datetime.date.fromisoformat(j['until']):%d.%m}"
    return {"id": j["id"], "time": j["time"], "period": period, "to": ", ".join(c["name"] for c in j["chats"]),
            "text": j.get("text") or "", "ai": j.get("ai") or "", "next": f"{nxt:%d.%m %H:%M}",
            "sent": j.get("sent", 0), "describe": jobs.describe(j)}


def run_json(agent):
    r = agent.run
    if not r:
        return None
    secs = r.get("secs") if r.get("state") != "work" else int(time.time() - r["started"])
    return {"id": r["id"], "q": r["q"], "state": r["state"], "steps": r["steps"][-12:], "secs": secs or 0,
            "html": r.get("html", ""), "source": r.get("source", "chat"),
            "ago": int(time.time() - r["ended"]) if r.get("ended") else 0,
            "confirm": agent.confirm_text if r["state"] == "work" and agent.waiting else ""}


def read_memory(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


class ApiError(Exception):
    def __init__(self, code, text):
        super().__init__(text)
        self.code = code


# ---------- HTTP ----------

class Web:
    def __init__(self, agent):
        self.agent = agent
        self.version = version()

    async def start(self, host="127.0.0.1", port=8794):
        self.server = await asyncio.start_server(self.on_conn, host, port, limit=64 * 1024)
        self.port = self.server.sockets[0].getsockname()[1]
        return self.server

    @staticmethod
    async def read_request(reader):
        """(метод, путь, заголовки, тело) или None, если клиент прислал мусор или пропал."""
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 15)
            lines = head.decode("latin-1").split("\r\n")
            method, target, _ = lines[0].split(" ", 2)
            headers = {}
            for ln in lines[1:]:
                k, _, v = ln.partition(":")
                if k:
                    headers[k.strip().lower()] = v.strip()
            n = int(headers.get("content-length") or 0)
            if n < 0 or n > (MAX_SUPPORT if target.split("?")[0] == "/api/support" else MAX_BODY):
                return method, target, headers, None
            body = await asyncio.wait_for(reader.readexactly(n), 15) if n else b""
            return method, target, headers, body
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError, ValueError, ConnectionError):
            return None

    async def on_conn(self, reader, writer):
        req = await self.read_request(reader)
        if req is None:
            writer.close()
            return
        try:
            if req[3] is None:
                raise ApiError(413, "слишком большой запрос")
            code, ctype, data, extra = await self.handle(*req)
        except ApiError as e:
            code, ctype, data, extra = e.code, "application/json; charset=utf-8", self.dump({"error": str(e)}), {}
        except Exception as e:
            print("web:", repr(e), flush=True)
            code, ctype, data, extra = 500, "application/json; charset=utf-8", self.dump({"error": "ошибка сервера"}), {}
        reason = {200: "OK", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found",
                  409: "Conflict", 413: "Payload Too Large", 500: "Internal Server Error", 502: "Bad Gateway"}.get(code, "OK")
        hdrs = {"Content-Type": ctype, "Content-Length": str(len(data)), "Connection": "close",
                "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
                "Cache-Control": "no-store", **extra}
        out = f"HTTP/1.1 {code} {reason}\r\n" + "".join(f"{k}: {v}\r\n" for k, v in hdrs.items()) + "\r\n"
        try:
            writer.write(out.encode("latin-1") + data)
            await writer.drain()
        except ConnectionError:
            pass
        finally:
            writer.close()

    @staticmethod
    def dump(obj):
        return json.dumps(obj, ensure_ascii=False).encode()

    async def handle(self, method, target, headers, body):
        path = urllib.parse.urlparse(target).path
        if path == "/health":
            return 200, "text/plain; charset=utf-8", b"ok", {}
        if path.startswith("/api/"):
            if method != "POST":
                raise ApiError(400, "нужен POST")
            user = check_init_data(headers.get("x-init-data", ""), self.agent.token)
            if not user:
                raise ApiError(401, "Откройте приложение из чата с Секретарём")
            if not self.agent.owner() or user["id"] != self.agent.owner():
                raise ApiError(403, "Это личный секретарь")
            try:
                req = json.loads(body or b"{}")
            except ValueError:
                raise ApiError(400, "плохой JSON")
            if not isinstance(req, dict):
                raise ApiError(400, "плохой JSON")
            if path == "/api/support":
                return 200, "application/json; charset=utf-8", self.dump(await self.support(req, user, headers)), {}
            return 200, "application/json; charset=utf-8", self.dump(await self.api(path, req, user)), {}
        if method != "GET":
            raise ApiError(400, "нужен GET")
        name = "index.html" if path in ("/", "/index.html") else path.lstrip("/")
        if name not in FILES:
            raise ApiError(404, "нет такой страницы")
        with open(os.path.join(WEB, name), "rb") as f:
            data = f.read()
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if name == "index.html":
            data = data.replace(b"{{v}}", self.version.encode())
            csp = ("default-src 'self'; script-src 'self' https://telegram.org; style-src 'self' 'unsafe-inline'; "
                   "img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'")
            return 200, ctype + "; charset=utf-8", data, {"Content-Security-Policy": csp}
        return 200, ctype + "; charset=utf-8", data, {"Cache-Control": "public, max-age=31536000, immutable"}

    # ---------- API ----------

    async def support(self, b, user, headers):
        """🛠 Поддержка из приложения: отдельным ботом — своя (common/support.py), в Главном — его «Доработать»
        для Секретаря (тот же бот, та же подпись Telegram)."""
        a = self.agent
        if a.support:
            try:
                return await asyncio.to_thread(a.support.web, user["id"], b)
            except PermissionError as e:
                raise ApiError(403, str(e))
            except ValueError as e:
                raise ApiError(400, str(e))
        if not a.via_hub:
            raise ApiError(400, "Поддержка ещё запускается — попробуйте через минуту")
        body = json.dumps({**b, "bot": "tg-agent"}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{HUB_PORT}/app/api/support", data=body, method="POST",
                                     headers={"X-Init-Data": headers.get("x-init-data", ""),
                                              "Content-Type": "application/json"})

        def call():
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return 200, json.load(r)
            except urllib.error.HTTPError as e:
                try:
                    return e.code, json.load(e)
                except ValueError:
                    return e.code, {}
            except OSError:
                return 502, {"error": "Главный не отвечает"}
        code, res = await asyncio.to_thread(call)
        if code != 200:
            raise ApiError(code, res.get("error") or "Главный не принял просьбу")
        return res

    async def api(self, path, b, user):
        a = self.agent
        if path == "/api/state":
            return await self.state(fresh=bool(b.get("fresh")), user=user)
        if path == "/api/run":
            return {"run": run_json(a), "history": a.history[-20:][::-1] if b.get("history") else None}
        if path == "/api/ask":
            text, title = self.task(b)
            run = a.begin(text, title, source="app")
            if run is None:
                raise ApiError(409, "Ещё занимаюсь прошлым поручением")
            asyncio.create_task(a.work(run))
            return {"run": run_json(a)}
        if path == "/api/stop":
            await a.stop(quiet=True)
            return {"run": run_json(a)}
        if path == "/api/new":
            a.new_conversation()
            return {"ok": True}
        if path == "/api/login":  # вход в аккаунт идёт в чате: номер, код с клавиатуры, пароль
            asyncio.create_task(a.start_login())
            return {"ok": True, "text": "Продолжите в чате: там кнопка «Войти»"}
        if path == "/api/jobs/delete":
            if not a.jobs.remove(str(b.get("id") or "")):
                raise ApiError(404, "Такого расписания уже нет")
            return {"jobs": [job_json(j) for j in a.jobs.load()]}
        if path == "/api/memory":
            return {"text": read_memory(a.memory_path())}
        if path == "/api/memory/save":
            text = str(b.get("text") or "")
            if len(text) > MEMORY_LIMIT:
                raise ApiError(400, f"Слишком длинно: больше {MEMORY_LIMIT} символов")
            a.save_memory(text)
            return {"text": read_memory(a.memory_path())}
        raise ApiError(404, "нет такого метода")

    def task(self, b):
        """Поручение из приложения -> (текст для Claude, заголовок для истории)."""
        a = self.agent
        preset = b.get("preset") or ""
        chat = re.sub(r"\s+", " ", str(b.get("chat") or "")).strip()[:200]
        cid = b.get("id")
        ref = f"«{chat}»" + (f" (id {int(cid)})" if isinstance(cid, int) else "")
        if preset == "missed":
            return a.ASK_MISSED, a.BTN_MISSED
        if preset == "sort":
            return a.ASK_SORT, a.BTN_SORT
        if preset in ("psy", "brief") and not chat:
            raise ApiError(400, "Укажите чат")
        if preset == "psy":
            return a.ASK_PSY.format(ref if cid is not None else chat), f"🧠 Разбор: {chat}"
        if preset == "brief":
            return (f"Перескажи кратко непрочитанное в чате {ref}: кто что хочет от меня и что стоит ответить."
                    f" Если непрочитанных нет — последние сообщения."), f"📬 {chat}"
        text = str(b.get("text") or "").strip()
        if not text:
            raise ApiError(400, "Напишите поручение")
        if len(text) > 4000:
            raise ApiError(400, "Слишком длинное поручение")
        return text, text

    async def state(self, fresh=False, user=None):
        a = self.agent
        me, chats, err = None, [], ""
        try:
            if await a.authorized():
                m = await a.client.get_me()
                me = {"name": actions.name(m), "first": m.first_name or "", "username": m.username or ""}
                ds = await actions.dialogs(a, fresh=fresh)
                unread = [d for d in ds if (d.unread_count or getattr(d, "unread_mentions_count", 0)) and not d.archived]
                unread.sort(key=lambda d: (-(getattr(d, "unread_mentions_count", 0) or 0), not getattr(d, "is_user", False),
                                           -(d.date.timestamp() if getattr(d, "date", None) else 0)))
                chats = [chat_json(d) for d in unread]
        except Exception as e:
            print("web state:", repr(e), flush=True)
            err = "Telegram не ответил, попробуйте обновить"
        hour = datetime.datetime.now(actions.MSK).hour
        hello = "Доброй ночи" if hour < 5 else "Доброе утро" if hour < 12 else "Добрый день" if hour < 18 else "Добрый вечер"
        return {
            "me": me, "error": err, "hello": hello,
            "first": (me or {}).get("first") or (user or {}).get("first_name") or "",
            "chats": chats[:40], "unread": len(chats), "mentions": sum(c["mentions"] for c in chats),
            "jobs": [job_json(j) for j in a.jobs.load()],
            "run": run_json(a), "history": a.history[-20:][::-1],
            "session": bool(a.session_active()), "examples": a.EXAMPLE_LIST,
        }
