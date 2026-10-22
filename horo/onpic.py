"""Ответы прямо на картинке задания (страница учебника или тетради) почерком Глеба — как он вписал бы ручкой.

Глеб, 22.10: «по испанскому надо сделать на скрине, а не в комментариях». Пустые строки для ответа (длинные
тонкие горизонтальные линии) находятся сами; писать можно и в любое место по координатам (V/F рядом с пунктом,
слово в пропуск). Не влезло на картинку (диалог, письмо, текст) — на лист `notes()` тем же почерком.

    import sys; sys.path.insert(0, 'horo')
    from onpic import Pic, notes
    p = Pic('horo/tmp/<task>/img1.png')
    print(p)                         # размер картинки и найденные строки: № x0–x1 y
    p.ruler('horo/tmp/<task>/ruler1.png')   # копия с сеткой координат — посмотреть через Read, куда писать
    p.line(0, 'Sí, he estado en un programa.')   # на строку №0 (текст сам уменьшается, чтобы влезть)
    p.write(1500, 420, 'V')          # левый край x, строка (низ букв) y — в пикселях p.img (как в print и ruler)
    p.save('horo/tmp/<task>/draft_files/1_page.jpg')
    notes('horo/tmp/<task>/draft_files/2_notes.jpg', ['Diálogo', 'A: ¿Qué te pasa?', 'B: Me duele la cabeza.'])
«ё», кавычки «», длинное тире и двоеточие заменяются сами (их нет в его алфавите или рисуются криво);
в диалоге — «A. Hola» или «A - Hola».

CLI: python3 horo/onpic.py <картинка> [ruler.png] — напечатать строки (и сохранить сетку).
"""
import sys

import numpy as np
from PIL import Image, ImageDraw

from hand import Hand

INK = (25, 125, 240)   # его синее перо на белом (как в table.py)
PEN = 2.4


def lines(img, min_frac=0.1, max_thick=5):
    """Пустые строки для ответа: длинные тонкие горизонтальные линии → [(x0, x1, y)] сверху вниз, слева направо."""
    g = np.asarray(img.convert('L'), dtype=np.int16)
    H, W = g.shape
    ink = (g < 228) & (g > 40)   # серые и тёмные линии на светлом фоне (чёрный текст — тоже ink, его отсекает толщина)
    out = []
    y = 0
    while y < H:
        row = ink[y]
        if row.mean() < min_frac * 0.5:
            y += 1
            continue
        # сплошные отрезки в строке
        d = np.diff(np.concatenate([[0], row.astype(np.int8), [0]]))
        starts, ends = np.where(d == 1)[0], np.where(d == -1)[0]
        found = False
        for s, e in zip(starts, ends):
            if e - s < W * min_frac:
                continue
            # тонкая: выше и ниже (через max_thick px) по этому отрезку почти пусто
            seg = slice(s, e)
            above = ink[max(0, y - 3), seg].mean() if y >= 3 else 0
            t = 1
            while y + t < H and ink[y + t, seg].mean() > 0.6:
                t += 1
            below = ink[min(H - 1, y + t + 2), seg].mean()
            # под строкой для ответа пусто (рамка-облачко с текстом внутри — не строка)
            under = ink[min(H - 1, y + t + 3):min(H, y + t + 10), seg]
            empty = under.size == 0 or under.mean() < 0.04
            # у рамки (облачко, таблица) на концах линии вертикальные стенки, у строки для ответа — нет
            def wall(x):
                xs = slice(max(0, x - 4), min(W, x + 5))
                up, down = ink[max(0, y - 14):max(0, y - 2), xs], ink[min(H, y + t + 2):min(H, y + t + 14), xs]
                return any(c.size and c.any(axis=1).mean() > 0.9 for c in (up, down))
            framed = wall(s) or wall(e - 1)
            if t <= max_thick and above < 0.3 and below < 0.3 and empty and not framed:
                out.append((int(s), int(e), int(y + t // 2)))
                found = True
        y += (max_thick + 2) if found else 1
    # одна линия, разбитая в нескольких соседних строках пикселей, — одна
    clean = []
    for ln in sorted(out, key=lambda v: (v[2], v[0])):
        if clean and abs(clean[-1][2] - ln[2]) <= 3 and abs(clean[-1][0] - ln[0]) < 20:
            continue
        clean.append(ln)
    # левая колонка сверху вниз, потом правая (правая — только длинные строки, начатые правее середины)
    return sorted(clean, key=lambda v: (v[0] > W * 0.45 and v[1] - v[0] > W * 0.3, v[2], v[0]))


class Pic:
    MIN_W = 1600   # мелкую картинку сначала увеличиваем: почерк чётче, перо не выходит жирным

    def __init__(self, path, ink=INK, seed=5, size=None):
        img = Image.open(path).convert('RGB')
        self.scale = max(1.0, self.MIN_W / img.size[0])
        if self.scale > 1:
            img = img.resize((round(img.size[0] * self.scale), round(img.size[1] * self.scale)), Image.LANCZOS)
        self.img = img
        self.lines = lines(self.img)
        self._probe = Hand(Image.new('RGB', (max(4000, self.img.size[0] * 2), 400)), seed=1)
        # один размер на всю страницу (как пишет человек): по свободному месту над строками, не крупнее шага строк
        gaps = sorted(b[2] - a[2] for a, b in zip(self.lines, self.lines[1:]) if 15 < b[2] - a[2] < 250)
        top = (gaps[len(gaps) // 2] * 0.62) if gaps else self.img.size[1] / 18
        free = sorted(self.free(i) for i in range(len(self.lines)))
        if free:
            top = min(top, free[len(free) // 2] * 0.8)
        self.size = int(size or min(60, max(22, top)))
        self.h = Hand(self.img, ink=ink, width=max(1.4, self.size * 0.058), seed=seed)

    def __str__(self):
        W, H = self.img.size
        rows = [f'картинка {W}×{H}' + (f' (увеличена в {self.scale:.2f} раза)' if self.scale > 1 else '') + f', размер почерка по умолчанию {self.size}, строк для ответа: {len(self.lines)}']
        rows += [f'  №{i}: x {x0}–{x1}, y {y}' for i, (x0, x1, y) in enumerate(self.lines)]
        return '\n'.join(rows)

    def width(self, s, size):
        return self._probe.text(s, 0, 200, size) * 1.08

    def write(self, x, y, text, size=None, maxw=None):
        """Текст от x, низ строки на y; не шире maxw (по умолчанию — до правого края): уменьшается, потом переносится.
        → размер, которым написано."""
        text = clean(text)
        size = size or self.size
        maxw = maxw or (self.img.size[0] - x - 10)
        s = size
        while s > size * 0.6 and self.width(text, s) > maxw:
            s -= 1
        if self.width(text, s) <= maxw:
            self.h.text(text, x, y, s)
            return s
        lines_, cur = [], ''
        for word in text.split():   # всё равно не влезло — по словам на несколько строк
            test = f'{cur} {word}'.strip()
            if cur and self.width(test, s) > maxw:
                lines_.append(cur)
                cur = word
            else:
                cur = test
        lines_.append(cur)
        for i, ln in enumerate(lines_):
            self.h.text(ln, x, y + i * s * 1.3, s)
        return s

    def free(self, i):
        """Сколько пикселей свободно над строкой №i (до текста задания выше) — чтобы ответ не налез на вопрос."""
        x0, x1, y = self.lines[i]
        g = np.asarray(self.img.convert('L'), dtype=np.int16)[:, x0:x1]
        k = 4
        while y - k > 0 and k < 200 and (g[y - k] < 200).mean() < 0.004:
            k += 1
        return k

    def line(self, i, text, size=None):
        """На найденную строку №i (см. print): над линией, с небольшим отступом слева."""
        x0, x1, y = self.lines[i]
        return self.write(x0 + 12, y - 5, text, size, maxw=x1 - x0 - 20)

    def ruler(self, path, step=100):
        """Копия картинки с сеткой координат (через step px) и номерами найденных строк — для выбора места."""
        im = self.img.copy()
        d = ImageDraw.Draw(im)
        W, H = im.size
        for x in range(0, W, step):
            d.line([x, 0, x, H], fill=(255, 0, 0) if x % 500 == 0 else (255, 170, 170), width=1)
            d.text((x + 2, 2), str(x), fill=(200, 0, 0))
        for y in range(0, H, step):
            d.line([0, y, W, y], fill=(255, 0, 0) if y % 500 == 0 else (255, 170, 170), width=1)
            d.text((2, y + 2), str(y), fill=(200, 0, 0))
        for i, (x0, x1, y) in enumerate(self.lines):
            d.line([x0, y, x1, y], fill=(0, 170, 0), width=3)
            d.text((x1 - 40, y - 14), f'№{i}', fill=(0, 140, 0))
        im.save(path)
        return path

    def save(self, path):
        self.h.save(path)
        return path


def clean(s):
    """Знаки, которых нет в его алфавите или которые рисуются криво: «ё», кавычки «», длинное тире, двоеточие."""
    s = str(s).replace('ё', 'е').replace('Ё', 'Е').replace('«', '').replace('»', '').replace('—', '-').replace('–', '-')
    return s.replace(': ', ' - ').replace(':', ' -')


def notes(path, paragraphs, size=48, width=1768):
    """Длинный ответ (диалог, письмо, текст), которому нет места на картинке: белый лист в линейку, его почерком.
    paragraphs — строки; длинные переносятся по словам. → путь картинки."""
    probe = Hand(Image.new('RGB', (4000, 400)), seed=1)
    w = lambda s: probe.text(s, 0, 200, size) * 1.08   # noqa: E731
    left, maxw, lh = 90, width - 180, size * 1.75
    rows = []
    for p in paragraphs:
        cur = ''
        for word in clean(p).split():
            test = f'{cur} {word}'.strip()
            if cur and w(test) > maxw:
                rows.append(cur)
                cur = word
            else:
                cur = test
        rows.append(cur)
    H = int(lh * (len(rows) + 2) + size)
    im = Image.new('RGB', (width, H), (255, 255, 255))
    d = ImageDraw.Draw(im)
    for k in range(1, len(rows) + 2):
        y = int(size * 0.6 + lh * k)
        d.line([40, y, width - 40, y], fill=(200, 214, 230), width=2)
    h = Hand(im, ink=INK, width=PEN * 1.2, seed=11)
    for k, r in enumerate(rows):
        if r:
            h.text(r, left, size * 0.6 + lh * (k + 1) - 8, size)
    h.save(path)
    return path


if __name__ == '__main__':
    p = Pic(sys.argv[1])
    print(p)
    if len(sys.argv) > 2:
        p.ruler(sys.argv[2])
