"""Кожа: модель ухода, расписание процедур, отметки, серия, дневник кожи, «Заполнить списком», лимиты, контекст тренера,
связь с режимом (напоминание об отбое) и персона тренера «по всему».
usage: python3 coach/test_skin.py
"""
import datetime
import os
import sys
import tempfile
import unittest
from unittest import mock
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_coach as T  # noqa: E402  (ставит окружение и поддельный WHOOP)
import ai  # noqa: E402
import coach  # noqa: E402
import f_skin as K  # noqa: E402

D = datetime.date
TODAY = D(2026, 11, 4)  # среда
ROUTINE = {"am": [{"title": "Умыться", "product": "мягкий гель"}, {"title": "Крем"}],
           "pm": [{"title": "Умыться"}, {"title": "Крем на ночь"}],
           "extra": [{"title": "Маска", "days": [1, 5]}, {"title": "Скраб", "weekly": 1}]}


class ParseTest(unittest.TestCase):
    def test_items(self):
        self.assertEqual(K.parse_item("Умыться гелем утром"), ("am", {"title": "Умыться гелем", "product": ""}))
        self.assertEqual(K.parse_item("Крем на ночь"), ("pm", {"title": "Крем", "product": ""}))
        self.assertEqual(K.parse_item("- Умываться утром и вечером"), ("both", {"title": "Умываться", "product": ""}))
        self.assertEqual(K.parse_item("Тоник 2 раза в день")[0], "both")
        self.assertEqual(K.parse_item("Умыться — гель для умывания"), ("both", {"title": "Умыться", "product": "Гель для умывания"}))
        self.assertEqual(K.parse_item("Крем (увлажняющий)", "pm"), ("pm", {"title": "Крем", "product": "Увлажняющий"}))
        self.assertIsNone(K.parse_item("   "))
        self.assertIsNone(K.parse_item("— !"))

    def test_schedule(self):
        self.assertEqual(K.parse_item("Маска 2 раза в неделю: вт, сб"),
                         ("extra", {"title": "Маска", "product": "", "days": [1, 5], "weekly": None}))
        self.assertEqual(K.parse_item("Скраб раз в неделю"), ("extra", {"title": "Скраб", "product": "", "days": [], "weekly": 1}))
        self.assertEqual(K.parse_item("Маска 2 раза в неделю")[1]["weekly"], 2)
        self.assertEqual(K.parse_item("Патчи по субботам")[1]["days"], [5])
        self.assertEqual(K.parse_item("Пилинг через день")[1]["weekly"], 3)
        self.assertEqual(K.parse_item("Маска", "extra"), ("extra", {"title": "Маска", "product": "", "days": [], "weekly": 1}))

    def test_text_sections_and_notes(self):
        r = K.parse_text("Утро: умыться гелем, крем\nВечер:\n- умыться\n- крем на ночь\n"
                         "Маска 2 раза в неделю: вт, сб\nСкраб раз в неделю\n"
                         "Тип кожи: комбинированная\nРеагирует на спиртовые лосьоны\nНе подходит: скраб с косточками\n\n")
        self.assertEqual([x["title"] for x in r["am"]], ["Умыться гелем", "Крем"])
        self.assertEqual([x["title"] for x in r["pm"]], ["Умыться", "Крем"])
        self.assertEqual([(x["title"], x["days"], x["weekly"]) for x in r["extra"]], [("Маска", [1, 5], None), ("Скраб", [], 1)])
        self.assertEqual(r["notes"], {"type": "комбинированная", "react": "Реагирует на спиртовые лосьоны",
                                      "avoid": "скраб с косточками"})
        r = K.parse_text("Умываться утром и вечером\nЗаметки:\nсухая на щеках")
        self.assertEqual((len(r["am"]), len(r["pm"]), r["notes"]), (1, 1, {"type": "сухая на щеках"}))
        r = K.parse_text("Дополнительно:\nмаска\nУтром: крем + SPF")
        self.assertEqual(([x["title"] for x in r["extra"]], [x["title"] for x in r["am"]]), (["Маска"], ["Крем", "SPF"]))


class CleanTest(unittest.TestCase):
    def test_bad(self):
        for part, bad in (("am", {"title": ""}), ("am", {"title": "a" * 81}), ("am", {"title": "x", "product": "p" * 81}),
                          ("extra", {"title": "x", "days": [7]}), ("extra", {"title": "x", "weekly": 9}),
                          ("extra", {"title": "x", "days": [0, 1, 2, 3, 4, 5, 6]}), ("am", "строка")):
            with self.assertRaises(ValueError, msg=bad):
                K.clean_step(bad, part)
        self.assertEqual(K.clean_step({"title": "x"}, "extra")["weekly"], 1)
        self.assertEqual(K.sched({"days": [1, 5]}), "вт, сб")
        self.assertEqual(K.sched({"days": [], "weekly": 2}), "2 раза в неделю")
        self.assertEqual(K.sched({"days": [], "weekly": 5}), "5 раз в неделю")

    def test_same(self):
        self.assertTrue(K.same("Крем на лицо", "Крем"))
        self.assertTrue(K.same("Крем на лицо", "Крем на ночь"))
        self.assertTrue(K.same("умыться", "Умыться"))
        self.assertFalse(K.same("Душ", "Умыться"))
        self.assertFalse(K.same("Телефон в сторону", "Крем"))


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, _ = T.seeded(os.path.join(tempfile.mkdtemp(), "skin.db"))

    def setUp(self):
        coach.db = self.db
        self.db.x("DELETE FROM meta WHERE key LIKE 'skin:%' OR key LIKE 'regime:%' OR key LIKE 'habits:%'")
        self.db.x("DELETE FROM sent")

    def call(self, path, body=None, day=TODAY):
        now = datetime.datetime.combine(day, datetime.time(21, 10), tzinfo=ZoneInfo("Europe/Moscow"))
        with mock.patch.object(coach, "user_now", lambda u: now), mock.patch.object(coach, "today_of", lambda uid: day):
            return coach.route(T.U, path, body or {})

    def setup_routine(self, day=TODAY):
        return self.call("/api/skin/save", {"routine": ROUTINE}, day)

    def ids(self, r, part):
        return [x["id"] for x in r["routine"][part]]

    def test_empty(self):
        r = self.call("/api/skin")
        self.assertEqual((r["has"], r["streak"], r["today"]["am_total"], len(r["diary"])), (False, 0, 0, 30))
        self.assertEqual(r["diary"][-1]["day"], TODAY.isoformat())
        self.assertEqual(K.context_lines(coach, T.U, TODAY), [])
        self.assertNotIn("КОЖ", coach.context(T.U, TODAY)[0])
        self.assertEqual(K.remind_line(self.db, T.U, TODAY), "")
        ext = self.call("/api/all")["ext"]["skin"]
        self.assertEqual((ext["has"], ext["streak"]), (False, 0))

    def test_save_and_schedule(self):
        r = self.setup_routine()
        self.assertTrue(r["has"])
        self.assertEqual([x["title"] for x in r["today"]["am"]], ["Умыться", "Крем"])
        self.assertEqual(r["routine"]["am"][0]["product"], "мягкий гель")
        self.assertEqual(r["routine"]["extra"][0]["sched"], "вт, сб")
        # среда: маска (вт, сб) — не сегодня; скраб 1 раз в неделю — ещё не делал, значит сегодня
        self.assertEqual([x["title"] for x in r["today"]["extra"]], ["Скраб"])
        self.assertEqual([x["title"] for x in r["today"]["later"]], ["Маска"])
        r2 = self.call("/api/skin", day=D(2026, 11, 7))  # суббота — маска по плану
        self.assertIn("Маска", [x["title"] for x in r2["today"]["extra"]])
        # скраб сделан во вторник — в среду уже не «по плану»
        sid = self.ids(r, "extra")[1]
        self.call("/api/skin/mark", {"id": sid, "done": True, "day": "2026-11-03"})
        r = self.call("/api/skin")
        self.assertEqual([x["title"] for x in r["today"]["later"]], ["Маска", "Скраб"])
        self.assertEqual(r["today"]["later"][1]["week_done"], 1)
        # правка: id сохраняется, новый шаг — новый id, чужой id не принимаем
        am = r["routine"]["am"]
        r = self.call("/api/skin/save", {"routine": {"am": [am[1], {"title": "SPF", "id": "x999"}, am[1]]}})
        self.assertEqual(self.ids(r, "am")[0], am[1]["id"])
        self.assertEqual(len(set(self.ids(r, "am"))), 3)
        self.assertEqual(len(r["routine"]["pm"]), 2)  # не прислал — не тронуто
        r = self.call("/api/skin/save", {"notes": {"type": "жирная в Т-зоне"}})
        self.assertEqual(r["notes"], {"type": "жирная в Т-зоне", "react": "", "avoid": ""})
        r = self.call("/api/skin/save", {"notes": {"avoid": "спирт"}})
        self.assertEqual((r["notes"]["type"], r["notes"]["avoid"]), ("жирная в Т-зоне", "спирт"))

    def test_limits_and_bad(self):
        self.setup_routine()
        for bad in ({}, {"routine": []}, {"routine": {}}, {"routine": {"am": "x"}},
                    {"routine": {"am": [{"title": "x"}] * (K.MAX_STEPS + 1)}},
                    {"routine": {"extra": [{"title": "x"}] * (K.MAX_EXTRA + 1)}},
                    {"routine": {"pm": [{"title": ""}]}}, {"notes": "x"}, {"notes": {"type": "x" * 301}}):
            with self.assertRaises(ValueError, msg=bad):
                self.call("/api/skin/save", bad)
        r = self.call("/api/skin")
        aid = self.ids(r, "am")[0]
        for bad in ({}, {"id": aid}, {"id": "nope", "done": True}, {"id": aid, "done": True, "day": "2026-10-27"},
                    {"id": aid, "done": True, "day": "2026-11-05"}, {"id": aid, "done": True, "day": "вчера"}):
            with self.assertRaises(ValueError, msg=bad):
                self.call("/api/skin/mark", bad)
        for bad in ({}, {"rating": 6}, {"rating": 0.4}, {"rating": "плохо"}, {"rating": True}, {"note": "x" * 201},
                    {"rating": 3, "day": "2026-10-01"}):
            with self.assertRaises(ValueError, msg=bad):
                self.call("/api/skin/rate", bad)
        self.assertEqual(self.call("/api/skin", [1, 2])["has"], True)  # не объект — как пустое тело
        self.assertIsNone(K.route(coach, T.U, "/api/other", {}, TODAY))
        with self.assertRaises(KeyError):
            self.call("/api/skin/nope")

    def test_marks_streak_and_flags(self):
        r = self.setup_routine()
        am, pm = self.ids(r, "am"), self.ids(r, "pm")
        self.call("/api/skin/mark", {"id": am[0], "done": True})
        r = self.call("/api/skin/mark", {"id": am[1], "done": True})
        self.assertEqual((r["today"]["am_done"], r["today"]["pm_done"], r["today"]["ok"], r["streak"]), (2, 0, False, 0))
        for i in pm:
            r = self.call("/api/skin/mark", {"id": i, "done": True})
        self.assertEqual((r["today"]["ok"], r["streak"], r["best"]), (True, 1, 1))
        # вчера и позавчера — целиком; уход «старше», серия 3
        for back in ("2026-11-03", "2026-11-02"):
            for i in am + pm:
                r = self.call("/api/skin/mark", {"id": i, "done": True, "day": back})
        self.assertEqual((r["streak"], r["best"]), (3, 3))
        # сегодня сняли отметку — серия не обрывается (день ещё идёт), но сегодня не в счёт
        r = self.call("/api/skin/mark", {"id": pm[0], "done": False})
        self.assertEqual((r["today"]["ok"], r["streak"]), (False, 2))
        # завтра новый день: вчера (сегодня) не закрыт — серия 0, лучшая 3
        r = self.call("/api/skin", day=D(2026, 11, 5))
        self.assertEqual((r["streak"], r["best"]), (0, 2))
        # новый вечерний шаг не переписывает прошлые дни
        r = self.call("/api/skin/save", {"routine": {"pm": r["routine"]["pm"] + [{"title": "Бальзам для губ"}]}}, D(2026, 11, 5))
        self.assertEqual(r["best"], 2)
        self.assertTrue(r["diary"][-3]["ok"])

    def test_diary_and_stats(self):
        r = self.setup_routine()
        pm = self.ids(r, "pm")
        self.assertIn("Оценивай кожу", r["stats"]["summary"])
        r = self.call("/api/skin/rate", {"rating": 4, "note": "  чуть  суше  "})
        self.assertEqual((r["today"]["rating"], r["today"]["word"], r["today"]["note"]), (4, "хорошо", "чуть суше"))
        r = self.call("/api/skin/rate", {"note": ""})
        self.assertEqual((r["today"]["rating"], r["today"]["note"]), (4, ""))
        r = self.call("/api/skin/rate", {"rating": None})
        self.assertIsNone(r["today"]["rating"])
        # 14 дней: неделю назад 2, последняя неделя 4; вечерний уход — в дни с 4
        for back in range(1, 7):
            d = (TODAY - datetime.timedelta(days=back)).isoformat()
            self.call("/api/skin/rate", {"rating": 4, "day": d})
            for i in pm:
                self.call("/api/skin/mark", {"id": i, "done": True, "day": d})
        for back in range(7, 14):  # дальше недели назад через API нельзя — пишем день напрямую
            K.put_doc(self.db, T.U, (TODAY - datetime.timedelta(days=back)).isoformat(), {"m": [], "r": 2, "t": "", "a": False, "p": False})
        st = K.load(self.db, T.U)
        st["created"] = "2026-10-01"
        K.store(self.db, T.U, st)
        r = self.call("/api/skin")
        s = r["stats"]
        self.assertEqual((s["avg7"], s["prev7"], s["trend"], s["rated"]), (4.0, 2.0, "лучше", 13))
        self.assertEqual(s["link"], {"with": 4.0, "without": 2.0})
        self.assertIn("Последняя неделя лучше прошлой", s["summary"])
        self.assertIn("Когда вечерний уход сделан, кожа в среднем 4, без него — 2", s["summary"])
        self.assertEqual([x["r"] for x in r["diary"][-3:]], [4, 4, None])
        self.assertEqual(len(r["diary"]), 30)

    def test_import(self):
        text = "Утро: умыться гелем, крем\nВечер:\nумыться\nкрем на ночь\nМаска 2 раза в неделю: вт, сб\nТип кожи: нормальная"
        p = self.call("/api/skin/import", {"text": text, "preview": True})
        self.assertEqual([x["key"] for x in p["am"]], ["am:0", "am:1"])
        self.assertEqual(p["extra"][0]["sched"], "вт, сб")
        self.assertFalse(self.call("/api/skin")["has"])  # предпросмотр ничего не сохраняет
        r = self.call("/api/skin/import", {"text": text, "drop": ["am:1"]})
        self.assertEqual((r["added"], r["notes"]["type"]), ({"am": 1, "pm": 2, "extra": 1}, "нормальная"))
        self.assertEqual([x["title"] for x in r["routine"]["am"]], ["Умыться гелем"])
        r = self.call("/api/skin/import", {"text": "Утро: умыться гелем, SPF"})  # повтор — пропуск
        self.assertEqual((r["dup"], [x["title"] for x in r["routine"]["am"]]), (["Умыться гелем"], ["Умыться гелем", "SPF"]))
        with self.assertRaises(ValueError):
            self.call("/api/skin/import", {"text": "Утро: умыться гелем"})  # нечего добавить
        for bad in ("", "  \n ", "x" * 4001, None):
            with self.assertRaises(ValueError):
                self.call("/api/skin/import", {"text": bad})
        many = "\n".join(f"Шаг номер {i} утром" for i in range(20))
        p = self.call("/api/skin/import", {"text": many, "preview": True})
        self.assertEqual((len(p["am"]), len(p["over"])), (K.MAX_STEPS - 2, 20 - (K.MAX_STEPS - 2)))

    def test_context_reminder_and_extra(self):
        r = self.setup_routine()
        self.call("/api/skin/save", {"notes": {"type": "комбинированная", "avoid": "спиртовые лосьоны"}})
        self.call("/api/skin/mark", {"id": self.ids(r, "am")[0], "done": True})
        self.call("/api/skin/mark", {"id": self.ids(r, "pm")[0], "done": True})
        self.call("/api/skin/rate", {"rating": 3, "note": "пара прыщиков"})
        with mock.patch.object(coach, "today_of", lambda uid: TODAY):
            ctx = coach.context(T.U, TODAY)[0]
        self.assertIn("УХОД ЗА КОЖЕЙ (настроил сам): утро — Умыться (мягкий гель), Крем; вечер — Умыться, Крем на ночь; "
                      "процедуры — Маска (вт, сб), Скраб (1 раз в неделю)", ctx)
        self.assertIn("Про кожу (написал сам): тип и особенности — комбинированная; что не подходит — спиртовые лосьоны", ctx)
        self.assertIn("утренний уход 1/2; вечерний 1/2 (осталось: Крем на ночь); скраб по плану сегодня — не сделано", ctx)
        self.assertIn("сегодня 3 — нормально («пара прыщиков»)", ctx)
        self.assertIn("к дерматологу и сказать родителям", ctx)
        ext = self.call("/api/all")["ext"]["skin"]
        self.assertEqual((ext["has"], ext["today"]["am_done"], ext["today"]["rating"], ext["notes"]), (True, 1, 3, True))
        self.assertEqual(K.remind_line(self.db, T.U, TODAY), "Уход на ночь: Крем на ночь, Скраб")
        # напоминание об отбое: уход — из «Кожи», одинаковые пункты режима («Крем на лицо») не повторяются
        self.call("/api/regime/save", {"cfg": {"evening": ["Душ", "Крем на лицо", "Телефон в сторону"]}})
        sent = []
        with mock.patch.object(coach, "send", lambda chat, text, buttons=None, **kw: sent.append(text)):
            coach.regime_remind(T.U, TODAY)
        self.assertIn("🧴 Уход на ночь: Крем на ночь, Скраб", sent[0])
        self.assertNotIn("Крем на лицо", sent[0])
        self.assertIn("Душ", sent[0])
        self.assertLessEqual(sent[0].split("<blockquote>")[1].count("\n") + 1, 5)

    def test_coach_sees_everything(self):
        """Тренер (вкладка «Тренер» и чат бота) получает весь контекст: WHOOP, цели, режим, привычки, кожу, время, разговор."""
        self.setup_routine()
        self.call("/api/habits/save", {"habit": {"title": "Зарядка"}})
        self.call("/api/regime/save", {"cfg": {"bed": "22:30"}})
        self.db.add_goal(T.U, "ложиться до 23:00")
        with mock.patch.object(ai, "claude", return_value="Ок") as c:
            r = self.call("/api/ask", {"text": "Что делать с кожей?"})
        p = c.call_args[0][0]
        for want in ("ПРОФИЛЬ", "ЦЕЛИ", "восстановление", "РЕЖИМ НА СЕГОДНЯ", "ПРИВЫЧКИ И ЦЕЛИ", "УХОД ЗА КОЖЕЙ",
                     "СЕЙЧАС: 21:10, среда", "ВОПРОС: Что делать с кожей?", "Ответ для приложения"):
            self.assertIn(want, p)
        self.assertEqual(r["answer"], "Ок")
        with mock.patch.object(ai, "claude", return_value="Ок") as c:
            self.call("/api/ask", {"text": "А ещё?"})
        self.assertIn("НЕДАВНИЙ РАЗГОВОР:\nЯ: Что делать с кожей?\nТренер: Ок", c.call_args[0][0])
        # чат бота — тот же контекст, но коротко
        sent = []
        with mock.patch.object(ai, "claude", return_value="Коротко") as c, \
                mock.patch.object(coach, "send", lambda chat, text, buttons=None, **kw: sent.append(text)), \
                mock.patch.object(coach, "typing", lambda chat: None):
            coach.answer(T.U, T.U, "Как не волноваться перед контрольной?")
        p = c.call_args[0][0]
        self.assertIn("УХОД ЗА КОЖЕЙ", p)
        self.assertIn("Ответ для чата: 2–5 коротких строк", p)

    def test_persona_covers_everything_safely(self):
        p = ai.persona()
        for want in ("о чём угодно", "кож", "учёб", "настроени", "рецептурных", "дерматолог", "родителям",
                     "8-800-2000-122", "без терминов" if "без терминов" in p else "простыми словами"):
            self.assertIn(want, p)


if __name__ == "__main__":
    unittest.main(verbosity=1)
