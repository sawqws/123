"""Тесты Студии и botkit без сети и без Telegram: python3 studio/test_studio.py"""
import hashlib
import hmac
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
import urllib.request
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="studio-test-")
os.environ["STUDIO_BOTS_DIR"] = os.path.join(TMP, "bots")
os.environ["STUDIO_TOKEN"] = "123456:TESTTOKENTESTTOKENTESTTOKENTESTTOKEN"
sys.path.insert(0, HERE)
import studio  # noqa: E402

kit = studio.kit
OWNER = 42


def signed(token, user):
    fields = {"auth_date": str(int(time.time())), "user": json.dumps(user)}
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(fields)


class Fake:
    """Подменяет Claude, pm2, @BotFather и getMe."""

    def __init__(self):
        self.prompts, self.pm2, self.pm2_all = [], [], []
        self.interview = {"questions": [{"q": "Сколько стаканов в день?", "options": ["8", "10"]},
                                        {"q": "Когда напоминать?", "options": ["Каждый час", "Утром"]}],
                          "names": ["Вода", "Глоток", "Аква"], "emoji": "💧"}
        self.plan = {"summary": "Считает воду", "chat": ["кнопки +250 мл"], "app": ["прогресс за день"],
                     "auto": [], "commands": [{"command": "start", "description": "Главное"}],
                     "username": "voda_test", "slug": "voda", "colors": ["#0ea5e9", "#6366f1"]}
        self.token = "777777:BOTTOKENBOTTOKENBOTTOKENBOTTOKENBOT"

    def claude(self, prompt, cwd, on_step=None, timeout=0, tools=True, model=None):
        self.prompts.append(prompt)
        if "уточняющих" in prompt:
            return True, json.dumps(self.interview, ensure_ascii=False)
        if "план бота" in prompt:
            return True, json.dumps(self.plan, ensure_ascii=False)
        if on_step:
            on_step("пишу bot.py")
        return True, "Считает воду и напоминает."

    def sh(self, *cmd, timeout=60, cwd=None, env=None):
        self.pm2_all.append(cmd)
        if cmd[1:2] and "tg.py" in cmd[1]:
            return 0, "ok"
        if cmd[0] == "pm2":
            self.pm2.append(cmd)
            if cmd[1] == "jlist":
                names = [c[c.index("--name") + 1] for c in self.pm2 if c[1] == "start"]
                return 0, json.dumps([{"name": n, "pm2_env": {"status": "online"}} for n in names])
            return 0, ""
        return REAL_SH(*cmd, timeout=timeout, cwd=cwd, env=env)


REAL_SH = studio.sh


class StudioFlow(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(os.path.join(TMP, "bots"), ignore_errors=True)
        self.t = studio.bot.testing(owner=OWNER)
        self.f = Fake()
        self.base = "https://apps.1-2-3-4.sslip.io/"
        studio.bot.db.data["base_url"] = self.base
        getme = io.BytesIO(json.dumps({"result": {"username": "voda_test_bot"}}).encode())
        self.patches = [mock.patch.object(studio, "claude", self.f.claude), mock.patch.object(studio, "sh", self.f.sh),
                        mock.patch.object(studio, "botfather", lambda n, w: (self.f.token, None)),
                        mock.patch.object(studio, "avatar", lambda p, d: None),
                        mock.patch.object(studio.time, "sleep", lambda s: None),
                        mock.patch.object(studio.urllib.request, "urlopen", lambda *a, **k: getme)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def texts(self):
        return "\n".join(p.get("text") or "" for p in self.t.messages)

    def test_full_flow_chat(self):
        self.t.send("/start")
        self.assertIn("Студия", self.t.last)
        self.t.send("бот, который напоминает пить воду")
        self.assertEqual(studio.draft()["stage"], "ask")
        self.assertIn("Вопрос 1 из 2", self.t.last)
        self.assertEqual(self.t.buttons[:2], ["8", "10"])
        d = studio.draft()
        self.t.press(f"ans|{d['id']}|0|1")  # «10»
        self.assertIn("Вопрос 2 из 2", self.t.last)
        self.t.send("вечером в 21")  # свой вариант текстом
        self.assertEqual(studio.draft()["stage"], "name")
        self.assertIn("Как назовём", self.t.last)
        self.t.send("Водичка")
        d = studio.draft()
        self.assertEqual(d["stage"], "plan")
        self.assertEqual(d["name"], "Водичка")
        self.assertEqual([q["a"] for q in d["qs"]], ["10", "вечером в 21"])
        self.assertIn("план", self.t.last)
        self.assertIn("✅ Создать", self.t.buttons)
        # правка плана
        self.t.send("добавь статистику за неделю")
        self.assertIn("добавь статистику", self.f.prompts[-1])
        self.assertEqual(studio.draft()["stage"], "plan")
        # создать
        self.t.press("go")
        info = studio.registry()["voda"]
        self.assertEqual(info["username"], "voda_test_bot")
        self.assertEqual(studio.draft()["stage"], "done")
        self.assertIn("готов", self.t.last)
        env = read(os.path.join(info["dir"], ".env"))
        self.assertIn(f"APP_URL={self.base}b/voda/", env)
        self.assertIn(f"BOT_OWNER={OWNER}", env)
        self.assertEqual(oct(os.stat(os.path.join(info["dir"], ".env")).st_mode & 0o777), "0o600")
        self.assertTrue(os.path.exists(os.path.join(info["dir"], "botkit.py")))
        self.assertIn("Водичка", read(os.path.join(info["dir"], "bot.py")))
        self.assertTrue(any(c[1] == "start" and "bot-voda" in c for c in self.f.pm2))
        build = next(p for p in self.f.prompts if "Ты делаешь Telegram-бота" in p)
        self.assertIn("botkit.py", build)
        self.assertIn("Мини-приложение обязательно", build)

    def test_flow_from_app_and_skip(self):
        code, s = self.t.api("idea", {"text": "список покупок"})
        self.assertEqual(code, 200)
        self.assertEqual(s["draft"]["stage"], "ask")
        code, s = self.t.api("enough")
        self.assertEqual(s["draft"]["stage"], "name")
        self.assertEqual(s["draft"]["names"], ["Вода", "Глоток", "Аква"])
        self.assertEqual(self.t.api("answer", {"text": "x"})[0], 400)  # шаг уже другой
        code, s = self.t.api("name", {"text": "Покупки"})
        self.assertEqual(s["draft"]["stage"], "plan")
        self.assertEqual(s["draft"]["plan"]["username"], "voda_test")
        code, s = self.t.api("confirm")
        self.assertEqual(code, 200)
        self.assertEqual(s["draft"]["stage"], "done")
        self.assertEqual(s["draft"]["result"]["name"], "Покупки")
        self.assertEqual(len(s["bots"]), 1)
        self.assertEqual(s["bots"][0]["status"], "online")

    def test_no_secretary_asks_token(self):
        with mock.patch.object(studio, "botfather", lambda n, w: (None, "Секретарь не вошёл в аккаунт")):
            self.t.api("idea", {"text": "дневник"})
            self.t.api("enough")
            self.t.api("name", {"text": "Дневник"})
            self.t.api("confirm")
        self.assertEqual(studio.draft()["stage"], "token")
        self.assertIn("токен", self.t.last)
        self.t.send("вот: " + self.f.token)
        self.assertEqual(studio.draft()["stage"], "done")
        self.assertTrue(any(m == "deleteMessage" for m, _ in self.t.calls))  # токен стёрт из чата

    def test_one_tap_managed_bot(self):
        """Managed Bots: кнопка «⚡ Создать», подтверждение в Telegram → токен сам → запуск."""
        created = {"username": "voda_test_bot", "id": 555}
        with mock.patch.object(studio.bot, "can_manage", lambda: True), \
                mock.patch.object(studio.bot, "managed_token", lambda i: self.f.token if i == 555 else None), \
                mock.patch.object(studio, "botfather", lambda n, w: self.fail("Секретарь не нужен")):
            studio.bot.username = "studio_test_bot"
            self.t.api("idea", {"text": "вода"})
            self.t.api("enough")
            self.t.api("name", {"text": "Вода"})
            orig = studio.claude

            def build_and_tap(prompt, cwd, **k):  # пока пишется код, Глеб жмёт кнопку и подтверждает
                link = studio.draft()["newbot"]
                self.assertTrue(link.startswith("https://t.me/newbot/studio_test_bot/voda_test_bot?name="))
                self.assertEqual(self.t.api("state")[1]["draft"]["newbot"], link)
                self.assertNotIn("token", self.t.api("state")[1]["draft"])
                self.t.bot.handle({"update_id": 2, "managed_bot": {"user": {"id": OWNER}, "bot": created}})
                return orig(prompt, cwd, **k)
            with mock.patch.object(studio, "claude", build_and_tap):
                self.t.api("confirm")
        self.assertIn("⚡ Создать @voda_test_bot", "\n".join(
            b["text"] for p in self.t.messages for row in (p.get("reply_markup") or {}).get("inline_keyboard", [])
            for b in row))
        self.assertEqual(studio.draft()["stage"], "done")
        self.assertIn(f"BOT_TOKEN={self.f.token}", read(os.path.join(studio.registry()["voda"]["dir"], ".env")))

    def test_managed_bot_after_code_ready(self):
        with mock.patch.object(studio.bot, "can_manage", lambda: True), \
                mock.patch.object(studio.bot, "managed_token", lambda i: self.f.token):
            studio.bot.username = "studio_test_bot"
            self.t.api("idea", {"text": "вода"})
            self.t.api("enough")
            self.t.api("name", {"text": "Вода"})
            self.t.api("confirm")
            self.assertEqual(studio.draft()["stage"], "token")
            self.assertIn("⚡ Создать @voda_test_bot", self.t.buttons)
            self.t.bot.handle({"update_id": 3, "managed_bot": {"user": {"id": 999}, "bot": {"id": 1}}})  # чужой
            self.assertEqual(studio.draft()["stage"], "token")
            self.t.bot.handle({"update_id": 4, "managed_bot": {"user": {"id": OWNER}, "bot": {"id": 1}}})
        self.assertEqual(studio.draft()["stage"], "done")
        access = [p for m, p in self.t.calls if m == "setManagedBotAccessSettings"]
        self.assertEqual(access, [{"user_id": 1, "is_access_restricted": True}])  # чужие бота не откроют

    def test_home_tip_and_folder(self):
        with mock.patch.object(studio.bot, "can_manage", lambda: False), \
                mock.patch.object(studio, "secretary_ready", lambda: False):
            studio._one_tap["at"] = 0
            self.t.send("/start")
        self.assertIn("Bot Management Mode", self.t.last)
        with mock.patch.object(studio.bot, "can_manage", lambda: True):
            studio._one_tap["at"] = 0
            self.t.send("/start")
        self.assertNotIn("Bot Management Mode", self.t.last)
        with mock.patch.object(studio, "secretary_ready", lambda: True):
            self.t.api("idea", {"text": "вода"})
            self.t.api("enough")
            self.t.api("name", {"text": "Вода"})
            self.t.api("confirm")
        self.assertTrue(any(c[0] == "python3" and "botsfolder" in c and "voda_test_bot" in c for c in self.f.pm2_all))

    def test_text_during_build_is_queued_and_free_text_asks(self):
        self.t.api("idea", {"text": "трекер"})
        self.t.api("enough")
        self.t.api("name", {"text": "Трекер"})
        d = studio.draft()
        d["stage"] = "build"
        studio.put(d)
        self.t.send("сделай кнопки зелёными")
        self.assertEqual(studio.draft()["queued"], ["сделай кнопки зелёными"])
        studio.put({})
        studio.reg_set("x", {"name": "Икс", "slug": "x", "pm2": "bot-x", "dir": "/nonexistent", "port": 8800})
        self.t.send("что-то новое")
        self.assertIn("Новый бот", self.t.buttons[0])
        self.assertIn("✏️ Икс", self.t.buttons)

    def test_bad_interview_still_goes_to_name(self):
        with mock.patch.object(studio, "talk", lambda p: (None, "Claude не закончил работу")):
            self.t.send("какой-то бот")
        self.assertEqual(studio.draft()["stage"], "name")

    def test_fix_and_delete(self):
        self.t.api("idea", {"text": "вода"})
        self.t.api("enough")
        self.t.api("name", {"text": "Вода"})
        self.t.api("confirm")
        slug = "voda"
        path = studio.registry()[slug]["dir"]

        def edit(prompt, cwd, on_step=None, **k):
            with open(os.path.join(cwd, "README.md"), "a") as f:
                f.write("\nправка\n")
            return True, "Добавил"
        with mock.patch.object(studio, "claude", edit):
            self.t.press(f"fix|{slug}")
            self.t.send("добавь кнопку сброса")
        self.assertIn("готово", self.t.last)
        self.assertIn("↩️ Откатить", self.t.buttons)
        self.assertIn("правка", read(os.path.join(path, "README.md")))
        code, s = self.t.api("delete", {"slug": slug})
        self.assertEqual(s["bots"], [])
        self.assertFalse(os.path.exists(path))

    def test_stranger(self):
        self.t.send("/start", uid=7)
        self.assertIn("личный", self.t.last)
        self.assertEqual(self.t.api("state", uid=7)[0], 403)


class Kit(unittest.TestCase):
    def test_style_helpers(self):
        self.assertEqual(kit.clip("x" * 30), "x" * 23 + "…")
        self.assertIn("<blockquote>a</blockquote>", kit.card("🧪 <b>T</b>", ["a"]))
        self.assertIn("expandable", kit.card("t", ["x" * 800]))
        self.assertEqual(kit.plural(3, "раз", "раза", "раз"), "3 раза")
        self.assertEqual(kit.plural(11, "раз", "раза", "раз"), "11 раз")
        self.assertEqual(kit.parse_days("пн-пт"), {0, 1, 2, 3, 4})
        self.assertEqual(kit.parse_days("сб,вс"), {5, 6})
        self.assertEqual(kit.rows([1, None, 2, 3], 2), [[1, 2], [3]])

    def test_style_fallback(self):
        b = kit.Bot("T", token="1:x", data=tempfile.mkdtemp())
        calls = []

        def fake(method, params, files=None):
            calls.append(params)
            if any("style" in btn for row in params["reply_markup"]["inline_keyboard"] for btn in row):
                e = urllib.error.HTTPError("u", 400, "bad", {}, None)
                e.detail = "Bad Request: can't parse inline keyboard button"
                raise e
            return {"ok": True, "result": {"message_id": 5}}
        b._call = fake
        self.assertEqual(b.send(1, "hi", [[kit.b("a", "a", "primary")]]), 5)
        self.assertTrue(b.plain_buttons)

    def test_template_and_http(self):
        """Заготовка: тесты проходят, сервер отдаёт страницу и api с подписью Telegram; прокси Студии до неё."""
        d = tempfile.mkdtemp()
        path = os.path.join(d, "demo")
        shutil.copytree(os.path.join(HERE, "kit", "template"), path)
        shutil.copy(os.path.join(HERE, "kit", "botkit.py"), path)
        code, out = REAL_SH(sys.executable, "test_bot.py", cwd=path, timeout=120, env={**os.environ, "BOT_TOKEN": ""})
        self.assertEqual(code, 0, out)

        b = kit.Bot("Demo", token="1:abc", port=free_port(), data=os.path.join(path, "data"),
                    webapp=os.path.join(path, "webapp"))
        b.db.set("_owner", OWNER)

        @b.api("ping")
        def ping(r):
            return {"pong": r.body.get("x")}
        threading.Thread(target=b.serve, daemon=True).start()
        base = f"http://127.0.0.1:{b.port}"
        wait_up(base)
        html = urllib.request.urlopen(base + "/").read().decode()
        self.assertIn("kit.css?v=", html)
        self.assertNotIn("{{v}}", html)
        self.assertIn(".tabs", urllib.request.urlopen(base + "/kit.css").read().decode())
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + "/../botkit.py")
        self.assertEqual(e.exception.code, 404)
        req = urllib.request.Request(base + "/api/ping", data=b'{"x": 1}', headers={
            "X-Init-Data": signed("1:abc", {"id": OWNER}), "Content-Type": "application/json"})
        self.assertEqual(json.load(urllib.request.urlopen(req)), {"pong": 1})
        bad = urllib.request.Request(base + "/api/ping", headers={"X-Init-Data": signed("1:other", {"id": OWNER})})
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(bad)
        self.assertEqual(e.exception.code, 401)

        # прокси Студии: /b/demo/… → этот бот
        studio.reg_set("demo", {"name": "Demo", "slug": "demo", "port": b.port, "dir": path, "pm2": "bot-demo"})
        sport = free_port()
        studio.bot.port = sport
        threading.Thread(target=studio.bot.serve, daemon=True).start()
        sbase = f"http://127.0.0.1:{sport}"
        wait_up(sbase)
        self.assertIn("kit.css", urllib.request.urlopen(sbase + "/b/demo/").read().decode())
        req = urllib.request.Request(sbase + "/b/demo/api/ping", data=b'{"x": 2}', headers={
            "X-Init-Data": signed("1:abc", {"id": OWNER}), "Content-Type": "application/json"})
        self.assertEqual(json.load(urllib.request.urlopen(req)), {"pong": 2})
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(sbase + "/b/nope/")
        self.assertEqual(e.exception.code, 404)
        studio.reg_set("demo", None)

    def test_avatar(self):
        out = os.path.join(tempfile.mkdtemp(), "a.png")
        code, msg = REAL_SH(sys.executable, os.path.join(HERE, "avatar.py"), out, "В", "#0ea5e9", "#6366f1", timeout=120)
        self.assertEqual(code, 0, msg)
        with open(out, "rb") as f:
            self.assertEqual(f.read(4), b"\x89PNG")


def read(path):
    with open(path) as f:
        return f.read()


def free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def wait_up(base):
    for _ in range(50):
        try:
            urllib.request.urlopen(base + "/health")
            return
        except Exception:
            time.sleep(0.05)


if __name__ == "__main__":
    import urllib.error  # noqa: F401
    unittest.main()
