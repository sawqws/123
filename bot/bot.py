#!/usr/bin/env python3
"""Telegram-бот: решает задания horodigital.ru через Claude Code (`claude -p`)
и показывает, что осталось сделать.

Переменные окружения:
  TELEGRAM_TOKEN  токен от @BotFather
  TG_ALLOWED_ID   твой Telegram ID (бот отвечает только тебе); если пусто,
                  владельцем становится тот, кто первым нажмёт /start
  HORO_LOGIN, HORO_PASSWORD  логин и пароль сайта (их читают скрипты horo/)

Только стандартная библиотека Python, ничего ставить не нужно.
Команда /update скачивает новую версию из GitHub и перезапускает бота (pm2 поднимет его сам).
Команда /emoji: пришли премиум-эмодзи, и бот будет ставить их вместо обычных в своих сообщениях
(Telegram показывает их, если у владельца бота есть Premium).
Задания с проверкой учителем: бот присылает черновик, Глеб правит или жмёт «Отправить»;
правки запоминаются (bot/drafts.py). /alphabet — алфавит его почерком (horo/alphabet.py).
"""
import datetime
import html
import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from zoneinfo import ZoneInfo

import drafts

TOKEN = os.environ["TELEGRAM_TOKEN"]
ALLOWED = int(os.environ.get("TG_ALLOWED_ID") or 0)  # 0: владельца ещё нет
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS = os.path.join(REPO, "horo", "tmp", "bot_settings.json")
API = f"https://api.telegram.org/bot{TOKEN}/"
LINK = re.compile(r"https://horodigital\.ru/student/topic/[0-9a-f-]{36}/task/[0-9a-f-]{36}\S*")
TIMEOUT = 15 * 60  # секунд на одно задание
MSK = ZoneInfo("Europe/Moscow")
DIGEST_HOUR = 15  # во сколько присылать ежедневную сводку (по Москве)

lock = threading.Lock()
offset = 0    # следующий update_id из Telegram
mode = {}     # chat -> "show" | "confirm_all" | "emoji" | "alphabet"
pending = {}  # chat -> список ссылок для «Сдать все автотесты»

BTN_DO = "📝 Решить и сдать"
BTN_SHOW = "👀 Только ответы"
BTN_LIST = "📋 Что надо сделать"
BTN_LATE = "❗ Просрочки"
BTN_ALL = "⚡ Сдать все автотесты"
BTN_COMM = "💬 Комментарии учителя"
BTN_GRADES = "📊 Мои оценки"
BTN_DIGEST = "🔔 Сводка каждый день"
BTN_HAND = "✍️ Мой почерк"
BTN_CHECK = "🩺 Проверка"
BTN_HELP = "❓ Помощь"
BUTTONS = {BTN_DO, BTN_SHOW, BTN_LIST, BTN_LATE, BTN_ALL, BTN_COMM, BTN_GRADES, BTN_DIGEST, BTN_HAND, BTN_CHECK, BTN_HELP}
MENU = json.dumps({
    "keyboard": [
        [BTN_DO, BTN_SHOW],
        [BTN_LIST, BTN_LATE],
        [BTN_ALL, BTN_COMM],
        [BTN_GRADES, BTN_DIGEST],
        [BTN_HAND, BTN_CHECK],
        [BTN_HELP],
    ],
    "resize_keyboard": True,
})
HELP = (
    "Что я умею:\n\n"
    f"{BTN_DO} — пришли ссылку на задание, я решу его и отправлю на сайт.\n"
    f"{BTN_SHOW} — пришли ссылку, я только покажу ответы, на сайт ничего не отправлю.\n"
    f"{BTN_LIST} — все несделанные обязательные задания по предметам.\n"
    f"{BTN_LATE} — только задания с прошедшим сроком.\n"
    f"{BTN_ALL} — найду все новые тесты с автопроверкой и сдам их по очереди (сначала спрошу).\n"
    f"{BTN_COMM} — что учитель написал по заданиям на доработке.\n"
    f"{BTN_GRADES} — оценки и уровни по предметам.\n"
    f"{BTN_DIGEST} — включить или выключить сводку каждый день в {DIGEST_HOUR}:00 по Москве.\n"
    f"{BTN_HAND} — пришлю алфавит, заполнишь своим почерком, и решения «на скрине» буду писать им.\n"
    f"{BTN_CHECK} — проверю, что всё работает: вход на сайт, Claude, почерк.\n\n"
    "Можно просто прислать ссылку без кнопки: решу и отправлю.\n"
    "Задания, которые проверяет учитель, сначала присылаю черновиком. Можно прислать исправленный текст "
    "или свои фото вместо моих, потом нажать «Отправить». Твои правки запоминаю и учитываю дальше.\n"
    "/emoji — поставить премиум-эмодзи вместо обычных (нужен Telegram Premium).\n"
    "/update — обновить бота до новой версии."
)


# ---------- Telegram ----------

def tg(method, **params):
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(API + method, data=data, timeout=70) as r:
        return json.load(r)


def premium(text):
    """Обычные эмодзи -> премиум (<tg-emoji>), если их прислали через /emoji. Текст экранируется для HTML."""
    text = html.escape(text, quote=False)
    emo = load_settings().get("emoji") or {}
    if not emo:
        return text
    keys = sorted(emo, key=len, reverse=True)
    rx = re.compile("|".join(map(re.escape, keys)))
    return rx.sub(lambda m: f'<tg-emoji emoji-id="{emo[m.group(0)]}">{m.group(0)}</tg-emoji>', text)


def send(chat, text, menu=False, markup=None):
    text = text.strip() or "(пустой ответ)"
    parts = [text[i:i + 4000] for i in range(0, len(text), 4000)]  # лимит Telegram 4096 символов
    for k, part in enumerate(parts):
        last = k == len(parts) - 1
        extra = {"reply_markup": markup or MENU} if last and (menu or markup) else {}
        try:
            tg("sendMessage", chat_id=chat, text=premium(part), parse_mode="HTML", disable_web_page_preview="true", **extra)
        except urllib.error.HTTPError:  # Telegram не принял разметку или эмодзи — шлём простым текстом
            tg("sendMessage", chat_id=chat, text=part, disable_web_page_preview="true", **extra)


def send_file(chat, path, caption=""):
    """Картинку — фото, остальное (и если фото не приняли) — файлом."""
    with open(path, "rb") as f:
        data = f.read()
    kinds = ([("sendPhoto", "photo")] if drafts.IMAGE.search(path) and not path.lower().endswith(".pdf") else []) + [("sendDocument", "document")]
    for method, field in kinds:
        b = uuid.uuid4().hex
        body = b""
        for k, v in {"chat_id": str(chat), "caption": caption}.items():
            body += f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
        body += (f'--{b}\r\nContent-Disposition: form-data; name="{field}"; filename="{os.path.basename(path)}"\r\n'
                 "Content-Type: application/octet-stream\r\n\r\n").encode() + data + f"\r\n--{b}--\r\n".encode()
        req = urllib.request.Request(API + method, data=body, headers={"Content-Type": f"multipart/form-data; boundary={b}"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            print(method, path, e)
    return None


def download(file_id):
    """Скачивает файл, присланный в Telegram: (байты, имя)."""
    path = tg("getFile", file_id=file_id)["result"]["file_path"]
    with urllib.request.urlopen(f"https://api.telegram.org/file/bot{TOKEN}/{path}", timeout=120) as r:
        return r.read(), os.path.basename(path)


def attachment(msg):
    """file_id картинки или файла из сообщения (фото — в самом большом размере)."""
    if msg.get("photo"):
        return msg["photo"][-1]["file_id"]
    if msg.get("document"):
        return msg["document"]["file_id"]
    return None


def utf16(text, off, length):
    """Кусок текста по смещениям Telegram (они в UTF-16)."""
    b = text.encode("utf-16-le")
    return b[off * 2:(off + length) * 2].decode("utf-16-le")


def save_emoji(chat, msg):
    """Запоминает премиум-эмодзи из сообщения: обычный эмодзи -> id премиального."""
    text = msg.get("text") or ""
    found = {}
    for e in msg.get("entities") or []:
        if e.get("type") == "custom_emoji":
            alt = utf16(text, e["offset"], e["length"])
            found[alt] = e["custom_emoji_id"]
            found[alt.replace("\ufe0f", "")] = e["custom_emoji_id"]  # и без невидимого вариационного знака
    if not found:
        send(chat, "Премиум-эмодзи в сообщении не нашёл. Пришли именно премиум-эмодзи (из набора Telegram Premium).", menu=True)
        return
    s = load_settings()
    s.setdefault("emoji", {}).update(found)
    save_settings(s)
    shown = sorted({a for a in found if "\ufe0f" not in a} | {a for a in found if a.replace("\ufe0f", "") not in found})
    send(chat, "Запомнил: " + " ".join(shown) + "\nТеперь в моих сообщениях эти эмодзи будут премиальными. "
               "Чтобы сбросить все: /emoji_reset", menu=True)


# ---------- Настройки ----------

def load_settings():
    try:
        with open(SETTINGS) as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(s):
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    with open(SETTINGS, "w") as f:
        json.dump(s, f)


def owner():
    return ALLOWED or int(load_settings().get("owner") or 0)


# ---------- Работа ----------

def node(*args, timeout=300, prog="node"):
    try:
        res = subprocess.run([prog, *args], cwd=REPO, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "Сайт не ответил вовремя, попробуй позже."
    if res.returncode != 0:
        return False, "Ошибка:\n" + (res.stderr or res.stdout)[-1500:]
    return True, res.stdout


def py(*args, timeout=300):
    return node(*args, timeout=timeout, prog="python3")


def claude(prompt, tools, timeout=TIMEOUT):
    """Запуск Claude Code без диалога: (успех, текст ответа)."""
    cmd = ["claude", "-p", prompt, "--permission-mode", "acceptEdits"] + (["--allowedTools", *tools] if tools else [])
    try:
        res = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"Не успел за {timeout // 60} минут, остановил."
    except FileNotFoundError:
        return False, "Claude Code не установлен (npm install -g @anthropic-ai/claude-code)."
    if res.returncode != 0:
        return False, f"Ошибка (код {res.returncode}):\n{res.stderr[-1500:]}\n{res.stdout[-1500:]}"
    return True, res.stdout


def run_script(chat, *args):
    if not lock.acquire(blocking=False):
        send(chat, "Сейчас занят заданием, попробуй через пару минут.")
        return
    try:
        send(chat, "Смотрю сайт…")
        send(chat, node(*args)[1], menu=True)
    finally:
        lock.release()


SOLVE_TOOLS = ["Bash(node horo/*)", "Bash(python3 horo/*)", "Bash(pdftoppm *)", "Bash(pdftotext *)", "Read", "Write", "Edit"]


def prompt_for(url, text, show):
    only_show = show or re.search(r"покажи|не отправляй|только ответ", text, re.I)
    how = (
        "Только реши и покажи ответы, на сайт НЕ отправляй (шаг 3 не делать)."
        if only_show
        else "Реши и отправь (шаг 3). Если ответ неоднозначный, а попытка последняя, не отправляй, а напиши варианты."
    )
    task = drafts.task_id(url)
    d = f"horo/tmp/{task}"
    return (
        f"Задание: {url}\n"
        f"Сделай его по инструкции из CLAUDE.md. {how}\n"
        f"Если это detailedAnswer, на сайт НЕ отправляй. Сделай черновик: текст для учителя запиши в {d}/draft.txt "
        "(каждая строка — абзац, только то, что пойдёт учителю). Если нужны картинки (решение от руки на скриншоте, "
        f"заполненный лист), сделай их по CLAUDE.md и положи в {d}/draft_files/. Скрипт рисования клади в {d}/ и запускай "
        f"`python3 {d}/draw.py` (в нём: import sys; sys.path.insert(0, 'horo'); from hand import Hand). "
        f"Сначала прочитай {os.path.relpath(drafts.STYLE, REPO)}, если он есть: это правила Глеба по его прошлым правкам, "
        "они важнее общих. Бот сам покажет черновик Глебу: в ответе текст черновика не повторяй, напиши 1–2 строки, что сделал.\n"
        "Ответ пиши для Telegram, коротко, без markdown-таблиц: результат (баллы) и что написал по каждому вопросу."
    )


def solve(url, text="", show=False):
    t0 = time.time()
    out = claude(prompt_for(url, text, show), SOLVE_TOOLS)[1]
    return f"{out.strip()}\n\n⏱ {int(time.time() - t0)} с"


def run_task(chat, url, text, show=False):
    if not lock.acquire(blocking=False):
        send(chat, "Сейчас делаю другое задание, пришли ссылку чуть позже.")
        return
    try:
        send(chat, "Принял, делаю… Обычно это 1–3 минуты.")
        task, t0 = drafts.task_id(url), time.time()
        drafts.clear(task)
        send(chat, solve(url, text, show), menu=True)
        if drafts.fresh(task, t0):
            offer_draft(chat, task, url)
    finally:
        lock.release()


# ---------- Черновики для учителя ----------

DRAFT_KB = json.dumps({"inline_keyboard": [[
    {"text": "✅ Отправить учителю", "callback_data": "send"},
    {"text": "❌ Не отправлять", "callback_data": "drop"},
]]})


def draft():
    return load_settings().get("draft")


def set_draft(d):
    s = load_settings()
    if d:
        s["draft"] = d
    else:
        s.pop("draft", None)
    save_settings(s)


def offer_draft(chat, task, url):
    title = drafts.task_info(task)[1]
    set_draft({"task": task, "url": url, "title": title, "pairs": [], "own_files": False})
    for f in drafts.files(task):
        send_file(chat, f)
    show_draft(chat)


def show_draft(chat):
    d = draft()
    text = drafts.read(d["task"])
    n = len(drafts.files(d["task"]))
    send(chat, f"📄 Черновик для учителя: {d['title']}\n\n{text or '(без текста)'}\n\n"
               + (f"Картинок: {n}\n" if n else "")
               + "Поправить: пришли исправленный текст целиком или свои фото вместо моих.", markup=DRAFT_KB)


def edit_draft(chat, text):
    """Глеб прислал готовый текст или просьбу («убери последний абзац»): просьбу применяет Claude."""
    d = draft()
    before = drafts.read(d["task"])
    if len(text) < 0.6 * len(before):
        if not lock.acquire(blocking=False):
            send(chat, "Сейчас занят, пришли правку чуть позже.")
            return
        try:
            send(chat, "Правлю…")
            path = os.path.relpath(drafts.text_path(d["task"]), REPO)
            ok, out = claude(
                f"В {path} черновик ответа учителю. Глеб написал про него: «{text}».\n"
                f"Если это готовый новый текст ответа, запиши его в {path} как есть. Если это просьба что-то изменить, "
                f"измени {path} по ней и больше ничего не трогай. Каждая строка файла — абзац. В ответе одна строка: что сделал.",
                ["Read", "Write", "Edit"], timeout=300)
        finally:
            lock.release()
        if not ok:
            send(chat, "Не получилось поправить:\n" + out, markup=DRAFT_KB)
            return
    else:
        drafts.write(d["task"], text)
    after = drafts.read(d["task"])
    if drafts.record_edit(d["task"], d["title"], before, after):
        d["pairs"].append([before, after, text])
        set_draft(d)
    send(chat, "Принял правку.")
    show_draft(chat)


def add_draft_photo(chat, msg):
    d = draft()
    data, name = download(attachment(msg))
    drafts.add_file(d["task"], data, name, replace=not d["own_files"])  # первое своё фото заменяет мои картинки
    d["own_files"] = True
    set_draft(d)
    n = len(drafts.files(d["task"]))
    send(chat, f"Фото добавил, всего картинок: {n}. Когда всё — жми «Отправить».", markup=DRAFT_KB)


def submit_draft(chat):
    if not lock.acquire(blocking=False):
        send(chat, "Сейчас занят заданием, нажми «Отправить» чуть позже.", markup=DRAFT_KB)
        return
    try:
        d = draft()
        if not d:
            send(chat, "Черновика уже нет.", menu=True)
            return
        if not os.path.exists(drafts.text_path(d["task"])):
            drafts.write(d["task"], "")
        send(chat, "Отправляю учителю…")
        ok, out = node("horo/answer.js", d["url"], drafts.text_path(d["task"]), *drafts.files(d["task"]), timeout=600)
        send(chat, out, menu=True)
        if not ok:
            send(chat, "Не отправилось. Черновик остался, можно нажать ещё раз.", markup=DRAFT_KB)
            return
        set_draft(None)
        if d["pairs"]:
            send(chat, "Запоминаю твои правки…")
            send(chat, learn_style(d["pairs"]), menu=True)
    finally:
        lock.release()


def learn_style(pairs):
    """Обобщает правки Глеба в правила (horo/tmp/style/style.md) для следующих ответов."""
    os.makedirs(drafts.STYLE_DIR, exist_ok=True)
    shown = "\n\n".join(f"БЫЛО:\n{p[0]}\n\nСТАЛО:\n{p[1]}" + (f"\n\nЧТО ОН НАПИСАЛ: {p[2]}" if len(p) > 2 and p[2] != p[1] else "")
                          for p in pairs)
    style = os.path.relpath(drafts.STYLE, REPO)
    ok, out = claude(
        "Глеб (ученик 8 класса) поправил черновик ответа учителю.\n\n" + shown + "\n\n"
        f"Обнови файл {style} (создай, если нет): короткий список правил, как Глеб хочет, чтобы были написаны ответы "
        "учителю (длина, тон, слова, оформление, чего избегать). Обобщай, не записывай содержание конкретного задания. "
        "Старые правила сохраняй, противоречащие заменяй новыми, не больше 30 пунктов. "
        "В ответе только новые или изменённые пункты, коротко, без markdown.",
        ["Read", "Write", "Edit"], timeout=300)
    return ("🧠 Запомнил:\n" + out.strip()) if ok else "Правки сохранил, но обобщить не вышло:\n" + out


def on_callback(cq):
    chat = cq["message"]["chat"]["id"]
    try:
        tg("answerCallbackQuery", callback_query_id=cq["id"])
        tg("editMessageReplyMarkup", chat_id=chat, message_id=cq["message"]["message_id"],  # убрать кнопки: не нажать дважды
           reply_markup=json.dumps({"inline_keyboard": []}))
    except urllib.error.HTTPError as e:
        print("callback:", e)
    if not draft():
        send(chat, "Этот черновик уже не актуален.", menu=True)
    elif cq.get("data") == "send":
        threading.Thread(target=submit_draft, args=(chat,), daemon=True).start()
    elif cq.get("data") == "drop":
        set_draft(None)
        send(chat, "Не отправляю. Если передумаешь, пришли ссылку ещё раз.", menu=True)


# ---------- Почерк ----------

ALPHA_DIR = os.path.join(REPO, "horo", "tmp", "alphabet")


def send_alphabet(chat):
    os.makedirs(ALPHA_DIR, exist_ok=True)
    send(chat, "Это алфавит из 3 листов. Открой каждый на iPad, напиши в клетках буквы своим почерком "
               "(как в заметках) и пришли обратно скриншотом или фото, можно все сразу. "
               "Пустые клетки можно оставить. Когда закончишь, напиши «готово».")
    for page in (1, 2, 3):
        out = os.path.join(ALPHA_DIR, f"alphabet_{page}.png")
        ok, err = py("horo/alphabet.py", "template", str(page), out)
        if not ok:
            send(chat, err, menu=True)
            return
        send_file(chat, out)
    mode[chat] = "alphabet"


def take_alphabet(chat, msg):
    if not lock.acquire(blocking=False):
        send(chat, "Сейчас занят заданием, пришли лист чуть позже.")
        return
    try:
        data, name = download(attachment(msg))
        path = os.path.join(ALPHA_DIR, f"in_{int(time.time() * 1000)}_{name}")
        with open(path, "wb") as f:
            f.write(data)
        ok, out = py("horo/alphabet.py", "ingest", path)
        send(chat, out.strip())
        if ok:
            prev = os.path.join(ALPHA_DIR, "preview.png")
            if py("horo/alphabet.py", "preview", prev)[0]:
                send_file(chat, prev, "Так теперь пишу твоим почерком. Пришли ещё лист или напиши «готово».")
    finally:
        lock.release()


# ---------- Проверка ----------

def check(chat):
    if not lock.acquire(blocking=False):
        send(chat, "Сейчас занят заданием, попробуй через пару минут.")
        return
    try:
        send(chat, "Проверяю, это до минуты…")
        git = lambda *a: subprocess.run(["git", *a], cwd=REPO, capture_output=True, text=True).stdout.strip()
        lines = [f"Версия: {git('rev-parse', '--abbrev-ref', 'HEAD')} {git('rev-parse', '--short', 'HEAD')}"]
        ok, out = node("horo/grades.js", timeout=180)
        lines.append("✅ Сайт: вход работает" if ok else "❌ Сайт: " + out.strip()[-300:])
        ok, out = claude("Ответь одним словом: ok", [], timeout=120)
        if ok and out.strip():
            lines.append("✅ Claude: отвечает")
        else:
            lines.append("❌ Claude: " + out.strip()[-300:] + "\nВойди на сервере: cd /root/horo && claude")
        ok, out = py("-c", "import numpy, scipy, skimage, PIL")
        lines.append("✅ Почерк: библиотеки есть" if ok else "❌ Почерк: нет библиотек, перезапусти установку (install.sh)")
        own = os.path.exists(os.path.join(REPO, "horo", "tmp", "fonts", "gleb_extra.npz"))
        lines.append("✍️ Алфавит твоим почерком: " + ("загружен" if own else f"ещё нет ({BTN_HAND})"))
        send(chat, "\n".join(lines), menu=True)
    finally:
        lock.release()


def find_auto_tests(chat):
    if not lock.acquire(blocking=False):
        send(chat, "Сейчас занят заданием, попробуй через пару минут.")
        return
    try:
        send(chat, "Ищу новые тесты с автопроверкой…")
        ok, out = node("horo/status.js", "--json")
        if not ok:
            send(chat, out, menu=True)
            return
        tests = [r for r in json.loads(out) if r["auto"] and r["rawStatus"] == "appointed"]
        if not tests:
            send(chat, "Новых тестов с автопроверкой нет 🎉", menu=True)
            return
        pending[chat] = [r["url"] for r in tests]
        mode[chat] = "confirm_all"
        lines = "\n".join(f"{i + 1}. {r['subj']}: {r['title']}" for i, r in enumerate(tests))
        send(chat, f"Нашёл {len(tests)}:\n{lines}\n\nСдать все? Напиши «да» или «нет».")
    finally:
        lock.release()


def run_all(chat, urls):
    if not lock.acquire(blocking=False):
        send(chat, "Сейчас занят заданием, попробуй через пару минут.")
        return
    try:
        for i, url in enumerate(urls, 1):
            send(chat, f"Тест {i} из {len(urls)}…")
            send(chat, solve(url))
        send(chat, "Все тесты сделаны ✅", menu=True)
    finally:
        lock.release()


def update(chat):
    git = lambda *a: subprocess.run(["git", *a], cwd=REPO, capture_output=True, text=True)
    old = git("rev-parse", "HEAD").stdout.strip()
    if git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() != "main":  # старые установки стояли на ветке
        res = git("fetch", "origin", "main")
        if res.returncode == 0:
            res = git("checkout", "-B", "main", "origin/main")
    else:
        res = git("pull", "--ff-only", "origin", "main")
    send(chat, (res.stdout + res.stderr).strip()[-1500:] or "ok")
    if res.returncode != 0:
        return
    if git("diff", "--name-only", old, "HEAD", "--", "package.json", "package-lock.json").stdout.strip():
        subprocess.run(["npm", "install", "--no-audit", "--no-fund"], cwd=REPO, capture_output=True)
    s = load_settings(); s["updated"] = chat; save_settings(s)
    send(chat, "Перезапускаюсь… Напишу, когда поднимусь.")
    tg("getUpdates", offset=offset, timeout=0)  # подтверждаем /update, чтобы не выполнить его повторно
    os._exit(0)  # pm2 сам запустит бота заново


def digest_loop():
    sent_day = None
    while True:
        now = datetime.datetime.now(MSK)
        s = load_settings()
        if s.get("digest") and now.hour == DIGEST_HOUR and sent_day != now.date():
            sent_day = now.date()
            ok, out = node("horo/status.js")
            try:
                send(owner(), "🔔 Сводка на сегодня\n" + out, menu=True)
            except Exception as e:
                print("digest:", e)
        time.sleep(60)


def handle(msg):
    chat = msg["chat"]["id"]
    text = (msg.get("text") or "").strip()
    name = msg.get("from", {}).get("first_name") or ""

    if text.startswith("/start"):
        send(chat, f"Привет{', ' + name if name else ''}! 👋\n"
                   "Я помогаю с заданиями на horodigital: решаю тесты, подсказываю ответы и слежу за сроками.\n\n"
                   "Выбери, что сделать 👇", menu=True)
        return
    if text.startswith("/update"):
        update(chat)
        return
    if text.startswith("/emoji_reset"):
        s = load_settings(); s.pop("emoji", None); save_settings(s)
        send(chat, "Премиум-эмодзи сброшены, снова обычные.", menu=True)
        return
    if text.startswith("/emoji"):
        mode[chat] = "emoji"
        send(chat, "Пришли одним сообщением премиум-эмодзи, которые хочешь видеть вместо обычных "
                   "(например 📝 👀 📋 ❗ ⚡ 💬 📊 🔔 ❓ ✅ 🎉 👋 ⏱ 🤔). Работает, если у тебя есть Telegram Premium.")
        return
    if mode.get(chat) == "emoji":
        mode.pop(chat)
        save_emoji(chat, msg)
        return
    if text.startswith("/alphabet_reset"):
        ok, out = py("horo/alphabet.py", "reset")
        send(chat, out, menu=True)
        return
    if text.startswith("/alphabet") or text == BTN_HAND:
        threading.Thread(target=send_alphabet, args=(chat,), daemon=True).start()
        return
    if text.startswith("/check") or text == BTN_CHECK:
        threading.Thread(target=check, args=(chat,), daemon=True).start()
        return
    if mode.get(chat) == "alphabet":
        if attachment(msg):
            threading.Thread(target=take_alphabet, args=(chat, msg), daemon=True).start()
            return
        if text.lower().strip(".! ") in ("готово", "все", "всё", "хватит"):
            mode.pop(chat)
            send(chat, "Готово ✍️ Теперь решения «на скрине» пишу твоим почерком.", menu=True)
            return
        if text not in BUTTONS and not LINK.search(text):
            send(chat, "Жду листы алфавита (скриншот или фото). Закончил — напиши «готово».")
            return
        mode.pop(chat)
    if mode.get(chat) == "confirm_all":
        mode.pop(chat)
        urls = pending.pop(chat, [])
        if text.lower() in ("да", "yes", "ок", "давай"):
            threading.Thread(target=run_all, args=(chat, urls), daemon=True).start()
        else:
            send(chat, "Отменил.", menu=True)
        return
    if draft() and attachment(msg):
        threading.Thread(target=add_draft_photo, args=(chat, msg), daemon=True).start()
        return
    if draft() and text and not text.startswith("/") and text not in BUTTONS and not LINK.search(text):
        if text.lower().strip(".! ") in ("ок", "ok", "да", "отправляй", "отправь"):
            threading.Thread(target=submit_draft, args=(chat,), daemon=True).start()
        else:
            edit_draft(chat, text)
        return

    simple = {
        BTN_LIST: ("horo/status.js",),
        BTN_LATE: ("horo/status.js", "--late"),
        BTN_COMM: ("horo/comments.js",),
        BTN_GRADES: ("horo/grades.js",),
    }
    if text in simple:
        threading.Thread(target=run_script, args=(chat, *simple[text]), daemon=True).start()
        return
    if text == BTN_HELP:
        send(chat, HELP, menu=True)
        return
    if text == BTN_DO:
        mode.pop(chat, None)
        send(chat, "Пришли ссылку на задание.")
        return
    if text == BTN_SHOW:
        mode[chat] = "show"
        send(chat, "Пришли ссылку, покажу ответы без отправки.")
        return
    if text == BTN_ALL:
        threading.Thread(target=find_auto_tests, args=(chat,), daemon=True).start()
        return
    if text == BTN_DIGEST:
        s = load_settings()
        s["digest"] = not s.get("digest")
        save_settings(s)
        send(chat, f"Сводка каждый день в {DIGEST_HOUR}:00 по Москве: {'включена ✅' if s['digest'] else 'выключена'}", menu=True)
        return

    m = LINK.search(text)
    if not m:
        send(chat, "Не понял 🤔 Пришли ссылку на задание или выбери действие в меню.", menu=True)
        return
    show = mode.pop(chat, None) == "show"
    threading.Thread(target=run_task, args=(chat, m.group(0), text, show), daemon=True).start()


def main():
    global offset
    threading.Thread(target=digest_loop, daemon=True).start()
    print("Бот запущен")
    s = load_settings()
    if s.pop("updated", None):  # поднялись после /update
        save_settings(s)
        try:
            head = subprocess.run(["git", "log", "-1", "--format=%h %s"], cwd=REPO, capture_output=True, text=True).stdout.strip()
            send(owner(), f"✅ Обновился и работаю. Версия: {head}", menu=True)
        except Exception as e:
            print("startup:", e)
    while True:
        try:
            upd = tg("getUpdates", offset=offset, timeout=60)
        except Exception as e:  # сеть моргнула — пробуем снова
            print("getUpdates:", e)
            time.sleep(5)
            continue
        for u in upd.get("result", []):
            offset = u["update_id"] + 1
            cq = u.get("callback_query")
            if cq:
                if cq.get("from", {}).get("id") == owner() and cq.get("message"):
                    try:
                        on_callback(cq)
                    except Exception as e:
                        print("callback:", e)
                continue
            msg = u.get("message") or {}
            uid = msg.get("from", {}).get("id")
            if "chat" not in msg:
                continue
            if not owner() and (msg.get("text") or "").startswith("/start"):
                s = load_settings(); s["owner"] = uid; save_settings(s)  # первый /start — это владелец
                print("Владелец бота:", uid)
            if uid != owner():
                continue
            try:
                handle(msg)
            except Exception as e:
                print("handle:", e)


if __name__ == "__main__":
    main()
