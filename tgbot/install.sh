#!/usr/bin/env bash
# Установка агента, который управляет аккаунтом Telegram (tgbot/), на сервер с HDP-ботом.
# Одной командой (от root):
#   curl -fsSL https://raw.githubusercontent.com/sawqws/123/main/tgbot/install.sh | bash
# Повторный запуск обновляет код и перезапускает агента, секреты второй раз не спрашивает.
set -euo pipefail
BRANCH=main
DIR=/root/horo
ENV=/etc/tg-agent.env
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a
[ "$(id -u)" = 0 ] || { echo "Запусти от root (или через sudo)"; exit 1; }

echo "== 1/5 Пакеты"
apt-get update -q
apt-get -y -q install git python3 python3-venv python3-pip ca-certificates

echo "== 2/5 Код"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" fetch origin "$BRANCH"
  git -C "$DIR" checkout -B "$BRANCH" "origin/$BRANCH"
else
  git clone -b "$BRANCH" https://github.com/sawqws/123.git "$DIR"
fi
cd "$DIR/tgbot"

echo "== 3/5 Python-библиотеки (Telethon)"
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

echo "== 4/5 Секреты"
if [ ! -s "$ENV" ]; then
  read -r -p "Токен нового бота от @BotFather: " TOKEN < /dev/tty
  echo "Ключи приложения Telegram (api_id и api_hash). Если их нет — просто нажми Enter:"
  echo "получу их сам с my.telegram.org, понадобятся номер и код из Telegram."
  read -r -p "api_id: " APIID < /dev/tty
  if [ -z "$APIID" ]; then
    KEYS=$(python3 "$DIR/tgbot/get_api.py" | tail -1) || { echo "Ключи получить не удалось, запусти установку ещё раз"; exit 1; }
    APIID=${KEYS%% *}
    APIHASH=${KEYS##* }
  else
    read -r -p "api_hash: " APIHASH < /dev/tty
  fi
  # владелец — тот же, что у HDP-бота, чтобы никто другой не успел нажать /start
  OWNER=$(python3 -c "import json;print(json.load(open('$DIR/horo/tmp/bot_settings.json')).get('owner') or '')" 2>/dev/null || true)
  umask 077
  printf 'TG_AGENT_TOKEN=%s\nTG_API_ID=%s\nTG_API_HASH=%s\nTG_ALLOWED_ID=%s\n' "$TOKEN" "$APIID" "$APIHASH" "$OWNER" > "$ENV"
  chmod 600 "$ENV"
else
  echo "Уже есть $ENV — не трогаю (поменять: nano $ENV, потом pm2 restart tg-agent --update-env)"
fi

echo "== 5/5 Запуск через pm2"
command -v pm2 >/dev/null || npm install -g pm2
command -v claude >/dev/null || npm install -g @anthropic-ai/claude-code
pm2 delete tg-agent >/dev/null 2>&1 || true
pm2 start ecosystem.config.js
pm2 save

cat <<'EOT'

Готово, агент запущен.
  1) Открой нового бота в Telegram, нажми /start, потом /login и войди в свой аккаунт.
  2) «🩺 Проверка» — покажет, что аккаунт и Claude работают.
  Логи: pm2 logs tg-agent · Перезапуск: pm2 restart tg-agent
EOT
