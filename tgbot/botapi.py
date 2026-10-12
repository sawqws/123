"""Бот агента через обычный Bot API (только токен, без api_id/api_hash).

Раньше бот работал через Telethon, а Telethon требует ключи my.telegram.org даже для бота —
из-за этого секретарь не запускался, пока не получены ключи. Теперь бот живёт на Bot API,
а Telethon нужен только для аккаунта Глеба (tgbot/actions.py), и ключи для него можно получить позже.

Интерфейс повторяет то, чем agent.py пользовался в Telethon: send_message/send_file -> Msg (.edit/.delete),
события сообщений и кнопок (.raw_text, .message, .data, .answer, .edit, .get_message), Button.inline/text/request_phone.
Только стандартная библиотека: HTTP через urllib в потоке.
"""
import asyncio
import contextlib
import html
import json
import mimetypes
import os
import types as pytypes
import urllib.error
import urllib.request
import uuid

API = "https://api.telegram.org"
# Секретарь внутри Главного (один бот на двоих): данные его кнопок начинаются с PREFIX — по нему Главный
# отдаёт нажатия Секретарю. Ставит agent.py в режиме «через Главного»; входящие данные PREFIX теряют.
PREFIX = ""


class BotError(Exception):
    @property
    def not_modified(self):
        return "message is not modified" in str(self).lower()


class Button:
    """Кнопки как в Telethon; style: primary (синяя) | success (зелёная) | danger (красная)."""

    @staticmethod
    def inline(text, data=None, style=None):
        b = {"text": text, "callback_data": (PREFIX + (data or text))[:64]}
        if style:
            b["style"] = style
        return b

    @staticmethod
    def url(text, link):
        return {"text": text, "url": link}

    @staticmethod
    def web_app(text, link):
        """Кнопка, открывающая мини-приложение (только https)."""
        return {"text": text, "web_app": {"url": link}}

    @staticmethod
    def text(text, resize=None, persistent=None, placeholder=None, style=None, single_use=None):
        b = {"text": text, "_kb": {k: v for k, v in (("resize_keyboard", resize), ("is_persistent", persistent),
                                                    ("input_field_placeholder", placeholder),
                                                    ("one_time_keyboard", single_use)) if v is not None}}
        if style:
            b["style"] = style
        return b

    @staticmethod
    def request_phone(text, resize=None, single_use=None):
        b = Button.text(text, resize=resize, single_use=single_use)
        b["request_contact"] = True
        return b


def markup(buttons, plain=False):
    """Строки кнопок -> reply_markup Bot API. plain: без цветов (если Telegram их не принял)."""
    if buttons is None:
        return None
    rows = [r if isinstance(r, list) else [r] for r in buttons]
    if not rows:
        return {"inline_keyboard": []}
    clean = lambda b: {k: v for k, v in b.items() if k != "_kb" and not (plain and k == "style")}
    if any("callback_data" in b or "url" in b or "web_app" in b for r in rows for b in r):
        return {"inline_keyboard": [[clean(b) for b in r] for r in rows]}
    opts = {}
    for r in rows:
        for b in r:
            opts.update(b.get("_kb") or {})
    return {"keyboard": [[clean(b) for b in r] for r in rows], **opts}


class Msg:
    def __init__(self, bot, chat, mid, text=""):
        self.bot, self.chat_id, self.id, self.text = bot, chat, mid, text

    async def edit(self, text=None, buttons=None, parse_mode="html"):
        return await self.bot.edit(self.chat_id, self.id, text, buttons, parse_mode)

    async def delete(self):
        await self.bot.call("deleteMessage", chat_id=self.chat_id, message_id=self.id)


class Bot:
    def __init__(self, token, base=API):
        self.token, self.base = token, base
        self.intercept = None  # fn(update) -> True: обновление уже обработано (🛠 поддержка, common/support.py)
        self.html = {}  # (chat, id) -> HTML, чтобы дописывать к карточке «✓ подтверждено»

    # ---------- HTTP ----------

    def _post(self, method, params=None, files=None, timeout=60):
        url = f"{self.base}/bot{self.token}/{method}"
        if files:
            boundary = uuid.uuid4().hex
            body = b""
            for k, v in (params or {}).items():
                v = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v)
                body += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
            for k, path in files.items():
                ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
                with open(path, "rb") as f:
                    data = f.read()
                body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; '
                         f'filename="{os.path.basename(path)}"\r\nContent-Type: {ctype}\r\n\r\n').encode() + data + b"\r\n"
            body += f"--{boundary}--\r\n".encode()
            req = urllib.request.Request(url, body, {"Content-Type": f"multipart/form-data; boundary={boundary}"})
        else:
            req = urllib.request.Request(url, json.dumps(params or {}, ensure_ascii=False).encode(),
                                         {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                res = json.load(r)
        except urllib.error.HTTPError as e:
            try:
                res = json.load(e)
            except ValueError:
                raise BotError(f"HTTP {e.code}")
        if not res.get("ok"):
            raise BotError(res.get("description") or "ошибка Bot API")
        return res["result"]

    async def call(self, method, files=None, _timeout=60, **params):
        params = {k: v for k, v in params.items() if v is not None}
        return await asyncio.to_thread(self._post, method, params, files, _timeout)

    async def _with_markup(self, method, buttons, files=None, **params):
        """Цветные кнопки — новинка Telegram; если сервер их не примет, шлём те же кнопки без цвета."""
        try:
            return await self.call(method, files=files, reply_markup=markup(buttons), **params)
        except BotError as e:
            if buttons is None or not any(w in str(e).lower() for w in ("button", "markup", "keyboard", "style")):
                raise
            return await self.call(method, files=files, reply_markup=markup(buttons, plain=True), **params)

    # ---------- сообщения ----------

    async def send_message(self, chat, text, buttons=None, parse_mode="html", link_preview=False):
        m = await self._with_markup("sendMessage", buttons, chat_id=chat, text=text,
                                    parse_mode=parse_mode, link_preview_options={"is_disabled": not link_preview})
        self.html[(chat, m["message_id"])] = text
        return Msg(self, chat, m["message_id"], text)

    async def send_file(self, chat, path, caption="", parse_mode="html", buttons=None):
        m = await self._with_markup("sendPhoto", buttons, files={"photo": path}, chat_id=chat, caption=caption,
                                    parse_mode=parse_mode)
        return Msg(self, chat, m["message_id"], caption)

    async def edit(self, chat, mid, text=None, buttons=None, parse_mode="html"):
        if text is None:
            await self.call("editMessageReplyMarkup", chat_id=chat, message_id=mid,
                            reply_markup=markup(buttons) or {"inline_keyboard": []})
        else:
            await self._with_markup("editMessageText", buttons if buttons is not None else [], chat_id=chat,
                                    message_id=mid, text=text, parse_mode=parse_mode,
                                    link_preview_options={"is_disabled": True})
            self.html[(chat, mid)] = text
        return Msg(self, chat, mid, text or self.html.get((chat, mid), ""))

    @contextlib.asynccontextmanager
    async def action(self, chat, kind="typing"):
        async def loop():
            while True:
                with contextlib.suppress(Exception):
                    await self.call("sendChatAction", chat_id=chat, action=kind)
                await asyncio.sleep(4)
        task = asyncio.create_task(loop())
        try:
            yield
        finally:
            task.cancel()

    async def get_me(self):
        return pytypes.SimpleNamespace(**await self.call("getMe"))

    async def download(self, file_id, folder):
        f = await self.call("getFile", file_id=file_id)
        path = os.path.join(folder, uuid.uuid4().hex + os.path.splitext(f["file_path"])[1])
        url = f"{self.base}/file/bot{self.token}/{f['file_path']}"

        def get():
            with urllib.request.urlopen(url, timeout=120) as r, open(path, "wb") as out:
                out.write(r.read())
        await asyncio.to_thread(get)
        return path

    # ---------- приём ----------

    async def run(self, on_message, on_callback):
        offset = 0
        while True:
            try:
                updates = await self.call("getUpdates", _timeout=70, offset=offset, timeout=50,
                                          allowed_updates=["message", "callback_query"])
            except Exception as e:  # сеть моргнула — ждём и снова
                print("getUpdates:", repr(e), flush=True)
                await asyncio.sleep(5)
                continue
            for u in updates:
                offset = u["update_id"] + 1
                if self.intercept and await asyncio.to_thread(self.intercept, u):
                    continue
                self.dispatch(u, on_message, on_callback)

    def dispatch(self, u, on_message, on_callback):
        """Одно обновление Telegram -> обработчик (в своей задаче). Так же приходят обновления от Главного."""
        if "message" in u:
            asyncio.create_task(self._safe(on_message, MessageEvent(self, u["message"])))
            return True
        if "callback_query" in u:
            asyncio.create_task(self._safe(on_callback, CallbackEvent(self, u["callback_query"])))
            return True
        return False

    @staticmethod
    async def _safe(handler, ev):
        try:
            await handler(ev)
        except Exception as e:
            print("handler:", repr(e), flush=True)


class Media:
    """Сообщение с вложениями: .voice/.audio/.video_note/.contact и download_media(file=папка)."""

    def __init__(self, bot, m):
        self.bot, self.raw = bot, m
        self.voice, self.audio, self.video_note = m.get("voice"), m.get("audio"), m.get("video_note")
        c = m.get("contact")
        self.contact = pytypes.SimpleNamespace(phone_number=c["phone_number"]) if c else None

    async def download_media(self, file):
        media = self.voice or self.audio or self.video_note
        os.makedirs(file, exist_ok=True)
        return await self.bot.download(media["file_id"], file)


class MessageEvent:
    def __init__(self, bot, m):
        self.bot, self.raw = bot, m
        self.chat_id = m["chat"]["id"]
        self.id = m["message_id"]
        self.sender_id = (m.get("from") or {}).get("id")
        self.is_private = m["chat"].get("type") == "private"
        self.raw_text = m.get("text") or m.get("caption") or ""
        self.message = Media(bot, m)

    async def delete(self):
        with contextlib.suppress(BotError):
            await self.bot.call("deleteMessage", chat_id=self.chat_id, message_id=self.id)


class CallbackEvent:
    def __init__(self, bot, q):
        self.bot, self.raw = bot, q
        self.id = q["id"]
        self.sender_id = q["from"]["id"]
        data = q.get("data") or ""
        self.data = (data[len(PREFIX):] if PREFIX and data.startswith(PREFIX) else data).encode()
        m = q.get("message") or {}
        self.chat_id, self.msg_id = (m.get("chat") or {}).get("id"), m.get("message_id")
        self.plain = m.get("text") or m.get("caption") or ""
        self._answered = False

    async def answer(self, text=None, alert=False):
        if self._answered:
            return
        self._answered = True
        with contextlib.suppress(BotError):
            await self.bot.call("answerCallbackQuery", callback_query_id=self.id, text=text, show_alert=alert or None)

    async def edit(self, text=None, buttons=None, parse_mode="html"):
        return await self.bot.edit(self.chat_id, self.msg_id, text, buttons, parse_mode)

    async def get_message(self):
        text = self.bot.html.get((self.chat_id, self.msg_id)) or html.escape(self.plain, quote=False)
        return Msg(self.bot, self.chat_id, self.msg_id, text)
