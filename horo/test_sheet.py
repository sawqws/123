"""Тесты horo/sheet.py: раскладка решения (скрин — ответы, лист — решение). python3 -m unittest horo/test_sheet.py"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from PIL import Image  # noqa: E402

import sheet  # noqa: E402

LONG = [('x − 12a', '(x − 4a)(x + 4a)'), ' + ', ('4a', 'x(x − 4a)'), ' = ',
        ('x² − 12ax + 4ax + 16a²', 'x(x − 4a)(x + 4a)'), ' = ', ('(x − 4a)²', 'x(x − 4a)(x + 4a)'), ' = ',
        ('x − 4a', 'x(x + 4a)')]


def layout():
    """Карточка задания: заголовок, примеры 1) и 2) — дробь-картинка над номером."""
    r = lambda x0, y0, x1, y1, t: dict(x0=x0, y0=y0, x1=x1, y1=y1, t=t)
    return {'width': 1768, 'height': 900, 'rects': [
        r(40, 20, 900, 70, 'Выполните сложение дробей'),
        r(40, 200, 80, 240, '1)'), r(100, 150, 600, 260, '[img]'),
        r(40, 500, 80, 540, '2)'), r(100, 450, 700, 560, '[img]'),
    ]}


class Sheet(unittest.TestCase):
    def test_chunks_split_on_equals(self):
        ch = sheet.chunks(LONG)
        self.assertEqual(len(ch), 4)
        self.assertTrue(all(c[0].startswith('= ') for c in ch[1:]))
        self.assertEqual(ch[-1][-1], ('x − 4a', 'x(x + 4a)'))

    def test_pack_fits_width(self):
        maxw = 1300
        rows = sheet.pack(LONG, sheet.SHEET_SIZE, maxw)
        self.assertGreater(len(rows), 1)
        for r in rows[:-1]:
            self.assertLessEqual(sheet.width(r, sheet.SHEET_SIZE), maxw)

    def test_items_from_layout(self):
        it = sheet.items(layout())
        self.assertEqual(sorted(it), [1, 2])
        self.assertEqual(it[1]['x'], 660)          # правее картинки примера
        self.assertEqual(it[2]['x'], 760)
        self.assertLess(it[1]['y'], it[2]['y'])
        self.assertGreater(it[1]['y'], 70)         # заголовок задания к примеру 1 не относится

    def test_save_keeps_screenshot_and_makes_sheets(self):
        with tempfile.TemporaryDirectory() as d:
            Image.new('RGB', (1768, 900), (28, 28, 30)).save(os.path.join(d, 'shot.png'))
            json.dump(layout(), open(os.path.join(d, 'shot.json'), 'w'))
            w = sheet.Work(d)
            w.solve(1, [LONG])
            w.solve(2, [LONG] * 12)               # длинное — на вторую страницу
            files = w.save()
            names = [os.path.basename(f) for f in files]
            self.assertEqual(names[0], '1_task.jpg')
            self.assertTrue(all(n.endswith('_sheet.jpg') for n in names[1:]))
            self.assertGreaterEqual(len(names), 3)
            self.assertEqual(Image.open(files[0]).size, (1768, 900))   # скрин не обрезан и не расширен
            self.assertEqual(Image.open(files[1]).size, (sheet.PAGE_W, sheet.PAGE_H))
            shot = Image.open(files[0]).convert('RGB')
            self.assertNotEqual(shot.getpixel((20, 20)), (255, 255, 255))   # фон скрина тёмный, не лист

    def test_answer_false_and_no_shot(self):
        with tempfile.TemporaryDirectory() as d:
            w = sheet.Work(d)
            w.solve(None, ['AB = CD'], answer=False)
            files = w.save()
            self.assertEqual([os.path.basename(f) for f in files], ['1_sheet.jpg'])


if __name__ == '__main__':
    unittest.main()
