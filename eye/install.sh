#!/usr/bin/env bash
# Установка бота «Глаз» (Claude смотрит через очки Ray-Ban Meta и говорит через их динамики) и запуск через pm2.
# Одной командой (от root):
#   curl -fsSL https://raw.githubusercontent.com/sawqws/123/main/eye/install.sh | bash
# Повторный запуск обновляет код и перезапускает бота, токен второй раз не спрашивает.
# Токен можно передать сразу: curl … | EYE_TOKEN=123:ABC bash   (или /eye в Главном — он сам создаст бота)
set -euo pipefail
BRANCH=main
DIR=/root/horo
ENV=/etc/eye-bot.env
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a
[ "$(id -u)" = 0 ] || { echo "Запусти от root (или через sudo)"; exit 1; }
APT="apt-get -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold"

echo "== 1/5 Пакеты"
if ! command -v python3 >/dev/null || ! command -v git >/dev/null || ! command -v node >/dev/null \
   || ! python3 -c "import ensurepip" >/dev/null 2>&1; then
  apt-get update
  $APT install curl git ca-certificates tzdata python3 python3-venv
  if ! command -v node >/dev/null; then
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
    $APT install nodejs
  fi
fi
command -v pm2 >/dev/null || npm install -g pm2
command -v claude >/dev/null || npm install -g @anthropic-ai/claude-code
# https для страницы: Caddy, порты 80 и 443
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

echo "== 3/5 Голос и фото"
bash "$DIR/common/setup.sh" || echo "⚠️ Распознавание речи не поставилось — разговор голосом не заработает"
VENV="$DIR/eye/.venv"
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q edge-tts pillow pillow-heif \
  || echo "⚠️ edge-tts/Pillow не поставились — ответы прочитает голос iPhone"

echo "== 4/5 Токен"
if [ -n "${EYE_TOKEN:-}" ] && [ -s "$ENV" ]; then
  sed -i "s|^EYE_TOKEN=.*|EYE_TOKEN=${EYE_TOKEN}|" "$ENV"
  echo "Токен обновлён в $ENV"
elif [ ! -s "$ENV" ]; then
  TOKEN="${EYE_TOKEN:-}"
  [ -n "$TOKEN" ] || read -r -p "Токен НОВОГО бота от @BotFather: " TOKEN < /dev/tty
  umask 077
  printf 'EYE_TOKEN=%s\nEYE_ALLOWED=\nEYE_URL=\n' "$TOKEN" > "$ENV"
  chmod 600 "$ENV"
else
  echo "Уже есть $ENV — не трогаю (поменять: nano $ENV, потом pm2 restart eye-bot --update-env)"
fi

echo "== 5/5 Запуск"
# порт 443 один на сервер: если работает планер, его Caddy держит и адрес «Глаза» — перезапускаем планер с новым кодом
if pm2 describe planner-bot >/dev/null 2>&1; then
  pm2 restart planner-bot >/dev/null && echo "Планер перезапущен — его Caddy теперь обслуживает и «Глаз»"
fi
pm2 delete eye-bot >/dev/null 2>&1 || true
pm2 start "$DIR/eye/ecosystem.config.js"
pm2 save
pm2 startup systemd -u root --hp /root >/dev/null || true

IP=$(curl -fsS --max-time 10 https://api.ipify.org || echo "IP")
cat <<EOT

Готово, «Глаз» запущен.
  1) Открой бота в Telegram и нажми /start — ты станешь владельцем.
  2) Через 1–2 минуты появится кнопка «Глаз» слева от поля ввода (адрес https://eye.${IP//./-}.sslip.io).
  3) Пришли фото с очков или открой «Глаз» → «🎙 Говорить».
     Логи: pm2 logs eye-bot · Проверка: /status в боте
EOT
