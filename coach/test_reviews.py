"""Разборы за день: утро сразу после сна (не ночью, раз в день), после тренировки (порог, не больше 2),
вечерний итог (время по режиму, раз в день, вместе с напоминанием об отбое), лента в приложении и «прочитано».
usage: python3 coach/test_reviews.py
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
import f_reviews as RV  # noqa: E402

U, TODAY = T.U, T.TODAY  # 11.10.2026, воскресенье; сон за сегодня 22:47–07:27, восстановление 82%
TZ = ZoneInfo("Europe/Moscow")
DAY = TODAY.isoformat()


def at(h, m=0, day=TODAY):
    return datetime.datetime.combine(day, datetime.time(h, m), tzinfo=TZ)


def workout(wid, minutes=60, strain=12.0, day=TODAY, hour=15):
    start = datetime.datetime.combine(day, datetime.time(hour, 0)) - datetime.timedelta(hours=3)  # UTC, у него +03:00
    z = lambda t: t.strftime("%Y-%m-%dT%H:%M:%S.000Z")  # noqa: E731
    return {"id": wid, "start": z(start), "end": z(start + datetime.timedelta(minutes=minutes)), "timezone_offset": "+03:00",
            "sport_name": "boxing", "score_state": "SCORED", "_day": day.isoformat(),
            "score": {"strain": strain, "kilojoule": 2500, "average_heart_rate": 150, "max_heart_rate": 190,
                      "zone_durations": {f"zone_{n}_milli": 600000 for n in ("zero", "one", "two", "three", "four", "five")}}}


class RulesTest(unittest.TestCase):
    def test_morning_sleep(self):
        self.assertTrue(RV.morning_sleep({"end": "07:27", "nap": False}))
        self.assertTrue(RV.morning_sleep({"end": "05:00", "nap": False}))
        self.assertFalse(RV.morning_sleep({"end": "04:59", "nap": False}))  # ночное пробуждение
        self.assertFalse(RV.morning_sleep({"end": "14:30", "nap": True}))  # дневной сон
        self.assertFalse(RV.morning_sleep({"end": "23:10", "nap": False}))
        self.assertFalse(RV.morning_sleep(None))

    def test_morning_due(self):
        due, a = coach.morning_due, datetime.time(7, 0)
        t = lambda h, m=0: datetime.datetime(2026, 10, 11, h, m)  # noqa: E731
        self.assertEqual(due(a, t(6, 10), "full", False, False, True), "brief")  # встал в 6 — сразу
        self.assertIsNone(due(a, t(6, 10), "full", False, False, False))  # ночью проснулся — ждём 07:00
        self.assertEqual(due(a, t(7, 0), "full", False, False, False), "brief")
        self.assertIsNone(due(a, t(6, 10), "full", True, False, True))  # уже был
        self.assertIsNone(due(a, t(6, 10), "part", False, False, True))  # восстановление ещё не посчитано
        self.assertIsNone(due(a, t(15, 0), "full", False, False, True))  # день прошёл
        self.assertIsNone(due(None, t(8, 0), "full", False, False, True))  # /morning off

    def test_workout_ok(self):
        self.assertTrue(RV.workout_ok({"minutes": 15, "strain": 5}))
        self.assertFalse(RV.workout_ok({"minutes": 14, "strain": 12}))
        self.assertFalse(RV.workout_ok({"minutes": 60, "strain": 4.9}))
        self.assertFalse(RV.workout_ok({"minutes": None, "strain": None}))

    def test_evening_due(self):
        self.assertIsNone(RV.evening_due(None, at(20, 59)))
        self.assertEqual(RV.evening_due(None, at(21, 0)), TODAY)
        self.assertEqual(RV.evening_due(None, at(22, 59)), TODAY)
        self.assertIsNone(RV.evening_due(None, at(23, 0)))  # опоздали больше чем на 2 ч — не шлём
        cfg = coach.f_regime.clean({"bed": "22:30", "wake": "07:00", "school_days": [0, 1, 2, 3, 4], "remind": 45},
                                   coach.f_regime.DEFAULTS)
        self.assertIsNone(RV.evening_due(cfg, at(21, 29)))  # вс, завтра учёба: отбой 22:30 → итог в 21:30
        self.assertEqual(RV.evening_due(cfg, at(21, 30)), TODAY)
        self.assertIsNone(RV.evening_due(cfg, at(22, 30)))  # отбой — поздно
        late = dict(cfg, bed="00:30")  # отбой после полуночи: итог в 23:30, после полуночи — ещё вчерашний день
        self.assertEqual(RV.evening_due(late, at(23, 40)), TODAY)
        self.assertEqual(RV.evening_due(late, at(0, 10, TODAY + datetime.timedelta(days=1))), TODAY)
        self.assertEqual(RV.evening_window(dict(cfg, remind=90), TODAY, TZ)[0], at(21, 0))  # напоминание раньше часа

    def test_html_md(self):
        self.assertEqual(RV.html_md("• <b>Отбой</b> до 23:00 &amp; вода"), "- **Отбой** до 23:00 & вода")


class FlowTest(unittest.TestCase):
    def setUp(self):
        self.db, _ = T.seeded(os.path.join(tempfile.mkdtemp(), "rv.db"))
        self.db.meta("owner", U)
        self.db.meta(f"mode:{U}", "app")
        self.sent, self.now = [], at(8, 0)
        self.p = [mock.patch.object(coach, "db", self.db), mock.patch.object(coach, "tg", side_effect=self.fake_tg),
                  mock.patch.object(coach, "user_now", lambda u: self.now),
                  mock.patch.object(coach, "public_url", "https://coach.example"),
                  mock.patch.object(coach, "allowed", lambda uid: True),
                  mock.patch.object(coach.threading, "Thread", T.SyncThread),
                  mock.patch.object(coach, "sync_user", lambda *a, **kw: []),
                  mock.patch.object(ai, "claude", side_effect=self.fake_claude)]
        for p in self.p:
            p.start()
        self.prompts = []
        coach.busy.clear()

    def tearDown(self):
        for p in self.p:
            p.stop()
        self.db.db.close()

    def fake_tg(self, method, **kw):
        self.sent.append((method, kw))
        return {"ok": True, "result": {"message_id": len(self.sent)}}

    def fake_claude(self, prompt, **kw):
        self.prompts.append((prompt, kw))
        return "**Хороший день — можно в полную.**\n- Бокс в полную, нагрузка 12–14\n- Вода 2,5 л, белок 120 г\n- Лечь до 23:00"

    def msgs(self, word):
        return [kw for m, kw in self.sent if m == "sendMessage" and word in kw.get("text", "")]

    def sleep_end(self, end_utc, nap=False):
        """Сон за сегодня закончился в end_utc (UTC «ЧЧ:ММ», у него +3)."""
        r = self.db.records(U, "sleep", DAY, DAY)[0]
        r["end"], r["nap"] = f"{DAY}T{end_utc}:00.000Z", nap
        self.db.put(U, "sleep", r["id"], DAY, r["start"], {k: v for k, v in r.items() if k != "_day"})

    def fresh(self):
        return [("sleep", dict(self.db.records(U, "sleep", DAY, DAY)[0])),
                ("recovery", dict(self.db.records(U, "recovery", DAY, DAY)[0]))]

    def items(self):
        return RV.items(self.db, U, DAY)

    def tick(self, h, m=0, day=TODAY):
        self.now = at(h, m, day)
        with mock.patch.object(coach, "bg", lambda f, *a, **kw: f(*a)):
            coach.tick()

    # ---------- утро ----------

    def test_morning_right_after_sleep(self):
        self.now = at(7, 40)  # встал в 07:27, WHOOP посчитал — /morning 08:30 не ждём
        self.db.save_profile(U, {"morning": "08:30"})
        coach.react(U, self.fresh())
        m = self.msgs("Доброе утро")
        self.assertEqual(len(m), 1)
        body = m[0]["text"].split("<blockquote>")[1].split("</blockquote>")[0]
        self.assertLessEqual(body.count("\n") + 1, 5)  # коротко: цифры и 2 строки
        it = self.items()
        self.assertEqual([(x["kind"], x["time"], x["seen"]) for x in it], [("morning", "07:40", False)])
        self.assertIn("Восстановление **82%**", it[0]["text"])
        self.assertIn("Бокс в полную", it[0]["text"])
        self.assertEqual(it[0]["title"], "Восстановление 82%")
        url = json.dumps(m[0]["reply_markup"])
        self.assertIn(f"review-{it[0]['id']}", url)  # «📱 Открыть» — сразу этот разбор
        self.assertEqual(self.prompts[0][1].get("model"), ai.CHAT_MODEL)  # быстрая модель
        coach.react(U, self.fresh())  # повторная синхронизация — второго нет
        self.tick(9, 0)
        self.assertEqual(len(self.msgs("Доброе утро")), 1)

    def test_night_wake_waits_for_morning_time(self):
        self.sleep_end("00:40")  # проснулся в 03:40 — это не утро
        self.now = at(3, 50)
        coach.react(U, self.fresh())
        for h, m in ((5, 0), (6, 59)):
            self.tick(h, m)
        self.assertEqual(self.msgs("Доброе утро"), [])
        self.tick(7, 0)  # время /morning — защита: теперь можно
        self.assertEqual(len(self.msgs("Доброе утро")), 1)

    def test_nap_is_not_morning(self):
        self.sleep_end("03:30", nap=True)  # только дневной сон за сегодня
        self.now = at(6, 40)
        coach.react(U, self.fresh())
        self.assertEqual(self.msgs("Доброе утро"), [])

    def test_morning_failed_ai_still_in_feed(self):
        with mock.patch.object(ai, "claude", side_effect=ai.AIError("лимит")):
            self.now = at(7, 40)
            coach.react(U, self.fresh())
        self.assertEqual(len(self.msgs("Доброе утро")), 1)
        it = self.items()
        self.assertIn("Ориентир нагрузки", it[0]["text"])
        self.assertNotIn("<b>", it[0]["text"])  # в приложение — Markdown, не HTML

    def test_sync_more_often_from_five(self):
        u = self.db.user(U)
        self.assertFalse(coach.morning_waiting(u, at(4, 50)))
        self.assertTrue(coach.morning_waiting(u, at(5, 0)))

    # ---------- после тренировки ----------

    def test_workout_threshold_and_limit(self):
        self.now = at(19, 0)
        coach.react(U, [("workout", workout("short", minutes=10)), ("workout", workout("easy", strain=3))])
        self.assertEqual(self.items(), [])
        coach.react(U, [("workout", workout("w1", hour=10))])
        coach.react(U, [("workout", workout("w1", hour=10))])  # та же — один раз
        coach.react(U, [("workout", workout("w2", hour=15)), ("workout", workout("w3", hour=17))])
        its = [x for x in self.items() if x["kind"] == "workout"]
        self.assertEqual(len(its), 2)  # не больше 2 за день
        self.assertEqual(its[0]["title"], "Бокс · 60 мин")
        self.assertIn("Нагрузка **12", its[0]["text"])
        self.assertIn("тяжёлых минут (зоны 4–5) 20", its[0]["text"])
        m = self.msgs("Бокс · 60 мин")
        self.assertEqual(len(m), 2)
        self.assertIn("📱 Открыть", json.dumps(m[0]["reply_markup"], ensure_ascii=False))
        p = self.prompts[0][0]
        for w in ("ОТБОЙ", "что съесть и выпить", "восстановления завтра"):
            self.assertIn(w, p)
        # назавтра — снова можно
        self.now = at(19, 0, TODAY + datetime.timedelta(days=1))
        coach.react(U, [("workout", workout("w4", day=TODAY + datetime.timedelta(days=1)))])
        self.assertEqual(len(self.msgs("Бокс · 60 мин")), 3)

    # ---------- вечер ----------

    def test_evening_default_21(self):
        self.tick(20, 59)
        self.assertEqual(self.msgs("Итог дня"), [])
        self.tick(21, 0)
        self.tick(21, 30)
        m = self.msgs("Итог дня")
        self.assertEqual(len(m), 1)
        body = m[0]["text"].split("<blockquote>")[1].split("</blockquote>")[0]
        self.assertLessEqual(body.count("\n") + 1, 5)
        self.assertIn("Нагрузка", body)
        ev = [x for x in self.items() if x["kind"] == "evening"]
        self.assertEqual(len(ev), 1)
        self.assertIn("Нагрузка за день", ev[0]["text"])
        self.assertIn("ИТОГ ДНЯ ПО ЦИФРАМ", self.prompts[-1][0])

    def test_evening_by_regime_and_combined_reminder(self):
        coach.route(U, "/api/regime/save", {"cfg": {"bed": "22:30", "wake": "07:00", "school_days": [0, 1, 2, 3, 4],
                                                    "remind": 45, "evening": ["Душ"]}})
        self.tick(21, 0)
        self.assertEqual(self.msgs("Итог дня"), [])  # не 21:00, а за час до отбоя 22:30
        self.tick(21, 30)
        self.tick(21, 45)  # напоминание об отбое — своё, отдельное и без повторов итога
        self.tick(22, 0)
        self.assertEqual(len(self.msgs("Итог дня")), 1)
        rem = self.msgs("Отбой в 22:30")
        self.assertEqual(len(rem), 1)
        self.assertNotIn("Нагрузка", rem[0]["text"])
        # напоминание раньше чем за час (90 мин) — одним сообщением в его время
        self.sent.clear()
        self.db.x("DELETE FROM sent")
        coach.route(U, "/api/regime/save", {"cfg": {"remind": 90}})
        self.tick(21, 0)
        self.tick(21, 30)
        m = self.msgs("Итог дня")
        self.assertEqual(len(m), 1)
        self.assertIn("отбой в 22:30", m[0]["text"])
        self.assertIn("Подъём завтра", m[0]["text"])
        self.assertEqual(self.msgs("Отбой в 22:30"), [])

    def test_evening_nothing_to_sum(self):
        self.db.x("DELETE FROM records WHERE user=?", (U,))
        coach.evening(U, at(21, 0))
        self.assertEqual(self.msgs("Итог дня"), [])


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.db, _ = T.seeded(os.path.join(tempfile.mkdtemp(), "rva.db"))
        self.db.meta("owner", U)
        self.p = [mock.patch.object(coach, "db", self.db),
                  mock.patch.object(coach, "user_now", lambda u: at(22, 0))]
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()
        self.db.db.close()

    def test_feed_and_seen(self):
        RV.add(self.db, U, DAY, "evening", "Итог дня", "**Итог**", "21:30")
        w = RV.add(self.db, U, DAY, "workout", "Бокс · 60 мин", "- вода", "16:10", ref="w1")
        m = RV.add(self.db, U, DAY, "morning", "Восстановление 82%", "- утро", "07:40")
        RV.add(self.db, U, "2026-10-10", "morning", "Вчера", "- вчера", "07:00")
        r = T.call("/api/reviews", {})
        self.assertEqual(r.code, 200)
        self.assertEqual([x["kind"] for x in r.json["items"]], ["morning", "workout", "evening"])  # по порядку дня
        self.assertEqual(r.json["new"], 3)
        self.assertEqual(r.json["items"][0], {"id": m, "kind": "morning", "time": "07:40", "title": "Восстановление 82%",
                                              "text": "- утро", "seen": False})
        r = T.call("/api/reviews/seen", {"id": w})
        self.assertEqual(r.json["new"], 2)
        self.assertTrue(next(x for x in r.json["items"] if x["id"] == w)["seen"])
        self.assertEqual(T.call("/api/reviews", {"day": "2026-10-10"}).json["items"][0]["title"], "Вчера")
        allp = T.call("/api/all", {}).json
        self.assertEqual(allp["ext"]["reviews"]["new"], 2)
        # новый текст того же разбора — снова «новый»
        RV.add(self.db, U, DAY, "workout", "Бокс · 60 мин", "- вода и белок", "16:12", ref="w1")
        self.assertEqual(T.call("/api/reviews", {}).json["new"], 3)
        # ошибки: чужой/нет id, день вне недели
        self.assertEqual(T.call("/api/reviews/seen", {"id": 99999}).code, 400)
        self.assertEqual(T.call("/api/reviews/seen", {"id": "x"}).code, 400)
        self.assertEqual(T.call("/api/reviews", {"day": "2026-09-01"}).code, 400)
        self.assertEqual(T.call("/api/reviews", {"day": "2026-10-12"}).code, 400)
        self.assertEqual(T.call("/api/reviews", {"day": "вчера"}).code, 400)
        with self.assertRaises(ValueError):
            RV.add(self.db, U, DAY, "nope", "x", "y", "10:00")

    def test_other_user_cannot_mark(self):
        rid = RV.add(self.db, 777, DAY, "morning", "чужой", "x", "07:00")
        self.assertEqual(T.call("/api/reviews/seen", {"id": rid}).code, 400)
        self.assertEqual(T.call("/api/reviews", {}).json["items"], [])

    def test_coach_sees_reviews(self):
        RV.add(self.db, U, DAY, "morning", "Восстановление 82%", "**Зелёный день — можно в полную.**\n- вода", "07:40")
        with mock.patch.object(coach, "today_of", lambda uid: TODAY):
            text, _ = coach.context(U, TODAY)
        self.assertIn("РАЗБОРЫ СЕГОДНЯ", text)
        self.assertIn("утро 07:40: Зелёный день — можно в полную.", text)


if __name__ == "__main__":
    unittest.main(verbosity=1)
