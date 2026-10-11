#!/usr/bin/env bash
# Общие голосовые для всех ботов: ffmpeg + faster-whisper в common/.venv (~500 МБ вместе с моделью «base»).
# Повторный запуск ничего не качает. Запускает Главный при «Обновить всех» и install.sh ботов.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
VENV="$HERE/.venv"
export DEBIAN_FRONTEND=noninteractive
if ! command -v ffmpeg >/dev/null || ! python3 -c "import ensurepip" >/dev/null 2>&1; then
  apt-get update -qq && apt-get install -y -qq ffmpeg python3-venv >/dev/null
fi
if [ ! -x "$VENV/bin/python" ] || ! "$VENV/bin/python" -c "import faster_whisper" >/dev/null 2>&1; then
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q --upgrade pip
  "$VENV/bin/pip" install -q "faster-whisper>=1.1" numpy
  # модель скачиваем сразу, чтобы первое голосовое не ждало
  "$VENV/bin/python" -c "
from faster_whisper import WhisperModel
WhisperModel('base', device='cpu', compute_type='int8', download_root='$VENV/whisper')" || true
fi
echo "Голосовые: готово"
