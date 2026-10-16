"""🛡 Страж HDP (hub/guard.py): без сети, pm2 и Claude — поддельный HDP (pm2, «пульс», журнал, настройки).
python3 hub/test_guard.py"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_hub import Base  # noqa: E402

import hub  # noqa: E402

guard = hub.guard
NOW = 1_800_000_000


class GuardTest(Base):
    def setUp(self):
        super().setUp()
        self.repo = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.repo, "horo", "tmp"))
        self.status, self.restarts, self.exit, self.web = "online", 1, 0, True
        self.fixes, self.rs = [], []
        self.q = [mock.patch.object(hub, "REPO", self.repo),
                  mock.patch.object(hub, "HDP_SETTINGS", self.tmp("bot_settings.json")),
                  mock.patch.object(guard, "web_ok", lambda: self.web),
                  mock.patch.object(hub, "log_text", lambda *a: "хвост лога"),
                  mock.patch.object(hub, "restart_app", self.fake_restart),
                  mock.patch.object(hub.maker, "fix", lambda chat, pm2, tz: self.fixes.append((pm2, tz)))]
        for p in self.q:
            p.start()
        self.beat(NOW)
        self.sent.clear()

    def tearDown(self):
        for p in self.q:
            p.stop()
        super().tearDown()

    def tmp(self, name):
        return os.path.join(self.repo, "horo", "tmp", name)

    def fake_restart(self, name, wait=4):
        self.rs.append(name)
        return True, "ok"

    def procs(self, up=7200, now=NOW):
        return [{"name": "horo-bot", "pm2_env": {"status": self.status, "restart_time": self.restarts,
                                                 "exit_code": self.exit, "pm_uptime": (now - up) * 1000,
                                                 "pm_err_log_path": ""}}]

    def beat(self, at):
        with open(self.tmp("heartbeat"), "w") as f:
            f.write("1")
        os.utime(self.tmp("heartbeat"), (at, at))

    def log(self, kind, text, detail=""):
        with open(self.tmp("journal.jsonl"), "a") as f:
            f.write(json.dumps({"at": NOW, "kind": kind, "text": text, "detail": detail}, ensure_ascii=False) + "\n")

    def tick(self, now=NOW, **kw):
        acts = guard.tick(now, self.procs(now=now, **kw))
        return acts

    def issues(self):
        return guard.state(hub.load())["issues"]

    def test_quiet_when_all_good(self):
        self.assertEqual(self.tick(), [])
        self.assertEqual(self.issues(), {})
        self.assertIsNone(guard.summary())

    def test_hang_restarts(self):
        self.beat(NOW - 900)
        acts = self.tick()
        self.assertIn(("restart", "завис"), acts)
        self.assertEqual(guard.summary(), "🛡 HDP: проблем 1")
        self.beat(NOW + 50)
        self.tick(NOW + 60)
        self.assertIsNone(guard.summary())  # ожил — ошибка закрыта

    def test_no_judging_while_warming_up_or_updating(self):
        self.beat(NOW - 900)
        self.assertEqual(self.tick(up=60), [])  # только что поднялся
        with mock.patch.object(hub, "updating", lambda: True):
            self.assertEqual(self.tick(), [])  # «Обновить всех» его перезапускает

    def test_down_two_ticks_then_restart_limited(self):
        self.status = "errored"
        self.assertEqual(self.tick(), [])  # один раз — может, мигнул
        acts = self.tick(NOW + 60)
        self.assertIn(("restart", "не работал"), acts)
        for i in range(2, 5):
            acts = self.tick(NOW + 60 * i)
        self.assertNotIn(("restart", "не работал"), acts)  # 3 перезапуска за час — хватит
        self.assertTrue(any("не поднимается" in it["text"] for it in self.issues().values()))

    def test_bug_in_journal_fixes_once(self):
        self.log("bug", "KeyError: 'title'", "Traceback ... KeyError: 'title'")
        acts = self.tick()
        fix = [a for a in acts if a[0] == "fix"]
        self.assertEqual(len(fix), 1)
        self.log("bug", "KeyError: 'title'")
        self.assertFalse([a for a in self.tick(NOW + 60) if a[0] == "fix"])  # уже чиним — второй раз не зовём
        it = self.issues()[fix[0][1]]
        self.assertEqual(it["count"], 2)

    def test_handle_fix_runs_claude_when_idle(self):
        self.log("bug", "TypeError: boom", "tb")
        acts = self.tick()
        with mock.patch.object(guard.threading, "Thread") as T:
            T.side_effect = lambda target, args, daemon: mock.Mock(start=lambda: target(*args))
            guard.handle(acts)
        self.assertEqual(len(self.fixes), 1)
        self.assertEqual(self.fixes[0][0], "horo-bot")
        self.assertIn("TypeError: boom", self.fixes[0][1])
        self.assertTrue(any("чиню сам" in kw.get("text", "") for _, kw in self.sent))

    def test_fix_waits_while_hdp_busy(self):
        with open(self.tmp("bot_settings.json"), "w") as f:
            json.dump({"active": {"t1": {"since": NOW - 60}}}, f)
        self.log("bug", "TypeError: boom")
        acts = self.tick()
        guard.handle(acts)
        self.assertEqual(self.fixes, [])
        it = next(iter(self.issues().values()))
        self.assertEqual(it["fix_at"], 0)  # позовём, как освободится

    def test_network_is_not_a_code_bug(self):
        self.log("script", "node horo/grades.js -> 1: Сайт долго не отвечает (timeout)")
        self.assertEqual(self.tick(), [])  # разовый сбой — молчим
        self.log("script", "node horo/grades.js -> 1: Сайт долго не отвечает (timeout)")
        acts = self.tick(NOW + 60)
        self.assertEqual([a[0] for a in acts], ["tell"])  # повторилось — сказать, но не чинить

    def test_script_error_fixed_when_repeats(self):
        self.log("script", "node horo/submit.js -> 1: Нет начатой попытки")
        self.assertEqual(self.tick(), [])
        self.log("script", "node horo/submit.js -> 1: Нет начатой попытки")
        self.assertEqual([a[0] for a in self.tick(NOW + 60)], ["fix"])

    def test_net_flood(self):
        for _ in range(10):
            self.log("net", "getUpdates: timed out")
        acts = self.tick()
        self.assertEqual([a[0] for a in acts], ["tell"])

    def test_stuck_test_and_resolve(self):
        with open(self.tmp("bot_settings.json"), "w") as f:
            json.dump({"tests": {"t1": {"title": "Тест 2.0", "at": NOW - 3600}}}, f)
        self.tick()
        self.assertTrue(any("тест не сдан" in it["text"] for it in self.issues().values()))
        with open(self.tmp("bot_settings.json"), "w") as f:
            json.dump({"tests": {}}, f)
        self.tick(NOW + 60)
        self.assertIsNone(guard.summary())

    def test_auto_off_only_tells(self):
        self.cq("grd|auto")
        self.log("bug", "TypeError: boom")
        self.assertEqual([a[0] for a in self.tick()], ["tell"])

    def test_screen_and_buttons(self):
        self.log("bug", "TypeError: boom")
        self.tick()
        self.cq("grd")
        text, kb = self.last()
        self.assertIn("Страж HDP", text)
        self.assertIn("TypeError: boom", text)
        self.assertIn("grd|fixall", kb)
        self.cq("grd|log")
        self.assertIn("Журнал", self.last()[0])
        it = next(iter(self.issues().values()))
        self.cq("grd|mute|" + guard.key(it["sig"]))
        self.assertIsNone(guard.summary())

    def test_tell_card(self):
        self.log("bug", "TypeError: boom")
        acts = self.tick()
        guard.tell(hub.owner(), [a[1] for a in acts])
        text, kb = self.last()
        self.assertIn("HDP", text)
        self.assertIn("grd|fix|", kb)

    def test_home_and_command(self):
        hub.on_message(self.msg("/guard"))
        self.assertIn("Страж HDP", self.last()[0])
        hub.on_message(self.msg("/start"))
        self.assertIn('"grd"', self.last()[1])

    def test_app(self):
        self.log("bug", "TypeError: boom")
        self.tick()
        st = hub.app_state()["guard"]
        self.assertTrue(st["auto"])
        self.assertEqual(st["issues"][0]["text"], "TypeError: boom")
        self.assertEqual(hub.app_action("guard", "auto")["text"], "Баги только показываю")
        with self.assertRaises(ValueError):
            hub.app_action("guard", "nope")

    def test_journal_rotation_reads_from_start(self):
        self.log("bug", "A: 1")
        self.tick()
        os.replace(self.tmp("journal.jsonl"), self.tmp("journal.jsonl.1"))
        self.log("bug", "B: x")
        self.tick(NOW + 60)
        self.assertTrue(any(it["text"] == "B: x" for it in self.issues().values()))

    def test_sig_ignores_numbers_and_ids(self):
        a = guard.sig_of("script", "submit.js 5c4a311d-27a7-4528-b751-b4d600d3af45 -> 1: вопрос 3 из 7")
        b = guard.sig_of("script", "submit.js 71f1a46a-8ad4-4cc9-9a50-b4d600d3af45 -> 1: вопрос 5 из 9")
        self.assertEqual(a, b)


class JournalInBot(unittest.TestCase):
    """HDP пишет журнал и «пульс» (bot/bot.py) — то, что читает Страж."""

    def test_bot_journal(self):
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot", "bot.py")) as f:
            src = f.read()
        for needle in ('JOURNAL = os.path.join(REPO, "horo", "tmp", "journal.jsonl")',
                       'HEARTBEAT = os.path.join(REPO, "horo", "tmp", "heartbeat")', 'journal("bug"', 'beat()'):
            self.assertIn(needle, src)


if __name__ == "__main__":
    unittest.main()
