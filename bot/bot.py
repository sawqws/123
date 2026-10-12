#!/usr/bin/env python3
"""Telegram-бот: решает задания horodigital.ru через Claude Code (`claude -p`)
и показывает, что осталось сделать.

Переменные окружения:
  TELEGRAM_TOKEN  токен от @BotFather
  TG_ALLOWED_ID   твой Telegram ID (бот отвечает только тебе); если пусто,
                  владельцем становится тот, кто первым нажмёт /start
  HORO_LOGIN, HORO_PASSWORD  логин и пароль сайта (их читают скрипты horo/)

Только стандартная библиотека Python, ничего ставить не нужно.
Команда /update скачивает новую версию из GitHub и перезапускает бота (pm2 поднимет его сам).
Команда /emoji: пришли премиум-эмодзи, и бот будет ставить их вместо обычных в своих сообщениях
(Telegram показывает их, если у владельца бота есть Premium).
Задания с проверкой учителем: бот присылает черновик, Глеб правит или жмёт «Отправить»;
правки запоминаются (bot/drafts.py). /alphabet — алфавит его почерком (horo/alphabet.py).
Голосовые распознаются (common/media.py) и дальше идут как набранный текст. Фото: при черновике учителю —
в черновик, иначе Claude ищет на нём задание (ссылку или название из списка) и предлагает решить.
Видео и кружки: речь — как голосовое, кадры — как фото.
"""
import datetime
import html
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from zoneinfo import ZoneInfo

import drafts
import web

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "common"))
import media  # noqa: E402  голосовые -> текст, фото (common/media.py)
import support  # noqa: E402  🛠 поддержка: /fix — Claude чинит и дорабатывает этого бота

# pm2 передаёт свой канал связи (NODE_CHANNEL_FD) — node-скрипты бота наследуют его и падают при выходе
# с кодом -6, хотя всё уже вывели. Скриптам бота этот канал не нужен.
for _k in ("NODE_CHANNEL_FD", "NODE_CHANNEL_SERIALIZATION_MODE"):
    os.environ.pop(_k, None)

TOKEN = os.environ["TELEGRAM_TOKEN"]
ALLOWED = int(os.environ.get("TG_ALLOWED_ID") or 0)  # 0: владельца ещё нет
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS = os.path.join(REPO, "horo", "tmp", "bot_settings.json")
API = f"https://api.telegram.org/bot{TOKEN}/"
LINK = re.compile(r"https://horodigital\.ru/student/topic/[0-9a-f-]{36}/task/[0-9a-f-]{36}\S*")
TIMEOUT = 15 * 60  # секунд на одно задание
MSK = ZoneInfo("Europe/Moscow")

lock = threading.Lock()
offset = 0    # следующий update_id из Telegram
mode = {}     # chat -> "show" | "confirm_all" | "emoji" | "alphabet" | "hand_tune"
pending = {}  # chat -> список ссылок для «Сдать все автотесты»

BTN_DO = "📝 Решить и сдать"
BTN_SHOW = "👀 Только ответы"
BTN_LIST = "📋 Задания"
BTN_LATE = "❗ Просрочки"
BTN_ALL = "⚡ Автотесты"
BTN_COMM = "💬 Учителя"
BTN_GRADES = "📊 Оценки"
BTN_HAND = "✍️ Почерк"
BTN_CHECK = "🩺 Проверка"
BTN_HELP = "❓ Помощь"
BTN_APP = "✨ Приложение"
BTN_MORE = "⚙️ Ещё"
ALIASES = {  # старые надписи кнопок (клавиатура у Глеба обновится со следующим сообщением)
    "📋 Что надо сделать": BTN_LIST, "⚡ Сдать все автотесты": BTN_ALL, "💬 Комментарии учителя": BTN_COMM,
    "📊 Мои оценки": BTN_GRADES, "✍️ Мой почерк": BTN_HAND,
}
BUTTONS = {BTN_DO, BTN_SHOW, BTN_LIST, BTN_LATE, BTN_ALL, BTN_COMM, BTN_GRADES, BTN_HAND, BTN_CHECK, BTN_HELP,
           BTN_APP, BTN_MORE}


def menu_kb():
    """Клавиатура у поля ввода: главное действие первым, редкое — в «Ещё» (там же просрочки, почерк,
    проверка и помощь). Старые кнопки (❗ Просрочки…) по-прежнему понимаются текстом. Сам бот не пишет (сводку убрали)."""
    b = lambda text, style=None: {"text": text, **({"style": style} if style else {})}
    return json.dumps({
        "keyboard": [
            [b(BTN_DO, "primary"), b(BTN_SHOW)],
            [b(BTN_LIST), b(BTN_GRADES), b(BTN_COMM)],
            [b(BTN_ALL), b(BTN_APP), b(BTN_MORE)],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
        "input_field_placeholder": "Ссылка на задание…",
    }, ensure_ascii=False)


HELP = (  # HTML: отправляется с raw_html=True
    "❓ <b>Что я умею</b>\n"
    "<blockquote>Пришли ссылку на задание — решу и сдам. Ответ учителю сначала покажу черновиком.</blockquote>\n"
    "<blockquote expandable><b>Задания</b> — всё несделанное\n"
    "<b>Оценки</b> — и сколько до следующей\n"
    "<b>Учителя</b> — комментарии и доработки\n"
    "<b>Автотесты</b> — сдам все новые тесты пачкой\n"
    "<b>Голосовое</b> — как текст\n"
    "<b>Фото, скриншот</b> — найду задание и решу; при черновике — фото в него\n"
    "<b>Видео</b> — кадры как фото, речь как текст\n"
    "<b>Ещё</b> — просрочки, почерк, проверка\n"
    "/style — почерк · /update — обновиться · /emoji — премиум-эмодзи</blockquote>\n"
    "<i>Всё то же — в приложении: кнопка «HDP» слева от поля ввода</i>"
)
COMMANDS = [  # меню команд у поля ввода
    ("start", "Главное меню"), ("app", "Открыть приложение"), ("help", "Что я умею"), ("check", "Проверить, что всё работает"),
    ("style", "Настроить почерк"), ("alphabet", "Заполнить алфавит почерка"),
    ("update", "Обновить бота"), ("emoji", "Премиум-эмодзи"),
]
ABOUT = ("Помощник по HDP. Решаю и сдаю задания по ссылке, пишу ответы учителю твоим почерком, "
         "слежу за дедлайнами, комментариями и оценками.")
ABOUT_SHORT = "Решаю задания HDP, слежу за дедлайнами и оценками"
BUSY = "⏳ Занят другим заданием — пришли ещё раз через пару минут"


def esc(x):
    return html.escape(str(x), quote=False)


def card(title, body="", note="", icon=""):
    """Единый вид сообщения (всё уже HTML): «эмодзи <b>Заголовок</b>», содержимое цитатой (длиннее 6 строк —
    свёрнутой), второстепенное — одной строкой курсивом в конце."""
    lines = [f"{icon} <b>{title}</b>" if icon else f"<b>{title}</b>"]
    if body:
        if len(body) > 3000:  # в одно сообщение не влезет: без цитаты, split() режет по абзацам
            lines.append("\n" + body + "\n")
        else:
            tag = "blockquote expandable" if body.count("\n") >= 6 or len(body) > 600 else "blockquote"
            lines.append(f"<{tag}>{body}</blockquote>")
    if note:
        lines.append(f"<i>{note}</i>")
    return "\n".join(lines)


def err(text, note=""):
    """Ошибка — одна понятная строка «⚠️ …» (HTML), под ней при желании приписка курсивом."""
    t = re.sub(r"^\s*(⚠️|⚠|❌|😕)\s*", "", str(text or "")).strip()
    line = next((x.strip() for x in t.splitlines() if x.strip()), "Что-то пошло не так")
    return f"⚠️ {esc(line[:300])}" + (f"\n<i>{note}</i>" if note else "")


def kb(*rows):
    """Инлайн-кнопки: kb([("Текст", "data"), ("Текст", "data", "success")], …). Вместо data можно {"url": …}."""
    out = []
    for row in rows:
        r = []
        for text, act, *style in row:
            btn = {"text": text, **(act if isinstance(act, dict) else {"callback_data": act})}
            if style and style[0]:
                btn["style"] = style[0]
            r.append(btn)
        out.append(r)
    return json.dumps({"inline_keyboard": out}, ensure_ascii=False)


# ---------- Telegram ----------

def tg(method, **params):
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(API + method, data=data, timeout=70) as r:
        return json.load(r)


COLORS = [True]  # Telegram принимает цветные кнопки (поле style)? Один отказ — дальше без цветов


def no_style(markup):
    """reply_markup без поля style (цвет кнопок)."""
    try:
        m = json.loads(markup)
    except (TypeError, ValueError):
        return markup
    for key in ("inline_keyboard", "keyboard"):
        for row in m.get(key) or []:
            for b in row:
                if isinstance(b, dict):
                    b.pop("style", None)
    return json.dumps(m, ensure_ascii=False)


def call(method, **params):
    """tg() для сообщений с кнопками: цветные кнопки — новинка Telegram; если он ответил 400 про кнопки/разметку,
    тот же запрос уходит без цветов (как _with_markup в tgbot/botapi.py)."""
    mk = params.get("reply_markup")
    styled = isinstance(mk, str) and '"style"' in mk
    if styled and not COLORS[0]:
        return tg(method, **{**params, "reply_markup": no_style(mk)})
    try:
        return tg(method, **params)
    except urllib.error.HTTPError as e:
        body = e.read() if hasattr(e, "read") else b""
        why = (body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body)).lower()
        if not (styled and e.code == 400 and any(w in why for w in ("button", "markup", "keyboard", "style"))):
            raise urllib.error.HTTPError(e.url, e.code, e.msg, e.hdrs, io.BytesIO(body or b""))  # тело — дальше
        res = tg(method, **{**params, "reply_markup": no_style(mk)})
        COLORS[0] = False
        print("Цветные кнопки Telegram не принял — дальше без цветов")
        return res


def premium(text, raw_html=False):
    """Обычные эмодзи -> премиум (<tg-emoji>), если их прислали через /emoji. Текст экранируется для HTML,
    если он ещё не HTML (raw_html — уже размечен, например список заданий со ссылками)."""
    if not raw_html:
        text = html.escape(text, quote=False)
    emo = load_settings().get("emoji") or {}
    if not emo:
        return text
    keys = sorted(emo, key=len, reverse=True)
    rx = re.compile("|".join(map(re.escape, keys)))
    return rx.sub(lambda m: f'<tg-emoji emoji-id="{emo[m.group(0)]}">{m.group(0)}</tg-emoji>', text)


def split(text, size=3500):
    """Части не длиннее size (лимит Telegram 4096). Режем между абзацами (по пустой строке), чтобы не разорвать
    цитату или ссылку; слишком длинный абзац — по строкам."""
    blocks = []
    for block in text.split("\n\n"):
        if len(block) <= size:
            blocks.append(block)
            continue
        cur = ""
        for line in block.split("\n"):
            for piece in [line[i:i + size] for i in range(0, len(line), size)] or [""]:
                if cur and len(cur) + len(piece) + 1 > size:
                    blocks.append(cur)
                    cur = piece
                else:
                    cur = f"{cur}\n{piece}" if cur else piece
        blocks.append(cur)
    parts, cur = [], ""
    for block in blocks:
        if cur and len(cur) + len(block) + 2 > size:
            parts.append(cur)
            cur = block
        else:
            cur = f"{cur}\n\n{block}" if cur else block
    return [p for p in parts + [cur] if p.strip()] or ["(пустой ответ)"]


def msg_id(res):
    r = (res or {}).get("result")
    return r.get("message_id") if isinstance(r, dict) else None


def send(chat, text, menu=False, markup=None, raw_html=False, effect=None):
    """Отправляет текст (HTML), возвращает id последнего сообщения. effect — анимация Telegram (EFFECT_*)."""
    parts = split(text.strip() or "(пустой ответ)")
    mid = None
    for k, part in enumerate(parts):
        last = k == len(parts) - 1
        extra = {"reply_markup": markup or menu_kb()} if last and (menu or markup) else {}
        plain = html.unescape(re.sub(r"<[^>]+>", "", part)) if raw_html else part
        tries = [dict(text=premium(part, raw_html), parse_mode="HTML", **extra)]
        if effect and last:  # эффект не принят (старый клиент, неверный id) — то же самое без него
            tries.insert(0, dict(tries[0], message_effect_id=effect))
        tries.append(dict(text=plain, **extra))  # Telegram не принял разметку или эмодзи — простым текстом
        for n, t in enumerate(tries):
            try:
                mid = msg_id(call("sendMessage", chat_id=chat, disable_web_page_preview="true", **t))
                break
            except urllib.error.HTTPError as e:
                print("sendMessage:", e)
                if n == len(tries) - 1:
                    raise
    return mid


def quiet(method, **params):
    """Вызов Telegram, ошибка которого не важна (правка, удаление, реакция)."""
    try:
        return call(method, **params)
    except Exception as e:
        if "not modified" not in str(getattr(e, "read", lambda: b"")() or e):
            print(method, e)


def edit(chat, mid, text, markup=None):
    if mid:
        quiet("editMessageText", chat_id=chat, message_id=mid, text=premium(text, True), parse_mode="HTML",
              disable_web_page_preview="true", **({"reply_markup": markup} if markup else {}))


def delete(chat, mid):
    if mid:
        quiet("deleteMessage", chat_id=chat, message_id=mid)


def react(chat, mid, emoji):
    """Реакция бота на сообщение Глеба (только стандартные: 👀 👍 🔥 🏆 🤔 ✍ 🎉 …)."""
    if mid:
        quiet("setMessageReaction", chat_id=chat, message_id=mid,
              reaction=json.dumps([{"type": "emoji", "emoji": emoji}] if emoji else [], ensure_ascii=False))


EFFECT_CONFETTI = "5046509860389126442"  # 🎉 эффекты сообщений в личном чате
EFFECT_LIKE = "5107584321108051014"      # 👍
EFFECT_FIRE = "5104841245755180586"      # 🔥
SPIN = "◐◓◑◒"
REAL_THREAD = threading.Thread  # тикер живой карточки — всегда настоящий поток (в тестах Thread подменяют)


def bar(frac, n=10):
    full = max(0, min(n, round(frac * n)))
    return "▰" * full + "▱" * (n - full)


def mmss(sec):
    sec = int(sec)
    return f"{sec // 60}:{sec % 60:02d}"


class Live:
    """Одно сообщение, которое обновляется на месте: заголовок, пройденные шаги, полоска, крутилка и таймер.
    Вместо пачки «⏳ …» — одна живая карточка; когда работа сделана, она исчезает, и приходит результат."""

    def __init__(self, chat, title, sub="", progress=True, sub_fn=None):
        self.chat, self.title, self.sub, self.progress, self.sub_fn = chat, title, sub, progress, sub_fn
        self.done_steps, self.current, self.frac = [], "", 0.0
        self.t0, self.tick = time.time(), 0
        self.stop = threading.Event()
        Live.active = self  # приложение показывает ту же работу, что и карточка в чате
        self.mid = send(chat, self.render(), raw_html=True)
        quiet("sendChatAction", chat_id=chat, action="typing")
        REAL_THREAD(target=self.loop, daemon=True).start()

    def render(self):
        """◐ <b>Решаю задание</b> / цитата: название и шаги (✓ сделано, ▸ сейчас) / полоска, проценты, время."""
        head = f"{SPIN[self.tick % len(SPIN)]} <b>{self.title}</b>"
        clock = mmss(time.time() - self.t0)
        steps = [f"✓ {s}" for s in self.done_steps[-3:]] + ([f"▸ <b>{self.current}</b>"] if self.current else [])
        foot = f"<code>{bar(self.frac)}</code> <i>{int(self.frac * 100)}% · {clock}</i>" if self.progress else ""
        if not steps and not self.progress:  # просто ожидание: подпись и время одной строкой
            return f"{head}\n<i>{self.sub + ' · ' if self.sub else ''}{clock}</i>"
        body = [self.sub] if self.sub else []
        body += steps
        lines = [head] + ([f"<blockquote>{chr(10).join(body)}</blockquote>"] if body else [])
        lines.append(foot or f"<i>{clock}</i>")
        return "\n".join(lines)

    def step(self, label, frac=None):
        if label and label != self.current:
            if self.current:
                self.done_steps.append(self.current)
            self.current = label
        if frac is not None:
            self.frac = max(self.frac, min(frac, 0.99))
        self.refresh()

    def tool(self, name, inp):
        """Вызов инструмента Claude -> шаг на карточке (см. STEPS)."""
        st = step_of(name, inp)
        if st:
            self.step(*st)

    def reset(self, sub=None, frac=None):
        """Следующий элемент пачки (тест 2 из 5): шаги заново, общая полоска продолжается."""
        self.done_steps, self.current = [], ""
        if sub is not None:
            self.sub = sub
        if frac is not None:
            self.frac = frac
        self.refresh()

    def refresh(self):
        if self.sub_fn and not self.sub:
            self.sub = self.sub_fn() or ""
        edit(self.chat, self.mid, self.render())

    def loop(self):
        while not self.stop.wait(3):
            self.tick += 1
            self.refresh()
            if self.tick % 2 == 0:
                quiet("sendChatAction", chat_id=self.chat, action="typing")

    def done(self):
        self.stop.set()
        if Live.active is self:
            Live.active = None
        delete(self.chat, self.mid)

    def state(self):
        """Для приложения: то же, что на карточке, без HTML."""
        return {"title": plain(self.title), "sub": plain(self.sub), "step": plain(self.current),
                "done": [plain(x) for x in self.done_steps[-3:]], "frac": self.frac if self.progress else None,
                "secs": int(self.seconds())}

    def seconds(self):
        return time.time() - self.t0


Live.active = None


def plain(x):
    """HTML бота -> обычный текст (для приложения)."""
    return html.unescape(re.sub(r"<[^>]+>", "", str(x or ""))).strip()


def send_file(chat, path, caption=""):
    """Картинку — фото, остальное (и если фото не приняли) — файлом."""
    with open(path, "rb") as f:
        data = f.read()
    kinds = ([("sendPhoto", "photo")] if drafts.IMAGE.search(path) and not path.lower().endswith(".pdf") else []) + [("sendDocument", "document")]
    for method, field in kinds:
        b = uuid.uuid4().hex
        body = b""
        for k, v in {"chat_id": str(chat), "caption": caption}.items():
            body += f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
        body += (f'--{b}\r\nContent-Disposition: form-data; name="{field}"; filename="{os.path.basename(path)}"\r\n'
                 "Content-Type: application/octet-stream\r\n\r\n").encode() + data + f"\r\n--{b}--\r\n".encode()
        req = urllib.request.Request(API + method, data=body, headers={"Content-Type": f"multipart/form-data; boundary={b}"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            print(method, path, e)
    return None


def download(file_id):
    """Скачивает файл, присланный в Telegram: (байты, имя)."""
    path = tg("getFile", file_id=file_id)["result"]["file_path"]
    with urllib.request.urlopen(f"https://api.telegram.org/file/bot{TOKEN}/{path}", timeout=120) as r:
        return r.read(), os.path.basename(path)


def attachment(msg):
    """file_id картинки или файла из сообщения (фото — в самом большом размере)."""
    if msg.get("photo"):
        return msg["photo"][-1]["file_id"]
    if msg.get("document"):
        return msg["document"]["file_id"]
    return None


def utf16(text, off, length):
    """Кусок текста по смещениям Telegram (они в UTF-16)."""
    b = text.encode("utf-16-le")
    return b[off * 2:(off + length) * 2].decode("utf-16-le")


def save_emoji(chat, msg):
    """Запоминает премиум-эмодзи из сообщения: обычный эмодзи -> id премиального."""
    text = msg.get("text") or ""
    found = {}
    for e in msg.get("entities") or []:
        if e.get("type") == "custom_emoji":
            alt = utf16(text, e["offset"], e["length"])
            found[alt] = e["custom_emoji_id"]
            found[alt.replace("\ufe0f", "")] = e["custom_emoji_id"]  # и без невидимого вариационного знака
    if not found:
        send(chat, err("Премиум-эмодзи в сообщении не нашёл", "Пришли именно премиум-эмодзи из набора Telegram Premium"),
             menu=True, raw_html=True)
        return
    s = load_settings()
    s.setdefault("emoji", {}).update(found)
    save_settings(s)
    shown = sorted({a for a in found if "\ufe0f" not in a} | {a for a in found if a.replace("\ufe0f", "") not in found})
    send(chat, card("Запомнил премиум-эмодзи", esc(" ".join(shown)), "Сбросить все: /emoji_reset", "✨"),
         menu=True, raw_html=True)


# ---------- Настройки ----------

def load_settings():
    try:
        with open(SETTINGS) as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(s):
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    with open(SETTINGS, "w") as f:
        json.dump(s, f)


def owner():
    return ALLOWED or int(load_settings().get("owner") or 0)


# ---------- Работа ----------

def node(*args, timeout=300, prog="node"):
    try:
        res = subprocess.run([prog, *args], cwd=REPO, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "⚠️ Сайт не ответил вовремя, попробуй позже."
    if res.returncode != 0:
        print(prog, *args, "->", res.returncode, (res.stderr or res.stdout)[-3000:])  # подробности — в лог pm2
        return False, explain(res.stderr or res.stdout)
    return True, res.stdout


def explain(err):
    """Ошибку скрипта — в одну понятную строку, без трассировки и кодов."""
    low = err.lower()
    if "timeout" in low or "timed out" in low:
        return "⚠️ Сайт долго не отвечает, попробуй через пару минут."
    if "login" in low or "логин" in low or "/auth" in low:
        return "⚠️ Не получилось войти на сайт. Проверь логин и пароль HORO на сервере."
    lines = [l.strip() for l in err.strip().splitlines() if l.strip() and not l.strip().startswith(("at ", "File ", "Traceback"))]
    last = lines[-1] if lines else "неизвестная ошибка"
    return "⚠️ Не получилось: " + re.sub(r"^(Error|ОШИБКА):\s*", "", last)[:200]


def py(*args, timeout=300):
    return node(*args, timeout=timeout, prog="python3")


def claude(prompt, tools, timeout=TIMEOUT, on_tool=None):
    """Запуск Claude Code без диалога: (успех, текст ответа). on_tool(имя, параметры) зовётся на каждый
    вызов инструмента — по нему живая карточка показывает, что Claude сейчас делает."""
    cmd = ["claude", "-p", prompt, "--permission-mode", "acceptEdits", "--output-format", "stream-json", "--verbose"] \
        + (["--allowedTools", *tools] if tools else [])
    try:
        proc = subprocess.Popen(cmd, cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    except FileNotFoundError:
        return False, "⚠️ Claude Code не установлен (npm install -g @anthropic-ai/claude-code)."
    late, finished = threading.Event(), threading.Event()

    def watchdog():
        if not finished.wait(timeout):
            late.set()
            proc.kill()

    REAL_THREAD(target=watchdog, daemon=True).start()
    result, other = None, []
    try:
        for line in proc.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                other.append(line)
                continue
            if ev.get("type") == "result":
                result = ev
            elif ev.get("type") == "assistant" and on_tool:
                for c in (ev.get("message") or {}).get("content") or []:
                    if c.get("type") == "tool_use":
                        try:
                            on_tool(c.get("name") or "", c.get("input") or {})
                        except Exception as e:
                            print("on_tool:", e)
        proc.wait()
    finally:
        finished.set()
        proc.stdout.close()
    if late.is_set():
        return False, f"⚠️ Не успел за {timeout // 60} минут, остановил."
    if proc.returncode != 0 or not result or result.get("is_error"):
        log = "".join(other)[-3000:] + (json.dumps(result, ensure_ascii=False)[-2000:] if result else "")
        print("claude ->", proc.returncode, log)  # подробности — в лог pm2
        low = log.lower()
        if any(w in low for w in ("login", "api key", "401", "unauthorized", "authenticat")):
            return False, "⚠️ Claude на сервере не вошёл в аккаунт. Напиши мне (Claude Code), я войду заново."
        if any(w in low for w in ("rate limit", "usage limit", "limit reached", "429", "overloaded")):
            return False, "⚠️ У Claude закончился лимит или он перегружен. Попробуй через час."
        return False, "⚠️ Claude не справился с заданием. Попробуй ещё раз."
    return True, result.get("result") or ""


STEPS = [  # (что в команде или пути, подпись шага, доля готовности)
    ("submit.js", "Сдаю на сайт", 0.88),
    ("answer.js", "Отправляю учителю", 0.9),
    ("fetch.js", "Читаю условие", 0.2),
    ("pdftoppm", "Листаю рабочий лист", 0.3),
    ("pdftotext", "Листаю рабочий лист", 0.3),
    ("shot.js", "Снимаю задание", 0.45),
    ("answers.json", "Записываю ответы", 0.6),
    ("draft.txt", "Пишу ответ учителю", 0.6),
    ("draw.py", "Пишу от руки", 0.75),
]


def step_of(name, inp):
    """Вызов инструмента Claude -> (подпись, доля) для живой карточки или None."""
    what = str(inp.get("command") or inp.get("file_path") or "")
    for key, label, frac in STEPS:
        if key in what:
            return label, frac
    if name == "Read" and drafts.IMAGE.search(what):
        return "Разглядываю картинки", 0.3
    if name == "Read":
        return "Изучаю материалы", 0.25
    return None


LOADING = {"--late": "Ищу просрочки", "horo/comments.js": "Читаю комментарии учителей", "horo/grades.js": "Считаю оценки"}


def run_script(chat, *args):
    if not lock.acquire(blocking=False):
        send(chat, BUSY)
        return
    try:
        title = next((v for k, v in LOADING.items() if k in args), "Собираю задания")
        live = Live(chat, title, "смотрю сайт HDP", progress=False)
        try:
            ok, out = node(*args)
        finally:
            live.done()
        if ok:
            send(chat, out, menu=True, raw_html="--html" in args)
        else:
            send(chat, err(out), menu=True, raw_html=True)
    finally:
        lock.release()


SOLVE_TOOLS = ["Bash(node horo/*)", "Bash(python3 horo/*)", "Bash(pdftoppm *)", "Bash(pdftotext *)", "Read", "Write", "Edit"]


def prompt_for(url, text, show):
    only_show = show or re.search(r"покажи|не отправляй|только ответ", text, re.I)
    how = (
        "Только реши и покажи ответы, на сайт НЕ отправляй (шаг 3 не делать)."
        if only_show
        else "Реши и отправь (шаг 3). Если ответ неоднозначный, а попытка последняя, не отправляй, а напиши варианты."
    )
    task = drafts.task_id(url)
    d = f"horo/tmp/{task}"
    return (
        f"Задание: {url}\n"
        f"Сделай его по инструкции из CLAUDE.md. {how}\n"
        f"Если это detailedAnswer, на сайт НЕ отправляй. Сделай черновик: текст для учителя запиши в {d}/draft.txt "
        "(каждая строка — абзац, только то, что пойдёт учителю). Если нужны картинки (решение от руки на скриншоте, "
        f"заполненный лист), сделай их по CLAUDE.md и положи в {d}/draft_files/. Скрипт рисования клади в {d}/ и запускай "
        f"`python3 {d}/draw.py` (в нём: import sys; sys.path.insert(0, 'horo'); from hand import Hand). "
        + ("" if os.path.exists(GLEB_EXTRA) else
           "Алфавита почерка Глеба ещё нет. Если для задания нужно решение от руки на скриншоте, ничего не рисуй "
           "и не отправляй, а ответь ровно одним словом НУЖЕН_АЛФАВИТ.\n")
        + f"Сначала прочитай {os.path.relpath(drafts.STYLE, REPO)}, если он есть: это правила Глеба по его прошлым правкам, "
        "они важнее общих. Бот сам покажет черновик Глебу: в ответе текст черновика не повторяй, напиши 1–2 строки, что сделал.\n"
        "Ответ пиши для Telegram, коротко, без markdown-таблиц: что написал по каждому вопросу. Если работа сдана и сайт "
        "показал результат, самой первой строкой напиши его в виде «БАЛЛЫ: 6/7» (или «БАЛЛЫ: 85%»), без других слов."
    )


def solve(url, text="", show=False, on_tool=None):
    """(успех, ответ Claude)."""
    return claude(prompt_for(url, text, show), SOLVE_TOOLS, on_tool=on_tool)


SCORE = re.compile(r"^[ \t*]*БАЛЛЫ:\s*(\d+(?:[.,]\d+)?)\s*(?:/\s*(\d+(?:[.,]\d+)?)|%)[^\n]*\n?", re.M | re.I)


def parse_score(out):
    """«БАЛЛЫ: 6/7» или «БАЛЛЫ: 85%» из ответа Claude -> ((набрано, из, процент) или None, текст без этой строки)."""
    m = SCORE.search(out)
    if not m:
        return None, out.strip()
    got, total = float(m.group(1).replace(",", ".")), m.group(2)
    total = float(total.replace(",", ".")) if total else None
    pct = round(got / total * 100) if total else round(got)
    return (got, total, pct), (out[:m.start()] + out[m.end():]).strip()


def md(text):
    """Ответ Claude (markdown) -> HTML Telegram: **жирный**, `код`, заголовки; ``` убираем, остальное экранируем."""
    t = esc(re.sub(r"^```\w*\n?|\n?```$", "", text.strip(), flags=re.M))
    t = re.sub(r"^#{1,6}\s*(.+)$", r"<b>\1</b>", t, flags=re.M)
    t = re.sub(r"\*\*([^*\n]+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", t)
    return re.sub(r"^[ \t]*[-*] ", "• ", t, flags=re.M)


def num(x):
    return str(int(x)) if x == int(x) else str(x).replace(".", ",")


def result_card(out, title="", secs=0, show=False):
    """Карточка результата: счёт в заголовке, ответ Claude цитатой, название и время — приписка. -> (текст, полный балл?)"""
    score, body = parse_score(out)
    full = bool(score) and score[2] >= 100
    if score:
        got, total, pct = score
        mark = "🏆" if full else "✅" if pct >= 70 else "📊"
        head = f"{mark} <b>{num(got)} / {num(total)}</b> · {pct}%" if total else f"{mark} <b>{pct}%</b>"
    else:
        head = "👀 <b>Ответы</b>" if show else "✅ <b>Готово</b>"
    lines = [head]
    if body:
        text = md(body)
        if len(body) >= 3000:
            lines += ["", text, ""]
        else:
            quote = "blockquote expandable" if len(body) > 600 or body.count("\n") >= 6 else "blockquote"
            lines.append(f"<{quote}>{text}</blockquote>")
    note = " · ".join(x for x in (esc(title), mmss(secs) if secs else "", "на сайт не отправлял" if show else "") if x)
    if note:
        lines.append(f"<i>{note}</i>")
    return "\n".join(lines), full


def run_task(chat, url, text, show=False, mid=None):
    if not lock.acquire(blocking=False):
        send(chat, BUSY)
        return
    try:
        task, t0 = drafts.task_id(url), time.time()
        react(chat, mid, "👀")
        drafts.clear(task)
        live = Live(chat, "Смотрю ответы" if show else "Решаю задание", esc(drafts.task_info(task)[1]),
                    sub_fn=lambda: esc(drafts.task_info(task)[1]))
        live.step("Запускаю Claude", 0.05)
        try:
            ok, out = solve(url, text, show, on_tool=live.tool)
        finally:
            live.done()
        title = drafts.task_info(task)[1]
        if "НУЖЕН_АЛФАВИТ" in out:
            drafts.clear(task)
            react(chat, mid, "✍")
            send(chat, card("Нужен твой почерк", "Это задание решается от руки, а алфавита у меня ещё нет. "
                            "Заполни 4 листа — и пришли ссылку на задание снова.", icon="✍️"),
                 markup=kb([("✍️ Заполнить алфавит", "m:hand", "primary")]), raw_html=True)
            return
        if not ok:
            react(chat, mid, "🤔")
            remember(title or "Задание", False, plain(out), url=url, show=show, secs=time.time() - t0)
            send(chat, err(out, esc(title)), menu=True, raw_html=True)
            return
        result, full = result_card(out, title, time.time() - t0, show)
        has_draft = drafts.fresh(task, t0)
        score, body = parse_score(out)
        remember(title or "Задание", True, body, score=score, url=url, show=show, secs=time.time() - t0, draft=has_draft)
        react(chat, mid, "🏆" if full else "✍" if has_draft else "👍")
        send(chat, result, menu=True, raw_html=True, effect=EFFECT_CONFETTI if full else None)
        if has_draft:
            offer_draft(chat, task, url)
    finally:
        lock.release()
        if not show:
            threading.Thread(target=refresh_overview, daemon=True).start()  # список заданий в приложении


# ---------- Черновики для учителя ----------

DRAFT_KB = kb([("✅ Отправить учителю", "send", "success")], [("Не отправлять", "drop")])  # главное — во всю ширину


def draft():
    return load_settings().get("draft")


def set_draft(d):
    s = load_settings()
    if d:
        s["draft"] = d
    else:
        s.pop("draft", None)
    save_settings(s)


def offer_draft(chat, task, url):
    title = drafts.task_info(task)[1]
    set_draft({"task": task, "url": url, "title": title, "pairs": [], "own_files": False})
    for f in drafts.files(task):
        send_file(chat, f)
    show_draft(chat)


def show_draft(chat):
    d = draft()
    text = drafts.read(d["task"])
    n = len(drafts.files(d["task"]))
    note = " · ".join(x for x in (esc(d["title"]), f"{n} {plural(n, 'картинка', 'картинки', 'картинок')} выше" if n else "",
                                  "поправить — пришли текст, просьбу или фото") if x)
    send(chat, card("Черновик для учителя", esc(text) if text else "<i>(без текста)</i>", note, "📄"),
         markup=DRAFT_KB, raw_html=True)


def plural(n, one, few, many):
    return one if n % 10 == 1 and n % 100 != 11 else few if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else many


def edit_draft(chat, text):
    """Глеб прислал готовый текст или просьбу («убери последний абзац»): просьбу применяет Claude."""
    d = draft()
    before = drafts.read(d["task"])
    if len(text) < 0.6 * len(before):
        if not lock.acquire(blocking=False):
            send(chat, BUSY)
            return
        try:
            live = Live(chat, "Правлю черновик", esc(text[:80]), progress=False)
            path = os.path.relpath(drafts.text_path(d["task"]), REPO)
            try:
                ok, out = claude(
                    f"В {path} черновик ответа учителю. Глеб написал про него: «{text}».\n"
                    f"Если это готовый новый текст ответа, запиши его в {path} как есть. Если это просьба что-то изменить, "
                    f"измени {path} по ней и больше ничего не трогай. Каждая строка файла — абзац. В ответе одна строка: что сделал.",
                    ["Read", "Write", "Edit"], timeout=300)
            finally:
                live.done()
        finally:
            lock.release()
        if not ok:
            send(chat, err(out, "Черновик не изменился — можно поправить ещё раз"), markup=DRAFT_KB, raw_html=True)
            return
    else:
        drafts.write(d["task"], text)
    after = drafts.read(d["task"])
    if drafts.record_edit(d["task"], d["title"], before, after):
        d["pairs"].append([before, after, text])
        set_draft(d)
    show_draft(chat)


def add_draft_photo(chat, msg):
    d = draft()
    data, name = download(attachment(msg))
    drafts.add_file(d["task"], data, name, replace=not d["own_files"])  # первое своё фото заменяет мои картинки
    d["own_files"] = True
    set_draft(d)
    n = len(drafts.files(d["task"]))
    send(chat, card("Фото добавил", note=f"Всего {n} {plural(n, 'картинка', 'картинки', 'картинок')}. "
                                          "Когда всё — «Отправить учителю»", icon="🖼"), markup=DRAFT_KB, raw_html=True)


def submit_draft(chat):
    if not lock.acquire(blocking=False):
        send(chat, BUSY, markup=DRAFT_KB)
        return
    try:
        d = draft()
        if not d:
            send(chat, "⚠️ Черновика уже нет", menu=True)
            return
        if not os.path.exists(drafts.text_path(d["task"])):
            drafts.write(d["task"], "")
        live = Live(chat, "Отправляю учителю", esc(d["title"]), progress=False)
        try:
            ok, out = node("horo/answer.js", d["url"], drafts.text_path(d["task"]), *drafts.files(d["task"]), timeout=600)
        finally:
            live.done()
        remember(d["title"], ok, "Отправлено учителю" if ok else plain(out), url=d["url"], kind="sent")
        if not ok:
            send(chat, err(out, "Не отправилось. Черновик остался — можно нажать ещё раз"), markup=DRAFT_KB, raw_html=True)
            return
        send(chat, card("Отправлено учителю", esc(out.strip()), esc(d["title"]), "📬"),
             menu=True, raw_html=True, effect=EFFECT_LIKE)
        set_draft(None)
        if d["pairs"]:
            live = Live(chat, "Запоминаю твои правки", progress=False)
            try:
                learned = learn_style(d["pairs"])
            finally:
                live.done()
            send(chat, learned, menu=True, raw_html=True)
    finally:
        lock.release()


def learn_style(pairs):
    """Обобщает правки Глеба в правила (horo/tmp/style/style.md) для следующих ответов."""
    os.makedirs(drafts.STYLE_DIR, exist_ok=True)
    shown = "\n\n".join(f"БЫЛО:\n{p[0]}\n\nСТАЛО:\n{p[1]}" + (f"\n\nЧТО ОН НАПИСАЛ: {p[2]}" if len(p) > 2 and p[2] != p[1] else "")
                          for p in pairs)
    style = os.path.relpath(drafts.STYLE, REPO)
    ok, out = claude(
        "Глеб (ученик 8 класса) поправил черновик ответа учителю.\n\n" + shown + "\n\n"
        f"Обнови файл {style} (создай, если нет): короткий список правил, как Глеб хочет, чтобы были написаны ответы "
        "учителю (длина, тон, слова, оформление, чего избегать). Обобщай, не записывай содержание конкретного задания. "
        "Старые правила сохраняй, противоречащие заменяй новыми, не больше 30 пунктов. "
        "В ответе только новые или изменённые пункты, коротко, без markdown.",
        ["Read", "Write", "Edit"], timeout=300)
    return (card("Запомнил, как ты пишешь", esc(out.strip()), "Учту в следующих ответах учителю", "🧠") if ok
            else err("Правки сохранил, но обобщить не вышло"))


def on_callback(cq):
    chat = cq["message"]["chat"]["id"]
    mid = cq["message"].get("message_id")
    data = cq.get("data") or ""
    if data.startswith("m:"):  # меню «Ещё»: правится на месте
        on_more(chat, mid, cq["id"], data[2:])
        return
    if data.startswith("hand:"):
        try:
            tg("answerCallbackQuery", callback_query_id=cq["id"])
        except urllib.error.HTTPError as e:
            print("callback:", e)
        _, key, *sign = data.split(":")
        quiet("editMessageReplyMarkup", chat_id=chat, message_id=mid,  # кнопки остаются только под новым примером
              reply_markup=json.dumps({"inline_keyboard": []}))
        if key == "ok":
            mode.pop(chat, None)
            send(chat, HAND_OK, menu=True, raw_html=True)
        else:
            adjust_hand(key, int(sign[0]))
            threading.Thread(target=send_sample, args=(chat, "Перерисовал"), daemon=True).start()
        return
    try:
        tg("answerCallbackQuery", callback_query_id=cq["id"])
        tg("editMessageReplyMarkup", chat_id=chat, message_id=mid,  # убрать кнопки: не нажать дважды
           reply_markup=json.dumps({"inline_keyboard": []}))
    except urllib.error.HTTPError as e:
        print("callback:", e)
    if data.startswith("all:"):
        mode.pop(chat, None)
        rows = pending.pop(chat, [])
        if data == "all:yes" and rows:
            threading.Thread(target=run_all, args=(chat, rows), daemon=True).start()
        else:
            edit(chat, mid, card("Автотесты не сдаю", note=f"Передумаешь — кнопка «{BTN_ALL}»", icon="👌"))
        return
    if data.startswith("ph:"):  # задание, найденное на фото
        on_photo_button(chat, data[3:])
        return
    if not draft():
        send(chat, "⚠️ Этот черновик уже неактуален", menu=True)
    elif data == "send":
        threading.Thread(target=submit_draft, args=(chat,), daemon=True).start()
    elif data == "drop":
        set_draft(None)
        send(chat, card("Не отправляю", note="Передумаешь — пришли ссылку ещё раз", icon="👌"), menu=True, raw_html=True)


# ---------- Меню «Ещё»: редкие действия, правится на месте ----------

def more_text():
    return card("Ещё", note="Сам я не пишу — только отвечаю", icon="⚙️")


def more_kb():
    rows = [
        [("❗ Просрочки", "m:late")],
        [("✍️ Почерк", "m:hand"), ("🩺 Проверка", "m:check")],
        [("❓ Помощь", "m:help")] + ([("✨ Приложение", {"web_app": {"url": APP_URL + "/"}})] if APP_URL else []),
    ]
    return kb(*rows)


def on_more(chat, mid, cq_id, key):
    quiet("answerCallbackQuery", callback_query_id=cq_id)
    if key in ("more", "digest"):  # digest — старая кнопка сводки (сводки больше нет)
        edit(chat, mid, more_text(), more_kb())
    elif key == "help":
        edit(chat, mid, HELP, kb([("‹ Назад", "m:more")]))
    elif key == "late":
        threading.Thread(target=run_script, args=(chat, "horo/status.js", "--late", "--html"), daemon=True).start()
    elif key == "hand":
        threading.Thread(target=send_alphabet, args=(chat,), daemon=True).start()
    elif key == "check":
        threading.Thread(target=check, args=(chat,), daemon=True).start()


# ---------- Почерк ----------

ALPHA_DIR = os.path.join(REPO, "horo", "tmp", "alphabet")
GLEB_EXTRA = os.path.join(REPO, "horo", "tmp", "fonts", "gleb_extra.npz")  # буквы из его алфавита


def send_alphabet(chat):
    os.makedirs(ALPHA_DIR, exist_ok=True)
    send(chat, card("Алфавит твоим почерком",
                    "1. Сохрани 4 листа ниже: русские буквы (2 листа), латиница, цифры и знаки.\n"
                    "2. На iPad открой лист в «Фото» → Править → Разметка.\n"
                    "3. Пиши синим или белым на линии: слева большая буква, справа маленькая. Пустые клетки можно оставить.\n"
                    "4. Не обрезай лист: розовые квадраты по углам должны быть видны.",
                    "Пришли листы сюда, потом напиши «готово»", "✍️"), raw_html=True)
    for page in (1, 2, 3, 4):
        out = os.path.join(ALPHA_DIR, f"alphabet_{page}.png")
        ok, why = py("horo/alphabet.py", "template", str(page), out)
        if not ok:
            send(chat, err(why), menu=True, raw_html=True)
            return
        send_file(chat, out)
    mode[chat] = "alphabet"


# ---------- Стиль почерка: пример решения и настройка кнопками или словами ----------

HAND_STYLE = os.path.join(REPO, "horo", "tmp", "hand_style.json")  # его читает horo/hand.py
HAND_STEPS = {  # ключ: (шаг, минимум, максимум, по умолчанию)
    "mess": (0.25, 0.25, 2.5, 1.0),
    "width": (0.2, 0.4, 2.6, 1.0),
    "size": (0.1, 0.6, 1.6, 1.0),
    "slant": (0.05, -0.2, 0.3, 0.0),
}
HAND_WORDS = [  # (слова в просьбе, что менять, куда)
    (("небреж", "размаш", "корявее", "быстрее"), "mess", +1),
    (("аккурат", "ровнее", "красивее", "чище"), "mess", -1),
    (("толщ", "жирн"), "width", +1),
    (("тоньш",), "width", -1),
    (("крупн", "больше"), "size", +1),
    (("мельч", "меньше"), "size", -1),
    (("наклон",), "slant", +1),
    (("прям",), "slant", -1),
]
HAND_KB = kb(
    [("Небрежнее", "hand:mess:1"), ("Аккуратнее", "hand:mess:-1")],
    [("Толще", "hand:width:1"), ("Тоньше", "hand:width:-1")],
    [("Крупнее", "hand:size:1"), ("Мельче", "hand:size:-1")],
    [("✅ Нравится", "hand:ok", "success")],
)
HAND_OK = card("Запомнил стиль", note="Задания «от руки» теперь пишу так", icon="✅")


def hand_style():
    try:
        with open(HAND_STYLE) as f:
            return json.load(f)
    except Exception:
        return {}


def adjust_hand(key, sign, half=False):
    step, lo, hi, default = HAND_STEPS[key]
    st = hand_style()
    st[key] = round(min(hi, max(lo, st.get(key, default) + sign * step * (0.5 if half else 1))), 3)
    os.makedirs(os.path.dirname(HAND_STYLE), exist_ok=True)
    with open(HAND_STYLE, "w") as f:
        json.dump(st, f)


def send_sample(chat, note=""):
    """Пример решённого задания его почерком с кнопками настройки. note — заголовок («Перерисовал»)."""
    out = os.path.join(ALPHA_DIR, "sample.png")
    os.makedirs(ALPHA_DIR, exist_ok=True)
    ok, why = py("horo/alphabet.py", "sample", out)
    if not ok:
        send(chat, err(why), menu=True, raw_html=True)
        return
    mode[chat] = "hand_tune"
    send_file(chat, out)
    send(chat, card(note or "Так буду писать от руки",
                    "Поправь кнопками или словами: «чуть небрежнее», «тоньше», «крупнее», «с наклоном» — перерисую.",
                    "Нравится — жми «Нравится» или напиши «ок»", "✍️"), markup=HAND_KB, raw_html=True)


def tune_hand(chat, text):
    """Просьба словами: «чуть небрежнее и тоньше» -> меняет стиль и присылает новый пример."""
    low = text.lower()
    if low.strip(".! ") in ("ок", "ok", "да", "нравится", "подтверждаю", "готово", "отлично", "норм"):
        mode.pop(chat, None)
        send(chat, HAND_OK, menu=True, raw_html=True)
        return
    half = any(w in low for w in ("чуть", "немного", "слегка"))
    changed = [(key, sign) for words, key, sign in HAND_WORDS if any(w in low for w in words)]
    if not changed:
        send(chat, card("Не понял, что поменять", note="Можно: небрежнее, аккуратнее, толще, тоньше, крупнее, мельче, "
                                                       "с наклоном, прямее — или «ок»", icon="🤔"),
             markup=HAND_KB, raw_html=True)
        return
    for key, sign in changed:
        adjust_hand(key, sign, half)
    send_sample(chat, "Перерисовал")


def take_alphabet(chat, msg):
    if not lock.acquire(blocking=False):
        send(chat, BUSY)
        return
    try:
        data, name = download(attachment(msg))
        path = os.path.join(ALPHA_DIR, f"in_{int(time.time() * 1000)}_{name}")
        with open(path, "wb") as f:
            f.write(data)
        ok, out = py("horo/alphabet.py", "ingest", path)
        lines = out.strip().splitlines()
        added = next((l.split(":", 1)[1].split() for l in lines if l.startswith("Добавил:")), [])
        added = [] if added == ["ничего"] else added
        errors = [l.split(":", 1)[1].strip() for l in lines if l.startswith("Ошибка:")]
        if added:
            send(chat, card(f"Добавил знаков: {len(added)}", esc(" ".join(added)), icon="✅"), raw_html=True)
        for e in errors:
            send(chat, err(f"Лист не разобрал: {e}"), raw_html=True)
        if not ok and not errors:
            send(chat, err(out), raw_html=True)  # непонятная ошибка — первой строкой
        if added:
            prev = os.path.join(ALPHA_DIR, "preview.png")
            if py("horo/alphabet.py", "preview", prev)[0]:
                send_file(chat, prev, "Так теперь пишу твоим почерком. Пришли ещё лист или напиши «готово».")
    finally:
        lock.release()


# ---------- Проверка ----------

def check(chat):
    if not lock.acquire(blocking=False):
        send(chat, BUSY)
        return
    try:
        live = Live(chat, "Проверяю, что всё работает", "до минуты")
        git = lambda *a: subprocess.run(["git", *a], cwd=REPO, capture_output=True, text=True).stdout.strip()
        rows, bad = [], 0

        def timed(label, frac, fn):
            live.step(label, frac)
            t = time.time()
            res = fn()
            return res, f"{time.time() - t:.1f} с"

        try:
            (ok, out), t = timed("Захожу на сайт", 0.1, lambda: node("horo/grades.js", timeout=180))
            rows.append(f"✅ <b>Сайт</b> · вход за {t}" if ok else "❌ <b>Сайт</b> · " + esc(out.strip()[-300:]))
            bad += not ok
            (ok, out), t = timed("Спрашиваю Claude", 0.5, lambda: claude("Ответь одним словом: ok", [], timeout=120))
            ok = ok and bool(out.strip())
            rows.append(f"✅ <b>Claude</b> · ответил за {t}" if ok else "❌ <b>Claude</b> · " + esc(out.strip()[-300:]))
            bad += not ok
            (ok, _), t = timed("Проверяю почерк", 0.9, lambda: py("-c", "import numpy, scipy, skimage, PIL"))
            rows.append("✅ <b>Почерк</b> · библиотеки на месте" if ok else "❌ <b>Почерк</b> · нет библиотек, нужна переустановка (install.sh)")
            bad += not ok
        finally:
            live.done()
        rows.append("✅ <b>Алфавит</b> · заполнен" if os.path.exists(GLEB_EXTRA) else "✍️ <b>Алфавит</b> · ещё не заполнен («Ещё» → «Почерк»)")
        rows.append("✅ <b>Голосовые</b> · распознавание на месте" if media.whisper_python()
                    else "🎙 <b>Голосовые</b> · распознавание не установлено (common/setup.sh)")
        when = git("log", "-1", "--format=%cd", "--date=format:%d.%m %H:%M")
        send(chat, card("Всё работает" if not bad else f"Проверка: проблем {bad}", "\n".join(rows),
                        f"Версия {esc(git('rev-parse', '--short', 'HEAD'))} от {esc(when)}", "🩺"), menu=True, raw_html=True)
    finally:
        lock.release()


def find_auto_tests(chat):
    if not lock.acquire(blocking=False):
        send(chat, BUSY)
        return
    try:
        live = Live(chat, "Ищу новые автотесты", "смотрю сайт HDP", progress=False)
        try:
            ok, out = node("horo/status.js", "--json")
        finally:
            live.done()
        if not ok:
            send(chat, err(out), menu=True, raw_html=True)
            return
        tests = [r for r in json.loads(out) if r["auto"] and r["rawStatus"] == "appointed"]
        if not tests:
            send(chat, card("Новых автотестов нет", note="Всё, что могу сдать сам, уже сдано", icon="🎉"), menu=True, raw_html=True)
            return
        pending[chat] = tests
        mode[chat] = "confirm_all"
        lines = "\n".join(f"{r.get('icon', '📘')} <b>{esc(r['subj'])}</b> — {esc(r['title'])}" for r in tests)
        send(chat, card(f"Нашёл автотесты: {len(tests)}", lines,
                        f"Сдам по очереди, это примерно {len(tests) * 2}–{len(tests) * 3} мин", "⚡"),
             markup=kb([(f"Сдать все ({len(tests)})", "all:yes", "success"), ("Не надо", "all:no")]), raw_html=True)
    finally:
        lock.release()


def run_all(chat, tests):
    """Сдаёт пачку тестов (строки из status.js --json или ссылки) с одной живой карточкой на всю пачку."""
    if not lock.acquire(blocking=False):
        send(chat, BUSY)
        return
    try:
        tests = [t if isinstance(t, dict) else {"url": t, "title": "", "subj": ""} for t in tests]
        n, t0, rows, details = len(tests), time.time(), [], []
        live = Live(chat, f"Сдаю автотесты · 0 из {n}")
        try:
            for i, t in enumerate(tests):
                live.title = f"Сдаю автотесты · {i + 1} из {n}"
                live.reset(esc(t["title"]), i / n)
                ok, out = solve(t["url"], on_tool=lambda name, inp: (lambda st: st and live.step(st[0], (i + st[1]) / n))(step_of(name, inp)))
                score, body = parse_score(out) if ok else (None, out)
                title = esc(t["title"] or f"Тест {i + 1}")
                if score:
                    got, total, pct = score
                    rows.append(f"{'🏆' if pct >= 100 else '✅' if pct >= 70 else '📊'} <b>{num(got)}/{num(total)}</b> · {title}"
                                if total else f"📊 <b>{pct}%</b> · {title}")
                else:
                    rows.append(f"{'✅' if ok else '⚠️'} {title}")
                details.append(f"<b>{title}</b>\n{md(body[:max(300, 2500 // n)])}")
        finally:
            live.done()
        perfect = sum(r.startswith("🏆") for r in rows)
        remember(f"Автотесты · {n}", True, "\n".join(plain(r) for r in rows), secs=time.time() - t0)
        send(chat, f"⚡ <b>Автотесты сданы: {n}</b>\n<blockquote>" + "\n".join(rows) + "</blockquote>\n"
             "<blockquote expandable>" + "\n\n".join(details) + "</blockquote>\n"
             f"<i>Заняло {mmss(time.time() - t0)} · подробности — в свёрнутой цитате</i>",
             menu=True, raw_html=True, effect=EFFECT_CONFETTI if perfect == n else None)
    finally:
        lock.release()
        threading.Thread(target=refresh_overview, daemon=True).start()


def update(chat):
    git = lambda *a: subprocess.run(["git", *a], cwd=REPO, capture_output=True, text=True)
    old = git("rev-parse", "HEAD").stdout.strip()
    if git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() != "main":  # старые установки стояли на ветке
        res = git("fetch", "origin", "main")
        if res.returncode == 0:
            res = git("checkout", "-B", "main", "origin/main")
    else:
        res = git("pull", "--ff-only", "origin", "main")
    log = esc((res.stdout + res.stderr).strip()[-1500:] or "ok")
    if res.returncode != 0:
        send(chat, "⚠️ Не обновился\n<blockquote expandable>" + log + "</blockquote>", raw_html=True)
        return
    if git("diff", "--name-only", old, "HEAD", "--", "package.json", "package-lock.json").stdout.strip():
        subprocess.run(["npm", "install", "--no-audit", "--no-fund"], cwd=REPO, capture_output=True)
    s = load_settings(); s["updated"] = chat; save_settings(s)
    send(chat, card("Обновляюсь", log, "Перезапускаюсь — напишу, когда поднимусь", "🔄"), raw_html=True)
    tg("getUpdates", offset=offset, timeout=0)  # подтверждаем /update, чтобы не выполнить его повторно
    os._exit(0)  # pm2 сам запустит бота заново


BOT_TOKEN = re.compile(r"\d{6,12}:[A-Za-z0-9_-]{30,}")


SIDE_BOTS = {  # соседние боты, которых HDP-бот умеет ставить на этот же сервер по токену
    "planner": {"env": "PLANNER_TOKEN", "what": "бота-планера", "short": "планер", "timeout": 900,
                "icon": "🗓", "title": "Бот-планер",
                "done": card("Планер запущен", "Открой @{name} и нажми /start — станешь владельцем. "
                             "Через минуту слева от поля ввода появится кнопка «Планер».", icon="✅")},
    "coach": {"env": "COACH_TOKEN", "what": "бота-тренера", "short": "тренера", "timeout": 900,
              "icon": "💪", "title": "Бот-тренер (WHOOP)",
              "done": card("Тренер запущен", "Открой @{name} и нажми /start — станешь владельцем. "
                           "Потом /connect — подключим WHOOP.", icon="✅")},
    # «Главный» сразу с входом для Claude (ключ выводится из токена — Claude его знает); владелец — этот чат
    "hub": {"env": "HUB_TOKEN", "what": "бота «Главный»", "short": "Главного", "timeout": 600,
            "icon": "👑", "title": "Бот «Главный»", "extra": {"HUB_REMOTE": "1"},
            "done": card("Главный запущен", "Открой @{name} и нажми /start — туда будут приходить "
                         "сообщения о ботах и о каждой команде Claude. Дальше Claude ставит и чинит ботов сам.", icon="✅")},
    "kpp": {"env": "KPP_TOKEN", "what": "бота «КПП»", "short": "КПП", "timeout": 600,
            "icon": "🚧", "title": "Бот «КПП»",
            "done": card("КПП запущен", "Открой @{name}, нажми /start и войди через Яндекс — "
                         "дальше просто присылай номер такси.", icon="✅")},
}


def open_kb(name):
    """Кнопка «Открыть @бот»: длинное имя Telegram обрезал бы — тогда просто «Открыть бота»."""
    text = f"Открыть @{name}" if len(name) <= 16 else "Открыть бота"
    return kb([(text, {"url": f"https://t.me/{name}"}, "primary")])


def side_prompt(chat, kind):
    """/planner, /coach, /kpp, /hub без токена: что прислать."""
    side = SIDE_BOTS[kind]
    send(chat, card(side["title"], f"Пришли токен {side['what']} от @BotFather — поставлю его на этот сервер.",
                    f"Например: /{kind} 123456789:AAH…", side["icon"]), menu=True, raw_html=True)


def install_planner(chat, token, msg_id=None, kind="planner"):
    """Ставит соседнего бота (planner/, kpp/, coach/ или hub/) на этот же сервер: токен в /etc/…-bot.env, pm2.
    Запускается только владельцем и только фиксированный <kind>/install.sh из репозитория."""
    side = SIDE_BOTS[kind]
    if msg_id:  # токен не должен висеть в чате
        try:
            tg("deleteMessage", chat_id=chat, message_id=msg_id)
        except Exception:
            pass
    if token == TOKEN:
        send(chat, err(f"Это токен этого бота — нужен токен нового {side['what']} от @BotFather"), raw_html=True)
        return
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getMe", timeout=30) as r:
            me = json.load(r)["result"]
    except Exception:
        send(chat, err("Токен не подошёл. Скопируй его из @BotFather целиком и пришли ещё раз"), raw_html=True)
        return
    name = me.get("username") or kind
    wait = send(chat, card(f"Ставлю {side['short']} @{esc(name)}", note="Это 1–2 минуты", icon="⏳"), raw_html=True)
    env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "HOME": "/root",  # без секретов этого бота
           "LANG": "C.UTF-8", side["env"]: token, **side.get("extra", {})}
    if kind == "hub":
        env["HUB_OWNER"] = str(chat)
    try:
        res = subprocess.run(["bash", os.path.join(REPO, kind, "install.sh")], cwd=REPO, env=env,
                             stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=side["timeout"])
    except subprocess.TimeoutExpired:
        delete(chat, wait)
        send(chat, err(f"Установка шла дольше {side['timeout'] // 60} минут и остановлена", "Логи: pm2 logs horo-bot"),
             raw_html=True)
        return
    delete(chat, wait)
    if res.returncode != 0:
        tail = (res.stdout + res.stderr).strip()[-1200:]
        send(chat, err(f"Не получилось поставить {side['short']}") + f"\n<blockquote expandable>{esc(tail)}</blockquote>",
             raw_html=True)
        return
    send(chat, side["done"].format(name=esc(name)), markup=open_kb(name), raw_html=True)


AGENT_ENV = "/etc/tg-agent.env"
HUB_ENV = "/etc/hub-bot.env"  # есть Главный — Секретарь живёт в нём (один бот), /agent только подсказывает
agent = {}  # chat -> {"token", "phone", "site", "hash"} — шаги /agent


def agent_env():
    try:
        with open(AGENT_ENV) as f:
            return dict(re.findall(r"^\s*([A-Z_]+)\s*=\s*(.*?)\s*$", f.read(), re.M))
    except OSError:
        return {}


def forget(chat, msg_id):
    """Токен и коды не должны висеть в чате."""
    try:
        tg("deleteMessage", chat_id=chat, message_id=msg_id)
    except Exception:
        pass


def agent_step(chat, text, msg_id=None):
    """/agent ТОКЕН — ставит секретаря (tgbot/) на этот сервер. Нужен только токен: бот работает на Bot API,
    а ключи my.telegram.org для аккаунта секретарь получит сам, когда Глеб нажмёт у него «Войти в аккаунт»."""
    if text == "/cancel":
        agent.pop(chat, None); mode.pop(chat, None)
        send(chat, "👌 Отменил установку секретаря", menu=True)
        return
    m = BOT_TOKEN.search(text)
    if os.path.exists(HUB_ENV):  # Секретарь теперь часть Главного: свой бот и токен не нужны
        agent.pop(chat, None); mode.pop(chat, None)
        if msg_id and m:
            forget(chat, msg_id)
        send(chat, card("Секретарь — часть Главного", "Отдельный бот не нужен: пиши Главному что угодно, "
                        "вход в аккаунт — /login там. Если Секретарь не отвечает, Главный поднимет его сам.",
                        icon="🤵"), raw_html=True)
        return
    if msg_id and m:
        forget(chat, msg_id)
    if not m:
        mode[chat] = "agent_token"
        send(chat, card("Секретарь", "Пришли токен его бота от @BotFather — поставлю на этот сервер.", "/cancel — отмена",
                        "🤵"), raw_html=True)
        return
    if m.group(0) == TOKEN:
        mode[chat] = "agent_token"
        send(chat, err("Нужен токен бота-секретаря от @BotFather, не этого бота", "/cancel — отмена"), raw_html=True)
        return
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{m.group(0)}/getMe", timeout=30) as r:
            name = json.load(r)["result"].get("username") or "agent"
    except Exception:
        mode[chat] = "agent_token"
        send(chat, err("Токен не подошёл. Скопируй его из @BotFather целиком", "/cancel — отмена"), raw_html=True)
        return
    agent[chat] = {"token": m.group(0), "name": name}
    agent_install(chat)


def agent_install(chat):
    st = agent.pop(chat, {}); mode.pop(chat, None)
    old = agent_env()  # ключи, если когда-то уже были, сохраняем
    fd = os.open(AGENT_ENV, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.write(fd, (f"TG_AGENT_TOKEN={st['token']}\nTG_API_ID={old.get('TG_API_ID', '')}\nTG_API_HASH={old.get('TG_API_HASH', '')}\n"
                  f"TG_ALLOWED_ID={old.get('TG_ALLOWED_ID') or owner() or ''}\n").encode())
    os.close(fd)
    wait = send(chat, card(f"Ставлю секретаря @{esc(st.get('name', 'agent'))}",
                           note="Это 3–7 минут: распознавание голоса тяжёлое", icon="⏳"), raw_html=True)
    env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin") + ":/usr/local/bin", "HOME": "/root",
           "LANG": "C.UTF-8", "DEBIAN_FRONTEND": "noninteractive"}
    try:
        res = subprocess.run(["bash", os.path.join(REPO, "tgbot", "install.sh")], cwd=REPO, env=env,
                             stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=1800)
    except subprocess.TimeoutExpired:
        delete(chat, wait)
        send(chat, err("Установка шла дольше 30 минут и остановлена", "Логи: pm2 logs horo-bot"), menu=True, raw_html=True)
        return
    delete(chat, wait)
    if res.returncode != 0:
        tail = (res.stdout + res.stderr).strip()[-1200:]
        send(chat, err("Установка секретаря не прошла") + f"\n<blockquote expandable>{esc(tail)}</blockquote>",
             menu=True, raw_html=True)
        return
    name = st.get("name", "agent")
    send(chat, card("Секретарь запущен", f"Открой @{esc(name)} и нажми /start — он уже отвечает на вопросы и голосовые.",
                    "Чтобы он вёл твой Telegram, нажми там «Войти в аккаунт»", "✅"),
         markup=open_kb(name), raw_html=True)


WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]


def spoken_button(text):
    """«Оценки.» голосом -> «📊 Оценки»: кнопку не произнести с эмодзи, поэтому сравниваем только слова."""
    words = lambda s: " ".join(re.findall(r"[a-zа-яё]+", s.lower().replace("ё", "е")))
    said = words(text)
    return next((b for b in sorted(BUTTONS) if said and words(b) == said), text)


def hear_voice(msg):
    """Голосовое, аудио или кружок -> текст (faster-whisper, несколько секунд) -> дальше как набранный текст."""
    chat = msg["chat"]["id"]
    mid = send(chat, "🎙 Слушаю…")
    stop = threading.Event()

    def typing():  # «печатает…» держится 5 секунд — повторяем, пока распознаётся
        while not stop.is_set():
            quiet("sendChatAction", chat_id=chat, action="typing")
            stop.wait(4)

    REAL_THREAD(target=typing, daemon=True).start()
    try:
        heard = media.voice_text(TOKEN, msg)
    except media.MediaError as e:
        delete(chat, mid)
        why = str(e)
        send(chat, f"⚠️ {why[:1].upper()}{why[1:]}.", menu=True)
        return
    except Exception as e:
        print("voice:", e)
        delete(chat, mid)
        send(chat, "⚠️ Не получилось скачать голосовое, пришли ещё раз.", menu=True)
        return
    finally:
        stop.set()
    echo = f"🎙 «{esc(heard)}»"
    if mid:
        edit(chat, mid, echo)
    else:
        send(chat, echo, raw_html=True)
    handle(as_text(msg, spoken_button(heard)))


MEDIA_KEYS = ("voice", "audio", "video", "video_note", "animation", "document", "photo", "caption")
INBOX = os.path.join(REPO, "horo", "tmp", "inbox")  # фото и кадры видео из чата (вне git)
seen = {}  # chat -> что нашёл на последнем фото: {"url", "title", "task", "images", "note"}


def inbox():
    return os.path.join(INBOX, time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4])


def as_text(msg, text):
    """То же сообщение, но как набранный текст (без вложений)."""
    return {**{k: v for k, v in msg.items() if k not in MEDIA_KEYS}, "text": text}


def look_photo(msg):
    """Фото или скриншот (черновика нет): Claude ищет на нём задание и предлагает решить."""
    chat = msg["chat"]["id"]
    caption = (msg.get("caption") or "").strip()
    if LINK.search(caption):  # ссылка в подписи — как будто её прислали текстом
        handle(as_text(msg, caption))
        return
    try:
        path = media.photo_path(TOKEN, msg, inbox())
    except media.MediaError as e:
        send(chat, err(str(e)), menu=True, raw_html=True)
        return
    except Exception as e:
        print("photo:", e)
        send(chat, err("Не получилось скачать фото, пришли ещё раз"), menu=True, raw_html=True)
        return
    look_images(chat, [path], caption, msg.get("message_id"))


def hear_video(msg):
    """Видео, кружок, гифка: речь — как голосовое (команда, ссылка, правка черновика), кадры — как фото."""
    chat = msg["chat"]["id"]
    mid = send(chat, "🎬 Смотрю видео…")
    quiet("sendChatAction", chat_id=chat, action="typing")
    try:
        v = media.video(TOKEN, msg, inbox())
    except media.MediaError as e:
        delete(chat, mid)
        send(chat, err(str(e)), menu=True, raw_html=True)
        return
    except Exception as e:
        print("video:", e)
        delete(chat, mid)
        send(chat, err("Не получилось скачать видео, пришли ещё раз"), menu=True, raw_html=True)
        return
    speech = (v.get("text") or "").strip()
    said = " ".join(x for x in ((msg.get("caption") or "").strip(), speech) if x)
    if speech:
        edit(chat, mid, f"🎙 «{esc(speech)}»")
    else:
        delete(chat, mid)
    frames = v.get("frames") or []
    if said and (LINK.search(said) or draft() or spoken_button(said) in BUTTONS or not frames):
        handle(as_text(msg, spoken_button(said)))
    elif frames:
        look_images(chat, frames, said, msg.get("message_id"), video=True)
    else:
        send(chat, err("В видео не разобрал ни кадров, ни слов"), menu=True, raw_html=True)


def look_prompt(paths, note, video):
    ov = overview() or {}
    rows = list(ov.get("tasks") or [])
    urls = {r.get("url") for r in rows}
    rows += [r for r in ov.get("comments") or [] if r.get("url") not in urls]
    tasks = "\n".join(f"- {r.get('subj', '')}: {r.get('title', '')} — {r.get('url', '')}" for r in rows[:40])
    return (
        f"Глеб прислал в бот {'кадры из видео' if video else 'фото или скриншот'}: {', '.join(paths)}. "
        "Открой инструментом Read.\n"
        + (f"Он пишет или говорит: «{note}»\n" if note else "")
        + "Найди на нём школьное задание (сайт horodigital.ru: тест, вопрос, рабочий лист, условие задачи).\n"
        + (f"Несделанные задания Глеба (предмет: название — ссылка):\n{tasks}\n" if tasks else "")
        + "Ответь только JSON без пояснений: {\"link\": \"ссылка horodigital.ru, если видна на фото целиком, иначе пусто\", "
          "\"match\": \"ссылка из списка выше, если на фото именно это задание, иначе пусто\", "
          "\"title\": \"название задания или что на фото, до 8 слов\", "
          "\"task\": \"условие задания с фото коротко, до 500 символов; пусто, если задания нет\"}"
    )


def parse_json(out):
    m = re.search(r"\{.*\}", out or "", re.S)
    try:
        data = json.loads(m.group(0)) if m else {}
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def look_images(chat, paths, note="", mid=None, video=False):
    if not lock.acquire(blocking=False):
        send(chat, BUSY)
        return
    try:
        react(chat, mid, "👀")
        live = Live(chat, "Смотрю видео" if video else "Смотрю фото", "ищу задание", progress=False)
        try:
            ok, out = claude(look_prompt(paths, note, video), ["Read"], timeout=300)
        finally:
            live.done()
    finally:
        lock.release()
    if not ok:
        send(chat, err(out), menu=True, raw_html=True)
        return
    found = parse_json(out)
    m = LINK.search(str(found.get("link") or "")) or LINK.search(str(found.get("match") or ""))
    title = str(found.get("title") or "").strip()[:120]
    task = str(found.get("task") or "").strip()[:600]
    seen[chat] = {"url": m.group(0) if m else "", "title": title, "task": task, "images": paths, "note": note}
    if m:
        send(chat, card("Нашёл задание", esc(title or "Задание с фото"), "Решить?", "📸"),
             markup=kb([("📝 Решить и сдать", "ph:do", "primary"), ("👀 Только ответы", "ph:show")]), raw_html=True)
    elif task:
        send(chat, card(esc(title) or "Задание на фото", esc(task), "Ссылки нет — решу по фото, на сайт ничего не отправлю",
                        "📸"), markup=kb([("🧠 Решить по фото", "ph:solve", "primary")]), raw_html=True)
    else:
        send(chat, card("Задания на фото не нашёл", esc(title) if title else "",
                        "Пришли скриншот задания или ссылку. Фото к ответу учителю — когда открыт черновик", "📸"),
             menu=True, raw_html=True)


def solve_photo(chat):
    """Задание с фото без ссылки: Claude решает по картинке и показывает ответ (на сайт ничего не уходит)."""
    s = seen.get(chat) or {}
    paths = [p for p in s.get("images") or [] if os.path.exists(p)]
    if not paths:
        send(chat, err("Фото уже нет — пришли его ещё раз"), menu=True, raw_html=True)
        return
    if not lock.acquire(blocking=False):
        send(chat, BUSY)
        return
    try:
        t0 = time.time()
        live = Live(chat, "Решаю по фото", esc(s.get("title") or ""), progress=False)
        try:
            ok, out = claude(
                f"На фото задание Глеба (ученик 8 класса): {', '.join(paths)}. Открой инструментом Read.\n"
                + (f"Он пишет: «{s['note']}»\n" if s.get("note") else "")
                + "Реши его и покажи ответы. На сайт ничего не отправляй, скрипты не запускай. "
                  "Ответ для Telegram, коротко, по пунктам, без markdown-таблиц; десятичные дроби через запятую.",
                ["Read"], timeout=600)
        finally:
            live.done()
    finally:
        lock.release()
    title = s.get("title") or "Задание по фото"
    remember(title, ok, plain(out), show=True, secs=time.time() - t0)
    if not ok:
        send(chat, err(out), menu=True, raw_html=True)
        return
    send(chat, result_card(out, title, time.time() - t0, show=True)[0], menu=True, raw_html=True)


def on_photo_button(chat, key):
    s = seen.get(chat) or {}
    if key == "solve":
        threading.Thread(target=solve_photo, args=(chat,), daemon=True).start()
    elif s.get("url"):
        threading.Thread(target=run_task, args=(chat, s["url"], "", key == "show"), daemon=True).start()
    else:
        send(chat, err("Это фото уже неактуально — пришли его ещё раз"), menu=True, raw_html=True)


def handle(msg):
    chat = msg["chat"]["id"]
    kind = media.kind(msg)
    if kind == "voice":  # распознаём не в цикле опроса Telegram
        threading.Thread(target=hear_voice, args=(msg,), daemon=True).start()
        return
    if kind == "video" and mode.get(chat) != "alphabet":
        threading.Thread(target=hear_video, args=(msg,), daemon=True).start()
        return
    text = (msg.get("text") or "").strip()
    text = ALIASES.get(text, text)
    name = msg.get("from", {}).get("first_name") or ""

    if text.startswith("/start"):
        send(chat, card(f"Привет{', ' + esc(name) if name else ''}!",
                        "Пришли ссылку на задание — решу и сдам. Можно голосом или скриншотом.",
                        "Всё то же — в приложении: кнопка «HDP» слева", "👋"),
             menu=True, raw_html=True)
        if APP_URL:
            on_app_url(APP_URL)  # кнопка приложения — новому владельцу
        return
    if text.startswith("/help"):
        send(chat, HELP, menu=True, raw_html=True)
        return
    if text.startswith("/app"):
        send_app(chat)
        return
    if text.startswith("/update"):
        update(chat)
        return
    if text.startswith("/agent") or (mode.get(chat) or "").startswith("agent_"):
        threading.Thread(target=agent_step, args=(chat, text, msg.get("message_id")), daemon=True).start()
        return
    if text.startswith("/kpp"):
        m = BOT_TOKEN.search(text)
        if not m:
            side_prompt(chat, "kpp")
            return
        threading.Thread(target=install_planner, args=(chat, m.group(0), msg.get("message_id"), "kpp"),
                         daemon=True).start()
        return
    if text.startswith("/hub"):
        m = BOT_TOKEN.search(text)
        if not m:
            side_prompt(chat, "hub")
            return
        threading.Thread(target=install_planner, args=(chat, m.group(0), msg.get("message_id"), "hub"),
                         daemon=True).start()
        return
    if text.startswith("/coach"):
        m = BOT_TOKEN.search(text)
        if not m:
            side_prompt(chat, "coach")
            return
        threading.Thread(target=install_planner, args=(chat, m.group(0), msg.get("message_id"), "coach"),
                         daemon=True).start()
        return
    if text.startswith("/planner") or BOT_TOKEN.fullmatch(text):
        m = BOT_TOKEN.search(text)
        if not m:
            side_prompt(chat, "planner")
            return
        threading.Thread(target=install_planner, args=(chat, m.group(0), msg.get("message_id")), daemon=True).start()
        return
    if text.startswith("/emoji_reset"):
        s = load_settings(); s.pop("emoji", None); save_settings(s)
        send(chat, "👌 Премиум-эмодзи сброшены, снова обычные", menu=True)
        return
    if text.startswith("/emoji"):
        mode[chat] = "emoji"
        send(chat, card("Премиум-эмодзи", "Пришли одним сообщением премиум-эмодзи — буду ставить их в своих "
                        "сообщениях вместо обычных.", "Работает, если у тебя есть Telegram Premium", "✨"), raw_html=True)
        return
    if mode.get(chat) == "emoji":
        mode.pop(chat)
        save_emoji(chat, msg)
        return
    if text.startswith("/alphabet_reset"):
        ok, out = py("horo/alphabet.py", "reset")
        send(chat, out if ok else err(out), menu=True, raw_html=not ok)
        return
    if text.startswith("/alphabet") or text == BTN_HAND:
        threading.Thread(target=send_alphabet, args=(chat,), daemon=True).start()
        return
    if text.startswith("/check") or text == BTN_CHECK:
        threading.Thread(target=check, args=(chat,), daemon=True).start()
        return
    if mode.get(chat) == "alphabet":
        if attachment(msg):
            threading.Thread(target=take_alphabet, args=(chat, msg), daemon=True).start()
            return
        if text.lower().strip(".! ") in ("готово", "все", "всё", "хватит"):
            mode.pop(chat)
            if os.path.exists(GLEB_EXTRA):
                threading.Thread(target=send_sample, args=(chat, "Алфавит записал"), daemon=True).start()
            else:
                send(chat, err("Пока ни одного листа не разобрал", "Пришли листы алфавита, потом напиши «готово»"),
                     menu=True, raw_html=True)
            return
        if text not in BUTTONS and not LINK.search(text):
            send(chat, card("Жду листы алфавита", note="Скриншот или фото. Закончил — напиши «готово»", icon="📸"),
                 raw_html=True)
            return
        mode.pop(chat)
    if text.startswith("/style"):
        threading.Thread(target=send_sample, args=(chat,), daemon=True).start()
        return
    if mode.get(chat) == "hand_tune" and text and text not in BUTTONS and not text.startswith("/") and not LINK.search(text):
        threading.Thread(target=tune_hand, args=(chat, text), daemon=True).start()
        return
    if mode.get(chat) == "hand_tune":
        mode.pop(chat)
    if mode.get(chat) == "confirm_all":
        mode.pop(chat)
        rows = pending.pop(chat, [])
        if text.lower() in ("да", "yes", "ок", "давай"):
            threading.Thread(target=run_all, args=(chat, rows), daemon=True).start()
        else:
            send(chat, "👌 Отменил", menu=True)
        return
    if draft() and attachment(msg):
        threading.Thread(target=add_draft_photo, args=(chat, msg), daemon=True).start()
        return
    if draft() and text and not text.startswith("/") and text not in BUTTONS and not LINK.search(text):
        if text.lower().strip(".! ") in ("ок", "ok", "да", "отправляй", "отправь"):
            threading.Thread(target=submit_draft, args=(chat,), daemon=True).start()
        else:
            edit_draft(chat, text)
        return
    if kind == "photo":  # черновика нет: ищем на фото задание
        threading.Thread(target=look_photo, args=(msg,), daemon=True).start()
        return

    simple = {
        BTN_LIST: ("horo/status.js", "--html"),
        BTN_LATE: ("horo/status.js", "--late", "--html"),
        BTN_COMM: ("horo/comments.js", "--html"),
        BTN_GRADES: ("horo/grades.js", "--html"),
    }
    if text in simple:
        threading.Thread(target=run_script, args=(chat, *simple[text]), daemon=True).start()
        return
    if text == BTN_HELP:
        send(chat, HELP, menu=True, raw_html=True)
        return
    if text == BTN_MORE:
        send(chat, more_text(), markup=more_kb(), raw_html=True)
        return
    if text == BTN_APP:
        send_app(chat)
        return
    if text == BTN_DO:
        mode.pop(chat, None)
        send(chat, card("Жду ссылку на задание", note="Решу и сдам сам. Ответ учителю сначала покажу черновиком",
                        icon="🔗"), raw_html=True)
        return
    if text == BTN_SHOW:
        mode[chat] = "show"
        send(chat, card("Жду ссылку на задание", note="Покажу ответы, на сайт ничего не отправлю", icon="👀"),
             raw_html=True)
        return
    if text == BTN_ALL:
        threading.Thread(target=find_auto_tests, args=(chat,), daemon=True).start()
        return

    m = LINK.search(text)
    if not m:
        send(chat, card("Не понял", note="Пришли ссылку на задание или выбери действие внизу", icon="🤔"),
             menu=True, raw_html=True)
        return
    show = mode.pop(chat, None) == "show"
    threading.Thread(target=run_task, args=(chat, m.group(0), text, show, msg.get("message_id")), daemon=True).start()


# ---------- Приложение внутри Telegram (Mini App, bot/webapp/) ----------
# Те же действия, что и кнопки в чате: приложение вызывает run_task, run_all, черновик и т. д.
# Результаты приходят и в чат, и в приложение.

OVERVIEW = os.path.join(REPO, "horo", "tmp", "overview.json")  # последние данные с сайта: открываем сразу
OV = {"loading": False, "error": ""}
site_lock = threading.Lock()  # к сайту за списком ходит один запрос за раз
LAST = {}                     # последний результат работы — карточка в приложении
APP_URL = ""                  # https-адрес приложения (туннель cloudflared), пусто — ещё не готов
STALE = 120                   # через сколько секунд данные приложения обновляются с сайта


def remember(title, ok, text, score=None, url="", show=False, secs=0, draft=False, kind="task"):
    LAST.clear()
    LAST.update(title=title, ok=ok, text=(text or "").strip()[:4000], score=list(score) if score else None, url=url,
                show=show, secs=int(secs), draft=draft, kind=kind, at=int(time.time()))


def overview():
    try:
        with open(OVERVIEW) as f:
            return json.load(f)
    except Exception:
        return None


def refresh_overview():
    """Задания, оценки и комментарии с сайта (horo/overview.js) -> horo/tmp/overview.json."""
    if not site_lock.acquire(blocking=False):
        return  # уже обновляется
    try:
        OV["loading"] = True
        ok, out = node("horo/overview.js", timeout=240)
        if ok:
            data = json.loads(out)
            os.makedirs(os.path.dirname(OVERVIEW), exist_ok=True)
            with open(OVERVIEW + ".part", "w") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(OVERVIEW + ".part", OVERVIEW)
            OV["error"] = ""
        else:
            OV["error"] = plain(out)
    except Exception as e:
        OV["error"] = f"Не разобрал ответ сайта: {e}"
    finally:
        OV["loading"] = False
        site_lock.release()


def app_state(_=None):
    """Что сейчас происходит: опрашивается приложением раз в пару секунд."""
    d = draft()
    ov = overview()
    return {
        "busy": lock.locked(),
        "live": Live.active.state() if Live.active and lock.locked() else None,
        "last": LAST or None,
        "draft": {"title": d["title"], "url": d["url"], "text": drafts.read(d["task"]),
                  "files": len(drafts.files(d["task"]))} if d else None,
        "at": ov["at"] if ov else 0,
        "loading": OV["loading"],
        "error": OV["error"],
    }


def app_overview(b):
    ov = overview()
    stale = not ov or time.time() - ov["at"] / 1000 > STALE
    if (b.get("fresh") or stale) and not OV["loading"]:
        OV["loading"] = True  # чтобы приложение сразу показало «обновляю»
        threading.Thread(target=refresh_overview, daemon=True).start()
        ov = overview()
    return {"data": ov, "loading": OV["loading"], "error": OV["error"]}


def app_me(_):
    git = lambda *a: subprocess.run(["git", *a], cwd=REPO, capture_output=True, text=True).stdout.strip()
    return {"alphabet": os.path.exists(GLEB_EXTRA),
            "version": git("rev-parse", "--short", "HEAD")}


def busy_check():
    if lock.locked():
        raise ValueError("Я сейчас занят другим заданием — подожди, пока закончу")


def app_solve(b):
    m = LINK.search(str(b.get("url") or ""))
    if not m:
        raise ValueError("Это не ссылка на задание HDP")
    busy_check()
    threading.Thread(target=run_task, args=(owner(), m.group(0), "", bool(b.get("show"))), daemon=True).start()
    return {"ok": True}


def app_auto(_):
    busy_check()
    tests = [r for r in (overview() or {}).get("tasks", []) if r.get("auto") and r.get("rawStatus") == "appointed"]
    if not tests:
        raise ValueError("Новых автотестов нет")
    threading.Thread(target=run_all, args=(owner(), tests), daemon=True).start()
    return {"ok": True, "n": len(tests)}


def app_draft(action):
    def fn(b):
        d = draft()
        if not d:
            raise ValueError("Черновика уже нет")
        if action == "send":
            busy_check()
            threading.Thread(target=submit_draft, args=(owner(),), daemon=True).start()
        elif action == "drop":
            set_draft(None)
            send(owner(), card("Не отправляю", note="Черновик отменён в приложении", icon="👌"), menu=True, raw_html=True)
        elif action == "save":
            text = str(b.get("text") or "").strip()
            before = drafts.read(d["task"])
            if text and text != before:
                drafts.write(d["task"], text)
                if drafts.record_edit(d["task"], d["title"], before, text):
                    d["pairs"].append([before, text, text])
                    set_draft(d)
        return app_state()
    return fn


def app_chat(fn):
    """Действие, результат которого приходит в чат (проверка, алфавит)."""
    def run(_):
        busy_check()
        threading.Thread(target=fn, args=(owner(),), daemon=True).start()
        return {"ok": True}
    return run


ROUTES = {
    "/api/state": app_state, "/api/overview": app_overview, "/api/me": app_me,
    "/api/solve": app_solve, "/api/auto": app_auto,
    "/api/draft/send": app_draft("send"), "/api/draft/drop": app_draft("drop"), "/api/draft/save": app_draft("save"),
    "/api/check": app_chat(check), "/api/alphabet": app_chat(send_alphabet),
}


def app_kb(text="✨ Открыть приложение"):
    return kb([(text, {"web_app": {"url": APP_URL + "/"}}, "primary")]) if APP_URL else None


def on_app_url(url):
    """Адрес приложения готов или сменился (перезапуск туннеля): обновить кнопку у поля ввода."""
    global APP_URL
    APP_URL = url
    button = {"type": "web_app", "text": "HDP", "web_app": {"url": url + "/"}}
    params = {"menu_button": json.dumps(button)}
    if owner():
        params["chat_id"] = owner()
    quiet("setChatMenuButton", **params)
    print("Приложение:", url)


def send_app(chat):
    if APP_URL:
        send(chat, card("Приложение HDP", note="Задания, оценки и учителя в одном окне. Кнопка «HDP» — "
                        "всегда слева от поля ввода, кнопки чата тоже работают", icon="✨"), markup=app_kb(), raw_html=True)
    else:
        send(chat, err("Приложение ещё запускается — попробуй через минуту", "Пока всё то же — кнопками внизу"),
             menu=True, raw_html=True)


def setup_profile():
    """Меню команд, описание и подпись бота в Telegram (видно в пустом чате и в профиле)."""
    quiet("setMyCommands", commands=json.dumps([{"command": c, "description": d} for c, d in COMMANDS]))
    quiet("setMyDescription", description=ABOUT)
    quiet("setMyShortDescription", short_description=ABOUT_SHORT)


def main():
    global offset
    threading.Thread(target=setup_profile, daemon=True).start()
    web.start(TOKEN, owner, ROUTES, on_app_url)
    print("Бот запущен")
    print("alphabet renorm:", py("horo/alphabet.py", "renorm"))  # старый алфавит — к размеру каждой буквы
    s = load_settings()
    if s.pop("updated", None):  # поднялись после /update
        save_settings(s)
        try:
            head = subprocess.run(["git", "log", "-1", "--format=%h %s"], cwd=REPO, capture_output=True, text=True).stdout.strip()
            send(owner(), card("Обновился", note=esc(head), icon="✨"), menu=True, raw_html=True)
        except Exception as e:
            print("startup:", e)
    sup = support.Support(TOKEN, "HDP", folders=["bot", "horo"], pm2="horo-bot", allowed=lambda uid: uid == owner(),
                          tests=["python3 bot/test_bot.py"])
    while True:
        try:
            upd = tg("getUpdates", offset=offset, timeout=60)
        except Exception as e:  # сеть моргнула — пробуем снова
            print("getUpdates:", e)
            time.sleep(5)
            continue
        for u in upd.get("result", []):
            offset = u["update_id"] + 1
            if sup.intercept(u):  # 🛠 /fix и ответы поддержке
                continue
            cq = u.get("callback_query")
            if cq:
                if cq.get("from", {}).get("id") == owner() and cq.get("message"):
                    try:
                        on_callback(cq)
                    except Exception as e:
                        print("callback:", e)
                continue
            msg = u.get("message") or {}
            uid = msg.get("from", {}).get("id")
            if "chat" not in msg:
                continue
            if not owner() and (msg.get("text") or "").startswith("/start"):
                s = load_settings(); s["owner"] = uid; save_settings(s)  # первый /start — это владелец
                print("Владелец бота:", uid)
            if uid != owner():
                continue
            try:
                handle(msg)
            except Exception as e:
                print("handle:", e)


if __name__ == "__main__":
    main()
