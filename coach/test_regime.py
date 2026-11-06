"""Режим дня: сдвиг сна, режимы каникулы/учёба/выходной, интенсивность, массаж, еда и вода, чек-лист, напоминание.
usage: python3 coach/test_regime.py
"""
import copy
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
import f_regime as R  # noqa: E402

D = datetime.date
CFG = {  # пример из договора (coach/SYNC.md, п. 4): каникулы до 8.11, учёба с пн 9.11
    "train_days": [0, 1, 2, 5], "train_time": "18:00", "train_minutes": 90,
    "school_days": [0, 1, 2, 3, 4], "school_start": "09:00", "school_end": "16:40",
    "wake": "06:45", "bed": "22:00", "weekend_wake": "08:00", "massage_days": [3],
    "vacation": {"from": "2026-10-26", "to": "2026-11-08", "train_time": "10:00", "massage_days": [3, 6]},
    "shift": {"from": "2026-11-01", "bed": "23:45", "wake": "08:30", "step": 30, "every": 2},
    "evening": ["Душ", "Ужин", "Телефон в сторону"], "remind": 45,
}


def cfg(**kw):
    c = R.clean(CFG, R.DEFAULTS)
    c.update(kw)
    return c


class CalcTest(unittest.TestCase):
    def test_shift_example(self):
        c = cfg()
        want = {1: ("23:45", "08:30"), 2: ("23:45", "08:30"), 3: ("23:15", "08:00"), 4: ("23:15", "08:00"),
                5: ("22:45", "07:30"), 6: ("22:45", "07:30"), 7: ("22:15", "07:00"),
                8: ("22:00", "07:00")}  # вечер перед учёбой — отбой уже цель; подъём этого утра — ещё по сдвигу
        for day, bw in want.items():
            self.assertEqual(R.bed_wake(c, D(2026, 11, day)), bw, day)
        self.assertEqual(R.bed_wake(c, D(2026, 11, 9)), ("22:00", "06:45"))  # учёба: сдвиг закончен
        self.assertEqual(R.bed_wake(c, D(2026, 10, 31))[1], "08:00")  # до сдвига — подъём выходного

    def test_shift_reaches_target_and_holds(self):
        c = cfg(vacation={"from": "2026-10-26", "to": "2026-11-20"})  # каникулы длиннее — до цели дошли раньше
        self.assertEqual(R.bed_wake(c, D(2026, 11, 9)), ("22:00", "06:45"))  # 4 шага по 30 > 105 мин — цель
        self.assertEqual(R.bed_wake(c, D(2026, 11, 15)), ("22:00", "06:45"))  # держим до учёбы
        self.assertEqual(R.bed_wake(c, D(2026, 11, 23)), ("22:00", "06:45"))
        self.assertEqual(R.bed_wake(c, D(2026, 11, 27))[0], "23:15")  # пт: завтра выходной — позже на 75 мин

    def test_vacation_wake_9_until_school(self):
        # Глеб 04.11: на каникулах подъём в 9 — выспаться; в воскресенье утром тоже 9, вечером — отбой к учёбе
        c = cfg(shift={"from": "2026-11-04", "bed": "22:45", "wake": "09:00", "step": 5, "every": 14})
        for day in (4, 5, 6, 7):
            self.assertEqual(R.bed_wake(c, D(2026, 11, day)), ("22:45", "09:00"), day)
        self.assertEqual(R.bed_wake(c, D(2026, 11, 8)), ("22:00", "09:00"))
        self.assertEqual(R.bed_wake(c, D(2026, 11, 9)), ("22:00", "06:45"))

    def test_shift_over_midnight(self):
        c = cfg(shift={"from": "2026-11-01", "bed": "00:30", "wake": "09:00", "step": 60, "every": 1})
        self.assertEqual(R.bed_wake(c, D(2026, 11, 1)), ("00:30", "09:00"))
        self.assertEqual(R.bed_wake(c, D(2026, 11, 2)), ("23:30", "08:00"))
        self.assertEqual(R.bed_wake(c, D(2026, 11, 4)), ("22:00", "06:45"))

    def test_modes_and_no_shift(self):
        c = cfg(shift=None, vacation=None)
        self.assertEqual(R.mode_of(c, D(2026, 11, 2)), "school")  # пн
        self.assertEqual(R.mode_of(c, D(2026, 11, 7)), "weekend")  # сб
        self.assertEqual(R.mode_of(cfg(), D(2026, 11, 2)), "vacation")
        self.assertEqual(R.bed_wake(c, D(2026, 11, 2)), ("22:00", "06:45"))
        self.assertEqual(R.bed_wake(c, D(2026, 11, 6)), ("23:15", "06:45"))  # пт: завтра выходной
        self.assertEqual(R.bed_wake(c, D(2026, 11, 7)), ("23:15", "08:00"))  # сб
        self.assertEqual(R.bed_wake(c, D(2026, 11, 8)), ("22:00", "08:00"))  # вс: завтра учёба

    def test_intensity(self):
        c = cfg(vacation=None, shift=None)  # пн вт ср сб
        self.assertEqual(R.intensity(c, D(2026, 11, 2))[0], "full")  # пн
        self.assertEqual(R.intensity(c, D(2026, 11, 3))[0], "full")  # вт — 2-й подряд
        lvl, why = R.intensity(c, D(2026, 11, 4))  # ср — 3-й подряд
        self.assertEqual(lvl, "light")
        self.assertIn("3-й день", why)
        self.assertEqual(R.intensity(c, D(2026, 11, 5))[0], "rest")  # чт
        self.assertEqual(R.intensity(c, D(2026, 11, 7))[0], "full")  # сб
        lvl, why = R.intensity(c, D(2026, 11, 2), recovery=33)
        self.assertEqual(lvl, "light")
        self.assertIn("33%", why)
        self.assertEqual(R.intensity(c, D(2026, 11, 2), recovery=34)[0], "full")
        self.assertEqual(R.intensity(c, D(2026, 11, 5), recovery=10)[0], "rest")
        self.assertEqual(R.streak(cfg(train_days=list(range(7)), vacation=None), D(2026, 11, 2)), 7)

    def test_vacation_overrides(self):
        c = cfg()
        p = R.day_plan(c, D(2026, 11, 2))  # пн каникул
        self.assertEqual(p["mode"], "vacation")
        self.assertEqual(p["train"], {"on": True, "time": "10:00", "minutes": 90, "intensity": "full",
                                      "why": "Можно в полную силу", "src": "plan"})
        self.assertTrue(R.day_plan(c, D(2026, 11, 8))["massage"])  # вс — массаж в каникулы
        self.assertFalse(R.day_plan(c, D(2026, 11, 15))["massage"])  # вс после каникул — нет
        self.assertTrue(R.day_plan(c, D(2026, 11, 12))["massage"])  # чт — всегда
        self.assertEqual(R.day_plan(c, D(2026, 11, 9))["train"]["time"], "18:00")
        rest = R.day_plan(c, D(2026, 11, 12))["train"]
        self.assertEqual((rest["on"], rest["time"], rest["minutes"], rest["intensity"]), (False, None, 0, "rest"))

    def test_plan_shape_food_and_tips(self):
        goals = {"goals": {"train": {"kcal": 3400, "p": 130, "f": 105, "c": 485}, "rest": {"kcal": 2800, "p": 125}},
                 "salt": 5, "water": {"train": 3000, "rest": 2500}}
        p = R.day_plan(cfg(), D(2026, 11, 3), recovery=32, kcal_goals=goals, done={"e1"})
        self.assertEqual(set(p), {"day", "mode", "bed", "wake", "train", "massage", "kcal", "protein", "water",
                                  "checklist", "tips"})
        self.assertEqual((p["kcal"], p["protein"], p["water"]), (3400, 130, 3000))
        self.assertEqual(p["train"]["intensity"], "light")
        self.assertEqual([x["done"] for x in p["checklist"]], [False, True, False])
        self.assertEqual(p["checklist"][0], {"id": "e0", "text": "Душ", "done": False})
        self.assertTrue(any("32%" in x for x in p["tips"]))
        self.assertTrue(any("на 30 мин раньше" in x for x in p["tips"]))
        self.assertLessEqual(len(p["tips"]), 3)
        p = R.day_plan(cfg(), D(2026, 11, 5), kcal_goals=goals)  # чт — день отдыха
        self.assertEqual((p["kcal"], p["protein"], p["water"]), (2800, 125, 2500))
        nut = {"kcal": 2600, "protein": 98, "water": 2150}
        p = R.day_plan(cfg(), D(2026, 11, 5), nutrition=nut, water_norm=2150)  # без «Калорий» — наш расчёт
        self.assertEqual((p["kcal"], p["protein"], p["water"]), (2600, 98, 2150))
        self.assertEqual(R.day_plan(cfg(), D(2026, 11, 5))["kcal"], None)

    def test_whoop_adjusts_day(self):
        """Глеб 03.11: «стоит, что четверг отдых, а я тренировался — пусть сам подтягивает по моим данным»."""
        goals = {"goals": {"train": {"kcal": 3400, "p": 130}, "rest": {"kcal": 2800, "p": 125}},
                 "water": {"train": 3000, "rest": 2500}}
        box = {"sport": "Бокс", "start": "18:10", "minutes": 85, "rest": False}
        c = cfg()
        p = R.day_plan(c, D(2026, 11, 5), kcal_goals=goals, workouts=[box, {"sport": "Бег", "start": "09:00",
                                                                         "minutes": 20, "rest": False}])  # чт — отдых
        tr = p["train"]
        self.assertEqual((tr["on"], tr["time"], tr["minutes"], tr["src"]), (True, "18:10", 85, "whoop"))
        self.assertEqual(tr["why"], "по WHOOP: бокс в 18:10, 85 мин")
        self.assertEqual((p["kcal"], p["protein"], p["water"]), (3400, 130, 3000))  # нормы — как в день тренировки
        self.assertEqual(c["train_days"], cfg()["train_days"])  # настройки не тронуты
        massage = {"sport": "Массаж", "start": "12:00", "minutes": 89, "rest": True}
        p = R.day_plan(c, D(2026, 11, 5), kcal_goals=goals, workouts=[massage, dict(box, minutes=10)])
        self.assertEqual((p["train"]["on"], p["train"]["src"], p["kcal"]), (False, "plan", 2800))  # массаж и 10 мин — нет
        # по плану тренировка в 10:00 (каникулы), а была в 12:30 — фактическое время
        p = R.day_plan(c, D(2026, 11, 2), workouts=[dict(box, start="12:30")])
        self.assertEqual((p["train"]["time"], p["train"]["src"]), ("12:30", "whoop"))
        # по плану тренировка, её ещё нет: сегодня — как в плане, прошедший день — «не было»
        p = R.day_plan(c, D(2026, 11, 2), workouts=[])
        self.assertEqual((p["train"]["on"], p["train"]["time"], p["train"]["src"]), (True, "10:00", "plan"))
        p = R.day_plan(c, D(2026, 11, 2), workouts=[], past=True)
        self.assertEqual((p["train"]["on"], p["train"]["src"], p["train"]["why"]), (False, "whoop", "по WHOOP: тренировки не было"))

    def test_due(self):
        c = cfg(vacation=None, shift=None)  # пн: отбой 22:00, за 45 мин
        tz = ZoneInfo("Europe/Moscow")
        at = lambda h, m, d=2: datetime.datetime(2026, 11, d, h, m, tzinfo=tz)  # noqa: E731
        self.assertIsNone(R.due(c, at(21, 14)))
        self.assertEqual(R.due(c, at(21, 15)), D(2026, 11, 2))
        self.assertEqual(R.due(c, at(21, 59)), D(2026, 11, 2))
        self.assertIsNone(R.due(c, at(22, 0)))
        self.assertIsNone(R.due(dict(c, remind=0), at(21, 30)))
        late = cfg(vacation=None, shift=None, bed="00:30", wake="08:30")  # отбой после полуночи — ещё «тот» день
        self.assertEqual(R.due(late, at(0, 10, 3)), D(2026, 11, 2))
        self.assertEqual(R.due(late, at(23, 50, 2)), D(2026, 11, 2))


class CleanTest(unittest.TestCase):
    def test_valid(self):
        c = R.clean({"train_days": [5, 0, 0, 2.0], "bed": "9.30", "remind": "30", "evening": ["  Душ ", "", "a\x00b"],
                     "vacation": {"from": "2026-12-28", "to": "2027-01-10", "massage_days": [6]},
                     "shift": {"from": "2026-12-28", "bed": "23:00", "wake": "9:00"}, "unknown": 1}, R.DEFAULTS)
        self.assertEqual(c["train_days"], [0, 2, 5])
        self.assertEqual(c["bed"], "09:30")
        self.assertEqual(c["remind"], 30)
        self.assertEqual(c["evening"], ["Душ", "a b"])
        self.assertEqual(c["vacation"], {"from": "2026-12-28", "to": "2027-01-10", "massage_days": [6]})
        self.assertEqual(c["shift"], {"from": "2026-12-28", "bed": "23:00", "wake": "09:00", "step": 30, "every": 2})
        self.assertNotIn("unknown", c)
        self.assertIsNone(R.clean({"vacation": None, "shift": {}}, c)["vacation"])
        self.assertEqual(R.DEFAULTS["train_days"], [])  # не испортили значения по умолчанию

    def test_invalid(self):
        bad = [{"bed": "25:00"}, {"wake": "утром"}, {"train_days": [7]}, {"train_days": "пн"}, {"train_days": [True]},
               {"train_minutes": 5}, {"remind": 500}, {"evening": "душ"}, {"evening": ["x"] * 13},
               {"vacation": {"from": "2026-11-10", "to": "2026-11-01"}}, {"vacation": {"from": "2026-02-30", "to": "2026-03-01"}},
               {"vacation": {"from": "2026-01-01", "to": "2026-12-31"}}, {"vacation": "лето"},
               {"shift": {"from": "2026-11-01", "bed": "23:00", "wake": "08:00", "step": 1}},
               {"shift": {"from": "2026-11-01", "bed": "23:00", "wake": "08:00", "every": 30}},
               {"shift": {"from": "сегодня", "bed": "23:00", "wake": "08:00"}}]
        for b in bad:
            with self.assertRaises(ValueError, msg=b):
                R.clean(b, R.DEFAULTS)

    def test_profile_days(self):
        self.assertEqual(R.profile_days({"schedule": "бокс пн, вт, среда и суббота в 18.30"}), ([0, 1, 2, 5], "18:30"))
        self.assertEqual(R.profile_days({"schedule": "4 раза в неделю"}), ([], None))
        self.assertEqual(R.profile_days({}), ([], None))


class ApiTest(unittest.TestCase):
    TODAY = D(2026, 11, 3)  # вт каникул, 2-й день сдвига 3–4.11

    @classmethod
    def setUpClass(cls):
        cls.db, _ = T.seeded(os.path.join(tempfile.mkdtemp(), "regime.db"))

    def setUp(self):
        coach.db = self.db
        self.db.x("DELETE FROM meta WHERE key LIKE 'regime:%' OR key LIKE 'kcal:%'")
        self.db.x("DELETE FROM sent")
        self.db.save_profile(T.U, {"age": "15", "sex": "m", "weight": "60", "height": "175", "schedule": "пн ср пт"})

    def call(self, path, body=None, day=None, hour=13):
        day = day or self.TODAY
        now = datetime.datetime.combine(day, datetime.time(hour, 40), tzinfo=ZoneInfo("Europe/Moscow"))
        with mock.patch.object(coach, "user_now", lambda u: now), mock.patch.object(coach, "today_of", lambda uid: day):
            return coach.route(T.U, path, body or {})

    def test_defaults_from_profile(self):
        r = self.call("/api/regime")
        self.assertEqual(set(r), {"cfg", "today", "week", "saved"})
        self.assertFalse(r["saved"])  # не сохранял сам — главная «На сегодня» не берёт отбой из режима
        self.assertEqual(r["cfg"]["train_days"], [0, 2, 4])  # из расписания в профиле
        self.assertEqual((r["cfg"]["bed"], r["cfg"]["wake"]), ("22:00", "07:00"))
        self.assertIsNone(r["cfg"]["vacation"])
        self.assertEqual(len(r["week"]), 7)
        self.assertEqual(r["week"][0], r["today"])
        self.assertEqual(r["today"]["day"], "2026-11-03")
        self.assertEqual([x["day"] for x in r["week"]][-1], "2026-11-09")
        self.assertIsNotNone(r["today"]["kcal"])  # без «Калорий» — наш расчёт по весу
        self.assertFalse(R.configured(self.db, T.U))
        self.assertEqual(coach.context(T.U, self.TODAY)[0].count("РЕЖИМ НА СЕГОДНЯ"), 0)  # не настроен — тренер не видит

    def test_rest_day_with_whoop_workout(self):
        z = lambda t: t.strftime("%Y-%m-%dT%H:%M:%S.000Z")  # noqa: E731
        t0 = datetime.datetime(2026, 11, 3, 15, 10)  # 18:10 у него (+03:00); вт — по расписанию отдых
        recs = [("wbox", "boxing", 85), ("wmas", "massage-therapy", 60)]
        for wid, sport, mins in recs:
            start = t0 if wid == "wbox" else t0 - datetime.timedelta(hours=5)
            self.db.put(T.U, "workout", wid, "2026-11-03", z(start), {
                "id": wid, "start": z(start), "end": z(start + datetime.timedelta(minutes=mins)), "timezone_offset": "+03:00",
                "sport_name": sport, "score_state": "SCORED", "score": {"strain": 12.0, "kilojoule": 2000}})
        try:
            r = self.call("/api/regime")
            tr = r["today"]["train"]
            self.assertEqual((tr["on"], tr["time"], tr["minutes"], tr["src"]), (True, "18:10", 85, "whoop"))
            self.assertIn("бокс в 18:10", tr["why"])
            self.assertNotIn(1, r["cfg"]["train_days"])  # расписание то же
            self.assertTrue(all(x["train"]["src"] == "plan" for x in r["week"][1:]))
            now = datetime.datetime.combine(self.TODAY, datetime.time(20, 0), tzinfo=ZoneInfo("Europe/Moscow"))
            with mock.patch.object(coach, "user_now", lambda u: now), mock.patch.object(coach, "today_of", lambda uid: self.TODAY):
                self.assertTrue(coach.kcal_forma(T.U)["train_today"])  # «Калории» — тот же тренировочный день
            # Глеб 04.11: WHOOP записал тренировку по ошибке, он удалил её — день снова без тренировки (вебхук workout.deleted)
            self.assertTrue(coach.whoop.drop_deleted(self.db, T.U, "workout.deleted", "wbox"))
            tr = self.call("/api/regime")["today"]["train"]
            self.assertEqual((tr["on"], tr["src"], tr["minutes"]), (False, "plan", 0))  # массаж — не тренировка
            with mock.patch.object(coach, "user_now", lambda u: now), mock.patch.object(coach, "today_of", lambda uid: self.TODAY):
                self.assertFalse(coach.kcal_forma(T.U)["train_today"])
        finally:
            for wid, _, _ in recs:
                self.db.drop_record(T.U, "workout", wid)

    def test_save_check_and_context(self):
        r = self.call("/api/regime/save", {"cfg": CFG})
        self.assertTrue(R.configured(self.db, T.U))
        self.assertTrue(r["saved"])  # сохранил сам — главная берёт отбой из режима
        self.assertEqual(r["cfg"]["train_days"], [0, 1, 2, 5])
        self.assertEqual((r["today"]["bed"], r["today"]["wake"], r["today"]["mode"]), ("23:15", "08:00", "vacation"))
        self.assertEqual(r["today"]["train"]["time"], "10:00")
        with self.assertRaises(ValueError):
            self.call("/api/regime/save", {"bed": "99:00"})
        self.assertEqual(self.call("/api/regime")["cfg"]["bed"], "22:00")  # неверное не сохранилось
        r = self.call("/api/regime/save", {"remind": 30})  # частичное — остальное не трогает
        self.assertEqual((r["cfg"]["remind"], r["cfg"]["bed"]), (30, "22:00"))
        r = self.call("/api/regime/check", {"id": "e1", "done": True})
        self.assertEqual([x["done"] for x in r["today"]["checklist"]], [False, True, False])
        self.assertFalse(r["week"][1]["checklist"][1]["done"])  # отметка — только на сегодня
        r = self.call("/api/regime/check", {"id": "e1", "done": False})
        self.assertFalse(r["today"]["checklist"][1]["done"])
        for bad in ({"id": "e9", "done": True}, {"id": "x", "done": True}, {}):
            with self.assertRaises(ValueError):
                self.call("/api/regime/check", bad)
        with mock.patch.object(coach, "today_of", lambda uid: self.TODAY):
            ctx = coach.context(T.U, self.TODAY)[0]
        self.assertIn("РЕЖИМ НА СЕГОДНЯ (каникулы", ctx)
        self.assertIn("отбой 23:15", ctx)
        self.assertIn("тренировка 10:00, 90 мин", ctx)

    def test_kcal_goals_and_extra(self):
        self.call("/api/regime/save", {"cfg": CFG})
        self.db.kcal_goals(T.U, {"goals": {"train": {"kcal": 3400, "p": 130}, "rest": {"kcal": 2800, "p": 125}},
                                 "salt": 5, "water": {"train": 3000, "rest": 2500}})
        r = self.call("/api/all")
        reg = r["ext"]["regime"]
        self.assertEqual(reg["today"]["day"], "2026-11-03")
        self.assertEqual((reg["today"]["kcal"], reg["today"]["protein"], reg["today"]["water"]), (3400, 130, 3000))
        self.assertEqual(reg["week"][2]["kcal"], 2800)  # чт — отдых

    def test_morning_and_reminder(self):
        sent = []
        with mock.patch.object(coach, "send", lambda chat, text, buttons=None, **kw: sent.append((text, buttons))):
            self.assertEqual(coach.regime_brief(T.U, {"day": "2026-11-03", "recovery": None}), "")  # не настроен
            self.call("/api/regime/save", {"cfg": CFG})
            t = coach.day_payload(T.U, self.TODAY)
            line = coach.regime_brief(T.U, t)
            self.assertIn("Отбой 23:15 · подъём 08:00", line)
            self.assertIn("Тренировка 10:00", line)
            u = self.db.user(T.U)
            tz = ZoneInfo("Europe/Moscow")
            with mock.patch.object(coach, "bg", lambda f, *a, **kw: f(*a)), \
                    mock.patch.object(coach, "allowed", lambda uid: True), \
                    mock.patch.object(coach, "maybe_morning", lambda *a: None), \
                    mock.patch.object(coach, "sync_user", lambda *a, **kw: None), \
                    mock.patch.object(coach.ai, "claude", side_effect=coach.ai.AIError("тест")):  # вечерний итог — без Claude
                for hh, mm in ((22, 20), (22, 31), (22, 50), (23, 10)):
                    now = datetime.datetime(2026, 11, 3, hh, mm, tzinfo=tz)
                    with mock.patch.object(coach, "user_now", lambda u_, now=now: now):
                        coach.tick()
        rem = [x for x in sent if "Отбой в 23:15" in x[0]]
        self.assertEqual(len(rem), 1)  # раз в день, с 22:30 (за 45 мин до 23:15)
        text, buttons = rem[0]
        self.assertTrue(text.startswith("🌙 <b>Отбой в 23:15 — иду расти</b>\n<blockquote>"))
        self.assertRegex(text, r"Подъём завтра в <b>08:00</b> · сна ~\d+:\d\d из \d+:\d\d\n")
        self.assertIn("Завтра тренировка в 10:00", text)  # ср — тренировка
        self.assertLessEqual(text.split("<blockquote>")[1].count("\n") + 1, 5)
        self.assertTrue(any(b.get("text") == "📱 Открыть" for row in buttons for b in row))
        self.assertIsNone(R.due(dict(copy.deepcopy(R.load(self.db, T.U)), remind=0), datetime.datetime(2026, 11, 3, 22, 40)))

    def test_sleep_ahead(self):
        """Глеб 06.11 («сон — для роста»): в напоминании об отбое — сколько чистого сна выйдет до подъёма и сколько нужно."""
        with mock.patch.object(coach.insights, "by_day", lambda *a: {}), \
                mock.patch.object(coach, "profile", lambda uid: {"age": 14}):
            net, need = coach.sleep_ahead(T.U, self.TODAY, "22:00", "06:45")
        self.assertEqual((coach.hm(net), coach.hm(need)), ("7:40", "8:30"))  # (8:45 − 15 мин) × 0,9; подростку ≥ 8,5 ч
        days = {self.TODAY.isoformat(): {"sleep": {"need": 9.75, "efficiency": 95}}}
        with mock.patch.object(coach.insights, "by_day", lambda *a: days), \
                mock.patch.object(coach, "profile", lambda uid: {"age": 14}):
            net, need = coach.sleep_ahead(T.U, self.TODAY, "23:30", "08:00")
        self.assertEqual((coach.hm(net), coach.hm(need)), ("7:50", "9:45"))  # эффективность и потребность — из WHOOP
        with mock.patch.object(coach, "sleep_ahead", side_effect=RuntimeError("сбой")):
            self.call("/api/regime/save", {"cfg": CFG})
            _, lines = coach.remind_lines(T.U, self.TODAY)
        self.assertEqual(lines[0], "Подъём завтра в <b>08:00</b>")  # сбой расчёта — напоминание без цифры сна


if __name__ == "__main__":
    unittest.main(verbosity=1)
