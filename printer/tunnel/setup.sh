#!/bin/sh
# Ставит на принтер обратный SSH-туннель до сервера ботов и печатает открытый ключ для сервера.
# Запуск на принтере: sh setup.sh <адрес сервера>
# Повторный запуск обновляет скрипты, ключ не меняет. Печать, Klipper и Moonraker не трогает.
set -e
SERVER="$1"
[ -n "$SERVER" ] || { echo "Нужен адрес сервера"; exit 1; }
DIR=/usr/data/printer-tunnel
mkdir -p "$DIR" /root/.ssh
echo "SERVER=$SERVER" > "$DIR/config"
[ -f "$DIR/id_ecdsa" ] || dropbearkey -t ecdsa -s 256 -f "$DIR/id_ecdsa" >/dev/null
chmod 600 "$DIR/id_ecdsa"

cat > "$DIR/run.sh" <<'EOF'
#!/bin/sh
# Держит туннель: порт 17125 на сервере ведёт в Moonraker, 17999 — в websocket экрана.
export HOME=/root
. /usr/data/printer-tunnel/config
while true; do
  dbclient -y -N -T -K 30 -W 262144 -i /usr/data/printer-tunnel/id_ecdsa \
    -R 17125:127.0.0.1:7125 -R 17999:127.0.0.1:9999 "printertunnel@$SERVER"
  sleep 20
done
EOF
chmod 755 "$DIR/run.sh"

cat > /etc/init.d/S99printer_tunnel <<'EOF'
#!/bin/sh
PIDFILE=/var/run/printer_tunnel.pid
stop() {
  [ -f "$PIDFILE" ] && kill "$(cat "$PIDFILE")" 2>/dev/null
  rm -f "$PIDFILE"
  killall dbclient 2>/dev/null
  return 0
}
case "$1" in
  start)
    /usr/data/printer-tunnel/run.sh >/dev/null 2>&1 &
    echo $! > "$PIDFILE"
    ;;
  stop) stop ;;
  restart) stop; sleep 1; "$0" start ;;
  *) echo "Usage: $0 {start|stop|restart}"; exit 1 ;;
esac
EOF
chmod 755 /etc/init.d/S99printer_tunnel
dropbearkey -y -f "$DIR/id_ecdsa" | grep '^ecdsa' | cut -d' ' -f1,2
