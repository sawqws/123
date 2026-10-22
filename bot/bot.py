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
import base64
import datetime
import html
import io
import json
import os
import random
import shutil
import re
import signal
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from zoneinfo import ZoneInfo

import drafts
import prep
import sitecheck
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
TIMEOUT = 20 * 60  # секунд на одно задание (тест сдаётся «как человек» — паузы до 6 мин)
MSK = ZoneInfo("Europe/Moscow")
# 🛡 Страж HDP в Главном (hub/guard.py) читает эти два файла: журнал ошибок и «пульс» цикла приёма сообщений
JOURNAL = os.path.join(REPO, "horo", "tmp", "journal.jsonl")
HEARTBEAT = os.path.join(REPO, "horo", "tmp", "heartbeat")
JOURNAL_MAX = 512 * 1024
journal_lock = threading.Lock()

MAX_PARALLEL = int(os.environ.get("HDP_PARALLEL") or 3)  # сколько заданий решать одновременно (память сервера 2 ГБ)


class Slots:
    """Как Lock, но пускает до n дел сразу. locked() — все места заняты."""

    def __init__(self, n):
        self.n, self.used, self.cond = n, 0, threading.Condition()

    def acquire(self, blocking=True):
        with self.cond:
            while self.used >= self.n:
                if not blocking:
                    return False
                self.cond.wait()
            self.used += 1
            return True

    def release(self):
        with self.cond:
            self.used = max(0, self.used - 1)
            self.cond.notify()

    def locked(self):
        with self.cond:
            return self.used >= self.n

    def busy(self):
        with self.cond:
            return self.used


lock = Slots(MAX_PARALLEL)
running = set()  # задания, которые решаются прямо сейчас (одно и то же дважды не запускаем)
running_lock = threading.Lock()
settings_lock = threading.RLock()  # настройки правят параллельные задания
offset = 0    # следующий update_id из Telegram
mode = {}     # chat -> "show" | "confirm_all" | "emoji" | "alphabet" | "hand_tune"
pending = {}  # chat -> список ссылок для «Сдать все автотесты»
AUTO_STATUSES = ("appointed", "reworking")  # «Пора начать» и «На доработке» (порог не добит — сайт даёт «Попробовать ещё раз»)
SUP = None    # 🛠 поддержка (common/support.py) — ставит main(), ей же пользуется приложение

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
BTN_PREP = "📖 Подготовка"
BTN_EXAMS = "🎯 Подтверждения"
BTN_WAIT = "⏳ Ожидание"
BTN_MODE = "⚙️ Режим"
BTN_DELAY_ON, BTN_DELAY_OFF = "⏳ Задержка: вкл", "⏳ Задержка: выкл"  # старые надписи (клавиатура обновится сама)
SEND_CHOICES = [("0", "сразу"), ("2-5", "2–5 мин"), ("4-12", "4–12 мин"), ("15-40", "15–40 мин")]  # отправка учителю
SEND_LABELS = {f"⏳ Отправка: {lab}" for _, lab in SEND_CHOICES}
ALIASES = {  # старые надписи кнопок (клавиатура у Глеба обновится со следующим сообщением)
    "📋 Что надо сделать": BTN_LIST, "⚡ Сдать все автотесты": BTN_ALL, "💬 Комментарии учителя": BTN_COMM,
    "📊 Мои оценки": BTN_GRADES, "✍️ Мой почерк": BTN_HAND,
}
BUTTONS = {BTN_DO, BTN_SHOW, BTN_LIST, BTN_LATE, BTN_ALL, BTN_COMM, BTN_GRADES, BTN_HAND, BTN_CHECK, BTN_HELP,
           BTN_APP, BTN_MORE, BTN_PREP, BTN_EXAMS, BTN_WAIT, BTN_MODE, BTN_DELAY_ON, BTN_DELAY_OFF, *SEND_LABELS}


def menu_kb():
    """Клавиатура у поля ввода: главное действие первым, редкое — в «Ещё» (там же просрочки,
    проверка и помощь; «✍️ Почерк» — и там, и на клавиатуре). Старые кнопки (❗ Просрочки…) по-прежнему понимаются текстом. Сам бот не пишет (сводку убрали).
    В режиме «📱 приложение» главная кнопка — открыть приложение (web_app) и 2–3 самых нужных действия;
    в режиме «💬 чат» приложение не предлагается (кнопка «HDP» слева от поля ввода остаётся)."""
    b = lambda text, style=None: {"text": text, **({"style": style} if style else {})}
    if ui_mode() == "app" and APP_URL:
        rows = [
            [{"text": "📱 Приложение", "web_app": {"url": APP_URL + "/"}, "style": "primary"}],
            [b(BTN_DO), b(BTN_LIST), b(BTN_EXAMS)],  # подготовка — в приложении и /prep
            [b(BTN_MODE), b(BTN_MORE)],  # задержка в режиме приложения — в его настройках
        ]
    else:
        rows = [
            [b(BTN_DO, "primary"), b(BTN_SHOW)],
            [b(BTN_LIST), b(BTN_GRADES), b(BTN_COMM)],
            [b(BTN_EXAMS), b(BTN_WAIT), b(BTN_PREP)],  # подтверждения 2.0/3.0 и что скоро уйдёт учителю (Глеб, 15.10)
            [b(BTN_ALL), b(BTN_HAND)],  # почерк — на виду (Глеб искал его, когда он был только в «Ещё»)
            [b(BTN_MODE), b(btn_delay()), b(BTN_MORE)],  # задержка — отдельной кнопкой, не только /delay
        ]
    return json.dumps({
        "keyboard": rows,
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
    "<b>Ожидание</b> — что скоро уйдёт учителю (во сколько), черновики и задания в работе\n"
    "<b>Подтверждения</b> — все подтверждения и контрольные 2.0 и 3.0: что сдано, что на доработке\n"
    "<b>Подготовка</b> — к проверочной по тому, что прошли: за 1, 5 или 10 минут (или по своему ТЗ)\n"
    "<b>Голосовое</b> — как текст\n"
    "<b>Фото, скриншот</b> — найду задание и решу; при черновике — фото в него\n"
    "<b>Видео</b> — кадры как фото, речь как текст\n"
    "<b>Ещё</b> — просрочки, почерк, проверка\n"
    "/style — почерк · /update — обновиться · /emoji — премиум-эмодзи\n"
    "/site — сайт как HDP, с кнопками «Сделать» и «Подготовь меня»</blockquote>\n"
    "<i>Всё то же — в приложении: кнопка «HDP» слева от поля ввода</i>"
)
COMMANDS = [  # меню команд у поля ввода
    ("start", "Главное меню"), ("exams", "Подтверждения и контрольные 2.0 / 3.0"),
    ("wait", "Что скоро отправится учителю"),
    ("prep", "Подготовка к проверочной"), ("app", "Открыть приложение"),
    ("site", "Сайт HDP в браузере"),
    ("mode", "Чат или приложение"),
    ("help", "Что я умею"), ("check", "Проверить, что всё работает"), ("sitecheck", "Проверка сайта от А до Я"),
    ("style", "Настроить почерк"), ("alphabet", "Заполнить алфавит почерка"),
    ("send", "Когда отправлять учителю"), ("delay", "Сдача как человек: вкл/выкл"),
    ("update", "Обновить бота"), ("emoji", "Премиум-эмодзи"),
]
ABOUT = ("Помощник по HDP. Решаю и сдаю задания по ссылке, пишу ответы учителю твоим почерком, "
         "слежу за дедлайнами, комментариями и оценками.")
ABOUT_SHORT = "Решаю задания HDP, слежу за дедлайнами и оценками"
BUSY = "⏳ Занят другим заданием — пришли ещё раз через пару минут"
CANCEL = "x:cancel"  # «✕ Отмена» под карточками, которые ждут ввода (ссылку, ТЗ, эмодзи, листы алфавита)


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


def cancel_kb():
    return kb([("✕ Отмена", CANCEL)])


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


HARMLESS = ("message is not modified", "message to edit not found", "message to delete not found",
            "message can't be edited", "message can't be deleted", "message_id_invalid", "message to react not found",
            "query is too old", "query id is invalid")  # устарело или уже сделано — не ошибка


def tg_why(e):
    """Описание ошибки Telegram (нижним регистром): из тела ответа HTTPError, иначе текст исключения."""
    why = getattr(e, "_why", None)
    if why is not None:
        return why
    body = b""
    try:
        body = e.read() if hasattr(e, "read") else b""
    except Exception:
        pass
    try:
        why = str(json.loads(body).get("description") or "")
    except Exception:
        why = body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body)
    why = (why or str(e)).lower()
    try:
        e._why = why
    except Exception:
        pass
    return why


def quiet(method, **params):
    """Вызов Telegram, ошибка которого не важна (правка, удаление, реакция)."""
    try:
        return call(method, **params)
    except Exception as e:
        why = tg_why(e)
        if not any(h in why for h in HARMLESS):
            print(method, e, "—", why[:200])


def edit(chat, mid, text, markup=None, resend=True):
    """Правка сообщения на месте. Не изменилось — молча; разметку не принял — тем же текстом без неё; сообщение
    удалено или не правится — resend: то же новым сообщением (карточку живого статуса так не воскрешаем)."""
    if not mid:
        return
    if len(text) > 4000:
        text = split(text)[0]
    params = dict(chat_id=chat, message_id=mid, text=premium(text, True), parse_mode="HTML",
                  disable_web_page_preview="true", **({"reply_markup": markup} if markup else {}))
    try:
        call("editMessageText", **params)
        return
    except urllib.error.HTTPError as e:
        why = tg_why(e)
        if "not modified" in why:
            return
        if "parse entities" in why or "can't parse" in why or "unsupported start tag" in why:
            params.pop("parse_mode")
            params["text"] = html.unescape(re.sub(r"<[^>]+>", "", text))[:4000]
            try:
                call("editMessageText", **params)
            except Exception as e2:
                if "not modified" not in tg_why(e2):
                    print("editMessageText (без разметки):", e2)
            return
        if resend and any(w in why for w in ("message to edit not found", "message can't be edited", "message_id_invalid")):
            try:
                send(chat, text, markup=markup, raw_html=True)
            except Exception as e2:
                print("edit -> send:", e2)
            return
        if not any(h in why for h in HARMLESS):
            print("editMessageText", e, "—", why[:200])
    except Exception as e:
        print("editMessageText", e)


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
        self.task, self.span = None, (0.0, 1.0)  # задание (для «вопрос 5 из 12» при сдаче) и его доля общей полоски
        self.t0, self.tick = time.time(), 0
        self.stop = threading.Event()
        self.lock = threading.Lock()  # правка (тикер и шаги Claude) не должна обгонять удаление карточки
        Live.active = self  # приложение показывает ту же работу, что и карточка в чате
        Live.items.append(self)
        self.mid = send(chat, self.render(), raw_html=True)
        quiet("sendChatAction", chat_id=chat, action="typing")
        REAL_THREAD(target=self.loop, daemon=True).start()

    def submitting(self):
        """Сдача теста идёт минутами (паузы «как человек» над каждым вопросом), а шаг Claude один — «Сдаю на сайт».
        submit.js пишет, на каком он вопросе (horo/tmp/<задание>/submit.json): (n, N) или None."""
        if self.current != SUBMIT_STEP or not self.task:
            return None
        try:
            with open(os.path.join(drafts.TMP, self.task, "submit.json")) as f:
                p = json.load(f)
            n, total = int(p.get("n") or 0), int(p.get("N") or 0)
            if float(p.get("at") or 0) / 1000 < self.t0:
                return None  # файл от прошлой сдачи этого задания
        except (OSError, ValueError, TypeError, AttributeError):
            return None
        return (min(n, total), total) if total > 0 else None

    def render(self):
        """◐ <b>Решаю задание</b> / цитата: название и шаги (✓ сделано, ▸ сейчас) / полоска, проценты, время."""
        head = f"{SPIN[self.tick % len(SPIN)]} <b>{self.title}</b>"
        clock = mmss(time.time() - self.t0)
        current = self.current
        sub = self.submitting()
        if sub:
            n, total = sub
            current = (f"{SUBMIT_STEP} · вопрос {min(n + 1, total)} из {total}" if n < total
                       else f"{SUBMIT_STEP} · отправляю")
            lo, hi = self.span
            self.frac = max(self.frac, min(lo + (0.88 + 0.1 * n / total) * (hi - lo), 0.99))
        steps = [f"✓ {s}" for s in self.done_steps[-3:]] + ([f"▸ <b>{current}</b>"] if current else [])
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
        with self.lock:
            if self.stop.is_set():  # работа закончена, карточка удалена или удаляется
                return
            if self.sub_fn and not self.sub:
                self.sub = self.sub_fn() or ""
            edit(self.chat, self.mid, self.render(), resend=False)

    def loop(self):
        while not self.stop.wait(3):
            self.tick += 1
            self.refresh()
            if self.tick % 2 == 0:
                quiet("sendChatAction", chat_id=self.chat, action="typing")

    def done(self):
        self.stop.set()
        if self in Live.items:
            Live.items.remove(self)
        if Live.active is self:  # параллельно идут несколько заданий — показываем самое свежее из оставшихся
            Live.active = Live.items[-1] if Live.items else None
        with self.lock:
            delete(self.chat, self.mid)

    def state(self):
        """Для приложения: то же, что на карточке, без HTML."""
        sub = self.submitting()
        step = f"{SUBMIT_STEP} · вопрос {min(sub[0] + 1, sub[1])} из {sub[1]}" if sub and sub[0] < sub[1] else self.current
        return {"title": plain(self.title), "sub": plain(self.sub), "step": plain(step),
                "done": [plain(x) for x in self.done_steps[-3:]], "frac": self.frac if self.progress else None,
                "secs": int(self.seconds())}

    def seconds(self):
        return time.time() - self.t0


Live.active = None
Live.items = []  # все идущие сейчас карточки (заданий до HDP_PARALLEL, плюс проверка, подготовка…)


def plain(x):
    """HTML бота -> обычный текст (для приложения)."""
    return html.unescape(re.sub(r"<[^>]+>", "", str(x or ""))).strip()


def send_file(chat, path, caption="", as_file=False):
    """Картинку — фото, остальное (и если фото не приняли) — файлом. as_file: картинку тоже файлом (без сжатия)."""
    with open(path, "rb") as f:
        data = f.read()
    photo = drafts.IMAGE.search(path) and not path.lower().endswith(".pdf") and not as_file
    kinds = ([("sendPhoto", "photo")] if photo else []) + [("sendDocument", "document")]
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
    with settings_lock:
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
    with settings_lock:
        tmp = f"{SETTINGS}.{threading.get_ident()}"
        try:
            with open(tmp, "w") as f:
                json.dump(s, f)
            os.replace(tmp, SETTINGS)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise


def owner():
    return ALLOWED or int(load_settings().get("owner") or 0)


# ---------- Режим: 💬 чат или 📱 приложение ----------
# Выбирается при первом /start, меняется по /mode, кнопкой «⚙️ Режим» и из приложения («💬 Перейти в чат»).
# «чат» — всё кнопками здесь, приложение не предлагается; «приложение» — главная кнопка открывает его,
# но команды и текст в чате работают всё равно. Хранится в settings.json («mode»).

MODES = {"chat": "💬 чат", "app": "📱 приложение"}


def ui_mode():
    m = load_settings().get("mode")
    return m if m in MODES else ""


def set_mode(m):
    if m not in MODES:
        raise ValueError("Режим — chat или app")
    with settings_lock:
        s = load_settings(); s["mode"] = m; save_settings(s)


def mode_note():
    """Подпись режима для карточки /start (одной строкой, курсивом — card() сам ставит <i>)."""
    return f"Режим: {MODES.get(ui_mode(), 'не выбран')} · сменить — /mode"


def mode_card():
    return card("Как удобнее?", "💬 <b>В чате</b> — всё кнопками и командами здесь\n"
                "📱 <b>Приложение</b> — задания, оценки и учителя в одном окне",
                "Поменять можно в любой момент: /mode", "🧭")


def mode_kb():
    return kb([("💬 В чате", "mode:chat", "primary"), ("📱 Приложение", "mode:app", "primary")])


def start_card(chat, name=""):
    """Главный экран по режиму: в чате — приветствие с полной клавиатурой, в приложении — коротко и кнопка
    открыть приложение (в клавиатуре у поля ввода, menu_kb)."""
    hi = f"Привет{', ' + esc(name) if name else ''}!"
    if ui_mode() == "app" and APP_URL:
        send(chat, card(hi, "Всё — в приложении: задания, оценки, учителя, подготовка. "
                        "Ссылку на задание можно прислать и сюда.", mode_note(), "👋"), menu=True, raw_html=True)
    else:
        send(chat, card(hi, "Пришли ссылку на задание — решу и сдам. Можно голосом или скриншотом.",
                        mode_note(), "👋"), menu=True, raw_html=True)


def on_mode(chat, mid, cq_id, key):
    quiet("answerCallbackQuery", callback_query_id=cq_id)
    if key not in MODES:
        return
    set_mode(key)
    edit(chat, mid, card("Режим: " + MODES[key], note="Сменить — /mode или кнопка «⚙️ Режим»", icon="✅"))
    start_card(chat)


# ---------- Работа ----------

def child_env(**extra):
    """Окружение для скриптов и Claude: без токена бота (им он не нужен; задание с сайта — чужой текст, а Claude
    умеет запускать код)."""
    env = {k: v for k, v in os.environ.items() if k != "TELEGRAM_TOKEN"}
    env.update(extra)
    return env


GROUPS = os.path.join(REPO, "horo", "tmp", "claude_groups.json")
groups_lock = threading.Lock()


def track_group(pid, alive):
    """Группы процессов Claude (у каждого своя сессия): pm2 при перезапуске убивает только бота, а Claude с его
    submit.js/Chromium жили дальше и сдавали тот же тест параллельно с новым запуском (15.10, «кто-то завершил
    попытку»). Записываем группы, при старте добиваем оставшиеся (kill_orphans)."""
    with groups_lock:
        try:
            with open(GROUPS) as f:
                ids = set(json.load(f))
        except (OSError, ValueError, TypeError):
            ids = set()
        (ids.add if alive else ids.discard)(pid)
        try:
            with open(GROUPS + ".part", "w") as f:
                json.dump(sorted(ids), f)
            os.replace(GROUPS + ".part", GROUPS)
        except OSError:
            pass


def kill_orphans():
    """При старте бота: Claude прошлого запуска, если ещё жив, — остановить вместе с его node и Chromium."""
    try:
        with open(GROUPS) as f:
            ids = json.load(f)
    except (OSError, ValueError):
        return 0
    n = 0
    for pgid in ids:
        try:
            with open(f"/proc/{pgid}/cmdline", "rb") as f:
                alive = b"claude" in f.read()
        except OSError:
            alive = False
        if alive:  # pid мог достаться другому процессу — убиваем, только если это всё ещё Claude
            try:
                os.killpg(pgid, signal.SIGKILL)
                n += 1
            except OSError:
                pass
    try:
        os.remove(GROUPS)
    except OSError:
        pass
    return n


def journal(kind, text, detail=""):
    """Ошибка — строкой в horo/tmp/journal.jsonl: Глеб видит одну строку «⚠️», а Страж HDP в Главном — всё,
    с трассировкой. kind: bug (упал код), script (скрипт сайта), claude, net (сеть). Не мешает работе."""
    rec = {"at": round(time.time()), "kind": kind, "text": str(text)[:400], "detail": str(detail)[-3000:]}
    try:
        with journal_lock:
            if os.path.exists(JOURNAL) and os.path.getsize(JOURNAL) > JOURNAL_MAX:
                os.replace(JOURNAL, JOURNAL + ".1")
            with open(JOURNAL, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        print("journal:", e)


def beat():
    """«Пульс» цикла приёма сообщений: давно не обновлялся — бот завис (Страж перезапустит)."""
    try:
        with open(HEARTBEAT, "w") as f:
            f.write(str(round(time.time())))
    except OSError:
        pass


def node(*args, timeout=300, prog="node"):
    name = " ".join(str(a) for a in args[:1])
    try:
        res = subprocess.run([prog, *args], cwd=REPO, capture_output=True, text=True, timeout=timeout, env=child_env())
    except subprocess.TimeoutExpired:
        journal("script", f"{prog} {name}: не уложился в {timeout} с")
        return False, "⚠️ Сайт не ответил вовремя, попробуй позже."
    except FileNotFoundError:
        journal("script", f"нет {prog}")
        return False, f"⚠️ На сервере нет {prog} — нужна переустановка (install.sh)."
    if res.returncode != 0:
        print(prog, *args, "->", res.returncode, (res.stderr or res.stdout)[-3000:])  # подробности — в лог pm2
        journal("script", f"{prog} {name} -> {res.returncode}: " + explain(res.stderr or res.stdout)[2:].strip(),
                (res.stderr or "") + "\n" + (res.stdout or "")[-1500:])
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
        proc = subprocess.Popen(cmd, cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                start_new_session=True,  # своя группа: по таймауту убиваем и node/Chromium, что он запустил
                                env=child_env(BASH_DEFAULT_TIMEOUT_MS="900000", BASH_MAX_TIMEOUT_MS="900000",
                                              HORO_DEFER="1"))
        # HORO_DEFER: submit.js вносит ответы, но тест не сдаёт — сдаёт бот сам позже (finish_test)
        track_group(proc.pid, True)
    except FileNotFoundError:
        return False, "⚠️ Claude Code не установлен (npm install -g @anthropic-ai/claude-code)."
    late, finished = threading.Event(), threading.Event()

    def watchdog():
        if not finished.wait(timeout):
            late.set()
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (OSError, AttributeError):
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
        track_group(proc.pid, False)
        proc.stdout.close()
    if late.is_set():
        return False, f"⚠️ Не успел за {timeout // 60} минут, остановил."
    if proc.returncode != 0 or not result or result.get("is_error"):
        log = "".join(other)[-3000:] + (json.dumps(result, ensure_ascii=False)[-2000:] if result else "")
        print("claude ->", proc.returncode, log)  # подробности — в лог pm2
        journal("claude", f"claude -> {proc.returncode}", log)
        low = log.lower()
        if any(w in low for w in ("login", "api key", "401", "unauthorized", "authenticat")):
            return False, "⚠️ Claude на сервере не вошёл в аккаунт. Напиши мне (Claude Code), я войду заново."
        if any(w in low for w in ("rate limit", "usage limit", "limit reached", "429", "overloaded")):
            return False, "⚠️ У Claude закончился лимит или он перегружен. Попробуй через час."
        return False, "⚠️ Claude не справился с заданием. Попробуй ещё раз."
    return True, result.get("result") or ""


SUBMIT_STEP = "Сдаю на сайт"
STEPS = [  # (что в команде или пути, подпись шага, доля готовности)
    ("submit.js", SUBMIT_STEP, 0.88),
    ("answer.js", "Отправляю учителю", 0.9),
    ("fetch.js", "Читаю условие", 0.2),
    ("pdftoppm", "Листаю рабочий лист", 0.3),
    ("pdftotext", "Листаю рабочий лист", 0.3),
    ("shot.js", "Снимаю задание", 0.45),
    ("answers.json", "Записываю ответы", 0.6),
    ("draft.txt", "Пишу ответ учителю", 0.6),
    ("check.py", "Перепроверяю решение", 0.85),
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


# Доработка: учитель вернул задание и написал, что доделать («Решить № 3-4»). Глеб хочет, чтобы бот сам понял,
# что именно доделать, и сделал это его почерком (вариант 3, выбрал 14.10: на скрине задания — только ответы,
# всё решение — на тёмном листе в клетку, отдельными картинками).
HANDWRITE = (
    "Письменное решение (математика, физика, задачи) — его почерком через horo/sheet.py: "
    "`node horo/shot.js {url} {d}/shot.png` (снимок только карточки с условием + shot.json с расположением номеров), "
    "затем {d}/draw.py: `import sys; sys.path.insert(0, 'horo'); from sheet import Work; w = Work('{d}')`, "
    "на каждый решённый номер `w.solve(номер, [строки решения])` (строка — элементы Hand.expr: текст и дроби "
    "(числитель, знаменатель); переносы модуль делает сам), в конце `w.save()`. Получится {d}/draft_files/1_task.jpg "
    "(скрин, справа от каждого примера только ответ) и 2_sheet.jpg… (всё решение на тёмном листе в клетку). Скрин сам "
    "не рисуй, не обрезай и не расширяй. Посмотри картинки через Read: ответ не налез на пример, дроби читаются.\n"
    + "Таблицу из условия (картинка с пустыми клетками, «заполни таблицу») заполняй его почерком ПРЯМО НА КАРТИНКЕ, "
    "а не текстом: в {d}/fill.py `import sys; sys.path.insert(0, 'horo'); from table import Table; "
    "t = Table('{d}/imgN.png'); print(t)` (сетка: строки и столбцы с нуля, строка 0 — обычно шапка, столбец 0 — "
    "подписи), `t.write(строка, столбец, 'коротко, как ученик')` в каждую пустую клетку, `t.save('{d}/draft_files/"
    "N_table.jpg')` — каждую картинку таблицы отдельным файлом по порядку; запусти `python3 {d}/fill.py` и посмотри "
    "результат через Read (текст в своих клетках, ничего не налезло). Без «ё», кавычек «» и длинного тире — их нет в его "
    "алфавите. В {d}/draft.txt тогда — только ответы на вопросы вне таблицы, таблицу текстом не дублируй.\n"
    + "{grade}"
)
ONPIC = (  # Глеб, 22.10: «по испанскому надо сделать на скрине, а не в комментариях»
    "Письменное задание по языку (испанский, английский…), где условие — картинка страницы учебника или тетради "
    "(строки для ответа, пропуски, вопросы, «verdadero/falso», «relaciona»): ответы вписывай его почерком ПРЯМО НА "
    "ЭТУ КАРТИНКУ, а не текстом учителю. В {d}/page.py: `import sys; sys.path.insert(0, 'horo'); from onpic import Pic, "
    "notes; p = Pic('{d}/imgN.png'); print(p)` — найденные строки для ответа (№, x, y); `p.ruler('{d}/rulerN.png')` — "
    "копия с сеткой координат и номерами строк, посмотри её через Read; `p.line(№, 'ответ')` — на строку, "
    "`p.write(x, y, 'V')` — в любое место (x — левый край, y — низ букв, в координатах сетки: рядом с пунктом, в пропуск); "
    "`p.save('{d}/draft_files/N_page.jpg')` — каждую картинку с ответами отдельным файлом по порядку. Длинный ответ, "
    "которому нет места на картинке (диалог, письмо, рассказ, ответы на вопросы без строк), — `notes('{d}/draft_files/"
    "N_notes.jpg', ['строка', …])`: лист тем же почерком (номер пункта в начале строки). Запусти `python3 {d}/page.py` "
    "и посмотри КАЖДУЮ картинку через Read: ответ на своей строке/месте, не налез на текст, всё читается; не так — "
    "поправь координаты и перерисуй. Пиши как ученик A2: коротко, просто, без ошибок. {d}/draft.txt тогда ПУСТОЙ.\n"
)
PRIVATE = (  # Глеб, 14.10: в заданиях — ни слова личного о нём
    "Не пиши в ответах ничего личного о Глебе: ни фамилии, ни адреса, телефона, почты, школы и класса, имён и "
    "подробностей о родных и друзьях, здоровья, денег, его ботов, проектов и аккаунтов, переписки. Если задание "
    "просит рассказать о себе или своей семье — пиши обобщённо и нейтрально, без настоящих деталей. Не упоминай, что "
    "помогал ИИ или бот.\n"
)
GRADE8 = (  # Глеб в 8 классе: решение должно быть таким, какое он сам мог написать (и какое ждёт учитель)
    "Решай только тем, что проходят до конца 8 класса (школа РФ): алгебра — дроби и действия с ними, степени, формулы "
    "сокращённого умножения, разложение на множители, квадратные корни, квадратные уравнения (дискриминант, теорема "
    "Виета), дробно-рациональные уравнения (с проверкой, что знаменатель ≠ 0), линейные неравенства и системы, функции "
    "y = kx + b, y = k/x, y = x², y = √x; геометрия — четырёхугольники, площади, теорема Пифагора, подобие, sin/cos/tg "
    "острого угла, вписанные и центральные углы. Без производных, логарифмов, тригонометрии сверх 8 класса, векторов "
    "и метода координат, если в условии их нет, и без приближённых ответов, если ответ можно записать точно.\n"
)
CHECK = (  # вторая, независимая проверка решения от руки — после того как решение нарисовано
    "Ты проверяешь решение ученика 8 класса перед отправкой учителю. Папка задания: {d}. Условие — {d}/task.json и "
    "картинки в {d} (посмотри через Read, shot.png — снимок условия), решение — {d}/draw.py (w.solve(номер, строки), "
    "ответ на скрине — последний кусок после «=» или answer=...), картинки — {d}/draft_files/.\n"
    "Проверь КАЖДЫЙ решённый номер заново, сам, не веря решению: условие переписано верно, каждый переход верный, "
    "ответ правильный и в той форме, что просят (сокращён, корни все, посторонние отброшены). Числа и выражения "
    "проверь подстановкой: напиши {d}/check.py (только стандартная библиотека Python: fractions, math) — например, "
    "подставь 2–3 значения переменных в исходное выражение и в ответ — и запусти `python3 {d}/check.py`.\n"
    + GRADE8 +
    "Нашёл ошибку или метод не из 8 класса — исправь {d}/draw.py и запусти `python3 {d}/draw.py` (картинки "
    "перерисуются), посмотри их через Read. Ошибок нет — ничего не меняй.\n"
    "Последней строкой ответа напиши «ПРОВЕРКА: всё верно» или «ПРОВЕРКА: исправил № … — что было не так» (коротко)."
)
NO_COMMENT = (  # Глеб, 15.10: ответ учителю — без комментариев, если задание не просит ответ словами
    "Текст в {d}/draft.txt пиши, ТОЛЬКО если само задание просит ответить словами (вопрос, на который нужен "
    "письменный ответ, сочинение, конспект, ответы на вопросы вне таблицы/листа). Если весь ответ — на картинках "
    "(решение от руки, заполненная таблица, лист, карта), {d}/draft.txt оставь ПУСТЫМ: никаких «Вот решение», "
    "«Доделал № …», «Готово», пояснений и подписей — учитель получит только картинки.\n"
)
REWORK = (
    "Если статус задания reworking или failed (на доработке / не зачтено) и учитель в «ОТВЕТЫ И КОММЕНТАРИИ УЧИТЕЛЯ» "
    "написал, что доделать (например «Решить № 3-4»), — это доработка. Посмотри через Read его прошлые файлы ответа "
    "(«файл ответа: …», prev*.jpg) и пометки учителя (mark*), пойми, что уже сделано, и сделай ТОЛЬКО то, что просит "
    "учитель; сданное заново не решай (в Work.solve — только эти номера). "
    "В {d}/draft.txt — ничего (пусто), если доделанное — на картинках; без «Доделал № …».\n"
)


RETRY = (  # Глеб, 15.10: «если тест сделан не правильно — проверь всё, удостоверься в ошибке и сделай вторую попытку»
    "Это ВТОРАЯ попытка теста. Прошлая сдана на {got} из {total} баллов (не хватает {miss}). Какие вопросы неверны, "
    "сайт не показывает — только общий балл. Твои ответы прошлой попытки — {d}/answers_try{k}.json.\n"
    "1) Прочитай условие заново (node horo/fetch.js) и посмотри все картинки. 2) Реши КАЖДЫЙ вопрос с нуля, не "
    "заглядывая в прошлые ответы, и только потом сравни. 3) Где ответы разошлись — разберись до конца, какой верный: "
    "перечитай условие, проверь правила сайта из CLAUDE.md (регистр, точка в конце, форма слова, запятая в дробях, "
    "лишние слова, порядок), пересчитай. Неверных вопросов столько, чтобы недобрать {miss} балл(ов) (баллы за вопрос — "
    "в task.json, если есть). 4) Меняй ответ, только если уверен, что прошлый неверный; совпавшие ответы не трогай без "
    "веской причины. 5) Запиши новый {d}/answers.json и запусти submit.js (он внесёт ответы, сдаст бот).\n"
    "В ответе — список: какие вопросы изменил, было → стало и почему (одной строкой на вопрос), и что оставил.\n"
)


def prompt_for(url, text, show, retry=None):
    only_show = show or re.search(r"покажи|не отправляй|только ответ", text, re.I)
    how = (
        "Только реши и покажи ответы, на сайт НЕ отправляй (шаг 3 не делать)."
        if only_show
        else "Реши и внеси ответы (шаг 3): submit.js внесёт их, но тест НЕ сдаст — его сдаст бот позже сам, "
        "строку БАЛЛЫ не пиши. Если ответ неоднозначный, а попытка последняя, submit.js не запускай, а напиши варианты."
    )
    task = drafts.task_id(url)
    d = f"horo/tmp/{task}"
    again = RETRY.format(d=d, **retry) if retry else ""
    return (
        f"Задание: {url}\n"
        f"Сделай его по инструкции из CLAUDE.md. {how}\n" + again +
        f"Если это detailedAnswer, на сайт НЕ отправляй. Сделай черновик: текст для учителя запиши в {d}/draft.txt "
        "(каждая строка — абзац, только то, что пойдёт учителю). " + NO_COMMENT.format(d=d) + "Если нужны картинки (решение от руки на скриншоте, "
        f"заполненный лист), сделай их по CLAUDE.md и положи в {d}/draft_files/. Скрипт рисования клади в {d}/ и запускай "
        f"`python3 {d}/draw.py`. "
        + ("" if os.path.exists(GLEB_EXTRA) else
           "Алфавит почерка Глеба ещё не заполнен: цифры, знаки и латиница — его настоящие, русские буквы — похожим "
           "шрифтом. Решение от руки всё равно рисуй, а в ответе последней строкой напомни: «русские буквы пока не "
           "мои — заполни алфавит (✍️ Почерк)».\n")
        + HANDWRITE.format(url=url, d=d, grade=GRADE8)
        + ONPIC.format(d=d)
        + REWORK.format(url=url, d=d)
        + PRIVATE
        + "".join(f"Пожелание Глеба к этому заданию (выполни, оно важнее общих правил оформления): {w}.\n"
                  for w in ([] if only_show else wishes(text)))
        + f"Сначала прочитай {os.path.relpath(drafts.STYLE, REPO)}, если он есть: это правила Глеба по его прошлым правкам, "
        "они важнее общих. Бот сам покажет черновик Глебу: в ответе текст черновика не повторяй, напиши 1–2 строки, что сделал.\n"
        "Ответ пиши для Telegram, коротко, без markdown-таблиц: что написал по каждому вопросу. Если работа сдана и сайт "
        "показал результат, самой первой строкой напиши его в виде «БАЛЛЫ: 6/7» (или «БАЛЛЫ: 85%»), без других слов."
    )


def hand_fresh(task, since):
    """В этом решении нарисовано решение от руки (draw.py задания)?"""
    draw = os.path.join(drafts.task_dir(task), "draw.py")
    return os.path.exists(draw) and os.path.getmtime(draw) >= since - 1 and bool(drafts.files(task))


def verify(task, on_tool=None):
    """Независимая перепроверка решения от руки (8 класс): ошибки Claude исправляет в draw.py и перерисовывает.
    -> строка для Глеба («Проверка: всё верно» / «Проверка: исправил № 3 — …») или None."""
    d = os.path.relpath(drafts.task_dir(task), REPO)
    ok, out = claude(CHECK.format(d=d), ["Read", "Write", "Edit", f"Bash(python3 {d}/check.py)", f"Bash(python3 {d}/draw.py)"],
                     timeout=600, on_tool=on_tool)
    if not ok:
        return "Перепроверить не вышло — посмотри решение сам"
    m = re.findall(r"ПРОВЕРКА:\s*(.+)", out)
    return "Проверка: " + (m[-1].strip() if m else "готово")


def solve(url, text="", show=False, on_tool=None, retry=None):
    """(успех, ответ Claude)."""
    return claude(prompt_for(url, text, show, retry), SOLVE_TOOLS, on_tool=on_tool)


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


def task_meta(task):
    """Задание из списка тем (overview.json): название, предмет, тема — известны и до того, как Claude открыл задание."""
    for tp in (overview() or {}).get("topics") or []:
        for t in tp.get("tasks") or []:
            if t.get("uuid") == task:
                return {"title": t.get("title") or "", "subj": tp.get("subj") or "", "icon": tp.get("icon") or "📘",
                        "topic": tp.get("uuid") or "", "type": t.get("type") or ""}
    return {}


def task_title(task):
    return drafts.task_info(task)[1] or task_meta(task).get("title", "")


def topic_closed(url):
    """Дата, до которой тема принимала ответы, если срок доработок прошёл (срок темы + дни доработок), иначе None.
    Не удалось узнать — None: решаем как обычно."""
    m = re.search(r"/topic/([\w-]+)/task/([\w-]+)", url)
    if not m:
        return None
    try:
        d = web.mirror.get_json(f"{web.mirror.EDU}/topics/{m.group(1)}/tasks?taskUuid={m.group(2)}")
        sp = ((d or {}).get("data") or {}).get("studyPeriod") or {}
        if not sp.get("deadlineDate") or sp.get("extraDeadlineDays") is None:
            return None
        until = datetime.date.fromisoformat(sp["deadlineDate"]) + datetime.timedelta(days=int(sp["extraDeadlineDays"]))
    except Exception:
        return None
    return until if datetime.datetime.now(MSK).date() > until else None


def run_task(chat, url, text, show=False, mid=None, retry=None):
    """Одно задание. Можно присылать несколько ссылок подряд: до MAX_PARALLEL решаются сразу, остальные ждут очереди."""
    task = drafts.task_id(url)
    with running_lock:
        if task in running:
            send(chat, card("Это задание уже решаю", note="Результат придёт сюда", icon="⏳"), raw_html=True)
            return
        running.add(task)
    until = None if show else topic_closed(url)
    if until:  # платформа не примет: «Прошёл срок доработок после окончания темы, отправка решений невозможна»
        with running_lock:
            running.discard(task)
        title = task_title(task) or "Задание"
        note = f"сдавать можно было до {until:%d.%m} — платформа больше не принимает ответы"
        remember(title, False, f"Срок темы прошёл: {note}", url=url)
        react(chat, mid, "🤷")
        send(chat, card("Срок темы прошёл", note=f"{esc(title)} — {note}", icon="⛔"), menu=True, raw_html=True)
        mark_active(task, None)
        return
    mark_active(task, {"chat": chat, "url": url, "text": text, "show": bool(show), "retry": retry})
    try:
        if not lock.acquire(blocking=False):
            react(chat, mid, "🤔")
            send(chat, card("В очереди", note=f"Уже решаю {MAX_PARALLEL} — начну это, как только одно закончу",
                            icon="⏳"), raw_html=True)
            lock.acquire()
        _run_task(chat, url, text, show, mid, task, retry)
    finally:
        with running_lock:
            running.discard(task)
        mark_active(task, None)
        web.mirror._swr.clear()  # сайт-зеркало: статусы заданий поменялись — показать свежие


def mark_active(task, info):
    """Задания в работе — в настройках: перезапуск бота (/update, «Обновить всех») их не теряет."""
    with settings_lock:
        s = load_settings()
        act = s.setdefault("active", {})
        if info:
            info["tries"] = act.get(task, {}).get("tries", 0)
            info["since"] = act.get(task, {}).get("since") or round(time.time())  # Страж видит зависшие
            act[task] = info
        else:
            act.pop(task, None)
        save_settings(s)


def resume_active():
    """После перезапуска: задания, которые решались, начать заново (не больше двух раз подряд)."""
    with settings_lock:
        s = load_settings()
        act = s.pop("active", None) or {}
        save_settings(s)
    for task, a in act.items():
        title = esc(drafts.task_info(task)[1] or "Задание")
        if a.get("tries", 0) >= 2:
            send(a["chat"], card("Не доделал", note=f"{title} — два перезапуска подряд. Пришли ссылку ещё раз", icon="⚠️"),
                 menu=True, raw_html=True)
            continue
        send(a["chat"], card("Продолжаю после перезапуска", note=f"{title} — решаю заново", icon="🔄"), raw_html=True)
        with settings_lock:  # счётчик попыток — чтобы задание, роняющее бота, не крутилось вечно
            s = load_settings()
            s.setdefault("active", {})[task] = {**a, "tries": a.get("tries", 0) + 1}
            save_settings(s)
        threading.Thread(target=run_task, args=(a["chat"], a["url"], a.get("text", ""), a.get("show", False), None,
                                                a.get("retry")), daemon=True).start()


def _run_task(chat, url, text, show, mid, task, retry=None):
    try:
        t0 = time.time()
        react(chat, mid, "👀")
        drafts.clear(task)
        live = Live(chat, "Смотрю ответы" if show else "Вторая попытка: перепроверяю" if retry else "Решаю задание",
                    esc(task_title(task)),
                    sub_fn=lambda: esc(task_title(task)))
        live.task = task
        live.step("Запускаю Claude", 0.05)
        try:
            ok, out = solve(url, text, show, on_tool=live.tool, **({"retry": retry} if retry else {}))
            if ok and hand_fresh(task, t0):  # решение от руки — перепроверить отдельно, как учитель
                live.step("Перепроверяю решение", 0.8)
                checked = verify(task, on_tool=live.tool)
                if checked:
                    out = f"{out.rstrip()}\n\n🔎 {checked}"
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
        pend = None if show else defer_of(task, t0)
        if pend:  # тест: ответы внесены, сдаст бот по таймеру
            remember(title or "Тест", True, parse_score(out)[1], url=url, secs=time.time() - t0)
            react(chat, mid, "👌")
            schedule_test(chat, task, pend, title, out, tries=(retry or {}).get("k", 0))
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
# Черновиков может быть несколько сразу (Глеб присылает 2–3 задания подряд): каждый показан своей карточкой со своими
# кнопками. Правка (текст, просьба, голос, фото) идёт в черновик, на который он ответил (reply), иначе — в «текущий»:
# последний показанный или тот, где он нажал «✏️ Править».

def draft_kb(task):
    """Главное — «Отправить» (как в настройке «⏳ Отправка»), ниже — как отправить именно это задание."""
    return kb([("✅ Отправить учителю", f"send:{task}", "success")],
              [("⚡ Сразу", f"now:{task}"), ("⏳ С задержкой", f"later:{task}"), ("🕐 Ко времени", f"atq:{task}")],
              [("✏️ Править", f"ed:{task}"), ("Не отправлять", f"drop:{task}")])


def _drafts(s):
    """{задание: черновик} в настройках; старый вид (один «draft» + «draft_queue») переводит сам."""
    ds = s.setdefault("drafts", {})
    old = s.pop("draft", None)
    if isinstance(old, dict) and old.get("task"):
        ds.setdefault(old["task"], old)
        s.setdefault("draft_on", old["task"])
    for x in s.pop("draft_queue", None) or []:
        if isinstance(x, dict) and x.get("task"):
            ds.setdefault(x["task"], {"task": x["task"], "url": x.get("url", ""), "title": drafts.task_info(x["task"])[1],
                                      "pairs": [], "own_files": False})
    return ds


def all_drafts():
    return list(_drafts(load_settings()).values())


def draft(task=None):
    """Черновик задания task, без task — текущий (куда идут правки). None — нет такого."""
    s = load_settings()
    ds = _drafts(s)
    if task:
        return ds.get(task)
    return ds.get(s.get("draft_on")) or (list(ds.values())[-1] if ds else None)


def set_draft(d, active=False):
    with settings_lock:
        s = load_settings()
        _drafts(s)[d["task"]] = d
        if active:
            s["draft_on"] = d["task"]
        save_settings(s)


def drop_draft(task):
    with settings_lock:
        s = load_settings()
        ds = _drafts(s)
        ds.pop(task, None)
        if s.get("draft_on") == task or s.get("draft_on") not in ds:
            s.pop("draft_on", None)
            if ds:
                s["draft_on"] = list(ds)[-1]
        save_settings(s)


def draft_of(msg):
    """Черновик, на сообщение которого (карточку или картинку) ответил Глеб, иначе текущий."""
    rid = (msg.get("reply_to_message") or {}).get("message_id")
    if rid:
        for d in all_drafts():
            if rid in (d.get("mids") or []):
                return d
    return draft()


def remember_mids(task, *mids):
    """id сообщений черновика — чтобы ответ на любое из них правил именно его."""
    with settings_lock:
        d = draft(task)
        if d:
            d["mids"] = ((d.get("mids") or []) + [m for m in mids if m])[-30:]
            set_draft(d)


def file_mid(r):
    return ((r or {}).get("result") or {}).get("message_id")


def send_draft_files(chat, task):
    remember_mids(task, *[file_mid(send_file(chat, f)) for f in drafts.files(task)])


def offer_draft(chat, task, url):
    """Черновик для учителя: картинки + карточка со своими кнопками. Другие черновики остаются — их тоже можно править."""
    title = drafts.task_info(task)[1]
    old = draft(task) or {}
    set_draft({"task": task, "url": url, "title": title, "pairs": old.get("pairs") or [], "own_files": False,
               "mids": []}, active=True)
    send_draft_files(chat, task)
    show_draft(chat, task)


def show_draft(chat, task=None):
    d = draft(task)
    if not d:
        return
    text = drafts.read(d["task"])
    n = len(drafts.files(d["task"]))
    many = len(all_drafts())
    note = " · ".join(x for x in (
        esc(d["title"]), f"{n} {plural(n, 'картинка', 'картинки', 'картинок')} выше" if n else "",
        f"черновиков: {many} — правлю тот, на который ответишь, или этот" if many > 1 else
        "поправить — пришли текст, просьбу или фото") if x)
    remember_mids(d["task"], send(chat, card("Черновик для учителя", esc(text) if text else "<i>(без текста)</i>", note, "📄"),
                                  markup=draft_kb(d["task"]), raw_html=True))


def finish_draft(chat, task=None):
    """Черновик отправлен или отменён. Остальные остаются; правки дальше идут в последний из них."""
    d = draft(task)
    if d:
        drop_draft(d["task"])
    left = all_drafts()
    if left and chat:
        nxt = draft()
        send(chat, card("Ещё черновики", note=f"{len(left)} ждут — правки теперь идут в «{esc(nxt['title'])}». "
                                              "Другой — ответь на его карточку или нажми «✏️ Править»", icon="📄"),
             markup=draft_kb(nxt["task"]), raw_html=True)


def plural(n, one, few, many):
    return one if n % 10 == 1 and n % 100 != 11 else few if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else many


edit_lock = threading.Lock()  # правки — по одной, но не ждут, пока решаются задания (места для решения не занимают)


def edit_draft(chat, text, task=None):
    """Глеб прислал готовый текст или просьбу («убери последний абзац», «в №3 ответ другой»).
    Просьбу применяет Claude: он правит текст, а решение от руки — через draw.py задания (картинки перерисуются)."""
    d = draft(task)
    if not d:
        send(chat, "⚠️ Этот черновик уже неактуален", menu=True)
        return
    set_draft(d, active=True)
    before = drafts.read(d["task"])
    draw = os.path.join(drafts.task_dir(d["task"]), "draw.py")
    hand = os.path.exists(draw) and not d.get("own_files")
    shot = {f: os.path.getmtime(f) for f in drafts.files(d["task"])}
    if hand or not before or len(text) < 0.6 * len(before):
        with edit_lock:
            live = Live(chat, "Правлю черновик", esc(f"{d['title']}: {text[:80]}"), progress=False)
            path = os.path.relpath(drafts.text_path(d["task"]), REPO)
            rel = os.path.relpath(draw, REPO)
            try:
                ok, out = claude(
                    f"В {path} черновик ответа учителю по заданию «{d['title']}». Глеб написал про него: «{text}».\n"
                    f"Если это готовый новый текст ответа, запиши его в {path} как есть. Если это просьба что-то изменить, "
                    f"измени {path} по ней и больше ничего не трогай. Каждая строка файла — абзац.\n"
                    + (f"Решение от руки нарисовано скриптом {rel} (horo/sheet.py: Work.solve по номерам, save). Если "
                       f"просьба про решение или ответы на картинках, поправь {rel} и запусти `python3 {rel}` — картинки "
                       "перерисуются; посмотри их через Read. Если просьба только про текст — картинки не трогай.\n"
                       if hand else "")
                    + PRIVATE + "В ответе одна строка: что сделал.",
                    ["Read", "Write", "Edit"] + ([f"Bash(python3 {rel})"] if hand else []), timeout=600)
            finally:
                live.done()
        if not ok:
            send(chat, err(out, "Черновик не изменился — можно поправить ещё раз"), markup=draft_kb(d["task"]), raw_html=True)
            return
    else:
        drafts.write(d["task"], text)
    after = drafts.read(d["task"])
    d = draft(d["task"]) or d
    if drafts.record_edit(d["task"], d["title"], before, after):
        d["pairs"].append([before, after, text])
        set_draft(d)
    if {f: os.path.getmtime(f) for f in drafts.files(d["task"])} != shot:  # решение перерисовано — новые картинки
        send_draft_files(chat, d["task"])
    show_draft(chat, d["task"])


def add_draft_photo(chat, msg, task=None):
    d = draft(task)
    if not d:
        send(chat, "⚠️ Этот черновик уже неактуален", menu=True)
        return
    try:
        data, name = download(attachment(msg))
    except Exception as e:  # сеть моргнула или файл больше 20 МБ — сказать, а не молчать
        print("draft photo:", e)
        send(chat, err("Не получилось скачать фото, пришли ещё раз"), markup=draft_kb(d["task"]), raw_html=True)
        return
    with settings_lock:  # альбом — несколько фото сразу: «первое своё фото заменяет мои картинки» — только первое
        d = draft(d["task"]) or d
        first = not d.get("own_files")
        d["own_files"] = True
        set_draft(d, active=True)
        drafts.add_file(d["task"], data, name, replace=first)
    n = len(drafts.files(d["task"]))
    remember_mids(d["task"], msg.get("message_id"), send(
        chat, card("Фото добавил", note=f"{esc(d['title'])} · всего {n} {plural(n, 'картинка', 'картинки', 'картинок')}. "
                                         "Когда всё — «Отправить учителю»", icon="🖼"),
        markup=draft_kb(d["task"]), raw_html=True))


# «Как человек»: после «Отправить» ответ уходит не сразу, а через несколько минут — будто Глеб сделал задание,
# отошёл и потом сам отправил. HDP_SEND_DELAY — минуты «от-до» (по умолчанию 4-12), «0» — сразу.
SEND_DELAY = os.environ.get("HDP_SEND_DELAY") or "4-12"


def human_on():
    """Сдавать «как человек» (паузы в тестах, отправка учителю через несколько минут)? Включено, пока не выключат."""
    return load_settings().get("human", True)


WISH_DEFAULT = "текстовые ответы — максимально кратко"  # Глеб 19.10: «по умолчанию текстовые задания максимально кратко»


def default_wish():
    """Пожелание ко всем заданиям (меняется на сайте: ⚙️ → «Пожелания по умолчанию»; пусто — без пожеланий)."""
    w = load_settings().get("wish")
    return WISH_DEFAULT if w is None else w


def set_default_wish(text):
    with settings_lock:
        s = load_settings()
        s["wish"] = str(text or "").strip()[:500]
        save_settings(s)


def wishes(text):
    """Пожелания к заданию для Claude: по умолчанию + слова Глеба рядом со ссылкой (или из «💬 Пожелания» на сайте)."""
    own = re.sub(r"\s+", " ", LINK.sub(" ", text or "")).strip()
    if re.fullmatch(r"(?i)(сделай|реши|пж|пожалуйста|плиз|вот|это|\W)*", own):
        own = ""  # «сделай пж <ссылка>» — не пожелание
    return [w for w in (default_wish(), own[:500]) if w]


def set_human(on):
    with settings_lock:
        s = load_settings()
        s["human"] = bool(on)
        save_settings(s)


def delay_range():
    """Сколько ждать перед отправкой учителю («от-до» минут, «0» — сразу): настройка «⏳ Отправка»."""
    v = load_settings().get("send_delay")
    if v is None:  # ещё не выбирал — как раньше: «как человек» вкл — HDP_SEND_DELAY, выкл — сразу
        v = SEND_DELAY if human_on() else "0"
    return v


def set_delay_range(v):
    with settings_lock:
        s = load_settings()
        s["send_delay"] = v
        save_settings(s)


def delay_label(v=None):
    v = delay_range() if v is None else v
    return dict(SEND_CHOICES).get(v, f"{v} мин")


def btn_delay():
    return f"⏳ Отправка: {delay_label()}"


def send_card():
    """Настройка отправки: задержка ответа учителю и паузы в тестах."""
    on = human_on()
    return card("Отправка", f"Ответ учителю после «Отправить»: <b>{delay_label()}</b>\n"
                f"Тесты: {'пауза 20–45 с на вопрос, как человек' if on else 'сдаю сразу'}",
                "На каждом черновике можно выбрать иначе: ⚡ Сразу · ⏳ С задержкой · 🕐 Ко времени", "⏳")


def send_kb():
    cur = delay_range()
    return kb([((("✓ " if v == cur else "") + lab), f"sd:{v}") for v, lab in SEND_CHOICES[:2]],
              [((("✓ " if v == cur else "") + lab), f"sd:{v}") for v, lab in SEND_CHOICES[2:]],
              [("🧪 Паузы в тестах: " + ("вкл" if human_on() else "выкл"), "sd:tests")])


def send_delay(rng=None):
    try:
        lo, _, hi = (rng or delay_range()).partition("-")
        lo = float(lo)
        return random.uniform(lo, float(hi or lo)) * 60
    except ValueError:
        return 0


def schedule_send(chat, task=None, secs=None, at=None):
    """Назначить отправку: через secs секунд, к моменту at (epoch) или по настройке «⏳ Отправка» (0 — сразу)."""
    d = draft(task)
    if not d:
        send(chat, "⚠️ Черновика уже нет", menu=True)
        return
    if at is None:
        secs = send_delay() if secs is None else secs
        if secs <= 0:
            submit_draft(chat, d["task"])
            return
        at = time.time() + secs
    d["send_at"] = at
    set_draft(d)
    when = datetime.datetime.fromtimestamp(at, MSK)
    day = "" if when.date() == datetime.datetime.now(MSK).date() else " завтра"
    send(chat, card(f"Отправлю{day} в {when.strftime('%H:%M')}", note=f"{esc(d['title'])} · как будто ты сам отправил",
                    icon="⏳"),
         markup=kb([("⚡ Сейчас", f"now:{d['task']}"), ("✖ Отменить", f"hold:{d['task']}")]), raw_html=True)


def time_choices(now=None):
    """Варианты «🕐 Ко времени»: (подпись, epoch) — через полчаса/час/два и ближайшие вечерние часы."""
    now = now or datetime.datetime.now(MSK)
    out = [("через 30 мин", now + datetime.timedelta(minutes=30)), ("через 1 ч", now + datetime.timedelta(hours=1)),
           ("через 2 ч", now + datetime.timedelta(hours=2))]
    for hh, mm in ((17, 0), (19, 0), (20, 30), (22, 0)):
        t = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if t > now + datetime.timedelta(minutes=40):
            out.append((f"в {t:%H:%M}", t))
    out.append(("завтра 7:30", (now + datetime.timedelta(days=1)).replace(hour=7, minute=30, second=0, microsecond=0)))
    jitter = lambda t: t + datetime.timedelta(seconds=random.randint(0, 240))  # не ровно в :00 — как человек
    return [(lab, int(jitter(t).timestamp())) for lab, t in out[:6]]


def ask_time(chat, d):
    ch = time_choices()
    rows = [[(lab, f"at:{d['task']}:{ts}") for lab, ts in ch[i:i + 3]] for i in range(0, len(ch), 3)]
    mode[chat] = "at:" + d["task"]
    send(chat, card("Ко скольки отправить?", note=f"{esc(d['title'])} · или напиши время, например 19:40",
                    icon="🕐"), markup=kb(*rows, [("✖ Отмена", f"hold:{d['task']}")]), raw_html=True)


def parse_time(text, now=None):
    """«19:40», «в 7.30», «через 40 минут», «через 2 часа» -> epoch или None."""
    now = now or datetime.datetime.now(MSK)
    t = text.lower().strip()
    m = re.search(r"через\s+(\d+)\s*(мин|ч)", t)
    if m:
        n = int(m.group(1))
        delta = datetime.timedelta(hours=n) if m.group(2) == "ч" else datetime.timedelta(minutes=n)
        return int((now + delta).timestamp()) if delta <= datetime.timedelta(days=3) else None
    m = re.search(r"(?<!\d)(\d{1,2})[:.](\d{2})(?!\d)", t)
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return None
    at = now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
    if at <= now or "завтра" in t:
        at += datetime.timedelta(days=1)
    return int(at.timestamp())


def due_sends(now=None):
    """Черновики, у которых подошло время отправки, — отправить (зовётся раз в 20 с)."""
    now = now or time.time()
    for old in all_drafts():
        if not old.get("send_at") or old["send_at"] > now:
            continue
        with settings_lock:  # пока шёл обход, Глеб мог отменить отправку или поправить черновик
            d = draft(old["task"])
            if not d or not d.get("send_at") or d["send_at"] > now:
                continue
            d.pop("send_at")
            set_draft(d)
        submit_draft(owner(), d["task"])


# ---------- ⏳ Ожидание: что скоро уйдёт учителю ----------
# Глеб, 15.10: «лист ожидания — что скоро отправится». Отправки по таймеру (во сколько, ⚡ Сейчас / ✖ Отменить),
# черновики, которые ждут его «Отправить», то, что отправляется прямо сейчас, и задания в работе / в очереди.

def short(text, n=14):
    text = (text or "Задание").strip()
    return text if len(text) <= n else text[:n - 1] + "…"


def waiting(now=None):
    """(по таймеру — по времени, ждут «Отправить», отправляются сейчас, решаются/в очереди)."""
    now = now or time.time()
    ds = all_drafts()
    with sending_lock:
        sending = set(SENDING)
    with running_lock:
        work = sorted(running)
    timed = sorted((d for d in ds if d.get("send_at") and d["task"] not in sending), key=lambda d: d["send_at"])
    held = [d for d in ds if not d.get("send_at") and d["task"] not in sending]
    return timed, held, [d for d in ds if d["task"] in sending], work


def waiting_card(now=None):
    """HTML и кнопки «⏳ Ожидание»."""
    now = now or time.time()
    timed, held, sending, work = waiting(now)
    today = datetime.datetime.fromtimestamp(now, MSK).date()
    parts, rows = ["⏳ <b>Ожидание</b>"], []
    tests = sorted(pending_tests().values(), key=lambda t: t.get("at") or 0)
    if tests:
        lines = []
        for t in tests:
            if t.get("finishing"):
                lines.append(f"🧪 {esc(t.get('title') or 'Тест')} — <b>сдаю сейчас</b>")
                continue
            if not t.get("at"):
                lines.append(f"🧪 {esc(t.get('title') or 'Тест')} — <i>ждёт «Сдать сейчас»</i>")
            else:
                left = max(0, int(t["at"] - now))
                lines.append(f"🧪 {esc(t.get('title') or 'Тест')} — <b>в {datetime.datetime.fromtimestamp(t['at'], MSK):%H:%M}</b>"
                             f" <i>(через {left // 60} мин {left % 60} с)</i>")
            rows.append([(f"⚡ {short(t.get('title'))}", f"tnow:{t['task']}"), ("✖ Не сдавать", f"tdrop:{t['task']}")])
        parts.append("<b>Сдам тесты</b>\n<blockquote>" + "\n".join(lines) + "</blockquote>")
    if timed:
        lines = []
        for d in timed:
            when = datetime.datetime.fromtimestamp(d["send_at"], MSK)
            left = max(0, int(d["send_at"] - now))
            soon = f"через {left // 60} мин" if left < 3600 else f"через {left // 3600} ч {left % 3600 // 60} мин"
            day = "" if when.date() == today else " завтра"
            lines.append(f"📬 {esc(d['title'])} — <b>{day.strip() + ' ' if day else ''}в {when:%H:%M}</b> <i>({soon})</i>")
            rows.append([(f"⚡ {short(d['title'])}", f"now:{d['task']}"), ("✖ Отменить", f"hold:{d['task']}")])
        parts.append("<b>Отправлю учителю</b>\n<blockquote>" + "\n".join(lines) + "</blockquote>")
    if sending:
        parts.append("<b>Отправляю сейчас</b>\n<blockquote>" + "\n".join(f"📤 {esc(d['title'])}" for d in sending)
                     + "</blockquote>")
    if held:
        parts.append("<b>Ждут твоего «Отправить»</b>\n<blockquote>" + "\n".join(f"📄 {esc(d['title'])}" for d in held)
                     + "</blockquote>")
        rows += [[(f"✅ {short(d['title'])}", f"send:{d['task']}"), ("✏️ Править", f"ed:{d['task']}")] for d in held[:6]]
    if work:
        parts.append("<b>Решаю или в очереди</b>\n<blockquote>" + "\n".join(
            f"🧠 {esc(drafts.task_info(t)[1] or 'Задание')}" for t in work) + "</blockquote>")
    if len(parts) == 1:
        parts.append("<i>Ничего не ждёт: тестов и таймеров на отправку нет, черновиков и заданий в работе тоже</i>")
    rows.append([("🔄 Обновить", "w:upd")])
    return "\n".join(parts), kb(*rows)


def send_waiting(chat, mid=None):
    text, markup = waiting_card()
    if mid:
        edit(chat, mid, text, markup)
    else:
        send(chat, text, markup=markup, raw_html=True)


# ---------- 🧪 Тест: решить сразу, сдать позже, при ошибке — вторая попытка ----------
# Глеб, 15.10: «бот должен максимально быстро сделать тест и не отправить его, а написать, что он выполнен и будет
# сдан через какое-то время» и «если тест сделан не правильно — проверь всё и сделай вторую попытку правильно».
# submit.js под Claude (HORO_DEFER) вносит ответы и не сдаёт → defer.json; бот сдаёт сам по таймеру (--continue-only:
# новую попытку при этом не начать). Не 100% — Claude перепроверяет каждый вопрос (RETRY) и вносит вторую попытку,
# если после неё останется запасная; последнюю — только по кнопке Глеба.

def defer_of(task, since):
    """Ответы теста внесены, но не сдан (этим решением)? -> {url, answers, N, at} или None."""
    try:
        with open(os.path.join(drafts.task_dir(task), "defer.json")) as f:
            d = json.load(f)
        if float(d.get("at") or 0) / 1000 >= since - 1 and d.get("url") and d.get("answers"):
            return d
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return None


def test_delay(n):
    """Через сколько сдать: как человек — 20–45 с на вопрос (1,5–6 мин); «как человек» выключено — сразу."""
    if not human_on():
        return 0
    return max(90.0, min(360.0, sum(random.uniform(20, 45) for _ in range(max(1, int(n or 1))))))


def pending_tests():
    return dict(load_settings().get("tests") or {})


def set_test(task, info):
    with settings_lock:
        s = load_settings()
        ts = s.setdefault("tests", {})
        if info is None:
            ts.pop(task, None)
        else:
            ts[task] = info
        save_settings(s)


def test_kb(task):
    return kb([("⚡ Сдать сейчас", f"tnow:{task}", "success"), ("✖ Не сдавать", f"tdrop:{task}")])


def schedule_test(chat, task, pend, title, out="", tries=0, silent=False):
    """Тест решён, ответы на сайте — сдать через test_delay (переживает перезапуск: settings → tests).
    silent — без своей карточки (пачка автотестов пишет одну общую). -> время сдачи (epoch)."""
    at = time.time() + test_delay(pend.get("N"))
    set_test(task, {"task": task, "chat": chat, "url": pend["url"], "answers": pend["answers"], "title": title,
                    "N": pend.get("N"), "at": at, "tries": tries})
    if at - time.time() < 5:
        threading.Thread(target=finish_test, args=(task,), daemon=True).start()
        return at
    if silent:
        return at
    body = md(parse_score(out)[1]) if out else ""
    when = datetime.datetime.fromtimestamp(at, MSK)
    note = f"Сдам в {when:%H:%M} (через {max(1, round((at - time.time()) / 60))} мин)" + (f" · {esc(title)}" if title else "")
    head = "Вторая попытка готова — ответы внесены" if tries else "Тест решён — ответы внесены"
    send(chat, card(head, body, note, "🧪"), markup=test_kb(task), raw_html=True)
    return at


def due_tests(now=None):
    """Тесты, которым пора сдаваться (зовётся раз в 20 с)."""
    now = now or time.time()
    for t in pending_tests().values():
        if t.get("finishing") or not t.get("at") or t["at"] > now:
            continue
        with settings_lock:  # Глеб мог нажать «Сдать сейчас» / «Не сдавать», пока шёл обход
            s = load_settings()
            cur = (s.get("tests") or {}).get(t["task"])
            if not cur or cur.get("finishing") or not cur.get("at") or cur["at"] > now:
                continue
            cur["finishing"] = True
            save_settings(s)
        threading.Thread(target=finish_test, args=(t["task"],), daemon=True).start()


TRIES = re.compile(r"^ПОПЫТКИ:\s*(\d+)\s*/\s*(\d+)", re.M)


def finish_test(task):
    try:
        return _finish_test(task)
    finally:
        web.mirror._swr.clear()  # тест сдан — зеркало показывает свежий статус


def _finish_test(task):
    """Сдать отложенный тест (тот же answers.json, только начатая попытка) и сообщить баллы."""
    t = pending_tests().get(task)
    if not t:
        return
    chat, title = t["chat"], t.get("title") or drafts.task_info(task)[1] or "Тест"
    set_test(task, {**t, "finishing": True})
    lock.acquire()  # браузер — как у задания (до HDP_PARALLEL сразу)
    try:
        ok, out = node("horo/submit.js", t["url"], t["answers"], "--continue-only", timeout=900)
    finally:
        lock.release()
    if not ok:
        set_test(task, {**t, "finishing": False, "at": None})
        send(chat, card("Не сдал тест", esc(plain(out)), esc(title), "⚠️"), markup=test_kb(task), raw_html=True)
        return
    set_test(task, None)
    score, _ = parse_score(out)
    m = TRIES.search(out)
    used, avail = (int(m.group(1)), int(m.group(2))) if m else (None, None)
    full = bool(score) and score[2] >= 100
    if score:
        got, total, pct = score
        mark = "🏆" if full else "✅" if pct >= 70 else "📊"
        head = f"{mark} <b>{num(got)} / {num(total)}</b> · {pct}%" if total else f"{mark} <b>{pct}%</b>"
    else:
        head = "✅ <b>Тест сдан</b>"
    note = " · ".join(x for x in (esc(title), f"попыток {used} из {avail}" if used else "") if x)
    remember(title, True, f"Сдан{': ' + num(score[0]) + '/' + num(score[1]) if score and score[1] else ''}",
             score=score, url=t["url"])
    send(chat, f"{head}\n<i>{note}</i>", menu=True, raw_html=True, effect=EFFECT_CONFETTI if full else None)
    threading.Thread(target=refresh_overview, daemon=True).start()
    if not score or full or not score[1] or used is None:
        return
    left = avail - used
    info = {"got": num(score[0]), "total": num(score[1]), "miss": num(score[1] - score[0]), "k": t.get("tries", 0) + 1}
    keep = os.path.join(drafts.task_dir(task), f"answers_try{info['k']}.json")
    try:
        shutil.copyfile(t["answers"], keep)  # Claude сравнит новые ответы с этими
    except OSError:
        pass
    if t.get("tries", 0) == 0 and left >= 2:
        send(chat, card("Не 100% — делаю вторую попытку", note=f"Перепроверю каждый вопрос и найду ошибку · после неё "
                        f"останется ещё {left - 1}", icon="🔁"), raw_html=True)
        threading.Thread(target=run_task, args=(chat, t["url"], "", False, None, info), daemon=True).start()
    elif left >= 1:
        set_offer(task, {**info, "url": t["url"], "title": title})
        why = "осталась последняя попытка" if left == 1 else "вторую уже делал"
        send(chat, card("Не 100%", note=f"{esc(title)} · {why} — пересдать?", icon="🔁"),
             markup=kb([("🔁 Перепроверить и пересдать", f"tretry:{task}", "primary")]), raw_html=True)


def set_offer(task, info):
    with settings_lock:
        s = load_settings()
        offers = s.setdefault("retry_offers", {})
        if info is None:
            offers.pop(task, None)
        else:
            offers[task] = info
        save_settings(s)


def on_test_button(chat, cq, data):
    """tnow:<задание> — сдать сейчас, tdrop: — не сдавать (ответы остаются на сайте), tretry: — пересдать."""
    kind, _, task = data.partition(":")
    if kind == "tretry":
        offer = (load_settings().get("retry_offers") or {}).get(task)
        quiet("answerCallbackQuery", callback_query_id=cq["id"], text="Перепроверяю…" if offer else "Уже не актуально")
        if offer:
            set_offer(task, None)
            info = {k: offer[k] for k in ("got", "total", "miss", "k")}
            threading.Thread(target=run_task, args=(chat, offer["url"], "", False, None, info), daemon=True).start()
        return
    t = pending_tests().get(task)
    if not t:
        quiet("answerCallbackQuery", callback_query_id=cq["id"], text="Тест уже сдан")
        return
    if kind == "tdrop":
        set_test(task, None)
        quiet("answerCallbackQuery", callback_query_id=cq["id"], text="Не сдаю")
        send(chat, card("Не сдаю", note=f"{esc(t.get('title') or 'Тест')} — ответы остались на сайте, попытка не "
                        "закрыта: сдать можно там", icon="👌"), menu=True, raw_html=True)
        return
    if t.get("finishing"):
        quiet("answerCallbackQuery", callback_query_id=cq["id"], text="Уже сдаю")
        return
    quiet("answerCallbackQuery", callback_query_id=cq["id"], text="Сдаю…")
    set_test(task, {**t, "at": time.time()})
    due_tests()


def resume_tests():
    """После перезапуска: сдача, прерванная на полпути, — ещё раз (--continue-only новую попытку не начнёт)."""
    for t in pending_tests().values():
        if t.get("finishing"):
            set_test(t["task"], {**t, "finishing": False, "at": time.time() + 30})


def sender_loop():
    while True:
        try:
            due_sends()
        except Exception as e:
            print("sender:", e)
            journal("bug", f"отправка по таймеру: {type(e).__name__}: {e}", traceback.format_exc())
        try:
            due_tests()
        except Exception as e:
            print("tests:", e)
            journal("bug", f"сдача тестов по таймеру: {type(e).__name__}: {e}", traceback.format_exc())
        threading.Event().wait(20)


SENDING = set()  # черновики, которые сейчас отправляются учителю
sending_lock = threading.Lock()


def submit_draft(chat, task=None):
    """«Отправить учителю». Двойное нажатие (или чат + приложение сразу) не отправляет ответ дважды."""
    d = draft(task)
    if not d:
        send(chat, "⚠️ Черновика уже нет", menu=True)
        return
    with sending_lock:
        if d["task"] in SENDING:
            send(chat, card("Уже отправляю", note=esc(d["title"]) + " — результат придёт сюда", icon="⏳"), raw_html=True)
            return
        SENDING.add(d["task"])
    try:
        _submit_draft(chat, d)
    finally:
        with sending_lock:
            SENDING.discard(d["task"])


def _submit_draft(chat, d):
    if not lock.acquire(blocking=False):  # все места заняты решением — отправим, как только одно освободится
        send(chat, card("Отправлю чуть позже", note=f"{esc(d['title'])} — сейчас решаю {MAX_PARALLEL} задания, "
                                                    "отправлю, как только одно закончу", icon="⏳"), raw_html=True)
        lock.acquire()
    try:
        d = draft(d["task"])
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
            send(chat, err(out, "Не отправилось. Черновик остался — можно нажать ещё раз"), markup=draft_kb(d["task"]),
                 raw_html=True)
            return
        send(chat, card("Отправлено учителю", esc(out.strip()), esc(d["title"]), "📬"),
             menu=True, raw_html=True, effect=EFFECT_LIKE)
        finish_draft(chat, d["task"])
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
    if data.startswith("sitel:"):  # «✅ Войти» на сайт: страница входа в Safari ждёт и получит cookie
        ua = web.site_approve(data[6:])
        quiet("answerCallbackQuery", callback_query_id=cq["id"],
              text="Вхожу…" if ua else "Просьба устарела — нажми «Войти через Telegram» на сайте ещё раз")
        if ua:
            edit(chat, mid, card("Вход на сайт подтверждён", esc(ua), icon="✅"), resend=False)
        return
    if data.startswith("sc:"):  # 🧪 проверка сайта: починить найденное / подробно / ещё раз
        quiet("answerCallbackQuery", callback_query_id=cq["id"])
        on_sitecheck(chat, data[3:])
        return
    if data.startswith("m:"):  # меню «Ещё»: правится на месте
        on_more(chat, mid, cq["id"], data[2:])
        return
    if data.startswith("mode:"):  # 💬 чат / 📱 приложение
        on_mode(chat, mid, cq["id"], data[5:])
        return
    if data.startswith(("tnow:", "tdrop:", "tretry:")):  # 🧪 отложенный тест: сдать сейчас / не сдавать / пересдать
        on_test_button(chat, cq, data)
        return
    if data == "w:upd":  # ⏳ ожидание: обновить на месте
        quiet("answerCallbackQuery", callback_query_id=cq["id"])
        send_waiting(chat, mid)
        return
    if data.startswith("ex:"):  # 🎯 подтверждения: ex:2 / ex:3 / ex:all, ex:upd:<то же> — свежие с сайта
        parts = data.split(":")
        fresh = parts[1] == "upd"
        key = (parts[2] if len(parts) > 2 else "all") if fresh else parts[1]
        quiet("answerCallbackQuery", callback_query_id=cq["id"], text="Обновляю с сайта…" if fresh else "")
        lvl = int(key) if key in ("2", "3") else None
        threading.Thread(target=send_exams, args=(chat, lvl, fresh), daemon=True).start()
        return
    if data.startswith("pp:"):  # 📖 подготовка: шаги правятся на месте
        on_prep_button(chat, mid, cq["id"], data[3:])
        return
    if data.startswith("ed:"):  # ✏️ Править: этот черновик — текущий, кнопки остаются
        d = draft(data[3:])
        try:
            tg("answerCallbackQuery", callback_query_id=cq["id"], text="Пиши правку" if d else "Черновика уже нет")
        except urllib.error.HTTPError as e:
            print("callback:", e)
        if not d:
            send(chat, "⚠️ Этот черновик уже неактуален", menu=True)
        else:
            set_draft(d, active=True)
            remember_mids(d["task"], send(chat, card("Что поправить?", note=f"{esc(d['title'])} · пришли текст, просьбу "
                                                     "(«в №3 ответ другой»), голосовое или фото", icon="✏️"), raw_html=True))
        return
    if data == CANCEL:  # ✕ Отмена: бот больше не ждёт ввода
        quiet("answerCallbackQuery", callback_query_id=cq["id"])
        mode.pop(chat, None)
        pending.pop(chat, None)
        edit(chat, mid, card("Отменил", note="Выбери действие внизу или пришли ссылку на задание", icon="👌"))
        return
    if data.startswith("sd:"):  # ⏳ Отправка: задержка и паузы в тестах
        quiet("answerCallbackQuery", callback_query_id=cq["id"])
        v = data[3:]
        if v == "tests":
            set_human(not human_on())
        elif v in dict(SEND_CHOICES):
            set_delay_range(v)
        edit(chat, mid, send_card(), send_kb())
        return
    if data.startswith("hand:"):
        try:
            tg("answerCallbackQuery", callback_query_id=cq["id"])
        except urllib.error.HTTPError as e:
            print("callback:", e)
        _, key, *sign = data.split(":")
        if key != "ok" and (key not in HAND_STEPS or not sign or sign[0] not in ("1", "-1")):  # кнопка старой версии
            send(chat, card("Кнопка устарела", note="Пример почерка заново — /style", icon="🤔"), menu=True, raw_html=True)
            return
        quiet("editMessageReplyMarkup", chat_id=chat, message_id=mid,  # кнопки остаются только под новым примером
              reply_markup=json.dumps({"inline_keyboard": []}))
        if key == "ok":
            mode.pop(chat, None)
            send(chat, HAND_OK, menu=True, raw_html=True)
        else:
            adjust_hand(key, int(sign[0]))
            threading.Thread(target=send_sample, args=(chat, "Перерисовал"), daemon=True).start()
        return
    quiet("answerCallbackQuery", callback_query_id=cq["id"])  # по отдельности: «запрос устарел» не мешает убрать кнопки
    quiet("editMessageReplyMarkup", chat_id=chat, message_id=mid,  # убрать кнопки: не нажать дважды
          reply_markup=json.dumps({"inline_keyboard": []}))
    if data.startswith("all:"):
        mode.pop(chat, None)
        rows = pending.pop(chat, [])
        if data == "all:yes" and rows:
            threading.Thread(target=run_all, args=(chat, rows), daemon=True).start()
        elif data == "all:yes" and ALL_RUNNING:  # второе нажатие, пока сдаю: не писать «не сдаю»
            return
        elif data == "all:yes":  # список из старого сообщения (бот перезапускался)
            edit(chat, mid, card("Список устарел", note=f"Свежий — кнопка «{BTN_ALL}»", icon="🤔"))
        else:
            edit(chat, mid, card("Автотесты не сдаю", note=f"Передумаешь — кнопка «{BTN_ALL}»", icon="👌"))
        return
    if data.startswith("ph:"):  # задание, найденное на фото
        on_photo_button(chat, data[3:])
        return
    act, _, task = data.partition(":")
    if act in ("later", "atq", "at"):  # ⏳ С задержкой · 🕐 Ко времени (варианты) · время выбрано
        if act == "at":
            task, _, ts = task.partition(":")
        d = draft(task or None)
        if not d:
            send(chat, "⚠️ Этот черновик уже неактуален", menu=True)
        elif act == "later":  # своя задержка, даже если в настройке «сразу»
            rng = delay_range()
            threading.Thread(target=schedule_send, args=(chat, d["task"], send_delay(rng if rng != "0" else "4-12")),
                             daemon=True).start()
        elif act == "atq":
            ask_time(chat, d)
        elif ts.isdigit():
            mode.pop(chat, None)
            schedule_send(chat, d["task"], at=int(ts))
        return
    if act in ("now", "hold"):  # отправка по таймеру: «⚡ Сейчас» / «✖ Отменить»
        d = draft(task or None)
        if not d:
            send(chat, "⚠️ Этот черновик уже неактуален", menu=True)
            return
        d.pop("send_at", None)
        set_draft(d)
        if mode.get(chat, "").startswith("at:"):
            mode.pop(chat, None)
        if act == "now":
            threading.Thread(target=submit_draft, args=(chat, d["task"]), daemon=True).start()
        else:
            remember_mids(d["task"], send(chat, card("Не отправляю", note=f"{esc(d['title'])} · черновик остался — "
                                                     "можно поправить или отправить", icon="👌"),
                                          markup=draft_kb(d["task"]), raw_html=True))
        return
    d = draft(task or None)
    if act not in ("send", "drop"):
        send(chat, card("Кнопка устарела", note="Пришли ссылку на задание ещё раз", icon="🤔"), menu=True, raw_html=True)
    elif not d:
        send(chat, "⚠️ Этот черновик уже неактуален", menu=True)
    elif act == "send":
        threading.Thread(target=schedule_send, args=(chat, d["task"]), daemon=True).start()
    else:
        send(chat, card("Не отправляю", note=f"{esc(d['title'])} · передумаешь — пришли ссылку ещё раз", icon="👌"),
             menu=True, raw_html=True)
        finish_draft(chat, d["task"])


# ---------- Меню «Ещё»: редкие действия, правится на месте ----------

def more_text():
    return card("Ещё", note="Сам я не пишу — только отвечаю", icon="⚙️")


def more_kb():
    app = [("✨ Приложение", {"web_app": {"url": APP_URL + "/"}})] if APP_URL and ui_mode() == "app" else []
    rows = [
        [("❗ Просрочки", "m:late"), ("🛠 Поддержка", "sup|ask")],  # sup|… ловит support.Support
        [("✍️ Почерк", "m:hand"), ("🩺 Проверка", "m:check")],
        [("🧪 Проверка сайта", "m:sitecheck")],
        [("❓ Помощь", "m:help"), ("⚙️ Режим", "m:mode")] + app,
        [(btn_delay(), "m:human"), ("⏳ Ожидание", "w:upd")],
    ]
    return kb(*rows)


def on_more(chat, mid, cq_id, key):
    quiet("answerCallbackQuery", callback_query_id=cq_id)
    if key in ("more", "digest"):  # digest — старая кнопка сводки (сводки больше нет)
        edit(chat, mid, more_text(), more_kb())
    elif key == "help":
        edit(chat, mid, HELP, kb([("‹ Назад", "m:more")]))
    elif key == "mode":
        edit(chat, mid, mode_card(), mode_kb())
    elif key == "late":
        threading.Thread(target=run_script, args=(chat, "horo/status.js", "--late", "--html"), daemon=True).start()
    elif key == "hand":
        threading.Thread(target=send_alphabet, args=(chat,), daemon=True).start()
    elif key == "check":
        threading.Thread(target=check, args=(chat,), daemon=True).start()
    elif key == "sitecheck":
        threading.Thread(target=site_check, args=(chat,), daemon=True).start()
    elif key == "human":  # ⏳ Отправка — настройка на месте меню
        edit(chat, mid, send_card(), send_kb())


# ---------- Почерк ----------

ALPHA_DIR = os.path.join(REPO, "horo", "tmp", "alphabet")
GLEB_EXTRA = os.path.join(REPO, "horo", "tmp", "fonts", "gleb_extra.npz")  # буквы из его алфавита


def send_alphabet(chat):
    os.makedirs(ALPHA_DIR, exist_ok=True)
    send(chat, card("Алфавит твоим почерком",
                    "1. Ниже 3 листа (файлами, в полном размере): русский алфавит, английский (все 26 букв), цифры и знаки. "
                    "Все оформлены одинаково.\n"
                    "2. На iPad открой лист → Разметка.\n"
                    "3. В каждой клетке: на верхней строке большая буква, на нижней — маленькая. Пустые клетки можно оставить.\n"
                    "4. Не обрезай лист: розовые квадраты по углам должны быть видны.",
                    "Пришли листы обратно файлом (📎 → Файл), чтобы Telegram их не сжал, потом напиши «готово»", "✍️"),
         raw_html=True)
    for page in (1, 2, 3):
        out = os.path.join(ALPHA_DIR, f"alphabet_{page}.png")
        ok, why = py("horo/alphabet.py", "template", str(page), out)
        if not ok:
            send(chat, err(why), menu=True, raw_html=True)
            return
        send_file(chat, out, as_file=True)  # фото Telegram сжимает до 1280 px — маленькие буквы выходят пятнами
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
        try:
            data, name = download(attachment(msg))
        except Exception as e:
            print("alphabet sheet:", e)
            send(chat, err("Не получилось скачать лист, пришли ещё раз"), raw_html=True)
            return
        path = os.path.join(ALPHA_DIR, f"in_{int(time.time() * 1000)}_{name}")
        with open(path, "wb") as f:
            f.write(data)
        ok, out = py("horo/alphabet.py", "ingest", path)
        lines = out.strip().splitlines()
        added = next((l.split(":", 1)[1].split() for l in lines if l.startswith("Добавил:")), [])
        added = [] if added == ["ничего"] else added
        errors = [l.split(":", 1)[1].strip() for l in lines if l.startswith("Ошибка:")]
        tips = [l.split(":", 1)[1].strip() for l in lines if l.startswith("Совет:")]
        if added:
            send(chat, card(f"Добавил знаков: {len(added)}", esc(" ".join(added)), esc(" ".join(tips)), icon="✅"),
                 raw_html=True)
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


def retry_alphabet():
    """Листы старого алфавита, которые раньше отклонялись («это лист старого алфавита»), теперь разбираются:
    один раз при старте (alphabet.py retry помнит разобранные) — и Глебу приходит, что взял и как теперь пишу."""
    ok, out = py("horo/alphabet.py", "retry", ALPHA_DIR, timeout=600)
    lines = out.strip().splitlines()
    added = next((l.split(":", 1)[1].split() for l in lines if l.startswith("Добавил:")), [])
    if not ok or not added or added == ["ничего"] or not owner():
        return
    tips = [l.split(":", 1)[1].strip() for l in lines if l.startswith("Совет:")]
    send(owner(), card(f"Разобрал твои листы алфавита: {len(added)} букв", esc(" ".join(added)),
                       esc(" ".join(tips)), "✍️"), raw_html=True)
    prev = os.path.join(ALPHA_DIR, "preview.png")
    if py("horo/alphabet.py", "preview", prev)[0]:
        send_file(owner(), prev, "Так теперь пишу твоим почерком")


# ---------- Проверка ----------

def site_check(chat):
    """🧪 Сайт от А до Я: обход всех страниц зеркала (ошибки, картинки, вёрстка, панель бота) → отчёт и «Починить»."""
    if sitecheck.running():
        send(chat, card("Проверка сайта уже идёт", note="Отчёт придёт сюда", icon="🧪"), raw_html=True)
        return
    send(chat, card("Проверяю сайт от А до Я", "Все страницы платформы через зеркало: ошибки, картинки, вёрстка на "
                    "компьютере, iPad и телефоне, кнопки бота. На платформе ничего не меняю.",
                    "Минут 5–15 — отчёт придёт сюда", "🧪"), raw_html=True)
    try:
        rep = sitecheck.run()
    except Exception as e:
        send(chat, err(f"Проверка сайта: {e}"), menu=True, raw_html=True)
        return
    send_sitecheck(chat, rep)


def send_sitecheck(chat, rep):
    head, rows, mine = sitecheck.summary(rep)
    btns = [[("🛠 Починить найденное", "sc:fix", "primary")]] if mine and SUP else []
    btns.append([("📋 Подробно", "sc:more"), ("🔁 Ещё раз", "sc:again")])
    send(chat, card(esc(head), esc("\n".join(rows)), icon="✅" if not mine else "🧪"), markup=kb(*btns), raw_html=True)


def on_sitecheck(chat, what):
    rep = sitecheck.last()
    if what == "again":
        threading.Thread(target=site_check, args=(chat,), daemon=True).start()
    elif not rep:
        send(chat, err("Отчёта ещё нет — запусти проверку"), raw_html=True)
    elif what == "fix":
        if not SUP or not sitecheck.ours(rep):
            send(chat, card("Чинить нечего", icon="✅"), raw_html=True)
            return
        SUP.start(chat, sitecheck.fix_text(rep))  # тот же Claude, что «🛠 Поддержка»: правка, тесты, перезапуск, откат
    elif what == "more":
        rows = [f"{p['path']} — {p['ms'] / 1000:.1f} с" + (f" · ошибок {p['n']}" if p["n"] else "") for p in rep.get("list", [])]
        rows += ["", "Все ошибки:"] + [("· " if not i.get("platform") else "· (платформа) ") + sitecheck.line(i)
                                       for i in rep.get("issues", [])[:40]]
        send(chat, card("Проверка сайта подробно", esc("\n".join(rows))[:3800], icon="📋"), raw_html=True)


def app_site_check(b):
    """Сайт: «🧪 Проверка сайта» (POST) — запустить; отчёт придёт в чат и сюда."""
    if b.get("start"):
        if sitecheck.running():
            raise ValueError("Проверка уже идёт")
        threading.Thread(target=site_check, args=(owner(),), daemon=True).start()
    rep = sitecheck.last()
    head, rows, mine = sitecheck.summary(rep) if rep else ("", [], [])
    return {"running": sitecheck.running(), "head": head, "rows": rows, "errors": len(mine),
            "age": sitecheck.age(rep)}


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
        tests = [r for r in json.loads(out) if r["auto"] and r["rawStatus"] in AUTO_STATUSES]
        if not tests:
            send(chat, card("Новых автотестов нет", note="Всё, что могу сдать сам, уже сдано", icon="🎉"), menu=True, raw_html=True)
            return
        pending[chat] = tests
        mode[chat] = "confirm_all"
        lines = "\n".join(f"{r.get('icon', '📘')} <b>{esc(r['subj'])}</b> — {esc(r['title'])}"
                          + (" · 🔁 доработка" if r["rawStatus"] == "reworking" else "") for r in tests)
        send(chat, card(f"Нашёл автотесты: {len(tests)}", lines,
                        f"Сдам по очереди, это примерно {len(tests) * 2}–{len(tests) * 3} мин", "⚡"),
             markup=kb([(f"Сдать все ({len(tests)})", "all:yes", "success"), ("Не надо", "all:no")]), raw_html=True)
    finally:
        lock.release()


ALL_RUNNING = set()  # чаты, где сейчас сдаётся пачка автотестов


def run_all(chat, tests):
    """Сдаёт пачку тестов (строки из status.js --json или ссылки) с одной живой карточкой на всю пачку."""
    if not lock.acquire(blocking=False):
        send(chat, BUSY)
        return
    ALL_RUNNING.add(chat)
    try:
        tests = [t if isinstance(t, dict) else {"url": t, "title": "", "subj": ""} for t in tests]
        n, t0, rows, details = len(tests), time.time(), [], []
        live = Live(chat, f"Сдаю автотесты · 0 из {n}")
        try:
            for i, t in enumerate(tests):
                live.title = f"Сдаю автотесты · {i + 1} из {n}"
                live.reset(esc(t["title"]), i / n)
                live.task, live.span = (re.findall(r"task/([0-9a-f-]{36})", t["url"]) or [None])[0], (i / n, (i + 1) / n)
                t1 = time.time()
                ok, out = solve(t["url"], on_tool=lambda name, inp: (lambda st: st and live.step(st[0], (i + st[1]) / n))(step_of(name, inp)))
                score, body = parse_score(out) if ok else (None, out)
                title = esc(t["title"] or f"Тест {i + 1}")
                pend = defer_of(live.task, t1) if ok and live.task else None
                if pend:  # ответы внесены — сдаст по таймеру, баллы придут отдельным сообщением
                    at = schedule_test(chat, live.task, pend, t["title"] or f"Тест {i + 1}", silent=True)
                    rows.append(f"🧪 {title} — сдам в {datetime.datetime.fromtimestamp(at, MSK):%H:%M}")
                elif score:
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
        later = sum(r.startswith("🧪") for r in rows)
        send(chat, (f"⚡ <b>Автотесты решены: {n}</b> · сдам по таймеру, баллы пришлю\n<blockquote>" if later else
                    f"⚡ <b>Автотесты сданы: {n}</b>\n<blockquote>") + "\n".join(rows) + "</blockquote>\n"
             "<blockquote expandable>" + "\n\n".join(details) + "</blockquote>\n"
             f"<i>Заняло {mmss(time.time() - t0)} · подробности — в свёрнутой цитате</i>",
             menu=True, raw_html=True, effect=EFFECT_CONFETTI if perfect == n else None)
    finally:
        ALL_RUNNING.discard(chat)
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
    with settings_lock:
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


def clean_inbox(days=14):
    """Фото и кадры видео из чата копились в horo/tmp/inbox навсегда — удаляем старше days дней. -> сколько убрано."""
    import shutil
    gone, edge = 0, time.time() - days * 86400
    try:
        names = os.listdir(INBOX)
    except OSError:
        return 0
    for name in names:
        path = os.path.join(INBOX, name)
        try:
            if os.path.getmtime(path) < edge:
                shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
                gone += 1
        except OSError as e:
            print("inbox:", e)
    return gone


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
    if said and (LINK.search(said) or draft() or spoken_button(said) in BUTTONS or prep.is_request(said) or not frames):
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
    if mode.get(chat) == "show" and (text.startswith("/") or (text in BUTTONS and text != BTN_SHOW)):
        mode.pop(chat)  # иначе ссылка, присланная когда-нибудь потом, только показала бы ответы, а не сдала

    if text in ("/start site", "/site") or text.startswith("/site "):  # ссылка t.me/<бот>?start=site со страницы входа
        send_site(chat)
        return
    if text.startswith("/start"):
        if APP_URL:
            on_app_url(APP_URL)  # кнопка приложения — новому владельцу
        if not ui_mode():  # первый раз: сначала выбор, чат или приложение
            send(chat, mode_card(), markup=mode_kb(), raw_html=True)
        else:
            start_card(chat, name)
        return
    if text.startswith("/mode") or text == BTN_MODE:
        send(chat, mode_card(), markup=mode_kb(), raw_html=True)
        return
    if text.startswith("/help"):
        send(chat, HELP, menu=True, raw_html=True)
        return
    if text.startswith("/wait") or text == BTN_WAIT:
        mode.pop(chat, None)
        send_waiting(chat)
        return
    if text.startswith("/exams") or text == BTN_EXAMS:
        mode.pop(chat, None)
        arg = text[6:].strip().lower() if text.startswith("/exams") else ""
        if arg in ("2", "2.0", "3", "3.0", "all", "все"):   # /exams 2 — сразу список
            lvl = None if arg in ("all", "все") else int(arg[0])
            threading.Thread(target=send_exams, args=(chat, lvl), daemon=True).start()
        else:
            exams_choice(chat)
        return
    if text.startswith("/prep") or text == BTN_PREP:
        mode.pop(chat, None)
        threading.Thread(target=prep_menu, args=(chat,), daemon=True).start()
        return
    if mode.get(chat) == "prep_tz":  # ждём своё ТЗ для подготовки (текстом или голосом)
        if text and not text.startswith("/") and text not in BUTTONS and not LINK.search(text):
            threading.Thread(target=prep_take_tz, args=(chat, text), daemon=True).start()
            return
        mode.pop(chat)
    if text.startswith("/app"):
        send_app(chat)
        return
    if text.startswith("/update"):
        update(chat)
        return
    if (mode.get(chat) or "").startswith("agent_") and (text in BUTTONS or text.startswith("/")) \
            and text != "/cancel" and not text.startswith("/agent"):
        agent.pop(chat, None); mode.pop(chat, None)  # кнопка или другая команда — шаг /agent закончен, выполняем её
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
        with settings_lock:
            s = load_settings(); s.pop("emoji", None); save_settings(s)
        send(chat, "👌 Премиум-эмодзи сброшены, снова обычные", menu=True)
        return
    if text.startswith("/emoji"):
        mode[chat] = "emoji"
        send(chat, card("Премиум-эмодзи", "Пришли одним сообщением премиум-эмодзи — буду ставить их в своих "
                        "сообщениях вместо обычных.", "Работает, если у тебя есть Telegram Premium", "✨"),
             markup=cancel_kb(), raw_html=True)
        return
    if mode.get(chat) == "emoji":
        mode.pop(chat)
        if text not in BUTTONS and not text.startswith("/"):  # кнопка или команда вместо эмодзи — выполняем её
            save_emoji(chat, msg)
            return
    if text.startswith("/alphabet_reset"):
        ok, out = py("horo/alphabet.py", "reset")
        send(chat, out if ok else err(out), menu=True, raw_html=not ok)
        return
    if text.startswith("/alphabet") or text == BTN_HAND:
        threading.Thread(target=send_alphabet, args=(chat,), daemon=True).start()
        return
    if text.startswith("/sitecheck"):
        threading.Thread(target=site_check, args=(chat,), daemon=True).start()
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
                 markup=cancel_kb(), raw_html=True)
            return
        mode.pop(chat)
    if text in SEND_LABELS or text in (BTN_DELAY_ON, BTN_DELAY_OFF) or text == "/send":  # ⏳ Отправка
        send(chat, send_card(), markup=send_kb(), raw_html=True)
        return
    if text.startswith("/delay"):  # /delay on|off — всё «как человек» сразу (задержка 4–12 мин и паузы в тестах)
        arg = text[6:].strip().lower()
        on = arg in ("on", "вкл", "1") if arg else not human_on()
        set_human(on)
        set_delay_range(SEND_DELAY if on else "0")
        send(chat, send_card(), menu=True, raw_html=True)  # клавиатура обновится: на кнопке новое состояние
        return
    if mode.get(chat, "").startswith("at:") and len(text) > 40 and not text.startswith("/"):
        mode.pop(chat, None)  # длинный текст — не время, а правка или просьба: обычное сообщение
    if mode.get(chat, "").startswith("at:") and text and not text.startswith("/") and text not in BUTTONS:
        at = parse_time(text)
        task = mode.pop(chat)[3:]
        if at and draft(task):
            schedule_send(chat, task, at=at)
            return
        if at is None and re.search(r"\d", text):
            send(chat, card("Не понял время", note="Напиши, например, 19:40 или «через 40 минут»", icon="🤔"), raw_html=True)
            mode[chat] = "at:" + task
            return
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
        if text.lower() in ("да", "yes", "ок", "давай"):
            threading.Thread(target=run_all, args=(chat, pending.pop(chat, [])), daemon=True).start()
            return
        if text and text not in BUTTONS and not text.startswith("/") and not LINK.search(text):
            pending.pop(chat, None)
            send(chat, "👌 Отменил", menu=True)
            return
        # другая кнопка, команда, ссылка или фото — выполняем их; «Сдать все» под списком по-прежнему работает
    target = draft_of(msg)  # ответ на карточку черновика — правка именно его
    if target and attachment(msg):
        threading.Thread(target=add_draft_photo, args=(chat, msg, target["task"]), daemon=True).start()
        return
    if target and text and not text.startswith("/") and text not in BUTTONS and not LINK.search(text):
        if text.lower().strip(".! ") in ("ок", "ok", "да", "отправляй", "отправь"):
            threading.Thread(target=schedule_send, args=(chat, target["task"]), daemon=True).start()
        else:
            threading.Thread(target=edit_draft, args=(chat, text, target["task"]), daemon=True).start()
        return
    if kind == "photo":  # черновика нет: ищем на фото задание
        threading.Thread(target=look_photo, args=(msg,), daemon=True).start()
        return
    if text and prep.is_request(text) and not LINK.search(text):  # «подготовь к проверочной по алгебре за 5 минут»
        threading.Thread(target=prep_text, args=(chat, text), daemon=True).start()
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
             markup=cancel_kb(), raw_html=True)
        return
    if text == BTN_ALL:
        threading.Thread(target=find_auto_tests, args=(chat,), daemon=True).start()
        return

    links = list({drafts.task_id(u): u for u in LINK.findall(text)}.values())  # несколько ссылок — каждое задание
    if not links:
        send(chat, card("Не понял", note="Пришли ссылку на задание или выбери действие внизу", icon="🤔"),
             menu=True, raw_html=True)
        return
    show = mode.pop(chat, None) == "show"
    if len(links) > 1:
        send(chat, card(f"Беру {len(links)} задания", note=f"До {MAX_PARALLEL} решаю сразу, остальные — следом. "
                        "Черновики учителю придут каждый своей карточкой, править можно любой", icon="📚"), raw_html=True)
    for u in links:
        threading.Thread(target=run_task, args=(chat, u, text if len(links) == 1 else "", show, msg.get("message_id")),
                         daemon=True).start()


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


def app_draft_info(d):
    with sending_lock:
        now = d["task"] in SENDING
    return {"task": d["task"], "title": d["title"], "url": d["url"], "text": drafts.read(d["task"]),
            "files": len(drafts.files(d["task"])), "send_at": d.get("send_at") or 0, "sending": now}


def app_state(_=None):
    """Что сейчас происходит: опрашивается приложением раз в пару секунд."""
    d = draft()
    ov = overview()
    lives = list(Live.items)
    live = Live.active if Live.active in lives else (lives[-1] if lives else None)
    return {
        "busy": lock.locked(),  # заняты ВСЕ места — новое задание встанет в очередь
        "working": lock.busy() > 0 or bool(lives),  # что-то решается прямо сейчас (хоть одно задание)
        "live": live.state() if live else None,
        "others": max(0, len(lives) - 1),  # сколько ещё идёт рядом с показанным
        "last": LAST or None,
        "draft": app_draft_info(d) if d else None,
        "drafts": [app_draft_info(x) for x in all_drafts()],
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
    return {"alphabet": os.path.exists(GLEB_EXTRA), "mode": ui_mode(), "human": human_on(),
            "version": git("rev-parse", "--short", "HEAD")}


def busy_check():
    if lock.locked():
        raise ValueError(f"Сейчас решаю {MAX_PARALLEL} задания — подожди, пока одно закончу")


def app_solve(b):
    m = LINK.search(str(b.get("url") or ""))
    if not m:
        raise ValueError("Это не ссылка на задание HDP")
    wish = str(b.get("wish") or "").strip()[:500]  # «💬 Пожелания» к заданию на сайте
    threading.Thread(target=run_task, args=(owner(), m.group(0), wish, bool(b.get("show"))), daemon=True).start()
    return {"ok": True}


def app_auto(_):
    busy_check()
    tests = [r for r in (overview() or {}).get("tasks", []) if r.get("auto") and r.get("rawStatus") in AUTO_STATUSES]
    if not tests:
        raise ValueError("Новых автотестов нет")
    threading.Thread(target=run_all, args=(owner(), tests), daemon=True).start()
    return {"ok": True, "n": len(tests)}


def app_draft(action):
    def fn(b):
        d = draft(str(b.get("task") or "") or None)
        if not d:
            raise ValueError("Черновика уже нет")
        if action == "send":
            threading.Thread(target=schedule_send, args=(owner(), d["task"]), daemon=True).start()
        elif action == "drop":
            send(owner(), card("Не отправляю", note=f"{esc(d['title'])} — отменён в приложении", icon="👌"), menu=True,
                 raw_html=True)
            finish_draft(owner(), d["task"])
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


def app_mode(b):
    """«💬 Перейти в чат» из приложения (или обратно): запоминает режим; в чате сразу приходит главное меню."""
    m = str(b.get("mode") or "")
    set_mode(m)
    if m == "chat":
        send(owner(), card("Режим: " + MODES[m], "Всё, что было в приложении, есть и здесь — кнопки внизу.",
                           "Вернуть приложение — /mode", "✅"), menu=True, raw_html=True)
    return {"mode": m}


def app_support(body):
    """🛠 Поддержка из приложения — та же, что /fix в чате (web.py уже проверил, что это владелец)."""
    if not SUP:
        raise ValueError("Поддержка ещё запускается — попробуй через минуту")
    return SUP.web(owner(), body)


ROUTES = {
    "/api/support": app_support, "/api/mode": app_mode,
    "/api/state": app_state, "/api/overview": app_overview, "/api/me": app_me,
    "/api/solve": app_solve, "/api/auto": app_auto,
    "/api/human": lambda b: (set_human(bool(b.get("on"))), {"human": human_on()})[1],
    "/api/draft/send": app_draft("send"), "/api/draft/drop": app_draft("drop"), "/api/draft/save": app_draft("save"),
    "/api/check": app_chat(check), "/api/alphabet": app_chat(send_alphabet),
}


# ---------- 🎯 Подтверждения и контрольные 2.0 / 3.0 ----------
# Все такие задания по всем предметам (из overview.json — темы с сайта): что сдано, что на проверке, что на доработке
# (результат и сколько нужно для зачёта), что ещё не начато — со ссылками. Глеб, 15.10: «отдельная кнопка».

EXAM = re.compile(r"подтвержд|контрольн|итогов\w* работ|unit test|test\s*[23][.,]0|тест на уровень|проверочн", re.I)
EXAM_SKIP = re.compile(r"подготовк", re.I)          # «Подготовка к подтверждению» — не само подтверждение
EXAM_ST = {  # статус: (значок, слово, порядок — несданное выше)
    "reworking": ("🔁", "на доработке", 0), "failed": ("❌", "не зачтено", 0), "appointed": ("📝", "не сдано", 1),
    "reviewing": ("🕓", "на проверке", 2), "approved": ("✅", "зачтено", 3),
}


def exam_items(data):
    """Подтверждения и контрольные из тем overview.json: [(уровень 2|3, пункт)], несданные первыми."""
    out = []
    for tp in (data or {}).get("topics") or []:
        for t in tp.get("tasks") or []:
            title = (t.get("title") or "").strip()
            if not EXAM.search(title) or EXAM_SKIP.search(title):
                continue
            lvl = 3 if "3.0" in title else 2 if "2.0" in title else (3 if (t.get("lvl") or 2) >= 3 else 2)
            st = EXAM_ST.get(t.get("status"), ("•", t.get("status") or "", 1))
            out.append({"lvl": lvl, "icon": st[0], "word": st[1], "order": st[2], "subj": tp.get("subj") or "",
                        "sicon": tp.get("icon") or "", "title": title, "deadline": tp.get("deadline") or "",
                        "pct": t.get("pct") if t.get("pct") is not None else (t.get("max") if t.get("score") is None
                                                                             and t.get("status") != "appointed" else None),
                        "pass": t.get("pass"), "status": t.get("status"),
                        "url": f"https://horodigital.ru/student/topic/{tp.get('uuid')}/task/{t.get('uuid')}"})
    out.sort(key=lambda x: (x["lvl"], x["order"], x["subj"], x["title"]))
    return out


def exam_line(x):
    bits = [x["word"]]
    if x["pct"] is not None:
        bits.append(f"{x['pct']}%" + (f" из {x['pass']}%" if x["pass"] and x["status"] in ("reworking", "failed") else ""))
    if x["status"] in ("appointed", "reworking", "failed") and re.match(r"\d{4}-\d\d-\d\d", x["deadline"]):
        late = x["deadline"][:10] < time.strftime("%Y-%m-%d")
        bits.append(("❗ срок был " if late else "до ") + ".".join(reversed(x["deadline"][:10].split("-")[1:])))
    title = x["title"] if len(x["title"]) <= 60 else x["title"][:57] + "…"
    return f'{x["icon"]} {x["sicon"]} <a href="{esc(x["url"])}">{esc(x["subj"])} · {esc(title)}</a> — {esc(", ".join(bits))}'


def exams_messages(data, lvl=None):
    """HTML-сообщения (каждое ≤ 3900 знаков с разметкой): шапка, по уровню 2.0 и 3.0 (или только lvl) — несданное
    цитатой, зачтённое — свёрнутой цитатой. Цитаты не режутся посреди: длинный список — несколькими цитатами."""
    items = [x for x in exam_items(data) if lvl in (None, x["lvl"])]
    name = f"Подтверждения {lvl}.0" if lvl else "Подтверждения и контрольные"
    if not items:
        return [card(name, note="На сайте таких нет" if lvl else "Подтверждений и контрольных на сайте не нашёл",
                     icon="🎯")]
    n = lambda f: sum(f(x) for x in items)
    head = (f"🎯 <b>{name}</b>\n<i>зачтено " + str(n(lambda x: x["status"] == "approved"))
            + f" из {len(items)} · на доработке " + str(n(lambda x: x["status"] in ("reworking", "failed")))
            + " · не сдано " + str(n(lambda x: x["status"] == "appointed"))
            + " · на проверке " + str(n(lambda x: x["status"] == "reviewing")) + "</i>")
    blocks = [head]
    for lv in (2, 3):
        mine = [x for x in items if x["lvl"] == lv]
        if not mine:
            continue
        if not lvl:
            blocks.append(f"<b>{lv}.0</b>")
        for tag, rows in (("blockquote", [x for x in mine if x["status"] != "approved"]),
                          ("blockquote expandable", [x for x in mine if x["status"] == "approved"])):
            lines = ([f"✅ зачтено: {len(rows)}"] if "expandable" in tag and rows else []) + [exam_line(x) for x in rows]
            cur = []
            for ln in lines:
                if cur and sum(len(c) + 1 for c in cur) + len(ln) > 3600:
                    blocks.append(f"<{tag}>" + "\n".join(cur) + "</blockquote>")
                    cur = []
                cur.append(ln)
            if cur:
                blocks.append(f"<{tag}>" + "\n".join(cur) + "</blockquote>")
    msgs, cur = [], ""
    for b in blocks:
        if cur and len(cur) + len(b) + 1 > 3900:
            msgs.append(cur)
            cur = ""
        cur = (cur + "\n" + b) if cur else b
    return msgs + ([cur] if cur else [])


def exams_choice(chat):
    """«🎯 Подтверждения»: сначала выбор — 2.0, 3.0 или все (на кнопке — сколько не зачтено)."""
    items = exam_items(overview() or {})
    left = lambda lv: sum(x["status"] != "approved" and lv in (None, x["lvl"]) for x in items)
    lab = lambda text, lv: text + (f" · {left(lv)}" if items and left(lv) else "")
    send(chat, card("Подтверждения", note="Какие показать? На кнопке — сколько ещё не зачтено" if items
                    else "Какие показать?", icon="🎯"),
         markup=kb([(lab("2.0", 2), "ex:2"), (lab("3.0", 3), "ex:3"), (lab("Все", None), "ex:all")]), raw_html=True)


def send_exams(chat, lvl=None, fresh=False):
    """Список подтверждений (lvl 2, 3 или все): данные не старше 10 минут, иначе (и по «🔄 Обновить») — свежие с сайта."""
    data = overview()
    if fresh or not data or time.time() - (data.get("at") or 0) / (1000 if (data.get("at") or 0) > 1e11 else 1) > 600:
        live = Live(chat, "Смотрю подтверждения", "с сайта", progress=False)
        try:
            refresh_overview()
        finally:
            live.done()
        data = overview() or data
    if not data:
        send(chat, err(OV.get("error") or "Не получилось взять задания с сайта"), menu=True, raw_html=True)
        return
    msgs = exams_messages(data, lvl)
    tag = str(lvl) if lvl else "all"
    nav = [("🔄 Обновить", f"ex:upd:{tag}")] + [(t, f"ex:{c}") for t, c in (("2.0", "2"), ("3.0", "3"), ("Все", "all"))
                                                 if c != tag]
    for k, text in enumerate(msgs):
        send(chat, text, markup=kb(nav) if k == len(msgs) - 1 else None, raw_html=True)


# ---------- 📖 Подготовка к проверочной ----------
# Предмет -> что прошли (темы с сайта, horo/prep.js, только чтение) -> 1 / 5 / 10 минут -> Claude (bot/prep_prompt.md)
# -> конспект и вопросы с ответами под спойлером. Или своё ТЗ. Логика — bot/prep.py, здесь чат и приложение.

PREP_DIR = os.path.join(REPO, "horo", "tmp", "prep")  # темы с сайта (если их нет в overview.json) и последний результат
PREP_FRESH = 24 * 3600                                 # темы меняются редко: раз в сутки достаточно
PREP_CTX = {}   # chat -> {"subj", "icon", "topics", "tz", "minutes", "asked", "details", "names"}
PREP_JOB = {"running": False, "title": "", "sub": "", "t0": 0, "error": "", "result": None}


def prep_cache(name):
    return os.path.join(PREP_DIR, name)


def prep_topics(fetch=True):
    """Все темы по предметам: из overview.json (его обновляет приложение), иначе свой кэш, иначе с сайта."""
    ov = overview() or {}
    if ov.get("topics") and time.time() - ov.get("at", 0) / 1000 < PREP_FRESH:
        return ov["topics"]
    try:
        if time.time() - os.path.getmtime(prep_cache("topics.json")) < PREP_FRESH:
            with open(prep_cache("topics.json")) as f:
                return json.load(f)
    except (OSError, ValueError):
        pass
    if not fetch:
        return ov.get("topics") or []
    ok, out = node("horo/prep.js", timeout=180)
    try:
        topics = json.loads(out) if ok else None
    except ValueError:
        topics = None
    if not isinstance(topics, list):
        print("prep topics:", out[:300])
        return ov.get("topics") or []
    os.makedirs(PREP_DIR, exist_ok=True)
    with open(prep_cache("topics.json") + ".part", "w") as f:
        json.dump(topics, f, ensure_ascii=False)
    os.replace(prep_cache("topics.json") + ".part", prep_cache("topics.json"))
    return topics


def prep_icon(topics, subj):
    return next((prep.icon_of(t) for t in topics if prep.subject_of(t) == subj), "📖")


def prep_ctx(chat, subj="", tz="", topics=None, uuids=None):
    """Новый контекст подготовки: предмет и его недавние темы (или выбранные в приложении), ТЗ."""
    every = topics if topics is not None else prep_topics()
    chosen = [t for t in every if t.get("uuid") in set(uuids)] if uuids else (prep.recent(every, subj) if subj else [])
    PREP_CTX[chat] = {"subj": subj or "", "icon": prep_icon(every, subj) if subj else "✍️", "topics": chosen,
                      "tz": tz or "", "minutes": 0, "asked": [], "details": None,
                      "names": [s["name"] for s in prep.subjects(every)]}
    return PREP_CTX[chat]


def prep_time_kb(back=True):
    rows = [[(prep.BUTTON[m], f"pp:t:{m}", "primary" if m == 5 else None) for m in prep.MINUTES]]
    if back:
        rows.append([("‹ Предметы", "pp:back"), ("✍️ Своё ТЗ", "pp:tz")])
    return kb(*rows)


def prep_menu(chat, mid=None):
    """Шаг 1: предметы кнопками (по 3 в ряд) и «Своё ТЗ»."""
    live = None
    if not (overview() or {}).get("topics") and not os.path.exists(prep_cache("topics.json")):
        live = Live(chat, "Смотрю, что вы прошли", "темы с сайта HDP", progress=False)
    try:
        topics = prep_topics()
    finally:
        if live:
            live.done()
    subs = prep.subjects(topics)
    PREP_CTX[chat] = {**PREP_CTX.get(chat, {}), "names": [s["name"] for s in subs]}
    btns = [(f"{s['icon']} {prep.short(s['name'])}", f"pp:s:{i}") for i, s in enumerate(subs)]
    rows = [btns[i:i + 3] for i in range(0, len(btns), 3)] + [[("✍️ Своё ТЗ", "pp:tz")]]
    text = card("Подготовка к проверочной",
                "Выбери предмет — посмотрю, что вы прошли, и подготовлю за 1, 5 или 10 минут." if subs else
                "Тем с сайта пока не вижу — напиши своё ТЗ, по чему готовиться.",
                "Или «✍️ Своё ТЗ»: напиши или скажи, по чему готовиться", "📖")
    if mid:
        edit(chat, mid, text, kb(*rows))
    else:
        send(chat, text, markup=kb(*rows), raw_html=True)


def prep_ask_tz(chat):
    mode[chat] = "prep_tz"
    subj = (PREP_CTX.get(chat) or {}).get("subj")
    send(chat, card("Своё ТЗ", "Напиши или скажи голосом, по чему подготовить: темы, параграфы, вопросы учителя.",
                    (f"Предмет — {esc(subj)}. " if subj else "") + "Можно сразу время: «за 5 минут»", "✍️"),
         markup=cancel_kb(), raw_html=True)


def prep_take_tz(chat, text):
    """Своё ТЗ: предмет и время — если названы, иначе предмет из прошлого шага и кнопки времени."""
    mode.pop(chat, None)
    before = PREP_CTX.get(chat) or {}
    topics = prep_topics()
    subj, minutes, _ = prep.detect(text, [s["name"] for s in prep.subjects(topics)])
    prep_ctx(chat, subj or before.get("subj") or "", text, topics)
    if minutes:
        prep_run(chat, minutes)
    else:
        send(chat, card("Понял ТЗ", esc(text), "Сколько у тебя времени?", "✍️"), markup=prep_time_kb(back=False),
             raw_html=True)


def prep_text(chat, text):
    """«Подготовь к проверочной по алгебре за 5 минут» текстом или голосом: сразу к делу, чего нет — спросить."""
    topics = prep_topics()
    subj, minutes, tz = prep.detect(text, [s["name"] for s in prep.subjects(topics)])
    if not subj and not tz:
        prep_menu(chat)
        return
    ctx = prep_ctx(chat, subj, tz, topics)
    if minutes:
        prep_run(chat, minutes)
    elif subj and not tz:
        send(chat, prep.topics_html(subj, ctx["icon"], ctx["topics"]), markup=prep_time_kb(), raw_html=True)
    else:
        send(chat, card("Понял ТЗ", esc(text), "Сколько у тебя времени?", "✍️"), markup=prep_time_kb(back=False),
             raw_html=True)


def prep_run(chat, minutes, more=False):
    """Готовит: материалы тем с сайта (только чтение) -> Claude -> карточки в чат (и в приложение)."""
    ctx = PREP_CTX.get(chat)
    if not ctx or not (ctx.get("subj") or ctx.get("tz")):
        send(chat, err("Эта подготовка уже неактуальна — выбери предмет заново"), raw_html=True)
        prep_menu(chat)
        return
    if not lock.acquire(blocking=False):  # кнопки под прошлым сообщением уже убраны — дать повторить
        send(chat, BUSY, markup=kb([("🔁 Ещё раз", "pp:more" if more else f"pp:t:{minutes}")]))
        return
    subj, tz, topics = ctx["subj"], ctx["tz"], ctx["topics"]
    minutes = minutes if minutes in prep.MINUTES else 5
    sub = " · ".join(x for x in (subj or "своё ТЗ", "новые вопросы" if more else f"за {prep.SPAN[minutes]}") if x)
    PREP_JOB.update(running=True, title="Ещё вопросы" if more else "Готовлю", sub=sub, t0=time.time(), error="")
    t0 = time.time()
    try:
        live = Live(chat, "Придумываю вопросы" if more else "Готовлю", esc(sub), progress=False)
        try:
            if topics and ctx.get("details") is None:  # теория и вопросы тем с сайта — один раз на подготовку
                ok, out = node("horo/prep.js", *[t["uuid"] for t in topics], timeout=180)
                try:
                    ctx["details"] = json.loads(out) if ok else []
                except ValueError:
                    ctx["details"] = []
            ok, out = claude(prep.build_prompt(minutes, subj, topics, ctx.get("details") or [], tz,
                                               ctx["asked"] if more else ()), [], timeout=300)
        finally:
            live.done()
    finally:
        lock.release()
        PREP_JOB["running"] = False
    if not ok:
        PREP_JOB["error"] = plain(out)
        send(chat, err(out), menu=True, raw_html=True)
        return
    res = prep.parse(out)
    if not more:
        ctx["minutes"], ctx["asked"] = minutes, []
    if res:
        ctx["asked"] += [q["q"] for q in res["questions"]]
    row = [("🔁 Ещё вопросы", "pp:more")] + ([("📚 Подробнее (10 мин)", "pp:deep")] if ctx["minutes"] < 10 else [])
    send(chat, prep.render(res, subj, minutes, more, topics, tz, out), markup=kb(row), raw_html=True)
    prev = PREP_JOB.get("result") or {}
    if more and res and prev.get("subj") == subj and prev.get("tz") == tz:  # в приложении — те же карточки, вопросов больше
        PREP_JOB["result"] = {**prev, "more": True, "questions": (prev.get("questions") or []) + res["questions"],
                              "added": len(res["questions"]), "at": int(time.time())}
    else:
        PREP_JOB["result"] = {"subj": subj, "icon": ctx["icon"], "minutes": ctx["minutes"], "more": more, "tz": tz,
                              "topics": [prep.topic_brief(t) for t in topics], "secs": int(time.time() - t0),
                              "at": int(time.time()), **(res or {"text": out.strip()[:6000]})}
    try:
        os.makedirs(PREP_DIR, exist_ok=True)
        with open(prep_cache("last.json"), "w") as f:
            json.dump(PREP_JOB["result"], f, ensure_ascii=False)
    except OSError as e:
        print("prep last:", e)


def on_prep_button(chat, mid, cq_id, key):
    """pp:s:N предмет · pp:t:M время · pp:tz своё ТЗ · pp:back к предметам · pp:more ещё вопросы · pp:deep 10 минут."""
    quiet("answerCallbackQuery", callback_query_id=cq_id)
    ctx = PREP_CTX.get(chat) or {}
    act, _, arg = key.partition(":")
    if act == "s":
        names = ctx.get("names") or []
        if not arg.isdigit() or int(arg) >= len(names):
            threading.Thread(target=prep_menu, args=(chat, mid), daemon=True).start()
            return
        def pick():  # темы могут идти с сайта (до 3 минут) — не в цикле опроса Telegram
            c = prep_ctx(chat, names[int(arg)])
            edit(chat, mid, prep.topics_html(c["subj"], c["icon"], c["topics"]), prep_time_kb())
        threading.Thread(target=pick, daemon=True).start()
    elif act == "back":
        threading.Thread(target=prep_menu, args=(chat, mid), daemon=True).start()
    elif act == "tz":
        quiet("editMessageReplyMarkup", chat_id=chat, message_id=mid, reply_markup=json.dumps({"inline_keyboard": []}))
        prep_ask_tz(chat)
    elif act in ("t", "more", "deep"):
        quiet("editMessageReplyMarkup", chat_id=chat, message_id=mid, reply_markup=json.dumps({"inline_keyboard": []}))
        minutes = int(arg) if arg.isdigit() else 10 if act == "deep" else ctx.get("minutes") or 5
        threading.Thread(target=prep_run, args=(chat, minutes, act == "more"), daemon=True).start()


def app_prep_subjects(_):
    """Предметы и их темы для приложения: недавние отмечены (их и возьмём по умолчанию)."""
    topics = prep_topics(fetch=False)
    if not topics and not PREP_JOB.get("loading"):
        PREP_JOB["loading"] = True

        def load():
            try:
                prep_topics()
            finally:
                PREP_JOB["loading"] = False
        threading.Thread(target=load, daemon=True).start()
    out = []
    for s in prep.subjects(topics):
        mine = sorted((t for t in prep.started(topics) if prep.subject_of(t) == s["name"]),
                      key=lambda t: t["start"], reverse=True)
        out.append({**s, "short": prep.short(s["name"]), "topics": [prep.topic_brief(t) for t in mine[:8]],
                    "recent": [t["uuid"] for t in prep.recent(topics, s["name"])]})
    return {"subjects": out, "loading": bool(PREP_JOB.get("loading")) and not topics}


def app_prep_run(b):
    """Подготовить из приложения: предмет, темы (uuid), минуты, своё ТЗ. more — ещё вопросы к последней."""
    busy_check()
    chat = owner()
    try:
        minutes = int(b.get("minutes") or 5)
    except (TypeError, ValueError):
        minutes = 0
    if minutes not in prep.MINUTES:
        raise ValueError("Время — 1, 5 или 10 минут")
    uuids = b.get("topics") or []
    if not isinstance(uuids, list):
        raise ValueError("Темы — списком")
    if b.get("more"):
        if not PREP_CTX.get(chat):
            raise ValueError("Сначала подготовься — потом будут ещё вопросы")
        threading.Thread(target=prep_run, args=(chat, PREP_CTX[chat].get("minutes") or 5, True), daemon=True).start()
        return {"ok": True}
    subj, tz = str(b.get("subj") or "").strip(), str(b.get("tz") or "").strip()[:1000]
    if not (b.get("again") and PREP_CTX.get(chat)):  # again: «Подробнее (10 мин)» — те же темы и ТЗ
        if not subj and not tz:
            raise ValueError("Выбери предмет или напиши своё ТЗ")
        prep_ctx(chat, subj, tz, uuids=[str(u) for u in uuids] or None)
    threading.Thread(target=prep_run, args=(chat, minutes), daemon=True).start()
    return {"ok": True}


def app_prep_state(_):
    res = PREP_JOB.get("result")
    if res is None:
        try:
            with open(prep_cache("last.json")) as f:
                res = PREP_JOB["result"] = json.load(f)
        except (OSError, ValueError):
            res = None
    return {"running": PREP_JOB["running"], "title": PREP_JOB["title"], "sub": PREP_JOB["sub"],
            "secs": int(time.time() - PREP_JOB["t0"]) if PREP_JOB["running"] else 0, "error": PREP_JOB["error"],
            "result": res}


ROUTES.update({"/api/prep/subjects": app_prep_subjects, "/api/prep/run": app_prep_run,
               "/api/prep/state": app_prep_state})


# ---------- 🌐 Сайт в браузере (bot/site/) ----------
# То же API, что у приложения; здесь только то, чего приложению не надо: теория темы целиком.
SITE_DIR = os.path.join(REPO, "horo", "tmp", "site")
THEORY_FRESH = 12 * 3600
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def app_site_theory(b):
    """Теория темы с сайта HDP (раздел «Материалы», только чтение) — с кэшем на 12 часов."""
    uid = str(b.get("uuid") or "")
    if not UUID.fullmatch(uid):
        raise ValueError("Нет такой темы")
    path = os.path.join(SITE_DIR, f"theory-{uid}.json")
    try:
        if time.time() - os.path.getmtime(path) < THEORY_FRESH and not b.get("fresh"):
            with open(path) as f:
                return {"items": json.load(f)}
    except (OSError, ValueError):
        pass
    ok, out = node("horo/prep.js", "--theory", uid, timeout=180)
    try:
        items = json.loads(out) if ok else None
    except ValueError:
        items = None
    if not isinstance(items, list):
        raise ValueError(plain(out) if not ok else "Не разобрал теорию с сайта")
    os.makedirs(SITE_DIR, exist_ok=True)
    with open(path + ".part", "w") as f:
        json.dump(items, f, ensure_ascii=False)
    os.replace(path + ".part", path)
    return {"items": items}


def app_site_running(_):
    """Какие задания решаются и ждут очереди — чтобы сайт показал «решаю» прямо на карточке."""
    with running_lock:
        now = list(running)
    return {"running": now, "busy": lock.locked()}


ROUTES.update({"/api/site/theory": app_site_theory, "/api/site/running": app_site_running})


# ---------- 🧩 Зеркало: функции бота поверх настоящего horodigital.ru (bot/site/mirror/overlay.js) ----------
# Ожидание, подтверждения, отправка черновика «сразу / с задержкой / ко времени», настройки отправки, картинки
# черновика, подготовка по одной теме. Всё — те же функции, что у кнопок в чате (now:/later:/at:/hold:, sd:, ⏳, 🎯).

def site_task_url(task):
    """Ссылка на задание в работе (из active в настройках) или пусто."""
    return ((load_settings().get("active") or {}).get(task) or {}).get("url", "")


def site_when(at, now):
    when = datetime.datetime.fromtimestamp(at, MSK)
    left = max(0, int(at - now))
    soon = f"через {left // 60} мин" if left < 3600 else f"через {left // 3600} ч {left % 3600 // 60} мин"
    day = "" if when.date() == datetime.datetime.fromtimestamp(now, MSK).date() else "завтра "
    return {"at": int(at), "left": left, "hhmm": f"{when:%H:%M}", "when": f"{day}в {when:%H:%M}", "soon": soon}


def site_work(work):
    """В работе и в очереди: что решается (шаг, доля, время) и что ждёт места — по порядку, с названием и предметом."""
    lives = {x.task: x for x in list(Live.items) if x.task}
    since = {t: a.get("since") or 0 for t, a in (load_settings().get("active") or {}).items()}
    out = []
    for t in sorted(work, key=lambda t: (t not in lives, since.get(t, 0))):
        meta = task_meta(t)
        url = site_task_url(t) or (f"https://horodigital.ru/student/topic/{meta['topic']}/task/{t}" if meta else "")
        item = {"task": t, "title": task_title(t) or "Задание", "url": url, "subj": meta.get("subj", ""),
                "icon": meta.get("icon", "📘")}
        live = lives.get(t)
        if live:
            st = live.state()
            item.update(state="live", step=st["step"], frac=st["frac"], secs=st["secs"])
        else:
            item["state"] = "queue"
        out.append(item)
    n = 0
    for item in out:
        if item["state"] == "queue":
            n += 1
            item["place"] = n
    return out


def app_site_wait(_=None, now=None):
    """⏳ Ожидание для сайта: по таймеру (во сколько и через сколько), отправляются сейчас, ждут «Отправить», в работе."""
    now = now or time.time()
    timed, held, sending, work = waiting(now)
    brief = lambda d: {"task": d["task"], "title": d["title"], "url": d["url"]}
    return {"timed": [{**brief(d), **site_when(d["send_at"], now)} for d in timed],
            "sending": [brief(d) for d in sending], "held": [brief(d) for d in held],
            "work": site_work(work),
            "busy": lock.locked(), "delay": delay_label()}


def app_site_exams(b):
    """🎯 Подтверждения для сайта: пункты exam_items (lvl: 2, 3 или все), данные старше 10 минут — обновляются."""
    data = overview()
    at = (data or {}).get("at") or 0
    stale = not data or time.time() - at / (1000 if at > 1e11 else 1) > 600
    if (b.get("fresh") or stale) and not OV["loading"]:
        OV["loading"] = True
        threading.Thread(target=refresh_overview, daemon=True).start()
        data = overview() or data
    items = exam_items(data or {})
    lvl = str(b.get("lvl") or "all")
    left = lambda lv: sum(x["status"] != "approved" and lv in (None, x["lvl"]) for x in items)
    return {"items": [x for x in items if lvl not in ("2", "3") or str(x["lvl"]) == lvl],
            "left": {"2": left(2), "3": left(3), "all": left(None)}, "at": (data or {}).get("at") or 0,
            "loading": OV["loading"], "error": OV["error"]}


def app_site_times(_=None):
    """Варианты «🕐 Ко времени» — те же, что кнопки в чате."""
    return {"times": [{"label": lab, "at": ts} for lab, ts in time_choices()]}


def app_site_send(b):
    """Отправка черновика с сайта: mode = send (по настройке «⏳ Отправка») · now (сразу) · later (с задержкой, даже
    если в настройке «сразу» — тогда 4–12 мин) · at (к моменту at, epoch, или ко времени словами text: «19:40»)."""
    d = draft(str(b.get("task") or "") or None)
    if not d:
        raise ValueError("Черновика уже нет")
    chat, mode = owner(), str(b.get("mode") or "send")
    if mode == "at":
        try:
            at = int(b.get("at") or 0)
        except (TypeError, ValueError):
            at = 0
        at = at or parse_time(str(b.get("text") or "")) or 0
        if not time.time() + 30 <= at <= time.time() + 3 * 86400 + 600:
            raise ValueError("Не понял время — напиши, например, 19:40 или «через 40 минут»")
        schedule_send(chat, d["task"], at=at)
    elif mode == "now":
        d.pop("send_at", None)
        set_draft(d)
        threading.Thread(target=submit_draft, args=(chat, d["task"]), daemon=True).start()
    elif mode == "later":
        rng = delay_range()
        threading.Thread(target=schedule_send, args=(chat, d["task"], send_delay(rng if rng != "0" else "4-12")),
                         daemon=True).start()
    elif mode == "send":
        threading.Thread(target=schedule_send, args=(chat, d["task"]), daemon=True).start()
    else:
        raise ValueError("Не знаю такой отправки")
    return app_state()


def app_site_hold(b):
    """«✖ Отменить» таймер отправки (как кнопка hold: в чате): черновик остаётся."""
    d = draft(str(b.get("task") or "") or None)
    if not d:
        raise ValueError("Черновика уже нет")
    if d.pop("send_at", None):
        set_draft(d)
        remember_mids(d["task"], send(owner(), card("Не отправляю", note=f"{esc(d['title'])} · таймер отменён на сайте, "
                                                    "черновик остался", icon="👌"),
                                      markup=draft_kb(d["task"]), raw_html=True))
    return app_state()


def app_site_settings(_=None):
    return {"human": human_on(), "delay": delay_range(), "label": delay_label(), "wish": default_wish(),
            "choices": [{"v": v, "label": lab} for v, lab in SEND_CHOICES]}


def app_site_settings_set(b):
    """Настройки «⏳ Отправка» с сайта: delay — задержка ответа учителю, human — паузы в тестах «как человек»."""
    if "delay" in b:
        if str(b["delay"]) not in dict(SEND_CHOICES):
            raise ValueError("Такой задержки нет")
        set_delay_range(str(b["delay"]))
    if "human" in b:
        set_human(bool(b["human"]))
    if "wish" in b:
        set_default_wish(b["wish"])
    return app_site_settings()


DRAFT_SHOW = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
              ".gif": "image/gif"}
DRAFT_SHOW_MAX = 12_000_000  # всего байт картинок в одном ответе (дальше — только имена)


def app_site_draft_files(b):
    """Картинки черновика (решение от руки, заполненная таблица) для сайта — data: URL; PDF — только имя."""
    d = draft(str(b.get("task") or "") or None)
    if not d:
        raise ValueError("Черновика уже нет")
    out, total = [], 0
    for path in drafts.files(d["task"]):
        name = os.path.basename(path)
        kind = DRAFT_SHOW.get(os.path.splitext(name)[1].lower())
        size = os.path.getsize(path)
        if not kind or total + size > DRAFT_SHOW_MAX:
            out.append({"name": name, "data": ""})
            continue
        total += size
        with open(path, "rb") as f:
            out.append({"name": name, "data": f"data:{kind};base64," + base64.b64encode(f.read()).decode()})
    return {"task": d["task"], "files": out}


def app_site_prep(b):
    """«📖 Подготовь меня» на странице темы: тема (uuid) и минуты -> та же подготовка, что в приложении."""
    uid, topics = str(b.get("topic") or ""), prep_topics(fetch=False)
    t = next((x for x in topics if x.get("uuid") == uid), None) if UUID.fullmatch(uid) else None
    if not t:
        raise ValueError("Этой темы нет в данных бота — обнови их и попробуй ещё раз")
    return app_prep_run({"subj": prep.subject_of(t), "topics": [uid], "minutes": b.get("minutes") or 5})


ROUTES.update({"/api/site/wait": app_site_wait, "/api/site/exams": app_site_exams, "/api/site/times": app_site_times,
               "/api/site/send": app_site_send, "/api/site/hold": app_site_hold,
               "/api/site/settings": app_site_settings, "/api/site/settings/set": app_site_settings_set,
               "/api/site/check": app_site_check,
               "/api/site/draft/files": app_site_draft_files, "/api/site/prep": app_site_prep})


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


def send_site(chat):
    """Сайт HDP в браузере: код из 6 цифр (ввести на сайте в Safari) и одноразовая ссылка — 15 минут, один раз;
    дальше браузер помнит вход полгода. На самом сайте есть и «Войти через Telegram» — подтверждение кнопкой."""
    base = web.site_url()
    if not base:
        send(chat, err("Сайт ещё запускается — попробуй через минуту"), menu=True, raw_html=True)
        return
    pin = web.site_code(pin=True)
    send(chat, card("Сайт HDP — копия платформы",
                    f"Открой в Safari: <b>{esc(base.split('//')[-1])}</b>\n"
                    f"Войди кнопкой «Войти через Telegram» или кодом <code>{pin[:3]} {pin[3:]}</code>",
                    "Код живёт 15 минут. Safari запомнит вход на полгода", "🌐"),
         markup=kb([("🌐 Открыть сайт", {"url": web.site_link()}, "primary")]), raw_html=True)


def site_login_ask(short, ua, pin):
    """Страница входа на сайт просит вход: карточка Глебу с «✅ Войти» (и код — на случай, если удобнее ввести)."""
    send(owner(), card("Вход на сайт HDP", f"{esc(ua)} просит войти.\nИли код: <code>{pin[:3]} {pin[3:]}</code>",
                       "Не ты — не нажимай. 15 минут", "🌐"),
         markup=kb([("✅ Войти", f"sitel:{short}", "success")]), raw_html=True)


def setup_profile():
    """Меню команд, описание и подпись бота в Telegram (видно в пустом чате и в профиле)."""
    quiet("setMyCommands", commands=json.dumps([{"command": c, "description": d} for c, d in COMMANDS]))
    quiet("setMyDescription", description=ABOUT)
    quiet("setMyShortDescription", short_description=ABOUT_SHORT)


def main():
    global offset
    threading.excepthook = thread_crash
    threading.Thread(target=setup_profile, daemon=True).start()
    web.start(TOKEN, owner, ROUTES, on_app_url, site_login_ask)
    print("Бот запущен")

    def alphabet_startup():  # по очереди (оба правят один файл букв) и не задерживая приём сообщений
        print("alphabet renorm:", py("horo/alphabet.py", "renorm"))  # старый алфавит — к размеру каждой буквы
        retry_alphabet()
    threading.Thread(target=alphabet_startup, daemon=True).start()
    threading.Thread(target=clean_inbox, daemon=True).start()
    killed = kill_orphans()  # до resume_active: иначе старый и новый запуск сдают одно задание вдвоём
    if killed:
        print("остановил Claude прошлого запуска:", killed)
    resume_tests()
    threading.Thread(target=sender_loop, daemon=True).start()
    threading.Thread(target=resume_active, daemon=True).start()  # задания, прерванные перезапуском  # отложенные «как человек» отправки учителю
    with settings_lock:
        s = load_settings()
        updated = s.pop("updated", None)
        if updated:
            save_settings(s)
    if updated:  # поднялись после /update
        try:
            head = subprocess.run(["git", "log", "-1", "--format=%h %s"], cwd=REPO, capture_output=True, text=True).stdout.strip()
            send(owner(), card("Обновился", note=esc(head), icon="✨"), menu=True, raw_html=True)
        except Exception as e:
            print("startup:", e)
    global SUP
    SUP = sup = support.Support(TOKEN, "HDP", folders=["bot", "horo"], pm2="horo-bot",
                                allowed=lambda uid: uid == owner(),
                                tests=["python3 bot/test_bot.py"])
    while True:
        try:
            upd = tg("getUpdates", offset=offset, timeout=60)
        except Exception as e:  # сеть моргнула — пробуем снова
            print("getUpdates:", e)
            journal("net", f"getUpdates: {e}")
            beat()  # цикл жив, просто нет сети
            time.sleep(5)
            continue
        beat()
        for u in upd.get("result", []):
            offset = u["update_id"] + 1
            on_update(u, sup)


def oops(chat, e, tb=None):
    """Непредвиденная ошибка в обработчике или фоновом деле: Глебу — одна строка «⚠️», подробности — в лог pm2
    и в журнал (с трассировкой — по ней Страж HDP в Главном зовёт Claude чинить)."""
    if tb is None:
        tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        print(tb, file=sys.stderr)
    journal("bug", f"{type(e).__name__}: {e}", tb)
    try:
        send(chat, err(f"Что-то сломалось: {e}", "Попробуй ещё раз. Повторится — 🛠 Поддержка (/fix)"),
             menu=True, raw_html=True)
    except Exception as e2:
        print("oops:", e2)


def thread_crash(args):
    """threading.excepthook: дело в фоне упало — раньше Глеб видел только тишину."""
    threading.__excepthook__(args)
    if not issubclass(args.exc_type, SystemExit) and owner():
        oops(owner(), args.exc_value, "".join(traceback.format_exception(args.exc_type, args.exc_value,
                                                                         args.exc_traceback)))


def on_update(u, sup=None):
    """Одно обновление Telegram (из цикла main; тесты зовут его же): поддержка, кнопка или сообщение."""
    try:
        if sup and sup.intercept(u):  # 🛠 /fix и ответы поддержке
            return
    except Exception as e:  # сбой поддержки не должен ронять приём сообщений
        print("support:", e)
    cq = u.get("callback_query")
    if cq:
        msg = cq.get("message") or {}
        if cq.get("from", {}).get("id") != owner():  # чужой человек нажал кнопку — крутилка не должна висеть
            quiet("answerCallbackQuery", callback_query_id=cq.get("id"), text="Это личный бот")
            return
        if "chat" not in msg:
            quiet("answerCallbackQuery", callback_query_id=cq.get("id"), text="Кнопка устарела — открой меню заново")
            return
        try:
            on_callback(cq)
        except Exception as e:
            print("callback:", e)
            quiet("answerCallbackQuery", callback_query_id=cq.get("id"))
            oops(msg["chat"]["id"], e)
        return
    msg = u.get("message") or {}
    uid = msg.get("from", {}).get("id")
    if "chat" not in msg:
        return
    if not owner() and (msg.get("text") or "").startswith("/start"):
        with settings_lock:
            s = load_settings(); s["owner"] = uid; save_settings(s)  # первый /start — это владелец
        print("Владелец бота:", uid)
    if uid != owner():
        return
    try:
        handle(msg)
    except Exception as e:
        print("handle:", e)
        oops(msg["chat"]["id"], e)


if __name__ == "__main__":
    main()
