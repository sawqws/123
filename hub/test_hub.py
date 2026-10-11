"""Проверка «Главного» без Telegram и pm2: список ботов, тревоги, установка по токену, срок оплаты.
usage: python3 hub/test_hub.py
"""
import datetime
import json
import os
import sys
import tempfile
import time
import unittest
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


class HubTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main(verbosity=1)
