"""Проверка алфавита: шаблон -> «написали» буквы -> сжали как Telegram -> разобрали.
usage: python3 -m unittest horo/test_alphabet.py
"""
import os
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import alphabet as A


def fill(page, skip=()):
    """Шаблон, в котором синим «написаны» все буквы страницы, кроме skip."""
    with tempfile.TemporaryDirectory() as d:
        A.template(page, os.path.join(d, 't.png'))
        im = Image.open(os.path.join(d, 't.png')).convert('RGB')
    dr = ImageDraw.Draw(im)
    f = ImageFont.truetype(A.FONT, 150)
    for i, ch in enumerate(A.PAGES[page][1]):
        if ch in skip:
            continue
        x0, x1, y0, base = A.cell(i)
        dr.text((x0 + 70, base), ch, font=f, fill=(90, 160, 240), anchor='ls')
    return im


def as_telegram(im, angle=0):
    """Скриншот с полями iPad, лёгкий поворот, уменьшение до 1280 и JPEG."""
    canvas = Image.new('RGB', (im.width + 300, im.height + 500), (0, 0, 0))
    canvas.paste(im, (150, 320))
    canvas = canvas.rotate(angle, expand=False, fillcolor=(0, 0, 0))
    k = 1280 / max(canvas.size)
    canvas = canvas.resize((int(canvas.width * k), int(canvas.height * k)), Image.LANCZOS)
    path = tempfile.mktemp(suffix='.jpg')
    canvas.save(path, quality=80)
    return path


class AlphabetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        A.EXTRA = os.path.join(self.tmp.name, 'extra.npz')

    def tearDown(self):
        self.tmp.cleanup()

    def test_roundtrip_page1(self):
        path = as_telegram(fill(0, skip='Жж'), angle=1.5)
        added, errors = A.ingest([path])
        self.assertEqual(errors, [])
        self.assertEqual(''.join(added), A.PAGES[0][1].replace('Ж', '').replace('ж', ''))
        z = np.load(A.EXTRA)
        big, small = z[f'{ord("П")}_0'], z[f'{ord("п")}_0']
        self.assertGreater(big[:, 1].max(), small[:, 1].max() + 0.3)  # большая выше маленькой — клетки не перепутаны
        z = np.load(A.EXTRA)
        pts, w = z[f'{ord("п")}_0'], float(z[f'{ord("п")}_0w'])
        # «п» строчная: высота около 1 XH, стоит на строке
        self.assertAlmostEqual(pts[:, 1].max(), 1.0, delta=0.35)
        self.assertGreater(pts[:, 1].min(), -0.15)
        self.assertGreater(w, 0.4)

    def test_page_detected_and_variants_append(self):
        p2 = as_telegram(fill(3))
        A.ingest([p2])
        added, _ = A.ingest([p2])
        self.assertIn('7', added)
        z = np.load(A.EXTRA)
        self.assertIn(f'{ord("7")}_1', z.files)  # второй вариант буквы, первый не затёрт

    def test_empty_and_garbage(self):
        empty = as_telegram(fill(2, skip=A.PAGES[2][1]))
        added, errors = A.ingest([empty])
        self.assertEqual((added, errors), ([], []))
        junk = tempfile.mktemp(suffix='.png')
        Image.new('RGB', (500, 500), (255, 255, 255)).save(junk)
        added, errors = A.ingest([junk])
        self.assertEqual(added, [])
        self.assertIn('метки', errors[0])


class OldSheetTest(unittest.TestCase):
    def test_old_alphabet_sheet_is_rejected(self):
        im = fill(0)
        d = ImageDraw.Draw(im)
        for k, (cx, cy) in enumerate(A.bits()):  # метки как у старого листа №1
            col = A.MAGENTA if k == 0 else A.BG
            d.rectangle([cx - A.BIT / 2, cy - A.BIT / 2, cx + A.BIT / 2, cy + A.BIT / 2], fill=col)
        with tempfile.TemporaryDirectory() as t:
            A.EXTRA = os.path.join(t, 'x.npz')
            added, errors = A.ingest([as_telegram(im)])
        self.assertEqual(added, [])
        self.assertIn('старого алфавита', errors[0])


class StyleTest(unittest.TestCase):
    def test_style_file_changes_hand(self):
        import hand, json
        from pathlib import Path
        with tempfile.TemporaryDirectory() as t:
            hand.STYLE_FILE = os.path.join(t, 's.json')
            Path(hand.STYLE_FILE).write_text(json.dumps({'mess': 1.5, 'width': 2, 'size': 1.2}))
            h = hand.Hand(Image.new('RGB', (10, 10)), width=4, mess=1.0)
        self.assertEqual((h.m, h.r, h.k), (1.5, 4 * 2 * hand.SS / 2, 1.2))

    def test_latin_letter_uses_russian_glyph(self):
        import hand
        bank = dict(hand.BANK)
        try:
            hand.BANK.clear()
            hand.BANK['о'] = [(np.array([[0, 0], [1, 1]], np.float32), 1.0)]
            lay, _ = hand.Hand(Image.new('RGB', (10, 10)))._layout('o', 44)
            self.assertIn('real', lay[0][4])  # латинская o нарисована его русской о
        finally:
            hand.BANK.clear(); hand.BANK.update(bank)


if __name__ == '__main__':
    unittest.main()
