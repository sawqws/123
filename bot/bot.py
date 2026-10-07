#!/usr/bin/env python3
"""Telegram-бот: решает задания horodigital.ru через Claude Code (`claude -p`)
и показывает, что осталось сделать.

Переменные окружения:
  TELEGRAM_TOKEN  токен от @BotFather
  TG_ALLOWED_ID   твой Telegram ID (бот отвечает только тебе)
  HORO_LOGIN, HORO_PASSWORD  логин и пароль сайта (их читают скрипты horo/)

Только стандартная библиотека Python, ничего ставить не нужно.
Команда /update скачивает новую версию из GitHub и перезапускает бота.
"""
import datetime
import json
import os
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

TOKEN = os.environ["TELEGRAM_TOKEN"]
ALLOWED = int(os.environ["TG_ALLOWED_ID"])
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS = os.path.join(REPO, "horo", "tmp", "bot_settings.json")
API = f"https://api.telegram.org/bot{TOKEN}/"
LINK = re.compile(r"https://horodigital\.ru/student/topic/[0-9a-f-]{36}/task/[0-9a-f-]{36}\S*")
TIMEOUT = 15 * 60  # секунд на одно задание
MSK = ZoneInfo("Europe/Moscow")
DIGEST_HOUR = 15  # во сколько присылать ежедневную сводку (по Москве)

lock = threading.Lock()
offset = 0    # следующий update_id из Telegram
mode = {}     # chat -> "show" | "confirm_all"
pending = {}  # chat -> список ссылок для «Сдать все автотесты»

BTN_DO = "📝 Решить и сдать"
BTN_SHOW = "👀 Только ответы"
BTN_LIST = "📋 Что надо сделать"
BTN_LATE = "❗ Просрочки"
BTN_ALL = "⚡ Сдать все автотесты"
BTN_COMM = "💬 Комментарии учителя"
BTN_GRADES = "📊 Мои оценки"
BTN_DIGEST = "🔔 Сводка каждый день"
BTN_HELP = "❓ Помощь"
MENU = json.dumps({
    "keyboard": [
        [BTN_DO, BTN_SHOW],
        [BTN_LIST, BTN_LATE],
        [BTN_ALL, BTN_COMM],
        [BTN_GRADES, BTN_DIGEST],
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
    f"{BTN_DIGEST} — включить или выключить сводку каждый день в {DIGEST_HOUR}:00 по Москве.\n\n"
    "Можно просто прислать ссылку без кнопки: решу и отправлю.\n"
    "Задания, которые проверяет учитель (конспекты, фото, устные), я не отправляю, а присылаю готовый текст.\n"
    "/update — обновить бота до новой версии."
)


# ---------- Telegram ----------

def tg(method, **params):
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(API + method, data=data, timeout=70) as r:
        return json.load(r)


def send(chat, text, menu=False):
    text = text.strip() or "(пустой ответ)"
    parts = [text[i:i + 4000] for i in range(0, len(text), 4000)]  # лимит Telegram 4096 символов
    for k, part in enumerate(parts):
        extra = {"reply_markup": MENU} if menu and k == len(parts) - 1 else {}
        tg("sendMessage", chat_id=chat, text=part, disable_web_page_preview="true", **extra)


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


# ---------- Работа ----------

def node(*args, timeout=300):
    try:
        res = subprocess.run(["node", *args], cwd=REPO, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "Сайт не ответил вовремя, попробуй позже."
    if res.returncode != 0:
        return False, "Ошибка:\n" + res.stderr[-1500:]
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


def prompt_for(url, text, show):
    only_show = show or re.search(r"покажи|не отправляй|только ответ", text, re.I)
    how = (
        "Только реши и покажи ответы, на сайт НЕ отправляй (шаг 3 не делать)."
        if only_show
        else "Реши и отправь (шаг 3). Если ответ неоднозначный, а попытка последняя, не отправляй, а напиши варианты."
    )
    return (
        f"Задание: {url}\n"
        f"Сделай его по инструкции из CLAUDE.md. {how}\n"
        "Если это detailedAnswer, на сайт не отправляй: дай готовый текст ответа.\n"
        "Ответ пиши для Telegram, коротко, без markdown-таблиц: результат (баллы) и что написал по каждому вопросу."
    )


def solve(url, text="", show=False):
    t0 = time.time()
    cmd = [
        "claude", "-p", prompt_for(url, text, show),
        "--permission-mode", "acceptEdits",
        "--allowedTools", "Bash(node horo/*)", "Read", "Write", "Edit",
    ]
    try:
        res = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=TIMEOUT)
        out = res.stdout if res.returncode == 0 else f"Ошибка (код {res.returncode}):\n{res.stderr[-1500:]}\n{res.stdout[-1500:]}"
    except subprocess.TimeoutExpired:
        out = f"Не успел за {TIMEOUT // 60} минут, остановил."
    return f"{out.strip()}\n\n⏱ {int(time.time() - t0)} с"


def run_task(chat, url, text, show=False):
    if not lock.acquire(blocking=False):
        send(chat, "Сейчас делаю другое задание, пришли ссылку чуть позже.")
        return
    try:
        send(chat, "Принял, делаю… Обычно это 1–3 минуты.")
        send(chat, solve(url, text, show), menu=True)
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
    res = subprocess.run(["git", "pull", "--ff-only"], cwd=REPO, capture_output=True, text=True)
    send(chat, (res.stdout + res.stderr).strip()[-1500:] or "ok")
    if res.returncode == 0:
        send(chat, "Перезапускаюсь… Через 10–20 секунд нажми /start.")
        tg("getUpdates", offset=offset, timeout=0)  # подтверждаем /update, чтобы не выполнить его повторно
        os._exit(0)  # systemd сам запустит бота заново (Restart=always)


def digest_loop():
    sent_day = None
    while True:
        now = datetime.datetime.now(MSK)
        s = load_settings()
        if s.get("digest") and now.hour == DIGEST_HOUR and sent_day != now.date():
            sent_day = now.date()
            ok, out = node("horo/status.js")
            try:
                send(ALLOWED, "🔔 Сводка на сегодня\n" + out, menu=True)
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
    if mode.get(chat) == "confirm_all":
        mode.pop(chat)
        urls = pending.pop(chat, [])
        if text.lower() in ("да", "yes", "ок", "давай"):
            threading.Thread(target=run_all, args=(chat, urls), daemon=True).start()
        else:
            send(chat, "Отменил.", menu=True)
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
    while True:
        try:
            upd = tg("getUpdates", offset=offset, timeout=60)
        except Exception as e:  # сеть моргнула — пробуем снова
            print("getUpdates:", e)
            time.sleep(5)
            continue
        for u in upd.get("result", []):
            offset = u["update_id"] + 1
            msg = u.get("message") or {}
            if msg.get("from", {}).get("id") != ALLOWED or "chat" not in msg:
                continue
            try:
                handle(msg)
            except Exception as e:
                print("handle:", e)


if __name__ == "__main__":
    main()
