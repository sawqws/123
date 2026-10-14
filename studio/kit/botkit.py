"""botkit — общий набор для ботов Студии: Telegram, единый стиль, мини-приложение, данные, расписания, тесты.
Только стандартная библиотека Python. Файл копируется в папку каждого бота (бот не зависит от репозитория).

    import botkit as kit
    bot = kit.Bot("Вода", emoji="💧", about="Считает воду за день", short="Вода за день",
                  commands=[("start", "Главное"), ("help", "Что умею")])

    @bot.home                         # главный экран: /start, кнопка «home», непонятный текст
    def home(c):
        bot.menu(c, "💧 <b>Вода</b>", ["Сегодня: 1,2 л из 2 л"],
                 buttons=[[kit.b("➕ 250 мл", "add|250", "primary"), kit.b("📊 Неделя", "week")]],
                 quick=[[kit.b("➕ 250 мл", "add|250", "primary")]])   # quick — что показать в режиме «приложение»

    @bot.button("add")                # кнопка с callback_data «add|250» → c.args == ["250"]
    def add(c):
        bot.db.update("ml", lambda v: (v or 0) + int(c.args[0]))
        c.toast("Записал")
        home(c)                       # c.show() правит то же сообщение

    @bot.command("help")
    def help_(c): c.say("…")

    @bot.text                         # свободный текст (и голосовое — уже распознанное)
    def on_text(c): c.say(kit.esc(c.text))

    @bot.reply("name")                # ответ на c.ask("name", "Как тебя зовут?")
    def got_name(c): ...

    @bot.photo                        # фото: c.photo — путь к .jpg
    def on_photo(c): ...

    @bot.api("state")                 # мини-приложение: GET/POST api/state → JSON
    def state(r): return {"ml": bot.db.get("ml", 0)}   # r.user, r.body; ошибка — raise kit.Fail("…")

    bot.daily("21:00", lambda: bot.send(bot.owner, "🌙 …"))   # каждый день по Москве; bot.every(30, fn) — раз в 30 мин

    if __name__ == "__main__":
        bot.run()

Окружение: BOT_TOKEN, APP_PORT (порт мини-приложения на 127.0.0.1), APP_URL (его https-адрес), BOT_OWNER (id
владельца), BOTKIT_COMMON (папка common/ репозитория: голосовые и фото). Данные — data/ рядом с ботом.
Тесты без сети: t = bot.testing(); t.send("/start"); t.press("add|250"); t.last, t.buttons, t.api("state").
Режим «💬 Чат / 📱 Приложение» встроен: первый /start (если есть https-адрес) спрашивает «🧭 Как удобнее?» (кнопки
mode:chat / mode:app), потом /mode, кнопка «⚙️ Режим» и строка «💬 Перейти в чат» в приложении (kit.js → POST
api/mode {mode}); bot.ui_mode() → "chat" | "app" | "". bot.menu(...) сам собирает главный
экран под режим: в чате — все кнопки и без кнопки приложения, в приложении — кнопка приложения + quick (1–2 ряда).
bot.app_button() в режиме «чат» возвращает None (rows() и c.show() пустые пропускают); /app показывает её всегда.
🛠 Поддержка встроена: /fix (и кнопка kit.support_button()) — владелец пишет, что сломалось или что улучшить,
Claude на сервере правит код бота, гоняет test_bot.py и перезапускает его (common/support.py). В мини-приложении —
строка «🛠 Поддержка и пожелания» (kit.js, сама внизу) → встроенный api("support") → то же самое.
"""
import datetime
import hashlib
import hmac
import html
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
MSK = datetime.timezone(datetime.timedelta(hours=3))
DAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
MODES = {"chat": "💬 чат", "app": "📱 приложение"}


# ---------- стиль сообщений (один на все боты) ----------

def esc(x):
    return html.escape(str(x), quote=False)


def card(title, lines=(), foot="", expand=False):
    """Заголовок жирным с одним эмодзи, содержимое — цитатой (длинное — сворачивается), подсказка курсивом ниже.
    title и lines — уже HTML (чужой текст оборачивай в esc). Колонки пробелами не выравнивать."""
    body = "\n".join(str(x) for x in lines if x is not None)
    text = title
    if body:
        text += f"\n<blockquote{' expandable' if expand or len(body) > 700 else ''}>{body}</blockquote>"
    if foot:
        text += "\n" + foot
    return text


def error(text):
    return f"⚠️ {text}"


def clip(text, n=24):
    """Текст кнопки до ~24 символов — иначе Telegram режет его посередине."""
    text = str(text)
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def b(text, data, style=None):
    """Кнопка. style: primary (синяя) | success (зелёная) | danger (красная) — если Telegram не примет, будет без цвета."""
    # Telegram: callback_data — до 64 БАЙТ (кириллица — 2 байта на букву), длиннее — 400 и сообщение не уходит
    btn = {"text": clip(text), "callback_data": str(data).encode()[:64].decode("utf-8", "ignore")}
    if style:
        btn["style"] = style
    return btn


def url(text, link, style=None):
    btn = {"text": clip(text), "url": link}
    if style:
        btn["style"] = style
    return btn


def rows(btns, n=2):
    """Кнопки по n в ряд (не больше 3). None пропускаются."""
    btns = [x for x in btns if x]
    return [btns[i:i + n] for i in range(0, len(btns), n)]


def now():
    return datetime.datetime.now(MSK)


def plural(n, one, few, many):
    """plural(3, "раз", "раза", "раз") → «3 раза»."""
    n10, n100 = abs(n) % 10, abs(n) % 100
    word = one if n10 == 1 and n100 != 11 else few if 2 <= n10 <= 4 and not 12 <= n100 <= 14 else many
    return f"{n} {word}"


class Fail(Exception):
    """Понятная пользователю ошибка: в чате — «⚠️ текст», в мини-приложении — {"error": текст} и код 400."""


# ---------- данные ----------

class Store:
    """JSON-файл data/db.json: bot.db.get(k, по_умолчанию), set(k, v), update(k, fn), delete(k). Потокобезопасно."""

    def __init__(self, path):
        self.path, self.lock = path, threading.RLock()
        self.data = {}
        try:
            with open(path) as f:
                self.data = json.load(f)
        except (OSError, ValueError):
            pass

    def save(self):
        with self.lock:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)

    def get(self, key, default=None):
        with self.lock:
            v = self.data.get(str(key))
            return default if v is None else json.loads(json.dumps(v))  # копия: правка снаружи не портит базу

    def set(self, key, value):
        with self.lock:
            self.data[str(key)] = value
            self.save()
            return value

    def update(self, key, fn, default=None):
        """Атомарно: новое = fn(старое или default); возвращает новое."""
        with self.lock:
            return self.set(key, fn(self.get(key, default)))

    def delete(self, key):
        with self.lock:
            self.data.pop(str(key), None)
            self.save()

    def keys(self):
        with self.lock:
            return list(self.data)


# ---------- контекст обработчика ----------

class Ctx:
    """Что пришло и как ответить. chat, uid, user, text, args, msg, mid (сообщение с кнопкой), photo, data."""

    def __init__(self, bot, chat, user=None, text="", args=None, msg=None, cq=None, mid=None, photo=None, data=None):
        self.bot, self.chat, self.user = bot, chat, user or {}
        self.uid = self.user.get("id")
        self.text, self.args, self.msg, self.cq = text or "", args or [], msg or {}, cq
        self.mid, self.photo, self.data = mid, photo, data
        self._toast = None

    def say(self, text, buttons=None):
        """Новое сообщение. Возвращает его id."""
        return self.bot.send(self.chat, text, buttons)

    def show(self, text, buttons=None):
        """Экран: из кнопки — правит то же сообщение, из сообщения — присылает новое."""
        if self.mid:
            self.bot.edit(self.chat, self.mid, text, buttons)
            return self.mid
        self.mid = self.say(text, buttons)
        return self.mid

    def toast(self, text, alert=False):
        """Всплывашка на нажатую кнопку."""
        self._toast = (str(text)[:190], alert)

    def ack(self, text="", alert=False):
        """Ответить на нажатие сразу (крутилка у кнопки гаснет) — перед долгим делом в обработчике кнопки."""
        if self.cq and not getattr(self, "_acked", False):
            self._acked = True
            try:
                self.bot.call("answerCallbackQuery", callback_query_id=self.cq["id"], text=str(text)[:190] or None,
                              show_alert=alert or None)
            except Exception:
                pass

    def ask(self, key, text, buttons=None, data=None):
        """Спросить и ждать ответ текстом: его получит @bot.reply(key), c.data — то, что передали сюда."""
        self.bot.waits[self.chat] = (key, data)
        return self.say(text, buttons or [[b("Отмена", "home")]])

    def typing(self):
        self.bot.call("sendChatAction", chat_id=self.chat, action="typing")

    def send_photo(self, path, caption="", buttons=None):
        return self.bot.send_file(self.chat, path, caption, buttons, kind="photo")

    def send_file(self, path, caption="", buttons=None):
        return self.bot.send_file(self.chat, path, caption, buttons, kind="document")

    def delete(self):
        if self.mid:
            self.bot.delete(self.chat, self.mid)


class Request:
    """Запрос мини-приложения: user (от Telegram, проверен), body (JSON или параметры адреса)."""

    def __init__(self, user, body):
        self.user, self.body = user, body if isinstance(body, dict) else {}
        self.uid = (user or {}).get("id")


# ---------- бот ----------

class Bot:
    def __init__(self, name, emoji="✨", about="", short="", commands=None, public=False, app_text=None,
                 token=None, port=None, app_url=None, data=None, webapp=None, support=None, tg_name=None):
        self.name, self.emoji, self.about, self.short = name, emoji, about, short
        self.tg_name = tg_name  # имя бота в Telegram (setMyName); None — не трогаем, оно задано в BotFather
        self.commands = commands or [("start", "Главное"), ("help", "Что умею")]
        self.public = public  # True — ботом пользуются все; иначе только владелец (первый /start или BOT_OWNER)
        self.app_text = app_text or name
        self.token = token if token is not None else os.environ.get("BOT_TOKEN", "")
        self.port = int(port or os.environ.get("APP_PORT") or 0)
        self.app_url = (app_url if app_url is not None else os.environ.get("APP_URL", "")).strip()
        if self.app_url and not self.app_url.endswith("/"):
            self.app_url += "/"
        caller = sys._getframe(1).f_globals.get("__file__")  # папка бота — там, где создан Bot (обычно bot.py)
        self.dir = os.path.dirname(os.path.abspath(caller)) if caller else HERE
        self.data_dir = data or os.path.join(self.dir, "data")
        self.webapp = webapp or os.path.join(self.dir, "webapp")
        self.db = Store(os.path.join(self.data_dir, "db.json"))
        self.handlers = {"command": {"mode": self._mode_cmd, "app": self._app_cmd},
                         "button": {"mode": self._mode_btn, "mode:chat": self._mode_btn, "mode:app": self._mode_btn},
                         "reply": {}, "api": {"mode": self._mode_api}}  # встроенные; бот может переопределить
        if all(c != "mode" for c, _ in self.commands):
            self.commands = list(self.commands) + [("mode", "Чат или приложение")]
        self.on_text = self.on_photo = self.home_fn = self.on_managed = None
        self.me = {}
        self.jobs = []
        self.waits = {}  # chat -> (ключ, data): ждём ответ на c.ask
        self.http_hook = None  # fn(handler, method) -> True, если запрос обработан (для Студии: прокси)
        self.support_cfg = support  # dict(name, folders, pm2, cwd, tests) или False — без поддержки
        self.support = None
        self.plain_buttons = False
        self.offset = 0
        self.testing_mode = None
        self.username = None
        env_owner = re.findall(r"\d+", os.environ.get("BOT_OWNER", ""))
        if env_owner and not self.db.get("_owner"):
            self.db.set("_owner", int(env_owner[0]))

    # --- регистрация ---

    def command(self, name):
        def deco(fn):
            self.handlers["command"][name.lstrip("/")] = fn
            return fn
        return deco

    def button(self, prefix):
        def deco(fn):
            self.handlers["button"][prefix] = fn
            return fn
        return deco

    def reply(self, key):
        def deco(fn):
            self.handlers["reply"][key] = fn
            return fn
        return deco

    def api(self, name):
        def deco(fn):
            self.handlers["api"][name] = fn
            return fn
        return deco

    def text(self, fn):
        self.on_text = fn
        return fn

    def photo(self, fn):
        self.on_photo = fn
        return fn

    def home(self, fn):
        self.home_fn = fn
        return fn

    def managed(self, fn):
        """fn(update): создан бот, которым управляет этот (Managed Bots, Bot API 9.6): update["bot"], update["user"].
        Токен нового бота — bot.managed_token(update["bot"]["id"])."""
        self.on_managed = fn
        return fn

    def newbot_link(self, username, name=""):
        """Ссылка «создать бота в один тап»: Telegram откроет окно с готовыми именем и @username, пользователь
        подтверждает — приходит @bot.managed. Работает, если у этого бота включён Bot Management Mode."""
        q = "?" + urllib.parse.urlencode({"name": name}) if name else ""
        return f"https://t.me/newbot/{self.username}/{username}{q}"

    def can_manage(self):
        """Включён ли у бота Bot Management Mode (свежий getMe)."""
        try:
            self.me = self.call("getMe").get("result") or self.me
        except Exception:
            pass
        return bool(self.me.get("can_manage_bots"))

    def managed_token(self, bot_id):
        return self.call("getManagedBotToken", user_id=bot_id).get("result")
    def daily(self, hhmm, fn, days=None):
        """fn() каждый день в ЧЧ:ММ по Москве; days — «пн-пт», «сб,вс» или список номеров 0..6."""
        h, m = (int(x) for x in hhmm.split(":"))
        self.jobs.append({"kind": "daily", "h": h, "m": m, "days": parse_days(days), "fn": fn, "last": None})

    def every(self, minutes, fn):
        self.jobs.append({"kind": "every", "sec": max(60, int(minutes * 60)), "fn": fn, "last": time.time()})

    # --- владелец ---

    @property
    def owner(self):
        return int(self.db.get("_owner") or 0)

    def allowed(self, uid):
        return self.public or (uid and uid == self.owner)

    # --- Telegram ---

    def _http(self, method, data, headers):
        req = urllib.request.Request(f"https://api.telegram.org/bot{self.token}/{method}", data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=70) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            if "message is not modified" in detail:
                return {"ok": True, "result": {}}
            e.detail = detail
            print(method, e.code, detail[:300], flush=True)
            raise

    def _call(self, method, params, files=None):
        if files:
            boundary = uuid.uuid4().hex
            body = b""
            for k, v in params.items():
                v = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v)
                body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n").encode()
            for k, path in files.items():
                with open(path, "rb") as f:
                    content = f.read()
                ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
                body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; "
                         f"filename=\"{os.path.basename(path)}\"\r\nContent-Type: {ctype}\r\n\r\n").encode()
                body += content + b"\r\n"
            body += f"--{boundary}--\r\n".encode()
            return self._http(method, body, {"Content-Type": f"multipart/form-data; boundary={boundary}"})
        return self._http(method, json.dumps(params, ensure_ascii=False).encode(),
                          {"Content-Type": "application/json"})

    def call(self, method, files=None, **params):
        """Любой метод Bot API. Цветные кнопки: если Telegram ответит 400 из-за них — те же кнопки без цвета."""
        params = {k: v for k, v in params.items() if v is not None}
        mk = params.get("reply_markup")
        if self.plain_buttons and mk:
            params["reply_markup"] = mk = no_style(mk)
        try:
            return self._call(method, params, files)
        except urllib.error.HTTPError as e:
            if e.code != 400 or not mk or no_style(mk) == mk or \
                    not re.search(r"button|markup|keyboard|style", getattr(e, "detail", ""), re.I):
                raise
            self.plain_buttons = True
            return self._call(method, {**params, "reply_markup": no_style(mk)}, files)

    @staticmethod
    def _markup(buttons):
        buttons = [[x for x in row if x] for row in (buttons or []) if row]
        return {"inline_keyboard": [r for r in buttons if r]}

    def send(self, chat, text, buttons=None):
        r = self.call("sendMessage", chat_id=chat, text=text[:4096], parse_mode="HTML",
                      link_preview_options={"is_disabled": True},
                      reply_markup=self._markup(buttons) if buttons else None)
        return (r.get("result") or {}).get("message_id")

    def edit(self, chat, mid, text, buttons=None):
        try:
            self.call("editMessageText", chat_id=chat, message_id=mid, text=text[:4096], parse_mode="HTML",
                      link_preview_options={"is_disabled": True}, reply_markup=self._markup(buttons))
            return mid
        except urllib.error.HTTPError:
            return self.send(chat, text, buttons)

    def delete(self, chat, mid):
        try:
            self.call("deleteMessage", chat_id=chat, message_id=mid)
        except Exception:
            pass

    def send_file(self, chat, path, caption="", buttons=None, kind="photo"):
        method = {"photo": "sendPhoto", "document": "sendDocument"}[kind]
        r = self.call(method, files={kind: path}, chat_id=chat, caption=caption[:1024] or None, parse_mode="HTML",
                      reply_markup=self._markup(buttons) if buttons else None)
        return (r.get("result") or {}).get("message_id")

    # --- режим «💬 Чат / 📱 Приложение» ---

    def ui_mode(self):
        """"chat" | "app" | "" (ещё не выбирал). Хранится в data/db.json («_mode»)."""
        m = self.db.get("_mode")
        return m if m in MODES else ""

    @property
    def mode(self):
        return self.ui_mode()

    def set_mode(self, mode):
        if mode not in MODES:
            raise ValueError("Режим — chat или app")
        self.db.set("_mode", mode)

    @property
    def app_mode(self):
        return self.ui_mode() == "app" and bool(self.app_url)

    def mode_line(self):
        """«Режим: 💬 чат · сменить — /mode» курсивом для подвала главного экрана ("" — если приложения нет)."""
        if not self.app_url:
            return ""
        return f"<i>Режим: {MODES.get(self.ui_mode(), 'не выбран')} · сменить — /mode</i>"

    def mode_button(self, text="⚙️ Режим"):
        return b(text, "mode") if self.app_url else None

    def choose_mode(self, c):
        """Карточка «🧭 Как удобнее?» — первый /start, /mode, кнопка «⚙️ Режим»."""
        if not self.app_url:
            return c.show(card("💬 <b>Всё здесь, в чате</b>", ["Приложения у меня пока нет — всё делается кнопками."]),
                          [[b("‹ Главное", "home")]])
        c.show(card("🧭 <b>Как удобнее?</b>", ["💬 <b>В чате</b> — всё кнопками и командами здесь",
                                                f"📱 <b>Приложение</b> — {esc(self.app_text)} в одном окне"],
                    "<i>Поменять можно в любой момент: /mode</i>"),
               [[b("💬 В чате", "mode:chat", "primary"), b("📱 Приложение", "mode:app", "primary")]])

    def _mode_cmd(self, c):
        self.choose_mode(c)

    def _app_cmd(self, c):
        if not self.app_url:
            return c.say("📱 Приложения у меня пока нет — всё здесь, в чате.")
        c.say(f"📱 <b>{esc(self.app_text)}</b>", [[self.app_button(always=True)]])

    def _mode_btn(self, c):
        data = (c.cq or {}).get("data") or ""
        mode = data.partition(":")[2] if data.startswith("mode:") else (c.args[0] if c.args else "")
        if mode not in MODES:
            return self.choose_mode(c)
        self.set_mode(mode)
        c.toast(f"Режим: {MODES[mode]}")
        (self.home_fn or self._default_home)(c)

    def _mode_api(self, r):
        """GET api/mode → {"mode"}; POST {"mode": "chat"|"app"} — переключить; «chat» — бот присылает главное меню."""
        mode = str(r.body.get("mode") or "")
        if mode in MODES:
            self.set_mode(mode)
            if mode == "chat" and r.uid:
                self.send(r.uid, card("✅ <b>Режим: 💬 чат</b>",
                                      ["Всё, что было в приложении, есть и здесь — кнопками."],
                                      "<i>Вернуть приложение — /mode</i>"))
                (self.home_fn or self._default_home)(Ctx(self, r.uid, r.user))
        return {"mode": self.ui_mode()}

    def menu(self, c, title, lines=(), buttons=None, quick=None, foot=""):
        """Главный экран под режим. buttons — ряды кнопок для чата; quick — 1–2 ряда самого нужного для режима
        «приложение» (нет — первый ряд buttons). Кнопка приложения, «⚙️ Режим» и «🛠 Поддержка» добавляются сами."""
        buttons = [row for row in (buttons or []) if row]
        tail = [[self.mode_button(), support_button() if self.support_cfg is not False else None]]
        mode = self.ui_mode()
        if mode == "chat" or not self.app_url:
            rows = buttons + tail
        elif mode == "app":
            rows = [[self.app_button()]] + [row for row in (quick or buttons[:1]) if row][:2] + tail
        else:
            rows = [[self.app_button()]] + buttons + tail
        foot = "\n".join(x for x in (foot, self.mode_line()) if x)
        return c.show(card(title, lines, foot), rows)

    def app_button(self, text=None, style="primary", always=False):
        """Кнопка «открыть мини-приложение». None, если https-адреса нет или выбран режим «чат» (rows() и c.show()
        пустые кнопки пропускают); always=True — показать и в режиме «чат» (/app)."""
        if not self.app_url or (self.ui_mode() == "chat" and not always):
            return None
        btn = {"text": clip(text or f"📱 {self.app_text}"), "web_app": {"url": self.app_url}}
        if style:
            btn["style"] = style
        return btn

    def notify(self, text, buttons=None):
        """Написать владельцу (для расписаний и фоновых задач)."""
        if self.owner:
            return self.send(self.owner, text, buttons)

    def later(self, fn, *a):
        """Долгое (ИИ, сеть, распознавание) — в фоне, чтобы бот не замирал. В тестах выполняется сразу."""
        if self.testing_mode:
            return fn(*a)
        threading.Thread(target=self._safe, args=(fn, *a), daemon=True).start()

    def _safe(self, fn, *a):
        try:
            fn(*a)
        except Exception as e:  # фон: сказать в чат (Ctx среди аргументов или chat владельца первым), иначе тишина
            chat = next((x.chat for x in a if isinstance(x, Ctx)), None)
            if chat is None and a and isinstance(a[0], int) and a[0] == self.owner:
                chat = a[0]
            if chat is None and isinstance(e, Fail):
                print("later:", e, flush=True)
            self._fail(chat, e)

    # --- разбор апдейтов ---

    def handle(self, upd):
        try:
            if self.support and self.support.intercept(upd):  # 🛠 /fix и ответы поддержке
                return
            if upd.get("managed_bot"):
                if self.on_managed:
                    self.on_managed(upd["managed_bot"])
            elif upd.get("message"):
                self._message(upd["message"])
            elif upd.get("callback_query"):
                self._callback(upd["callback_query"])
        except Exception as e:
            chat = ((upd.get("message") or (upd.get("callback_query") or {}).get("message") or {}).get("chat") or {})
            self._fail(chat.get("id"), e)

    def _fail(self, chat, e):
        """Ошибка в обработчике → в чат понятное «⚠️ …» с выходом на главный (Fail — его текст), в лог — трассировка."""
        if not isinstance(e, Fail):
            traceback.print_exc()
        if chat:
            try:
                self.send(chat, error(esc(e) if isinstance(e, Fail) else "Что-то пошло не так. Попробуй ещё раз."),
                          [[b("‹ Главное", "home")]])
            except Exception:
                pass

    def _message(self, msg):
        chat, user = msg["chat"]["id"], msg.get("from") or {}
        if msg["chat"].get("type") != "private" or msg.get("managed_bot_created"):
            return  # о новом боте приходит и отдельный апдейт managed_bot
        text = (msg.get("text") or msg.get("caption") or "").strip()
        uid = user.get("id")
        if not self.owner and text.startswith("/start"):
            self.db.set("_owner", uid)
            self._menu()
        if not self.allowed(uid):
            return self.send(chat, f"🔒 <b>{esc(self.name)}</b> — личный бот.")
        if text.startswith("/") and not msg.get("photo"):
            cmd, _, rest = text[1:].partition(" ")
            cmd = cmd.split("@")[0].lower()
            self.waits.pop(chat, None)
            fn = self.handlers["command"].get(cmd) or self.home_fn or self._default_home  # неизвестная — главный экран
            if cmd == "start" and self.app_url and not self.ui_mode():  # первый раз — как удобнее?
                fn = self.choose_mode
            return fn(Ctx(self, chat, user, text=rest.strip(), args=rest.split(), msg=msg))
        kind = media_kind(msg)
        if kind == "voice":
            return self.later(self._voice, chat, user, msg)
        if kind == "photo":
            if self.on_photo:
                return self.later(self._photo, chat, user, msg, text)
            if text:  # подпись к фото — как обычный текст
                return self._route_text(Ctx(self, chat, user, text=text, msg=msg))
            return self.send(chat, "📷 Картинки я не разбираю — напиши словами, что на ней.")
        if text:
            return self._route_text(Ctx(self, chat, user, text=text, msg=msg))
        if kind == "video":  # видео без подписи — слушаем речь, как голосовое
            return self.later(self._voice, chat, user, msg)
        if any(msg.get(k) for k in ("sticker", "document", "animation", "location", "contact", "venue", "poll",
                                    "dice")):  # не молчим (служебные сообщения Telegram — пропускаем)
            self.send(chat, "🤷 Такое я не разберу — напиши текстом или пришли голосовое.")

    def _route_text(self, c):
        wait = self.waits.pop(c.chat, None)
        if wait and wait[0] in self.handlers["reply"]:
            c.data = wait[1]
            return self.handlers["reply"][wait[0]](c)
        if self.on_text:
            return self.on_text(c)
        return (self.home_fn or self._default_home)(c)

    def _voice(self, chat, user, msg):
        try:
            text = media().voice_text(self.token, msg)
        except Exception as e:
            return self.send(chat, error(esc(e) or "Не разобрал голосовое — напиши текстом."))
        if not text.strip():
            return self.send(chat, "🎙 Тишина — повтори, пожалуйста.")
        self.send(chat, f"🎙 <i>«{esc(text.strip())}»</i>")
        try:
            self._route_text(Ctx(self, chat, user, text=text.strip(), msg=msg))
        except Exception as e:  # в фоне: без этого ошибка обработчика — тишина в чате
            self._fail(chat, e)

    def _photo(self, chat, user, msg, caption):
        try:
            path = media().photo_path(self.token, msg, os.path.join(self.data_dir, "photos"))
        except Exception as e:
            return self.send(chat, error(esc(e) or "Не скачал фото."))
        try:
            self.on_photo(Ctx(self, chat, user, text=caption, msg=msg, photo=path))
        except Exception as e:
            self._fail(chat, e)

    def _callback(self, cq):
        msg, user = cq.get("message") or {}, cq.get("from") or {}
        chat = (msg.get("chat") or {}).get("id") or user.get("id")
        c = Ctx(self, chat, user, msg=msg, cq=cq, mid=msg.get("message_id"))
        try:
            if not self.allowed(user.get("id")):
                c.toast("🔒 Личный бот")
            else:
                prefix, *args = (cq.get("data") or "").split("|")
                c.args = args
                fn = self.handlers["button"].get(prefix)
                self.waits.pop(chat, None)  # нажал кнопку — ожидание ответа текстом («Отмена» и любая другая) снято
                if prefix == "home" and not fn:
                    fn = self.home_fn or self._default_home
                if fn:
                    fn(c)
                else:
                    c.toast("Эта кнопка устарела — открой /start")
        except Exception as e:
            c.toast("⚠️ " + (str(e) if isinstance(e, Fail) else "Не получилось — попробуй ещё раз"))
            raise  # handle() пришлёт понятное «⚠️ …» с кнопкой «‹ Главное»
        finally:
            toast, alert = c._toast or ("", False)
            c.ack(toast, alert)

    def _default_home(self, c):
        self.menu(c, f"{self.emoji} <b>{esc(self.name)}</b>", [esc(self.about)] if self.about else [])

    # --- мини-приложение ---

    def check_init_data(self, init_data, max_age=86400):
        """Подпись initData от Telegram (HMAC с ключом HMAC("WebAppData", токен)), не старше суток → user или None."""
        try:
            fields = dict(urllib.parse.parse_qsl(init_data or "", keep_blank_values=True, strict_parsing=True))
        except ValueError:
            return None
        got = fields.pop("hash", "")
        secret = hmac.new(b"WebAppData", self.token.encode(), hashlib.sha256).digest()

        def sign(d):
            return hmac.new(secret, "\n".join(f"{k}={v}" for k, v in sorted(d.items())).encode(),
                            hashlib.sha256).hexdigest()
        variants = [fields] + ([{k: v for k, v in fields.items() if k != "signature"}] if "signature" in fields else [])
        if not got or not any(hmac.compare_digest(sign(d), got) for d in variants):
            return None
        try:
            if time.time() - int(fields.get("auth_date", 0)) > max_age:
                return None
            user = json.loads(fields.get("user", "null"))
        except ValueError:
            return None
        return user if isinstance(user, dict) and isinstance(user.get("id"), int) else None

    def call_api(self, name, user, body):
        """(код, dict) — то, что отдаст сервер мини-приложения. Отдельно, чтобы проверять в тестах."""
        fn = self.handlers["api"].get(name)
        if not fn and name == "support":  # 🛠 встроено: лист «Поддержка и пожелания» в kit.js
            return self._support_api(user, body)
        if not fn:
            return 404, {"error": "нет такого действия"}
        if not user or not self.allowed(user.get("id")):
            if user and not self.owner:
                self.db.set("_owner", user["id"])
            else:
                return 403, {"error": "Это личный бот"}
        body = {} if body is None else body
        if not isinstance(body, dict):  # список, число, строка вместо объекта — не 500, а понятный отказ
            return 400, {"error": "ждём JSON-объект"}
        try:
            res = fn(Request(user, body))
            return 200, res if isinstance(res, dict) else {"ok": True, "result": res}
        except Fail as e:
            return 400, {"error": str(e)}
        except Exception as e:
            traceback.print_exc()
            return 500, {"error": f"Ошибка: {e}"}

    def _support_api(self, user, body):
        """Просьба из мини-приложения → та же поддержка, что /fix в чате (common/support.py). Только владельцу."""
        if not self.support:
            return 400, {"error": "Поддержка выключена или ещё запускается"}
        try:
            return 200, self.support.web((user or {}).get("id"), body if isinstance(body, dict) else {})
        except PermissionError as e:
            return 403, {"error": str(e)}
        except ValueError as e:
            return 400, {"error": str(e)}

    def serve(self):
        bot = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def reply(self, code, body, ctype="application/json; charset=utf-8", cache="no-store"):
                data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", cache)
                self.end_headers()
                self.wfile.write(data)

            def route(self, method):
                if bot.http_hook and bot.http_hook(self, method):
                    return
                path = urllib.parse.urlparse(self.path)
                p = path.path
                if p == "/health":
                    return self.reply(200, b"ok", "text/plain")
                if p.startswith("/api/"):
                    user = bot.check_init_data(self.headers.get("X-Init-Data") or "")
                    if not user:
                        return self.reply(401, {"error": "Открой приложение из Telegram"})
                    body = dict(urllib.parse.parse_qsl(path.query))
                    if method == "POST":
                        n = int(self.headers.get("Content-Length") or 0)
                        try:
                            body = json.loads(self.rfile.read(min(n, 5_000_000)) or b"{}")
                        except ValueError:
                            return self.reply(400, {"error": "плохой JSON"})
                    return self.reply(*bot.call_api(p[5:], user, body))
                if method != "GET":
                    return self.reply(405, {"error": "нельзя"})
                return self.static(p)

            def static(self, p):
                rel = "index.html" if p in ("", "/") else p.lstrip("/")
                full = os.path.realpath(os.path.join(bot.webapp, rel))
                if not full.startswith(os.path.realpath(bot.webapp) + os.sep) or not os.path.isfile(full):
                    return self.reply(404, b"not found", "text/plain")
                with open(full, "rb") as f:
                    data = f.read()
                ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
                if rel == "index.html":
                    v = str(int(max(os.path.getmtime(os.path.join(bot.webapp, x)) for x in os.listdir(bot.webapp))))
                    data = data.replace(b"{{v}}", v.encode())
                    return self.reply(200, data, "text/html; charset=utf-8")
                if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
                    ctype += "; charset=utf-8"
                self.reply(200, data, ctype, "public, max-age=3600")

            def do_GET(self):
                self.route("GET")

            def do_POST(self):
                self.route("POST")

        httpd = ThreadingHTTPServer(("127.0.0.1", self.port), H)
        print(f"Мини-приложение: 127.0.0.1:{self.port} → {self.app_url or 'адреса https нет'}", flush=True)
        httpd.serve_forever()

    # --- запуск ---

    def _menu(self):
        """Кнопка мини-приложения слева от поля ввода (или обычное меню команд, если https нет)."""
        try:
            if self.app_url:
                self.call("setChatMenuButton", menu_button={"type": "web_app", "text": clip(self.app_text, 16),
                                                           "web_app": {"url": self.app_url}})
            else:
                self.call("setChatMenuButton", menu_button={"type": "commands"})
        except Exception as e:
            print("menu:", e, flush=True)

    def setup(self):
        """Имя для ссылок, команды, описание, кнопка приложения, аватар (data/avatar.png или avatar.png)."""
        steps = [
            ("getMe", {}),
            *([("setMyName", {"name": self.tg_name[:64]})] if self.tg_name else []),
            ("setMyCommands", {"commands": [{"command": c, "description": d[:256]} for c, d in self.commands]}),
            ("setMyDescription", {"description": (self.about or "")[:512]}),
            ("setMyShortDescription", {"short_description": (self.short or self.about or "")[:120]}),
        ]
        for method, params in steps:
            try:
                r = self.call(method, **params)
                if method == "getMe":
                    self.me = r.get("result") or {}
                    self.username = self.me.get("username")
            except Exception as e:
                print(method, e, flush=True)
        self._menu()
        for path in (os.path.join(self.data_dir, "avatar.png"), os.path.join(self.dir, "avatar.png")):
            if os.path.exists(path):
                stamp = f"{os.path.getsize(path)}:{int(os.path.getmtime(path))}"
                if self.db.get("_avatar") != stamp:
                    try:
                        self.call("setMyProfilePhoto", files={"avatar": path},
                                  photo={"type": "static", "photo": "attach://avatar"})
                        self.db.set("_avatar", stamp)
                    except Exception as e:
                        print("avatar:", e, flush=True)
                break

    def _scheduler(self):
        while True:
            t = now()
            for j in self.jobs:
                try:
                    if j["kind"] == "daily":
                        key = t.strftime("%Y-%m-%d")
                        if (t.hour, t.minute) >= (j["h"], j["m"]) and j["last"] != key and \
                                (not j["days"] or t.weekday() in j["days"]) and \
                                (t.hour, t.minute) < (j["h"], j["m"] + 30):  # после простоя не шлём старое
                            j["last"] = key
                            j["fn"]()
                    elif time.time() - j["last"] >= j["sec"]:
                        j["last"] = time.time()
                        j["fn"]()
                except Exception:
                    traceback.print_exc()
            time.sleep(20)

    def _start_support(self):
        """common/support.py: /fix — Claude чинит и дорабатывает этого бота прямо на сервере."""
        if self.support_cfg is False:
            return
        try:
            media()  # тот же путь к common/
            import support
        except (Fail, ImportError) as e:
            print("support:", e, flush=True)
            return
        cfg = self.support_cfg or {}
        self.support = support.Support(
            self.token, cfg.get("name", self.name), folders=cfg.get("folders", ["."]),
            pm2=cfg.get("pm2") or os.environ.get("BOT_PM2") or "bot-" + os.path.basename(self.dir),
            allowed=lambda uid: bool(uid) and uid == self.owner, cwd=cfg.get("cwd", self.dir),
            tests=cfg.get("tests", ["python3 test_bot.py"] if os.path.exists(os.path.join(self.dir, "test_bot.py"))
                          else []))

    def run(self):
        if not self.token:
            sys.exit("Нет BOT_TOKEN")
        self.setup()
        self._start_support()
        if self.port:
            threading.Thread(target=self.serve, daemon=True).start()
        if self.jobs:
            threading.Thread(target=self._scheduler, daemon=True).start()
        print(f"{self.name} (@{self.username}) работает", flush=True)
        while True:
            try:
                r = self.call("getUpdates", offset=self.offset, timeout=50,
                              allowed_updates=["message", "callback_query", "managed_bot"])
                for upd in r.get("result", []):
                    self.offset = upd["update_id"] + 1
                    threading.Thread(target=self.handle, args=(upd,), daemon=True).start()
            except Exception as e:
                print("getUpdates:", e, flush=True)
                time.sleep(5)

    # --- тесты ---

    def testing(self, owner=1):
        """Бот без сети: всё, что он шлёт в Telegram, копится в t.calls. Возвращает Tester."""
        return Tester(self, owner)


class Tester:
    """t.send("/start"); t.press("add|250"); t.last — текст последнего сообщения; t.buttons — тексты его кнопок;
    t.api("state", {...}) → (код, dict); t.calls — все вызовы Bot API [(метод, параметры)]."""

    def __init__(self, bot, owner):
        self.bot, self.owner, self.calls, self.n = bot, owner, [], 100
        bot.testing_mode = self
        bot.token = bot.token or "123:TEST"
        bot.db.data = {}
        bot.db.save = lambda: None  # в тестах база только в памяти
        if owner:
            bot.db.data["_owner"] = owner

        def fake(method, params, files=None):
            self.calls.append((method, params))
            self.n += 1
            return {"ok": True, "result": {"message_id": self.n, "username": "test_bot"}}
        bot._call = fake

    def send(self, text, uid=None, **extra):
        uid = uid or self.owner
        msg = {"message_id": 1, "chat": {"id": uid, "type": "private"}, "from": {"id": uid, "first_name": "Тест"},
               "text": text, **extra}
        self.bot.handle({"update_id": 1, "message": msg})
        return self

    def press(self, data, uid=None, mid=None):
        uid = uid or self.owner
        self.bot.handle({"update_id": 1, "callback_query": {
            "id": "1", "data": data, "from": {"id": uid},
            "message": {"message_id": mid or self.n, "chat": {"id": uid, "type": "private"}}}})
        return self

    def api(self, name, body=None, uid=None):
        return self.bot.call_api(name, {"id": uid or self.owner, "first_name": "Тест"}, body or {})

    @property
    def messages(self):
        return [p for m, p in self.calls if m in ("sendMessage", "editMessageText", "sendPhoto", "sendDocument")]

    @property
    def last(self):
        m = self.messages
        return (m[-1].get("text") or m[-1].get("caption") or "") if m else ""

    @property
    def buttons(self):
        m = self.messages
        kb = ((m[-1].get("reply_markup") or {}).get("inline_keyboard") or []) if m else []
        return [btn["text"] for row in kb for btn in row]

    @property
    def keyboard(self):
        """Кнопки последнего сообщения целиком (dict): проверить web_app, url, style."""
        m = self.messages
        kb = ((m[-1].get("reply_markup") or {}).get("inline_keyboard") or []) if m else []
        return [btn for row in kb for btn in row]

    @property
    def toasts(self):
        return [p.get("text") for m, p in self.calls if m == "answerCallbackQuery"]


# ---------- мелочи ----------

def support_button(text="🛠 Поддержка"):
    """Кнопка поддержки (ошибка или доработка) — для главного экрана или /help."""
    return {"text": text, "callback_data": "sup|ask"}


def no_style(markup):
    if not isinstance(markup, dict) or "inline_keyboard" not in markup:
        return markup
    return {**markup, "inline_keyboard": [[{k: v for k, v in btn.items() if k != "style"} for btn in row]
                                          for row in markup["inline_keyboard"]]}


def parse_days(days):
    if not days:
        return None
    if isinstance(days, (list, tuple, set)):
        return set(days)
    s, res = str(days).lower().replace(" ", ""), set()
    for part in s.split(","):
        if "-" in part:
            a, z = part.split("-", 1)
            if a in DAYS and z in DAYS:
                i, j = DAYS.index(a), DAYS.index(z)
                res.update(range(i, j + 1) if i <= j else list(range(i, 7)) + list(range(0, j + 1)))
        elif part in DAYS:
            res.add(DAYS.index(part))
    return res or None


_media = None


def media():
    """common/media.py из репозитория (голосовые через faster-whisper, фото)."""
    global _media
    if _media is None:
        for d in (os.environ.get("BOTKIT_COMMON"), "/root/horo/common",
                  os.path.join(HERE, "..", "..", "common")):
            if d and os.path.exists(os.path.join(d, "media.py")):
                sys.path.insert(0, os.path.abspath(d))
                break
        try:
            import media as m
        except ImportError:
            raise Fail("Голосовые и фото здесь не настроены — напиши текстом.")
        _media = m
    return _media


def media_kind(msg):
    if msg.get("voice") or msg.get("audio") or msg.get("video_note"):
        return "voice"
    doc = msg.get("document") or {}
    if msg.get("photo") or (doc.get("mime_type") or "").startswith("image/"):
        return "photo"
    if msg.get("video") or (doc.get("mime_type") or "").startswith("video/"):
        return "video"
    return None


def claude(prompt, timeout=180, model=None, as_json=False):
    """Спросить Claude (Claude Code на сервере уже вошёл в аккаунт). Без инструментов. Долго — зови из bot.later.
    as_json=True — вытащить из ответа первый JSON-объект {…} или список […]. Ошибка — Fail с понятным текстом."""
    cmd = ["claude", "-p", prompt, "--output-format", "json",
           "--disallowedTools", "Bash", "Edit", "Write", "WebFetch", "WebSearch", "Task"]
    if model:
        cmd += ["--model", model]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        res = json.loads(p.stdout or "{}")
    except FileNotFoundError:
        raise Fail("ИИ на сервере не установлен")
    except subprocess.TimeoutExpired:
        raise Fail("ИИ думал слишком долго — попробуй ещё раз")
    except ValueError:
        raise Fail("ИИ ответил непонятно — попробуй ещё раз")
    text = (res.get("result") or "").strip()
    if res.get("is_error") or not text:
        raise Fail("ИИ сейчас недоступен — попробуй позже")
    if not as_json:
        return text
    m = re.search(r"(\{.*\}|\[.*\])", text, re.S)
    try:
        return json.loads(m.group(1)) if m else None
    except ValueError:
        return None
