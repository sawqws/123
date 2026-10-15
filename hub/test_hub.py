"""Проверка «Главного» без Telegram и pm2: список ботов, тревоги, установка по токену, срок оплаты.
usage: python3 hub/test_hub.py
"""
import datetime
import hashlib
import hmac
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.update(HUB_TOKEN="111:HUB")
os.environ.pop("HUB_ALLOWED", None)
import hub  # noqa: E402

U = 7


def jlist(**status):
    """status: имя → (статус, перезапусков[, код последнего выхода — по умолчанию 1, падение])."""
    return json.dumps([{"name": n, "pm2_env": {"status": v[0], "pm_uptime": (time.time() - 7200) * 1000,
                                               "restart_time": v[1], "exit_code": v[2] if len(v) > 2 else 1},
                        "monit": {"memory": 50 * 1048576, "cpu": 1}}
                       for n, v in status.items()])


class Base(unittest.TestCase):
    def setUp(self):
        d = self.dir = tempfile.mkdtemp()
        self.p = [mock.patch.object(hub, "DATA", d), mock.patch.object(hub, "STATE", os.path.join(d, "s.json")),
                  mock.patch.object(hub, "tg", side_effect=self.fake_tg),
                  mock.patch.object(hub, "server", return_value={"mem_total": 2000, "mem_free": 900,
                                                                 "disk_total": 80.0, "disk_free": 40.0})]
        self.p.append(mock.patch.object(hub, "AGENT_SOCK", os.path.join(d, "agent.sock")))  # Секретаря пока нет
        for p in self.p:
            p.start()
        self.sent = []
        self.pm2 = {"horo-bot": ("online", 1), "coach-bot": ("online", 0), "hub-bot": ("online", 0)}
        self.runs = []
        self.sh = mock.patch.object(hub, "sh", side_effect=self.fake_sh)
        self.sh.start()
        hub.on_message(self.msg("/start"))  # первый /start — «Как удобнее?»; дальше тесты живут в режиме «чат»
        self.cq("mode|chat")

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

    def cq(self, data, uid=U, mid=1):
        hub.on_callback({"id": "1", "from": {"id": uid}, "data": data, "message": {"chat": {"id": uid}, "message_id": mid}})

    def last(self):
        m = [kw for k, kw in self.sent if k in ("sendMessage", "editMessageText")][-1]
        return m["text"], json.dumps(m.get("reply_markup", {}), ensure_ascii=False)


class HubTest(Base):
    def test_owner_and_home(self):
        self.assertEqual(hub.owner(), U)
        text, btns = self.last()  # /start — один главный экран
        self.assertIn("Все боты работают · 3", text)
        self.assertIn("Секретарь не отвечает", text)
        self.assertIn('"srst"', btns)
        self.assertIn('"bots"', btns)
        hub.on_message(self.msg("/bots"))
        text, btns = self.last()
        self.assertIn("работает 3 из 3", text)
        self.assertIn("Тренер «Форма»", text)
        self.assertIn("app|coach-bot", btns)
        self.assertNotIn("app|hub-bot", btns)
        hub.on_message(self.msg("/bots", uid=99))
        self.assertIn("личный", self.last()[0])
        hub.on_message(self.msg("привет", uid=99))
        self.assertIn("личный", self.last()[0])  # чужой текст Секретарю не уходит

    def answers(self):
        return [kw.get("text") for k, kw in self.sent if k == "answerCallbackQuery"]

    def test_every_button_is_answered(self):
        """Устаревшая кнопка, упавший экран, удалённое сообщение — ответ на нажатие есть всегда, ошибка — «⚠️ …» в чат."""
        self.cq("старое|что-то")
        self.assertIn("устарела", self.answers()[-1])
        self.cq("rs")  # без бота — тоже устаревшая, а не тишина
        self.assertIn("устарела", self.answers()[-1])
        with mock.patch.object(hub, "server_card", side_effect=RuntimeError("диск не ответил")):
            self.cq("srv")
        self.assertIn("⚠️", self.answers()[-1])
        text, btns = self.last()
        self.assertIn("Не получилось", text)
        self.assertIn("диск не ответил", text)
        self.assertIn('"home"', btns)
        hub.on_callback({"id": "1", "from": {"id": U}, "data": "home", "message": {}})  # сообщение недоступно
        self.assertIn("устарела", self.answers()[-1])
        self.cq("mkr|нет-такого")
        self.assertIn("уже нет", self.answers()[-1])

    def test_background_action_failure_is_reported(self):
        """Долгое действие из чата упало в фоне — человек узнаёт об этом, а не ждёт вечно."""
        with mock.patch.object(hub.threading, "Thread", lambda target, args=(), daemon=None: mock.Mock(start=lambda: target(*args))), \
                mock.patch.object(hub, "restart_app", side_effect=OSError("pm2 пропал")):
            self.cq("rs|coach-bot")
        self.assertEqual(self.answers()[-1], "Перезапускаю…")
        self.assertIn("pm2 пропал", self.last()[0])

    def test_update_not_twice(self):
        hub.live.clear()
        with mock.patch.object(hub, "bg") as bg:
            self.cq("upd")
            self.cq("upd")
        self.assertEqual(bg.call_count, 1)
        self.assertIn("Уже обновляю", self.answers()[-1])
        hub.live.clear()

    def test_logs_fit_in_message(self):
        with mock.patch.object(hub, "log_text", return_value="<&>" * 3000):
            hub.logs(U, "coach-bot")
        self.assertLessEqual(len(self.last()[0]), 4096)
        self.assertTrue(self.last()[0].endswith("</pre>"))

    def test_send_falls_back_to_plain_text(self):
        """Telegram не принял разметку (400) — то же сообщение уходит простым текстом, а не пропадает."""
        calls = []

        def fake(method, **kw):
            calls.append((method, kw))
            if kw.get("parse_mode") == "HTML":
                e = urllib.error.HTTPError("u", 400, "Bad Request", {}, None)
                e.detail = '{"description":"Bad Request: can\'t parse entities: Unsupported start tag"}'
                raise e
            return {"ok": True}
        with mock.patch.object(hub, "tg", side_effect=fake):
            hub.send(U, "🤖 <b>Claude:</b> &lt;script&gt; " + "я" * 5000, [[hub.b("OK", "home")]])
        self.assertEqual(len(calls), 2)
        text = calls[1][1]["text"]
        self.assertNotIn("<b>", text)
        self.assertIn("<script>", text)
        self.assertLessEqual(len(text), 4096)
        self.assertIn("reply_markup", calls[1][1])  # кнопки на месте
        self.assertNotIn("parse_mode", calls[1][1])

    def test_send_other_errors_are_raised(self):
        e = urllib.error.HTTPError("u", 403, "Forbidden", {}, None)
        e.detail = "bot was blocked by the user"
        with mock.patch.object(hub, "tg", side_effect=e):
            with self.assertRaises(urllib.error.HTTPError):
                hub.send(U, "x")

    def test_mode_card_has_way_back(self):
        hub.on_message(self.msg("/mode"))
        self.assertIn('"home"', self.last()[1])

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

    def test_planned_restarts_are_not_a_loop(self):
        """3 обновления подряд (pm2 restart, код выхода 0) — не «падает и перезапускается»."""
        now = time.time()
        hub.watch_once(now)
        for i in range(1, 6):
            self.pm2["horo-bot"] = ("online", 1 + i, 0)
            self.assertEqual(hub.watch_once(now + 60 * i), [])
        self.pm2["horo-bot"] = ("online", 9, 1)  # а настоящие падения после них — тревога
        self.assertIn("перезапускается", hub.watch_once(now + 400)[0][0])

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

    def test_eye_via_botfather(self):
        """/eye → «Создать бота сам» → Секретарь создаёт бота в @BotFather → установка с его токеном."""
        tok = "8740000000:AAH" + "e" * 32

        class Resp:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self): return b'{"ok":true,"result":{"username":"glaz_eye_bot"}}'
        with mock.patch.object(hub.urllib.request, "urlopen", lambda *a, **k: Resp()), \
                mock.patch.object(hub.threading, "Thread", lambda target, args, daemon: mock.Mock(start=lambda: target(*args))), \
                mock.patch.object(hub.maker, "secretary_ready", return_value=True), \
                mock.patch.object(hub.maker, "botfather", return_value=(tok, None)) as bf:
            hub.on_message(self.msg("/eye"))
            text, btns = self.last()
            self.assertIn("Глаз", text)
            self.assertIn("bf|eye", btns)
            hub.on_callback({"id": "1", "from": {"id": U}, "data": "bf|eye",
                             "message": {"chat": {"id": U}, "message_id": 3}})
        bf.assert_called_once_with("Глаз", "glaz_eye")
        cmd, env = [r for r in self.runs if r[0][0] == "bash"][0]
        self.assertTrue(cmd[1].endswith("eye/install.sh"))
        self.assertEqual(env["EYE_TOKEN"], tok)
        self.assertIn("t.me/glaz_eye_bot", self.last()[1])

    def test_studio_install_by_token(self):
        """/studio <токен> → studio/install.sh с STUDIO_TOKEN; «🛠 Сделать бота» ведёт в Студию, если она есть."""
        tok = "8750000000:AAH" + "s" * 32

        class Resp:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self): return b'{"ok":true,"result":{"username":"bot_studio_bot"}}'
        with mock.patch.object(hub.urllib.request, "urlopen", lambda *a, **k: Resp()), \
                mock.patch.object(hub.threading, "Thread", lambda target, args, daemon: mock.Mock(start=lambda: target(*args))):
            hub.on_message(self.msg("/studio " + tok, mid=7))
        cmd, env = [r for r in self.runs if r[0][0] == "bash"][0]
        self.assertTrue(cmd[1].endswith("studio/install.sh"))
        self.assertEqual(env["STUDIO_TOKEN"], tok)
        self.assertIn(("deleteMessage", {"chat_id": U, "message_id": 7}), self.sent)
        self.assertIn("Студия", self.last()[0])
        self.assertIn("t.me/bot_studio_bot", self.last()[1])
        with mock.patch.object(hub.maker, "studio_link", lambda: "bot_studio_bot"):
            hub.maker.ask_make(U)
        self.assertIn("t.me/bot_studio_bot", self.last()[1])

    def test_eye_without_secretary(self):
        with mock.patch.object(hub.maker, "secretary_ready", return_value=False):
            hub.on_message(self.msg("/eye"))
        text, btns = self.last()
        self.assertIn("Пришли токен", text)
        self.assertNotIn("bf|eye", btns)

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

    def test_server_percent(self):
        s = {"mem_total": 1964, "mem_free": 1439, "disk_total": 78.7, "disk_free": 65.4, "load": "0.23",
             "uptime": 3 * 86400}
        self.assertEqual(hub.usage(s, cpus=1), {"mem": 27, "disk": 17, "cpu": 23})
        self.assertEqual(hub.usage({"load": "3.0"}, cpus=2), {"cpu": 150})
        self.assertEqual(hub.usage({}), {})
        with mock.patch.object(hub, "server", return_value=s), mock.patch.object(hub.os, "cpu_count", return_value=1):
            hub.on_message(self.msg("/server"))
        text, _ = self.last()
        self.assertIn("Сервер: всё в порядке", text)
        self.assertIn("🟢 Память: занято 27% (525 из 1964 МБ)", text)
        self.assertIn("🟢 Диск: занято 17% (13.3 из 78.7 ГБ)", text)
        self.assertIn("🟢 Процессор: занят 23%", text)
        with mock.patch.object(hub, "server", return_value={**s, "mem_free": 100}):
            hub.on_message(self.msg("/server"))
        self.assertIn("Сервер: перегружен", self.last()[0])


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
        self.assertEqual(self.post("status", key="bad")[0], 429)  # неверные ключи отклоняются на час
        self.assertEqual(self.post("status", key=hub.setting("key"))[0], 200)  # а верный ключ владельца не запирается
        self.assertTrue(any("ещё час" in kw.get("text", "") for _, kw in self.sent))

    def test_remote_validation(self):
        hub.on_callback({"id": "1", "from": {"id": U}, "data": "cl|on", "message": {"chat": {"id": U}, "message_id": 3}})
        k = hub.setting("key")
        self.assertEqual(self.post("restart", {"name": "--update-env"}, key=k)[0], 400)  # имя бота — не флаг pm2
        self.assertEqual(self.post("logs", {"name": ""}, key=k)[0], 400)

    def test_remote_cli(self):
        hub.on_callback({"id": "1", "from": {"id": U}, "data": "cl|on", "message": {"chat": {"id": U}, "message_id": 3}})
        r = self.remote("status")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("coach-bot", r.stdout)
        r = self.remote("sh", "echo привет; sleep 3; echo готово; exit 3")
        self.assertEqual(r.returncode, 3)
        self.assertEqual(r.stdout, "привет\nготово\n")
        texts = [kw.get("text", "") for _, kw in self.sent]
        # по умолчанию молча (Глеб 14.10): команда только в журнале «📜 Что делал»
        self.assertFalse(any("Claude выполняет" in t or "кодом 3" in t for t in texts))
        self.assertIn("echo привет", hub.CMDLOG[-1][1])
        hub.setting("remote_notify", True)
        self.remote("sh", "echo снова; exit 3")
        texts = [kw.get("text", "") for _, kw in self.sent]
        self.assertTrue(any("Claude выполняет" in t and "echo снова" in t for t in texts))
        self.assertTrue(any("кодом 3" in t for t in texts))
        hub.setting("remote_notify", False)
        hub.on_callback({"id": "2", "from": {"id": U}, "data": "cl|log", "message": {"chat": {"id": U}, "message_id": 3}})
        self.assertTrue(any("Что делал Claude" in kw.get("text", "") and "echo снова" in kw.get("text", "")
                            for _, kw in self.sent))
        self.assertEqual(self.remote("say", "проверка связи").returncode, 0)
        self.assertIn("🤖 <b>Claude:</b> проверка связи", [kw.get("text") for _, kw in self.sent])
        r = self.remote("status", key="bad")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("неверный ключ", r.stderr)


def init_data(uid=U, age=0, token="111:HUB", bad=False):
    """initData, как его подписывает Telegram (core.telegram.org/bots/webapps)."""
    p = {"auth_date": str(int(time.time()) - age), "query_id": "q", "user": json.dumps({"id": uid, "first_name": "Г"})}
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    p["hash"] = hmac.new(secret, "\n".join(f"{k}={v}" for k, v in sorted(p.items())).encode(), hashlib.sha256).hexdigest()
    if bad:
        p["hash"] = ("0" if p["hash"][0] != "0" else "1") + p["hash"][1:]
    return urllib.parse.urlencode(p)


class AppTest(Base):
    """Мини-приложение: проверка подписи, /app/api/state и действия — на настоящем веб-сервере Главного."""

    def setUp(self):
        super().setUp()
        self.p2 = [mock.patch.object(hub.maker, "username", lambda tok: None),
                   mock.patch.object(hub.time, "sleep", lambda s: None)]
        for p in self.p2:
            p.start()
        hub.fails.clear()
        hub.live.clear()
        self.httpd = hub.ThreadingHTTPServer(("127.0.0.1", 0), hub.Web)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        for p in self.p2:
            p.stop()
        super().tearDown()

    def call(self, path, body=None, init=None):
        req = urllib.request.Request(self.url + path, method="POST" if body is not None else "GET",
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"X-Init-Data": init if init is not None else init_data()})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_support_from_app(self):
        """🛠 Поддержка в приложении: бот из списка + текст (+ скриншот) → та же доработка, что «Доработать»."""
        import base64
        st = json.loads(self.call("/app/api/state")[1])
        self.assertIn({"id": "coach-bot", "name": hub.short(hub.maker.CATALOG["coach-bot"][0])}, st["support"])
        fixes = []
        with mock.patch.object(hub.maker, "fix", lambda chat, pm2, tz: fixes.append((chat, pm2, tz))), \
                mock.patch.object(hub.threading, "Thread", lambda target, args=(), daemon=None: mock.Mock(
                    start=lambda: target(*args))):
            shot = base64.b64encode(b"\x89PNG" + bytes(200_000)).decode()  # больше лимита /app/api/action
            code, body = self.call("/app/api/support", {"bot": "coach-bot", "text": "кольца пустые", "photo": shot})
            self.assertEqual((code, json.loads(body)), (200, {"ok": True}))
            self.assertEqual(fixes[-1][:2], (U, "coach-bot"))
            self.assertIn("кольца пустые", fixes[-1][2])
            shots = hub.setting("shots")
            self.assertTrue(shots[-1].endswith(".png") and os.path.exists(shots[-1]))  # maker.fix заберёт
            self.assertEqual(self.call("/app/api/support", {"bot": "nope-bot", "text": "x"})[0], 400)
            self.assertEqual(self.call("/app/api/support", {"bot": "coach-bot", "text": " "})[0], 400)
            self.assertEqual(self.call("/app/api/support", {"bot": "coach-bot", "text": "x"}, init=init_data(uid=99))[0],
                             403)
            with hub.maker.job:  # Claude занят другой правкой
                self.assertEqual(self.call("/app/api/support", {"bot": "coach-bot", "text": "x"})[0], 400)
        self.assertEqual(len(fixes), 1)

    def test_init_data(self):
        self.assertEqual(hub.check_init_data(init_data())["id"], U)
        self.assertIsNone(hub.check_init_data(init_data(bad=True)))  # битая подпись
        self.assertIsNone(hub.check_init_data(init_data(age=2 * 86400)))  # старше суток
        self.assertIsNone(hub.check_init_data(init_data(token="222:OTHER")))  # подписано другим ботом
        self.assertIsNone(hub.check_init_data(""))
        self.assertIsNone(hub.check_init_data("hash=abc&&&"))
        self.assertEqual(self.call("/app/api/state", init=init_data(bad=True))[0], 401)
        self.assertEqual(self.call("/app/api/state", init=init_data(age=2 * 86400))[0], 401)
        self.assertEqual(self.call("/app/api/state", init="")[0], 401)
        code, body = self.call("/app/api/state", init=init_data(uid=99))  # чужой: подпись верная, но не владелец
        self.assertEqual(code, 403)
        self.assertEqual(self.call("/app/api/action", {"action": "restart", "arg": "coach-bot"}, init=init_data(uid=99))[0], 403)
        self.assertNotIn(("pm2", "restart", "coach-bot"), [r[0] for r in self.runs])

    def test_page_and_state(self):
        code, page = self.call("/app", init="")
        self.assertEqual(code, 200)
        self.assertIn(b"telegram-web-app.js", page)
        self.assertIn(b"/app/app.js?v=", page)
        self.assertNotIn(b"{{v}}", page)
        self.assertEqual(self.call("/app/app.css", init="")[0], 200)
        self.assertEqual(self.call("/app/../hub.py", init="")[0], 404)
        hub.setting("paid", (datetime.datetime.now(hub.TZ).date() + datetime.timedelta(days=3)).isoformat())
        with hub.lock:
            st = hub.load()
            hub.digest.command(st, "/remind 09:00 вода", time.time())
            hub.save(st)
        code, body = self.call("/app/api/state")
        self.assertEqual(code, 200)
        s = json.loads(body)
        bots = {b["name"]: b for b in s["bots"]}
        self.assertEqual(bots["coach-bot"]["status"], "online")
        self.assertEqual(bots["coach-bot"]["title"], "Тренер «Форма»")
        self.assertEqual(bots["coach-bot"]["mem"], 50)
        self.assertEqual(bots["horo-bot"]["restarts"], 1)
        self.assertIsNone(bots["kpp-bot"]["status"])  # не поставлен, но в списке есть
        self.assertTrue(bots["hub-bot"]["self"])
        self.assertEqual(s["server"]["mem_total"], 2000)
        self.assertEqual(s["paid"]["left"], 3)
        self.assertEqual(s["reminders"][0]["text"], "вода")
        self.assertEqual(s["digest"], "21:00")

    def test_actions(self):
        code, body = self.call("/app/api/action", {"action": "restart", "arg": "coach-bot"})
        self.assertEqual(code, 200, body)
        self.assertTrue(json.loads(body)["ok"])
        self.assertIn(("pm2", "restart", "coach-bot"), [r[0] for r in self.runs])
        self.assertEqual(self.call("/app/api/action", {"action": "restart", "arg": "all"})[0], 400)  # только боты из pm2
        self.assertNotIn(("pm2", "restart", "all"), [r[0] for r in self.runs])
        code, body = self.call("/app/api/action", {"action": "logs", "arg": "horo-bot"})
        self.assertEqual(json.loads(body)["text"], "ok")
        with hub.lock:
            st = hub.load()
            hub.digest.command(st, "/remind 09:00 вода", time.time())
            hub.save(st)
        rid = hub.load()["reminders"][0]["id"]
        code, body = self.call("/app/api/action", {"action": "reminder_delete", "arg": rid})
        self.assertTrue(json.loads(body)["ok"])
        self.assertEqual(hub.load()["reminders"], [])
        self.assertEqual(self.call("/app/api/action", {"action": "rm -rf"})[0], 400)
        with mock.patch.object(hub, "update_all") as upd:
            code, body = self.call("/app/api/action", {"action": "update"})
            self.assertEqual(code, 200)
            for _ in range(50):
                if upd.called:
                    break
                threading.Event().wait(0.02)  # time.sleep здесь заглушен
            upd.assert_called_once_with(U)
        self.assertEqual(hub.live["kind"], "update")

    def test_claude_key_still_works(self):
        hub.on_callback({"id": "1", "from": {"id": U}, "data": "cl|on", "message": {"chat": {"id": U}, "message_id": 3}})
        req = urllib.request.Request(f"{self.url}/api/status", data=b"{}", method="POST",
                                     headers={"Authorization": f"Bearer {hub.setting('key')}"})
        with urllib.request.urlopen(req, timeout=10) as r:
            self.assertEqual(json.load(r)["apps"][0]["name"], "horo-bot")
        self.assertEqual(self.call("/api/status", {}, init=init_data())[0], 401)  # подпись приложения ключ не заменяет


class ChatTest(Base):
    """Чат: кнопка приложения, цвет кнопок с откатом, все старые кнопки работают."""

    def markup(self):
        return [kw.get("reply_markup") for k, kw in self.sent if k in ("sendMessage", "editMessageText")][-1]

    def test_app_button(self):
        with mock.patch.dict(os.environ, {"HUB_URL": ""}), mock.patch.object(hub, "public_ip", None):
            hub.on_message(self.msg("/app"))
            self.assertIn("https", self.last()[0])
            self.assertNotIn("web_app", self.last()[1])
        with mock.patch.dict(os.environ, {"HUB_URL": "https://hub.1-2-3-4.sslip.io/"}):
            hub.on_message(self.msg("/app"))
            self.assertIn('"web_app": {"url": "https://hub.1-2-3-4.sslip.io/app"}', self.last()[1])
            hub.set_mode("app")  # кнопка приложения в экранах — только в режиме «приложение»
            hub.on_message(self.msg("/bots"))
            self.assertEqual(self.markup()["inline_keyboard"][0][0]["web_app"]["url"], "https://hub.1-2-3-4.sslip.io/app")
            self.assertTrue(hub.set_menu())
            self.assertEqual(self.sent[-1][1]["menu_button"]["web_app"]["url"], "https://hub.1-2-3-4.sslip.io/app")
            self.assertEqual(self.sent[-1][1]["chat_id"], U)
        with mock.patch.dict(os.environ, {"HUB_URL": "http://no-tls"}), mock.patch.object(hub, "public_ip", None):
            self.assertEqual(hub.app_url(), "")  # Telegram открывает мини-приложения только по https

    def test_layout(self):
        self.pm2["kpp-bot"] = ("errored", 3)
        hub.on_message(self.msg("/bots"))
        text, _ = self.last()
        self.assertIn("<blockquote>", text)
        self.assertNotIn("  ", text)  # без выравнивания пробелами
        rows = self.markup()["inline_keyboard"]
        self.assertTrue(all(len(r) <= 3 for r in rows))
        self.assertTrue(all(len(btn["text"]) <= 24 for r in rows for btn in r))
        kpp = [btn for r in rows for btn in r if btn.get("callback_data") == "app|kpp-bot"][0]
        self.assertEqual(kpp["style"], "danger")  # упавший — красный

    def test_all_old_buttons_work(self):
        """Каждая кнопка главного экрана, «Ещё», сервера и карточки бота отвечает и не падает."""
        seen, queue = set(), ["home", "more", "srv", "app|coach-bot", "cl", "add", "all", "fix", "mk", "rmd|list",
                              "add|coach", "lg|coach-bot", "rs|coach-bot", "upd"]
        with mock.patch.object(hub.threading, "Thread", lambda target, args=(), daemon=None: mock.Mock(start=lambda: None)), \
                mock.patch.object(hub.maker, "username", lambda tok: None):
            while queue:
                data = queue.pop(0)
                if data in seen:
                    continue
                seen.add(data)
                n = len(self.sent)
                hub.on_callback({"id": "1", "from": {"id": U}, "data": data, "message": {"chat": {"id": U}, "message_id": 3}})
                self.assertEqual(self.sent[-1][0], "answerCallbackQuery", data)
                for k, kw in self.sent[n:]:
                    for r in (kw.get("reply_markup") or {}).get("inline_keyboard", []):
                        self.assertLessEqual(len(r), 3, data)
                        queue += [btn["callback_data"] for btn in r if "callback_data" in btn
                                  and btn["callback_data"].split("|")[0] in ("home", "more", "srv", "app", "cl", "add", "all", "fix", "rmd")
                                  and btn["callback_data"] not in ("cl|on", "cl|new", "cl|off", "cl|show")]
        self.assertIn("more", seen)

    def test_style_fallback(self):
        calls = []

        class Resp:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self): return b'{"ok":true,"result":{}}'

        def urlopen(req, timeout=0):
            body = json.loads(req.data)
            calls.append(body)
            if '"style"' in json.dumps(body.get("reply_markup") or {}):
                raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {},
                                             io.BytesIO(b'{"ok":false,"description":"Bad Request: can\'t parse inline keyboard button"}'))
            return Resp()
        self.p[2].stop()  # настоящий hub.tg, поддельная сеть
        try:
            with mock.patch.object(hub.urllib.request, "urlopen", urlopen), mock.patch.object(hub, "plain_buttons", False):
                hub.send(U, "x", [[hub.b("a", "home", "danger")]])
                self.assertEqual(len(calls), 2)
                self.assertNotIn("style", json.dumps(calls[1]["reply_markup"]))
                hub.send(U, "y", [[hub.b("a", "home", "danger")]])
                self.assertEqual(len(calls), 3)  # дальше сразу без цвета
        finally:
            self.p[2].start()


class FakeSecretary:
    """Сокет Секретаря (как у tgbot/agent.py): пишет запросы, на _status отвечает аккаунтом."""

    def __init__(self, path, account=None):
        import socketserver
        self.got = []
        fake = self

        class H(socketserver.StreamRequestHandler):
            def handle(self):
                req = json.loads(self.rfile.readline())
                fake.got.append(req)
                ok, out = True, "ok"
                if req.get("cmd") == "_status":
                    out = {"account": account, "busy": False, "jobs": 0}
                elif req.get("cmd") == "_dialogs":
                    out = [{"id": 7, "name": "Петя", "kind": "личка"}, {"id": -100, "name": "8Б класс — очень длинное название", "kind": "группа"},
                           {"id": 8, "name": "Саша", "kind": "личка"}, {"id": 9, "name": "Мама", "kind": "личка"}] if account else []
                elif req.get("cmd") == "_psy":
                    ok, out = (True, {"id": "ab12", "q": "🧠 Разбор"}) if account else (False, "аккаунт не подключён — нужно войти в аккаунт (/login)")
                self.wfile.write(json.dumps({"ok": ok, "out": out}, ensure_ascii=False).encode() + b"\n")

        self.srv = socketserver.ThreadingUnixStreamServer(path, H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.open = True

    def updates(self):
        return [r["update"] for r in self.got if r.get("cmd") == "_hub"]

    def psy(self):
        return [r["args"] for r in self.got if r.get("cmd") == "_psy"]

    def close(self):
        if self.open:
            self.open = False
            self.srv.shutdown()
            self.srv.server_close()


def sync_threads():
    """threading.Thread, который выполняет работу сразу (для проверок без ожиданий)."""
    return mock.patch.object(hub.threading, "Thread",
                             lambda target, args=(), daemon=None: mock.Mock(start=lambda: target(*args)))


class SecretaryTest(Base):
    """Один бот: что Главный оставляет себе, что отдаёт Секретарю, кнопки с префиксом, Секретарь не отвечает."""

    def setUp(self):
        super().setUp()
        self.sec = FakeSecretary(hub.AGENT_SOCK, {"name": "Глеб Смирнов", "username": "gleb_s"})
        self.addCleanup(self.sec.close)

    def test_task_button_everywhere_and_next_message_goes_to_secretary(self):
        """Жалоба «секретарю не могу написать задачу»: кнопка на /start в обоих режимах, дальше любое сообщение — Секретарю."""
        for mode in ("chat", "app"):
            hub.set_mode(mode)
            with mock.patch.dict(os.environ, {"HUB_URL": "https://hub.1-2-3-4.sslip.io"}):
                hub.on_message(self.msg("/start"))
            text, btns = self.last()
            self.assertIn("✨ Поручить Секретарю", btns, mode)
            self.assertIn('"task"', btns)
            self.assertIn("напишите", text.lower())
            for row in json.loads(btns)["inline_keyboard"]:
                self.assertLessEqual(len(row), 3)
                for btn in row:
                    self.assertLessEqual(len(btn["text"]), 24)
        self.assertIn("Просто напишите задачу", hub.HELP)
        hub.set_mode("chat")
        n = len(self.sec.updates())
        self.cq("task")
        text, btns = self.last()
        self.assertEqual(hub.setting("wait"), "task")
        for ex in ("найди билеты в Китай на ноябрь", "забронируй стол", "что я пропустил"):
            self.assertIn(ex, text)
        self.assertIn('"home"', btns)  # отмена
        hub.on_message(self.msg("найди билеты в Китай на ноябрь"))
        self.assertEqual(self.sec.updates()[-1]["message"]["text"], "найди билеты в Китай на ноябрь")
        self.assertEqual(len(self.sec.updates()), n + 1)
        self.assertIsNone(hub.setting("wait"))  # одно сообщение — одно поручение
        hub.on_message(self.msg("а ещё отель"))  # и дальше обычный текст уходит Секретарю как раньше
        self.assertEqual(self.sec.updates()[-1]["message"]["text"], "а ещё отель")

    def test_task_voice_photo_command_and_cancel(self):
        n = len(self.sec.updates())
        self.cq("task")
        with sync_threads(), mock.patch.object(hub.media, "voice_text", return_value="найди билеты в Пекин"):
            hub.on_message({**self.msg(""), "voice": {"file_id": "v"}})
        self.assertEqual(self.sec.updates()[-1]["message"]["text"], "найди билеты в Пекин")
        self.assertIsNone(hub.setting("wait"))
        self.cq("task")
        hub.on_message({**self.msg(""), "photo": [{"file_id": "p"}], "caption": "найди такой же билет"})
        self.assertIn("photo", self.sec.updates()[-1]["message"])
        self.assertIsNone(hub.setting("wait"))
        hub.on_message(self.msg("/task найди отель в Шанхае"))  # сразу с текстом
        self.assertEqual(self.sec.updates()[-1]["message"]["text"], "найди отель в Шанхае")
        self.assertEqual(len(self.sec.updates()), n + 3)
        hub.on_message(self.msg("/task"))  # без текста — подсказка и ожидание
        self.assertIn("Поручение Секретарю", self.last()[0])
        self.assertEqual(hub.setting("wait"), "task")
        hub.on_message(self.msg("/bots"))  # команда Главного снимает ожидание
        self.assertIsNone(hub.setting("wait"))
        self.cq("task")
        self.cq("home")  # «Отмена»
        self.assertIsNone(hub.setting("wait"))
        hub.on_message(self.msg("привет"))
        self.assertEqual(self.sec.updates()[-1]["message"]["text"], "привет")

    def test_task_when_secretary_down_revives(self):
        self.sec.close()
        os.remove(hub.AGENT_SOCK)
        self.cq("task")
        with mock.patch.object(hub, "revive") as rv, sync_threads():
            hub.on_message(self.msg("найди билеты в Китай"))
        self.assertIn("Секретарь не отвечает", self.last()[0])
        rv.assert_called_once()
        self.assertIsNone(hub.setting("wait"))

    def test_webapp_composer_always_visible(self):
        """Поле поручения не прячется, когда Секретарь лежит: подсказки, поднятие и повтор отправки есть в app.js."""
        with open(os.path.join(HERE, "webapp", "app.js"), encoding="utf-8") as f:
            js = f.read()
        self.assertIn("Поручение или уточнение", js)  # поле — в закреплённом #dock, его строит buildDock() независимо от состояния Секретаря
        self.assertIn("const on = S.tab === 'sec' && !S.gate;", js)
        for chip in ("✈️ Найти билеты", "🔎 Найти в интернете", "📬 Что я пропустил"):
            self.assertIn(chip, js)
        view = js[js.index("function secView()"):js.index("// 🧠 Психолог")]
        self.assertNotIn("return out + `<section", view)  # раньше при ошибке возвращался экран без поля
        self.assertIn('id="convo"', view)
        self.assertIn('id="ask"', js)
        self.assertIn("action: 'secretary'", js)  # отправка при ошибке поднимает Секретаря…
        self.assertIn("retryQueued", js)          # …и повторяет запрос

    def test_home_shows_account(self):
        hub.on_message(self.msg("/start"))
        text, btns = self.last()
        self.assertIn("👤 Глеб Смирнов · @gleb_s", text)
        self.assertIn('"s:m:home"', btns)  # «Аккаунт» — экран Секретаря
        for label in ("Боты", "Аккаунт", "Сделать бота", "Обновить всех", "🛠 Поддержка"):
            self.assertIn(label, btns)
        self.assertEqual(self.sec.updates(), [])

    def test_what_goes_where(self):
        hub.on_message(self.msg("Ответьте Пете, что буду в 6"))
        hub.on_message(self.msg("/login"))
        hub.on_message(self.msg("/jobs"))
        hub.on_message({**self.msg(""), "contact": {"phone_number": "79990001122"}})  # номер для входа
        texts = [u["message"].get("text") for u in self.sec.updates()]
        self.assertEqual(texts, ["Ответьте Пете, что буду в 6", "/login", "/jobs", ""])
        self.assertEqual({r["owner"] for r in self.sec.got if r.get("cmd") == "_hub"}, {U})
        n = len(self.sec.updates())
        for own in ("/bots", "/help", "/server", "/claude", "/paid 07.11", "✦ Меню", "/make"):
            hub.on_message(self.msg(own))
        self.assertEqual(len(self.sec.updates()), n)  # команды Главного — себе
        with mock.patch.object(hub.maker, "make") as make, sync_threads():
            hub.on_message(self.msg("бот, который напоминает пить воду"))  # ждём ТЗ после /make — это Главному
        make.assert_called_once_with(U, "бот, который напоминает пить воду")
        self.assertEqual(len(self.sec.updates()), n)
        hub.on_message(self.msg("/make"))
        hub.on_message(self.msg("/login"))  # передумал: вход в аккаунт — ожидание ТЗ снято
        hub.on_message(self.msg("+79990001122"))
        self.assertEqual([u["message"]["text"] for u in self.sec.updates()[-2:]], ["/login", "+79990001122"])

    def test_media(self):
        with sync_threads(), mock.patch.object(hub.media, "voice_text", return_value="что я пропустил"):
            hub.on_message({**self.msg(""), "voice": {"file_id": "v"}})
        self.assertIn("🎙 <i>«что я пропустил»</i>", [kw.get("text") for _, kw in self.sent])
        self.assertEqual(self.sec.updates()[-1]["message"]["text"], "что я пропустил")
        self.assertNotIn("voice", self.sec.updates()[-1]["message"])
        hub.on_message({**self.msg(""), "photo": [{"file_id": "p"}], "caption": "что это?"})
        hub.on_message({**self.msg(""), "video": {"file_id": "v", "duration": 5}})
        self.assertIn("photo", self.sec.updates()[-2]["message"])
        self.assertIn("video", self.sec.updates()[-1]["message"])

    def test_photo_to_task_while_fixing(self):
        n = len(self.sec.updates())
        hub.setting("wait", "fix|coach-bot")
        with sync_threads(), mock.patch.object(hub.media, "photo_path", return_value="/tmp/shot.jpg"), \
                mock.patch.object(hub.maker, "fix") as fix:
            hub.on_message({**self.msg(""), "photo": [{"file_id": "p"}], "caption": "кнопка не работает"})
        self.assertEqual(len(self.sec.updates()), n)  # скриншот к ТЗ, а не Секретарю
        self.assertEqual(hub.setting("shots"), ["/tmp/shot.jpg"])
        fix.assert_called_once_with(U, "coach-bot", "кнопка не работает")

    def test_callbacks(self):
        cq = {"id": "1", "from": {"id": U}, "data": "s:m:home", "message": {"chat": {"id": U}, "message_id": 3}}
        n = len(self.sent)
        hub.on_callback(cq)
        self.assertEqual(self.sec.updates()[-1], {"callback_query": cq})
        self.assertEqual(self.sent[n:], [])  # на кнопку отвечает сам Секретарь
        hub.on_callback({**cq, "data": "bots"})
        self.assertEqual(len(self.sec.updates()), 1)
        self.assertIn("работает", self.last()[0])
        hub.on_callback({**cq, "from": {"id": 99}})  # чужой — никуда
        self.assertEqual(len(self.sec.updates()), 1)

    def test_secretary_down(self):
        self.sec.close()
        os.remove(hub.AGENT_SOCK)
        with mock.patch.object(hub, "revive") as rv, sync_threads():
            hub.on_message(self.msg("привет"))
        text, btns = self.last()
        self.assertIn("Секретарь не отвечает", text)
        self.assertIn('"srst"', btns)
        rv.assert_called_once()
        with mock.patch.object(hub, "revive"), sync_threads():
            hub.on_callback({"id": "1", "from": {"id": U}, "data": "s:m:home",
                             "message": {"chat": {"id": U}, "message_id": 3}})
        self.assertEqual(self.sent[-1], ("answerCallbackQuery", {"callback_query_id": "1",
                                                                 "text": "Секретарь не отвечает"}))

    def test_revive_restarts_or_installs(self):
        self.pm2["tg-agent"] = ("errored", 4)
        with mock.patch.object(hub.time, "sleep", lambda s: None):
            hub.revive(U, force=True)
        self.assertIn(("pm2", "restart", "tg-agent"), [r[0] for r in self.runs])
        del self.pm2["tg-agent"]
        with mock.patch.object(hub.time, "sleep", lambda s: None):
            ok, _ = hub.revive(U, force=True)
        cmd, env = [r for r in self.runs if r[0][0] == "bash"][-1]
        self.assertTrue(cmd[1].endswith("tgbot/install.sh"))
        self.assertEqual(env["TG_MODE"], "hub")
        self.assertNotIn("TG_AGENT_TOKEN", env)  # своего токена у Секретаря больше нет
        self.assertTrue(ok)
        self.assertFalse(hub.revive(U)[0])  # сам по себе — не чаще раза в 2 минуты
        self.assertNotIn("tgbot", hub.INSTALL)  # ставить Секретаря по токену больше нельзя


class ProxyTest(AppTest):
    """Вкладка «Секретарь» мини-приложения: /app/agent/api/* → сервер приложения Секретаря."""

    def setUp(self):
        super().setUp()
        import http.server
        got = self.got = []

        class Agent(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                got.append((self.path, self.headers.get("X-Init-Data"), body))
                code = 401 if self.headers.get("X-Init-Data") == "bad" else 200
                data = json.dumps({"ok": code == 200, "path": self.path}).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(data)

        self.agent = hub.ThreadingHTTPServer(("127.0.0.1", 0), Agent)
        threading.Thread(target=self.agent.serve_forever, daemon=True).start()
        p = mock.patch.object(hub, "AGENT_PORT", self.agent.server_address[1])
        p.start()
        self.addCleanup(p.stop)
        self.agent_up = True

    def tearDown(self):
        if self.agent_up:
            self.agent.shutdown()
            self.agent.server_close()
        super().tearDown()

    def test_proxy(self):
        code, body = self.call("/app/agent/api/ask", {"text": "привет"}, init="sig")
        self.assertEqual((code, json.loads(body)["path"]), (200, "/api/ask"))
        self.assertEqual(self.got[-1], ("/api/ask", "sig", json.dumps({"text": "привет"}).encode()))
        self.assertEqual(self.call("/app/agent/api/state", {}, init="bad")[0], 401)  # подпись проверяет Секретарь
        self.assertEqual(self.call("/app/agent/index.html", init="")[0], 404)  # только API
        self.agent.shutdown()
        self.agent.server_close()
        self.agent_up = False
        code, body = self.call("/app/agent/api/state", {})
        self.assertEqual((code, json.loads(body)["error"]), (502, "Секретарь не запущен"))

    def test_page_has_kit_and_secretary_state(self):
        code, page = self.call("/app", init="")
        self.assertIn(b"/app/kit.css?v=", page)
        self.assertIn(b"/app/kit.js?v=", page)
        self.assertEqual(self.call("/app/kit.js", init="")[0], 200)
        self.assertEqual(self.call("/app/health", init="")[1], b"ok")
        s = json.loads(self.call("/app/api/state")[1])
        self.assertFalse(s["secretary"]["running"])
        self.pm2["tg-agent"] = ("online", 0)
        s = json.loads(self.call("/app/api/state")[1])
        self.assertTrue(s["secretary"]["running"])
        sec = [b for b in s["bots"] if b["name"] == "tg-agent"][0]
        self.assertTrue(sec["part"])
        self.assertIsNone(sec["username"])  # своего бота у Секретаря нет




class ModeTest(Base):
    """Режим «💬 чат / 📱 приложение»: выбор при первом /start, /mode, кнопки приложения только в режиме «приложение»."""

    def web_apps(self):
        mk = [kw.get("reply_markup") or {} for k, kw in self.sent if k in ("sendMessage", "editMessageText")][-1]
        return [btn for row in mk.get("inline_keyboard", []) for btn in row if "web_app" in btn]

    def test_first_start_asks(self):
        hub.setting("mode", None)
        hub.on_message(self.msg("/start"))
        text, btns = self.last()
        self.assertIn("Как удобнее?", text)
        self.assertIn('"mode|chat"', btns)
        self.assertIn('"mode|app"', btns)
        self.assertNotIn("Все боты работают", text)
        self.cq("mode|app")
        self.assertEqual(hub.ui_mode(), "app")
        texts = [kw.get("text") for k, kw in self.sent if k in ("sendMessage", "editMessageText")]
        self.assertIn("Режим: 📱 приложение", texts[-2])  # карточка стала подтверждением…
        self.assertIn("👑 <b>Главный</b>", texts[-1])    # …и пришёл главный экран
        self.assertIn("Режим: 📱 приложение", texts[-1])

    def test_chat_mode_has_no_webapp_buttons(self):
        with mock.patch.dict(os.environ, {"HUB_URL": "https://hub.1-2-3-4.sslip.io"}):
            hub.on_message(self.msg("/start"))
            text, btns = self.last()
            self.assertIn("<i>Режим: 💬 чат", text)
            self.assertEqual(self.web_apps(), [])
            for label in ("Боты", "Секретарь", "Сделать бота", "Обновить всех", "🛠 Поддержка", "🧠 Психолог", "⚙️ Режим"):
                self.assertIn(label, btns)
            hub.on_message(self.msg("/bots"))
            self.assertEqual(self.web_apps(), [])
            hub.on_message(self.msg("/app"))  # явная просьба — кнопка есть
            self.assertEqual(self.web_apps()[0]["web_app"]["url"], "https://hub.1-2-3-4.sslip.io/app")
            hub.on_message(self.msg("/mode"))
            self.assertIn("Как удобнее?", self.last()[0])
            self.cq("mode|ask")
            self.assertIn("Как удобнее?", self.last()[0])

    def test_app_mode_short_start(self):
        with mock.patch.dict(os.environ, {"HUB_URL": "https://hub.1-2-3-4.sslip.io"}):
            self.cq("mode|app")
            text, btns = self.last()
            self.assertIn("Режим: 📱 приложение", text)
            self.assertEqual(self.web_apps()[0]["web_app"]["url"], "https://hub.1-2-3-4.sslip.io/app")
            rows = [kw.get("reply_markup") for k, kw in self.sent if k == "sendMessage"][-1]["inline_keyboard"]
            self.assertEqual(len(rows), 4)  # приложение, «Поручить Секретарю», действия, режим
            self.assertTrue(all(len(r) <= 3 for r in rows))
            self.assertIn("✨ Поручить Секретарю", btns)
            self.assertIn("🧠 Психолог", btns)
            self.assertNotIn("Сделать бота", btns)  # коротко: главное — приложение
            hub.on_message(self.msg("/bots"))
            self.assertTrue(self.web_apps())
            hub.on_message(self.msg("привет"))  # текст всё равно работает (ушёл бы Секретарю)
            self.assertIn("Секретарь не отвечает", self.last()[0])
        with mock.patch.dict(os.environ, {"HUB_URL": ""}), mock.patch.object(hub, "public_ip", None):
            hub.on_message(self.msg("/start"))  # https нет — честно и всё в чате
            text, _ = self.last()
            self.assertIn("https-адреса пока нет", text)
            self.assertEqual(self.web_apps(), [])

    def test_switch_from_app(self):
        self.cq("mode|app")
        with sync_threads():
            self.assertEqual(hub.app_mode({"mode": "chat"}), {"mode": "chat"})
        self.assertEqual(hub.ui_mode(), "chat")
        texts = [kw.get("text") for k, kw in self.sent if k == "sendMessage"]
        self.assertIn("Режим: 💬 чат", texts[-2])
        self.assertIn("👑 <b>Главный</b>", texts[-1])  # главное меню чата пришло само
        self.assertEqual(self.web_apps(), [])
        with self.assertRaises(ValueError):
            hub.app_mode({"mode": "voice"})

    def test_mode_api(self):
        self.cq("mode|app")
        hub.fails.clear()
        httpd = hub.ThreadingHTTPServer(("127.0.0.1", 0), hub.Web)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        url = f"http://127.0.0.1:{httpd.server_address[1]}/app/api/mode"
        with sync_threads():
            req = urllib.request.Request(url, data=b'{"mode": "chat"}', method="POST", headers={"X-Init-Data": init_data()})
            with urllib.request.urlopen(req, timeout=10) as r:
                self.assertEqual(json.load(r), {"mode": "chat"})
        self.assertEqual(hub.ui_mode(), "chat")
        self.assertEqual(hub.app_state()["mode"], "chat")
        req = urllib.request.Request(url, data=b'{"mode": "chat"}', method="POST", headers={"X-Init-Data": "x"})
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(e.exception.code, 401)

    def test_secretary_gets_mode(self):
        sec = FakeSecretary(hub.AGENT_SOCK, None)
        self.addCleanup(sec.close)
        hub.on_message(self.msg("привет"))
        self.assertEqual([r["mode"] for r in sec.got if r.get("cmd") == "_hub"], ["chat"])


class FeedFrontTest(unittest.TestCase):
    """Лента разговора «Секретарь» в приложении (hub/webapp): уточнения во время работы встают в очередь."""

    def setUp(self):
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webapp")
        with open(os.path.join(base, "app.js"), encoding="utf-8") as f:
            self.js = f.read()
        with open(os.path.join(base, "index.html"), encoding="utf-8") as f:
            self.html = f.read()

    def test_syntax(self):
        import shutil
        node = shutil.which("node")
        if not node:
            self.skipTest("нет node")
        r = subprocess.run([node, "--check", os.path.join(os.path.dirname(os.path.abspath(__file__)), "webapp", "app.js")],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_dock_and_queue(self):
        self.assertIn('id="dock"', self.html)  # закреплённое поле ввода
        for need in ("S.sec.queue", "r.queue", "sec.conv", "sameConv", "followFeed", "в очереди №", "Добавил в очередь",
                     "Раньше", "Новый разговор"):
            self.assertIn(need, self.js, need)

    def test_send_never_blocked_while_working(self):
        # прежняя блокировка «Ещё занимаюсь прошлым поручением» и disabled от isWork() убраны
        self.assertNotIn("Ещё занимаюсь прошлым поручением", self.js)
        self.assertNotIn("|| isWork();", self.js)
        self.assertIn("isBusy()", self.js)

    def test_new_conversation_confirms(self):
        i = self.js.index("act === 'new'")
        self.assertIn("confirmBox(", self.js[i:i + 400])


class StudioTest(Base):
    """«Бот, который делает ботов»: видна на главном экране; если её нет, а Секретарь вошёл в аккаунт — ставится сама."""

    def setUp(self):
        super().setUp()
        self.sec = FakeSecretary(hub.AGENT_SOCK, {"name": "Глеб Смирнов", "username": "gleb_s"})
        self.addCleanup(self.sec.close)
        self.env = mock.patch.object(hub, "STUDIO_ENV", os.path.join(self.dir, "studio-bot.env"))
        self.env.start()
        self.addCleanup(self.env.stop)
        self.installs = []
        self.bf = mock.patch.object(hub, "botfather_install", side_effect=lambda chat, kind: self.installs.append(kind))
        self.bf.start()
        self.addCleanup(self.bf.stop)
        hub.setting("studio_try", None)

    def test_home_offers_install_when_missing(self):
        hub.on_message(self.msg("/start"))
        text, btns = self.last()
        self.assertIn("Поставить Студию", btns)
        self.assertIn('"stu"', btns)

    def test_home_shows_open_button_when_installed(self):
        self.pm2["studio-bot"] = ("online", 0)
        hub.setting("studio_user", "my_studio_bot")
        hub.on_message(self.msg("/start"))
        _, btns = self.last()
        self.assertIn("https://t.me/my_studio_bot", btns)
        self.assertIn("🏗 Студия", btns)
        self.assertNotIn("Поставить Студию", btns)

    def wait_installs(self, n):
        for _ in range(100):
            if len(self.installs) >= n:
                break
            time.sleep(0.02)
        return self.installs

    def test_button_installs_through_secretary(self):
        with mock.patch.object(hub.maker, "secretary_ready", return_value=True):
            self.cq("stu")
        self.assertEqual(self.wait_installs(1), ["studio"])

    def test_button_without_secretary_asks_token(self):
        with mock.patch.object(hub.maker, "secretary_ready", return_value=False):
            self.cq("stu")
        self.assertEqual(hub.setting("wait"), "studio")
        self.assertIn("токен", self.last()[0].lower())

    def test_auto_install_once_when_account_connected(self):
        t0 = 1_800_000_000.0
        self.assertTrue(hub.studio_auto(now=t0))
        self.assertEqual(self.wait_installs(1), ["studio"])
        self.assertFalse(hub.studio_auto(now=t0 + 60))  # повторно — не раньше чем через 6 часов
        self.assertEqual(self.installs, ["studio"])
        self.assertTrue(hub.studio_auto(now=t0 + hub.STUDIO_RETRY + 1))  # прошло 6 часов, всё ещё нет — ещё раз
        self.assertEqual(self.wait_installs(2), ["studio", "studio"])

    def test_no_auto_install_right_after_secretary_start(self):
        """Сразу после рестарта Секретаря (сокет свежий) аккаунт ещё подключается — ждём пару минут."""
        t_now = os.path.getmtime(hub.AGENT_SOCK) + 30
        self.assertFalse(hub.studio_auto(now=t_now))
        self.assertEqual(self.installs, [])
        self.assertTrue(hub.studio_auto(now=t_now + hub.STUDIO_SEC_AGE))

    def test_no_auto_install_without_account_or_when_present(self):
        self.sec.account = None
        with mock.patch.object(hub, "secretary_status", return_value={"account": None}):
            self.assertFalse(hub.studio_auto(now=1_800_000_000.0))
        self.pm2["studio-bot"] = ("errored", 5)  # стоит, но упала — это не повод ставить заново
        self.assertFalse(hub.studio_auto(now=1_800_100_000.0))
        self.assertEqual(self.installs, [])


class PsyTest(Base):
    """🧠 Психолог: карточка, «Все чаты» и «Один чат» уходят Секретарю, без входа в аккаунт — честно и кнопка входа."""

    def rows(self):
        mk = [kw.get("reply_markup") or {} for k, kw in self.sent if k in ("sendMessage", "editMessageText")][-1]
        return mk.get("inline_keyboard", [])

    def test_card_and_runs(self):
        sec = FakeSecretary(hub.AGENT_SOCK, {"name": "Глеб", "username": "gleb"})
        self.addCleanup(sec.close)
        hub.on_message(self.msg("/psy"))
        text, btns = self.last()
        self.assertIn("🧠 <b>Психолог</b>", text)
        for data in ("psy|week", "psy|month", "psy|one"):
            self.assertIn(f'"{data}"', btns)
        self.assertTrue(all(len(r) <= 3 for r in self.rows()))
        self.assertTrue(all(len(btn["text"]) <= 24 for r in self.rows() for btn in r))
        with sync_threads():
            self.cq("psy|week", mid=3)
        self.assertEqual(sec.psy(), [{"scope": "week", "chat": None}])
        self.assertIn("Разбираю", self.last()[0])
        with sync_threads():
            self.cq("psy|month", mid=3)
        self.assertEqual(sec.psy()[-1], {"scope": "month", "chat": None})
        # один чат: последние диалоги кнопками (по 3 в ряд, ≤ 24 символов) или имя текстом
        with sync_threads():
            self.cq("psy|one", mid=3)
        text, btns = self.last()
        self.assertIn("Один чат", text)
        self.assertIn('"psy|c|7"', btns)
        self.assertIn('"psy|c|-100"', btns)
        self.assertTrue(all(len(r) <= 3 for r in self.rows()))
        self.assertTrue(all(len(btn["text"]) <= 24 for r in self.rows() for btn in r))
        self.assertEqual(hub.setting("wait"), "psy")
        with sync_threads():
            self.cq("psy|c|-100", mid=3)
        self.assertEqual(sec.psy()[-1], {"scope": "chat", "chat": "-100"})
        self.assertIsNone(hub.setting("wait"))
        with sync_threads():
            self.cq("psy|one", mid=3)
            hub.on_message(self.msg("Саша за месяц, как ко мне относится"))
        self.assertEqual(sec.psy()[-1], {"scope": "chat", "chat": "Саша за месяц, как ко мне относится"})
        self.assertEqual(sec.updates(), [])  # имя чата — не свободный текст Секретарю
        hub.on_message(self.msg("/start"))
        self.assertIn('"psy"', self.last()[1])  # кнопка на главном экране
        hub.on_message(self.msg("🧠 Разбор чата"))  # кнопка клавиатуры Секретаря — та же карточка
        self.assertIn("🧠 <b>Психолог</b>", self.last()[0])
        self.assertEqual(sec.updates(), [])

    def test_not_logged_in(self):
        sec = FakeSecretary(hub.AGENT_SOCK, None)
        self.addCleanup(sec.close)
        hub.on_message(self.msg("/psy"))
        text, btns = self.last()
        self.assertIn("аккаунт пока не подключён", text)
        self.assertIn('"s:login:"', btns)  # вход — у Секретаря
        self.assertIn('"s:m:home"', btns)
        self.assertNotIn("psy|week", btns)
        with sync_threads():  # всё же запустили (старая карточка) — Секретарь отказал, предлагаем войти
            hub.psy_run(U, "week")
        text, btns = self.last()
        self.assertIn("Разбор не запустился", text)
        self.assertIn("аккаунт не подключён", text)
        self.assertIn('"s:login:"', btns)

    def test_secretary_down(self):
        with mock.patch.object(hub, "revive"), sync_threads():
            hub.on_message(self.msg("/psy"))
        text, btns = self.last()
        self.assertIn("не отвечает", text)
        self.assertIn('"srst"', btns)


if __name__ == "__main__":
    unittest.main(verbosity=1)
