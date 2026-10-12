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
    try:
        with open(STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def scrub(st):
    """Номер квартиры из уже сохранённых адресов (сохранены до того, как его стали вырезать)."""
    changed = False
    if st.get("address") and dl.no_flat(st["address"]) != st["address"]:
        st["address"], changed = dl.no_flat(st["address"]), True
    for p in st.get("places") or []:
        if p.get("title") and dl.no_flat(p["title"]) != p["title"]:
            p["title"], changed = dl.no_flat(p["title"]), True
    return changed


def save(st):
    os.makedirs(DATA, exist_ok=True)
    tmp = STATE + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE)


st = {}


def client():
    return dl.Client(st.get("yandex_token"), st.get("jwt"), st.get("place_id"), st.get("building_id"))


def call(fn):
    """Вызов API с одной попыткой обновить JWT по сохранённому Яндекс-токену."""
    c = client()
    try:
        return fn(c)
    except dl.AuthError:
        if not st.get("yandex_token"):
            raise
        c.jwt = None
        c.login()
        st["jwt"] = c.jwt
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


def btn(text, data, style=None):
    """Inline-кнопка; style — "success" | "danger" | "primary" (цвет, скупо)."""
    b = {"text": text, "callback_data": data}
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
        {"command": "passes", "description": "Мои пропуска"},
        {"command": "auto", "description": "Автопропуск в Домиленде"},
        {"command": "place", "description": "Адрес"},
        {"command": "login", "description": "Войти через Яндекс"},
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

def allowed(uid):
    if ALLOWED:
        return uid in ALLOWED
    return uid == st.get("owner")


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
    if st.get("address"):
        lines.append(f"📍 {esc(st['address'])}")
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
                                                   ("/auto", "автопропуск")))
HELP = fmt("🚕 КПП", HELP_BODY, HELP_NOTE)


def menu_buttons():
    rows = app_buttons()
    last = st.get("last")
    if last and st.get("jwt"):
        rows.append([btn(f"🔁 {dl.pretty(last)}", f"rp|{last}")])
    return rows + [[btn("🗂 Пропуска", "m|passes"), btn("📍 Адрес", "m|place"), support.Support.button()]]


def app_buttons():
    return [[{"text": "📱 Открыть приложение", "web_app": {"url": public_url + "/"}}]] if public_url else []


def welcome(chat, name):
    hi = f"Привет, {esc(name)}! " if name else ""
    if st.get("jwt"):
        send(chat, fmt("🚕 КПП", hi + HELP_BODY, HELP_NOTE), menu_buttons())
    else:
        send(chat, fmt("🔑 Вход через Яндекс",
                       (f"{hi}Сначала один раз войди в Домиленд.\n" if hi else "") + LOGIN_STEPS, LOGIN_NOTE),
             login_buttons())


def need_login(chat, msg_id=None):
    return edit(chat, msg_id, LOGIN_TEXT, login_buttons()) if msg_id else send(chat, LOGIN_TEXT, login_buttons())


# ---------- вход и адрес ----------

def do_login(chat, token):
    msg = send(chat, "⏳ <b>Вхожу в Домиленд…</b>")["result"]["message_id"]
    c = dl.Client(token)
    try:
        c.login()
        me = c.me()
        places = c.places()
    except dl.AuthError:
        edit(chat, msg, fail("Домиленд не принял вход — попробуй ещё раз."), login_buttons())
        return
    except dl.Error as e:
        edit(chat, msg, fail(f"Домиленд не ответил: {esc(e)}"))
        return
    st.update(yandex_token=token, jwt=c.jwt, name=me.get("shortName") or me.get("fullName") or "")
    st["places"] = [{"id": p.get("id"), "building": p.get("buildingId"),
                     "title": dl.no_flat(p.get("address") or p.get("title") or p.get("buildingTitle") or str(p.get("id"))),
                     "flat": p.get("placeTypeId") == 1} for p in places if p.get("id") is not None]
    save(st)
    edit(chat, msg, fmt("✅ Вход выполнен", note=f"Аккаунт: {esc(st['name'])}" if st.get("name") else ""))
    choose_place(chat)


def choose_place(chat, msg_id=None):
    places = st.get("places") or []
    if not places:
        text = fail("В Домиленде нет ни одного адреса — тот ли это аккаунт? /login")
        return edit(chat, msg_id, text) if msg_id else send(chat, text)
    flats = [p for p in places if p["flat"]] or places
    if len(flats) == 1 and not msg_id:
        return set_place(chat, flats[0])
    rows = [[btn(("✓ " if p["id"] == st.get("place_id") else "") + p["title"][:60], f"pl|{p['id']}",
                 "primary" if p["id"] == st.get("place_id") else None)] for p in places]
    if st.get("place_id"):
        rows.append([btn("‹ Назад", "m|home")])
    text = fmt("📍 Куда заказывать пропуск?")
    return edit(chat, msg_id, text, rows) if msg_id else send(chat, text, rows)


def apply_place(p):
    st.update(place_id=p["id"], building_id=p["building"], address=p["title"])
    try:
        st["taxi"] = list(call(lambda c: c.calibrate()))
    except dl.Error as e:
        print("calibrate:", e)
    save(st)


def set_place(chat, p, msg_id=None):
    apply_place(p)
    text = fmt("📍 Адрес выбран", esc(p["title"]), "Теперь просто пришли номер такси.")
    edit(chat, msg_id, text, []) if msg_id else send(chat, text)


# ---------- пропуск ----------

def order(chat, plate, force=False):
    if not st.get("jwt"):
        need_login(chat)
        return
    if not st.get("place_id"):
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
        st["pending"] = plate
        save(st)
        return
    except dl.Error as e:
        edit(chat, msg, fail(f"Не получилось заказать {plate_tag(plate)}: {esc(str(e)[:200])}"),
             [[btn("🔁 Повторить", f"again|{plate}")]])
        return
    edit(chat, msg, card_ok(plate, now), cancel_buttons(pid))


def cancel_buttons(pid):
    return [[btn("✖️ Отменить", f"x|{pid}", "danger")]] if pid is not None else []


def recent_pass(plate):
    """Тот же номер недавно (REPEAT_WINDOW) — не дублируем: запись {ts, id} или None."""
    last = (st.get("recent") or {}).get(plate)
    return last if last and time.time() - last["ts"] < REPEAT_WINDOW else None


def create_pass(plate):
    """Заказать пропуск в Домиленде и запомнить его. -> (id, время). Ошибки — dl.AuthError / dl.Error."""
    type_id, sub_id = st.get("taxi") or (dl.TAXI_TYPE, dl.TAXI_SUBTYPE)
    resp = call(lambda c: c.create_taxi_pass(plate, type_id, sub_id))
    now = time.time()
    pid = dl.created_id(resp)
    recent = {k: v for k, v in (st.get("recent") or {}).items() if now - v["ts"] < 86400}
    recent[plate] = {"ts": now, "id": pid}
    st["recent"] = recent
    st["last"] = plate
    save(st)
    return pid, now


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
    items = call(lambda c: c.passes())[:6]
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
    rows = [[btn(("🟢 " if p.get("isActive") else "⚪️ ") + str(p.get("title") or p.get("id"))[:50],
                 f"ap|{p.get('id')}")] for p in places]
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


def spawn(fn, *args):
    """Медленное (речь, картинки, видео) — в отдельном потоке, чтобы бот не замирал."""
    def run():
        try:
            fn(*args)
        except Exception as e:
            print("media:", e)
            traceback.print_exc()
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
    text = (msg.get("text") or msg.get("caption") or "").strip()
    if text.startswith("/start"):
        claim(uid)
    if not allowed(uid):
        send(chat, "🔒 Это личный бот.")
        return
    cmd = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
    if cmd == "/start":
        welcome(chat, frm.get("first_name"))
    elif cmd == "/help":
        send(chat, HELP, menu_buttons())
    elif cmd == "/app":
        if public_url:
            send(chat, fmt("📱 Приложение", note="Номер, скриншот, пропуска — на одном экране."), app_buttons())
        else:
            send(chat, fail(f"Приложение ещё настраивается: {esc(https_note)}"))
    elif cmd == "/login":
        send(chat, LOGIN_TEXT, login_buttons())
    elif cmd == "/logout":
        for k in ("yandex_token", "jwt", "places", "place_id", "building_id", "address", "taxi"):
            st.pop(k, None)
        save(st)
        send(chat, fmt("👋 Вышел из Домиленда", note="Токены удалены. Войти снова — /login"))
    elif cmd in ("/place", "/auto", "/passes") and not st.get("jwt"):
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
        do_login(chat, dl.extract_token(text))
        if st.get("pending") and st.get("place_id"):
            order(chat, st.pop("pending"))
    elif dl.plates(text):
        for plate in dl.plates(text)[:3]:
            order(chat, plate)
    elif media.kind(msg) == "photo":
        spawn(on_photo, chat, msg)
    elif media.kind(msg) == "video":
        spawn(on_video, chat, msg)
    else:
        send(chat, fail("Не вижу номера — пришли так: <code>А123ВС777</code>, скриншотом или голосом"))


def on_callback(cq):
    uid = cq["from"]["id"]
    msg = cq.get("message") or {}
    chat, mid = msg.get("chat", {}).get("id"), msg.get("message_id")
    data = (cq.get("data") or "").split("|")
    note = ""
    if not allowed(uid) or not chat:
        tg("answerCallbackQuery", callback_query_id=cq["id"])
        return
    try:
        if data[0] == "x":  # отменить пропуск
            call(lambda c: c.cancel(data[1]))
            plate = next((k for k, v in (st.get("recent") or {}).items() if str(v.get("id")) == data[1]), "")
            if plate:
                st["recent"].pop(plate, None)
                save(st)
                edit(chat, mid, fmt("↩️ Пропуск отменён", pass_body(plate)), [])
            else:
                edit(chat, mid, *passes_view())
            note = "Отменил"
        elif data[0] == "again":
            order(chat, data[1], force=True)
        elif data[0] == "rp":  # повторить последний
            order(chat, data[1])
        elif data[0] == "pl":
            p = next((p for p in st.get("places") or [] if str(p["id"]) == data[1]), None)
            if p:
                set_place(chat, p, mid)
                note = "Адрес выбран"
        elif data[0] == "ap":
            toggle_autopass(data[1])
            edit(chat, mid, *autopass_view())
            note = "Сохранил"
        elif data[0] == "m":  # меню: правка сообщения на месте
            if not st.get("jwt"):
                need_login(chat, mid)
            elif data[1] == "passes":
                edit(chat, mid, *passes_view())
            elif data[1] == "place":
                choose_place(chat, mid)
            elif data[1] == "auto":
                edit(chat, mid, *autopass_view())
            else:
                edit(chat, mid, HELP, menu_buttons())
    except dl.AuthError:
        send(chat, fail("Вход в Домиленд устарел — войди ещё раз."), login_buttons())
    except Exception as e:
        print("callback:", e)
        traceback.print_exc()
        note = "Не получилось"
    tg("answerCallbackQuery", callback_query_id=cq["id"], text=note)


def update(chat):
    def git(*a):
        return subprocess.run(["git", *a], cwd=REPO, capture_output=True, text=True)
    res = git("pull", "--ff-only", "origin", "main")
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
    if not got or not any(hmac.compare_digest(sign(d), got) for d in variants):
        return None
    try:
        if time.time() - int(pairs.get("auth_date", 0)) > max_age:
            return None
        return json.loads(pairs.get("user", "null"))
    except ValueError:
        return None


def web_user(headers):
    """Владелец, открывший приложение из Telegram, или None."""
    user = check_init_data(headers.get("X-Init-Data") or "")
    return user if isinstance(user, dict) and allowed(user.get("id")) else None


def pass_items():
    out = []
    for it in call(lambda c: c.passes())[:8]:
        out.append({"id": it.get("id"), "title": it.get("title") or "Пропуск",
                    "status": (it.get("status") or {}).get("title") or "", "subtitle": it.get("subtitle") or "",
                    "fields": [{"title": f.get("title") or "", "value": f["value"]}
                               for f in (it.get("fields") or [])[:3] if f.get("value")],
                    "cancel": can_cancel(it)})
    return out


def app_state():
    now = time.time()
    recent = sorted(((k, v) for k, v in (st.get("recent") or {}).items() if now - v["ts"] < 86400),
                    key=lambda kv: -kv[1]["ts"])
    res = {"login": bool(st.get("jwt")), "name": st.get("name") or "", "address": st.get("address") or "",
           "place_id": st.get("place_id"), "login_url": dl.LOGIN_URL,
           "places": [{"id": p["id"], "title": p["title"]} for p in st.get("places") or []],
           "recent": [{"plate": k, "pretty": dl.pretty(k), "when": when_text(v["ts"]), "id": v.get("id"),
                       "fresh": now - v["ts"] < REPEAT_WINDOW} for k, v in recent[:6]],
           "last": {"plate": st["last"], "pretty": dl.pretty(st["last"])} if st.get("last") else None,
           "passes": [], "passes_error": ""}
    if st.get("jwt"):
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
    if not st.get("jwt"):
        return 401, {"error": "Сначала войди через Яндекс", "login": True}
    if not st.get("place_id"):
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
    call(lambda c: c.cancel(pid))
    plate = next((k for k, v in (st.get("recent") or {}).items() if str(v.get("id")) == pid), "")
    if plate:
        st["recent"].pop(plate, None)
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
    """POST /api/… от владельца. -> (код, ответ)."""
    if path == "/api/order":
        return api_order(chat, body)
    if path == "/api/cancel":
        return api_cancel(body)
    if path == "/api/login":
        token = dl.extract_token(str(body.get("text") or ""))
        if not token:
            return 400, {"error": "В адресе нет токена — скопируй его целиком после входа"}
        do_login(chat, token)
        if st.get("yandex_token") != token:
            return 401, {"error": "Домиленд не принял вход — попробуй ещё раз", "login": True}
        return 200, app_state()
    if path == "/api/place":
        p = next((p for p in st.get("places") or [] if str(p["id"]) == str(body.get("id"))), None)
        if not p:
            return 404, {"error": "Нет такого адреса"}
        apply_place(p)
        return 200, app_state()
    return 404, {"error": "Нет такого метода"}


def asset_version():
    h = hashlib.sha1()
    for f in sorted(os.listdir(WEB)):
        with open(os.path.join(WEB, f), "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()[:10]


VERSION = asset_version() if os.path.isdir(WEB) else "0"


class Web(BaseHTTPRequestHandler):
    server_version = "KPP/1"

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
            if not web_user(self.headers):
                return self.reply(401, {"error": "Открой приложение из Telegram"})
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
        n = int(self.headers.get("Content-Length") or 0)
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
    SUP = sup = support.Support(TOKEN, "КПП", folders=["kpp"], pm2="kpp-bot", allowed=allowed,
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


if __name__ == "__main__":
    main()
