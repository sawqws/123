"""Проверка планера без Telegram: разбор текста, данные, подпись Mini App, API, напоминания, утро и вечер.
usage: python3 planner/test_planner.py
"""
import datetime
import hashlib
import hmac
import io
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
import support  # noqa: E402
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
        self.db.meta("mode", "app")  # режим выбран (иначе /start — карточка выбора); чат — в test_mode_*

    def has_app(self, p=None):
        """Есть ли в сообщении кнопка web_app."""
        p = p if p is not None else self.sent[-1][1]
        return any("web_app" in b for row in p.get("reply_markup", {}).get("inline_keyboard", []) for b in row)

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

    def test_stale_buttons(self):
        """Кнопки старого сообщения, а задачу/цель уже удалили: понятный ответ и свежий экран, без ошибки в логе."""
        today = planner.today_of(U).isoformat()
        t = self.db.save_task(U, {"title": "Бокс", "date": today})
        g = self.db.save_goal(U, {"title": "Спарринг", "horizon": "week"})
        self.db.delete_task(U, t["id"])
        self.db.delete_goal(U, g["id"])
        out = io.StringIO()
        with mock.patch("sys.stdout", out), mock.patch("sys.stderr", out):
            for data in (f"t|{t['id']}|{today}", f"gs|{t['id']}|{g['id']}", f"r|{t['id']}|{today}", f"gd|{g['id']}"):
                self.cb(data)
        self.assertEqual(out.getvalue(), "")
        notes = [p.get("text") for m, p in self.sent if m == "answerCallbackQuery"]
        self.assertEqual(notes, ["⚠️ Этой задачи уже нет", "⚠️ Этого шага уже нет", "⚠️ Этой задачи уже нет", "⚠️ Цели больше нет"])
        self.assertIn("Пусто", self.texts()[0])
        self.assertIn("Этой задачи уже нет", self.texts()[2])
        self.assertIn("Цели больше нет", self.texts()[3])

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
        self.db.save_note(U, {"text": "мысль"})
        for c in ("/today", "/week", "/month", "/goals", "/notes", "/settings", "/help", "/start"):
            self.msg(c)
        g = self.db.goals(U)[0]
        planner.on_callback({"id": "1", "from": {"id": U}, "message": {"chat": {"id": U}, "message_id": 3},
                             "data": f"g|{g['id']}"})
        for m, p in self.sent:
            if m not in ("sendMessage", "editMessageText"):
                continue
            if "keyboard" in p.get("reply_markup", {}):  # ☰ меню-квадратик: служебная строка, кнопки — свои
                self.assertEqual(p["text"], planner.menukb.NOTE)
                for row in p["reply_markup"]["keyboard"]:
                    self.assertLessEqual(len(row), 3)
                    self.assertTrue(all(len(b["text"]) <= 24 for b in row))
                continue
            first = p["text"].split("\n")[0]
            self.assertRegex(first, r"^\S+ <b>.+</b>$")
            self.assertNotIn("  ", p["text"])
            self.assertLess(len(p["text"]), 1500)  # без простыней
            for row in p.get("reply_markup", {}).get("inline_keyboard", []):
                self.assertLessEqual(len(row), 3)
                for b in row:
                    self.assertLessEqual(len(b["text"]), 24, b["text"])
        self.assertIn("<blockquote", self.texts()[0])

    def test_note_date_in_user_timezone(self):
        n = self.db.save_note(U, {"text": "поздно"})
        self.db.x("UPDATE notes SET created='2026-10-09T22:30:00+00:00' WHERE id=?", n["id"])
        self.db.update_settings(U, {"tz": "Asia/Tokyo"})
        self.assertEqual(planner.note_when(U, "2026-10-09T22:30:00+00:00"), "10 окт")  # в Токио уже 10-е
        self.db.update_settings(U, {"tz": "Europe/Moscow"})
        self.assertEqual(planner.note_when(U, "2026-10-09T22:30:00+00:00"), "10 окт")  # в Москве 01:30
        self.db.update_settings(U, {"tz": "America/New_York"})
        self.assertEqual(planner.note_when(U, "2026-10-09T22:30:00+00:00"), "9 окт")

    def cb(self, data):
        planner.on_callback({"id": "1", "from": {"id": U}, "message": {"chat": {"id": U}, "message_id": 3}, "data": data})

    def answers(self):
        return [p.get("text", "") for m, p in self.sent if m == "answerCallbackQuery"]

    def buttons(self, p):
        return [b for row in p.get("reply_markup", {}).get("inline_keyboard", []) for b in row]

    def test_every_button_answers(self):
        """Каждая кнопка каждого экрана (оба режима): обработчик есть, нажатие отвечено, без «устарела»/ошибки."""
        today = planner.today_of(U)
        self.db.save_task(U, {"title": "Бокс", "date": today.isoformat(), "time": "18:00"})
        g = self.db.save_goal(U, {"title": "Спарринг", "emoji": "🥊", "horizon": "week",
                                  "start": store.period_start("week", today).isoformat()})
        self.db.save_task(U, {"title": "Шаг", "goal": g["id"]})
        self.db.save_note(U, {"text": "мысль"})
        with mock.patch.object(planner, "owner", lambda: U):
            for mode in ("chat", "app"):
                self.db.meta("mode", mode)
                self.sent.clear()
                for c in ("/start", "/today", "/week", "/month", "/goals", "/notes", "/settings", "/help", "/mode",
                          "завтра кино", "заметка: идея", "цель на неделю: бег"):
                    self.msg(c)
                screens = [p for m, p in self.sent if m in ("sendMessage", "editMessageText")]
                seen, queue = set(), [b["callback_data"] for p in screens for b in self.buttons(p) if "callback_data" in b]
                while queue:
                    data = queue.pop(0)
                    if data in seen or data.startswith(("sup|", "u|", "un|", "ug|", "c|")):
                        continue  # поддержка — common/support.py; отмены удаляют — их проверяют отдельные тесты
                    if data.startswith("d|") and abs((datetime.date.fromisoformat(data[2:]) - today).days) > 2:
                        continue  # листание дней бесконечно — хватит соседних
                    seen.add(data)
                    n = len(self.sent)
                    self.cb(data)
                    new = self.sent[n:]
                    ans = [p for m, p in new if m == "answerCallbackQuery"]
                    self.assertEqual(len(ans), 1, data)
                    self.assertNotIn("⚠️", ans[0].get("text", ""), data)
                    for m, p in new:
                        queue += [b["callback_data"] for b in self.buttons(p) if "callback_data" in b]
                self.assertIn("home", seen)
                self.assertTrue(any(d.startswith("v|") for d in seen) or mode == "app")

    def test_menu_edits_in_place_with_back(self):
        """Меню чата не плодит сообщения: экран встаёт на место меню, «‹ Меню» возвращает его."""
        self.db.meta("mode", "chat")
        for d in ("v|week", "v|month", "v|goals", "v|notes", "v|settings", f"d|{planner.today_of(U).isoformat()}"):
            self.sent.clear()
            self.cb(d)
            m, p = self.sent[0]
            self.assertEqual(m, "editMessageText", d)
            self.assertIn("home", [b.get("callback_data") for b in self.buttons(p)], d)
        self.sent.clear()
        self.cb("home")
        self.assertEqual(self.sent[0][0], "editMessageText")
        self.assertIn("v|week", json.dumps(self.sent[0][1]))

    def test_stale_and_broken_buttons(self):
        """Кнопки старых сообщений: удалённая задача/цель, мусор в данных, неизвестная кнопка — ответ «⚠️», бот живой."""
        today = planner.today_of(U).isoformat()
        for data in (f"t|999|{today}", "gs|999|1", "gd|999", "m|999|-", "r|999|" + today, "zzz",
                     "t|abc|x", "d|not-a-date", "v|nope", "mode", "s"):
            self.sent.clear()
            self.cb(data)
            ans = self.answers()
            self.assertEqual(len(ans), 1, data)
            self.assertIn("⚠️", ans[0], data)
        self.sent.clear()
        self.cb(f"t|999|{today}")  # экран дня перерисован без пропавшей задачи
        self.assertTrue(any(m == "editMessageText" and "Пусто" in p["text"] for m, p in self.sent))
        self.cb("g|999")  # открыть удалённую цель — спокойный экран с выходом, не ошибка
        self.assertIn("Цели больше нет", self.texts()[-1])

    def test_old_query_and_uneditable_message(self):
        """Telegram не принял ответ на старое нажатие или правку старого сообщения — не падаем, шлём новым."""
        def fake(m, **p):
            self.sent.append((m, p))
            if m == "answerCallbackQuery":
                raise planner.TgError(m, 400, "query is too old and response timeout expired")
            if m == "editMessageText":
                raise planner.TgError(m, 400, "message can't be edited")
            return {"ok": True, "result": {"message_id": 5}}
        with mock.patch.object(planner, "tg", side_effect=fake):
            self.cb("v|week")
        self.assertEqual([m for m, p in self.sent], ["editMessageText", "sendMessage", "answerCallbackQuery"])
        self.assertIn("Неделя", self.sent[1][1]["text"])

    def test_errors_reach_user(self):
        """Неожиданная ошибка в сообщении, фото или выгрузке — понятное «⚠️», а не тишина."""
        with mock.patch.object(planner, "handle_text", side_effect=RuntimeError("db locked")):
            planner.handle_update({"message": {"chat": {"id": U, "type": "private"}, "from": {"id": U}, "text": "кино"}})
        self.assertIn("⚠️", self.texts()[-1])
        with mock.patch.object(planner, "on_callback", side_effect=RuntimeError("boom")):
            planner.handle_update({"callback_query": {"id": "9", "from": {"id": U}, "data": "x"}})
        self.assertIn("⚠️", self.answers()[-1])
        with mock.patch.object(planner, "spawn", lambda fn, *a: fn(*a)), \
                mock.patch.object(planner.media, "photo_path", side_effect=RuntimeError("disk full")):
            planner.on_message({"chat": {"id": U, "type": "private"}, "from": {"id": U}, "photo": [{"file_id": "f"}]})
        self.assertIn("⚠️ Не получилось разобрать", self.texts()[-1])
        with mock.patch.object(planner, "_export", side_effect=OSError("network")):
            self.msg("/export")
        self.assertIn("⚠️", self.texts()[-1])
        self.msg("/nope")
        self.assertIn("⚠️", self.texts()[-1])
        planner.on_message({"chat": {"id": U, "type": "private"}, "from": {"id": U}, "sticker": {"file_id": "a"}})
        self.assertIn("не разберу", self.texts()[-1])

    def test_note_and_goal_by_text(self):
        self.msg("Заметка: купить подарок маме")
        n = self.db.notes(U)[0]
        self.assertEqual((n["text"], n["goal"]), ("Купить подарок маме", None))
        self.assertEqual(self.db.tasks(U), [])
        self.assertIn("Заметка сохранена", self.texts()[-1])
        self.cb(f"un|{n['id']}")
        self.assertEqual(self.db.notes(U), [])
        self.msg("цель на неделю: выйти на спарринг")
        g = self.db.goals(U)[0]
        self.assertEqual((g["title"], g["horizon"]), ("Выйти на спарринг", "week"))
        self.assertIn(g, self.db.current_goals(U, planner.today_of(U)))
        step = self.db.save_task(U, {"title": "Лапы", "goal": g["id"]})
        self.cb(f"g|{g['id']}")
        edits = [p for m, p in self.sent if m == "editMessageText"]
        self.assertIn("Лапы", json.dumps(edits[-1]["reply_markup"], ensure_ascii=False))
        self.cb(f"gs|{step['id']}|{g['id']}")
        self.assertEqual(self.db.task(U, step["id"])["done"], 1)
        self.assertIn("100%", self.texts()[-1])
        self.cb(f"gd|{g['id']}")
        self.assertEqual(self.db.goal(U, g["id"])["status"], "done")
        self.cb("gl")
        self.assertIn("Целей пока нет", self.texts()[-1])
        self.msg("цель жизни")  # без двоеточия — просто задача
        self.assertEqual(self.db.tasks(U)[-1]["title"], "Цель жизни")

    def test_month_notes_settings(self):
        today = planner.today_of(U)
        self.db.save_task(U, {"title": "Контрольная", "date": today.isoformat()})
        self.msg("/month")
        self.assertIn("Контрольная", self.texts()[-1])
        self.msg("/notes")
        self.assertIn("Заметок пока нет", self.texts()[-1])
        self.msg("/morning 8:05")
        self.assertEqual(self.db.user(U)["morning"], "08:05")
        self.msg("/evening off")
        self.assertEqual(self.db.user(U)["evening"], "")
        self.msg("/morning утром")
        self.assertIn("⚠️", self.texts()[-1])
        self.cb("s|e")
        self.assertEqual(self.db.user(U)["evening"], "21:30")
        self.cb("s|r")
        self.assertEqual(self.db.user(U)["remind"], 30)
        self.assertIn("за 30 мин", self.texts()[-1])

    def test_morning_quiet_when_empty(self):
        self.db.update_settings(U, {"morning": "07:30"})
        now = planner.user_now(self.db.user(U)).replace(hour=7, minute=40)
        with mock.patch.object(planner, "user_now", return_value=now):
            planner.tick()
        self.assertEqual(self.texts(), [])

    def test_unknown_media(self):
        self.media_msg(sticker={"file_id": "s"})
        self.assertIn("не разберу", self.texts()[-1])

    def run_video(self, vid=None, claude=None, **msg):
        fake = lambda m, **p: self.sent.append((m, p)) or {"ok": True, "result": {"message_id": 55}}  # noqa: E731
        with mock.patch.object(planner, "spawn", lambda fn, *a: fn(*a)), mock.patch.object(planner, "tg", side_effect=fake), \
                mock.patch.object(planner.media, "video", **vid), mock.patch.object(planner, "claude", **(claude or {})):
            self.media_msg(**(msg or {"video_note": {"file_id": "c1", "duration": 5}}))

    def test_video_frames_and_speech(self):
        seen = {}

        def fake_claude(prompt, cwd):
            seen["prompt"] = prompt
            return "Купить кроссовки\n17.10 Спарринг!"

        frames = {"side_effect": lambda tok, m, folder: {"frames": [os.path.join(folder, "frame-1.jpg"),
                                                                    os.path.join(folder, "frame-2.jpg")],
                                                         "text": "не забыть кроссовки", "seconds": 6}}
        self.run_video(frames, {"new": fake_claude}, video={"file_id": "v", "duration": 6}, caption="план")
        self.assertIn("frame-1.jpg", seen["prompt"])
        self.assertIn("frame-2.jpg", seen["prompt"])
        self.assertIn("не забыть кроссовки", seen["prompt"])
        self.assertIn("«план»", seen["prompt"])
        self.assertEqual({t["title"] for t in self.db.tasks(U)}, {"Купить кроссовки", "Спарринг"})
        self.assertIn("Смотрю видео", self.texts()[0])
        self.assertIn("Добавил 2 задачи", self.texts()[-1])
        self.assertIn("🎙 «не забыть кроссовки»", self.texts()[-1])

    def test_video_note_speech_only(self):
        """Кружок: Claude недоступен или ничего не нашёл на кадрах — речь работает как голосовое."""
        res = {"return_value": {"frames": ["/tmp/f.jpg"], "text": "завтра в 18:00 бокс", "seconds": 4}}
        self.run_video(res, {"side_effect": planner.AIError("Claude не ответил")})
        t = self.db.tasks(U)[0]
        self.assertEqual((t["title"], t["time"]), ("Бокс", "18:00"))
        self.run_video({"return_value": {"frames": ["/tmp/f.jpg"], "text": "заметка: идея для проекта", "seconds": 4}})
        self.assertEqual(self.db.notes(U)[0]["text"], "Идея для проекта")
        self.run_video({"return_value": {"frames": ["/tmp/f.jpg"], "text": "", "seconds": 2}}, {"return_value": "НЕТ"})
        self.assertIn("Не нашёл в видео задач", self.texts()[-1])
        self.run_video({"side_effect": planner.media.MediaError("видео больше 20 МБ")})
        self.assertIn("⚠️ Не получилось: видео больше 20 МБ", self.texts()[-1])

    def test_video_kinds_go_to_video(self):
        for extra in ({"video_note": {"file_id": "c"}}, {"video": {"file_id": "v"}}, {"animation": {"file_id": "a"}}):
            with mock.patch.object(planner, "spawn") as sp:
                self.media_msg(**extra)
            self.assertIs(sp.call_args[0][0], planner.on_video)

    def test_voice_note(self):
        with mock.patch.object(planner, "spawn", lambda fn, *a: fn(*a)), \
                mock.patch.object(planner.media, "voice_text", return_value="Заметка. Позвонить Саше про билеты"):
            self.media_msg(voice={"file_id": "v1"})
        self.assertEqual(self.db.notes(U)[0]["text"], "Позвонить Саше про билеты")
        self.assertEqual(self.db.tasks(U), [])

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

    def test_start_caddy_stops_other_caddy_first(self):
        calls = []
        with mock.patch.object(planner.subprocess, "run", side_effect=lambda a, **k: calls.append(a)), \
                mock.patch.object(planner.subprocess, "Popen", side_effect=lambda a, **k: calls.append(a) or mock.Mock(stderr=[])), \
                mock.patch.object(planner.time, "sleep"):
            planner.start_caddy("/bin/caddy", "/tmp/Caddyfile")
        self.assertEqual(calls[0], ["pkill", "-f", "caddy run --config"])  # лишний Caddy на 443 — до запуска своего
        self.assertEqual(calls[1][:2], ["/bin/caddy", "run"])

    def test_https_url_and_notify(self):
        conf, cands = planner.caddyfile("2.27.206.165")
        self.assertEqual(cands, ["https://2-27-206-165.sslip.io", "https://2.27.206.165"])
        self.assertIn("profile shortlived", conf)
        self.assertEqual(conf.count("encode zstd gzip"), 3)  # сжатие — приложение на имени и на IP, сайт HDP
        self.assertIn(f"reverse_proxy 127.0.0.1:{planner.PORT}", conf)
        for name, port in (("coach", 8789), ("hub", 8790), ("eye", 8791), ("kpp", 8792), ("avito", 8793), ("agent", 8794),
                           ("apps", 8795)):
            self.assertIn(f"https://{name}.2-27-206-165.sslip.io {{\n\treverse_proxy 127.0.0.1:{port}\n}}", conf)
        self.db.meta("owner", U)
        with mock.patch.object(planner, "public_url", ""):
            self.assertTrue(planner.use_url(cands[0]))
            self.assertEqual(planner.public_url, cands[0])
            planner.use_url(cands[0])  # тот же адрес — второй раз не пишет
            planner.use_url(cands[1])  # адрес сменился (IP, туннель) — кнопка новая, но сообщения нет
            planner.use_url(cands[0])
        ready = [t for t in self.texts() if "Планер готов" in t]
        self.assertEqual(len(ready), 1)
        menu = [p for m, p in self.sent if m == "setChatMenuButton"]
        self.assertEqual(menu[-1]["menu_button"]["web_app"]["url"], cands[0] + "/")

    def test_welcome_without_app(self):
        with mock.patch.object(planner, "public_url", ""):
            self.msg("/start")
        self.assertIn("пришлю кнопку", self.texts()[-1])
        self.assertFalse(self.has_app())
        self.msg("/start")
        self.assertIn("Режим: 📱 приложение", self.texts()[-1])
        self.assertTrue(self.has_app())

    # ---- 💬 чат / 📱 приложение ----
    def test_mode_choice_on_first_start(self):
        """Первый /start — «Как удобнее?»; выбор запоминается, /mode и «⚙️ Режим» меняют, /start подписан режимом."""
        self.db.meta("mode", "")
        self.msg("/start")
        self.assertIn("Как удобнее?", self.texts()[-1])
        datas = [b["callback_data"] for row in self.sent[-1][1]["reply_markup"]["inline_keyboard"] for b in row]
        self.assertEqual(datas, ["mode|chat", "mode|app"])
        self.cb("mode|chat")
        self.assertEqual(planner.ui_mode(), "chat")
        edits = [p for m, p in self.sent if m == "editMessageText"]
        self.assertIn("Режим: 💬 чат", edits[-1]["text"])
        m, p = [x for x in self.sent if x[0] == "sendMessage"][-1]  # сразу главное меню чата
        self.assertIn("Режим: 💬 чат", p["text"])
        datas = [b.get("callback_data") for row in p["reply_markup"]["inline_keyboard"] for b in row]
        for d in ("v|week", "v|month", "v|goals", "v|notes", "v|settings", "mode|ask"):
            self.assertIn(d, datas)
        self.assertFalse(self.has_app(p))
        self.msg("/mode")
        self.assertIn("Как удобнее?", self.texts()[-1])
        self.cb("mode|ask")
        self.assertIn("Как удобнее?", self.texts()[-1])
        self.cb("mode|app")
        self.assertEqual(planner.ui_mode(), "app")
        self.msg("/start")
        self.assertIn("Режим: 📱 приложение", self.texts()[-1])
        self.assertTrue(self.has_app())
        with self.assertRaises(ValueError):
            planner.set_mode("x")

    def test_chat_mode_has_no_app_buttons(self):
        """В режиме «чат» кнопки «открыть планер» нет нигде, кроме явного /app; меню чата открывает все экраны."""
        self.db.meta("mode", "chat")
        self.db.save_task(U, {"title": "Бокс", "date": planner.today_of(U).isoformat(), "time": "18:00"})
        for c in ("/start", "/today", "/week", "/month", "/goals", "/notes", "/settings", "/help", "завтра кино"):
            self.msg(c)
        for d in ("v|week", "v|month", "v|goals", "v|notes", "v|settings"):
            self.cb(d)
        self.assertFalse(any(self.has_app(p) for m, p in self.sent if m in ("sendMessage", "editMessageText")))
        self.assertIn("/tz", [p["text"] for m, p in self.sent if "Настройки" in p.get("text", "")][-1])
        self.assertIn("Неделя", self.texts()[-5] + self.texts()[-4])  # кнопки меню присылают экраны
        self.msg("/app")
        self.assertTrue(self.has_app())
        self.msg("/tz Asia/Tokyo")
        self.assertEqual(self.db.user(U)["tz"], "Asia/Tokyo")
        self.msg("/tz Nowhere/Nope")
        self.assertIn("⚠️", self.texts()[-1])

    def test_mode_from_app(self):
        """«💬 Перейти в чат» в приложении: POST /api/mode -> режим «чат», в чат приходит главное меню."""
        self.db.meta("owner", 999)
        with self.assertRaises(PermissionError):  # PUBLIC: режим общий на весь бот — чужой его не меняет
            planner.route(U, "/api/mode", {"mode": "chat"})
        self.cb("mode|chat")
        self.assertIn("владелец", self.answers()[-1])
        self.assertEqual(planner.ui_mode(), "app")
        self.db.meta("owner", U)
        r = planner.route(U, "/api/mode", {"mode": "chat"})
        self.assertEqual((r, planner.ui_mode()), ({"mode": "chat"}, "chat"))
        m, p = self.sent[-1]
        self.assertEqual((m, p["chat_id"]), ("sendMessage", U))
        self.assertIn("Режим: 💬 чат", p["text"])
        self.assertFalse(self.has_app(p))
        self.assertEqual(planner.route(U, "/api/all", {})["mode"], "chat")
        with self.assertRaises(ValueError):
            planner.route(U, "/api/mode", {"mode": "x"})
        n = len(self.sent)
        planner.route(U, "/api/mode", {"mode": "app"})
        self.assertEqual((planner.ui_mode(), len(self.sent)), ("app", n))  # обратно — молча

    def test_support_button_in_menus(self):
        with mock.patch.object(planner, "owner", lambda: U):
            self.msg("/start")
            self.msg("/help")
        for m, p in self.sent[-2:]:
            datas = [b.get("callback_data") for row in p["reply_markup"]["inline_keyboard"] for b in row]
            self.assertIn("sup|ask", datas)
        self.sent.clear()
        with mock.patch.object(planner, "owner", lambda: 999):  # чужим в PUBLIC кнопку не показываем
            self.msg("/help")
        self.assertNotIn("sup|ask", json.dumps(self.sent))

    def test_menu_keyboard(self):
        """☰ Меню-квадратик: один раз на версию при запуске, снова — по /start, кнопка открывает экран, а не задачу."""
        kb = lambda: [p for m, p in self.sent if "keyboard" in p.get("reply_markup", {})]
        planner.ensure_kb(U)
        planner.ensure_kb(U)  # второй запуск бота — уже стоит
        self.assertEqual(len(kb()), 1)
        mk = kb()[0]["reply_markup"]
        self.assertTrue(mk["one_time_keyboard"] and not mk["is_persistent"])
        texts = [b["text"] for row in mk["keyboard"] for b in row]
        for t in ("⚙️ Режим", "🛠 Поддержка", "📱 Приложение", "📅 Сегодня"):
            self.assertIn(t, texts)
        self.assertEqual(self.db.meta("kb_ver"), str(planner.KB.version))
        self.msg("/start")
        self.assertEqual(len(kb()), 2)  # /start ставит снова
        self.sent.clear()
        with mock.patch.object(planner, "claude", side_effect=AssertionError("в Claude")), \
                mock.patch.object(planner, "quick_add", side_effect=AssertionError("в задачи")):
            self.msg("🗓 Неделя")
            self.assertIn("Неделя", self.texts()[-1])
            self.msg("📅 Сегодня")
            self.msg("⚙️ Режим")
            self.assertIn("Как удобнее", self.texts()[-1])
            self.msg("📱 Приложение")
            self.assertTrue(self.has_app())
            sup = mock.Mock()
            with mock.patch.object(planner, "SUP", sup), mock.patch.object(planner, "owner", lambda: U):
                self.msg("🛠 Поддержка")
            sup.ask.assert_called_once_with(U)
        self.assertEqual(self.db.tasks(U), [])
        # в режиме ожидания поддержки кнопка меню — не текст просьбы
        sup = support.Support("123:TEST", "Планер", ["planner"], "planner-bot", allowed=lambda uid: True)
        sup.waiting[U] = {"at": time.time(), "shots": []}
        with mock.patch.object(sup, "start", side_effect=AssertionError("в поддержку")):
            planner.handle_update({"message": {"chat": {"id": U, "type": "private"}, "from": {"id": U},
                                               "text": "🎯 Цели"}}, sup)
        self.assertNotIn(U, sup.waiting)

    def test_menu_keyboard_needs_owner(self):
        """Без владельца меню при запуске не шлётся (некому)."""
        with mock.patch.object(planner, "ALLOWED", set()):
            planner.kb_on_start()
            self.assertEqual(self.sent, [])
            self.db.meta("owner", U)
            planner.kb_on_start()
            planner.kb_on_start()
        self.assertEqual([(m, p["chat_id"]) for m, p in self.sent], [("sendMessage", U)])

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
        self.assertLess(body.index('"kit.css'), body.index('"app.css'))  # общий вид — до своего
        self.assertLess(body.index('"kit.js'), body.index('"app.js'))
        self.assertEqual(self.get("/app.css")[0], 200)
        code, kit = self.get("/kit.css")  # ссылка на common/webapp/kit.css
        self.assertEqual(code, 200)
        self.assertIn("--accent", kit)
        self.assertEqual(self.get("/kit.js")[0], 200)
        self.assertEqual(self.get("/../planner.py")[0], 404)
        self.assertEqual(self.get("/api/all")[0], 401)
        with mock.patch.object(planner, "PUBLIC", True):
            code, body = self.get("/api/all", {"X-Init-Data": signed({"id": 42, "first_name": "A"})})
            self.assertEqual(code, 200)
            self.assertIn("today", json.loads(body))
            code, body = self.get("/api/task", {"X-Init-Data": signed({"id": 42}), "Content-Type": "application/json"},
                                  json.dumps({"title": ""}).encode())
            self.assertEqual(code, 400)
            for raw in (b"[1]", b"null", b'{"id": null}', b'{"title": "x", "date": 5}'):  # не объект, не тот тип — 400
                self.assertEqual(self.get("/api/task", {"X-Init-Data": signed({"id": 42})}, raw)[0], 400, raw)
        with mock.patch.object(planner, "PUBLIC", False), mock.patch.object(planner, "ALLOWED", {1}):
            self.assertEqual(self.get("/api/all", {"X-Init-Data": signed({"id": 42})})[0], 403)

    def test_support_from_app(self):
        sup = support.Support.__new__(support.Support)  # без потоков и Telegram; start — заглушка
        sup.allowed, sup.media_dir, calls = planner.can_support, tempfile.mkdtemp(), []
        sup.start = lambda chat, text, shots=None: calls.append((chat, text, shots))
        post = lambda uid, body: self.get("/api/support", {"X-Init-Data": signed({"id": uid}),
                                                          "Content-Type": "application/json"}, json.dumps(body).encode())
        with mock.patch.object(planner, "PUBLIC", True), mock.patch.object(planner, "owner", lambda: 42):
            self.assertEqual(post(42, {"text": "x"})[0], 400)  # поддержка ещё не запущена
            with mock.patch.object(planner, "SUP", sup):
                code, body = post(42, {"text": "неделя пустая", "photo": "iVBORw0KGgo="})
                self.assertEqual((code, json.loads(body)), (200, {"ok": True}))
                self.assertEqual(calls[-1][0], 42)
                self.assertIn("неделя пустая", calls[-1][1])
                self.assertTrue(os.path.exists(calls[-1][2][0]))
                self.assertEqual(post(43, {"text": "чужой"})[0], 403)  # в PUBLIC — не владелец
                self.assertEqual(post(42, {"text": " "})[0], 400)
        self.assertEqual(len(calls), 1)

    def test_audit_http_limits_and_errors(self):
        """Аудит: GET меняет ничего, большое тело — 413 (кроме скриншота поддержки), ошибки — человеческим языком,
        траверсал в статике не работает, подпись без id — 401."""
        hdr = {"X-Init-Data": signed({"id": 42, "first_name": "A"}), "Content-Type": "application/json"}
        with mock.patch.object(planner, "PUBLIC", True), mock.patch.object(planner, "owner", lambda: 42):
            self.assertEqual(self.get("/api/task", hdr)[0], 405)  # GET только для /api/all
            self.assertEqual(self.get("/api/all", hdr)[0], 200)
            big = json.dumps({"title": "x" * 1_100_000}).encode()
            self.assertEqual(self.get("/api/task", hdr, big)[0], 413)
            # скриншот в «Поддержке» — до 16 МБ (в планер он доезжает; здесь поддержка не запущена -> 400, не 413)
            code, body = self.get("/api/support", hdr, json.dumps({"text": "x", "photo": "A" * 2_000_000}).encode())
            self.assertEqual(code, 400)
            self.assertEqual(self.get("/api/task", hdr, b"[1, 2]")[0], 400)  # не словарь
            code, body = self.get("/api/task/toggle", hdr, b"{}")
            self.assertEqual(code, 400)
            self.assertNotIn("'id'", body)
            self.assertIn("Неверные данные", json.loads(body)["error"])
            code, body = self.get("/api/task", hdr, json.dumps({"title": "Ок", "date": "вчера"}).encode())
            self.assertEqual((code, json.loads(body)["error"]), (400, "Неверная дата"))
            code, body = self.get("/api/task", hdr, json.dumps({"title": "Ок", "time": "25:99"}).encode())
            self.assertIn("Неверное время", json.loads(body)["error"])
            code, body = self.get("/api/settings", hdr, json.dumps({"tz": "Nowhere/Nope"}).encode())
            self.assertEqual((code, json.loads(body)["error"]), (400, "Такого часового пояса нет"))
        self.assertEqual(self.get("/../planner.py")[0], 404)
        self.assertEqual(self.get("/%2e%2e/planner.py")[0], 404)
        self.assertEqual(self.get("/api/all", {"X-Init-Data": signed({"first_name": "A"})})[0], 401)  # подпись верна, id нет

    def test_asset_version_skips_dirs(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "sub"))
        with open(os.path.join(d, "a.js"), "w") as f:
            f.write("1")
        with mock.patch.object(planner, "WEB", d):
            self.assertEqual(len(planner.asset_version()), 10)


class SchedulerAuditTest(unittest.TestCase):
    """Напоминания: сбой одного не рушит остальных, временная ошибка не теряет напоминание, постоянная не зацикливает."""
    def setUp(self):
        self.db = store.Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        self.addCleanup(self.db.close)
        self.sent = []
        self.fail = None
        self.now = datetime.datetime(2026, 10, 9, 17, 50, tzinfo=datetime.timezone.utc)

        def send(chat, text, buttons=None, **kw):
            if self.fail:
                raise self.fail
            self.sent.append((chat, text))
        self.patches = [mock.patch.object(planner, "db", self.db), mock.patch.object(planner, "PUBLIC", True),
                        mock.patch.object(planner, "send", side_effect=send),
                        mock.patch.object(planner, "user_now", side_effect=lambda u: self.now.astimezone(datetime.timezone.utc))]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])
        self.db.update_settings(U, {"tz": "UTC", "morning": "", "evening": ""})
        self.db.save_task(U, {"title": "Бокс", "date": "2026-10-09", "time": "18:00", "remind": 15})

    def test_transient_failure_is_retried(self):
        self.fail = OSError("сеть")
        planner.tick()
        self.assertEqual(self.sent, [])
        self.fail = None
        planner.tick()  # следующий проход — напоминание уходит
        self.assertEqual(len(self.sent), 1)
        planner.tick()
        self.assertEqual(len(self.sent), 1)  # и только раз

    def test_permanent_failure_not_retried_and_other_users_ok(self):
        self.db.user(8)
        self.db.update_settings(8, {"tz": "UTC", "morning": "", "evening": ""})
        self.db.save_task(8, {"title": "Йога", "date": "2026-10-09", "time": "18:00", "remind": 15})
        calls = []

        def send(chat, text, buttons=None, **kw):
            calls.append(chat)
            if chat == U:
                raise planner.TgError("sendMessage", 403, "bot was blocked by the user")
        with mock.patch.object(planner, "send", side_effect=send):
            planner.tick()
            planner.tick()
        self.assertEqual(calls.count(U), 1)  # заблокировал бота — не долбим каждые 20 с
        self.assertEqual(calls.count(8), 1)  # а второй пользователь своё получил

    def test_setup_caddy_writes_config_atomically(self):
        d = tempfile.mkdtemp()
        proc = mock.Mock()
        with mock.patch.object(planner, "CADDY_DIR", d), mock.patch.object(planner, "server_ip", return_value="1.2.3.4"), \
                mock.patch.object(planner, "caddy_bin", return_value="/bin/caddy"), \
                mock.patch.object(planner, "start_caddy", return_value=proc) as sc:
            got = planner.setup_caddy()
        self.assertEqual(got, (proc, ["https://1-2-3-4.sslip.io", "https://1.2.3.4"], "/bin/caddy", os.path.join(d, "Caddyfile")))
        self.assertTrue(os.path.exists(os.path.join(d, "Caddyfile")))
        self.assertFalse(os.path.exists(os.path.join(d, "Caddyfile.new")))
        with mock.patch.object(planner, "server_ip", return_value=None):
            with self.assertRaises(RuntimeError):
                planner.setup_caddy()  # IP не узнали — https() повторит через минуту, а не сдастся навсегда

    def test_prune_sent_and_unmark(self):
        self.assertTrue(self.db.mark_sent("a"))
        self.assertFalse(self.db.mark_sent("a"))
        self.db.unmark_sent("a")
        self.assertTrue(self.db.mark_sent("a"))
        self.db.x("UPDATE sent SET at='2020-01-01T00:00:00+00:00' WHERE key='a'")
        self.db.mark_sent("b")
        self.assertEqual(self.db.prune_sent(60), 1)
        self.assertTrue(self.db.mark_sent("a"))
        self.assertFalse(self.db.mark_sent("b"))

    def test_repeat_without_date_uses_user_today(self):
        t = self.db.save_task(U, {"title": "Зарядка", "repeat": "1234567"}, datetime.date(2026, 10, 10))
        self.assertEqual(t["date"], "2026-10-10")
        g = self.db.save_goal(U, {"title": "Цель", "horizon": "month"}, datetime.date(2026, 10, 31))
        self.assertEqual(g["start"], "2026-10-01")

    def test_claude_env_has_no_secrets(self):
        env = {"PLANNER_TOKEN": "x", "HUB_KEY": "k", "TG_AGENT_TOKEN": "t", "ANTHROPIC_API_KEY": "a", "CLAUDE_CODE_OAUTH_TOKEN": "c",
               "PATH": "/bin", "HOME": "/root"}
        with mock.patch.dict(os.environ, env, clear=True):
            got = planner.clean_env()
        self.assertEqual(sorted(got), ["ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "HOME", "PATH"])

    def test_leap_day_parse_and_long_cards(self):
        r = parse.parse("29.02 день", datetime.date(2027, 3, 20))  # 29 февраля 2027 нет — не падаем
        self.assertEqual(r["title"], "29.02 день")
        r = parse.parse("29.02.2028 день", datetime.date(2027, 3, 20))
        self.assertEqual(r["date"], datetime.date(2028, 2, 29))
        today = datetime.date(2026, 10, 9)
        tasks = [self.db.save_task(U, {"title": "Очень длинное название задачи " * 8, "date": "2026-10-09"}) for _ in range(20)]
        sent = []
        with mock.patch.object(planner, "replace", side_effect=lambda chat, mid, text, buttons=None: sent.append(text)):
            planner.report_tasks(U, None, "📷", tasks, today)
        self.assertLess(len(sent[0]), 4096)
        self.assertIn("и ещё 5", sent[0])


if __name__ == "__main__":
    unittest.main()
