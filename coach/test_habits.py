"""Привычки и цели на день: модель, отметки, серии, weekly, дни, пропуски, цели и перенос, импорт списком, лимиты, API.
usage: python3 coach/test_habits.py
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
import coach  # noqa: E402
import f_habits as H  # noqa: E402

D = datetime.date
TODAY = D(2026, 11, 4)  # среда
ALL = [0, 1, 2, 3, 4, 5, 6]


class ParseTest(unittest.TestCase):
    def p(self, line):
        h = H.parse_line(line)
        return {k: h[k] for k in ("title", "kind", "target", "unit", "days", "weekly", "time")}

    def test_count_with_units(self):
        self.assertEqual(self.p("Пить 8 стаканов воды"), {"title": "Пить 8 стаканов воды", "kind": "count", "target": 8,
                                                          "unit": "стаканов", "days": ALL, "weekly": None, "time": None})
        self.assertEqual(self.p("10 000 шагов")["target"], 10000)
        self.assertEqual(self.p("10000 шагов каждый день")["title"], "10000 шагов")
        self.assertEqual(self.p("Читать 20 мин")["unit"], "мин")
        self.assertEqual(self.p("Медитация 10 минут")["unit"], "мин")
        self.assertEqual(self.p("2 л воды")[("target")], 2000)  # литры — в мл
        self.assertEqual(self.p("2 л воды")["unit"], "мл")
        self.assertEqual((self.p("Отжимания 50 раз")["target"], self.p("Отжимания 50 раз")["unit"]), (50, ""))
        self.assertEqual(self.p("Пробежка 5 км")["unit"], "км")
        self.assertEqual(self.p("Чтение 1 глава")["kind"], "check")  # одна — просто отметка

    def test_weekly(self):
        self.assertEqual(self.p("Английский 3 раза в неделю")["weekly"], 3)
        self.assertEqual(self.p("Английский 3 раза в неделю")["title"], "Английский")
        self.assertEqual(self.p("Спортзал 2 р/нед")["weekly"], 2)
        self.assertEqual(self.p("Бассейн 2/нед")["weekly"], 2)
        self.assertEqual(self.p("Баня раз в неделю")["weekly"], 1)
        self.assertEqual(self.p("Звонить бабушке дважды в неделю")["weekly"], 2)
        self.assertEqual(self.p("Зарядка 7 раз в неделю")["weekly"], None)  # 7 — это каждый день

    def test_days(self):
        self.assertEqual(self.p("Бег пн ср пт")["days"], [0, 2, 4])
        self.assertEqual(self.p("Тренировка вт, чт, сб")["days"], [1, 3, 5])
        self.assertEqual(self.p("Растяжка по понедельникам и средам")["days"], [0, 2])
        self.assertEqual(self.p("Прогулка пн-пт")["days"], [0, 1, 2, 3, 4])
        self.assertEqual(self.p("Уборка сб-вс")["days"], [5, 6])
        self.assertEqual(self.p("Отжимания по будням")["days"], [0, 1, 2, 3, 4])
        self.assertEqual(self.p("Убраться в комнате по выходным"), {"title": "Убраться в комнате", "kind": "check", "target": 1,
                                                                    "unit": "", "days": [5, 6], "weekly": None, "time": None})
        self.assertEqual(self.p("Зарядка каждый день")["days"], ALL)
        self.assertEqual(self.p("Пробежка 5 км по субботам")["days"], [5])

    def test_time(self):
        self.assertEqual(self.p("Зарядка утром")["time"], "утро")
        self.assertEqual(self.p("Зарядка утром")["title"], "Зарядка")
        self.assertEqual(self.p("Читать перед сном")["time"], "вечер")
        self.assertEqual(self.p("Прогулка днём")["time"], "день")
        self.assertEqual(self.p("Бег пн ср пт в 07:00"), {"title": "Бег", "kind": "check", "target": 1, "unit": "",
                                                          "days": [0, 2, 4], "weekly": None, "time": "07:00"})
        self.assertEqual(self.p("Дневник вечером в 22:30")["time"], "22:30")
        self.assertEqual(self.p("Дневник вечером в 22:30")["title"], "Дневник")
        self.assertEqual(self.p("Лечь до 23:00")["title"], "Лечь до 23:00")  # «до» — оставляем, как написано
        self.assertEqual(self.p("Лечь до 23:00")["time"], "23:00")

    def test_bullets_icons_and_empty(self):
        self.assertEqual(self.p("- Медитация")["title"], "Медитация")
        self.assertEqual(self.p("1. Витамины утром")["title"], "Витамины")
        self.assertEqual(self.p("• читать книгу")["title"], "Читать книгу")
        self.assertEqual(self.p("📚 Читать 30 стр")["title"], "Читать 30 стр")
        self.assertEqual(H.parse_line("Пить 8 стаканов воды")["icon"], "water")
        self.assertEqual(H.parse_line("Читать книгу")["icon"], "book")
        self.assertEqual(H.parse_line("Что-то своё")["icon"], "check")
        self.assertIsNone(H.parse_line("  — "))
        self.assertEqual(len(H.parse_line("а" * 200)["title"]), H.MAX_TEXT)


class CalcTest(unittest.TestCase):
    """Модель без базы: история — словарь дней."""

    def calc(self, items, hist):
        items = [H.clean_habit(h) | {"id": h["id"], "created": h.get("created", "2026-09-01")} for h in items]
        return H.Calc(items, {d: H._clean_doc(v) for d, v in hist.items()}, TODAY), items

    def day(self, n):
        return (TODAY - datetime.timedelta(days=n)).isoformat()

    def test_daily_streak_today_not_breaking(self):
        hist = {self.day(i): {"v": {"a": 1}} for i in range(1, 6)}  # вчера и 4 дня до — сделано, сегодня ещё нет
        c, (h,) = self.calc([{"id": "a", "title": "Зарядка"}], hist)
        self.assertEqual(c.streak(h), 5)
        hist[self.day(0)] = {"v": {"a": 1}}
        c, (h,) = self.calc([{"id": "a", "title": "Зарядка"}], hist)
        self.assertEqual(c.streak(h), 6)
        hist.pop(self.day(2))  # дырка позавчера — серия 2 (сегодня и вчера)
        c, (h,) = self.calc([{"id": "a", "title": "Зарядка"}], hist)
        self.assertEqual(c.streak(h), 2)
        self.assertEqual(c.best(h), 3)  # 3 дня до дырки

    def test_days_and_skip_do_not_break(self):
        # пн ср пт; сегодня ср. Сделано пн 2.11, пт 30.10, ср 28.10; вт/чт — не по плану
        hist = {"2026-11-02": {"v": {"a": 1}}, "2026-10-30": {"v": {"a": 1}}, "2026-10-28": {"v": {"a": 1}},
                "2026-10-26": {"s": ["a"]}, "2026-10-23": {"v": {"a": 1}}}
        c, (h,) = self.calc([{"id": "a", "title": "Бег", "days": [0, 2, 4]}], hist)
        self.assertEqual(c.streak(h), 4)  # пропуск 26.10 серию не рвёт
        self.assertFalse(c.scheduled(h, D(2026, 11, 3)))
        hist["2026-10-26"] = {"s": ["*"]}  # пропуск всего дня — так же
        c, (h,) = self.calc([{"id": "a", "title": "Бег", "days": [0, 2, 4]}], hist)
        self.assertEqual(c.streak(h), 4)

    def test_created_limits_history(self):
        c, (h,) = self.calc([{"id": "a", "title": "Новая", "created": "2026-11-04"}], {})
        self.assertEqual(c.streak(h), 0)
        self.assertEqual(c.cell(h, D(2026, 11, 3)), "f")  # до создания — как будущее

    def test_count(self):
        hist = {self.day(0): {"v": {"w": 5}}, self.day(1): {"v": {"w": 8}}}
        c, (h,) = self.calc([{"id": "w", "title": "Вода", "kind": "count", "target": 8, "unit": "стаканов"}], hist)
        self.assertFalse(c.done(h, TODAY))
        self.assertEqual(c.cell(h, TODAY), "p")
        self.assertEqual(c.cell(h, D(2026, 11, 3)), "d")
        self.assertEqual(c.streak(h), 1)
        self.assertEqual(H.step_of(8), 1)
        self.assertEqual(H.step_of(20), 5)
        self.assertEqual(H.step_of(10000), 1000)
        self.assertEqual(H.step_of(2000, "мл"), 250)

    def test_weekly(self):
        # 3 раза в неделю. Эта неделя (пн 2.11): пн — сделано. Прошлая (26.10): 3 раза. Позапрошлая: 2 — нет нормы.
        hist = {"2026-11-02": {"v": {"e": 1}},
                "2026-10-26": {"v": {"e": 1}}, "2026-10-28": {"v": {"e": 1}}, "2026-10-31": {"v": {"e": 1}},
                "2026-10-20": {"v": {"e": 1}}, "2026-10-22": {"v": {"e": 1}}}
        c, (h,) = self.calc([{"id": "e", "title": "Английский", "weekly": 3}], hist)
        self.assertEqual(c.streak(h), 1)  # текущая неделя ещё идёт — не обрывает; прошлая — норма
        self.assertEqual(c.week_count(h, TODAY), (1, 0))
        self.assertTrue(c.due(h, TODAY))  # норма не набрана — идёт в счёт дня
        # норма 2 уже набрана (пн, вт) — сегодня не в счёт и точка «не по плану»; серия: эта неделя + прошлая
        c2, (h2,) = self.calc([{"id": "e", "title": "Английский", "weekly": 2}],
                              hist | {"2026-11-03": {"v": {"e": 1}}})
        self.assertFalse(c2.due(h2, TODAY))
        self.assertEqual(c2.cell(h2, TODAY), "o")
        self.assertEqual(c2.streak(h2), 3)  # и позапрошлая (2 раза) — норма для 2/нед
        hist["2026-11-03"] = {"s": ["e"], "v": {}}  # вт — пропуск: норма недели −… не меньше 1
        c3, (h3,) = self.calc([{"id": "e", "title": "Английский", "weekly": 3}], hist)
        self.assertEqual(c3.week_count(h3, TODAY), (1, 1))
        self.assertEqual(c3.need(h3, TODAY), 3)

    def test_weekly_best(self):
        hist = {}
        for w in range(4):  # 4 недели подряд по 2 раза
            mon = D(2026, 10, 5) + datetime.timedelta(days=7 * w)
            hist[mon.isoformat()] = {"v": {"e": 1}}
            hist[(mon + datetime.timedelta(days=2)).isoformat()] = {"v": {"e": 1}}
        c, (h,) = self.calc([{"id": "e", "title": "Бассейн", "weekly": 2}], hist)
        self.assertEqual(c.best(h), 4)
        self.assertEqual(c.streak(h), 4)  # текущая неделя ещё идёт — не обрывает
        hist.pop("2026-10-28")
        c, (h,) = self.calc([{"id": "e", "title": "Бассейн", "weekly": 2}], hist)
        self.assertEqual((c.streak(h), c.best(h)), (0, 3))  # неделя 26.10 без нормы — серия оборвалась


class ValidateTest(unittest.TestCase):
    def test_clean(self):
        h = H.clean_habit({"title": "  Вода  ", "kind": "count", "target": "8", "unit": "стаканов", "icon": "water"})
        self.assertEqual((h["title"], h["target"], h["icon"]), ("Вода", 8, "water"))
        self.assertEqual(H.clean_habit({"title": "x", "kind": "check", "target": 5})["target"], 1)  # check — всегда 1
        self.assertEqual(H.clean_habit({"title": "x", "icon": "<script>"})["icon"], "check")
        self.assertEqual(H.clean_habit({"title": "x", "weekly": 3})["days"], ALL)
        self.assertEqual(H.clean_habit({"title": "x", "weekly": 7})["weekly"], None)
        self.assertEqual(H.clean_habit({"title": "x", "time": "7.30"})["time"], "07:30")
        self.assertEqual(H.clean_habit({"title": "x", "time": "вечер"})["time"], "вечер")
        for bad in ({"title": ""}, {"title": "a" * 81}, {"title": "x", "kind": "nope"}, {"title": "x", "target": 0},
                    {"title": "x", "target": "много"}, {"title": "x", "days": []}, {"title": "x", "days": [7]},
                    {"title": "x", "weekly": 9}, {"title": "x", "time": "25:00"}, {"title": "x", "time": "ночь"},
                    {"title": "x", "target": True}, "строка"):
            with self.assertRaises(ValueError, msg=bad):
                H.clean_habit(bad)


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, _ = T.seeded(os.path.join(tempfile.mkdtemp(), "habits.db"))

    def setUp(self):
        coach.db = self.db
        self.db.x("DELETE FROM meta WHERE key LIKE 'habits:%' OR key LIKE 'regime:%'")
        self.db.x("DELETE FROM sent")

    def call(self, path, body=None, day=TODAY):
        now = datetime.datetime.combine(day, datetime.time(13, 40), tzinfo=ZoneInfo("Europe/Moscow"))
        with mock.patch.object(coach, "user_now", lambda u: now), mock.patch.object(coach, "today_of", lambda uid: day):
            return coach.route(T.U, path, body or {})

    def add(self, **h):
        return self.call("/api/habits/save", {"habit": h})

    def test_empty(self):
        r = self.call("/api/habits")
        self.assertEqual(set(r) >= {"habits", "today", "week", "stats"}, True)
        self.assertEqual((r["today"]["done"], r["today"]["total"], r["habits"]), (0, 0, []))
        self.assertEqual(len(r["week"]), 7)
        self.assertEqual(r["week"][-1]["day"], TODAY.isoformat())
        self.assertIsNone(r["week"][0]["pct"])
        self.assertEqual(coach.context(T.U, TODAY)[0].count("ПРИВЫЧКИ"), 0)  # нечего показывать тренеру

    def test_create_mark_and_today(self):
        r = self.add(title="Зарядка", time="утро")
        hid = r["saved"]
        self.add(title="Вода", kind="count", target=8, unit="стаканов")
        self.add(title="Бег", days=[0, 2, 4])  # ср — сегодня
        self.add(title="Бассейн", days=[1, 3])  # не сегодня
        r = self.call("/api/habits")
        self.assertEqual([x["title"] for x in r["today"]["items"]], ["Зарядка", "Вода", "Бег"])
        self.assertEqual((r["today"]["done"], r["today"]["total"]), (0, 3))
        r = self.call("/api/habits/mark", {"id": hid, "done": True})
        it = r["today"]["items"][0]
        self.assertEqual((it["done"], it["streak"], it["slot"]), (True, 1, "утро"))
        self.assertEqual(r["today"]["done"], 1)
        wid = r["today"]["items"][1]["id"]
        r = self.call("/api/habits/mark", {"id": wid, "value": 5})
        self.assertEqual((r["today"]["items"][1]["value"], r["today"]["items"][1]["done"]), (5, False))
        r = self.call("/api/habits/mark", {"id": wid, "value": 8})
        self.assertTrue(r["today"]["items"][1]["done"])
        self.assertEqual(r["today"]["done"], 2)
        self.assertEqual(r["week"][-1]["pct"], 67)
        self.assertEqual(r["habits"][0]["grid"][-1], "d")
        r = self.call("/api/habits/mark", {"id": hid, "done": False})
        self.assertEqual(r["today"]["done"], 1)
        # прошлые дни — до 7 назад; дальше и будущее — нельзя
        r = self.call("/api/habits/mark", {"id": wid, "value": 8, "day": "2026-11-03"})
        self.assertEqual(r["habits"][1]["grid"][-2], "d")  # отметка раньше создания — привычка «старше»
        self.assertEqual(r["habits"][1]["created"], "2026-11-03")
        for day in ("2026-10-27", "2026-11-05", "вчера"):
            with self.assertRaises(ValueError):
                self.call("/api/habits/mark", {"id": wid, "value": 1, "day": day})
        for bad in ({"id": "nope", "done": True}, {"id": wid}, {"id": wid, "value": -1}, {"id": wid, "value": 10 ** 9}, {}):
            with self.assertRaises(ValueError, msg=bad):
                self.call("/api/habits/mark", bad)

    def test_streak_across_days_and_skip(self):
        hid = self.add(title="Чтение")["saved"]
        for i in range(4, 0, -1):
            self.call("/api/habits/mark", {"id": hid, "done": True, "day": (TODAY - datetime.timedelta(days=i)).isoformat()})
        r = self.call("/api/habits")  # отметки прошлых дней сдвинули дату создания — серия считается
        self.assertEqual(r["habits"][0]["streak"], 4)
        self.assertEqual(r["habits"][0]["grid"], "ffddddm")  # до первой отметки — как до создания
        r = self.call("/api/habits/mark", {"id": hid, "skip": True})
        self.assertTrue(r["today"]["items"][0]["skip"])
        self.assertEqual(r["today"]["total"], 0)  # пропуск — не в счёт
        self.assertEqual(r["habits"][0]["grid"][-1], "s")
        r = self.call("/api/habits/mark", {"id": hid, "done": True})  # отметил — пропуск снят
        self.assertFalse(r["today"]["items"][0]["skip"])
        self.assertEqual(r["habits"][0]["streak"], 5)
        r = self.call("/api/habits/mark", {"skip": True})  # весь день
        self.assertTrue(r["stats"]["skip_day"])
        r = self.call("/api/habits/mark", {"skip": False})
        self.assertFalse(r["stats"]["skip_day"])

    def test_edit_order_delete_archive(self):
        a = self.add(title="А")["saved"]
        b = self.add(title="Б")["saved"]
        r = self.call("/api/habits/save", {"habit": {"id": a, "title": "А2", "kind": "count", "target": 10}})
        self.assertEqual((r["habits"][0]["title"], r["habits"][0]["target"]), ("А2", 10))
        r = self.call("/api/habits/save", {"order": [b, a]})
        self.assertEqual([x["id"] for x in r["habits"]], [b, a])
        r = self.call("/api/habits/save", {"habit": {"id": a, "archived": True}})
        self.assertEqual(len(r["today"]["items"]), 1)  # в архиве — не на сегодня
        self.assertEqual(len(r["habits"]), 2)
        r = self.call("/api/habits/save", {"delete": b})
        self.assertEqual([x["id"] for x in r["habits"]], [a])
        for bad in ({"delete": "nope"}, {"habit": {"id": "nope", "title": "x"}}, {}, {"order": "x"}):
            with self.assertRaises(ValueError, msg=bad):
                self.call("/api/habits/save", bad)

    def test_limits(self):
        st = H.load(self.db, T.U)
        for i in range(H.MAX_HABITS):
            H.add(st, {"title": f"П{i}"}, TODAY)
        H.store(self.db, T.U, st)
        with self.assertRaises(ValueError):
            self.add(title="Лишняя")
        r = self.call("/api/habits/import", {"text": "Ещё одна"})
        self.assertEqual((r["created"], r["over"]), ([], ["Ещё одна"]))
        for i in range(H.MAX_GOALS):
            self.call("/api/habits/goal", {"op": "add", "text": f"цель {i}"})
        with self.assertRaises(ValueError):
            self.call("/api/habits/goal", {"op": "add", "text": "лишняя"})
        with self.assertRaises(ValueError):
            self.call("/api/habits/import", {"text": "x\n" * 5000})

    def test_goals_and_move(self):
        r = self.call("/api/habits/goal", {"op": "add", "text": "Купить кроссовки"})
        g1 = r["today"]["goals"][0]["id"]
        r = self.call("/api/habits/goal", {"op": "add", "text": "Сдать реферат"})
        g2 = r["today"]["goals"][1]["id"]
        self.assertEqual((r["today"]["done"], r["today"]["total"]), (0, 2))  # цели — в кольце дня
        r = self.call("/api/habits/goal", {"op": "done", "id": g1, "done": True})
        self.assertEqual(r["today"]["goals_done"], 1)
        r = self.call("/api/habits/goal", {"op": "move"})
        self.assertEqual(r["moved"], 1)
        self.assertEqual([g["text"] for g in r["today"]["goals"]], ["Купить кроссовки"])
        r = self.call("/api/habits", day=TODAY + datetime.timedelta(days=1))
        self.assertEqual([(g["text"], g["done"]) for g in r["today"]["goals"]], [("Сдать реферат", False)])
        with self.assertRaises(ValueError):
            self.call("/api/habits/goal", {"op": "move"})  # всё выполнено — нечего
        r = self.call("/api/habits/goal", {"op": "edit", "id": g1, "text": "Купить кеды"})
        self.assertEqual(r["today"]["goals"][0]["text"], "Купить кеды")
        r = self.call("/api/habits/goal", {"op": "del", "id": g1})
        self.assertEqual(r["today"]["goals"], [])
        for bad in ({"op": "add", "text": ""}, {"op": "add", "text": "x" * 81}, {"op": "done", "id": "nope"}, {"op": "zz"}):
            with self.assertRaises(ValueError, msg=bad):
                self.call("/api/habits/goal", bad)
        self.assertNotIn(g2, [g["id"] for g in self.call("/api/habits")["today"]["goals"]])

    def test_import_preview_and_create(self):
        text = "Пить 8 стаканов воды\n10 000 шагов\nАнглийский 3 раза в неделю\nБег пн ср пт в 07:00\n\n—\nЧитать 20 мин перед сном"
        r = self.call("/api/habits/import", {"text": text, "preview": True})
        self.assertEqual(len(r["habits"]), 5)
        self.assertEqual(r["habits"][2]["line"], "Английский 3 раза в неделю")
        self.assertEqual(r["skipped"], ["—"])
        self.assertEqual(self.call("/api/habits")["habits"], [])  # предпросмотр ничего не создаёт
        r = self.call("/api/habits/import", {"text": text})
        self.assertEqual([h["title"] for h in r["created"]],
                         ["Пить 8 стаканов воды", "10 000 шагов", "Английский", "Бег", "Читать 20 мин"])
        self.assertEqual(len(r["habits"]), 5)
        self.assertNotIn("line", r["habits"][0])
        r = self.call("/api/habits/import", {"text": "Английский\nНовая"})  # повтор по названию — пропуск
        self.assertEqual((r["dup"], [h["title"] for h in r["created"]]), (["Английский"], ["Новая"]))
        with self.assertRaises(ValueError):
            self.call("/api/habits/import", {"text": "  \n "})

    def test_extra_context_brief_and_reminder(self):
        hid = self.add(title="Зарядка")["saved"]
        self.add(title="Вода", kind="count", target=8, unit="стаканов")
        self.call("/api/habits/goal", {"op": "add", "text": "Сдать реферат"})
        self.call("/api/habits/mark", {"id": hid, "done": True})
        r = self.call("/api/all")
        ext = r["ext"]["habits"]
        self.assertEqual((ext["today"]["done"], ext["today"]["total"], ext["has"]), (1, 3, True))
        self.assertNotIn("habits", ext)  # главной — коротко
        with mock.patch.object(coach, "today_of", lambda uid: TODAY):
            ctx = coach.context(T.U, TODAY)[0]
        self.assertIn("ПРИВЫЧКИ И ЦЕЛИ (отмечает сам): сегодня 1 из 3; сделано: Зарядка; осталось: Вода, Сдать реферат", ctx)
        self.assertEqual(H.brief_line(self.db, T.U, TODAY), "✅ Привычки сегодня: 2 · цели: 1")
        self.assertEqual(coach.habits_brief(T.U, {"day": TODAY.isoformat()}), "✅ Привычки сегодня: 2 · цели: 1")
        self.assertEqual(H.remind_line(self.db, T.U, TODAY), "Осталось: Вода, Сдать реферат")
        sent = []
        with mock.patch.object(coach, "send", lambda chat, text, buttons=None, **kw: sent.append(text)):
            coach.regime_remind(T.U, TODAY)
        self.assertIn("✅ Осталось: Вода, Сдать реферат", sent[0])
        self.assertLessEqual(sent[0].split("<blockquote>")[1].count("\n") + 1, 5)

    def test_reminder_without_habits(self):
        sent = []
        with mock.patch.object(coach, "send", lambda chat, text, buttons=None, **kw: sent.append(text)):
            coach.regime_remind(T.U, TODAY)
        self.assertNotIn("Осталось", sent[0])
        self.assertEqual(coach.habits_brief(T.U, {"day": TODAY.isoformat()}), "")


if __name__ == "__main__":
    unittest.main(verbosity=1)
