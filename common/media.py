"""Голосовые и фото из Telegram для любого бота — только стандартная библиотека.

  import media                                   # sys.path.insert(0, <корень репо>/common)
  kind = media.kind(msg)                         # "voice" | "photo" | None
  text = media.voice_text(TOKEN, msg)            # голосовое/аудио/кружок -> текст (или MediaError)
  path = media.photo_path(TOKEN, msg, папка)     # фото/картинка-документ -> путь к .jpg

Распознавание — faster-whisper отдельным процессом (common/voice.py в common/.venv, его ставит common/setup.sh;
если его нет — берём tgbot/.venv Секретаря). Память модели (~400 МБ) освобождается сразу после распознавания.
"""
import json
import os
import subprocess
import tempfile
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PYTHONS = [os.path.join(HERE, ".venv", "bin", "python"), os.path.join(REPO, "tgbot", ".venv", "bin", "python")]
MAX_VOICE = 20 * 1024 * 1024  # Bot API отдаёт файлы до 20 МБ


class MediaError(Exception):
    """Понятная причина для пользователя."""


def kind(msg):
    if msg.get("voice") or msg.get("audio") or msg.get("video_note"):
        return "voice"
    doc = msg.get("document") or {}
    if msg.get("photo") or (doc.get("mime_type") or "").startswith("image/"):
        return "photo"
    if (doc.get("mime_type") or "").startswith("audio/"):
        return "voice"
    return None


def download(token, file_id, dst):
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/getFile",
                                 data=json.dumps({"file_id": file_id}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        path = json.load(r)["result"]["file_path"]
    urllib.request.urlretrieve(f"https://api.telegram.org/file/bot{token}/{path}", dst)
    return dst


def whisper_python():
    return next((p for p in PYTHONS if os.path.exists(p)), None)


def transcribe(path, timeout=300):
    py = whisper_python()
    if not py:
        raise MediaError("на сервере не установлено распознавание речи (common/setup.sh)")
    try:
        p = subprocess.run([py, os.path.join(HERE, "voice.py"), path], capture_output=True, text=True,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        raise MediaError("голосовое слишком длинное — разбей на части") from None
    if p.returncode != 0:
        print("voice.py:", (p.stderr or "")[-500:])
        raise MediaError("не получилось распознать голосовое")
    text = p.stdout.strip()
    if not text:
        raise MediaError("в голосовом не слышно слов")
    return text


def voice_text(token, msg):
    """Текст голосового/аудио/кружка из сообщения Telegram."""
    obj = msg.get("voice") or msg.get("audio") or msg.get("video_note") or msg.get("document") or {}
    if not obj.get("file_id"):
        raise MediaError("в сообщении нет звука")
    if (obj.get("file_size") or 0) > MAX_VOICE:
        raise MediaError("файл больше 20 МБ — Telegram не отдаёт такие ботам")
    with tempfile.TemporaryDirectory() as tmp:
        src = download(token, obj["file_id"], os.path.join(tmp, "voice"))
        return transcribe(src)


def photo_path(token, msg, folder=None):
    """Скачать фото (самое большое) или картинку-документ; вернуть путь к файлу."""
    if msg.get("photo"):
        fid, ext = msg["photo"][-1]["file_id"], ".jpg"
    else:
        doc = msg.get("document") or {}
        fid = doc.get("file_id")
        ext = os.path.splitext(doc.get("file_name") or "")[1].lower() or ".jpg"
    if not fid:
        raise MediaError("в сообщении нет фото")
    folder = folder or tempfile.mkdtemp(prefix="tgphoto-")
    os.makedirs(folder, exist_ok=True)
    return download(token, fid, os.path.join(folder, f"photo-{fid[-12:]}{ext}"))
