"""Речь для «Глаза»: голос Глеба -> текст (faster-whisper) и текст -> голос (edge-tts).

Распознавание — отдельный процесс stt_worker.py в common/.venv: модель держится в памяти, пока идёт разговор,
и выгружается через IDLE секунд тишины. Озвучка — edge-tts (голоса Microsoft) в eye/.venv; если её нет
или сервис не ответил — tts() вернёт None, и страница прочитает ответ голосом самого iPhone.
"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "common"))
import media  # noqa: E402

VOICE = os.environ.get("EYE_VOICE", "ru-RU-DmitryNeural")
RATE = os.environ.get("EYE_VOICE_RATE", "+8%")
IDLE = 10 * 60


class SpeechError(Exception):
    """Понятная причина для Глеба."""


class Whisper:
    def __init__(self):
        self.proc = None
        self.lock = threading.Lock()
        self.used = 0.0
        threading.Thread(target=self._reaper, daemon=True).start()

    def _start(self):
        py = media.whisper_python()
        if not py:
            raise SpeechError("на сервере не установлено распознавание речи (common/setup.sh)")
        self.proc = subprocess.Popen([py, os.path.join(HERE, "stt_worker.py")], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        if not self._read(120).get("ready"):
            self.stop()
            raise SpeechError("распознавание речи не запустилось")

    def _read(self, timeout):
        box = {}
        t = threading.Thread(target=lambda: box.update(line=self.proc.stdout.readline()), daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive() or not box.get("line"):
            self.stop()
            raise SpeechError("распознавание речи зависло — скажи ещё раз")
        return json.loads(box["line"])

    def warm(self):
        """Загрузить модель заранее (страница открылась — скоро заговорят)."""
        def go():
            with self.lock:
                self.used = time.time()
                if not self.proc or self.proc.poll() is not None:
                    try:
                        self._start()
                    except SpeechError as e:
                        print("whisper:", e)
        threading.Thread(target=go, daemon=True).start()

    def stop(self):
        if self.proc:
            try:
                self.proc.kill()
            except OSError:
                pass
        self.proc = None

    def text(self, path, timeout=90):
        with self.lock:
            if not self.proc or self.proc.poll() is not None:
                self._start()
            self.used = time.time()
            self.proc.stdin.write(json.dumps({"path": path}) + "\n")
            self.proc.stdin.flush()
            res = self._read(timeout)
            self.used = time.time()
        if res.get("error"):
            print("whisper:", res["error"])
            raise SpeechError("не разобрал звук")
        return res.get("text", "").strip()

    def _reaper(self):
        while True:
            time.sleep(60)
            with self.lock:
                if self.proc and time.time() - self.used > IDLE:
                    self.stop()


whisper = Whisper()


def hear(data, suffix=".wav"):
    """Байты звука -> текст ('' если слов не слышно)."""
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(data)
    try:
        return whisper.text(f.name)
    finally:
        os.remove(f.name)


def tts(text):
    """mp3 с ответом или None, если озвучки на сервере нет."""
    try:
        import edge_tts
    except ImportError:
        return None

    async def go():
        buf = bytearray()
        async for chunk in edge_tts.Communicate(text, VOICE, rate=RATE).stream():
            if chunk.get("type") == "audio":
                buf += chunk["data"]
        return bytes(buf)
    for attempt in range(2):  # сервис изредка отвечает «нет звука» — второй раз обычно получается
        try:
            mp3 = asyncio.run(asyncio.wait_for(go(), 10))
            if mp3:
                return mp3
        except Exception as e:
            print("tts:", repr(e)[:300])
    return None


def to_ogg(mp3):
    """mp3 -> ogg/opus: так Telegram показывает голосовое, а не файл."""
    try:
        p = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", "pipe:0", "-c:a", "libopus",
                            "-b:a", "32k", "-f", "ogg", "pipe:1"], input=mp3, capture_output=True, timeout=60)
        return p.stdout if p.returncode == 0 and p.stdout else None
    except (OSError, subprocess.TimeoutExpired):
        return None
