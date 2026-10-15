"""Оценки недели / месяца и календарь (coach/f_assess.py) на поддельном WHOOP из test_coach.
usage: python3 coach/test_assess.py
"""
import datetime
import os
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


if __name__ == "__main__":
    unittest.main(argv=sys.argv[:1])
