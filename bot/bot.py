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
"""
import datetime
import html
import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from zoneinfo import ZoneInfo

import drafts

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
DIGEST_HOUR = 15  # во сколько присылать ежедневную сводку (по Москве)

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
BTN_DIGEST = "🔔 Сводка"
BTN_HAND = "✍️ Почерк"
BTN_CHECK = "🩺 Проверка"
BTN_HELP = "❓ Помощь"
ALIASES = {  # старые надписи кнопок (клавиатура у Глеба обновится со следующим сообщением) и «выключенная» сводка
    "📋 Что надо сделать": BTN_LIST, "⚡ Сдать все автотесты": BTN_ALL, "💬 Комментарии учителя": BTN_COMM,
    "📊 Мои оценки": BTN_GRADES, "🔔 Сводка каждый день": BTN_DIGEST, "✍️ Мой почерк": BTN_HAND, "🔕 Сводка": BTN_DIGEST,
}
BUTTONS = {BTN_DO, BTN_SHOW, BTN_LIST, BTN_LATE, BTN_ALL, BTN_COMM, BTN_GRADES, BTN_DIGEST, BTN_HAND, BTN_CHECK, BTN_HELP}


def menu_kb():
    digest = BTN_DIGEST if load_settings().get("digest") else "🔕 Сводка"
    return json.dumps({
        "keyboard": [
            [BTN_DO, BTN_SHOW],
            [BTN_LIST, BTN_LATE, BTN_ALL],
            [BTN_COMM, BTN_GRADES, BTN_HAND],
            [digest, BTN_CHECK, BTN_HELP],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
        "input_field_placeholder": "Ссылка на задание…",
    })


HELP = (  # HTML: отправляется с raw_html=True
    "📖 <b>Что я умею</b>\n\n"
    "<b>Задания</b>\n"
    "<blockquote>"
    f"{BTN_DO} — пришли ссылку, решу и сдам\n"
    f"{BTN_SHOW} — только покажу ответы\n"
    f"{BTN_ALL} — найду новые тесты и сдам пачкой"
    "</blockquote>\n"
    "<b>Учёба</b>\n"
    "<blockquote>"
    f"{BTN_LIST} — всё несделанное по предметам\n"
    f"{BTN_LATE} — только просроченное\n"
    f"{BTN_COMM} — что написали учителя\n"
    f"{BTN_GRADES} — оценки и сколько до следующей\n"
    f"{BTN_DIGEST} — список дел каждый день в {DIGEST_HOUR}:00 мск"
    "</blockquote>\n"
    "<b>Ответы учителю</b>\n"
    "<blockquote>Сначала присылаю черновик. Поправь текстом, просьбой («убери второй абзац») "
    "или своими фото — и жми «✅ Отправить». Правки запоминаю.</blockquote>\n"
    "<b>Почерк и настройки</b>\n"
    "<blockquote>"
    f"{BTN_HAND} — алфавит, чтобы писать решения твоим почерком\n"
    "/style — как пишу: небрежность, толщина, размер\n"
    f"{BTN_CHECK} — проверю, что всё работает\n"
    "/update — обновиться · /emoji — премиум-эмодзи"
    "</blockquote>"
)
COMMANDS = [  # меню команд у поля ввода
    ("start", "Главное меню"), ("help", "Что я умею"), ("check", "Проверить, что всё работает"),
    ("style", "Настроить почерк"), ("alphabet", "Заполнить алфавит почерка"),
    ("update", "Обновить бота"), ("emoji", "Премиум-эмодзи"),
]
ABOUT = ("Помощник по HDP ⚡ Решаю и сдаю задания по ссылке, пишу ответы учителю твоим почерком, "
         "слежу за дедлайнами, комментариями и оценками.")
ABOUT_SHORT = "Решаю задания HDP, слежу за дедлайнами и оценками ⚡"
BUSY = "⏳ Занят другим заданием — освобожусь через пару минут, тогда пришли ещё раз."


def esc(x):
    return html.escape(str(x), quote=False)


# ---------- Telegram ----------

def tg(method, **params):
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(API + method, data=data, timeout=70) as r:
        return json.load(r)


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
                mid = msg_id(tg("sendMessage", chat_id=chat, disable_web_page_preview="true", **t))
                break
            except urllib.error.HTTPError as e:
                print("sendMessage:", e)
                if n == len(tries) - 1:
                    raise
    return mid


def quiet(method, **params):
    """Вызов Telegram, ошибка которого не важна (правка, удаление, реакция)."""
    try:
        return tg(method, **params)
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
        self.mid = send(chat, self.render(), raw_html=True)
        quiet("sendChatAction", chat_id=chat, action="typing")
        REAL_THREAD(target=self.loop, daemon=True).start()

    def render(self):
        lines = [f"{SPIN[self.tick % len(SPIN)]} <b>{self.title}</b>"]
        if self.sub:
            lines.append(f"<i>{self.sub}</i>")
        if self.done_steps or self.current:
            lines.append("")
            lines += [f"✓ {s}" for s in self.done_steps[-3:]]
            if self.current:
                lines.append(f"▸ <b>{self.current}</b>")
        lines.append("")
        clock = mmss(time.time() - self.t0)
        lines.append(f"<code>{bar(self.frac)}</code>  {int(self.frac * 100)}% · {clock}" if self.progress else f"<i>{clock}</i>")
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
        delete(self.chat, self.mid)

    def seconds(self):
        return time.time() - self.t0


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
        send(chat, "Премиум-эмодзи в сообщении не нашёл. Пришли именно премиум-эмодзи (из набора Telegram Premium).", menu=True)
        return
    s = load_settings()
    s.setdefault("emoji", {}).update(found)
    save_settings(s)
    shown = sorted({a for a in found if "\ufe0f" not in a} | {a for a in found if a.replace("\ufe0f", "") not in found})
    send(chat, "Запомнил: " + " ".join(shown) + "\nТеперь в моих сообщениях эти эмодзи будут премиальными. "
               "Чтобы сбросить все: /emoji_reset", menu=True)


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
        return False, "Сайт не ответил вовремя, попробуй позже."
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
        return False, "Claude Code не установлен (npm install -g @anthropic-ai/claude-code)."
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
        return False, f"Не успел за {timeout // 60} минут, остановил."
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
    ("submit.js", "📤 Сдаю на сайт", 0.88),
    ("answer.js", "📤 Отправляю учителю", 0.9),
    ("fetch.js", "📖 Читаю условие", 0.2),
    ("pdftoppm", "📄 Листаю рабочий лист", 0.3),
    ("pdftotext", "📄 Листаю рабочий лист", 0.3),
    ("shot.js", "📸 Снимаю задание", 0.45),
    ("answers.json", "🧠 Записываю ответы", 0.6),
    ("draft.txt", "📝 Пишу ответ учителю", 0.6),
    ("draw.py", "✍️ Пишу от руки", 0.75),
]


def step_of(name, inp):
    """Вызов инструмента Claude -> (подпись, доля) для живой карточки или None."""
    what = str(inp.get("command") or inp.get("file_path") or "")
    for key, label, frac in STEPS:
        if key in what:
            return label, frac
    if name == "Read" and drafts.IMAGE.search(what):
        return "🖼 Разглядываю картинки", 0.3
    if name == "Read":
        return "🔍 Изучаю материалы", 0.25
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
        send(chat, out, menu=True, raw_html=ok and "--html" in args)
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
    """Карточка результата: счёт крупно с полоской, название, ответ Claude цитатой, время. -> (текст, полный балл?)"""
    score, body = parse_score(out)
    full = bool(score) and score[2] >= 100
    if score:
        got, total, pct = score
        mark = "🏆" if full else "✅" if pct >= 70 else "📊"
        lines = [f"{mark} <b>{num(got)} / {num(total)}</b> · {pct}%" if total else f"{mark} <b>{pct}%</b>",
                 f"<code>{bar(pct / 100)}</code>"]
    else:
        lines = ["👀 <b>Ответы</b>" if show else "✅ <b>Готово</b>"]
    if title:
        lines.append(f"<i>{esc(title)}</i>")
    if body:
        quote = "blockquote expandable" if len(body) > 600 else "blockquote"
        lines += ["", f"<{quote}>{md(body)}</blockquote>" if len(body) < 3000 else md(body)]
    if secs:
        lines += ["", f"⏱ {mmss(secs)}" + (" · на сайт не отправлял" if show else "")]
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
        live.step("🤖 Запускаю Claude", 0.05)
        try:
            ok, out = solve(url, text, show, on_tool=live.tool)
        finally:
            live.done()
        title = drafts.task_info(task)[1]
        if "НУЖЕН_АЛФАВИТ" in out:
            drafts.clear(task)
            react(chat, mid, "✍")
            send(chat, "✍️ <b>Нужен твой почерк</b>\n\nЭто задание решается от руки, а твоего алфавита у меня ещё нет. "
                       f"Нажми «{BTN_HAND}», заполни 4 листа и пришли ссылку на задание снова.", menu=True, raw_html=True)
            return
        if not ok:
            react(chat, mid, "🤔")
            send(chat, f"😕 <b>Не получилось</b>\n<i>{esc(title)}</i>\n\n{esc(out)}" if title else out, menu=True, raw_html=bool(title))
            return
        card, full = result_card(out, title, time.time() - t0, show)
        has_draft = drafts.fresh(task, t0)
        react(chat, mid, "🏆" if full else "✍" if has_draft else "👍")
        send(chat, card, menu=True, raw_html=True, effect=EFFECT_CONFETTI if full else None)
        if has_draft:
            offer_draft(chat, task, url)
    finally:
        lock.release()


# ---------- Черновики для учителя ----------

DRAFT_KB = json.dumps({"inline_keyboard": [[
    {"text": "✅ Отправить учителю", "callback_data": "send"},
    {"text": "❌ Не отправлять", "callback_data": "drop"},
]]})


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
    quote = "blockquote expandable" if len(text) > 700 else "blockquote"  # длинный текст свёрнут, раскрывается по нажатию
    send(chat, f"📄 <b>Черновик для учителя</b>\n<i>{esc(d['title'])}</i>\n\n"
               + (f"<{quote}>{esc(text)}</{quote.split()[0]}>\n" if text else "")
               + (f"🖼 {n} {plural(n, 'картинка', 'картинки', 'картинок')} — выше\n" if n else "")
               + "\n<i>✏️ Поправь: пришли новый текст, просьбу («убери второй абзац») или свои фото</i>",
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
            send(chat, "Не получилось поправить:\n" + out, markup=DRAFT_KB)
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
    send(chat, f"🖼 Фото добавил — всего {n} {plural(n, 'картинка', 'картинки', 'картинок')}.\n"
               "Когда всё — жми «✅ Отправить учителю».", markup=DRAFT_KB)


def submit_draft(chat):
    if not lock.acquire(blocking=False):
        send(chat, BUSY, markup=DRAFT_KB)
        return
    try:
        d = draft()
        if not d:
            send(chat, "Черновика уже нет.", menu=True)
            return
        if not os.path.exists(drafts.text_path(d["task"])):
            drafts.write(d["task"], "")
        live = Live(chat, "Отправляю учителю", esc(d["title"]), progress=False)
        try:
            ok, out = node("horo/answer.js", d["url"], drafts.text_path(d["task"]), *drafts.files(d["task"]), timeout=600)
        finally:
            live.done()
        if ok:
            send(chat, f"📬 <b>Отправлено учителю</b>\n<i>{esc(d['title'])}</i>\n\n"
                       f"<blockquote expandable>{esc(out.strip())}</blockquote>", menu=True, raw_html=True, effect=EFFECT_LIKE)
        else:
            send(chat, out, menu=True)
        if not ok:
            send(chat, "⚠️ Не отправилось. Черновик остался, можно нажать ещё раз.", markup=DRAFT_KB)
            return
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
    return (f"🧠 <b>Запомнил, как ты пишешь</b>\n<blockquote>{esc(out.strip())}</blockquote>" if ok
            else "Правки сохранил, но обобщить не вышло:\n" + esc(out))


def on_callback(cq):
    chat = cq["message"]["chat"]["id"]
    if (cq.get("data") or "").startswith("hand:"):
        try:
            tg("answerCallbackQuery", callback_query_id=cq["id"])
        except urllib.error.HTTPError as e:
            print("callback:", e)
        _, key, *sign = cq["data"].split(":")
        if key == "ok":
            mode.pop(chat, None)
            send(chat, "✅ Запомнил стиль. Задания «от руки» теперь пишу так.", menu=True)
        else:
            adjust_hand(key, int(sign[0]))
            threading.Thread(target=send_sample, args=(chat, "🔄 Перерисовал."), daemon=True).start()
        return
    try:
        tg("answerCallbackQuery", callback_query_id=cq["id"])
        tg("editMessageReplyMarkup", chat_id=chat, message_id=cq["message"]["message_id"],  # убрать кнопки: не нажать дважды
           reply_markup=json.dumps({"inline_keyboard": []}))
    except urllib.error.HTTPError as e:
        print("callback:", e)
    if (cq.get("data") or "").startswith("all:"):
        mode.pop(chat, None)
        rows = pending.pop(chat, [])
        if cq["data"] == "all:yes" and rows:
            threading.Thread(target=run_all, args=(chat, rows), daemon=True).start()
        else:
            send(chat, "👌 Отменил.", menu=True)
        return
    if not draft():
        send(chat, "Этот черновик уже не актуален.", menu=True)
    elif cq.get("data") == "send":
        threading.Thread(target=submit_draft, args=(chat,), daemon=True).start()
    elif cq.get("data") == "drop":
        set_draft(None)
        send(chat, "👌 Не отправляю. Если передумаешь, пришли ссылку ещё раз.", menu=True)


# ---------- Почерк ----------

ALPHA_DIR = os.path.join(REPO, "horo", "tmp", "alphabet")
GLEB_EXTRA = os.path.join(REPO, "horo", "tmp", "fonts", "gleb_extra.npz")  # буквы из его алфавита


def send_alphabet(chat):
    os.makedirs(ALPHA_DIR, exist_ok=True)
    send(chat, "✍️ <b>Алфавит твоим почерком</b>\n\n"
               "1. Сохрани 4 листа ниже: русские буквы (2 листа), латиница, цифры и знаки.\n"
               "2. На iPad открой лист в «Фото» → Править → Разметка.\n"
               "3. Пиши синим или белым, на линии. В рамке слева — большая буква, справа — маленькая. Пустые клетки можно оставить.\n"
               "4. Не обрезай лист: розовые квадраты по углам должны быть видны.\n"
               "5. Пришли листы сюда, потом напиши «готово».", raw_html=True)
    for page in (1, 2, 3, 4):
        out = os.path.join(ALPHA_DIR, f"alphabet_{page}.png")
        ok, err = py("horo/alphabet.py", "template", str(page), out)
        if not ok:
            send(chat, err, menu=True)
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
HAND_KB = json.dumps({"inline_keyboard": [
    [{"text": "😬 Небрежнее", "callback_data": "hand:mess:1"}, {"text": "✨ Аккуратнее", "callback_data": "hand:mess:-1"}],
    [{"text": "🖊 Толще", "callback_data": "hand:width:1"}, {"text": "✏️ Тоньше", "callback_data": "hand:width:-1"}],
    [{"text": "🔍 Крупнее", "callback_data": "hand:size:1"}, {"text": "🔎 Мельче", "callback_data": "hand:size:-1"}],
    [{"text": "✅ Нравится", "callback_data": "hand:ok"}],
]})


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
    """Пример решённого задания его почерком с кнопками настройки."""
    out = os.path.join(ALPHA_DIR, "sample.png")
    os.makedirs(ALPHA_DIR, exist_ok=True)
    ok, err = py("horo/alphabet.py", "sample", out)
    if not ok:
        send(chat, err, menu=True)
        return
    mode[chat] = "hand_tune"
    send_file(chat, out)
    send(chat, (note + "\n\n" if note else "") + "✍️ <b>Так буду писать решения от руки.</b>\n"
         "Поправь кнопками или словами («чуть небрежнее», «тоньше», «крупнее», «с наклоном») — "
         "перерисую. Когда нравится — «✅ Нравится».", markup=HAND_KB, raw_html=True)


def tune_hand(chat, text):
    """Просьба словами: «чуть небрежнее и тоньше» -> меняет стиль и присылает новый пример."""
    low = text.lower()
    if low.strip(".! ") in ("ок", "ok", "да", "нравится", "подтверждаю", "готово", "отлично", "норм"):
        mode.pop(chat, None)
        send(chat, "✅ Запомнил стиль. Задания «от руки» теперь пишу так.", menu=True)
        return
    half = any(w in low for w in ("чуть", "немного", "слегка"))
    changed = [(key, sign) for words, key, sign in HAND_WORDS if any(w in low for w in words)]
    if not changed:
        send(chat, "🤔 Не понял, что поменять. Можно: небрежнее, аккуратнее, толще, тоньше, крупнее, мельче, "
                   "с наклоном, прямее — или «ок».", markup=HAND_KB)
        return
    for key, sign in changed:
        adjust_hand(key, sign, half)
    send_sample(chat, "🔄 Перерисовал.")


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
            send(chat, f"✅ Добавил знаков: {len(added)}\n" + " ".join(added))
        for e in errors:
            send(chat, f"⚠️ Лист не разобрал: {e}")
        if not ok and not errors:
            send(chat, out)  # непонятная ошибка — как есть
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
            (ok, out), t = timed("🌐 Захожу на сайт", 0.1, lambda: node("horo/grades.js", timeout=180))
            rows.append(f"✅ <b>Сайт</b> · вход за {t}" if ok else "❌ <b>Сайт</b> · " + esc(out.strip()[-300:]))
            bad += not ok
            (ok, out), t = timed("🤖 Спрашиваю Claude", 0.5, lambda: claude("Ответь одним словом: ok", [], timeout=120))
            ok = ok and bool(out.strip())
            rows.append(f"✅ <b>Claude</b> · ответил за {t}" if ok else "❌ <b>Claude</b> · " + esc(out.strip()[-300:]))
            bad += not ok
            (ok, _), t = timed("✍️ Проверяю почерк", 0.9, lambda: py("-c", "import numpy, scipy, skimage, PIL"))
            rows.append("✅ <b>Почерк</b> · библиотеки на месте" if ok else "❌ <b>Почерк</b> · нет библиотек, нужна переустановка (install.sh)")
            bad += not ok
        finally:
            live.done()
        rows.append("✅ <b>Алфавит</b> · заполнен" if os.path.exists(GLEB_EXTRA) else f"✍️ <b>Алфавит</b> · ещё не заполнен ({BTN_HAND})")
        head = "🩺 <b>Проверка</b> · " + ("всё работает ✨" if not bad else f"проблем: {bad}")
        when = git("log", "-1", "--format=%cd", "--date=format:%d.%m %H:%M")
        send(chat, "\n".join([head, "", *rows, "", f"<i>Версия {esc(git('rev-parse', '--short', 'HEAD'))} от {esc(when)}</i>"]),
             menu=True, raw_html=True)
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
            send(chat, out, menu=True)
            return
        tests = [r for r in json.loads(out) if r["auto"] and r["rawStatus"] == "appointed"]
        if not tests:
            send(chat, "🎉 <b>Новых автотестов нет</b>\nВсё, что бот может сдать сам, уже сдано.", menu=True, raw_html=True)
            return
        pending[chat] = tests
        mode[chat] = "confirm_all"
        lines = "\n".join(f"{r.get('icon', '📘')} <b>{esc(r['subj'])}</b> — {esc(r['title'])}" for r in tests)
        kb = json.dumps({"inline_keyboard": [[
            {"text": f"⚡ Сдать все ({len(tests)})", "callback_data": "all:yes"},
            {"text": "✖️ Не надо", "callback_data": "all:no"},
        ]]})
        send(chat, f"⚡ <b>Нашёл автотесты: {len(tests)}</b>\n\n<blockquote>{lines}</blockquote>\n"
                   f"Сдам по очереди, это примерно {len(tests) * 2}–{len(tests) * 3} мин.", markup=kb, raw_html=True)
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
        send(chat, f"⚡ <b>Автотесты сданы</b> · {n}\n⏱ {mmss(time.time() - t0)}\n\n" + "\n".join(rows)
             + "\n\n<blockquote expandable>" + "\n\n".join(details) + "</blockquote>",
             menu=True, raw_html=True, effect=EFFECT_CONFETTI if perfect == n else None)
    finally:
        lock.release()


def update(chat):
    git = lambda *a: subprocess.run(["git", *a], cwd=REPO, capture_output=True, text=True)
    old = git("rev-parse", "HEAD").stdout.strip()
    if git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() != "main":  # старые установки стояли на ветке
        res = git("fetch", "origin", "main")
        if res.returncode == 0:
            res = git("checkout", "-B", "main", "origin/main")
    else:
        res = git("pull", "--ff-only", "origin", "main")
    send(chat, (res.stdout + res.stderr).strip()[-1500:] or "ok")
    if res.returncode != 0:
        return
    if git("diff", "--name-only", old, "HEAD", "--", "package.json", "package-lock.json").stdout.strip():
        subprocess.run(["npm", "install", "--no-audit", "--no-fund"], cwd=REPO, capture_output=True)
    s = load_settings(); s["updated"] = chat; save_settings(s)
    send(chat, "Перезапускаюсь… Напишу, когда поднимусь.")
    tg("getUpdates", offset=offset, timeout=0)  # подтверждаем /update, чтобы не выполнить его повторно
    os._exit(0)  # pm2 сам запустит бота заново


WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]


def digest_loop():
    sent_day = None
    while True:
        now = datetime.datetime.now(MSK)
        s = load_settings()
        if s.get("digest") and now.hour == DIGEST_HOUR and sent_day != now.date():
            sent_day = now.date()
            ok, out = node("horo/status.js", "--html")
            day = f"{WEEKDAYS[now.weekday()]}, {now.day} {MONTHS[now.month - 1]}"
            try:
                send(owner(), f"🔔 <b>Сводка</b> · {day}\n\n" + (out if ok else esc(out)), menu=True, raw_html=True)
            except Exception as e:
                print("digest:", e)
        time.sleep(60)


def handle(msg):
    chat = msg["chat"]["id"]
    text = (msg.get("text") or "").strip()
    text = ALIASES.get(text, text)
    name = msg.get("from", {}).get("first_name") or ""

    if text.startswith("/start"):
        send(chat, f"👋 <b>Привет{', ' + esc(name) if name else ''}!</b>\n"
                   "Я твой помощник по HDP ⚡\n\n"
                   "<blockquote>📝 Решаю и сдаю задания по ссылке\n"
                   "✍️ Пишу ответы учителю — твоим почерком\n"
                   "📋 Слежу за дедлайнами и просрочками\n"
                   f"📊 Считаю оценки · 🔔 сводка в {DIGEST_HOUR}:00</blockquote>\n"
                   "Кинь ссылку на задание или выбери действие 👇", menu=True, raw_html=True)
        return
    if text.startswith("/help"):
        send(chat, HELP, menu=True, raw_html=True)
        return
    if text.startswith("/update"):
        update(chat)
        return
    if text.startswith("/emoji_reset"):
        s = load_settings(); s.pop("emoji", None); save_settings(s)
        send(chat, "Премиум-эмодзи сброшены, снова обычные.", menu=True)
        return
    if text.startswith("/emoji"):
        mode[chat] = "emoji"
        send(chat, "Пришли одним сообщением премиум-эмодзи, которые хочешь видеть вместо обычных "
                   "(например 📝 👀 📋 ❗ ⚡ 💬 📊 🔔 ❓ ✅ 🎉 👋 ⏱ 🤔). Работает, если у тебя есть Telegram Premium.")
        return
    if mode.get(chat) == "emoji":
        mode.pop(chat)
        save_emoji(chat, msg)
        return
    if text.startswith("/alphabet_reset"):
        ok, out = py("horo/alphabet.py", "reset")
        send(chat, out, menu=True)
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
                threading.Thread(target=send_sample, args=(chat, "✍️ Алфавит записал!"), daemon=True).start()
            else:
                send(chat, "Пока ни одного листа не разобрал. Пришли листы алфавита, потом напиши «готово».", menu=True)
            return
        if text not in BUTTONS and not LINK.search(text):
            send(chat, "📸 Жду листы алфавита (скриншот или фото). Закончил — напиши «готово».")
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
            send(chat, "👌 Отменил.", menu=True)
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
    if text == BTN_DO:
        mode.pop(chat, None)
        send(chat, "🔗 <b>Жду ссылку на задание</b>\nРешу и сдам сам. Задания для учителя сначала покажу черновиком.", raw_html=True)
        return
    if text == BTN_SHOW:
        mode[chat] = "show"
        send(chat, "🔗 <b>Жду ссылку на задание</b>\nПокажу ответы, на сайт ничего не отправлю.", raw_html=True)
        return
    if text == BTN_ALL:
        threading.Thread(target=find_auto_tests, args=(chat,), daemon=True).start()
        return
    if text == BTN_DIGEST:
        s = load_settings()
        s["digest"] = not s.get("digest")
        save_settings(s)
        send(chat, (f"🔔 <b>Сводка включена</b>\nКаждый день в {DIGEST_HOUR}:00 по Москве пришлю, что надо сделать."
                    if s["digest"] else "🔕 <b>Сводка выключена</b>\nВключить снова — та же кнопка."),
             menu=True, raw_html=True)
        return

    m = LINK.search(text)
    if not m:
        send(chat, "🤔 Не понял. Пришли ссылку на задание или выбери действие внизу.", menu=True)
        return
    show = mode.pop(chat, None) == "show"
    threading.Thread(target=run_task, args=(chat, m.group(0), text, show, msg.get("message_id")), daemon=True).start()


def setup_profile():
    """Меню команд, описание и подпись бота в Telegram (видно в пустом чате и в профиле)."""
    quiet("setMyCommands", commands=json.dumps([{"command": c, "description": d} for c, d in COMMANDS]))
    quiet("setMyDescription", description=ABOUT)
    quiet("setMyShortDescription", short_description=ABOUT_SHORT)


def main():
    global offset
    threading.Thread(target=digest_loop, daemon=True).start()
    threading.Thread(target=setup_profile, daemon=True).start()
    print("Бот запущен")
    print("alphabet renorm:", py("horo/alphabet.py", "renorm"))  # старый алфавит — к размеру каждой буквы
    s = load_settings()
    if s.pop("updated", None):  # поднялись после /update
        save_settings(s)
        try:
            head = subprocess.run(["git", "log", "-1", "--format=%h %s"], cwd=REPO, capture_output=True, text=True).stdout.strip()
            send(owner(), f"✨ <b>Обновился</b>\n<i>{esc(head)}</i>", menu=True, raw_html=True)
        except Exception as e:
            print("startup:", e)
    while True:
        try:
            upd = tg("getUpdates", offset=offset, timeout=60)
        except Exception as e:  # сеть моргнула — пробуем снова
            print("getUpdates:", e)
            time.sleep(5)
            continue
        for u in upd.get("result", []):
            offset = u["update_id"] + 1
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
