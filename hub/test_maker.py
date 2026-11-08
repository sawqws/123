"""Мастерская Главного без Telegram, pm2 и Claude: все боты, «Сделать бота», «Доработать», откат.
usage: python3 hub/test_maker.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.update(HUB_TOKEN="111:HUB")
os.environ.pop("HUB_ALLOWED", None)
import hub  # noqa: E402
import maker  # noqa: E402

U = 7


class Sync:
    """threading.Thread, который выполняет сразу — тесты последовательные."""

    def __init__(self, target, args=(), daemon=None):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


class Base(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.bots = os.path.join(d, "bots")
        self.sent, self.runs = [], []
        self.pm2 = {"horo-bot": "online", "hub-bot": "online"}
        self.p = [mock.patch.object(hub, "DATA", d), mock.patch.object(hub, "STATE", os.path.join(d, "s.json")),
                  mock.patch.object(hub, "tg", side_effect=self.fake_tg),
                  mock.patch.object(hub, "sh", side_effect=self.fake_sh),
                  mock.patch.object(maker, "BOTS", self.bots),
                  mock.patch.object(maker.threading, "Thread", Sync),
                  mock.patch.object(maker.time, "sleep", lambda s: None),
                  mock.patch.object(maker, "username", lambda tok: "test_bot" if tok else None)]
        for p in self.p:
            p.start()
        hub.on_message(self.msg("/start"))
        self.sent.clear()

    def tearDown(self):
        for p in self.p:
            p.stop()

    def fake_tg(self, method, **kw):
        self.sent.append((method, kw))
        return {"ok": True, "result": {"message_id": len(self.sent)}}

    def fake_sh(self, *cmd, timeout=60, env=None):
        self.runs.append(cmd)
        if cmd[:2] == ("pm2", "jlist"):
            return 0, json.dumps([{"name": n, "pm2_env": {"status": s, "pm_uptime": time.time() * 1000},
                                   "monit": {}} for n, s in self.pm2.items()])
        if cmd[:2] == ("pm2", "start"):
            self.pm2[cmd[cmd.index("--name") + 1]] = "online"
        if cmd[0] == "git":
            p = subprocess.run(cmd, capture_output=True, text=True)
            return p.returncode, p.stdout + p.stderr
        return 0, "ok"

    def msg(self, text):
        return {"chat": {"id": U, "type": "private"}, "from": {"id": U}, "text": text, "message_id": 99}

    def press(self, data):
        hub.on_callback({"id": "1", "from": {"id": U}, "data": data, "message": {"chat": {"id": U}, "message_id": 5}})

    def texts(self):
        return [kw.get("text", "") for m, kw in self.sent if m in ("sendMessage", "editMessageText")]


class AllBots(Base):
    def test_list_and_install_buttons(self):
        hub.on_message(self.msg("/all"))
        text = self.texts()[-1]
        self.assertIn("🟢 <b>HDP</b>", text)
        self.assertIn("⚪️ <b>КПП</b> · не поставлен", text)
        self.assertNotIn("      ", text)  # без выравнивания пробелами
        self.assertIn("<blockquote>", text)
        self.assertIn("Боты", text)
        kb = json.dumps([kw for m, kw in self.sent][-1]["reply_markup"], ensure_ascii=False)
        self.assertIn("add|kpp", kb)
        self.assertIn("add|avito", kb)
        self.assertIn('"mk"', kb)


class Make(Base):
    def fake_claude(self, prompt, cwd, on_step=None, timeout=0, tools=True):
        if not tools:
            return True, '{"name": "Вода", "username": "water_rem", "slug": "water", "summary": "напоминает пить"}'
        on_step and on_step("пишу bot.py")
        with open(os.path.join(cwd, "bot.py"), "w") as f:
            f.write("print('hi')\n")
        return True, "Напоминает пить воду каждые 2 часа."

    def test_make_with_manual_token_then_fix_and_rollback(self):
        with mock.patch.object(maker, "claude", self.fake_claude), \
                mock.patch.object(maker, "botfather", lambda n, w: (None, "Секретарь не подключён")):
            self.press("mk")
            hub.on_message(self.msg("бот, который напоминает пить воду"))
            self.assertTrue(any("Осталось создать бота" in t and "water_rem_bot" in t for t in self.texts()))
            hub.on_message(self.msg("123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"))
        self.assertIn(("deleteMessage", {"chat_id": U, "message_id": 99}), self.sent)
        info = maker.made()["water"]
        self.assertEqual(info["pm2"], "bot-water")
        self.assertEqual(self.pm2["bot-water"], "online")
        env = os.path.join(self.bots, "water", ".env")
        self.assertEqual(oct(os.stat(env).st_mode & 0o777), "0o600")
        self.assertIn("BOT_TOKEN=123456789:", open(env).read())
        self.assertTrue(any("работает — @test_bot" in t for t in self.texts()))

        def edit_claude(prompt, cwd, on_step=None, timeout=0, tools=True):
            self.assertIn("кнопку «Выпил»", prompt)
            with open(os.path.join(cwd, "bot.py"), "a") as f:
                f.write("print('button')\n")
            return True, "Добавил кнопку."
        with mock.patch.object(maker, "claude", edit_claude):
            self.press("fix|bot-water")
            hub.on_message(self.msg("добавь кнопку «Выпил»"))
        self.assertIn("button", open(os.path.join(self.bots, "water", "bot.py")).read())
        self.assertIn(("pm2", "restart", "bot-water"), self.runs)
        kb = json.dumps([kw for m, kw in self.sent if m == "editMessageText"][-1]["reply_markup"])
        sha = kb.split("rb|bot-water|")[1].split('"')[0]
        self.press(f"rb|bot-water|{sha}")
        self.assertNotIn("button", open(os.path.join(self.bots, "water", "bot.py")).read())

        self.press("del|water")
        self.assertNotIn("water", maker.made())
        self.assertFalse(os.path.exists(os.path.join(self.bots, "water")))

    def test_busy(self):
        maker.job.acquire()
        try:
            maker.make(U, "ещё бот")
        finally:
            maker.job.release()
        self.assertIn("занят", self.texts()[-1])


class AutoCheck(Base):
    """Сразу после запуска нового бота — обход всех его кнопок, тесты, лог → Claude исправляет и улучшает →
    тесты → перезапуск. Claude, botcrawl, Секретарь и Telegram — подменены."""
    fake_claude = Make.fake_claude

    def test_quality_in_prompts(self):
        for prompt in (maker.CODE, maker.FIX):
            for need in ("1–5 строк", "до 24 символов", "не больше 3 в ряд", "плавно", "мёртвых кнопок",
                         "Оникс-стекло", "отклик"):
                self.assertIn(need, prompt)
            self.assertNotIn("{quality}", prompt)
        maker.CODE.format(dir="d", tz="t", name="n", common="c")
        maker.FIX.format(label="l", where="w", tz="t")

    def test_crawl_fix_restart_after_deploy(self):
        crawled, prompts = [], []

        def crawl(username, out, tick=None, params=None):
            crawled.append((username, params))
            return {"clicks": 9, "problems": ["/start › ⚙️ Настройки: в ответе: ⚠️ ошибка"]}

        def claude(prompt, cwd, on_step=None, timeout=0, tools=True):
            if "автопроверка" in prompt:
                prompts.append(prompt)
                with open(os.path.join(cwd, "bot.py"), "a") as f:
                    f.write("print('settings')\n")
                return True, "Починил настройки."
            return self.fake_claude(prompt, cwd, on_step, timeout, tools)
        with mock.patch.object(maker, "claude", claude), mock.patch.object(maker.check, "crawl", crawl), \
                mock.patch.object(maker, "botfather", lambda n, w: ("123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", None)), \
                mock.patch.object(hub, "secretary_status", lambda: {"account": {"name": "Глеб"}}):
            self.press("mk")
            hub.on_message(self.msg("бот, который напоминает пить воду"))
        self.assertEqual(crawled, [("test_bot", maker.check.NEW_CRAWL)])  # только новый бот
        self.assertEqual(len(prompts), 1)
        self.assertIn("⚠️ ошибка", prompts[0])
        self.assertIn("красивее, проще, короче", prompts[0])
        self.assertIn("проверил 9 кнопок, исправил 1", self.texts()[-1])
        self.assertIn(("pm2", "restart", "bot-water"), self.runs)
        self.assertIn("settings", open(os.path.join(self.bots, "water", "bot.py")).read())
        self.assertFalse(maker.job.locked())

    def test_no_secretary_account_skips_crawl(self):
        with mock.patch.object(maker, "claude", self.fake_claude), \
                mock.patch.object(maker.check, "crawl", lambda *a, **k: self.fail("без входа кнопки не жмём")), \
                mock.patch.object(maker, "botfather", lambda n, w: (None, "нет")):
            self.press("mk")
            hub.on_message(self.msg("бот для воды"))
            hub.on_message(self.msg("123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"))
        self.assertIn("ошибок нет, улучшил", self.texts()[-1])  # Глеб 16.10: улучшать сразу, даже без ошибок
        self.assertIn("Секретарь не вошёл", self.texts()[-1])


class Update(Base):
    def test_local_commits_rebased(self):
        root = tempfile.mkdtemp()

        def g(*a):
            p = subprocess.run(["git", "-C", root, "-c", "user.name=t", "-c", "user.email=t@t", *a],
                               capture_output=True, text=True)
            return p.returncode, p.stdout + p.stderr
        g("init", "-q", "-b", "main")
        open(os.path.join(root, "a.txt"), "w").write("1\n")
        g("add", "-A"), g("commit", "-qm", "base"), g("update-ref", "refs/remotes/origin/main", "HEAD")
        open(os.path.join(root, "b.txt"), "w").write("local\n")
        g("add", "-A"), g("commit", "-qm", "local")
        code, _ = hub.sync_main(g)
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(os.path.join(root, "b.txt")))  # локальная правка не потерялась
        code, _ = hub.sync_main(g, reset=True)
        self.assertFalse(os.path.exists(os.path.join(root, "b.txt")))


    def test_conflict_keeps_local_in_branch_and_takes_main(self):
        # 22.10: Страж коммитил здесь, те же строки пришли в main — rebase конфликтовал, обновление не шло совсем
        root = tempfile.mkdtemp()

        def g(*a):
            p = subprocess.run(["git", "-C", root, "-c", "user.name=t", "-c", "user.email=t@t", *a],
                               capture_output=True, text=True)
            return p.returncode, p.stdout + p.stderr
        g("init", "-q", "-b", "main")
        open(os.path.join(root, "a.txt"), "w").write("1\n")
        g("add", "-A"), g("commit", "-qm", "base")
        open(os.path.join(root, "a.txt"), "w").write("main\n")
        g("commit", "-qam", "upstream"), g("update-ref", "refs/remotes/origin/main", "HEAD")
        g("reset", "-q", "--hard", "HEAD~1")
        open(os.path.join(root, "a.txt"), "w").write("local\n")
        g("commit", "-qam", "local fix")
        code, out = hub.sync_main(g)
        self.assertEqual(code, 0)
        self.assertIn("ветке local-", out)
        self.assertEqual(open(os.path.join(root, "a.txt")).read(), "main\n")  # код — свежий main
        _, br = g("branch", "--list", "local-*")
        _, kept = g("show", br.strip() + ":a.txt")
        self.assertEqual(kept, "local\n")  # местная правка не потерялась


class LostFixes(Base):
    """08.11: коммит поддержки выпал из кода при rebase, а сообщение об этом не ушло никому."""

    def setUp(self):
        super().setUp()
        self.root = tempfile.mkdtemp()
        p = mock.patch.object(hub, "REPO", self.root)
        p.start()
        self.p.append(p)
        self.pm2["coach-bot"] = "online"
        self.g("init", "-q", "-b", "main")
        self.commit({"coach/bot.py": "a\nb\nc\n", "coach/CLAUDE.md": "one\ntwo\n"}, "base")

    def g(self, *a):
        p = subprocess.run(["git", "-C", self.root, "-c", "user.name=t", "-c", "user.email=t@t", *a],
                           capture_output=True, text=True)
        return p.returncode, p.stdout + p.stderr

    def commit(self, files, msg):
        for rel, text in files.items():
            path = os.path.join(self.root, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        self.g("add", "-A")
        self.g("commit", "-qm", msg)
        return self.g("rev-parse", "HEAD")[1].strip()

    def diverge(self, upstream, local, msg="Поддержка (Форма): правка"):
        """В main пришло upstream, на сервере поверх базы — коммит local."""
        self.commit(upstream, "upstream")
        self.g("update-ref", "refs/remotes/origin/main", "HEAD")
        self.g("reset", "-q", "--hard", "HEAD~1")
        return self.commit(local, msg)

    def read(self, rel):
        with open(os.path.join(self.root, rel), encoding="utf-8") as f:
            return f.read()

    def branches(self):
        return self.g("branch", "--list", "local-*")[1].split()

    def ahead(self):
        return int(self.g("rev-list", "--count", "origin/main..HEAD")[1])

    def in_progress(self):
        git_dir = os.path.join(self.root, ".git")
        return [x for x in ("rebase-merge", "rebase-apply", "CHERRY_PICK_HEAD") if os.path.exists(os.path.join(git_dir, x))]

    def cards(self):
        return [t for t in self.texts() if "Правка выпала" in t]

    def buttons(self):
        return json.dumps([kw for m, kw in self.sent if m == "sendMessage"], ensure_ascii=False)

    def dropped(self):
        """Конфликт в коде: правка ушла в ветку, код — свежий main."""
        sha = self.diverge({"coach/bot.py": "a\nMAIN\nc\n"}, {"coach/bot.py": "a\nFIX\nc\n"})
        self.assertEqual(hub.sync_main(hub.repo_git)[0], 0)
        self.assertEqual(self.read("coach/bot.py"), "a\nMAIN\nc\n")
        return sha

    def install_line(self):
        """Настоящая строка из coach/install.sh."""
        with open(os.path.join(os.path.dirname(HERE), "coach", "install.sh"), encoding="utf-8") as f:
            return next(x for x in f if 'branch -f "local-' in x)

    def run_install_line(self):
        subprocess.run(["bash", "-c", self.install_line()], env={**os.environ, "DIR": self.root, "BRANCH": "main"}, check=True)

    def saved_by_install(self, upstream, local):
        """install.sh: местный коммит в ветку, код — origin/main."""
        sha = self.diverge(upstream, local)
        self.run_install_line()
        self.g("checkout", "-q", "-B", "main", "origin/main")
        return sha

    # --- sync_main

    def test_doc_conflict_keeps_code(self):
        """Настоящий случай: соседняя строка в CLAUDE.md не должна уносить правку кода."""
        self.diverge({"coach/CLAUDE.md": "one\ntwo\nthree\n"}, {"coach/CLAUDE.md": "one\nTWO\n", "coach/bot.py": "a\nFIX\nc\n"})
        code, _ = hub.sync_main(hub.repo_git)
        self.assertEqual(code, 0)
        self.assertEqual(self.read("coach/bot.py"), "a\nFIX\nc\n")
        self.assertEqual(self.read("coach/CLAUDE.md"), "one\ntwo\nthree\n")
        self.assertEqual(self.branches(), [])
        self.assertEqual(self.ahead(), 1)
        self.assertEqual(self.in_progress(), [])

    def test_doc_only_commit_disappears(self):
        self.diverge({"coach/CLAUDE.md": "one\ntwo\nthree\n"}, {"coach/CLAUDE.md": "one\nTWO\n"})
        self.assertEqual(hub.sync_main(hub.repo_git)[0], 0)
        self.assertEqual(self.ahead(), 0)
        self.assertEqual(self.branches(), [])
        self.assertEqual(self.in_progress(), [])

    def test_doc_then_code_conflict_keeps_both_in_branch(self):
        self.diverge({"coach/CLAUDE.md": "one\ntwo\nthree\n", "coach/bot.py": "a\nMAIN\nc\n"}, {"coach/CLAUDE.md": "one\nTWO\n"}, "справка")
        self.commit({"coach/bot.py": "a\nFIX\nc\n"}, "код")
        self.assertEqual(hub.sync_main(hub.repo_git)[0], 0)
        self.assertEqual(self.in_progress(), [])
        self.assertEqual(self.read("coach/bot.py"), "a\nMAIN\nc\n")
        br = self.branches()[0]
        self.assertEqual(self.g("show", br + ":coach/bot.py")[1], "a\nFIX\nc\n")
        self.assertEqual(self.g("show", br + ":coach/CLAUDE.md")[1], "one\nTWO\n")

    # --- карточка

    def test_card_once(self):
        sha = self.dropped()
        hub.tell_lost(U)
        self.assertEqual(len(self.cards()), 1)
        self.assertIn("Поддержка (Форма): правка", self.cards()[0])
        self.assertIn("Тренер «Форма»", self.cards()[0])
        self.assertIn(f"lost|fix|{sha}", self.buttons())
        self.assertIn(f"lost|drop|{sha}", self.buttons())
        hub.tell_lost(U)
        self.assertEqual(len(self.cards()), 1)

    def test_undelivered_card_retried(self):
        self.dropped()
        with mock.patch.object(hub, "send", side_effect=RuntimeError("сеть")):
            hub.tell_lost(U)
        self.assertEqual(self.cards(), [])
        hub.tell_lost(U)
        self.assertEqual(len(self.cards()), 1)

    def test_update_all_tells_before_restart(self):
        """Сообщение sync_main раньше терялось: _update_all его не показывал."""
        bare = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", bare], check=True)
        self.g("remote", "add", "origin", bare)
        self.g("push", "-q", "origin", "main")
        self.commit({"coach/bot.py": "a\nMAIN\nc\n"}, "upstream")
        self.g("push", "-q", "origin", "main")
        self.g("reset", "-q", "--hard", "HEAD~1")
        self.commit({"coach/bot.py": "a\nFIX\nc\n"}, "Поддержка (Форма): правка")
        with mock.patch.object(hub.os, "_exit", side_effect=SystemExit):
            with self.assertRaises(SystemExit):
                hub._update_all(U)
        texts = self.texts()
        lost = next(i for i, t in enumerate(texts) if "Правка выпала" in t)
        done = next(i for i, t in enumerate(texts) if "Обновил и перезапустил" in t)
        self.assertLess(lost, done)

    def test_branch_saved_before_checkout_survives(self):
        """Между «сохранил в ветку» и checkout -B код ещё тот же: git cherry пуст, но удалять ветку нельзя."""
        self.diverge({"coach/bot.py": "a\nMAIN\nc\n"}, {"coach/bot.py": "a\nFIX\nc\n"})
        self.run_install_line()
        hub.tell_lost(U)
        self.assertEqual(len(self.branches()), 1)
        self.assertEqual(self.cards(), [])
        self.g("checkout", "-q", "-B", "main", "origin/main")
        hub.tell_lost(U)
        self.assertEqual(len(self.cards()), 1)

    def test_same_content_in_code_is_no_card(self):
        self.commit({"coach/bot.py": "a\nb\nc\nd\n"}, "Поддержка: то же")
        self.g("branch", "local-1108-0000", "HEAD")
        self.g("reset", "-q", "--hard", "HEAD~1")
        self.commit({"coach/CLAUDE.md": "one\ntwo\nthree\n"}, "другое")
        self.commit({"coach/bot.py": "a\nb\nc\nd\n"}, "то же в main")
        hub.tell_lost(U)
        self.assertEqual(self.cards(), [])
        self.assertEqual(self.branches(), [])

    # --- кнопки

    def test_restore_clean(self):
        sha = self.saved_by_install({"coach/CLAUDE.md": "one\ntwo\nthree\n"}, {"coach/bot.py": "a\nFIX\nc\n"})
        self.assertEqual(len(self.branches()), 1)
        self.assertEqual(self.read("coach/bot.py"), "a\nb\nc\n")
        self.press(f"lost|fix|{sha}")
        self.assertEqual(self.read("coach/bot.py"), "a\nFIX\nc\n")
        self.assertEqual(self.ahead(), 1)
        self.assertIn(("pm2", "restart", "coach-bot"), [r[:3] for r in self.runs])
        self.assertTrue(any("снова в коде" in t for t in self.texts()))
        self.assertEqual(hub.lost_commits(), [])
        hub.tell_lost(U)
        self.assertEqual(self.branches(), [])

    def test_restore_doc_conflict(self):
        sha = self.saved_by_install({"coach/CLAUDE.md": "one\ntwo\nthree\n"},
                                    {"coach/CLAUDE.md": "one\nTWO\n", "coach/bot.py": "a\nFIX\nc\n"})
        self.press(f"lost|fix|{sha}")
        self.assertEqual(self.read("coach/bot.py"), "a\nFIX\nc\n")
        self.assertEqual(self.read("coach/CLAUDE.md"), "one\ntwo\nthree\n")
        self.assertEqual(self.in_progress(), [])
        self.assertEqual(hub.lost_commits(), [])

    def test_restore_code_conflict_goes_to_claude(self):
        sha = self.dropped()
        seen = {}

        def fix(chat, pm2, tz):
            seen.update(pm2=pm2, tz=tz, busy=self.in_progress())
            self.commit({"coach/bot.py": "a\nMAIN FIX\nc\n"}, "Главный: правка")
        with mock.patch.object(maker, "fix", side_effect=fix):
            self.press(f"lost|fix|{sha}")
        self.assertEqual(seen["pm2"], "coach-bot")
        self.assertEqual(seen["busy"], [])  # неудачный cherry-pick снят до Claude
        self.assertIn("Поддержка (Форма): правка", seen["tz"])
        self.assertIn("+FIX", seen["tz"])
        self.assertEqual(hub.lost_commits(), [])
        hub.tell_lost(U)
        self.assertEqual(self.branches(), [])

    def test_restore_failed_stays_open(self):
        sha = self.dropped()
        with mock.patch.object(maker, "fix"):
            self.press(f"lost|fix|{sha}")
        self.assertEqual([c["sha"] for c in hub.lost_commits()], [sha])
        self.assertEqual(len(self.branches()), 1)

    def test_restore_busy(self):
        sha = self.dropped()
        with maker.job:
            self.press(f"lost|fix|{sha}")
        self.assertTrue(any("занят" in t for t in self.texts()))
        self.assertEqual(self.read("coach/bot.py"), "a\nMAIN\nc\n")
        self.assertEqual([c["sha"] for c in hub.lost_commits()], [sha])

    def test_restore_common_code_conflict_asks_chat(self):
        self.commit({"common/x.py": "1\n"}, "общий")
        self.diverge({"common/x.py": "main\n"}, {"common/x.py": "local\n"})
        hub.sync_main(hub.repo_git)
        sha = hub.lost_commits()[0]["sha"]
        with mock.patch.object(maker, "fix") as fix:
            self.press(f"lost|fix|{sha}")
        fix.assert_not_called()
        self.assertTrue(any("Сам не верну" in t for t in self.texts()))

    def test_drop(self):
        sha = self.dropped()
        hub.tell_lost(U)
        self.press(f"lost|drop|{sha}")
        self.assertEqual(hub.lost_commits(), [])
        hub.tell_lost(U)
        self.assertEqual(self.branches(), [])
        self.assertEqual(len(self.cards()), 1)

    # --- для чатов

    def test_status_shows_local_and_does_not_touch(self):
        self.diverge({"coach/CLAUDE.md": "one\ntwo\nthree\n"}, {"coach/bot.py": "a\nFIX\nc\n"}, "только на сервере")
        self.g("branch", "local-1108-0000", "HEAD")
        self.g("reset", "-q", "--hard", "HEAD~1")
        self.commit({"kpp/bot.py": "x\n"}, "ещё на сервере")
        r = hub.remote_api("status", {})["local"]
        self.assertEqual(len(r["ahead"]), 1)
        self.assertIn("ещё на сервере", r["ahead"][0])
        self.assertEqual(len(r["lost"]), 1)
        self.assertIn("только на сервере", r["lost"][0])
        self.assertEqual(self.branches(), ["local-1108-0000"])

    def test_remote_prints_local(self):
        import contextlib
        import io
        import remote
        answer = {"apps": [], "server": {}, "local": {"ahead": ["abc правка"], "lost": ["def local-1 выпала"]}}
        out = io.StringIO()
        with mock.patch.object(remote, "call", return_value=answer), contextlib.redirect_stdout(out):
            remote.main(["status"])
        self.assertIn("Только на сервере", out.getvalue())
        self.assertIn("abc правка", out.getvalue())
        self.assertIn("Выпали из кода", out.getvalue())
        answer["local"] = {"ahead": [], "lost": []}
        out = io.StringIO()
        with mock.patch.object(remote, "call", return_value=answer), contextlib.redirect_stdout(out):
            remote.main(["status"])
        self.assertNotIn("⚠️", out.getvalue())

    # --- установщики

    def test_every_install_script_keeps_local(self):
        repo = os.path.dirname(HERE)
        checked = 0
        for kind in sorted(os.listdir(repo)):
            path = os.path.join(repo, kind, "install.sh")
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8") as f:
                lines = f.read().splitlines()
            for i, line in enumerate(lines):
                if "checkout -B" in line:
                    checked += 1
                    around = lines[i - 1] + line  # tgbot при местных коммитах код вообще не трогает
                    self.assertTrue('branch -f "local-' in around or "rev-list origin/main..HEAD" in around, kind)
        self.assertGreaterEqual(checked, 11)

    def test_install_line_no_local_no_branch(self):
        self.commit({"coach/CLAUDE.md": "one\ntwo\nthree\n"}, "upstream")
        self.g("update-ref", "refs/remotes/origin/main", "HEAD")
        self.run_install_line()
        self.assertEqual(self.branches(), [])


class StudioBots(Base):
    def test_studio_registry_merged(self):
        os.makedirs(self.bots, exist_ok=True)
        with open(os.path.join(self.bots, "registry.json"), "w") as f:
            json.dump({"voda": {"name": "Вода", "pm2": "bot-voda", "dir": os.path.join(self.bots, "voda"),
                                "summary": "вода", "username": "voda_bot", "slug": "voda"}}, f)
        self.assertIn("voda", maker.made())
        self.assertEqual(maker.label("bot-voda"), "🧩 Вода")
        self.assertIn(("bot-voda", "🧩 Вода"), maker.targets())
        maker.set_made("voda", None)  # удаление из Главного — из списка Студии
        with open(os.path.join(self.bots, "registry.json")) as f:
            self.assertEqual(json.load(f), {})


class CommitScope(unittest.TestCase):
    """«Доработать» коммитит только код бота: данные и чужие папки в коммит не попадают."""

    def test_only_bot_folder_without_data(self):
        def real(*cmd, timeout=60, env=None):
            p = subprocess.run(cmd, capture_output=True, text=True)
            return p.returncode, p.stdout + p.stderr
        d = tempfile.mkdtemp()
        for rel, text in (("coach/bot.py", "v1"), ("kpp/bot.py", "v1")):
            os.makedirs(os.path.join(d, os.path.dirname(rel)), exist_ok=True)
            with open(os.path.join(d, rel), "w") as f:
                f.write(text)
        real("git", "-C", d, "init", "-q")
        real("git", "-C", d, "add", "-A")
        real("git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
        os.makedirs(os.path.join(d, "coach", "data"))
        for rel, text in (("coach/bot.py", "v2"), ("coach/data/coach.db", "секрет"), ("kpp/bot.py", "чужая правка"),
                          ("coach/new.py", "новый файл")):
            with open(os.path.join(d, rel), "w") as f:
                f.write(text)
        with mock.patch.object(hub, "sh", side_effect=real):
            sha = maker.commit(d, "тест", ["coach"])
        self.assertTrue(sha)
        _, files = real("git", "-C", d, "show", "--name-only", "--pretty=", "HEAD")
        self.assertEqual(sorted(files.split()), ["coach/bot.py", "coach/new.py"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
