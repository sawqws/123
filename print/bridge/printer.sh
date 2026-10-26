#!/bin/sh
# Мост принтер -> сервер Глеба (Ender-3 V3 KE, root по ssh). Запускается setup.ps1 с компьютера Глеба.
# На вход: H — адрес сервера, P — публичный ключ сервера для входа на принтер.
# Ключ туннеля создаётся тут же, на принтере; наружу уходит только его публичная часть (строка BRIDGE-KEY).
# Принтер сам держит обратный ssh-туннель: порт 2222 на сервере (только localhost) -> ssh принтера.
# Сообщения — латиницей: консоль Windows может не показать кириллицу.
set -u
say() { echo "[bridge] $*"; }
[ -n "${H:-}" ] && [ -n "${P:-}" ] || { say "ERROR: no host/key"; exit 1; }

D=/usr/data/bridge
mkdir -p "$D" && chmod 700 "$D"

# вход с сервера на принтер по ключу (пароль root не нужен)
HOME_DIR=$(awk -F: '$1=="root"{print $6}' /etc/passwd); HOME_DIR=${HOME_DIR:-/root}
mkdir -p "$HOME_DIR/.ssh" && chmod 700 "$HOME_DIR/.ssh"
grep -qF "$P" "$HOME_DIR/.ssh/authorized_keys" 2>/dev/null || echo "$P" >> "$HOME_DIR/.ssh/authorized_keys"
chmod 600 "$HOME_DIR/.ssh/authorized_keys"
if [ -d /etc/dropbear ]; then
  grep -qF "$P" /etc/dropbear/authorized_keys 2>/dev/null || echo "$P" >> /etc/dropbear/authorized_keys
  chmod 600 /etc/dropbear/authorized_keys
fi

# какой ssh-клиент есть на принтере; ключ туннеля — в его формате
if command -v ssh >/dev/null 2>&1 && ssh -V 2>&1 | grep -q OpenSSH && command -v ssh-keygen >/dev/null 2>&1; then
  [ -s "$D/key" ] || ssh-keygen -q -t rsa -b 3072 -N "" -C printer-tunnel -f "$D/key" >/dev/null
  PUB=$(cat "$D/key.pub")
  CL="ssh -i $D/key -N -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=$D/known_hosts -R 2222:127.0.0.1:22 printer@$H"
elif command -v dbclient >/dev/null 2>&1 && command -v dropbearkey >/dev/null 2>&1; then
  [ -s "$D/key.db" ] || dropbearkey -t rsa -s 3072 -f "$D/key.db" >/dev/null 2>&1
  PUB=$(dropbearkey -y -f "$D/key.db" 2>/dev/null | grep '^ssh-rsa')
  CL="dbclient -i $D/key.db -N -y -K 30 -R 2222:127.0.0.1:22 printer@$H"
else
  say "ERROR: no ssh client on printer"; uname -a; ls /usr/bin /usr/sbin /bin | tr '\n' ' ' | head -c 3000; exit 2
fi
[ -n "$PUB" ] || { say "ERROR: cannot make key"; exit 1; }
say "client: ${CL%% *}"

cat > "$D/run.sh" <<RUN
#!/bin/sh
while true; do
  [ -f $D/log ] && [ \$(wc -c < $D/log) -gt 200000 ] && : > $D/log
  echo "\$(date) connect" >> $D/log
  $CL >> $D/log 2>&1
  sleep 20
done
RUN
chmod 755 "$D/run.sh"

cat > /etc/init.d/S99bridge <<SVC
#!/bin/sh
case "\$1" in
  start) start-stop-daemon -S -b -m -p /var/run/bridge.pid -x /bin/sh -- $D/run.sh 2>/dev/null || (nohup $D/run.sh >/dev/null 2>&1 & echo \$! > /var/run/bridge.pid) ;;
  stop) kill \$(cat /var/run/bridge.pid 2>/dev/null) 2>/dev/null; pkill -f "printer@$H" 2>/dev/null ;;
  restart) \$0 stop; sleep 1; \$0 start ;;
esac
SVC
chmod 755 /etc/init.d/S99bridge
/etc/init.d/S99bridge restart

say "service started; it connects as soon as the server accepts the key"
echo "BRIDGE-KEY: $PUB"
