#!/usr/bin/env bash
# Установка бота-планера «Ритм» на сервер Ubuntu/Debian (можно рядом с HDP-ботом) и запуск через pm2.
# Одной командой (от root):
#   curl -fsSL https://raw.githubusercontent.com/sawqws/123/main/planner/install.sh | bash
# Повторный запуск обновляет код и перезапускает бота, токен второй раз не спрашивает.
# Токен можно передать сразу: curl … | PLANNER_TOKEN=123:ABC bash
set -euo pipefail
BRANCH=main
DIR=/root/horo
ENV=/etc/planner-bot.env
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a
[ "$(id -u)" = 0 ] || { echo "Запусти от root (или через sudo)"; exit 1; }
APT="apt-get -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold"

echo "== 1/5 Пакеты"
if ! command -v python3 >/dev/null || ! command -v git >/dev/null || ! command -v node >/dev/null; then
  apt-get update
  $APT install curl git ca-certificates tzdata python3
  if ! command -v node >/dev/null; then
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
    $APT install nodejs
  fi
fi
command -v pm2 >/dev/null || npm install -g pm2

echo "== 2/5 cloudflared (https-адрес для приложения без домена)"
if ! command -v cloudflared >/dev/null; then
  ARCH=$(dpkg --print-architecture)
  curl -fsSL -o /tmp/cloudflared.deb "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${ARCH}.deb"
  dpkg -i /tmp/cloudflared.deb
  rm -f /tmp/cloudflared.deb
fi
cloudflared --version

# приложению нужен https: Caddy (его бот скачивает сам) берёт сертификат, для этого открыты порты 80 и 443
if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
  ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null
fi

echo "== 3/5 Код"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" fetch origin "$BRANCH"
  git -C "$DIR" checkout -B "$BRANCH" "origin/$BRANCH"
else
  git clone -b "$BRANCH" https://github.com/sawqws/123.git "$DIR"
fi

echo "== 4/5 Токен"
# токен можно передать сразу: curl … | PLANNER_TOKEN='123:ABC' bash
if [ -n "${PLANNER_TOKEN:-}" ] && [ -s "$ENV" ]; then
  sed -i "s|^PLANNER_TOKEN=.*|PLANNER_TOKEN=${PLANNER_TOKEN}|" "$ENV"
  echo "Токен обновлён в $ENV"
elif [ ! -s "$ENV" ]; then
  TOKEN="${PLANNER_TOKEN:-}"
  [ -n "$TOKEN" ] || read -r -p "Токен НОВОГО бота от @BotFather (не HDP-бота): " TOKEN < /dev/tty
  umask 077
  printf 'PLANNER_TOKEN=%s\nPLANNER_ALLOWED=\nPLANNER_PUBLIC=\nPLANNER_URL=\n' "$TOKEN" > "$ENV"
  chmod 600 "$ENV"
else
  echo "Уже есть $ENV — не трогаю (поменять: nano $ENV, потом pm2 restart planner-bot --update-env)"
fi

echo "== 5/5 Запуск"
pm2 delete planner-bot >/dev/null 2>&1 || true
pm2 start "$DIR/planner/ecosystem.config.js"
pm2 save
pm2 startup systemd -u root --hp /root >/dev/null || true

cat <<'EOT'

Готово, планер запущен.
  1) Открой нового бота в Telegram и нажми /start — ты станешь владельцем.
  2) Через минуту слева от поля ввода появится кнопка «Планер».
  3) Логи:        pm2 logs planner-bot
     Перезапуск:  pm2 restart planner-bot
     Обновить:    /update в боте или эта же команда установки
EOT
