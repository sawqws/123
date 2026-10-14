#!/usr/bin/env bash
# «Студия сайтов» для другого человека (сестры Глеба): только сайты, без ботов и без /fix, Claude работает только
# в /srv/studio-sites (ключи ботов и сессию Telegram на сервере не видит). Код — общий /root/horo.
#   bash /root/horo/studio/install_sites.sh <токен бота от @BotFather>
# Владелец — тот, кто первым нажмёт /start в этом боте. Повторный запуск — перезапуск с новым кодом.
set -euo pipefail
DIR=/root/horo
ENV=/etc/studio-sites.env
WORK=/srv/studio-sites
TOKEN="${1:-${STUDIO_TOKEN:-}}"
[ "$(id -u)" = 0 ] || { echo "Запусти от root"; exit 1; }
mkdir -p "$WORK/sites" "$WORK/data"
if [ -n "$TOKEN" ]; then
  IP=$(curl -fsS -m 10 https://api.ipify.org || true)
  umask 077
  {
    printf 'STUDIO_TOKEN=%s\n' "$TOKEN"
    printf 'STUDIO_TG_NAME=Site Studio\n'
    [ -n "$IP" ] && printf 'STUDIO_URL=https://web.%s.sslip.io/\n' "${IP//./-}"
  } > "$ENV"
  chmod 600 "$ENV"
elif [ ! -s "$ENV" ]; then
  echo "Нужен токен: bash $0 <токен>"; exit 1
fi
pm2 delete studio-sites >/dev/null 2>&1 || true
pm2 start "$DIR/studio/ecosystem.sites.config.js"
pm2 save
pm2 restart planner-bot >/dev/null 2>&1 || true  # Caddy планера подхватит адрес web.<host>
echo "Готово: «Студия сайтов» запущена (pm2 studio-sites)."
