#!/usr/bin/env bash
# Установка бота «Принтер» на сервер: пользователь для туннеля с принтера, секреты, запуск через pm2.
# PRINTER_TOKEN=123:ABC BOT_OWNER=<id> bash printer/install.sh
# PRINTER_TUNNEL_KEY='ecdsa-sha2-nistp256 AAAA...' — открытый ключ принтера (см. printer/tunnel/setup.sh).
# SKIP_PULL=1 — не трогать git: код уже на месте.
set -euo pipefail
DIR=/root/horo
ENV=/etc/printer-bot.env
USER_NAME=printertunnel
[ "$(id -u)" = 0 ] || { echo "Запусти от root"; exit 1; }

if [ -z "${SKIP_PULL:-}" ]; then
  git -C "$DIR" fetch origin main
  [ -z "$(git -C "$DIR" rev-list "origin/main..HEAD" 2>/dev/null)" ] || git -C "$DIR" branch -f "local-$(date +%m%d-%H%M)" HEAD  # местные правки — в ветку, Главный покажет их Глебу
  git -C "$DIR" checkout -B main origin/main
fi

echo "== Пользователь для туннеля"
id "$USER_NAME" >/dev/null 2>&1 || useradd -m -s /usr/sbin/nologin "$USER_NAME"
HOME_DIR=$(getent passwd "$USER_NAME" | cut -d: -f6)
install -d -m 700 -o "$USER_NAME" -g "$USER_NAME" "$HOME_DIR/.ssh"
if [ -n "${PRINTER_TUNNEL_KEY:-}" ]; then
  # только обратные порты бота: ни команд, ни других портов
  printf 'restrict,port-forwarding,permitlisten="17125",permitlisten="17999" %s\n' "$PRINTER_TUNNEL_KEY" > "$HOME_DIR/.ssh/authorized_keys"
  chown "$USER_NAME:$USER_NAME" "$HOME_DIR/.ssh/authorized_keys"
  chmod 600 "$HOME_DIR/.ssh/authorized_keys"
fi
CONF=/etc/ssh/sshd_config.d/printertunnel.conf
if [ ! -f "$CONF" ]; then
  cat > "$CONF" <<EOF
Match User $USER_NAME
    PasswordAuthentication no
    AllowTcpForwarding remote
    X11Forwarding no
    PermitTTY no
    ClientAliveInterval 20
    ClientAliveCountMax 3
EOF
  sshd -t
  systemctl reload ssh
fi

echo "== Секреты"
if [ -n "${PRINTER_TOKEN:-}" ]; then
  umask 077
  { printf 'PRINTER_TOKEN=%s\n' "$PRINTER_TOKEN"
    [ -z "${BOT_OWNER:-}" ] || printf 'BOT_OWNER=%s\n' "$BOT_OWNER"
    printf 'PRINTER_URL=http://127.0.0.1:17125\nPRINTER_WS_PORT=17999\n'; } > "$ENV"
  chmod 600 "$ENV"
fi
[ -s "$ENV" ] || { echo "Нет $ENV: передай PRINTER_TOKEN"; exit 1; }

echo "== Песочница для нарезки"
NEEDRESTART_SUSPEND=1 bash "$DIR/printer/setup_slicer.sh"

echo "== Запуск"
pm2 delete printer-bot >/dev/null 2>&1 || true
pm2 start "$DIR/printer/ecosystem.config.js"
pm2 save
echo "Готово. Логи: pm2 logs printer-bot"
