"""«Запас сил» больше нигде не пишется (Глеб 04.11): замена в words.fix, в ответах Claude, чате, советах, разборах."""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ai  # noqa: E402
import f_reviews  # noqa: E402
import store  # noqa: E402
import words  # noqa: E402

CASES = {
    "Запас сил сегодня низкий, твой запас сил был малым": "Гибкость пульса сегодня низкая, твоя гибкость пульса была малой",
    "Запас сил остался низким": "Гибкость пульса осталась низкой",
    "запас сил с низким пульсом": "гибкость пульса с низким пульсом",
    "Вчера запас сил был средний (51%)": "Вчера гибкость пульса была средняя (51%)",
    "Запас сил на 23% ниже нормы": "Гибкость пульса на 23% ниже нормы",
    "запас сил 89 (обычно 115)": "гибкость пульса 89 (обычно 115)",
    "с малым запасом сил": "с малой гибкостью пульса",
    "по запасу сил": "по гибкости пульса",
    "низкий запас сил": "низкая гибкость пульса",
    "Отдых нервов упал": "Гибкость пульса упала",
    "без большого запаса сил": "без большой гибкости пульса",
    "Сегодня запас сил ещё не посчитан": "Сегодня гибкость пульса ещё не посчитана",
    "запасной ответ": "запасной ответ",
    "запас воды": "запас воды",
}


class WordsTest(unittest.TestCase):
    def test_fix(self):
        for a, b in CASES.items():
            self.assertEqual(words.fix(a), b, a)
        self.assertIsNone(words.fix(None))

    def test_everywhere_saved(self):
        db = store.Store(os.path.join(tempfile.mkdtemp(), "w.db"))
        db.say(1, "assistant", "Запас сил низкий")
        db.say(1, "user", "что такое запас сил?")  # слова самого пользователя не трогаем
        self.assertEqual([m["text"] for m in db.history(1)], ["Гибкость пульса низкая", "что такое запас сил?"])
        db.brief(1, "2026-11-04", "morning", "запас сил 89")
        self.assertEqual(db.brief(1, "2026-11-04", "morning")["text"], "гибкость пульса 89")
        rid = f_reviews.add(db, 1, "2026-11-04", "morning", "Запас сил 89", "Вчера запас сил был средний", "09:00")
        r = db.q("SELECT title, text FROM reviews WHERE id=?", (rid,))[0]
        self.assertEqual((r["title"], r["text"]), ("Гибкость пульса 89", "Вчера гибкость пульса была средняя"))

    def test_claude_answer(self):
        with mock.patch.object(ai, "_run", wraps=None) as run:
            run.side_effect = lambda *a, **k: words.fix("запас сил упал")
            self.assertEqual(ai.claude("x"), "гибкость пульса упала")
        self.assertNotIn("запас", ai.trim_answer("Запас сил в норме")[0].lower())

    def test_code_has_no_old_name(self):
        root = os.path.dirname(os.path.abspath(__file__))
        bad = []
        for dp, _, fs in os.walk(root):
            if "__pycache__" in dp or "/data" in dp:
                continue
            for f in fs:
                if f.endswith((".py", ".js", ".md", ".html")) and f not in ("test_words.py", "words.py", "CLAUDE.md"):
                    with open(os.path.join(dp, f), encoding="utf-8") as fh:
                        txt = fh.read()
                    if txt != words.fix(txt):  # в коде и подсказках — ни одного старого названия
                        bad.append(f)
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
