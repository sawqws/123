#!/usr/bin/env python3
"""Telegram-бот «Авито»: фото вещи → белый фон, продающее описание, средняя цена по похожим объявлениям.

Как пользоваться:
  1. Прислать 1–10 фото (можно альбомом) — бот убирает фон, вещь остаётся на чисто белом квадрате.
     Видео или кружок — 1–3 самых чётких кадра идут как фото, а сказанное в видео — как рассказ о вещи.
  2. Написать или наговорить, что это (бренд, размер, состояние…) — Claude пишет название и описание.
  3. Число («3500», «3.5к») — цена; любой другой текст — правка описания («короче», «добавь, что есть чек»).
  4. «📊 Средняя цена» — до 100 похожих объявлений на Авито → медиана, «быстро продать», «дороже».
  То же самое — в мини-приложении (/app): загрузка фото, описание, цена с гистограммой, прошлые объявления.

Переменные окружения (на сервере — /etc/avito-bot.env):
  AVITO_TOKEN    токен от @BotFather
  AVITO_ALLOWED  Telegram ID через запятую; если пусто — владельцем станет первый /start
  AVITO_DATA     папка с данными (по умолчанию avito/data, вне git)
  AVITO_BG_MODEL модель rembg (по умолчанию isnet-general-use)
  AVITO_PORT     порт мини-приложения на 127.0.0.1 (по умолчанию 8793)
  AVITO_URL      свой https-адрес приложения; если пусто — https://avito.<ip-через-дефисы>.sslip.io
                 (снаружи его проксирует Caddy планера)

Сам бот — только стандартная библиотека Python. Фон убирает avito/photos.py в avito/.venv (rembg).
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
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "common"))
import ai  # noqa: E402
import market  # noqa: E402
import media  # noqa: E402

TOKEN = os.environ.get("AVITO_TOKEN", "")
API = f"https://api.telegram.org/bot{TOKEN}/"
FILES = f"https://api.telegram.org/file/bot{TOKEN}/"
ALLOWED = {int(x) for x in re.findall(r"\d+", os.environ.get("AVITO_ALLOWED", ""))}
DATA = os.environ.get("AVITO_DATA") or os.path.join(HERE, "data")
STATE = os.path.join(DATA, "state.json")
DRAFTS = os.path.join(DATA, "drafts")
REPO = os.path.dirname(HERE)
VENV_PY = os.path.join(HERE, ".venv", "bin", "python")
MAX_PHOTOS = 10
MAX_UPLOAD = 15 * 1024 * 1024  # одно фото из приложения
ALBUM_WAIT = 2.5      # секунд ждём остальные фото альбома
MARKET_TTL = 6 * 3600  # средняя цена считается заново не чаще, чем раз в 6 часов (или по кнопке)
KEEP_ADS = 15          # папки (и карточки в приложении) последних объявлений
PORT = int(os.environ.get("AVITO_PORT") or 8793)
WEB = os.path.join(HERE, "webapp")
public_url = (os.environ.get("AVITO_URL") or "").rstrip("/")
https_note = "ищу адрес"

offset = 0
lock = threading.RLock()
busy = set()  # чаты, где сейчас что-то делается (фото, описание, цена)
timers = {}
st = {}


def esc(x):
    return html.escape(str(x), quote=False)


# ---------- состояние ----------

def load():
    try:
        with open(STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save():
    with lock:
        os.makedirs(DATA, exist_ok=True)
        tmp = STATE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
        os.replace(tmp, STATE)


def draft(create=False):
    """Текущее объявление. create=True — завести новое, если его нет."""
    with lock:
        d = st.get("draft")
        if not d and create:
            did = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            d = st["draft"] = {"id": did, "photos": [], "info": "", "price": None}
            os.makedirs(os.path.join(DRAFTS, did), exist_ok=True)
            cleanup_old()
            save()
        return d


def cleanup_old(keep=KEEP_ADS):
    """Храним папки последних 15 объявлений, остальные удаляем."""
    try:
        dirs = sorted(os.listdir(DRAFTS))
    except OSError:
        return
    for name in dirs[:-keep]:
        shutil.rmtree(os.path.join(DRAFTS, name), ignore_errors=True)


def ddir(d):
    return os.path.join(DRAFTS, d["id"])


# ---------- Telegram ----------

def plain_markup(params):
    """Те же кнопки без цвета (style) — если Telegram ещё не знает цветных кнопок."""
    rows = (params.get("reply_markup") or {}).get("inline_keyboard")
    if not rows or not any("style" in btn for row in rows for btn in row):
        return None
    return dict(params, reply_markup={"inline_keyboard": [[{k: v for k, v in btn.items() if k != "style"}
                                                            for btn in row] for row in rows]})


def tg(method, **params):
    req = urllib.request.Request(API + method, data=json.dumps(params).encode(),
                                 headers={"Content-Type": "application/json"})
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


def tg_files(method, fields, files):
    """multipart/form-data: fields — обычные поля, files — {имя: путь}."""
    boundary = uuid.uuid4().hex
    body = bytearray()
    for k, v in fields.items():
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    for k, path in files.items():
        with open(path, "rb") as f:
            data = f.read()
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{k}.jpg"\r\n'
                 "Content-Type: image/jpeg\r\n\r\n").encode() + data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(API + method, data=bytes(body),
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        print(method, e.code, e.read().decode(errors="replace")[:300])
        raise


def send(chat, text, buttons=None, **extra):
    if buttons is not None:
        extra["reply_markup"] = {"inline_keyboard": buttons}
    return tg("sendMessage", chat_id=chat, text=text, parse_mode="HTML",
              link_preview_options={"is_disabled": True}, **extra)


def edit(chat, mid, text, buttons=None):
    try:
        return tg("editMessageText", chat_id=chat, message_id=mid, text=text, parse_mode="HTML",
                  link_preview_options={"is_disabled": True}, reply_markup={"inline_keyboard": buttons or []})
    except urllib.error.HTTPError:
        return send(chat, text, buttons)


def b(text, data, style=None):
    """Inline-кнопка; style — "success" | "danger" | "primary" (цвет, скупо)."""
    btn = {"text": text, "callback_data": data}
    if style:
        btn["style"] = style
    return btn


def quote(text, expandable=False):
    """Содержимое сообщения — в цитате (длинное — в сворачиваемой)."""
    return f"<blockquote{' expandable' if expandable else ''}>{text}</blockquote>"


def long(text):
    return text.count("\n") >= 8 or len(text) > 600


def fmt(title, body="", note=""):
    """Сообщение в едином стиле: жирный заголовок с одним эмодзи, цитата, второстепенное курсивом."""
    parts = [f"<b>{title}</b>"]
    if body:
        parts.append(quote(body, long(body)))
    if note:
        parts.append(f"<i>{note}</i>")
    return "\n".join(parts)


def fail(text):
    """Ошибка — одна строка."""
    return f"⚠️ {text}"


def send_album(chat, paths, caption=""):
    """Фото альбомом (по 10 штук), подпись — у первого."""
    for i in range(0, len(paths), 10):
        part = paths[i:i + 10]
        if len(part) == 1:
            tg_files("sendPhoto", {"chat_id": chat, "caption": caption if i == 0 else ""}, {"photo": part[0]})
            continue
        media = [{"type": "photo", "media": f"attach://p{n}", **({"caption": caption} if i == n == 0 and caption
                                                                  else {})} for n in range(len(part))]
        tg_files("sendMediaGroup", {"chat_id": chat, "media": json.dumps(media, ensure_ascii=False)},
                 {f"p{n}": p for n, p in enumerate(part)})


def setup_bot():
    commands = [
        {"command": "app", "description": "Приложение"},
        {"command": "new", "description": "Новое объявление"},
        {"command": "ad", "description": "Текущее объявление"},
        {"command": "price", "description": "Средняя цена на Авито"},
        {"command": "photos", "description": "Фото на белом фоне"},
        {"command": "region", "description": "Регион для сравнения цен"},
        {"command": "help", "description": "Как пользоваться"},
    ]
    desc = ("Объявление для Авито за минуту.\n\n"
            "Пришли фото вещи — уберу фон, напишу продающее описание и подскажу цену по похожим объявлениям.")
    short = "Фото на белом фоне, описание и цена для Авито."
    key = hashlib.sha1(json.dumps([commands, desc, short]).encode()).hexdigest()
    if st.get("bot_setup") != key:
        try:
            tg("setMyCommands", commands=commands)
            tg("setMyDescription", description=desc)
            tg("setMyShortDescription", short_description=short)
            st["bot_setup"] = key
            save()
        except Exception as e:
            print("setup:", e)


# ---------- доступ ----------

def allowed(uid):
    return uid in ALLOWED if ALLOWED else uid == st.get("owner")


def claim(uid):
    if not ALLOWED and not st.get("owner"):
        st["owner"] = uid
        save()
        print("Владелец:", uid)


# ---------- цена ----------

PRICE = re.compile(r"^\s*(?:цена|за|ставь|поставь|стоимость)?\s*:?\s*(\d[\d\s .,]*)\s*(к|тыс\.?|тысяч[иа]?)?\s*"
                   r"(?:₽|р\.?|руб\.?|рублей|рубля)?\s*$", re.I)


def parse_price(text):
    """«3500», «3 500 ₽», «цена 3.5к», «12 тыс» → число рублей; иначе None."""
    m = PRICE.match(text or "")
    if not m:
        return None
    bare = not re.search(r"[а-яёa-z₽]", text, re.I)
    if bare and int(re.sub(r"\D", "", m.group(1)) or 0) < 300:
        return None  # «42», «128» — скорее ответ про размер или память, а не цена
    num = m.group(1).replace(" ", "").replace(" ", "")
    if m.group(2):
        try:
            return int(round(float(num.replace(",", ".")) * 1000))
        except ValueError:
            return None
    num = re.sub(r"[.,]\d{1,2}$", "", num)  # копейки отбрасываем
    num = re.sub(r"\D", "", num)
    return int(num) if num and 0 < int(num) < 100_000_000 else None


# ---------- карточка объявления ----------

def card(d):
    """Карточка объявления: название, описание в цитате, цена, фото, вопросы."""
    if d.get("title"):
        desc = esc(d.get("description") or "")
        lines = [f"<b>📝 {esc(d['title'])}</b>"] + ([quote(desc, long(desc))] if desc else [])
    else:
        lines = ["<b>📝 Описания пока нет</b>"]
    lines.append(f"<b>💰 {market.fmt_rub(d['price'])}</b>" if d.get("price")
                 else "💰 Пришли цену — например <code>3500</code>")
    if d.get("photos"):
        clean = sum(1 for p in d["photos"] if p.get("clean"))
        lines.append(f"🖼 На белом фоне: {clean} из {len(d['photos'])}")
    if d.get("questions"):
        lines.append("<b>❓ Ответь — описание станет точнее</b>")
        lines.append(quote("\n".join(f"• {esc(q)}" for q in d["questions"])))
    if d.get("title"):
        hint = "пиши правку или цену"
        lines.append(f"<i>{esc(d['category']) + ' · ' + hint if d.get('category') else hint.capitalize()}</i>")
    else:
        lines.append("<i>Напиши пару слов о вещи — бренд, размер, состояние.</i>")
    return "\n".join(lines)


def card_buttons(d):
    rows = []
    if d.get("title"):
        rows.append([b("📊 Цена на Авито", "mk", None if d.get("price") else "primary"), b("📋 Скопировать", "cp")])
        rows.append([b("🔄 Переписать", "re"), b("🆕 Новое", "new")])  # фото — альбомом выше и /photos
        return rows
    first = []
    if d.get("photos"):
        first.append(b("✨ Описание по фото", "gen", "primary"))
    if d.get("info"):
        first.append(b("📊 Цена на Авито", "mk"))
    if first:
        rows.append(first)
    rows.append(([b("🖼 Фото", "ph")] if d.get("photos") else []) + [b("🆕 Новое", "new")])
    return rows


def show_card(chat, mid=None):
    d = draft()
    if not d:
        return send(chat, fmt("📝 Объявления пока нет", note="Пришли фото вещи или опиши её текстом."))
    text, btns = card(d), card_buttons(d)
    return edit(chat, mid, text, btns) if mid else send(chat, text, btns)


# ---------- фото ----------

def download(file_id, dst):
    info = tg("getFile", file_id=file_id)["result"]
    urllib.request.urlretrieve(FILES + info["file_path"], dst)


def new_orig(d):
    """Путь для нового фото объявления."""
    os.makedirs(ddir(d), exist_ok=True)
    return os.path.join(ddir(d), f"orig_{len(d['photos']) + 1:02d}_{uuid.uuid4().hex[:4]}.jpg")


def keep_photo(d, path):
    with lock:
        d["photos"].append({"orig": path, "clean": None})
        save()


def add_info(d, text):
    text = (text or "").strip()
    if text:
        with lock:
            d["info"] = (d.get("info", "") + "\n" + text).strip()
            save()


def add_photo(chat, msg):
    """Сохранить фото из сообщения в текущее объявление и через ALBUM_WAIT секунд обработать все новые."""
    if msg.get("photo"):
        fid = msg["photo"][-1]["file_id"]
    else:
        fid = msg["document"]["file_id"]
    d = draft(create=True)
    if len(d["photos"]) >= MAX_PHOTOS:
        if not d.get("warned_max"):
            d["warned_max"] = True
            save()
            send(chat, fail(f"На Авито хватит {MAX_PHOTOS} фото — остальные пропускаю."))
        return
    path = new_orig(d)
    download(fid, path)
    keep_photo(d, path)
    add_info(d, msg.get("caption"))
    schedule_photos(chat, ALBUM_WAIT)


def best_frames(paths, k=3):
    """k самых чётких кадров (в порядке видео). Чёткость — размер JPEG: размытый кадр сжимается сильнее."""
    sizes = {p: os.path.getsize(p) for p in paths if os.path.exists(p)}
    top = set(sorted(sizes, key=sizes.get, reverse=True)[:k])
    return [p for p in paths if p in top]


def from_video(chat, msg):
    """Видео или кружок: 1–3 лучших кадра → фото вещи (белый фон), речь и подпись → рассказ о вещи."""
    mid = (send(chat, fmt("🎬 Смотрю видео…")).get("result") or {}).get("message_id")
    tg("sendChatAction", chat_id=chat, action="upload_photo")
    tmp = tempfile.mkdtemp(prefix="video-", dir=DATA)
    try:
        try:
            v = media.video(TOKEN, msg, tmp, frames=6)
        except media.MediaError as e:
            return edit(chat, mid, fail(f"Видео не открылось: {esc(e)}"))
        d = draft(create=True)
        had_title = bool(d.get("title"))
        room = MAX_PHOTOS - len(d["photos"])
        frames = best_frames(v["frames"], min(3, max(room, 0)))
        for f in frames:
            dst = new_orig(d)
            shutil.copyfile(f, dst)
            keep_photo(d, dst)
        speech = (v.get("text") or "").strip()
        text = "\n".join(t for t in ((msg.get("caption") or "").strip(), speech) if t)
        try:
            tg("deleteMessage", chat_id=chat, message_id=mid)
        except Exception:
            pass
        if speech:
            send(chat, f"🎙 <i>«{esc(speech)}»</i>")
        if not frames:
            if text:
                return on_text(chat, text, now=True)
            return send(chat, fail(f"На Авито хватит {MAX_PHOTOS} фото — кадры не добавляю." if room <= 0
                                   else "В видео не нашлось кадров с вещью — пришли фото."))
        if v.get("cover_only"):
            send(chat, "<i>Видео больше 20 МБ — взял только обложку.</i>")
        if had_title:
            process_photos(chat, card=not text)
            if text:
                add_info(d, text)
                write(chat, text)
        else:
            add_info(d, text)
            process_photos(chat)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def schedule_photos(chat, delay):
    t = timers.pop(chat, None)
    if t:
        t.cancel()
    t = timers[chat] = threading.Timer(delay, lambda: run(chat, process_photos, chat, later=schedule_photos))
    t.daemon = True
    t.start()


def remove_bg(pairs):
    """[(вход, выход)] → результаты photos.py. Без venv — ошибка у каждого фото."""
    py = VENV_PY if os.path.exists(VENV_PY) else None
    if not py:
        return [{"src": s, "dst": t, "ok": False, "error": "на сервере не установлен rembg"} for s, t in pairs]
    args = [x for pair in pairs for x in pair]
    try:
        p = subprocess.run([py, os.path.join(HERE, "photos.py"), *args], capture_output=True, text=True,
                           timeout=120 + 60 * len(pairs))
    except subprocess.TimeoutExpired:
        return [{"src": s, "dst": t, "ok": False, "error": "слишком долго"} for s, t in pairs]
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        print("photos.py:", p.returncode, (p.stderr or "")[-800:])
        return [{"src": s, "dst": t, "ok": False, "error": "обработка фото упала"} for s, t in pairs]


def todo_photos(d):
    return [p for p in d.get("photos") or [] if not p.get("clean") and not p.get("err")]


def clean_photos(d, on_start=None):
    """Убрать фон у ещё не обработанных фото объявления -> (готовые пути, ошибки).
    Фото, которые уже обрабатываются (из чата или приложения), второй раз не берём."""
    with lock:
        todo = [p for p in todo_photos(d) if not p.get("work")]
        for p in todo:
            p["work"] = True
    if not todo:
        return [], []
    if on_start:
        on_start(len(todo))
    pairs = [(p["orig"], p["orig"].replace("orig_", "white_")) for p in todo]
    try:
        res = {r["src"]: r for r in remove_bg(pairs)}
    finally:
        with lock:
            for p in todo:
                p.pop("work", None)
    ok, bad = [], []
    with lock:
        for p in todo:
            r = res.get(p["orig"]) or {"ok": False, "error": "нет ответа"}
            if r["ok"]:
                p["clean"] = r["dst"]
                ok.append(r["dst"])
            else:
                p["err"] = r.get("error") or "не получилось"
                bad.append(p["err"])
        save()
    return ok, bad


def process_photos(chat, card=True):
    d = draft()
    if not d:
        return
    status = {}

    def started(n):
        status["mid"] = send(chat, fmt(f"🪄 Убираю фон · {n} фото…"))["result"]["message_id"]
        tg("sendChatAction", chat_id=chat, action="upload_photo")
    ok, bad = clean_photos(d, started)
    if not status:
        return
    try:
        tg("deleteMessage", chat_id=chat, message_id=status["mid"])
    except Exception:
        pass
    if ok:
        send_album(chat, ok, f"✅ Фон убран · {len(ok)} фото")
    if bad:
        send(chat, fail(f"{len(bad)} фото не получилось: {esc(bad[0])} — возьми оригинал."))
    if not card:
        return
    if d.get("title"):
        show_card(chat)
    elif d.get("info"):
        write(chat)  # подпись к фото уже есть — сразу пишем описание
    else:
        send(chat, fmt("✍️ Что это за вещь?", note="Бренд, размер, состояние — текстом или голосом."),
             [[b("✨ Опиши по фото", "gen", "primary")]])


# ---------- описание ----------

def write(chat, request="", mid=None):
    d = draft(create=True)
    status = fmt("✍️ Переписываю…" if request or d.get("title") else "✍️ Пишу описание…")
    mid = ((edit(chat, mid, status) if mid else send(chat, status)).get("result") or {}).get("message_id", mid)
    tg("sendChatAction", chat_id=chat, action="typing")
    try:
        compose(d, request)
    except ai.AIError as e:
        return edit(chat, mid, fail(f"Описание не получилось: {esc(e)}"), [[b("🔁 Ещё раз", "gen")]])
    show_card(chat, mid)


def compose(d, request=""):
    """Claude пишет (или переписывает) название и описание объявления. Ошибка — ai.AIError."""
    images = [p.get("orig") for p in d.get("photos") or []][:6]
    prev = {k: d[k] for k in ("title", "description") if d.get(k)} or None
    ad = ai.write_ad(d.get("info", ""), images, prev, request)
    with lock:
        if ad["query"] != d.get("query"):
            d.pop("market", None)
        d.update(ad)
        save()
    return d


# ---------- средняя цена ----------

def region_name(region=None):
    region = region or st.get("region") or "all"
    return "вся Россия" if region == "all" else region


def market_text(d, m):
    s = m.get("stats")
    when = f" · {datetime.datetime.fromtimestamp(m['ts']):%d.%m %H:%M}" if m.get("ts") else ""
    note = f"«{esc(m['query'])}» · {esc(region_name())}{when}"
    if not s:
        return fmt("📊 Похожих не нашлось", f"Просмотрел объявлений: {m.get('seen', 0)}. "
                   "Уточни описание — поищу снова.", note)
    body = "\n".join([
        f"Похожих объявлений: <b>{s['n']}</b> из {m.get('seen', s['total'])}",
        f"Диапазон: {market.fmt_rub(s['min'])[:-2]} – {market.fmt_rub(s['max'])}",
        f"⚡ Быстро продать: {market.fmt_rub(s['fast'])}",
        f"💎 Дороже рынка: {market.fmt_rub(s['high'])}",
    ])
    return fmt(f"📊 Медиана: {market.fmt_rub(s['median'])}", body, note)


def market_buttons(m):
    s = m.get("stats")
    rows = []
    if s:
        num = lambda x: market.fmt_rub(x)[:-2]  # noqa: E731  без «₽» — три цены влезают в ряд
        rows.append([b(f"⚡ {num(s['fast'])}", f"pr|{s['fast']}"),
                     b(f"🎯 {num(s['median'])}", f"pr|{s['median']}", "primary"),
                     b(f"💎 {num(s['high'])}", f"pr|{s['high']}")])
    rows.append([b("‹ Назад", "card"), b("🔄 Заново", "mk|f"),
                 {"text": "🔎 Авито", "url": market.search_url(m["query"], st.get("region"))}])
    return rows


def ad_query(d):
    return d.get("query") or d.get("title") or (d.get("info") or "")[:60]


def fresh_market(d, query):
    m = d.get("market")
    if m and m.get("query") == query and time.time() - m.get("ts", 0) < MARKET_TTL:
        return m
    return None


def market_for(d, query):
    """Собрать цены на Авито по запросу и запомнить в объявлении. Ошибки — market.Blocked и прочие."""
    items = market.collect(query, st.get("region") or "all")
    similar = items
    if items:
        try:
            similar = ai.pick_similar(f"{d.get('title') or query}. {d.get('info', '')[:300]}", items)
        except ai.AIError as e:
            print("pick_similar:", e)
            similar = market.relevant(query, items)
    m = {"query": query, "ts": time.time(), "seen": len(items), "stats": market.stats(similar),
         "prices": market.trim_outliers([i["price"] for i in similar]),  # для гистограммы в приложении
         "sample": [{"t": i["title"], "p": i["price"], "u": i["url"]} for i in similar[:10]]}
    with lock:
        d["market"] = m
        save()
    return m


def check_market(chat, force=False, mid=None):
    """Средняя цена. mid — сообщение, которое правим на месте (карточка или прошлый результат)."""
    d = draft()
    if not d:
        return send(chat, fail("Сначала пришли фото или опиши вещь."))
    query = ad_query(d)
    if not query:
        return send(chat, fail("Сначала опиши вещь — по описанию найду похожие объявления."))
    m = None if force else fresh_market(d, query)
    if m:
        return edit(chat, mid, market_text(d, m), market_buttons(m)) if mid else \
            send(chat, market_text(d, m), market_buttons(m))
    status = fmt("🔎 Ищу похожие на Авито…", note=f"До {market.WANT} объявлений — это до минуты.")
    if mid:
        edit(chat, mid, status)
    else:
        mid = send(chat, status)["result"]["message_id"]
    tg("sendChatAction", chat_id=chat, action="typing")
    try:
        m = market_for(d, query)
    except market.Blocked as e:
        print("market blocked:", e)
        return edit(chat, mid, fail("Авито не пустил к поиску — попробуй через пару минут."),
                    [[b("🔁 Ещё раз", "mk|f"), {"text": "🔎 Открыть поиск",
                                                "url": market.search_url(query, st.get("region"))}],
                     [b("‹ Назад", "card")]])
    except Exception as e:
        print("market:", e)
        traceback.print_exc()
        return edit(chat, mid, fail(f"Не получилось открыть Авито: {esc(e)}"),
                    [[b("🔁 Ещё раз", "mk|f"), b("‹ Назад", "card")]])
    edit(chat, mid, market_text(d, m), market_buttons(m))


# ---------- обработчики ----------

HELP_BODY = "Пришли фото или видео вещи — уберу фон, напишу описание и подскажу цену."
HELP_STEPS = (
    "• Фото — до 10, можно альбомом.\n"
    "• Пара слов о вещи: бренд, размер, состояние.\n"
    "• Число — цена, другой текст — правка («короче»).\n"
    "• «Цена на Авито» — до 100 похожих объявлений.\n"
    "• Голосовые тоже понимаю.\n"
    "• /region moskva — цены по городу, /region all — по России."
)
HELP_NOTE = " · ".join(c + " " + w for c, w in (("/new", "новое"), ("/ad", "текущее"), ("/price", "цена"),
                                                      ("/app", "приложение")))


def help_text(name=""):
    hi = f"Привет, {esc(name)}! " if name else ""
    return "\n".join(["<b>🛍 Авито-помощник</b>", quote(hi + HELP_BODY), quote(HELP_STEPS, True),
                      f"<i>{HELP_NOTE}</i>"])


def help_buttons():
    rows = app_buttons()
    if draft():
        rows.append([b("📝 Текущее объявление", "card")])
    return rows


def run(chat, fn, *args, later=None):
    """Тяжёлое (фото, Claude, Авито) — в отдельном потоке, по одному делу на чат.
    later(chat, сек) — что сделать, если чат занят (фото не теряем, а обрабатываем чуть позже)."""
    def go():
        with lock:
            if chat in busy:
                if later:
                    later(chat, 3)
                else:
                    send(chat, "⏳ <b>Ещё занят предыдущим — секунду</b>")
                return
            busy.add(chat)
        try:
            fn(*args)
        except Exception as e:
            print("task:", e)
            traceback.print_exc()
            try:
                send(chat, fail(f"Что-то сломалось: {esc(e)}"))
            except Exception:
                pass
        finally:
            with lock:
                busy.discard(chat)
    threading.Thread(target=go, daemon=True).start()


def set_region(arg):
    """«moskva», «https://www.avito.ru/moskva/…», «all» -> код региона (и сброс старой цены) или None."""
    arg = re.sub(r"^https?://(www\.)?avito\.ru/", "", (arg or "").strip().lower()).split("/")[0].split("?")[0]
    if not re.fullmatch(r"[a-z_\-]+", arg or ""):
        return None
    with lock:
        st["region"] = arg
        d = st.get("draft")
        if d:
            d.pop("market", None)
        save()
    return arg


def archive():
    """Убрать текущее объявление в прошлые (они остаются в приложении)."""
    with lock:
        old = st.pop("draft", None)
        if old and (old.get("title") or old.get("photos")):
            st["ads"] = ([old] + [a for a in st.get("ads") or [] if a.get("id") != old.get("id")])[:KEEP_ADS - 1]
        save()


def new_ad(chat):
    archive()
    send(chat, fmt("🆕 Новое объявление", note="Пришли фото, видео или опиши вещь."))


def copy_text(chat):
    d = draft()
    if not d or not d.get("title"):
        return send(chat, fail("Описания ещё нет."))
    lines = ["<b>📋 Для Авито</b>", f"Название: <code>{esc(d['title'])}</code>"]
    if d.get("price"):
        lines.append(f"Цена: <code>{d['price']}</code>")
    lines.append("<i>Нажми на название или цену — скопируется. Описание — следующим сообщением.</i>")
    send(chat, "\n".join(lines))
    tg("sendMessage", chat_id=chat, text=d.get("description") or "")


def on_message(msg):
    chat = msg["chat"]["id"]
    frm = msg.get("from") or {}
    uid = frm.get("id")
    if msg["chat"].get("type") != "private":
        return
    text = (msg.get("text") or "").strip()
    if text.startswith("/start"):
        claim(uid)
    if not allowed(uid):
        send(chat, "🔒 Это личный бот.")
        return
    if media.kind(msg) == "photo":
        add_photo(chat, msg)
        return
    if media.kind(msg) == "voice":
        run(chat, hear, chat, msg)
        return
    if media.kind(msg) == "video":
        run(chat, from_video, chat, msg)
        return
    cmd = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
    if cmd in ("/start", "/help"):
        send(chat, help_text(frm.get("first_name") if cmd == "/start" else ""), help_buttons() or None)
    elif cmd == "/app":
        if public_url:
            send(chat, fmt("📱 Приложение", note="Объявления, описания и цены — на одном экране."), app_buttons())
        else:
            send(chat, fail(f"Приложение ещё настраивается: {esc(https_note)}"))
    elif cmd == "/new":
        new_ad(chat)
    elif cmd == "/ad":
        show_card(chat)
    elif cmd == "/price":
        run(chat, check_market, chat)
    elif cmd == "/photos":
        d = draft()
        paths = [p.get("clean") or p["orig"] for p in (d or {}).get("photos") or []]
        if paths:
            run(chat, send_album, chat, paths, "🖼 Фото для объявления")
        else:
            send(chat, fail("Фото пока нет — пришли их сюда."))
    elif cmd == "/region":
        arg = set_region(text.split(maxsplit=1)[1] if len(text.split()) > 1 else "")
        if not arg:
            return send(chat, fmt(f"📍 Регион: {esc(region_name())}",
                                  "<code>/region moskva</code> — как в адресе avito.ru/moskva\n"
                                  "<code>/region all</code> — вся Россия"))
        send(chat, fmt(f"📍 Регион: {esc(region_name(arg))}", note="Цены сравниваю здесь."))
    elif cmd == "/update" and uid == (st.get("owner") or min(ALLOWED or {0})):
        update(chat)
    elif cmd:
        send(chat, fail("Такой команды нет — /help"))
    elif not text:
        send(chat, fmt("📸 Пришли фото вещи", note="Или видео, текст, голосовое."))
    else:
        on_text(chat, text)


def hear(chat, msg):
    """Голосовое → текст → как будто его написали (уже в потоке run, поэтому описание пишем здесь же)."""
    tg("sendChatAction", chat_id=chat, action="typing")
    try:
        text = media.voice_text(TOKEN, msg)
    except media.MediaError as e:
        return send(chat, fail(f"Голосовое не разобрал: {esc(e)}"))
    send(chat, f"🎙 <i>«{esc(text)}»</i>")
    on_text(chat, text, now=True)


ASK_PRICE = re.compile(r"сколько\s+(\w+\s+){0,2}сто[ия]т|почём|почем\b|средн\w*\s+цен|за\s+сколько\s+продать|"
                       r"какую\s+(\w+\s+)?цену", re.I)


def on_text(chat, text, now=False):
    """Число — цена, у готового объявления — правка, иначе — рассказ о вещи. now=True — без нового потока."""
    def later(fn, *args):
        fn(*args) if now else run(chat, fn, *args)
    d = draft(create=True)
    price = parse_price(text)
    if price is not None and (d.get("title") or d.get("info") or d.get("photos")):
        d["price"] = price
        save()
        show_card(chat)  # в карточке уже новая цена
        return
    if ASK_PRICE.search(text) and (d.get("title") or d.get("info")):
        later(check_market, chat)  # «сколько такое стоит?» — средняя цена
        return
    with lock:
        d["info"] = (d.get("info", "") + "\n" + text).strip()
        save()
    if d.get("title"):
        later(write, chat, text)
    elif any(not p.get("clean") and not p.get("err") for p in d["photos"]):
        send(chat, fmt("👌 Запомнил", note="Опишу, как только обработаю фото."))
    else:
        later(write, chat)


def on_callback(cq):
    uid = cq["from"]["id"]
    msg = cq.get("message") or {}
    chat, mid = msg.get("chat", {}).get("id"), msg.get("message_id")
    data = (cq.get("data") or "").split("|")
    note = ""
    if not allowed(uid) or not chat:
        tg("answerCallbackQuery", callback_query_id=cq["id"])
        return
    d = draft()
    if data[0] == "gen":
        run(chat, write, chat, "", mid)
    elif data[0] == "re":
        run(chat, write, chat, "Напиши другой вариант: живее и убедительнее, факты те же.", mid)
    elif data[0] == "mk":
        run(chat, check_market, chat, len(data) > 1, mid)
    elif data[0] == "pr" and d:
        d["price"] = int(data[1])
        save()
        note = f"Цена {market.fmt_rub(d['price'])}"
        show_card(chat, mid)
    elif data[0] == "card":
        show_card(chat, mid)
    elif data[0] == "cp":
        copy_text(chat)
    elif data[0] == "ph":
        paths = [p.get("clean") or p["orig"] for p in (d or {}).get("photos") or []]
        if paths:
            run(chat, send_album, chat, paths, "🖼 Фото для объявления")
        else:
            note = "Фото пока нет"
    elif data[0] == "new":
        new_ad(chat)
    try:
        tg("answerCallbackQuery", callback_query_id=cq["id"], text=note)
    except Exception:
        pass


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

def app_buttons():
    return [[{"text": "📱 Приложение", "web_app": {"url": public_url + "/"}}]] if public_url else []


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


def all_ads():
    """Текущее объявление и прошлые (новые сверху)."""
    with lock:
        d = st.get("draft")
        return ([d] if d else []) + [a for a in st.get("ads") or [] if not d or a.get("id") != d.get("id")]


def find_ad(aid):
    return next((a for a in all_ads() if a.get("id") == aid), None)


def market_json(m):
    if not m:
        return None
    s = m.get("stats") or {}
    return {"query": m.get("query"), "seen": m.get("seen", 0), "n": s.get("n", 0), "median": s.get("median"),
            "fast": s.get("fast"), "high": s.get("high"), "min": s.get("min"), "max": s.get("max"),
            "prices": m.get("prices") or [], "sample": m.get("sample") or [],
            "when": datetime.datetime.fromtimestamp(m["ts"]).strftime("%d.%m %H:%M") if m.get("ts") else ""}


def ad_json(d):
    photos = d.get("photos") or []
    return {"id": d["id"], "current": d is st.get("draft"), "title": d.get("title") or "",
            "description": d.get("description") or "", "category": d.get("category") or "",
            "price": d.get("price"), "price_text": market.fmt_rub(d["price"]) if d.get("price") else "",
            "query": ad_query(d), "photos": [f"/api/photo/{d['id']}/{n}" for n in range(len(photos))],
            "clean": sum(1 for p in photos if p.get("clean")), "market": market_json(d.get("market"))}


def ad_view(d):
    """Объявление для приложения: ad_json + состояние каждого фото и дел (фон, описание)."""
    out = ad_json(d)
    photos = d.get("photos") or []
    date = re.match(r"\d{4}(\d\d)(\d\d)", d.get("id") or "")
    out.update({
        # ?c — у фото на белом свой адрес: кэш браузера не покажет оригинал вместо готового
        "photos": [{"url": f"/api/photo/{d['id']}/{n}" + ("?c" if p.get("clean") else ""),
                    "state": photo_state(p)} for n, p in enumerate(photos)],
        "pending": len(todo_photos(d)), "info": bool(d.get("info")), "questions": d.get("questions") or [],
        "date": f"{date.group(2)}.{date.group(1)}" if date else "",
        "job": d.get("job") or "", "error": d.get("error") or ""})
    return out


def photo_state(p):
    if p.get("clean"):
        return "clean"
    return "err" if p.get("err") else "wait"


def app_state():
    region = st.get("region") or "all"
    return {"region": region, "region_name": region_name(region), "ads": [ad_view(a) for a in all_ads()]}


def spawn(fn):
    threading.Thread(target=fn, daemon=True).start()


def web_job(d, name, fn):
    """Долгое дело из приложения (фон, описание) — в отдельном потоке. Ход виден в d["job"], приложение
    спрашивает /api/state, пока он не пустой; ошибка — в d["error"]. False — уже чем-то занято."""
    with lock:
        if d.get("job"):
            return False
        d["job"], d["error"] = name, ""

    def go():
        try:
            fn()
        except ai.AIError as e:
            d["error"] = f"Описание не получилось: {e}"
        except Exception as e:
            traceback.print_exc()
            d["error"] = f"Что-то сломалось: {e}"[:200]
        finally:
            with lock:
                d["job"] = ""
                save()
    spawn(go)
    return True


def web_photos(d):
    """Из приложения: убрать фон у новых фото, затем (если описания ещё нет) написать его."""
    ok, bad = clean_photos(d)
    if bad:
        d["error"] = f"{len(bad)} фото не получилось: {bad[0]}"
    if not d.get("title"):
        d["job"] = "write"
        compose(d)


def save_upload(data, ctype):
    """Фото из приложения (тело запроса — сама картинка) -> в текущее объявление. -> (код, ответ)."""
    if not ctype.startswith("image/") or len(data) < 100:
        return 400, {"error": "Это не фото"}
    d = draft(create=True)
    if len(d["photos"]) >= MAX_PHOTOS:
        return 400, {"error": f"На Авито хватит {MAX_PHOTOS} фото"}
    path = new_orig(d)
    with open(path, "wb") as f:
        f.write(data)
    keep_photo(d, path)
    return 200, {"ad": ad_view(d)}


def api_post(path, body, user=None):
    """POST /api/… от владельца. -> (код, ответ)."""
    if path == "/api/market":
        ad = find_ad(str(body.get("id"))) if body.get("id") else None
        if body.get("id") and ad is None:
            return 404, {"error": "Нет такого объявления"}
        query = str(body.get("query") or "").strip()[:100] or (ad_query(ad) if ad else "")
        if not query:
            return 400, {"error": "Напиши, что ищем"}
        m = fresh_market(ad, query) if ad and not body.get("force") else None
        try:
            m = m or market_for(ad if ad is not None else {}, query)  # просто запрос — без объявления
        except market.Blocked:
            return 503, {"error": "Авито не пустил к поиску — попробуй через пару минут",
                         "url": market.search_url(query, st.get("region"))}
        return 200, {"market": market_json(m), "url": market.search_url(query, st.get("region")),
                     "ad": ad_view(ad) if ad else None}
    if path == "/api/price":
        d = find_ad(str(body.get("id") or ""))
        try:
            price = int(body.get("price"))
        except (TypeError, ValueError):
            return 400, {"error": "Цена — число"}
        if not d or not 0 < price < 100_000_000:
            return 400, {"error": "Нет такого объявления или цены"}
        with lock:
            d["price"] = price
            save()
        return 200, {"ad": ad_view(d)}
    if path == "/api/process":  # после загрузки фото: фон и описание
        d = find_ad(str(body.get("id") or "")) or draft()
        if not d:
            return 404, {"error": "Нет такого объявления"}
        if todo_photos(d) or not d.get("title"):
            web_job(d, "bg" if todo_photos(d) else "write", lambda: web_photos(d))
        return 200, {"ad": ad_view(d)}
    if path == "/api/write":  # рассказ о вещи, правка или «другой вариант»
        d = find_ad(str(body.get("id"))) if body.get("id") else draft(create=True)
        if not d:
            return 404, {"error": "Нет такого объявления"}
        info = str(body.get("info") or "").strip()[:2000]
        request = str(body.get("request") or "").strip()[:500]
        if not (info or request or d.get("info") or d.get("photos")):
            return 400, {"error": "Расскажи о вещи или добавь фото"}
        add_info(d, info)
        if d.get("title") and info and not request:
            request = info
        if not web_job(d, "write", lambda: compose(d, request)):
            return 409, {"error": "Ещё занят — секунду"}
        return 200, {"ad": ad_view(d)}
    if path == "/api/new":
        archive()
        return 200, app_state()
    if path == "/api/region":
        if not set_region(str(body.get("region") or "")):
            return 400, {"error": "Регион — как в адресе avito.ru/moskva, или all"}
        return 200, app_state()
    if path == "/api/send":  # фото на белом — в чат, чтобы сохранить на телефон
        d = find_ad(str(body.get("id") or ""))
        chat = (user or {}).get("id")
        paths = [p.get("clean") or p["orig"] for p in (d or {}).get("photos") or []]
        if not d or not chat or not paths:
            return 400, {"error": "Фото пока нет"}
        spawn(lambda: send_album(chat, paths, (d.get("title") or "🖼 Фото для объявления")[:1000]))
        return 200, {"ok": True}
    return 404, {"error": "Нет такого метода"}


def asset_version():
    h = hashlib.sha1()
    for f in sorted(os.listdir(WEB)):
        with open(os.path.join(WEB, f), "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()[:10]


VERSION = asset_version() if os.path.isdir(WEB) else "0"


class Web(BaseHTTPRequestHandler):
    server_version = "Avito/1"

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
                return self.reply(200, app_state())
            m = re.fullmatch(r"/api/photo/([\w-]+)/(\d+)", path)
            if m:
                d = find_ad(m.group(1))
                photos = (d or {}).get("photos") or []
                n = int(m.group(2))
                p = photos[n] if n < len(photos) else {}
                file = p.get("clean") or p.get("orig")
                inside = os.path.abspath(file or "").startswith(os.path.abspath(DRAFTS) + os.sep)
                if file and inside and os.path.isfile(file):
                    with open(file, "rb") as f:
                        return self.reply(200, f.read(), "image/jpeg", "private, max-age=86400")
                return self.reply(404, {"error": "Нет фото"})
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
        if not web_user(self.headers):
            return self.reply(401, {"error": "Открой приложение из Telegram"})
        n = int(self.headers.get("Content-Length") or 0)
        if path == "/api/upload":
            if n > MAX_UPLOAD:
                return self.reply(413, {"error": "Фото больше 15 МБ"})
            return self.reply(*save_upload(self.rfile.read(n), self.headers.get("Content-Type") or ""))
        if n > 64 * 1024:
            return self.reply(413, {"error": "Слишком большой запрос"})
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self.reply(400, {"error": "Плохой запрос"})
        try:
            code, res = api_post(path, body, web_user(self.headers))
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
# Telegram открывает мини-приложение только по https. Адрес постоянный: avito.<ip-через-дефисы>.sslip.io,
# его проксирует Caddy планера на 127.0.0.1:AVITO_PORT. Как только адрес отвечает — ставим кнопку меню.

def set_menu():
    if not public_url:
        return False
    try:
        tg("setChatMenuButton", menu_button={"type": "web_app", "text": "Авито", "web_app": {"url": public_url + "/"}})
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
    if os.environ.get("AVITO_IP"):
        return os.environ["AVITO_IP"]
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
    return "avito." + ip.replace(".", "-") + ".sslip.io"


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
        sys.exit("Нет AVITO_TOKEN (токен от @BotFather). Впиши его в /etc/avito-bot.env")
    st = load()
    for a in all_ads():  # дела, прерванные перезапуском
        a.pop("job", None)
        for ph in a.get("photos") or []:
            ph.pop("work", None)
    os.makedirs(DRAFTS, exist_ok=True)
    threading.Thread(target=serve, daemon=True).start()
    setup_bot()
    if public_url:
        set_menu()
    else:
        threading.Thread(target=https, daemon=True).start()
    print("Авито-бот запущен")
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
