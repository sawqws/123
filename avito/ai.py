"""Claude для объявлений: описание по фото и характеристикам, отбор похожих объявлений для цены.

Думает Claude Code на сервере (`claude -p`, тот же вход, что у HDP-бота). Работает в отдельной папке
вне репозитория, чтобы не подхватывать чужие CLAUDE.md.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(tempfile.gettempdir(), "avito-ai")
NO_TOOLS = ["--disallowedTools", "Bash", "Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch", "Task", "Agent"]


class AIError(Exception):
    pass


def rules():
    with open(os.path.join(HERE, "prompt.md"), encoding="utf-8") as f:
        return f.read()


def claude(prompt, images=(), system="", timeout=240):
    """Текст ответа. images — пути к картинкам: Claude откроет их инструментом Read."""
    os.makedirs(WORK, exist_ok=True)
    tools, paths = [], []
    if images:
        for img in images:
            dst = os.path.join(WORK, f"photo-{uuid.uuid4().hex[:8]}" + os.path.splitext(img)[1].lower())
            shutil.copyfile(img, dst)
            paths.append(dst)
        prompt += "\n\nФото вещи (открой каждое инструментом Read):\n" + "\n".join(paths)
        tools = ["--allowedTools", "Read", "--max-turns", str(len(paths) + 3)]
    cmd = ["claude", "-p", prompt, "--output-format", "json", *tools, *NO_TOOLS]
    if system:
        cmd += ["--append-system-prompt", system]
    try:
        p = subprocess.run(cmd, cwd=WORK, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise AIError("Claude Code не установлен на сервере") from None
    except subprocess.TimeoutExpired:
        raise AIError("Claude долго думает — попробуй ещё раз") from None
    finally:
        for f in paths:
            try:
                os.remove(f)
            except OSError:
                pass
    try:
        res = json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else {}
    except ValueError:
        res = {}
    if p.returncode != 0 or res.get("is_error") or not res.get("result"):
        log = (p.stderr + p.stdout)[-1500:]
        print("claude ->", p.returncode, log)
        low = log.lower()
        if any(w in low for w in ("login", "api key", "401", "unauthorized", "authenticat")):
            raise AIError("Claude на сервере не вошёл в аккаунт")
        if any(w in low for w in ("rate limit", "usage limit", "limit reached", "429", "overloaded")):
            raise AIError("у Claude закончился лимит, попробуй позже")
        raise AIError("Claude не ответил")
    return res["result"].strip()


def json_from(text):
    """Первый JSON-объект из ответа (Claude иногда оборачивает его в ```json)."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise AIError("Claude ответил не по формату")
    try:
        return json.loads(m.group(0))
    except ValueError:
        raise AIError("Claude ответил не по формату") from None


def write_ad(info, images=(), previous=None, request=""):
    """{title, description, query, category, questions}. info — всё, что рассказал продавец."""
    parts = ["Составь объявление для Авито."]
    parts.append("Что рассказал продавец:\n" + (info.strip() or "(ничего — только фото)"))
    if previous:
        parts.append("Прошлая версия объявления:\n" + json.dumps(previous, ensure_ascii=False))
    if request:
        parts.append("Продавец просит исправить: " + request.strip())
    ad = json_from(claude("\n\n".join(parts), images, system=rules()))
    title = str(ad.get("title") or "").strip().strip('"«»')
    if len(title) > 50:
        title = title[:50].rsplit(" ", 1)[0].rstrip(",;:- ")
    return {"title": title, "description": str(ad.get("description") or "").strip(),
            "query": str(ad.get("query") or title).strip(), "category": str(ad.get("category") or "").strip(),
            "questions": [str(q) for q in (ad.get("questions") or [])][:3]}


def pick_similar(item, items):
    """Номера объявлений из items, которые продают то же самое (а не чехол, запчасть или другую модель)."""
    lines = [f"{n}. {i['title']} — {i['price']} ₽" for n, i in enumerate(items)]
    prompt = (f"Продаю: {item}\n\nНиже объявления с Авито. Выбери те, где продают ТАКУЮ ЖЕ вещь "
              "(та же модель/тип, сопоставимая комплектация; не аксессуары, не запчасти, не «куплю», "
              "не другая модель). Ответ — только JSON: {\"ids\": [номера]}\n\n" + "\n".join(lines))
    ids = json_from(claude(prompt, timeout=120)).get("ids") or []
    return [items[n] for n in ids if isinstance(n, int) and 0 <= n < len(items)]
