"""Агент целиком против имитации Bot API (HTTP): tgbot/.venv/bin/python tgbot/test_botapi.py

Проверяет то, что раньше держалось на Telethon: приветствие с картинкой, меню и кнопки, правка карточек,
откат цветных кнопок, если Telegram их не примет, и вход в аккаунт без ключей (ключи бот получает сам).
"""
import asyncio
import http.server
import json
import os
import sys
import tempfile
import threading
import types as pytypes
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent  # noqa: E402
import botapi  # noqa: E402
import get_api  # noqa: E402

OWNER = 777


class FakeTelegram:
    """Bot API: пишет все вызовы, отдаёт обновления из очереди. reject_style — как будто цвета ещё не поддерживаются."""

    def __init__(self, reject_style=False):
        self.calls, self.updates, self.reject_style, self.mid = [], [], reject_style, 100
        fake = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                method = self.path.rsplit("/", 1)[-1]
                if "multipart" in (self.headers.get("Content-Type") or ""):
                    params = {"_multipart": body.decode("utf-8", "replace")}
                else:
                    params = json.loads(body or b"{}")
                ok, result = fake.answer(method, params)
                data = json.dumps({"ok": ok, "result": result} if ok else {"ok": False, "description": result}).encode()
                self.send_response(200 if ok else 400)
                self.end_headers()
                self.wfile.write(data)

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_port}"

    def answer(self, method, p):
        if method == "getUpdates":
            ups, self.updates = self.updates, []
            if not ups:
                import time
                time.sleep(0.05)
            return True, ups
        if self.reject_style and "style" in json.dumps(p):
            self.calls.append(("REJECTED", method, p))
            return False, "Bad Request: can't parse inline keyboard button: unknown field \"style\""
        self.calls.append((method, p))
        if method == "getMe":
            return True, {"id": 1, "is_bot": True, "username": "secretary_bot"}
        if method in ("sendMessage", "sendPhoto"):
            self.mid += 1
            return True, {"message_id": self.mid, "chat": {"id": OWNER}}
        return True, True

    def push_text(self, text, contact=None):
        m = {"message_id": 1, "chat": {"id": OWNER, "type": "private"}, "from": {"id": OWNER}, "text": text}
        if contact:
            m.pop("text")
            m["contact"] = {"phone_number": contact}
        self.updates.append({"update_id": len(self.calls) + 1000 + len(self.updates), "message": m})

    def push_button(self, data, mid):
        self.updates.append({"update_id": 5000 + len(self.calls), "callback_query": {
            "id": "q1", "from": {"id": OWNER}, "data": data,
            "message": {"message_id": mid, "chat": {"id": OWNER}, "text": "карточка"}}})

    def sent(self, method):
        return [p for m, p in ((c[0], c[1]) for c in self.calls if c[0] != "REJECTED") if m == method]


class AgentOverBotApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        for name in ("TMP", "WORK"):
            p = mock.patch.object(agent, name, self.tmp)
            p.start()
            self.addCleanup(p.stop)
        for name, val in (("SETTINGS", os.path.join(self.tmp, "settings.json")), ("SOCK", os.path.join(self.tmp, "a.sock")),
                          ("MEMORY", os.path.join(self.tmp, "memory.md")), ("PORT", 0)):
            p = mock.patch.object(agent, name, val)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(agent.Agent, "app_link", mock.AsyncMock())  # без интернета: адрес приложения не ищем
        p.start()
        self.addCleanup(p.stop)

    def run_agent(self, tg, scenario):
        async def go():
            a = agent.Agent("123:abc", allowed=OWNER)
            a.bot = botapi.Bot("123:abc", base=tg.base)
            a.check_card = mock.AsyncMock(return_value="ok")
            task = asyncio.create_task(a.main())
            try:
                await scenario(a)
            finally:
                task.cancel()
        asyncio.run(asyncio.wait_for(go(), 30))

    async def until(self, cond, timeout=5):
        for _ in range(int(timeout / 0.05)):
            if cond():
                return
            await asyncio.sleep(0.05)
        self.fail("не дождался")

    async def choose(self, tg, data):
        """Без ключей /login сначала спрашивает способ входа."""
        await self.until(lambda: any("Как подключиться" in p["text"] for p in tg.sent("sendMessage")))
        tg.push_button(data, tg.mid)
        await self.until(lambda: any("request_contact" in json.dumps(p) for p in tg.sent("sendMessage")))

    def test_login_as_desktop(self):
        tg = FakeTelegram()
        made = []
        client = mock.Mock()
        client.is_connected.return_value = True
        client.is_user_authorized = mock.AsyncMock(return_value=False)
        client.send_code_request = mock.AsyncMock(return_value=pytypes.SimpleNamespace(
            phone_code_hash="h", type=pytypes.SimpleNamespace(length=5)))

        async def scenario(a):
            tg.push_text("/login")
            await self.until(lambda: any("Как подключиться" in p["text"] for p in tg.sent("sendMessage")))
            tg.push_button("login:desktop", tg.mid)
            await self.until(lambda: any("против правил" in p["text"] for p in tg.sent("editMessageText")))
            self.assertEqual(made, [])  # до «Да, войти» ничего не меняем
            tg.push_button("login:desktop!", tg.mid)
            await self.until(lambda: any("request_contact" in json.dumps(p) for p in tg.sent("sendMessage")))
            self.assertEqual(made, [(2040, "b18441a1ff607e10a989891a5462e627")])
            with open(agent.SETTINGS) as f:
                self.assertEqual(json.load(f)["api_id"], "2040")
            tg.push_text("", contact="79138434865")
            await self.until(lambda: client.send_code_request.await_count)
            client.send_code_request.assert_awaited_with("+79138434865")  # my.telegram.org не трогаем

        def fake_make(i, h):
            made.append((i, h))
            return client
        with mock.patch.object(agent.Agent, "make_client", staticmethod(fake_make)), \
                mock.patch.object(get_api, "Site", side_effect=AssertionError("my.telegram.org не нужен")):
            self.run_agent(tg, scenario)

    def test_broken_session_is_reset(self):
        """Сохранённые ключи есть, но сессия испорчена (AUTH_KEY_*) — /login даёт выбор, сессия сбрасывается, код уходит."""
        from telethon import errors
        tg = FakeTelegram()
        with open(agent.SETTINGS, "w") as f:
            json.dump({"api_id": "555", "api_hash": "h" * 32}, f)
        open(os.path.join(self.tmp, "account.session"), "w").close()
        broken, fresh, made = mock.Mock(), mock.Mock(), []
        for c in (broken, fresh):
            c.is_connected.return_value = True
            c.connect, c.disconnect = mock.AsyncMock(), mock.AsyncMock()
            c.is_user_authorized = mock.AsyncMock(return_value=False)
            c.api_id, c.api_hash = 555, "h" * 32
        broken.send_code_request = mock.AsyncMock(side_effect=errors.AuthKeyUnregisteredError(request=None))
        fresh.send_code_request = mock.AsyncMock(return_value=pytypes.SimpleNamespace(
            phone_code_hash="h", type=pytypes.SimpleNamespace(length=5)))

        def fake_make(i, h):
            made.append(int(i))
            return broken if len(made) == 1 else fresh

        async def scenario(a):
            tg.push_text("/login")
            await self.choose(tg, "login:own")
            tg.push_text("", contact="79138434865")
            await self.until(lambda: fresh.send_code_request.await_count)
            self.assertFalse(os.path.exists(os.path.join(self.tmp, "account.session")))
            broken.session.delete.assert_called_once()
            await self.until(lambda: any("Наберите его кнопками" in p["text"] for p in tg.sent("sendMessage")))

        with mock.patch.object(agent.Agent, "make_client", staticmethod(fake_make)):
            self.run_agent(tg, scenario)
        self.assertEqual(made, [555, 555])

    def test_desktop_client_looks_like_desktop(self):
        c = agent.Agent.make_client(2040, "b18441a1ff607e10a989891a5462e627")
        r = c._init_request
        self.assertEqual((r.api_id, r.device_model, r.lang_pack), (2040, "Desktop", "tdesktop"))
        own = agent.Agent.make_client(123, "x" * 32)
        self.assertEqual(own._init_request.device_model, "HDP Agent")

    def test_start_menu_and_buttons(self):
        tg = FakeTelegram()

        async def scenario(a):
            await self.until(lambda: tg.sent("setMyCommands"))
            tg.push_text("/start")
            await self.until(lambda: tg.sent("sendPhoto"))
            photo = tg.sent("sendPhoto")[0]["_multipart"]
            self.assertIn("Здравствуйте", photo)
            self.assertIn("login:", photo)  # аккаунт не подключён — кнопка входа
            tg.push_text("/menu")
            await self.until(lambda: any("Аккаунт не подключён" in p["text"] for p in tg.sent("sendMessage")))
            card = [p for p in tg.sent("sendMessage") if "Аккаунт не подключён" in p["text"]][0]
            self.assertEqual(card["parse_mode"], "html")
            self.assertEqual(card["reply_markup"]["inline_keyboard"][0][0]["callback_data"], "login:")
            tg.push_button("m:help", tg.mid)
            await self.until(lambda: tg.sent("editMessageText"))
            self.assertIn("Чем я могу помочь", tg.sent("editMessageText")[0]["text"])
            self.assertTrue(tg.sent("answerCallbackQuery"))
        self.run_agent(tg, scenario)

    def test_every_button_is_answered(self):
        """Устаревшая кнопка, упавший обработчик, чужой человек — крутилка на кнопке не висит, есть понятный ответ."""
        tg = FakeTelegram()

        async def scenario(a):
            await self.until(lambda: tg.sent("setMyCommands"))
            tg.push_button("zz:старое", tg.mid)  # кнопка из прошлой версии
            await self.until(lambda: tg.sent("answerCallbackQuery"))
            self.assertIn("устарела", tg.sent("answerCallbackQuery")[-1]["text"])
            n = len(tg.sent("answerCallbackQuery"))
            with mock.patch.object(a, "dashboard", mock.AsyncMock(side_effect=RuntimeError("Telegram не ответил"))):
                tg.push_button("m:home", tg.mid)
                await self.until(lambda: len(tg.sent("answerCallbackQuery")) > n)
            ans = tg.sent("answerCallbackQuery")[-1]
            self.assertIn("⚠️", ans["text"])
            self.assertIn("Telegram не ответил", ans["text"])
            n = len(tg.sent("answerCallbackQuery"))
            tg.updates.append({"update_id": 9001, "callback_query": {"id": "q2", "from": {"id": 5}, "data": "m:home",
                                                                     "message": {"message_id": 1, "chat": {"id": 5}}}})
            await self.until(lambda: len(tg.sent("answerCallbackQuery")) > n)
            self.assertIn("личный", tg.sent("answerCallbackQuery")[-1]["text"])
            n = len(tg.sent("answerCallbackQuery"))
            tg.push_button("j:rm:нет-такого", tg.mid)  # расписание уже удалено в приложении
            await self.until(lambda: len(tg.sent("answerCallbackQuery")) > n)
            self.assertIn("уже нет", tg.sent("answerCallbackQuery")[-1]["text"])
        self.run_agent(tg, scenario)

    def test_guard_long_text_and_files(self):
        """Подтверждение показывает то, что уйдёт: длинный текст и файлы вне рабочей папки — отказ до карточки."""
        a = agent.Agent
        with self.assertRaises(agent.actions.Fail):
            a.guard("send", {"chat": "x", "text": "я" * 3001})
        self.assertEqual(a.guard("send", {"chat": "x", "text": "я" * 3000})["text"], "я" * 3000)
        for bad in ("/etc/passwd", os.path.join(agent.TMP, "account.session"), os.path.join(agent.TMP, "settings.json"),
                    "/tmp/x/../../etc/hostname"):
            with self.assertRaises(agent.actions.Fail, msg=bad):
                a.guard("send", {"chat": "x", "file": bad})
        ok = os.path.join(self.tmp, "ok.png")  # TMP/WORK в тестах — временная папка
        open(ok, "w").close()
        with mock.patch.object(agent, "WORK", self.tmp):
            self.assertEqual(a.guard("send", {"chat": "x", "file": ok})["file"], os.path.realpath(ok))

    def test_claude_cannot_read_secrets(self):
        """Claude Секретаря получает запрет читать сессию аккаунта, настройки и ключи сервера."""
        deny = agent.Agent.secret_reads()
        t = "/" + agent.TMP
        for need in (f"Read({t}/account*)", f"Read({t}/settings.json)", "Read(//etc/**)", "Read(**/.env)"):
            self.assertIn(need, deny)
        self.assertTrue(all(d.startswith("Read(") and d.endswith(")") for d in deny))

    def test_auto_from_claude_is_not_trusted(self):
        """--auto без подтверждения — только для Главного и Ловца: запрос от Claude Секретаря превращается в обычный."""
        tg = FakeTelegram()
        seen = []

        async def scenario(a):
            a.authorized = mock.AsyncMock(return_value=True)

            async def fake_execute(cmd, args):
                seen.append(args)
                return True, "ok"
            a.execute = fake_execute

            async def call(req):
                r, w = await asyncio.open_unix_connection(agent.SOCK)
                w.write(json.dumps(req).encode() + b"\n")
                await w.drain()
                out = json.loads(await r.readline())
                w.close()
                return out
            await self.until(lambda: os.path.exists(agent.SOCK))
            await call({"cmd": "newbot", "args": {"auto": True}, "caller": "claude"})
            await call({"cmd": "newbot", "args": {"auto": True}})
        self.run_agent(tg, scenario)
        self.assertFalse(seen[0]["auto"])  # от Claude — спросит Глеба
        self.assertTrue(seen[1]["auto"])   # от Главного/Ловца (без метки) — как раньше

    def test_jobs_buttons_three_in_row(self):
        a = agent.Agent.__new__(agent.Agent)
        a.jobs = mock.Mock(load=lambda: [{"id": str(i), "next": "2026-10-15T07:00:00+03:00", "chats": [{"id": 1, "name": "Мама"}],
                                          "time": "07:00", "days": [], "text": "привет", "sent": 0} for i in range(7)])
        with mock.patch.object(agent.jobs, "describe", lambda j: "07:00 → Мама"):
            _, rows = a.jobs_card()
        self.assertTrue(all(len(r) <= 3 for r in rows))
        self.assertEqual(sum(len(r) for r in rows), 7)

    def test_colors_fall_back_when_rejected(self):
        tg = FakeTelegram(reject_style=True)

        async def scenario(a):
            await a.bot.send_message(OWNER, "x", buttons=[[botapi.Button.inline("Да", "ok:1", style="success")]])
            rejected = [c for c in tg.calls if c[0] == "REJECTED"]
            self.assertTrue(rejected)
            sent = tg.sent("sendMessage")[-1]
            self.assertEqual(sent["reply_markup"], {"inline_keyboard": [[{"text": "Да", "callback_data": "ok:1"}]]})
        self.run_agent(tg, scenario)

    def test_confirm_card_gets_mark(self):
        tg = FakeTelegram()

        async def scenario(a):
            confirm = asyncio.create_task(a.confirm("📤 Написать в «Петя»:\n\nбуду в 6"))
            await self.until(lambda: a.waiting)
            mid = tg.mid
            tg.push_button(f"ok:{next(iter(a.waiting))}", mid)
            await confirm
            await self.until(lambda: tg.sent("editMessageText"))
            edited = tg.sent("editMessageText")[-1]
            self.assertIn("<b>📤 Написать в «Петя»</b>", edited["text"])  # исходный HTML, не плоский текст
            self.assertIn("✓ подтверждено", edited["text"])
            self.assertEqual(edited["reply_markup"], {"inline_keyboard": []})
        self.run_agent(tg, scenario)

    def test_login_gets_keys_then_asks_telegram_code(self):
        tg = FakeTelegram()
        site = mock.Mock()
        site.send_code.return_value = "rh"
        site.keys.side_effect = [(None, "form"), ("1234567", "0123456789abcdef0123456789abcdef")]
        client = mock.Mock()
        client.is_connected.return_value = True
        client.connect = mock.AsyncMock()
        client.is_user_authorized = mock.AsyncMock(return_value=False)
        client.send_code_request = mock.AsyncMock(return_value=pytypes.SimpleNamespace(
            phone_code_hash="h", type=pytypes.SimpleNamespace(length=5)))

        async def scenario(a):
            tg.push_text("/login")
            await self.choose(tg, "login:own")
            tg.push_text("", contact="79138434865")
            await self.until(lambda: any("Ключи Telegram" in p["text"] for p in tg.sent("sendMessage")))
            site.send_code.assert_called_with("+79138434865")
            tg.push_text("e W - S G 3")
            await self.until(lambda: client.send_code_request.await_count)
            site.login.assert_called_with("+79138434865", "rh", "eW-SG3")
            site.create.assert_called_with("form")
            with open(agent.SETTINGS) as f:
                s = json.load(f)
            self.assertEqual((s["api_id"], s["api_hash"]), ("1234567", "0123456789abcdef0123456789abcdef"))
            self.assertEqual(oct(os.stat(agent.SETTINGS).st_mode & 0o777), "0o600")
            self.assertTrue(tg.sent("deleteMessage"))  # код с сайта удалён из чата
            await self.until(lambda: any("Наберите его кнопками" in p["text"] for p in tg.sent("sendMessage")))
            pad = [p for p in tg.sent("sendMessage") if "Наберите его кнопками" in p["text"]][-1]["reply_markup"]
            self.assertEqual(pad["inline_keyboard"][0][0]["callback_data"], "k:1")

        with mock.patch.object(get_api, "Site", return_value=site), \
                mock.patch.object(agent.Agent, "make_client", staticmethod(lambda i, h: client)):
            self.run_agent(tg, scenario)

    def test_too_many_tries_is_explained(self):
        tg = FakeTelegram()
        site = mock.Mock()
        site.send_code.side_effect = get_api.Fail("Sorry, too many tries. Please try again later.")

        async def scenario(a):
            tg.push_text("/login")
            await self.choose(tg, "login:own")
            tg.push_text("+79138434865")
            await self.until(lambda: any("временно ограничил" in p["text"] for p in tg.sent("sendMessage")))
            await self.until(lambda: any("Ключи вручную" in p["text"] for p in tg.sent("sendMessage")))
            self.assertEqual(a.login["step"], "manual")

        with mock.patch.object(get_api, "Site", return_value=site):
            self.run_agent(tg, scenario)


    def test_site_error_then_manual_keys(self):
        tg = FakeTelegram()
        site = mock.Mock()
        site.send_code.return_value = "rh"
        site.keys.return_value = (None, "form")
        site.create.side_effect = get_api.Fail("сайт ответил ERROR при создании приложения")
        client = mock.Mock()
        client.is_connected.return_value = True
        client.connect = mock.AsyncMock()
        client.is_user_authorized = mock.AsyncMock(return_value=False)
        client.send_code_request = mock.AsyncMock(return_value=pytypes.SimpleNamespace(
            phone_code_hash="h", type=pytypes.SimpleNamespace(length=5)))
        made = []

        async def scenario(a):
            tg.push_text("/login")
            await self.choose(tg, "login:own")
            tg.push_text("", contact="79138434865")
            await self.until(lambda: any("Ключи Telegram" in p["text"] for p in tg.sent("sendMessage")))
            tg.push_text("e W - S G 3")
            await self.until(lambda: any("Ключи вручную" in p["text"] for p in tg.sent("sendMessage")))
            tg.push_text("просто текст")
            await self.until(lambda: any("Не вижу ключей" in p["text"] for p in tg.sent("sendMessage")))
            tg.push_text("App api_id: 2345678\nApp api_hash: ABCDEF0123456789abcdef0123456789")
            await self.until(lambda: client.send_code_request.await_count)
            client.send_code_request.assert_awaited_with("+79138434865")
            with open(agent.SETTINGS) as f:
                s = json.load(f)
            self.assertEqual((s["api_id"], s["api_hash"]), ("2345678", "abcdef0123456789abcdef0123456789"))
            self.assertEqual(made, [(2345678, "abcdef0123456789abcdef0123456789")])

        def make(i, h):
            made.append((int(i), h))
            return client
        with mock.patch.object(get_api, "Site", return_value=site), \
                mock.patch.object(agent.Agent, "make_client", staticmethod(make)):
            self.run_agent(tg, scenario)

    def test_manual_keys_parse(self):
        self.assertEqual(agent.manual_keys("api_id 1234567 api_hash 0123456789abcdef0123456789abcdef"),
                         ("1234567", "0123456789abcdef0123456789abcdef"))
        self.assertEqual(agent.manual_keys("0123456789abcdef0123456789abcdef\n1234567"),
                         ("1234567", "0123456789abcdef0123456789abcdef"))
        self.assertIsNone(agent.manual_keys("1234567"))
        self.assertIsNone(agent.manual_keys("e W - S G 3"))


class ViaHub(AgentOverBotApi):
    """Секретарь внутри Главного: Telegram не опрашивает, обновления приходят от hub.py через сокет агента,
    кнопки — с префиксом «s:», ответы — тем же токеном. Проверяем вместе с настоящим кодом hub.py."""

    def setUp(self):
        super().setUp()
        sys.path.insert(0, os.path.join(os.path.dirname(agent.HERE), "hub"))
        import hub
        self.hub = hub
        self.hub_sent = []
        for p in (mock.patch.object(hub, "AGENT_SOCK", agent.SOCK),
                  mock.patch.object(hub, "tg", side_effect=lambda m, **kw: self.hub_sent.append((m, kw)) or {"ok": True}),
                  mock.patch.object(hub, "owner", return_value=OWNER), mock.patch.object(hub, "allowed", lambda u: u == OWNER),
                  mock.patch.object(hub, "setting", return_value=None)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(setattr, botapi, "PREFIX", "")

    def run_hub_agent(self, tg, scenario, old_token="", allowed=0):
        async def go():
            a = agent.Agent("123:abc", allowed=allowed, via_hub=True, old_token=old_token)
            a.bot = botapi.Bot("123:abc", base=tg.base)
            task = asyncio.create_task(a.main())
            try:
                await self.until(lambda: os.path.exists(agent.SOCK))
                await scenario(a)
            finally:
                task.cancel()
        asyncio.run(asyncio.wait_for(go(), 30))

    def msg(self, **kw):
        return {"message_id": 5, "chat": {"id": OWNER, "type": "private"}, "from": {"id": OWNER}, **kw}

    def test_free_text_goes_to_secretary_and_answer_comes_back(self):
        tg = FakeTelegram()

        async def scenario(a):
            a.run_claude = mock.AsyncMock(return_value=(True, "**Готово**: буду в 6"))
            await asyncio.to_thread(self.hub.on_message, self.msg(text="Ответьте Пете, что буду в 6"))
            await self.until(lambda: any("буду в 6" in p["text"] for p in tg.sent("sendMessage")))
            self.assertEqual(a.run_claude.await_args[0][0], "Ответьте Пете, что буду в 6")
            self.assertEqual(a.owner(), OWNER)  # владелец — тот, что у Главного
            self.assertFalse(tg.sent("getUpdates"))  # Telegram опрашивает только Главный
            self.assertFalse(tg.sent("setMyCommands"))  # команды и профиль — Главного
            self.assertFalse([kw for m, kw in self.hub_sent if m == "sendMessage"])  # Главный сам не отвечал
        self.run_hub_agent(tg, scenario)

    def test_buttons_prefixed_and_routed_back(self):
        tg = FakeTelegram()

        async def scenario(a):
            await asyncio.to_thread(self.hub.on_message, self.msg(text="/login"))
            await self.until(lambda: any("Как подключиться" in p["text"] for p in tg.sent("sendMessage")))
            kb = [p for p in tg.sent("sendMessage") if "Как подключиться" in p["text"]][0]["reply_markup"]
            datas = [b["callback_data"] for r in kb["inline_keyboard"] for b in r]
            self.assertEqual(datas, ["s:login:desktop", "s:login:own"])
            cq = {"id": "q", "from": {"id": OWNER}, "data": "s:m:help",
                  "message": {"message_id": tg.mid, "chat": {"id": OWNER}, "text": "x"}}
            await asyncio.to_thread(self.hub.on_callback, cq)
            await self.until(lambda: any("Чем я могу помочь" in p["text"] for p in tg.sent("editMessageText")))
            self.assertTrue(tg.sent("answerCallbackQuery"))  # ответил на кнопку Секретарь, не Главный
            self.assertFalse([m for m, kw in self.hub_sent if m == "answerCallbackQuery"])
            body, rows = await a.dashboard()
            self.assertIn({"text": "‹ Главная", "callback_data": "home"}, [b for r in rows for b in r])
            st = await asyncio.to_thread(self.hub.secretary_status)
            self.assertEqual((st["account"], st["busy"]), (None, False))
        self.run_hub_agent(tg, scenario)

    def test_photo_and_video_to_claude(self):
        tg = FakeTelegram()
        seen = []

        def photo_path(token, msg, folder):
            self.assertEqual(token, "123:abc")
            os.makedirs(folder, exist_ok=True)
            open(os.path.join(folder, "p.jpg"), "w").close()
            return os.path.join(folder, "p.jpg")

        def video(token, msg, folder):
            os.makedirs(folder, exist_ok=True)
            return {"frames": [os.path.join(folder, "frame-1.jpg"), os.path.join(folder, "frame-2.jpg")],
                    "text": "как решить это уравнение", "seconds": 7}

        async def scenario(a):
            async def ask(text, title=None, source="chat"):
                seen.append((text, title, sorted(os.listdir(agent.WORK))))
            a.ask = ask
            await asyncio.to_thread(self.hub.on_message, self.msg(photo=[{"file_id": "F"}], caption="что это?"))
            await self.until(lambda: seen)
            text, title, _ = seen[0]
            self.assertTrue(text.startswith("что это?"))
            self.assertIn("инструментом Read: " + os.path.join(agent.WORK, "media"), text)
            self.assertEqual(title, "📷 что это?")
            await asyncio.to_thread(self.hub.on_message, self.msg(video_note={"file_id": "V", "duration": 7}))
            await self.until(lambda: len(seen) == 2)
            text, title, _ = seen[1]
            self.assertIn("frame-1.jpg\n", text)
            self.assertIn("«как решить это уравнение»", text)
            self.assertIn("(7 с)", text)
            await self.until(lambda: not os.listdir(os.path.join(agent.WORK, "media")))  # файлы убраны
        with mock.patch.object(agent.media, "photo_path", photo_path), mock.patch.object(agent.media, "video", video):
            self.run_hub_agent(tg, scenario)

    def test_moved_notice_in_old_bot_once(self):
        tg = FakeTelegram()

        async def scenario(a):
            await self.until(lambda: any("переехал" in p.get("text", "") for p in tg.sent("sendMessage")))
            sent = [p for p in tg.sent("sendMessage") if "переехал" in p["text"]][0]
            self.assertIn("@secretary_bot", sent["text"])
            self.assertEqual(sent["reply_markup"], {"remove_keyboard": True})
            self.assertEqual(tg.sent("setChatMenuButton")[-1]["menu_button"], {"type": "commands"})
            with open(agent.SETTINGS) as f:
                self.assertTrue(json.load(f)["moved"])
        self.run_hub_agent(tg, scenario, old_token="999:old", allowed=OWNER)
        n = len(tg.sent("sendMessage"))
        self.run_hub_agent(tg, lambda a: asyncio.sleep(0.3), old_token="999:old", allowed=OWNER)
        self.assertEqual(len(tg.sent("sendMessage")), n)  # второй раз не пишет


class Resolve(unittest.TestCase):
    def test_modes(self):
        d = tempfile.mkdtemp()
        hub_env, own_env = os.path.join(d, "hub.env"), os.path.join(d, "own.env")
        with open(own_env, "w") as f:
            f.write("TG_AGENT_TOKEN=2:own\nTG_ALLOWED_ID=7\n")
        self.assertEqual(agent.resolve({}, hub_env, own_env), ("2:own", False, ""))  # Главного нет — свой бот
        with open(hub_env, "w") as f:
            f.write("HUB_TOKEN=1:hub\nHUB_ALLOWED=7\n")
        self.assertEqual(agent.resolve({}, hub_env, own_env), ("1:hub", True, "2:own"))  # есть Главный — внутри него
        self.assertEqual(agent.resolve({"TG_MODE": "own"}, hub_env, own_env), ("2:own", False, ""))
        with open(own_env, "a") as f:
            f.write("TG_MODE=own\n")  # режим в файле действует и после pm2 restart со старым окружением
        self.assertEqual(agent.resolve({}, hub_env, own_env)[1], False)
        with self.assertRaises(SystemExit):
            agent.resolve({}, os.path.join(d, "nope"), os.path.join(d, "nope2"))


class MarkupTest(unittest.TestCase):
    def test_prefix_only_inside_hub(self):
        self.assertEqual(botapi.Button.inline("Да", "ok:1")["callback_data"], "ok:1")
        try:
            botapi.PREFIX = "s:"
            self.assertEqual(botapi.Button.inline("Да", "ok:1")["callback_data"], "s:ok:1")
            ev = botapi.CallbackEvent(None, {"id": "1", "from": {"id": 1}, "data": "s:ok:1"})
            self.assertEqual(ev.data, b"ok:1")
        finally:
            botapi.PREFIX = ""

    def test_reply_keyboard_options(self):
        m = botapi.markup([[botapi.Button.text("A", resize=True, persistent=True, placeholder="…")], [botapi.Button.text("B")]])
        self.assertEqual(m, {"keyboard": [[{"text": "A"}], [{"text": "B"}]], "resize_keyboard": True,
                             "is_persistent": True, "input_field_placeholder": "…"})

    def test_phone_button(self):
        m = botapi.markup([[botapi.Button.request_phone("📱", resize=True, single_use=True)]])
        self.assertEqual(m["keyboard"][0][0], {"text": "📱", "request_contact": True})
        self.assertTrue(m["one_time_keyboard"])

    def test_inline_and_none(self):
        self.assertIsNone(botapi.markup(None))
        self.assertEqual(botapi.markup([]), {"inline_keyboard": []})
        self.assertIn("inline_keyboard", botapi.markup([[botapi.Button.url("x", "https://t.me/x")]]))


if __name__ == "__main__":
    unittest.main()
