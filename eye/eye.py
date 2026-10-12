#!/usr/bin/env python3
"""Telegram-бот «Глаз»: Claude смотрит через очки Ray-Ban Meta и разговаривает голосом через их динамики.

Как пользоваться:
- фото с очков (из «Фото» или приложения Meta AI) — в чат боту или кнопкой «📷» на странице «Глаз»:
  Claude узнаёт, что это (здание, вещь, текст), и говорит вслух;
- «🎙 Говорить» на странице (кнопка «Глаз» слева от поля ввода или ссылка для Safari, /link): разговор
  без рук — слушает, пока ты говоришь, отвечает голосом. Очки подключены к iPhone как Bluetooth-гарнитура,
  поэтому микрофон и звук — очки;
- в чате тоже можно: текст, голосовые, геопозиция (📎 → Геопозиция) — всё идёт в один разговор.
Где ты сейчас, бот знает по GPS телефона (страница спрашивает разрешение) и подсказывает Claude адрес.

Переменные окружения (на сервере — /etc/eye-bot.env):
  EYE_TOKEN       токен от @BotFather
  EYE_ALLOWED     Telegram ID через запятую; если пусто — владелец тот, кто первым нажал /start
  EYE_URL         свой https-адрес страницы; если пусто — https://eye.<ip-через-дефисы>.sslip.io
  EYE_PORT        порт веб-сервера на 127.0.0.1 (по умолчанию 8791)
  EYE_MODEL, EYE_TALK_MODEL, EYE_VOICE — см. ai.py и speech.py
"""
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
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ai  # noqa: E402
import photos  # noqa: E402
import speech  # noqa: E402

TOKEN = os.environ.get("EYE_TOKEN", "")
API = f"https://api.telegram.org/bot{TOKEN}/"
ALLOWED = {int(x) for x in re.findall(r"\d+", os.environ.get("EYE_ALLOWED", ""))}
PORT = int(os.environ.get("EYE_PORT") or 8791)
DATA = os.path.join(HERE, "data")
PICS = os.path.join(DATA, "photos")
STATE = os.path.join(DATA, "state.json")
WEB = os.path.join(HERE, "webapp")
NAME = "Глаз"
FRESH_PLACE = 20 * 60  # сколько минут верим последней геопозиции
NEW_TALK = 2 * 3600  # после такой паузы разговор начинается заново
KEEP_PHOTOS = 200

lock = threading.RLock()
think = threading.BoundedSemaphore(2)  # не больше двух Claude одновременно: на сервере 2 ГБ
public_url = os.environ.get("EYE_URL", "").rstrip("/")
https_note = "задан вручную" if public_url else "ещё не настроен"
offset = 0


def esc(x):
    return html.escape(str(x), quote=False)


# ---------- оформление сообщений ----------
# Один стиль: жирный заголовок с одним эмодзи, суть — в цитате (длинная — свёрнутая),
# второстепенное — одной строкой курсивом, ошибка — одна строка «⚠️ …».

def quote(body):
    """body — уже готовый HTML."""
    body = str(body).strip()
    tag = "blockquote expandable" if len(body) > 350 or body.count("\n") > 5 else "blockquote"
    return f"<{tag}>{body}</blockquote>"


def card(icon, title, body="", note=""):
    """Сообщение: «icon <b>title</b>», цитата body (HTML), строка note курсивом (простой текст)."""
    parts = [f"{icon} <b>{esc(title)}</b>"]
    if body:
        parts.append(quote(body))
    if note:
        parts.append(f"<i>{esc(note)}</i>")
    return "\n".join(parts)


def err(text):
    text = " ".join(str(text).split()) or "что-то пошло не так"
    return "⚠️ " + esc(text[:1].upper() + text[1:])


# ---------- состояние ----------

def load():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(st):
    os.makedirs(DATA, exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE)


def setting(key, value=None, delete=False):
    with lock:
        st = load()
        if value is None and not delete:
            return st.get(key)
        if delete:
            st.pop(key, None)
        else:
            st[key] = value
        save(st)
        return value


def owner():
    return setting("owner") or 0


def allowed(uid):
    return uid in ALLOWED if ALLOWED else bool(uid) and uid == owner()


def chat_id():
    return owner() or min(ALLOWED or {0})


def web_key():
    """Ключ для страницы в Safari (вне Telegram): выводится из токена, в репозиторий не попадает."""
    return hmac.new(TOKEN.encode(), b"eye-web", hashlib.sha256).hexdigest()[:32]


# ---------- Telegram ----------

def tg(method, **params):
    req = urllib.request.Request(API + method, data=json.dumps(params).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=70) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        e.body = e.read().decode(errors="replace")
        print(method, e.code, e.body[:300])
        raise


def tg_file(method, field, name, data, ctype, **params):
    """Отправка файла (голосовое) — multipart/form-data."""
    boundary = uuid.uuid4().hex
    body = bytearray()
    for k, v in params.items():
        v = json.dumps(v) if isinstance(v, (dict, list)) else str(v)
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{name}\"\r\n"
             f"Content-Type: {ctype}\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(API + method, data=bytes(body),
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def plain(buttons):
    return [[{k: v for k, v in b.items() if k != "style"} for b in row] for row in buttons]


def tg_markup(method, buttons, **params):
    """Цветные кнопки ("style") — новинка Telegram; если сервер их не примет, шлём те же кнопки без цвета."""
    if buttons is not None:
        params["reply_markup"] = {"inline_keyboard": buttons}
    try:
        return tg(method, **params)
    except urllib.error.HTTPError as e:
        why = (getattr(e, "body", "") or "").lower()
        if e.code != 400 or not buttons or not any(w in why for w in ("button", "markup", "keyboard", "style")):
            raise
        params["reply_markup"] = {"inline_keyboard": plain(buttons)}
        return tg(method, **params)


def send(chat, text, buttons=None, **extra):
    return tg_markup("sendMessage", buttons, chat_id=chat, text=text, parse_mode="HTML",
                     link_preview_options={"is_disabled": True}, **extra)


def msg_id(res):
    return ((res or {}).get("result") or {}).get("message_id")


def put(chat, text, mid=None, buttons=None):
    """Правка на месте: заглушка («Смотрю…», «вопрос») превращается в ответ; не вышло — новое сообщение."""
    if mid:
        try:
            return tg_markup("editMessageText", buttons, chat_id=chat, message_id=mid, text=text,
                             parse_mode="HTML", link_preview_options={"is_disabled": True})
        except urllib.error.HTTPError as e:
            if "not modified" in (getattr(e, "body", "") or ""):
                return None  # «Обновить», а ничего не поменялось
            print("edit:", e)
        except Exception as e:
            print("edit:", e)
    return send(chat, text, buttons)


def typing(chat, action="typing"):
    try:
        tg("sendChatAction", chat_id=chat, action=action)
    except Exception:
        pass


def safari_link():
    return f"{public_url}/#k={web_key()}" if public_url else ""


def app_buttons():
    if not public_url:
        return []
    return [[{"text": "👁 Открыть «Глаз»", "web_app": {"url": public_url + "/"}, "style": "primary"}],
            [{"text": "🧭 Открыть в Safari", "url": safari_link()}]]


def menu_buttons():
    """Под /start и /help: главное — открыть страницу; ниже — Safari и «Заново»."""
    rows = app_buttons()[:1]
    rows.append(([{"text": "🧭 В Safari", "url": safari_link()}] if public_url else []) +
                [{"text": "🧹 Заново", "callback_data": "new"}])
    return rows


def status_buttons():
    return [[{"text": "🔄 Обновить", "callback_data": "status"}]]


def setup_bot():
    commands = [
        {"command": "app", "description": "Разговор и фото"},
        {"command": "link", "description": "Ссылка для Safari"},
        {"command": "new", "description": "Начать разговор заново"},
        {"command": "status", "description": "Проверка"},
        {"command": "help", "description": "Как пользоваться"},
    ]
    desc = ("Claude смотрит через твои очки и говорит с тобой через их динамики.\n\n"
            "Пришли фото с очков — скажу, что это за здание, вещь или надпись. "
            "Кнопка «Глаз» — разговор голосом без рук.")
    key = hashlib.sha1(json.dumps([commands, desc]).encode()).hexdigest()
    if setting("bot_setup") != key:
        try:
            tg("setMyCommands", commands=commands)
            tg("setMyDescription", description=desc)
            tg("setMyShortDescription", short_description="Claude в очках: смотрит, узнаёт, отвечает голосом.")
            setting("bot_setup", key)
        except Exception as e:
            print("setup:", e)


def set_menu():
    if not public_url:
        return False
    try:
        tg("setChatMenuButton", menu_button={"type": "web_app", "text": NAME, "web_app": {"url": public_url + "/"}})
        return True
    except Exception as e:
        print("menu:", e)
        return False


HELP = card("👁", "Глаз", (
    "Claude смотрит через твои очки и отвечает голосом в их динамики.\n\n"
    "<b>Фото.</b> Снимки с очков Meta AI сохраняет в «Фото» iPhone — пришли их сюда, "
    "можно несколько и с вопросом в подписи. "
    "Скажу, что это за здание, вещь или надпись.\n\n"
    "<b>Разговор.</b> Кнопка «Глаз» слева от поля ввода → «Говорить». Дальше без рук: "
    "говоришь — слушаю, замолчал — отвечаю. Очки подключены к iPhone по Bluetooth, "
    "поэтому микрофон и звук — их.\n\n"
    "<b>Место.</b> Страница берёт GPS телефона — так точнее узнаю здания. "
    "В чате: 📎 → Геопозиция.\n\n"
    "<b>Нет микрофона в Telegram?</b> Открой ту же страницу в Safari: /link"
), "/new — разговор заново · /status — проверка")


# ---------- память разговора и фото ----------

def history(n=12):
    with lock:
        st = load()
        h = st.get("history") or []
        if h and time.time() - h[-1]["t"] > NEW_TALK:
            st["history"] = h = []
            save(st)
        return [{"role": x["role"], "text": x["text"]} for x in h[-n:]]


def remember(role, text):
    with lock:
        st = load()
        st["history"] = (st.get("history") or [])[-39:] + [{"role": role, "text": text, "t": time.time()}]
        save(st)


def ago(sec):
    sec = max(0, int(sec))
    if sec < 90:
        return f"{sec} с"
    if sec < 5400:
        return f"{sec // 60} мин"
    return f"{sec // 3600} ч"


def recent_photos(n=3, within=3 * 3600):
    now = time.time()
    with lock:
        items = [p for p in load().get("photos") or [] if now - p["t"] < within and os.path.exists(p["path"])]
    return [{**p, "ago": ago(now - p["t"])} for p in items[-n:]]


def save_where(lat, lon):
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    setting("where", {"lat": lat, "lon": lon, "t": time.time()})
    return lat, lon


def where_now():
    w = setting("where")
    if w and time.time() - w["t"] < FRESH_PLACE:
        return w["lat"], w["lon"]
    return None


def place_now():
    return photos.place(where_now())


# ---------- события для страницы и звук ----------

events = []
audio = {}
page_seen = [0.0]
ev_lock = threading.Lock()


def page_open():
    """Страница «Глаз» открыта (опрашивает сервер) — значит, она сама прочитает ответ вслух."""
    return time.time() - page_seen[0] < 12


def keep_audio(mp3):
    if not mp3:
        return ""
    aid = secrets.token_hex(16)
    with ev_lock:
        audio[aid] = mp3
        for k in list(audio)[:-40]:
            audio.pop(k, None)
    return f"/a/{aid}.mp3"


def push_event(kind, **data):
    with ev_lock:
        eid = (events[-1]["id"] + 1) if events else int(time.time())
        events.append({"id": eid, "kind": kind, "t": time.time(), **data})
        del events[:-50]
    return eid


def last_event_id():
    with ev_lock:
        return events[-1]["id"] if events else 0


# ---------- главное: фото и ответы ----------

def add_photo(data, where=None, source="chat"):
    """Сохранить фото; вернуть его запись. where — (lat, lon) с телефона, если в фото нет своего GPS."""
    jpeg, own = photos.prepare(data)
    pid = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
    os.makedirs(PICS, exist_ok=True)
    path = os.path.join(PICS, pid + ".jpg")
    with open(path, "wb") as f:
        f.write(jpeg)
    spot = own or where or where_now()
    rec = {"id": pid, "path": path, "t": time.time(), "source": source,
           "where": list(spot) if spot else None, "text": "", "say": "", "title": ""}
    with lock:
        st = load()
        st["photos"] = (st.get("photos") or []) + [rec]
        for old in st["photos"][:-KEEP_PHOTOS]:
            try:
                os.remove(old["path"])
            except OSError:
                pass
        st["photos"] = st["photos"][-KEEP_PHOTOS:]
        save(st)
    push_event("photo_new", photo=pid)
    return rec


def update_photo(pid, **fields):
    with lock:
        st = load()
        for p in st.get("photos") or []:
            if p["id"] == pid:
                p.update(fields)
        save(st)


def analyze(rec, question="", chat=None, wait=None):
    """Разбор фото: в разговор, на страницу (вслух) и в чат. wait — заглушка «Смотрю…», её правим на месте."""
    pid = rec["id"]
    spot = rec.get("where")
    try:
        place = photos.place(tuple(spot)) if spot else ""
        with think:
            res = ai.look(rec["path"], place, history(6), question)
    except ai.AIError as e:
        push_event("error", text=str(e))
        if chat:
            put(chat, err(e), wait)
        return
    except Exception as e:
        traceback.print_exc()
        push_event("error", text="не получилось разобрать фото")
        if chat:
            put(chat, err(f"Не получилось разобрать фото: {e}"), wait)
        return
    update_photo(pid, text=res["text"], say=res["say"], title=res["title"], place=place)
    remember("user", f"[фото {res['title'] or ''}]" + (f" {question}" if question else ""))
    remember("assistant", res["say"])
    mp3 = speech.tts(res["say"])
    push_event("photo", photo=pid, say=res["say"], text=res["text"], title=res["title"], audio=keep_audio(mp3))
    chat = chat or chat_id()
    if chat:
        put(chat, card("👁", res["title"] or "Что на фото", esc(res["text"]),
                       f"📍 {place}" if place else ""), wait)
        if mp3 and not page_open():
            voice_reply(chat, mp3)


def answer(text, where=None):
    """Ответ на реплику Глеба: (что сказать, mp3 | None)."""
    if where:
        save_where(*where)
    hist = history()
    remember("user", text)
    with think:
        say = ai.talk(text, recent_photos(), place_now(), hist)
    remember("assistant", say)
    return say, speech.tts(say)


def voice_reply(chat, mp3):
    ogg = speech.to_ogg(mp3)
    if not ogg:
        return
    try:
        tg_file("sendVoice", "voice", "answer.ogg", ogg, "audio/ogg", chat_id=chat)
    except Exception as e:
        print("sendVoice:", e)


# ---------- чат ----------

def download(file_id):
    info = tg("getFile", file_id=file_id)["result"]
    with urllib.request.urlopen(f"https://api.telegram.org/file/bot{TOKEN}/{info['file_path']}", timeout=120) as r:
        return r.read(), os.path.splitext(info["file_path"])[1] or ".bin"


def on_photo(msg):
    chat = msg["chat"]["id"]
    if msg.get("photo"):
        fid = msg["photo"][-1]["file_id"]
    else:
        fid = (msg.get("document") or {}).get("file_id")
    typing(chat, "upload_photo")
    try:
        data, _ = download(fid)
    except Exception as e:
        return send(chat, err(f"Не скачал фото: {e}"))
    rec = add_photo(data, source="chat")
    wait = None if msg.get("media_group_id") else msg_id(send(chat, "👀 <b>Смотрю…</b>"))
    analyze(rec, (msg.get("caption") or "").strip(), chat, wait)


def on_voice(msg):
    chat = msg["chat"]["id"]
    obj = msg.get("voice") or msg.get("audio") or msg.get("video_note") or msg.get("document") or {}
    typing(chat, "record_voice")
    try:
        data, ext = download(obj["file_id"])
        text = speech.hear(data, ext)
    except speech.SpeechError as e:
        return send(chat, err(e))
    except Exception as e:
        return send(chat, err(f"Не получилось: {e}"))
    if not text:
        return send(chat, err("Не слышно слов — скажи ещё раз."))
    wait = msg_id(send(chat, f"🎙 <i>«{esc(text)}»</i>"))
    reply_in_chat(chat, text, voice=True, wait=wait)


def reply_in_chat(chat, text, voice=False, wait=None):
    """Ответ в чат. На голосовое сообщение с услышанным правится на месте в ответ."""
    typing(chat)
    try:
        say, mp3 = answer(text)
    except ai.AIError as e:
        return send(chat, err(e))
    push_event("talk", heard=text, say=say)
    if voice:
        put(chat, card("🎙", "Ответ", esc(say), f"«{text}»"), wait)
    else:
        send(chat, card("💬", "Ответ", esc(say)))
    if voice and mp3:
        voice_reply(chat, mp3)


def status_text():
    st = load()
    rows = [
        ("Страница", https_note + (f" — {public_url}" if public_url else "")),
        ("Claude", "есть" if shutil.which("claude") else "НЕТ (npm install -g @anthropic-ai/claude-code)"),
        ("Распознавание речи", "есть" if speech.media.whisper_python() else "нет (bash common/setup.sh)"),
        ("Озвучка", "edge-tts" if _has("edge_tts") else "голос iPhone (edge-tts не установлен)"),
        ("Фото", "Pillow" if photos.Image else "без уменьшения (Pillow не установлен)"),
        ("Фото в памяти", str(len(st.get("photos") or []))),
        ("Место", place_now() or "неизвестно (открой страницу или пришли геопозицию)"),
    ]
    return card("🩺", "Проверка", "\n".join(f"• {esc(k)} — {esc(v)}" for k, v in rows))


def _has(module):
    try:
        __import__(module)
        return True
    except ImportError:
        return False


def not_ready():
    note = https_note[:1].upper() + https_note[1:]
    return card("⏳", "Страница ещё настраивается", note=f"{note}. Загляни через пару минут.")


def on_message(msg):
    chat = msg["chat"]["id"]
    uid = (msg.get("from") or {}).get("id")
    if msg["chat"].get("type") != "private":
        return
    text = (msg.get("text") or "").strip()
    if text.startswith("/start") and not ALLOWED and not owner():
        setting("owner", uid)
        print("Владелец:", uid)
    if not allowed(uid):
        return send(chat, "🔒 Это личный бот.")
    doc = msg.get("document") or {}
    mime = doc.get("mime_type") or ""
    if msg.get("photo") or mime.startswith("image/"):
        return threading.Thread(target=on_photo, args=(msg,), daemon=True).start()
    if msg.get("voice") or msg.get("audio") or msg.get("video_note") or mime.startswith("audio/"):
        return threading.Thread(target=on_voice, args=(msg,), daemon=True).start()
    if msg.get("location"):
        loc = msg["location"]
        save_where(loc["latitude"], loc["longitude"])
        return send(chat, card("📍", "Запомнил, где ты", esc(place_now() or "координаты получил"),
                               "Верю месту 20 минут — на прогулке лучше трансляция геопозиции."))
    cmd = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
    if cmd in ("/start", "/help"):
        send(chat, HELP, menu_buttons())
    elif cmd == "/app":
        if public_url:
            send(chat, card("👁", "Глаз", note="Открой и нажми «Говорить» — дальше без рук."), app_buttons())
        else:
            send(chat, not_ready())
    elif cmd == "/link":
        if public_url:
            send(chat, card("🧭", "Ссылка для Safari", f"<code>{esc(safari_link())}</code>",
                            "Личная — не пересылай. «Поделиться» → «На экран „Домой“» — будет как приложение."))
        else:
            send(chat, not_ready())
    elif cmd == "/new":
        setting("history", [])
        send(chat, card("🧹", "Начали разговор заново", note="Фото помню."))
    elif cmd == "/status":
        send(chat, status_text(), status_buttons())
    elif text and not cmd:
        threading.Thread(target=reply_in_chat, args=(chat, text), daemon=True).start()
    else:
        send(chat, HELP, menu_buttons())


def on_callback(cb):
    """Кнопки под сообщениями: «🧹 Заново» — всплывашка без нового сообщения, «🔄 Обновить» — правка на месте."""
    msg = cb.get("message") or {}
    chat = (msg.get("chat") or {}).get("id")
    note = ""
    if not allowed((cb.get("from") or {}).get("id")):
        note = "🔒 Это личный бот."
    elif cb.get("data") == "new":
        setting("history", [])
        note = "🧹 Начали заново. Фото помню."
    elif cb.get("data") == "status" and chat:
        put(chat, status_text(), msg.get("message_id"), status_buttons())
        note = "Обновлено"
    try:
        tg("answerCallbackQuery", callback_query_id=cb["id"], text=note)
    except Exception as e:
        print("callback:", e)


# ---------- веб: страница разговора ----------

def check_init_data(init_data, max_age=7 * 86400):
    """Подпись данных Mini App (core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app)."""
    try:
        pairs = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
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


def authorized(headers):
    key = headers.get("X-Key") or ""
    if key and TOKEN and hmac.compare_digest(key, web_key()):
        return True
    user = check_init_data(headers.get("X-Init-Data") or "")
    return bool(user) and allowed(user.get("id"))


def photo_list(n=12):
    with lock:
        items = (load().get("photos") or [])[-n:]
    return [{"id": p["id"], "t": p["t"], "title": p.get("title") or "", "say": p.get("say") or "",
             "text": p.get("text") or "", "place": p.get("place") or ""} for p in reversed(items)]


def asset_version():
    h = hashlib.sha1()
    for f in sorted(os.listdir(WEB)):
        with open(os.path.join(WEB, f), "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()[:10]


VERSION = asset_version() if os.path.isdir(WEB) else "0"
MAX_UPLOAD = 25 * 1024 * 1024


class Web(BaseHTTPRequestHandler):
    server_version = "Eye/1"

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

    def query(self):
        return dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/health":
            return self.reply(200, "ok", "text/plain")
        m = re.fullmatch(r"/a/([0-9a-f]{32})\.mp3", path)
        if m:
            data = audio.get(m.group(1))
            return self.reply(200, data, "audio/mpeg", "private, max-age=3600") if data else \
                self.reply(404, {"error": "нет звука"})
        if path.startswith("/api/"):
            if not authorized(self.headers):
                return self.reply(401, {"error": "Открой «Глаз» из Telegram или по ссылке /link"})
            return self.api_get(path)
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

    def api_get(self, path):
        q = self.query()
        if path == "/api/state":
            page_seen[0] = time.time()
            if speech.media.whisper_python():
                speech.whisper.warm()
            return self.reply(200, {"photos": photo_list(), "history": history(20), "last": last_event_id(),
                                    "place": place_now(), "tts": _has("edge_tts"), "safari": safari_link()})
        if path == "/api/events":
            page_seen[0] = time.time()
            after = int(q.get("after") or 0)
            with ev_lock:
                new = [e for e in events if e["id"] > after]
            return self.reply(200, {"events": new})
        m = re.fullmatch(r"/api/photo/([\w-]+)", path)
        if m:
            with lock:
                rec = next((p for p in load().get("photos") or [] if p["id"] == m.group(1)), None)
            if rec and os.path.exists(rec["path"]):
                with open(rec["path"], "rb") as f:
                    return self.reply(200, f.read(), "image/jpeg", "private, max-age=86400")
            return self.reply(404, {"error": "нет фото"})
        self.reply(404, {"error": "нет такого метода"})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if not authorized(self.headers):
            return self.reply(401, {"error": "Открой «Глаз» из Telegram или по ссылке /link"})
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_UPLOAD:
            return self.reply(413, {"error": "файл больше 25 МБ"})
        raw = self.rfile.read(n) if n else b""
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        q = self.query()
        where = save_where(q["lat"], q["lon"]) if q.get("lat") and q.get("lon") else None
        page_seen[0] = time.time()
        try:
            if path == "/api/upload":
                if not raw:
                    return self.reply(400, {"error": "пустой файл"})
                rec = add_photo(raw, where, source="page")
                threading.Thread(target=analyze, args=(rec, q.get("q", "")), daemon=True).start()
                return self.reply(200, {"id": rec["id"]})
            if path == "/api/talk":
                if ctype.startswith("audio/") or ctype == "application/octet-stream":
                    ext = {"audio/wav": ".wav", "audio/x-wav": ".wav", "audio/mp4": ".m4a", "audio/webm": ".webm",
                           "audio/ogg": ".ogg", "audio/mpeg": ".mp3"}.get(ctype, ".wav")
                    heard = speech.hear(raw, ext)
                else:
                    heard = str((json.loads(raw or b"{}") or {}).get("text") or "").strip()
                if not heard:
                    return self.reply(200, {"heard": "", "say": ""})
                say, mp3 = answer(heard, where)
                return self.reply(200, {"heard": heard, "say": say, "audio": keep_audio(mp3)})
            if path == "/api/new":
                setting("history", [])
                return self.reply(200, {"ok": True})
            if path == "/api/where":
                return self.reply(200, {"place": place_now()})
        except (ai.AIError, speech.SpeechError) as e:
            return self.reply(503, {"error": str(e)})
        except ValueError:
            return self.reply(400, {"error": "плохой запрос"})
        except Exception as e:
            traceback.print_exc()
            return self.reply(500, {"error": str(e)})
        self.reply(404, {"error": "нет такого метода"})


def serve():
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Web)
    httpd.daemon_threads = True
    print(f"Веб-сервер: http://127.0.0.1:{PORT}")
    httpd.serve_forever()


# ---------- https-адрес ----------
# Telegram открывает страницу и Safari даёт микрофон только по https. Адрес постоянный:
# eye.<ip-через-дефисы>.sslip.io. Если на сервере есть Caddy планера (порт 443 занят) — он проксирует
# и этот адрес (planner/planner.py, caddyfile). Если 443 свободен — запускаем свой Caddy.
CADDY_DIR = os.path.join(DATA, "caddy")


def healthy(url):
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
    if setting("app_url") != url:
        setting("app_url", url)
        if chat_id():
            try:
                send(chat_id(), card("👁", "«Глаз» готов", note="Кнопка «Глаз» — слева от поля ввода. "
                                                                 "Открой и нажми «Говорить»."), app_buttons())
            except Exception as e:
                print("notify:", e)
    return True


def server_ip():
    if os.environ.get("EYE_IP"):
        return os.environ["EYE_IP"]
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
    return "eye." + ip.replace(".", "-") + ".sslip.io"


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
                              "/update в Главном или pm2 restart planner-bot")
            else:
                try:
                    proc = own_caddy(host_for(ip))
                    https_note = "Caddy получает сертификат"
                except Exception as e:
                    https_note = f"Caddy не запустился: {e}"
        elif time.time() - started > 600:
            https_note = "Caddy запущен, но адрес не открывается (открыты ли порты 80/443?)"
        time.sleep(20)


def main():
    global offset
    if not TOKEN:
        sys.exit("Нет EYE_TOKEN (токен от @BotFather). Впиши его в /etc/eye-bot.env")
    threading.Thread(target=serve, daemon=True).start()
    setup_bot()
    if public_url:
        set_menu()
    else:
        threading.Thread(target=https, daemon=True).start()
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
                if u.get("message"):
                    on_message(u["message"])
                elif u.get("callback_query"):
                    on_callback(u["callback_query"])
            except Exception as e:
                print("update:", e)
                traceback.print_exc()


if __name__ == "__main__":
    main()
