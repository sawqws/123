"""Проверка бота без Telegram и сайта: черновик -> правка -> отправка -> правила, алфавит, /update.
usage: python3 bot/test_bot.py
"""
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("TELEGRAM_TOKEN", "test")
import bot  # noqa: E402
import drafts  # noqa: E402
import web  # noqa: E402

URL = "https://horodigital.ru/student/topic/11111111-1111-1111-1111-111111111111/task/22222222-2222-2222-2222-222222222222"
TASK = "22222222-2222-2222-2222-222222222222"
CHAT = 42


class Sync:
    """threading.Thread, который выполняет задачу сразу."""
    def __init__(self, target, args=(), daemon=None):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


class BotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.patches = [
            mock.patch.object(bot, "SETTINGS", os.path.join(t, "settings.json")),
            mock.patch.object(bot, "OVERVIEW", os.path.join(t, "overview.json")),
            mock.patch.object(drafts, "TMP", t),
            mock.patch.object(drafts, "STYLE_DIR", os.path.join(t, "style")),
            mock.patch.object(drafts, "STYLE", os.path.join(t, "style", "style.md")),
            mock.patch.object(drafts, "EDITS", os.path.join(t, "style", "edits.jsonl")),
            mock.patch.object(bot.threading, "Thread", Sync),
            mock.patch.object(bot, "tg", self.fake_tg),
            mock.patch.object(bot, "send_file", lambda chat, path, caption="": self.files.append(path)),
            mock.patch.object(bot, "claude", self.fake_claude),
            mock.patch.object(bot, "node", self.fake_node),
        ]
        for p in self.patches:
            p.start()
        self.msgs, self.files, self.prompts, self.nodes, self.reads = [], [], [], [], []
        self.ov = {"tasks": [], "grades": [], "comments": [], "at": 1}
        os.makedirs(drafts.task_dir(TASK))
        with open(os.path.join(drafts.task_dir(TASK), "task.json"), "w") as f:
            json.dump({"data": {"type": "detailedAnswer", "title": "Конспект §5"}}, f)

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()
        bot.mode.clear()
        bot.LAST.clear()

    # ---- заглушки ----
    def fake_tg(self, method, **p):
        if method == "sendMessage":
            self.msgs.append((p["text"], p.get("reply_markup")))
        return {"ok": True, "result": []}

    def fake_claude(self, prompt, tools, timeout=0, on_tool=None):
        self.prompts.append(prompt)
        if prompt.startswith("Задание:"):  # решение: Claude пишет черновик и картинку
            drafts.write(TASK, "Клетка делится митозом.\nЭто важно.")
            d = drafts.files_dir(TASK)
            os.makedirs(d, exist_ok=True)
            pathlib.Path(d, "hand.png").write_bytes(b"png")
            return True, "Сделал черновик"
        if "Глеб написал про него" in prompt:  # просьба поправить черновик
            drafts.write(TASK, "Клетка делится митозом.")
            return True, "Убрал второе предложение"
        os.makedirs(drafts.STYLE_DIR, exist_ok=True)
        pathlib.Path(drafts.STYLE).write_text("- коротко\n")
        return True, "- писать короче"

    def fake_node(self, *args, timeout=0, prog="node"):
        if args[:1] == ("horo/overview.js",):  # чтение списка для приложения — не отправка
            self.reads.append(args)
            return True, json.dumps(self.ov)
        self.nodes.append(args)
        return True, "Статус: checking"

    def say(self, text="", **extra):
        bot.handle({"chat": {"id": CHAT}, "from": {"id": CHAT}, "text": text, **extra})

    def last(self):
        return self.msgs[-1]

    # ---- тесты ----
    def test_draft_edit_and_submit(self):
        self.say(URL)
        self.assertIn("horo/tmp/" + TASK + "/draft.txt", self.prompts[0])
        self.assertTrue(any("Черновик для учителя</b>\n<i>Конспект §5</i>" in m and kb for m, kb in self.msgs))
        self.assertEqual(self.files[-1], os.path.join(drafts.files_dir(TASK), "hand.png"))
        self.assertEqual(self.nodes, [])  # без подтверждения ничего не отправлено

        self.say("Клетка делится митозом.")
        self.assertEqual(drafts.read(TASK), "Клетка делится митозом.")
        self.assertEqual(drafts.recent_edits()[-1]["before"], "Клетка делится митозом.\nЭто важно.")

        bot.on_callback({"id": "1", "data": "send", "message": {"chat": {"id": CHAT}, "message_id": 5}})
        args = self.nodes[-1]
        self.assertEqual(args[:3], ("horo/answer.js", URL, drafts.text_path(TASK)))
        self.assertIn(os.path.join(drafts.files_dir(TASK), "hand.png"), args)
        self.assertIsNone(bot.draft())
        self.assertIn("БЫЛО:", self.prompts[-1])  # правки ушли на обобщение
        self.assertIn("Запомнил", self.last()[0])

    def test_short_message_is_a_request(self):
        self.say(URL)
        self.say("убери второе")
        self.assertIn("«убери второе»", self.prompts[-1])
        self.assertEqual(drafts.read(TASK), "Клетка делится митозом.")
        self.assertEqual(bot.draft()["pairs"][0][2], "убери второе")
        self.say("ок")
        self.assertIn("ЧТО ОН НАПИСАЛ: убери второе", self.prompts[-1])

    def test_own_photo_replaces_generated(self):
        self.say(URL)
        with mock.patch.object(bot, "download", lambda fid: (b"jpg", "photo.jpg")):
            self.say(photo=[{"file_id": "a"}, {"file_id": "b"}])
            self.say(photo=[{"file_id": "c"}])
        names = [os.path.basename(f) for f in drafts.files(TASK)]
        self.assertEqual(names, ["01_photo.jpg", "02_photo.jpg"])

    def test_drop_and_ok_word(self):
        self.say(URL)
        bot.on_callback({"id": "1", "data": "drop", "message": {"chat": {"id": CHAT}, "message_id": 5}})
        self.assertIsNone(bot.draft())
        self.assertEqual(self.nodes, [])
        self.say(URL)
        self.say("ок")
        self.assertEqual(self.nodes[-1][0], "horo/answer.js")
        self.assertEqual(len(self.prompts), 2)  # правок не было — обобщать нечего

    def test_autotest_has_no_draft(self):
        def solve_only(prompt, tools, timeout=0, on_tool=None):
            self.prompts.append(prompt)
            return True, "Баллы 5/5"
        with mock.patch.object(bot, "claude", solve_only):
            self.say(URL)
        self.assertIsNone(bot.draft())
        self.assertIn("Баллы 5/5", self.last()[0])

    def test_late_list_sent_as_html(self):
        with mock.patch.object(bot, "node", lambda *a, timeout=0, prog="node": (True, '❗ <b>Просрочено: 1</b>\n🔁 <a href="u">Тест &amp; Ко</a>')):
            self.say(bot.BTN_LATE)
        self.assertEqual(self.last()[0], '❗ <b>Просрочено: 1</b>\n🔁 <a href="u">Тест &amp; Ко</a>')  # разметка не экранирована

    def test_handwriting_needs_alphabet(self):
        with mock.patch.object(bot, "GLEB_EXTRA", "/nonexistent"):
            self.assertIn("НУЖЕН_АЛФАВИТ", bot.prompt_for(URL, "", False))
            with mock.patch.object(bot, "claude", lambda p, t, timeout=0, on_tool=None: (True, "НУЖЕН_АЛФАВИТ")):
                self.say(URL)
        self.assertIn("алфавита у меня ещё нет", self.last()[0])
        self.assertIsNone(bot.draft())
        with mock.patch.object(bot, "GLEB_EXTRA", __file__):
            self.assertNotIn("НУЖЕН_АЛФАВИТ", bot.prompt_for(URL, "", False))

    def test_errors_are_short(self):
        trace = "Error: page.goto: net::ERR_NAME_NOT_RESOLVED at https://x\n    at open (/root/horo/horo/lib.js:40:11)"
        self.assertEqual(bot.explain(trace), "⚠️ Не получилось: page.goto: net::ERR_NAME_NOT_RESOLVED at https://x")
        self.assertIn("долго не отвечает", bot.explain("TimeoutError: Timeout 30000ms exceeded"))

    def test_every_message_is_valid_telegram_html(self):
        """Все сообщения (они уходят с parse_mode=HTML) — только разрешённые теги, все закрыты."""
        from html.parser import HTMLParser
        allowed = {"b", "i", "u", "s", "a", "code", "pre", "blockquote", "tg-emoji"}

        class Check(HTMLParser):
            def __init__(self):
                super().__init__(); self.stack = []
            def handle_starttag(self, tag, attrs):
                assert tag in allowed, tag
                self.stack.append(tag)
            def handle_endtag(self, tag):
                assert self.stack and self.stack.pop() == tag, tag

        sent = []
        real_tg = self.fake_tg
        def tg(method, **p):
            if method == "sendMessage":
                sent.append(p["text"])
            return real_tg(method, **p)
        with mock.patch.object(bot, "tg", tg), \
             mock.patch.object(bot, "py", lambda *a, timeout=0: (True, "Добавил: а <б>")), \
             mock.patch.object(bot, "download", lambda fid: (b"jpg", "s.jpg")), \
             mock.patch.object(bot, "ALPHA_DIR", self.tmp.name):
            for t in ("/start", bot.BTN_HELP, bot.BTN_DIGEST, bot.BTN_DIGEST, bot.BTN_CHECK, bot.BTN_DO, bot.BTN_SHOW,
                      "что-то непонятное", bot.BTN_HAND):
                self.say(t)
            self.say(photo=[{"file_id": "a"}])
            self.say("готово")
            self.say(URL)                      # черновик
            self.say("Новый <текст> & ещё")    # правка с символами HTML
            bot.on_callback({"id": "1", "data": "send", "message": {"chat": {"id": CHAT}, "message_id": 5}})
        self.assertGreater(len(sent), 15)
        for text in sent:
            c = Check(); c.feed(text); c.close()
            self.assertEqual(c.stack, [], text)
            self.assertNotIn("<текст>", text)  # пользовательский текст экранирован

    def test_hand_style_tuning(self):
        style = os.path.join(self.tmp.name, "hand_style.json")
        with mock.patch.object(bot, "HAND_STYLE", style), mock.patch.object(bot, "GLEB_EXTRA", __file__), \
             mock.patch.object(bot, "py", lambda *a, timeout=0: (True, "")), mock.patch.object(bot, "ALPHA_DIR", self.tmp.name):
            bot.mode[CHAT] = "alphabet"
            self.say("готово")
            self.assertEqual(bot.mode.get(CHAT), "hand_tune")
            self.assertIn("hand:mess:1", self.last()[1])           # кнопки настройки под примером
            bot.on_callback({"id": "1", "data": "hand:mess:1", "message": {"chat": {"id": CHAT}, "message_id": 5}})
            self.say("чуть тоньше и крупнее")
            st = json.loads(pathlib.Path(style).read_text())
            self.assertEqual(st, {"mess": 1.25, "width": 0.9, "size": 1.05})
            self.say("ок")
        self.assertNotIn(CHAT, bot.mode)
        self.assertIn("Запомнил стиль", self.last()[0])

    def test_alphabet_mode(self):
        with mock.patch.object(bot, "py", lambda *a, timeout=0: (True, "Добавил: а б")) as _, \
             mock.patch.object(bot, "download", lambda fid: (b"jpg", "s.jpg")), \
             mock.patch.object(bot, "ALPHA_DIR", self.tmp.name):
            self.say(bot.BTN_HAND)
            self.assertEqual(len(self.files), 4)
            self.say(photo=[{"file_id": "a"}])
            self.assertTrue(any("Добавил знаков: 2\nа б" in m for m, _ in self.msgs))
            self.say("готово")
        self.assertNotIn(CHAT, bot.mode)

    def test_score_card_and_confetti(self):
        sent = []
        def tg(method, **p):
            if method == "sendMessage":
                sent.append(p)
            if method == "setMessageReaction":
                sent.append({"reaction": p["reaction"]})
            return {"ok": True, "result": {"message_id": 7}}
        with mock.patch.object(bot, "tg", tg), \
             mock.patch.object(bot, "claude", lambda p, t, timeout=0, on_tool=None: (True, "БАЛЛЫ: 7/7\n1. ответ А")):
            bot.handle({"chat": {"id": CHAT}, "from": {"id": CHAT}, "text": URL, "message_id": 3})
        card = [p for p in sent if "text" in p][-1]
        self.assertIn("🏆 <b>7 / 7</b> · 100%", card["text"])
        self.assertNotIn("БАЛЛЫ", card["text"])
        self.assertEqual(card.get("message_effect_id"), bot.EFFECT_CONFETTI)
        self.assertEqual(json.loads([p for p in sent if "reaction" in p][-1]["reaction"]), [{"type": "emoji", "emoji": "🏆"}])
        self.assertEqual(bot.parse_score("БАЛЛЫ: 85%")[0], (85.0, None, 85))
        self.assertEqual(bot.parse_score("Сдал\n**БАЛЛЫ: 6/7**")[0][2], 86)
        self.assertIsNone(bot.parse_score("баллы не показали")[0])
        self.assertEqual(bot.md("**1.** ответ `a<b`\n- пункт\n```\nx\n```"), "<b>1.</b> ответ <code>a&lt;b</code>\n• пункт\nx")

    def test_effect_falls_back(self):
        """Неизвестный эффект Telegram не принял — сообщение уходит без него, с разметкой."""
        import io, urllib.error
        calls = []
        def tg(method, **p):
            calls.append(p)
            if "message_effect_id" in p:
                raise urllib.error.HTTPError("u", 400, "bad effect", {}, io.BytesIO(b""))
            return {"ok": True, "result": {"message_id": 1}}
        with mock.patch.object(bot, "tg", tg):
            self.assertEqual(bot.send(CHAT, "<b>ok</b>", raw_html=True, effect="1"), 1)
        self.assertEqual(calls[-1]["text"], "<b>ok</b>")
        self.assertEqual(calls[-1]["parse_mode"], "HTML")

    def test_split_keeps_quotes_whole(self):
        quote = "<blockquote>" + "\n".join(["строка задания"] * 100) + "</blockquote>"
        parts = bot.split("📋 <b>Надо сделать</b>\n\n" + "\n\n".join([quote] * 4))
        self.assertGreater(len(parts), 1)
        for p in parts:
            self.assertEqual(p.count("<blockquote>"), p.count("</blockquote>"))

    def test_steps_from_tools(self):
        self.assertEqual(bot.step_of("Bash", {"command": "node horo/fetch.js " + URL})[0], "📖 Читаю условие")
        self.assertEqual(bot.step_of("Bash", {"command": "node horo/submit.js u horo/tmp/x/answers.json"})[0], "📤 Сдаю на сайт")
        self.assertEqual(bot.step_of("Write", {"file_path": "horo/tmp/x/answers.json"})[0], "🧠 Записываю ответы")
        self.assertEqual(bot.step_of("Read", {"file_path": "horo/tmp/x/q1.png"})[0], "🖼 Разглядываю картинки")
        self.assertIsNone(bot.step_of("Glob", {"pattern": "*"}))

    def test_claude_stream(self):
        """Настоящий claude() на поддельном CLI: шаги из потока событий и итоговый текст."""
        d = tempfile.mkdtemp(dir=self.tmp.name)
        events = [
            {"type": "system", "subtype": "init"},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "node horo/fetch.js x"}}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "думаю"}]}},
            {"type": "result", "subtype": "success", "is_error": False, "result": "БАЛЛЫ: 3/4\nготово"},
        ]
        fake = pathlib.Path(d, "claude")
        fake.write_text("#!/bin/sh\ncat <<'EOF'\n" + "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\nEOF\n")
        fake.chmod(0o755)
        tools = []
        next(p for p in self.patches if p.attribute == "claude").stop()  # настоящий claude()
        try:
            with mock.patch.dict(os.environ, {"PATH": d + os.pathsep + os.environ["PATH"]}):
                ok, out = bot.claude("x", [], timeout=20, on_tool=lambda n, i: tools.append((n, i["command"])))
                fake.write_text('#!/bin/sh\necho \'{"type":"result","is_error":true,"result":"Usage limit reached"}\'\n')
                bad = bot.claude("x", [], timeout=20)
        finally:
            next(p for p in self.patches if p.attribute == "claude").start()
        self.assertEqual((ok, out), (True, "БАЛЛЫ: 3/4\nготово"))
        self.assertEqual(tools, [("Bash", "node horo/fetch.js x")])
        self.assertEqual(bad[0], False)
        self.assertIn("лимит", bad[1])

    def test_autotests_confirm_by_button(self):
        rows = [{"auto": True, "rawStatus": "appointed", "url": URL, "title": "Тест <1>", "subj": "Информатика", "icon": "💻"}]
        with mock.patch.object(bot, "node", lambda *a, timeout=0, prog="node": (True, json.dumps(rows))), \
             mock.patch.object(bot, "claude", lambda p, t, timeout=0, on_tool=None: (True, "БАЛЛЫ: 7/7\nвсё")):
            self.say(bot.BTN_ALL)
            self.assertIn("all:yes", self.last()[1])
            self.assertIn("Тест &lt;1&gt;", self.last()[0])
            bot.on_callback({"id": "1", "data": "all:yes", "message": {"chat": {"id": CHAT}, "message_id": 5}})
        self.assertIn("🏆 <b>7/7</b> · Тест &lt;1&gt;", self.last()[0])

    def test_old_button_names_still_work(self):
        self.say("📊 Мои оценки")
        self.assertEqual(self.nodes[-1], ("horo/grades.js", "--html"))
        kb = json.loads(bot.menu_kb())
        self.assertIn(bot.BTN_GRADES, sum(kb["keyboard"], []))

    def test_planner_install(self):
        tok = "8800000000:AAH" + "x" * 32
        runs = []

        class Resp:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self): return b'{"ok":true,"result":{"username":"plan_bot"}}'

        def fake_run(cmd, **kw):
            runs.append((cmd, kw["env"]))
            return mock.Mock(returncode=0, stdout="ok", stderr="")
        with mock.patch.object(bot.urllib.request, "urlopen", lambda *a, **k: Resp()), \
                mock.patch.object(bot.subprocess, "run", fake_run):
            self.say("/planner")
            self.assertIn("Пришли токен", self.last()[0])
            self.say(tok, message_id=9)
        self.assertTrue(runs[0][0][-1].endswith("planner/install.sh"))
        self.assertEqual(runs[0][1]["PLANNER_TOKEN"], tok)
        self.assertNotIn("TELEGRAM_TOKEN", runs[0][1])  # секреты HDP-бота установке не передаются
        self.assertIn("Планер запущен", self.last()[0])
        self.assertIn("t.me/plan_bot", self.last()[1])
        def bad(*a, **k):
            raise OSError("401")
        with mock.patch.object(bot.urllib.request, "urlopen", bad), mock.patch.object(bot.subprocess, "run", fake_run):
            self.say("/planner 1234567:" + "y" * 40)  # неверный токен — установка не запускается
        self.assertEqual(len(runs), 1)
        self.assertIn("не подошёл", self.last()[0])


class AppTest(BotTest):
    """Приложение (Mini App): подпись Telegram, доступ только владельцу, действия те же, что у кнопок."""

    def init_data(self, uid=CHAT, token="test", age=0):
        import hashlib, hmac, time, urllib.parse
        pairs = {"auth_date": str(int(time.time()) - age), "query_id": "q", "user": json.dumps({"id": uid, "first_name": "Глеб"})}
        secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        pairs["hash"] = hmac.new(secret, "\n".join(f"{k}={v}" for k, v in sorted(pairs.items())).encode(), hashlib.sha256).hexdigest()
        return urllib.parse.urlencode(pairs)

    def test_init_data_signature(self):
        self.assertEqual(web.check_init_data(self.init_data(), "test")["id"], CHAT)
        self.assertIsNone(web.check_init_data(self.init_data(token="other"), "test"))  # подпись чужим токеном
        self.assertIsNone(web.check_init_data(self.init_data(age=5 * 86400), "test"))  # устарело
        forged = self.init_data().replace("%3A+42", "%3A+43")  # подменили id пользователя
        self.assertNotEqual(forged, self.init_data())
        self.assertIsNone(web.check_init_data(forged, "test"))
        self.assertIsNone(web.check_init_data("", "test"))

    def test_server_lets_only_owner_in(self):
        import http.client
        from http.server import ThreadingHTTPServer
        bot.save_settings({"owner": CHAT})
        web._cfg.update(token="test", owner=bot.owner, routes=bot.ROUTES, version="t")
        srv = ThreadingHTTPServer(("127.0.0.1", 0), web.Web)
        bot.REAL_THREAD(target=srv.serve_forever, daemon=True).start()  # threading.Thread в тестах синхронный
        try:
            def get(path, data=None):
                c = http.client.HTTPConnection("127.0.0.1", srv.server_port, timeout=10)
                c.request("GET", path, headers={"X-Init-Data": data} if data else {})
                r = c.getresponse()
                return r.status, r.read()
            self.assertEqual(get("/api/state")[0], 401)
            self.assertEqual(get("/api/state", self.init_data(uid=7))[0], 403)
            code, body = get("/api/state", self.init_data())
            self.assertEqual((code, json.loads(body)["busy"]), (200, False))
            self.assertEqual(get("/")[0], 200)
            self.assertEqual(get("/app.js")[0], 200)
            self.assertEqual(get("/../bot.py")[0], 404)
            self.assertEqual(get("/%2e%2e%2fbot.py")[0], 404)
        finally:
            srv.shutdown()

    def test_solve_from_app_goes_like_link(self):
        bot.save_settings({"owner": CHAT})
        with self.assertRaises(ValueError):
            bot.app_solve({"url": "https://example.com"})
        self.assertEqual(bot.app_solve({"url": URL, "show": False}), {"ok": True})
        self.assertIn("horo/tmp/" + TASK + "/draft.txt", self.prompts[0])  # тот же run_task, что и по ссылке в чате
        st = bot.app_state()
        self.assertEqual(st["draft"]["text"], "Клетка делится митозом.\nЭто важно.")
        self.assertTrue(st["last"]["ok"] and st["last"]["draft"])
        self.assertEqual(self.reads, [("horo/overview.js",)])  # список в приложении обновился
        # правка текста в приложении запоминается как правка, отправка — тот же answer.js
        bot.ROUTES["/api/draft/save"]({"text": "Клетка делится митозом."})
        self.assertEqual(drafts.read(TASK), "Клетка делится митозом.")
        bot.ROUTES["/api/draft/send"]({})
        self.assertEqual(self.nodes[-1][:2], ("horo/answer.js", URL))
        self.assertIsNone(bot.draft())
        self.assertEqual(bot.LAST["kind"], "sent")

    def test_busy_and_autotests(self):
        bot.save_settings({"owner": CHAT})
        bot.lock.acquire()
        try:
            with self.assertRaises(ValueError):
                bot.app_solve({"url": URL})
            self.assertTrue(bot.app_state()["busy"])
        finally:
            bot.lock.release()
        with self.assertRaises(ValueError):
            bot.app_auto({})  # списка ещё нет — сдавать нечего
        pathlib.Path(bot.OVERVIEW).write_text(json.dumps({"at": 1, "tasks": [
            {"auto": True, "rawStatus": "appointed", "url": URL, "title": "Тест", "subj": "Информатика"},
            {"auto": True, "rawStatus": "failed", "url": URL + "x", "title": "Старый", "subj": "Информатика"}]}))
        self.assertEqual(bot.app_auto({})["n"], 1)
        self.assertIn("Автотесты сданы", self.msgs[-1][0])

    def test_overview_refresh_and_menu_button(self):
        r = bot.app_overview({})
        self.assertTrue(r["loading"] or bot.overview())  # данных не было — пошёл за ними
        self.assertEqual(bot.overview()["at"], 1)
        bot.save_settings({"owner": CHAT})
        calls = []
        with mock.patch.object(bot, "tg", lambda m, **p: calls.append((m, p)) or {"ok": True, "result": []}):
            bot.on_app_url("https://abc.trycloudflare.com")
            bot.handle({"chat": {"id": CHAT}, "from": {"id": CHAT}, "text": "/app"})
        m, p = calls[0]
        self.assertEqual((m, p["chat_id"]), ("setChatMenuButton", CHAT))
        self.assertEqual(json.loads(p["menu_button"])["web_app"]["url"], "https://abc.trycloudflare.com/")
        self.assertIn("web_app", calls[-1][1]["reply_markup"])



for _name in dir(BotTest):  # AppTest берёт у BotTest только заготовки, его тесты второй раз не гоняем
    if _name.startswith("test_") and _name not in AppTest.__dict__:
        setattr(AppTest, _name, None)

if __name__ == "__main__":
    unittest.main()
