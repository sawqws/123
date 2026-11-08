"""Самопроверка «Формы» (coach/selfcheck.py): расписание, блокировка, разбор итогов, решение «чинить/молчать»,
передача в поддержку, раз в сутки, доступ к /api/selfcheck и /selfcheck только владельцу, фикстура не трогает живую базу.
usage: python3 coach/test_selfcheck.py
"""
import datetime
import fcntl
import json
import os
import re
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_coach as tc  # noqa: E402  окружение (временная база), подпись initData, сам тренер

coach, store = tc.coach, tc.store
TMP = tempfile.mkdtemp()
os.environ["SELFCHECK_DIR"] = os.path.join(TMP, "reports")
os.environ["SELFCHECK_LOCK"] = os.path.join(TMP, "selfcheck.lock")
import selfcheck as sc  # noqa: E402

sc.ROOT, sc.LOCK = os.environ["SELFCHECK_DIR"], os.environ["SELFCHECK_LOCK"]
U, OTHER = 7, 8


def rep(n=2, folder=None):
    folder = folder or tempfile.mkdtemp(dir=TMP)
    probs = [{"kind": "js-error", "sev": "error", "where": "?p=weight", "what": f"TypeError {i}", "steps": ["открыть ?p=weight", "нажать «Замер»"],
              "shot": f"{folder}/shots/{i}.png", "configs": ["phone-dark"], "also": []} for i in range(n)]
    probs.append({"kind": "crawler", "sev": "note", "where": "-", "what": "заметка", "steps": [], "configs": [], "also": []})
    r = {"started": "2026-11-03T03:30:00", "dir": folder, "problems": probs, "notes": [], "seconds": 540,
         "stats": {"screens": 76, "buttons": 840, "sheets": 300, "tests": {"test_coach.py": 0}}}
    sc.save(r, folder)
    return r


def write(path, text, mode="w"):
    with open(path, mode, encoding="utf-8") as f:
        f.write(text)


class FakeSup:
    def __init__(self, busy=None):
        self.calls, self.state = [], busy

    def web(self, uid, body):
        self.calls.append((uid, body))
        return {"ok": True}

    def busy(self):
        return self.state


class App(types.SimpleNamespace):
    """Тренер для самопроверки: база, владелец, время, отправка — без Telegram."""


def app(hour=10, minute=0, day=3, busy=None, owner=U):
    db = store.Store(os.path.join(tempfile.mkdtemp(dir=TMP), "c.db"))
    if owner:
        db.meta("owner", owner)
    db.user(U, "Глеб")
    a = App(db=db, SUP=FakeSup(busy), ALLOWED=set(), sent=[])
    a.now = datetime.datetime(2026, 11, day, hour, minute, tzinfo=coach.ZoneInfo("Europe/Moscow"))
    a.owner = lambda: int(db.meta("owner") or 0)
    a.is_admin = lambda uid: uid == a.owner()
    a.user_now = lambda u: a.now
    a.send = lambda chat, text, *x, **k: a.sent.append((chat, text))
    a.box, a.err = coach.box, coach.err
    return a


class ScheduleTest(unittest.TestCase):
    def test_night_once_a_day(self):
        a = app(hour=3, minute=29)
        with mock.patch.object(sc, "start", return_value=True) as st:
            self.assertNotEqual(sc.tick(a), "start")  # 03:29 — рано
            a.now = a.now.replace(minute=30)
            self.assertEqual(sc.tick(a), "start")
            a.now = a.now.replace(minute=31)
            self.assertNotEqual(sc.tick(a), "start")  # тот же день — уже было
            a.now = a.now.replace(hour=4, minute=50)
            self.assertNotEqual(sc.tick(a), "start")
            self.assertEqual(st.call_count, 1)
            a.now = a.now.replace(day=4, hour=3, minute=30)  # следующая ночь
            self.assertEqual(sc.tick(a), "start")
            self.assertEqual(st.call_count, 2)

    def test_daytime_start_does_not_fire_late(self):
        a = app(hour=14)  # бот запустили днём — ночную не догоняем, ждём следующей ночи
        with mock.patch.object(sc, "start", return_value=True) as st:
            sc.tick(a)
            st.assert_not_called()

    def test_no_owner_nothing(self):
        a = app(hour=3, minute=30, owner=0)
        with mock.patch.object(sc, "start") as st:
            self.assertIsNone(sc.tick(a))
            st.assert_not_called()

    def test_no_support_no_run(self):
        a = app(hour=3, minute=30)
        a.SUP = None  # тесты и импорт без main(): ночная проверка не стартует
        with mock.patch.object(sc, "start") as st:
            self.assertIsNone(sc.tick(a))
            st.assert_not_called()

    def test_coach_tick_calls_selfcheck(self):
        with mock.patch.object(coach.db, "users", return_value=[]), mock.patch.object(sc, "tick") as t:
            coach.tick()
            t.assert_called_once_with(coach)


class LockTest(unittest.TestCase):
    def test_busy_when_locked(self):
        with open(sc.LOCK, "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(sc.Busy):
                sc.run(os.path.join(TMP, "x"), server=False)
            fcntl.flock(f, fcntl.LOCK_UN)

    def test_busy_when_running_here(self):
        sc.RUNNING.set()
        try:
            with self.assertRaises(sc.Busy):
                sc.run(os.path.join(TMP, "y"), server=False)
            self.assertFalse(sc.start(app(), why="test"))  # второй фоновый прогон не стартует
        finally:
            sc.RUNNING.clear()

    def test_run_releases_lock_and_saves_report(self):
        """Прогон с подменёнными частями: отчёт сохранён, lock отпущен, RUNNING снят."""
        out = os.path.join(TMP, "run1")

        class Fx:
            url = "http://127.0.0.1:1/"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        def fake_sh(cmd, timeout, **k):
            if "coach/selfcheck.js" in cmd:
                os.makedirs(out, exist_ok=True)
                write(os.path.join(out, "crawl.json"), json.dumps({
                    "problems": [{"kind": "back", "sev": "error", "where": "?p=sleep", "what": "кнопка «Назад» не видна",
                                  "steps": [], "configs": ["phone-dark"], "also": []}],
                    "notes": [], "stats": {"screens": 3, "buttons": 9}}))
                return 0, ""
            if "common/devcheck.js" in cmd:
                return 1, "✓ iPhone SE 375×667\n✗ Монитор 1920×1080\n   - маленькая кнопка: <button> 30×30\n"
            return 0, "Плавно"
        with mock.patch.object(sc, "Fixture", Fx), mock.patch.object(sc, "sh", fake_sh), \
                mock.patch.object(sc, "run_tests", return_value=([], {"test_coach.py": 0})):
            r = sc.run(out, server=False)
        self.assertEqual(r["count"], 2)  # «Назад» + маленькая кнопка (одна строка на обе темы)
        self.assertTrue(os.path.isfile(os.path.join(out, "report.json")))
        self.assertFalse(sc.RUNNING.is_set())
        with open(sc.LOCK, "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)  # не занят
            fcntl.flock(f, fcntl.LOCK_UN)


class Found0411Test(unittest.TestCase):
    """Находки самопроверки 04.11: ложное «не плавно», вода в фикстуре, мелкие подписи и кнопки, налезание на ✕."""
    def test_perf_alone(self):
        """Замер плавности идёт один: тесты стартуют только после него (иначе процессор занят и замер врёт)."""
        order = []

        class Fx:
            url = "http://127.0.0.1:1/"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        def fake_sh(cmd, timeout, **k):
            order.append(os.path.basename(cmd[1]))
            return 0, "Плавно"

        def fake_tests(deadline):
            order.append("tests")
            return [], {}
        with mock.patch.object(sc, "Fixture", Fx), mock.patch.object(sc, "sh", fake_sh), \
                mock.patch.object(sc, "run_tests", side_effect=fake_tests):
            sc.run(os.path.join(TMP, "perf"), server=False)
        self.assertEqual(order[0], "perfcheck.js")
        self.assertIn("tests", order)

    def test_tests_run_without_fixture(self):
        class Bad:
            def __enter__(self):
                raise RuntimeError("фикстура не поднялась")

            def __exit__(self, *a):
                pass
        with mock.patch.object(sc, "Fixture", Bad), mock.patch.object(sc, "run_tests", return_value=([], {"test_coach.py": 0})) as rt:
            sc.run(os.path.join(TMP, "nofx"), server=False)
        rt.assert_called_once()

    def test_css_sizes(self):
        """Подписи ≥ 11 px, сегменты и ✕ в виде WHOOP ≥ 36 px, заголовок листа не ниже ✕."""
        css = {f: open(os.path.join(HERE, "webapp", f), encoding="utf-8").read() for f in ("plan.css", "assess.css", "whoop.css", "layout.css")}

        def rule(f, sel):
            m = re.search(r"^" + re.escape(sel) + r" \{([^}]*)\}", css[f], re.M)
            self.assertTrue(m, sel)
            return m.group(1)

        def px(body):
            return [float(x) for x in re.findall(r"(?<![\w.-])(\d+(?:\.\d+)?)px", body)]
        self.assertGreaterEqual(px(rule("plan.css", ".pl-d em"))[0], 11)
        self.assertGreaterEqual(px(rule("assess.css", ".as-cw"))[0], 11)
        self.assertGreaterEqual(min(px(rule("assess.css", ".as-cp"))), 11)
        seg = rule("whoop.css", "html.wh .seg button")
        self.assertGreaterEqual(px(seg)[0], 11)
        self.assertIn("min-height: 36px", seg)
        self.assertIn("width: 36px; height: 36px", rule("whoop.css", "html.wh .sheet-x"))
        self.assertIn("min-height: 42px", rule("layout.css", "html:root :is(.sheet-x + h2, .sheet-x + * > h2:first-child)"))

    def test_crawler_waits_for_transitions(self):
        js = open(os.path.join(HERE, "selfcheck.js"), encoding="utf-8").read()
        self.assertIn("document.documentElement.dataset.nav", js)  # переход экрана
        self.assertIn("document.getAnimations()", js)  # появление листа
        self.assertIn("scrollTop > 0", js)  # прилипший ✕ над прокрученным листом — не налезание


class FixtureWaterTest(unittest.TestCase):
    def test_water(self):
        """В фикстуре есть вода: приложение кладёт ответ в «сегодня» — без примера ломались «Чат» и главная."""
        import shots_fixture
        api = shots_fixture.build()["api"]
        for p in ("/api/water", "/api/water/delete"):
            self.assertEqual(api[p]["day"]["day"], api["/api/all"]["today"]["day"], p)


class ParseTest(unittest.TestCase):
    def test_devcheck(self):
        out = ("✓ Android маленький 360 360×740\n✗ iPhone SE 375×667\n   - прокрутка вбок: ширина 400 > 375\n"
               "   - ошибка: boom\n✗ iPad mini 768×1024\n   - прокрутка вбок: ширина 400 > 375\n\nНЕ ПРОШЛО: iPhone SE")
        p = sc.parse_devcheck(out, "dark")
        self.assertEqual(len(p), 2)
        self.assertEqual(p[0]["where"], "Главная · iPhone SE 375×667")
        self.assertEqual(p[0]["also"], ["iPad mini 768×1024"])
        self.assertEqual(p[1]["sev"], "error")
        self.assertEqual(sc.parse_devcheck("✓ всё\nВсе 11 размеров в порядке", "dark"), [])

    def test_logs_skip_network(self):
        log = ("sync: 7 <urlopen error timed out>\ngetUpdates: HTTP Error 502: Bad Gateway\n"
               "Traceback (most recent call last):\n  File \"/root/horo/coach/coach.py\", line 10, in route\n    x = y['a']\n"
               "KeyError: 'a'\nupdate: ok\n"
               "Traceback (most recent call last):\n  File \"/root/horo/coach/coach.py\", line 10, in route\n    x = y['a']\n"
               "KeyError: 'a'\n"
               "Traceback (most recent call last):\n  File \"/usr/lib/python3/urllib/request.py\", line 1, in open\n"
               "TimeoutError: The read operation timed out\n")
        e = sc.parse_logs(log)
        self.assertEqual(list(e), ["KeyError: 'a'"])
        self.assertEqual(e["KeyError: 'a'"]["n"], 2)
        self.assertIn("coach/coach.py", e["KeyError: 'a'"]["where"])

    def test_read_new_only_appended(self):
        f, offs = os.path.join(TMP, "err.log"), {}
        write(f, "старое\n")
        self.assertIn("старое", sc.read_new(f, offs))
        self.assertEqual(sc.read_new(f, offs), "")
        write(f, "новое\n", "a")
        self.assertEqual(sc.read_new(f, offs), "новое\n")
        write(f, "x\n")  # ротация: файл стал меньше — читаем с начала
        self.assertEqual(sc.read_new(f, offs), "x\n")

    def test_pm2_status_and_restarts(self):
        log = os.path.join(TMP, "pm2-err.log")
        write(log, "Traceback (most recent call last):\n  File \"/root/horo/coach/f_weight.py\", line 3\nValueError: bad\n")
        js = json.dumps([{"name": "coach-bot", "pm2_env": {"status": "errored", "restart_time": 12, "pm_err_log_path": log}}])
        state = {"restarts": 2}
        with mock.patch.object(sc, "sh", return_value=(0, js)):
            found, info = sc.pm2_check(state)
        what = [p["what"] for p in found]
        self.assertIn("coach-bot: статус errored", what)
        self.assertTrue(any("перезапускался 10 раз" in w for w in what))
        self.assertTrue(any(w.startswith("ValueError: bad") for w in what))
        self.assertEqual(state["restarts"], 12)
        with mock.patch.object(sc, "sh", return_value=(127, "")):
            self.assertEqual(sc.pm2_check({})[0], [])  # нет pm2 (локально) — не проблема

    def test_pm2_deploy_restarts_are_not_crashes(self):
        """13 выкаток за сутки: бот выходит сам («Форма остановлен» / KeyboardInterrupt старого кода) — не проблема."""
        err, out = os.path.join(TMP, "pm2-err2.log"), os.path.join(TMP, "pm2-out2.log")
        write(err, "Traceback (most recent call last):\n  File \"/root/horo/coach/coach.py\", line 170, in tg\n"
                   "    with urllib.request.urlopen(req, timeout=70) as r:\nKeyboardInterrupt\n")
        write(out, "Веб-сервер: http://127.0.0.1:8789\nФорма запущен\nФорма остановлен\n" * 12)
        env = {"status": "online", "restart_time": 181, "pm_err_log_path": err, "pm_out_log_path": out}
        state = {"restarts": 168, "offsets": {err: 0, out: 0}}
        with mock.patch.object(sc, "sh", return_value=(0, json.dumps([{"name": "coach-bot", "pm2_env": env}]))):
            self.assertEqual(sc.pm2_check(state)[0], [])
        env["restart_time"] = 187  # ещё 6 перезапусков без остановки — падения
        with mock.patch.object(sc, "sh", return_value=(0, json.dumps([{"name": "coach-bot", "pm2_env": env}]))):
            self.assertIn("coach-bot падал и перезапускался 6 раз с прошлой проверки", [p["what"] for p in sc.pm2_check(state)[0]])


class DecideTest(unittest.TestCase):
    def test_clean_is_quiet_weekly_ok(self):
        a, r = app(), rep(0)
        self.assertEqual(sc.decide(r, a, a.now), "ok")  # «всё ок» ещё не было
        self.assertEqual(sc.act(a, r), "ok")
        self.assertIn("Проверил приложение: всё ок", a.sent[-1][1])
        self.assertEqual(sc.act(a, r), "quiet")  # неделю — молчит
        self.assertEqual(len(a.sent), 1)
        a.db.meta("selfcheck:ok", time.time() - 8 * 86400)
        self.assertEqual(sc.decide(r, a, a.now), "ok")

    def test_clean_manual_always_answers(self):
        a, r = app(), rep(0)
        a.db.meta("selfcheck:ok", time.time())
        sc.act(a, r, chat=U)
        self.assertIn("всё ок", a.sent[-1][1])

    def test_notes_are_not_problems(self):
        r = rep(0)
        self.assertEqual(sc.real(r), [])  # заметка обходчика — не повод чинить

    def test_repair_once_a_day(self):
        a, r = app(), rep(2)
        self.assertEqual(sc.act(a, r), "repair")
        self.assertEqual(len(a.SUP.calls), 1)
        uid, body = a.SUP.calls[0]
        self.assertEqual(uid, U)
        self.assertIn("Самопроверка Формы — чинить только найденное, тесты обязательны", body["text"])
        self.assertIn(os.path.join(r["dir"], "report.txt"), body["text"])
        self.assertIn("нажать «Замер»", body["text"])  # шаги воспроизведения
        self.assertIn("🛠 <b>Самопроверка нашла 2 проблемы — чиню</b>", a.sent[-1][1])
        self.assertEqual(sc.act(a, rep(1)), "limit")  # второй раз за день — нет
        self.assertEqual(len(a.SUP.calls), 1)
        a.now = a.now + datetime.timedelta(days=1)
        self.assertEqual(sc.act(a, rep(1)), "repair")
        self.assertEqual(len(a.SUP.calls), 2)

    def test_support_busy_retry_in_hour(self):
        a, r = app(busy="other"), rep(3)
        self.assertEqual(sc.act(a, r), "later")
        self.assertEqual(a.SUP.calls, [])
        self.assertEqual(a.sent, [])  # ночью/сам — без сообщений
        p = json.loads(a.db.meta("selfcheck:pending"))
        self.assertAlmostEqual(p["at"], time.time() + 3600, delta=5)
        self.assertIsNone(sc.retry(a))  # час не прошёл
        a.SUP.state = None
        with mock.patch.object(sc.time, "time", return_value=time.time() + 3700):
            self.assertEqual(sc.retry(a), "repair")
        self.assertEqual(len(a.SUP.calls), 1)
        self.assertEqual(a.db.meta("selfcheck:pending"), "")
        self.assertIsNone(sc.retry(a))

    def test_busy_self_also_waits(self):
        a = app(busy="self")
        self.assertEqual(sc.decide(rep(1), a, a.now), "later")

    def test_night_waits_morning_manual_does_not(self):
        a = app(hour=3, minute=40)
        self.assertEqual(sc.act(a, rep(1)), "later")
        p = json.loads(a.db.meta("selfcheck:pending"))
        self.assertEqual(datetime.datetime.fromtimestamp(p["at"], a.now.tzinfo).hour, 7)
        self.assertEqual(a.SUP.calls, [])
        b = app(hour=3, minute=40)
        self.assertEqual(sc.act(b, rep(1), chat=U), "repair")  # сам нажал ночью — чиним сразу

    def test_task_fits_support_limit(self):
        r = rep(200)
        t = sc.task(r)
        self.assertLessEqual(len(t), 4000)
        self.assertIn("…и ещё", t)
        self.assertIn("report.txt", t)

    def test_plural(self):
        self.assertEqual([sc.plural(n) for n in (1, 2, 5, 11, 21, 22)],
                         ["проблему", "проблемы", "проблем", "проблем", "проблему", "проблемы"])


class AccessTest(unittest.TestCase):
    def setUp(self):
        self.db, _ = tc.seeded(os.path.join(tempfile.mkdtemp(dir=TMP), "bot.db"))
        self.db.meta("owner", U)
        self.db.user(OTHER, "Друг")
        self.sent = []
        self.p = [mock.patch.object(coach, "db", self.db), mock.patch.object(coach, "ALLOWED", {U, OTHER}),
                  mock.patch.object(coach, "tg", side_effect=lambda m, **kw: self.sent.append((m, kw)) or {"ok": True, "result": {}}),
                  mock.patch.object(sc, "start", return_value=True)]
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()

    def test_api_owner_only_and_rate(self):
        r = tc.call("/api/selfcheck", {}, user={"id": OTHER, "first_name": "Друг"})
        self.assertEqual(r.code, 403)
        sc.start.assert_not_called()
        self.assertEqual(tc.call("/api/selfcheck", {}, user={"id": 99}).code, 403)  # чужой
        r = tc.call("/api/selfcheck", {})
        self.assertEqual(r.code, 200)
        self.assertTrue(r.json["running"])
        sc.start.assert_called_once()
        r = tc.call("/api/selfcheck", {})  # через минуту — рано
        self.assertEqual(r.code, 400)
        self.assertIn("через", r.json["error"])
        self.assertEqual(sc.start.call_count, 1)
        self.db.meta("selfcheck:manual", time.time() - 31 * 60)
        self.assertEqual(tc.call("/api/selfcheck", {}).code, 200)
        self.assertEqual(sc.start.call_count, 2)

    def test_admin_flag_in_all(self):
        self.assertTrue(tc.call("/api/all").json["admin"])
        self.assertFalse(tc.call("/api/all", user={"id": OTHER, "first_name": "Друг"}).json["admin"])

    def test_command_owner_only(self):
        msg = lambda uid, t: {"chat": {"id": uid, "type": "private"}, "from": {"id": uid, "first_name": "x"}, "text": t}  # noqa: E731
        self.db.meta(f"mode:{OTHER}", "app")
        coach.on_message(msg(OTHER, "/selfcheck"))
        sc.start.assert_not_called()
        self.assertIn("Такой команды нет", self.sent[-1][1]["text"])
        self.db.meta(f"mode:{U}", "app")
        coach.on_message(msg(U, "/selfcheck"))
        sc.start.assert_called_once()
        self.assertIn("Проверяю приложение", self.sent[-1][1]["text"])
        coach.on_message(msg(U, "/selfcheck"))  # сразу снова — «недавно проверял»
        self.assertIn("Недавно проверял", self.sent[-1][1]["text"])
        self.assertEqual(sc.start.call_count, 1)


class FixtureTest(unittest.TestCase):
    def test_fixture_local_temp_db_and_gap_header(self):
        """Настоящая фикстура: только 127.0.0.1, временная база, живая coach/data/coach.db не появляется и не меняется."""
        live = os.path.join(HERE, "data", "coach.db")
        before = os.stat(live).st_mtime if os.path.exists(live) else None
        with sc.Fixture() as fx:
            self.assertTrue(fx.url.startswith("http://127.0.0.1:"))
            r = sc.LOCAL.open(fx.url + "api/all", timeout=10)
            self.assertTrue(json.loads(r.read())["admin"])
            req = sc.urllib.request.Request(fx.url + "api/no-such-example", data=b"{}", method="POST")
            self.assertEqual(sc.LOCAL.open(req, timeout=10).headers.get("X-Fixture"), "none")  # примера нет — помечено
            tmp = fx.tmp
        self.assertFalse(os.path.exists(tmp))
        self.assertIsNotNone(fx.proc.poll())  # погашена
        after = os.stat(live).st_mtime if os.path.exists(live) else None
        self.assertEqual(before, after)

    def test_fixture_env(self):
        with mock.patch.object(sc.subprocess, "Popen") as po, mock.patch.object(sc.LOCAL, "open") as op:
            po.return_value.poll.return_value = None
            op.return_value.status = 200
            fx = sc.Fixture()
            env = po.call_args.kwargs["env"]
            self.assertTrue(env["COACH_DB"].startswith(fx.tmp))
            self.assertNotIn("COACH_ALLOWED", env)
            fx.close()


if __name__ == "__main__":
    unittest.main(verbosity=1)
