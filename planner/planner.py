#!/usr/bin/env python3
"""Telegram-бот «Ритм»: планер дня, недели и месяца, цели и заметки к ним.

Главное — приложение внутри Telegram (Mini App, папка webapp/): день, неделя, месяц, цели.
В чате: быстрое добавление текстом («завтра в 18:00 бокс»), план на сегодня с галочками,
утренний план, вечерние итоги, напоминания.

Переменные окружения (на сервере — /etc/planner-bot.env):
  PLANNER_TOKEN    токен от @BotFather
  PLANNER_ALLOWED  Telegram ID через запятую, кому можно пользоваться; если пусто —
                   владельцем станет тот, кто первым нажмёт /start
  PLANNER_PUBLIC   1 — пускать всех (у каждого свои данные)
  PLANNER_URL      свой https-адрес приложения; если пусто — бот сам поднимает туннель cloudflared
  PLANNER_PORT     порт веб-сервера на 127.0.0.1 (по умолчанию 8787)
  PLANNER_DB       файл базы (по умолчанию planner/data/planner.db)

Только стандартная библиотека Python.
"""
import datetime
import hashlib
import hmac
import html
import json
import mimetypes
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import parse  # noqa: E402
import store  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(HERE), "common"))
import media  # noqa: E402  голосовые и фото (common/media.py)
import menukb  # noqa: E402  ☰ меню-квадратик у поля ввода (common/menukb.py)
import support  # noqa: E402  🛠 поддержка: /fix — Claude чинит и дорабатывает этого бота

TOKEN = os.environ.get("PLANNER_TOKEN", "")
API = f"https://api.telegram.org/bot{TOKEN}/"
ALLOWED = {int(x) for x in re.findall(r"\d+", os.environ.get("PLANNER_ALLOWED", ""))}
PUBLIC = os.environ.get("PLANNER_PUBLIC") == "1"
PORT = int(os.environ.get("PLANNER_PORT") or 8787)
COACH_PORT = 8789  # coach/coach.py
HUB_PORT = 8790  # hub/hub.py
EYE_PORT = 8791  # eye/eye.py
KPP_PORT = 8792  # kpp/kpp.py
PULSE_PORT = 8798  # pulse/pulse.py
AVITO_PORT = 8793  # avito/avito.py
AGENT_PORT = 8794  # tgbot/agent.py
STUDIO_PORT = 8795  # studio/studio.py: приложение Студии и мини-приложения сделанных ею ботов
CATCHER_PORT = 8796  # catcher/catcher.py: «Ловец» юзернеймов
SITES_PORT = 8797  # studio/studio.py в режиме STUDIO_MODE=sites: «Студия сайтов» сестры (ecosystem.sites.config.js)
HDP_PORT = 8788  # bot/web.py: сайт HDP в браузере (/site/) и мини-приложение HDP-бота
DATA = os.path.join(HERE, "data")
WEB = os.path.join(HERE, "webapp")
REPO = os.path.dirname(HERE)
NAME = "Ритм"

db = store.Store(os.environ.get("PLANNER_DB") or os.path.join(DATA, "planner.db"))
public_url = os.environ.get("PLANNER_URL", "").rstrip("/")
SUP = None  # 🛠 поддержка (common/support.py) — ставит main(), ей же пользуется приложение
offset = 0

WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
WD_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября",
              "октября", "ноября", "декабря"]
MONTHS = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь",
          "Октябрь", "Ноябрь", "Декабрь"]
HORIZON = {"week": "На неделю", "month": "На месяц", "year": "На год", "none": "Без срока"}


def esc(x):
    return html.escape(str(x), quote=False)


def plural(n, one, few, many):
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def bar(pct, size=10):
    full = round(pct / 100 * size)
    return "▰" * full + "▱" * (size - full)


def human_date(d, today=None):
    today = today or d
    word = {0: "Сегодня", 1: "Завтра", -1: "Вчера"}.get((d - today).days)
    base = f"{d.day} {MONTHS_GEN[d.month - 1]}"
    return f"{word}, {base}" if word else f"{WEEKDAYS[d.weekday()]}, {base}"


def repeat_text(rep):
    if rep == "1234567":
        return "каждый день"
    if rep == "12345":
        return "по будням"
    if rep == "67":
        return "по выходным"
    return "по " + ", ".join(WD_SHORT[int(c) - 1] for c in rep)


# ---------- Telegram ----------

class TgError(Exception):
    """Ответ Telegram с ошибкой: код и текст (по тексту видно, что именно не понравилось)."""

    def __init__(self, method, code, detail):
        super().__init__(f"{method} {code}: {detail[:300]}")
        self.code, self.detail = code, detail


def tg(method, **params):
    body = json.dumps(params).encode()
    req = urllib.request.Request(API + method, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=70) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        if "message is not modified" in detail:
            return {"ok": True}
        print(method, e.code, detail[:300])
        raise TgError(method, e.code, detail) from None


# Цветные кнопки ("style": primary | success | danger) — новинка Telegram. Если сервер их не примет,
# шлём те же кнопки без цвета и дальше уже не пробуем.
STYLE_OK = True


def btn(text, data, style=None):
    b = {"text": text, "callback_data": data}
    if style:
        b["style"] = style
    return b


def _plain(rows):
    return [[{k: v for k, v in b.items() if k != "style"} for b in row] for row in rows]


def with_markup(method, buttons, **params):
    global STYLE_OK
    if buttons is not None:
        params["reply_markup"] = {"inline_keyboard": buttons if STYLE_OK else _plain(buttons)}
    try:
        return tg(method, **params)
    except TgError as e:
        styled = STYLE_OK and any("style" in b for row in buttons or [] for b in row)
        words = ("button", "markup", "keyboard", "style")
        if not (styled and e.code == 400 and any(w in e.detail.lower() for w in words)):
            raise
        params["reply_markup"] = {"inline_keyboard": _plain(buttons)}
        res = tg(method, **params)
        STYLE_OK = False
        return res


def send(chat, text, buttons=None, **extra):
    return with_markup("sendMessage", buttons, chat_id=chat, text=text, parse_mode="HTML",
                       link_preview_options={"is_disabled": True}, **extra)


def edit(chat, msg_id, text, buttons=None):
    return with_markup("editMessageText", buttons, chat_id=chat, message_id=msg_id, text=text, parse_mode="HTML",
                       link_preview_options={"is_disabled": True})


def show(chat, msg_id, text, buttons=None):
    """Экран на месте старого сообщения; не вышло (сообщение удалено, слишком старое, без текста) — новым."""
    if msg_id:
        try:
            return edit(chat, msg_id, text, buttons)
        except TgError as e:
            if e.code != 400:
                raise
    return send(chat, text, buttons)


def answer(cq_id, text=""):
    """Ответ на нажатие (крутилка у кнопки гаснет). Старое нажатие Telegram уже не примет — это не ошибка."""
    try:
        tg("answerCallbackQuery", callback_query_id=cq_id, text=text[:190])
    except Exception as e:
        print("answerCallbackQuery:", e)


HOME = "home"  # «‹ Меню»: главный экран на месте текущего сообщения


def home_btn():
    return btn("‹ Меню", HOME)


def app_button(text="🗓 Открыть планер", path="", style=None, always=False):
    """Кнопка «открыть планер» (web_app). В режиме «💬 чат» не ставится — кроме явной просьбы (/app, always)."""
    if not public_url or (ui_mode() == "chat" and not always):
        return None
    # экран передаём в ?p=, а не в #: в # Telegram дописывает свои параметры запуска
    b = {"text": text, "web_app": {"url": public_url + "/" + (f"?p={urllib.parse.quote(path)}" if path else "")}}
    if style:
        b["style"] = style
    return b


def with_app(rows, text="🗓 Открыть планер", path="", style=None, always=False):
    b = app_button(text, path, style, always)
    return rows + [[b]] if b else rows


def card(emoji, title, body=None, note=None, expand=None):
    """Единый вид сообщения: «эмодзи + жирный заголовок», содержимое в цитате, второстепенное курсивом в конце.
    body — строка или список строк (уже в HTML); длинная цитата сворачивается (expandable)."""
    parts = [f"{emoji} <b>{title}</b>"]
    if isinstance(body, (list, tuple)):
        body = "\n".join(body)
    if body:
        if expand is None:
            expand = body.count("\n") >= 12 or len(body) > 700
        parts.append(f"<blockquote{' expandable' if expand else ''}>{body.strip()}</blockquote>")
    if note:
        parts.append(f"<i>{note}</i>")
    return "\n".join(parts)


def short(text, n=24):
    """Подпись кнопки: не длиннее n символов."""
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def setup_bot():
    """Команды, описание и кнопка меню. Описание меняем, только если поменялось (у Telegram лимиты)."""
    commands = [
        {"command": "today", "description": "План на сегодня"},
        {"command": "tomorrow", "description": "План на завтра"},
        {"command": "week", "description": "Неделя"},
        {"command": "month", "description": "Месяц"},
        {"command": "goals", "description": "Мои цели"},
        {"command": "notes", "description": "Заметки"},
        {"command": "app", "description": "Открыть планер"},
        {"command": "mode", "description": "Чат или приложение"},
        {"command": "settings", "description": "Утро, вечер, напоминания"},
        {"command": "help", "description": "Как пользоваться"},
    ]
    desc = ("Планер на день, неделю и месяц: цели, заметки, напоминания.\n\n"
            "Напиши «завтра в 18:00 тренировка» — и задача уже в плане. Можно голосом, фото или видео.")
    about = "Планер: день, неделя, месяц и цели."
    key = hashlib.sha1(json.dumps([commands, desc, about, NAME]).encode()).hexdigest()
    if db.meta("bot_setup") != key:
        try:
            tg("setMyCommands", commands=commands)
            tg("setMyDescription", description=desc)
            tg("setMyShortDescription", short_description=about)
            db.meta("bot_setup", key)
        except Exception as e:
            print("setup:", e)


def set_menu():
    if not public_url:
        return False
    try:
        tg("setChatMenuButton", menu_button={"type": "web_app", "text": "Планер", "web_app": {"url": public_url + "/"}})
        return True
    except Exception as e:
        print("menu:", e)
        return False


# ---------- доступ ----------

def owner():
    v = db.meta("owner")
    return int(v) if v else 0


def allowed(uid):
    if PUBLIC:
        return True
    if ALLOWED:
        return uid in ALLOWED
    return uid == owner()


def claim(uid):
    """Первый /start становится владельцем, если список ALLOWED пуст."""
    if not PUBLIC and not ALLOWED and not owner():
        db.meta("owner", uid)
        print("Владелец:", uid)


def can_support(uid):
    """🛠 Поддержка (правка кода бота) — только владельцу, даже в PUBLIC."""
    return bool(uid) and (uid == owner() or uid in ALLOWED)


def can_set_mode(uid):
    """Режим «чат/приложение» один на весь бот. В обычном режиме бот личный — можно; в PUBLIC меняет владелец
    (пока владельца нет — любой, иначе первый выбор был бы невозможен)."""
    return not PUBLIC or can_support(uid) or not (owner() or ALLOWED)


def sup_row(uid):
    """Ряд с кнопкой «🛠 Поддержка» для главных меню — только тем, кому она доступна."""
    return [support.Support.button()] if can_support(uid) else []


# ---------- режим: 💬 чат или 📱 приложение ----------
# Выбирается при первом /start, меняется по /mode, кнопкой «⚙️ Режим» и из приложения («💬 Перейти в чат»).
# «чат» — всё кнопками здесь, кнопки «открыть планер» в сообщениях нет (кнопка «Планер» слева от поля ввода
# остаётся); «приложение» — главная кнопка открывает его, но команды и текст работают всё равно. Хранится в db.meta.

MODES = {"chat": "💬 чат", "app": "📱 приложение"}


def ui_mode():
    m = db.meta("mode")
    return m if m in MODES else ""


def set_mode(m):
    if m not in MODES:
        raise ValueError("Режим — chat или app")
    db.meta("mode", m)


def mode_note():
    return f"Режим: {MODES.get(ui_mode(), 'не выбран')} · сменить — /mode"


def mode_card():
    return (card("🧭", "Как удобнее?", ["💬 <b>В чате</b> — всё кнопками и командами здесь",
                                        "📱 <b>Приложение</b> — день, неделя, цели и заметки экранами"],
                 "Поменять можно в любой момент: /mode"),
            [[btn("💬 В чате", "mode|chat", "primary"), btn("📱 Приложение", "mode|app", "primary")]])


def menu_rows(uid, today):
    """Главное меню чата (режим «💬 чат»): всё, что есть в приложении, — кнопками."""
    tomorrow = today + datetime.timedelta(days=1)
    rows = [[btn("📅 Сегодня", f"d|{today.isoformat()}"), btn("📆 Завтра", f"d|{tomorrow.isoformat()}")],
            [btn("🗓 Неделя", "v|week"), btn("📅 Месяц", "v|month")],
            [btn("🎯 Цели", "v|goals"), btn("📝 Заметки", "v|notes")],
            [btn("⚙️ Настройки", "v|settings"), btn("⚙️ Режим", "mode|ask")]]
    return rows + [r for r in [sup_row(uid)] if r]


# ☰ Меню-квадратик справа от поля ввода (как у Главного): обычная клавиатура Telegram, свёрнута в значок.
# Нажатие приходит текстом — on_kb() открывает тот же экран, что кнопка главного меню; в разбор задач не идёт.
# Ставится по /start и один раз на версию владельцу при запуске (db.meta «kb_ver»); в обоих режимах.
KB = menukb.MenuKB([["📅 Сегодня", "📆 Завтра", "🗓 Неделя"],
                    ["📅 Месяц", "🎯 Цели", "📝 Заметки"],
                    ["⚙️ Настройки", "⚙️ Режим"],
                    ["🛠 Поддержка", "📱 Приложение"]],
                   version=1, placeholder="Задача, заметка или ☰ справа")
KB_VIEWS = {"🗓 Неделя": "week", "📅 Месяц": "month", "🎯 Цели": "goals", "📝 Заметки": "notes",
            "⚙️ Настройки": "settings"}


def send_kb(chat, text, markup):
    return tg("sendMessage", chat_id=chat, text=text, reply_markup=markup)


def ensure_kb(chat, force=False):
    """Поставить меню-квадратик: по /start (force) — всегда, иначе — один раз на версию."""
    v = db.meta("kb_ver")
    KB.ensure(send_kb, chat, done=int(v) if v and str(v).isdigit() else None,
              mark=lambda ver: db.meta("kb_ver", ver), force=force)


def kb_on_start():
    """При запуске: ☰ меню владельцу — один раз на версию. Владельца ещё нет — некому, не шлём."""
    first = owner() or min(ALLOWED or {0})
    if first:
        ensure_kb(first)


def on_kb(chat, uid, text, today):
    """Кнопка меню-квадратика → тот же экран, что у кнопок главного меню и команд."""
    if text == "📅 Сегодня":
        send(chat, *day_view(uid, today))
    elif text == "📆 Завтра":
        send(chat, *day_view(uid, today + datetime.timedelta(days=1)))
    elif text in KB_VIEWS:
        send(chat, *view_for(uid, KB_VIEWS[text], today))
    elif text == "⚙️ Режим":
        send(chat, *mode_card())
    elif text == "🛠 Поддержка":
        if not can_support(uid):
            send(chat, card("🔒", "Поддержка — только для владельца"))
        elif SUP:
            SUP.ask(chat)
        else:
            send(chat, "⚠️ Поддержка ещё запускается — попробуй через минуту")
    elif text == "📱 Приложение":
        open_app(chat)


def open_app(chat):
    """/app и «📱 Приложение»: кнопка открыть планер (в любом режиме)."""
    if public_url:
        send(chat, card("🗓", "Твой планер", note="Он же — кнопка «Планер» слева от поля ввода"),
             with_app([], "🗓 Открыть планер", style="primary", always=True))
    else:
        send(chat, card("⏳", "Приложение запускается", note="Пришлю кнопку, как только будет готово"))


def view_for(uid, name, today):
    """Экраны чата по имени — для команд и кнопок главного меню одинаково."""
    if name == "week":
        return week_text(uid, today), with_app([[btn("📅 Отметить сегодня", f"d|{today.isoformat()}"), home_btn()]],
                                               "🗓 Неделя в планере", "/week")
    if name == "month":
        return month_text(uid, today), with_app([[home_btn()]], "📅 Месяц в планере", "/month")
    if name == "goals":
        return goals_view(uid)
    if name == "notes":
        return notes_view(uid)
    if name == "settings":
        return settings_view(uid)
    raise ValueError(f"нет экрана {name}")


def check_init_data(init_data, token=None, max_age=7 * 86400):
    """Проверка подписи данных Mini App (https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app).
    Возвращает пользователя (dict) или None."""
    token = token or TOKEN
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


# ---------- время пользователя ----------

def user_now(u):
    try:
        tz = ZoneInfo(u.get("tz") or "Europe/Moscow")
    except Exception:
        tz = ZoneInfo("Europe/Moscow")
    return datetime.datetime.now(tz)


def today_of(uid):
    return user_now(db.user(uid)).date()


# ---------- сообщения ----------
# Шрифт в Telegram пропорциональный: колонки пробелами не выравниваем, списки — «• …».

def task_line(t, show_done=True):
    """Строка задачи: «• 18:00 Бокс», сделанная — «✓ 18:00 Бокс» зачёркнутой."""
    time_ = f"{t['time']} " if t.get("time") else ""
    extra = (" <b>!</b>" if t["important"] else "") + (" ↻" if t["repeat"] else "")
    if show_done and t["done"]:
        return f"✓ <s>{time_}{esc(t['title'])}</s>{extra}"
    return f"• {f'<b>{time_.strip()}</b> ' if time_ else ''}{esc(t['title'])}{extra}"


def goal_line(uid, g):
    """Цель одной строкой (для плана дня, утра, недели)."""
    return f"{g['emoji']} {esc(g['title'])} — {db.progress(uid, g)}%"


def day_label(d, today):
    """Подпись кнопки листания: «Вчера», «Завтра» или «пт, 9»."""
    word = {0: "Сегодня", 1: "Завтра", -1: "Вчера"}.get((d - today).days)
    return word or f"{WD_SHORT[d.weekday()]}, {d.day}"


def day_view(uid, day):
    """Текст и кнопки «плана на день» с галочками: коротко — прогресс, а задачи — кнопками."""
    today = today_of(uid)
    tasks = db.day(uid, day)
    done = sum(t["done"] for t in tasks)
    if not tasks:
        body = "Пусто. Напиши задачу: <i>завтра в 18:00 тренировка</i>"
    elif done == len(tasks):
        body = f"✨ <b>Всё сделано!</b> {done} из {len(tasks)}"
    else:
        body = f"<code>{bar(round(100 * done / len(tasks)), 10)}</code> {done} из {len(tasks)}"
    note = []
    if tasks and done < len(tasks):
        note.append("Нажми на задачу — отмечу")
    late = db.overdue(uid, today) if day == today else []
    if late:
        note.append(f"просрочено: {len(late)}" if note else f"Просрочено: {len(late)}")
    text = card("📅", human_date(day, today), body, " · ".join(note))
    rows = []
    for t in tasks[:40]:
        label = ("✅ " if t["done"] else "◻️ ") + (f"{t['time']} " if t["time"] else "") + t["title"]
        rows.append([btn(short(label), f"t|{t['id']}|{day.isoformat()}")])
    prev, nxt = day - datetime.timedelta(days=1), day + datetime.timedelta(days=1)
    nav = [btn("‹ " + day_label(prev, today), f"d|{prev.isoformat()}")]
    if today not in (prev, day, nxt):
        nav.append(btn("Сегодня", f"d|{today.isoformat()}"))
    nav.append(btn(day_label(nxt, today) + " ›", f"d|{nxt.isoformat()}"))
    rows.append(nav)
    rows.append([home_btn()])
    return text, with_app(rows, "🗓 Открыть в планере", f"/day/{day.isoformat()}")


def day_line(uid, d, today, words=True):
    """Один день одной строкой: «<b>пн, 12</b> · 3/6 · Зарядка, Бокс +4»."""
    tasks = db.day(uid, d)
    head = f"<b>{WD_SHORT[d.weekday()]}, {d.day}</b>" + (" · сегодня" if d == today else "")
    if not tasks:
        return head + " · <i>свободно</i>"
    dn = sum(t["done"] for t in tasks)
    left = [t for t in tasks if not t["done"]]
    line = f"{head} · {dn}/{len(tasks)}"
    if words and left:
        names = ", ".join(esc(short(t["title"], 22)) for t in left[:3])
        line += f" · {names}" + (f" +{len(left) - 3}" if len(left) > 3 else "")
    return line


def span_text(mon):
    sun = mon + datetime.timedelta(days=6)
    return (f"{mon.day}–{sun.day} {MONTHS_GEN[sun.month - 1]}" if mon.month == sun.month
            else f"{mon.day} {MONTHS_GEN[mon.month - 1]} – {sun.day} {MONTHS_GEN[sun.month - 1]}")


def week_text(uid, day):
    """Неделя: по строке на день (что осталось), цели недели — курсивом в конце."""
    today = today_of(uid)
    mon = day - datetime.timedelta(days=day.weekday())
    days = [mon + datetime.timedelta(days=i) for i in range(7)]
    total = done = 0
    for d in days:
        tasks = db.day(uid, d)
        total += len(tasks)
        done += sum(t["done"] for t in tasks)
    body = ([f"<code>{bar(round(100 * done / total), 10)}</code> {done} из {total}"] if total else [])
    body += [day_line(uid, d, today) for d in days]
    goals = [g for g in db.current_goals(uid, day) if g["horizon"] == "week"]
    note = ("Цели: " + ", ".join(goal_line(uid, g) for g in goals)) if goals else "Галочки — в /today"
    return card("🗓", f"Неделя · {span_text(mon)}", body, note)


def month_text(uid, day):
    """Месяц: итог и только дни, где что-то есть (длинное — свёрнуто)."""
    today = today_of(uid)
    first = day.replace(day=1)
    nxt = (first + datetime.timedelta(days=32)).replace(day=1)
    lines, total, done = [], 0, 0
    d = first
    while d < nxt:
        tasks = db.day(uid, d)
        total += len(tasks)
        done += sum(t["done"] for t in tasks)
        if any(not t["repeat"] for t in tasks):  # дни только с повторами (зарядка и т.п.) не перечисляем
            lines.append(day_line(uid, d, today, words=d >= today))
        d += datetime.timedelta(days=1)
    if not total:
        return card("📅", f"{MONTHS[first.month - 1]} · пусто", "Задач на этот месяц пока нет")
    head = f"<code>{bar(round(100 * done / total), 10)}</code> {done} из {total}"
    goals = [g for g in db.current_goals(uid, day) if g["horizon"] == "month"]
    note = ("Цели: " + ", ".join(goal_line(uid, g) for g in goals)) if goals else None
    return card("📅", f"{MONTHS[first.month - 1]} {first.year}", [head] + lines, note, expand=len(lines) > 8)


def goals_view(uid):
    """Активные цели одной цитатой и кнопки — открыть цель (шаги, заметки, «достигнута»)."""
    goals = [g for g in db.goals(uid) if g["status"] == "active"]
    if not goals:
        return (card("🎯", "Целей пока нет", "Напиши: <i>цель на месяц: прочитать 2 книги</i>",
                     "Или поставь в планере — вкладка «Цели»"), with_app([[home_btn()]], "🎯 Открыть цели", "/goals"))
    lines = []
    for hz in ("week", "month", "year", "none"):
        part = [g for g in goals if g["horizon"] == hz]
        if part:
            lines.append(f"<b>{HORIZON[hz]}</b>")
            lines += [goal_line(uid, g) for g in part[:12]]
            if len(part) > 12:
                lines.append(f"<i>… и ещё {len(part) - 12} — в планере</i>")
    rows, row = [], []
    for g in goals[:9]:
        row.append(btn(short(f"{g['emoji']} {g['title']}", 24), f"g|{g['id']}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([home_btn()])
    return card("🎯", "Мои цели", lines, "Нажми на цель — шаги и заметки"), with_app(rows, "🎯 Открыть цели", "/goals")


def goal_view(uid, gid):
    g = db.goal(uid, gid)
    if not g:
        return card("🎯", "Цели больше нет", note="Её удалили — список ниже"), [[btn("‹ Все цели", "gl"), home_btn()]]
    p = db.progress(uid, g)
    steps = [t for t in db.tasks(uid) if t["goal"] == gid and not t["repeat"]]
    notes = [n for n in db.notes(uid) if n["goal"] == gid]
    body = [f"<code>{bar(p, 10)}</code> {p}% · {HORIZON[g['horizon']].lower()}"]
    if g["why"]:
        body.append(f"Зачем: {esc(short(g['why'], 160))}")
    if notes:
        body.append(f"📝 {esc(short(notes[0]['text'], 120))}")
    extra = [f"шаги: {sum(t['done'] for t in steps)} из {len(steps)}" if steps else "шагов нет — добавь в планере"]
    if notes:
        extra.append(f"заметок: {len(notes)}")
    if g["status"] == "done":
        extra.insert(0, "🏆 достигнута")
    rows = [[btn(short(("✅ " if t["done"] else "◻️ ") + t["title"]), f"gs|{t['id']}|{gid}")]
            for t in sorted(steps, key=lambda t: t["done"])[:8]]
    if g["status"] == "active":
        rows.append([btn("🏆 Достигнута", f"gd|{gid}", "success"), btn("‹ Все цели", "gl")])
    else:
        rows.append([btn("↩️ Вернуть в работу", f"ga|{gid}"), btn("‹ Все цели", "gl")])
    title = f"{esc(g['title'])}"
    return card(g["emoji"] or "🎯", title, body, " · ".join(extra).capitalize()), with_app(rows, "🎯 Открыть цель", f"/goal/{gid}")


def note_when(uid, created):
    """«9 окт» — дата заметки в часовом поясе пользователя (в базе время UTC: ночью дата иначе съезжала бы на день)."""
    try:
        d = datetime.datetime.fromisoformat(created).astimezone(user_now(db.user(uid)).tzinfo)
    except ValueError:
        d = datetime.datetime.fromisoformat(created[:10])
    return f"{d.day} {MONTHS_GEN[d.month - 1][:3]}"


def notes_view(uid):
    notes = db.notes(uid)
    goals = {g["id"]: g for g in db.goals(uid)}
    if not notes:
        return card("📝", "Заметок пока нет", "Напиши: <i>заметка: купить подарок маме</i>", "Можно голосом"), \
            with_app([[home_btn()]], "📝 Открыть заметки", "/notes")
    lines = []
    for n in notes[:8]:
        g = goals.get(n["goal"])
        tag = f"{g['emoji']} " if g else ""
        when = note_when(uid, n["created"])
        lines.append(f"• {tag}{esc(short(n['text'], 110))} <i>{when}</i>")
    more = f"Ещё {len(notes) - 8} — в планере" if len(notes) > 8 else "Новая: «заметка: …»"
    return card("📝", "Заметки", lines, more), with_app([[home_btn()]], "📝 Все заметки", "/notes")


REMINDS = [-1, 0, 10, 15, 30, 60]


def remind_text(r):
    return "без напоминаний" if r < 0 else "вовремя" if r == 0 else "за час" if r == 60 else f"за {r} мин"


def settings_view(uid):
    u = db.user(uid)
    body = [f"☀️ Утренний план — {u['morning'] or 'выкл'}", f"🌙 Итоги дня — {u['evening'] or 'выкл'}",
            f"⏰ Задачи со временем — {remind_text(u['remind'])}", f"🌍 {esc(u['tz'])}"]
    rows = [[btn(("☀️ Выключить" if u["morning"] else "☀️ Включить") + " утро", "s|m"),
             btn(("🌙 Выключить" if u["evening"] else "🌙 Включить") + " вечер", "s|e")],
            [btn("⏰ " + remind_text(u["remind"]).capitalize() + " ›", "s|r")], [home_btn()]]
    tz = "пояс: /tz Europe/Moscow" if ui_mode() == "chat" else "пояс — в планере"
    return (card("⚙️", "Настройки", body, f"Время: /morning 08:00, /evening 22:00 · {tz}"),
            with_app(rows, "⚙️ Все настройки", "/settings"))


HELP = (
    "✨ <b>Как пользоваться</b>\n"
    "<blockquote>Задачу просто напиши: <i>завтра в 18:00 тренировка</i>, <i>по пн и ср английский в 19:00</i>, "
    "<i>12.10 контрольная!</i> («!» — важное)\n"
    "Заметка: <i>заметка: …</i> · цель: <i>цель на месяц: …</i>\n"
    "Голосовые, фото списка и видео тоже понимаю.</blockquote>\n"
    "<i>/today /tomorrow /week /month /goals /notes /settings</i>"
)


SUP_NOTE = "\n\n🛠 <b>Поддержка</b> — напиши, что сломалось или чего не хватает — починю/добавлю."


def welcome(chat, name, today=None):
    """Главный экран по режиму — новым сообщением. chat — личный чат, то же, что uid."""
    send(chat, *home_view(chat, name, today))


def home_view(chat, name, today=None):
    """Главный экран по режиму: (текст, кнопки). В режиме «📱 приложение» — коротко: открыть планер и пара действий;
    в режиме «💬 чат» — полное меню кнопками."""
    today = today or datetime.date.today()
    title = f"Привет{', ' + esc(name) if name else ''}! Я {NAME}"
    if ui_mode() == "app":
        body = ["Всё — в приложении: день, неделя, цели и заметки.",
                "Задачу можно написать и сюда: <i>завтра в 18:00 тренировка</i> — голосом, фото или видео тоже."]
        if public_url:
            note = mode_note()
            rows = with_app([], "🗓 Открыть планер", style="primary")
        else:
            note = "Приложение запускается — пришлю кнопку, как только будет готово"
            rows = []
        rows = rows + [[btn("📅 Сегодня", f"d|{today.isoformat()}"), btn("🗓 Неделя", "v|week")],
                       [btn("⚙️ Режим", "mode|ask")] + sup_row(chat)]
    else:
        body = ["Пиши задачи как есть: <i>завтра в 18:00 тренировка</i>", "Можно голосом, фото или видео."]
        note = mode_note()
        rows = menu_rows(chat, today)
    return card("👋", title, body, note), rows


def add_task(uid, text, today):
    """Текст -> сохранённая задача (или None, если в тексте нет названия)."""
    p = parse.parse(text, today)
    if not p["title"]:
        return None
    return db.save_task(uid, {"title": p["title"], "date": p["date"].isoformat(), "time": p["time"],
                              "repeat": p["repeat"], "important": p["important"],
                              "remind": db.user(uid)["remind"] if p["time"] else -1})


NOTE_RX = re.compile(r"^\s*(?:📝|заметк[аиу]|запомни|запиши мысль)\s*[:.,—–-]?\s*(.+)", re.I | re.S)
GOAL_RX = re.compile(r"^\s*(?:🎯\s*)?цель(?:\s+на\s+(неделю|месяц|год))?\s*[:—–-]\s*(.+)", re.I | re.S)


def handle_text(chat, uid, text, mid=None, heard=None):
    """Обычный текст (или распознанная речь): «заметка: …» — заметка, «цель: …» — цель, иначе — задача."""
    m = NOTE_RX.match(text)
    if m and m.group(1).strip():
        return save_note(chat, uid, m.group(1).strip(), mid, heard)
    m = GOAL_RX.match(text)
    if m and m.group(2).strip():
        return save_goal(chat, uid, m.group(2).strip(), m.group(1), mid, heard)
    quick_add(chat, uid, text, mid, heard)


def save_note(chat, uid, text, mid=None, heard=None):
    n = db.save_note(uid, {"text": text[:1].upper() + text[1:]})
    rows = [[btn("↩️ Отменить", f"un|{n['id']}")]]
    app = app_button("📝 Заметки", "/notes")
    if app:
        rows[0].append(app)
    replace(chat, mid, card("📝", "Заметка сохранена", esc(short(n["text"], 600)),
                            f"🎙 «{esc(heard)}»" if heard else "Все заметки — /notes"), rows)


def save_goal(chat, uid, title, horizon_word, mid=None, heard=None):
    hz = {"неделю": "week", "месяц": "month", "год": "year"}.get((horizon_word or "").lower(), "month")
    g = db.save_goal(uid, {"title": title[:1].upper() + title[1:], "horizon": hz,
                           "start": store.period_start(hz, today_of(uid)).isoformat()})
    rows = [[btn("🎯 Открыть", f"g|{g['id']}"), btn("↩️ Отменить", f"ug|{g['id']}")]]
    replace(chat, mid, card("🎯", "Цель поставлена", f"{g['emoji']} <b>{esc(g['title'])}</b> · {HORIZON[hz].lower()}",
                            f"🎙 «{esc(heard)}»" if heard else "Шаги и заметки — в планере"), rows)


def quick_add(chat, uid, text, mid=None, heard=None):
    """Добавить задачу из текста; mid — сообщение-статус, которое заменится результатом (голосовые)."""
    today = today_of(uid)
    t = add_task(uid, text, today)
    if not t:
        tail = f" в «{esc(heard)}»" if heard else ""
        replace(chat, mid, f"⚠️ Не понял, что добавить{tail}. Например: <i>завтра в 18:00 тренировка</i>")
        return
    replace(chat, mid, added_text(t, today, heard), added_buttons(t, today))


def added_text(t, today, heard=None):
    when = human_date(datetime.date.fromisoformat(t["date"]), today) if t["date"] else "Без даты"
    if t["repeat"]:
        when = repeat_text(t["repeat"]).capitalize()
    body = [f"<b>{esc(t['title'])}</b>", when + (f" · {t['time']}" if t["time"] else "")]
    extra = []
    if t["important"]:
        extra.append("важное")
    if t["time"] and t["remind"] >= 0:
        extra.append("напомню " + ("в это время" if t["remind"] == 0 else f"за {t['remind']} мин"))
    if extra:
        body.append(", ".join(extra).capitalize())
    return card("✅", "Добавлено", body, f"🎙 «{esc(heard)}»" if heard else None)


def added_buttons(t, today):
    rows = []
    if not t["repeat"]:
        opts = [("На сегодня", today), ("На завтра", today + datetime.timedelta(days=1))]
        moves = [btn(label, f"m|{t['id']}|{d.isoformat()}") for label, d in opts if t["date"] != d.isoformat()]
        if t["date"]:
            moves.append(btn("Без даты", f"m|{t['id']}|-"))
        rows.append(moves)
    last = [btn("↩️ Отменить", f"u|{t['id']}")]
    app = app_button("🗓 В планере", f"/day/{t['date']}" if t["date"] else "/week")
    if app:
        last.append(app)
    rows.append(last)
    return [r for r in rows if r]


# ---------- голосовые и фото ----------

class AIError(Exception):
    """Понятная причина для пользователя."""


NO_TOOLS = ["--disallowedTools", "Bash", "Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch", "Task", "Agent"]


def spawn(fn, *args):
    """Медленное (распознавание речи, Claude) — в отдельном потоке, чтобы бот не замирал."""
    def run():
        try:
            fn(*args)
        except Exception as e:
            print("media:", e)
            traceback.print_exc()
    threading.Thread(target=run, daemon=True).start()


def status(chat, text):
    """Короткий статус («Слушаю…»), который потом заменится результатом; -> message_id или None."""
    try:
        tg("sendChatAction", chat_id=chat, action="typing")
        res = send(chat, text).get("result")
        return res.get("message_id") if isinstance(res, dict) else None
    except Exception:
        return None


def replace(chat, mid, text, buttons=None):
    return show(chat, mid, text, buttons)


def guarded(fn):
    """Голос, фото, видео: любая неожиданная ошибка — понятное «⚠️» вместо вечного «Слушаю…»."""
    def run(chat, uid, msg):
        box = {}
        try:
            return fn(chat, uid, msg, box)
        except Exception as e:
            print("media:", e)
            traceback.print_exc()
            try:
                replace(chat, box.get("mid"), "⚠️ Не получилось разобрать — попробуй ещё раз или напиши текстом")
            except Exception:
                pass
    run.__name__ = fn.__name__
    return run


@guarded
def on_voice(chat, uid, msg, box):
    mid = box["mid"] = status(chat, "🎙 <i>Слушаю…</i>")
    try:
        text = media.voice_text(TOKEN, msg)
    except media.MediaError as e:
        replace(chat, mid, f"⚠️ Не расслышал: {esc(e)}")
        return
    handle_text(chat, uid, text, mid, heard=text)  # дальше — как будто это написали; статус заменится карточкой


SECRET_ENV = re.compile(r"(TOKEN|SECRET|PASSWORD|_KEY)$", re.I)


def clean_env():
    """Окружение для `claude -p`: без токенов ботов и чужих ключей (свой вход Claude — ANTHROPIC_*/CLAUDE_* — остаётся)."""
    return {k: v for k, v in os.environ.items()
            if k.startswith(("ANTHROPIC_", "CLAUDE_")) or not SECRET_ENV.search(k)}


def claude(prompt, cwd, timeout=240):
    """`claude -p` в отдельной папке (без чужих CLAUDE.md); картинки читает инструментом Read."""
    cmd = ["claude", "-p", prompt, "--output-format", "json", "--allowedTools", "Read", "--max-turns", "4", *NO_TOOLS]
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=clean_env())
    except FileNotFoundError:
        raise AIError("на сервере нет Claude Code — напиши задачи текстом") from None
    except subprocess.TimeoutExpired:
        raise AIError("Claude долго думает — попробуй ещё раз") from None
    try:
        res = json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else {}
    except ValueError:
        res = {}
    if p.returncode != 0 or res.get("is_error") or not res.get("result"):
        log = (p.stderr + p.stdout)[-1000:]
        print("claude ->", p.returncode, log)
        low = log.lower()
        if any(w in low for w in ("login", "api key", "401", "unauthorized", "authenticat")):
            raise AIError("Claude на сервере не вошёл в аккаунт")
        if any(w in low for w in ("rate limit", "usage limit", "limit reached", "429", "overloaded")):
            raise AIError("у Claude закончился лимит, попробуй позже")
        raise AIError("Claude не ответил")
    return res["result"].strip()


def photo_prompt(path, caption, today, speech=""):
    """path — картинка или список кадров видео; speech — что говорят в видео."""
    paths = [path] if isinstance(path, str) else list(path)
    what = (f"На картинке {paths[0]} — расписание, список дел или заметка (скриншот или от руки). Открой её"
            if len(paths) == 1 else
            f"Это кадры из видео: {', '.join(paths)} — там может быть расписание, список дел или заметка. Открой их")
    return (f"Сегодня {today:%d.%m.%Y}, {WEEKDAYS[today.weekday()].lower()}. {what} инструментом Read и выпиши задачи "
            "по одной на строку в формате планера:\n"
            "• «ДД.ММ ЧЧ:ММ название» — дату и время пиши, только если они есть на картинке или в подписи;\n"
            "• просто день недели (пн, вт…) — переведи в ближайшую такую дату ДД.ММ, начиная с сегодня;\n"
            "• повторяющиеся — только если так и написано (каждый, по понедельникам, расписание уроков на неделю): "
            "«по пн и ср ЧЧ:ММ название», «каждый день ЧЧ:ММ название»;\n"
            "• без даты — просто «название»; важное — «!» в конце.\n"
            "Название — коротко, по-русски, как написано. Без нумерации, заголовков и пояснений. "
            "Если задач нет — ответь одним словом НЕТ."
            + (f"\n\nПодпись: «{caption}»" if caption else "")
            + (f"\n\nЧто говорят в видео (задачи из речи тоже выпиши): «{speech}»" if speech else ""))


def task_lines(answer):
    """Ответ Claude -> строки задач (без маркеров списка и «НЕТ»)."""
    out = []
    for line in (answer or "").splitlines():
        line = re.sub(r"^\s*(?:[-•*·]|\d{1,2}[.)])\s+", "", line).strip(" «»\"'`")
        if line and not line.startswith("```") and line.upper().rstrip(".!") != "НЕТ":
            out.append(line[:200])
    return out[:20]


@guarded
def on_photo(chat, uid, msg, box):
    mid = box["mid"] = status(chat, "📷 <i>Читаю фото…</i>")
    today = today_of(uid)
    work = tempfile.mkdtemp(prefix="planner-ai-")
    try:
        path = media.photo_path(TOKEN, msg, work)
        answer = claude(photo_prompt(path, (msg.get("caption") or "").strip(), today), work)
    except (media.MediaError, AIError) as e:
        replace(chat, mid, f"⚠️ Не получилось: {esc(e)}")
        return
    finally:
        shutil.rmtree(work, ignore_errors=True)
    tasks = [t for t in (add_task(uid, line, today) for line in task_lines(answer)) if t]
    if not tasks:
        replace(chat, mid, card("📷", "Не нашёл на фото задач", note="Напиши их текстом или скажи голосом"))
        return
    report_tasks(chat, mid, "📷", tasks, today)


def report_tasks(chat, mid, emoji, tasks, today, heard=None):
    """Карточка «Добавил N задач» (из фото или видео)."""
    lines = []
    for t in tasks[:15]:
        when = (repeat_text(t["repeat"]) if t["repeat"]
                else human_date(datetime.date.fromisoformat(t["date"]), today).lower() if t["date"] else "без даты")
        lines.append(f"• {esc(short(t['title'], 80))} — {when}" + (f" · {t['time']}" if t["time"] else "")
                     + (" <b>!</b>" if t["important"] else ""))
    if len(tasks) > 15:
        lines.append(f"<i>… и ещё {len(tasks) - 15} — в планере</i>")
    first = tasks[0]["date"]
    note = f"🎙 «{esc(short(heard, 200))}»" if heard else "Поправить можно в планере"
    replace(chat, mid, card(emoji, f"Добавил {len(tasks)} {plural(len(tasks), 'задачу', 'задачи', 'задач')}", lines, note),
            with_app([], "🗓 Открыть в планере", f"/day/{first}" if first else "/week"))


@guarded
def on_video(chat, uid, msg, box):
    """Видео и кружки: кадры (что на них написано) + речь — как фото и голосовое вместе."""
    mid = box["mid"] = status(chat, "🎬 <i>Смотрю видео…</i>")
    today = today_of(uid)
    work = tempfile.mkdtemp(prefix="planner-ai-")
    try:
        try:
            v = media.video(TOKEN, msg, work)
        except media.MediaError as e:
            replace(chat, mid, f"⚠️ Не получилось: {esc(e)}")
            return
        speech = (v.get("text") or "").strip()
        caption = (msg.get("caption") or "").strip()
        if speech and (NOTE_RX.match(speech) or GOAL_RX.match(speech)):
            return handle_text(chat, uid, speech, mid, heard=speech)  # «заметка: …» голосом в кружке
        answer = ""
        if v.get("frames"):
            try:
                answer = claude(photo_prompt(v["frames"], caption, today, speech), work)
            except AIError as e:
                if not speech:
                    replace(chat, mid, f"⚠️ Не получилось: {esc(e)}")
                    return
        tasks = [t for t in (add_task(uid, line, today) for line in task_lines(answer)) if t]
        if tasks:
            report_tasks(chat, mid, "🎬", tasks, today, heard=speech)
        elif speech:
            handle_text(chat, uid, speech, mid, heard=speech)  # на кадрах ничего — тогда как голосовое
        else:
            replace(chat, mid, card("🎬", "Не нашёл в видео задач", note="Скажи их голосом или напиши текстом"))
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---------- обработка ----------

def on_message(msg):
    chat = msg["chat"]["id"]
    frm = msg.get("from") or {}
    uid = frm.get("id")
    text = (msg.get("text") or "").strip()
    if msg["chat"].get("type") != "private":
        return
    if text.startswith("/start"):
        claim(uid)
    if not allowed(uid):
        send(chat, card("🔒", "Это личный планер", note="Попроси владельца добавить тебя"))
        return
    db.user(uid, frm.get("first_name"))
    cmd = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
    today = today_of(uid)
    if KB.has(text):  # кнопка меню-квадратика — экран, не задача
        return on_kb(chat, uid, text.strip(), today)
    if cmd == "/start":
        ensure_kb(chat, force=True)  # ☰ меню у поля ввода — до главного экрана
        if not ui_mode():  # первый раз: сначала выбор, чат или приложение
            send(chat, *mode_card())
        else:
            welcome(chat, frm.get("first_name"), today)
    elif cmd == "/mode":
        send(chat, *mode_card())
    elif cmd == "/tz":
        arg = text.split()[1] if len(text.split()) > 1 else ""
        try:
            if not arg:
                raise ValueError
            db.update_settings(uid, {"tz": arg})
            send(chat, *settings_view(uid))
        except Exception:
            send(chat, "⚠️ Напиши пояс: <i>/tz Europe/Moscow</i>")
    elif cmd == "/help":
        send(chat, HELP + SUP_NOTE * can_support(uid), with_app([r for r in [sup_row(uid)] if r]))
    elif cmd == "/today":
        send(chat, *day_view(uid, today))
    elif cmd == "/tomorrow":
        send(chat, *day_view(uid, today + datetime.timedelta(days=1)))
    elif cmd in ("/week", "/month", "/goals", "/notes", "/settings"):
        send(chat, *view_for(uid, cmd[1:], today))
    elif cmd in ("/morning", "/evening"):
        arg = text.split()[1].lower() if len(text.split()) > 1 else ""
        try:
            value = "" if arg in ("off", "выкл", "нет") else store._clean_time(arg) if arg else None
        except (ValueError, IndexError):
            value = None
        if value is None:
            send(chat, f"⚠️ Напиши время: <i>{cmd} 08:00</i> или <i>{cmd} off</i>")
        else:
            db.update_settings(uid, {cmd[1:]: value})
            send(chat, *settings_view(uid))
    elif cmd == "/app":
        open_app(chat)
    elif cmd == "/status":
        send(chat, card("🩺", "Состояние", [f"Адрес: {esc(public_url or 'нет адреса')}", f"https: {esc(https_note)}"]))
    elif cmd == "/export":
        export(chat, uid)
    elif cmd == "/update" and uid == (owner() or min(ALLOWED or {0})):
        update(chat)
    elif cmd:
        send(chat, "⚠️ Такой команды нет — /help подскажет, что я умею")
    elif media.kind(msg) == "voice":
        spawn(on_voice, chat, uid, msg)
    elif media.kind(msg) == "photo":
        spawn(on_photo, chat, uid, msg)
    elif media.kind(msg) == "video":
        spawn(on_video, chat, uid, msg)
    elif text:
        handle_text(chat, uid, text)
    else:  # стикер, файл, место, опрос… — не молчим
        send(chat, card("🤔", "Это я не разберу", "Пришли текст, голосовое, фото списка или видео — "
                                                   "выпишу задачи"))


def on_callback(cq):
    uid = cq["from"]["id"]
    msg = cq.get("message") or {}
    chat, mid = msg.get("chat", {}).get("id"), msg.get("message_id")
    data = (cq.get("data") or "").split("|")
    note = ""
    if not allowed(uid) or not chat:
        answer(cq["id"], "🔒 Это личный планер" if chat else "")
        return
    today = today_of(uid)
    kind = data[0]
    try:
        if kind == "mode":  # 💬 чат / 📱 приложение
            if data[1] in MODES and not can_set_mode(uid):  # режим общий на весь бот — меняет владелец
                note = "Режим меняет владелец"
            elif data[1] in MODES:
                set_mode(data[1])
                show(chat, mid, card("✅", "Режим: " + MODES[data[1]], note="Сменить — /mode или кнопка «⚙️ Режим»"))
                welcome(chat, cq["from"].get("first_name"), today)
            else:
                show(chat, mid, *mode_card())
        elif kind == HOME:  # «‹ Меню» — главный экран на месте
            show(chat, mid, *home_view(chat, cq["from"].get("first_name"), today))
        elif kind == "v":  # главное меню чата: экран на месте меню, назад — «‹ Меню»
            show(chat, mid, *view_for(uid, data[1], today))
        elif kind == "t":  # галочка в плане дня
            if db.task(uid, int(data[1])):
                new = db.toggle(uid, int(data[1]), data[2])
                note = "Сделано ✨" if new else "Снял отметку"
            else:  # кнопка из старого сообщения, задачу уже удалили — просто обновим план
                note = "⚠️ Этой задачи уже нет"
            show(chat, mid, *day_view(uid, datetime.date.fromisoformat(data[2])))
        elif kind == "d":  # листаем дни
            show(chat, mid, *day_view(uid, datetime.date.fromisoformat(data[1])))
        elif kind == "u":  # отменить быстрое добавление
            t = db.task(uid, int(data[1]))
            db.delete_task(uid, int(data[1]))
            show(chat, mid, card("↩️", "Отменено", f"<s>{esc(t['title'])}</s>" if t else None))
        elif kind == "m":  # перенести
            t = db.save_task(uid, {"id": int(data[1]), "date": None if data[2] == "-" else data[2]})
            show(chat, mid, added_text(t, today), added_buttons(t, today))
            note = "Перенёс"
        elif kind == "r":  # «сделано» из напоминания
            t = db.task(uid, int(data[1]))
            if t:
                db.toggle(uid, int(data[1]), data[2], done=True)
            else:
                note = "⚠️ Этой задачи уже нет"
            show(chat, mid, card("✅", "Сделано", f"<s>{esc(t['title'])}</s>", "Отлично!") if t
                 else card("🗑", "Этой задачи уже нет"))
        elif kind == "c":  # перенести несделанное на завтра (из вечерних итогов)
            day = datetime.date.fromisoformat(data[1])
            nxt = (day + datetime.timedelta(days=1)).isoformat()
            tasks = db.day(uid, day)  # как было в итогах — до переноса
            moved = 0
            for t in tasks:
                if not t["done"] and not t["repeat"]:
                    db.move(uid, t["id"], nxt)
                    moved += 1
            note = f"Перенёс {moved} на завтра" if moved else "Нечего переносить"
            text = evening_text(uid, day, tasks, moved) if tasks else card("➡️", f"Перенёс на завтра: {moved}")
            show(chat, mid, text, with_app([], "🗓 План на завтра", f"/day/{nxt}"))
        elif kind == "g":  # открыть цель
            show(chat, mid, *goal_view(uid, int(data[1])))
        elif kind == "gl":  # назад к целям
            show(chat, mid, *goals_view(uid))
        elif kind == "gs":  # шаг цели — галочка
            if db.task(uid, int(data[1])):
                new = db.toggle(uid, int(data[1]))
                note = "Сделано ✨" if new else "Снял отметку"
            else:
                note = "⚠️ Этого шага уже нет"
            show(chat, mid, *goal_view(uid, int(data[2])))
        elif kind in ("gd", "ga"):  # цель достигнута / снова в работе
            if db.goal(uid, int(data[1])):
                db.save_goal(uid, {"id": int(data[1]), "status": "done" if kind == "gd" else "active"})
                note = "Цель достигнута! 🏆" if kind == "gd" else "Снова в работе"
            else:
                note = "⚠️ Цели больше нет"
            show(chat, mid, *goal_view(uid, int(data[1])))
        elif kind == "un":  # отменить заметку
            n = db.note(uid, int(data[1]))
            db.delete_note(uid, int(data[1]))
            show(chat, mid, card("↩️", "Заметка удалена", f"<s>{esc(short(n['text'], 200))}</s>" if n else None))
        elif kind == "ug":  # отменить цель
            g = db.goal(uid, int(data[1]))
            db.delete_goal(uid, int(data[1]))
            show(chat, mid, card("↩️", "Цель удалена", f"<s>{esc(g['title'])}</s>" if g else None))
        elif kind == "s":  # настройки из чата
            u = db.user(uid)
            if data[1] == "r":
                cur = REMINDS.index(u["remind"]) if u["remind"] in REMINDS else 0
                db.update_settings(uid, {"remind": REMINDS[(cur + 1) % len(REMINDS)]})
            else:
                key, default = ("morning", "07:30") if data[1] == "m" else ("evening", "21:30")
                db.update_settings(uid, {key: "" if u[key] else default})
            note = "Сохранил"
            show(chat, mid, *settings_view(uid))
        else:
            note = "⚠️ Кнопка устарела — открой /start"
    except KeyError as e:  # задачу/цель/заметку уже удалили (кнопка из старого сообщения)
        reason = str(e).strip("'\"") or "этого уже нет"
        note = f"⚠️ {reason[:1].upper() + reason[1:]} — экран обновлён"
        refresh_after_gone(chat, mid, uid, kind, data, today)
    except (ValueError, IndexError) as e:
        print("callback:", data, e)
        note = "⚠️ Кнопка устарела — открой /start"
    except Exception as e:
        print("callback:", e)
        traceback.print_exc()
        note = "⚠️ Не получилось — попробуй ещё раз"
    answer(cq["id"], note)


def refresh_after_gone(chat, mid, uid, kind, data, today):
    """Нажали на то, чего уже нет: перерисовать экран без этой кнопки (не вышло — не страшно)."""
    try:
        if kind == "t":
            show(chat, mid, *day_view(uid, datetime.date.fromisoformat(data[2])))
        elif kind == "gs":
            show(chat, mid, *goal_view(uid, int(data[2])))
        elif kind in ("g", "gd", "ga"):
            show(chat, mid, *goals_view(uid))
        elif kind in ("m", "r", "u"):
            show(chat, mid, card("↩️", "Задачи больше нет", note="Её уже удалили"), [[home_btn()]])
    except Exception as e:
        print("refresh:", e)


def handle_update(u, sup=None):
    """Одно обновление Telegram. Ошибка не роняет бота: нажатие гаснет, в чат — понятное «⚠️»."""
    try:
        m = u.get("message") or {}
        if sup and KB.has(m.get("text")):  # кнопка меню — выход из ожидания поддержки, а не текст просьбы
            sup.waiting.pop((m.get("chat") or {}).get("id"), None)
        if sup and sup.intercept(u):  # 🛠 /fix и ответы поддержке
            return
        if u.get("callback_query"):
            on_callback(u["callback_query"])
        elif u.get("message"):
            on_message(u["message"])
    except Exception as e:
        print("update:", e)
        traceback.print_exc()
        cq, msg = u.get("callback_query"), u.get("message")
        if cq:
            answer(cq["id"], "⚠️ Не получилось — попробуй ещё раз")
        elif msg and msg.get("chat", {}).get("type") == "private":
            try:
                send(msg["chat"]["id"], "⚠️ Что-то пошло не так — попробуй ещё раз. Не выйдет — /fix")
            except Exception:
                pass


def export(chat, uid):
    try:
        _export(chat, uid)
    except Exception as e:
        print("export:", e)
        send(chat, "⚠️ Не получилось прислать файл — попробуй ещё раз через минуту")


def _export(chat, uid):
    import uuid
    data = db.export_json(uid).encode()
    b = uuid.uuid4().hex
    name = f"planner-{today_of(uid).isoformat()}.json"
    body = (f'--{b}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{chat}\r\n'
            f'--{b}\r\nContent-Disposition: form-data; name="caption"\r\n\r\n💾 Копия задач, целей и заметок\r\n'
            f'--{b}\r\nContent-Disposition: form-data; name="document"; filename="{name}"\r\n'
            "Content-Type: application/json\r\n\r\n").encode() + data + f"\r\n--{b}--\r\n".encode()
    req = urllib.request.Request(API + "sendDocument", data=body, headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    urllib.request.urlopen(req, timeout=60).read()


def update(chat):
    def git(*a):
        try:
            return subprocess.run(["git", *a], cwd=REPO, capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(a, 1, "", "git не ответил за 2 минуты")
    res = git("pull", "--ff-only", "origin", "main")
    ok = res.returncode == 0
    send(chat, card("🔄", "Обновление" if ok else "Не обновилось", esc((res.stdout + res.stderr).strip()[-1500:] or "ok"),
                    "Перезапускаюсь…" if ok else None))
    if ok:
        tg("getUpdates", offset=offset, timeout=0)
        os._exit(0)  # pm2 поднимет заново


# ---------- расписание: напоминания, утро, вечер ----------

def morning(uid, chat, now):
    day = now.date()
    tasks = db.day(uid, day)
    u = db.user(uid)
    late = db.overdue(uid, day)
    if not tasks and not late:
        return False  # пустой день — не пишем лишнего
    hello = "Доброе утро" + (f", {esc(u['name'])}" if u["name"] else "")
    if tasks:
        body = [task_line(t, show_done=False) for t in tasks[:10]]
        if len(tasks) > 10:
            body.append(f"<i>… и ещё {len(tasks) - 10}</i>")
    else:
        body = ["На сегодня планов нет — напиши задачу прямо сюда."]
    goals = db.current_goals(uid, day)
    if goals:
        body += ["", "<b>В фокусе:</b> " + ", ".join(goal_line(uid, g) for g in goals[:3])]
    note = f"{WEEKDAYS[day.weekday()]}, {day.day} {MONTHS_GEN[day.month - 1]}"
    if late:
        note += f" · с прошлых дней висит {len(late)}"
    rows = [[btn("✅ Отметить в чате", f"d|{day.isoformat()}")]]
    app = app_button("🗓 Планер", f"/day/{day.isoformat()}")
    if app:
        rows[0].append(app)
    send(chat, card("☀️", hello + "!", body, note), rows)
    return True


def evening_text(uid, day, tasks, moved=None):
    """Итоги дня по списку задач; moved — сколько уже перенесли на завтра (после кнопки)."""
    done = [t for t in tasks if t["done"]]
    left = [t for t in tasks if not t["done"]]
    pct = round(100 * len(done) / len(tasks))
    mood = "🔥 Идеальный день!" if not left else ("💪 Хороший день" if pct >= 60 else "🌱 Завтра будет лучше")
    body = [f"<code>{bar(pct, 10)}</code> {len(done)} из {len(tasks)}", mood]
    if left:
        body += ["", "<b>Не успел</b>"] + [task_line(t, show_done=False) for t in left[:6]]
        if len(left) > 6:
            body.append(f"<i>… и ещё {len(left) - 6}</i>")
    notes = []
    if moved is not None:
        notes.append(f"Перенёс на завтра: {moved}")
    if day.weekday() == 6:  # воскресенье — ещё и неделя
        mon = day - datetime.timedelta(days=6)
        wt = [t for i in range(7) for t in db.day(uid, mon + datetime.timedelta(days=i))]
        if wt:
            wd = sum(t["done"] for t in wt)
            notes.append(f"Неделя: {wd} из {len(wt)} ({round(100 * wd / len(wt))}%) — спланируй следующую")
    return card("🌙", "Итоги дня", body, " · ".join(notes))


def evening(uid, chat, now):
    day = now.date()
    tasks = db.day(uid, day)
    if not tasks:
        return
    rows = []
    if any(not t["done"] and not t["repeat"] for t in tasks):
        rows.append([btn("➡️ Перенести на завтра", f"c|{day.isoformat()}", "primary")])
    nxt = (day + datetime.timedelta(days=1)).isoformat()
    send(chat, evening_text(uid, day, tasks), with_app(rows, "🗓 План на завтра", f"/day/{nxt}"))


def in_window(now, hhmm, minutes):
    if not hhmm:
        return False
    h, m = map(int, hhmm.split(":"))
    start = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return start <= now < start + datetime.timedelta(minutes=minutes)


def transient(e):
    """Стоит ли повторить отправку: сеть, 429 и 5xx — да; 400/403 (заблокировал бота, плохая разметка) — нет."""
    return not (isinstance(e, TgError) and e.code in (400, 401, 403, 404))


def tick():
    for u in db.users():
        try:
            tick_user(u)
        except Exception as e:  # у одного пользователя сбой (закрыл бота, плохой пояс) — остальные не страдают
            print("tick", u.get("id"), e)
            traceback.print_exc()


def tick_user(u):
    uid = u["id"]
    if not allowed(uid):
        return
    now = user_now(u)
    day = now.date()
    iso = day.isoformat()
    # напоминания (на сегодня и на завтра — если напоминание «за час» переходит через полночь)
    for d in (day, day + datetime.timedelta(days=1)):
        for t in db.day(uid, d):
            if t["done"] or not t["time"] or t["remind"] < 0:
                continue
            h, m = map(int, t["time"].split(":"))
            at = now.replace(year=d.year, month=d.month, day=d.day, hour=h, minute=m, second=0, microsecond=0)
            fire = at - datetime.timedelta(minutes=t["remind"])
            key = f"r:{t['id']}:{d}:{t['time']}:{t['remind']}"
            if fire <= now < fire + datetime.timedelta(minutes=10) and db.mark_sent(key):
                when = "Сейчас" if t["remind"] == 0 else f"Через {t['remind']} мин"
                note = esc(" ".join(t["note"].split())[:200]) if t["note"] else None
                try:
                    send(uid, card("⏰", f"{when} · {t['time']}", esc(t["title"]), note),
                         [[btn("✅ Сделано", f"r|{t['id']}|{d.isoformat()}", "success")]])
                except Exception as e:
                    print("reminder:", e)
                    if transient(e):  # сеть/лимит — следующий проход (через 20 с) попробует снова
                        db.unmark_sent(key)
    for kind, hhmm, minutes, fn in (("m", u["morning"], 180, morning), ("e", u["evening"], 120, evening)):
        key = f"{kind}:{uid}:{iso}"
        if in_window(now, hhmm, minutes) and db.mark_sent(key):
            try:
                fn(uid, uid, now)
            except Exception as e:
                print("digest:", kind, e)
                if transient(e):
                    db.unmark_sent(key)


def scheduler():
    last_backup = None
    while True:
        try:
            tick()
            if last_backup != datetime.date.today():
                db.backup(os.path.join(DATA, "backups"))
                db.prune_sent(60)  # старые отметки «уже отправлено» не копим вечно
                last_backup = datetime.date.today()
        except Exception as e:
            print("scheduler:", e)
            traceback.print_exc()
        time.sleep(20)


# ---------- веб: приложение и API ----------

def asset_version():
    h = hashlib.sha1()
    for f in sorted(os.listdir(WEB)):
        if not os.path.isfile(os.path.join(WEB, f)):
            continue
        with open(os.path.join(WEB, f), "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()[:10]


def view(uid, kind, obj):
    """Цели отдаём вместе с прогрессом, чтобы приложение и бот считали одинаково."""
    if kind == "goal" and obj:
        obj = dict(obj, pct=db.progress(uid, obj))
    return obj


BODY_LIMIT = 1_000_000          # обычные запросы — до 1 МБ
BODY_LIMIT_SUPPORT = 16_000_000  # «🛠 Поддержка» со скриншотом (до 10 МБ картинки в base64)


class Web(BaseHTTPRequestHandler):
    server_version = "Ritm/1"
    timeout = 30  # зависшее соединение не держит поток вечно

    def log_message(self, fmt, *args):  # без шума в логах
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
            if path != "/api/all":  # остальное меняет данные — только POST
                return self.reply(405, {"error": "Только POST"})
            return self.api(path, {})
        if path in ("/", "/index.html"):
            with open(os.path.join(WEB, "index.html"), encoding="utf-8") as f:
                page = f.read().replace("{{v}}", VERSION)
            return self.reply(200, page, "text/html; charset=utf-8")
        name = os.path.basename(path)
        file = os.path.join(WEB, name)
        if name and os.path.isfile(file):
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
            limit = BODY_LIMIT_SUPPORT if path == "/api/support" else BODY_LIMIT
            if n > limit:
                left = min(n, 32_000_000)  # тело дочитываем и выбрасываем — иначе клиент увидит обрыв вместо ответа
                while left > 0:
                    chunk = self.rfile.read(min(left, 65536))
                    if not chunk:
                        break
                    left -= len(chunk)
                self.close_connection = True
                return self.reply(413, {"error": "Слишком большой запрос"})
            body = json.loads(self.rfile.read(n) or b"{}") if n else {}
        except (ValueError, OSError):
            return self.reply(400, {"error": "Не разобрал запрос"})
        if not isinstance(body, dict):
            return self.reply(400, {"error": "Не разобрал запрос"})
        self.api(path, body)

    def api(self, path, body):
        user = check_init_data(self.headers.get("X-Init-Data") or "")
        if not user:
            return self.reply(401, {"error": "Открой планер через Telegram"})
        uid = user.get("id") if isinstance(user, dict) else None
        if not isinstance(uid, int):
            return self.reply(401, {"error": "Открой планер через Telegram"})
        if not allowed(uid):
            return self.reply(403, {"error": "Это личный планер"})
        db.user(uid, user.get("first_name"))
        try:
            self.reply(200, route(uid, path, body))
        except PermissionError as e:
            self.reply(403, {"error": str(e)})
        except (ValueError, KeyError, TypeError) as e:
            self.reply(400, {"error": human_error(e)})
        except Exception:
            traceback.print_exc()  # подробности — в pm2 logs, человеку — понятное
            self.reply(500, {"error": "Сервер споткнулся — попробуй ещё раз через минуту"})


def human_error(e):
    """Текст ошибки запроса для человека: без «'id'», «invalid literal…» и прочего питоновского."""
    msg = str(e).strip("'\"")
    if isinstance(e, TypeError) or re.fullmatch(r"\w+", msg) or re.search(
            r"invalid literal|isoformat|unpack|NoneType|not supported|object", msg):
        return "Неверные данные запроса — обнови приложение и попробуй ещё раз"
    return msg[:200] or "Неверные данные запроса"


def route(uid, path, b):
    if path == "/api/all":
        data = db.everything(uid)
        data["goals"] = [view(uid, "goal", g) for g in data["goals"]]
        data["today"] = today_of(uid).isoformat()
        data["mode"] = ui_mode()
        return data
    if path == "/api/mode":  # «💬 Перейти в чат» из приложения (или обратно): в чат сразу приходит главное меню
        m = str(b.get("mode") or "")
        if not can_set_mode(uid):  # режим общий на весь бот — менять его может только владелец
            raise PermissionError("Режим меняет владелец")
        set_mode(m)
        if m == "chat":
            welcome(uid, db.user(uid).get("name"), today_of(uid))
        return {"mode": m}
    if path == "/api/task":
        t = db.save_task(uid, b, today_of(uid))
        return {"task": t, "goals": [view(uid, "goal", g) for g in db.goals(uid)]}
    if path == "/api/task/delete":
        db.delete_task(uid, int(b["id"]), b.get("date"))
        return {"ok": True, "task": db.task(uid, int(b["id"])), "goals": [view(uid, "goal", g) for g in db.goals(uid)]}
    if path == "/api/task/toggle":
        done = db.toggle(uid, int(b["id"]), b.get("date"), b.get("done"))
        return {"done": done, "task": db.task(uid, int(b["id"])), "goals": [view(uid, "goal", g) for g in db.goals(uid)]}
    if path == "/api/goal":
        return {"goal": view(uid, "goal", db.save_goal(uid, b, today_of(uid)))}
    if path == "/api/goal/delete":
        return {"ok": db.delete_goal(uid, int(b["id"]))}
    if path == "/api/note":
        return {"note": db.save_note(uid, b)}
    if path == "/api/note/delete":
        return {"ok": db.delete_note(uid, int(b["id"]))}
    if path == "/api/settings":
        return {"user": db.update_settings(uid, b)}
    if path == "/api/support":  # 🛠 из приложения — та же поддержка, что /fix в чате
        if not SUP:
            raise ValueError("Поддержка ещё запускается — попробуй через минуту")
        return SUP.web(uid, b)
    raise KeyError("нет такого метода")


def serve():
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Web)
    httpd.daemon_threads = True
    print(f"Веб-сервер: http://127.0.0.1:{PORT}")
    httpd.serve_forever()


# ---------- https-адрес для приложения ----------
# Telegram открывает мини-приложение только по https. Основной путь — Caddy на этом же сервере
# (порты 80 и 443): сертификат Let's Encrypt на имя вида 1-2-3-4.sslip.io и на сам IP сервера.
# Если за 5 минут не вышло — запасной путь, туннель cloudflared (он работает не везде).
CADDY_DIR = os.path.join(DATA, "caddy")
https_note = "запускается"  # что с адресом приложения — для /status


def healthy(url=None):
    url = url or public_url
    if not url:
        return False
    try:
        with urllib.request.urlopen(url + "/health", timeout=20) as r:
            return r.read() == b"ok"
    except Exception:
        return False


def use_url(url):
    """Ставит адрес приложения на кнопку «Планер»; при самом первом адресе пишет владельцу, что всё готово."""
    global public_url
    old, public_url = public_url, url
    if not set_menu():
        public_url = old
        return False
    first = not db.meta("app_url")  # раньше адреса не было — значит, планер только что поставили
    if db.meta("app_url") != url:
        db.meta("app_url", url)
    # «готово» — один раз за всё время: адрес может меняться (sslip/IP/туннель), кнопка обновляется молча
    if owner() and first and db.mark_sent("app_ready"):
        try:
            send(owner(), card("✨", "Планер готов", "Кнопка «Планер» теперь слева от поля ввода."),
                 with_app([], "🗓 Открыть планер", style="primary"))
        except Exception as e:
            print("notify:", e)
    return True


def server_ip():
    if os.environ.get("PLANNER_IP"):
        return os.environ["PLANNER_IP"]
    for url in ("https://api.ipify.org", "https://ipv4.icanhazip.com", "https://ifconfig.me/ip"):
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                ip = r.read().decode().strip()
            if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", ip):
                return ip
        except Exception:
            pass
    return None


def caddy_bin():
    """Caddy из системы или скачанный в planner/data/caddy (один файл, официальная сборка)."""
    found = shutil.which("caddy")
    if found:
        return found
    path = os.path.join(CADDY_DIR, "caddy")
    if not os.path.exists(path):
        arch = {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine(), "amd64")
        os.makedirs(CADDY_DIR, exist_ok=True)
        print("Скачиваю Caddy…")
        urllib.request.urlretrieve(f"https://caddyserver.com/api/download?os=linux&arch={arch}", path + ".part")
        os.chmod(path + ".part", 0o755)
        os.replace(path + ".part", path)
    return path


def caddyfile(ip):
    """Настройки Caddy и адреса, по которым приложение будет доступно (сначала имя, потом IP)."""
    host = ip.replace(".", "-") + ".sslip.io"
    conf = (
        "{\n"
        f"\tstorage file_system {CADDY_DIR}/data\n"
        "\tadmin off\n"
        "}\n"
        f"https://{host} {{\n"
        "\tencode zstd gzip\n"
        f"\treverse_proxy 127.0.0.1:{PORT}\n"
        "}\n"
        f"https://{ip} {{\n"
        "\ttls {\n"
        "\t\tissuer acme https://acme-v02.api.letsencrypt.org/directory {\n"
        "\t\t\tprofile shortlived\n"
        "\t\t}\n"
        "\t}\n"
        "\tencode zstd gzip\n"
        f"\treverse_proxy 127.0.0.1:{PORT}\n"
        "}\n"
        # бот-тренер «Форма» (coach/) на этом же сервере: порт 443 один, поэтому его адрес держит этот Caddy
        f"https://coach.{host} {{\n"
        f"\treverse_proxy 127.0.0.1:{COACH_PORT}\n"
        "}\n"
        # «Главный» (hub/): вход для Claude
        f"https://hub.{host} {{\n"
        f"\treverse_proxy 127.0.0.1:{HUB_PORT}\n"
        "}\n"
        # «Глаз» (eye/): страница разговора через очки
        f"https://eye.{host} {{\n"
        f"\treverse_proxy 127.0.0.1:{EYE_PORT}\n"
        "}\n"
        # мини-приложения других ботов: КПП (kpp/), Авито (avito/), Секретарь (tgbot/)
        f"https://kpp.{host} {{\n"
        f"\treverse_proxy 127.0.0.1:{KPP_PORT}\n"
        "}\n"
        f"https://avito.{host} {{\n"
        f"\treverse_proxy 127.0.0.1:{AVITO_PORT}\n"
        "}\n"
        f"https://agent.{host} {{\n"
        f"\treverse_proxy 127.0.0.1:{AGENT_PORT}\n"
        "}\n"
        # «Студия» (studio/): её приложение и мини-приложения ботов, которых она сделала (/b/<slug>/)
        f"https://apps.{host} {{\n"
        f"\treverse_proxy 127.0.0.1:{STUDIO_PORT}\n"
        "}\n"
        # «Студия сайтов» (studio/, STUDIO_MODE=sites): приложение и сайты сестры Глеба (/s/<slug>/)
        f"https://web.{host} {{\n"
        f"\treverse_proxy 127.0.0.1:{SITES_PORT}\n"
        "}\n"
        # «Ловец» (catcher/): юзернеймы на каналы
        f"https://catcher.{host} {{\n"
        f"\treverse_proxy 127.0.0.1:{CATCHER_PORT}\n"
        "}\n"
        # «Пульс» (pulse/): все чаты Claude Code
        f"https://pulse.{host} {{\n"
        f"\treverse_proxy 127.0.0.1:{PULSE_PORT}\n"
        "}\n"
        # HDP-бот (bot/): сайт в браузере — «как платформа» с кнопками «Сделать» и «Подготовь меня»
        f"https://hdp.{host} {{\n"
        f"\treverse_proxy 127.0.0.1:{HDP_PORT}\n"
        "}\n"
    )
    return conf, [f"https://{host}", f"https://{ip}"]


def stop_other_caddy():
    """Лишний Caddy на 443 (оставшийся от прошлого запуска или свой у тренера/глаза) забирает половину соединений,
    а сертификатов других ботов у него нет — страницы открываются через раз. https на сервере раздаёт только этот."""
    try:
        subprocess.run(["pkill", "-f", "caddy run --config"], timeout=10)
        time.sleep(1)
    except (OSError, subprocess.TimeoutExpired):
        pass


def start_caddy(binary, conf_path):
    stop_other_caddy()
    proc = subprocess.Popen([binary, "run", "--config", conf_path, "--adapter", "caddyfile"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

    def log():  # в pm2 logs — только ошибки и полученные сертификаты
        for line in proc.stderr:
            if '"level":"error"' in line or "certificate obtained" in line:
                print("caddy:", line.strip()[:400])
    threading.Thread(target=log, daemon=True).start()
    return proc


def setup_caddy():
    """Узнаёт IP, пишет Caddyfile, запускает Caddy -> (процесс, адреса, путь к бинарю, путь к конфигу)."""
    global https_note
    ip = server_ip()
    if not ip:
        raise RuntimeError("не узнал IP сервера")
    conf, cands = caddyfile(ip)
    os.makedirs(CADDY_DIR, exist_ok=True)
    conf_path = os.path.join(CADDY_DIR, "Caddyfile")
    tmp = conf_path + ".new"
    with open(tmp, "w") as f:
        f.write(conf)
    os.replace(tmp, conf_path)  # конфиг пишем целиком или не пишем — Caddy не прочитает половину файла
    binary = caddy_bin()
    proc = start_caddy(binary, conf_path)
    https_note = "Caddy получает сертификат"
    return proc, cands, binary, conf_path


def https():
    global https_note
    proc = cands = binary = conf_path = None
    try:
        proc, cands, binary, conf_path = setup_caddy()
    except Exception as e:
        https_note = f"Caddy не запустился: {e}"
        print(https_note)
    fallback = False
    started = time.time()
    last_try = time.time()
    misses = 0  # адрес меняем только после трёх неудачных проверок подряд, а не от одного сбоя
    while True:
        if not proc and time.time() - last_try > 60:  # IP не узнали / Caddy не скачался — пробуем снова, а не навсегда сдаёмся
            last_try = time.time()
            try:
                proc, cands, binary, conf_path = setup_caddy()
            except Exception as e:
                https_note = f"Caddy не запустился: {e}"
                print(https_note)
        if proc and proc.poll() is not None:  # Caddy упал (например, порт 443 занят) — пробуем снова
            print("Caddy остановился, перезапускаю")
            https_note = "Caddy перезапускается (занят порт 80/443?)"
            time.sleep(10)
            proc = start_caddy(binary, conf_path)
        ok_now = public_url in (cands or []) and healthy()
        misses = 0 if ok_now else misses + 1
        if cands and not ok_now and (public_url not in cands or misses >= 3):
            for c in cands:
                if healthy(c) and use_url(c):
                    https_note = "работает через Caddy"
                    print("Приложение:", c)
                    break
        if not fallback and public_url not in (cands or []) and time.time() - started > 300:
            fallback = True  # своего https пока нет — запасной туннель
            threading.Thread(target=tunnel, daemon=True).start()
        time.sleep(15 if public_url not in (cands or []) else 120)


def tunnel():
    """Запасной путь: cloudflared даёт адрес https://….trycloudflare.com (меняется при перезапуске).
    Адрес Caddy важнее: туннель занимает кнопку, только пока своего https нет."""
    global https_note
    while True:
        try:
            proc = subprocess.Popen(["cloudflared", "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{PORT}"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        except FileNotFoundError:
            print("cloudflared не установлен — запасного туннеля нет")
            return
        found = {}
        ready = threading.Event()

        def read():
            for line in proc.stderr:
                m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
                if m and not ready.is_set():
                    found["url"] = m.group(0)
                    ready.set()

        threading.Thread(target=read, daemon=True).start()
        if ready.wait(60):
            url = found["url"]
            for _ in range(30):  # адрес начинает открываться не сразу
                if healthy(url):
                    break
                time.sleep(3)
            fails = 0
            while proc.poll() is None:
                ok = healthy(url)
                if ok and (not public_url or "trycloudflare" in public_url) and public_url != url and use_url(url):
                    https_note = "работает через туннель cloudflared"
                if public_url and "trycloudflare" not in public_url:
                    proc.kill()  # появился свой https — туннель больше не нужен
                    return
                fails = 0 if ok else fails + 1
                if fails >= 3:
                    print("Туннель не отвечает — перезапускаю")
                    proc.kill()
                time.sleep(60)
        else:
            print("cloudflared не выдал адрес за минуту")
            proc.kill()
        proc.wait()
        time.sleep(30)


VERSION = asset_version() if os.path.isdir(WEB) else "0"


def main():
    global offset
    if not TOKEN:
        sys.exit("Нет PLANNER_TOKEN (токен от @BotFather). Впиши его в /etc/planner-bot.env")
    threading.Thread(target=serve, daemon=True).start()
    setup_bot()
    if public_url:
        set_menu()
    else:
        threading.Thread(target=https, daemon=True).start()
    threading.Thread(target=scheduler, daemon=True).start()
    print(f"{NAME} запущен")
    global SUP
    SUP = sup = support.Support(TOKEN, "Планер «Ритм»", folders=["planner"], pm2="planner-bot",  # только владелец
                                allowed=can_support, tests=["python3 planner/test_planner.py"])
    kb_on_start()
    while True:
        try:
            upd = tg("getUpdates", offset=offset, timeout=50, allowed_updates=["message", "callback_query"])
        except Exception as e:
            print("getUpdates:", e)
            time.sleep(5)
            continue
        for u in upd.get("result", []):
            offset = u["update_id"] + 1
            handle_update(u, sup)


if __name__ == "__main__":
    main()
