"""Тренды (coach/f_trends.py): показатели WHOOP за неделю / месяц / полгода на поддельном WHOOP из test_coach.
usage: python3 coach/test_trends.py
"""
import datetime
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_coach as T  # noqa: E402  (ставит окружение и поддельный WHOOP)
import coach  # noqa: E402
import f_trends as TR  # noqa: E402
import store  # noqa: E402
import whoop  # noqa: E402

NOW = datetime.datetime.combine(T.TODAY, datetime.time(13, 40))
H = 3_600_000


def seeded(days=70, steps=True):
    """Поддельный WHOOP + шаги в цикле (step_count — поле самого цикла в API v2)."""
    db = store.Store(os.path.join(tempfile.mkdtemp(), "c.db"))
    db.user(T.U, "Тест")
    db.save_token(T.U, "acc", "ref", time.time() + 3600)
    data = T.fake_whoop(days)
    if steps:
        for i, c in enumerate(data["cycle"]):
            c["step_count"] = 6000 + i * 10
    behind = max(0, (datetime.date.today() - T.TODAY).days)
    with mock.patch.object(whoop.Client, "get", T.FakeWhoop(data).get):
        whoop.sync(db, T.U, "cid", "sec", days=days + 2 + behind)
    return db


DB = seeded()


def call(body, db=DB):
    coach.db = db
    with mock.patch.object(coach, "user_now", lambda u: NOW), mock.patch.object(coach, "today_of", lambda uid: T.TODAY):
        return coach.route(T.U, "/api/trends", body)


class ListTest(unittest.TestCase):
    def test_groups(self):
        out = call({})
        self.assertEqual([g["title"] for g in out["groups"]], ["Нагрузка", "Восстановление", "Сон", "Другое"])
        keys = [i["key"] for g in out["groups"] for i in g["items"]]
        for k in ("strain", "steps", "zones_13", "zones_45", "zones_all", "avg_hr", "kcal", "recovery", "hrv", "rhr",
                  "resp", "spo2", "skin_temp", "sleep_perf", "in_bed", "sleep_hours", "consistency", "restor",
                  "efficiency", "debt", "weight"):
            self.assertIn(k, keys)
        self.assertIn("VO₂ max", out["missing"])
        hrv = next(i for g in out["groups"] for i in g["items"] if i["key"] == "hrv")
        self.assertEqual((hrv["title"], hrv["unit"]), ("Запас сил", "мс"))

    def test_unknown_metric_and_period(self):
        with self.assertRaises(ValueError):
            call({"metric": "vo2max"})
        with self.assertRaises(ValueError):
            call({"metric": "hrv", "period": "year"})
        with self.assertRaises(ValueError):
            call({"metric": "hrv", "end": "вчера"})

    def test_unknown_metric_is_400(self):
        """Через HTTP-обработчик: неизвестный показатель — 400, а не 500."""
        h = coach.Web.__new__(coach.Web)
        sent = []
        h.reply = lambda code, body, **kw: sent.append((code, body))
        h.headers = {"X-Init-Data": "x"}
        coach.db = DB
        with mock.patch.object(coach, "check_init_data", return_value={"id": T.U}), \
                mock.patch.object(coach, "allowed", return_value=True), \
                mock.patch.object(coach, "user_now", lambda u: NOW):
            h.api("/api/trends", {"metric": "vo2max"})
        self.assertEqual(sent[0][0], 400)
        self.assertIn("показател", sent[0][1]["error"])


class CalcTest(unittest.TestCase):
    def test_week_points_avg_and_change(self):
        out = call({"metric": "hrv", "period": "w"})
        self.assertEqual((out["since"], out["until"]), ("2026-10-05", "2026-10-11"))
        self.assertEqual(len(out["points"]), 7)
        self.assertEqual(out["label"], "5–11 окт")
        vals = [p["v"] for p in out["points"]]
        self.assertTrue(all(v is not None for v in vals))
        self.assertAlmostEqual(out["avg"], round(sum(vals) / 7, 0), delta=1)
        self.assertIsNotNone(out["prev_avg"])
        pct = round((out["avg"] - out["prev_avg"]) / out["prev_avg"] * 100)
        self.assertLessEqual(abs(out["change_pct"] - pct), 1)
        self.assertTrue(out["text"].startswith("В среднем за неделю запас сил — "))
        self.assertIn("за прошлые 7 дней", out["text"])
        lo, hi = out["typical"]
        self.assertLess(lo, hi)
        self.assertTrue(out["can_back"])
        self.assertFalse(out["can_next"])

    def test_change_and_phrase(self):
        self.assertEqual(TR.change(115, 106)[1], (115 - 106) / 106 * 100)
        self.assertEqual(TR.change(33.6, 33.4, absdiff=True)[1], None)  # температура — разницей, без %
        self.assertEqual(TR.change(5, None), (None, None))
        self.assertEqual(TR.change(5, 0)[1], None)
        m = TR.METRICS["hrv"]
        t = TR.phrase(m, "m", 115, 106, 9, 8.5)
        self.assertEqual(t, "В среднем за месяц запас сил — 115 мс: это выше, чем за прошлые 30 дней (106 мс).")
        self.assertIn("примерно так же", TR.phrase(m, "m", 106.3, 106, .3, .28))
        self.assertIn("больше", TR.phrase(TR.METRICS["steps"], "w", 9000, 8000, 1000, 12.5))
        self.assertIn("сравнить не с чем", TR.phrase(m, "w", 100, None, None, None))

    def test_zones(self):
        """Бокс 4 раза в неделю: зоны 1–5 = 20/30/30/15/5 мин; без тренировки — 0 (браслет носил)."""
        self.assertEqual(TR.zone_minutes([{"score": {"zone_durations": {
            "zone_zero_milli": 300000, "zone_one_milli": 1200000, "zone_two_milli": 1800000,
            "zone_three_milli": 1800000, "zone_four_milli": 900000, "zone_five_milli": 300000}}}] * 2), (160, 40, 200))
        for key, val in (("zones_13", 80), ("zones_45", 20), ("zones_all", 100)):
            out = call({"metric": key, "period": "w"})
            by = {p["day"]: p["v"] for p in out["points"]}
            self.assertEqual(by["2026-10-05"], val)  # понедельник — бокс
            self.assertEqual(by["2026-10-06"], 0)    # вторник — без тренировки
            self.assertEqual(out["unit"], "мин")

    def test_restorative_sleep(self):
        """Глубокий + быстрый: в фикстуре по 25% сна каждый."""
        h = call({"metric": "restor", "period": "w"})
        hrs = call({"metric": "sleep_hours", "period": "w"})
        pct = call({"metric": "restor_pct", "period": "w"})
        for a, b, c in zip(h["points"], hrs["points"], pct["points"]):
            # в кровати h ч: лёгкий h/2 − 0,5, глубокий h/4, быстрый h/4 → сон = h − 0,5, восстанавливающий = h/2
            self.assertAlmostEqual(a["v"], (b["v"] + .5) / 2, delta=.02)
            self.assertAlmostEqual(c["v"], round(a["v"] / b["v"] * 100), delta=1)
        self.assertEqual(h["unit"], "ч")

    def test_sleep_vs_need(self):
        """Нужно = базовая + долг + после нагрузки − дневной сон (по модулю): 8,5 + 0,4 + 0,2 = 9,1 ч."""
        r = {"score": {"sleep_needed": {"baseline_milli": 8.5 * H, "need_from_sleep_debt_milli": .4 * H,
                                        "need_from_recent_strain_milli": .2 * H, "need_from_recent_nap_milli": -.5 * H}}}
        self.assertEqual(TR.sleep_need(r), 8.6)
        r["score"]["sleep_needed"]["need_from_recent_nap_milli"] = .5 * H  # знак не важен — дневной сон вычитается
        self.assertEqual(TR.sleep_need(r), 8.6)
        out = call({"metric": "sleep_hours", "period": "w"})
        p = out["points"][-1]
        self.assertEqual(p["need"], 9.1)
        self.assertIn("Нужно было в среднем 9\u00a0ч\u00a006\u00a0мин", out["text"])
        pct = call({"metric": "sleep_need_pct", "period": "w"})["points"][-1]["v"]
        self.assertEqual(pct, round(p["v"] / 9.1 * 100))

    def test_steps_saved_and_partial_today(self):
        rec = DB.records(T.U, "cycle", "2026-10-10", "2026-10-10")[0]
        self.assertIn("step_count", rec)  # синхронизация хранит запись целиком — шаги не теряются
        out = call({"metric": "steps", "period": "w"})
        last = out["points"][-1]
        self.assertTrue(last.get("partial"))  # сегодня цикл ещё идёт
        full = [p["v"] for p in out["points"] if not p.get("partial")]
        self.assertAlmostEqual(out["avg"], sum(full) / len(full), delta=1)
        self.assertEqual(out["days"], 6)

    def test_month_and_half_year(self):
        m = call({"metric": "recovery", "period": "m"})
        self.assertEqual(len(m["points"]), 30)
        self.assertEqual(m["label"], "12 сен – 11 окт")
        self.assertIn("к прошлому месяцу", m["vs"])
        y = call({"metric": "strain", "period": "6m"})
        self.assertEqual(len(y["points"]), 180)
        self.assertIsNone(y["prev_avg"])  # данных за прошлые полгода нет
        self.assertIsNone(y["change_pct"])
        self.assertFalse(y["can_back"])

    def test_back_in_history(self):
        out = call({"metric": "rhr", "period": "w", "end": "2026-10-04"})
        self.assertEqual((out["since"], out["until"]), ("2026-09-28", "2026-10-04"))
        self.assertTrue(out["can_next"])
        future = call({"metric": "rhr", "period": "w", "end": "2027-01-01"})
        self.assertEqual(future["until"], "2026-10-11")  # вперёд — не дальше сегодня

    def test_temperature_abs(self):
        out = call({"metric": "skin_temp", "period": "w"})
        self.assertIsNone(out["change_pct"])
        self.assertEqual(out["avg"], 33.4)
        self.assertIn("°C", out["text"])

    def test_empty(self):
        db = store.Store(os.path.join(tempfile.mkdtemp(), "e.db"))
        db.user(T.U, "Тест")
        out = call({"metric": "hrv", "period": "m"}, db)
        self.assertEqual(len(out["points"]), 30)
        self.assertTrue(all(p["v"] is None for p in out["points"]))
        self.assertIsNone(out["avg"])
        self.assertIsNone(out["typical"])
        self.assertIn("данных нет", out["text"])
        self.assertFalse(out["can_back"])

    def test_weight_source(self):
        out = call({"metric": "weight", "period": "m"})
        self.assertIsNone(out["avg"])
        self.assertIn("61,5", out["text"])  # текущий вес WHOOP — подписью
        mod = type("W", (), {"trend_points": staticmethod(lambda app, uid, a, b: [
            {"day": "2026-10-01", "kg": 61.0}, ("2026-10-08", 61.6), ("2025-01-01", 50)])})
        with mock.patch.dict(coach.FEATURES, {"weight": mod}):
            out = call({"metric": "weight", "period": "m"})
        by = {p["day"]: p["v"] for p in out["points"] if p["v"] is not None}
        self.assertEqual(by, {"2026-10-01": 61.0, "2026-10-08": 61.6})
        self.assertEqual(out["avg"], 61.3)

    def test_one_query_per_kind(self):
        calls = []
        orig = DB.records

        def spy(uid, kind, since=None, until=None):
            calls.append(kind)
            return orig(uid, kind, since, until)
        with mock.patch.object(DB, "records", spy):
            call({"metric": "zones_all", "period": "6m"})
        self.assertEqual(sorted(calls), ["cycle", "workout"])


if __name__ == "__main__":
    unittest.main()
