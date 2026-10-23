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
        self.status, self.restarts, self.exit, self.web, self.site = "online", 1, 0, True, True
        self.fixes, self.rs = [], []
        self.q = [mock.patch.object(hub, "REPO", self.repo),
                  mock.patch.object(hub, "HDP_SETTINGS", self.tmp("bot_settings.json")),
                  mock.patch.object(guard, "web_ok", lambda: self.web),
                  mock.patch.object(guard, "site_ok", lambda: self.site),
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

    def test_site_down_restart_then_claude_at_once(self):
        """Сайт лёг: 2 мин — перезапуск, не поднялся — Claude чинит сразу, даже если HDP занят."""
        self.web = False
        self.assertEqual(self.tick(), [])
        self.assertIn(("restart", "сайт не отвечал"), self.tick(NOW + 60))
        self.assertFalse([a for a in self.tick(NOW + 120) if a[0] == "fix"])
        fix = [a for a in self.tick(NOW + 180) if a[0] == "fix"]
        self.assertEqual(len(fix), 1)
        self.assertTrue(self.issues()[fix[0][1]]["urgent"])
        with mock.patch.object(guard, "busy", lambda: True), mock.patch.object(guard.threading, "Thread") as th:
            guard.handle(fix)
        th.assert_called_once()  # сайт лежит — не ждём, пока HDP освободится
        self.web = True
        self.tick(NOW + 240)
        self.assertIsNone(guard.summary())  # поднялся — всё закрыто

    def test_site_outside_down_restarts_caddy_then_claude(self):
        self.site = False
        self.assertEqual(self.tick(), [])
        self.assertIn(("caddy", "сайт снаружи не открывался"), self.tick(NOW + 60))
        self.tick(NOW + 120)
        fix = [a for a in self.tick(NOW + 180) if a[0] == "fix"]
        self.assertEqual(self.issues()[fix[0][1]].get("target"), "planner-bot")
        guard.handle([("caddy", "тест")])
        self.assertIn("planner-bot", self.rs)
        self.site = True
        self.tick(NOW + 240)
        self.assertIsNone(guard.summary())

    def test_bug_in_journal_fixes_once(self):
        self.log("bug", "KeyError: 'title'", "Traceback ... KeyError: 'title'")
        acts = self.tick()
        fix = [a for a in acts if a[0] == "fix"]
        self.assertEqual(len(fix), 1)
        self.log("bug", "KeyError: 'title'")
        self.assertFalse([a for a in self.tick(NOW + 60) if a[0] == "fix"])  # уже чиним — второй раз не зовём
        it = self.issues()[fix[0][1]]
        self.assertEqual(it["count"], 2)

    def test_stale_bug_not_fixed_next_day(self):
        """16.10: нарочные сбои тестов (ValueError «фон упал») ждали в очереди — лимит 4 починки в сутки, — и через
        21 ч Страж позвал Claude чинить ошибку, которой давно нет. Не повторялась 6 ч — не чиним и не напоминаем."""
        with hub.lock:
            st = hub.load()
            guard.state(st)["fixes"] = [NOW - 4 * 3600] * guard.FIX_PER_DAY  # лимит на сутки исчерпан
            hub.save(st)
        self.log("bug", "ValueError: фон упал")
        self.assertFalse([a for a in self.tick() if a[0] == "fix"])
        later = NOW + 21 * 3600  # лимит прошёл, ошибка больше не появлялась
        self.assertFalse([a for a in self.tick(later) if a[0] in ("fix", "tell")])
        self.log("bug", "ValueError: фон упал")  # повторилась — снова свежая, чиним
        self.assertTrue([a for a in self.tick(later + 60) if a[0] == "fix"])

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

    def test_run_reason_is_error_not_last_stdout_line(self):
        # grades.js всё вывел, легенда — последняя строка stdout; ошибкой она быть не должна
        legend = "Уровень → оценка: от 2.0 — 3, от 2.5 — 4, от 3.5 — 5"
        ok, out, why = guard.run(["bash", "-c", f"echo '{legend}'; exit 1"], 10)
        self.assertFalse(ok)
        self.assertIn(legend, out)
        self.assertEqual(why, "код выхода 1, ошибки не написал")
        self.assertEqual(guard.run(["bash", "-c", "echo x; echo 'API 500: /disciplines' >&2; exit 1"], 10)[2],
                         "API 500: /disciplines")
        self.assertIn("SIGKILL", guard.run(["bash", "-c", "echo x; kill -9 $$"], 10)[2])
        self.assertEqual(guard.run(["bash", "-c", "echo ok"], 10), (True, "ok\n", ""))

    # ---------- 🪞 Дозор зеркала ----------

    BAD = {"ok": False, "secs": 2.1, "checks": [{"name": "страница", "ok": False, "ms": 80, "why": "ответ 502"}],
           "issues": [{"text": "зеркало: страница /student/ — ответ 502", "detail": "<html>Bad Gateway</html>"}]}
    GOOD = {"ok": True, "secs": 1.9, "checks": [{"name": "страница", "ok": True, "ms": 80, "why": ""}], "issues": []}

    def mirror_fixes(self, acts):
        return [a for a in acts if a[0] == "fix"]

    def test_mirror_error_fixed_when_repeats_then_closes(self):
        """Разовый сбой — не чиним; повторился через 5 мин — Claude чинит зеркало; прошёл — ошибка закрыта."""
        self.assertEqual(self.mirror_fixes(guard.mirror_apply(self.BAD, "", NOW)), [])
        acts = guard.mirror_apply(self.BAD, "", NOW + 300)
        self.assertEqual(len(self.mirror_fixes(acts)), 1)
        with mock.patch.object(guard.threading, "Thread") as T:
            T.side_effect = lambda target, args, daemon: mock.Mock(start=lambda: target(*args))
            guard.handle(acts)
        self.assertEqual(len(self.fixes), 1)
        self.assertIn("Дозор зеркала", self.fixes[0][1])  # своё ТЗ: код зеркала и как проверить себя
        self.assertIn("ответ 502", self.fixes[0][1])
        guard.mirror_apply(self.BAD, "", NOW + 600)
        guard.mirror_apply(self.GOOD, "", NOW + 900)
        self.assertTrue(all(it.get("closed") for it in self.issues().values() if it["kind"] == "mirror"))
        m = guard.state(hub.load())["mirror"]
        self.assertTrue(m["ok"])
        self.assertEqual(m["at"], NOW + 900)

    def test_mirror_waits_while_hdp_busy(self):
        with open(self.tmp("bot_settings.json"), "w") as f:
            json.dump({"active": {"t1": {"since": NOW - 60}}}, f)
        guard.mirror_apply(self.BAD, "", NOW)
        guard.handle(guard.mirror_apply(self.BAD, "", NOW + 300))
        self.assertEqual(self.fixes, [])  # HDP решает — починка (с перезапуском) подождёт

    def test_mirror_az_fixes_at_once_and_keeps_light_issues(self):
        guard.mirror_apply(self.BAD, "", NOW)
        az = {"pages": 30, "ours": ["ошибка JS: x is undefined — /student/topic/1"], "fix": "🧪 Проверка сайта… x is undefined"}
        acts = guard.mirror_apply(az, "", NOW + 60, az=True)
        self.assertEqual(len(self.mirror_fixes(acts)), 1)  # полный обход сам сверяет с платформой — чиним сразу
        az_it = self.issues()[acts[0][1]]
        self.assertTrue(az_it["text"].startswith(guard.AZ))
        self.assertIn("x is undefined", az_it["detail"])
        guard.mirror_apply({"pages": 30, "ours": [], "fix": ""}, "", NOW + 120, az=True)
        light = [it for it in self.issues().values() if it["text"].startswith("зеркало:")]
        self.assertFalse(light[0].get("closed"))  # чистый обход не закрывает ошибку лёгкой проверки
        self.assertTrue(self.issues()[acts[0][1]]["closed"])
        self.assertEqual(guard.state(hub.load())["mirror"]["az"], 0)

    def test_mirror_check_failed_network_only_tells(self):
        guard.mirror_apply(None, "URLError: Network is unreachable", NOW)
        acts = guard.mirror_apply(None, "URLError: Network is unreachable", NOW + 300)
        self.assertEqual(self.mirror_fixes(acts), [])
        self.assertTrue([a for a in acts if a[0] == "tell"])

    def test_mirror_az_failed_retries_in_hour(self):
        guard.mirror_apply(None, "FileNotFoundError: site_sessions.json.part", NOW, az=True)
        az_at = guard.state(hub.load())["mirror"]["az_at"]
        self.assertEqual(az_at + guard.MIRROR_AZ_EVERY, NOW + 3600)

    def test_mirror_due_schedules(self):
        started = []
        with mock.patch.object(guard, "mirror_base", lambda: "https://hdp.1-2-3-4.sslip.io"), \
                mock.patch.object(guard.threading, "Thread") as T:
            T.side_effect = lambda target, daemon, args=(): mock.Mock(start=lambda: started.append(target.__name__))
            self.assertEqual(guard.mirror_due(NOW), ["watch", "az"])
            guard.mirror_apply(self.GOOD, "", NOW)
            guard.mirror_apply({"pages": 1, "ours": []}, "", NOW, az=True)
            self.assertEqual(guard.mirror_due(NOW + 60), [])
            self.assertEqual(guard.mirror_due(NOW + guard.MIRROR_EVERY + 1), ["watch"])
            self.web = False
            self.assertIsNone(guard.mirror_due(NOW + 3600))  # сайт лежит — это дело минутной проверки
        self.assertEqual(started, ["mirror_check", "mirror_az", "mirror_check"])

    def test_mirror_check_button_card(self):
        out = json.dumps(self.BAD, ensure_ascii=False)
        with mock.patch.object(guard, "mirror_base", lambda: "https://hdp.x"), \
                mock.patch.object(guard, "run", lambda cmd, t: (True, "warn\n" + out, "")) as r:
            guard.mirror_check(hub.owner())
        text, kb = self.last()
        self.assertIn("Зеркало: проблем 1", text)
        self.assertIn("ответ 502", text)
        self.assertIn("grd|mir", kb)
        self.cq("grd")
        self.assertIn("🪞 Зеркало", self.last()[0])
        self.assertIsNotNone(r)

    def test_child_env_drops_pm2_channel(self):
        with mock.patch.dict(os.environ, {"NODE_CHANNEL_FD": "3", "NODE_CHANNEL_SERIALIZATION_MODE": "json"}):
            env = guard.child_env()
        self.assertNotIn("NODE_CHANNEL_FD", env)
        self.assertNotIn("NODE_CHANNEL_SERIALIZATION_MODE", env)
        self.assertEqual(env["HOME"], "/root")

    def deep(self, site):
        """Глубокая проверка с подменой сайта: site — (прошла, вывод, почему); Claude и тесты в порядке."""
        def fake_run(cmd, timeout):
            return site if "grades.js" in cmd[-1] else (True, "ok", "")
        acts = []
        with mock.patch.object(guard, "run", fake_run), \
                mock.patch.object(guard.check, "worktree", lambda: None), \
                mock.patch.object(guard.check, "drop_worktree", lambda src: None), \
                mock.patch.object(guard.check, "run_test", lambda base, cmd: (True, "")), \
                mock.patch.object(guard, "handle", acts.extend):
            guard.deep()
        return acts

    def test_deep_site_one_off_waits_for_repeat(self):
        legend = "Уровень → оценка: от 2.0 — 3, от 2.5 — 4, от 3.5 — 5"
        site = (False, legend, "код выхода 1, ошибки не написал")
        self.assertEqual(self.deep(site), [])  # разовый сбой — не чиним и не тревожим
        it = next(iter(self.issues().values()))
        self.assertIn("код выхода 1", it["text"])
        self.assertNotIn(legend, it["text"])
        self.assertEqual([a[0] for a in self.deep(site)], ["fix"])  # повторилось — чиним
        self.deep((True, legend, ""))
        self.assertTrue(all(it["closed"] for it in self.issues().values()))  # прошла — ошибка закрыта

    def test_deep_site_killed_is_not_code(self):
        site = (False, "", "прерван сигналом SIGKILL (память или перезапуск), ошибки не написал")
        self.deep(site)
        self.assertEqual([a[0] for a in self.deep(site)], ["tell"])  # повторилось — сказать, но не чинить


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
