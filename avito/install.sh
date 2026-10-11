#!/usr/bin/env bash
# Установка бота «Авито» (фото на белом фоне, описание, средняя цена) на сервер Ubuntu/Debian и запуск через pm2.
# Одной командой (от root):
#   curl -fsSL https://raw.githubusercontent.com/sawqws/123/main/avito/install.sh | bash
# Повторный запуск обновляет код и перезапускает бота, токен второй раз не спрашивает.
# Токен можно передать сразу: curl … | AVITO_TOKEN=123:ABC bash
set -euo pipefail
BRANCH=main
DIR=/root/horo
ENV=/etc/avito-bot.env
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

echo "== 2/5 Код"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" fetch origin "$BRANCH"
  git -C "$DIR" checkout -B "$BRANCH" "origin/$BRANCH"
else
  git clone -b "$BRANCH" https://github.com/sawqws/123.git "$DIR"
fi

echo "== 3/5 Удаление фона (rembg, ~400 МБ вместе с моделью — один раз)"
VENV="$DIR/avito/.venv"
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q "rembg[cpu]" pillow
"$VENV/bin/python" -c "from rembg import new_session; new_session('${AVITO_BG_MODEL:-isnet-general-use}')" \
  || echo "⚠️ Модель не скачалась — бот попробует при первом фото"
bash "$DIR/common/setup.sh" || echo "⚠️ Голосовые не поставились — бот работает и без них"
# Запасной поиск цен через Chromium (если он уже стоит для HDP-бота — ничего не качаем)
if [ ! -d "$DIR/node_modules/playwright" ]; then
  (cd "$DIR" && npm install --no-audit --no-fund >/dev/null && npx playwright install --with-deps chromium >/dev/null) \
    || echo "⚠️ Chromium не поставился — цены будут только простым запросом"
fi

# Подкачка 2 ГБ, если её нет: на сервере 2 ГБ памяти, а Claude, Chromium и удаление фона вместе могут не влезть
if ! swapon --show 2>/dev/null | grep -q . && [ ! -e /swapfile ]; then
  (fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile \
    && echo '/swapfile none swap sw 0 0' >> /etc/fstab && echo "Подкачка 2 ГБ включена") \
    || { rm -f /swapfile; echo "⚠️ Подкачку включить не удалось — не страшно"; }
fi

echo "== 4/5 Токен"
if [ -n "${AVITO_TOKEN:-}" ] && [ -s "$ENV" ]; then
  sed -i "s|^AVITO_TOKEN=.*|AVITO_TOKEN=${AVITO_TOKEN}|" "$ENV"
  echo "Токен обновлён в $ENV"
elif [ ! -s "$ENV" ]; then
  TOKEN="${AVITO_TOKEN:-}"
  [ -n "$TOKEN" ] || read -r -p "Токен НОВОГО бота от @BotFather: " TOKEN < /dev/tty
  umask 077
  printf 'AVITO_TOKEN=%s\nAVITO_ALLOWED=\n' "$TOKEN" > "$ENV"
  chmod 600 "$ENV"
else
  echo "Уже есть $ENV — не трогаю (поменять: nano $ENV, потом pm2 restart avito-bot --update-env)"
fi

echo "== 5/5 Запуск"
pm2 delete avito-bot >/dev/null 2>&1 || true
pm2 start "$DIR/avito/ecosystem.config.js"
pm2 save
pm2 startup systemd -u root --hp /root >/dev/null || true

cat <<'EOT'

Готово, «Авито» запущен.
  1) Открой бота в Telegram и нажми /start — ты станешь владельцем.
  2) Пришли фото вещи — уберу фон; напиши, что это — сделаю описание.
     Логи: pm2 logs avito-bot · Обновить: /update в боте или эта же команда
EOT
