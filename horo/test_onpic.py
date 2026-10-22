"""Ответы на картинке задания (horo/onpic.py): строки для ответа находятся, рамки — нет, почерк ложится на место."""
import os
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(__file__))
import onpic  # noqa: E402


def page():
    """Страница тетради: два вопроса со строками, облачко-рамка с текстом, короткий пропуск."""
    im = Image.new('RGB', (1800, 700), 'white')
    d = ImageDraw.Draw(im)
    d.rectangle([900, 20, 1500, 110], outline=(190, 190, 190), width=2)   # облачко: рамка, не строка
    d.text((920, 50), 'Ejemplo', fill='black')
    for y in (250, 320, 500, 570):
        d.line([100, y, 850, y], fill=(200, 200, 200), width=2)          # строки для ответа
    d.line([1000, 600, 1200, 600], fill=(200, 200, 200), width=2)        # пропуск
    return im


class OnPicTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, 'img1.png')
        page().save(self.path)

    def tearDown(self):
        self.dir.cleanup()

    def test_lines_found_frame_skipped(self):
        found = onpic.lines(Image.open(self.path))
        self.assertEqual([(round(x0, -1), round(y, -1)) for x0, _, y in found][:4], [(100, 250), (100, 320), (100, 500), (100, 570)])
        self.assertEqual(len(found), 5)
        self.assertFalse(any(y < 150 for *_, y in found))   # рамка облачка — не строка

    def test_write_on_line_and_save(self):
        p = onpic.Pic(self.path)
        p.line(0, 'Sí, lo he visto.')
        out = p.save(os.path.join(self.dir.name, 'out.jpg'))
        a = np.asarray(Image.open(out).convert('RGB'), np.int16)
        x0, x1, y = p.lines[0]
        band = a[y - 60:y, x0:x1]
        blue = (band[..., 2] - band[..., 0]) > 60
        self.assertGreater(blue.sum(), 200)                  # синие чернила над строкой
        self.assertLess(((a[:y - 80, :, 2] - a[:y - 80, :, 0]) > 60).sum(), 50)   # выше ничего не налезло

    def test_small_picture_scaled_and_notes(self):
        Image.open(self.path).resize((900, 350)).save(self.path)
        p = onpic.Pic(self.path)
        self.assertGreater(p.scale, 1)
        self.assertEqual(p.img.size[0], onpic.Pic.MIN_W)
        n = onpic.notes(os.path.join(self.dir.name, 'n.jpg'), ['A: Hola, ¿qué te pasa?', 'B: Me duele la cabeza.'])
        self.assertTrue(os.path.getsize(n) > 1000)
        self.assertEqual(onpic.clean('«ё» — A: hola'), 'е - A - hola')


if __name__ == '__main__':
    unittest.main()
