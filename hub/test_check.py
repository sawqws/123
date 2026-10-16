"""🧪 Проверка ботов (hub/check.py): без сети и pm2 — поддельные боты с тестами, логом и живым обходом.
python3 hub/test_check.py"""
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_hub import Base, U  # noqa: E402

import hub  # noqa: E402

check = hub.check

OK_TEST = "import unittest\nclass T(unittest.TestCase):\n    def test_ok(self): pass\nunittest.main()\n"
BAD_TEST = ("import unittest\nclass T(unittest.TestCase):\n    def test_water(self):\n"
            "        raise ValueError('нет кнопки «Вода»')\nunittest.main()\n")


class CheckTest(Base):
    def setUp(self):
        super().setUp()
        self.repo = tempfile.mkdtemp()
        for folder, body in (("coach", BAD_TEST), ("bot", OK_TEST), ("hub", OK_TEST), ("common", OK_TEST)):
            os.makedirs(os.path.join(self.repo, folder))
            with open(os.path.join(self.repo, folder, "test_x.py"), "w") as f:
                f.write(body)
        self.err = os.path.join(self.repo, "coach-err.log")
        with open(self.err, "w") as f:
            f.write("Traceback (most recent call last):\n  File x\nKeyError: 'sleep'\n"
                    "Traceback (most recent call last):\n  File y\nKeyboardInterrupt\n")  # перезапуск pm2 — не ошибка
        self.procs = [{"name": n, "pm2_env": {"status": "online", "restart_time": 2, "exec_interpreter": "python3",
                                             "pm_err_log_path": self.err if n == "coach-bot" else ""}}
                      for n in ("horo-bot", "coach-bot", "hub-bot")]
        self.q = [mock.patch.object(hub, "REPO", self.repo), mock.patch.object(check, "jlist", lambda: self.procs)]
        for p in self.q:
            p.start()

    def tearDown(self):
        for p in self.q:
            p.stop()
        super().tearDown()

    def test_targets_and_tests(self):
        ts = {t["pm2"]: t for t in check.targets(self.procs)}
        self.assertEqual(set(ts), {"horo-bot", "coach-bot", "hub-bot"})
        self.assertEqual([r for r, _ in check.tests_of(ts["coach-bot"])], ["coach/test_x.py"])
        self.assertEqual(check.python_of({"pm2_env": {"exec_interpreter": "node"}}), "python3")

    def test_quick_run_report_and_fix(self):
        res = check.check_all(full=False)
        by = {b["pm2"]: b for b in res["bots"]}
        self.assertTrue(by["horo-bot"]["ok"])
        self.assertTrue(by["common"]["ok"])
        coach = by["coach-bot"]
        self.assertFalse(coach["ok"])
        self.assertEqual(coach["tests"], "0/1")
        self.assertTrue(any("нет кнопки «Вода»" in p for p in coach["problems"]), coach["problems"])
        self.assertIn("в логе 1 ошибок, последняя: KeyError: 'sleep'", coach["problems"])
        # вторая проверка: старые ошибки лога не повторяются, новый перезапуск — виден
        self.procs[1]["pm2_env"]["restart_time"] = 5
        coach = {b["pm2"]: b for b in check.check_all()["bots"]}["coach-bot"]
        self.assertFalse(any("KeyError" in p for p in coach["problems"]))
        self.assertTrue(any("перезапускался 3" in p for p in coach["problems"]))
        text, btns = check.report(hub.setting("check_last"))
        self.assertIn("Сломано 1 из", text)
        self.assertIn("❌ Тренер «Форма» — ", text)
        self.assertNotIn("нет кнопки", text)  # подробности — только по «📋 Подробно»
        self.assertIn("нет кнопки «Вода»", check.report(hub.setting("check_last"), details=True)[0])
        flat = [x for r in btns for x in r]
        self.assertTrue(all(len(r) <= 3 for r in btns))
        self.assertTrue(all(len(x["text"]) <= 24 for x in flat))
        self.assertIn("chk|fix|coach-bot", [x.get("callback_data") for x in flat])
        fixes = []
        with mock.patch.object(hub.maker, "fix", lambda chat, pm2, tz: fixes.append((pm2, tz))):
            check.fix_one(U, "coach-bot")
            check.fix_one(U, "horo-bot")  # здоров — Claude не зовём
        self.assertEqual([p for p, _ in fixes], ["coach-bot"])
        self.assertIn("перезапускался", fixes[0][1])

    def test_tokens_hidden_and_sandbox(self):
        out = "FAIL: test_apply\nAssertionError: ['8649915091:AAG4oKDNAohsmZwxiSPzUG_yj6F6pFabc'] != []"
        self.assertNotIn("AAG4", check.failure(out))
        self.assertIn("‹токен›", check.failure(out))
        cmd = check.sandboxed(["python3", "x.py"])
        self.assertEqual(cmd[-2:], ["python3", "x.py"])
        self.assertIn("nice", " ".join(cmd))
        if cmd[0] == "unshare":
            self.assertIn("/etc/*.env", cmd[6])

    def test_full_crawls_every_bot_hub_last(self):
        seen = []

        def crawl(username, out, tick=None):
            seen.append(username)
            tick and tick("✅ /start")
            return {"clicks": 7, "problems": ["/start › ⚙️ Режим: сбой: таймаут"] if username == "kpp" else []}
        with mock.patch.object(check, "crawl", crawl), mock.patch.object(check, "PARALLEL_CRAWL", 1), \
                mock.patch.object(hub.maker, "username", lambda tok: None), \
                mock.patch.object(hub.maker, "all_bots", lambda rows: [
                    {"pm2": "hub-bot", "username": "hub"}, {"pm2": "horo-bot", "username": "hdp"},
                    {"pm2": "coach-bot", "username": "kpp"}]):
            res = check.check_all(full=True, live=check.Live(U, 5))
        self.assertEqual(seen[-1], "hub")  # Главный — последним
        by = {b["pm2"]: b for b in res["bots"]}
        self.assertEqual(by["horo-bot"]["clicks"], 7)
        self.assertTrue(any("таймаут" in p for p in by["coach-bot"]["problems"]))

    def test_buttons_in_hub(self):
        hub.on_message(self.msg("/1"))
        self.assertIn('"chk"', self.last()[1])
        hub.on_message(self.msg("/test"))
        text, btns = self.last()
        self.assertIn("Проверка ботов", text)
        self.assertIn("chk|run", btns)
        self.assertIn("chk|full", btns)
        started = []
        with mock.patch.object(check, "threading", mock.Mock(
                Thread=lambda target, args=(), daemon=None: mock.Mock(start=lambda: started.append(target)))):
            self.cq("chk|run")
        self.assertEqual(started, [check.run])
        self.cq("chk|last")  # отчёта ещё нет — всплывашка, не тишина
        answers = [kw.get("text") for k, kw in self.sent if k == "answerCallbackQuery"]
        self.assertIn("Отчёта ещё нет", answers[-1])

    def test_run_sends_report(self):
        with mock.patch.object(check, "check_all", lambda full, live: {
                "at": time.time(), "full": full, "secs": 75,
                "bots": [{"pm2": "horo-bot", "label": "📚 HDP", "ok": True, "problems": [], "tests": "3/3", "clicks": None}]}):
            check.run(U, None, full=True)  # Секретаря нет — живой обход честно отменяется
        texts = [kw.get("text", "") for k, kw in self.sent if k == "sendMessage"]
        self.assertTrue(any("Живой обход недоступен" in t for t in texts))
        self.assertIn("Все 1 в порядке", texts[-1])
        self.assertLess(len(texts[-1]), 60)  # отчёт — одна строка
        self.assertFalse(check.running.locked())


class AppTest(CheckTest):
    def test_app_and_home(self):
        """Проверка видна на главном экране чата и в приложении (вкладка «Боты»), запускается и чинит оттуда."""
        hub.on_message(self.msg("/start"))
        self.assertIn('"chk"', self.last()[1])
        self.assertIsNone(hub.app_state()["check"]["last"])
        check.check_all()
        started = []
        fake = mock.Mock(Thread=lambda target, args=(), daemon=None: mock.Mock(start=lambda: started.append((target, args))))
        with mock.patch.object(check, "threading", fake):  # только у check: настоящий threading не трогаем
            r = hub.app_action("check", "full")
            self.assertTrue(r["ok"])
            self.assertEqual(started[-1], (check.run, (U, None, True)))
            last = hub.app_state()["check"]["last"]
            coach = [b for b in last["bots"] if b["pm2"] == "coach-bot"][0]
            self.assertEqual((coach["ok"], coach["title"]), (False, "Тренер «Форма»"))
            self.assertIn("тесты", coach["gist"])
            hub.app_action("check_fix", "coach-bot")
            self.assertEqual(started[-1], (check.fix_one, (U, "coach-bot")))
            with self.assertRaises(ValueError):
                hub.app_action("check_fix", "horo-bot")  # здоров
            hub.app_action("check_fixall", "")
            self.assertEqual(started[-1][0], check.fix_all)
        check.running.acquire()
        try:
            self.assertTrue(hub.app_state()["check"]["running"])
            with self.assertRaises(ValueError):
                hub.app_action("check", "run")
        finally:
            check.running.release()


class NewBotTest(unittest.TestCase):
    def test_crawl_new_bot_args(self):
        """Новый бот: глубина 3, до 80 нажатий, карточка «Как удобнее?» — через «💬 В чате»; без Главного (Студия)."""
        cmds = []

        class P:
            stdout = iter(["✅ /start\n"])

            def wait(self):
                pass
        with mock.patch.object(check, "ui", None), \
                mock.patch.object(check.subprocess, "Popen", lambda cmd, **k: cmds.append((cmd, k)) or P()):
            res = check.crawl("voda_bot", os.path.join(tempfile.mkdtemp(), "no.json"), params=check.NEW_CRAWL)
        cmd, k = cmds[0]
        self.assertEqual(cmd[1:3], ["tgbot/botcrawl.py", "voda_bot"])
        self.assertEqual(cmd[cmd.index("--max") + 1], "80")
        self.assertEqual(cmd[cmd.index("--depth") + 1], "3")
        self.assertEqual(cmd[cmd.index("--mode") + 1], "💬 В чате")
        self.assertEqual(k["cwd"], check.REPO)
        self.assertIn("fail", res)  # отчёта нет — честный сбой, а не «всё хорошо»

    def test_after_text(self):
        base = {"clicks": 21, "skip": "", "found": [], "fixed": 0, "left": [], "why": "", "sha": None, "out": ""}
        self.assertIn("проверил 21 кнопку — всё в порядке", check.after_text(base, "Вода")[0])
        self.assertIn("ошибок нет, улучшил", check.after_text(dict(base, improved=True), "Вода")[0])
        self.assertIn("проверил 3 кнопки, исправил 2", check.after_text({**base, "clicks": 3, "fixed": 2}, "Вода")[0])
        title, lines, foot = check.after_text({**base, "clicks": None, "skip": "нет @username",
                                               "left": ["тесты: x"], "why": "откатил"}, "<b>")
        self.assertIn("проверил тесты и логи, осталось 1", title)
        self.assertIn("&lt;b&gt;", title)
        self.assertIn("нет @username", foot)
        self.assertNotIn("{", check.QUALITY + check.POLISH_TZ)  # вставляется в шаблоны .format


class RiskyTest(unittest.TestCase):
    def test_crawler_never_presses_check(self):
        """Живой обход Главного не должен запускать проверку сам себя."""
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tgbot", "actions.py")) as f:
            src = f.read()
        self.assertIn('r"🧪|автотест|', src)


if __name__ == "__main__":
    unittest.main()
