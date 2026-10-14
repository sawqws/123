"""Чаты Авито в Telegram: новые сообщения покупателей → сюда, Claude пишет ответ, «✅ Отправить» — и он в Авито.

Официальный API Авито (api.avito.ru, Messenger API): ключи client_id и client_secret — avito.ru/professionals/api.
По каталогу Авито для разработчиков API мессенджера в «Товарах» работает только на подписке «Максимальный»
(без неё Авито отвечает 403 — бот так и пишет).

Режимы (st["av_mode"]): ask — черновик ответа и кнопка «Отправить» (по умолчанию); auto — отвечает сам и пишет,
что ответил (подозрительное и торг ниже минимума — всё равно спрашивает); off — только уведомления.
Модуль подключается из avito.py: chats.ui = модуль avito (st, lock, save, send, edit, b, esc, fmt, fail, ai, ...).
"""
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

ui = None
API = "https://api.avito.ru"
POLL = int(os.environ.get("AVITO_CHAT_POLL") or 30)  # секунд между проверками новых сообщений
MAX_TEXT = 1000  # лимит Авито на сообщение
MODES = {"ask": "✋ С подтверждением", "auto": "🤖 Автоответ", "off": "🔕 Только уведомления"}
NO_TARIFF = ("Авито не пускает к чатам: API мессенджера для товаров работает только на подписке «Максимальный» "
             "(Профиль → Для профессионалов → Тарифы).")


class AvitoError(Exception):
    """Понятная причина для Глеба."""


# ---------- API ----------

_token = {"value": None, "until": 0}


def keys():
    k = ui.st.get("av_keys") or {}
    cid = k.get("id") or os.environ.get("AVITO_CLIENT_ID")
    sec = k.get("secret") or os.environ.get("AVITO_CLIENT_SECRET")
    return (cid, sec) if cid and sec else None


def connected():
    return bool(keys() and ui.st.get("av_user"))


def http(method, path, params=None, body=None, form=None, auth=True):
    url = API + path + ("?" + urllib.parse.urlencode(params, doseq=True) if params else "")
    headers = {}
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body, ensure_ascii=False).encode()
        headers["Content-Type"] = "application/json"
    if auth:
        headers["Authorization"] = "Bearer " + token()
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        if e.code == 401 and auth:
            _token["until"] = 0
        if e.code == 403:
            raise AvitoError(NO_TARIFF)
        if e.code == 429:
            raise AvitoError("Авито просит подождать (слишком много запросов)")
        if e.code in (400, 401) and path == "/token":
            raise AvitoError("Ключи не подошли — проверь client_id и client_secret")
        raise AvitoError(f"Авито ответил {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise AvitoError(f"Авито недоступен: {e.reason}")


def token():
    if _token["value"] and time.time() < _token["until"]:
        return _token["value"]
    k = keys()
    if not k:
        raise AvitoError("Авито не подключён — /chats")
    r = http("POST", "/token", form={"grant_type": "client_credentials", "client_id": k[0], "client_secret": k[1]},
             auth=False)
    if not r.get("access_token"):
        raise AvitoError("Ключи не подошли — проверь client_id и client_secret")
    _token.update(value=r["access_token"], until=time.time() + int(r.get("expires_in") or 3600) - 120)
    return _token["value"]


def me():
    return http("GET", "/core/v1/accounts/self")


def uid():
    return ui.st.get("av_user", {}).get("id")


def list_chats(unread=False, limit=30):
    params = {"limit": limit, "chat_types": "u2i"}
    if unread:
        params["unread_only"] = "true"
    return http("GET", f"/messenger/v2/accounts/{uid()}/chats", params).get("chats") or []


def messages(chat_id, limit=30):
    r = http("GET", f"/messenger/v3/accounts/{uid()}/chats/{chat_id}/messages/", {"limit": limit})
    msgs = r if isinstance(r, list) else r.get("messages") or []
    return sorted(msgs, key=lambda m: m.get("created") or 0)


def send_text(chat_id, text):
    text = text.strip()[:MAX_TEXT]
    if not text:
        raise AvitoError("Пустой ответ")
    r = http("POST", f"/messenger/v1/accounts/{uid()}/chats/{chat_id}/messages",
             body={"message": {"text": text}, "type": "text"})
    try:
        http("POST", f"/messenger/v1/accounts/{uid()}/chats/{chat_id}/read")
    except AvitoError:
        pass
    return r


def mark_read(chat_id):
    http("POST", f"/messenger/v1/accounts/{uid()}/chats/{chat_id}/read")


# ---------- разбор ----------

def text_of(m):
    c = m.get("content") or {}
    t = m.get("type")
    if t == "text" or c.get("text"):
        return c.get("text") or ""
    return {"image": "📷 фото", "voice": "🎤 голосовое", "item": "📦 объявление", "location": "📍 геометка",
            "link": "🔗 " + str((c.get("link") or {}).get("url") or "ссылка"), "call": "📞 звонок",
            "deleted": "(удалено)"}.get(t, f"[{t}]")


def buyer_of(chat):
    me_id = uid()
    for u in chat.get("users") or []:
        if u.get("id") != me_id:
            return u.get("name") or "Покупатель"
    return "Покупатель"


def item_of(chat):
    v = ((chat.get("context") or {}).get("value")) or {}
    return {"id": v.get("id"), "title": v.get("title") or "", "price": v.get("price_string") or "",
            "url": v.get("url") or ""}


def key(chat_id):
    """Короткий ключ чата для кнопок (callback_data ≤ 64 байт)."""
    k = hashlib.sha1(chat_id.encode()).hexdigest()[:10]
    with ui.lock:
        ids = ui.st.setdefault("av_ids", {})
        if ids.get(k) != chat_id:
            ids[k] = chat_id
            if len(ids) > 300:
                for old in list(ids)[:100]:
                    ids.pop(old, None)
            ui.save()
    return k


def chat_id_of(k):
    return (ui.st.get("av_ids") or {}).get(k)


def local_ad(item):
    """Наше объявление из бота по названию — там описание, минимальная цена и заметки."""
    title = (item.get("title") or "").lower().strip()
    if not title:
        return None
    best = None
    for a in ui.all_ads():
        t = (a.get("title") or "").lower().strip()
        if t and (t == title or t in title or title in t):
            best = a
            break
    return best


def history(msgs):
    me_id = uid()
    return [{"who": "me" if (m.get("direction") == "out" or m.get("author_id") == me_id) else "buyer",
             "text": text_of(m)} for m in msgs if m.get("type") != "system"]


def suggest(chat, msgs):
    """Ответ Claude: {reply, note}."""
    item = item_of(chat)
    ad = dict(local_ad(item) or {})
    ad.setdefault("title", item["title"])
    ad["status"] = "на Авито"
    if not ad.get("price"):
        digits = re.sub(r"\D", "", item["price"])
        ad["price"] = int(digits) if digits else None
    hist = history(msgs)
    new = []
    while hist and hist[-1]["who"] == "buyer":
        new.insert(0, hist.pop()["text"])
    return ui.ai.reply_buyer(ad, "\n".join(new) or "(новое сообщение)", None, hist, ad.get("floor"),
                             ad.get("info", ""))


# ---------- уведомления ----------

def risky(note):
    return bool(re.search(r"мошен|предоплат|код|ссылк|курьер|осторожн|ниже минимал", note or "", re.I))


def poll_once():
    """Одна проверка: новые входящие → карточка в Telegram (и автоответ, если включён)."""
    if not connected():
        return 0
    seen = ui.st.setdefault("av_seen", {})
    n = 0
    for chat in list_chats(unread=True):
        last = chat.get("last_message") or {}
        if last.get("direction") != "in" or seen.get(chat["id"]) == last.get("id"):
            continue
        msgs = messages(chat["id"], 20)
        fresh = [m for m in msgs if m.get("direction") == "in" and (m.get("created") or 0) > seen_time(chat["id"])]
        with ui.lock:
            seen[chat["id"]] = last.get("id")
            ui.st.setdefault("av_seen_at", {})[chat["id"]] = max([m.get("created") or 0 for m in msgs] or [0])
            ui.save()
        if fresh:
            n += 1
            notify(chat, msgs, fresh)
    return n


def seen_time(chat_id):
    return (ui.st.get("av_seen_at") or {}).get(chat_id, 0)


def card_text(chat, fresh, extra=""):
    item = item_of(chat)
    head = f"💬 Авито · {ui.esc(buyer_of(chat))}"
    lines = [f"<i>{ui.esc(item['title'])}{' · ' + ui.esc(item['price']) if item['price'] else ''}</i>"]
    lines += [f"— {ui.esc(text_of(m))}" for m in fresh[-5:]]
    return ui.fmt(head, "\n".join(lines)) + (("\n" + extra) if extra else "")


def notify(chat, msgs, fresh):
    owner = ui.owner()
    if not owner:
        return
    mode = ui.st.get("av_mode") or "ask"
    k = key(chat["id"])
    if mode == "off":
        return ui.send(owner, card_text(chat, fresh), open_buttons(k))
    try:
        res = suggest(chat, msgs)
    except Exception as e:
        return ui.send(owner, card_text(chat, fresh, ui.fail(f"Ответ не придумал: {ui.esc(e)}")), open_buttons(k))
    reply, note = res["reply"][:MAX_TEXT], res.get("note") or ""
    if mode == "auto" and not risky(note):
        try:
            send_text(chat["id"], reply)
            return ui.send(owner, card_text(chat, fresh, f"🤖 <b>Ответил сам:</b>\n{ui.quote(ui.esc(reply))}"),
                           [[ui.b("✏️ Дописать", f"avw|{k}"), ui.b("📜 Чат", f"avo|{k}")]])
        except Exception as e:
            note = f"Не отправилось: {e}"
    with ui.lock:
        ui.st.setdefault("av_drafts", {})[k] = reply
        ui.save()
    ui.send(owner, card_text(chat, fresh, draft_text(reply, note)), draft_buttons(k))


def draft_text(reply, note=""):
    return (f"✍️ <b>Ответ:</b>\n{ui.quote(ui.esc(reply))}" + (f"\n<i>💡 {ui.esc(note)}</i>" if note else ""))


def draft_buttons(k):
    return [[ui.b("✅ Отправить", f"avs|{k}", "success")],
            [ui.b("🔄 Другой", f"avg|{k}"), ui.b("✏️ Свой ответ", f"avw|{k}"), ui.b("📜 Чат", f"avo|{k}")]]


def open_buttons(k):
    return [[ui.b("🤖 Предложить ответ", f"avg|{k}", "primary")], [ui.b("✏️ Ответить", f"avw|{k}"),
                                                                   ui.b("📜 Чат", f"avo|{k}")]]


def poller():
    while True:
        try:
            poll_once()
            ui.st.pop("av_error", None)
        except AvitoError as e:
            if ui.st.get("av_error") != str(e):  # одну и ту же ошибку пишем один раз
                ui.st["av_error"] = str(e)
                ui.save()
                if ui.owner():
                    ui.send(ui.owner(), ui.fail(f"Чаты Авито: {ui.esc(e)}"))
        except Exception as e:
            print("avito chats:", e)
        time.sleep(POLL)


def start():
    threading.Thread(target=poller, daemon=True).start()


# ---------- экраны ----------

KEYS_HELP = ("1. Открой avito.ru → Профиль → <b>Для профессионалов → API</b> (или avito.ru/professionals/api).\n"
             "2. «Получить ключи» → скопируй <b>client_id</b> и <b>client_secret</b>.\n"
             "3. Пришли их сюда одним сообщением через пробел — сообщение сразу удалю.")


def show_chats(chat, mid=None):
    if not keys():
        return ask_keys(chat, mid)
    try:
        rows = list_chats(limit=12)
    except AvitoError as e:
        return ui.show(chat, mid, ui.fail(ui.esc(e)), [[ui.b("🔑 Другие ключи", "avk")]])
    lines, btns = [], []
    for c in rows[:12]:
        last = c.get("last_message") or {}
        unread = last.get("direction") == "in" and seen_time(c["id"]) < (last.get("created") or 0)
        who = buyer_of(c)
        lines.append(f"{'🔵' if unread else '⚪️'} <b>{ui.esc(who)}</b> · {ui.esc(item_of(c)['title'][:40])}")
        lines.append(f"<i>{'Я: ' if last.get('direction') == 'out' else ''}{ui.esc(text_of(last)[:70])}</i>")
        btns.append(ui.b(ui.clip(f"{'🔵 ' if unread else ''}{who}"), f"avo|{key(c['id'])}"))
    mode = ui.st.get("av_mode") or "ask"
    text = ui.fmt(f"💬 Чаты Авито · {len(rows)}", "\n".join(lines) or "Пока пусто.",
                  f"Режим: {MODES[mode]}. Новые сообщения приходят сюда сами.")
    ui.show(chat, mid, text, [btns[i:i + 2] for i in range(0, len(btns), 2)] +
            [[ui.b("⚙️ Режим ответов", "avm"), ui.b("🔄 Обновить", "avl")]])


def ask_keys(chat, mid=None):
    with ui.lock:
        ui.st["wait"] = {"kind": "avkeys", "ts": time.time()}
        ui.save()
    ui.show(chat, mid, ui.fmt("🔑 Подключить чаты Авито", KEYS_HELP,
                              "Нужна подписка «Максимальный»: без неё Авито не даёт API чатов."),
            [[{"text": "🔑 Получить ключи", "url": "https://www.avito.ru/professionals/api"}],
             [ui.b("Отмена", "nowait")]])


def got_keys(chat, msg, text):
    parts = re.findall(r"[A-Za-z0-9_\-]{8,}", text)
    try:
        ui.tg("deleteMessage", chat_id=chat, message_id=msg["message_id"])  # ключи не должны висеть в чате
    except Exception:
        pass
    if len(parts) < 2:
        return ui.send(chat, ui.fail("Нужны два ключа через пробел: client_id client_secret"))
    with ui.lock:
        ui.st.pop("wait", None)
        ui.st["av_keys"] = {"id": parts[0], "secret": parts[1]}
        ui.save()
    _token["until"] = 0
    try:
        who = me()
    except AvitoError as e:
        with ui.lock:
            ui.st.pop("av_keys", None)
            ui.save()
        return ui.send(chat, ui.fail(ui.esc(e)), [[ui.b("🔑 Ещё раз", "avk")]])
    with ui.lock:
        ui.st["av_user"] = {"id": who.get("id"), "name": who.get("name")}
        ui.st.pop("av_error", None)
        ui.save()
    try:  # старые непрочитанные не шлём пачкой — отмечаем как виденные
        for c in list_chats(unread=True, limit=100):
            last = c.get("last_message") or {}
            ui.st.setdefault("av_seen", {})[c["id"]] = last.get("id")
            ui.st.setdefault("av_seen_at", {})[c["id"]] = last.get("created") or 0
        ui.save()
    except AvitoError as e:
        return ui.send(chat, ui.fail(ui.esc(e)))
    ui.send(chat, ui.fmt(f"✅ Авито подключён · {ui.esc(who.get('name') or '')}",
                         "Новые сообщения покупателей будут приходить сюда с готовым ответом — «✅ Отправить», "
                         "и он уйдёт в Авито.", "Режим можно сменить: /chats → «⚙️ Режим ответов»."),
            [[ui.b("💬 Чаты", "avl", "primary")]])


def open_chat(chat, k, mid=None):
    cid = chat_id_of(k)
    if not cid:
        return ui.send(chat, ui.fail("Чат устарел — открой /chats"))
    try:
        c = http("GET", f"/messenger/v2/accounts/{uid()}/chats/{cid}")
        msgs = messages(cid, 15)
    except AvitoError as e:
        return ui.send(chat, ui.fail(ui.esc(e)))
    item = item_of(c)
    lines = [f"<i>{ui.esc(item['title'])}{' · ' + ui.esc(item['price']) if item['price'] else ''}</i>"]
    for h in history(msgs)[-12:]:
        lines.append(f"{'<b>Я:</b>' if h['who'] == 'me' else '👤'} {ui.esc(h['text'][:300])}")
    ui.show(chat, mid, ui.fmt(f"💬 {ui.esc(buyer_of(c))}", "\n".join(lines)),
            [[ui.b("🤖 Предложить ответ", f"avg|{k}", "primary")],
             [ui.b("✏️ Ответить", f"avw|{k}"), ui.b("‹ Чаты", "avl")]] +
            ([[{"text": "Объявление на Авито", "url": item["url"]}]] if item["url"].startswith("http") else []))


def make_draft(chat, k):
    cid = chat_id_of(k)
    if not cid:
        return ui.send(chat, ui.fail("Чат устарел — открой /chats"))
    ui.tg("sendChatAction", chat_id=chat, action="typing")
    try:
        c = http("GET", f"/messenger/v2/accounts/{uid()}/chats/{cid}")
        res = suggest(c, messages(cid, 20))
    except Exception as e:
        return ui.send(chat, ui.fail(f"Не получилось: {ui.esc(e)}"), [[ui.b("🔁 Ещё раз", f"avg|{k}")]])
    with ui.lock:
        ui.st.setdefault("av_drafts", {})[k] = res["reply"][:MAX_TEXT]
        ui.save()
    ui.send(chat, draft_text(res["reply"][:MAX_TEXT], res.get("note")), draft_buttons(k))


def send_draft(chat, k, mid=None):
    text = (ui.st.get("av_drafts") or {}).get(k)
    cid = chat_id_of(k)
    if not text or not cid:
        return "Черновика уже нет"
    try:
        send_text(cid, text)
    except AvitoError as e:
        ui.send(chat, ui.fail(ui.esc(e)))
        return "Не отправилось"
    with ui.lock:
        ui.st["av_drafts"].pop(k, None)
        ui.save()
    if mid:
        ui.tg("editMessageReplyMarkup", chat_id=chat, message_id=mid,
              reply_markup={"inline_keyboard": [[ui.b("✅ Отправлено · 📜 Чат", f"avo|{k}")]]})
    return "Отправил в Авито"


def ask_own(chat, k):
    with ui.lock:
        ui.st["wait"] = {"kind": "avreply", "ts": time.time(), "chat": k}
        ui.save()
    ui.send(chat, ui.fmt("✏️ Что ответить?", note="Напиши или надиктуй — отправлю в Авито как есть."),
            [[ui.b("Отмена", "nowait")]])


def send_own(chat, k, text):
    cid = chat_id_of(k)
    with ui.lock:
        ui.st.pop("wait", None)
        ui.save()
    if not cid:  # ключ чата вытеснен (их храним 300) — не слать в /chats/None
        return ui.send(chat, ui.fail("Чат устарел — открой /chats"))
    try:
        send_text(cid, text)
    except AvitoError as e:
        return ui.send(chat, ui.fail(ui.esc(e)), [[ui.b("✏️ Ещё раз", f"avw|{k}")]])
    ui.send(chat, "✅ Отправил в Авито", [[ui.b("📜 Чат", f"avo|{k}")]])


def show_modes(chat, mid=None):
    cur = ui.st.get("av_mode") or "ask"
    ui.show(chat, mid, ui.fmt("⚙️ Как отвечать покупателям",
                              "✋ <b>С подтверждением</b> — пишу черновик, ты жмёшь «Отправить».\n"
                              "🤖 <b>Автоответ</b> — отвечаю сам и показываю, что ответил. Подозрительное "
                              "(предоплата, ссылки, коды) — всё равно спрошу.\n"
                              "🔕 <b>Только уведомления</b> — просто присылаю сообщения."),
            [[ui.b(("● " if m == cur else "") + label, f"avmode|{m}", "primary" if m == cur else None)]
             for m, label in MODES.items()] + [[ui.b("‹ Чаты", "avl")]])


# ---------- входы из avito.py ----------

def on_wait(chat, msg, text, w):
    """Ждали ключи или свой ответ. True — обработано."""
    if w["kind"] == "avkeys" and text:
        ui.run(chat, got_keys, chat, msg, text)
        return True
    if w["kind"] == "avreply":
        if ui.media.kind(msg) == "voice":
            def voice():
                try:
                    said = ui.media.voice_text(ui.TOKEN, msg)
                except ui.media.MediaError as e:
                    return ui.send(chat, ui.fail(f"Голосовое не разобрал: {ui.esc(e)}"))
                ui.send(chat, f"🎙 <i>«{ui.esc(said)}»</i>")
                send_own(chat, w["chat"], said)
            ui.run(chat, voice)
            return True
        if text:
            ui.run(chat, send_own, chat, w["chat"], text)
            return True
    return False


def on_callback(chat, mid, data):
    """Кнопки чатов. Текст всплывашки или None, если кнопка не наша."""
    if data[0] == "avl":
        ui.run(chat, show_chats, chat, mid)
    elif data[0] == "avk":
        ask_keys(chat, mid)
    elif data[0] == "avo":
        ui.run(chat, open_chat, chat, data[1], mid)
    elif data[0] == "avg":
        ui.run(chat, make_draft, chat, data[1])
        return "Пишу ответ…"
    elif data[0] == "avs":
        return send_draft(chat, data[1], mid)
    elif data[0] == "avw":
        ask_own(chat, data[1])
    elif data[0] == "avm":
        show_modes(chat, mid)
    elif data[0] == "avmode" and data[1] in MODES:
        with ui.lock:
            ui.st["av_mode"] = data[1]
            ui.save()
        show_modes(chat, mid)
        return MODES[data[1]]
    else:
        return None
    return ""
