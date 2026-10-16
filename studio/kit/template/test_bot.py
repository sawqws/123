"""Тесты без сети: python3 test_bot.py"""
import unittest

import bot as app


class BotTest(unittest.TestCase):
    def setUp(self):
        self.t = app.bot.testing()

    def test_start(self):
        self.t.send("/start")
        self.assertIn("Заметки", self.t.last)
        self.assertIn("🛠 Поддержка", self.t.buttons)  # поддержка — на главном экране

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

    def test_menu_square(self):
        """☰ Меню-квадратик у поля ввода (botkit сам, из главного экрана): /start ставит, нажатие — как кнопка."""
        self.t.send("/start")
        self.assertIn("➕ Новая", self.t.kb)
        self.assertIn("🛠 Поддержка", self.t.kb)
        self.t.send("➕ Новая")  # текст кнопки — не заметка, а то же, что inline-кнопка
        self.assertIn("Что записать", self.t.last)
        self.assertEqual(app.notes(), [])
        self.t.send("идея")
        self.assertEqual(app.notes()[-1]["text"], "идея")

    def test_stranger_locked(self):
        self.t.send("/start", uid=999)
        self.assertIn("личный", self.t.last)
        self.assertEqual(self.t.api("state", uid=999)[0], 403)

    def test_empty_note_error(self):
        self.assertEqual(self.t.api("add", {"text": " "})[0], 400)

    def test_mode_chat_or_app(self):
        """С https-адресом первый /start спрашивает «Как удобнее?»; в чате — без кнопки приложения, в приложении — с ней."""
        app.bot.app_url = "https://apps.example/b/notes/"
        try:
            self.t.send("/start")
            self.assertIn("Как удобнее", self.t.last)
            self.t.press("mode:chat")
            self.assertIn("Режим: 💬 чат", self.t.last)
            self.assertFalse(any("web_app" in b for b in self.t.keyboard))
            self.assertIn("⚙️ Режим", self.t.buttons)
            self.t.send("/app")  # явная просьба — кнопка есть и в режиме «чат»
            self.assertTrue(any("web_app" in b for b in self.t.keyboard))
            self.t.send("/mode")
            self.t.press("mode:app")
            self.assertIn("Режим: 📱 приложение", self.t.last)
            self.assertTrue(any("web_app" in b for b in self.t.keyboard))
            self.assertEqual(self.t.api("mode", {"mode": "chat"})[1]["mode"], "chat")  # «Перейти в чат» из приложения
            self.assertIn("Заметки", self.t.last)  # бот прислал главное меню
        finally:
            app.bot.app_url = ""


if __name__ == "__main__":
    unittest.main()
