"""Ещё из WHOOP (модуль wx): сохранение и замена, лимит, границы, отсев того, что есть в API, дневник и «другое»,
скрин WHOOP (Claude — заглушкой: дата видна / не видна / в будущем), payload (порядок latest, series), удаление,
ext в /api/all, строка для тренера.
usage: python3 coach/test_wx.py
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
import f_wx as X  # noqa: E402
from test_skin import GRAY_URL  # noqa: E402  серая картинка вместо настоящего скрина

D = datetime.date
TODAY = D(2026, 11, 5)


def ago(n):
    return (TODAY - datetime.timedelta(days=n)).isoformat()


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, _ = T.seeded(os.path.join(tempfile.mkdtemp(), "wx.db"))

    def setUp(self):
        coach.db = self.db
        self.db.x("DELETE FROM meta WHERE key LIKE 'wx:%'")
        self.root = tempfile.mkdtemp()
        p = mock.patch.object(coach, "DATA", self.root)
        p.start()
        self.addCleanup(p.stop)

    def call(self, path, body=None, day=TODAY):
        now = datetime.datetime.combine(day, datetime.time(9, 15), tzinfo=ZoneInfo("Europe/Moscow"))
        with mock.patch.object(coach, "user_now", lambda u: now), mock.patch.object(coach, "today_of", lambda uid: day):
            return coach.route(T.U, path, body or {})

    def add(self, day, *metrics, **kw):
        return self.call("/api/wx/save", {"day": day, "metrics": list(metrics), **kw})


class SaveTest(Base):
    def test_save_known_and_replace(self):
        r = self.add(ago(4), {"key": "vo2max", "value": "51,3"})
        self.assertEqual(r["count"], 1)
        it = r["items"][0]
        self.assertEqual((it["key"], it["label"], it["value"], it["text"], it["unit"], it["day"], it["time"], it["src"]),
                         ("vo2max", "VO₂ max", 51.3, None, "мл/кг/мин", ago(4), "", "manual"))
        self.assertTrue(it["id"] and isinstance(it["at"], int))
        self.assertEqual(r["saved"][0]["id"], it["id"])
        # тот же день и показатель — замена (id тот же), другой день — новая запись
        r = self.add(ago(4), {"key": "vo2max", "value": 52, "label": "чужая подпись", "unit": "чужая"}, time="8:05", src="photo")
        self.assertEqual(r["count"], 1)
        it2 = r["items"][0]
        self.assertEqual((it2["id"], it2["value"], it2["label"], it2["unit"], it2["time"], it2["src"]),
                         (it["id"], 52.0, "VO₂ max", "мл/кг/мин", "08:05", "photo"))
        r = self.add(ago(1), {"key": "vo2max", "value": 52.4})
        self.assertEqual(r["count"], 2)
        self.assertEqual(self.add("", {"key": "stress", "value": 1.2})["items"][0]["day"], TODAY.isoformat())  # без даты — сегодня

    def test_journal_and_other(self):
        r = self.add(ago(0), {"key": "journal", "label": "Пил кофеин?", "text": "Нет"},
                     {"key": "journal", "label": "Сколько часов за экраном", "value": "3,5", "unit": "ч"},
                     {"key": "other", "label": "Время силовых", "text": "1,5", "unit": "ч"},
                     {"key": "journal", "label": "Читал перед сном", "value": True},
                     {"key": "other", "label": "Мышечная нагрузка", "value": 7.24})
        self.assertEqual(r["count"], 5)
        by = {x["label"]: x for x in r["items"]}
        self.assertEqual((by["Пил кофеин?"]["text"], by["Пил кофеин?"]["value"]), ("Нет", None))
        self.assertEqual((by["Сколько часов за экраном"]["value"], by["Сколько часов за экраном"]["unit"]), (3.5, "ч"))
        self.assertEqual((by["Время силовых"]["value"], by["Время силовых"]["text"]), (1.5, None))  # число текстом — число
        self.assertEqual(by["Читал перед сном"]["text"], "Да")  # дневник — сон в вопросе не мешает (отсев только у other)
        self.assertEqual(by["Мышечная нагрузка"]["value"], 7.24)  # её нет в API — берём
        # тот же вопрос в другом регистре — замена; другой вопрос — новая запись
        r = self.add(ago(0), {"key": "journal", "label": "пил  КОФЕИН?", "text": "Да"})
        self.assertEqual(r["count"], 5)
        self.assertEqual(next(x for x in r["items"] if x["key"] == "journal" and "офеин" in x["label"].lower())["text"], "Да")
        self.assertEqual(self.add(ago(0), {"key": "other", "label": "Время силовых 2", "value": 1})["count"], 6)

    def test_ranges(self):
        good = {"vo2max": (10, 90), "whoop_age": (5, 100), "pace": (-1, 3), "stress": (0, 3), "bp_sys": (70, 200),
                "bp_dia": (40, 130)}
        for key, (lo, hi) in good.items():
            for v in (lo - 0.5, hi + 0.5, "abc", True, "nan", None, ""):
                with self.subTest(key=key, v=v), self.assertRaises(ValueError):
                    self.add(ago(0), {"key": key, "value": v})
            self.add(ago(0), {"key": key, "value": lo}, {"key": key, "value": hi})
        bad = [{"metrics": []}, {"metrics": "x"}, {"metrics": [{"key": "nope", "value": 1}]}, {"metrics": ["x"]},
               {"metrics": [{"key": "journal", "text": "Да"}]}, {"metrics": [{"key": "journal", "label": "x" * 61, "text": "Да"}]},
               {"metrics": [{"key": "other", "label": "x" * 41, "value": 1}]},
               {"metrics": [{"key": "other", "label": "Что-то", "text": "x" * 41}]},
               {"metrics": [{"key": "other", "label": "Что-то", "value": 1, "unit": "x" * 17}]},
               {"metrics": [{"key": "other", "label": "Что-то"}]}, {"metrics": [{"key": "other", "label": "Что-то", "value": 1e9}]},
               {"metrics": [{"key": "other", "label": "Что-то", "value": [1]}]},
               {"metrics": [{"key": "vo2max", "value": 50}] * (X.MAX_METRICS + 1)},
               {"day": "2026-11-06", "metrics": [{"key": "vo2max", "value": 50}]},
               {"day": "05.11.2026", "metrics": [{"key": "vo2max", "value": 50}]},
               {"day": "2026-02-30", "metrics": [{"key": "vo2max", "value": 50}]},
               {"day": "2020-01-01", "metrics": [{"key": "vo2max", "value": 50}]},
               {"time": "25:00", "metrics": [{"key": "vo2max", "value": 50}]},
               # одно неверное — не пишется ничего
               {"metrics": [{"key": "vo2max", "value": 50}, {"key": "stress", "value": 9}]}]
        n = self.call("/api/wx")["count"]
        for b in bad:
            with self.subTest(b=str(b)[:80]), self.assertRaises(ValueError):
                self.call("/api/wx/save", b)
        self.assertEqual(self.call("/api/wx")["count"], n)
        with self.assertRaises(ValueError) as e:
            self.add(ago(0), {"key": "bp_sys", "value": 250})
        self.assertEqual(str(e.exception), "Давление, верхнее: от 70 до 200 мм рт. ст.")
        for key, v, text in (("stress", 7, "Стресс: от 0 до 3"), ("pace", -2, "Темп старения: от −1 до 3")):
            with self.assertRaises(ValueError) as e:
                self.add(ago(0), {"key": key, "value": v})
            self.assertEqual(str(e.exception), text)

    def test_api_metrics_rejected(self):
        for label in ("Восстановление", "Recovery", "HRV", "Гибкость пульса", "Пульс покоя", "Resting Heart Rate", "Сон",
                      "Hours of Sleep", "Глубокий сон", "Стадии сна", "Нагрузка", "Strain", "Шаги", "Steps", "SpO₂",
                      "Температура кожи", "Skin Temp", "Частота дыхания", "Respiratory Rate", "Калории", "Calories", "Avg HR"):
            with self.subTest(label=label), self.assertRaises(ValueError) as e:
                self.add(ago(0), {"key": "other", "label": label, "value": 50})
            self.assertIn("уже берёт из WHOOP", str(e.exception))
        self.assertEqual(self.call("/api/wx")["count"], 0)

    def test_delete(self):
        r = self.add(ago(0), {"key": "vo2max", "value": 50}, {"key": "stress", "value": 1})
        mid = r["saved"][0]["id"]
        r = self.call("/api/wx/delete", {"id": mid})
        self.assertEqual((r["count"], [x["key"] for x in r["items"]]), (1, ["stress"]))
        for b in ({"id": mid}, {"id": "nope"}, {}):
            with self.subTest(b=b), self.assertRaises(ValueError):
                self.call("/api/wx/delete", b)

    def test_limit(self):
        items = [{"id": f"x{i:03d}", "day": ago(700 - i), "time": "", "key": "stress", "label": "Стресс", "value": 1,
                  "text": None, "unit": "из 3", "src": "manual", "at": 1} for i in range(X.MAX_ITEMS)]
        self.db.meta(f"wx:{T.U}", json.dumps(items))
        r = self.add(ago(0), {"key": "vo2max", "value": 50})
        self.assertEqual(r["count"], X.MAX_ITEMS)
        stored = X.load(self.db, T.U)
        self.assertNotIn("x000", [x["id"] for x in stored])  # самый старый ушёл
        self.assertEqual(stored[-1]["key"], "vo2max")
        self.assertEqual(len(r["items"]), X.MAX_LIST)
        self.assertEqual(r["items"][0]["key"], "vo2max")  # новые сверху
        self.assertEqual(len(r["series"]["stress"]), X.MAX_SERIES)
        self.assertEqual(r["series"]["stress"][-1]["day"], ago(101))  # ряд — последние точки

    def test_broken_storage(self):
        self.db.meta(f"wx:{T.U}", "{oops")
        self.assertEqual(self.call("/api/wx")["count"], 0)
        self.db.meta(f"wx:{T.U}", json.dumps([
            {"id": "a", "day": ago(1), "key": "vo2max", "value": 999}, "x", {"id": "b", "day": "вчера", "key": "stress", "value": 1},
            {"key": "stress", "day": ago(1), "value": 1}, {"id": "c", "day": ago(2), "key": "stress", "value": 1, "src": "evil"}]))
        r = self.call("/api/wx")
        self.assertEqual([x["id"] for x in r["items"]], ["c"])
        self.assertEqual((r["items"][0]["src"], r["items"][0]["at"]), ("manual", 0))

    def test_unknown_path_and_http(self):
        with self.assertRaises(KeyError):
            self.call("/api/wx/nope")
        self.assertIsNone(X.route(coach, T.U, "/api/wxyz", {}, TODAY))
        allow = mock.patch.object(coach, "ALLOWED", {T.U})
        allow.start()
        self.addCleanup(allow.stop)
        res = T.call("/api/wx/save", {"metrics": [{"key": "vo2max", "value": 5}]})
        self.assertEqual(res.code, 400)
        self.assertIn("VO₂ max: от 10 до 90", res.json["error"])
        self.assertEqual(T.call("/api/wx/nope", {}).code, 404)
        self.assertEqual(T.call("/api/wx", {}).json["count"], 0)


class PayloadTest(Base):
    def test_latest_order_series_known(self):
        self.add(ago(40), {"key": "vo2max", "value": 49.8}, {"key": "whoop_age", "value": 13.6})
        self.add(ago(3), {"key": "other", "label": "Время силовых", "value": 1.5, "unit": "ч"})
        self.add(ago(2), {"key": "journal", "label": "Пил кофеин", "text": "Нет"},
                 {"key": "bp_dia", "value": 75}, {"key": "bp_sys", "value": 118})
        self.add(ago(1), {"key": "stress", "value": 0.8}, {"key": "journal", "label": "Пил кофеин", "text": "Да"},
                 {"key": "journal", "label": "Растяжка", "text": "Да"})
        self.add(ago(0), {"key": "pace", "value": -0.25}, {"key": "vo2max", "value": 51.3},
                 {"key": "other", "label": "Сухая масса", "value": 54.2, "unit": "кг"})
        r = self.call("/api/wx")
        lt = r["latest"]
        self.assertEqual([(x["key"], x["label"]) for x in lt], [
            ("vo2max", "VO₂ max"), ("whoop_age", "Возраст WHOOP"), ("pace", "Темп старения"), ("stress", "Стресс"),
            ("bp_sys", "Давление, верхнее"), ("bp_dia", "Давление, нижнее"),
            ("journal", "Пил кофеин"), ("journal", "Растяжка"),  # одинаково свежие — порядок добавления
            ("other", "Сухая масса"), ("other", "Время силовых")])
        self.assertEqual((lt[0]["value"], lt[0]["day"]), (51.3, ago(0)))  # самое свежее
        self.assertEqual(lt[6]["text"], "Да")
        self.assertEqual(set(lt[0]), {"id", "key", "label", "value", "text", "unit", "day", "time"})
        self.assertEqual(r["series"]["vo2max"], [{"day": ago(40), "v": 49.8}, {"day": ago(0), "v": 51.3}])
        self.assertEqual(r["series"]["pace"], [{"day": ago(0), "v": -0.25}])
        self.assertEqual(set(r["series"]), set(X.KNOWN))
        self.assertEqual(r["count"], 12)
        days = [x["day"] for x in r["items"]]
        self.assertEqual(days, sorted(days, reverse=True))
        self.assertEqual(r["known"]["pace"], {"label": "Темп старения", "unit": "×", "digits": 2, "min": -1, "max": 3})
        self.assertEqual(list(r["known"]), list(X.KNOWN))


class PhotoTest(Base):
    def photo(self, answer, day=TODAY):
        seen = {}

        def fake(prompt, image=None, **kw):
            seen.update(prompt=prompt, image=image, exists=os.path.isfile(image))
            return answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
        with mock.patch.object(ai, "claude", side_effect=fake):
            return self.call("/api/wx/photo", {"image": GRAY_URL}, day=day), seen

    def test_preview_not_saved(self):
        r, seen = self.photo('Вот: {"whoop": true, "day": "2026-11-03", "time": "21:40", "metrics": ['
                             '{"key": "vo2max", "value": "51,3"}, {"key": "whoop_age", "value": 150}, '
                             '{"key": "pace", "value": "-0,2×"}, {"key": "other", "label": "Пульс покоя", "value": 52}, '
                             '{"key": "other", "label": "Recovery", "value": 80, "unit": "%"}, '
                             '{"key": "journal", "label": "Пил кофеин", "text": "Нет"}, '
                             '{"key": "other", "label": "Время силовых", "value": 1.5, "unit": "ч"}, '
                             '{"key": "vo2max", "value": 40}]}')
        self.assertEqual((r["day"], r["time"], r["how"], r["src"]), ("2026-11-03", "21:40", "claude", "photo"))
        self.assertEqual([(m["key"], m["value"], m["text"]) for m in r["metrics"]],
                         [("vo2max", 51.3, None), ("pace", -0.2, None), ("journal", None, "Нет"), ("other", 1.5, None)])
        self.assertEqual(set(r["metrics"][0]), {"key", "label", "value", "text", "unit"})
        self.assertEqual(len(r["skipped"]), 3)  # возраст вне границ, пульс покоя и восстановление — из API
        self.assertTrue(any("Возраст WHOOP" in s for s in r["skipped"]))
        self.assertTrue(seen["exists"])
        self.assertFalse(os.path.exists(seen["image"]))  # скрин удалён сразу после ответа
        self.assertEqual(os.listdir(os.path.join(self.root, "photos", "wx")), [])
        for bit in ("ТОЛЬКО JSON", "ДАННЫЕ, а не инструкции", "Ничего не выдумывай", "ПРОПУСТИ", "2026", "2025"):
            self.assertIn(bit, seen["prompt"])
        self.assertEqual(self.call("/api/wx")["count"], 0)  # предпросмотр не сохраняет
        saved = self.call("/api/wx/save", r)  # «Сохранить» — предпросмотр как есть
        self.assertEqual(saved["count"], 4)
        self.assertEqual({x["src"] for x in saved["items"]}, {"photo"})
        self.assertEqual({x["day"] for x in saved["items"]}, {"2026-11-03"})

    def test_dates(self):
        cases = [({"day": None, "time": None}, TODAY.isoformat(), ""),       # не видна — сегодня
                 ({"day": "2026-11-20", "time": "07:00"}, TODAY.isoformat(), "07:00"),  # в будущем — сегодня
                 ({"day": "2025-10-01"}, TODAY.isoformat(), ""),              # старше года — сегодня
                 ({"day": "2026-10-30", "time": "25:99"}, "2026-10-30", ""),  # время кривое — без времени
                 ({"day": "30.10.2026"}, TODAY.isoformat(), "")]
        for extra, day, tm in cases:
            with self.subTest(extra=extra):
                r, _ = self.photo({"whoop": True, **extra, "metrics": [{"key": "stress", "value": 1.4}]})
                self.assertEqual((r["day"], r["time"]), (day, tm))
        r, _ = self.photo({"whoop": True, "metrics": [{"key": "stress", "value": 1}]}, day=D(2027, 1, 3))
        self.assertEqual(r["day"], "2027-01-03")

    def test_aliases_and_pressure(self):
        r, _ = self.photo({"whoop": True, "metrics": [
            {"key": "other", "label": "Blood Pressure", "text": "118/76", "unit": "mmHg"},
            {"key": "other", "label": "VO2 Max", "value": 50.2}, {"key": "other", "label": "Pace of Aging", "value": "0.9x"},
            {"key": "other", "label": "Stress", "value": 2.1}, {"key": "other", "label": "Время в стрессе", "value": 2, "unit": "ч"},
            {"key": "journal", "label": "В" * 80, "text": "Д" * 70}]})
        got = {m["key"]: m for m in r["metrics"]}
        self.assertEqual((got["bp_sys"]["value"], got["bp_dia"]["value"]), (118, 76))
        self.assertEqual((got["vo2max"]["value"], got["pace"]["value"], got["stress"]["value"]), (50.2, 0.9, 2.1))
        self.assertEqual([(m["label"], m["value"]) for m in r["metrics"] if m["key"] == "other"], [("Время в стрессе", 2)])
        self.assertEqual((len(got["journal"]["label"]), len(got["journal"]["text"])), (60, 40))  # длинное — обрезано

    def test_nothing_found(self):
        for answer in ('{"whoop": false}', "не вижу тут ничего", '{"whoop": true, "metrics": []}',
                       {"whoop": True, "metrics": [{"key": "other", "label": "Восстановление", "value": 80}]}):
            with self.subTest(answer=answer), self.assertRaises(ValueError) as e:
                self.photo(answer)
            self.assertTrue(str(e.exception).startswith("На скрине не нашёл показателей WHOOP — пришли экран с цифрами"))
        with self.assertRaises(ValueError):
            self.call("/api/wx/photo", {"image": "not base64!"})
        with mock.patch.object(ai, "claude", side_effect=ai.AIError("занят")), self.assertRaises(ai.AIError):
            self.call("/api/wx/photo", {"image": GRAY_URL})


class CoachTest(Base):
    def test_extra_and_context(self):
        self.assertEqual(X.context_lines(coach, T.U, TODAY), [])
        self.assertEqual(self.call("/api/all")["ext"]["wx"], {"latest": [], "count": 0})
        self.add("2026-10-20", {"key": "vo2max", "value": 49.0})
        self.add("2026-11-01", {"key": "vo2max", "value": 51.3}, {"key": "bp_sys", "value": 118}, {"key": "bp_dia", "value": 76})
        self.add("2026-11-03", {"key": "whoop_age", "value": 12.8}, {"key": "pace", "value": -0.25},
                 {"key": "journal", "label": "Пил кофеин", "text": "Да"})
        self.add("2026-11-05", {"key": "stress", "value": 1}, {"key": "journal", "label": "Пил кофеин", "text": "Нет"},
                 {"key": "journal", "label": "Растяжка вечером", "text": "Да"},
                 *[{"key": "other", "label": f"Цифра {i}", "value": i} for i in range(1, 7)])
        lines = X.context_lines(coach, T.U, TODAY)
        line = lines[0]
        self.assertTrue(line.startswith("ИЗ ПРИЛОЖЕНИЯ WHOOP (по скринам, не из API): VO₂ max 51,3 мл/кг/мин (01.11) · "
                                        "возраст WHOOP 12,8 лет (03.11) · темп старения −0,25× (03.11) · стресс 1 из 3 (05.11) · "
                                        "давление 118/76 мм рт. ст. (01.11) · "), line)
        self.assertNotIn("49", line)  # только последние
        self.assertEqual(line.split("; дневник")[0].count(" · "), 7)  # не больше 8 показателей
        self.assertIn("; дневник 05.11: пил кофеин — нет; растяжка вечером — да", line)
        self.assertIn("не тонометр", lines[1])
        with mock.patch.object(coach, "user_now", lambda u: datetime.datetime(2026, 11, 5, 12, 0, tzinfo=ZoneInfo("Europe/Moscow"))), \
                mock.patch.object(coach, "today_of", lambda uid: TODAY):
            ctx, _ = coach.context(T.U, TODAY)
        self.assertIn("ИЗ ПРИЛОЖЕНИЯ WHOOP", ctx)
        ext = self.call("/api/all")["ext"]["wx"]
        self.assertEqual((ext["count"], ext["latest"][0]["key"], ext["latest"][0]["value"]), (16, "vo2max", 51.3))

    def test_context_bp_apart_and_journal_only(self):
        self.add("2026-11-04", {"key": "journal", "label": "Пил кофеин", "text": "Да"})
        self.assertEqual(X.context_lines(coach, T.U, TODAY), ["ИЗ ПРИЛОЖЕНИЯ WHOOP (по скринам, не из API): дневник 04.11: пил кофеин — да"])
        self.add("2026-11-01", {"key": "bp_sys", "value": 118})
        self.add("2026-11-02", {"key": "bp_dia", "value": 75})
        line = X.context_lines(coach, T.U, TODAY)[0]
        self.assertIn("давление верхнее 118 мм рт. ст. (01.11) · давление нижнее 75 мм рт. ст. (02.11)", line)


if __name__ == "__main__":
    unittest.main(verbosity=1)
