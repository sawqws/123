#!/usr/bin/env bash
# Песочница для нарезки и сборки моделей: Ubuntu 24.04 в /opt/orca/root, запуск через bubblewrap.
# Нужна потому, что OrcaSlicer для Linux собран под Ubuntu 24.04, а сервер ботов на 22.04. Систему сервера не меняет.
# Повторный запуск ставит только то, чего не хватает. Запуск от root: bash printer/setup_slicer.sh
set -euo pipefail
ORCA_TAG=v2.4.2
ORCA_ASSET=OrcaSlicer_Linux_AppImage_Ubuntu2404_V2.4.2.AppImage
THREE_URL=https://cdn.jsdelivr.net/npm/three@0.128.0/build/three.min.js
THREE_SHA=${THREE_SHA:-}
BASE=https://cdimage.ubuntu.com/ubuntu-base/releases/noble/release
TOP=/opt/orca
ROOT=$TOP/root
PACKAGES="libgtk-3-0t64 libwebkit2gtk-4.1-0 libjavascriptcoregtk-4.1-0 libsoup-3.0-0 libegl1 libopengl0 libgl1 libglu1-mesa \
libgstreamer1.0-0 libgstreamer-plugins-base1.0-0 libmspack0t64 libsecret-1-0 xvfb xauth openscad nodejs ca-certificates"
[ "$(id -u)" = 0 ] || { echo "Запусти от root"; exit 1; }

# needrestart выключен: 08.11 установка пакета через него перезапустила pm2 и вместе с ним всех ботов
command -v bwrap >/dev/null || { echo "== bubblewrap"; NEEDRESTART_SUSPEND=1 apt-get install -y -qq bubblewrap >/dev/null; }
mkdir -p "$TOP"

if [ ! -f "$ROOT/etc/os-release" ]; then
  echo "== Ubuntu 24.04"
  sums=$(curl -fsSL -m 60 "$BASE/SHA256SUMS")
  line=$(printf '%s\n' "$sums" | grep -E 'ubuntu-base-24\.04(\.[0-9]+)?-base-amd64\.tar\.gz$' | tail -1)
  name=${line##* }; name=${name#\*}
  [ -n "$name" ] || { echo "Не нашёл образ в SHA256SUMS"; exit 1; }
  curl -fsSL -m 600 -o "$TOP/base.tar.gz" "$BASE/$name"
  echo "${line%% *}  $TOP/base.tar.gz" | sha256sum -c - >/dev/null
  mkdir -p "$ROOT"
  tar -xzf "$TOP/base.tar.gz" -C "$ROOT"
  rm -f "$TOP/base.tar.gz"
fi

inside() {  # команда внутри образа от root, с сетью: только для установки пакетов
  env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin HOME=/root LC_ALL=C.UTF-8 DEBIAN_FRONTEND=noninteractive \
    nice -n 15 bwrap --bind "$ROOT" / --proc /proc --dev /dev --tmpfs /tmp --ro-bind /etc/resolv.conf /etc/resolv.conf "$@"
}

if ! inside sh -c 'command -v openscad >/dev/null && command -v node >/dev/null && command -v xvfb-run >/dev/null'; then
  echo "== Пакеты"
  inside apt-get update -qq
  inside apt-get install -y -qq --no-install-recommends $PACKAGES >/dev/null
  inside apt-get clean
fi

if [ ! -x "$ROOT/opt/orca/AppRun" ]; then
  echo "== OrcaSlicer $ORCA_TAG"
  if [ ! -d "$TOP/squashfs-root" ]; then
    read -r url sum < <(python3 - "$ORCA_TAG" "$ORCA_ASSET" <<'EOF'
import json, sys, urllib.request
req = urllib.request.Request(f"https://api.github.com/repos/OrcaSlicer/OrcaSlicer/releases/tags/{sys.argv[1]}",
                             headers={"Accept": "application/vnd.github+json", "User-Agent": "printer-bot"})
for a in json.load(urllib.request.urlopen(req, timeout=30))["assets"]:
    if a["name"] == sys.argv[2]:
        print(a["url"], a["digest"].split(":")[1])
EOF
)
    curl -fsSL -m 900 -H "Accept: application/octet-stream" -H "User-Agent: printer-bot" -o "$TOP/orca.AppImage" "$url"
    echo "$sum  $TOP/orca.AppImage" | sha256sum -c - >/dev/null
    chmod +x "$TOP/orca.AppImage"
    (cd "$TOP" && ./orca.AppImage --appimage-extract >/dev/null)
  fi
  mkdir -p "$ROOT/opt"
  mv "$TOP/squashfs-root" "$ROOT/opt/orca"
  chmod -R a+rX "$ROOT/opt/orca"
  rm -f "$TOP/orca.AppImage"
fi

if [ ! -f "$ROOT/opt/three.min.js" ]; then
  echo "== three.js"
  curl -fsSL -m 120 -o "$ROOT/opt/three.min.js" "$THREE_URL"
  if [ -n "$THREE_SHA" ]; then echo "$THREE_SHA  $ROOT/opt/three.min.js" | sha256sum -c - >/dev/null; fi
fi

mkdir -p /var/lib/printer-bot/build /var/lib/printer-bot/ai/models/src /var/lib/printer-bot/ai/models/stl /var/lib/printer-bot/ai/models/png
echo "Готово: $(du -sh "$ROOT" | cut -f1)"
