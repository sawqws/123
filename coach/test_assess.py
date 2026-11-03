"""Оценки недели / месяца и календарь (coach/f_assess.py) на поддельном WHOOP из test_coach.
usage: python3 coach/test_assess.py
"""
import datetime
import os
import re
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_coach as T  # noqa: E402  (ставит окружение и поддельный WHOOP)
import coach  # noqa: E402
import f_assess as A  # noqa: E402

DB, _ = T.seeded(days=40)
NOW = datetime.datetime.combine(T.TODAY, datetime.time(13, 40))


def call(path, body=None):
    coach.db = DB
    with mock.patch.object(coach, "user_now", lambda u: NOW), mock.patch.object(coach, "today_of", lambda uid: T.TODAY):
        return coach.route(T.U, path, body or {})


class WeekTest(unittest.TestCase):
    def test_week_full_days_vs_previous(self):
        w = call("/api/assess")
        self.assertEqual(w["period"], "week")
        self.assertEqual((w["since"], w["until"]), ("2026-10-04", "2026-10-10"))  # полные дни: по вчера
        self.assertEqual(w["label"], "4–10 окт")
        self.assertEqual(w["prev_label"], "27 сен – 3 окт")
        self.assertEqual(len(w["days"]), 7)
        self.assertTrue(w["enough"])
        self.assertEqual(w["cur"]["days"], 7)
        self.assertEqual(w["cur"]["green"] + w["cur"]["yellow"] + w["cur"]["red"], 7)
        # бокс 4 раза в неделю по 110 мин, зоны Z1–Z5: 20/30/30/15/5 мин на тренировку
        self.assertEqual(w["cur"]["workouts"], 4)
        self.assertEqual(w["cur"]["zones"], [80, 120, 120, 60, 20])
        self.assertEqual([r["label"] for r in w["by_weekday"]], A.WD_S)  # пн–вс
        self.assertIn(w["state"]["key"], ("optimal", "over", "restore"))
        self.assertTrue(w["state"]["text"])
        self.assertIn(w["sleep"]["key"], ("good", "ok", "bad"))
        self.assertAlmostEqual(w["sleep"]["restor"], w["cur"]["restor"])

    def test_bad_period(self):
        with self.assertRaises(ValueError):
            call("/api/assess", {"period": "year"})


class StateTest(unittest.TestCase):
    """Наш расчёт состояния тренированности: пороги отношения нагрузки 7/28 и восстановления."""
    def test_thresholds(self):
        self.assertEqual(A.training_state(1.0, 60)["key"], "optimal")
        self.assertEqual(A.training_state(1.35, 80)["key"], "over")
        self.assertEqual(A.training_state(1.15, 40)["key"], "over")      # выше обычной и плохо восстанавливается
        self.assertEqual(A.training_state(1.15, 70)["key"], "optimal")
        self.assertEqual(A.training_state(0.75, 50)["key"], "restore")
        self.assertEqual(A.training_state(0.9, 75)["key"], "restore")    # мало нагрузки при зелёном восстановлении
        self.assertEqual(A.training_state(0.9, 50)["key"], "optimal")
        none = A.training_state(None, 60)
        self.assertIsNone(none["key"])
        self.assertEqual(none["word"], "Мало данных")

    def test_sleep_status(self):
        self.assertEqual(A.sleep_status({"sleep": 8.5, "need": 9, "bed_spread": 0.3})["key"], "good")
        self.assertEqual(A.sleep_status({"sleep": 8.5, "need": 9, "bed_spread": 1.2})["key"], "ok")  # режим гуляет
        self.assertEqual(A.sleep_status({"sleep": 6.5, "need": 9, "bed_spread": 0.2})["key"], "bad")
        self.assertIsNone(A.sleep_status({"sleep": None})["key"])


class MonthTest(unittest.TestCase):
    def test_month_weeks_and_trend(self):
        m = call("/api/assess", {"period": "month"})
        self.assertEqual(m["span"], 30)
        self.assertEqual((m["since"], m["until"]), ("2026-09-11", "2026-10-10"))
        self.assertEqual(len(m["days"]), 30)
        # недели месяца покрывают все 30 дней без пропусков, по порядку
        self.assertEqual(m["weeks"][0]["since"], m["since"])
        self.assertEqual(m["weeks"][-1]["until"], m["until"])
        for a, b in zip(m["weeks"], m["weeks"][1:]):
            self.assertEqual(datetime.date.fromisoformat(a["until"]) + datetime.timedelta(days=1),
                             datetime.date.fromisoformat(b["since"]))
        self.assertTrue(all(r["hrv"] for r in m["days"]))
        self.assertEqual(sum(r["n"] for r in m["by_weekday"]), 30)
        self.assertLess(m["prev"]["days"], 30)  # в тестовых данных прошлый месяц неполный — так и отдаём


class CalendarTest(unittest.TestCase):
    def test_current_month(self):
        c = call("/api/calendar")
        self.assertEqual(c["month"], "2026-10")
        self.assertEqual(c["title"], "Октябрь 2026")
        self.assertEqual(c["offset"], 3)  # 1 октября 2026 — четверг
        self.assertEqual(len(c["days"]), 31)
        self.assertEqual(c["prev"], "2026-09")
        self.assertIsNone(c["next"])
        d11 = c["days"][10]
        self.assertTrue(d11["today"])
        self.assertEqual(d11["recovery"], 82)
        self.assertTrue(c["days"][11]["future"])
        self.assertEqual(c["stats"]["days"], 11)

    def test_limits(self):
        self.assertEqual(call("/api/calendar", {"month": "2026-09"})["next"], "2026-10")
        old = call("/api/calendar", {"month": "2026-04"})  # 180 дней назад — 14 апреля
        self.assertIsNone(old["prev"])
        self.assertTrue(old["days"][0]["old"])
        for bad in ("2026-03", "2026-11", "октябрь"):
            with self.assertRaises(ValueError):
                call("/api/calendar", {"month": bad})


class WiringTest(unittest.TestCase):
    def test_extra_and_context(self):
        ext = call("/api/all")["ext"]["assess"]["week"]
        self.assertEqual(len(ext["zones"]), 7)
        self.assertIn(ext["state"]["key"], ("optimal", "over", "restore"))
        lines = A.context_lines(coach, T.U, T.TODAY)
        self.assertEqual(len(lines), 1)
        self.assertIn("тренированность", lines[0])
        self.assertIn("сон", lines[0])
        with mock.patch.object(coach, "user_now", lambda u: NOW):
            text, _ = coach.context(T.U, T.TODAY)
        self.assertIn("Оценка недели", text)

    def test_no_data(self):
        coach.db = DB
        empty = A.assess(coach, 999, T.TODAY, "week")
        self.assertFalse(empty["enough"])
        self.assertEqual(A.context_lines(coach, 999, T.TODAY), [])


# термины и значки, которых не должно быть в главной части экрана (Глеб 03.11: «ничего не понял»)
BANNED = re.compile(r"%|\bмс\b|×|из 21|[A-Za-z]|ВСР|пульсов|зон[аы] \d|тренированност|baseline|strain", re.I)
# советы — только сон, еда, вода, восстановление, режим; не про то, как тренироваться
TRAIN_ADVICE = re.compile(r"тренируйся|добавь тренировк|пропусти тренировк|отмени тренировк|спарринг|интенсивн|"
                          r"лёгкую тренировк|легкую тренировк|меньше тренир|больше тренир", re.I)


def main_texts(s, weeks=()):
    """Все строки главной части экрана (story) — то, что видно без «Подробнее»."""
    out = [s.get(k) for k in ("headline", "title", "summary", "empty", "partial", "no_prev")]
    for r in s.get("rows", []):
        out += [r["name"], r["value"], r["sub"], (r.get("cmp") or {}).get("text")]
    for k in ("best", "hard"):
        if s.get(k):
            out += [s[k]["title"], s[k]["why"], s[k].get("word")]
    out += s.get("tips", []) + [d["word"] for d in s.get("days", [])] + [s.get("grade", {}).get("word")]
    out += [w["word"] for w in weeks] + [w["line"] for w in weeks]
    return [x for x in out if x]


class StoryTest(unittest.TestCase):
    """Понятная оценка: итог фразой, оценка 1–5, строки «что было» со сравнением словами, без терминов."""
    def test_week_story(self):
        s = call("/api/assess")["story"]
        self.assertTrue(s["enough"])
        self.assertTrue(s["headline"].startswith("Неделя "))
        self.assertIn(": ", s["headline"])  # «Неделя хорошая: …»
        self.assertEqual(s["title"] + ": " + s["summary"][:1].lower() + s["summary"][1:-1], s["headline"])
        self.assertIn(s["grade"]["score"], range(1, 6))
        self.assertIn(s["grade"]["word"], A.GRADE_W["week"].values())
        keys = [r["key"] for r in s["rows"]]
        self.assertTrue(3 <= len(keys) <= 5)
        self.assertEqual(keys[:4], ["sleep", "rec", "load", "train"])
        sleep = s["rows"][0]
        self.assertRegex(sleep["value"], r"^\d+\xa0ч( \d+\xa0мин)?$")  # «7 ч 9 мин» — привычные единицы
        self.assertEqual(sleep["cmp"]["text"], "на 15\xa0мин меньше, чем на прошлой неделе")
        self.assertEqual((sleep["cmp"]["dir"], sleep["cmp"]["good"]), ("down", False))
        self.assertEqual(s["rows"][3]["value"], "4 тренировки")
        self.assertTrue(s["best"]["why"] and s["hard"]["why"])
        self.assertEqual(s["hard"]["day"], "2026-10-07")  # среда — самая большая нагрузка
        self.assertTrue(2 <= len(s["tips"]) <= 3)
        self.assertEqual(len(s["days"]), 7)
        self.assertTrue(all(d["level"] in (0, 1, 2, 3) for d in s["days"]))
        for x in main_texts(s):
            self.assertIsNone(BANNED.search(x), x)
        for x in s["tips"]:
            self.assertIsNone(TRAIN_ADVICE.search(x), x)

    def test_month_story_by_weeks(self):
        m = call("/api/assess", {"period": "month"})
        s = m["story"]
        self.assertTrue(s["headline"].startswith("Месяц "))
        self.assertIn(s["grade"]["word"], A.GRADE_W["month"].values())
        self.assertIn("в прошлом месяце", s["rows"][0]["cmp"]["text"])
        self.assertTrue(s["rows"][2]["sub"].startswith("тяжелее всего — по "))
        self.assertIsNone(s["rows"][3]["cmp"])  # прошлый месяц неполный — суммы тренировок не сравниваем
        self.assertEqual(len(s["days"]), 30)
        for w in m["weeks"]:
            self.assertIn("score", w)
            self.assertTrue(w["line"].startswith("сон "))
        for x in main_texts(s, m["weeks"]):
            self.assertIsNone(BANNED.search(x), x)
        for x in s["tips"]:
            self.assertIsNone(TRAIN_ADVICE.search(x), x)

    def test_empty_week(self):
        coach.db = DB
        s = A.assess(coach, 999, T.TODAY, "week")["story"]
        self.assertFalse(s["enough"])
        self.assertEqual(s["empty"], "Мало данных за неделю — нужно хотя бы 4 дня с браслетом. Пока данных нет.")
        self.assertNotIn("headline", s)
        self.assertEqual(len(s["days"]), 7)
        self.assertTrue(all(d["level"] == 0 for d in s["days"]))

    def test_few_days_honest(self):
        """3 дня из 7 — не оцениваем; 5 из 7 — оценка с пометкой «примерная»."""
        cur = {"days": 3, "sleep": 8, "need": 9, "recovery": 70, "green": 2, "red": 0}
        s = A.story(coach, 999, "week", cur, {"days": 0}, [], None, "2026-10-04", "2026-10-10", "2026-09-27", "2026-10-03")
        self.assertFalse(s["enough"])
        self.assertIn("Пока есть 3 дня.", s["empty"])
        cur.update(days=5, workouts=3, minutes=300, strain=11)
        s = A.story(coach, 999, "week", cur, {"days": 0}, [], None, "2026-10-04", "2026-10-10", "2026-09-27", "2026-10-03")
        self.assertTrue(s["enough"])
        self.assertEqual(s["partial"], "Данные есть за 5 из 7 дней — оценка примерная.")
        self.assertEqual(s["no_prev"], "За прошлую неделю мало данных — сравнивать не с чем.")
        self.assertTrue(all(r["cmp"] is None for r in s["rows"]))

    def test_headline_and_grade(self):
        good = {"days": 7, "sleep": 9, "need": 9, "recovery": 75, "green": 6, "red": 0, "bed_spread": .6}
        prev = {"days": 7, "sleep": 8.5, "recovery": 65}
        self.assertEqual(A.grade(good, "optimal"), 5)
        self.assertEqual(A._headline("week", 5, good, prev, "optimal"), "Неделя отличная: спал больше, восстанавливался лучше")
        bad = {"days": 7, "sleep": 6.5, "need": 9, "recovery": 40, "green": 1, "red": 3, "bed_spread": 1.1}
        self.assertEqual(A.grade(bad, "over"), 1)
        self.assertEqual(A._headline("week", 2, bad, {"days": 7, "sleep": 6.6, "recovery": 42}, "over"),
                         "Неделя тяжелее обычной: много нагрузки, сна не хватало")
        flat = {"days": 7, "sleep": 8, "need": 9, "recovery": 60, "green": 3, "red": 0, "bed_spread": .6}
        self.assertEqual(A._headline("week", 3, flat, dict(flat), "optimal"), "Неделя обычная: всё ровно, как на прошлой неделе")

    def test_compare_in_words(self):
        self.assertEqual(A._cmp(30, 10, "больше", "меньше", lambda m: A.dur(m / 60), "week", True),
                         {"text": "на 30\xa0мин больше, чем на прошлой неделе", "dir": "up", "good": True})
        self.assertEqual(A._cmp(-90, 10, "больше", "меньше", lambda m: A.dur(m / 60), "month", True)["text"],
                         "на 1\xa0ч 30\xa0мин меньше, чем в прошлом месяце")
        self.assertEqual(A._cmp(5, 10, "больше", "меньше", str, "week")["text"], "как на прошлой неделе")
        self.assertEqual(A._word_cmp(-7, 5, "лучше", "хуже", "week", True),
                         {"text": "хуже, чем на прошлой неделе", "dir": "down", "good": False})
        self.assertIsNone(A._cmp(None, 10, "больше", "меньше", str, "week"))

    def test_water_row_and_tip(self):
        db, _ = T.seeded(days=40)
        for i in range(1, 15):
            db.add_water(T.U, (T.TODAY - datetime.timedelta(days=i)).isoformat(), "12:00", 1200 if i <= 7 else 1500)
        coach.db = db
        try:
            s = A.assess(coach, T.U, T.TODAY, "week")["story"]
        finally:
            coach.db = DB
        food = s["rows"][-1]
        self.assertEqual((food["key"], food["value"]), ("food", "1,2\xa0л воды"))
        self.assertEqual(food["cmp"]["text"], "на 300\xa0мл меньше, чем на прошлой неделе")
        self.assertTrue(any("Пей больше воды" in x for x in s["tips"]))


if __name__ == "__main__":
    unittest.main(argv=sys.argv[:1])
