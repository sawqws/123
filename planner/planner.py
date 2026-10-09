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
import re
import subprocess
import sys
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

TOKEN = os.environ.get("PLANNER_TOKEN", "")
API = f"https://api.telegram.org/bot{TOKEN}/"
ALLOWED = {int(x) for x in re.findall(r"\d+", os.environ.get("PLANNER_ALLOWED", ""))}
PUBLIC = os.environ.get("PLANNER_PUBLIC") == "1"
PORT = int(os.environ.get("PLANNER_PORT") or 8787)
DATA = os.path.join(HERE, "data")
WEB = os.path.join(HERE, "webapp")
REPO = os.path.dirname(HERE)
NAME = "Ритм"

db = store.Store(os.environ.get("PLANNER_DB") or os.path.join(DATA, "planner.db"))
public_url = os.environ.get("PLANNER_URL", "").rstrip("/")
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
        raise


def send(chat, text, buttons=None, **extra):
    if buttons is not None:
        extra["reply_markup"] = {"inline_keyboard": buttons}
    return tg("sendMessage", chat_id=chat, text=text, parse_mode="HTML",
              link_preview_options={"is_disabled": True}, **extra)


def edit(chat, msg_id, text, buttons=None):
    extra = {"reply_markup": {"inline_keyboard": buttons}} if buttons is not None else {}
    return tg("editMessageText", chat_id=chat, message_id=msg_id, text=text, parse_mode="HTML",
              link_preview_options={"is_disabled": True}, **extra)


def app_button(text="🗓 Открыть планер", path=""):
    if not public_url:
        return None
    # экран передаём в ?p=, а не в #: в # Telegram дописывает свои параметры запуска
    return {"text": text, "web_app": {"url": public_url + "/" + (f"?p={urllib.parse.quote(path)}" if path else "")}}


def with_app(rows, text="🗓 Открыть планер", path=""):
    b = app_button(text, path)
    return rows + [[b]] if b else rows


def setup_bot():
    """Команды, описание и кнопка меню. Описание меняем, только если поменялось (у Telegram лимиты)."""
    commands = [
        {"command": "today", "description": "План на сегодня"},
        {"command": "tomorrow", "description": "План на завтра"},
        {"command": "week", "description": "Неделя"},
        {"command": "goals", "description": "Мои цели"},
        {"command": "app", "description": "Открыть планер"},
        {"command": "help", "description": "Как пользоваться"},
    ]
    desc = ("Планер дня, недели и месяца. Цели с прогрессом и заметками, напоминания, "
            "утренний план и итоги дня.\n\nНапиши «завтра в 18:00 тренировка» — и задача уже в плане.")
    short = "Планер дня, недели и месяца: цели, заметки, напоминания."
    key = hashlib.sha1(json.dumps([commands, desc, short, NAME]).encode()).hexdigest()
    if db.meta("bot_setup") != key:
        try:
            tg("setMyCommands", commands=commands)
            tg("setMyDescription", description=desc)
            tg("setMyShortDescription", short_description=short)
            db.meta("bot_setup", key)
        except Exception as e:
            print("setup:", e)
    set_menu()


def set_menu():
    if not public_url:
        return
    try:
        tg("setChatMenuButton", menu_button={"type": "web_app", "text": "Планер", "web_app": {"url": public_url + "/"}})
        print("Приложение:", public_url)
    except Exception as e:
        print("menu:", e)


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

def task_line(t, show_done=True):
    mark = ("✅" if t["done"] else "◻️") if show_done else "•"
    time_ = f"<b>{t['time']}</b>  " if t.get("time") else ""
    imp = "  ❗" if t["important"] else ""
    rep = "  🔁" if t["repeat"] else ""
    title = f"<s>{esc(t['title'])}</s>" if t["done"] and show_done else esc(t["title"])
    return f"{mark} {time_}{title}{imp}{rep}"


def goal_line(uid, g):
    p = db.progress(uid, g)
    return f"{g['emoji']} {esc(g['title'])}\n      <code>{bar(p, 10)}</code> {p}%"


def day_view(uid, day):
    """Текст и кнопки «плана на день» с галочками."""
    today = today_of(uid)
    tasks = db.day(uid, day)
    done = sum(t["done"] for t in tasks)
    lines = [f"📅 <b>{human_date(day, today)}</b>"]
    if tasks:
        pct = round(100 * done / len(tasks))
        lines.append(f"<code>{bar(pct, 12)}</code>  {done} из {len(tasks)}")
        if done == len(tasks):
            lines.append("\n🎉 <b>Всё сделано!</b> Красота.")
        else:
            lines.append("\n<i>Нажми на задачу, чтобы отметить.</i>")
    else:
        lines.append("\nНа этот день пока пусто.\nНапиши, например: <i>«завтра в 18:00 тренировка»</i>")
    if day == today:
        late = db.overdue(uid, today)
        if late:
            lines.append(f"\n⏳ Просрочено: {len(late)} — в приложении можно перенести на сегодня.")
    goals = db.current_goals(uid, day)
    if goals:
        lines.append("\n🎯 <b>Цели</b>")
        lines += [goal_line(uid, g) for g in goals[:5]]
    rows = []
    for t in tasks[:40]:
        label = ("✅ " if t["done"] else "◻️ ") + (f"{t['time']} · " if t["time"] else "") + t["title"]
        rows.append([{"text": label[:60], "callback_data": f"t|{t['id']}|{day.isoformat()}"}])
    prev, nxt = day - datetime.timedelta(days=1), day + datetime.timedelta(days=1)
    rows.append([{"text": "‹", "callback_data": f"d|{prev.isoformat()}"},
                 {"text": "Сегодня" if day != today else "· Сегодня ·", "callback_data": f"d|{today.isoformat()}"},
                 {"text": "›", "callback_data": f"d|{nxt.isoformat()}"}])
    return "\n".join(lines), with_app(rows, path=f"/day/{day.isoformat()}")


def week_text(uid, day):
    today = today_of(uid)
    mon = day - datetime.timedelta(days=day.weekday())
    sun = mon + datetime.timedelta(days=6)
    span = (f"{mon.day}–{sun.day} {MONTHS_GEN[sun.month - 1]}" if mon.month == sun.month
            else f"{mon.day} {MONTHS_GEN[mon.month - 1]} – {sun.day} {MONTHS_GEN[sun.month - 1]}")
    lines = [f"🗓 <b>Неделя · {span}</b>"]
    total = done = 0
    for i in range(7):
        d = mon + datetime.timedelta(days=i)
        tasks = db.day(uid, d)
        total += len(tasks)
        done += sum(t["done"] for t in tasks)
        mark = "  ← сегодня" if d == today else ""
        head = f"\n<b>{WEEKDAYS[i]}, {d.day}</b>{mark}"
        if tasks:
            lines.append(head + f"  <i>{sum(t['done'] for t in tasks)}/{len(tasks)}</i>")
            lines += ["  " + task_line(t) for t in tasks[:8]]
            if len(tasks) > 8:
                lines.append(f"  … и ещё {len(tasks) - 8}")
        else:
            lines.append(head + "  <i>свободно</i>")
    if total:
        pct = round(100 * done / total)
        lines.insert(1, f"<code>{bar(pct, 12)}</code>  {done} из {total}")
    goals = db.current_goals(uid, day)
    goals = [g for g in goals if g["horizon"] == "week"]
    if goals:
        lines.append("\n🎯 <b>Цели недели</b>")
        lines += [goal_line(uid, g) for g in goals]
    return "\n".join(lines)


def goals_text(uid):
    goals = [g for g in db.goals(uid) if g["status"] == "active"]
    if not goals:
        return ("🎯 <b>Цели</b>\n\nЦелей пока нет. Открой планер → «Цели» → «+» — "
                "поставь цель на неделю, месяц или год, разбей на шаги и веди заметки.")
    lines = ["🎯 <b>Мои цели</b>"]
    notes = {}
    for n in db.notes(uid):
        notes[n["goal"]] = notes.get(n["goal"], 0) + 1
    for hz in ("week", "month", "year", "none"):
        part = [g for g in goals if g["horizon"] == hz]
        if not part:
            continue
        lines.append(f"\n<b>{HORIZON[hz]}</b>")
        for g in part:
            extra = f"  · 📝 {notes[g['id']]}" if notes.get(g["id"]) else ""
            lines.append(goal_line(uid, g) + extra)
    return "\n".join(lines)


HELP = (
    "✨ <b>Как пользоваться</b>\n\n"
    "<b>Планер</b> — кнопка «Планер» слева от поля ввода. Там день, неделя, месяц и цели.\n\n"
    "<b>Быстро добавить</b> — просто напиши задачу:\n"
    "• <i>завтра в 18:00 тренировка</i>\n"
    "• <i>в пятницу кино</i>\n"
    "• <i>12.10 контрольная!</i>  (! — важное)\n"
    "• <i>по пн, ср и пт английский в 7 вечера</i>\n"
    "• <i>каждый день зарядка в 7:30</i>\n\n"
    "<b>Команды</b>\n"
    "/today — план на сегодня с галочками\n"
    "/tomorrow — на завтра\n"
    "/week — неделя целиком\n"
    "/goals — цели и прогресс\n\n"
    "Утром пришлю план на день, вечером — итоги. Время меняется в планере → ⚙️."
)


def welcome(chat, name):
    text = (f"👋 <b>Привет{', ' + esc(name) if name else ''}! Я {NAME}</b> — твой планер.\n\n"
            "🗓 <b>День, неделя, месяц</b> — всё в одном месте\n"
            "🎯 <b>Цели</b> — с шагами, прогрессом и заметками\n"
            "⏰ <b>Напоминания</b>, утренний план и итоги дня\n\n"
            "Чтобы добавить задачу, просто напиши её, например:\n"
            "<i>завтра в 18:00 тренировка</i>\n\n"
            "А всё самое красивое — в планере 👇")
    send(chat, text, with_app([], "✨ Открыть планер"))


def quick_add(chat, uid, text):
    today = today_of(uid)
    p = parse.parse(text, today)
    if not p["title"]:
        send(chat, "🤔 Не понял, что добавить. Напиши, например: <i>завтра в 18:00 тренировка</i>")
        return
    t = db.save_task(uid, {"title": p["title"], "date": p["date"].isoformat(), "time": p["time"],
                           "repeat": p["repeat"], "important": p["important"],
                           "remind": db.user(uid)["remind"] if p["time"] else -1})
    send(chat, added_text(t, today), added_buttons(t, today))


def added_text(t, today):
    when = human_date(datetime.date.fromisoformat(t["date"]), today) if t["date"] else "Без даты"
    if t["repeat"]:
        when = repeat_text(t["repeat"]).capitalize()
    parts = [f"✅ <b>{esc(t['title'])}</b>", f"📅 {when}" + (f" · {t['time']}" if t["time"] else "")]
    if t["important"]:
        parts.append("❗ Важное")
    if t["time"] and t["remind"] >= 0:
        parts.append("⏰ Напомню " + ("в это время" if t["remind"] == 0 else f"за {t['remind']} мин"))
    return "\n".join(parts)


def added_buttons(t, today):
    rows = []
    if not t["repeat"]:
        opts = [("Сегодня", today), ("Завтра", today + datetime.timedelta(days=1))]
        rows.append([{"text": label, "callback_data": f"m|{t['id']}|{d.isoformat()}"}
                     for label, d in opts if t["date"] != d.isoformat()]
                    + [{"text": "Без даты", "callback_data": f"m|{t['id']}|-"}] * bool(t["date"]))
    rows.append([{"text": "↩️ Отменить", "callback_data": f"u|{t['id']}"}])
    return with_app([r for r in rows if r], "Открыть в планере", f"/day/{t['date']}" if t["date"] else "/week")


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
        send(chat, "🔒 Это личный планер. Попроси владельца добавить тебя.")
        return
    db.user(uid, frm.get("first_name"))
    cmd = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
    today = today_of(uid)
    if cmd == "/start":
        welcome(chat, frm.get("first_name"))
    elif cmd == "/help":
        send(chat, HELP, with_app([]))
    elif cmd == "/today":
        send(chat, *day_view(uid, today))
    elif cmd == "/tomorrow":
        send(chat, *day_view(uid, today + datetime.timedelta(days=1)))
    elif cmd == "/week":
        send(chat, week_text(uid, today), with_app([], path="/week"))
    elif cmd == "/goals":
        send(chat, goals_text(uid), with_app([], "🎯 Открыть цели", "/goals"))
    elif cmd == "/app":
        if public_url:
            send(chat, "🗓 Твой планер:", with_app([]))
        else:
            send(chat, "Приложение ещё запускается, попробуй через минуту.")
    elif cmd == "/export":
        export(chat, uid)
    elif cmd == "/update" and uid == (owner() or min(ALLOWED or {0})):
        update(chat)
    elif cmd:
        send(chat, "Такой команды нет. /help — что я умею.")
    elif text:
        quick_add(chat, uid, text)


def on_callback(cq):
    uid = cq["from"]["id"]
    msg = cq.get("message") or {}
    chat, mid = msg.get("chat", {}).get("id"), msg.get("message_id")
    data = (cq.get("data") or "").split("|")
    note = ""
    if not allowed(uid) or not chat:
        tg("answerCallbackQuery", callback_query_id=cq["id"])
        return
    today = today_of(uid)
    kind = data[0]
    try:
        if kind == "t":  # галочка в плане дня
            new = db.toggle(uid, int(data[1]), data[2])
            note = "Сделано ✨" if new else "Снял отметку"
            edit(chat, mid, *day_view(uid, datetime.date.fromisoformat(data[2])))
        elif kind == "d":  # листаем дни
            edit(chat, mid, *day_view(uid, datetime.date.fromisoformat(data[1])))
        elif kind == "u":  # отменить быстрое добавление
            t = db.task(uid, int(data[1]))
            db.delete_task(uid, int(data[1]))
            edit(chat, mid, f"↩️ Отменил: <s>{esc(t['title'])}</s>" if t else "↩️ Отменил")
        elif kind == "m":  # перенести
            t = db.save_task(uid, {"id": int(data[1]), "date": None if data[2] == "-" else data[2]})
            edit(chat, mid, added_text(t, today), added_buttons(t, today))
            note = "Перенёс"
        elif kind == "r":  # «сделано» из напоминания
            t = db.task(uid, int(data[1]))
            db.toggle(uid, int(data[1]), data[2], done=True)
            edit(chat, mid, f"✅ <s>{esc(t['title'])}</s>\nОтлично!" if t else "✅")
        elif kind == "c":  # перенести несделанное на завтра (из вечерних итогов)
            day = datetime.date.fromisoformat(data[1])
            nxt = (day + datetime.timedelta(days=1)).isoformat()
            moved = 0
            for t in db.day(uid, day):
                if not t["done"] and not t["repeat"]:
                    db.move(uid, t["id"], nxt)
                    moved += 1
            note = f"Перенёс {moved} на завтра" if moved else "Нечего переносить"
            edit(chat, mid, (msg.get("text") and esc(msg["text"]) or "") + f"\n\n➡️ <b>Перенёс на завтра: {moved}</b>",
                 with_app([], path=f"/day/{nxt}"))
    except Exception as e:
        print("callback:", e)
        traceback.print_exc()
        note = "Не получилось 😕"
    tg("answerCallbackQuery", callback_query_id=cq["id"], text=note)


def export(chat, uid):
    import uuid
    data = db.export_json(uid).encode()
    b = uuid.uuid4().hex
    name = f"planner-{today_of(uid).isoformat()}.json"
    body = (f'--{b}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{chat}\r\n'
            f'--{b}\r\nContent-Disposition: form-data; name="caption"\r\n\r\n💾 Копия всех твоих задач, целей и заметок\r\n'
            f'--{b}\r\nContent-Disposition: form-data; name="document"; filename="{name}"\r\n'
            "Content-Type: application/json\r\n\r\n").encode() + data + f"\r\n--{b}--\r\n".encode()
    req = urllib.request.Request(API + "sendDocument", data=body, headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    urllib.request.urlopen(req, timeout=60).read()


def update(chat):
    def git(*a):
        return subprocess.run(["git", *a], cwd=REPO, capture_output=True, text=True)
    res = git("pull", "--ff-only", "origin", "main")
    send(chat, esc((res.stdout + res.stderr).strip()[-1500:] or "ok"))
    if res.returncode == 0:
        send(chat, "Перезапускаюсь…")
        tg("getUpdates", offset=offset, timeout=0)
        os._exit(0)  # pm2 поднимет заново


# ---------- расписание: напоминания, утро, вечер ----------

def morning(uid, chat, now):
    day = now.date()
    tasks = db.day(uid, day)
    u = db.user(uid)
    hello = "Доброе утро" + (f", {esc(u['name'])}" if u["name"] else "")
    lines = [f"☀️ <b>{hello}!</b>", f"{human_date(day, day)} · {WEEKDAYS[day.weekday()].lower()}"]
    if tasks:
        lines.append(f"\n📋 <b>План на день</b> — {len(tasks)} {plural(len(tasks), 'задача', 'задачи', 'задач')}")
        lines += [task_line(t, show_done=False) for t in tasks[:15]]
    else:
        lines.append("\nНа сегодня планов нет — можно добавить прямо сюда сообщением.")
    late = db.overdue(uid, day)
    if late:
        lines.append(f"\n⏳ Висит с прошлых дней: {len(late)}")
    goals = db.current_goals(uid, day)
    if goals:
        lines.append("\n🎯 <b>Держим в фокусе</b>")
        lines += [goal_line(uid, g) for g in goals[:5]]
    send(chat, "\n".join(lines), with_app([[{"text": "✅ Отмечать в чате", "callback_data": f"d|{day.isoformat()}"}]],
                                          path=f"/day/{day.isoformat()}"))


def evening(uid, chat, now):
    day = now.date()
    tasks = db.day(uid, day)
    if not tasks:
        return
    done = [t for t in tasks if t["done"]]
    left = [t for t in tasks if not t["done"]]
    pct = round(100 * len(done) / len(tasks))
    mood = "🔥 Идеальный день!" if not left else ("💪 Хороший день." if pct >= 60 else "🌱 Завтра будет лучше.")
    lines = ["🌙 <b>Итоги дня</b>", f"<code>{bar(pct, 12)}</code>  {len(done)} из {len(tasks)}", "", mood]
    if left:
        lines.append("\n<b>Не успел:</b>")
        lines += [task_line(t, show_done=False) for t in left[:10]]
    if day.weekday() == 6:  # воскресенье — ещё и неделя
        mon = day - datetime.timedelta(days=6)
        wt = [t for i in range(7) for t in db.day(uid, mon + datetime.timedelta(days=i))]
        if wt:
            wd = sum(t["done"] for t in wt)
            lines.append(f"\n📊 <b>Неделя:</b> {wd} из {len(wt)} ({round(100 * wd / len(wt))}%). Спланируй следующую 👇")
    rows = []
    if any(not t["repeat"] for t in left):
        rows.append([{"text": "➡️ Перенести несделанное на завтра", "callback_data": f"c|{day.isoformat()}"}])
    send(chat, "\n".join(lines), with_app(rows, path=f"/day/{(day + datetime.timedelta(days=1)).isoformat()}"))


def in_window(now, hhmm, minutes):
    if not hhmm:
        return False
    h, m = map(int, hhmm.split(":"))
    start = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return start <= now < start + datetime.timedelta(minutes=minutes)


def tick():
    for u in db.users():
        uid = u["id"]
        if not allowed(uid):
            continue
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
                if fire <= now < fire + datetime.timedelta(minutes=10) and db.mark_sent(f"r:{t['id']}:{d}:{t['time']}:{t['remind']}"):
                    when = "Сейчас" if t["remind"] == 0 else f"Через {t['remind']} мин"
                    note = f"\n\n<i>{esc(t['note'][:300])}</i>" if t["note"] else ""
                    send(uid, f"⏰ <b>{when}</b> · {t['time']}\n{esc(t['title'])}{note}",
                         [[{"text": "✅ Сделано", "callback_data": f"r|{t['id']}|{d.isoformat()}"}]])
        if in_window(now, u["morning"], 180) and db.mark_sent(f"m:{uid}:{iso}"):
            morning(uid, uid, now)
        if in_window(now, u["evening"], 120) and db.mark_sent(f"e:{uid}:{iso}"):
            evening(uid, uid, now)


def scheduler():
    last_backup = None
    while True:
        try:
            tick()
            if last_backup != datetime.date.today():
                db.backup(os.path.join(DATA, "backups"))
                last_backup = datetime.date.today()
        except Exception as e:
            print("scheduler:", e)
            traceback.print_exc()
        time.sleep(20)


# ---------- веб: приложение и API ----------

def asset_version():
    h = hashlib.sha1()
    for f in sorted(os.listdir(WEB)):
        with open(os.path.join(WEB, f), "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()[:10]


def view(uid, kind, obj):
    """Цели отдаём вместе с прогрессом, чтобы приложение и бот считали одинаково."""
    if kind == "goal" and obj:
        obj = dict(obj, pct=db.progress(uid, obj))
    return obj


class Web(BaseHTTPRequestHandler):
    server_version = "Ritm/1"

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
            body = json.loads(self.rfile.read(min(n, 1_000_000)) or b"{}") if n else {}
        except ValueError:
            return self.reply(400, {"error": "bad json"})
        self.api(path, body)

    def api(self, path, body):
        user = check_init_data(self.headers.get("X-Init-Data") or "")
        if not user:
            return self.reply(401, {"error": "Открой планер через Telegram"})
        uid = user["id"]
        if not allowed(uid):
            return self.reply(403, {"error": "Это личный планер"})
        db.user(uid, user.get("first_name"))
        try:
            self.reply(200, route(uid, path, body))
        except (ValueError, KeyError) as e:
            self.reply(400, {"error": str(e).strip("'")})
        except Exception as e:
            traceback.print_exc()
            self.reply(500, {"error": str(e)})


def route(uid, path, b):
    if path == "/api/all":
        data = db.everything(uid)
        data["goals"] = [view(uid, "goal", g) for g in data["goals"]]
        data["today"] = today_of(uid).isoformat()
        return data
    if path == "/api/task":
        t = db.save_task(uid, b)
        return {"task": t, "goals": [view(uid, "goal", g) for g in db.goals(uid)]}
    if path == "/api/task/delete":
        db.delete_task(uid, int(b["id"]), b.get("date"))
        return {"ok": True, "task": db.task(uid, int(b["id"])), "goals": [view(uid, "goal", g) for g in db.goals(uid)]}
    if path == "/api/task/toggle":
        done = db.toggle(uid, int(b["id"]), b.get("date"), b.get("done"))
        return {"done": done, "task": db.task(uid, int(b["id"])), "goals": [view(uid, "goal", g) for g in db.goals(uid)]}
    if path == "/api/goal":
        return {"goal": view(uid, "goal", db.save_goal(uid, b))}
    if path == "/api/goal/delete":
        return {"ok": db.delete_goal(uid, int(b["id"]))}
    if path == "/api/note":
        return {"note": db.save_note(uid, b)}
    if path == "/api/note/delete":
        return {"ok": db.delete_note(uid, int(b["id"]))}
    if path == "/api/settings":
        return {"user": db.update_settings(uid, b)}
    raise KeyError("нет такого метода")


def serve():
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Web)
    httpd.daemon_threads = True
    print(f"Веб-сервер: http://127.0.0.1:{PORT}")
    httpd.serve_forever()


# ---------- туннель: https-адрес без домена ----------

def healthy():
    try:
        with urllib.request.urlopen(public_url + "/health", timeout=20) as r:
            return r.read() == b"ok"
    except Exception:
        return False


def tunnel():
    """cloudflared даёт адрес https://….trycloudflare.com. Он меняется при перезапуске —
    тогда бот обновляет кнопку «Планер». Если адрес перестал открываться, туннель перезапускается."""
    global public_url
    while True:
        try:
            proc = subprocess.Popen(["cloudflared", "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{PORT}"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        except FileNotFoundError:
            print("cloudflared не установлен — приложение недоступно, работает только чат. Установка: planner/install.sh")
            return
        found = threading.Event()

        def read():
            global public_url
            for line in proc.stderr:
                m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
                if m and not found.is_set():
                    public_url = m.group(0)
                    found.set()

        threading.Thread(target=read, daemon=True).start()
        if found.wait(60):
            for _ in range(30):  # адрес начинает открываться не сразу
                if healthy():
                    break
                time.sleep(3)
            set_menu()
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


VERSION = asset_version() if os.path.isdir(WEB) else "0"


def main():
    global offset
    if not TOKEN:
        sys.exit("Нет PLANNER_TOKEN (токен от @BotFather). Впиши его в /etc/planner-bot.env")
    threading.Thread(target=serve, daemon=True).start()
    if not public_url:
        threading.Thread(target=tunnel, daemon=True).start()
    setup_bot()
    threading.Thread(target=scheduler, daemon=True).start()
    print(f"{NAME} запущен")
    while True:
        try:
            upd = tg("getUpdates", offset=offset, timeout=50, allowed_updates=["message", "callback_query"])
        except Exception as e:
            print("getUpdates:", e)
            time.sleep(5)
            continue
        for u in upd.get("result", []):
            offset = u["update_id"] + 1
            try:
                if u.get("callback_query"):
                    on_callback(u["callback_query"])
                elif u.get("message"):
                    on_message(u["message"])
            except Exception as e:
                print("update:", e)
                traceback.print_exc()


if __name__ == "__main__":
    main()
