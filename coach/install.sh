#!/usr/bin/env bash
# Установка бота-тренера «Форма» (WHOOP) на сервер Ubuntu/Debian рядом с другими ботами и запуск через pm2.
# Одной командой (от root):
#   curl -fsSL https://raw.githubusercontent.com/sawqws/123/main/coach/install.sh | bash
# Повторный запуск обновляет код и перезапускает бота, токен второй раз не спрашивает.
# Токен можно передать сразу: curl … | COACH_TOKEN=123:ABC bash
set -euo pipefail
BRANCH=main
DIR=/root/horo
ENV=/etc/coach-bot.env
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
# думает Claude Code — тот же, что у HDP-бота; если его нет, ставим (войти: claude, один раз)
command -v claude >/dev/null || npm install -g @anthropic-ai/claude-code
# https для приложения и входа в WHOOP: Caddy, порты 80 и 443
if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
  ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null
fi

echo "== 2/5 Код"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" fetch origin "$BRANCH"
  git -C "$DIR" checkout -B "$BRANCH" "origin/$BRANCH"
else
  git clone -b "$BRANCH" https://github.com/sawqws/123.git "$DIR"
fi

echo "== 3/5 Токен"
if [ -n "${COACH_TOKEN:-}" ] && [ -s "$ENV" ]; then
  sed -i "s|^COACH_TOKEN=.*|COACH_TOKEN=${COACH_TOKEN}|" "$ENV"
  echo "Токен обновлён в $ENV"
elif [ ! -s "$ENV" ]; then
  TOKEN="${COACH_TOKEN:-}"
  [ -n "$TOKEN" ] || read -r -p "Токен НОВОГО бота от @BotFather (не HDP и не планера): " TOKEN < /dev/tty
  umask 077
  printf 'COACH_TOKEN=%s\nCOACH_ALLOWED=\nCOACH_URL=\nWHOOP_CLIENT_ID=\nWHOOP_CLIENT_SECRET=\n' "$TOKEN" > "$ENV"
  chmod 600 "$ENV"
else
  echo "Уже есть $ENV — не трогаю (поменять: nano $ENV, потом pm2 restart coach-bot --update-env)"
fi

echo "== 4/5 Адрес https"
# порт 443 один на сервер: если работает планер, его Caddy держит и адрес тренера — перезапускаем планер с новым кодом
if pm2 describe planner-bot >/dev/null 2>&1; then
  pm2 restart planner-bot >/dev/null && echo "Планер перезапущен — его Caddy теперь обслуживает и тренера"
fi

echo "== 5/5 Запуск"
pm2 delete coach-bot >/dev/null 2>&1 || true
pm2 start "$DIR/coach/ecosystem.config.js"
pm2 save
pm2 startup systemd -u root --hp /root >/dev/null || true

IP=$(curl -fsS --max-time 10 https://api.ipify.org || echo "IP")
cat <<EOT

Готово, тренер запущен.
  1) Открой нового бота в Telegram и нажми /start — ты станешь владельцем.
  2) Через 1–2 минуты появится кнопка «Форма» слева от поля ввода.
  3) /connect — пошаговое подключение WHOOP. Redirect URL:
       https://coach.${IP//./-}.sslip.io/whoop/callback
  4) Логи:  pm2 logs coach-bot     Проверка: /status в боте
EOT
