"""Разбор прогресса (Глеб 03.11: «проверить все мои данные и, исходя из моих целей, написать, как максимально быстро
улучшить»; «должен быть легче и плавнее»): полный контекст (WHOOP по неделям + цели, вес, еда и вода, привычки…),
промпт (цели, быстрые действия с цифрами и сроком, без советов по тренировкам и диет), поток /api/progress/stream.
usage: python3 coach/test_progress.py
"""
import datetime
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_coach as T  # noqa: E402  (ставит окружение и поддельный WHOOP)
import ai  # noqa: E402
import coach  # noqa: E402
import insights  # noqa: E402

TODAY = T.TODAY
NOW = datetime.datetime.combine(TODAY, datetime.time(13, 40))


def events(r):
    return [json.loads(x[6:]) for x in r.body.decode().split("\n\n") if x.startswith("data: ")]


class ProgressTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, _ = T.seeded(os.path.join(tempfile.mkdtemp(), "progress.db"))
        db = cls.db
        db.meta("owner", T.U)
        db.save_profile(T.U, {"age": "14", "sex": "m", "sport": "бокс", "goal": "form"})
        db.add_goal(T.U, "ложиться до 23:00")
        db.add_goal(T.U, "набрать 3 кг мышц")
        for i in range(1, 6):  # еда и вода прошлых дней: Форма + «Калории»
            d = (TODAY - datetime.timedelta(days=i)).isoformat()
            db.save_meal(T.U, {"day": d, "time": "13:00", "title": "Гречка с курицей", "kcal": 600, "protein": 40, "fat": 12, "carbs": 70})
            db.add_water(T.U, d, "10:00", 500)
        db.save_kcal_days(T.U, {(TODAY - datetime.timedelta(days=1)).isoformat(): {
            "items": [{"name": "Творог", "kcal": 260, "p": 34, "f": 10, "c": 14, "salt": .2}], "water": [750], "type": "train"}})

    def setUp(self):
        self.p = [mock.patch.object(coach, "db", self.db), mock.patch.object(coach, "user_now", lambda u: NOW),
                  mock.patch.object(coach, "today_of", lambda uid: TODAY), mock.patch.object(coach, "tg", return_value={"ok": True, "result": {}})]
        for p in self.p:
            p.start()
        coach.route(T.U, "/api/weight/save", {"day": TODAY.isoformat(), "kg": 63.2, "fat_pct": 14.3})

    def tearDown(self):
        for p in self.p:
            p.stop()

    def test_context_has_all_data(self):
        ctx, p = coach.progress_ctx(T.U, TODAY)
        for part in ("ПО НЕДЕЛЯМ", "нужно сна", "ЦЕЛИ", "ложиться до 23:00", "набрать 3 кг мышц", "ВЕС", "63,2 кг",
                     "ЕДА И ВОДА ЗА 14 ДНЕЙ", "ВОДА СЕГОДНЯ", "ПОСЛЕДНИЕ 14 ДНЕЙ", "ПРОФИЛЬ"):
            self.assertIn(part, ctx, part)
        self.assertTrue(any(w["days"] for w in p["weeks"]))
        self.assertTrue(all("need" in w for w in p["weeks"]))
        food = insights.food_period(self.db, T.U, TODAY)
        self.assertIn("еду записывал 5 из 14 дней", food)
        self.assertIn("белок", food)
        self.assertIn("воду записывал 5 из 14 дней", food)
        self.assertEqual(insights.food_period(self.db, 999, TODAY), "")

    def test_prompt(self):
        ctx, _ = coach.progress_ctx(T.U, TODAY)
        pr = ai.progress_prompt(ctx)
        self.assertIn(ctx, pr)
        self.assertIn(ai.NO_TRAIN_ADVICE, pr)  # без советов, как тренироваться
        self.assertIn("**Итог**", pr)
        self.assertIn("ЦЕЛИ", pr)
        self.assertIn("через сколько дней", pr)  # быстрые действия с цифрами и сроком
        self.assertIn("без диет", pr)
        self.assertIn("лекарств", pr)
        self.assertIn("запас сил", pr)  # без терминов
        self.assertNotIn("сколько тренировок и какой интенсивности", pr)  # старый «план» с тренировками
        wk = ai.progress_prompt(ctx, week=True)
        self.assertIn(ai.NO_TRAIN_ADVICE, wk)
        self.assertNotIn("тренировок и какой", wk)
        with mock.patch.object(ai, "claude", return_value="**Итог**\nхорошо") as c:
            self.assertEqual(ai.progress(ctx), "**Итог**\nхорошо")
        self.assertEqual(c.call_args.kwargs.get("model"), ai.CHAT_MODEL)  # быстрая модель

    def test_stream(self):
        parts = ["**Итог**\nСон — главный ", "тормоз.\n\n**Ложиться до 23:00**\nСейчас: 23:40.\n", "- Ложись в 22:45 — через 7 дней 8 ч"]
        with mock.patch.object(ai, "claude_stream", return_value=iter(parts)) as cs:
            r = T.call("/api/progress/stream", {})
        self.assertEqual(r.code, 200)
        self.assertEqual(r.headers["Content-Type"], "text/event-stream; charset=utf-8")
        evs = events(r)
        self.assertEqual("".join(e.get("t", "") for e in evs), "".join(parts))
        done = evs[-1]
        self.assertTrue(done["done"])
        self.assertEqual(done["progress"]["day"], TODAY.isoformat())
        self.assertIn("<b>Итог</b>", done["progress"]["text"])  # сохранён как HTML, как у бота
        self.assertEqual(cs.call_args.kwargs["model"], ai.CHAT_MODEL)  # быстрая модель
        prompt = cs.call_args.args[0]
        self.assertIn("ЦЕЛИ", prompt)
        self.assertIn("ВЕС", prompt)
        self.assertIn(ai.NO_TRAIN_ADVICE, prompt)
        saved = T.call("/api/progress", {}).json["progress"]
        self.assertEqual(saved, done["progress"])

    def test_stream_fallback_and_errors(self):
        with mock.patch.object(ai, "claude_stream", side_effect=ai.AIError("нет модели")), \
                mock.patch.object(ai, "claude", return_value="**Итог**\nзапасной"):
            evs = events(T.call("/api/progress/stream", {}))
        self.assertEqual(evs[-1]["text"], "**Итог**\nзапасной")
        with mock.patch.object(ai, "claude_stream", side_effect=ai.AIError("лимит")), \
                mock.patch.object(ai, "claude", side_effect=ai.AIError("лимит")):
            evs = events(T.call("/api/progress/stream", {}))
        self.assertEqual(evs[-1], {"error": "лимит"})
        self.assertEqual(T.call("/api/progress/stream", {}, init="").code, 401)
        with mock.patch.object(coach, "progress_ctx", return_value=("", {"weeks": [{"days": 0}]})):
            self.assertEqual(T.call("/api/progress/stream", {}).code, 400)


if __name__ == "__main__":
    unittest.main(verbosity=1)
