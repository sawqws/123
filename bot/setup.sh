#!/usr/bin/env bash
# Установка бота на сервер Ubuntu/Debian. Запускать из папки репозитория: sudo bash bot/setup.sh
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"

echo "== Пакеты"
apt-get update
apt-get install -y curl git python3 ca-certificates tzdata
if ! command -v node >/dev/null || [ "$(node -p 'process.versions.node.split(".")[0]')" -lt 20 ]; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  apt-get install -y nodejs
fi

echo "== Playwright и Chromium"
cd "$REPO"
npm install
npx playwright install --with-deps chromium

echo "== Claude Code"
npm install -g @anthropic-ai/claude-code

echo "== Файл с секретами"
ENV=/etc/horo-bot.env
if [ ! -f "$ENV" ]; then
  cat > "$ENV" <<'EOT'
TELEGRAM_TOKEN=
TG_ALLOWED_ID=
HORO_LOGIN=
HORO_PASSWORD=
EOT
  chmod 600 "$ENV"
fi

echo "== Служба systemd"
cat > /etc/systemd/system/horo-bot.service <<EOT
[Unit]
Description=horodigital Telegram bot
After=network-online.target

[Service]
EnvironmentFile=$ENV
WorkingDirectory=$REPO
ExecStart=/usr/bin/python3 $REPO/bot/bot.py
Restart=always
RestartSec=10
User=$(stat -c %U "$REPO")

[Install]
WantedBy=multi-user.target
EOT
systemctl daemon-reload

echo
echo "Готово. Дальше:"
echo "  1) nano $ENV   — впиши токен, свой ID, логин и пароль"
echo "  2) claude      — войди в аккаунт Claude (один раз), потом /exit"
echo "  3) systemctl enable --now horo-bot"
echo "  4) journalctl -u horo-bot -f   — смотреть логи"
