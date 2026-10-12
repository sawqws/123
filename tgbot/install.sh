#!/usr/bin/env bash
# Установка Секретаря (tgbot/) — он работает с аккаунтом Telegram Глеба и отвечает на вопросы.
# Обычно Секретарь — часть «Главного» (hub/): отдельного бота нет, Telegram опрашивает Главный, а Секретарь
# отвечает его токеном. Ставит его сам Главный (кнопка «🔄 Секретарь», «Обновить всех»), или одной командой (от root):
#   curl -fsSL https://raw.githubusercontent.com/sawqws/123/main/tgbot/install.sh | bash
# Без Главного (или TG_MODE=own) — отдельный бот со своим токеном TG_AGENT_TOKEN, как раньше.
# Повторный запуск обновляет код и перезапускает агента; настройки, вход в аккаунт и память (tgbot/tmp/) не трогает.
set -euo pipefail
BRANCH=main
DIR=/root/horo
ENV=/etc/tg-agent.env
HUB_ENV=/etc/hub-bot.env
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a
[ "$(id -u)" = 0 ] || { echo "Запусти от root (или через sudo)"; exit 1; }

echo "== 1/5 Пакеты"
if ! command -v ffmpeg >/dev/null || ! command -v git >/dev/null || ! python3 -c "import ensurepip" >/dev/null 2>&1; then
  apt-get update -q
  apt-get -y -q install git python3 python3-venv python3-pip ca-certificates ffmpeg
fi

echo "== 2/5 Код"
if [ -d "$DIR/.git" ]; then
  # код уже есть (его обновляет Главный, вместе с локальными правками «Доработать») — не сбрасываем
  [ -n "${TG_SKIP_PULL:-}" ] || [ -n "$(git -C "$DIR" rev-list origin/main..HEAD 2>/dev/null)" ] || {
    git -C "$DIR" fetch origin "$BRANCH" && git -C "$DIR" checkout -B "$BRANCH" "origin/$BRANCH"; }
else
  git clone -b "$BRANCH" https://github.com/sawqws/123.git "$DIR"
fi
cd "$DIR/tgbot"

echo "== 3/5 Python-библиотеки (Telethon)"
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

echo "== 4/5 Режим и секреты"
touch "$ENV"; chmod 600 "$ENV"
set_var() {  # KEY VALUE — заменить или дописать строку в $ENV
  if grep -q "^$1=" "$ENV"; then sed -i "s|^$1=.*|$1=$2|" "$ENV"; else echo "$1=$2" >> "$ENV"; fi
}
MODE=${TG_MODE:-}
[ -n "$MODE" ] || MODE=$(sed -n 's/^TG_MODE=//p' "$ENV" | tail -1)
if [ -z "$MODE" ]; then
  if grep -q '^HUB_TOKEN=.' "$HUB_ENV" 2>/dev/null; then MODE=hub; else MODE=own; fi
fi
set_var TG_MODE "$MODE"
if [ "$MODE" = hub ]; then
  grep -q '^HUB_TOKEN=.' "$HUB_ENV" 2>/dev/null || { echo "Нет Главного ($HUB_ENV): поставь его (hub/install.sh) или TG_MODE=own"; exit 1; }
  echo "Секретарь — часть Главного: отвечает токеном Главного, свой бот не нужен"
  # прежний токен (если был) остаётся в файле: по нему один раз скажем в старом чате, что переехали
else
  TOKEN=${TG_AGENT_TOKEN:-}
  [ -n "$TOKEN" ] || TOKEN=$(sed -n 's/^TG_AGENT_TOKEN=//p' "$ENV" | tail -1)
  [ -n "$TOKEN" ] || read -r -p "Токен бота от @BotFather: " TOKEN < /dev/tty
  set_var TG_AGENT_TOKEN "$TOKEN"
fi
if ! grep -q '^TG_ALLOWED_ID=.' "$ENV"; then
  # владелец — тот же, что у Главного или HDP-бота, чтобы никто другой не успел нажать /start
  OWNER=$(sed -n 's/^HUB_ALLOWED=\([0-9]*\).*/\1/p' "$HUB_ENV" 2>/dev/null | tail -1)
  [ -n "$OWNER" ] || OWNER=$(python3 -c "import json;print(json.load(open('$DIR/hub/data/state.json')).get('owner') or '')" 2>/dev/null || true)
  [ -n "$OWNER" ] || OWNER=$(python3 -c "import json;print(json.load(open('$DIR/horo/tmp/bot_settings.json')).get('owner') or '')" 2>/dev/null || true)
  set_var TG_ALLOWED_ID "$OWNER"
fi

echo "== 5/5 Запуск через pm2"
command -v pm2 >/dev/null || npm install -g pm2
command -v claude >/dev/null || npm install -g @anthropic-ai/claude-code
pm2 delete tg-agent >/dev/null 2>&1 || true
pm2 start ecosystem.config.js
pm2 save

if [ "$MODE" = hub ]; then
cat <<'EOT'

Готово: Секретарь запущен внутри «Главного».
  Пиши Главному что угодно — ответит Секретарь. Вход в аккаунт: /login или «👤 Аккаунт» в /start.
  Логи: pm2 logs tg-agent · Перезапуск: pm2 restart tg-agent
EOT
else
cat <<'EOT'

Готово, агент запущен отдельным ботом.
  1) Открой бота в Telegram, нажми /start, потом /login и войди в свой аккаунт.
  2) «🩺 Проверка» — покажет, что аккаунт и Claude работают.
  Логи: pm2 logs tg-agent · Перезапуск: pm2 restart tg-agent
EOT
fi
