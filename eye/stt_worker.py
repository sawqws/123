#!/usr/bin/env python3
"""Распознавание речи без перезагрузки модели: для разговора важна каждая секунда.

Запускается из eye/speech.py интерпретатором common/.venv (там faster-whisper). Модель грузится один раз,
дальше по строке JSON на запрос: {"path": "файл"} -> {"text": "..."} или {"error": "..."}.
Когда разговора долго нет, speech.py закрывает процесс — память (~300 МБ) возвращается системе.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "common"))
import voice  # noqa: E402

from faster_whisper import WhisperModel  # noqa: E402

model = WhisperModel(voice.MODEL, device="cpu", compute_type="int8", download_root=voice.CACHE)
print(json.dumps({"ready": True}), flush=True)
for line in sys.stdin:
    try:
        req = json.loads(line)
        segments, _ = model.transcribe(voice.decode(req["path"]), language=voice.LANG or None, beam_size=1,
                                       vad_filter=True)
        out = {"text": " ".join(s.text.strip() for s in segments).strip()}
    except Exception as e:  # плохой файл не должен ронять распознавание
        out = {"error": str(e)[-300:]}
    print(json.dumps(out, ensure_ascii=False), flush=True)
