"""Дневник (f_journal): ответы за вчера, сопоставление с восстановлением СЛЕДУЮЩЕГО утра, влияние, API.
usage: python3 coach/test_journal.py
"""
import datetime
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.argv = sys.argv[:1]
import test_coach as T  # noqa: E402  (окружение и поддельный WHOOP)
import coach  # noqa: E402
import f_journal as J  # noqa: E402

D = datetime.timedelta


def call(path, body=None):
    now = datetime.datetime.combine(T.TODAY, datetime.time(9, 0))
    with mock.patch.object(coach, "user_now", lambda u: now), mock.patch.object(coach, "today_of", lambda uid: T.TODAY):
        return coach.route(T.U, path, body or {})


class Pairing(unittest.TestCase):
    """Ответ за день X сравниваем с восстановлением дня X+1 (утро после этого вечера), а не дня X."""

    def test_after_is_next_morning(self):
        self.assertEqual(J.after("2026-10-10"), "2026-10-11")
        self.assertEqual(J.after("2026-12-31"), "2027-01-01")

    def test_impact_uses_next_day_recovery(self):
        # «да» в дни 1,3,5; «нет» в 2,4,6. Восстановление того же дня у «да» высокое (ловушка),
        # а наутро после «да» — низкое: влияние должно быть отрицательным.
        ans, rec = {}, {}
        base = datetime.date(2026, 9, 1)
        for i in range(1, 7):
            d = base + D(days=i)
            ans[d.isoformat()] = {"caffeine": i % 2 == 1}
            rec[d.isoformat()] = 90 if i % 2 == 1 else 40          # тот же день — не должен учитываться
            rec[(d + D(days=1)).isoformat()] = None                 # перезапишем ниже
        for i in range(1, 7):
            nxt = (base + D(days=i + 1)).isoformat()
            rec[nxt] = 40 if i % 2 == 1 else 80                      # наутро: после «да» 40, после «нет» 80
        rec = {k: v for k, v in rec.items() if v is not None}
        out = {x["key"]: x for x in J.impact(ans, rec)}["caffeine"]
        self.assertEqual((out["yes"], out["no"]), (3, 3))
        self.assertTrue(out["enough"])
        self.assertEqual(out["yes_avg"], 40)
        self.assertEqual(out["no_avg"], 80)
        self.assertEqual(out["impact"], -40)

    def test_not_enough(self):
        base = datetime.date(2026, 9, 1)
        ans = {(base + D(days=i)).isoformat(): {"stretch": i < 2} for i in range(5)}  # 2 «да», 3 «нет»
        rec = {(base + D(days=i + 1)).isoformat(): 60 for i in range(5)}
        x = J.impact(ans, rec)[0]
        self.assertFalse(x["enough"])
        self.assertIsNone(x["impact"])
        self.assertEqual((x["yes"], x["no"]), (2, 3))

    def test_no_morning_recovery_skipped(self):
        ans = {"2026-09-01": {"sick": True}}
        self.assertEqual(J.impact(ans, {"2026-09-01": 50}), [])  # своё утро есть, следующего нет — не считаем

    def test_sorted_by_strength(self):
        base = datetime.date(2026, 9, 1)
        ans, rec = {}, {}
        for i in range(8):
            d = (base + D(days=i)).isoformat()
            ans[d] = {"bed23": i % 2 == 0, "sweets": i % 2 == 0}
            rec[J.after(d)] = 70 if i % 2 == 0 else 60
        ans[(base + D(days=8)).isoformat()] = {"nap": True}
        rec[J.after((base + D(days=8)).isoformat())] = 10
        # bed23 и sweets одинаковы (+10); nap — мало данных, в конце
        out = J.impact(ans, rec)
        self.assertEqual(out[-1]["key"], "nap")
        self.assertTrue(all(x["enough"] for x in out[:-1]))


class Api(unittest.TestCase):
    def setUp(self):
        self.db, _ = T.seeded()
        coach.db = self.db

    def test_defaults_and_yesterday(self):
        r = call("/api/journal")
        y = (T.TODAY - D(days=1)).isoformat()
        self.assertEqual(r["day"], y)
        self.assertEqual(r["selected"], J.DEFAULT)
        self.assertTrue(6 <= len(J.DEFAULT) <= 8)
        self.assertEqual(len(r["habits"]), len(J.HABITS))
        self.assertGreaterEqual(len(J.HABITS), 17)
        self.assertEqual(r["answers"], {})
        self.assertEqual(r["ext"]["pending"], len(J.DEFAULT))
        # нельзя про сегодня и раньше 30 дней
        with self.assertRaises(ValueError):
            call("/api/journal/answer", {"day": T.TODAY.isoformat(), "key": "bed23", "value": True})
        with self.assertRaises(ValueError):
            call("/api/journal/answer", {"day": (T.TODAY - D(days=40)).isoformat(), "key": "bed23", "value": True})
        with self.assertRaises(ValueError):
            call("/api/journal/answer", {"day": y, "key": "alcohol", "value": True})

    def test_answer_clear_and_extra(self):
        y = (T.TODAY - D(days=1)).isoformat()
        r = call("/api/journal/answer", {"day": y, "key": "bed23", "value": True})
        self.assertEqual(r["answers"], {"bed23": True})
        r = call("/api/journal/answer", {"day": y, "key": "caffeine", "value": False})
        self.assertEqual(r["answers"], {"bed23": True, "caffeine": False})
        self.assertEqual(r["ext"]["answered"], 2)
        r = call("/api/journal/answer", {"day": y, "key": "caffeine", "value": None})
        self.assertEqual(r["answers"], {"bed23": True})
        allp = call("/api/all")
        self.assertEqual(allp["ext"]["journal"]["pending"], len(J.DEFAULT) - 1)
        # прошлый день листается
        d3 = (T.TODAY - D(days=3)).isoformat()
        call("/api/journal/answer", {"day": d3, "key": "stress", "value": True})
        self.assertEqual(call("/api/journal", {"d": d3})["answers"], {"stress": True})

    def test_setup(self):
        r = call("/api/journal/setup", {"keys": ["injury", "sauna", "nope"]})
        self.assertEqual(r["selected"], ["sauna", "injury"])  # порядок списка, лишнее отброшено
        with self.assertRaises(ValueError):
            call("/api/journal/setup", {"keys": []})

    def test_impact_against_real_series(self):
        """Сквозная проверка: влияние = среднее восстановление из insights.series следующего дня."""
        rec = {r["day"]: r["recovery"] for r in coach.insights.series(self.db, T.U, T.TODAY, 30)}
        yes, no = [], []
        for i in range(2, 22):
            d = T.TODAY - D(days=i)
            v = i % 3 == 0
            call("/api/journal/answer", {"day": d.isoformat(), "key": "screen", "value": v})
            (yes if v else no).append(rec[(d + D(days=1)).isoformat()])
        r = call("/api/journal")
        x = next(i for i in r["impact"] if i["key"] == "screen")
        self.assertEqual((x["yes"], x["no"]), (len(yes), len(no)))
        self.assertAlmostEqual(x["yes_avg"], round(sum(yes) / len(yes), 1))
        self.assertAlmostEqual(x["no_avg"], round(sum(no) / len(no), 1))
        self.assertEqual(x["impact"], round(x["yes_avg"] - x["no_avg"]))
        # и тренер это видит
        lines = J.context_lines(coach, T.U, T.TODAY)
        self.assertTrue(any("Влияние на восстановление" in s and "экран перед сном" in s for s in lines))
        top = call("/api/all")["ext"]["journal"]["top"]
        self.assertEqual(top[0]["key"], "screen")

    def test_context_yesterday(self):
        y = (T.TODAY - D(days=1)).isoformat()
        self.assertEqual(J.context_lines(coach, T.U, T.TODAY), [])
        call("/api/journal/answer", {"day": y, "key": "sick", "value": True})
        lines = J.context_lines(coach, T.U, T.TODAY)
        self.assertIn("болел — да", lines[0])

    def test_hints_from_real_data(self):
        y = T.TODAY - D(days=1)
        self.db.add_water(T.U, y.isoformat(), "10:00", 1800)
        h = call("/api/journal")["hints"]
        self.assertEqual(h.get("water2"), "по учёту воды: 1,8 л")


if __name__ == "__main__":
    unittest.main(verbosity=1)
