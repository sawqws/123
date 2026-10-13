#!/usr/bin/env bash
# Установка бота «Ловец» (юзернеймы на каналы) на сервер Ubuntu/Debian и запуск через pm2.
# Одной командой (от root):
#   curl -fsSL https://raw.githubusercontent.com/sawqws/123/main/catcher/install.sh | bash
# Повторный запуск обновляет код и перезапускает бота, токен второй раз не спрашивает.
# Токен можно передать сразу: curl … | CATCHER_TOKEN=123:ABC bash
# Ловит через Секретаря (tgbot/, часть Главного): он должен быть запущен и вошёл в аккаунт Telegram.
set -euo pipefail
BRANCH=main
DIR=/root/horo
ENV=/etc/catcher-bot.env
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
if [ -n "${CATCHER_TOKEN:-}" ] && [ -s "$ENV" ]; then
  sed -i "s|^CATCHER_TOKEN=.*|CATCHER_TOKEN=${CATCHER_TOKEN}|" "$ENV"
  echo "Токен обновлён в $ENV"
elif [ ! -s "$ENV" ]; then
  TOKEN="${CATCHER_TOKEN:-}"
  [ -n "$TOKEN" ] || read -r -p "Токен НОВОГО бота от @BotFather: " TOKEN < /dev/tty
  umask 077
  printf 'CATCHER_TOKEN=%s\n' "$TOKEN" > "$ENV"
  chmod 600 "$ENV"
else
  echo "Уже есть $ENV — не трогаю (поменять: nano $ENV, потом pm2 restart catcher-bot --update-env)"
fi

# голосовые и фото (common/.venv); без них бот работает, просто не слушает голосовые
bash "$DIR/common/setup.sh" || echo "Голосовые не установились — бот работает без них"

echo "== 4/4 Запуск"
pm2 delete catcher-bot >/dev/null 2>&1 || true
pm2 start "$DIR/catcher/ecosystem.config.js"
pm2 save
pm2 startup systemd -u root --hp /root >/dev/null || true

cat <<'EOT'

Готово, «Ловец» запущен.
  1) Открой бота в Telegram и нажми /start — ты станешь владельцем.
  2) Пришли @имя (можно списком) или нажми «🎲 По пожеланиям».
  3) Секретарь (часть Главного) должен быть в твоём аккаунте: в Главном /login.
     Логи: pm2 logs catcher-bot · Обновить: «Обновить всех» в Главном или эта же команда
EOT
