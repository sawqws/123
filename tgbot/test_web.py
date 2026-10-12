"""Мини-приложение Секретаря на заглушках: tgbot/.venv/bin/python tgbot/test_web.py

Подпись initData (верная, чужой, просроченная, битая), раздача страниц, API состояния, поручения (тот же путь,
что в чате: живой статус, «ждёт подтверждения», ответ в чат и в историю), расписание, память.
"""
import asyncio
import contextlib
import datetime
import json
import os
import sys
import tempfile
import time
import types as pytypes
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from telethon.tl import types  # noqa: E402

import actions  # noqa: E402
import agent  # noqa: E402
import botapi  # noqa: E402
import jobs  # noqa: E402
import ui  # noqa: E402
import web  # noqa: E402

TOKEN = "123456:SECRET"
OWNER = 777
UTC = datetime.timezone.utc


class InitData(unittest.TestCase):
    def test_valid(self):
        init = web.sign_init_data(TOKEN, {"id": OWNER, "first_name": "Глеб"})
        self.assertEqual(web.check_init_data(init, TOKEN)["id"], OWNER)

    def test_with_signature_field(self):  # новые клиенты добавляют signature — подпись считается и с ним
        init = web.sign_init_data(TOKEN, {"id": OWNER}, signature="abc")
        self.assertEqual(web.check_init_data(init, TOKEN)["id"], OWNER)

    def test_expired(self):
        old = web.sign_init_data(TOKEN, {"id": OWNER}, auth_date=time.time() - 2 * 86400)
        self.assertIsNone(web.check_init_data(old, TOKEN))
        fresh = web.sign_init_data(TOKEN, {"id": OWNER}, auth_date=time.time() - 3600)
        self.assertIsNotNone(web.check_init_data(fresh, TOKEN))

    def test_broken(self):
        init = web.sign_init_data(TOKEN, {"id": OWNER})
        self.assertIsNone(web.check_init_data(init, "999:OTHER"))  # подпись другим ботом
        tampered = init.replace("777", "778")
        self.assertIsNone(web.check_init_data(tampered, TOKEN))
        no_hash = "&".join(p for p in init.split("&") if not p.startswith("hash="))
        self.assertIsNone(web.check_init_data(no_hash, TOKEN))
        for junk in ("", "мусор", "hash=", "a=1&&&", None):
            self.assertIsNone(web.check_init_data(junk, TOKEN))

    def test_user_must_have_id(self):
        init = web.sign_init_data(TOKEN, {"first_name": "без id"})
        self.assertIsNone(web.check_init_data(init, TOKEN))


# ---------- агент с заглушками ----------

class FakeMsg:
    def __init__(self, bot, text):
        self.bot, self.text = bot, text

    async def edit(self, text=None, buttons=None, parse_mode="html"):
        self.bot.edits.append(text)

    async def delete(self):
        self.bot.deleted += 1


class FakeBot:
    def __init__(self, reject_html=0):
        self.sent, self.edits, self.deleted, self.calls, self.reject_html = [], [], 0, [], reject_html

    async def send_message(self, chat, text, buttons=None, parse_mode="html", link_preview=False):
        if parse_mode == "html" and self.reject_html:
            self.reject_html -= 1
            raise botapi.BotError("Bad Request: can't parse entities: unclosed tag")
        self.sent.append({"text": text, "buttons": buttons, "parse_mode": parse_mode})
        return FakeMsg(self, text)

    async def call(self, method, **kw):
        self.calls.append((method, kw))

    @contextlib.asynccontextmanager
    async def action(self, chat, kind="typing"):
        yield


def dialog(entity, unread, mentions=0, mins=0, text=""):
    from telethon.utils import get_peer_id
    return pytypes.SimpleNamespace(
        entity=entity, name=actions.name(entity), id=get_peer_id(entity), unread_count=unread,
        unread_mentions_count=mentions, archived=False, is_user=isinstance(entity, types.User),
        date=datetime.datetime.now(UTC) - datetime.timedelta(minutes=mins),
        message=pytypes.SimpleNamespace(message=text, out=False, action=None, media=None))


class FakeClient:
    def __init__(self):
        petya = types.User(id=7, first_name="Петя", access_hash=1)
        klass = types.Channel(id=100, title="8Б класс", photo=types.ChatPhotoEmpty(), date=None, megagroup=True, access_hash=1)
        read = types.User(id=9, first_name="Прочитанный", access_hash=1)
        self.dialogs = [dialog(petya, 2, 0, 1, "привет\nкак дела"), dialog(klass, 30, 1, 50, "контрольная"), dialog(read, 0)]

    def is_connected(self):
        return True

    async def connect(self):
        pass

    async def is_user_authorized(self):
        return True

    async def get_me(self):
        return types.User(id=OWNER, first_name="Глеб", last_name="Х", username="gleb", access_hash=1)

    async def get_dialogs(self, limit=None):
        return self.dialogs


class Api(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        for name, val in (("TMP", self.tmp), ("WORK", self.tmp), ("SETTINGS", os.path.join(self.tmp, "settings.json")),
                          ("MEMORY", os.path.join(self.tmp, "memory.md"))):
            p = mock.patch.object(agent, name, val)
            p.start()
            self.addCleanup(p.stop)
        actions._dialogs.clear()

    def make(self, client=True):
        a = agent.Agent(TOKEN, allowed=OWNER)
        a.bot = FakeBot()
        if client:
            a.client = FakeClient()
        return a

    def go(self, a, scenario):
        async def main():
            w = web.Web(a)
            await w.start("127.0.0.1", 0)
            self.base = f"http://127.0.0.1:{w.port}"
            try:
                await scenario()
            finally:
                w.server.close()
                await w.server.wait_closed()
        asyncio.run(asyncio.wait_for(main(), 20))

    async def req(self, path, body=None, user=OWNER, method="POST", init=None):
        if init is None:
            init = web.sign_init_data(TOKEN, {"id": user, "first_name": "x"}) if user else ""

        def do():
            data = json.dumps(body or {}).encode() if method == "POST" else None
            r = urllib.request.Request(self.base + path, data=data, method=method,
                                       headers={"X-Init-Data": init, "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(r, timeout=10) as resp:
                    return resp.status, resp.read(), dict(resp.headers)
            except urllib.error.HTTPError as e:
                return e.code, e.read(), dict(e.headers)
        code, raw, headers = await asyncio.to_thread(do)
        if "json" in headers.get("Content-Type", ""):
            return code, json.loads(raw)
        return code, raw, headers

    async def until(self, cond, timeout=5):
        for _ in range(int(timeout / 0.02)):
            if cond():
                return
            await asyncio.sleep(0.02)
        self.fail("не дождался")

    # ---------- доступ и страницы ----------

    def test_access_only_owner(self):
        a = self.make()

        async def scenario():
            self.assertEqual((await self.req("/api/state", user=None))[0], 401)
            code, body = await self.req("/api/state", user=12345)
            self.assertEqual((code, body["error"]), (403, "Это личный секретарь"))
            old = web.sign_init_data(TOKEN, {"id": OWNER}, auth_date=time.time() - 3 * 86400)
            self.assertEqual((await self.req("/api/state", init=old))[0], 401)
            bad = web.sign_init_data("1:OTHER", {"id": OWNER})
            self.assertEqual((await self.req("/api/state", init=bad))[0], 401)
            self.assertEqual((await self.req("/api/state"))[0], 200)
            self.assertEqual((await self.req("/api/nope"))[0], 404)
        self.go(a, scenario)

    def test_no_owner_yet(self):
        a = self.make()
        a.allowed = 0

        async def scenario():
            self.assertEqual((await self.req("/api/state"))[0], 403)  # пока никто не нажал /start — никого не пускаем
        self.go(a, scenario)

    def test_pages(self):
        a = self.make()

        async def scenario():
            code, raw, headers = await self.req("/", method="GET", user=None)
            self.assertEqual(code, 200)
            self.assertIn(b"telegram-web-app.js", raw)
            self.assertNotIn(b"{{v}}", raw)
            self.assertIn("script-src 'self' https://telegram.org", headers["Content-Security-Policy"])
            code, raw, headers = await self.req("/app.js", method="GET", user=None)
            self.assertEqual((code, headers["Content-Type"].split(";")[0]), (200, "text/javascript"))
            self.assertEqual((await self.req("/health", method="GET", user=None))[1], b"ok")
            for bad in ("/../agent.py", "/agent.py", "/webapp/app.js", "/%2e%2e/agent.py"):
                self.assertEqual((await self.req(bad, method="GET", user=None))[0], 404, bad)
        self.go(a, scenario)

    # ---------- состояние ----------

    def test_state(self):
        a = self.make()
        st = jobs.Store(os.path.join(self.tmp, "jobs.json"))
        st.add(st.new([{"id": 7, "name": "Мама"}], "07:00", days="будни", text="доброе утро"))
        a.history = [{"id": "h1", "q": "вопрос", "state": "done", "html": "<b>ответ</b>", "source": "chat"}]

        async def scenario():
            code, s = await self.req("/api/state", {"fresh": True})
            self.assertEqual(code, 200)
            self.assertEqual(s["me"], {"name": "Глеб Х", "first": "Глеб", "username": "gleb"})
            self.assertEqual([c["name"] for c in s["chats"]], ["8Б класс", "Петя"])  # с упоминанием — первым
            self.assertEqual((s["unread"], s["mentions"]), (2, 1))
            petya = s["chats"][1]
            self.assertEqual((petya["initials"], petya["kind"], petya["preview"]), ("П", "личка", "привет как дела"))
            self.assertEqual(s["jobs"][0]["period"], "по будням")
            self.assertEqual(s["jobs"][0]["to"], "Мама")
            self.assertEqual(s["history"][0]["q"], "вопрос")
            self.assertIsNone(s["run"])
            self.assertTrue(s["examples"])
        self.go(a, scenario)

    def test_state_without_account(self):
        a = self.make(client=False)

        async def scenario():
            code, s = await self.req("/api/state")
            self.assertEqual((code, s["me"], s["chats"]), (200, None, []))
            self.assertEqual(s["first"], "x")  # имя — из initData
        self.go(a, scenario)

    # ---------- поручения ----------

    def test_ask_runs_like_chat_and_lands_in_both(self):
        a = self.make()
        gate = asyncio.Event()
        seen = []

        async def fake_claude(text, resume=True, on_step=None):
            seen.append(text)
            on_step("🗂 Листаю список чатов")
            await gate.wait()
            return True, "**Готово**: написал Пете"
        a.run_claude = fake_claude

        async def scenario():
            code, r = await self.req("/api/ask", {"text": "  ответь Пете  "})
            self.assertEqual((code, r["run"]["state"], r["run"]["source"]), (200, "work", "app"))
            await self.until(lambda: seen)
            code, busy = await self.req("/api/ask", {"preset": "missed"})
            self.assertEqual((code, busy["error"]), (409, "Ещё занимаюсь прошлым поручением"))
            # действие ждёт «Да/Нет» — в чате; приложение это показывает
            confirm = asyncio.create_task(a.confirm("📤 Написать в «Петя»:\n\nбуду в 6"))
            await self.until(lambda: a.waiting)
            _, r = await self.req("/api/run")
            self.assertIn("Петя", r["run"]["confirm"])
            self.assertEqual(r["run"]["steps"], ["🗂 Листаю список чатов"])
            next(iter(a.waiting.values())).set_result("ok")
            await confirm
            _, r = await self.req("/api/run")
            self.assertEqual(r["run"]["confirm"], "")
            gate.set()
            await self.until(lambda: a.run["state"] == "done")
            _, r = await self.req("/api/run", {"history": True})
            self.assertIn("<b>Готово</b>", r["run"]["html"])
            self.assertEqual(r["history"][0]["source"], "app")
            self.assertEqual(r["history"][0]["q"], "ответь Пете")
            self.assertFalse(a.busy)
        self.go(a, scenario)
        self.assertEqual(seen, ["ответь Пете"])
        texts = [m["text"] for m in a.bot.sent]
        self.assertTrue(any("Работаю" in t for t in texts))  # живой статус — и в чате
        self.assertTrue(any("<b>Готово</b>: написал Пете" in t for t in texts))  # ответ продублирован в чат
        with open(a.history_path()) as f:
            self.assertEqual(json.load(f)[0]["source"], "app")

    def test_presets(self):
        a = self.make()
        w = web.Web(a)
        self.assertEqual(w.task({"preset": "missed"}), (agent.ASK_MISSED, agent.BTN_MISSED))
        self.assertEqual(w.task({"preset": "sort"}), (agent.ASK_SORT, agent.BTN_SORT))
        text, title = w.task({"preset": "psy", "chat": "8Б класс", "id": -1000000000100})
        self.assertIn("«8Б класс» (id -1000000000100)", text)
        self.assertEqual(title, "🧠 Разбор: 8Б класс")
        text, title = w.task({"preset": "brief", "chat": "Петя", "id": 7})
        self.assertIn("«Петя» (id 7)", text)
        for bad in ({"preset": "psy"}, {"text": "   "}, {"text": "x" * 5000}):
            with self.assertRaises(web.ApiError):
                w.task(bad)

    def test_stop_from_app(self):
        a = self.make()
        gate = asyncio.Event()

        async def fake_claude(text, resume=True, on_step=None):
            await gate.wait()
            return False, agent.card("⏹ Остановил", "Что успел сделать — сделано.")
        a.run_claude = fake_claude

        async def scenario():
            await self.req("/api/ask", {"text": "долгое"})
            await self.until(lambda: a.busy and a.bot.sent)
            await self.req("/api/stop")
            gate.set()
            await self.until(lambda: a.run["state"] != "work")
            self.assertEqual(a.run["state"], "stopped")
            self.assertEqual(a.history[-1]["state"], "stopped")
        self.go(a, scenario)

    # ---------- расписание и память ----------

    def test_jobs_delete(self):
        a = self.make()
        st = jobs.Store(os.path.join(self.tmp, "jobs.json"))
        j = st.new([{"id": 7, "name": "Петя"}], "21:00", once="2099-01-01", ai="поздравить")
        st.add(j)

        async def scenario():
            _, s = await self.req("/api/state")
            self.assertEqual(s["jobs"][0]["period"], "один раз 01.01")
            self.assertEqual(s["jobs"][0]["ai"], "поздравить")
            code, r = await self.req("/api/jobs/delete", {"id": j["id"]})
            self.assertEqual((code, r["jobs"]), (200, []))
            self.assertEqual((await self.req("/api/jobs/delete", {"id": j["id"]}))[0], 404)
        self.go(a, scenario)
        self.assertEqual(a.jobs.load(), [])

    def test_memory(self):
        a = self.make()

        async def scenario():
            self.assertEqual((await self.req("/api/memory"))[1]["text"], "")
            code, r = await self.req("/api/memory/save", {"text": "— бокс по вт\r\n— маму зовут Ольга\n\n"})
            self.assertEqual((code, r["text"]), (200, "— бокс по вт\n— маму зовут Ольга\n"))
            self.assertEqual((await self.req("/api/memory/save", {"text": "x" * 20001}))[0], 400)
            self.assertEqual((await self.req("/api/memory"))[1]["text"], "— бокс по вт\n— маму зовут Ольга\n")
        self.go(a, scenario)
        self.assertIn("маму зовут Ольга", a.memory_card())


# ---------- чат: кнопка приложения, отказ разметки, нарезка длинных ответов ----------

class Chat(unittest.TestCase):
    def test_web_app_button_markup(self):
        m = botapi.markup([[botapi.Button.web_app("📱 Приложение", "https://agent.1-2-3-4.sslip.io/")]])
        self.assertEqual(m, {"inline_keyboard": [[{"text": "📱 Приложение", "web_app": {"url": "https://agent.1-2-3-4.sslip.io/"}}]]})

    def test_app_button_without_url_explains(self):
        a = agent.Agent.__new__(agent.Agent)
        self.assertEqual(a.app_button()["callback_data"], "m:app")
        text, buttons = a.app_card()
        self.assertIn("https-адрес", text)
        self.assertIsNone(buttons)
        a.app_url = "https://agent.1-2-3-4.sslip.io"
        self.assertEqual(a.app_button()["web_app"]["url"], "https://agent.1-2-3-4.sslip.io/")

    def test_say_falls_back_to_plain_text(self):
        a = agent.Agent.__new__(agent.Agent)
        a.allowed, a.bot = 1, FakeBot(reject_html=1)
        asyncio.run(a.say("<b>Ответ</b> &lt;3"))
        self.assertEqual(a.bot.sent, [{"text": "Ответ <3", "buttons": None, "parse_mode": None}])

    def test_button_texts_short(self):
        """Текст кнопок — до ~24 символов, иначе Telegram обрезает его на телефоне."""
        a = agent.Agent.__new__(agent.Agent)
        rows = [r for r in agent.MENU] + agent.keypad() + [[a.app_button()]]
        for r in rows:
            for b in r:
                self.assertLessEqual(len(b["text"]), 24, b["text"])
        with open(agent.__file__, encoding="utf-8") as f:
            import re
            for t in re.findall(r'Button\.(?:inline|text|web_app|request_phone)\(\s*"([^"]+)"', f.read()):
                self.assertLessEqual(len(t), 24, t)


class Split(unittest.TestCase):
    def test_paragraphs_kept_and_quote_balanced(self):
        report = "<b>⏰ По расписанию</b>\n<blockquote>" + "\n\n".join(f"✓ <b>Чат {i}</b>\nтекст {i}" for i in range(400)) + "</blockquote>"
        parts = ui.split_html(report)
        self.assertGreater(len(parts), 1)
        for p in parts:
            self.assertLessEqual(len(p), 4096)
            self.assertEqual(ui.open_stack(p), [], p[-60:])
        self.assertIn("текст 0\n\n✓ <b>Чат 1</b>", parts[0])  # пустые строки между абзацами не теряются
        self.assertTrue(parts[1].startswith("<blockquote>"))

    def test_long_line_cut_outside_tags_and_entities(self):
        line = " ".join(f'<a href="https://x.ru/{i}">ссылка&amp;{i}</a>' for i in range(400))
        parts = ui.split_html(line)
        self.assertGreater(len(parts), 1)
        for p in parts:
            self.assertEqual(ui.open_stack(p), [])
            self.assertNotRegex(p, r"&[a-z]*$")
            self.assertNotRegex(p, r"<[^>]*$")

    def test_link_with_underscores_and_quotes(self):
        self.assertEqual(ui.md("[x](https://a.ru/_b_/c) https://t.me/_bot_"),
                         '<a href="https://a.ru/_b_/c">x</a> https://t.me/_bot_')
        self.assertNotIn('<a', ui.md('[x](https://a.ru/"onclick=alert(1))'))  # кавычка в адресе — не ссылка, просто текст
        self.assertIn('href="https://a.ru/?a=1&amp;b=2"', ui.md("[x](https://a.ru/?a=1&b=2)"))

    def test_jobs_card_no_space_alignment(self):
        a = agent.Agent.__new__(agent.Agent)
        a.jobs = jobs.Store(os.path.join(tempfile.mkdtemp(), "j.json"))
        for i in range(15):
            a.jobs.add(a.jobs.new([{"id": 7, "name": "Петя"}], "07:00", text="очень длинный текст " * 30))
        text, buttons = a.jobs_card()
        self.assertNotIn("\n ", text)  # колонки пробелами не выравниваем — шрифт пропорциональный
        self.assertLess(len(text), 4096)
        self.assertIn("…и ещё 3", text)
        self.assertEqual(sum(len(r) for r in buttons), 12)


if __name__ == "__main__":
    unittest.main()
