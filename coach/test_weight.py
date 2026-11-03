"""Вес и состав тела: сохранение и правка замеров, границы, удаление, лимит, разбор текста (несколько форматов весов),
изменения и тренд, нормы еды/воды/белка по свежему весу, строка для тренера, скрин весов (Claude — заглушкой), series.
usage: python3 coach/test_weight.py
"""
import datetime
import json
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
import f_weight as W  # noqa: E402
import insights  # noqa: E402
import whoop  # noqa: E402
from test_skin import GRAY_URL  # noqa: E402  серая картинка вместо настоящего скрина

D = datetime.date
TODAY = D(2026, 11, 4)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, _ = T.seeded(os.path.join(tempfile.mkdtemp(), "weight.db"))

    def setUp(self):
        coach.db = self.db
        self.db.x("DELETE FROM meta WHERE key LIKE 'weight:%'")
        self.root = tempfile.mkdtemp()
        p = mock.patch.object(coach, "DATA", self.root)
        p.start()
        self.addCleanup(p.stop)

    def call(self, path, body=None, day=TODAY, hhmm=(7, 32)):
        now = datetime.datetime.combine(day, datetime.time(*hhmm), tzinfo=ZoneInfo("Europe/Moscow"))
        with mock.patch.object(coach, "user_now", lambda u: now), mock.patch.object(coach, "today_of", lambda uid: day):
            return coach.route(T.U, path, body or {})

    def add(self, day, kg, **kw):
        return self.call("/api/weight/save", {"day": day.isoformat(), "kg": kg, **kw})["saved"]


class SaveTest(Base):
    def test_save_minimal_and_full(self):
        r = self.call("/api/weight/save", {"kg": "63,4"})
        m = r["saved"]
        self.assertEqual((m["kg"], m["day"], m["time"], m["src"]), (63.4, "2026-11-04", "07:32", "manual"))
        self.assertIsNone(m["fat_pct"])
        self.assertEqual(r["count"], 1)
        self.assertEqual(r["last"]["id"], m["id"])
        full = self.call("/api/weight/save", {"kg": 63.1, "fat_pct": "14,2", "muscle_kg": 50.1, "lean_kg": 54.2, "water_pct": 58,
                                              "bone_kg": 2.8, "visceral": 4, "bmr": "1650", "bmi": 20.1, "protein_pct": 18,
                                              "note": "после сна", "src": "photo", "time": "7:05"})["saved"]
        self.assertEqual((full["fat_pct"], full["bmr"], full["time"], full["src"], full["note"]), (14.2, 1650, "07:05", "photo", "после сна"))
        items = self.call("/api/weight")["items"]
        self.assertEqual([x["id"] for x in items], [m["id"], full["id"]])  # новые сверху: 07:32 позже 07:05

    def test_ranges(self):
        bad = [{"kg": ""}, {"kg": 24.9}, {"kg": 201}, {"kg": "abc"}, {"kg": True}, {"kg": 60, "fat_pct": 70},
               {"kg": 60, "water_pct": 20}, {"kg": 60, "bmr": 300}, {"kg": 60, "visceral": 40}, {"kg": 60, "muscle_kg": 61},
               {"kg": 60, "lean_kg": 60}, {"kg": 60, "day": "2026-11-05"}, {"kg": 60, "day": "2026-02-30"},
               {"kg": 60, "day": "04.11.2026"}, {"kg": 60, "day": "2020-01-01"}, {"kg": 60, "time": "25:00"},
               {"kg": 60, "note": "x" * 121}, {"kg": "nan"}]
        for b in bad:
            with self.subTest(b=b), self.assertRaises(ValueError):
                self.call("/api/weight/save", b)
        self.assertEqual(self.call("/api/weight")["count"], 0)
        ok = self.call("/api/weight/save", {"kg": 25, "fat_pct": 2, "day": "2026-10-01", "src": "evil"})["saved"]
        self.assertEqual((ok["src"], ok["time"]), ("manual", ""))  # задним числом — без «сейчас»

    def test_edit_and_delete(self):
        m = self.add(TODAY, 63.4, fat_pct=14.2)
        e = self.call("/api/weight/save", {"id": m["id"], "kg": "63,0", "fat_pct": ""})["saved"]
        self.assertEqual((e["id"], e["kg"], e["fat_pct"], e["day"]), (m["id"], 63.0, None, m["day"]))
        self.assertEqual(self.call("/api/weight")["count"], 1)
        with self.assertRaises(ValueError):
            self.call("/api/weight/save", {"id": "nope", "kg": 60})
        with self.assertRaises(ValueError):
            self.call("/api/weight/delete", {"id": "nope"})
        r = self.call("/api/weight/delete", {"id": m["id"]})
        self.assertEqual((r["count"], r["last"]), (0, None))

    def test_limit(self):
        items = [{"id": f"x{i:03d}", "day": (TODAY - datetime.timedelta(days=500 - i)).isoformat(), "time": "07:00", "kg": 60,
                  "note": "", "src": "manual"} for i in range(W.MAX_ITEMS)]
        self.db.meta(f"weight:{T.U}", json.dumps(items))
        r = self.call("/api/weight/save", {"kg": 61})
        self.assertEqual(r["count"], W.MAX_ITEMS)
        self.assertEqual(len(r["items"]), W.MAX_ITEMS)
        self.assertNotIn("x000", [x["id"] for x in r["items"]])  # самый старый ушёл
        self.assertEqual(r["items"][0]["kg"], 61)

    def test_broken_storage(self):
        self.db.meta(f"weight:{T.U}", "{oops")
        self.assertEqual(self.call("/api/weight")["count"], 0)
        self.db.meta(f"weight:{T.U}", json.dumps([{"id": "a", "day": "2026-11-01", "kg": 999}, "x",
                                                  {"id": "b", "day": "2026-11-02", "kg": 62}]))
        self.assertEqual([x["id"] for x in self.call("/api/weight")["items"]], ["b"])

    def test_change_and_trend(self):
        for back, kg in [(60, 61.0), (29, 62.0), (20, 62.4), (10, 62.8), (3, 63.1), (0, 63.4)]:
            self.add(TODAY - datetime.timedelta(days=back), kg, fat_pct=14.0 + back / 100)
        self.add(TODAY, 64.0, fat_pct=15)  # вечером — для тренда берётся утренний
        r = self.call("/api/weight")
        ch = r["change"]
        self.assertEqual((ch["prev"]["kg"], ch["prev"]["days"]), (0.6, 0))
        self.assertEqual((ch["d30"]["kg"], ch["d30"]["days"]), (2.0, 29))
        t30, t90 = r["trend"]["d30"], r["trend"]["d90"]
        self.assertEqual((t30["n"], t30["first"], t30["last"], t30["word"]), (5, 62.0, 63.4, "растёт"))
        self.assertAlmostEqual(t30["per_week"], 0.36, delta=0.05)
        self.assertEqual(t90["n"], 6)
        flat = W.trend([{"day": "2026-10-18", "kg": 60, "time": ""}, {"day": "2026-11-03", "kg": 60.1, "time": ""}], TODAY, 30)
        self.assertEqual(flat["word"], "держится ровно")
        self.assertEqual(W.trend([], TODAY, 30), {"n": 0, "days": 30})

    def test_series_for_trends(self):
        self.add(TODAY - datetime.timedelta(days=5), 62.5, fat_pct=15.0)
        self.add(TODAY - datetime.timedelta(days=1), 63.0, lean_kg=54.0, muscle_kg=50.0)
        self.add(TODAY - datetime.timedelta(days=1), 63.6)  # второй за день — в series один (первый), n = 2
        self.add(TODAY - datetime.timedelta(days=40), 61.0)
        rows = W.series(self.db, T.U, TODAY - datetime.timedelta(days=30), TODAY.isoformat())
        self.assertEqual([r["day"] for r in rows], ["2026-10-30", "2026-11-03"])
        a, b = rows
        self.assertEqual((a["kg"], a["fat"], a["lean"], a["lean_est"]), (62.5, 15.0, 53.1, True))
        self.assertEqual((b["kg"], b["lean"], b["lean_est"], b["muscle"], b["n"]), (63.0, 54.0, False, 50.0, 2))
        for k in ("day", "kg", "fat", "muscle", "lean", "water", "bone", "visceral", "bmr", "bmi", "protein"):
            self.assertIn(k, a)


class ParseTest(unittest.TestCase):
    def test_short_line(self):
        item, bad = W.parse_text("вес 63,4 жир 14,2% мышцы 50,1 вода 58%")
        self.assertEqual(item, {"kg": 63.4, "fat_pct": 14.2, "muscle_kg": 50.1, "water_pct": 58.0})
        self.assertEqual(bad, [])

    def test_app_copy_ru(self):
        item, _ = W.parse_text("Вес: 63.4 кг\nИМТ 20,1\nЖир: 14.2 %\nМышечная масса 50.1 кг\nВода 58%\nВисцеральный жир 4\n"
                               "Базальный метаболизм 1 650 ккал\nКостная масса 2,8 кг\nБелок 18%\nМетаболический возраст 15\n"
                               "Безжировая масса 54,4 кг")
        self.assertEqual(item, {"kg": 63.4, "bmi": 20.1, "fat_pct": 14.2, "muscle_kg": 50.1, "water_pct": 58.0, "visceral": 4.0,
                                "bmr": 1650, "bone_kg": 2.8, "protein_pct": 18.0, "lean_kg": 54.4})

    def test_english(self):
        item, _ = W.parse_text("Weight 63.4kg | Body fat percentage 14.2% | Muscle mass 50.1kg | BMI 20.1 | BMR 1650 kcal | "
                               "Visceral fat 4 | Body water 58% | Bone mass 2.8kg | Protein 18% | Body age 15")
        self.assertEqual(item, {"kg": 63.4, "fat_pct": 14.2, "muscle_kg": 50.1, "bmi": 20.1, "bmr": 1650, "visceral": 4.0,
                                "water_pct": 58.0, "bone_kg": 2.8, "protein_pct": 18.0})

    def test_bare_numbers_units_and_noise(self):
        self.assertEqual(W.parse_text("63,4")[0], {"kg": 63.4})
        self.assertEqual(W.parse_text("63,4 кг, жир 14 %")[0], {"kg": 63.4, "fat_pct": 14.0})
        self.assertEqual(W.parse_text("Вес 140 lbs")[0], {"kg": 63.5})
        self.assertEqual(W.parse_text("Жировая масса 9 кг\nВес 63 кг")[0], {"kg": 63.0, "fat_pct": 14.3})  # кг жира → %
        self.assertEqual(W.parse_text("04.11 07:32 вес 63,4 жир 14,2% (норма 10–20%)")[0], {"kg": 63.4, "fat_pct": 14.2})
        self.assertEqual(W.parse_text("Подкожный жир 12%\nВес 63")[0], {"kg": 63.0})
        item, bad = W.parse_text("вес 63 мышцы 70")
        self.assertEqual(item, {"kg": 63.0})
        self.assertTrue(bad)
        self.assertEqual(W.parse_text("вес 300")[0], {})
        self.assertEqual(W.parse_text("")[0], {})


class TextApiTest(Base):
    def test_parser_without_claude(self):
        with mock.patch.object(ai, "claude", side_effect=AssertionError("Claude не нужен")):
            r = self.call("/api/weight/text", {"text": "вес 63,4 жир 14,2% мышцы 50,1 вода 58%"})
        self.assertEqual((r["how"], r["item"]["kg"], r["item"]["src"], r["item"]["day"]), ("parser", 63.4, "text", "2026-11-04"))
        self.assertEqual(r["found"], ["kg", "fat_pct", "muscle_kg", "water_pct"])
        self.assertEqual(self.call("/api/weight")["count"], 0)  # предпросмотр не сохраняет

    def test_claude_fallback(self):
        out = json.dumps({"scale": True, "kg": 63.4, "fat_pct": 14.2, "muscle_kg": None, "day": None})
        with mock.patch.object(ai, "claude", return_value=out) as cl:
            r = self.call("/api/weight/text", {"text": "шестьдесят три и четыре, жира четырнадцать с копейками, 2 раза взвесился"})
        self.assertTrue(cl.called)
        self.assertEqual((r["how"], r["item"]["kg"], r["item"]["fat_pct"]), ("claude", 63.4, 14.2))
        with mock.patch.object(ai, "claude", side_effect=ai.AIError("нет")), self.assertRaises(ValueError):
            self.call("/api/weight/text", {"text": "я взвесился, всё норм 2"})
        with self.assertRaises(ValueError):
            self.call("/api/weight/text", {"text": "  "})


class PhotoTest(Base):
    def test_photo_preview_not_saved(self):
        seen = {}

        def fake(prompt, image=None, **kw):
            seen["prompt"], seen["image"], seen["exists"] = prompt, image, os.path.isfile(image)
            return 'Вот: {"scale": true, "kg": "63,4", "fat_pct": 14.2, "muscle_kg": 50.1, "lean_kg": 70, "water_pct": 58, ' \
                   '"bmr": 1650, "visceral": 4, "day": "2026-11-04", "time": "07:31"}'
        with mock.patch.object(ai, "claude", side_effect=fake):
            r = self.call("/api/weight/photo", {"image": GRAY_URL})
        it = r["item"]
        self.assertEqual((it["kg"], it["fat_pct"], it["muscle_kg"], it["bmr"], it["day"], it["time"], it["src"]),
                         (63.4, 14.2, 50.1, 1650, "2026-11-04", "07:31", "photo"))
        self.assertNotIn("lean_kg", it)  # больше веса — пропущено с пометкой
        self.assertTrue(r["skipped"])
        self.assertTrue(seen["exists"])
        self.assertFalse(os.path.exists(seen["image"]))  # скрин удалён сразу после ответа
        self.assertIn("умных весов", seen["prompt"])
        self.assertIn("2026", seen["prompt"])
        self.assertEqual(os.listdir(os.path.join(self.root, "photos", "weight")), [])
        self.assertEqual(self.call("/api/weight")["count"], 0)
        saved = self.call("/api/weight/save", it)["saved"]  # «Сохранить» в предпросмотре
        self.assertEqual((saved["kg"], saved["src"], saved["time"]), (63.4, "photo", "07:31"))

    def test_photo_not_scale_or_future_day(self):
        with mock.patch.object(ai, "claude", return_value='{"scale": false}'), self.assertRaises(ValueError):
            self.call("/api/weight/photo", {"image": GRAY_URL})
        with mock.patch.object(ai, "claude", return_value='{"scale": true, "kg": 63, "day": "2026-12-30"}'):
            r = self.call("/api/weight/photo", {"image": GRAY_URL})
        self.assertEqual(r["item"]["day"], "2026-11-04")  # дата в будущем — сегодня
        with self.assertRaises(ValueError):
            self.call("/api/weight/photo", {"image": "not base64!"})

    def test_http_photo(self):
        allow = mock.patch.object(coach, "ALLOWED", {T.U})
        allow.start()
        self.addCleanup(allow.stop)
        with mock.patch.object(ai, "claude", return_value='{"scale": true, "kg": 63}'):
            res = T.call("/api/weight/photo", {"image": GRAY_URL})
        self.assertEqual((res.code, res.json["item"]["kg"]), (200, 63))
        res = T.call("/api/weight/save", {"kg": 10})
        self.assertEqual(res.code, 400)
        self.assertIn("Вес", res.json["error"])


class NormsTest(Base):
    """Нормы еды, воды и белка — по свежему замеру весов, если он новее (не старше FRESH_DAYS)."""

    def setUp(self):
        super().setUp()
        self.db.save_profile(T.U, {"age": "15", "sex": "m", "weight": "60", "height": "175", "goal": "form"})
        self.addCleanup(lambda: self.db.save_profile(T.U, {"age": "", "sex": "", "weight": "", "height": "", "goal": ""}))

    def nut(self):
        return self.call("/api/all")["today"]["nutrition"]

    def test_fresh_scale_weight_used(self):
        before = self.nut()
        self.assertEqual((before["weight"], before["protein"], before["weight_day"]), (60.0, 96, None))
        with mock.patch.object(W, "_today", return_value=TODAY):
            self.add(TODAY - datetime.timedelta(days=1), 80.0)
            after = self.nut()
            self.assertEqual((after["weight"], after["protein"], after["weight_day"]), (80.0, 128, "2026-11-03"))
            self.assertGreater(after["water"], before["water"])  # 80 × 35 мл больше нормы подростка 2,6 л
            body = whoop.body(self.db, T.U)
            self.assertEqual(body["scale"]["kg"], 80.0)
            self.assertIn("вес 80.0 кг (умные весы, 03.11)", insights.profile_line(coach.profile(T.U), body))

    def test_old_scale_weight_ignored(self):
        with mock.patch.object(W, "_today", return_value=TODAY):
            self.add(TODAY - datetime.timedelta(days=W.FRESH_DAYS + 1), 70.0)
            self.assertNotIn("scale", whoop.body(self.db, T.U))
            self.assertEqual(self.nut()["weight"], 60.0)

    def test_weight_of(self):
        self.assertEqual(insights.weight_of({"weight": 60}, {"weight_kilogram": 58, "scale": {"kg": 63.4}}), 63.4)
        self.assertEqual(insights.weight_of({"weight": 60}, {"weight_kilogram": 58}), 60)
        self.assertEqual(insights.weight_of({}, {"weight_kilogram": 58, "scale": {}}), 58)


class CoachTest(Base):
    def test_context_line_and_extra(self):
        self.assertEqual(W.context_lines(coach, T.U, TODAY), [])
        self.add(TODAY - datetime.timedelta(days=30), 62.8, fat_pct=14.8, muscle_kg=49.6)
        self.add(TODAY - datetime.timedelta(days=3), 63.1)
        self.call("/api/weight/save", {"kg": 63.4, "fat_pct": 14.2, "muscle_kg": 50.1, "time": "07:10"})
        lines = W.context_lines(coach, T.U, TODAY)
        self.assertTrue(lines[0].startswith("ВЕС (умные весы, вводит сам): 63,4 кг (замер 04.11 в 07:10, утром)"), lines[0])
        self.assertIn("жир 14,2 %", lines[0])
        self.assertIn("мышцы 50,1 кг", lines[0])
        self.assertIn("к прошлому замеру (01.11) +0,3 кг", lines[0])
        self.assertIn("за 30 дн. +0,6 кг, жир −0,6 п.п., мышцы +0,5 кг", lines[0])
        self.assertIn("не советуй худеть", lines[1])
        with mock.patch.object(coach, "user_now", lambda u: datetime.datetime(2026, 11, 4, 12, 0, tzinfo=ZoneInfo("Europe/Moscow"))), \
                mock.patch.object(coach, "today_of", lambda uid: TODAY):
            ctx, _ = coach.context(T.U, TODAY)
        self.assertIn("ВЕС (умные весы", ctx)
        ext = self.call("/api/all")["ext"]["weight"]
        self.assertEqual((ext["count"], ext["last"]["kg"], ext["prev"]["kg"], len(ext["spark"])), (3, 63.4, 63.1, 2))



class AssistTest(unittest.TestCase):
    def test_coach_chat_weight_goes_to_scale_log(self):
        import types
        import store as ST
        db = ST.Store(os.path.join(tempfile.mkdtemp(), "w.db"))
        app = types.SimpleNamespace(db=db)
        today = datetime.date(2026, 11, 4)
        self.assertIsNone(W.assist(app, 1, {"type": "water", "ml": 300}, today))
        r = W.assist(app, 1, {"type": "weight", "kg": 63.4}, today)
        self.assertEqual(r["label"], "вес 63,4 кг")
        self.assertEqual(W.load(db, 1)[-1]["kg"], 63.4)
        r["undo"]()
        self.assertEqual(W.load(db, 1), [])
        with self.assertRaises(ValueError):
            W.assist(app, 1, {"type": "weight", "kg": 400}, today)

if __name__ == "__main__":
    unittest.main(verbosity=1)
