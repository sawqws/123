"""Claude для «Глаза»: разбор фото с очков и ответы в разговоре.

Думает Claude Code на сервере (`claude -p`, тот же вход, что у остальных ботов). Работает в отдельной папке
вне репозитория, чтобы не подхватывать чужие CLAUDE.md. Модели: EYE_MODEL — для фото (по умолчанию
та, что у Claude Code), EYE_TALK_MODEL — для разговора (по умолчанию haiku: отвечает быстрее всех).
"""
import json
import os
import re
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(tempfile.gettempdir(), "eye-ai")
NO_TOOLS = ["Bash", "Edit", "Write", "NotebookEdit", "Task", "Agent"]
LOOK_MODEL = os.environ.get("EYE_MODEL", "")
TALK_MODEL = os.environ.get("EYE_TALK_MODEL", "haiku")


class AIError(Exception):
    pass


def rules():
    with open(os.path.join(HERE, "prompt.md"), encoding="utf-8") as f:
        return f.read()


def run(prompt, tools=("Read",), model="", turns=6, timeout=180):
    """Текст ответа Claude. Если модель не подошла (нет такой) — повтор с моделью по умолчанию."""
    os.makedirs(WORK, exist_ok=True)
    cmd = ["claude", "-p", prompt, "--output-format", "json", "--append-system-prompt", rules(),
           "--max-turns", str(turns), "--disallowedTools", *NO_TOOLS]
    if tools:
        cmd += ["--allowedTools", *tools]
    if model:
        cmd += ["--model", model]
    try:
        p = subprocess.run(cmd, cwd=WORK, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise AIError("Claude Code не установлен на сервере") from None
    except subprocess.TimeoutExpired:
        raise AIError("Claude долго думает — спроси ещё раз") from None
    try:
        res = json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else {}
    except ValueError:
        res = {}
    if p.returncode == 0 and not res.get("is_error") and res.get("result"):
        return res["result"].strip()
    log = (p.stderr + p.stdout)[-1500:]
    print("claude ->", p.returncode, log)
    low = log.lower()
    if model and "model" in low and any(w in low for w in ("not found", "invalid", "unknown", "not available")):
        return run(prompt, tools, "", turns, timeout)
    if any(w in low for w in ("login", "api key", "401", "unauthorized", "authenticat")):
        raise AIError("Claude на сервере не вошёл в аккаунт")
    if any(w in low for w in ("rate limit", "usage limit", "limit reached", "429", "overloaded")):
        raise AIError("у Claude закончился лимит, попробуй позже")
    raise AIError("Claude не ответил")


def json_from(text):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None


def context(photos=(), place="", history=()):
    """Что Claude знает о происходящем: место, последние фото, последние реплики."""
    parts = []
    if place:
        parts.append("Где сейчас телефон Глеба (по GPS): " + place)
    if photos:
        lines = []
        for p in photos:
            ago = p.get("ago") or ""
            lines.append(f"- фото {ago} назад, файл {p['path']}" + (f", снято у: {p['place']}" if p.get("place") else "")
                         + (f"\n  разбор: {p['text']}" if p.get("text") else ""))
        parts.append("Последние фото с очков (самое новое — последнее):\n" + "\n".join(lines))
    if history:
        parts.append("Разговор до этого:\n" + "\n".join(
            ("Глеб: " if h["role"] == "user" else "Ты: ") + h["text"] for h in history))
    return "\n\n".join(parts)


def look(path, place="", history=(), question=""):
    """Разбор нового фото: {"say": что сказать вслух, "text": подробнее для чата}."""
    prompt = (context(place=place, history=history) + "\n\n" if place or history else "") + (
        f"Глеб только что сфотографировал очками: {path}\nОткрой фото инструментом Read.\n"
        + (f"Он спрашивает: {question}\n" if question else
           "Он хочет знать, что перед ним. Если это здание, памятник или место — что это и чем интересно; "
           "если вещь, еда, текст, вывеска — что это и главное о ней.\n")
        + "Ответ — только JSON без пояснений: {\"say\": \"1–3 предложения вслух\", "
          "\"text\": \"то же подробнее, 2–5 предложений, для чата\", \"title\": \"что это, 2–5 слов\"}")
    out = run(prompt, ("Read", "WebSearch", "WebFetch"), LOOK_MODEL, turns=8, timeout=240)
    data = json_from(out) or {}
    say = str(data.get("say") or "").strip() or clean(out)
    return {"say": say, "text": str(data.get("text") or say).strip(), "title": str(data.get("title") or "").strip()}


def talk(text, photos=(), place="", history=()):
    """Ответ вслух на реплику Глеба."""
    ctx = context(photos, place, history)
    prompt = (ctx + "\n\n" if ctx else "") + "Глеб говорит: " + text.strip() + "\n\nОтветь ему вслух."
    return clean(run(prompt, ("Read", "WebSearch", "WebFetch"), TALK_MODEL, turns=6, timeout=150))


def clean(text):
    """Убрать то, что синтезатор прочитает криво: markdown, эмодзи, ссылки."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[*_#`>|]+", "", text)
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.M)
    text = re.sub(r"[\U0001F000-\U0001FAFF☀-➿️]", "", text)
    return re.sub(r"\s+", " ", text).strip()
