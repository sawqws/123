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

Только стандартная библиотека Python. Скриншоты читает `claude -p`, если он есть на сервере.
"""
import datetime
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import domyland as dl  # noqa: E402

TOKEN = os.environ.get("KPP_TOKEN", "")
API = f"https://api.telegram.org/bot{TOKEN}/"
FILES = f"https://api.telegram.org/file/bot{TOKEN}/"
ALLOWED = {int(x) for x in re.findall(r"\d+", os.environ.get("KPP_ALLOWED", ""))}
DATA = os.environ.get("KPP_DATA") or os.path.join(HERE, "data")
STATE = os.path.join(DATA, "state.json")
REPO = os.path.dirname(HERE)
TZ = ZoneInfo("Europe/Moscow")
REPEAT_WINDOW = 20 * 60  # тот же номер в течение 20 минут — не дублируем пропуск

offset = 0
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


def setup_bot():
    commands = [
        {"command": "passes", "description": "Мои пропуска"},
        {"command": "auto", "description": "Автопропуск в Домиленде"},
        {"command": "place", "description": "Адрес"},
        {"command": "login", "description": "Войти через Яндекс"},
        {"command": "help", "description": "Как пользоваться"},
    ]
    desc = ("Пропуск для такси в ЖК за одно сообщение.\n\n"
            "Пришли номер машины из Яндекс Go — бот закажет пропуск в «Домиленд+».")
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
    return f"<code>{esc(dl.pretty(plate))}</code>"


def card_ok(plate, ts):
    lines = ["✅ <b>Пропуск заказан</b>", "", f"🚕  {plate_tag(plate)}"]
    if st.get("address"):
        lines.append(f"📍  {esc(st['address'])}")
    lines.append(f"🕑  {when_text(ts)}")
    return "\n".join(lines)


def login_buttons():
    return [[{"text": "🔑 Войти через Яндекс", "url": dl.LOGIN_URL}]]


LOGIN_TEXT = (
    "🔑 <b>Вход</b>\n\n"
    "1. Нажми кнопку и войди тем же Яндекс ID, что в Домиленде.\n"
    "2. После входа откроется пустая страница или ошибка — так и должно быть.\n"
    "3. Скопируй <b>адрес этой страницы</b> целиком и пришли сюда.\n\n"
    "<i>Удобнее с iPad или компьютера: на телефоне ссылку может перехватить само приложение Домиленд.</i>"
)

HELP = (
    "🚧 <b>КПП</b> — пропуск для такси за одно сообщение\n\n"
    "Пришли номер машины — <code>А123ВС777</code>. Подойдёт и текст целиком: "
    "«белый Kia Rio, А 123 ВС 777» — номер я найду сам. "
    "Можно переслать сообщение или прислать скриншот из Яндекс Go.\n\n"
    "/passes — мои пропуска, отменить\n"
    "/auto — автопропуск в Домиленде\n"
    "/place — адрес, на который заказывать\n"
    "/login — войти заново"
)


def welcome(chat, name):
    hi = f"Привет, {esc(name)}!\n\n" if name else ""
    if st.get("jwt"):
        send(chat, hi + HELP)
    else:
        send(chat, hi + "🚧 <b>КПП</b> — пропуск для такси за одно сообщение.\n\n"
                   "Сначала один раз войду в Домиленд через твой Яндекс ID.")
        send(chat, LOGIN_TEXT, login_buttons())


# ---------- вход и адрес ----------

def do_login(chat, token):
    msg = send(chat, "⏳ Вхожу в Домиленд…")["result"]["message_id"]
    c = dl.Client(token)
    try:
        c.login()
        me = c.me()
        places = c.places()
    except dl.AuthError:
        edit(chat, msg, "😕 Домиленд не принял этот вход. Попробуй ещё раз — ссылка ниже.", login_buttons())
        return
    except dl.Error as e:
        edit(chat, msg, f"😕 Домиленд не ответил: {esc(e)}\nПопробуй через минуту.")
        return
    st.update(yandex_token=token, jwt=c.jwt, name=me.get("shortName") or me.get("fullName") or "")
    st["places"] = [{"id": p.get("id"), "building": p.get("buildingId"),
                     "title": p.get("address") or p.get("title") or p.get("buildingTitle") or str(p.get("id")),
                     "flat": p.get("placeTypeId") == 1} for p in places if p.get("id") is not None]
    save(st)
    who = f" как <b>{esc(st['name'])}</b>" if st.get("name") else ""
    edit(chat, msg, f"✅ Вошёл{who}.")
    choose_place(chat)


def choose_place(chat, msg_id=None):
    places = st.get("places") or []
    if not places:
        text = "🤔 В Домиленде не нашлось ни одного адреса. Проверь, тот ли это аккаунт: /login"
        return edit(chat, msg_id, text) if msg_id else send(chat, text)
    flats = [p for p in places if p["flat"]] or places
    if len(flats) == 1 and not msg_id:
        return set_place(chat, flats[0])
    rows = [[{"text": ("● " if p["id"] == st.get("place_id") else "") + p["title"][:60],
              "callback_data": f"pl|{p['id']}"}] for p in places]
    text = "📍 <b>Куда заказывать пропуск?</b>"
    return edit(chat, msg_id, text, rows) if msg_id else send(chat, text, rows)


def set_place(chat, p, msg_id=None):
    st.update(place_id=p["id"], building_id=p["building"], address=p["title"])
    try:
        st["taxi"] = list(call(lambda c: c.calibrate()))
    except dl.Error as e:
        print("calibrate:", e)
    save(st)
    text = (f"📍 Адрес: <b>{esc(p['title'])}</b>\n\n"
            "Готово! Теперь просто пришли номер такси — <code>А123ВС777</code>.")
    edit(chat, msg_id, text) if msg_id else send(chat, text)


# ---------- пропуск ----------

def order(chat, plate, force=False):
    if not st.get("jwt"):
        send(chat, "Сначала войди в Домиленд 👇", login_buttons())
        return
    if not st.get("place_id"):
        choose_place(chat)
        return
    now = time.time()
    last = (st.get("recent") or {}).get(plate)
    if last and now - last["ts"] < REPEAT_WINDOW and not force:
        send(chat, f"☑️ На {plate_tag(plate)} пропуск уже заказан — {when_text(last['ts'])}.",
             [[{"text": "Заказать ещё раз", "callback_data": f"again|{plate}"}]])
        return
    msg = send(chat, f"⏳ Заказываю пропуск на {plate_tag(plate)}…")["result"]["message_id"]
    type_id, sub_id = st.get("taxi") or (dl.TAXI_TYPE, dl.TAXI_SUBTYPE)
    try:
        resp = call(lambda c: c.create_taxi_pass(plate, type_id, sub_id))
    except dl.AuthError:
        edit(chat, msg, "🔑 Вход в Домиленд устарел — войди ещё раз, и я сразу закажу.", login_buttons())
        st["pending"] = plate
        save(st)
        return
    except dl.Error as e:
        edit(chat, msg, f"😕 Не получилось: {esc(e)}",
             [[{"text": "🔁 Повторить", "callback_data": f"again|{plate}"}]])
        return
    pid = dl.created_id(resp)
    recent = {k: v for k, v in (st.get("recent") or {}).items() if now - v["ts"] < 86400}
    recent[plate] = {"ts": now, "id": pid}
    st["recent"] = recent
    save(st)
    buttons = [[{"text": "✖️ Отменить", "callback_data": f"x|{pid}"}]] if pid is not None else []
    edit(chat, msg, card_ok(plate, now), buttons)


def passes_view():
    items = call(lambda c: c.passes())
    if not items:
        return "🗂 Пропусков пока нет.", []
    lines, rows = ["🗂 <b>Мои пропуска</b>"], []
    for it in items[:6]:
        status = (it.get("status") or {}).get("title") or ""
        title = it.get("title") or "Пропуск"
        sub = it.get("subtitle") or ""
        lines.append("")
        lines.append(f"<b>{esc(title)}</b>" + (f" · {esc(status)}" if status else ""))
        if sub:
            lines.append(esc(sub))
        for f in (it.get("fields") or [])[:3]:
            if f.get("value"):
                lines.append(f"<i>{esc(f.get('title') or '')}</i> {esc(f['value'])}")
        if any("cancel" in str(a.get("type", "")).lower() for a in it.get("actions") or []):
            rows.append([{"text": f"✖️ Отменить: {title[:40]}", "callback_data": f"x|{it.get('id')}"}])
    return "\n".join(lines), rows


def autopass_view():
    data = call(lambda c: c.autopass())
    places = data.get("places") or []
    head = f"🤖 <b>{esc(data.get('screenTitle') or 'Автопропуск')}</b>"
    desc = data.get("screenDescription") or ""
    lines = [head] + (["", esc(desc)] if desc else [])
    if not places:
        lines += ["", "Для твоих адресов Домиленд эту настройку не показывает."]
    rows = [[{"text": ("🟢 " if p.get("isActive") else "⚪️ ") + str(p.get("title") or p.get("id"))[:50],
              "callback_data": f"ap|{p.get('id')}"}] for p in places]
    for p in places:
        if p.get("description"):
            lines += ["", f"<b>{esc(p.get('title') or '')}</b>: {esc(p['description'])}"]
    return "\n".join(lines), rows


def toggle_autopass(pid):
    data = call(lambda c: c.autopass())
    places = data.get("places") or []
    for p in places:
        if str(p.get("id")) == pid:
            p["isActive"] = not p.get("isActive")
    call(lambda c: c.set_autopass(places))


# ---------- скриншоты ----------

def read_photo(photo):
    """Номер со скриншота: Telegram отдаёт файл, `claude -p` читает с картинки."""
    claude = shutil.which("claude")
    if not claude:
        return None
    info = tg("getFile", file_id=photo["file_id"])["result"]
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "shot.jpg")
        urllib.request.urlretrieve(FILES + info["file_path"], path)
        prompt = (f"На скриншоте {path} — заказ такси. Прочитай госномер машины. "
                  "Ответь только номером, без пробелов, например А123ВС777. Если номера нет — ответь НЕТ.")
        try:
            res = subprocess.run([claude, "-p", prompt, "--allowedTools", "Read"], cwd=tmp,
                                 capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            return None
        return res.stdout


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
        send(chat, HELP)
    elif cmd == "/login":
        send(chat, LOGIN_TEXT, login_buttons())
    elif cmd == "/logout":
        for k in ("yandex_token", "jwt", "places", "place_id", "building_id", "address", "taxi"):
            st.pop(k, None)
        save(st)
        send(chat, "👋 Вышел. Токены удалены. Войти снова: /login")
    elif cmd in ("/place", "/auto", "/passes") and not st.get("jwt"):
        send(chat, "Сначала войди в Домиленд 👇", login_buttons())
    elif cmd == "/place":
        choose_place(chat)
    elif cmd in ("/auto", "/passes"):
        try:
            send(chat, *(autopass_view() if cmd == "/auto" else passes_view()))
        except dl.AuthError:
            send(chat, "🔑 Вход в Домиленд устарел — войди ещё раз.", login_buttons())
        except dl.Error as e:
            send(chat, f"😕 Домиленд не ответил: {esc(e)}")
    elif cmd == "/update" and uid == (st.get("owner") or min(ALLOWED or {0})):
        update(chat)
    elif cmd:
        send(chat, "Такой команды нет. /help — что я умею.")
    elif dl.extract_token(text):
        do_login(chat, dl.extract_token(text))
        if st.get("pending") and st.get("place_id"):
            order(chat, st.pop("pending"))
    else:
        found = dl.plates(text)
        if not found and msg.get("photo"):
            tg("sendChatAction", chat_id=chat, action="typing")
            found = dl.plates(read_photo(msg["photo"][-1]) or "")
            if not found:
                send(chat, "🔍 Не разглядел номер на скриншоте. Напиши его текстом: <code>А123ВС777</code>")
                return
        if not found:
            send(chat, "Не вижу номера 🤔\nПришли так: <code>А123ВС777</code> или <code>АВ12377</code>")
            return
        for plate in found[:3]:
            order(chat, plate)


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
                edit(chat, mid, f"↩️ Пропуск на {plate_tag(plate)} отменён.")
            else:
                edit(chat, mid, *passes_view())
            note = "Отменил"
        elif data[0] == "again":
            order(chat, data[1], force=True)
        elif data[0] == "pl":
            p = next((p for p in st.get("places") or [] if str(p["id"]) == data[1]), None)
            if p:
                set_place(chat, p, mid)
                note = "Адрес выбран"
        elif data[0] == "ap":
            toggle_autopass(data[1])
            edit(chat, mid, *autopass_view())
            note = "Сохранил"
    except dl.AuthError:
        send(chat, "🔑 Вход в Домиленд устарел — войди ещё раз.", login_buttons())
    except Exception as e:
        print("callback:", e)
        traceback.print_exc()
        note = "Не получилось 😕"
    tg("answerCallbackQuery", callback_query_id=cq["id"], text=note)


def update(chat):
    def git(*a):
        return subprocess.run(["git", *a], cwd=REPO, capture_output=True, text=True)
    res = git("pull", "--ff-only", "origin", "main")
    send(chat, esc((res.stdout + res.stderr).strip()[-1500:] or "ok"))
    if res.returncode == 0:
        send(chat, "Перезапускаюсь…")
        tg("getUpdates", offset=offset, timeout=0)
        os._exit(0)  # pm2 поднимет заново


def main():
    global offset, st
    if not TOKEN:
        sys.exit("Нет KPP_TOKEN (токен от @BotFather). Впиши его в /etc/kpp-bot.env")
    st = load()
    setup_bot()
    print("КПП запущен")
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
