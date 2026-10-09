"""Черновики ответов на задания с проверкой учителем и память о правках Глеба.

Claude пишет черновик в horo/tmp/<задание>/draft.txt (каждая строка — абзац),
картинки для прикрепления — в horo/tmp/<задание>/draft_files/.
Глеб правит текст в Telegram, правки копятся в horo/tmp/style/edits.jsonl,
а выводы из них — в horo/tmp/style/style.md (его читает Claude перед следующим ответом).
Всё лежит в horo/tmp: репозиторий публичный, ответы и правки туда не попадают.
"""
import datetime
import json
import os
import re
import shutil

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = os.path.join(REPO, "horo", "tmp")
STYLE_DIR = os.path.join(TMP, "style")
STYLE = os.path.join(STYLE_DIR, "style.md")
EDITS = os.path.join(STYLE_DIR, "edits.jsonl")
IMAGE = re.compile(r"\.(png|jpe?g|webp|gif|pdf)$", re.I)


def task_id(url):
    m = re.search(r"/task/([0-9a-f-]{36})", url)
    return m.group(1) if m else None


def task_dir(task):
    return os.path.join(TMP, task)


def text_path(task):
    return os.path.join(task_dir(task), "draft.txt")


def files_dir(task):
    return os.path.join(task_dir(task), "draft_files")


def task_info(task):
    """(тип задания, название) из task.json, который пишет horo/fetch.js."""
    try:
        with open(os.path.join(task_dir(task), "task.json")) as f:
            t = json.load(f)
        return t["data"].get("type"), t["data"].get("title") or ""
    except Exception:
        return None, ""


def files(task):
    d = files_dir(task)
    if not os.path.isdir(d):
        return []
    return [os.path.join(d, f) for f in sorted(os.listdir(d)) if IMAGE.search(f)]


def read(task):
    try:
        with open(text_path(task)) as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


def fresh(task, since):
    """Черновик сделан после момента since (секунды epoch)?"""
    paths = [text_path(task)] + files(task)
    return any(os.path.exists(p) and os.path.getmtime(p) >= since - 1 for p in paths)


def clear(task):
    """Удаляет старый черновик перед новым решением."""
    if os.path.exists(text_path(task)):
        os.remove(text_path(task))
    shutil.rmtree(files_dir(task), ignore_errors=True)


def write(task, text):
    os.makedirs(task_dir(task), exist_ok=True)
    with open(text_path(task), "w") as f:
        f.write(text.strip() + "\n")


def add_file(task, data, name, replace=False):
    d = files_dir(task)
    if replace:
        shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{len(os.listdir(d)) + 1:02d}_{os.path.basename(name)}")
    with open(path, "wb") as f:
        f.write(data)
    return path


def record_edit(task, title, before, after):
    """Запоминает правку Глеба. Возвращает False, если текст не изменился."""
    if before.strip() == after.strip():
        return False
    os.makedirs(STYLE_DIR, exist_ok=True)
    row = {"date": datetime.date.today().isoformat(), "task": task, "title": title,
           "before": before.strip(), "after": after.strip()}
    with open(EDITS, "a") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return True


def recent_edits(n=5):
    try:
        with open(EDITS) as f:
            rows = [json.loads(line) for line in f if line.strip()]
    except FileNotFoundError:
        return []
    return rows[-n:]
