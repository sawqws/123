#!/usr/bin/env python3
"""Telegram-бот «Авито»: фото вещи → белый фон, продающее описание, средняя цена по похожим объявлениям.

Как пользоваться:
  1. Прислать 1–10 фото (можно альбомом) — бот убирает фон, вещь остаётся на чисто белом квадрате.
  2. Написать, что это (бренд, размер, состояние…) — Claude пишет название и описание для Авито.
  3. Число («3500», «3.5к») — цена; любой другой текст — правка описания («короче», «добавь, что есть чек»).
  4. «📊 Средняя цена» — до 100 похожих объявлений на Авито → медиана, «быстро продать», «дороже».

Переменные окружения (на сервере — /etc/avito-bot.env):
  AVITO_TOKEN    токен от @BotFather
  AVITO_ALLOWED  Telegram ID через запятую; если пусто — владельцем станет первый /start
  AVITO_DATA     папка с данными (по умолчанию avito/data, вне git)
  AVITO_BG_MODEL модель rembg (по умолчанию isnet-general-use)

Сам бот — только стандартная библиотека Python. Фон убирает avito/photos.py в avito/.venv (rembg).
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
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid

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
ALBUM_WAIT = 2.5      # секунд ждём остальные фото альбома
MARKET_TTL = 6 * 3600  # средняя цена считается заново не чаще, чем раз в 6 часов (или по кнопке)

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


def cleanup_old(keep=15):
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


def b(text, data):
    return {"text": text, "callback_data": data}


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
    lines = []
    if d.get("title"):
        lines.append(f"📝 <b>{esc(d['title'])}</b>")
        if d.get("category"):
            lines.append(f"<i>{esc(d['category'])}</i>")
        lines += ["", esc(d.get("description") or "")]
    else:
        lines.append("📝 Описания пока нет — напиши пару слов о вещи или нажми «✨ Описание по фото».")
    lines.append("")
    lines.append(f"💰 <b>Цена: {market.fmt_rub(d['price'])}</b>" if d.get("price")
                 else "💰 Цена не указана — пришли число, например <code>3500</code>")
    clean = sum(1 for p in d.get("photos") or [] if p.get("clean"))
    if d.get("photos"):
        lines.append(f"🖼 Фото на белом фоне: {clean} из {len(d['photos'])}")
    if d.get("questions"):
        lines += ["", "❓ <b>Ответь — описание станет точнее:</b>"] + [f"• {esc(q)}" for q in d["questions"]]
    lines += ["", "<i>Любой текст — правка описания, число — цена.</i>"]
    return "\n".join(lines)


def card_buttons(d):
    rows = []
    if d.get("title") or d.get("info"):
        rows.append([b("📊 Средняя цена на Авито", "mk")])
    if d.get("title"):
        rows.append([b("🔄 Переписать", "re"), b("📋 Текст для копирования", "cp")])
    elif d.get("photos"):
        rows.append([b("✨ Описание по фото", "gen")])
    rows.append([b("🖼 Фото", "ph"), b("🆕 Новое объявление", "new")])
    return rows


def show_card(chat, mid=None):
    d = draft()
    if not d:
        return send(chat, "Пока нет объявления. Пришли фото вещи 📸")
    text, btns = card(d), card_buttons(d)
    return edit(chat, mid, text, btns) if mid else send(chat, text, btns)


# ---------- фото ----------

def download(file_id, dst):
    info = tg("getFile", file_id=file_id)["result"]
    urllib.request.urlretrieve(FILES + info["file_path"], dst)


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
            send(chat, f"На Авито хватит {MAX_PHOTOS} фото — остальные пропускаю.")
        return
    n = len(d["photos"]) + 1
    path = os.path.join(ddir(d), f"orig_{n:02d}_{uuid.uuid4().hex[:4]}.jpg")
    download(fid, path)
    with lock:
        d["photos"].append({"orig": path, "clean": None})
        cap = (msg.get("caption") or "").strip()
        if cap:
            d["info"] = (d.get("info", "") + "\n" + cap).strip()
        save()
    schedule_photos(chat, ALBUM_WAIT)


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


def process_photos(chat):
    d = draft()
    if not d:
        return
    todo = [p for p in d["photos"] if not p.get("clean") and not p.get("err")]
    if not todo:
        return
    msg = send(chat, f"🪄 Убираю фон с {len(todo)} фото…")["result"]["message_id"]
    tg("sendChatAction", chat_id=chat, action="upload_photo")
    pairs = [(p["orig"], p["orig"].replace("orig_", "white_")) for p in todo]
    res = {r["src"]: r for r in remove_bg(pairs)}
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
    try:
        tg("deleteMessage", chat_id=chat, message_id=msg)
    except Exception:
        pass
    if ok:
        send_album(chat, ok, f"✅ Фон убран: {len(ok)} фото" + (" — сохрани их для Авито" if len(ok) > 1 else ""))
    if bad:
        send(chat, f"😕 {len(bad)} фото не получилось: {esc(bad[0])}. На Авито можно взять оригинал.")
    if d.get("title"):
        show_card(chat)
    elif d.get("info"):
        write(chat)  # подпись к фото уже есть — сразу пишем описание
    else:
        send(chat, "✍️ <b>Расскажи о вещи</b> — что это, бренд/модель, размер, состояние, комплект, "
                   "почему продаёшь, как отдашь. Можно коротко, в свободной форме.\n\n"
                   "Или нажми — опишу по фото:", [[b("✨ Описание по фото", "gen")]])


# ---------- описание ----------

def write(chat, request="", mid=None):
    d = draft(create=True)
    status = "✍️ Переписываю…" if request or d.get("title") else "✍️ Пишу описание…"
    mid = ((edit(chat, mid, status) if mid else send(chat, status)).get("result") or {}).get("message_id", mid)
    tg("sendChatAction", chat_id=chat, action="typing")
    images = [p.get("orig") for p in d.get("photos") or []][:6]
    prev = {k: d[k] for k in ("title", "description") if d.get(k)} or None
    try:
        ad = ai.write_ad(d.get("info", ""), images, prev, request)
    except ai.AIError as e:
        return edit(chat, mid, f"😕 Не получилось: {esc(e)}", [[b("🔁 Ещё раз", "gen")]])
    with lock:
        if ad["query"] != d.get("query"):
            d.pop("market", None)
        d.update(ad)
        save()
    show_card(chat, mid)


# ---------- средняя цена ----------

def market_text(d, m):
    region = st.get("region") or "all"
    where = "по всей России" if region == "all" else f"регион «{region}»"
    s = m.get("stats")
    head = f"📊 <b>Цены на Авито</b> · «{esc(m['query'])}» · {esc(where)}"
    if not s:
        return head + f"\n\nПохожих объявлений не нашлось (просмотрел {m.get('seen', 0)}). " \
                      "Уточни описание — поищу снова."
    lines = [head, "",
             f"Похожих объявлений: <b>{s['n']}</b> из {m.get('seen', s['total'])} просмотренных", "",
             f"🎯 Средняя (медиана): <b>{market.fmt_rub(s['median'])}</b>",
             f"⚡ Продать быстро: {market.fmt_rub(s['fast'])}",
             f"💎 Дороже рынка: {market.fmt_rub(s['high'])}",
             f"Разброс: {market.fmt_rub(s['min'])} – {market.fmt_rub(s['max'])}", "",
             "<i>Это цены в объявлениях, а не цены продажи. Нажми, чтобы поставить:</i>"]
    if m.get("ts"):
        lines.append(f"<i>Проверено {datetime.datetime.fromtimestamp(m['ts']):%d.%m %H:%M}</i>")
    return "\n".join(lines)


def market_buttons(m):
    s = m.get("stats")
    rows = []
    if s:
        rows.append([b(f"⚡ {market.fmt_rub(s['fast'])}", f"pr|{s['fast']}"),
                     b(f"🎯 {market.fmt_rub(s['median'])}", f"pr|{s['median']}"),
                     b(f"💎 {market.fmt_rub(s['high'])}", f"pr|{s['high']}")])
    rows.append([{"text": "🔎 Открыть поиск на Авито", "url": market.search_url(m["query"], st.get("region"))}])
    rows.append([b("🔄 Проверить заново", "mk|f"), b("‹ К объявлению", "card")])
    return rows


def check_market(chat, force=False):
    d = draft()
    if not d:
        return send(chat, "Сначала пришли фото или опиши вещь.")
    query = d.get("query") or d.get("title") or (d.get("info") or "")[:60]
    if not query:
        return send(chat, "Сначала опиши вещь — по описанию найду похожие объявления.")
    m = d.get("market")
    if m and not force and m.get("query") == query and time.time() - m.get("ts", 0) < MARKET_TTL:
        return send(chat, market_text(d, m), market_buttons(m))
    mid = send(chat, f"🔎 Смотрю до {market.WANT} похожих объявлений на Авито… Это до минуты.")["result"]["message_id"]
    tg("sendChatAction", chat_id=chat, action="typing")
    try:
        items = market.collect(query, st.get("region") or "all")
    except market.Blocked as e:
        print("market blocked:", e)
        return edit(chat, mid, "🚫 Авито не пустил сервер к поиску (защита от ботов). Попробую через пару минут — "
                               "нажми «Ещё раз».", [[b("🔁 Ещё раз", "mk|f")],
                                                     [{"text": "🔎 Открыть поиск",
                                                       "url": market.search_url(query, st.get("region"))}]])
    except Exception as e:
        print("market:", e)
        traceback.print_exc()
        return edit(chat, mid, f"😕 Не получилось открыть Авито: {esc(e)}", [[b("🔁 Ещё раз", "mk|f")]])
    similar = items
    if items:
        try:
            similar = ai.pick_similar(f"{d.get('title') or query}. {d.get('info', '')[:300]}", items)
        except ai.AIError as e:
            print("pick_similar:", e)
            similar = market.relevant(query, items)
    m = {"query": query, "ts": time.time(), "seen": len(items), "stats": market.stats(similar),
         "sample": [{"t": i["title"], "p": i["price"], "u": i["url"]} for i in similar[:10]]}
    with lock:
        d["market"] = m
        save()
    edit(chat, mid, market_text(d, m), market_buttons(m))


# ---------- обработчики ----------

HELP = (
    "🛍 <b>Авито-помощник</b>\n\n"
    "1. Пришли фото вещи (до 10, можно альбомом) — уберу фон, останется вещь на белом.\n"
    "2. Напиши, что это: бренд, размер, состояние, комплект — сделаю продающее описание.\n"
    "3. Пришли число — это цена. Любой другой текст — правка: «короче», «добавь, что есть чек».\n"
    "4. «📊 Средняя цена» — посмотрю до 100 похожих объявлений и подскажу цену.\n"
    "🎙 Можно голосовыми — пойму так же, как текст.\n\n"
    "/new — новое объявление · /ad — текущее · /price — цена на рынке\n"
    "/region moskva — сравнивать цены по городу (как в адресе avito.ru/<b>moskva</b>), /region all — по России"
)


def run(chat, fn, *args, later=None):
    """Тяжёлое (фото, Claude, Авито) — в отдельном потоке, по одному делу на чат.
    later(chat, сек) — что сделать, если чат занят (фото не теряем, а обрабатываем чуть позже)."""
    def go():
        with lock:
            if chat in busy:
                if later:
                    later(chat, 3)
                else:
                    send(chat, "⏳ Ещё занят предыдущим — секунду.")
                return
            busy.add(chat)
        try:
            fn(*args)
        except Exception as e:
            print("task:", e)
            traceback.print_exc()
            try:
                send(chat, f"😕 Что-то сломалось: {esc(e)}")
            except Exception:
                pass
        finally:
            with lock:
                busy.discard(chat)
    threading.Thread(target=go, daemon=True).start()


def new_ad(chat):
    with lock:
        st.pop("draft", None)
        save()
    send(chat, "🆕 Новое объявление. Пришли фото вещи 📸 (или просто опиши её текстом).")


def copy_text(chat):
    d = draft()
    if not d or not d.get("title"):
        return send(chat, "Описания ещё нет.")
    send(chat, f"<b>Название</b> (нажми, чтобы скопировать):\n<code>{esc(d['title'])}</code>")
    tg("sendMessage", chat_id=chat, text=d.get("description") or "")
    if d.get("price"):
        send(chat, f"<b>Цена:</b> <code>{d['price']}</code>")


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
    cmd = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
    if cmd in ("/start", "/help"):
        hi = f"Привет, {esc(frm.get('first_name'))}!\n\n" if cmd == "/start" and frm.get("first_name") else ""
        send(chat, hi + HELP)
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
            send(chat, "Фото пока нет — пришли их сюда.")
    elif cmd == "/region":
        arg = text.split(maxsplit=1)[1].strip().lower() if len(text.split()) > 1 else ""
        arg = re.sub(r"^https?://(www\.)?avito\.ru/", "", arg).split("/")[0].split("?")[0]
        if not re.fullmatch(r"[a-z_\-]+", arg or ""):
            return send(chat, f"Сейчас: <b>{esc(st.get('region') or 'all')}</b>\n\n"
                              "Пришли так: <code>/region moskva</code> (как в адресе avito.ru/moskva) "
                              "или <code>/region all</code> — вся Россия.")
        st["region"] = arg
        d = draft()
        if d:
            d.pop("market", None)
        save()
        send(chat, f"📍 Цены сравниваю: <b>{'по всей России' if arg == 'all' else esc(arg)}</b>")
    elif cmd == "/update" and uid == (st.get("owner") or min(ALLOWED or {0})):
        update(chat)
    elif cmd:
        send(chat, "Такой команды нет. /help — что я умею.")
    elif not text:
        send(chat, "Пришли фото вещи или напиши текстом (или голосовым), что продаём.")
    else:
        on_text(chat, text)


def hear(chat, msg):
    """Голосовое → текст → как будто его написали (уже в потоке run, поэтому описание пишем здесь же)."""
    tg("sendChatAction", chat_id=chat, action="typing")
    try:
        text = media.voice_text(TOKEN, msg)
    except media.MediaError as e:
        return send(chat, f"🎙 {esc(e)}")
    send(chat, f"🎙 <i>«{esc(text)}»</i>")
    on_text(chat, text, now=True)


def on_text(chat, text, now=False):
    """Число — цена, у готового объявления — правка, иначе — рассказ о вещи. now=True — без нового потока."""
    def later(fn, *args):
        fn(*args) if now else run(chat, fn, *args)
    d = draft(create=True)
    price = parse_price(text)
    if price is not None and (d.get("title") or d.get("info") or d.get("photos")):
        d["price"] = price
        save()
        send(chat, f"💰 Цена: <b>{market.fmt_rub(price)}</b>")
        show_card(chat)
        return
    with lock:
        d["info"] = (d.get("info", "") + "\n" + text).strip()
        save()
    if d.get("title"):
        later(write, chat, text)
    elif any(not p.get("clean") and not p.get("err") for p in d["photos"]):
        send(chat, "👌 Запомнил. Допишу описание, как только обработаю фото.")
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
        run(chat, check_market, chat, len(data) > 1)
    elif data[0] == "pr" and d:
        d["price"] = int(data[1])
        save()
        note = f"Цена {market.fmt_rub(d['price'])}"
        show_card(chat)
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
    send(chat, esc((res.stdout + res.stderr).strip()[-1500:] or "ok"))
    if res.returncode == 0:
        send(chat, "Перезапускаюсь…")
        tg("getUpdates", offset=offset, timeout=0)
        os._exit(0)  # pm2 поднимет заново


def main():
    global offset, st
    if not TOKEN:
        sys.exit("Нет AVITO_TOKEN (токен от @BotFather). Впиши его в /etc/avito-bot.env")
    st = load()
    os.makedirs(DRAFTS, exist_ok=True)
    setup_bot()
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
