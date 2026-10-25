#!/usr/bin/env bash
# Откат VPN: выключает awg0, убирает пакеты, поставленные install.sh, PPA и настройку пересылки — сервер как до установки.
# Ключи и конфиги клиентов остаются в /root/vpn/saved и /root/vpn/clients: повторный install.sh вернёт тот же конфиг
# роутера. Стереть и их: bash vpn/uninstall.sh --purge
set -uo pipefail

STATE=/root/vpn
export DEBIAN_FRONTEND=noninteractive

systemctl disable --now awg-quick@awg0 2>/dev/null
rmmod amneziawg 2>/dev/null
rm -f /etc/sysctl.d/99-vpn-awg.conf
sysctl -q -w net.ipv4.ip_forward="$(cat "$STATE/before-ip_forward.txt" 2>/dev/null || echo 0)"

if [[ -s $STATE/installed-by-vpn.txt ]]; then
  # shellcheck disable=SC2046
  apt-get -y -q -o DPkg::Lock::Timeout=600 purge $(cat "$STATE/installed-by-vpn.txt") >/dev/null
fi
add-apt-repository -y --remove ppa:amnezia/ppa >/dev/null 2>&1
rm -f /etc/apt/sources.list.d/amnezia-ubuntu-ppa-*.list /etc/apt/sources.list.d/amnezia-ubuntu-ppa-*.sources
rm -rf /etc/amnezia
rm -f "$STATE/before-packages.txt" "$STATE/before-ip_forward.txt" "$STATE/installed-by-vpn.txt"
[[ ${1:-} == --purge ]] && rm -rf "$STATE"

echo "✅ VPN убран, сервер как до установки"
ip link show awg0 >/dev/null 2>&1 && echo "⚠️ awg0 ещё есть" || true
