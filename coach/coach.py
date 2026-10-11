#!/usr/bin/env python3
"""Telegram-бот «Форма»: личный тренер по данным WHOOP — спортивный врач, тренер, нутрициолог и сон.

Главное — приложение внутри Telegram (Mini App, папка webapp/): Сегодня, Тренды, Питание, Тренер.
В чате: утренний разбор после пробуждения, разбор каждой тренировки, вопросы тренеру текстом,
еда фото или текстом («съел гречку с курицей») — бот считает ккал и БЖУ.

Переменные окружения (на сервере — /etc/coach-bot.env):
  COACH_TOKEN          токен от @BotFather
  COACH_ALLOWED        Telegram ID через запятую; если пусто — владелец тот, кто первым нажал /start
  COACH_URL            свой https-адрес приложения; если пусто — https://coach.<ip-через-дефисы>.sslip.io
  COACH_PORT           порт веб-сервера на 127.0.0.1 (по умолчанию 8789)
  COACH_DB             файл базы (по умолчанию coach/data/coach.db)
  WHOOP_CLIENT_ID      из developer-dashboard.whoop.com (можно прислать боту: /whoop <id> <secret>)
  WHOOP_CLIENT_SECRET

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
import secrets
import shutil
import socket
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
import ai  # noqa: E402
import insights  # noqa: E402
import store  # noqa: E402
import whoop  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(HERE), "common"))
import media  # noqa: E402  голосовые -> текст (общий помощник)

TOKEN = os.environ.get("COACH_TOKEN", "")
API = f"https://api.telegram.org/bot{TOKEN}/"
ALLOWED = {int(x) for x in re.findall(r"\d+", os.environ.get("COACH_ALLOWED", ""))}
PORT = int(os.environ.get("COACH_PORT") or 8789)
DATA = os.path.join(HERE, "data")
WEB = os.path.join(HERE, "webapp")
REPO = os.path.dirname(HERE)
NAME = "Форма"
SYNC_EVERY = 15 * 60
SYNC_MORNING = 5 * 60  # утром, пока ждём сон для разбора

db = store.Store(os.environ.get("COACH_DB") or os.path.join(DATA, "coach.db"))
public_url = os.environ.get("COACH_URL", "").rstrip("/")
offset = 0
https_note = "запускается"
busy = set()  # пользователи, для которых сейчас думает Claude


def esc(x):
    return html.escape(str(x), quote=False)


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


def typing(chat):
    try:
        tg("sendChatAction", chat_id=chat, action="typing")
    except Exception:
        pass


def app_button(text="⚡ Открыть Форму", path=""):
    if not public_url:
        return None
    return {"text": text, "web_app": {"url": public_url + "/" + (f"?p={urllib.parse.quote(path)}" if path else "")}}


def with_app(rows, text="⚡ Открыть Форму", path=""):
    b = app_button(text, path)
    return rows + [[b]] if b else rows


def setup_bot():
    commands = [
        {"command": "today", "description": "Разбор дня"},
        {"command": "progress", "description": "Разбор прогресса за 2 месяца"},
        {"command": "app", "description": "Открыть приложение"},
        {"command": "food", "description": "Питание за сегодня"},
        {"command": "goals", "description": "Мои цели"},
        {"command": "morning", "description": "Время утреннего разбора"},
        {"command": "connect", "description": "Подключить WHOOP"},
        {"command": "sync", "description": "Обновить данные WHOOP"},
        {"command": "new", "description": "Начать разговор с тренером заново"},
        {"command": "status", "description": "Проверка"},
        {"command": "help", "description": "Как пользоваться"},
    ]
    desc = ("Личный тренер по данным WHOOP: восстановление, сон, нагрузка, питание.\n\n"
            "Утром — разбор и план на день, после тренировки — что съесть и как восстановиться. "
            "Пришли фото еды — посчитаю ккал и БЖУ.")
    short = "Тренер, спортивный врач и нутрициолог по данным WHOOP."
    key = hashlib.sha1(json.dumps([commands, desc, short]).encode()).hexdigest()
    if db.meta("bot_setup") != key:
        try:
            tg("setMyCommands", commands=commands)
            tg("setMyDescription", description=desc)
            tg("setMyShortDescription", short_description=short)
            db.meta("bot_setup", key)
        except Exception as e:
            print("setup:", e)


def set_menu():
    if not public_url:
        return False
    try:
        tg("setChatMenuButton", menu_button={"type": "web_app", "text": "Форма", "web_app": {"url": public_url + "/"}})
        return True
    except Exception as e:
        print("menu:", e)
        return False


def download(file_id, folder):
    info = tg("getFile", file_id=file_id)["result"]
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{int(time.time())}{os.path.splitext(info['file_path'])[1] or '.jpg'}")
    urllib.request.urlretrieve(f"https://api.telegram.org/file/bot{TOKEN}/{info['file_path']}", path)
    return path


# ---------- доступ ----------

def owner():
    v = db.meta("owner")
    return int(v) if v else 0


def allowed(uid):
    return uid in ALLOWED if ALLOWED else uid == owner()


def claim(uid):
    if not ALLOWED and not owner():
        db.meta("owner", uid)
        print("Владелец:", uid)


def is_admin(uid):
    return uid == (owner() or min(ALLOWED or {0}))


def check_init_data(init_data, token=None, max_age=7 * 86400):
    """Подпись данных Mini App (core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app)."""
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
    variants = [pairs] + ([{k: v for k, v in pairs.items() if k != "signature"}] if "signature" in pairs else [])
    if not got or not any(hmac.compare_digest(sign(d), got) for d in variants):
        return None
    try:
        if time.time() - int(pairs.get("auth_date", 0)) > max_age:
            return None
        return json.loads(pairs.get("user", "null"))
    except ValueError:
        return None


# ---------- время и данные пользователя ----------

def user_now(u):
    try:
        tz = ZoneInfo(u.get("tz") or "Europe/Moscow")
    except Exception:
        tz = ZoneInfo("Europe/Moscow")
    return datetime.datetime.now(tz)


def today_of(uid):
    return user_now(db.user(uid)).date()


def profile(uid):
    return db.user(uid)["profile"]


def context(uid, day=None):
    day = day or today_of(uid)
    return insights.text_context(db, uid, day, profile(uid), whoop.body(db, uid))


# ---------- WHOOP ----------

def creds():
    cid = os.environ.get("WHOOP_CLIENT_ID") or db.meta("whoop_client_id") or ""
    sec = os.environ.get("WHOOP_CLIENT_SECRET") or db.meta("whoop_client_secret") or ""
    return cid, sec


def redirect_url():
    return public_url + "/whoop/callback" if public_url else ""


def connect_link(uid):
    cid, _ = creds()
    if not (cid and public_url):
        return None
    state = secrets.token_urlsafe(16)
    db.meta(f"state:{state}", f"{uid}:{int(time.time())}")
    return whoop.auth_link(cid, redirect_url(), state)


def setup_text():
    url = redirect_url() or "(адрес приложения ещё не готов — /status)"
    return ("🔌 <b>Подключение WHOOP</b> — один раз, 3 минуты. Это не новое приложение на телефон, а ключ доступа: "
            "только так WHOOP разрешает боту читать твои данные.\n\n"
            "1. Открой <a href=\"https://developer-dashboard.whoop.com/apps/create\">developer-dashboard.whoop.com</a> "
            "и войди своим аккаунтом WHOOP.\n"
            "2. Создай команду (Team, любое имя), потом <b>Create App</b>: имя <code>Forma</code>, почта в Contacts.\n"
            "3. Privacy Policy:\n<code>https://sawqws.github.io/123/privacy.html</code>\n"
            f"4. Redirect URL (и нажми ADD):\n<code>{esc(url)}</code>\n"
            "5. Scopes: отметь все 6 <code>read:*</code> (offline бот запросит сам).\n"
            "6. Нажми Create App и пришли мне сюда одной строкой:\n"
            "<code>/whoop CLIENT_ID CLIENT_SECRET</code>\n\n"
            "Секрет храню только на сервере, сообщение с ним сразу удалю.")


def connect(chat, uid):
    cid, sec = creds()
    if not (cid and sec):
        send(chat, setup_text())
        return
    link = connect_link(uid)
    if not link:
        send(chat, "Адрес приложения ещё не готов. Подожди минуту и повтори /connect (что с адресом — /status).")
        return
    send(chat, "🔗 Нажми, войди в WHOOP и разреши доступ — потом вернись сюда.",
         [[{"text": "Подключить WHOOP", "url": link}]])


def finish_connect(state, code):
    """Возврат из WHOOP: код -> токены, первая загрузка за 60 дней. Возвращает (ok, текст для страницы)."""
    v = db.meta(f"state:{state}") if state else None
    if not v:
        return False, "Ссылка устарела. Отправь /connect в боте ещё раз."
    uid, at = (int(x) for x in v.split(":"))
    db.meta(f"state:{state}", "")
    if time.time() - at > 3600:
        return False, "Ссылка устарела. Отправь /connect в боте ещё раз."
    cid, sec = creds()
    try:
        whoop.exchange(db, uid, code, cid, sec, redirect_url())
        db.mark_synced(uid)  # первую загрузку делает first_sync, расписание пусть не дублирует
    except Exception as e:
        print("whoop exchange:", e)
        try:  # новая кнопка сразу в чат — писать /connect не нужно
            send(uid, "⚠️ WHOOP не принял вход. Вот новая кнопка — попробуй ещё раз.")
            connect(uid, uid)
        except Exception as e2:
            print("reconnect:", e2)
        return False, "WHOOP не принял вход. Новая кнопка уже в Telegram."
    threading.Thread(target=first_sync, args=(uid,), daemon=True).start()
    return True, "WHOOP подключён. Возвращайся в Telegram."


def first_sync(uid):
    try:
        send(uid, "✅ <b>WHOOP подключён.</b> Загружаю данные за 2 месяца…")
        sync_user(uid, days=60, quiet=True)
        n = db.record_count(uid)
        b = whoop.body(db, uid)
        p = profile(uid)
        upd = {}
        if b.get("weight_kilogram") and not p.get("weight"):
            upd["weight"] = round(b["weight_kilogram"], 1)
        if b.get("height_meter") and not p.get("height"):
            upd["height"] = round(b["height_meter"] * 100)
        if upd:
            db.save_profile(uid, upd)
        send(uid, f"📥 Загружено записей: {n}.\n\nДобавь в профиле возраст, спорт и цель — так советы будут точнее. "
                  "Утром пришлю первый разбор.", with_app([[{"text": "🧠 Разбор прямо сейчас", "callback_data": "b|now"}]],
                                                         "👤 Заполнить профиль", "/profile"))
    except Exception as e:
        traceback.print_exc()
        send(uid, f"⚠️ Не получилось загрузить данные WHOOP: {esc(str(e)[:200])}")


def sync_user(uid, days=3, quiet=False):
    cid, sec = creds()
    try:
        fresh = whoop.sync(db, uid, cid, sec, days=days)
    except whoop.NotConnected:
        db.drop_token(uid)
        send(uid, "⚠️ WHOOP отключился (вход истёк). Подключи заново: /connect")
        return []
    if not quiet:
        react(uid, fresh)
    return fresh


def react(uid, fresh):
    """Новые оценённые записи: тренировка -> разбор тренировки. Утренний разбор шлёт расписание (maybe_morning)."""
    u = db.user(uid)
    now = user_now(u)
    day = now.date().isoformat()
    for kind, r in fresh:
        if kind == "workout" and r.get("_day") == day and 6 <= now.hour < 23:
            w = insights.workout_of(r)
            if (w["minutes"] or 0) >= 15 and db.once(f"workout:{r.get('id')}"):
                threading.Thread(target=workout_note, args=(uid, w), daemon=True).start()


# ---------- разборы ----------

ZONE_ICON = {"green": "🟢", "yellow": "🟡", "red": "🔴", None: "⚪️"}


def num(x, n=1):
    return str(round(x, n)).replace(".", ",") if n else str(round(x))


def headline(t):
    """Цифры утра: восстановление, ВСР и пульс покоя против нормы, сон, нагрузка вчера."""
    r, s, b, y = t.get("recovery"), t.get("sleep"), t.get("base") or {}, t.get("yesterday") or {}
    lines = []
    if r:
        parts = [f"{ZONE_ICON[r['zone']]} Восстановление <b>{r['score']}%</b>"]
        if r.get("hrv"):
            parts.append(f"ВСР {round(r['hrv'])}" + (f" (норма {round(b['hrv'])})" if b.get("hrv") else ""))
        if r.get("rhr"):
            parts.append(f"пульс покоя {r['rhr']}" + (f" ({round(b['rhr'])})" if b.get("rhr") else ""))
        lines.append(" · ".join(parts))
    if s:
        parts = [f"😴 Сон <b>{insights.fmt_h(s['hours'])}</b>" + (f" из {insights.fmt_h(s['need'])}" if s.get("need") else "")]
        if s.get("performance") is not None:
            parts.append(f"выполнение {s['performance']}%")
        if s.get("efficiency") is not None:
            parts.append(f"эффективность {s['efficiency']}%")
        lines.append(" · ".join(parts))
    if y.get("strain") is not None:
        ws = ", ".join(f"{w['sport'].lower()} {w['minutes']} мин" for w in y.get("workouts") or [] if w.get("minutes"))
        lines.append(f"🔥 Нагрузка вчера <b>{num(y['strain'])}</b>" + (f" · {esc(ws)}" if ws else ""))
    return "\n".join(lines)


def bed_line(t):
    bt = t.get("bedtime")
    if not bt:
        return ""
    return f"Отбой сегодня — до <b>{bt['bed']}</b>, чтобы поспать {insights.fmt_h(bt['need'])} до подъёма в {bt['wake']}."


def rule_brief(t):
    """Разбор без Claude: выводы по правилам, ориентир нагрузки, питание."""
    lines = [f"• {x['text']}" for x in t["tips"][:4]]
    if t["band"]:
        lines.append(f"• Ориентир нагрузки на сегодня: <b>{t['band'][0]}–{t['band'][1]}</b>.")
    n = t["nutrition"]
    if n:
        lines.append(f"• Питание: <b>{n['kcal']} ккал</b>, белок {n['protein']} г, вода {n['water'] / 1000:.1f} л.".replace(".", ",", 1))
    if bed_line(t):
        lines.append("• " + bed_line(t))
    return "\n".join(lines) or "Данных за сегодня пока нет."


def rule_early(t):
    """План без сегодняшнего сна (WHOOP ещё не посчитал) — по вчерашнему дню."""
    y = t.get("yesterday") or {}
    hard = (y.get("strain") or 0) >= 14
    lines = ["• Тренировка — по самочувствию: разминка 15 мин; тяжело и вялый — техника вместо спарринга."
             + (" Вчера была большая нагрузка — не форсируй." if hard else "")]
    n = t["nutrition"]
    if n:
        lines.append(f"• Вода {n['water'] / 1000:.1f} л, белок {n['protein']} г, завтрак не пропускай.".replace(".", ",", 1))
    else:
        lines.append("• Вода 2–2,5 л, белок в каждый приём пищи, завтрак не пропускай.")
    if bed_line(t):
        lines.append("• " + bed_line(t))
    return "\n".join(lines)


def send_long(chat, text, buttons=None):
    """Telegram пускает до 4096 символов — длинный разбор режем по абзацам."""
    parts, cur = [], ""
    for para in text.split("\n\n"):
        if cur and len(cur) + len(para) + 2 > 3800:
            parts.append(cur)
            cur = para
        else:
            cur = f"{cur}\n\n{para}" if cur else para
    parts.append(cur)
    for i, p in enumerate(parts):
        send(chat, p, buttons if i == len(parts) - 1 else None)


def progress_report(uid, chat=None, week=False):
    """Разбор прогресса за 60 дней (или итог недели — week=True)."""
    chat = chat or uid
    if uid in busy:
        send(chat, "Секунду, ещё думаю над прошлым сообщением…")
        return
    busy.add(uid)
    try:
        typing(chat)
        day = today_of(uid)
        ctx, p = insights.progress_context(db, uid, day, profile(uid), whoop.body(db, uid), 28 if week else 60)
        if not any(w["days"] for w in p["weeks"]):
            send(chat, "Данных WHOOP пока нет. Подключи браслет: /connect")
            return
        if not week:
            send(chat, "🧠 Разбираю все твои данные за 2 месяца — это минута-две…")
        try:
            body = ai.to_html(ai.progress(ctx, week=week))
        except ai.AIError as e:
            send(chat, f"⚠️ Разбор не получился: {esc(e)}. Попробуй ещё раз: /progress")
            return
        db.brief(uid, day.isoformat(), "week" if week else "progress", body)
        title = "📅 <b>Итог недели</b>" if week else "📈 <b>Разбор прогресса за 2 месяца</b>"
        send_long(chat, f"{title}\n\n{body}", with_app([[{"text": "💬 Спросить тренера", "callback_data": "q|"}]],
                                                       "📊 Тренды", "/trends"))
    finally:
        busy.discard(uid)


def morning_brief(uid, chat=None, force=False):
    """Разбор дня. True — отправлено (или сказано, что данных нет), False — занят другим ответом."""
    chat = chat or uid
    if uid in busy and not force:
        return False
    busy.add(uid)
    try:
        typing(chat)
        text, t = context(uid)
        if not t["has_data"]:
            send(chat, "Данных WHOOP пока нет. Подключи браслет: /connect")
            return True
        try:
            body = ai.to_html(ai.morning(text))
        except ai.AIError as e:
            body = rule_brief(t) + f"\n\n<i>Подробный разбор недоступен: {esc(e)}</i>"
        db.brief(uid, t["day"], "morning", body)
        send(chat, f"☀️ <b>Доброе утро</b>\n{headline(t)}\n\n{body}",
             with_app([[{"text": "💬 Спросить тренера", "callback_data": "q|"}]], "⚡ Открыть день"))
        if t["recovery"]:  # разбор с данными за сегодня уже был — расписание второй не пришлёт
            db.once(f"morning:{uid}:{t['day']}")
        return True
    finally:
        busy.discard(uid)


def morning_early(uid):
    """Утро, а сна за ночь нет (WHOOP не посчитал или браслет не надет): план по вчерашнему дню."""
    if uid in busy:
        return False
    busy.add(uid)
    try:
        text, t = context(uid)
        try:
            body = ai.to_html(ai.morning_early(text))
        except ai.AIError:
            body = rule_early(t)
        db.brief(uid, t["day"], "morning", body)
        y = headline({"yesterday": t.get("yesterday")})
        send(uid, "☀️ <b>Доброе утро</b>\nWHOOP ещё не посчитал сон — разбор пришлю, как только он появится "
                  "(если браслет был на руке).\n" + (y + "\n" if y else "") + f"\n{body}",
             with_app([[{"text": "🔄 Обновить WHOOP", "callback_data": "s|"}]], "⚡ Открыть день"))
        return True
    finally:
        busy.discard(uid)


# ---------- утренний разбор по расписанию ----------
# Каждый день не раньше времени из /morning (по умолчанию 07:00, часовой пояс пользователя): как только WHOOP
# посчитал восстановление — полный разбор. Не посчитал за MORNING_WAIT часов — короткий план без сна, а полный
# разбор — когда данные появятся (до MORNING_LATE часов после времени). Каждое — не больше раза в день.
MORNING_WAIT = datetime.timedelta(hours=3)
MORNING_LATE = datetime.timedelta(hours=8)


def morning_at(u):
    """Время утреннего разбора (datetime.time) или None — выключен."""
    v = u["profile"].get("morning") or store.MORNING_DEFAULT
    if v == "off":
        return None
    try:
        h, m = (int(x) for x in v.split(":"))
        return datetime.time(h, m)
    except ValueError:
        return datetime.time(7, 0)


def morning_text(u):
    at = morning_at(u)
    if not at:
        return "☀️ Утренний разбор <b>выключен</b>. Включить: /morning 07:00"
    late = (datetime.datetime.combine(datetime.date.today(), at) + MORNING_WAIT).strftime("%H:%M")
    return (f"☀️ Утренний разбор — каждый день с <b>{at.strftime('%H:%M')}</b>: как только WHOOP посчитает сон "
            f"(если проснёшься позже — сразу после этого). Не посчитал к {late} — пришлю короткий план без сна.\n\n"
            "Другое время: <code>/morning 08:30</code> · выключить: <code>/morning off</code>")


def morning_buttons(u):
    cur = u["profile"].get("morning") or store.MORNING_DEFAULT
    times = ["06:30", "07:00", "07:30", "08:00", "09:00"]
    row = [{"text": ("✓ " if cur == x else "") + x, "callback_data": f"mt|{x}"} for x in times]
    return [row[:3], row[3:] + [{"text": ("✓ " if cur == "off" else "") + "Выкл", "callback_data": "mt|off"}]]


def night_ready(uid, day):
    """'full' — восстановление за сегодня посчитано, 'part' — только сон, None — ничего."""
    d = insights.by_day(db, uid, day, day).get(day) or {}
    return "full" if d.get("recovery") else "part" if d.get("sleep") else None


def morning_due(at, now, ready, sent_brief, sent_early):
    """Что прислать сейчас: 'brief' (полный разбор), 'early' (план без сна) или None."""
    if at is None or sent_brief:
        return None
    start = datetime.datetime.combine(now.date(), at, now.tzinfo)
    if not start <= now < start + MORNING_LATE:
        return None
    if ready == "full":
        return "brief"
    if now >= start + MORNING_WAIT:
        if ready == "part":
            return "brief"
        if not sent_early:
            return "early"
    return None


def morning_waiting(u, now):
    """Ждём сон к утреннему разбору — синхронизировать WHOOP чаще (с 10 минут до времени разбора)."""
    at = morning_at(u)
    if not at or db.was_sent(f"morning:{u['id']}:{now.date()}"):
        return False
    start = datetime.datetime.combine(now.date(), at, now.tzinfo)
    return start - datetime.timedelta(minutes=10) <= now < start + MORNING_LATE


def maybe_morning(u, now):
    uid = u["id"]
    if not db.token(uid) or uid in busy:
        return None
    day = now.date().isoformat()
    keys = {"brief": f"morning:{uid}:{day}", "early": f"morning-early:{uid}:{day}"}
    kind = morning_due(morning_at(u), now, night_ready(uid, day), db.was_sent(keys["brief"]), db.was_sent(keys["early"]))
    if kind and db.once(keys[kind]):
        threading.Thread(target=morning_job, args=(uid, kind, keys[kind]), daemon=True).start()
    return kind


def morning_job(uid, kind, key):
    ok = False
    try:
        ok = morning_brief(uid) if kind == "brief" else morning_early(uid)
    except Exception as e:
        print("morning:", uid, kind, e)
        traceback.print_exc()
    if not ok:  # не отправилось — расписание повторит через минуту
        db.unsend(key)


def workout_note(uid, w):
    typing(uid)
    text, t = context(uid)
    try:
        body = ai.to_html(ai.after_workout(text, w))
    except ai.AIError:
        body = ("• Выпей 500–700 мл воды в течение часа.\n• Поешь в ближайшие 1–2 часа: белок 25–35 г и углеводы "
                "(рис, гречка, паста, фрукты).\n• Вечером без экранов за 30 минут до сна — так быстрее восстановишься.")
    db.brief(uid, t["day"], f"workout:{w['id']}", body)
    send(uid, f"🥊 <b>{esc(w['sport'])}</b> · {w['minutes']} мин · нагрузка <b>{w['strain']}</b> · пульс {w['avg_hr']}/{w['max_hr']}\n\n{body}",
         with_app([], "⚡ Открыть день"))


def evening(uid, now):
    """Вечером — если записывал еду, а до цели далеко: сколько добрать и чем."""
    day = now.date()
    _, t = context(uid, day)
    n, e = t["nutrition"], t["eaten"]
    if not n or not e["count"]:
        return
    lack_p, lack_k = n["protein"] - e["protein"], n["kcal"] - e["kcal"]
    if lack_p < 20 and lack_k < 300:
        return
    tip = []
    if lack_p >= 20:
        tip.append(f"белка не хватает {lack_p} г — творог, яйца, курица или кефир перед сном")
    if lack_k >= 300:
        tip.append(f"до нормы {lack_k} ккал — добавь нормальный ужин, не пропускай")
    send(uid, "🍽 <b>Итог питания</b>\n" + f"Съедено {e['kcal']} из {n['kcal']} ккал, белок {e['protein']} из {n['protein']} г.\n\n"
         + "\n".join(f"• {x.capitalize()}." for x in tip), with_app([], "🍽 Открыть питание", "/food"))


# ---------- чат ----------

HELP = ("⚡ <b>Форма</b> — твой тренер по данным WHOOP.\n\n"
        "• Каждое утро (с 07:00, время — /morning) пришлю разбор: восстановление, сон, можно ли в полную, что есть, когда лечь.\n"
        "• <b>Цели</b> (/goals): «ложиться до 23:00», «соревнования весной» — все советы привязываю к ним.\n"
        "• После тренировки — что съесть и как восстановиться.\n"
        "• <b>Спрашивай что угодно</b> текстом: «почему низкое восстановление?», «что съесть перед боксом?».\n"
        "• <b>Еда</b>: пришли фото тарелки или напиши «съел гречку с курицей» — посчитаю ккал и БЖУ.\n"
        "• <b>Голосовые</b> понимаю как текст. <b>Скрин</b> WHOOP или плана тренировок — пришли с вопросом в подписи.\n"
        "• <b>/progress</b> — большой разбор прогресса за 2 месяца и план; по воскресеньям в 19:00 — итог недели.\n\n"
        "/today — разбор дня · /goals — цели · /morning — время разбора · /progress — прогресс · /food — питание · "
        "/sync — обновить · /connect — WHOOP · /new — новый разговор")

FOOD_WORDS = re.compile(r"^(съел[аи]?|поел[аи]?|ел[аи]?|выпил[аи]?|завтрак|обед|ужин|перекус|еда)\b[:\s,-]*", re.I)


def welcome(chat, uid, name):
    cid, _ = creds()
    connected = bool(db.token(uid))
    rows = [] if connected else [[{"text": "🔌 Подключить WHOOP", "callback_data": "c|"}]]
    send(chat, f"Привет{', ' + esc(name) if name else ''}! Я <b>Форма</b> — тренер, спортивный врач и нутрициолог в одном.\n\n"
               "Смотрю на твои восстановление, сон, пульс и нагрузку с WHOOP и говорю, как тренироваться, "
               "есть и спать сегодня.\n\n" + ("Данные WHOOP подключены ✅" if connected else
                                               "Начнём с подключения WHOOP 👇" if cid else "Сначала подключим WHOOP — это 3 минуты 👇"),
         with_app(rows, "⚡ Открыть Форму"))


def meal_card(m, comment=""):
    s = (f"🍽 <b>{esc(m['title'])}</b>\n{round(m['kcal'])} ккал · Б {round(m['protein'])} · Ж {round(m['fat'])} · У {round(m['carbs'])}")
    return s + (f"\n\n{esc(comment)}" if comment else "")


def food_text(uid):
    _, t = context(uid)
    n, e = t["nutrition"], t["eaten"]
    lines = ["🍽 <b>Питание сегодня</b>"]
    if n:
        lines.append(f"{e['kcal']} / {n['kcal']} ккал · Б {e['protein']}/{n['protein']} · Ж {e['fat']}/{n['fat']} · У {e['carbs']}/{n['carbs']}")
        lines.append(f"Вода: {n['water'] / 1000:.1f} л".replace(".", ","))
    else:
        lines.append("Добавь вес в профиле — посчитаю норму.")
    for m in t["meals"]:
        lines.append(f"• {esc(m['time'])} {esc(m['title'])} — {round(m['kcal'])} ккал")
    if not t["meals"]:
        lines.append("\nПока ничего не записано. Пришли фото еды или напиши «съел …».")
    return "\n".join(lines)


PHOTO_ASK = "Что на фото? Разбери с учётом моих данных и целей."
QUESTION = re.compile(r"^(почему|зачем|что|как|какой|какая|какие|сколько|когда|можно|стоит|нормально|разбери|объясни|"
                      r"оцени|посмотри|глянь|проверь|помоги)\b", re.I)


def log_meal(chat, uid, text="", photo=None):
    if uid in busy:
        send(chat, "Секунду, ещё думаю над прошлым сообщением…")
        return
    busy.add(uid)
    try:
        typing(chat)
        ctx, _ = context(uid)
        try:
            est = ai.meal(ctx, text, image=photo)
        except ai.AIError as e:
            send(chat, f"⚠️ Не смог оценить еду: {esc(e)}. Можно добавить вручную в приложении.", with_app([], "🍽 Питание", "/food"))
            return
        if est.get("food") is False:
            if photo:  # скрин WHOOP, план тренировок и т. п. — это вопрос тренеру с картинкой
                busy.discard(uid)
                answer(chat, uid, text or PHOTO_ASK, photo)
            else:
                send(chat, esc(est.get("comment") or "Не похоже на еду. Если это вопрос — напиши его текстом."))
            return
        now = user_now(db.user(uid))
        m = db.save_meal(uid, {**est, "day": now.date().isoformat(), "time": now.strftime("%H:%M")})
        _, t = context(uid)
        n, e = t["nutrition"], t["eaten"]
        total = f"\n\nЗа день: {e['kcal']}" + (f" / {n['kcal']} ккал, белок {e['protein']}/{n['protein']} г" if n else " ккал")
        send(chat, meal_card(m, est.get("comment", "")) + total,
             with_app([[{"text": "🗑 Удалить", "callback_data": f"m|del|{m['id']}"}]], "🍽 Питание", "/food"))
    finally:
        busy.discard(uid)


def answer(chat, uid, question, photo=None):
    if uid in busy:
        send(chat, "Секунду, ещё думаю над прошлым сообщением…")
        return
    busy.add(uid)
    try:
        typing(chat)
        ctx, _ = context(uid)
        hist = db.history(uid)
        try:
            out = ai.claude(ai.chat_prompt(ctx, hist, question), image=photo)
        except ai.AIError as e:
            send(chat, f"⚠️ {esc(e)}.")
            return
        db.say(uid, "user", question)
        db.say(uid, "coach", out)
        send(chat, ai.to_html(out))
    finally:
        busy.discard(uid)


def goals_view(uid):
    gs = db.goals(uid)
    if gs:
        lines = ["🎯 <b>Мои цели</b>"] + [f"{i}. {esc(g['text'])}" for i, g in enumerate(gs, 1)]
        lines.append("\nУчитываю их в каждом совете, утром пишу, как день двигает к цели.")
    else:
        lines = ["🎯 <b>Цели</b> пока не записаны.",
                 "Напиши, к чему идёшь: «ложиться до 23:00», «бокс — соревнования весной», «вес 60 кг к декабрю». "
                 "Все советы буду привязывать к ним."]
    rows = [[{"text": f"🗑 {i}. {g['text'][:28]}", "callback_data": f"g|del|{g['id']}"}] for i, g in enumerate(gs, 1)]
    if len(gs) < store.MAX_GOALS:
        rows.append([{"text": "➕ Добавить цель", "callback_data": "g|add"}])
    return "\n".join(lines), rows


def add_goal(chat, uid, text):
    try:
        db.add_goal(uid, text)
    except ValueError as e:
        send(chat, f"⚠️ {esc(e)}")
        return
    body, rows = goals_view(uid)
    send(chat, "✅ Цель записана.\n\n" + body, rows)


def awaiting(uid, what=None):
    """Ждём от пользователя текст (например, цель после кнопки «Добавить цель»). what=None — проверить и сбросить."""
    if what:
        db.meta(f"await:{uid}", f"{what}:{int(time.time())}")
        return what
    v = db.meta(f"await:{uid}")
    if not v:
        return None
    db.meta(f"await:{uid}", "")
    kind, _, at = v.partition(":")
    return kind if time.time() - int(at or 0) < 15 * 60 else None


def set_morning(chat, uid, value):
    try:
        db.save_profile(uid, {"morning": value})
    except ValueError as e:
        send(chat, f"⚠️ {esc(e)}")
        return
    u = db.user(uid)
    send(chat, morning_text(u), morning_buttons(u))


def on_message(msg):
    chat = msg["chat"]["id"]
    frm = msg.get("from") or {}
    uid = frm.get("id")
    text = (msg.get("text") or msg.get("caption") or "").strip()
    if msg["chat"].get("type") != "private":
        return
    if text.startswith("/start"):
        claim(uid)
    if not allowed(uid):
        send(chat, "🔒 Это личный тренер. Попроси владельца добавить тебя.")
        return
    db.user(uid, frm.get("first_name"))
    cmd = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
    arg = text.split(None, 1)[1].strip() if cmd and len(text.split(None, 1)) > 1 else ""
    wait = awaiting(uid)
    if cmd == "/start":
        arg = text.split()[1] if len(text.split()) > 1 else ""
        welcome(chat, uid, frm.get("first_name"))
        if arg == "connect":
            connect(chat, uid)
    elif cmd == "/help":
        send(chat, HELP, with_app([]))
    elif cmd == "/app":
        if public_url:
            send(chat, "⚡ Твоя Форма:", with_app([]))
        else:
            send(chat, "Приложение ещё запускается — пришлю кнопку, как только будет готово. /status — что с адресом.")
    elif cmd == "/connect":
        connect(chat, uid)
    elif cmd == "/whoop":
        save_creds(chat, uid, msg, text)
    elif cmd == "/today":
        threading.Thread(target=morning_brief, args=(uid, chat), daemon=True).start()
    elif cmd == "/progress":
        threading.Thread(target=progress_report, args=(uid, chat), daemon=True).start()
    elif cmd == "/food":
        send(chat, food_text(uid), with_app([], "🍽 Открыть питание", "/food"))
    elif cmd in ("/goals", "/goal"):
        if arg:
            add_goal(chat, uid, arg)
        else:
            send(chat, *goals_view(uid))
    elif cmd == "/morning":
        if arg:
            set_morning(chat, uid, arg)
        else:
            u = db.user(uid)
            send(chat, morning_text(u), morning_buttons(u))
    elif cmd == "/sync":
        if not db.token(uid):
            connect(chat, uid)
        else:
            send(chat, "🔄 Обновляю…")
            threading.Thread(target=lambda: (sync_user(uid, days=7),
                                             send(chat, "✅ Данные обновлены.", with_app([]))), daemon=True).start()
    elif cmd == "/new":
        db.clear_history(uid)
        send(chat, "🧹 Начали разговор заново. Данные и профиль на месте.")
    elif cmd == "/status":
        threading.Thread(target=status, args=(chat, uid), daemon=True).start()
    elif cmd == "/update" and is_admin(uid):
        update(chat)
    elif cmd:
        send(chat, "Такой команды нет. /help — что я умею.")
    elif msg.get("photo") or (msg.get("document") or {}).get("mime_type", "").startswith("image/"):
        fid = msg["photo"][-1]["file_id"] if msg.get("photo") else msg["document"]["file_id"]
        path = download(fid, os.path.join(DATA, "photos", str(uid)))
        # подпись-вопрос — сразу тренеру с картинкой; иначе Claude решает: еда -> ккал, не еда -> разбор (log_meal)
        is_question = text.endswith("?") or len(text) > 80 or bool(QUESTION.match(text))
        target = answer if is_question else log_meal
        threading.Thread(target=target, args=(chat, uid, text, path), daemon=True).start()
    elif media.kind(msg) == "voice":
        threading.Thread(target=on_voice, args=(chat, uid, msg, wait), daemon=True).start()
    elif text:
        on_text(chat, uid, text, wait)


def on_text(chat, uid, text, wait=None):
    """Обычный текст (или распознанное голосовое): цель после кнопки, еда или вопрос тренеру."""
    if wait == "goal":
        add_goal(chat, uid, text)
        return
    m = FOOD_WORDS.match(text)
    if m and len(text) > len(m.group(0)) + 1:
        threading.Thread(target=log_meal, args=(chat, uid, text), daemon=True).start()
    else:
        threading.Thread(target=answer, args=(chat, uid, text), daemon=True).start()


def on_voice(chat, uid, msg, wait=None):
    """Голосовое, аудио или кружок: распознать (не в главном цикле), показать текст и разобрать как написанное."""
    typing(chat)
    try:
        text = media.voice_text(TOKEN, msg).strip()
    except media.MediaError as e:
        send(chat, f"⚠️ Голосовое: {esc(e)}. Можно написать текстом.")
        if wait:
            awaiting(uid, wait)  # цель всё ещё ждём
        return
    except Exception as e:
        print("voice:", e)
        send(chat, "⚠️ Не получилось скачать голосовое — попробуй ещё раз или напиши текстом.")
        if wait:
            awaiting(uid, wait)
        return
    send(chat, f"🎙 «{esc(text)}»")
    on_text(chat, uid, text, wait)


def save_creds(chat, uid, msg, text):
    if not is_admin(uid):
        send(chat, "Это может сделать только владелец бота.")
        return
    parts = text.split()
    try:
        tg("deleteMessage", chat_id=chat, message_id=msg["message_id"])
    except Exception:
        pass
    if len(parts) != 3:
        send(chat, "Нужно так: <code>/whoop CLIENT_ID CLIENT_SECRET</code>")
        return
    db.meta("whoop_client_id", parts[1])
    db.meta("whoop_client_secret", parts[2])
    send(chat, "🔐 Ключи WHOOP сохранены на сервере, сообщение удалил.")
    connect(chat, uid)


def on_callback(cq):
    uid = cq["from"]["id"]
    msg = cq.get("message") or {}
    chat, mid = msg.get("chat", {}).get("id"), msg.get("message_id")
    data = (cq.get("data") or "").split("|")
    note = ""
    if not allowed(uid):
        note = "Нет доступа"
    elif data[0] == "c":
        connect(chat, uid)
    elif data[0] == "b":
        threading.Thread(target=morning_brief, args=(uid, chat), daemon=True).start()
        note = "Готовлю разбор…"
    elif data[0] == "p":
        threading.Thread(target=progress_report, args=(uid, chat), daemon=True).start()
        note = "Разбираю прогресс…"
    elif data[0] == "q":
        send(chat, "Напиши вопрос — отвечу с учётом твоих данных. Например: «что съесть перед тренировкой?»")
    elif data[0] == "g" and data[1] == "add":
        awaiting(uid, "goal")
        send(chat, "✍️ Напиши цель одним сообщением. Например: «ложиться до 23:00» или «соревнования по боксу в марте».")
    elif data[0] == "g" and data[1] == "del":
        if db.delete_goal(uid, int(data[2])):
            note = "Удалено"
        edit(chat, mid, *goals_view(uid))
    elif data[0] == "mt":
        try:
            db.save_profile(uid, {"morning": data[1]})
            u = db.user(uid)
            edit(chat, mid, morning_text(u), morning_buttons(u))
            note = "Выключено" if data[1] == "off" else f"Разбор с {data[1]}"
        except ValueError as e:
            note = str(e)
    elif data[0] == "s":
        if db.token(uid):
            note = "Обновляю WHOOP…"
            threading.Thread(target=sync_user, args=(uid, 3), daemon=True).start()
        else:
            connect(chat, uid)
    elif data[0] == "m" and data[1] == "del":
        if db.delete_meal(uid, int(data[2])):
            edit(chat, mid, "🗑 <s>Удалено</s>")
            note = "Удалено"
    tg("answerCallbackQuery", callback_query_id=cq["id"], text=note)


def status(chat, uid):
    cid, sec = creds()
    tok = db.token(uid)
    lines = ["🩺 <b>Проверка</b>",
             ("✓" if public_url else "✕") + f" Приложение: {esc(public_url or 'нет адреса')} — {esc(https_note)}",
             ("✓" if cid and sec else "✕") + " Ключи WHOOP" + ("" if cid and sec else " — /connect, там инструкция"),
             ("✓" if tok else "✕") + " WHOOP подключён" + (f", обновлено {esc((tok.get('synced') or '—')[:16].replace('T', ' '))} UTC, "
                                                          f"записей {db.record_count(uid)}" if tok else "")]
    try:
        ai.claude("Ответь одним словом: ok", timeout=90)
        lines.append("✓ Claude отвечает")
    except ai.AIError as e:
        lines.append(f"✕ Claude: {esc(e)}")
    if public_url:
        lines.append(f"\nRedirect URL для WHOOP:\n<code>{esc(redirect_url())}</code>")
    send(chat, "\n".join(lines))


def update(chat):
    def git(*a):
        return subprocess.run(["git", *a], cwd=REPO, capture_output=True, text=True)
    res = git("pull", "--ff-only", "origin", "main")
    send(chat, esc((res.stdout + res.stderr).strip()[-1500:] or "ok"))
    if res.returncode == 0:
        send(chat, "Перезапускаюсь…")
        tg("getUpdates", offset=offset, timeout=0)
        os._exit(0)  # pm2 поднимет заново


# ---------- расписание ----------

def tick():
    for u in db.users():
        uid = u["id"]
        if not allowed(uid):
            continue
        tok = db.token(uid)
        now = user_now(u)
        if tok:
            last = tok.get("synced")
            age = time.time() - datetime.datetime.fromisoformat(last).timestamp() if last else 1e9
            every = SYNC_MORNING if morning_waiting(u, now) and night_ready(uid, now.date().isoformat()) != "full" \
                else SYNC_EVERY
            if age >= every:
                try:
                    sync_user(uid, days=3 if db.record_count(uid) else 60, quiet=not db.record_count(uid))
                except Exception as e:
                    print("sync:", uid, e)
            try:
                maybe_morning(db.user(uid), now)
            except Exception as e:
                print("morning:", uid, e)
        if now.hour == 20 and now.minute >= 30 and db.once(f"evening:{uid}:{now.date()}"):
            try:
                evening(uid, now)
            except Exception as e:
                print("evening:", e)
        # итог недели — воскресенье, 19:00
        if tok and now.weekday() == 6 and now.hour == 19 and db.once(f"week:{uid}:{now.date()}"):
            threading.Thread(target=progress_report, args=(uid, uid, True), daemon=True).start()


def scheduler():
    while True:
        try:
            tick()
        except Exception as e:
            print("scheduler:", e)
            traceback.print_exc()
        time.sleep(60)


# ---------- веб: приложение и API ----------

def asset_version():
    h = hashlib.sha1()
    for f in sorted(os.listdir(WEB)):
        with open(os.path.join(WEB, f), "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()[:10]


PAGE_DONE = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Форма</title><style>body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#07080a;color:#f2f4f7;
font:17px/1.4 -apple-system,system-ui,sans-serif;text-align:center}}b{{font-size:44px;display:block;margin-bottom:12px}}
a{{color:#2ee59d}}</style><div><b>{icon}</b>{text}<br><br><a href="{link}">Вернуться в Telegram</a></div>"""


class Web(BaseHTTPRequestHandler):
    server_version = "Forma/1"

    def log_message(self, fmt, *args):
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
        u = urllib.parse.urlparse(self.path)
        path = u.path
        if path == "/health":
            return self.reply(200, "ok", "text/plain")
        if path == "/whoop/callback":
            qs = dict(urllib.parse.parse_qsl(u.query))
            if qs.get("error"):
                ok, text = False, "Доступ не дан. Отправь /connect в боте, чтобы попробовать снова."
            else:
                ok, text = finish_connect(qs.get("state"), qs.get("code"))
            link = f"https://t.me/{db.meta('bot_username') or ''}"
            return self.reply(200, PAGE_DONE.format(icon="✅" if ok else "⚠️", text=esc(text), link=esc(link)),
                              "text/html; charset=utf-8")
        if path.startswith("/api/"):
            return self.api(path, dict(urllib.parse.parse_qsl(u.query)))
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
            return self.reply(401, {"error": "Открой Форму через Telegram"})
        uid = user["id"]
        if not allowed(uid):
            return self.reply(403, {"error": "Это личный тренер"})
        db.user(uid, user.get("first_name"))
        try:
            self.reply(200, route(uid, path, body))
        except ai.AIError as e:
            self.reply(503, {"error": str(e)})
        except (ValueError, KeyError) as e:
            self.reply(400, {"error": str(e).strip("'")})
        except Exception as e:
            traceback.print_exc()
            self.reply(500, {"error": str(e)})


def day_payload(uid, day):
    t = insights.today(db, uid, day, profile(uid), whoop.body(db, uid))
    b = db.brief(uid, day.isoformat(), "morning")
    t["brief"] = b["text"] if b else None
    return t


def route(uid, path, b):
    u = db.user(uid)
    today = user_now(u).date()
    if path == "/api/all":
        cid, sec = creds()
        tok = db.token(uid)
        return {
            "user": {"name": u["name"], "tz": u["tz"]}, "profile": u["profile"], "body": whoop.body(db, uid),
            "connected": bool(tok), "synced": (tok or {}).get("synced"), "keys": bool(cid and sec),
            "today": day_payload(uid, today), "series": insights.series(db, uid, today, 30),
            "redirect": redirect_url(), "thinking": uid in busy,
            "goals": db.goals(uid), "morning": u["profile"].get("morning") or store.MORNING_DEFAULT,
        }
    if path == "/api/day":
        day = datetime.date.fromisoformat(b.get("d") or today.isoformat())
        return {"day": day_payload(uid, min(day, today))}
    if path == "/api/series":
        n = max(7, min(int(b.get("n") or 30), 180))
        return {"series": insights.series(db, uid, today, n)}
    if path == "/api/profile":
        return {"profile": db.save_profile(uid, b)["profile"]}
    if path == "/api/goal":
        db.add_goal(uid, b.get("text"))
        return {"goals": db.goals(uid)}
    if path == "/api/goal/delete":
        db.delete_goal(uid, int(b["id"]))
        return {"goals": db.goals(uid)}
    if path == "/api/meal":
        b = dict(b)
        b.setdefault("day", today.isoformat())
        b.setdefault("time", user_now(u).strftime("%H:%M"))
        return {"meal": db.save_meal(uid, b), "day": day_payload(uid, datetime.date.fromisoformat(b["day"]))}
    if path == "/api/meal/delete":
        db.delete_meal(uid, int(b["id"]))
        return {"ok": True, "day": day_payload(uid, today)}
    if path == "/api/meal/estimate":
        text = str(b.get("text") or "").strip()
        if not text:
            raise ValueError("напиши, что съел")
        ctx, _ = context(uid)
        est = ai.meal(ctx, text)
        if est.get("food") is False:
            raise ValueError(est.get("comment") or "не похоже на еду")
        m = db.save_meal(uid, {**est, "day": today.isoformat(), "time": user_now(u).strftime("%H:%M")})
        return {"meal": m, "comment": est.get("comment", ""), "day": day_payload(uid, today)}
    if path == "/api/ask":
        q = str(b.get("text") or "").strip()[:2000]
        if not q:
            raise ValueError("пустой вопрос")
        ctx, _ = context(uid)
        out = ai.ask(ctx, db.history(uid), q)
        db.say(uid, "user", q)
        db.say(uid, "coach", out)
        return {"answer": out, "history": db.history(uid, 40)}
    if path == "/api/history":
        return {"history": db.history(uid, 40)}
    if path == "/api/history/clear":
        db.clear_history(uid)
        return {"history": []}
    if path == "/api/progress":
        if b.get("make"):
            ctx, _ = insights.progress_context(db, uid, today, profile(uid), whoop.body(db, uid), 60)
            db.brief(uid, today.isoformat(), "progress", ai.to_html(ai.progress(ctx)))
        last = db.q("SELECT day, text FROM briefs WHERE user=? AND kind='progress' ORDER BY day DESC LIMIT 1", (uid,))
        return {"progress": last[0] if last else None}
    if path == "/api/brief":
        ctx, t = context(uid)
        if not t["has_data"]:
            raise ValueError("нет данных WHOOP")
        try:
            text = ai.to_html(ai.morning(ctx))
        except ai.AIError:
            text = rule_brief(t)
        db.brief(uid, today.isoformat(), "morning", text)
        return {"brief": text}
    if path == "/api/sync":
        if not db.token(uid):
            raise ValueError("WHOOP не подключён")
        sync_user(uid, days=7, quiet=True)
        return {"ok": True}
    if path == "/api/connect":
        link = connect_link(uid)
        if not link:
            raise ValueError("сначала ключи WHOOP: напиши боту /connect")
        return {"url": link}
    raise KeyError("нет такого метода")


def serve():
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Web)
    httpd.daemon_threads = True
    print(f"Веб-сервер: http://127.0.0.1:{PORT}")
    httpd.serve_forever()


# ---------- https-адрес ----------
# Telegram открывает приложение, а WHOOP возвращает после входа только на https. Адрес постоянный:
# coach.<ip-через-дефисы>.sslip.io. Если на сервере уже есть Caddy планера (порт 443 занят) — он
# проксирует и этот адрес (см. planner.caddyfile). Если 443 свободен — запускаем свой Caddy.
CADDY_DIR = os.path.join(DATA, "caddy")


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
    global public_url
    old, public_url = public_url, url
    if not set_menu():
        public_url = old
        return False
    if db.meta("app_url") != url:
        db.meta("app_url", url)
        if owner():
            try:
                send(owner(), "⚡ <b>Форма готова!</b>\n\nКнопка «Форма» — слева от поля ввода.",
                     with_app([] if db.token(owner()) else [[{"text": "🔌 Подключить WHOOP", "callback_data": "c|"}]]))
            except Exception as e:
                print("notify:", e)
    return True


def server_ip():
    if os.environ.get("COACH_IP"):
        return os.environ["COACH_IP"]
    for url in ("https://api.ipify.org", "https://ipv4.icanhazip.com", "https://ifconfig.me/ip"):
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                ip = r.read().decode().strip()
            if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", ip):
                return ip
        except Exception:
            pass
    return None


def host_for(ip):
    return "coach." + ip.replace(".", "-") + ".sslip.io"


def port_busy(port):
    with socket.socket() as s:
        s.settimeout(2)
        return s.connect_ex(("127.0.0.1", port)) == 0


def caddy_bin():
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


def own_caddy(host):
    conf = (f"{{\n\tstorage file_system {CADDY_DIR}/data\n\tadmin off\n}}\n"
            f"https://{host} {{\n\treverse_proxy 127.0.0.1:{PORT}\n}}\n")
    os.makedirs(CADDY_DIR, exist_ok=True)
    path = os.path.join(CADDY_DIR, "Caddyfile")
    with open(path, "w") as f:
        f.write(conf)
    proc = subprocess.Popen([caddy_bin(), "run", "--config", path, "--adapter", "caddyfile"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

    def log():
        for line in proc.stderr:
            if '"level":"error"' in line or "certificate obtained" in line:
                print("caddy:", line.strip()[:400])
    threading.Thread(target=log, daemon=True).start()
    return proc


def https():
    global https_note
    ip = None
    while not ip:
        ip = server_ip()
        if not ip:
            https_note = "не узнал IP сервера"
            time.sleep(60)
    url = "https://" + host_for(ip)
    proc = None
    started = time.time()
    while True:
        if healthy(url):
            if public_url != url:
                use_url(url)
            https_note = "работает" + (" (свой Caddy)" if proc else "")
            time.sleep(300)
            continue
        if proc is None or proc.poll() is not None:
            if port_busy(443) and proc is None:
                https_note = ("порт 443 занят (Caddy планера?) — нужен перезапуск планера с новым кодом: "
                              "/update в планере или pm2 restart planner-bot")
            else:
                try:
                    proc = own_caddy(host_for(ip))
                    https_note = "Caddy получает сертификат"
                except Exception as e:
                    https_note = f"Caddy не запустился: {e}"
        elif time.time() - started > 600:
            https_note = "Caddy запущен, но адрес не открывается (открыты ли порты 80/443?)"
        time.sleep(20)


VERSION = asset_version() if os.path.isdir(WEB) else "0"


def main():
    global offset
    if not TOKEN:
        sys.exit("Нет COACH_TOKEN (токен от @BotFather). Впиши его в /etc/coach-bot.env")
    threading.Thread(target=serve, daemon=True).start()
    try:
        db.meta("bot_username", tg("getMe")["result"].get("username") or "")
    except Exception as e:
        print("getMe:", e)
    setup_bot()
    if public_url:
        set_menu()
    else:
        threading.Thread(target=https, daemon=True).start()
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
