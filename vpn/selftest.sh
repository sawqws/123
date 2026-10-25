#!/usr/bin/env bash
# Самопроверка VPN на сервере: клиент из конфига (по умолчанию router) в отдельном netns подключается к awg0
# по публичному IP, проходит рукопожатие и выходит в интернет через туннель. Печатает внешний IP клиента.
set -euo pipefail
CL=/root/vpn/clients/${1:-router}.conf
NS=vpntest
EP=$(grep -oP '^Endpoint = \K[^:]+' "$CL")

cleanup() { ip netns del $NS 2>/dev/null || true; ip link del vt0 2>/dev/null || true; }
trap cleanup EXIT
cleanup

ip netns add $NS
ip link add vt0 type veth peer name vt1
ip link set vt1 netns $NS
ip addr add 10.77.0.1/30 dev vt0 && ip link set vt0 up
ip -n $NS addr add 10.77.0.2/30 dev vt1 && ip -n $NS link set vt1 up && ip -n $NS link set lo up
ip -n $NS route add "$EP/32" via 10.77.0.1

ip -n $NS link add awgt type amneziawg
ip netns exec $NS awg setconf awgt <(sed -E '/^(Address|DNS|MTU) =/d' "$CL")
ip -n $NS addr add "$(grep -oP '^Address = \K\S+' "$CL")" dev awgt
ip -n $NS link set awgt mtu 1280 up
ip -n $NS route add default dev awgt

ip=$(ip netns exec $NS curl -4 -s --max-time 15 https://ifconfig.me || true)
hs=$(ip netns exec $NS awg show awgt latest-handshakes | awk '{print $2}')
echo "рукопожатие: $([[ ${hs:-0} -gt 0 ]] && echo есть || echo НЕТ) · IP через туннель: ${ip:-нет ответа}"
[[ ${hs:-0} -gt 0 && -n $ip ]]
