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


if __name__ == "__main__":
    unittest.main(verbosity=1)
