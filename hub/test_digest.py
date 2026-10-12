"""Проверка отбивки и напоминаний «Главного» (hub/digest.py) без Telegram.
usage: python3 hub/test_digest.py
"""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import digest  # noqa: E402


def ts(y, mo, d, h=0, mi=0):
    return datetime.datetime(y, mo, d, h, mi, tzinfo=digest.TZ).timestamp()


SAT = ts(2026, 10, 10, 12, 0)  # суббота, 12:00 по Москве
APPS = [{"name": "horo-bot", "label": "📚 HDP", "status": "online", "uptime": 100, "restarts": 0, "mem": 50},
        {"name": "coach-bot", "label": "💪 Тренер", "status": "errored", "uptime": 0, "restarts": 9, "mem": 0}]
SRV = {"mem_total": 2000, "mem_free": 900, "disk_total": 80.0, "disk_free": 40.0}


class Parse(unittest.TestCase):
    def p(self, s, now=SAT):
        return digest.parse_remind(s, now)

    def test_daily(self):
        self.assertEqual(self.p("9:05 выпить воды"),
                         {"time": "09:05", "days": None, "date": None, "text": "выпить воды"})
        self.assertEqual(self.p("09:00 звонок в 10:30")["text"], "звонок в 10:30")

    def test_weekdays(self):
        self.assertEqual(self.p("пн,ср 18:30 спорт")["days"], [0, 2])
        self.assertEqual(self.p("пн ср пт 18:30 спорт")["days"], [0, 2, 4])
        self.assertEqual(self.p("пн-пт 8:00 школа")["days"], [0, 1, 2, 3, 4])
        self.assertEqual(self.p("пт-пн 8:00 x")["days"], [0, 4, 5, 6])
        self.assertEqual(self.p("будни 8:00 x")["days"], [0, 1, 2, 3, 4])
        self.assertEqual(self.p("Выходные 11:00 x")["days"], [5, 6])
        self.assertIsNone(self.p("будни,выходные 8:00 x")["days"])

    def test_once(self):
        self.assertEqual(self.p("25.10 10:00 врач")["date"], "2026-10-25")
        self.assertEqual(self.p("01.03 10:00 x")["date"], "2027-03-01")  # прошло — следующий год
        self.assertEqual(self.p("10.10 13:00 x")["date"], "2026-10-10")  # сегодня позже
        self.assertEqual(self.p("завтра 7:00 x")["date"], "2026-10-11")
        self.assertEqual(self.p("5.1.2027 7:00 x")["date"], "2027-01-05")

    def test_invalid(self):
        for s in ("", "текст без времени", "25:00 x", "9:00", "пн,хх 9:00 x", "31.02 9:00 x",
                  "10.10 11:00 x", "01.01.2020 9:00 x", "сегодня 11:59 x"):
            with self.assertRaises(ValueError, msg=s):
                self.p(s)


class Commands(unittest.TestCase):
    def test_remind_and_list_and_delete(self):
        st = {}
        reply, btn = digest.command(st, "/remind пн,ср 18:30 спорт <b>", SAT)
        self.assertIn("по пн, ср в 18:30", reply)
        self.assertIn("&lt;b&gt;", reply)
        digest.command(st, "/remind@HubBot 25.10 10:00 врач", SAT)
        text, rows = digest.command(st, "/reminders", SAT)
        self.assertIn("25.10 в 10:00 — врач", text)
        self.assertEqual(len(rows), 2)
        rid = st["reminders"][0]["id"]
        note, text, rows = digest.callback(st, f"rmd|{rid}")
        self.assertEqual(note, "🗑 Удалено")
        self.assertEqual(len(st["reminders"]), 1)
        self.assertEqual(digest.callback(st, f"rmd|{rid}")[0], "Уже удалено")
        self.assertIsNone(digest.callback(st, "home"))
        self.assertIn("Не понял", digest.command(st, "/remind когда-нибудь 9:00 x", SAT)[0])
        self.assertIn("Как задать", digest.command(st, "/remind", SAT)[0])
        self.assertIn("Напоминаний нет", digest.command({}, "/reminders", SAT)[0])

    def test_not_ours(self):
        for t in ("/paid 07.11", "привет", "/remindme", "", None):
            self.assertIsNone(digest.command({}, t, SAT))

    def test_digest_setting(self):
        st = {}
        self.assertIn("21:00", digest.command(st, "/digest", SAT)[0])
        digest.command(st, "/digest off", SAT)
        self.assertEqual(st["digest_time"], "off")
        digest.command(st, "/digest 8:30", SAT)
        self.assertEqual(st["digest_time"], "08:30")
        self.assertIn("Пришли время", digest.command(st, "/digest 25:00", SAT)[0])


class Due(unittest.TestCase):
    def run_at(self, st, t):
        return digest.due(st, t, APPS, SRV, "2026-10-17")

    def test_daily_at_and_after_time_no_duplicates(self):
        st = {"digest_time": "off"}
        digest.command(st, "/remind 13:00 вода", SAT)
        self.assertEqual(self.run_at(st, ts(2026, 10, 10, 12, 59)), [])
        self.assertEqual(self.run_at(st, ts(2026, 10, 10, 13, 2)), [("⏰ вода", None)])  # пропущенные минуты
        self.assertEqual(self.run_at(st, ts(2026, 10, 10, 13, 3)), [])
        self.assertEqual(self.run_at(st, ts(2026, 10, 11, 13, 0)), [("⏰ вода", None)])  # на следующий день

    def test_created_after_time_not_sent_today(self):
        st = {"digest_time": "off"}
        digest.command(st, "/remind 9:00 утро", SAT)
        self.assertEqual(self.run_at(st, SAT + 60), [])
        self.assertEqual(len(self.run_at(st, ts(2026, 10, 11, 9, 0))), 1)

    def test_too_late_skipped(self):
        st = {"digest_time": "off"}
        digest.command(st, "/remind 13:00 вода", SAT)
        self.assertEqual(self.run_at(st, ts(2026, 10, 10, 16, 30)), [])  # 3.5 ч — пропуск
        self.assertEqual(self.run_at(st, ts(2026, 10, 10, 17, 0)), [])

    def test_weekdays(self):
        st = {"digest_time": "off"}
        digest.command(st, "/remind будни 13:00 школа", SAT)
        self.assertEqual(self.run_at(st, ts(2026, 10, 10, 13, 0)), [])  # суббота
        self.assertEqual(self.run_at(st, ts(2026, 10, 11, 13, 0)), [])  # воскресенье
        self.assertEqual(len(self.run_at(st, ts(2026, 10, 12, 13, 0))), 1)  # понедельник

    def test_once_removed(self):
        st = {"digest_time": "off"}
        digest.command(st, "/remind 10.10 14:00 разово", SAT)
        self.assertEqual(self.run_at(st, ts(2026, 10, 10, 14, 0)), [("⏰ разово", None)])
        self.assertEqual(st["reminders"], [])
        self.assertEqual(self.run_at(st, ts(2026, 10, 10, 14, 1)), [])

    def test_once_missed_delivered_late(self):
        st = {"digest_time": "off"}
        digest.command(st, "/remind 10.10 14:00 разово", SAT)
        out = self.run_at(st, ts(2026, 10, 12, 9, 0))
        self.assertEqual(len(out), 1)
        self.assertIn("было на 10.10 14:00", out[0][0])
        self.assertEqual(st["reminders"], [])

    def test_digest_once_per_day_and_content(self):
        st = {}
        digest.command(st, "/remind 22:00 спать", SAT)
        digest.command(st, "/remind вс 10:00 уборка", SAT)
        self.assertEqual(self.run_at(st, ts(2026, 10, 10, 20, 59)), [])
        out = self.run_at(st, ts(2026, 10, 10, 21, 1))
        self.assertEqual(len(out), 1)
        text, buttons = out[0]
        self.assertIn("Отбивка · сб 10.10", text)
        self.assertIn("Не работают: 💪 Тренер (errored)", text)
        self.assertIn("Работают: 1", text)
        self.assertIn("Сервер занят: память 55% · диск 50%", text)
        self.assertIn("осталось 7 дн.", text)
        self.assertIn("Сегодня: 22:00 спать", text)
        self.assertIn("Завтра: 10:00 уборка; 22:00 спать", text)
        self.assertIn("Доработать", text)
        self.assertEqual(buttons, [[{"text": "✏️ Доработать", "callback_data": "fix"}]])
        self.assertEqual(self.run_at(st, ts(2026, 10, 10, 21, 30)), [])
        self.assertEqual(len(self.run_at(st, ts(2026, 10, 11, 21, 0))), 1)

    def test_digest_all_ok_and_no_paid(self):
        text = digest.due({}, ts(2026, 10, 10, 21, 0), APPS[:1], {}, None)[0][0]
        self.assertIn("Все боты работают (1)", text)
        self.assertIn("/paid", text)
        self.assertNotIn("⏰", text)
        self.assertIn("pm2 не ответил", digest.due({}, ts(2026, 10, 10, 21, 0), None, SRV, None)[0][0])

    def test_digest_off_and_late(self):
        st = {}
        digest.command(st, "/digest off", SAT)
        self.assertEqual(self.run_at(st, ts(2026, 10, 10, 21, 0)), [])
        st = {}
        self.assertEqual(self.run_at(st, ts(2026, 10, 11, 0, 30)), [])  # вчерашняя не догоняет
        st = {"digest_time": "09:00"}
        self.assertEqual(self.run_at(st, ts(2026, 10, 10, 12, 30)), [])  # 3.5 ч опоздания
        self.assertEqual(st["digest_day"], "2026-10-10")

    def test_digest_moved_later_resends_today(self):
        st = {"digest_time": "11:00"}
        self.assertEqual(len(self.run_at(st, ts(2026, 10, 10, 11, 0))), 1)
        digest.command(st, "/digest 13:00", SAT)
        self.assertEqual(len(self.run_at(st, ts(2026, 10, 10, 13, 0))), 1)


if __name__ == "__main__":
    unittest.main()
