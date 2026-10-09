#!/usr/bin/env bash
# Установка Telegram-бота на новый сервер Ubuntu/Debian и запуск через pm2.
# Одной командой (от root):
#   curl -fsSL https://raw.githubusercontent.com/sawqws/123/claude/fervent-turing-gcfdu9/bot/install.sh | bash
# Можно запускать повторно: обновит код и перезапустит бота, секреты не спрашивает второй раз.
set -euo pipefail
BRANCH=claude/fervent-turing-gcfdu9
DIR=/root/horo
ENV=/etc/horo-bot.env
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a
[ "$(id -u)" = 0 ] || { echo "Запусти от root (или через sudo)"; exit 1; }
APT="apt-get -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold"

echo "== 1/6 Обновление системы и пакеты"
apt-get update
$APT upgrade
$APT install curl git ca-certificates tzdata python3 python3-pip
# из обычного apt приходит слишком старый Node.js — ставим 22
if ! command -v node >/dev/null || [ "$(node -p 'process.versions.node.split(".")[0]')" -lt 20 ]; then
  $APT remove nodejs npm libnode-dev >/dev/null 2>&1 || true
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  $APT install nodejs
fi
echo "Node.js $(node -v)"

echo "== 2/6 Код бота"
if [ -d "$DIR/.git" ]; then git -C "$DIR" pull --ff-only; else git clone -b "$BRANCH" https://github.com/sawqws/123.git "$DIR"; fi
cd "$DIR"

echo "== 3/6 Браузер Chromium для сайта"
npm install
npx playwright install --with-deps chromium

echo "== 4/6 Claude Code и pm2"
npm install -g @anthropic-ai/claude-code pm2

echo "== 5/6 Секреты"
if [ ! -s "$ENV" ]; then
  read -r -p "Токен бота от @BotFather: " TOKEN < /dev/tty
  read -r -p "Логин horodigital: " LOGIN < /dev/tty
  read -r -s -p "Пароль horodigital (при вводе не видно): " PASS < /dev/tty; echo
  umask 077
  printf 'TELEGRAM_TOKEN=%s\nTG_ALLOWED_ID=\nHORO_LOGIN=%s\nHORO_PASSWORD=%s\n' "$TOKEN" "$LOGIN" "$PASS" > "$ENV"
  chmod 600 "$ENV"
else
  echo "Уже есть $ENV — не трогаю (поменять: nano $ENV, потом pm2 restart horo-bot --update-env)"
fi

echo "== 6/6 Запуск через pm2"
systemctl disable --now horo-bot >/dev/null 2>&1 || true   # старый вариант через systemd, если был
rm -f /etc/systemd/system/horo-bot.service
pm2 delete horo-bot >/dev/null 2>&1 || true
pm2 start bot/ecosystem.config.js
pm2 save
pm2 startup systemd -u root --hp /root >/dev/null   # бот поднимется сам после перезагрузки сервера

cat <<'EOT'

Готово, бот запущен.
  1) Открой бота в Telegram и нажми /start — ты станешь его владельцем (другим он отвечать не будет).
  2) Войди в Claude (один раз), без этого бот не сможет решать задания:
       cd /root/horo && claude
     выбери вход через аккаунт, открой ссылку, вставь код, потом напиши /exit
  3) Логи:          pm2 logs horo-bot
     Перезапуск:    pm2 restart horo-bot
     Статус:        pm2 status
EOT
