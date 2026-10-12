#!/usr/bin/env python3
"""Команды «Главному» из облачной сессии Claude. Адрес — HUB_URL, ключ — HUB_KEY
(Глеб получает их в Главном: /claude → «Включить») или HUB_TOKEN (токен Главного, если его ставили с HUB_REMOTE=1).

usage:
  python3 hub/remote.py status                  боты и сервер
  python3 hub/remote.py logs coach-bot [строк]  последние строки логов
  python3 hub/remote.py restart coach-bot       перезапуск
  python3 hub/remote.py sh "pm2 ls" [секунд]    команда на сервере (bash в /root/horo), вывод по мере работы
  python3 hub/remote.py say "текст"             написать Глебу через Главного
  python3 hub/remote.py update                  обновить всех ботов из main
Причину для Глеба можно передать через WHY="…" в окружении — она придёт вместе с командой.
"""
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request


def _call(action, body=None):
    url, key = os.environ.get("HUB_URL", "").rstrip("/"), os.environ.get("HUB_KEY", "")
    if not key and os.environ.get("HUB_TOKEN"):  # ключ, выведенный из токена Главного (HUB_REMOTE=1)
        key = hmac.new(os.environ["HUB_TOKEN"].encode(), b"claude-remote-v1", hashlib.sha256).hexdigest()
    if not (url and key):
        sys.exit("Нет HUB_URL / HUB_KEY. Глеб включает вход в Главном (/claude) и добавляет их в переменные облачной среды.")
    req = urllib.request.Request(f"{url}/api/{action}", data=json.dumps(body or {}).encode(), method="POST",
                                 headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    for attempt in range(20):  # сбой TLS бывает при рукопожатии (запрос ещё не ушёл) — повтор безопасен
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.load(r)
        except urllib.error.HTTPError:
            raise
        except urllib.error.URLError as e:
            if "SSL" not in str(e.reason) and "EOF" not in str(e.reason) or attempt == 19:
                raise
            time.sleep(2)


def call(action, body=None):
    try:
        return _call(action, body)
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e).get("error")
        except ValueError:
            msg = e.reason
        sys.exit(f"Главный ответил {e.code}: {msg}")
    except urllib.error.URLError as e:
        sys.exit(f"Главный не отвечает: {e.reason}")


def main(argv):
    if not argv:
        sys.exit(__doc__)
    cmd, args = argv[0], argv[1:]
    why = os.environ.get("WHY", "")
    if cmd == "status":
        r = call("status")
        for a in r["apps"] or []:
            print(f"{'🟢' if a['status'] == 'online' else '🔴'} {a['name']:<12} {a['status']:<8} "
                  f"{round(a['uptime'] / 60)} мин  {a['mem']} МБ  перезапусков {a['restarts']}")
        print("сервер:", json.dumps(r["server"], ensure_ascii=False), "· оплачен до", r.get("paid") or "—")
    elif cmd == "logs":
        print(call("logs", {"name": args[0], "lines": int(args[1]) if len(args) > 1 else 60})["out"])
    elif cmd == "restart":
        print(call("restart", {"name": args[0], "why": why})["out"])
    elif cmd == "say":
        call("say", {"text": " ".join(args)})
        print("отправлено")
    elif cmd == "update":
        print(call("update").get("note"))
    elif cmd == "sh":
        timeout = int(args[1]) if len(args) > 1 else 600
        jid = call("exec", {"cmd": args[0], "timeout": timeout, "why": why})["id"]
        pos = 0
        while True:
            r = call("job", {"id": jid, "from": pos})
            sys.stdout.write(r["out"])
            sys.stdout.flush()
            pos = r["len"]
            if r["done"]:
                sys.exit(r["code"] or 0)
            time.sleep(2)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
