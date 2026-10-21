"""Вопрос «сопоставь» в submit.js (horo/matching.js): новая попытка, досдача с готовыми парами, неверные пары.

Сам тест — horo/test_matching.js (Playwright, страница-имитация сайта); здесь он запускается вместе с другими тестами.
python3 -m unittest horo/test_matching.py
"""
import os
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


class MatchingTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "нет node")
    def test_matching(self):
        r = subprocess.run(["node", os.path.join(HERE, "test_matching.js")], capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.count("ok "), 4, r.stdout)


if __name__ == "__main__":
    unittest.main()
