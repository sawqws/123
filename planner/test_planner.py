"""Проверка планера без Telegram: разбор текста, данные, подпись Mini App, API, напоминания, утро и вечер.
usage: python3 planner/test_planner.py
"""
import datetime
import hashlib
import hmac
import json
import os
import sys
import tempfile
import time
import unittest
import urllib.parse
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
TMP = tempfile.mkdtemp()
os.environ.update(PLANNER_TOKEN="123:TEST", PLANNER_DB=os.path.join(TMP, "boot.db"))
import parse  # noqa: E402
import planner  # noqa: E402
import store  # noqa: E402

FRI = datetime.date(2026, 10, 9)
U = 7


def signed(user, token="123:TEST", age=0):
    p = {"auth_date": str(int(time.time()) - age), "query_id": "q", "user": json.dumps(user)}
    check = "\n".join(f"{k}={v}" for k, v in sorted(p.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    p["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(p)


class ParseTest(unittest.TestCase):
    def p(self, s):
        return parse.parse(s, FRI)

    def test_dates_and_times(self):
        r = self.p("завтра в 18:00 бокс")
        self.assertEqual((r["title"], r["date"], r["time"]), ("Бокс", FRI + datetime.timedelta(days=1), "18:00"))
        self.assertEqual(self.p("в среду созвон в 15:30")["date"], datetime.date(2026, 10, 14))
        self.assertEqual(self.p("в пятницу кино")["date"], FRI)
        self.assertEqual(self.p("20 октября днюха")["date"], datetime.date(2026, 10, 20))
        self.assertEqual(self.p("через 3 дня проект")["date"], datetime.date(2026, 10, 12))
        self.assertEqual(self.p("бокс в 7 вечера")["time"], "19:00")
        self.assertEqual(self.p("бокс 18:00!")["time"], "18:00")
        self.assertEqual(self.p("1.02 что-то")["date"], datetime.date(2027, 2, 1))

    def test_repeat_and_important(self):
        r = self.p("по пн, ср и пт английский в 19:00")
        self.assertEqual((r["title"], r["repeat"], r["time"]), ("Английский", "135", "19:00"))
        self.assertEqual(self.p("каждый день зарядка")["repeat"], "1234567")
        self.assertEqual(self.p("по будням школа")["repeat"], "12345")
        r = self.p("12.10 контрольная!")
        self.assertTrue(r["important"])
        self.assertEqual(r["title"], "Контрольная")

    def test_plain_text(self):
        r = self.p("купить молоко")
        self.assertEqual((r["title"], r["date"], r["time"], r["repeat"]), ("Купить молоко", FRI, None, ""))


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.db = store.Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        self.addCleanup(self.db.close)

    def test_repeat_toggle_skip(self):
        t = self.db.save_task(U, {"title": "Бокс", "date": "2026-10-05", "time": "18:00", "repeat": "1356"})
        days = [d for d in range(5, 12) if any(x["id"] == t["id"] for x in self.db.day(U, datetime.date(2026, 10, d)))]
        self.assertEqual(days, [5, 7, 9, 10])
        self.assertTrue(self.db.toggle(U, t["id"], "2026-10-09"))
        self.assertEqual(self.db.day(U, FRI)[0]["done"], 1)
        self.assertEqual(self.db.day(U, datetime.date(2026, 10, 10))[0]["done"], 0)
        self.db.delete_task(U, t["id"], "2026-10-10")
        self.assertEqual(self.db.day(U, datetime.date(2026, 10, 10)), [])
        self.assertTrue(self.db.task(U, t["id"]))

    def test_goal_progress_and_notes(self):
        g = self.db.save_goal(U, {"title": "Спарринг", "horizon": "week", "progress": 40})
        self.assertEqual(self.db.progress(U, g), 40)
        a = self.db.save_task(U, {"title": "Лапы", "goal": g["id"]})
        self.db.save_task(U, {"title": "Скакалка", "goal": g["id"]})
        self.db.toggle(U, a["id"])
        self.assertEqual(self.db.progress(U, g), 50)
        n = self.db.save_note(U, {"goal": g["id"], "text": "руки выше"})
        self.assertEqual(self.db.notes(U)[0]["text"], "руки выше")
        self.db.delete_goal(U, g["id"])
        self.assertIsNone(self.db.note(U, n["id"]))
        self.assertIsNone(self.db.task(U, a["id"])["goal"])

    def test_users_are_separate(self):
        t = self.db.save_task(U, {"title": "моё"})
        with self.assertRaises(KeyError):
            self.db.save_task(999, {"id": t["id"], "title": "чужое"})
        self.assertEqual(self.db.tasks(999), [])
        with self.assertRaises(ValueError):
            self.db.save_task(U, {"title": "  "})

    def test_backup(self):
        self.db.save_task(U, {"title": "x"})
        path = self.db.backup(os.path.join(tempfile.mkdtemp(), "b"))
        self.assertTrue(os.path.getsize(path) > 0)


class BotTest(unittest.TestCase):
    def setUp(self):
        self.db = store.Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        self.addCleanup(self.db.close)
        self.sent = []
        self.patches = [
            mock.patch.object(planner, "db", self.db),
            mock.patch.object(planner, "public_url", "https://x.trycloudflare.com"),
            mock.patch.object(planner, "PUBLIC", True),
            mock.patch.object(planner, "tg", side_effect=lambda m, **p: self.sent.append((m, p)) or {"ok": True, "result": []}),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def texts(self):
        return [p.get("text", "") for m, p in self.sent if m in ("sendMessage", "editMessageText")]

    def msg(self, text):
        planner.on_message({"chat": {"id": U, "type": "private"}, "from": {"id": U, "first_name": "Глеб"}, "text": text})

    def test_init_data(self):
        self.assertEqual(planner.check_init_data(signed({"id": 5})), {"id": 5})
        self.assertIsNone(planner.check_init_data(signed({"id": 5}, token="other")))
        self.assertIsNone(planner.check_init_data(signed({"id": 5}, age=8 * 86400)))
        self.assertIsNone(planner.check_init_data("garbage"))

    def test_owner_only(self):
        with mock.patch.object(planner, "PUBLIC", False), mock.patch.object(planner, "ALLOWED", set()):
            self.msg("/start")
            self.assertEqual(planner.owner(), U)
            planner.on_message({"chat": {"id": 99, "type": "private"}, "from": {"id": 99}, "text": "/start"})
            self.assertIn("личный", self.texts()[-1])
            self.assertEqual(planner.owner(), U)

    def test_quick_add_and_buttons(self):
        self.msg("завтра в 18:00 бокс")
        t = self.db.tasks(U)[0]
        self.assertEqual((t["title"], t["time"], t["remind"]), ("Бокс", "18:00", 15))
        self.assertIn("Бокс", self.texts()[-1])
        markup = self.sent[-1][1]["reply_markup"]["inline_keyboard"]
        self.assertTrue(any("web_app" in b for row in markup for b in row))
        url = [b for row in markup for b in row if "web_app" in b][0]["web_app"]["url"]
        self.assertTrue(url.startswith("https://x.trycloudflare.com/?p=/day/"))
        cq = {"id": "1", "from": {"id": U}, "message": {"chat": {"id": U}, "message_id": 3}, "data": f"u|{t['id']}"}
        planner.on_callback(cq)
        self.assertEqual(self.db.tasks(U), [])

    def test_today_toggle(self):
        today = planner.today_of(U)
        t = self.db.save_task(U, {"title": "Алгебра", "date": today.isoformat()})
        self.msg("/today")
        self.assertIn("Алгебра", json.dumps(self.sent[-1][1]["reply_markup"], ensure_ascii=False))
        planner.on_callback({"id": "1", "from": {"id": U}, "message": {"chat": {"id": U}, "message_id": 3},
                             "data": f"t|{t['id']}|{today.isoformat()}"})
        self.assertEqual(self.db.task(U, t["id"])["done"], 1)
        self.assertIn("Всё сделано", self.texts()[-1])

    def test_week_goals_help(self):
        self.db.save_goal(U, {"title": "Спарринг", "emoji": "🥊", "horizon": "week"})
        for c in ("/week", "/goals", "/help", "/tomorrow"):
            self.msg(c)
        joined = "\n".join(self.texts())
        self.assertIn("Неделя", joined)
        self.assertIn("🥊 Спарринг", joined)

    def test_message_style(self):
        """Единый вид: заголовок с одним эмодзи, цитата, короткие кнопки, без выравнивания пробелами."""
        today = planner.today_of(U)
        self.db.save_task(U, {"title": "Очень-очень длинное название задачи про контрольную по алгебре",
                              "date": today.isoformat(), "time": "18:00"})
        self.db.save_goal(U, {"title": "Спарринг", "emoji": "🥊", "horizon": "week"})
        for c in ("/today", "/week", "/goals", "/help", "/start"):
            self.msg(c)
        for m, p in self.sent:
            if m != "sendMessage":
                continue
            first = p["text"].split("\n")[0]
            self.assertRegex(first, r"^\S+ <b>.+</b>$")
            self.assertNotIn("  ", p["text"])
            for row in p.get("reply_markup", {}).get("inline_keyboard", []):
                self.assertLessEqual(len(row), 3)
                for b in row:
                    self.assertLessEqual(len(b["text"]), 28, b["text"])
        self.assertIn("<blockquote", self.texts()[0])

    def test_style_fallback(self):
        """Если Telegram не принимает цвет кнопок — те же кнопки уходят без цвета, и дальше без него."""
        calls = []

        def fake(m, **p):
            calls.append(p)
            if any("style" in b for row in p["reply_markup"]["inline_keyboard"] for b in row):
                raise planner.TgError(m, 400, "Bad Request: can't parse inline keyboard button: unknown field style")
            return {"ok": True, "result": {"message_id": 1}}

        with mock.patch.object(planner, "tg", side_effect=fake), mock.patch.object(planner, "STYLE_OK", True):
            planner.send(U, "x", [[planner.btn("ok", "a", "success")]])
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[-1]["reply_markup"]["inline_keyboard"], [[{"text": "ok", "callback_data": "a"}]])
            self.assertFalse(planner.STYLE_OK)
            planner.send(U, "y", [[planner.btn("ok", "a", "success")]])
            self.assertEqual(len(calls), 3)  # уже без повторной попытки
        with mock.patch.object(planner, "tg", side_effect=planner.TgError("sendMessage", 400, "chat not found")):
            with self.assertRaises(planner.TgError):
                planner.send(U, "z", [[planner.btn("ok", "a")]])

    def media_msg(self, **extra):
        planner.on_message({"chat": {"id": U, "type": "private"}, "from": {"id": U}, **extra})

    def test_voice_like_text(self):
        voice = {"voice": {"file_id": "v1", "duration": 3}}
        fake = lambda m, **p: self.sent.append((m, p)) or {"ok": True, "result": {"message_id": 55}}  # noqa: E731
        with mock.patch.object(planner, "spawn", lambda fn, *a: fn(*a)), mock.patch.object(planner, "tg", side_effect=fake), \
                mock.patch.object(planner.media, "voice_text", return_value="завтра в 18:00 бокс <b>") as vt:
            self.media_msg(**voice)
        vt.assert_called_once()
        t = self.db.tasks(U)[0]
        self.assertEqual((t["title"], t["time"]), ("Бокс <b>", "18:00"))
        texts = self.texts()
        self.assertIn("Слушаю", texts[0])
        # статус «Слушаю…» заменяется карточкой задачи, где видно и что услышал
        self.assertEqual(self.sent[-1][0], "editMessageText")
        self.assertEqual(self.sent[-1][1]["message_id"], 55)
        self.assertIn("🎙 «завтра в 18:00 бокс &lt;b&gt;»", texts[-1])
        self.assertIn("Бокс &lt;b&gt;", texts[-1])
        self.assertIn("sendChatAction", [m for m, p in self.sent])

    def test_voice_error(self):
        with mock.patch.object(planner, "spawn", lambda fn, *a: fn(*a)), \
                mock.patch.object(planner.media, "voice_text", side_effect=planner.media.MediaError("в голосовом не слышно слов")):
            self.media_msg(voice={"file_id": "v1"})
        self.assertIn("не слышно слов", self.texts()[-1])
        self.assertEqual(self.db.tasks(U), [])

    def test_voice_runs_in_thread(self):
        started = []
        with mock.patch.object(planner.threading, "Thread", side_effect=lambda **k: started.append(k) or mock.Mock()):
            self.media_msg(voice={"file_id": "v1"})
        self.assertEqual(len(started), 1)
        self.assertEqual(self.sent, [])  # главный цикл не ждёт распознавания

    def test_photo_to_tasks(self):
        today = planner.today_of(U)
        seen = {}

        def fake_claude(prompt, cwd):
            seen["prompt"], seen["cwd"] = prompt, cwd
            return "1. 12.10 18:00 Бокс\n- Купить молоко!\n• по пн и ср 19:00 Английский\n"

        with mock.patch.object(planner, "spawn", lambda fn, *a: fn(*a)), \
                mock.patch.object(planner.media, "photo_path", side_effect=lambda tok, m, folder: os.path.join(folder, "p.jpg")), \
                mock.patch.object(planner, "claude", fake_claude):
            self.media_msg(photo=[{"file_id": "a"}, {"file_id": "b"}], caption="расписание на неделю")
        self.assertIn("p.jpg", seen["prompt"])
        self.assertIn("расписание на неделю", seen["prompt"])
        self.assertIn(f"{today:%d.%m.%Y}", seen["prompt"])
        self.assertFalse(os.path.exists(seen["cwd"]))  # временная папка удалена
        tasks = {t["title"]: t for t in self.db.tasks(U)}
        self.assertEqual(set(tasks), {"Бокс", "Купить молоко", "Английский"})
        self.assertEqual(tasks["Бокс"]["time"], "18:00")
        self.assertEqual(tasks["Бокс"]["date"][5:], "10-12")
        self.assertTrue(tasks["Купить молоко"]["important"])
        self.assertEqual(tasks["Английский"]["repeat"], "13")
        self.assertIn("Добавил 3 задачи", self.texts()[-1])

    def test_photo_nothing_or_error(self):
        photo = {"photo": [{"file_id": "a"}]}
        with mock.patch.object(planner, "spawn", lambda fn, *a: fn(*a)), \
                mock.patch.object(planner.media, "photo_path", return_value="/tmp/x.jpg"):
            with mock.patch.object(planner, "claude", return_value="НЕТ"):
                self.media_msg(**photo)
            self.assertIn("Не нашёл", self.texts()[-1])
            with mock.patch.object(planner, "claude", side_effect=planner.AIError("Claude не ответил")):
                self.media_msg(**photo)
            self.assertIn("Claude не ответил", self.texts()[-1])
        self.assertEqual(self.db.tasks(U), [])

    def test_task_lines(self):
        self.assertEqual(planner.task_lines("```\n1) Алгебра\n2. 12.10 Контрольная\nНЕТ\n\n«Бокс»"),
                         ["Алгебра", "12.10 Контрольная", "Бокс"])

    def test_reminder_morning_evening(self):
        self.db.user(U)
        now = planner.user_now(self.db.user(U)).replace(hour=17, minute=31, second=0, microsecond=0)
        day = now.date().isoformat()
        self.db.save_task(U, {"title": "Бокс", "date": day, "time": "18:00", "remind": 30})
        self.db.save_task(U, {"title": "Бег", "date": day})
        self.db.update_settings(U, {"morning": "07:30", "evening": "21:30"})
        with mock.patch.object(planner, "user_now", return_value=now):
            planner.tick()
            planner.tick()  # второй раз не дублирует
        rem = [t for t in self.texts() if "⏰" in t]
        self.assertEqual(len(rem), 1)
        self.assertIn("Через 30 мин", rem[0])
        with mock.patch.object(planner, "user_now", return_value=now.replace(hour=7, minute=40)):
            planner.tick()
        self.assertIn("Доброе утро", self.texts()[-1])
        with mock.patch.object(planner, "user_now", return_value=now.replace(hour=21, minute=35)):
            planner.tick()
        self.assertIn("Итоги дня", self.texts()[-1])
        carry = [b for row in self.sent[-1][1]["reply_markup"]["inline_keyboard"] for b in row if b.get("callback_data", "").startswith("c|")]
        planner.on_callback({"id": "1", "from": {"id": U}, "message": {"chat": {"id": U}, "message_id": 3, "text": "Итоги"},
                             "data": carry[0]["callback_data"]})
        self.assertTrue(all(t["date"] > day for t in self.db.tasks(U)))

    def test_https_url_and_notify(self):
        conf, cands = planner.caddyfile("2.27.206.165")
        self.assertEqual(cands, ["https://2-27-206-165.sslip.io", "https://2.27.206.165"])
        self.assertIn("profile shortlived", conf)
        self.assertIn(f"reverse_proxy 127.0.0.1:{planner.PORT}", conf)
        for name, port in (("coach", 8789), ("hub", 8790), ("eye", 8791), ("kpp", 8792), ("avito", 8793), ("agent", 8794),
                           ("apps", 8795)):
            self.assertIn(f"https://{name}.2-27-206-165.sslip.io {{\n\treverse_proxy 127.0.0.1:{port}\n}}", conf)
        self.db.meta("owner", U)
        with mock.patch.object(planner, "public_url", ""):
            self.assertTrue(planner.use_url(cands[0]))
            self.assertEqual(planner.public_url, cands[0])
            planner.use_url(cands[0])  # тот же адрес — второй раз не пишет
        ready = [t for t in self.texts() if "Планер готов" in t]
        self.assertEqual(len(ready), 1)
        menu = [p for m, p in self.sent if m == "setChatMenuButton"]
        self.assertEqual(menu[-1]["menu_button"]["web_app"]["url"], cands[0] + "/")

    def test_welcome_without_app(self):
        with mock.patch.object(planner, "public_url", ""):
            self.msg("/start")
        self.assertIn("пришлю кнопку", self.texts()[-1])
        self.msg("/start")
        self.assertIn("кнопка «Планер»", self.texts()[-1])

    def test_api_routes(self):
        r = planner.route(U, "/api/task", {"title": "Шаг", "date": "2026-10-09"})
        tid = r["task"]["id"]
        g = planner.route(U, "/api/goal", {"title": "Цель", "horizon": "month"})["goal"]
        planner.route(U, "/api/task", {"id": tid, "goal": g["id"]})
        r = planner.route(U, "/api/task/toggle", {"id": tid})
        self.assertTrue(r["done"])
        self.assertEqual([x["pct"] for x in r["goals"]], [100])
        planner.route(U, "/api/note", {"goal": g["id"], "text": "ok"})
        data = planner.route(U, "/api/all", {})
        self.assertEqual((len(data["tasks"]), len(data["goals"]), len(data["notes"])), (1, 1, 1))
        with self.assertRaises(KeyError):
            planner.route(U, "/api/nope", {})
        self.assertEqual(planner.route(U, "/api/settings", {"morning": "", "remind": 30})["user"]["morning"], "")


class WebTest(unittest.TestCase):
    """Настоящий HTTP: страница, статика, API с подписью и без."""
    @classmethod
    def setUpClass(cls):
        import threading
        from http.server import ThreadingHTTPServer
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), planner.Web)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def get(self, path, headers=None, data=None):
        import urllib.error
        import urllib.request
        req = urllib.request.Request(self.base + path, headers=headers or {}, data=data)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_page_and_api(self):
        code, body = self.get("/")
        self.assertEqual(code, 200)
        self.assertIn(f"app.js?v={planner.VERSION}", body)
        self.assertEqual(self.get("/app.css")[0], 200)
        self.assertEqual(self.get("/../planner.py")[0], 404)
        self.assertEqual(self.get("/api/all")[0], 401)
        with mock.patch.object(planner, "PUBLIC", True):
            code, body = self.get("/api/all", {"X-Init-Data": signed({"id": 42, "first_name": "A"})})
            self.assertEqual(code, 200)
            self.assertIn("today", json.loads(body))
            code, body = self.get("/api/task", {"X-Init-Data": signed({"id": 42}), "Content-Type": "application/json"},
                                  json.dumps({"title": ""}).encode())
            self.assertEqual(code, 400)
        with mock.patch.object(planner, "PUBLIC", False), mock.patch.object(planner, "ALLOWED", {1}):
            self.assertEqual(self.get("/api/all", {"X-Init-Data": signed({"id": 42})})[0], 403)


if __name__ == "__main__":
    unittest.main()
