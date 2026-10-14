"""Тесты horo/table.py: клетки таблицы находятся сами, текст вписывается и влезает. python3 -m unittest horo/test_table.py"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from PIL import Image, ImageDraw  # noqa: E402

import table  # noqa: E402


def drawn(path, bottom=True):
    """Таблица 3 столбца × 2 строки; bottom=False — продолжается на следующей картинке (нет нижней линии)."""
    im = Image.new('RGB', (1200, 600), (255, 255, 255))
    d = ImageDraw.Draw(im)
    for y in ([50, 300, 550] if bottom else [50, 300]):
        d.line([20, y, 1180, y], fill=(0, 0, 0), width=3)
    for x in (20, 400, 790, 1180):
        d.line([x, 50, x, 550 if bottom else 599], fill=(0, 0, 0), width=3)
    im.save(path)


class TableTest(unittest.TestCase):
    def test_grid(self):
        with tempfile.TemporaryDirectory() as d:
            drawn(os.path.join(d, 't.png'))
            t = table.Table(os.path.join(d, 't.png'))
            self.assertEqual((t.rows, t.cols), (2, 3))
            x0, y0, x1, y1 = t.cell(1, 2)
            self.assertAlmostEqual(x0, 790, delta=3)
            self.assertAlmostEqual(y1, 550, delta=3)

    def test_table_goes_on_next_picture(self):
        with tempfile.TemporaryDirectory() as d:
            drawn(os.path.join(d, 't.png'), bottom=False)
            t = table.Table(os.path.join(d, 't.png'))
            self.assertEqual(t.rows, 2)  # последняя строка — до края картинки
            self.assertEqual(t.cell(1, 0)[3], 599)

    def test_write_fits_cell(self):
        with tempfile.TemporaryDirectory() as d:
            drawn(os.path.join(d, 't.png'))
            t = table.Table(os.path.join(d, 't.png'))
            long = 'любит Машу искренне пишет ей стихи дерется за нее на дуэли и спасает из плена ' * 2
            s = t.write(1, 1, long)
            self.assertLess(s, 44)  # длинный текст — мельче, но влез
            out = t.save(os.path.join(d, 'out.jpg'))
            self.assertEqual(Image.open(out).size, (1200, 600))
            im = Image.open(out).convert('RGB')
            x0, y0, x1, y1 = t.cell(1, 1)
            blue = sum(1 for x in range(x0 + 5, x1 - 5, 4) for y in range(y0 + 5, y1 - 5, 4)
                       if im.getpixel((x, y))[2] > 180 and im.getpixel((x, y))[0] < 120)
            self.assertGreater(blue, 50)  # в клетке написано синим


if __name__ == '__main__':
    unittest.main()
