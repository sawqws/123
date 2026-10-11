"""Проверка тренера без Telegram и WHOOP: синхронизация (поддельный WHOOP), расчёты, подпись Mini App, API,
вход в WHOOP, утренний разбор по расписанию, цели и разбор тренировки (Claude подменён).
usage: python3 coach/test_coach.py
"""
import datetime
import hashlib
import hmac
import io
import json
import os
import random
import sys
import tempfile
import time
import unittest
import urllib.parse
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
TMP = tempfile.mkdtemp()
os.environ.update(COACH_TOKEN="123:TEST", COACH_DB=os.path.join(TMP, "boot.db"), COACH_URL="https://coach.example")
for k in ("WHOOP_CLIENT_ID", "WHOOP_CLIENT_SECRET", "COACH_ALLOWED"):
    os.environ.pop(k, None)
import ai  # noqa: E402
import coach  # noqa: E402
import insights  # noqa: E402
import store  # noqa: E402
import whoop  # noqa: E402

U = 7
TODAY = datetime.date(2026, 10, 11)


def fake_whoop(days=40, today=TODAY, seed=1):
    """Записи как в WHOOP API v2: цикл со сна накануне, восстановление, сон, бокс 4 раза в неделю."""
    rnd = random.Random(seed)
    out = {"cycle": [], "recovery": [], "sleep": [], "workout": []}
    for i in range(days, -1, -1):
        d = today - datetime.timedelta(days=i)
        bed = datetime.datetime.combine(d, datetime.time(0, 0)) - datetime.timedelta(hours=1, minutes=rnd.randint(0, 80)) \
            - datetime.timedelta(hours=3)  # UTC, у пользователя +03:00
        wake = bed + datetime.timedelta(hours=7, minutes=rnd.randint(0, 120))
        z = lambda t: t.strftime("%Y-%m-%dT%H:%M:%S.000Z")  # noqa: E731
        cid, sid = 1000 + i, f"s-{i}"
        last = i == 0
        out["cycle"].append({"id": cid, "start": z(bed), "end": None if last else z(bed + datetime.timedelta(days=1)),
                             "timezone_offset": "+03:00", "score_state": "SCORED",
                             "score": {"strain": round(rnd.uniform(6, 17), 1), "kilojoule": rnd.uniform(9000, 13000),
                                       "average_heart_rate": 70, "max_heart_rate": 185}})
        rec = rnd.randint(25, 95) if i else 82
        out["recovery"].append({"cycle_id": cid, "sleep_id": sid, "created_at": z(wake), "score_state": "SCORED",
                                "score": {"user_calibrating": False, "recovery_score": rec,
                                          "resting_heart_rate": 52 + (95 - rec) // 10, "hrv_rmssd_milli": 50 + rec * .6,
                                          "spo2_percentage": 97.1, "skin_temp_celsius": 33.4}})
        ms = lambda h: int(h * 3_600_000)  # noqa: E731
        hours = (wake - bed).total_seconds() / 3600
        out["sleep"].append({"id": sid, "cycle_id": cid, "start": z(bed), "end": z(wake), "timezone_offset": "+03:00",
                             "nap": False, "score_state": "SCORED", "score": {
                                 "stage_summary": {"total_in_bed_time_milli": ms(hours), "total_awake_time_milli": ms(.5),
                                                   "total_light_sleep_time_milli": ms(hours * .5 - .5),
                                                   "total_slow_wave_sleep_time_milli": ms(hours * .25),
                                                   "total_rem_sleep_time_milli": ms(hours * .25),
                                                   "sleep_cycle_count": 4, "disturbance_count": 6},
                                 "sleep_needed": {"baseline_milli": ms(8.5), "need_from_sleep_debt_milli": ms(.4),
                                                  "need_from_recent_strain_milli": ms(.2), "need_from_recent_nap_milli": 0},
                                 "respiratory_rate": 15.2, "sleep_performance_percentage": rnd.randint(70, 98),
                                 "sleep_consistency_percentage": rnd.randint(55, 90), "sleep_efficiency_percentage": 93.5}})
        if d.weekday() in (0, 2, 4, 5) and not last:
            st = datetime.datetime.combine(d, datetime.time(15, 0))
            out["workout"].append({"id": f"w-{i}", "start": z(st), "end": z(st + datetime.timedelta(minutes=110)),
                                   "timezone_offset": "+03:00", "sport_name": "boxing", "sport_id": 39, "score_state": "SCORED",
                                   "score": {"strain": round(rnd.uniform(11, 16), 1), "average_heart_rate": 148, "max_heart_rate": 192,
                                             "kilojoule": 3400, "percent_recorded": 100,
                                             "zone_durations": {"zone_zero_milli": 300000, "zone_one_milli": 1200000,
                                                                "zone_two_milli": 1800000, "zone_three_milli": 1800000,
                                                                "zone_four_milli": 900000, "zone_five_milli": 300000}}})
    return out


class FakeWhoop:
    """Подменяет HTTP: отдаёт fake_whoop постранично (по 25, с next_token)."""

    def __init__(self, data):
        self.data, self.calls = data, []

    def get(self, path, params=None):
        self.calls.append(path)
        if path.endswith("/body"):
            return {"height_meter": 1.72, "weight_kilogram": 61.5, "max_heart_rate": 205}
        if path.endswith("/basic"):
            return {"first_name": "Gleb"}
        kind = {v: k for k, v in whoop.PATHS.items()}[path]
        rows = [r for r in self.data[kind] if (r.get("start") or r.get("created_at")) >= params["start"][:19]]
        at = int(params.get("nextToken") or 0)
        page = rows[at:at + 25]
        return {"records": page, "next_token": str(at + 25) if at + 25 < len(rows) else None}


def signed(user, token="123:TEST", age=0):
    p = {"auth_date": str(int(time.time()) - age), "query_id": "q", "user": json.dumps(user)}
    check = "\n".join(f"{k}={v}" for k, v in sorted(p.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    p["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(p)


def seeded(path=None, days=40):
    db = store.Store(path or os.path.join(tempfile.mkdtemp(), "c.db"))
    db.user(U, "Глеб")
    db.save_token(U, "acc", "ref", time.time() + 3600)
    fw = FakeWhoop(fake_whoop(days))
    with mock.patch.object(whoop.Client, "get", fw.get):
        whoop.sync(db, U, "cid", "sec", days=days + 2)
    return db, fw


class SyncTest(unittest.TestCase):
    def test_days_and_pages(self):
        db, fw = seeded()
        self.assertGreater(len([c for c in fw.calls if "cycle" in c]), 1)  # было несколько страниц
        rec = db.records(U, "recovery", TODAY.isoformat(), TODAY.isoformat())
        self.assertEqual(len(rec), 1)
        self.assertEqual(rec[0]["score"]["recovery_score"], 82)
        # сон, закончившийся утром 11-го, относится к 11-му; цикл, начавшийся вечером 10-го, — тоже к 11-му
        self.assertEqual(len(db.records(U, "sleep", TODAY.isoformat(), TODAY.isoformat())), 1)
        self.assertEqual(len(db.records(U, "cycle", TODAY.isoformat(), TODAY.isoformat())), 1)
        self.assertEqual(whoop.body(db, U)["weight_kilogram"], 61.5)

    def test_fresh_only_new(self):
        db, _ = seeded()
        fw = FakeWhoop(fake_whoop(40))
        with mock.patch.object(whoop.Client, "get", fw.get):
            self.assertEqual(whoop.sync(db, U, "cid", "sec", days=3), [])
        data = fake_whoop(40)
        data["workout"].append({"id": "w-new", "start": "2026-10-11T12:00:00.000Z", "end": "2026-10-11T13:30:00.000Z",
                                "timezone_offset": "+03:00", "sport_name": "boxing", "score_state": "SCORED",
                                "score": {"strain": 14.2, "kilojoule": 3000, "average_heart_rate": 150, "max_heart_rate": 190}})
        with mock.patch.object(whoop.Client, "get", FakeWhoop(data).get):
            fresh = whoop.sync(db, U, "cid", "sec", days=3)
        self.assertEqual([(k, r["id"]) for k, r in fresh], [("workout", "w-new")])
        self.assertEqual(fresh[0][1]["_day"], "2026-10-11")

    def test_day_of(self):
        self.assertEqual(whoop.day_of("cycle", {"start": "2026-10-10T20:30:00Z", "timezone_offset": "+03:00"}), "2026-10-11")
        self.assertEqual(whoop.day_of("cycle", {"start": "2026-10-11T04:00:00Z", "timezone_offset": "+03:00"}), "2026-10-11")
        self.assertEqual(whoop.day_of("sleep", {"start": "2026-10-10T20:00:00Z", "end": "2026-10-11T05:00:00Z",
                                                "timezone_offset": "+03:00"}), "2026-10-11")
        self.assertEqual(whoop.day_of("workout", {"start": "2026-10-11T23:30:00Z", "timezone_offset": "-05:00"}), "2026-10-11")

    def test_refresh_on_expired(self):
        db = store.Store(os.path.join(tempfile.mkdtemp(), "c.db"))
        db.save_token(U, "old", "r1", time.time() - 10)
        with mock.patch.object(whoop, "_post_form", return_value={"access_token": "new", "refresh_token": "r2",
                                                                  "expires_in": 3600}) as pf:
            self.assertEqual(whoop.Client(db, U, "c", "s")._access(), "new")
        form = pf.call_args[0][1]
        self.assertEqual((form["grant_type"], form["refresh_token"], form["scope"]), ("refresh_token", "r1", "offline"))
        self.assertEqual(db.token(U)["refresh"], "r2")
        with mock.patch.object(whoop, "_post_form", side_effect=RuntimeError("WHOOP 400: invalid_grant")):
            db.save_token(U, "x", "r2", time.time() - 10)
            with self.assertRaises(whoop.NotConnected):
                whoop.Client(db, U, "c", "s")._access()

    def test_auth_link(self):
        u = urllib.parse.urlparse(whoop.auth_link("CID", "https://x/whoop/callback", "abcdefgh12"))
        q = dict(urllib.parse.parse_qsl(u.query))
        self.assertEqual(u.netloc, "api.prod.whoop.com")
        self.assertEqual(q["response_type"], "code")
        self.assertIn("offline", q["scope"].split())
        self.assertIn("read:recovery", q["scope"].split())


class InsightsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, _ = seeded()

    def test_today(self):
        t = insights.today(self.db, U, TODAY, {"age": 14, "weight": 60}, whoop.body(self.db, U))
        self.assertEqual(t["recovery"]["score"], 82)
        self.assertEqual(t["recovery"]["zone"], "green")
        self.assertEqual(t["band"], (14, 18))
        self.assertGreater(t["sleep"]["hours"], 6)
        self.assertGreater(t["base"]["days"], 20)
        n = t["nutrition"]
        self.assertTrue(n["teen"])
        self.assertEqual(n["protein"], 96)
        self.assertGreater(n["kcal"], 2000)
        self.assertEqual(round((n["protein"] * 4 + n["fat"] * 9 + n["carbs"] * 4) / 100), round(n["kcal"] / 100))

    def test_teen_no_hard_cut(self):
        base = {"kcal": 2800}
        teen = insights.nutrition({"age": 14, "weight": 60, "goal": "dry"}, {}, base)
        adult = insights.nutrition({"age": 25, "weight": 60, "goal": "dry"}, {}, base)
        self.assertGreaterEqual(teen["kcal"], 2800 * .95 - 10)
        self.assertLess(adult["kcal"], teen["kcal"])
        self.assertIsNone(insights.nutrition({}, {}, base))
        self.assertEqual(insights.nutrition({}, {"weight_kilogram": 70}, {})["how"], "примерно, по весу (добавь рост и возраст)")

    def test_tips(self):
        base = {"hrv": 100, "rhr": 50, "skin_temp": 33.0, "resp": 15}
        day = {"recovery": {"zone": "red", "hrv": 70, "rhr": 58, "skin_temp": 33.8, "calibrating": False},
               "sleep": {"hours": 6.0, "consistency": 50, "resp": 16.5}}
        texts = " ".join(x["text"] for x in insights.tips(day, base, 1.5, {"age": 14}, []))
        for w in ("ВСР на 30%", "Пульс покоя 58", "Температура", "дыхания", "8–10 часов", "Режим сна", "1,5 раза"):
            self.assertIn(w, texts)
        good = insights.tips({"recovery": {"zone": "green", "hrv": 101, "rhr": 50, "calibrating": False}, "sleep": None},
                             base, 1.0, {}, [])
        self.assertEqual(good[0]["tone"], "good")

    def test_series_and_context(self):
        s = insights.series(self.db, U, TODAY, 30)
        self.assertEqual(len(s), 30)
        self.assertEqual(s[-1]["recovery"], 82)
        text, _ = insights.text_context(self.db, U, TODAY, {"age": 14, "sport": "бокс"}, {})
        self.assertIn("14 лет", text)
        self.assertIn("восстановление: 82%", text)
        self.assertIn("Бокс", text)

    def test_bedtime(self):
        t = insights.today(self.db, U, TODAY, {"age": 14}, {})
        bt = t["bedtime"]
        self.assertEqual(bt["need"], 9.1)  # нужный сон WHOOP 8,5 + 0,4 + 0,2 больше подросткового минимума
        wake = int(bt["wake"][:2]) * 60 + int(bt["wake"][3:])
        bed = int(bt["bed"][:2]) * 60 + int(bt["bed"][3:])
        self.assertTrue(0 <= (wake - 546 - 20) - bed + (1440 if bed > wake else 0) < 10)
        self.assertEqual(bt["weekend"], False)  # 12.10.2026 — понедельник
        self.assertIsNotNone(t["yesterday"]["strain"])
        text, _ = insights.text_context(self.db, U, TODAY, {"age": 14}, {})
        self.assertIn(f"лечь до {bt['bed']}", text)
        self.assertNotIn("ЦЕЛИ", text)
        days = {"2026-10-11": {"sleep": {"end": "07:00", "need": 7.0}}, "2026-10-10": {"sleep": {"end": "07:20"}}}
        self.assertEqual(insights.bedtime(days, TODAY, {"age": 30})["bed"], "23:20")  # 07:10 − 7,5 ч − 20 мин
        self.assertIsNone(insights.bedtime({}, TODAY, {}))

    def test_sports(self):
        self.assertEqual(insights.sport_name("boxing"), "Бокс")
        self.assertEqual(insights.sport_name("box-jumps"), "Box jumps")


class AITest(unittest.TestCase):
    def test_to_html(self):
        h = ai.to_html("### Итог\n**Готов** к работе <3\n- сон 8 ч\n* вода")
        self.assertEqual(h, "<b>Итог</b>\n<b>Готов</b> к работе &lt;3\n• сон 8 ч\n• вода")

    def test_meal_parse(self):
        with mock.patch.object(ai, "claude", return_value='Вот: {"food": true, "title": "Гречка", "kcal": "350.4", '
                                                          '"protein": 20, "fat": 8, "carbs": 50, "comment": "ок"}'):
            m = ai.meal("", "гречка")
        self.assertEqual((m["title"], m["kcal"], m["protein"]), ("Гречка", 350, 20))
        with mock.patch.object(ai, "claude", return_value="не знаю"):
            with self.assertRaises(ai.AIError):
                ai.meal("", "что-то")


class SyncThread:
    """threading.Thread, который выполняет задачу сразу (для проверки расписания)."""

    def __init__(self, target, args=(), kwargs=None, daemon=None):
        self.target, self.args, self.kwargs = target, args, kwargs or {}

    def start(self):
        self.target(*self.args, **self.kwargs)


class GoalsStoreTest(unittest.TestCase):
    def test_crud(self):
        db = store.Store(os.path.join(tempfile.mkdtemp(), "g.db"))
        g = db.add_goal(U, "  сгонка   до 60 кг к декабрю ")
        self.assertEqual(g["text"], "сгонка до 60 кг к декабрю")
        db.add_goal(U, "ложиться до 23:00")
        db.add_goal(8, "чужая цель")
        self.assertEqual([x["text"] for x in db.goals(U)], ["сгонка до 60 кг к декабрю", "ложиться до 23:00"])
        self.assertFalse(db.delete_goal(8, g["id"]))  # чужую не удалить
        self.assertTrue(db.delete_goal(U, g["id"]))
        self.assertEqual(len(db.goals(U)), 1)
        with self.assertRaises(ValueError):
            db.add_goal(U, " ")
        for i in range(store.MAX_GOALS - 1):
            db.add_goal(U, f"цель {i}")
        with self.assertRaises(ValueError):
            db.add_goal(U, "ещё одна")
        self.assertEqual(len(db.add_goal(9, "я" * 500)["text"]), 300)

    def test_morning_value(self):
        self.assertEqual(store.morning_value("7:30"), "07:30")
        self.assertEqual(store.morning_value("08.15"), "08:15")
        self.assertEqual(store.morning_value("Выкл"), "off")
        self.assertEqual(store.morning_value("вкл"), "07:00")
        for bad in ("25:00", "1:00", "утром", "7"):
            with self.assertRaises(ValueError):
                store.morning_value(bad)


class MorningDueTest(unittest.TestCase):
    def test_rules(self):
        due, at = coach.morning_due, datetime.time(7, 0)
        t = lambda h, m=0: datetime.datetime(2026, 10, 11, h, m)  # noqa: E731
        self.assertIsNone(due(at, t(6, 59), "full", False, False))
        self.assertEqual(due(at, t(7), "full", False, False), "brief")
        self.assertIsNone(due(at, t(7), "full", True, False))  # уже было
        self.assertIsNone(due(at, t(8), "part", False, False))  # только сон — ждём восстановление
        self.assertIsNone(due(at, t(9, 59), None, False, False))
        self.assertEqual(due(at, t(10), "part", False, False), "brief")
        self.assertEqual(due(at, t(10), None, False, False), "early")
        self.assertIsNone(due(at, t(10, 5), None, False, True))
        self.assertEqual(due(at, t(11), "full", False, True), "brief")  # сон досчитан после плана
        self.assertIsNone(due(at, t(15), "full", False, True))
        self.assertIsNone(due(None, t(8), "full", False, False))  # выключено
        tz = coach.ZoneInfo("Europe/Moscow")
        self.assertEqual(due(at, datetime.datetime(2026, 10, 11, 7, 30, tzinfo=tz), "full", False, False), "brief")


class Resp:
    def __init__(self):
        self.code, self.headers, self.body = None, {}, b""


def call(path, body=None, user=None, init=None, method=None):
    """Запрос к веб-серверу бота без сети."""
    h = coach.Web.__new__(coach.Web)
    raw = json.dumps(body).encode() if body is not None else b""
    h.headers = {"X-Init-Data": init if init is not None else signed(user or {"id": U, "first_name": "Глеб"}),
                 "Content-Length": str(len(raw))}
    h.rfile, h.wfile = io.BytesIO(raw), io.BytesIO()
    h.path = path
    out = Resp()
    h.send_response = lambda c: setattr(out, "code", c)
    h.send_header = lambda k, v: out.headers.__setitem__(k, v)
    h.end_headers = lambda: None
    (h.do_POST if (method or ("POST" if body is not None else "GET")) == "POST" else h.do_GET)()
    out.body = h.wfile.getvalue()
    try:
        out.json = json.loads(out.body)
    except ValueError:
        out.json = None
    return out


class BotTest(unittest.TestCase):
    def setUp(self):
        self.db, _ = seeded(os.path.join(tempfile.mkdtemp(), "bot.db"))
        self.db.meta("owner", U)
        self.sent = []
        self.p = [mock.patch.object(coach, "db", self.db),
                  mock.patch.object(coach, "tg", side_effect=self.fake_tg),
                  mock.patch.object(coach, "user_now", return_value=datetime.datetime(2026, 10, 11, 8, 0))]
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()
        self.db.db.close()

    def fake_tg(self, method, **kw):
        self.sent.append((method, kw))
        return {"ok": True, "result": {}}

    def texts(self):
        return [kw.get("text", "") for m, kw in self.sent if m == "sendMessage"]

    def test_access(self):
        self.assertEqual(call("/api/all", init="bad").code, 401)
        self.assertEqual(call("/api/all", user={"id": 99}).code, 403)
        r = call("/api/all")
        self.assertEqual(r.code, 200)
        self.assertTrue(r.json["connected"])
        self.assertEqual(r.json["today"]["recovery"]["score"], 82)
        self.assertEqual(len(r.json["series"]), 30)
        self.assertEqual(r.json["redirect"], "https://coach.example/whoop/callback")

    def test_profile_and_meals(self):
        self.assertEqual(call("/api/profile", {"age": "14", "weight": "60,5", "goal": "mass", "sport": "бокс"}).json["profile"],
                         {"age": 14, "weight": 60.5, "goal": "mass", "sport": "бокс"})
        self.assertEqual(call("/api/profile", {"age": 3}).code, 400)
        r = call("/api/meal", {"title": "Омлет", "kcal": 300, "protein": 20, "fat": 20, "carbs": 5})
        self.assertEqual(r.json["day"]["eaten"]["kcal"], 300)
        mid = r.json["meal"]["id"]
        self.assertEqual(call("/api/meal", {"id": mid, "kcal": 350}).json["day"]["eaten"]["kcal"], 350)
        with mock.patch.object(ai, "claude", return_value='{"food": true, "title": "Банан", "kcal": 100, "protein": 1, '
                                                          '"fat": 0, "carbs": 25, "comment": "перед тренировкой — отлично"}'):
            r = call("/api/meal/estimate", {"text": "банан"})
        self.assertEqual(r.json["day"]["eaten"]["kcal"], 450)
        self.assertEqual(call("/api/meal/delete", {"id": mid}).json["day"]["eaten"]["kcal"], 100)

    def test_ask_keeps_history(self):
        with mock.patch.object(ai, "claude", return_value="**Да**, но без спарринга.") as c:
            r = call("/api/ask", {"text": "Можно спарринг?"})
        self.assertEqual([m["role"] for m in r.json["history"]], ["user", "coach"])
        self.assertIn("восстановление: 82%", c.call_args[0][0])
        with mock.patch.object(ai, "claude", side_effect=ai.AIError("лимит")):
            self.assertEqual(call("/api/ask", {"text": "ещё"}).code, 503)

    def test_connect_flow(self):
        self.assertEqual(call("/api/connect", {}).code, 400)  # ключей ещё нет
        coach.on_message({"chat": {"id": U, "type": "private"}, "from": {"id": U}, "message_id": 5,
                          "text": "/whoop CID SECRET"})
        self.assertEqual(coach.creds(), ("CID", "SECRET"))
        self.assertIn(("deleteMessage", {"chat_id": U, "message_id": 5}), self.sent)
        url = call("/api/connect", {}).json["url"]
        state = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))["state"]
        with mock.patch.object(whoop, "exchange") as ex, mock.patch.object(coach, "first_sync"):
            r = call(f"/whoop/callback?code=abc&state={state}", method="GET", init="")
            self.assertIn("WHOOP подключён", r.body.decode())
            self.assertEqual(ex.call_args[0][2:], ("abc", "CID", "SECRET", "https://coach.example/whoop/callback"))
            r = call(f"/whoop/callback?code=abc&state={state}", method="GET", init="")  # второй раз — нет
            self.assertIn("устарела", r.body.decode())

    def test_morning_and_workout(self):
        with mock.patch.object(ai, "claude", return_value="**Зелёный день.**\n- бокс в полную"):
            coach.morning_brief(U)
        t = self.texts()[-1]
        self.assertIn("Доброе утро", t)
        self.assertIn("82%", t)
        self.assertIn("• бокс в полную", t)
        self.assertIn("бокс в полную", self.db.brief(U, "2026-10-11")["text"])
        with mock.patch.object(ai, "claude", side_effect=ai.AIError("нет")):
            coach.morning_brief(U)
        self.assertIn("Ориентир нагрузки", self.texts()[-1])
        # react: тренировка -> разбор один раз; утренний разбор шлёт расписание, а не react
        with mock.patch.object(coach.threading, "Thread") as th:
            rec = {"_day": "2026-10-11", "cycle_id": 1}
            wk = {"_day": "2026-10-11", "id": "w9", "start": "2026-10-11T12:00:00Z", "end": "2026-10-11T13:00:00Z",
                  "score_state": "SCORED", "score": {"strain": 12}}
            coach.react(U, [("recovery", rec), ("workout", wk)])
            coach.react(U, [("recovery", rec), ("workout", wk)])
        targets = [c.kwargs["target"].__name__ for c in th.call_args_list]
        self.assertEqual(targets, ["workout_note"])

    def test_chat_routing(self):
        with mock.patch.object(coach.threading, "Thread") as th:
            coach.on_message({"chat": {"id": U, "type": "private"}, "from": {"id": U}, "text": "съел гречку с курицей"})
            coach.on_message({"chat": {"id": U, "type": "private"}, "from": {"id": U}, "text": "Почему упало восстановление?"})
        self.assertEqual([c.kwargs["target"].__name__ for c in th.call_args_list], ["log_meal", "answer"])
        coach.on_message({"chat": {"id": 99, "type": "private"}, "from": {"id": 99}, "text": "привет"})
        self.assertIn("личный тренер", self.texts()[-1])

    def test_log_meal(self):
        with mock.patch.object(ai, "claude", return_value='{"food": true, "title": "Гречка с курицей", "kcal": 520, '
                                                          '"protein": 38, "fat": 12, "carbs": 60, "comment": "Отлично после бокса"}'):
            coach.log_meal(U, U, "съел гречку с курицей")
        t = self.texts()[-1]
        self.assertIn("Гречка с курицей", t)
        self.assertIn("520 ккал", t)
        self.assertEqual(self.db.meals(U, "2026-10-11")[0]["kcal"], 520)

    # ---------- утренний разбор по расписанию ----------

    def at(self, hh, mm, day=11):
        return datetime.datetime(2026, 10, day, hh, mm)

    def mornings(self):
        return [t for t in self.texts() if "Доброе утро" in t]

    def run_morning(self, now, reply="**Зелёный день.**"):
        with mock.patch.object(coach.threading, "Thread", SyncThread), \
                mock.patch.object(ai, "claude", return_value=reply) as c:
            kind = coach.maybe_morning(self.db.user(U), now)
        return kind, c

    def test_morning_once_after_sleep(self):
        self.assertIsNone(self.run_morning(self.at(6, 59))[0])  # раньше времени — ничего
        kind, c = self.run_morning(self.at(7, 0))
        self.assertEqual(kind, "brief")
        m = self.mornings()
        self.assertEqual(len(m), 1)
        for w in ("Восстановление <b>82%</b>", "ВСР", "пульс покоя", "Сон <b>", "выполнение", "эффективность",
                  "Нагрузка вчера"):
            self.assertIn(w, m[0])
        self.assertIn("ОТБОЙ СЕГОДНЯ", c.call_args[0][0])
        for hm in ((7, 1), (9, 30), (12, 0)):
            self.assertIsNone(self.run_morning(self.at(*hm))[0])
        self.assertEqual(len(self.mornings()), 1)
        # назавтра — снова (данных за 12-е нет: к 10:00 короткий план)
        self.assertIsNone(self.run_morning(self.at(7, 0, 12))[0])
        self.assertEqual(self.run_morning(self.at(10, 0, 12))[0], "early")

    def test_morning_manual_counts(self):
        with mock.patch.object(ai, "claude", return_value="ок"):
            coach.morning_brief(U)  # /today после пробуждения
        self.assertIsNone(self.run_morning(self.at(7, 30))[0])
        self.assertEqual(len(self.mornings()), 1)

    def test_morning_fallback_then_brief(self):
        today = TODAY.isoformat()
        saved = self.db.q("SELECT * FROM records WHERE user=? AND day=? AND kind IN ('recovery','sleep')", (U, today))
        self.db.x("DELETE FROM records WHERE user=? AND day=? AND kind IN ('recovery','sleep')", (U, today))
        for hm in ((7, 0), (8, 30), (9, 59)):
            self.assertIsNone(self.run_morning(self.at(*hm))[0])
        with mock.patch.object(ai, "claude", side_effect=ai.AIError("нет")):
            with mock.patch.object(coach.threading, "Thread", SyncThread):
                self.assertEqual(coach.maybe_morning(self.db.user(U), self.at(10, 0)), "early")
        m = self.mornings()
        self.assertEqual(len(m), 1)
        self.assertIn("ещё не посчитал сон", m[0])
        self.assertIn("Отбой сегодня", m[0])  # план по правилам, Claude недоступен
        self.assertIn("Нагрузка вчера", m[0])
        self.assertIsNone(self.run_morning(self.at(10, 1))[0])
        for r in saved:  # WHOOP досчитал сон
            self.db.x("INSERT INTO records VALUES (?,?,?,?,?,?)", tuple(r.values()))
        self.assertEqual(self.run_morning(self.at(10, 30))[0], "brief")
        self.assertIsNone(self.run_morning(self.at(10, 45))[0])
        self.assertEqual(len(self.mornings()), 2)

    def test_morning_too_late_and_off(self):
        self.assertIsNone(self.run_morning(self.at(15, 0))[0])  # 07:00 + 8 ч — день прошёл
        self.db.save_profile(U, {"morning": "выкл"})
        self.assertIsNone(self.run_morning(self.at(7, 30))[0])
        self.db.save_profile(U, {"morning": "8:30"})
        self.assertIsNone(self.run_morning(self.at(8, 0))[0])
        self.assertEqual(self.run_morning(self.at(8, 30))[0], "brief")

    def test_morning_busy_and_retry(self):
        coach.busy.add(U)
        try:
            self.assertIsNone(self.run_morning(self.at(7, 0))[0])
        finally:
            coach.busy.discard(U)
        self.assertFalse(self.db.was_sent(f"morning:{U}:2026-10-11"))
        with mock.patch.object(coach, "send", side_effect=RuntimeError("telegram down")), \
                mock.patch.object(coach.threading, "Thread", SyncThread), mock.patch.object(ai, "claude", return_value="ок"):
            self.assertEqual(coach.maybe_morning(self.db.user(U), self.at(7, 0)), "brief")
        self.assertFalse(self.db.was_sent(f"morning:{U}:2026-10-11"))  # не ушло — повторим
        self.assertEqual(self.run_morning(self.at(7, 1))[0], "brief")
        self.assertEqual(len(self.mornings()), 1)

    def test_morning_no_whoop(self):
        self.db.drop_token(U)
        self.assertIsNone(self.run_morning(self.at(10, 0))[0])

    def test_tick_sends_morning(self):
        self.db.mark_synced(U)
        with mock.patch.object(coach, "user_now", return_value=self.at(7, 5)), \
                mock.patch.object(coach, "sync_user") as su, \
                mock.patch.object(coach.threading, "Thread", SyncThread), mock.patch.object(ai, "claude", return_value="ок"):
            coach.tick()
            coach.tick()
        su.assert_not_called()  # синхронизировали только что
        self.assertEqual(len(self.mornings()), 1)

    def test_morning_command(self):
        msg = lambda t: {"chat": {"id": U, "type": "private"}, "from": {"id": U}, "text": t}  # noqa: E731
        coach.on_message(msg("/morning"))
        self.assertIn("с <b>07:00</b>", self.texts()[-1])
        self.assertIn("10:00", self.texts()[-1])
        coach.on_message(msg("/morning 8.30"))
        self.assertEqual(self.db.user(U)["profile"]["morning"], "08:30")
        self.assertIn("11:30", self.texts()[-1])
        coach.on_message(msg("/morning off"))
        self.assertEqual(self.db.user(U)["profile"]["morning"], "off")
        self.assertIn("выключен", self.texts()[-1])
        coach.on_message(msg("/morning 25:00"))
        self.assertIn("⚠️", self.texts()[-1])
        coach.on_callback({"id": "1", "from": {"id": U}, "data": "mt|07:30",
                           "message": {"chat": {"id": U}, "message_id": 3}})
        self.assertEqual(self.db.user(U)["profile"]["morning"], "07:30")

    # ---------- цели ----------

    def test_goals_chat(self):
        msg = lambda t: {"chat": {"id": U, "type": "private"}, "from": {"id": U}, "text": t}  # noqa: E731
        coach.on_message(msg("/goals"))
        self.assertIn("пока не записаны", self.texts()[-1])
        coach.on_message(msg("/goal ложиться до 23:00"))
        self.assertIn("1. ложиться до 23:00", self.texts()[-1])
        # кнопка «Добавить» -> следующий текст становится целью, а не вопросом тренеру
        coach.on_callback({"id": "1", "from": {"id": U}, "data": "g|add", "message": {"chat": {"id": U}, "message_id": 3}})
        with mock.patch.object(coach.threading, "Thread") as th:
            coach.on_message(msg("бокс — соревнования весной"))
            coach.on_message(msg("а это уже вопрос?"))
        self.assertEqual([c.kwargs["target"].__name__ for c in th.call_args_list], ["answer"])
        self.assertEqual([g["text"] for g in self.db.goals(U)], ["ложиться до 23:00", "бокс — соревнования весной"])
        gid = self.db.goals(U)[0]["id"]
        coach.on_callback({"id": "2", "from": {"id": U}, "data": f"g|del|{gid}", "message": {"chat": {"id": U}, "message_id": 3}})
        self.assertEqual([g["text"] for g in self.db.goals(U)], ["бокс — соревнования весной"])
        self.assertIn("1. бокс", [kw for m, kw in self.sent if m == "editMessageText"][-1]["text"])

    def test_goals_in_every_prompt(self):
        self.db.add_goal(U, "вес 60 кг к декабрю")
        with mock.patch.object(ai, "claude", return_value="ок") as c:
            coach.morning_brief(U)
            call("/api/ask", {"text": "Можно спарринг?"})
            coach.workout_note(U, insights.workout_of({"id": "w1", "sport_name": "boxing"}))
        with mock.patch.object(ai, "claude", return_value='{"food": true, "title": "Банан", "kcal": 100}') as c2:
            coach.log_meal(U, U, "съел банан")
        prompts = [a[0][0] for a in c.call_args_list] + [c2.call_args[0][0]]
        self.assertEqual(len(prompts), 4)
        for p in prompts:
            self.assertIn("ЦЕЛИ", p)
            self.assertIn("вес 60 кг к декабрю", p)
        self.assertIn("ЦЕЛИ — одна строка", prompts[0])  # утром — про прогресс к цели

    def test_goals_api(self):
        r = call("/api/all")
        self.assertEqual((r.json["goals"], r.json["morning"]), ([], "07:00"))
        r = call("/api/goal", {"text": "ложиться до 23:00"})
        self.assertEqual([g["text"] for g in r.json["goals"]], ["ложиться до 23:00"])
        self.assertEqual(call("/api/goal", {"text": "x"}).code, 400)
        gid = r.json["goals"][0]["id"]
        self.assertEqual(call("/api/goal/delete", {"id": gid}).json["goals"], [])
        self.assertEqual(call("/api/profile", {"morning": "выкл"}).json["profile"]["morning"], "off")
        self.assertEqual(call("/api/all").json["morning"], "off")

    # ---------- голосовые и фото ----------

    def test_voice(self):
        msg = {"chat": {"id": U, "type": "private"}, "from": {"id": U}, "voice": {"file_id": "v1", "duration": 3}}
        with mock.patch.object(coach.threading, "Thread") as th:
            coach.on_message(msg)
        self.assertEqual(th.call_args.kwargs["target"].__name__, "on_voice")  # не в главном цикле
        with mock.patch.object(coach.media, "voice_text", return_value="Съел гречку с курицей.") as vt, \
                mock.patch.object(coach.threading, "Thread") as th:
            coach.on_voice(U, U, msg)
            vt.return_value = "почему <низкое> восстановление"
            coach.on_voice(U, U, msg)
        self.assertEqual(vt.call_args[0], (coach.TOKEN, msg))
        self.assertEqual([c.kwargs["target"].__name__ for c in th.call_args_list], ["log_meal", "answer"])
        self.assertEqual(th.call_args_list[1].kwargs["args"][2], "почему <низкое> восстановление")
        self.assertIn("🎙 «Съел гречку с курицей.»", self.texts())
        self.assertIn("🎙 «почему &lt;низкое&gt; восстановление»", self.texts())
        self.assertIn(("sendChatAction", {"chat_id": U, "action": "typing"}), self.sent)

    def test_voice_goal_and_errors(self):
        msg = {"chat": {"id": U, "type": "private"}, "from": {"id": U}, "video_note": {"file_id": "n1"}}
        coach.on_callback({"id": "1", "from": {"id": U}, "data": "g|add", "message": {"chat": {"id": U}, "message_id": 3}})
        with mock.patch.object(coach.media, "voice_text", side_effect=coach.media.MediaError("в голосовом не слышно слов")), \
                mock.patch.object(coach.threading, "Thread", SyncThread):
            coach.on_message(msg)
        self.assertIn("не слышно слов", self.texts()[-1])
        self.assertEqual(self.db.goals(U), [])
        with mock.patch.object(coach.media, "voice_text", return_value="Ложиться до 23:00"), \
                mock.patch.object(coach.threading, "Thread", SyncThread):
            coach.on_message(msg)  # цель всё ещё ждём — голосовое стало целью
        self.assertEqual([g["text"] for g in self.db.goals(U)], ["Ложиться до 23:00"])

    def test_photo_routing(self):
        base = {"chat": {"id": U, "type": "private"}, "from": {"id": U}, "photo": [{"file_id": "p1"}]}
        with mock.patch.object(coach, "download", return_value="/tmp/p.jpg"), \
                mock.patch.object(coach.threading, "Thread") as th:
            coach.on_message(base)
            coach.on_message({**base, "caption": "обед"})
            coach.on_message({**base, "caption": "Разбери мой план на неделю"})
            coach.on_message({**base, "caption": "это нормально?"})
        self.assertEqual([c.kwargs["target"].__name__ for c in th.call_args_list], ["log_meal", "log_meal", "answer", "answer"])

    def test_photo_not_food_goes_to_coach(self):
        with mock.patch.object(ai, "claude", side_effect=['{"food": false, "comment": "это скрин WHOOP"}',
                                                          "**Восстановление 82%** — можно бокс в полную."]) as c:
            coach.log_meal(U, U, "", "/tmp/whoop.jpg")
        self.assertEqual(c.call_args_list[1].kwargs["image"], "/tmp/whoop.jpg")
        self.assertIn(coach.PHOTO_ASK, c.call_args_list[1][0][0])
        self.assertIn("<b>Восстановление 82%</b>", self.texts()[-1])
        self.assertEqual(self.db.meals(U, "2026-10-11"), [])
        self.assertNotIn(U, coach.busy)

    def test_static(self):
        r = call("/", method="GET", init="")
        self.assertEqual(r.code, 200)
        self.assertIn(b"app.js?v=", r.body)
        self.assertEqual(call("/health", method="GET", init="").body, b"ok")


if __name__ == "__main__":
    unittest.main(verbosity=1)
