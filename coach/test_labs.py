"""Анализы крови (модуль labs): хранение как есть, перевод единиц, флаги, границы с бланка, динамика, сохранение,
удаление, ошибки по-русски, ext в /api/all, строки для тренера с правилом «к врачу, без диет».
Все числа здесь — условные примеры, не чьи-то анализы.
usage: python3 coach/test_labs.py
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
import coach  # noqa: E402
import f_labs as L  # noqa: E402

D = datetime.date
TODAY = D(2026, 11, 5)
A, B = "2026-03-01", "2026-09-01"  # две условные сдачи


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, _ = T.seeded(os.path.join(tempfile.mkdtemp(), "labs.db"))

    def setUp(self):
        coach.db = self.db
        self.db.x("DELETE FROM meta WHERE key LIKE 'labs:%'")

    def call(self, path, body=None, day=TODAY):
        now = datetime.datetime.combine(day, datetime.time(9, 15), tzinfo=ZoneInfo("Europe/Moscow"))
        with mock.patch.object(coach, "user_now", lambda u: now), mock.patch.object(coach, "today_of", lambda uid: day):
            return coach.route(T.U, path, body or {})

    def put_raw(self, raw):
        self.db.meta(f"labs:{T.U}", json.dumps(raw))

    def raw(self):
        return json.loads(self.db.meta(f"labs:{T.U}") or "{}")


class StoreTest(Base):
    def test_existing_format_read_and_kept(self):
        """Формат, как уже лежит на сервере ({день: {key: число}}): читается, флаги считаются, запись — не переписывает."""
        raw = {A: {"hemoglobin": 13.5, "ferritin": 15, "glucose": 4.8, "zzz_new": 1},
               B: {"hemoglobin": 14.2, "ferritin": 28, "crp": 0.6}}
        self.put_raw(raw)
        r = self.call("/api/labs")
        self.assertEqual(r["count"], 2)
        lt = {x["key"]: x for x in r["latest"]}
        self.assertEqual((lt["hemoglobin"]["v"], lt["hemoglobin"]["unit"], lt["hemoglobin"]["unit_in"]), (142, "г/л", "г/дл"))
        self.assertEqual(lt["hemoglobin"]["flag"], "ok")
        self.assertEqual(lt["ferritin"]["prev"], {"v": 15, "day": A})
        self.assertEqual((lt["ferritin"]["delta"], lt["ferritin"]["trend"], lt["ferritin"]["better"]), (13, "up", "better"))
        self.assertEqual(lt["glucose"]["day"], A)  # глюкоза только в первой сдаче — она и последняя
        self.assertNotIn("zzz_new", lt)
        self.assertEqual([d["day"] for d in r["days"]], [B, A])
        # дописали одно значение — всё прежнее (и неизвестный ключ) лежит как было
        self.call("/api/labs/save", {"day": B, "values": {"magnesium": "0,85"}})
        after = self.raw()
        self.assertEqual(after[A], raw[A])
        self.assertEqual(after[B], {**raw[B], "magnesium": 0.85})

    def test_units_converted_on_read(self):
        self.put_raw({A: {"glucose": 90, "cholesterol": 160, "vitd": 75, "iron": 90, "tsat": 0.3, "protein": 7.2,
                          "magnesium": 2.0}})
        lt = {x["key"]: x for x in self.call("/api/labs")["latest"]}
        self.assertAlmostEqual(lt["glucose"]["v"], 5.0, places=1)
        self.assertAlmostEqual(lt["cholesterol"]["v"], 4.1, places=1)
        self.assertEqual(lt["vitd"]["v"], 75)  # 75 нг/мл — уже наши единицы
        self.assertAlmostEqual(lt["iron"]["v"], 16.1, places=1)
        self.assertEqual(lt["tsat"]["v"], 30)
        self.assertEqual(lt["protein"]["v"], 72)
        self.assertAlmostEqual(lt["magnesium"]["v"], 0.82, places=2)
        self.put_raw({A: {"vitd": 200}})  # 200 нмоль/л → ~80 нг/мл
        self.assertEqual(self.call("/api/labs")["latest"][0]["v"], 80)

    def test_flags_and_lab_range(self):
        self.put_raw({A: {"ferritin": 12, "crp": 9, "hdl": 1.4, "ldl": 2.0, "tsh": {"v": 4.8, "lo": 0.4, "hi": 5.0}}})
        lt = {x["key"]: x for x in self.call("/api/labs")["latest"]}
        self.assertEqual(lt["ferritin"]["flag"], "low")
        self.assertEqual(lt["crp"]["flag"], "high")
        self.assertEqual((lt["hdl"]["flag"], lt["hdl"]["hi"]), ("ok", None))
        self.assertEqual((lt["ldl"]["flag"], lt["ldl"]["lo"]), ("ok", None))
        self.assertEqual((lt["tsh"]["flag"], lt["tsh"]["lab"], lt["tsh"]["hi"]), ("ok", True, 5.0))  # бланк главнее нашей нормы
        s = self.call("/api/labs")["summary"]
        self.assertEqual((s["total"], s["ok"], s["low"], s["high"], s["day"]), (5, 3, ["ferritin"], ["crp"], A))

    def test_trend_same_and_worse(self):
        self.put_raw({A: {"glucose": 5.0, "ferritin": 25}, B: {"glucose": 5.05, "ferritin": 14}})
        lt = {x["key"]: x for x in self.call("/api/labs")["latest"]}
        self.assertEqual((lt["glucose"]["trend"], lt["glucose"]["better"]), ("same", ""))
        self.assertEqual((lt["ferritin"]["trend"], lt["ferritin"]["better"]), ("down", "worse"))
        self.assertEqual([p["day"] for p in self.call("/api/labs")["series"]["ferritin"]], [A, B])

    def test_broken_storage(self):
        self.db.meta(f"labs:{T.U}", "{не json")
        r = self.call("/api/labs")
        self.assertEqual((r["count"], r["latest"]), (0, []))
        self.put_raw({A: "строка", "плохой день": {"glucose": 5}, B: {"glucose": "abc", "ferritin": 30}})
        r = self.call("/api/labs")
        self.assertEqual([x["key"] for x in r["latest"]], ["ferritin"])


class SaveTest(Base):
    def test_save_list_with_lab_range_and_delete(self):
        r = self.call("/api/labs/save", {"day": B, "values": [{"key": "ferritin", "value": "30"},
                                                             {"key": "tsh", "value": "2,1", "lo": "0,4", "hi": "4"}]})
        self.assertEqual(r["saved"], {"day": B, "keys": ["ferritin", "tsh"]})
        self.assertEqual(self.raw()[B], {"ferritin": 30, "tsh": {"v": 2.1, "lo": 0.4, "hi": 4.0}})
        r = self.call("/api/labs/delete", {"day": B, "key": "tsh"})
        self.assertEqual(self.raw()[B], {"ferritin": 30})
        r = self.call("/api/labs/delete", {"day": B})
        self.assertEqual((r["count"], self.raw()), (0, {}))

    def test_errors(self):
        bad = [({"day": "2026-12-01", "values": {"glucose": 5}}, "будущего"),
               ({"day": "05.11.2026", "values": {"glucose": 5}}, "ГГГГ-ММ-ДД"),
               ({"day": A, "values": {}}, "хотя бы одно"),
               ({"day": A, "values": {"sugar": 5}}, "неизвестный"),
               ({"day": A, "values": {"glucose": "много"}}, "нужно число"),
               ({"day": A, "values": {"glucose": 0.1}}, "проверь число"),
               ({"day": A, "values": [{"key": "tsh", "value": 2, "lo": 5, "hi": 1}]}, "меньше верхней"),
               ({"day": A, "values": {f"k{i}": 1 for i in range(41)}}, "не больше 40")]
        for body, word in bad:
            with self.subTest(word=word), self.assertRaises(ValueError) as e:
                self.call("/api/labs/save", body)
            self.assertIn(word, str(e.exception))
        # одно неверное — ничего не записано
        with self.assertRaises(ValueError):
            self.call("/api/labs/save", {"day": A, "values": {"glucose": 5, "ferritin": "x"}})
        self.assertEqual(self.raw(), {})
        for body, word in [({"day": A}, "не найдена"), ({"day": A, "key": "glucose"}, "не найдена")]:
            with self.subTest(word=word), self.assertRaises(ValueError) as e:
                self.call("/api/labs/delete", body)
            self.assertIn(word, str(e.exception))
        self.call("/api/labs/save", {"day": A, "values": {"glucose": 5}})
        with self.assertRaises(ValueError) as e:
            self.call("/api/labs/delete", {"day": A, "key": "ferritin"})
        self.assertIn("Значение не найдено", str(e.exception))

    def test_unknown_path(self):
        with self.assertRaises(KeyError):
            self.call("/api/labs/zzz")
        self.assertIsNone(L.route(coach, T.U, "/api/labsx", {}, TODAY))

    def test_comma_and_signs(self):
        self.call("/api/labs/save", {"day": A, "values": {"crp": "< 0,5", "b12": "1 200"}})
        self.assertEqual(self.raw()[A], {"crp": 0.5, "b12": 1200})


class CoachTest(Base):
    def test_extra_in_all_and_context(self):
        self.assertEqual(L.context_lines(coach, T.U, TODAY), [])
        self.put_raw({A: {"ferritin": 15, "glucose": 4.8}, B: {"ferritin": 28, "crp": 7}})
        e = L.extra(coach, T.U, TODAY)
        self.assertEqual([x["key"] for x in e["latest"]], ["ferritin", "glucose", "crp"])
        self.assertEqual(e["summary"]["high"], ["crp"])
        with mock.patch.object(coach, "profile", lambda uid: {"age": "14"}):
            lines = L.context_lines(coach, T.U, TODAY)
        text = "\n".join(lines)
        self.assertIn("АНАЛИЗЫ КРОВИ", text)
        self.assertIn("сдано 01.09.2026", text)
        self.assertIn("ферритин 28 нг/мл (норма 20–150) — норма; было 15 (01.03.2026), ближе к норме", text)
        self.assertIn("глюкоза 4,8 ммоль/л (норма 3,3–5,6) — норма, сдано 01.03.2026", text)
        self.assertIn("— ВЫШЕ НОРМЫ", text)
        self.assertIn("к врачу", text)
        self.assertIn("Диеты в 14 лет не назначай", text)
        self.assertIn("не диагноз", text)
        with mock.patch.object(coach, "profile", lambda uid: {}):
            self.assertIn("Диет не назначай", "\n".join(L.context_lines(coach, T.U, TODAY)))
        # тренер видит строки через общий context()
        with mock.patch.object(coach, "profile", lambda uid: {"age": 14}):
            ctx, _ = coach.context(T.U, TODAY)
        self.assertIn("АНАЛИЗЫ КРОВИ", ctx)

    def test_api_all_has_labs(self):
        self.put_raw({A: {"glucose": 5}})
        self.assertIn("labs", coach.FEATURES)
        ext = coach.features_extra(T.U, TODAY)
        self.assertEqual(ext["labs"]["latest"][0]["key"], "glucose")

    def test_known_consistent(self):
        for k, v in L.KNOWN.items():
            lo, hi, (pmin, pmax) = v[2], v[3], v[5]
            self.assertIn(v[6], L.GROUPS, k)
            self.assertTrue(lo is not None or hi is not None, k)
            for b in (lo, hi):
                if b is not None:
                    self.assertTrue(pmin < b < pmax, k)
            if lo is not None and hi is not None:
                self.assertLess(lo, hi, k)


if __name__ == "__main__":
    unittest.main()
