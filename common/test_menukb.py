#!/usr/bin/env python3
"""Тесты меню-квадратика (common/menukb.py)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import menukb  # noqa: E402


class MenuKBTest(unittest.TestCase):
    def test_markup_and_once_per_version(self):
        kb = menukb.MenuKB([["🔄 Обновить", "⚙️"]], version=2, placeholder="Пиши")
        m = kb.markup()
        self.assertEqual(m["keyboard"], [[{"text": "🔄 Обновить"}, {"text": "⚙️"}]])
        self.assertTrue(m["one_time_keyboard"] and m["resize_keyboard"] and not m["is_persistent"])
        sent, ver = [], {}
        send = lambda chat, text, mk: sent.append((chat, text, mk))
        self.assertTrue(kb.ensure(send, 7, done=None, mark=lambda v: ver.update(v=v)))
        self.assertEqual(ver["v"], 2 + 1000 * menukb.REV)  # REV +1 — все боты ставят меню заново
        self.assertFalse(kb.ensure(send, 7, done=ver["v"]))
        self.assertTrue(kb.ensure(send, 7, done=2))  # старая версия — снова
        self.assertTrue(kb.ensure(send, 7, done=ver["v"], force=True))
        self.assertEqual(len(sent), 3)
        self.assertTrue(kb.has(" ⚙️ ") and not kb.has("привет"))

    def test_send_error_does_not_mark(self):
        kb = menukb.MenuKB([["a"]])
        ver = {}

        def boom(*a):
            raise OSError("сеть")
        self.assertFalse(kb.ensure(boom, 1, mark=lambda v: ver.update(v=v)))
        self.assertEqual(ver, {})


if __name__ == "__main__":
    unittest.main()
