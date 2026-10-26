#!/bin/sh
# Мост принтер -> сервер Глеба (Ender-3 V3 KE, root по ssh). Запускается setup.ps1 с компьютера Глеба.
# На вход: K — ключ туннеля (base64), H — адрес сервера, P — ключ сервера для входа на принтер.
# Принтер сам держит обратный ssh-туннель: порт 2222 на сервере (только localhost) -> ssh принтера.
# Сообщения — латиницей: консоль Windows может не показать кириллицу.
set -u
say() { echo "[bridge] $*"; }
[ -n "${K:-}" ] && [ -n "${H:-}" ] && [ -n "${P:-}" ] || { say "ERROR: no key/host"; exit 1; }

D=/usr/data/bridge
mkdir -p "$D" && chmod 700 "$D"
b64d() { base64 -d 2>/dev/null || python3 -c 'import sys,base64;sys.stdout.buffer.write(base64.b64decode(sys.stdin.read()))'; }
echo "$K" | b64d > "$D/key" && chmod 600 "$D/key"
[ -s "$D/key" ] || { say "ERROR: cannot decode key"; exit 1; }

# вход с сервера на принтер по ключу (пароль root не нужен)
HOME_DIR=$(awk -F: '$1=="root"{print $6}' /etc/passwd); HOME_DIR=${HOME_DIR:-/root}
mkdir -p "$HOME_DIR/.ssh" && chmod 700 "$HOME_DIR/.ssh"
grep -qF "$P" "$HOME_DIR/.ssh/authorized_keys" 2>/dev/null || echo "$P" >> "$HOME_DIR/.ssh/authorized_keys"
chmod 600 "$HOME_DIR/.ssh/authorized_keys"
if [ -d /etc/dropbear ]; then
  grep -qF "$P" /etc/dropbear/authorized_keys 2>/dev/null || echo "$P" >> /etc/dropbear/authorized_keys
  chmod 600 /etc/dropbear/authorized_keys
fi

# какой ssh-клиент есть на принтере
if command -v ssh >/dev/null 2>&1 && ssh -V 2>&1 | grep -q OpenSSH; then
  CL="ssh -i $D/key -N -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=$D/known_hosts -R 2222:127.0.0.1:22 printer@$H"
elif command -v dbclient >/dev/null 2>&1; then
  KEY=$D/key
  if command -v dropbearconvert >/dev/null 2>&1 && dropbearconvert openssh dropbear "$D/key" "$D/key.db" >/dev/null 2>&1; then KEY=$D/key.db; fi
  CL="dbclient -i $KEY -N -y -K 30 -R 2222:127.0.0.1:22 printer@$H"
else
  say "ERROR: no ssh client on printer"; uname -a; ls /usr/bin | tr '\n' ' ' | head -c 3000; exit 2
fi
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

sleep 10
if pgrep -f "printer@$H" >/dev/null 2>&1 || ps | grep -v grep | grep -q "printer@$H"; then
  say "OK: bridge is up"
else
  say "ERROR: tunnel did not start"; tail -n 15 "$D/log"; exit 3
fi
