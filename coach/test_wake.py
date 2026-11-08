"""«Будильник» (сайт docs/wake, договор — coach/SYNC.md п. 9): GET /api/wake, страховка /api/wake/arm → «⏰ Подъём»
в Telegram ровно один раз в at, /api/wake/done (снять, итог ночи), статика /wake/.
usage: python3 coach/test_wake.py
"""
import datetime
import hashlib
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_coach as T  # noqa: E402  (ставит окружение и поддельный WHOOP)
import coach  # noqa: E402

MSK = ZoneInfo("Europe/Moscow")
SUN_EVE = datetime.datetime(2026, 10, 11, 21, 40, tzinfo=MSK)  # T.TODAY — воскресенье, завтра учёба
GH = "https://sawqws.github.io"


def night(light, deep, rem, cycles, **kw):
    return {"hours": round(light + deep + rem, 2), "in_bed": round(light + deep + rem + .5, 2), "light": light, "deep": deep,
            "rem": rem, "cycles": cycles, "need": 9.0, "efficiency": 92, "performance": 84, "start": "23:10", "end": "07:05",
            "nap": False, **kw}


def days_with(sleeps, rec=None):
    """{день: {sleep, recovery, naps}} как insights.by_day — для расчёта цикла без настоящих записей WHOOP."""
    out = {d: {"recovery": None, "sleep": s, "naps": [], "cycle": None, "workouts": []} for d, s in sleeps.items()}
    if rec:
        out.setdefault(T.TODAY.isoformat(), {"recovery": None, "sleep": None, "naps": [], "cycle": None, "workouts": []})
        out[T.TODAY.isoformat()]["recovery"] = rec
    return out


def ago(n):
    return (T.TODAY - datetime.timedelta(days=n)).isoformat()


class WakeTest(unittest.TestCase):
    def setUp(self):
        self.db, _ = T.seeded(os.path.join(tempfile.mkdtemp(), "wake.db"))
        self.db.meta("owner", T.U)
        coach.pwa._fails.clear()
        coach.pwa._hits.clear()
        self.now = SUN_EVE
        self.sent = []
        self.p = [mock.patch.object(coach, "db", self.db), mock.patch.object(coach, "DATA", tempfile.mkdtemp()),
                  mock.patch.object(coach, "user_now", lambda u: self.now),
                  mock.patch.object(coach, "today_of", lambda uid: self.now.date()),
                  mock.patch.object(coach, "bg", lambda f, *a, **kw: f(*a)),
                  mock.patch.object(coach, "send", lambda chat, text, buttons=None, **kw: self.sent.append((chat, text)))]
        for p in self.p:
            p.start()
        self.tok = coach.pwa.issue(self.db, T.U, kind="kcal")

    def tearDown(self):
        for p in self.p:
            p.stop()
        self.db.db.close()

    def get(self, **kw):
        return T.kcal_call("/api/wake", token=kw.pop("token", self.tok), method="GET", **kw)

    def post(self, path, body, **kw):
        return T.kcal_call(path, body, token=kw.pop("token", self.tok), **kw)

    def arm_rec(self):
        return json.loads(self.db.meta(f"wake:arm:{T.U}") or "null")

    # ---------- GET /api/wake ----------

    def test_get_fields(self):
        r = self.get()
        self.assertEqual(r.code, 200, r.json)
        w = r.json
        self.assertEqual(set(w), {"now", "tz", "plan", "need", "eff", "latency", "cycle", "nights", "last", "recovery", "armed"})
        self.assertEqual((w["now"], w["tz"]), ("2026-10-11T21:40", 180))
        # вечер воскресенья: сегодняшний подъём (08:00, выходной) прошёл — подъём завтра, в понедельник (учёба)
        self.assertEqual(w["plan"], {"day": "2026-10-12", "wake": "07:00", "bed": "22:00", "mode": "school"})
        self.assertEqual(w["need"], 9.1)  # WHOOP за прошлую ночь (8,5 + 0,4 + 0,2), не меньше 8,5
        self.assertEqual(w["eff"], 0.94)  # медиана эффективности WHOOP (94%) за 14 ночей
        self.assertEqual(w["latency"], coach.f_plan.FALL_ASLEEP)
        self.assertTrue(70 <= w["cycle"] <= 120)
        self.assertEqual(w["nights"], 14)
        self.assertEqual(w["last"]["day"], T.TODAY.isoformat())
        self.assertEqual(set(w["last"]), {"day", "hours", "in_bed", "light", "deep", "rem", "cycles", "performance", "start", "end"})
        self.assertEqual(w["last"]["cycles"], 4)
        self.assertEqual(set(w["recovery"]), {"score", "hrv", "rhr"})
        self.assertEqual(w["recovery"]["score"], 82)
        self.assertIsNone(w["armed"])
        self.assertEqual(r.headers["Access-Control-Allow-Origin"], GH)

    def test_plan_today_or_tomorrow(self):
        self.now = datetime.datetime(2026, 10, 11, 2, 0, tzinfo=MSK)  # ночь на воскресенье: подъём сегодня, в 08:00
        self.assertEqual(self.get().json["plan"], {"day": "2026-10-11", "wake": "08:00", "bed": "23:00", "mode": "weekend"})
        self.now = datetime.datetime(2026, 10, 11, 7, 59, tzinfo=MSK)
        self.assertEqual(self.get().json["plan"]["day"], "2026-10-11")
        self.now = datetime.datetime(2026, 10, 11, 8, 0, tzinfo=MSK)  # подъём наступил — уже следующий
        self.assertEqual(self.get().json["plan"]["day"], "2026-10-12")
        # свой режим: подъём в учёбу 06:45, отбой 21:30 — их и отдаём
        coach.f_regime.save(self.db, T.U, {"wake": "06:45", "bed": "21:30"})
        self.now = SUN_EVE
        self.assertEqual(self.get().json["plan"], {"day": "2026-10-12", "wake": "06:45", "bed": "21:30", "mode": "school"})

    def test_cycle(self):
        def get(days):
            with mock.patch.object(coach.insights, "by_day", lambda *a: days):
                return self.get().json
        # медиана (light+deep+rem)·60 / cycles: 84, 90, 120 → 90; ночь с 1 циклом и старше 14 дней не в счёт
        w = get(days_with({ago(0): night(3.5, 1.5, 2.0, 5), ago(1): night(4, 1.5, 2, 5), ago(2): night(4, 2, 2, 4),
                           ago(3): night(6, 1, 1, 1), ago(14): night(1, 1, 1, 2)}))
        self.assertEqual((w["cycle"], w["nights"]), (90, 3))
        self.assertEqual(w["last"]["day"], ago(0))
        self.assertIsNone(w["recovery"])  # восстановления за сегодня нет
        # обрезка 70–120
        self.assertEqual(get(days_with({ago(i): night(6, 2, 2, 4) for i in range(4)}))["cycle"], 120)
        self.assertEqual(get(days_with({ago(i): night(2, 1, 1, 4) for i in range(4)}))["cycle"], 70)
        # меньше 3 ночей — null (сайт возьмёт 90)
        w = get(days_with({ago(0): night(4, 1.5, 2, 5), ago(5): night(4, 1.5, 2, None), ago(6): night(4, 1.5, 2, 5)}))
        self.assertEqual((w["cycle"], w["nights"]), (None, 2))
        # данных нет совсем: цикл и ночь — null, потребность — подростку/взрослому, эффективность — 0,9
        w = get({})
        self.assertEqual((w["cycle"], w["nights"], w["last"], w["recovery"], w["eff"]), (None, 0, None, None, 0.9))
        with mock.patch.object(coach, "profile", lambda uid: {"age": 14}):
            self.assertEqual(get({})["need"], 8.5)
        with mock.patch.object(coach, "profile", lambda uid: {"age": 30}):
            self.assertEqual(get({})["need"], 7.5)

    def test_auth_cors_rate(self):
        self.assertEqual(self.get(token="").code, 401)
        self.assertEqual(self.get(token="x" * 40).code, 401)
        site_tok = coach.pwa.issue(self.db, T.U, kind="site")  # cookie-сессия Формы — не Bearer «Калорий»
        self.assertEqual(self.get(token=site_tok).code, 401)
        self.assertEqual(self.post("/api/wake/arm", {"at": "2026-10-12T07:00"}, token="").code, 401)
        self.assertEqual(self.post("/api/wake/done", {"cancel": True}, token="").code, 401)
        self.assertEqual(self.get(origin="https://evil.example").code, 403)
        self.assertEqual(self.post("/api/wake/arm", {"at": "2026-10-12T07:00"}, origin="https://evil.example").code, 403)
        self.assertIsNone(self.arm_rec())
        self.db.meta("owner", 999)  # не владелец — «личный тренер»
        self.assertEqual(self.get().code, 403)
        self.db.meta("owner", T.U)
        # preflight с GitHub Pages — можно, с чужого сайта — нет
        for path in coach.WAKE_PATHS:
            r = T.kcal_call(path, method="OPTIONS")
            self.assertEqual((r.code, r.headers["Access-Control-Allow-Origin"]), (204, GH), path)
            self.assertIn("Authorization", r.headers["Access-Control-Allow-Headers"])
            self.assertEqual(T.kcal_call(path, method="OPTIONS", origin="https://evil.example").code, 403)
        # тот же домен (/wake/ внутри Формы) — без CORS-заголовков
        own = {"Host": "coach.1-2-3-4.sslip.io"}
        r = self.get(origin="https://coach.1-2-3-4.sslip.io", headers=own)
        self.assertEqual(r.code, 200)
        self.assertNotIn("Access-Control-Allow-Origin", r.headers)
        # 120 в час на токен — свой счётчик у каждого пути и у «Калорий»
        coach.pwa._hits.clear()
        n = coach.SYNC_RATE[0]
        self.assertEqual([self.get().code for _ in range(n + 1)], [200] * n + [429])
        self.assertEqual(self.post("/api/wake/done", {"cancel": True}).code, 200)
        self.assertEqual(T.kcal_call("/api/kcal/forma", token=self.tok, method="GET").code, 200)
        key = hashlib.sha256(self.tok.encode()).hexdigest()
        self.assertEqual(len(coach.pwa._hits["wake:" + key]), n)

    # ---------- POST /api/wake/arm ----------

    def test_arm(self):
        r = self.post("/api/wake/arm", {"at": "2026-10-12T07:00", "from": "06:30"})
        self.assertEqual(r.code, 200, r.json)
        self.assertEqual(r.json, {"ok": True, "armed": {"at": "2026-10-12T07:00", "from": "06:30"}})
        self.assertEqual(self.arm_rec()["ts"], datetime.datetime(2026, 10, 12, 7, 0, tzinfo=MSK).timestamp())
        self.assertEqual(self.get().json["armed"], {"at": "2026-10-12T07:00", "from": "06:30"})
        # повторный arm заменяет прошлый; без from — окно с самого at
        self.assertEqual(self.post("/api/wake/arm", {"at": "2026-10-12T06:50"}).json["armed"], {"at": "2026-10-12T06:50", "from": "06:50"})
        self.assertEqual(self.arm_rec()["at"], "2026-10-12T06:50")
        # прошлое, «сейчас», дальше 20 ч, кривой формат — 400, прежняя страховка остаётся
        for bad in ({"at": "2026-10-11T21:39"}, {"at": "2026-10-11T21:40"}, {"at": "2026-10-12T17:41"}, {"at": "2026-10-13T07:00"},
                    {"at": "2026-10-12 07:00"}, {"at": "2026-10-12T7:00"}, {"at": "2026-13-12T07:00"}, {"at": ""}, {},
                    {"at": 1760241600}, {"at": "2026-10-12T07:00", "from": "25:00"}, {"at": "2026-10-12T07:00", "from": "утро"}):
            r = self.post("/api/wake/arm", bad)
            self.assertEqual(r.code, 400, bad)
            self.assertTrue(r.json["error"])
        self.assertEqual(self.arm_rec()["at"], "2026-10-12T06:50")
        self.assertEqual(self.post("/api/wake/arm", {"at": "2026-10-12T17:40"}).code, 200)  # ровно +20 ч — можно

    # ---------- страховка: «⏰ Подъём» в Telegram ----------

    def test_insurance_once_at_time(self):
        self.post("/api/wake/arm", {"at": "2026-10-12T07:00", "from": "06:30"})
        ts = self.arm_rec()["ts"]
        for t in (ts - 3600, ts - 61, ts - 1):  # раньше at — не будим
            coach.wake_tick(T.U, t)
        self.assertEqual(self.sent, [])
        coach.wake_tick(T.U, ts + 20)
        self.assertEqual(len(self.sent), 1)
        chat, text = self.sent[0]
        self.assertEqual(chat, T.U)
        self.assertTrue(text.startswith("⏰ <b>Подъём</b>\n<blockquote>"), text)
        self.assertIn("Будильник на сайте не сработал — пора вставать", text)
        self.assertIn("<b>07:00</b> · окно с 06:30", text)
        self.assertLessEqual(text.split("<blockquote>")[1].count("\n") + 1, 3)
        self.assertIsNone(self.arm_rec())  # снята
        self.assertIsNone(self.get().json["armed"])
        for t in (ts + 80, ts + 140, ts + 3600):
            coach.wake_tick(T.U, t)
        self.assertEqual(len(self.sent), 1)  # ровно один раз

    def test_insurance_via_tick(self):
        """Страховку будит общий tick (раз в минуту, как напоминание режима)."""
        at = time.time() - 30
        self.db.meta(f"wake:arm:{T.U}", json.dumps({"at": "2026-10-12T07:00", "from": "07:00", "ts": at}))
        with mock.patch.object(coach, "allowed", lambda uid: True), \
                mock.patch.object(coach, "maybe_morning", lambda *a: None), \
                mock.patch.object(coach, "sync_user", lambda *a, **kw: None), \
                mock.patch.object(coach.selfcheck, "tick", lambda *a: None), \
                mock.patch.object(coach, "backup_db", lambda *a: None), \
                mock.patch.object(coach.ai, "claude", side_effect=coach.ai.AIError("тест")):
            coach.tick()
            coach.tick()
        wake = [t for _, t in self.sent if t.startswith("⏰ <b>Подъём</b>")]
        self.assertEqual(len(wake), 1)
        self.assertNotIn("окно с", wake[0])  # окно не задано — только время

    def test_done_cancels(self):
        for done in ({"cancel": True}, {"woke": "06:41", "reason": "move"}, {}):
            self.post("/api/wake/arm", {"at": "2026-10-12T07:00"})
            ts = self.arm_rec()["ts"]
            r = self.post("/api/wake/done", done)
            self.assertEqual((r.code, r.json), (200, {"ok": True}))
            self.assertIsNone(self.arm_rec())
            coach.wake_tick(T.U, ts + 10)
            self.assertEqual(self.sent, [], done)
        nights = json.loads(self.db.meta(f"wake:nights:{T.U}"))
        self.assertEqual(len(nights), 1)  # cancel и пустой итог — не ночь

    def test_overdue_not_sent(self):
        """Сервер лежал: страховка просрочена больше чем на 2 ч — не будим, снимаем."""
        self.post("/api/wake/arm", {"at": "2026-10-12T07:00"})
        ts = self.arm_rec()["ts"]
        coach.wake_tick(T.U, ts + 2 * 3600 + 60)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.db.meta(f"wake:arm:{T.U}"), "")
        coach.wake_tick(T.U, ts + 2 * 3600 + 120)
        self.assertEqual(self.sent, [])
        # опоздание меньше 2 ч — будим
        self.post("/api/wake/arm", {"at": "2026-10-12T07:00"})
        coach.wake_tick(T.U, ts + 2 * 3600 - 60)
        self.assertEqual(len(self.sent), 1)

    def test_insurance_retry_and_rearm(self):
        """Telegram не принял — следующий tick повторит; новая страховка, поставленная во время отправки, не снимается."""
        self.post("/api/wake/arm", {"at": "2026-10-12T07:00"})
        ts = self.arm_rec()["ts"]
        with mock.patch.object(coach, "send", side_effect=OSError("нет сети")):
            with self.assertRaises(OSError):
                coach.wake_tick(T.U, ts + 5)
        self.assertIsNotNone(self.arm_rec())
        coach.wake_tick(T.U, ts + 65)
        self.assertEqual(len(self.sent), 1)

        def send_and_rearm(chat, text, buttons=None, **kw):
            self.sent.append((chat, text))
            self.now = datetime.datetime(2026, 10, 12, 7, 1, tzinfo=MSK)
            self.post("/api/wake/arm", {"at": "2026-10-12T07:30"})
        self.now = SUN_EVE
        self.post("/api/wake/arm", {"at": "2026-10-12T07:05"})
        ts = self.arm_rec()["ts"]
        with mock.patch.object(coach, "send", send_and_rearm):
            coach.wake_tick(T.U, ts + 5)
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.arm_rec()["at"], "2026-10-12T07:30")

    # ---------- POST /api/wake/done: итог ночи ----------

    def test_done_night_clean(self):
        body = {"start": "22:05", "onset": "22:21", "woke": "6:32", "reason": "cycle", "window": 30, "minutes": 451,
                "light": 38, "deep": 31.556, "awake": 6, "moves": 57, "extra": "x" * 9999, "secret": {"a": 1}}
        self.assertEqual(self.post("/api/wake/done", body).json, {"ok": True})
        n = json.loads(self.db.meta(f"wake:nights:{T.U}"))[-1]
        self.assertEqual(n, {"day": "2026-10-11", "saved": "2026-10-11T21:40", "start": "22:05", "onset": "22:21", "woke": "06:32",
                             "reason": "cycle", "window": 30, "minutes": 451, "light": 38, "deep": 31.56, "awake": 6, "moves": 57})
        bad = {"start": "25:10", "onset": "<b>", "woke": 632, "reason": "alarm", "window": -5, "minutes": 99999,
               "light": "38", "deep": True, "awake": float("nan"), "moves": None}
        self.post("/api/wake/done", {**bad, "woke": "07:00"})
        n = json.loads(self.db.meta(f"wake:nights:{T.U}"))[-1]
        self.assertEqual(n, {"day": "2026-10-11", "saved": "2026-10-11T21:40", "start": None, "onset": None, "woke": "07:00",
                             "reason": None, "window": 0, "minutes": 1440, "light": None, "deep": None, "awake": None, "moves": None})
        for i in range(40):  # хранится 30 последних
            self.post("/api/wake/done", {"minutes": i})
        nights = json.loads(self.db.meta(f"wake:nights:{T.U}"))
        self.assertEqual(len(nights), coach.WAKE_KEEP)
        self.assertEqual([x["minutes"] for x in nights[-3:]], [37, 38, 39])

    # ---------- статика /wake/ ----------

    def test_static(self):
        root = tempfile.mkdtemp()
        wd = os.path.join(root, "wake")
        os.makedirs(os.path.join(wd, "sub"))
        files = {"index.html": "<!doctype html><title>Будильник</title>" + "x" * 2000, "sw.js": "self.x=1;", "app.css": "a{}",
                 "manifest.webmanifest": "{}", "icon-192.png": "PNG", ".secret.js": "s", "sub/x.js": "x", "notes.txt": "t"}
        for n, v in files.items():
            with open(os.path.join(wd, n), "w") as f:
                f.write(v)
        sound = bytes(range(256)) * 4
        with open(os.path.join(wd, "alarm.mp3"), "wb") as f:
            f.write(sound)
        with open(os.path.join(root, "outside.js"), "w") as f:
            f.write("secret")
        os.symlink(os.path.join(root, "outside.js"), os.path.join(wd, "link.js"))
        get = lambda p, **h: T.kcal_call(p, method="GET", origin="", headers=h)  # noqa: E731
        with mock.patch.object(coach, "WAKE_DIR", wd):
            r = get("/wake")
            self.assertEqual((r.code, r.headers["Location"]), (302, "/wake/"))
            self.assertEqual(get("/wake?theme=light").headers["Location"], "/wake/?theme=light")
            r = get("/wake/")
            self.assertEqual((r.code, r.body.decode(), r.headers["Content-Type"], r.headers["Cache-Control"]),
                             (200, files["index.html"], "text/html; charset=utf-8", "no-cache"))
            self.assertEqual(get("/wake/#pair=abc").code, 200)
            r = get("/wake/sw.js")
            self.assertEqual((r.headers["Content-Type"], r.headers["Service-Worker-Allowed"]), ("text/javascript; charset=utf-8", "/wake/"))
            self.assertNotIn("Service-Worker-Allowed", get("/wake/").headers)
            self.assertEqual(get("/wake/app.css").headers["Content-Type"], "text/css; charset=utf-8")
            # звук: целиком и кусками (Safari на iPhone без 206 не играет <audio>)
            r = get("/wake/alarm.mp3")
            self.assertEqual((r.code, r.body, r.headers["Content-Type"], r.headers["Accept-Ranges"]), (200, sound, "audio/mpeg", "bytes"))
            r = get("/wake/alarm.mp3", Range="bytes=0-1")
            self.assertEqual((r.code, r.body, r.headers["Content-Range"], r.headers["Content-Length"]), (206, sound[:2], "bytes 0-1/1024", "2"))
            r = get("/wake/alarm.mp3", Range="bytes=1000-")
            self.assertEqual((r.code, r.body, r.headers["Content-Range"]), (206, sound[1000:], "bytes 1000-1023/1024"))
            r = get("/wake/alarm.mp3", Range="bytes=-24")
            self.assertEqual((r.code, r.body), (206, sound[-24:]))
            self.assertEqual(get("/wake/alarm.mp3", Range="bytes=2000-").code, 416)
            self.assertEqual(get("/wake/alarm.mp3", Range="items=0-1").code, 200)
            for bad in ("/wake/../coach.py", "/wake/%2e%2e/coach.py", "/wake/..%2fcoach.py", "/wake/.secret.js", "/wake/sub/x.js",
                        "/wake/sub/", "/wake/notes.txt", "/wake/link.js", "/wake/nope.js", "/wake/app.js", "/wake//etc/passwd",
                        "/wake/index.html/", "/wakeup", "/wake.html"):
                self.assertEqual(get(bad).code, 404, bad)
            # «Калории» по-прежнему из своей папки — не из папки будильника
            self.assertNotEqual(get("/kcal/sw.js").body, b"self.x=1;")
        # папки нет — 404, а не ошибка
        with mock.patch.object(coach, "WAKE_DIR", os.path.join(root, "nope")):
            self.assertEqual(get("/wake/").code, 404)
            self.assertEqual(get("/wake/index.html").code, 404)
        self.assertEqual(os.path.realpath(coach.WAKE_DIR), os.path.realpath(os.path.join(coach.REPO, "docs", "wake")))

    def test_link_and_entry(self):
        """Вход из Формы: /api/kcal/link даёт токен «Калорий» — им же открывается /api/wake; на главной — строка «Умный будильник»."""
        tok = T.call("/api/kcal/link", {}).json["token"]
        self.assertEqual(self.get(token=tok).code, 200)
        with open(os.path.join(coach.WEB, "app.js"), encoding="utf-8") as f:
            js = f.read()
        self.assertIn('data-a="wake-open"', js)
        self.assertIn("'/api/kcal/link'", js)
        self.assertIn("'/wake/' + (saved ? '' : '#pair=' + encodeURIComponent(tok))", js)


if __name__ == "__main__":
    unittest.main(verbosity=1)
