#!/usr/bin/env python3
"""Голосовые в текст: faster-whisper на процессоре сервера.

  common/.venv/bin/python common/voice.py файл.ogg     -> печатает текст
  … voice.py файл.m4a --model small --prompt "гречка, Teos…"   -> своя модель и словарь-подсказка (еда, марки)

Запускается отдельным процессом на каждое голосовое: модель «base» занимает ~400 МБ,
после распознавания память возвращается системе (на сервере 2 ГБ и много ботов).
На одном ядре 10 секунд речи — около 3 секунд. Модель: TG_WHISPER_MODEL (base|small), язык: TG_WHISPER_LANG.
Звук декодирует ffmpeg (встроенный декодер faster-whisper ломается на новых версиях PyAV).
"""
import os
import subprocess
import sys

MODEL = os.environ.get("WHISPER_MODEL") or os.environ.get("TG_WHISPER_MODEL", "base")
LANG = os.environ.get("TG_WHISPER_LANG", "ru")
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".venv", "whisper")


def decode(path):
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", path, "-f", "s16le", "-ac", "1",
                          "-ar", "16000", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768


def transcribe(path, model=MODEL, language=LANG, prompt=None, beam=1):
    """prompt — подсказка словами (initial_prompt): нужные слова и марки распознаются точнее («Teos», «Nordic»)."""
    from faster_whisper import WhisperModel
    m = WhisperModel(model, device="cpu", compute_type="int8", download_root=CACHE)
    segments, _ = m.transcribe(decode(path), language=language or None, beam_size=beam, vad_filter=True,
                               initial_prompt=prompt or None)
    return " ".join(s.text.strip() for s in segments).strip()


def _args(argv):
    out, i = {"path": argv[0]}, 1
    while i < len(argv) - 1:
        if argv[i] in ("--model", "--prompt", "--beam"):
            out[argv[i][2:]] = argv[i + 1]
            i += 2
        else:
            i += 1
    return out


if __name__ == "__main__":
    try:
        a = _args(sys.argv[1:])
        print(transcribe(a["path"], model=a.get("model") or MODEL, prompt=a.get("prompt"), beam=int(a.get("beam") or 1)))
    except subprocess.CalledProcessError as e:
        sys.exit("ffmpeg не смог прочитать звук: " + e.stderr.decode(errors="replace")[-300:])
