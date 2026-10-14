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
import functools
import base64
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
import water  # noqa: E402
import whoop  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(HERE), "common"))
import media  # noqa: E402  голосовые -> текст (общий помощник)
import support  # noqa: E402  🛠 поддержка: /fix — Claude чинит и дорабатывает этого бота

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
SUP = None  # 🛠 поддержка (common/support.py) — ставит main(), ей же пользуется приложение
offset = 0
https_note = "запускается"
busy = set()  # пользователи, для которых сейчас думает Claude


def esc(x):
    return html.escape(str(x), quote=False)


# ---------- оформление сообщений ----------
# Один стиль на все сообщения: первая строка — короткий жирный заголовок с одним эмодзи, суть — в цитате
# (длинное — в раскрывающейся), второстепенное — одной строкой курсивом в конце. Ошибки — одна строка «⚠️ …».
# Колонки пробелами не выравниваем (шрифт в Telegram пропорциональный) — только строки «• …» и « · ».

def quote(body, expand=None):
    body = (body or "").strip()
    if not body:
        return ""
    if expand is None:
        expand = len(body) > 700 or body.count("\n") >= 10
    return f"<blockquote{' expandable' if expand else ''}>{body}</blockquote>"


def box(icon, title, body=None, note="", expand=None):
    """Сообщение в общем стиле. body — строка или список строк (каждая — своя цитата); всё уже в HTML."""
    parts = [f"{icon} <b>{title}</b>" if icon else f"<b>{title}</b>"]
    for b in [body] if isinstance(body, str) else body or []:
        q = quote(b, expand)
        if q:
            parts.append(q)
    if note:
        parts.append(f"<i>{note}</i>")
    return "\n".join(parts)


def err(text):
    return f"⚠️ {text}"


BUSY = "⏳ Ещё думаю над прошлым сообщением — секунду."
OOPS = "Что-то пошло не так — попробуй ещё раз. Не проходит — /fix, починю."


def bg(target, *args, chat=None):
    """Долгое дело в фоне: упало — в чат понятное «⚠️ …», а не тишина (трассировка — только в лог)."""
    @functools.wraps(target)  # имя дела в логах и тестах — как у самой функции
    def run(*a):
        try:
            target(*a)
        except Exception:
            traceback.print_exc()
            if chat:
                try:
                    send(chat, err(OOPS))
                except Exception as e:
                    print("oops:", e)
    threading.Thread(target=run, args=args, daemon=True).start()


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
        e.detail = detail
        raise


STYLES = {"ok": True}  # Telegram принимает цветные кнопки; False — после первого отказа шлём без цвета


def plain(buttons):
    return [[{k: v for k, v in b.items() if k != "style"} for b in row] for row in buttons]


def tg_kb(method, buttons, **params):
    """Запрос с кнопками. Цвет кнопки (style: success | danger | primary) — новинка Bot API: если Telegram
    ответит 400 про кнопки или разметку — те же кнопки без цвета."""
    if buttons is None:
        return tg(method, **params)
    styled = any("style" in b for row in buttons for b in row)
    if styled and not STYLES["ok"]:
        buttons, styled = plain(buttons), False
    try:
        return tg(method, reply_markup={"inline_keyboard": buttons}, **params)
    except urllib.error.HTTPError as e:
        detail = (getattr(e, "detail", "") or "").lower()
        if not styled or e.code != 400 or not any(w in detail for w in ("button", "markup", "keyboard", "style")):
            raise
        res = tg(method, reply_markup={"inline_keyboard": plain(buttons)}, **params)
        STYLES["ok"] = False
        return res


def send(chat, text, buttons=None, **extra):
    return tg_kb("sendMessage", buttons, chat_id=chat, text=text, parse_mode="HTML",
                 link_preview_options={"is_disabled": True}, **extra)


def edit(chat, msg_id, text, buttons=None):
    return tg_kb("editMessageText", buttons, chat_id=chat, message_id=msg_id, text=text, parse_mode="HTML",
                 link_preview_options={"is_disabled": True})


def mid_of(res):
    r = (res or {}).get("result")
    return r.get("message_id") if isinstance(r, dict) else None


def replace(chat, mid, text, buttons=None):
    """Правка сообщения на месте; нет id или правка не прошла — новое сообщение."""
    if mid:
        try:
            return edit(chat, mid, text, buttons)
        except Exception as e:
            print("edit:", e)
    return send(chat, text, buttons)


def typing(chat):
    try:
        tg("sendChatAction", chat_id=chat, action="typing")
    except Exception:
        pass


def btn(text, data, style=None):
    b = {"text": text, "callback_data": data}
    if style:
        b["style"] = style
    return b


def kb(*rows):
    """Ряды кнопок без пустых мест (кнопки приложения нет, пока нет адреса)."""
    out = [[b for b in row if b] for row in rows]
    return [r for r in out if r]


def app_button(uid, text="📱 Открыть", path="", style=None, always=False):
    """Кнопка приложения (web_app). В режиме «💬 чат» её нет — только по явному /app (always=True)."""
    if not public_url or (ui_mode(uid) == "chat" and not always):
        return None
    b = {"text": text, "web_app": {"url": public_url + "/" + (f"?p={urllib.parse.quote(path)}" if path else "")}}
    if style:
        b["style"] = style
    return b


def with_app(uid, rows, text="📱 Открыть", path="", style=None, always=False):
    return kb(*rows, [app_button(uid, text, path, style, always)])


def setup_bot():
    # в меню — только частые команды; остальные (/app, /connect, /status, /new) работают, как раньше
    commands = [
        {"command": "app", "description": "📱 Приложение"},
        {"command": "mode", "description": "💬 Чат или 📱 приложение"},
        {"command": "today", "description": "Разбор дня"},
        {"command": "profile", "description": "Профиль"},
        {"command": "water", "description": "Вода"},
        {"command": "food", "description": "Питание"},
        {"command": "goals", "description": "Цели"},
        {"command": "progress", "description": "Прогресс за 2 месяца"},
        {"command": "morning", "description": "Время утреннего разбора"},
        {"command": "sync", "description": "Обновить WHOOP"},
        {"command": "new", "description": "Новый разговор"},
        {"command": "help", "description": "Что я умею"},
    ]
    desc = ("Личный тренер по данным WHOOP: восстановление, сон, нагрузка, питание.\n\n"
            "Утром — короткий план на день, после тренировки — что съесть. Фото или видео еды — ккал и БЖУ, "
            "видео упражнения — разбор техники, голосом — как текстом.")
    short = "Тренер, врач и нутрициолог по данным WHOOP."
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


# ---------- режим: 💬 чат или 📱 приложение ----------
# Выбор при первом /start, потом — /mode или кнопка «⚙️ Режим». «Чат» — всё кнопками и командами здесь, кнопок
# web_app в сообщениях нет (кнопка «Форма» слева от поля ввода остаётся — её ставит Telegram). «Приложение» —
# /start и ответы короткие, главная кнопка — открыть приложение; чат всё равно отвечает на команды и текст.

MODES = {"chat": "💬 чат", "app": "📱 приложение"}


def ui_mode(uid):
    """'chat' | 'app' | '' (ещё не выбирал: /start предложит выбор, кнопки приложения пока как раньше)."""
    m = db.meta(f"mode:{uid}")
    return m if m in MODES else ""


def set_mode(uid, m):
    if m not in MODES:
        raise ValueError("Режим — chat или app")
    db.meta(f"mode:{uid}", m)
    return m


def mode_note(uid):
    """Подпись режима для карточки /start (одной строкой, курсивом — box() сам ставит <i>)."""
    return f"Режим: {MODES.get(ui_mode(uid), 'не выбран')} · сменить — /mode"


def mode_card():
    return (box("🧭", "Как удобнее?", ["💬 <b>В чате</b> — всё кнопками и командами здесь",
                                        "📱 <b>Приложение</b> — кольца, тренды, питание и тренер экранами"],
                "Поменять можно в любой момент: /mode"),
            kb([btn("💬 В чате", "mode|chat", "primary"), btn("📱 Приложение", "mode|app", "primary")]))


def ask_mode(chat):
    send(chat, *mode_card())


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


DEV_DASHBOARD = "https://developer-dashboard.whoop.com/apps/create"


def setup_text():
    url = redirect_url() or "(адрес приложения ещё не готов — /status)"
    steps = ("1. Открой developer-dashboard.whoop.com (кнопка ниже) и войди аккаунтом WHOOP.\n"
             "2. Создай Team (любое имя), затем <b>Create App</b>: имя <code>Forma</code>, почта в Contacts.\n"
             "3. Privacy Policy:\n<code>https://sawqws.github.io/123/privacy.html</code>\n"
             f"4. Redirect URL (и нажми ADD):\n<code>{esc(url)}</code>\n"
             "5. Scopes: отметь все 6 <code>read:*</code>.\n"
             "6. Create App — и пришли сюда одной строкой:\n<code>/whoop CLIENT_ID CLIENT_SECRET</code>")
    return box("🔌", "Подключение WHOOP · 3 минуты", steps, expand=False,
               note="Это ключ доступа, а не новое приложение. Секрет храню на сервере, сообщение с ним удалю.")


def connect(chat, uid):
    cid, sec = creds()
    if not (cid and sec):
        send(chat, setup_text(), [[{"text": "🌐 WHOOP Developer", "url": DEV_DASHBOARD}]])
        return
    link = connect_link(uid)
    if not link:
        send(chat, err("Адрес приложения ещё не готов — повтори /connect через минуту (/status)."))
        return
    send(chat, box("🔗", "Подключение WHOOP", "Нажми кнопку, войди в WHOOP и разреши доступ — потом вернись сюда."),
         [[{"text": "Подключить WHOOP", "url": link, "style": "success"}]])


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
            send(uid, err("WHOOP не принял вход — вот новая кнопка, попробуй ещё раз."))
            connect(uid, uid)
        except Exception as e2:
            print("reconnect:", e2)
        return False, "WHOOP не принял вход. Новая кнопка уже в Telegram."
    threading.Thread(target=first_sync, args=(uid,), daemon=True).start()
    return True, "WHOOP подключён. Возвращайся в Telegram."


def first_sync(uid):
    try:
        mid = mid_of(send(uid, box("✅", "WHOOP подключён", "Загружаю данные за 2 месяца — минуту…")))
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
        replace(uid, mid, box("✅", "WHOOP подключён", f"Загружено записей: <b>{n}</b>. Утром пришлю первый разбор.",
                              note="Добавь в профиле возраст, спорт и цель — советы будут точнее."),
                kb([btn("🧠 Разбор сейчас", "b|now", "success"),
                    app_button(uid, "👤 Профиль", "/profile") or btn("👤 Профиль", "pf|")]))
    except Exception as e:
        traceback.print_exc()
        send(uid, err("Не получилось загрузить данные WHOOP — повтори через пару минут."), kb([btn("🔄 Загрузить", "s|")]))


def sync_user(uid, days=3, quiet=False, notify=True):
    cid, sec = creds()
    try:
        fresh = whoop.sync(db, uid, cid, sec, days=days)
    except whoop.NotConnected:
        db.drop_token(uid)
        if notify:
            send(uid, err("WHOOP отключился — вход истёк. Подключи заново: /connect"))
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


def fmt(x, n=1):
    return "—" if x is None else num(x, n)


def headline(t):
    """Цифры утра: восстановление, ВСР и пульс покоя против нормы, сон, нагрузка вчера (строки для цитаты)."""
    r, s, b, y = t.get("recovery"), t.get("sleep"), t.get("base") or {}, t.get("yesterday") or {}
    lines = []
    if r:
        line = f"{ZONE_ICON[r['zone']]} Восстановление <b>{r['score']}%</b>"
        if r.get("hrv"):
            line += f" · ВСР {round(r['hrv'])}" + (f" (норма {round(b['hrv'])})" if b.get("hrv") else "")
        if r.get("rhr"):
            line += f" · пульс покоя {r['rhr']}"
        lines.append(line)
    if s:
        lines.append(f"😴 Сон <b>{hm(s['hours'])}</b>" + (f" из {hm(s['need'])}" if s.get("need") else "")
                     + (f" · {s['performance']}%" if s.get("performance") is not None else ""))
    if y.get("strain") is not None:
        ws = ", ".join(f"{w['sport'].lower()} {w['minutes']} мин" for w in (y.get("workouts") or [])[:2] if w.get("minutes"))
        lines.append(f"⚡ Нагрузка вчера <b>{num(y['strain'])}</b>" + (f" · {esc(ws)}" if ws else ""))
    return "\n".join(lines)


def hm(h):
    """8.17 -> «8:10» (часы:минуты — короче, чем «8 ч 10 мин»)."""
    if h is None:
        return "—"
    m = round(h * 60)
    return f"{m // 60}:{m % 60:02d}"


def bed_line(t):
    bt = t.get("bedtime")
    if not bt:
        return ""
    return f"Отбой до <b>{bt['bed']}</b> — подъём в {bt['wake']}."


def first_sentence(text, limit=90):
    s = re.split(r"(?<=[.!?])\s|\s—\s", text.strip(), maxsplit=1)[0].rstrip(".")
    return s if len(s) <= limit else s[:limit].rsplit(" ", 1)[0] + "…"


def rule_brief(t):
    """Разбор без Claude (3–4 строки): главный сигнал, ориентир нагрузки, еда и вода, отбой."""
    lines = [f"• {esc(first_sentence(x['text']))}" for x in t["tips"][:1]]
    if t["band"]:
        lines.append(f"• Ориентир нагрузки: <b>{t['band'][0]}–{t['band'][1]}</b>")
    n = t["nutrition"]
    if n:
        lines.append(f"• {n['kcal']} ккал · белок {n['protein']} г · вода {water.liters(n['water'])}")
    if bed_line(t):
        lines.append("• " + bed_line(t))
    return "\n".join(lines) or "Данных за сегодня пока нет."


def rule_early(t):
    """План без сегодняшнего сна (WHOOP ещё не посчитал) — по вчерашнему дню."""
    y = t.get("yesterday") or {}
    hard = (y.get("strain") or 0) >= 14
    lines = ["• Тренировка по самочувствию" + (": вчера было тяжело — не форсируй" if hard else ", разминка 15 мин")]
    n = t["nutrition"]
    lines.append(f"• Вода {water.liters(n['water'])}, белок {n['protein']} г" if n else "• Вода 2–2,5 л, белок в каждый приём")
    if bed_line(t):
        lines.append("• " + bed_line(t))
    return "\n".join(lines)


def short(md, lines=5, chars=600):
    """Ответ Claude для чата: первые строки в HTML и обрезано ли (полный — в приложении)."""
    text, cut = ai.clip(md, lines, chars)
    return ai.to_html(text), cut


def progress_report(uid, chat=None, week=False):
    """Разбор прогресса за 60 дней (или итог недели — week=True). В чат — только итог, весь разбор — в приложении."""
    chat = chat or uid
    if uid in busy:
        send(chat, BUSY)
        return
    busy.add(uid)
    try:
        typing(chat)
        day = today_of(uid)
        ctx, p = insights.progress_context(db, uid, day, profile(uid), whoop.body(db, uid), 28 if week else 60)
        if not any(w["days"] for w in p["weeks"]):
            send(chat, err("Данных WHOOP пока нет — подключи браслет: /connect"))
            return
        head = ("📅", "Итог недели") if week else ("📈", "Прогресс · 2 месяца")
        mid = None if week else mid_of(send(chat, box(*head, "Разбираю все данные — минута-две…")))
        try:
            md = ai.progress(ctx, week=week)
        except ai.AIError as e:
            replace(chat, mid, err(f"Разбор не получился: {esc(e)}. Ещё раз: /progress"))
            return
        db.brief(uid, day.isoformat(), "week" if week else "progress", ai.to_html(md))
        body, _ = short(md, 5, 600)
        replace(chat, mid, box(*head, body), kb([app_button(uid, "📱 Весь разбор", "/trends", "primary")
                                                  or btn("📄 Весь разбор", f"full|{'week' if week else 'progress'}"),
                                                  btn("💬 Спросить", "q|")]))
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
            send(chat, err("Данных WHOOP пока нет — подключи браслет: /connect"))
            return True
        note = ""
        try:
            md = ai.morning(text)
            full = ai.to_html(md)
            body, _ = short(md, 4, 420)
        except ai.AIError as e:
            full = body = rule_brief(t)
            note = f"Claude недоступен: {esc(e)}"
        db.brief(uid, t["day"], "morning", full)
        send(chat, box("☀️", "Доброе утро", [headline(t), body], note=note, expand=False),
             kb([app_button(uid, "📱 Открыть", style="primary"), btn("💬 Спросить", "q|")]))
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
            md = ai.morning_early(text)
            full = ai.to_html(md)
            body, _ = short(md, 3, 320)
        except ai.AIError:
            full = body = rule_early(t)
        db.brief(uid, t["day"], "morning", full)
        y = headline({"yesterday": t.get("yesterday")})
        send(uid, box("☀️", "Доброе утро", [y, body], note="WHOOP ещё не посчитал сон — разбор пришлю позже.",
                      expand=False),
             kb([btn("🔄 Обновить", "s|"), app_button(uid, "📱 Открыть")]))
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
        return box("☀️", "Утренний разбор выключен", "Включить — выбери время кнопкой ниже.",
                   note="Или командой: /morning 07:00")
    late = (datetime.datetime.combine(datetime.date.today(), at) + MORNING_WAIT).strftime("%H:%M")
    return box("☀️", "Утренний разбор",
               f"Каждый день с <b>{at.strftime('%H:%M')}</b> — как только WHOOP посчитает сон.\n"
               f"Не посчитал к {late} — пришлю короткий план без сна.",
               note="Своё время: /morning 08:30 · выключить: /morning off")


def morning_buttons(u):
    cur = u["profile"].get("morning") or store.MORNING_DEFAULT
    times = ["06:30", "07:00", "07:30", "08:00", "09:00"]
    row = [btn(("✓ " if cur == x else "") + x, f"mt|{x}") for x in times]
    return [row[:3], row[3:] + [btn(("✓ " if cur == "off" else "") + "Выкл", "mt|off")]]


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


def sport_icon(sport):
    s = (sport or "").lower()
    if re.search(r"бокс|кик|единобор|мма|борьб|джиу", s):
        return "🥊"
    if re.search(r"бег|ходьб|поход", s):
        return "🏃"
    if re.search(r"силов|пауэр|функц", s):
        return "🏋️"
    return "⚡"


def workout_note(uid, w):
    typing(uid)
    text, t = context(uid)
    try:
        md = ai.after_workout(text, w)
        full = ai.to_html(md)
        body, _ = short(md, 3, 330)
    except ai.AIError:
        full = body = ("• Вода 500–700 мл за час\n• Белок 25–35 г и углеводы в ближайшие 1–2 часа\n"
                       "• Вечером без экранов за 30 мин до сна")
    db.brief(uid, t["day"], f"workout:{w['id']}", full)
    stats = f"Нагрузка <b>{fmt(w['strain'])}</b> · пульс {w['avg_hr'] or '—'}/{w['max_hr'] or '—'}" \
        + (f" · {w['kcal']} ккал" if w.get("kcal") else "")
    title = esc(w["sport"]) + (f" · {w['minutes']} мин" if w.get("minutes") else "")
    send(uid, box(sport_icon(w["sport"]), title, [stats, body], expand=False), with_app(uid, [], "📱 Открыть"))


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
    lines = [f"Съедено <b>{e['kcal']}</b> из {n['kcal']} ккал · белок {e['protein']}/{n['protein']} г"]
    if lack_p >= 20:
        lines.append(f"• +{lack_p} г белка: творог, яйца, курица или кефир")
    elif lack_k >= 300:
        lines.append(f"• +{lack_k} ккал — нормальный ужин, не пропускай")
    send(uid, box("🍽", "Итог питания", "\n".join(lines)), with_app(uid, [], "📱 Питание", "/food"))


# ---------- чат ----------

def help_text(uid):
    return box("⚡", "Форма — тренер по WHOOP", [
        "☀️ Утром — план на день, после тренировки — что съесть\n"
        "💬 Вопрос — текстом или голосом\n"
        "🍽 Еда — фото, видео или «съел гречку»\n"
        "🎥 Видео упражнения — разбор техники\n"
        "💧 Вода — «выпил 500 мл»",
        "/today · /food · /water · /goals · /profile · /progress · /morning · /sync · /new · /status · /mode"],
        note=("Всё подробно — в приложении: кнопка «Форма» слева от поля ввода." if ui_mode(uid) == "app"
              else "Всё — кнопками и командами здесь; приложение — /app или /mode.") + f"\n{mode_note(uid)}")


FOOD_WORDS = re.compile(r"^(съел[аи]?|поел[аи]?|ел[аи]?|выпил[аи]?|завтрак|обед|ужин|перекус|еда)\b[:\s,-]*", re.I)


def main_menu(uid):
    """Главное меню чата. «📱 приложение» — открыть и 2–3 главных действия; «💬 чат» — все функции кнопками."""
    connected = bool(db.token(uid))
    conn = [btn("🔌 Подключить WHOOP", "c|", "success")] if not connected else []
    if ui_mode(uid) == "app":
        return kb(conn, [app_button(uid, "📱 Открыть", style="primary" if connected else None)],
                  [btn("🧠 Разбор дня", "b|now"), btn("💧 Вода", "water|")],
                  [btn("⚙️ Режим", "mode|ask"), support.Support.button()])
    return kb(conn, [btn("🧠 Разбор дня", "b|now", "success" if connected else None), btn("📈 Прогресс", "p|")],
              [btn("🍽 Питание", "food|"), btn("💧 Вода", "water|"), btn("🎯 Цели", "goals|")],
              [btn("📊 Сводка", "sum|week"), btn("👤 Профиль", "pf|"), btn("☀️ Утро", "morning|")],
              [btn("⚙️ Режим", "mode|ask"), support.Support.button()])


def welcome(chat, uid, name):
    connected = bool(db.token(uid))
    hint = ("Всё главное — в приложении." if ui_mode(uid) == "app" else "Всё — кнопками ниже и командами.") \
        if connected else "Сначала подключим WHOOP — 3 минуты."
    send(chat, box("👋", f"Привет{', ' + esc(name) if name else ''}!",
                   "Я <b>Форма</b> — тренер по данным WHOOP: как сегодня тренироваться, есть и спать.",
                   note=f"{hint}\n{mode_note(uid)}"),
         main_menu(uid))


def food_button(uid):
    """«Питание»: в режиме «приложение» — экран еды, в чате — карточка /food сюда."""
    return app_button(uid, "📱 Питание", "/food") or btn("🍽 Питание", "food|")


def more_button(uid, kind, path="/coach"):
    """«Полностью»: в режиме «приложение» — открыть приложение, в чате — прислать весь текст сюда."""
    return app_button(uid, "📱 Полностью", path) or btn("📄 Полностью", f"full|{kind}")


def send_long(chat, icon, title, html_text, limit=3500):
    """Длинный HTML-текст — несколькими сообщениями, по строкам."""
    parts, cur = [], ""
    for line in html_text.split("\n"):
        if cur and len(cur) + len(line) + 1 > limit:
            parts.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    parts.append(cur)
    for i, p in enumerate(parts):
        send(chat, box(icon, title if not i else f"{title} · {i + 1}", p, expand=False))


def send_full(chat, uid, kind):
    """Полный текст в чат (режим «чат»): последний ответ тренера или сохранённый разбор (progress, week, sum-…)."""
    if kind == "answer":
        h = [m for m in db.history(uid, 40) if m["role"] == "coach"]
        text, icon, title = (ai.to_html(h[-1]["text"]) if h else ""), "💬", "Тренер · полностью"
    else:
        r = db.q("SELECT text FROM briefs WHERE user=? AND kind=? ORDER BY day DESC LIMIT 1", (uid, kind))
        text = r[0]["text"] if r else ""
        if kind.startswith("sum-"):
            text = ai.to_html(text)
        icon, title = ("📊", "Сводка · полностью") if kind.startswith("sum-") else ("📈", "Разбор · полностью")
    if not text.strip():
        send(chat, err("Пока нечего показать"))
        return
    send_long(chat, icon, title, text)


PERIODS = [("day", "День"), ("week", "Неделя"), ("month", "Месяц"), ("all", "Всё время")]


def summary_report(chat, uid, period="week"):
    """«📊 Сводка и советы» в чате: цифры за период против прошлого и советы Claude (кэш на день, как в приложении)."""
    if period not in dict(PERIODS):
        period = "week"
    if uid in busy:
        send(chat, BUSY)
        return
    busy.add(uid)
    try:
        typing(chat)
        today = today_of(uid)
        s = insights.summary(db, uid, today, period)
        if not s["stats"]["days"] and not (s.get("day") or {}).get("cycle"):
            send(chat, err("За этот период нет данных WHOOP — подключи браслет: /connect"))
            return
        kind = f"sum-{period}"
        cached = db.brief(uid, today.isoformat(), kind)
        if cached:
            text = cached["text"]
        else:
            ctx = insights.summary_context(s, profile(uid), whoop.body(db, uid))
            try:
                text = ai.summary(ctx, period)
            except ai.AIError as e:
                send(chat, err(f"Сводка не получилась: {esc(e)}"))
                return
            db.brief(uid, today.isoformat(), kind, text)
        body, cut = short(text, 8, 900)
        rows = [btn(("✓ " if k == period else "") + w, f"sum|{k}") for k, w in PERIODS]
        send(chat, box("📊", f"Сводка · {dict(PERIODS)[period].lower()}", body, expand=False),
             kb(rows[:2], rows[2:], [app_button(uid, "📱 Подробнее", "/summary")
                                     or (btn("📄 Полностью", f"full|{kind}") if cut else None)]))
    finally:
        busy.discard(uid)


# профиль в чате: посмотреть и поменять словами («вес 61, рост 170, спорт бокс»); подробная форма — в приложении
PROFILE_LABELS = [("age", "Возраст"), ("sex", "Пол"), ("height", "Рост, см"), ("weight", "Вес, кг"), ("goal", "Цель"),
                  ("sport", "Спорт"), ("schedule", "Расписание"), ("diet", "Еда"), ("notes", "Тренеру важно"),
                  ("morning", "Утренний разбор")]
PROFILE_WORDS = {"возраст": "age", "лет": "age", "рост": "height", "вес": "weight", "пол": "sex", "цель": "goal",
                 "спорт": "sport", "расписание": "schedule", "тренировки": "schedule", "еда": "diet", "ем": "diet",
                 "питание": "diet", "тренеру": "notes", "заметки": "notes", "важно": "notes", "утро": "morning",
                 "разбор": "morning"}
GOAL_WORDS = {"form": "форма", "mass": "масса", "dry": "рельеф", "weight": "вес к соревнованиям"}


def profile_view(uid, title="Профиль"):
    p = profile(uid)
    lines = []
    for k, label in PROFILE_LABELS:
        v = p.get(k)
        if v in (None, ""):
            continue
        if k == "sex":
            v = {"m": "парень", "f": "девушка"}.get(v, v)
        elif k == "goal":
            v = GOAL_WORDS.get(v, v)
        elif k == "morning" and v == "off":
            v = "выключен"
        lines.append(f"• {label} — {esc(v)}")
    return (box("👤", title, "\n".join(lines) or "Пока пусто — расскажи о себе, советы станут точнее.",
                note="Изменить — кнопкой: «вес 61, рост 170», «спорт бокс», «цель масса», «пол парень»…"),
            kb([btn("✏️ Изменить", "pf|edit"), btn("🎯 Цели", "goals|")]))


def parse_profile(text):
    """«вес 61, рост 170, спорт бокс, 3 раза в неделю» → поля профиля; части — через запятую или с новой строки."""
    out = {}
    for part in re.split(r"[;\n]+|,(?!\d)", text or ""):  # запятая внутри числа («61,5») — не разделитель
        m = re.match(r"\s*([^\s:—-]+)\s*[:—-]?\s*(.+?)\s*$", part)
        if not m:
            continue
        k = PROFILE_WORDS.get(m.group(1).lower().rstrip(".:"))
        v = m.group(2)
        if not k:
            continue
        if k in ("age", "height", "weight"):
            n = re.search(r"\d+(?:[.,]\d+)?", v)
            if not n:
                continue
            v = n.group(0)
        elif k == "sex":
            v = "f" if v.lower().startswith(("д", "ж")) else "m"
        elif k == "goal":
            v = next((code for code, w in GOAL_WORDS.items() if v.lower().startswith(w[:3])), v)
        elif k == "morning":
            v = "off" if v.lower().startswith("вык") else v
        out[k] = v
    return out


def edit_profile(chat, uid, text):
    data = parse_profile(text)
    if not data:
        awaiting(uid, "profile")
        send(chat, err("Не понял. Например: «вес 61, рост 170, спорт бокс» — или /profile"), CANCEL)
        return
    try:
        db.save_profile(uid, data)
    except ValueError as e:
        awaiting(uid, "profile")
        send(chat, err(f"{esc(e)}. Попробуй ещё раз"), CANCEL)
        return
    send(chat, *profile_view(uid, "Профиль обновлён"))


def meal_card(m, comment="", total=""):
    body = f"<b>{round(m['kcal'])} ккал</b> · Б {round(m['protein'])} · Ж {round(m['fat'])} · У {round(m['carbs'])}"
    return box("🍽", esc(m["title"]), body + (f"\n{total}" if total else ""), note=esc(first_sentence(comment, 100)))


def food_text(uid):
    _, t = context(uid)
    n, e = t["nutrition"], t["eaten"]
    if n:
        top = (f"<b>{e['kcal']}</b> из {n['kcal']} ккал\n"
               f"Б {e['protein']}/{n['protein']} · Ж {e['fat']}/{n['fat']} · У {e['carbs']}/{n['carbs']} г")
    else:
        top = f"<b>{e['kcal']}</b> ккал · добавь вес в профиле — посчитаю норму"
    wt = t["water"]
    top += f"\n💧 {water.day_line(wt['drank'], wt['target'])}"
    meals = t["meals"]
    lines = [f"• {esc(m['time'])} {esc(m['title'])} — {round(m['kcal'])}" for m in meals[-5:]]
    if len(meals) > 5:
        lines.insert(0, f"<i>…ещё {len(meals) - 5}</i>")
    note = "" if meals else "Пришли фото еды или напиши «съел …»."
    return box("🍽", "Питание сегодня", [top, "\n".join(lines)], note=note)


def water_buttons(uid, last_id=None):
    return kb([btn("+250 мл", "wa|add|250"), btn("+500 мл", "wa|add|500")],
              [btn("↩️ Отменить", f"wa|del|{last_id}") if last_id else None, food_button(uid)])


def water_text(uid, added=0, title=None):
    u = db.user(uid)
    now = user_now(u)
    _, t = context(uid)
    wt = t["water"]
    log = "" if added or not wt["log"] else " · ".join(
        f"{esc(w['time'])} {water.liters(w['ml'])}" for w in wt["log"][-4:])
    note = None if added or wt["log"] else "Пиши «500 мл», «стакан воды» или жми кнопки."
    text = water.card(added, wt["drank"], wt["target"], wt["why"], now.hour + now.minute / 60, wt["trained"],
                      title=title, extra=log, note=note)
    return text, (wt["log"][-1]["id"] if wt["log"] else None)


def log_water(chat, uid, ml, mid=None):
    """Записать воду. mid — карточка воды, в которой нажали кнопку: обновим её на месте."""
    now = user_now(db.user(uid))
    row = db.add_water(uid, now.date().isoformat(), now.strftime("%H:%M"), ml)
    text, _ = water_text(uid, ml)
    if mid:
        replace(chat, mid, text, water_buttons(uid, row["id"]))
    else:
        send(chat, text, water_buttons(uid, row["id"]))


PHOTO_ASK = ai.PHOTO_ASK
QUESTION = re.compile(r"^(почему|зачем|что|как|какой|какая|какие|сколько|когда|можно|стоит|нормально|разбери|объясни|"
                      r"оцени|посмотри|глянь|проверь|помоги)\b", re.I)


def save_meal_and_reply(chat, uid, est):
    now = user_now(db.user(uid))
    m = db.save_meal(uid, {**est, "day": now.date().isoformat(), "time": now.strftime("%H:%M")})
    _, t = context(uid)
    n, e = t["nutrition"], t["eaten"]
    total = f"За день {e['kcal']}" + (f" из {n['kcal']} ккал" if n else " ккал")
    send(chat, meal_card(m, est.get("comment", ""), total),
         kb([btn("🗑 Удалить", f"m|del|{m['id']}"), food_button(uid)]))
    return m


def log_meal(chat, uid, text="", photo=None):
    if uid in busy:
        send(chat, BUSY)
        return
    busy.add(uid)
    try:
        typing(chat)
        ctx, _ = context(uid)
        try:
            est = ai.meal(ctx, text, image=photo)
        except ai.AIError as e:
            send(chat, err(f"Не смог оценить еду: {esc(e)}. Добавь вручную в приложении."),
                 kb([food_button(uid)]))
            return
        if est.get("food") is False:
            if photo:  # скрин WHOOP, тренировки, план — это вопрос тренеру с картинкой
                busy.discard(uid)
                answer(chat, uid, text or PHOTO_ASK, photo)
            else:
                send(chat, "🤔 " + esc(first_sentence(est.get("comment") or "Не похоже на еду. Если это вопрос — "
                                                                              "напиши его текстом.", 140)))
            return
        save_meal_and_reply(chat, uid, est)
    finally:
        busy.discard(uid)


def answer(chat, uid, question, photo=None):
    """Вопрос тренеру. В чат — коротко (до 5 строк); целиком ответ остаётся в приложении, во вкладке «Тренер»."""
    if uid in busy:
        send(chat, BUSY)
        return
    busy.add(uid)
    try:
        typing(chat)
        ctx, _ = context(uid)
        hist = db.history(uid)
        try:
            out = ai.claude(ai.chat_prompt(ctx, hist, question), image=photo)
        except ai.AIError as e:
            send(chat, err(f"{esc(e)}."))
            return
        db.say(uid, "user", question if not photo else f"📷 {question}")
        db.say(uid, "coach", out)
        body, cut = short(out, 5, 600)
        send(chat, box("💬", "Тренер", body, expand=False),
             kb([more_button(uid, "answer")]) if cut else None)
    finally:
        busy.discard(uid)


def goals_view(uid, title=None):
    gs = db.goals(uid)
    if gs:
        text = box("🎯", title or "Мои цели", "\n".join(f"{i}. {esc(g['text'])}" for i, g in enumerate(gs, 1)),
                   note="Учитываю их в каждом совете, утром пишу, как день двигает к цели.")
    else:
        text = box("🎯", title or "Цели пока не записаны",
                   "Напиши, к чему идёшь: «ложиться до 23:00», «бокс — соревнования весной», «вес 60 кг к декабрю».",
                   note="Все советы буду привязывать к ним.")
    rows = [[btn("➕ Добавить цель", "g|add", "success")]] if len(gs) < store.MAX_GOALS else []
    dels = [btn(f"🗑 {i}", f"g|del|{g['id']}") for i, g in enumerate(gs, 1)]  # номера — как в списке выше
    rows += [dels[i:i + 3] for i in range(0, len(dels), 3)]
    return text, rows


def add_goal(chat, uid, text):
    try:
        db.add_goal(uid, text)
    except ValueError as e:
        send(chat, err(esc(e)))
        return
    send(chat, *goals_view(uid, "Цель записана"))


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


CANCEL = [[{"text": "✖️ Отмена", "callback_data": "w|cancel"}]]  # под каждым «✍️ Напиши …»


def brief_now(uid, chat):
    """«🧠 Разбор дня» / /today: занят прошлым ответом — так и сказать, а не молчать."""
    if not morning_brief(uid, chat):
        send(chat, BUSY)


def set_morning(chat, uid, value):
    try:
        db.save_profile(uid, {"morning": value})
    except ValueError as e:
        send(chat, err(esc(e)))
        return
    u = db.user(uid)
    send(chat, morning_text(u), morning_buttons(u))


def sync_now(chat, uid, mid):
    """«🔄 Обновить» / /sync: статус в том же сообщении, без вечного «Обновляю…» при ошибке."""
    try:
        sync_user(uid, days=7, notify=False)
    except Exception as e:
        print("sync:", e)
        replace(chat, mid, err("WHOOP сейчас не отвечает — попробуй через пару минут."), kb([btn("🔄 Ещё раз", "s|")]))
        return
    if not db.token(uid):
        replace(chat, mid, err("Вход в WHOOP истёк — подключи заново."), kb([btn("🔌 Подключить WHOOP", "c|", "success")]))
        return
    _, t = context(uid)
    replace(chat, mid, box("✅", "WHOOP обновлён", headline(t)), with_app(uid, [], "📱 Открыть"))


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
    kind = media.kind(msg)
    if cmd == "/start":
        arg = text.split()[1] if len(text.split()) > 1 else ""
        if ui_mode(uid):
            welcome(chat, uid, frm.get("first_name"))
        else:  # первый раз — сначала выбор: 💬 чат или 📱 приложение
            ask_mode(chat)
        if arg == "connect":
            connect(chat, uid)
    elif cmd == "/mode":
        ask_mode(chat)
    elif cmd == "/help":
        send(chat, help_text(uid), with_app(uid, [[support.Support.button()]]))
    elif cmd == "/profile":
        if arg:
            edit_profile(chat, uid, arg)
        else:
            send(chat, *profile_view(uid))
    elif cmd == "/app":
        if public_url:
            send(chat, box("📱", "Форма", note="Сегодня, тренды, питание и тренер."), with_app(uid, [], style="primary", always=True))
        else:
            send(chat, "⏳ Приложение ещё запускается — пришлю кнопку, как будет готово. Что с адресом — /status")
    elif cmd == "/connect":
        connect(chat, uid)
    elif cmd == "/whoop":
        save_creds(chat, uid, msg, text)
    elif cmd == "/today":
        bg(brief_now, uid, chat, chat=chat)
    elif cmd == "/progress":
        bg(progress_report, uid, chat, chat=chat)
    elif cmd == "/food":
        send(chat, food_text(uid), with_app(uid, [], "📱 Питание", "/food"))
    elif cmd == "/water":
        ml = water.parse(arg) or water.parse("вода " + arg) if arg else None
        if ml:
            log_water(chat, uid, ml)
        else:
            text, last = water_text(uid)
            send(chat, text, water_buttons(uid, last))
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
            mid = mid_of(send(chat, "🔄 Обновляю WHOOP…"))
            bg(sync_now, chat, uid, mid, chat=chat)
    elif cmd == "/new":
        db.clear_history(uid)
        send(chat, box("🧹", "Новый разговор", note="Данные, цели и профиль на месте."))
    elif cmd == "/status":
        bg(status, chat, uid, chat=chat)
    elif cmd == "/update" and is_admin(uid):
        update(chat)
    elif cmd:
        send(chat, err("Такой команды нет — что я умею: /help"))
    elif kind == "photo":  # скачивание и Claude — не в главном цикле
        bg(on_photo, chat, uid, msg, text, chat=chat)
    elif kind == "video":
        bg(on_video, chat, uid, msg, wait, chat=chat)
    elif kind == "voice":
        bg(on_voice, chat, uid, msg, wait, chat=chat)
    elif text:
        on_text(chat, uid, text, wait)
    else:
        send(chat, box("🤷", "Это я не разберу", "Пришли текст, голосовое, фото или видео."))


def on_text(chat, uid, text, wait=None):
    """Обычный текст (или распознанное голосовое): цель после кнопки, вода, еда или вопрос тренеру."""
    if wait == "goal":
        add_goal(chat, uid, text)
        return
    if wait == "profile":
        edit_profile(chat, uid, text)
        return
    ml = water.parse(text)
    if ml:
        log_water(chat, uid, ml)
        return
    m = FOOD_WORDS.match(text)
    if m and len(text) > len(m.group(0)) + 1:
        bg(log_meal, chat, uid, text, chat=chat)
    else:
        bg(answer, chat, uid, text, chat=chat)


def media_failed(chat, uid, what, e, wait=None):
    if isinstance(e, media.MediaError):
        send(chat, err(f"{what}: {esc(e)}. Можно написать текстом."))
    else:
        print(what, e)
        send(chat, err(f"{what}: не получилось скачать — попробуй ещё раз или напиши текстом."))
    if wait:
        awaiting(uid, wait)  # цель всё ещё ждём


def on_voice(chat, uid, msg, wait=None):
    """Голосовое или аудио: распознать (не в главном цикле), показать текст и разобрать как написанное."""
    typing(chat)
    try:
        text = media.voice_text(TOKEN, msg).strip()
    except Exception as e:
        media_failed(chat, uid, "Голосовое", e, wait)
        return
    send(chat, f"🎙 «{esc(text)}»")
    on_text(chat, uid, text, wait)


def on_photo(chat, uid, msg, text=""):
    """Фото: подпись-вопрос — сразу тренеру с картинкой; иначе Claude решает: еда -> ккал, скрин -> короткий разбор."""
    typing(chat)
    try:
        path = media.photo_path(TOKEN, msg, os.path.join(DATA, "photos", str(uid)))
    except Exception as e:
        media_failed(chat, uid, "Фото", e)
        return
    is_question = text.endswith("?") or len(text) > 80 or bool(QUESTION.match(text))
    (answer if is_question else log_meal)(chat, uid, text, path)


def on_video(chat, uid, msg, wait=None):
    """Видео, кружок или гифка: кадры и речь -> Claude. Упражнение — разбор техники, еда — ккал и БЖУ,
    другое — ответ по смыслу. Ждём цель — сказанное в видео становится целью."""
    typing(chat)
    caption = (msg.get("caption") or "").strip()
    folder = os.path.join(DATA, "video", f"{uid}-{int(time.time() * 1000)}")
    try:
        try:
            v = media.video(TOKEN, msg, folder)
        except Exception as e:
            media_failed(chat, uid, "Видео", e, wait)
            return
        speech = (v.get("text") or "").strip()
        if wait and speech:
            send(chat, f"🎙 «{esc(speech)}»")
            on_text(chat, uid, speech, wait)
            return
        if not v.get("frames"):  # кадров нет, только речь — как голосовое
            send(chat, f"🎙 «{esc(speech)}»")
            on_text(chat, uid, speech)
            return
        if uid in busy:
            send(chat, BUSY)
            return
        busy.add(uid)
        try:
            ctx, _ = context(uid)
            try:
                out = ai.video(ctx, v["frames"], speech, caption)
            except ai.AIError as e:
                send(chat, err(f"Не смог разобрать видео: {esc(e)}."))
                return
            if out["kind"] == "food":
                save_meal_and_reply(chat, uid, out)
                return
            q = caption or speech or "Видео"
            db.say(uid, "user", f"🎥 {q}")
            db.say(uid, "coach", out["text"])
            body, cut = short(out["text"], 4, 450)
            title = esc(first_sentence(out.get("title") or "", 40)) if out["kind"] == "exercise" else ""
            send(chat, box("🎥" if out["kind"] == "exercise" else "💬", title or ("Техника" if out["kind"] == "exercise"
                                                                                  else "Тренер"), body, expand=False),
                 kb([more_button(uid, "answer")]) if cut else None)
        finally:
            busy.discard(uid)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def save_creds(chat, uid, msg, text):
    if not is_admin(uid):
        send(chat, err("Это может сделать только владелец бота."))
        return
    parts = text.split()
    try:
        tg("deleteMessage", chat_id=chat, message_id=msg["message_id"])
    except Exception:
        pass
    if len(parts) != 3:
        send(chat, err("Нужно так: <code>/whoop CLIENT_ID CLIENT_SECRET</code>"))
        return
    db.meta("whoop_client_id", parts[1])
    db.meta("whoop_client_secret", parts[2])
    send(chat, box("🔐", "Ключи WHOOP сохранены", note="Храню на сервере, сообщение с ними удалил."))
    connect(chat, uid)


def on_callback(cq):
    uid = cq["from"]["id"]
    msg = cq.get("message") or {}
    chat, mid = msg.get("chat", {}).get("id"), msg.get("message_id")
    data = (cq.get("data") or "").split("|") + ["", "", ""]  # устаревшая/обрезанная кнопка не роняет разбор
    note = ""
    try:
        note = callback(uid, chat, mid, data, cq.get("from") or {})
    except Exception:
        traceback.print_exc()
        note = "⚠️ Не вышло — попробуй ещё раз"
        try:
            send(chat, err(OOPS))
        except Exception as e:
            print("oops:", e)
    try:  # крутилка на кнопке не должна висеть ни в одной ветке
        tg("answerCallbackQuery", callback_query_id=cq["id"], text=note or "")
    except Exception as e:
        print("answerCallbackQuery:", e)


def rid(x):
    """id из кнопки; битый — 0 (такой записи нет → «Уже удалено»)."""
    return int(x) if x.isdigit() else 0


def callback(uid, chat, mid, data, frm):
    """Кнопка → текст всплывашки (пусто — без неё)."""
    note = ""
    if not allowed(uid):
        note = "Нет доступа"
    elif data[0] == "c":
        connect(chat, uid)
    elif data[0] == "b":
        bg(brief_now, uid, chat, chat=chat)
        note = "Готовлю разбор…"
    elif data[0] == "p":
        bg(progress_report, uid, chat, chat=chat)
        note = "Разбираю прогресс…"
    elif data[0] == "q":
        send(chat, "💬 Спроси текстом или голосом.\n<i>Например: «что съесть перед тренировкой?»</i>")
    elif data[0] == "mode":
        if data[1] in MODES:
            set_mode(uid, data[1])
            replace(chat, mid, box("✅", "Режим: " + MODES[data[1]], note="Сменить — /mode или кнопка «⚙️ Режим»"))
            welcome(chat, uid, frm.get("first_name"))
            note = MODES[data[1]]
        else:
            ask_mode(chat)
    elif data[0] == "full":
        send_full(chat, uid, data[1])
    elif data[0] == "food":
        send(chat, food_text(uid), with_app(uid, [], "📱 Питание", "/food"))
    elif data[0] == "water":
        text, last = water_text(uid)
        send(chat, text, water_buttons(uid, last))
    elif data[0] == "goals":
        send(chat, *goals_view(uid))
    elif data[0] == "morning":
        u = db.user(uid)
        send(chat, morning_text(u), morning_buttons(u))
    elif data[0] == "pf":
        if data[1] == "edit":
            awaiting(uid, "profile")
            send(chat, "✍️ Напиши, что поменять, одним сообщением.\n<i>Например: «вес 61, рост 170, спорт бокс».</i>",
                 CANCEL)
        else:
            send(chat, *profile_view(uid))
    elif data[0] == "sum":
        bg(summary_report, chat, uid, data[1] or "week", chat=chat)
        note = "Считаю сводку…"
    elif data[0] == "g" and data[1] == "add":
        awaiting(uid, "goal")
        send(chat, "✍️ Напиши цель одним сообщением.\n<i>Например: «ложиться до 23:00».</i>", CANCEL)
    elif data[0] == "g" and data[1] == "del":
        note = "Удалено" if db.delete_goal(uid, rid(data[2])) else "Уже удалено"
        replace(chat, mid, *goals_view(uid))
    elif data[0] == "mt":
        try:
            db.save_profile(uid, {"morning": data[1]})
            u = db.user(uid)
            replace(chat, mid, morning_text(u), morning_buttons(u))
            note = "Выключено" if data[1] == "off" else f"Разбор с {data[1]}"
        except ValueError as e:
            note = str(e)
    elif data[0] == "s":
        if db.token(uid):
            note = "Обновляю WHOOP…"
            bg(sync_now, chat, uid, mid_of(send(chat, "🔄 Обновляю WHOOP…")), chat=chat)
        else:
            connect(chat, uid)
    elif data[0] == "wa" and data[1] == "add":
        log_water(chat, uid, int(data[2]), mid)
        note = f"+{data[2]} мл"
    elif data[0] == "wa" and data[1] == "del":
        if db.delete_water(uid, rid(data[2])):
            text, last = water_text(uid, title="↩️ <b>Отменил</b>")
            replace(chat, mid, text, water_buttons(uid, last))
            note = "Отменил"
        else:
            note = "Уже отменено"
    elif data[0] == "m" and data[1] == "del":
        note = "Удалено" if db.delete_meal(uid, rid(data[2])) else "Уже удалено"
        replace(chat, mid, "🗑 <s>Удалено</s>")
    elif data[0] == "w" and data[1] == "cancel":  # «✖️ Отмена» под «✍️ Напиши …»
        awaiting(uid)
        replace(chat, mid, "✖️ <i>Отменил</i>")
        note = "Отменил"
    else:
        note = "Кнопка устарела — открой /start"
    return note


def status(chat, uid):
    cid, sec = creds()
    tok = db.token(uid)
    lines = [("✓" if public_url else "✕") + f" Приложение — {esc(https_note)}",
             ("✓" if cid and sec else "✕") + " Ключи WHOOP" + ("" if cid and sec else " — /connect, там инструкция"),
             ("✓" if tok else "✕") + " WHOOP подключён" + (f" · обновлено {esc((tok.get('synced') or '—')[:16].replace('T', ' '))} UTC"
                                                          f" · записей {db.record_count(uid)}" if tok else "")]
    try:
        ai.claude("Ответь одним словом: ok", timeout=90)
        lines.append("✓ Claude отвечает")
    except ai.AIError as e:
        lines.append(f"✕ Claude: {esc(e)}")
    extra = (f"Адрес: {esc(public_url)}\nRedirect URL для WHOOP:\n<code>{esc(redirect_url())}</code>"
             if public_url else "")
    send(chat, box("🩺", "Проверка", ["\n".join(lines), extra]))


def update(chat):
    def git(*a):
        return subprocess.run(["git", *a], cwd=REPO, capture_output=True, text=True)
    res = git("pull", "--ff-only", "origin", "main")
    send(chat, esc((res.stdout + res.stderr).strip()[-1500:] or "ok"))
    if res.returncode == 0:
        send(chat, "🔄 Перезапускаюсь…")
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
<meta name="color-scheme" content="light"><title>Форма</title><style>
body{{margin:0;min-height:100vh;display:grid;place-items:center;padding:24px;box-sizing:border-box;background:#f6f3ee;color:#1f1b17;
font:17px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,system-ui,sans-serif;text-align:center}}
div{{max-width:340px;background:#fff;border:1px solid rgba(70,50,30,.065);border-radius:26px;padding:34px 26px;overflow-wrap:anywhere;
box-shadow:0 1px 1px rgba(70,45,20,.03),0 12px 32px -16px rgba(70,45,20,.16)}}
b{{font-size:44px;display:block;margin-bottom:16px}}a{{display:block;margin-top:24px;padding:14px;border-radius:999px;
background:#c5e9dc;color:#1f1b17;text-decoration:none;font-weight:600}}
</style><div><b>{icon}</b>{text}<a href="{link}">Вернуться в Telegram</a></div>"""


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
            limit = MAX_PHOTO * 4 // 3 + 4096 if path == "/api/photo" else 1_000_000  # фото — base64
            if n > limit:
                return self.reply(413, {"error": "слишком большой файл"})
            body = json.loads(self.rfile.read(min(n, limit)) or b"{}") if n else {}
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
        except PermissionError as e:
            self.reply(403, {"error": str(e)})
        except ai.AIError as e:
            self.reply(503, {"error": str(e)})
        except KeyError as e:
            field = str(e).strip("'")
            self.reply(400 if field != "нет такого метода" else 404,
                       {"error": field if field == "нет такого метода" else f"не хватает поля «{field}»"})
        except ValueError as e:
            self.reply(400, {"error": str(e).strip("'")})
        except Exception:
            traceback.print_exc()
            self.reply(500, {"error": "Что-то сломалось на сервере — попробуй ещё раз. Не проходит — «Поддержка» внизу"})


def day_payload(uid, day):
    t = insights.today(db, uid, day, profile(uid), whoop.body(db, uid))
    b = db.brief(uid, day.isoformat(), "morning")
    t["brief"] = b["text"] if b else None
    now = user_now(db.user(uid))
    if day == now.date():
        wt = t["water"]
        wt["mark"], wt["note"] = water.assess(wt["drank"], wt["target"], now.hour + now.minute / 60, wt["trained"])
    return t


MAX_PHOTO = 8 * 1024 * 1024


def photo_api(uid, b):
    """Фото из приложения (base64 или data:URL). mode="food" — оценить еду и записать;
    mode="ask" — вопрос тренеру с картинкой (скрин тренировки, показателей)."""
    data = str(b.get("image") or "")
    m = re.match(r"data:image/([\w.+-]+);base64,", data)
    try:
        raw = base64.b64decode(data[m.end():] if m else data, validate=True)
    except ValueError:
        raise ValueError("не получилось прочитать фото") from None
    if not raw:
        raise ValueError("нет фото")
    if len(raw) > MAX_PHOTO:
        raise ValueError("фото больше 8 МБ")
    ext = {"png": ".png", "webp": ".webp", "heic": ".heic"}.get((m.group(1) if m else "").lower(), ".jpg")
    folder = os.path.join(DATA, "photos", str(uid))
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"app-{int(time.time() * 1000)}{ext}")
    with open(path, "wb") as f:
        f.write(raw)
    text = str(b.get("text") or "").strip()[:500]
    ctx, _ = context(uid)
    u = db.user(uid)
    if b.get("mode") == "ask":
        out = ai.claude(ai.chat_prompt(ctx, db.history(uid), text or PHOTO_ASK), image=path)
        db.say(uid, "user", f"📷 {text}" if text else "📷 Фото")
        db.say(uid, "coach", out)
        return {"answer": out, "history": db.history(uid, 40)}
    est = ai.meal(ctx, text, image=path)
    if est.get("food") is False:
        raise ValueError(first_sentence(est.get("comment") or "не похоже на еду", 140)
                         + " — такое фото пришли во вкладке «Тренер»")
    today = user_now(u).date()
    meal = db.save_meal(uid, {**est, "day": today.isoformat(), "time": user_now(u).strftime("%H:%M")})
    return {"meal": meal, "comment": est.get("comment", ""), "day": day_payload(uid, today)}


def route(uid, path, b):
    if path == "/api/support":  # 🛠 из приложения — та же поддержка, что /fix в чате
        if not SUP:
            raise ValueError("Поддержка ещё запускается — попробуй через минуту")
        return SUP.web(uid, b)
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
            "mode": ui_mode(uid),
        }
    if path == "/api/mode":  # «💬 Перейти в чат» из приложения: режим «чат» и главное меню — в чат
        m = set_mode(uid, str(b.get("mode") or ""))
        if m == "chat":
            welcome(uid, uid, u["name"])
        return {"mode": m}
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
    if path == "/api/water":
        now = user_now(u)
        db.add_water(uid, today.isoformat(), now.strftime("%H:%M"), int(b.get("ml") or 0))
        return {"day": day_payload(uid, today)}
    if path == "/api/water/delete":
        db.delete_water(uid, int(b["id"]))
        return {"day": day_payload(uid, today)}
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
    if path == "/api/photo":
        return photo_api(uid, b)
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
    if path == "/api/summary":
        period = str(b.get("period") or "week")
        s = insights.summary(db, uid, today, period)
        kind = f"sum-{period}"
        if b.get("make"):
            if not s["stats"]["days"] and not (s.get("day") or {}).get("cycle"):
                raise ValueError("за этот период нет данных WHOOP")
            ctx = insights.summary_context(s, profile(uid), whoop.body(db, uid))
            db.brief(uid, today.isoformat(), kind, ai.summary(ctx, period))
        cached = db.brief(uid, today.isoformat(), kind)
        return {"summary": s, "text": cached["text"] if cached else None}
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
        try:
            sync_user(uid, days=7, quiet=True, notify=False)
        except (RuntimeError, OSError) as e:  # ответ WHOOP с ошибкой или нет сети
            print("sync:", e)
            raise ValueError("WHOOP сейчас не отвечает — попробуй через пару минут") from None
        if not db.token(uid):
            raise ValueError("вход в WHOOP истёк — подключи заново в профиле")
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
                send(owner(), box("⚡", "Форма готова", note="Кнопка «Форма» — слева от поля ввода."),
                     with_app(owner(), [] if db.token(owner()) else [[btn("🔌 Подключить WHOOP", "c|", "success")]]))
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


PLANNER_ENV = "/etc/planner-bot.env"


def planner_here():
    """На сервере есть Планер — значит, https для всех ботов раздаёт его Caddy (planner.caddyfile).
    Два Caddy на одном порту 443 делят соединения пополам, и у второго нет сертификатов других ботов."""
    return os.path.exists(PLANNER_ENV)


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
        if planner_here():  # https раздаёт Caddy планера; свой Caddy рядом с ним ломает чужие адреса
            if proc is not None and proc.poll() is None:
                proc.terminate()
            proc = None
            https_note = "адрес даёт Caddy планера — жду его"
        elif proc is None or proc.poll() is not None:
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
    global SUP
    SUP = sup = support.Support(TOKEN, "Тренер «Форма»", folders=["coach"], pm2="coach-bot", allowed=allowed,
                                tests=["python3 coach/test_coach.py"])
    while True:
        try:
            upd = tg("getUpdates", offset=offset, timeout=50, allowed_updates=["message", "callback_query"])
        except Exception as e:
            print("getUpdates:", e)
            time.sleep(5)
            continue
        for u in upd.get("result", []):
            offset = u["update_id"] + 1
            if sup.intercept(u):  # 🛠 /fix и ответы поддержке
                continue
            try:
                if u.get("callback_query"):
                    on_callback(u["callback_query"])
                elif u.get("message"):
                    on_message(u["message"])
            except Exception as e:
                print("update:", e)
                traceback.print_exc()
                chat = ((u.get("message") or {}).get("chat") or {}).get("id")
                if chat:
                    try:
                        send(chat, err(OOPS))
                    except Exception as e2:
                        print("oops:", e2)


if __name__ == "__main__":
    main()
