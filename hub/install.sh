#!/usr/bin/env bash
# Установка «Главного» — бота, который следит за остальными ботами на сервере, — и запуск через pm2.
# Одной командой (от root):
#   curl -fsSL https://raw.githubusercontent.com/sawqws/123/main/hub/install.sh | HUB_TOKEN=123:ABC bash
# Заодно можно поставить тренера WHOOP: … | HUB_TOKEN=123:ABC COACH_TOKEN=456:DEF bash
# Повторный запуск обновляет код и перезапускает бота, токен второй раз не спрашивает.
set -euo pipefail
BRANCH=main
DIR=/root/horo
ENV=/etc/hub-bot.env
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a
[ "$(id -u)" = 0 ] || { echo "Запусти от root (или через sudo)"; exit 1; }
APT="apt-get -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold"

echo "== 1/4 Пакеты"
if ! command -v python3 >/dev/null || ! command -v git >/dev/null || ! command -v node >/dev/null; then
  apt-get update
  $APT install curl git ca-certificates tzdata python3
  if ! command -v node >/dev/null; then
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
    $APT install nodejs
  fi
fi
command -v pm2 >/dev/null || npm install -g pm2

echo "== 2/4 Код"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" fetch origin "$BRANCH"
  git -C "$DIR" checkout -B "$BRANCH" "origin/$BRANCH"
else
  git clone -b "$BRANCH" https://github.com/sawqws/123.git "$DIR"
fi

echo "== 3/4 Токен"
if [ -n "${HUB_TOKEN:-}" ] && [ -s "$ENV" ]; then
  sed -i "s|^HUB_TOKEN=.*|HUB_TOKEN=${HUB_TOKEN}|" "$ENV"
  echo "Токен обновлён в $ENV"
elif [ ! -s "$ENV" ]; then
  TOKEN="${HUB_TOKEN:-}"
  [ -n "$TOKEN" ] || read -r -p "Токен бота «Главный» от @BotFather: " TOKEN < /dev/tty
  umask 077
  printf 'HUB_TOKEN=%s\nHUB_ALLOWED=\n' "$TOKEN" > "$ENV"
  chmod 600 "$ENV"
else
  echo "Уже есть $ENV — не трогаю (поменять: nano $ENV, потом pm2 restart hub-bot --update-env)"
fi

echo "== 4/4 Запуск"
pm2 delete hub-bot >/dev/null 2>&1 || true
pm2 start "$DIR/hub/ecosystem.config.js"
pm2 save
pm2 startup systemd -u root --hp /root >/dev/null || true

if [ -n "${COACH_TOKEN:-}" ]; then
  echo "== Тренер WHOOP"
  COACH_TOKEN="$COACH_TOKEN" bash "$DIR/coach/install.sh"
fi

cat <<'EOT'

Готово, «Главный» запущен.
  Открой его в Telegram и нажми /start — станешь владельцем.
  Логи: pm2 logs hub-bot
EOT
