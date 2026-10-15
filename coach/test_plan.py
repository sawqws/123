"""«Мой план»: прогресс недели пн–вс на поддельных данных WHOOP, вода и еда, планер сна, сохранение целей.
usage: python3 coach/test_plan.py
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
import f_plan  # noqa: E402
import insights  # noqa: E402

WED = datetime.date(2026, 10, 7)  # середина недели 5–11 октября (T.TODAY = вс 11.10)


class PlanTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, _ = T.seeded()

    def setUp(self):
        coach.db = self.db
        self.db.x("DELETE FROM water")
        self.db.x("DELETE FROM meals")
        self.db.x("DELETE FROM goals")
        f_plan._db(coach)
        self.db.x("DELETE FROM plan")
        self.db.save_profile(T.U, {"age": "15", "sex": "m", "schedule": ""})

    def call(self, path, body=None, day=T.TODAY):
        now = datetime.datetime.combine(day, datetime.time(13, 40))
        with mock.patch.object(coach, "user_now", lambda u: now), mock.patch.object(coach, "today_of", lambda uid: day):
            return coach.route(T.U, path, body or {})

    def goal(self, r, gid):
        return next(g for g in r["goals"] if g["id"] == gid)

    def days(self, until=T.TODAY):
        return insights.by_day(self.db, T.U, "2026-09-01", until.isoformat())

    # ---------- неделя ----------

    def test_week_bounds_and_dots(self):
        r = self.call("/api/plan")
        self.assertEqual(r["week"][0], "2026-10-05")  # понедельник
        self.assertEqual(r["week"][-1], "2026-10-11")
        for g in r["goals"]:
            self.assertEqual(len(g["dots"]), 7)
            self.assertTrue(set(g["dots"]) <= {"done", "miss", "today", "future", "nodata"})
        r = self.call("/api/plan", day=WED)
        for g in r["goals"]:
            self.assertEqual(g["dots"][3:], ["future"] * 4, g["id"])  # чт–вс ещё впереди

    def test_workouts_count_and_default_from_history(self):
        r = self.call("/api/plan")
        w = self.goal(r, "workouts")
        days = self.days()
        real = sum(len((days.get(d) or {}).get("workouts") or []) for d in r["week"])
        self.assertEqual(w["count"], real)
        self.assertEqual(w["need"], 4)  # бокс 4 раза в неделю в поддельных данных
        self.assertEqual([x == "done" for x in w["dots"]], [bool((days.get(d) or {}).get("workouts")) for d in r["week"]])
        self.assertEqual(w["status"], "done" if real >= 4 else w["status"])
        # в среду: пн и ср сделаны, нужно ещё 2, впереди 4 дня (с сегодня) — успевает
        w = self.goal(self.call("/api/plan", day=WED), "workouts")
        self.assertEqual((w["count"], w["left"], w["status"]), (2, 2, "ok"))

    def test_workouts_default_from_profile(self):
        self.db.save_profile(T.U, {"schedule": "пн, ср, пт — 2 часа вечером"})
        self.assertEqual(self.goal(self.call("/api/plan"), "workouts")["need"], 3)
        self.db.save_profile(T.U, {"schedule": "5 раз в неделю"})
        self.assertEqual(self.goal(self.call("/api/plan"), "workouts")["need"], 5)

    def test_strain_in_band_by_recovery_rule(self):
        r = self.call("/api/plan")
        days = self.days()
        want = 0
        for d in r["week"]:
            v = days.get(d) or {}
            band = insights.strain_band(v.get("recovery"))
            sv = (v.get("cycle") or {}).get("strain")
            want += bool(band and sv is not None and band[0] <= sv <= band[1])
        self.assertEqual(self.goal(r, "strain")["count"], want)

    def test_sleep_average(self):
        r = self.call("/api/plan")
        s = self.goal(r, "sleep")
        days = self.days()
        perf = [days[d]["sleep"]["performance"] for d in r["week"] if days.get(d, {}).get("sleep")]
        self.assertAlmostEqual(s["avg"], sum(perf) / len(perf), places=1)
        self.assertEqual(s["status"] == "done", s["avg"] >= 85)
        self.assertNotIn("today", s["dots"])  # сон прошлой ночи уже посчитан — точка сегодня окончательная

    def test_water_and_protein_honest(self):
        r = self.call("/api/plan")
        self.assertEqual(self.goal(r, "water")["status"], "nodata")  # ничего не записано — не «провал»
        t = self.call("/api/all")["today"]
        norm = t["water"]["target"]
        self.db.add_water(T.U, "2026-10-06", "12:00", norm)  # вт — норма
        self.db.add_water(T.U, "2026-10-07", "12:00", 200)  # ср — мало
        w = self.goal(self.call("/api/plan"), "water")
        self.assertEqual(w["dots"][1:3], ["done", "miss"])
        self.assertEqual(w["dots"][0], "nodata")
        self.assertEqual(w["count"], 1)
        self.db.save_meal(T.U, {"day": "2026-10-08", "title": "курица", "protein": 200, "kcal": 900})
        self.db.save_meal(T.U, {"day": "2026-10-09", "title": "яблоко", "protein": 1, "kcal": 80})
        p = self.goal(self.call("/api/plan"), "protein")
        self.assertEqual(p["dots"][3:5], ["done", "miss"])
        self.assertFalse(p["on"])  # белок по умолчанию выключен

    def test_bedtime_window_wraps_midnight(self):
        self.assertEqual(f_plan._diff(f_plan._mins("23:50"), f_plan._mins("00:10")), -20)
        self.assertEqual(f_plan._diff(f_plan._mins("00:20"), f_plan._mins("23:55")), 25)
        days = self.days()
        sl = days["2026-10-08"]["sleep"]
        self.call("/api/plan/save", {"goals": {"bedtime": {"time": sl["start"]}}})
        b = self.goal(self.call("/api/plan"), "bedtime")
        self.assertEqual(b["time"], sl["start"])
        self.assertEqual(b["dots"][3], "done")

    # ---------- планер сна ----------

    def test_planner_modes(self):
        r = self.call("/api/plan/save", {"wake": "07:00"})
        p = r["planner"]
        self.assertEqual((p["wake"], p["mode"], p["wake_auto"]), ("07:00", "norm", False))
        for o in p["options"]:
            bed = 7 * 60 - round(p["need"] * o["pct"] / 100 * 60) - f_plan.FALL_ASLEEP
            bed -= bed % 5
            self.assertEqual(o["bed"], f_plan._hhmm(bed), o["mode"])
        peak, norm, low = (next(o for o in p["options"] if o["mode"] == m) for m in ("peak", "norm", "min"))
        m = f_plan._mins  # отбой может перейти за полночь — сравниваем по кругу суток
        self.assertGreater(f_plan._diff(m(norm["bed"]), m(peak["bed"])), 0)
        self.assertGreater(f_plan._diff(m(low["bed"]), m(norm["bed"])), 0)
        self.assertGreaterEqual(p["need"], 8.5)  # 15 лет — не меньше 8,5 ч

    def test_planner_saved_and_validated(self):
        r = self.call("/api/plan/save", {"sleep_mode": "peak"})
        self.assertEqual(r["planner"]["mode"], "peak")
        self.assertEqual(self.call("/api/plan")["planner"]["bed"],
                         next(o["bed"] for o in r["planner"]["options"] if o["mode"] == "peak"))
        with self.assertRaises(ValueError):
            self.call("/api/plan/save", {"sleep_mode": "max"})
        with self.assertRaises(ValueError):
            self.call("/api/plan/save", {"wake": "25:00"})
        r = self.call("/api/plan/save", {"wake": "6.30"})
        self.assertEqual(r["planner"]["wake"], "06:30")
        r = self.call("/api/plan/save", {"wake": None})
        self.assertTrue(r["planner"]["wake_auto"])

    # ---------- цели ----------

    def test_save_goals(self):
        r = self.call("/api/plan/save", {"goals": {
            "sleep": {"kind": "hours", "value": 8.3}, "workouts": {"n": 5}, "water": {"on": False},
            "protein": {"on": True, "n": 3}, "bogus": {"on": True}}})
        r = self.call("/api/plan")
        s = self.goal(r, "sleep")
        self.assertEqual((s["kind"], r["config"]["sleep"]["value"]), ("hours", 8.5))  # шаг полчаса
        self.assertEqual(self.goal(r, "workouts")["need"], 5)
        self.assertFalse(self.goal(r, "water")["on"])
        self.assertTrue(self.goal(r, "protein")["on"])
        self.assertEqual(r["total"], 5)
        with self.assertRaises(ValueError):
            self.call("/api/plan/save", {"goals": {"strain": {"n": 9}}})
        with self.assertRaises(ValueError):
            self.call("/api/plan/save", {"goals": {"sleep": {"kind": "pct", "value": 30}}})

    def test_all_context_and_texts(self):
        self.db.add_goal(T.U, "ложиться до 23:00")
        a = self.call("/api/all")
        p = a["ext"]["plan"]
        self.assertEqual(p["texts"][0]["text"], "ложиться до 23:00")
        self.assertEqual(p["total"], 5)
        now = datetime.datetime.combine(T.TODAY, datetime.time(13, 40))
        with mock.patch.object(coach, "user_now", lambda u: now):
            text, _ = coach.context(T.U, T.TODAY)
        self.assertIn("Мой план на неделю", text)
        self.assertIn(f"лечь в {p['planner']['bed']}", text)
        self.assertIn("Тренировки (4 в неделю)", text)


if __name__ == "__main__":
    unittest.main()
