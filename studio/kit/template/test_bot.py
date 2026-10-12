"""Тесты без сети: python3 test_bot.py"""
import unittest

import bot as app


class BotTest(unittest.TestCase):
    def setUp(self):
        self.t = app.bot.testing()

    def test_start(self):
        self.t.send("/start")
        self.assertIn("Заметки", self.t.last)

    def test_note_from_text_and_app(self):
        self.t.send("купить молоко")
        self.assertEqual(app.notes()[0]["text"], "купить молоко")
        code, res = self.t.api("add", {"text": "позвонить"})
        self.assertEqual(code, 200)
        self.assertEqual(res["notes"][0]["text"], "позвонить")

    def test_button_and_reply(self):
        self.t.press("new")
        self.t.send("идея")
        self.assertEqual(app.notes()[-1]["text"], "идея")

    def test_stranger_locked(self):
        self.t.send("/start", uid=999)
        self.assertIn("личный", self.t.last)
        self.assertEqual(self.t.api("state", uid=999)[0], 403)

    def test_empty_note_error(self):
        self.assertEqual(self.t.api("add", {"text": " "})[0], 400)


if __name__ == "__main__":
    unittest.main()
