"""Заполнить таблицу из условия (картинку) почерком Глеба — прямо в клетках, как он вписал бы сам.

Клетки находятся сами по линиям таблицы (тёмные длинные линии). Таблица может продолжаться на следующей
картинке без верхней или нижней рамки — край картинки тогда считается границей строки.

    import sys; sys.path.insert(0, 'horo')
    from table import Table
    t = Table('horo/tmp/<task>/img1.png')
    print(t)                       # строки × столбцы и размеры клеток — чтобы понять, куда что писать
    t.write(1, 1, 'учился дома, учил Савельич')   # строка 1, столбец 1 (с нуля; строка 0 — шапка, столбец 0 — подписи)
    t.save('horo/tmp/<task>/draft_files/1_table.jpg')
Текст сам переносится по словам и уменьшается, чтобы влезть в клетку. «ё», кавычки «» и длинное тире его
алфавит не знает — пишем «е», без кавычек и «-».

CLI: python3 horo/table.py <картинка> — напечатать сетку.
"""
import random
import sys

import numpy as np
from PIL import Image

from hand import Hand

INK = (25, 125, 240)   # его синее перо на белом (как на скриншотах HDP)
PEN = 2.4   # как у него на листе алфавита: штрих ~1/9 высоты буквы (3.4 выходило жирно)


def _runs(mask, min_gap=4):
    """Индексы True → середины сплошных отрезков (линия толщиной в несколько px — одна)."""
    idx = np.flatnonzero(mask)
    if not len(idx):
        return []
    out, start, prev = [], idx[0], idx[0]
    for i in idx[1:]:
        if i - prev > min_gap:
            out.append((start + prev) // 2)
            start = i
        prev = i
    out.append((start + prev) // 2)
    return out


def grid(img):
    """(xs, ys) — координаты вертикальных и горизонтальных линий таблицы."""
    g = np.asarray(img.convert('L'), dtype=np.int16)
    dark = g < 110
    H, W = dark.shape
    ys = _runs(dark.mean(axis=1) > 0.5)                          # строка почти целиком тёмная — горизонталь
    top, bot = (ys[0], ys[-1]) if ys else (0, H - 1)
    span = dark[top:bot + 1] if bot - top > 20 else dark
    xs = _runs(span.mean(axis=0) > 0.6)                          # столбец тёмный по всей высоте таблицы — вертикаль
    if xs:
        # вертикали уходят за верхнюю/нижнюю горизонталь до края — таблица продолжается на соседней картинке
        col = dark[:, xs[0]]
        if ys and col[min(H - 1, bot + 8):].mean() > 0.9:
            ys.append(H - 1)
        if ys and col[:max(1, top - 8)].mean() > 0.9:
            ys.insert(0, 0)
        if not ys:
            ys = [0, H - 1]
    clean = lambda v: [x for i, x in enumerate(v) if i == 0 or x - v[i - 1] > 20]  # рамка у самого края — не строка
    return clean(xs), clean(ys)


class Table:
    def __init__(self, path, ink=INK, seed=7):
        self.img = Image.open(path).convert('RGB')
        self.xs, self.ys = grid(self.img)
        self.h = Hand(self.img, ink=ink, width=PEN, seed=seed, mess=1.15, slant=0.04)
        self.rnd = random.Random(seed)
        W, H = self.img.size
        self._probe = Hand(Image.new('RGB', (max(4000, W * 2), 400)), seed=1)

    @property
    def rows(self):
        return max(0, len(self.ys) - 1)

    @property
    def cols(self):
        return max(0, len(self.xs) - 1)

    def cell(self, r, c):
        return self.xs[c], self.ys[r], self.xs[c + 1], self.ys[r + 1]

    def __str__(self):
        out = [f'{self.rows} строк × {self.cols} столбцов']
        for r in range(self.rows):
            out.append(f'  строка {r}: высота {self.ys[r + 1] - self.ys[r]} px, столбцы ' +
                       ', '.join(f'{c}: {self.xs[c + 1] - self.xs[c]} px' for c in range(self.cols)))
        return '\n'.join(out)

    def _w(self, s, size):
        return self._probe.text(s, 0, 200, size) * 1.08

    def _wrap(self, text, size, maxw):
        lines, cur = [], ''
        for word in text.split():
            test = f'{cur} {word}'.strip()
            if cur and self._w(test, size) > maxw:
                lines.append(cur)
                cur = word
            else:
                cur = test
        if cur:
            lines.append(cur)
        return lines

    def write(self, r, c, text, size=None):
        """Вписать текст в клетку (строка r, столбец c): по словам, крупно, но так, чтобы влез."""
        x0, y0, x1, y1 = self.cell(r, c)
        pad = 14
        maxw, maxh = x1 - x0 - 2 * pad, y1 - y0 - 2 * pad
        for s in ([size] if size else range(44, 15, -2)):
            lines = self._wrap(text, s, maxw)
            if len(lines) * s * 1.3 <= maxh and all(self._w(l, s) <= maxw for l in lines):
                break
        lh = s * 1.3
        y = y0 + pad + s * 0.95
        for line in lines:
            self.h.text(line, x0 + pad + self.rnd.uniform(-3, 5), y + self.rnd.uniform(-2, 2), s)
            y += lh
        return s

    def save(self, path):
        self.h.save(path)
        return path


if __name__ == '__main__':
    for p in sys.argv[1:]:
        print(p)
        print(Table(p))
