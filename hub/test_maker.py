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
