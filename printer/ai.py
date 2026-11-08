"""Claude в боте «Принтер»: вопросы текстом, голосом и фото, ответы со знанием текущей печати.

Думает Claude Code на сервере (`claude -p`, тот же вход, что у остальных ботов). Работает в отдельной папке вне
репозитория: не подхватывает чужие CLAUDE.md и читает файлы только из неё. Управлять принтером не может: команд нет,
править можно только исходники моделей (models/src). Собирает и нарезает их бот, в песочнице (printer/slicer.py).
Действия Claude предлагает метками в ответе, бот показывает их кнопками, нажимает Глеб.
"""
import json
import os
import re
import shutil
import subprocess
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(os.environ.get("PRINTER_HOME") or "/var/lib/printer-bot", "ai")
PHOTOS = "photos"
NO_TOOLS = ["Bash", "NotebookEdit", "Task", "Agent"]
ALLOWED = ["WebSearch", "WebFetch", "Edit(./models/src/**)", "Write(./models/src/**)"]
SECRET_ENV = re.compile(r"TOKEN|SECRET|PASSWORD|PASSWD|KEY|COOKIE|CREDENTIAL|OWNER", re.I)
PHOTO_DAYS = 7


class AIError(Exception):
    pass


def rules():
    with open(os.path.join(HERE, "prompt.md"), encoding="utf-8") as f:
        return f.read()


def clean_env():
    """Без токенов ботов и чужих ключей: свой вход Claude (ANTHROPIC_*, CLAUDE_*) остаётся."""
    return {k: v for k, v in os.environ.items() if k.startswith(("ANTHROPIC_", "CLAUDE_")) or not SECRET_ENV.search(k)}


def keep_photo(path, now=None):
    """Переносит фото в рабочую папку Claude и возвращает путь от неё. Старые фото удаляет."""
    folder = os.path.join(WORK, PHOTOS)
    os.makedirs(folder, exist_ok=True)
    limit = (now or time.time()) - PHOTO_DAYS * 86400
    for name in os.listdir(folder):
        old = os.path.join(folder, name)
        if os.path.getmtime(old) < limit:
            os.remove(old)
    name = uuid.uuid4().hex + (os.path.splitext(path)[1] or ".jpg")
    shutil.move(path, os.path.join(folder, name))
    return f"{PHOTOS}/{name}"


def ask(prompt, session=None, timeout=600):
    """(ответ, id разговора). Разговор потерян — начинает новый. Сбой — AIError с понятным текстом."""
    os.makedirs(WORK, exist_ok=True)
    cmd = ["claude", "-p", prompt, "--output-format", "json", "--append-system-prompt", rules(),
           "--max-turns", "25", "--disallowedTools", *NO_TOOLS, "--allowedTools", *ALLOWED]
    if session:
        cmd += ["--resume", session]
    try:
        # stdin закрыт: иначе claude -p дописывает к вопросу всё, что придёт на вход
        p = subprocess.run(cmd, cwd=WORK, env=clean_env(), stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           timeout=timeout)
    except FileNotFoundError:
        raise AIError("Claude Code не установлен на сервере") from None
    except subprocess.TimeoutExpired:
        raise AIError("Claude думал слишком долго, спроси ещё раз") from None
    try:
        res = json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else {}
    except ValueError:
        res = {}
    if p.returncode == 0 and not res.get("is_error") and (res.get("result") or "").strip():
        return res["result"].strip(), res.get("session_id") or session
    log = (p.stderr + p.stdout)[-1500:]
    print("claude ->", p.returncode, log, flush=True)
    low = log.lower()
    if session and ("no conversation" in low or "session" in low and "not found" in low):
        return ask(prompt, None, timeout)
    if any(w in low for w in ("login", "api key", "401", "unauthorized", "authenticat", "oauth")):
        raise AIError("Claude на сервере не вошёл в аккаунт")
    if any(w in low for w in ("rate limit", "usage limit", "limit reached", "429", "overloaded")):
        raise AIError("У Claude закончился лимит, попробуй позже")
    raise AIError("Claude не ответил, спроси ещё раз")
