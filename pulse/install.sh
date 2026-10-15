#!/usr/bin/env bash
# Установка бота «Пульс» (все чаты Claude Code на одном экране) на сервер и запуск через pm2.
#   curl -fsSL https://raw.githubusercontent.com/sawqws/123/main/pulse/install.sh | PULSE_TOKEN=123:ABC bash
# Повторный запуск обновляет код и перезапускает бота, токен второй раз не спрашивает.
# Сводки чатов приходят через Главного (hub/hub.py, действие pulse) — он должен стоять на этом же сервере.
set -euo pipefail
BRANCH=main
DIR=/root/horo
ENV=/etc/pulse-bot.env
[ "$(id -u)" = 0 ] || { echo "Запусти от root (или через sudo)"; exit 1; }
command -v python3 >/dev/null && command -v git >/dev/null && command -v pm2 >/dev/null \
  || { echo "Нужны python3, git и pm2 (их ставит install.sh любого другого бота)"; exit 1; }

echo "== 1/3 Код"
if [ -d "$DIR/.git" ]; then
  if [ "${PULSE_NO_PULL:-}" != 1 ]; then
    git -C "$DIR" fetch origin "$BRANCH"
    git -C "$DIR" merge --ff-only "origin/$BRANCH" || echo "Код не обновил (локальные правки) — ставлю что есть"
  fi
else
  git clone -b "$BRANCH" https://github.com/sawqws/123.git "$DIR"
fi

echo "== 2/3 Токен"
if [ -n "${PULSE_TOKEN:-}" ] && [ -s "$ENV" ]; then
  sed -i "s|^PULSE_TOKEN=.*|PULSE_TOKEN=${PULSE_TOKEN}|" "$ENV"
  echo "Токен обновлён в $ENV"
elif [ ! -s "$ENV" ]; then
  TOKEN="${PULSE_TOKEN:-}"
  [ -n "$TOKEN" ] || read -r -p "Токен НОВОГО бота от @BotFather: " TOKEN < /dev/tty
  umask 077
  printf 'PULSE_TOKEN=%s\nPULSE_ALLOWED=%s\n' "$TOKEN" "${PULSE_ALLOWED:-}" > "$ENV"
  chmod 600 "$ENV"
else
  echo "Уже есть $ENV — не трогаю"
fi

echo "== 3/3 Запуск"
pm2 delete pulse-bot >/dev/null 2>&1 || true
pm2 start "$DIR/pulse/ecosystem.config.js"
pm2 save
echo "Готово: «Пульс» запущен. Открой бота и нажми /start."
