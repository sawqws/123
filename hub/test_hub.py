"""Проверка «Главного» без Telegram и pm2: список ботов, тревоги, установка по токену, срок оплаты.
usage: python3 hub/test_hub.py
"""
import datetime
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.update(HUB_TOKEN="111:HUB")
os.environ.pop("HUB_ALLOWED", None)
import hub  # noqa: E402

U = 7


def jlist(**status):
    return json.dumps([{"name": n, "pm2_env": {"status": s, "pm_uptime": (time.time() - 7200) * 1000,
                                               "restart_time": r}, "monit": {"memory": 50 * 1048576, "cpu": 1}}
                       for n, (s, r) in status.items()])


class Base(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.p = [mock.patch.object(hub, "DATA", d), mock.patch.object(hub, "STATE", os.path.join(d, "s.json")),
                  mock.patch.object(hub, "tg", side_effect=self.fake_tg),
                  mock.patch.object(hub, "server", return_value={"mem_total": 2000, "mem_free": 900,
                                                                 "disk_total": 80.0, "disk_free": 40.0})]
        for p in self.p:
            p.start()
        self.sent = []
        self.pm2 = {"horo-bot": ("online", 1), "coach-bot": ("online", 0), "hub-bot": ("online", 0)}
        self.runs = []
        self.sh = mock.patch.object(hub, "sh", side_effect=self.fake_sh)
        self.sh.start()
        hub.on_message(self.msg("/start"))

    def tearDown(self):
        for p in self.p + [self.sh]:
            p.stop()

    def fake_tg(self, method, **kw):
        self.sent.append((method, kw))
        return {"ok": True, "result": {}}

    def fake_sh(self, *cmd, timeout=60, env=None):
        self.runs.append((cmd, env))
        if cmd[:2] == ("pm2", "jlist"):
            return 0, jlist(**self.pm2)
        return 0, "ok"

    def msg(self, text, uid=U, mid=1):
        return {"chat": {"id": uid, "type": "private"}, "from": {"id": uid}, "text": text, "message_id": mid}

    def last(self):
        m = [kw for k, kw in self.sent if k in ("sendMessage", "editMessageText")][-1]
        return m["text"], json.dumps(m.get("reply_markup", {}), ensure_ascii=False)


class HubTest(Base):
    def test_owner_and_home(self):
        self.assertEqual(hub.owner(), U)
        text, btns = self.last()
        self.assertIn("работает 3 из 3", text)
        self.assertIn("Тренер «Форма»", text)
        self.assertIn("app|coach-bot", btns)
        self.assertNotIn("app|hub-bot", btns)
        hub.on_message(self.msg("/bots", uid=99))
        self.assertIn("личный", self.last()[0])

    def test_alerts(self):
        self.assertEqual(hub.watch_once(), [])
        self.pm2["coach-bot"] = ("errored", 0)
        self.assertEqual(hub.watch_once(), [])  # одна неудачная проверка — ещё не тревога
        out = hub.watch_once()
        self.assertEqual(len(out), 1)
        self.assertIn("не работает", out[0][0])
        self.assertEqual(hub.watch_once(), [])  # второй раз не пишем
        self.pm2["coach-bot"] = ("online", 0)
        self.assertIn("снова работает", hub.watch_once()[0][0])

    def test_restart_loop(self):
        now = time.time()
        hub.watch_once(now)
        self.pm2["horo-bot"] = ("online", 5)
        out = hub.watch_once(now + 120)
        self.assertIn("перезапускается", out[0][0])
        self.pm2["horo-bot"] = ("online", 9)
        self.assertEqual(hub.watch_once(now + 240), [])  # не чаще раза в час

    def test_install_by_token(self):
        tok = "8730000000:AAH" + "z" * 32

        class Resp:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self): return b'{"ok":true,"result":{"username":"TrainerWhoopbot"}}'
        with mock.patch.object(hub.urllib.request, "urlopen", lambda *a, **k: Resp()), \
                mock.patch.object(hub.threading, "Thread", lambda target, args, daemon: mock.Mock(start=lambda: target(*args))):
            hub.on_message(self.msg("/coach"))
            self.assertIn("Пришли токен", self.last()[0])
            hub.on_message(self.msg(tok, mid=5))
        cmd, env = [r for r in self.runs if r[0][0] == "bash"][0]
        self.assertTrue(cmd[1].endswith("coach/install.sh"))
        self.assertEqual(env["COACH_TOKEN"], tok)
        self.assertNotIn("HUB_TOKEN", env)
        self.assertIn(("deleteMessage", {"chat_id": U, "message_id": 5}), self.sent)
        self.assertIn("запущен", self.last()[0])
        self.assertIn("t.me/TrainerWhoopbot", self.last()[1])

    def test_paid(self):
        today = datetime.date(2026, 10, 11)
        self.assertEqual(hub.parse_paid("/paid 07.11", today), "2026-11-07")
        self.assertEqual(hub.parse_paid("/paid 05.01", today), "2027-01-05")
        self.assertEqual(hub.parse_paid("/paid 7.11.2026", today), "2026-11-07")
        hub.setting("paid", "2026-11-07")
        at = datetime.datetime(2026, 10, 31, 12, 0, tzinfo=hub.TZ).timestamp()  # за 7 дней
        out = hub.watch_once(at)
        self.assertIn("осталось 7 дн", out[0][0])
        self.assertEqual(hub.watch_once(at + 60), [])
        self.assertEqual(hub.watch_once(at + 86400), [])  # за 6 дней молчим

    def test_low_disk(self):
        with mock.patch.object(hub, "server", return_value={"disk_total": 80.0, "disk_free": 3.0}):
            self.assertIn("На диске", hub.watch_once()[0][0])
            self.assertEqual(hub.watch_once(), [])


class RemoteTest(Base):
    """Вход для Claude: настоящий веб-сервер Главного на свободном порту и hub/remote.py против него."""

    def setUp(self):
        super().setUp()
        self.sh.stop()  # здесь bash настоящий, pm2 — поддельный
        real = hub.sh
        self.sh = mock.patch.object(hub, "sh", side_effect=lambda *c, **k: self.fake_sh(*c, **k)
                                    if c[0] == "pm2" else real(*c, **k))
        self.sh.start()
        hub.fails.clear()
        self.httpd = hub.ThreadingHTTPServer(("127.0.0.1", 0), hub.Web)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        super().tearDown()

    def post(self, action, body=None, key=None):
        req = urllib.request.Request(f"{self.url}/api/{action}", data=json.dumps(body or {}).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {key}"} if key else {})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def remote(self, *args, key=None):
        env = {**os.environ, "HUB_URL": self.url, "HUB_KEY": key or hub.setting("key") or "",
               "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}
        return subprocess.run([sys.executable, os.path.join(HERE, "remote.py"), *args], env=env,
                              capture_output=True, text=True, timeout=30)

    def test_off_by_default_and_keys(self):
        self.assertEqual(self.post("status", key="x")[0], 403)
        hub.on_callback({"id": "1", "from": {"id": U}, "data": "cl|on", "message": {"chat": {"id": U}, "message_id": 3}})
        key = hub.setting("key")
        self.assertGreater(len(key), 30)
        self.assertTrue(any(key in kw.get("text", "") for _, kw in self.sent))  # ключ пришёл владельцу
        self.assertEqual(self.post("status")[0], 401)
        self.assertEqual(self.post("status", key="wrong")[0], 401)
        code, r = self.post("status", key=key)
        self.assertEqual(code, 200)
        self.assertEqual(r["apps"][0]["name"], "horo-bot")
        hub.on_callback({"id": "2", "from": {"id": U}, "data": "cl|new", "message": {"chat": {"id": U}, "message_id": 3}})
        self.assertEqual(self.post("status", key=key)[0], 401)  # старый ключ больше не работает
        hub.on_callback({"id": "3", "from": {"id": U}, "data": "cl|off", "message": {"chat": {"id": U}, "message_id": 3}})
        self.assertEqual(self.post("status", key=hub.setting("key"))[0], 403)

    def test_bootstrap_token_key(self):
        with mock.patch.dict(os.environ, {"HUB_REMOTE": "1"}):
            hub.bootstrap_remote()
        self.assertEqual(hub.setting("key"), hub.token_key("111:HUB"))
        r = self.remote("status", key="-")  # без HUB_KEY, ключ — из HUB_TOKEN
        self.assertNotEqual(r.returncode, 0)
        env = {**os.environ, "HUB_URL": self.url, "HUB_TOKEN": "111:HUB", "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}
        env.pop("HUB_KEY", None)
        r = subprocess.run([sys.executable, os.path.join(HERE, "remote.py"), "status"], env=env,
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        hub.on_callback({"id": "1", "from": {"id": U}, "data": "cl|new", "message": {"chat": {"id": U}, "message_id": 3}})
        r = subprocess.run([sys.executable, os.path.join(HERE, "remote.py"), "status"], env=env,
                           capture_output=True, text=True, timeout=30)
        self.assertNotEqual(r.returncode, 0)  # после «Новый ключ» ключ из токена больше не подходит

    def test_lockout(self):
        hub.on_callback({"id": "1", "from": {"id": U}, "data": "cl|on", "message": {"chat": {"id": U}, "message_id": 3}})
        for _ in range(20):
            self.post("status", key="bad")
        self.assertEqual(self.post("status", key=hub.setting("key"))[0], 429)
        self.assertTrue(any("закрыт на час" in kw.get("text", "") for _, kw in self.sent))

    def test_remote_cli(self):
        hub.on_callback({"id": "1", "from": {"id": U}, "data": "cl|on", "message": {"chat": {"id": U}, "message_id": 3}})
        r = self.remote("status")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("coach-bot", r.stdout)
        r = self.remote("sh", "echo привет; sleep 3; echo готово; exit 3")
        self.assertEqual(r.returncode, 3)
        self.assertEqual(r.stdout, "привет\nготово\n")
        texts = [kw.get("text", "") for _, kw in self.sent]
        self.assertTrue(any("Claude выполняет" in t and "echo привет" in t for t in texts))
        self.assertTrue(any("кодом 3" in t for t in texts))
        self.assertEqual(self.remote("say", "проверка связи").returncode, 0)
        self.assertIn("🤖 <b>Claude:</b> проверка связи", [kw.get("text") for _, kw in self.sent])
        r = self.remote("status", key="bad")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("неверный ключ", r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=1)
