"""Тесты поддержки (common/support.py) без Telegram и без Claude: python3 common/test_support.py"""
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["SUPPORT_LOCK"] = os.path.join(tempfile.mkdtemp(), "lock")
import support  # noqa: E402

OWNER = 7


class Sync:
    """threading.Thread, который выполняет сразу."""

    def __init__(self, target, args=(), daemon=None):
        self.target, self.args = target, args

    def start(self):
        if self.target.__name__ != "_commands":
            self.target(*self.args)


def repo():
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "demo"))
    with open(os.path.join(d, "demo", "bot.py"), "w") as f:
        f.write("X = 1\n")
    with open(os.path.join(d, "demo", "test_ok.py"), "w") as f:
        f.write("import sys\nsys.path.insert(0, 'demo')\nimport bot\nassert bot.X > 0\n")
    for cmd in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"]):
        subprocess.run(["git", "-C", d, *cmd], check=True)
    return d


class SupportTest(unittest.TestCase):
    def setUp(self):
        self.cwd = repo()
        self.sent, self.restarts = [], []
        self.patches = [mock.patch.object(support.threading, "Thread", Sync),
                        mock.patch.object(support.Support, "restart", lambda s: self.restarts.append(s.pm2)),
                        mock.patch.object(support.Support, "logs", lambda s: "Traceback: KeyError 'week'")]
        for p in self.patches:
            p.start()
        self.sup = support.Support("1:x", "Демо", folders=["demo"], pm2="demo-bot", allowed=lambda u: u == OWNER,
                                   tests=["python3 demo/test_ok.py"], cwd=self.cwd)
        self.sup.tg = self.tg

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def tg(self, method, **p):
        self.sent.append((method, p))
        return {"ok": True, "result": {"message_id": len(self.sent)}}

    def texts(self):
        return [p.get("text", "") for m, p in self.sent if m in ("sendMessage", "editMessageText")]

    def msg(self, text, uid=OWNER, **kw):
        return {"update_id": 1, "message": {"message_id": 1, "chat": {"id": uid, "type": "private"},
                                            "from": {"id": uid}, "text": text, **kw}}

    def fake_claude(self, value):
        def run(prompt, cwd, on_step=None, timeout=0):
            self.prompt = prompt
            if on_step:
                on_step("правлю bot.py")
            if value is not None:
                with open(os.path.join(cwd, "demo", "bot.py"), "w") as f:
                    f.write(f"X = {value}\n")
            return True, "Кнопка «Неделя» падала на пустой неделе — починил."
        return mock.patch.object(support, "claude", run)

    def test_ask_then_fix_commit_restart_undo(self):
        self.assertTrue(self.sup.intercept(self.msg("/fix")))
        self.assertIn("Поддержка «Демо»", self.texts()[-1])
        with self.fake_claude(2):
            self.assertTrue(self.sup.intercept(self.msg("кнопка Неделя падает")))
        self.assertIn("кнопка Неделя падает", self.prompt)
        self.assertIn("KeyError", self.prompt)  # логи бота в просьбе к Claude
        self.assertIn("python3 demo/test_ok.py", self.prompt)
        self.assertIn("Демо — готово", self.texts()[-1])
        self.assertEqual(self.restarts, ["demo-bot"])
        log = subprocess.run(["git", "-C", self.cwd, "log", "--format=%s"], capture_output=True, text=True).stdout
        self.assertIn("Поддержка (Демо): кнопка Неделя падает", log)
        undo = [b for m, p in self.sent if m == "editMessageText" for row in (p.get("reply_markup") or {}).get(
            "inline_keyboard", []) for b in row if b.get("callback_data", "").startswith("sup|undo|")][-1]
        self.sup.intercept({"update_id": 2, "callback_query": {"id": "1", "from": {"id": OWNER}, "data": undo["callback_data"],
                                                               "message": {"chat": {"id": OWNER}, "message_id": 9}}})
        with open(os.path.join(self.cwd, "demo", "bot.py")) as f:
            self.assertEqual(f.read(), "X = 1\n")
        self.assertEqual(len(self.restarts), 2)

    def test_inline_fix_and_failing_tests_reverted(self):
        with self.fake_claude(0):  # X = 0 — тест падает, вторая попытка тоже
            self.sup.intercept(self.msg("/fix сделай ноль"))
        self.assertIn("не прошла тесты", self.texts()[-1])
        with open(os.path.join(self.cwd, "demo", "bot.py")) as f:
            self.assertEqual(f.read(), "X = 1\n")
        self.assertEqual(self.restarts, [])

    def test_question_without_changes(self):
        with self.fake_claude(None):
            self.sup.intercept(self.msg("/fix а что делает кнопка Неделя?"))
        self.assertIn("код не менялся", self.texts()[-1])
        self.assertEqual(self.restarts, [])

    def test_not_ours(self):
        self.assertFalse(self.sup.intercept(self.msg("/fix", uid=99)))  # чужой — бот разберётся сам
        self.assertFalse(self.sup.intercept(self.msg("привет")))  # не ждали — не наше
        self.assertFalse(self.sup.intercept({"update_id": 3, "callback_query": {"id": "1", "data": "add|1",
                                                                                "from": {"id": OWNER}}}))
        self.sup.intercept(self.msg("/fix"))
        self.assertFalse(self.sup.intercept(self.msg("/start")))  # другая команда — выход из поддержки
        self.assertFalse(self.sup.intercept(self.msg("текст")))
        self.sup.waiting[OWNER] = {"at": time.time() - 3600, "shots": []}
        self.assertFalse(self.sup.intercept(self.msg("старое ожидание протухло")))

    def test_cancel_button(self):
        self.sup.intercept(self.msg("/fix"))
        self.sup.intercept({"update_id": 4, "callback_query": {"id": "1", "data": "sup|no", "from": {"id": OWNER},
                                                               "message": {"chat": {"id": OWNER}, "message_id": 1}}})
        self.assertNotIn(OWNER, self.sup.waiting)
        self.assertFalse(self.sup.intercept(self.msg("текст")))


if __name__ == "__main__":
    unittest.main()
