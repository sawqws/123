#!/usr/bin/env python3
"""Telegram-бот: получает ссылку на задание horodigital.ru, запускает Claude Code
(`claude -p`) в папке репозитория и присылает результат.

Переменные окружения:
  TELEGRAM_TOKEN  токен от @BotFather
  TG_ALLOWED_ID   твой Telegram ID (бот отвечает только тебе)
  HORO_LOGIN, HORO_PASSWORD  логин и пароль сайта (их читают скрипты horo/)

Только стандартная библиотека Python, ничего ставить не нужно.
"""
import json
import os
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request

TOKEN = os.environ["TELEGRAM_TOKEN"]
ALLOWED = int(os.environ["TG_ALLOWED_ID"])
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = f"https://api.telegram.org/bot{TOKEN}/"
LINK = re.compile(r"https://horodigital\.ru/student/topic/[0-9a-f-]{36}/task/[0-9a-f-]{36}\S*")
TIMEOUT = 15 * 60  # секунд на одно задание

lock = threading.Lock()


def tg(method, **params):
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(API + method, data=data, timeout=70) as r:
        return json.load(r)


def send(chat, text):
    text = text.strip() or "(пустой ответ)"
    for i in range(0, len(text), 4000):  # лимит Telegram 4096 символов
        tg("sendMessage", chat_id=chat, text=text[i:i + 4000])


def prompt_for(url, text):
    only_show = re.search(r"покажи|не отправляй|только ответ", text, re.I)
    mode = (
        "Только реши и покажи ответы, на сайт НЕ отправляй (шаг 3 не делать)."
        if only_show
        else "Реши и отправь (шаг 3). Если ответ неоднозначный, а попытка последняя, не отправляй, а напиши варианты."
    )
    return (
        f"Задание: {url}\n"
        f"Сделай его по инструкции из CLAUDE.md. {mode}\n"
        "Если это detailedAnswer, на сайт не отправляй: дай готовый текст ответа.\n"
        "Ответ пиши для Telegram, коротко, без markdown-таблиц: результат (баллы) и что написал по каждому вопросу."
    )


def run_task(chat, url, text):
    if not lock.acquire(blocking=False):
        send(chat, "Сейчас делаю другое задание, пришли ссылку чуть позже.")
        return
    try:
        send(chat, "Принял, делаю…")
        t0 = time.time()
        cmd = [
            "claude", "-p", prompt_for(url, text),
            "--permission-mode", "acceptEdits",
            "--allowedTools", "Bash(node horo/*)", "Read", "Write", "Edit",
        ]
        try:
            res = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=TIMEOUT)
            out = res.stdout if res.returncode == 0 else f"Ошибка (код {res.returncode}):\n{res.stderr[-1500:]}\n{res.stdout[-1500:]}"
        except subprocess.TimeoutExpired:
            out = f"Не успел за {TIMEOUT // 60} минут, остановил."
        send(chat, f"{out}\n\n⏱ {int(time.time() - t0)} с")
    finally:
        lock.release()


def main():
    offset = 0
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
            chat = msg.get("chat", {}).get("id")
            text = msg.get("text") or ""
            if msg.get("from", {}).get("id") != ALLOWED:
                continue
            if text.startswith("/start"):
                send(chat, "Пришли ссылку на задание. Добавь «покажи», если не нужно отправлять.")
                continue
            m = LINK.search(text)
            if not m:
                send(chat, "Не вижу ссылку на задание horodigital.ru")
                continue
            threading.Thread(target=run_task, args=(chat, m.group(0), text), daemon=True).start()


if __name__ == "__main__":
    main()
