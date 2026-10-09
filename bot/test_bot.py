"""Проверка бота без Telegram и сайта: черновик -> правка -> отправка -> правила, алфавит, /update.
usage: python3 bot/test_bot.py
"""
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("TELEGRAM_TOKEN", "test")
import bot  # noqa: E402
import drafts  # noqa: E402

URL = "https://horodigital.ru/student/topic/11111111-1111-1111-1111-111111111111/task/22222222-2222-2222-2222-222222222222"
TASK = "22222222-2222-2222-2222-222222222222"
CHAT = 42


class Sync:
    """threading.Thread, который выполняет задачу сразу."""
    def __init__(self, target, args=(), daemon=None):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


class BotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.patches = [
            mock.patch.object(bot, "SETTINGS", os.path.join(t, "settings.json")),
            mock.patch.object(drafts, "TMP", t),
            mock.patch.object(drafts, "STYLE_DIR", os.path.join(t, "style")),
            mock.patch.object(drafts, "STYLE", os.path.join(t, "style", "style.md")),
            mock.patch.object(drafts, "EDITS", os.path.join(t, "style", "edits.jsonl")),
            mock.patch.object(bot.threading, "Thread", Sync),
            mock.patch.object(bot, "tg", self.fake_tg),
            mock.patch.object(bot, "send_file", lambda chat, path, caption="": self.files.append(path)),
            mock.patch.object(bot, "claude", self.fake_claude),
            mock.patch.object(bot, "node", self.fake_node),
        ]
        for p in self.patches:
            p.start()
        self.msgs, self.files, self.prompts, self.nodes = [], [], [], []
        os.makedirs(drafts.task_dir(TASK))
        with open(os.path.join(drafts.task_dir(TASK), "task.json"), "w") as f:
            json.dump({"data": {"type": "detailedAnswer", "title": "Конспект §5"}}, f)

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()
        bot.mode.clear()

    # ---- заглушки ----
    def fake_tg(self, method, **p):
        if method == "sendMessage":
            self.msgs.append((p["text"], p.get("reply_markup")))
        return {"ok": True, "result": []}

    def fake_claude(self, prompt, tools, timeout=0):
        self.prompts.append(prompt)
        if prompt.startswith("Задание:"):  # решение: Claude пишет черновик и картинку
            drafts.write(TASK, "Клетка делится митозом.\nЭто важно.")
            d = drafts.files_dir(TASK)
            os.makedirs(d, exist_ok=True)
            pathlib.Path(d, "hand.png").write_bytes(b"png")
            return True, "Сделал черновик"
        if "Глеб написал про него" in prompt:  # просьба поправить черновик
            drafts.write(TASK, "Клетка делится митозом.")
            return True, "Убрал второе предложение"
        os.makedirs(drafts.STYLE_DIR, exist_ok=True)
        pathlib.Path(drafts.STYLE).write_text("- коротко\n")
        return True, "- писать короче"

    def fake_node(self, *args, timeout=0, prog="node"):
        self.nodes.append(args)
        return True, "Статус: checking"

    def say(self, text="", **extra):
        bot.handle({"chat": {"id": CHAT}, "from": {"id": CHAT}, "text": text, **extra})

    def last(self):
        return self.msgs[-1]

    # ---- тесты ----
    def test_draft_edit_and_submit(self):
        self.say(URL)
        self.assertIn("horo/tmp/" + TASK + "/draft.txt", self.prompts[0])
        self.assertTrue(any("Черновик для учителя: Конспект §5" in m and kb for m, kb in self.msgs))
        self.assertEqual(self.files[-1], os.path.join(drafts.files_dir(TASK), "hand.png"))
        self.assertEqual(self.nodes, [])  # без подтверждения ничего не отправлено

        self.say("Клетка делится митозом.")
        self.assertEqual(drafts.read(TASK), "Клетка делится митозом.")
        self.assertEqual(drafts.recent_edits()[-1]["before"], "Клетка делится митозом.\nЭто важно.")

        bot.on_callback({"id": "1", "data": "send", "message": {"chat": {"id": CHAT}, "message_id": 5}})
        args = self.nodes[-1]
        self.assertEqual(args[:3], ("horo/answer.js", URL, drafts.text_path(TASK)))
        self.assertIn(os.path.join(drafts.files_dir(TASK), "hand.png"), args)
        self.assertIsNone(bot.draft())
        self.assertIn("БЫЛО:", self.prompts[-1])  # правки ушли на обобщение
        self.assertIn("Запомнил", self.last()[0])

    def test_short_message_is_a_request(self):
        self.say(URL)
        self.say("убери второе")
        self.assertIn("«убери второе»", self.prompts[-1])
        self.assertEqual(drafts.read(TASK), "Клетка делится митозом.")
        self.assertEqual(bot.draft()["pairs"][0][2], "убери второе")
        self.say("ок")
        self.assertIn("ЧТО ОН НАПИСАЛ: убери второе", self.prompts[-1])

    def test_own_photo_replaces_generated(self):
        self.say(URL)
        with mock.patch.object(bot, "download", lambda fid: (b"jpg", "photo.jpg")):
            self.say(photo=[{"file_id": "a"}, {"file_id": "b"}])
            self.say(photo=[{"file_id": "c"}])
        names = [os.path.basename(f) for f in drafts.files(TASK)]
        self.assertEqual(names, ["01_photo.jpg", "02_photo.jpg"])

    def test_drop_and_ok_word(self):
        self.say(URL)
        bot.on_callback({"id": "1", "data": "drop", "message": {"chat": {"id": CHAT}, "message_id": 5}})
        self.assertIsNone(bot.draft())
        self.assertEqual(self.nodes, [])
        self.say(URL)
        self.say("ок")
        self.assertEqual(self.nodes[-1][0], "horo/answer.js")
        self.assertEqual(len(self.prompts), 2)  # правок не было — обобщать нечего

    def test_autotest_has_no_draft(self):
        def solve_only(prompt, tools, timeout=0):
            self.prompts.append(prompt)
            return True, "Баллы 5/5"
        with mock.patch.object(bot, "claude", solve_only):
            self.say(URL)
        self.assertIsNone(bot.draft())
        self.assertIn("Баллы 5/5", self.last()[0])

    def test_late_list_sent_as_html(self):
        with mock.patch.object(bot, "node", lambda *a, timeout=0, prog="node": (True, '❗ <b>Просрочено: 1</b>\n🔁 <a href="u">Тест &amp; Ко</a>')):
            self.say(bot.BTN_LATE)
        self.assertEqual(self.last()[0], '❗ <b>Просрочено: 1</b>\n🔁 <a href="u">Тест &amp; Ко</a>')  # разметка не экранирована

    def test_handwriting_needs_alphabet(self):
        with mock.patch.object(bot, "GLEB_EXTRA", "/nonexistent"):
            self.assertIn("НУЖЕН_АЛФАВИТ", bot.prompt_for(URL, "", False))
            with mock.patch.object(bot, "claude", lambda p, t, timeout=0: (True, "НУЖЕН_АЛФАВИТ")):
                self.say(URL)
        self.assertIn("алфавита у меня ещё нет", self.last()[0])
        self.assertIsNone(bot.draft())
        with mock.patch.object(bot, "GLEB_EXTRA", __file__):
            self.assertNotIn("НУЖЕН_АЛФАВИТ", bot.prompt_for(URL, "", False))

    def test_errors_are_short(self):
        trace = "Error: page.goto: net::ERR_NAME_NOT_RESOLVED at https://x\n    at open (/root/horo/horo/lib.js:40:11)"
        self.assertEqual(bot.explain(trace), "⚠️ Не получилось: page.goto: net::ERR_NAME_NOT_RESOLVED at https://x")
        self.assertIn("долго не отвечает", bot.explain("TimeoutError: Timeout 30000ms exceeded"))

    def test_alphabet_mode(self):
        with mock.patch.object(bot, "py", lambda *a, timeout=0: (True, "Добавил: а б")) as _, \
             mock.patch.object(bot, "download", lambda fid: (b"jpg", "s.jpg")), \
             mock.patch.object(bot, "ALPHA_DIR", self.tmp.name):
            self.say(bot.BTN_HAND)
            self.assertEqual(len(self.files), 3)
            self.say(photo=[{"file_id": "a"}])
            self.assertTrue(any("Добавил: а б" in m for m, _ in self.msgs))
            self.say("готово")
        self.assertNotIn(CHAT, bot.mode)


if __name__ == "__main__":
    unittest.main()
