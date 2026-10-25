#!/usr/bin/env bash
# VPN для домашнего роутера Глеба: сервер AmneziaWG (параметры 1.0 — их понимает стабильная KeeneticOS ≥ 4.3.4).
#   bash vpn/install.sh [клиент]   — ставит сервер awg0 (повторно — ничего не ломает) и создаёт клиента:
#                                     /root/vpn/clients/<клиент>.conf (по умолчанию router) — этот файл грузить в роутер.
# Выключить на время: systemctl stop awg-quick@awg0 · включить: systemctl start awg-quick@awg0
# Убрать совсем: bash vpn/uninstall.sh (ключи сохраняются — повторный install вернёт тот же конфиг роутера).
set -euo pipefail

CLIENT=${1:-router}
PORT=${VPN_PORT:-47811}
NET=10.66.66
STATE=/root/vpn
DIR=/etc/amnezia/amneziawg
CONF=$DIR/awg0.conf
APT=(apt-get -y -q -o DPkg::Lock::Timeout=600)
export DEBIAN_FRONTEND=noninteractive

[[ $CLIENT =~ ^[a-z0-9_-]{1,32}$ ]] || { echo "⚠️ имя клиента: a-z 0-9 _ -" >&2; exit 2; }
mkdir -p "$STATE/clients" && chmod 700 "$STATE"

# 1. Снимок «до» — для отката (только при первой установке)
if [[ ! -f $STATE/before-packages.txt ]]; then
  dpkg-query -W -f='${Package}\n' | sort > "$STATE/before-packages.txt"
  sysctl -n net.ipv4.ip_forward > "$STATE/before-ip_forward.txt"
  echo "снимок системы до установки: $STATE"
fi

# 2. Пакеты
if ! command -v awg-quick >/dev/null || ! command -v iptables >/dev/null; then
  "${APT[@]}" install software-properties-common >/dev/null
  grep -rqs amnezia /etc/apt/sources.list.d/ || add-apt-repository -y ppa:amnezia/ppa >/dev/null
  "${APT[@]}" update >/dev/null
  pre=$(mktemp); dpkg-query -W -f='${Package}\n' | sort > "$pre"
  "${APT[@]}" install "linux-headers-$(uname -r)" amneziawg iptables
  { cat "$STATE/installed-by-vpn.txt" 2>/dev/null; dpkg-query -W -f='${Package}\n' | sort | comm -13 "$pre" -; } \
    | sort -u > "$STATE/installed.tmp" && mv "$STATE/installed.tmp" "$STATE/installed-by-vpn.txt"
  rm -f "$pre"
fi
modprobe amneziawg

# 3. Пересылка пакетов
echo 'net.ipv4.ip_forward = 1' > /etc/sysctl.d/99-vpn-awg.conf
sysctl -q -p /etc/sysctl.d/99-vpn-awg.conf

# 4. Сервер (ключи из прошлой установки — если были)
mkdir -p "$DIR" && chmod 700 "$DIR"
if [[ ! -f $CONF && -d $STATE/saved ]]; then cp -a "$STATE/saved/." "$DIR/"; echo "ключи восстановлены из $STATE/saved"; fi
if [[ ! -f $CONF ]]; then
  WAN=$(ip route show default | awk '{for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1); exit}}')
  r() { shuf -i "$1-$2" -n 1; }
  S1=$(r 15 150); S2=$(r 15 150); while (( S1 + 56 == S2 )); do S2=$(r 15 150); done
  mapfile -t H < <(shuf -i 5-2147483647 -n 4)
  umask 077
  cat > "$CONF" <<EOF
[Interface]
PrivateKey = $(awg genkey)
Address = $NET.1/24
ListenPort = $PORT
Jc = $(r 4 8)
Jmin = 40
Jmax = 70
S1 = $S1
S2 = $S2
H1 = ${H[0]}
H2 = ${H[1]}
H3 = ${H[2]}
H4 = ${H[3]}
PostUp = iptables -t nat -A POSTROUTING -s $NET.0/24 -o $WAN -j MASQUERADE; iptables -A FORWARD -i %i -j ACCEPT; iptables -A FORWARD -o %i -j ACCEPT
PostDown = iptables -t nat -D POSTROUTING -s $NET.0/24 -o $WAN -j MASQUERADE; iptables -D FORWARD -i %i -j ACCEPT; iptables -D FORWARD -o %i -j ACCEPT
EOF
  echo "сервер создан: $CONF"
fi
umask 077
mkdir -p "$STATE/saved" && cp -a "$DIR/." "$STATE/saved/"

# 5. Клиент
CL=$STATE/clients/$CLIENT.conf
if [[ ! -f $CL ]]; then
  last=$(grep -oP "^AllowedIPs = $NET\.\K[0-9]+" "$CONF" | sort -n | tail -1 || true)
  IP=$NET.$(( ${last:-1} + 1 ))
  KEY=$(awg genkey); PSK=$(awg genpsk)
  SRVPUB=$(awk -F' = ' '/^PrivateKey/{print $2; exit}' "$CONF" | awg pubkey)
  PUBIP=$(curl -4 -s --max-time 10 https://ifconfig.me || ip -4 -o addr show scope global | awk '{sub(/\/.*/,"",$4); print $4; exit}')
  printf '\n# %s\n[Peer]\nPublicKey = %s\nPresharedKey = %s\nAllowedIPs = %s/32\n' \
    "$CLIENT" "$(echo "$KEY" | awg pubkey)" "$PSK" "$IP" >> "$CONF"
  {
    printf '[Interface]\nPrivateKey = %s\nAddress = %s/32\nDNS = 1.1.1.1, 8.8.8.8\nMTU = 1280\n' "$KEY" "$IP"
    grep -E '^(Jc|Jmin|Jmax|S1|S2|H1|H2|H3|H4) =' "$CONF"
    printf '\n[Peer]\nPublicKey = %s\nPresharedKey = %s\nEndpoint = %s:%s\nAllowedIPs = 0.0.0.0/0\nPersistentKeepalive = 25\n' \
      "$SRVPUB" "$PSK" "$PUBIP" "$PORT"
  } > "$CL"
  cp -a "$DIR/." "$STATE/saved/"
  echo "клиент создан: $CL ($IP)"
fi

# 6. Запуск (новый пир подхватывается без обрыва остальных)
systemctl enable -q awg-quick@awg0
if systemctl is-active -q awg-quick@awg0; then
  awg syncconf awg0 <(awg-quick strip awg0)
else
  systemctl start awg-quick@awg0
fi
awg show awg0 | sed -E 's/(private key|preshared key): .*/\1: (скрыт)/'
echo "✅ VPN работает: UDP $PORT"
