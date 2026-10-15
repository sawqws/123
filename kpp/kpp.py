#!/usr/bin/env python3
"""Telegram-бот «КПП»: пропуск для такси в ЖК через «Домиленд+» одним сообщением.

Пришёл номер машины (текстом, пересылкой, скриншотом из Яндекс Go) → бот сразу заказывает
разовый пропуск «Такси» в Домиленде и показывает карточку с кнопкой «Отменить».

Вход: один раз через Яндекс ID (тот же аккаунт, что в Домиленде) — бот присылает ссылку,
человек присылает обратно адрес страницы после входа (в нём токен).

Переменные окружения (на сервере — /etc/kpp-bot.env):
  KPP_TOKEN    токен от @BotFather
  KPP_ALLOWED  Telegram ID через запятую; если пусто — владельцем станет первый /start
  KPP_DATA     папка с данными (по умолчанию kpp/data, вне git)
  KPP_PORT     порт мини-приложения на 127.0.0.1 (по умолчанию 8792)
  KPP_URL      свой https-адрес приложения; если пусто — https://kpp.<ip-через-дефисы>.sslip.io
               (снаружи его проксирует Caddy планера)

Только стандартная библиотека Python. Скриншоты и кадры видео (запись экрана Яндекс Go) читает `claude -p`,
если он есть на сервере; речь (голосовые, кружки, видео) распознаёт common/media.py (faster-whisper),
продиктованный словами номер переводит `claude -p`.
"""
import datetime
import hashlib
import hmac
import html
import json
import mimetypes
import os
import re
import secrets
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
import domyland as dl  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(HERE), "common"))
import media  # noqa: E402  голосовые и фото (common/media.py)
import support  # noqa: E402  🛠 поддержка: /fix — Claude чинит и дорабатывает этого бота

TOKEN = os.environ.get("KPP_TOKEN", "")
API = f"https://api.telegram.org/bot{TOKEN}/"
ALLOWED = {int(x) for x in re.findall(r"\d+", os.environ.get("KPP_ALLOWED", ""))}
DATA = os.environ.get("KPP_DATA") or os.path.join(HERE, "data")
STATE = os.path.join(DATA, "state.json")
REPO = os.path.dirname(HERE)
TZ = ZoneInfo("Europe/Moscow")
REPEAT_WINDOW = 20 * 60  # тот же номер в течение 20 минут — не дублируем пропуск
PHOTO_MAX = 12 * 1024 * 1024  # скриншот из приложения
PORT = int(os.environ.get("KPP_PORT") or 8792)
WEB = os.path.join(HERE, "webapp")
public_url = (os.environ.get("KPP_URL") or "").rstrip("/")
SUP = None  # 🛠 поддержка (common/support.py) — ставит main(), ей же пользуется приложение
https_note = "ищу адрес"

offset = 0
LOCK = threading.Lock()  # заказ пропусков и состояние — по одному: голосовые разбираются в своём потоке
MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября",
              "октября", "ноября", "декабря"]


def esc(x):
    return html.escape(str(x), quote=False)


# ---------- состояние (токены — только здесь, файл 600, вне git) ----------

def load():
    """Состояние с диска. Файл испорчен — берём прошлую копию (.bak) и откладываем испорченный в .corrupt:
    пустое состояние означало бы «владельца нет» и потерю токенов Домиленда."""
    for path in (STATE, STATE + ".bak"):
        try:
            with open(path) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as e:
            print(f"состояние {os.path.basename(path)} не читается: {e}")
            if path == STATE:
                try:
                    os.replace(STATE, STATE + ".corrupt")
                except OSError:
                    pass
    return {}


def scrub(st):
    """Номер квартиры из уже сохранённых адресов (сохранены до того, как его стали вырезать)."""
    changed = False
    for a in [st, *(st.get("users") or {}).values()]:
        if a.get("address") and dl.no_flat(a["address"]) != a["address"]:
            a["address"], changed = dl.no_flat(a["address"]), True
        for p in a.get("places") or []:
            if p.get("title") and dl.no_flat(p["title"]) != p["title"]:
                p["title"], changed = dl.no_flat(p["title"]), True
    return changed


def save(st):
    os.makedirs(DATA, exist_ok=True)
    tmp = STATE + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    try:  # прошлая целая копия — на случай порчи основного файла
        if os.path.exists(STATE):
            shutil.copy2(STATE, STATE + ".bak")
            os.chmod(STATE + ".bak", 0o600)
    except OSError as e:
        print("копия состояния:", e)
    os.replace(tmp, STATE)


st = {}

# ---------- у каждого своё: владелец — корень st, гость — st["users"][id] ----------
# Личное (режим, последний номер, недавние, история пропусков «mine») — у каждого своё. Вход в Домиленд: у владельца
# свой; гость по умолчанию заказывает на вход владельца, но может войти своим Яндекс ID (/login) — тогда всё своё.
# Кто сейчас пишет — в потоке (_ctx): ставят on_message, on_callback, api_post, приложение и spawn.

_ctx = threading.local()
ACCOUNT = ("yandex_token", "jwt", "name", "places", "place_id", "building_id", "address", "taxi")


def use(uid):
    _ctx.uid = uid


def cur():
    return getattr(_ctx, "uid", None)


def guest(uid=None):
    uid = cur() if uid is None else uid
    return bool(uid) and not is_owner(uid) and str(uid) in (st.get("guests") or {})


def me():
    """Личное текущего человека: у владельца — корень st (как раньше), у гостя — его запись в st["users"]."""
    uid = cur()
    return st.setdefault("users", {}).setdefault(str(uid), {}) if guest(uid) else st


def acc():
    """Вход в Домиленд, на который заказываем: свой у гостя, вошедшего своим Яндекс ID, иначе — владельца."""
    u = me()
    return u if u is st or u.get("yandex_token") else st


def shared():
    """Гость на входе владельца: адрес и автопропуск не его, история — только его пропуска."""
    return acc() is not me()


def client():
    a = acc()
    return dl.Client(a.get("yandex_token"), a.get("jwt"), a.get("place_id"), a.get("building_id"))


def call(fn):
    """Вызов API с одной попыткой обновить JWT по сохранённому Яндекс-токену."""
    a = acc()
    c = client()
    try:
        return fn(c)
    except dl.AuthError:
        if not a.get("yandex_token"):
            raise
        c.jwt = None
        c.login()
        a["jwt"] = c.jwt
        save(st)
        return fn(c)


# ---------- Telegram ----------

def plain_markup(params):
    """Те же кнопки без цвета (style) — если Telegram ещё не знает цветных кнопок."""
    rm = params.get("reply_markup") or {}
    rows = rm.get("inline_keyboard")
    if not rows or not any("style" in btn for row in rows for btn in row):
        return None
    return dict(params, reply_markup={"inline_keyboard": [[{k: v for k, v in btn.items() if k != "style"}
                                                            for btn in row] for row in rows]})


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
        plain = plain_markup(params)
        if e.code == 400 and plain and any(w in detail.lower() for w in ("button", "markup", "keyboard", "style")):
            return tg(method, **plain)
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


def clip(text, n=24):
    """Текст кнопки до 24 символов — иначе Telegram режет его посередине (правило всех ботов)."""
    text = str(text)
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def btn(text, data, style=None):
    """Inline-кнопка; style — "success" | "danger" | "primary" (цвет, скупо)."""
    b = {"text": clip(text), "callback_data": data}
    if style:
        b["style"] = style
    return b


def quote(text, expandable=False):
    """Содержимое сообщения — в цитате (длинное — в сворачиваемой)."""
    return f"<blockquote{' expandable' if expandable else ''}>{text}</blockquote>"


def fmt(title, body="", note=""):
    """Сообщение в едином стиле: жирный заголовок с одним эмодзи, цитата, второстепенное курсивом."""
    parts = [f"<b>{title}</b>"]
    if body:
        parts.append(quote(body, expandable=body.count("\n") >= 8 or len(body) > 600))
    if note:
        parts.append(f"<i>{note}</i>")
    return "\n".join(parts)


def fail(text):
    """Ошибка — одна строка."""
    return f"⚠️ {text}"


def setup_bot():
    commands = [
        {"command": "app", "description": "Приложение"},
        {"command": "mode", "description": "Чат или приложение"},
        {"command": "passes", "description": "Мои пропуска"},
        {"command": "auto", "description": "Автопропуск в Домиленде"},
        {"command": "place", "description": "Адрес"},
        {"command": "login", "description": "Войти через Яндекс"},
        {"command": "invite", "description": "Дать доступ (сестре, семье)"},
        {"command": "help", "description": "Как пользоваться"},
    ]
    desc = ("Пропуск для такси в ЖК за одно сообщение.\n\n"
            "Пришли номер машины из Яндекс Go — текстом, скриншотом, видео или голосом. "
            "Бот закажет пропуск в «Домиленд+».")
    short = "Пропуск для такси в ЖК за одно сообщение."
    key = hashlib.sha1(json.dumps([commands, desc, short]).encode()).hexdigest()
    if st.get("bot_setup") != key:
        try:
            tg("setMyCommands", commands=commands)
            tg("setMyDescription", description=desc)
            tg("setMyShortDescription", short_description=short)
            st["bot_setup"] = key
            save(st)
        except Exception as e:
            print("setup:", e)


# ---------- доступ ----------

def owner_id():
    return st.get("owner") or min(ALLOWED or {0})


def is_owner(uid):
    """Владелец (и все из KPP_ALLOWED): вход в Домиленд, адрес, автопропуск, приглашения, /fix."""
    return bool(uid) and (uid in ALLOWED if ALLOWED else uid == st.get("owner"))


def allowed(uid):
    """Владелец или гость по приглашению (/invite): гость заказывает пропуска на вход владельца."""
    return is_owner(uid) or str(uid) in (st.get("guests") or {})


# ---------- гости: «👥 Дать доступ» (/invite) — одноразовая ссылка на сутки ----------
INVITE_TTL = 24 * 3600
OWNER_ONLY = {"/update", "/invite", "/guests"}
GUEST_OWN = {"/place", "/auto"}  # гостю — только со своим входом в Домиленд
_me = {}


def bot_username():
    if not _me.get("username"):
        try:
            _me.update(tg("getMe").get("result") or {})
        except Exception:
            pass
    return _me.get("username") or ""


def make_invite():
    code = secrets.token_urlsafe(9).replace("-", "x").replace("_", "y")
    now = time.time()
    st["invites"] = {c: t for c, t in (st.get("invites") or {}).items() if now - t < INVITE_TTL}
    st["invites"][code] = now
    save(st)
    return f"https://t.me/{bot_username()}?start=inv_{code}"


def accept_invite(uid, code, frm):
    """True — гость добавлен (ссылка одноразовая, живёт сутки)."""
    t = (st.get("invites") or {}).pop(code, None)
    if not t or time.time() - t > INVITE_TTL or not uid:
        return False
    name = " ".join(x for x in (frm.get("first_name"), frm.get("last_name")) if x) or (frm.get("username") or "гость")
    st.setdefault("guests", {})[str(uid)] = {"name": name[:60], "added": int(time.time())}
    st.setdefault("users", {})[str(uid)] = {}  # своя история с чистого листа
    save(st)
    if owner_id():
        send(owner_id(), fmt("👥 Новый доступ", f"{esc(name)} теперь может заказывать пропуска.",
                             "История у каждого своя. Убрать — /guests"))
    return True


def guests_view():
    g = st.get("guests") or {}
    if not g:
        return fmt("👥 Доступ", "Пока только у тебя.", "Дать доступ — /invite"), []
    rows = [[btn(f"✕ {v.get('name', k)}"[:24], f"gdel|{k}")] for k, v in g.items()]
    return fmt("👥 Доступ", "\n".join(f"• {esc(v.get('name', k))}" for k, v in g.items()),
               "Заказывают на твой вход (или свой), история у каждого своя. Нажми, чтобы убрать."), rows


def claim(uid):
    if not ALLOWED and not st.get("owner"):
        st["owner"] = uid
        save(st)
        print("Владелец:", uid)


# ---------- тексты ----------

def when_text(ts):
    d = datetime.datetime.fromtimestamp(ts, TZ)
    today = datetime.datetime.now(TZ).date()
    day = {0: "сегодня", -1: "вчера", 1: "завтра"}.get((d.date() - today).days,
                                                       f"{d.day} {MONTHS_GEN[d.month - 1]}")
    return f"{day}, {d:%H:%M}"


def plate_tag(plate):
    return f"<code>{esc(dl.pretty(plate)).replace(' ', NBSP)}</code>"  # номер не разрывается между строками


NBSP = " "


def pass_body(plate, ts=None):
    """Карточка пропуска: номер, адрес, срок — по строке, по одному эмодзи."""
    lines = [f"🚕 <b>{esc(dl.pretty(plate))}</b>"]
    if acc().get("address"):
        lines.append(f"📍 {esc(acc()['address'])}")
    if ts:
        lines.append(f"🕑 Разовый · {when_text(ts)}")
    return "\n".join(lines)


def card_ok(plate, ts):
    return fmt("✅ Пропуск заказан", pass_body(plate, ts))


def login_buttons():
    return [[{"text": "🔑 Войти через Яндекс", "url": dl.LOGIN_URL, "style": "primary"}],
            [{"text": "Не вышло — вторая ссылка", "url": dl.LOGIN_URL_ANDROID}]]


LOGIN_STEPS = (
    "1. Нажми кнопку и войди тем же Яндекс ID, что в Домиленде.\n"
    "2. Откроется пустая страница или ошибка — так и надо (не сайт Домиленда).\n"
    "3. Скопируй её адрес целиком (в нём access_token) и пришли сюда."
)
LOGIN_NOTE = "Лучше с iPad или компьютера: телефон может открыть приложение Домиленд."
LOGIN_TEXT = fmt("🔑 Вход через Яндекс", LOGIN_STEPS, LOGIN_NOTE)

HELP_BODY = "Пришли номер такси — сразу закажу пропуск.\nТекстом, скриншотом, видео или голосом."
HELP_NOTE = " · ".join(c + NBSP + w for c, w in (("/passes", "пропуска"), ("/place", "адрес"),
                                                   ("/auto", "автопропуск"), ("/mode", "режим")))
APP_BODY = "Номер, скриншот и пропуска — в приложении. Номер можно прислать и сюда."


# ---------- Режим: 💬 чат или 📱 приложение ----------
# Выбирается при первом /start, меняется по /mode, кнопкой «⚙️ Режим» и из приложения («💬 Перейти в чат»).
# «чат» — всё кнопками здесь, приложение не предлагается (web_app только по /app); «приложение» — главная кнопка
# открывает его, но команды и номера в чате работают всё равно. Хранится в state.json («mode»). Как в HDP и планере.

MODES = {"chat": "💬 чат", "app": "📱 приложение"}


def ui_mode():
    m = me().get("mode")
    return m if m in MODES else ""


def set_mode(m):
    if m not in MODES:
        raise ValueError("Режим — chat или app")
    me()["mode"] = m
    save(st)


def mode_note():
    """Подпись режима для карточки /start (одной строкой, курсивом)."""
    return f"Режим: {MODES.get(ui_mode(), 'не выбран')} · сменить — /mode"


def mode_card():
    return fmt("🧭 Как удобнее?", "💬 <b>В чате</b> — всё кнопками и командами здесь\n"
               "📱 <b>Приложение</b> — номер, скриншот и пропуска в одном окне",
               "Поменять можно в любой момент: /mode")


def mode_kb():
    return [[btn("💬 В чате", "mode:chat", "primary"), btn("📱 Приложение", "mode:app", "primary")]]


def ask_mode(chat, mid=None):
    return edit(chat, mid, mode_card(), mode_kb()) if mid else send(chat, mode_card(), mode_kb())


def on_mode(chat, mid, key):
    """Выбор в карточке: карточка становится «✅ Режим: …», следом — главный экран по режиму."""
    set_mode(key)
    edit(chat, mid, fmt("✅ Режим: " + MODES[key], note="Сменить — /mode или кнопка «⚙️ Режим»"), [])
    welcome(chat, "")


def home_text(hi=""):
    """Главный экран: в чате — подсказка и команды, в приложении — коротко."""
    if ui_mode() == "app":
        body = APP_BODY if public_url else f"{APP_BODY}\nПриложение ещё настраивается: {esc(https_note)}"
        return fmt("🚕 КПП", hi + body, mode_note())
    return fmt("🚕 КПП", hi + HELP_BODY, HELP_NOTE + "\n" + mode_note())


def menu_buttons():
    """Главное меню. Чат: без web_app. Приложение: открыть приложение + 2–3 нужных действия."""
    rows = []
    last = me().get("last")
    if ui_mode() == "app":
        rows += app_buttons()
        if last and acc().get("jwt"):
            rows.append([btn(f"🔁 {dl.pretty(last)}", f"rp|{last}")])
        rows.append([btn("🗂 Пропуска", "m|passes"), btn("⚙️ Режим", "mode")])
        return rows
    if last and acc().get("jwt"):
        rows.append([btn(f"🔁 {dl.pretty(last)}", f"rp|{last}")])
    if shared():  # гость на входе владельца: адрес, автопропуск и /fix — у владельца
        rows.append([btn("🗂 Пропуска", "m|passes"), btn("⚙️ Режим", "mode")])
        return rows
    rows.append([btn("🗂 Пропуска", "m|passes"), btn("📍 Адрес", "m|place")]
                + ([support.Support.button()] if not guest() else []))
    rows.append([btn("🤖 Автопропуск", "m|auto"), btn("⚙️ Режим", "mode")])
    return rows


def app_buttons(always=False):
    """Кнопка открыть приложение; в режиме «чат» её нет (always=True — всё равно, для /app)."""
    if not public_url or (ui_mode() == "chat" and not always):
        return []
    return [[{"text": "📱 Открыть приложение", "web_app": {"url": public_url + "/"}}]]


def welcome(chat, name, mid=None):
    hi = f"Привет, {esc(name)}! " if name else ""
    if acc().get("jwt"):
        text, rows = home_text(hi), menu_buttons()
        edit(chat, mid, text, rows) if mid else send(chat, text, rows)
    else:
        text = fmt("🔑 Вход через Яндекс",
                   (f"{hi}Сначала один раз войди в Домиленд.\n" if hi else "") + LOGIN_STEPS, LOGIN_NOTE)
        edit(chat, mid, text, login_buttons()) if mid else send(chat, text, login_buttons())


def need_login(chat, msg_id=None):
    return edit(chat, msg_id, LOGIN_TEXT, login_buttons()) if msg_id else send(chat, LOGIN_TEXT, login_buttons())


# ---------- вход и адрес ----------

def do_login(chat, token):
    """Вход по Яндекс-токену. -> None, если вошли, иначе (код, текст ошибки) — для приложения."""
    msg = send(chat, "⏳ <b>Вхожу в Домиленд…</b>")["result"]["message_id"]
    c = dl.Client(token)
    try:
        c.login()
        me_ = c.me()
        places = c.places()
    except dl.AuthError:
        edit(chat, msg, fail("Домиленд не принял вход — войди в Яндекс ещё раз и пришли новый адрес."),
             login_buttons())
        return 401, "Домиленд не принял вход — войди в Яндекс ещё раз и вставь новый адрес"
    except dl.Error as e:
        edit(chat, msg, fail(f"Домиленд не ответил: {esc(str(e)[:200])}\nПришли тот же адрес ещё раз чуть позже."))
        return 502, f"Домиленд не ответил: {str(e)[:200]} — попробуй ещё раз"
    a = me()  # гость входит своим Яндекс ID — дальше всё на его вход
    a.update(yandex_token=token, jwt=c.jwt, name=me_.get("shortName") or me_.get("fullName") or "")
    a["places"] = [{"id": p.get("id"), "building": p.get("buildingId"),
                     "title": dl.no_flat(p.get("address") or p.get("title") or p.get("buildingTitle") or str(p.get("id"))),
                     "flat": p.get("placeTypeId") == 1} for p in places if p.get("id") is not None]
    save(st)
    edit(chat, msg, fmt("✅ Вход выполнен", note=f"Аккаунт: {esc(a['name'])}" if a.get("name") else ""))
    choose_place(chat)


def short_labels(titles, n=22):
    """Подписи кнопок ≤ n символов: общее начало адресов («ЖК «Домиленд+», корпус») убираем — оно уходит
    в подпись сообщения; длинное — без первых слов (важен конец: дом, корпус), в крайнем случае с «…».
    Полные адреса — в тексте сообщения. -> (подписи, общее начало)."""
    words = [str(t).split() for t in titles]
    common = 0
    if len(words) > 1:
        while all(len(w) > common + 1 for w in words) and len({w[common] for w in words}) == 1:
            common += 1
        cut = [i + 1 for i in range(common) if words[0][i].endswith(",")]
        common = cut[-1] if cut else common  # по запятой: «корпус 2» лучше, чем просто «2»
    out = []
    for w, t in zip(words, titles):
        w = w[common:] or [str(t)]
        while len(" ".join(w)) > n and len(w) > 1:  # отличается обычно конец адреса (дом, корпус) — режем начало
            w = w[1:]
        o = " ".join(w)
        out.append(o if len(o) <= n else o[:n - 1].rstrip(" ,") + "…")
    return (out,
            " ".join(words[0][:common]).rstrip(",") if words else "")


def choose_place(chat, msg_id=None):
    a = acc()
    places = a.get("places") or []
    if not places:
        text = fail("В Домиленде нет ни одного адреса — тот ли это аккаунт? /login")
        return edit(chat, msg_id, text) if msg_id else send(chat, text)
    flats = [p for p in places if p["flat"]] or places
    if len(flats) == 1 and not msg_id:
        return set_place(chat, flats[0])
    # адрес целиком — в тексте, на кнопке отличающаяся часть (до 24 символов): «✓ корпус 2, кв. 148»
    labels, common = short_labels([p["title"] for p in places])
    rows = [[btn(("✓ " if p["id"] == a.get("place_id") else "") + label, f"pl|{p['id']}",
                 "primary" if p["id"] == a.get("place_id") else None)] for p, label in zip(places, labels)]
    if a.get("place_id"):
        rows.append([btn("‹ Назад", "m|home")])
    text = fmt("📍 Куда заказывать пропуск?", "\n".join(f"• {esc(p['title'])}" for p in places),
               f"Все адреса: {esc(common)}" if common else "")
    return edit(chat, msg_id, text, rows) if msg_id else send(chat, text, rows)


def apply_place(p):
    a = acc()
    a.update(place_id=p["id"], building_id=p["building"], address=p["title"])
    try:
        a["taxi"] = list(call(lambda c: c.calibrate()))
    except dl.Error as e:
        print("calibrate:", e)
    save(st)


def set_place(chat, p, msg_id=None):
    apply_place(p)
    text = fmt("📍 Адрес выбран", esc(p["title"]), "Теперь просто пришли номер такси.")
    edit(chat, msg_id, text, []) if msg_id else send(chat, text)


# ---------- пропуск ----------

def order(chat, plate, force=False):
    if not acc().get("jwt"):
        need_login(chat)
        return
    if not acc().get("place_id"):
        choose_place(chat)
        return
    last = recent_pass(plate)
    if last and not force:
        send(chat, fmt("☑️ Пропуск уже заказан", pass_body(plate, last["ts"])),
             [[btn("🔁 Заказать ещё раз", f"again|{plate}")]])
        return
    msg = send(chat, fmt("⏳ Заказываю пропуск…", pass_body(plate)))["result"]["message_id"]
    try:
        pid, now = create_pass(plate)
    except dl.AuthError:
        edit(chat, msg, fail("Вход в Домиленд устарел — войди ещё раз, и я сразу закажу."), login_buttons())
        me()["pending"] = plate
        save(st)
        return
    except dl.Error as e:
        maybe = "\nВозможно, пропуск всё же заказан — загляни в /passes, прежде чем повторять." \
            if isinstance(e, dl.NetError) else ""
        edit(chat, msg, fail(f"Не получилось заказать {plate_tag(plate)}: {esc(str(e)[:200])}{maybe}"),
             [[btn("🔁 Повторить", f"again|{plate}")]])
        return
    edit(chat, msg, card_ok(plate, now), cancel_buttons(pid))


def cancel_buttons(pid):
    return [[btn("✖️ Отменить", f"x|{pid}", "danger")]] if pid is not None else []


def recent_pass(plate):
    """Тот же номер недавно (REPEAT_WINDOW) — не дублируем: запись {ts, id} или None."""
    last = (me().get("recent") or {}).get(plate)
    return last if last and time.time() - last["ts"] < REPEAT_WINDOW else None


def create_pass(plate):
    """Заказать пропуск в Домиленде и запомнить его. -> (id, время). Ошибки — dl.AuthError / dl.Error."""
    type_id, sub_id = acc().get("taxi") or (dl.TAXI_TYPE, dl.TAXI_SUBTYPE)
    resp = call(lambda c: c.create_taxi_pass(plate, type_id, sub_id))
    now = time.time()
    pid = dl.created_id(resp)
    u = me()
    recent = {k: v for k, v in (u.get("recent") or {}).items() if now - v["ts"] < 86400}
    recent[plate] = {"ts": now, "id": pid}
    u["recent"] = recent
    u["last"] = plate
    if guest():  # история гостя: его пропуска (на входе владельца их видит только он)
        u["mine"] = (u.get("mine") or [])[-49:] + [{"id": pid, "plate": plate, "ts": int(now), "shared": shared()}]
    save(st)
    return pid, now


def mine_ids(u, only_shared=False):
    return {str(x["id"]) for x in u.get("mine") or []
            if x.get("id") is not None and (x.get("shared") or not only_shared)}


def my_passes(items):
    """История у каждого своя: гость на входе владельца видит только свои пропуска, владелец — без пропусков гостей."""
    if shared():
        ids, keep = mine_ids(me()), True
    elif acc() is st:
        ids, keep = set().union(*(mine_ids(u, True) for u in (st.get("users") or {}).values())), False
    else:
        return items
    return [it for it in items if (str(it.get("id")) in ids) == keep]


def my_pass(pid):
    """Гость на входе владельца отменяет только свои пропуска."""
    return not shared() or str(pid) in mine_ids(me())


def pass_line(it):
    """Одна строка о пропуске: номер (или название) · статус · дата."""
    sub = it.get("subtitle") or ""
    head = plate_tag(dl.plates(sub)[0]) if dl.plates(sub) else f"<b>{esc(sub or it.get('title') or 'Пропуск')}</b>"
    status = (it.get("status") or {}).get("title") or ""
    when = next((f["value"] for f in it.get("fields") or [] if f.get("value")), "")
    return " · ".join(x for x in (head, esc(status), esc(when)) if x)


def can_cancel(it):
    return any("cancel" in str(a.get("type", "")).lower() for a in it.get("actions") or [])


def passes_view():
    items = my_passes(call(lambda c: c.passes()))[:6]
    back = [btn("‹ Назад", "m|home")]
    if not items:
        return fmt("🗂 Пропуска", note="Пока пусто — пришли номер такси."), [back]
    rows = []
    for it in items:
        if can_cancel(it):
            sub = it.get("subtitle") or it.get("title") or ""
            rows.append([btn(f"✖️ Отменить {sub[:12]}", f"x|{it.get('id')}", "danger")])
    return fmt("🗂 Пропуска", "\n".join(pass_line(it) for it in items)), rows + [back]


def autopass_view():
    data = call(lambda c: c.autopass())
    places = data.get("places") or []
    desc = data.get("screenDescription") or ""
    body = [esc(desc)] if desc else []
    for p in places:
        if p.get("description"):
            body.append(f"<b>{esc(p.get('title') or '')}</b>: {esc(p['description'])}")
    note = "" if places else "Для твоих адресов Домиленд эту настройку не показывает."
    text = fmt(f"🤖 {esc(data.get('screenTitle') or 'Автопропуск')}", "\n\n".join(body), note)
    labels, _ = short_labels([str(p.get("title") or p.get("id")) for p in places], 20)
    rows = [[btn(("🟢 " if p.get("isActive") else "⚪️ ") + label, f"ap|{p.get('id')}")]
            for p, label in zip(places, labels)]
    return text, rows + [[btn("‹ Назад", "m|home")]]


def toggle_autopass(pid):
    data = call(lambda c: c.autopass())
    places = data.get("places") or []
    for p in places:
        if str(p.get("id")) == pid:
            p["isActive"] = not p.get("isActive")
    call(lambda c: c.set_autopass(places))


# ---------- скриншоты и голосовые ----------

def ask_claude(prompt, cwd, read=False):
    """Ответ `claude -p` (пустая строка, если его нет или он не ответил)."""
    claude = shutil.which("claude")
    if not claude:
        return ""
    tools = ["--allowedTools", "Read"] if read else []
    try:
        res = subprocess.run([claude, "-p", prompt, *tools], cwd=cwd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return ""
    return res.stdout


def read_image(paths):
    """Номер с картинок (скриншот, кадры записи экрана): ответ `claude -p` или None, если Claude нет."""
    if not shutil.which("claude") or not paths:
        return None
    folder = os.path.dirname(paths[0])
    prompt = (f"Картинки: {' '.join(paths)} — скриншоты или кадры записи экрана приложения такси (Яндекс Go). "
              "Открой каждую и найди госномер машины. Ответь только номером, без пробелов, например А123ВС777. "
              "Если номера нет — ответь НЕТ.")
    return ask_claude(prompt, folder, read=True)


def spoken_plate(text):
    """Номер, продиктованный словами («а сто двадцать три вэ эс семьсот семьдесят семь») -> А123ВС777."""
    prompt = (f"Человек продиктовал голосом госномер машины такси (Россия). Распознанный текст: «{text}».\n"
              "Запиши номер в виде А123ВС777: буквы кириллицей только из АВЕКМНОРСТУХ "
              "(«а»→А, «бэ/вэ»→В, «е»→Е, «ка»→К, «эм»→М, «эн»→Н, «о»→О, «пэ/эр»→Р, «эс»→С, «тэ»→Т, «у»→У, «ха»→Х), "
              "числа словами — цифрами («сто двадцать три»→123, «семьсот семьдесят семь»→777). "
              "Ответь только номером, без пробелов. Если номера нет — ответь НЕТ.")
    with tempfile.TemporaryDirectory() as tmp:
        return ask_claude(prompt, tmp)


def spawn(fn, chat, *args):
    """Медленное (речь, картинки, видео) — в отдельном потоке, чтобы бот не замирал.
    Упало — человек узнаёт об этом, а не смотрит вечно на «⏳ Ищу номер…»."""
    uid = cur()

    def run():
        use(uid)
        try:
            fn(chat, *args)
        except Exception as e:
            print("media:", e)
            traceback.print_exc()
            try:
                send(chat, fail("Не получилось разобрать — пришли номер текстом: <code>А123ВС777</code>"))
            except Exception:
                pass
    threading.Thread(target=run, daemon=True).start()


def status_msg(chat, text):
    """Временное сообщение «⏳ …» и функция, которая заменяет его на ответ."""
    tg("sendChatAction", chat_id=chat, action="typing")
    mid = ((send(chat, text) or {}).get("result") or {}).get("message_id")
    return (lambda t: edit(chat, mid, t)) if mid else (lambda t: send(chat, t))


def order_found(chat, found, miss):
    """Заказать пропуска на найденные номера (до трёх) или сказать, что номера нет."""
    with LOCK:
        if not found:
            send(chat, fail(miss))
            return
        for plate in found[:3]:
            order(chat, plate)


def voice_plates(text):
    """Номера в распознанной речи: сначала цифрами («А, 123, ВС, 777»), потом словами через Claude."""
    return dl.plates(text.replace(",", " ")) or dl.plates(spoken_plate(text) or "")


def on_voice(chat, msg):
    say = status_msg(chat, "🎙 <b>Слушаю…</b>")
    try:
        text = media.voice_text(TOKEN, msg)
    except media.MediaError as e:
        say(fail(f"Голосовое не разобрал: {esc(e)}"))
        return
    say(f"🎙 <i>«{esc(text)}»</i>")
    order_found(chat, voice_plates(text), "Не расслышал номер — повтори по буквам или напиши <code>А123ВС777</code>")


def on_photo(chat, msg):
    """Скриншот из Яндекс Go → номер (Claude читает картинку) → пропуск."""
    say = status_msg(chat, "🔍 <b>Ищу номер на скриншоте…</b>")
    with tempfile.TemporaryDirectory() as tmp:
        try:
            path = media.photo_path(TOKEN, msg, tmp)
        except media.MediaError as e:
            say(fail(f"Не открыл картинку: {esc(e)}"))
            return
        out = read_image([path])
    if out is None:
        say(fail("Скриншоты читать не умею: на сервере нет Claude. Напиши номер текстом."))
        return
    found = dl.plates(out)
    if not found:
        say(fail("Не разглядел номер на скриншоте — напиши текстом: <code>А123ВС777</code>"))
        return
    say(f"🔍 <i>Нашёл: {', '.join(plate_tag(p) for p in found[:3])}</i>")
    order_found(chat, found, "")


def on_video(chat, msg):
    """Запись экрана Яндекс Go, видео или кружок: номер в речи, потом на кадрах, потом продиктованный словами."""
    say = status_msg(chat, "🎬 <b>Смотрю видео…</b>")
    caption = (msg.get("caption") or "").strip()
    with tempfile.TemporaryDirectory() as tmp:
        try:
            v = media.video(TOKEN, msg, tmp, frames=6)
        except media.MediaError as e:
            say(fail(f"Видео не открылось: {esc(e)}"))
            return
        speech = v.get("text") or ""
        found = dl.plates(caption) or dl.plates(speech.replace(",", " "))
        if not found and v.get("frames"):
            found = dl.plates(read_image(v["frames"]) or "")
    if not found and speech:
        found = dl.plates(spoken_plate(speech) or "")
    heard = f"\n🎙 <i>«{esc(speech[:300])}»</i>" if speech else ""
    if found:
        say(f"🎬 <i>Нашёл: {', '.join(plate_tag(p) for p in found[:3])}</i>{heard}")
        order_found(chat, found, "")
    else:
        say(fail("Не нашёл номер в видео — пришли скриншот или напиши <code>А123ВС777</code>") + heard)


# ---------- обработчики ----------

def on_message(msg):
    chat = msg["chat"]["id"]
    frm = msg.get("from") or {}
    uid = frm.get("id")
    if msg["chat"].get("type") != "private":
        return
    use(uid)
    text = (msg.get("text") or msg.get("caption") or "").strip()
    if text.startswith("/start inv_") and not allowed(uid):  # гость по ссылке-приглашению
        if accept_invite(uid, text.split("inv_", 1)[1].strip(), frm):
            send(chat, home_text(f"Привет, {esc(frm.get('first_name') or '')}! ") if acc().get("jwt") else
                 fmt("👋 Доступ есть", "Вход в Домиленд пока не настроен — владелец скоро войдёт.",
                     "Или войди своим Яндекс ID: /login"), menu_buttons())
        else:
            send(chat, "🔒 Ссылка устарела или уже использована — попроси новую.")
        return
    if text.startswith("/start"):
        claim(uid)
    if not allowed(uid):
        send(chat, "🔒 Это личный бот.")
        return
    cmd = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
    if cmd in OWNER_ONLY and not is_owner(uid):
        send(chat, fail("Это может только владелец бота. Пропуск — просто пришли номер машины."))
        return
    if cmd in GUEST_OWN and shared():
        send(chat, fail("Адрес и автопропуск — на входе владельца. Свой вход в Домиленд — /login"))
        return
    if cmd == "/invite":
        link = make_invite()
        send(chat, fmt("👥 Дать доступ", f"Перешли эту ссылку — по ней можно один раз войти, она живёт сутки:\n{esc(link)}",
                       "Гость заказывает на твой вход, история и последний номер у него свои. "
                       "Может войти и своим Яндекс ID (/login) — тогда и адрес свой."))
        return
    if cmd == "/guests":
        send(chat, *guests_view())
        return
    if cmd == "/start" and not ui_mode():
        ask_mode(chat)  # первый раз: сначала выбор, чат или приложение
    elif cmd == "/start":
        welcome(chat, frm.get("first_name"))
    elif cmd == "/help":
        send(chat, home_text(), menu_buttons())
    elif cmd == "/mode":
        ask_mode(chat)
    elif cmd == "/app":
        if public_url:
            send(chat, fmt("📱 Приложение", note="Номер, скриншот, пропуска — на одном экране."),
                 app_buttons(always=True))
        else:
            send(chat, fail(f"Приложение ещё настраивается: {esc(https_note)}"))
    elif cmd == "/login":
        send(chat, LOGIN_TEXT, login_buttons())
    elif cmd == "/logout" and shared():
        send(chat, fmt("👋 Своего входа нет", note="Пропуска идут на вход владельца. Войти своим Яндекс ID — /login"))
    elif cmd == "/logout":
        for k in ACCOUNT:
            me().pop(k, None)
        save(st)
        send(chat, fmt("👋 Вышел из Домиленда", note="Токены удалены. Войти снова — /login"
                       + (" · Пока заказываю на вход владельца." if guest() and st.get("jwt") else "")))
    elif cmd in ("/place", "/auto", "/passes") and not acc().get("jwt"):
        need_login(chat)
    elif cmd == "/place":
        choose_place(chat)
    elif cmd in ("/auto", "/passes"):
        try:
            send(chat, *(autopass_view() if cmd == "/auto" else passes_view()))
        except dl.AuthError:
            send(chat, fail("Вход в Домиленд устарел — войди ещё раз."), login_buttons())
        except dl.Error as e:
            send(chat, fail(f"Домиленд не ответил: {esc(e)}"))
    elif cmd == "/update" and uid == (st.get("owner") or min(ALLOWED or {0})):
        update(chat)
    elif cmd:
        send(chat, fail("Такой команды нет — /help"))
    elif media.kind(msg) == "voice":
        spawn(on_voice, chat, msg)
    elif dl.extract_token(text):
        try:  # в ссылке — доступ к Яндекс ID Глеба: в чате не оставляем
            tg("deleteMessage", chat_id=chat, message_id=msg["message_id"])
        except Exception:
            pass
        if not do_login(chat, dl.extract_token(text)) and me().get("pending") and acc().get("place_id"):
            order(chat, me().pop("pending"))
    elif dl.plates(text):
        for plate in dl.plates(text)[:3]:
            order(chat, plate)
    elif media.kind(msg) == "photo":
        spawn(on_photo, chat, msg)
    elif media.kind(msg) == "video":
        spawn(on_video, chat, msg)
    else:
        send(chat, fail("Не вижу номера — пришли так: <code>А123ВС777</code>, скриншотом или голосом"))


def answer_cb(cq, text="", alert=False):
    """Ответ на нажатие (убирает «часики» у кнопки); сбой Telegram здесь не должен ронять обработку."""
    try:
        tg("answerCallbackQuery", callback_query_id=cq["id"], text=text[:190], show_alert=alert)
    except Exception as e:
        print("answerCallbackQuery:", e)


def drop_buttons(chat, mid):
    """Убрать кнопки у сообщения — чтобы второе нажатие «Заказать ещё раз» не заказало второй пропуск."""
    try:
        tg("editMessageReplyMarkup", chat_id=chat, message_id=mid, reply_markup={"inline_keyboard": []})
    except Exception as e:
        print("drop buttons:", e)


SLOW = {"again", "rp", "x", "ap"}  # ходят в Домиленд: отвечаем на нажатие сразу, результат — сообщением


def on_callback(cq):
    uid = cq["from"]["id"]
    msg = cq.get("message") or {}
    chat, mid = msg.get("chat", {}).get("id"), msg.get("message_id")
    data = (cq.get("data") or "").split("|")
    note, alert, answered = "", False, False
    use(uid)
    if not allowed(uid) or not chat:
        answer_cb(cq, "🔒 Это личный бот." if chat else "")
        return
    if (data[0] == "gdel" and not is_owner(uid)) or (
            (data[0] in ("pl", "ap") or data[:2] in (["m", "place"], ["m", "auto"])) and shared()):
        answer_cb(cq, "Это может только владелец бота", True)
        return
    if data[0] == "x" and len(data) > 1 and not my_pass(data[1]):
        answer_cb(cq, "Это не твой пропуск", True)
        return
    if data[0] in SLOW and len(data) > 1:
        answer_cb(cq, {"again": "⏳ Заказываю…", "rp": "⏳ Заказываю…", "x": "⏳ Отменяю…"}.get(data[0], "⏳ Секунду…"))
        answered = True
    try:
        if data[0] == "gdel":  # убрать гостя
            (st.get("guests") or {}).pop(data[1], None)
            (st.get("users") or {}).pop(data[1], None)  # и его вход в Домиленд, если был
            save(st)
            edit(chat, mid, *guests_view())
            note = "Убрал"
        elif data[0] == "x":  # отменить пропуск
            call(lambda c: c.cancel(data[1]))
            plate = next((k for k, v in (me().get("recent") or {}).items() if str(v.get("id")) == data[1]), "")
            if plate:
                me()["recent"].pop(plate, None)
                save(st)
                edit(chat, mid, fmt("↩️ Пропуск отменён", pass_body(plate)), [])
            else:
                edit(chat, mid, *passes_view())
        elif data[0] == "again":  # «Заказать ещё раз» / «Повторить» — кнопка одноразовая
            drop_buttons(chat, mid)
            order(chat, data[1], force=True)
        elif data[0] == "rp":  # повторить последний (тот же номер недавно — «уже заказан», не дубль)
            order(chat, data[1])
        elif data[0] == "pl":
            p = next((p for p in acc().get("places") or [] if str(p["id"]) == data[1]), None)
            if p:
                set_place(chat, p, mid)
                note = "Адрес выбран"
            else:  # кнопка из старого сообщения: после нового входа адресов могло стать других
                choose_place(chat, mid)
                note, alert = "Этого адреса уже нет — выбери из списка", True
        elif data[0] == "ap":
            toggle_autopass(data[1])
            edit(chat, mid, *autopass_view())
        elif data[0].startswith("mode"):  # «⚙️ Режим» → карточка на месте; mode:chat / mode:app — выбор
            key = data[0].partition(":")[2]
            if key in MODES:
                on_mode(chat, mid, key)
                note = "Режим: " + MODES[key]
            else:
                ask_mode(chat, mid)
        elif data[0] == "m":  # меню: правка сообщения на месте
            if not acc().get("jwt"):
                need_login(chat, mid)
            elif data[1] == "passes":
                edit(chat, mid, *passes_view())
            elif data[1] == "place":
                choose_place(chat, mid)
            elif data[1] == "auto":
                edit(chat, mid, *autopass_view())
            else:
                edit(chat, mid, home_text(), menu_buttons())
        else:  # старая кнопка от прошлой версии бота
            note = "Кнопка устарела — /start"
    except dl.AuthError:
        send(chat, fail("Вход в Домиленд устарел — войди ещё раз."), login_buttons())
        note = "Нужно войти заново"
    except dl.Error as e:  # Домиленд не ответил — без трассировки в логах
        text = fail(f"Домиленд не ответил: {esc(str(e)[:200])}\nПопробуй ещё раз через минуту.")
        if answered:
            send(chat, text)
        note, alert = "⚠️ Домиленд не ответил — попробуй ещё раз", True
    except IndexError:  # обрезанные данные кнопки
        note = "Кнопка устарела — /start"
    except Exception as e:
        print("callback:", e)
        traceback.print_exc()
        if answered:
            send(chat, fail("Не получилось — попробуй ещё раз или /start"))
        note, alert = "⚠️ Не получилось — попробуй ещё раз", True
    if not answered:
        answer_cb(cq, note, alert)


def oops(u):
    """Необработанная ошибка в обработчике: сказать человеку, а не молчать."""
    try:
        if u.get("callback_query"):
            answer_cb(u["callback_query"], "⚠️ Не получилось — попробуй ещё раз", True)
        elif (u.get("message") or {}).get("chat", {}).get("type") == "private":
            send(u["message"]["chat"]["id"], fail("Что-то пошло не так — попробуй ещё раз или /start"))
    except Exception as e:
        print("oops:", e)


def update(chat):
    def git(*a, timeout=60):
        return subprocess.run(["git", *a], cwd=REPO, capture_output=True, text=True, timeout=timeout)
    try:
        res = git("pull", "--ff-only", "origin", "main", timeout=120)
    except subprocess.TimeoutExpired:
        send(chat, fail("Обновление не успело за 2 минуты — попробуй ещё раз /update"))
        return
    send(chat, fmt("🔄 Обновление", esc((res.stdout + res.stderr).strip()[-1500:] or "ok")))
    if res.returncode == 0:
        send(chat, "♻️ <b>Перезапускаюсь…</b>")
        tg("getUpdates", offset=offset, timeout=0)
        os._exit(0)  # pm2 поднимет заново


# ---------- мини-приложение ----------

def check_init_data(init_data, max_age=86400):
    """Подпись данных Mini App: ключ HMAC("WebAppData", токен), не старше суток. -> user или None."""
    try:
        pairs = dict(urllib.parse.parse_qsl(init_data or "", keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None
    got = pairs.pop("hash", "")
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()

    def sign(d):
        check = "\n".join(f"{k}={v}" for k, v in sorted(d.items()))
        return hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    variants = [pairs] + ([{k: v for k, v in pairs.items() if k != "signature"}] if "signature" in pairs else [])
    if not got or not any(hmac.compare_digest(sign(d).encode(), got.encode()) for d in variants):
        return None
    try:
        if time.time() - int(pairs.get("auth_date", 0)) > max_age:
            return None
        return json.loads(pairs.get("user", "null"))
    except ValueError:
        return None


def web_user(headers):
    """Владелец или гость, открывший приложение из Telegram, или None."""
    user = check_init_data(headers.get("X-Init-Data") or "")
    return user if isinstance(user, dict) and allowed(user.get("id")) else None


def pass_items():
    out = []
    for it in my_passes(call(lambda c: c.passes()))[:8]:
        out.append({"id": it.get("id"), "title": it.get("title") or "Пропуск",
                    "status": (it.get("status") or {}).get("title") or "", "subtitle": it.get("subtitle") or "",
                    "fields": [{"title": f.get("title") or "", "value": f["value"]}
                               for f in (it.get("fields") or [])[:3] if f.get("value")],
                    "cancel": can_cancel(it)})
    return out


def app_state():
    now = time.time()
    u, a = me(), acc()
    recent = sorted(((k, v) for k, v in (u.get("recent") or {}).items() if now - v["ts"] < 86400),
                    key=lambda kv: -kv[1]["ts"])
    res = {"login": bool(a.get("jwt")), "name": a.get("name") or "", "address": a.get("address") or "",
           "place_id": a.get("place_id"), "login_url": dl.LOGIN_URL, "mode": ui_mode(), "shared": shared(),
           "places": [] if shared() else [{"id": p["id"], "title": p["title"]} for p in a.get("places") or []],
           "recent": [{"plate": k, "pretty": dl.pretty(k), "when": when_text(v["ts"]), "id": v.get("id"),
                       "fresh": now - v["ts"] < REPEAT_WINDOW} for k, v in recent[:6]],
           "last": {"plate": u["last"], "pretty": dl.pretty(u["last"])} if u.get("last") else None,
           "passes": [], "passes_error": ""}
    if a.get("jwt"):
        try:
            res["passes"] = pass_items()
        except dl.AuthError:
            res["passes_error"] = "Вход в Домиленд устарел — войди ещё раз."
            res["login"] = False
        except dl.Error as e:
            res["passes_error"] = f"Домиленд не ответил: {str(e)[:200]}"
    return res


def api_order(chat, body):
    found = dl.plates(str(body.get("text") or ""))
    if not found:
        return 400, {"error": "Не вижу номера — например, А123ВС777"}
    plate = found[0]
    if not acc().get("jwt"):
        return 401, {"error": "Сначала войди через Яндекс", "login": True}
    if not acc().get("place_id"):
        return 400, {"error": "Сначала выбери адрес"}
    last = recent_pass(plate)
    if last and not body.get("force"):
        return 200, {"repeat": True, "plate": plate, "pretty": dl.pretty(plate), "when": when_text(last["ts"])}
    try:
        pid, now = create_pass(plate)
    except dl.AuthError:
        return 401, {"error": "Вход в Домиленд устарел — войди ещё раз", "login": True}
    except dl.Error as e:
        return 502, {"error": f"Не получилось: {str(e)[:200]}"}
    try:  # та же карточка — в чат, чтобы и там была кнопка «Отменить»
        send(chat, card_ok(plate, now), cancel_buttons(pid))
    except Exception as e:
        print("приложение → чат:", e)
    return 200, {"ok": True, "plate": plate, "pretty": dl.pretty(plate), "when": when_text(now), "id": pid}


def api_cancel(body):
    pid = str(body.get("id") or "")
    if not pid:
        return 400, {"error": "нет id"}
    if not my_pass(pid):
        return 403, {"error": "Это не твой пропуск"}
    call(lambda c: c.cancel(pid))
    plate = next((k for k, v in (me().get("recent") or {}).items() if str(v.get("id")) == pid), "")
    if plate:
        me()["recent"].pop(plate, None)
        save(st)
    return 200, {"ok": True}


IMAGE_EXT = {"image/png": ".png", "image/webp": ".webp", "image/heic": ".heic", "image/gif": ".gif"}


def api_photo(data, ctype=""):
    """Скриншот из приложения (тело запроса — сама картинка) → найденные номера. Пропуск не заказывает:
    номер встаёт в поле, человек проверяет и жмёт «Оформить»."""
    if not data:
        return 400, {"error": "Пустая картинка"}
    ext = IMAGE_EXT.get((ctype or "").split(";")[0].strip().lower(), ".jpg")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "screen" + ext)
        with open(path, "wb") as f:
            f.write(data)
        out = read_image([path])
    if out is None:
        return 503, {"error": "Скриншоты читать не умею: на сервере нет Claude"}
    found = dl.plates(out)
    if not found:
        return 422, {"error": "Не разглядел номер — впиши руками"}
    return 200, {"plates": found, "pretty": [dl.pretty(p) for p in found]}


def api_post(chat, path, body):
    """POST /api/… от владельца или гостя (chat — его id). -> (код, ответ)."""
    use(chat)
    if (path == "/api/invite" and not is_owner(chat)) or (path == "/api/place" and shared()):
        return 403, {"error": "Это может только владелец бота"}
    if path == "/api/invite":  # ссылка-приглашение для гостя (как /invite в чате)
        return 200, {"link": make_invite()}
    if path == "/api/order":
        return api_order(chat, body)
    if path == "/api/cancel":
        return api_cancel(body)
    if path == "/api/login":
        token = dl.extract_token(str(body.get("text") or ""))
        if not token:
            return 400, {"error": "В адресе нет токена — скопируй его целиком после входа"}
        bad = do_login(chat, token)
        if bad:
            return bad[0], {"error": bad[1], "login": True}
        return 200, app_state()
    if path == "/api/place":
        p = next((p for p in acc().get("places") or [] if str(p["id"]) == str(body.get("id"))), None)
        if not p:
            return 404, {"error": "Нет такого адреса"}
        apply_place(p)
        return 200, app_state()
    if path == "/api/mode":  # «💬 Перейти в чат» из приложения (или обратно): в чат сразу приходит главное меню
        m = str(body.get("mode") or "")
        if m not in MODES:
            return 400, {"error": "Режим — chat или app"}
        set_mode(m)
        if m == "chat":
            try:
                send(chat, fmt("✅ Режим: " + MODES[m], "Всё, что было в приложении, есть и здесь.",
                               "Вернуть приложение — /mode"), menu_buttons())
            except Exception as e:
                print("режим → чат:", e)
        return 200, {"mode": m}
    return 404, {"error": "Нет такого метода"}


def asset_version():
    h = hashlib.sha1()
    for f in sorted(os.listdir(WEB)):
        with open(os.path.join(WEB, f), "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()[:10]


VERSION = asset_version() if os.path.isdir(WEB) else "0"


def content_length(headers):
    """Content-Length как число (мусор и минус — 0), чтобы кривой запрос не ронял обработчик."""
    try:
        return max(0, int(headers.get("Content-Length") or 0))
    except ValueError:
        return 0


class Web(BaseHTTPRequestHandler):
    server_version = "KPP/1"
    timeout = 60  # медленный клиент не держит поток вечно

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
        path = urllib.parse.urlparse(self.path).path
        if path == "/health":
            return self.reply(200, "ok", "text/plain")
        if path.startswith("/api/"):
            user = web_user(self.headers)
            if not user:
                return self.reply(401, {"error": "Открой приложение из Telegram"})
            use(user["id"])
            if path == "/api/state":
                with LOCK:
                    return self.reply(200, app_state())
            return self.reply(404, {"error": "Нет такого метода"})
        if path in ("/", "/index.html"):
            with open(os.path.join(WEB, "index.html"), encoding="utf-8") as f:
                return self.reply(200, f.read().replace("{{v}}", VERSION), "text/html; charset=utf-8")
        name = os.path.basename(path)
        file = os.path.join(WEB, name)
        if name and os.path.isfile(file):
            ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            with open(file, "rb") as f:
                return self.reply(200, f.read(), ctype, "public, max-age=31536000, immutable")
        self.reply(404, {"error": "not found"})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        user = web_user(self.headers)
        if not user:
            return self.reply(401, {"error": "Открой приложение из Telegram"})
        n = content_length(self.headers)
        if path == "/api/photo":  # картинка целиком в теле; читаем без LOCK — Claude думает долго
            if n > PHOTO_MAX:
                return self.reply(413, {"error": "Картинка больше 12 МБ"})
            try:
                code, res = api_photo(self.rfile.read(n), self.headers.get("Content-Type"))
            except Exception as e:
                traceback.print_exc()
                code, res = 500, {"error": str(e)[:200]}
            return self.reply(code, res)
        if n > (PHOTO_MAX * 4 // 3 + 4096 if path == "/api/support" else 64 * 1024):  # в поддержке — скриншот
            return self.reply(413, {"error": "Слишком большой запрос"})
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self.reply(400, {"error": "Плохой запрос"})
        if path == "/api/support":  # 🛠 из приложения — та же поддержка, что /fix в чате
            if not SUP:
                return self.reply(503, {"error": "Поддержка ещё запускается — попробуй через минуту"})
            try:
                return self.reply(200, SUP.web(user["id"], body))
            except PermissionError as e:
                return self.reply(403, {"error": str(e)})
            except ValueError as e:
                return self.reply(400, {"error": str(e)})
        try:
            with LOCK:
                code, res = api_post(user["id"], path, body)
        except dl.AuthError:
            code, res = 401, {"error": "Вход в Домиленд устарел — войди ещё раз", "login": True}
        except dl.Error as e:
            code, res = 502, {"error": f"Домиленд не ответил: {str(e)[:200]}"}
        except Exception as e:
            traceback.print_exc()
            code, res = 500, {"error": str(e)[:200]}
        self.reply(code, res)


def serve():
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Web)
    httpd.daemon_threads = True
    print(f"Приложение: http://127.0.0.1:{PORT}")
    httpd.serve_forever()


# ---------- https-адрес ----------
# Telegram открывает мини-приложение только по https. Адрес постоянный: kpp.<ip-через-дефисы>.sslip.io,
# его проксирует Caddy планера на 127.0.0.1:KPP_PORT. Как только адрес отвечает — ставим кнопку меню.

def set_menu():
    if not public_url:
        return False
    try:
        tg("setChatMenuButton", menu_button={"type": "web_app", "text": "КПП", "web_app": {"url": public_url + "/"}})
        return True
    except Exception as e:
        print("menu:", e)
        return False


def healthy(url):
    try:
        with urllib.request.urlopen(url + "/health", timeout=20) as r:
            return r.read() == b"ok"
    except Exception:
        return False


def server_ip():
    if os.environ.get("KPP_IP"):
        return os.environ["KPP_IP"]
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
    return "kpp." + ip.replace(".", "-") + ".sslip.io"


def https():
    """Ждём, пока https-адрес заработает, и включаем приложение (кнопка меню, /app)."""
    global public_url, https_note
    ip = None
    while not ip:
        ip = server_ip()
        if not ip:
            https_note = "не узнал IP сервера"
            time.sleep(60)
    url = "https://" + host_for(ip)
    while True:
        if healthy(url):
            if public_url != url:
                public_url = url
                set_menu()
            https_note = "работает"
            time.sleep(600)
            continue
        https_note = f"{url} пока не открывается (нужен маршрут в Caddy планера)"
        time.sleep(60)


def main():
    global offset, st
    if not TOKEN:
        sys.exit("Нет KPP_TOKEN (токен от @BotFather). Впиши его в /etc/kpp-bot.env")
    st = load()
    if scrub(st):
        save(st)
    threading.Thread(target=serve, daemon=True).start()
    setup_bot()
    if public_url:
        set_menu()
    else:
        threading.Thread(target=https, daemon=True).start()
    print("КПП запущен")
    global SUP
    SUP = sup = support.Support(TOKEN, "КПП", folders=["kpp"], pm2="kpp-bot", allowed=is_owner,
                                tests=["python3 kpp/test_kpp.py"])
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
                with LOCK:
                    if u.get("callback_query"):
                        on_callback(u["callback_query"])
                    elif u.get("message"):
                        on_message(u["message"])
            except Exception as e:
                print("update:", e)
                traceback.print_exc()
                oops(u)


if __name__ == "__main__":
    main()
