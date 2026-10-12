"""Голосовые и фото из Telegram для любого бота — только стандартная библиотека.

  import media                                   # sys.path.insert(0, <корень репо>/common)
  kind = media.kind(msg)                         # "voice" | "photo" | "video" | None
  text = media.voice_text(TOKEN, msg)            # голосовое/аудио/кружок/видео -> текст (или MediaError)
  path = media.photo_path(TOKEN, msg, папка)     # фото/картинка-документ -> путь к .jpg
  v = media.video(TOKEN, msg, папка)             # видео/кружок/гифка -> {"frames": [.jpg], "text": речь, "seconds"}
  m = media.understand(TOKEN, msg, папка)        # что угодно -> {"kind", "text", "images"}: одна функция на всё

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
    doc = msg.get("document") or {}
    mime = doc.get("mime_type") or ""
    if msg.get("voice") or msg.get("audio") or mime.startswith("audio/"):
        return "voice"
    if msg.get("photo") or mime.startswith("image/"):
        return "photo"
    if msg.get("video") or msg.get("video_note") or msg.get("animation") or mime.startswith("video/"):
        return "video"
    return None


def video_obj(msg):
    return msg.get("video") or msg.get("video_note") or msg.get("animation") or msg.get("document") or {}


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
    obj = msg.get("voice") or msg.get("audio") or video_obj(msg)
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


def _frames(src, folder, n, seconds):
    """n кадров равномерно по видео (ffmpeg), не больше 1024 px по длинной стороне."""
    out = []
    for i in range(n):
        at = seconds * (i + 0.5) / n if seconds else 0
        dst = os.path.join(folder, f"frame-{i + 1}.jpg")
        p = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-ss", f"{at:.2f}", "-i", src,
                            "-frames:v", "1", "-vf", "scale='min(1024,iw)':-2", "-q:v", "3", dst],
                           capture_output=True, timeout=60)
        if p.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst):
            out.append(dst)
        if not seconds:
            break
    return out


def _seconds(src):
    try:
        p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", src],
                           capture_output=True, text=True, timeout=30)
        return float(p.stdout.strip() or 0)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 0.0


def video(token, msg, folder=None, frames=4):
    """Видео, кружок или гифка: несколько кадров и что в нём говорят.
    {"frames": [пути к .jpg], "text": речь или "", "seconds": длительность}. Больше 20 МБ — только обложка."""
    obj = video_obj(msg)
    if not obj.get("file_id"):
        raise MediaError("в сообщении нет видео")
    folder = folder or tempfile.mkdtemp(prefix="tgvideo-")
    os.makedirs(folder, exist_ok=True)
    thumb = obj.get("thumbnail") or obj.get("thumb") or {}
    seconds = float(obj.get("duration") or 0)
    if (obj.get("file_size") or 0) > MAX_VOICE:
        if not thumb.get("file_id"):
            raise MediaError("видео больше 20 МБ — Telegram не отдаёт такие ботам; пришли покороче")
        return {"frames": [download(token, thumb["file_id"], os.path.join(folder, "cover.jpg"))], "text": "",
                "seconds": seconds, "cover_only": True}
    src = download(token, obj["file_id"], os.path.join(folder, "video"))
    seconds = seconds or _seconds(src)
    shots = []
    try:
        shots = _frames(src, folder, 1 if seconds < 3 else frames, seconds)
    except (OSError, subprocess.TimeoutExpired):
        pass
    if not shots and thumb.get("file_id"):
        shots = [download(token, thumb["file_id"], os.path.join(folder, "cover.jpg"))]
    text = ""
    if not msg.get("animation"):  # у гифок нет звука
        try:
            text = transcribe(src)
        except MediaError:
            pass
    if not shots and not text:
        raise MediaError("не получилось открыть видео")
    return {"frames": shots, "text": text, "seconds": seconds}


def understand(token, msg, folder=None):
    """Любое сообщение -> {"kind": "text"|"voice"|"photo"|"video", "text": текст (подпись, речь), "images": [пути]}.
    Для ботов, которым всё равно, чем прислали: голосовое — текст, фото — картинка, видео — кадры и речь."""
    k = kind(msg) or "text"
    caption = (msg.get("caption") or msg.get("text") or "").strip()
    if k == "voice":
        return {"kind": k, "text": voice_text(token, msg), "images": []}
    if k == "photo":
        return {"kind": k, "text": caption, "images": [photo_path(token, msg, folder)]}
    if k == "video":
        v = video(token, msg, folder)
        text = "\n".join(t for t in (caption, v["text"] and f"(в видео говорят) {v['text']}") if t)
        return {"kind": k, "text": text, "images": v["frames"], "seconds": v["seconds"]}
    return {"kind": k, "text": caption, "images": []}
