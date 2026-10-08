"""Почерк Глеба (Apple Pencil, ярко-голубой, печатные буквы) поверх картинки: разный размер и наклон букв, гуляющая строка, неровный нажим, рукописные дроби."""
import math, random
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import os
FONT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fonts', 'Neucha.ttf')
_fonts = {}
def F(sz, wght=520):
    k = (sz, wght)
    if k not in _fonts:
        f = ImageFont.truetype(FONT, sz)
        try: f.set_variation_by_axes([wght])
        except Exception: pass
        _fonts[k] = f
    return _fonts[k]

class Hand:
    def __init__(self, img, ink=(70, 150, 255), seed=1):
        random.seed(seed)
        self.img = img.convert('RGBA')
        self.layer = Image.new('RGBA', img.size, (0, 0, 0, 0))
        self.ink = ink

    def _ink(self):
        r, g, b = self.ink; j = random.randint(-10, 10)
        return (max(0, r + j), max(0, g + j), min(255, b + j), random.randint(225, 255))

    def glyph(self, ch, x, base, size):
        sz = max(10, int(size * random.uniform(0.85, 1.18)))
        f = F(sz, random.choice([480, 520, 560, 600]))
        w = int(f.getlength(ch)) + 12; h = int(sz * 1.5)
        t = Image.new('RGBA', (w + 10, h), (0, 0, 0, 0))
        ImageDraw.Draw(t).text((5, 2), ch, font=f, fill=self._ink())
        t = t.rotate(random.uniform(-7, 6), resample=Image.BICUBIC, expand=True)
        self.layer.alpha_composite(t, (int(x - 5), int(base - sz * 1.0 + random.uniform(-2, 2))))
        return f.getlength(ch)

    def text(self, s, x, base, size=36):
        drift, phase, amp = 0, random.uniform(0, 6), random.uniform(3, 7)
        tilt = random.uniform(-0.05, 0.035)
        x0 = x
        for i, ch in enumerate(s):
            drift += random.uniform(-0.6, 0.6)
            y = base + amp * math.sin(phase + i * 0.22) + drift * 0.6 + (x - x0) * tilt
            if ch in '²³':  # степень: маленькая цифра сверху
                x += self.glyph('2' if ch == '²' else '3', x + 1, y - size * 0.42, int(size * 0.6)) + 2
                continue
            ch = {'−': '-', '–': '-', '≠': '≠'}.get(ch, ch)
            x += self.glyph(ch, x, y, size) + (random.uniform(6, 14) if ch == ' ' else random.uniform(0.5, 4)) * size / 36
        return x

    def stroke(self, pts, width=3):
        d = ImageDraw.Draw(self.layer)
        pts = [(px + random.uniform(-1, 1), py + random.uniform(-1, 1)) for px, py in pts]
        d.line(pts, fill=self._ink(), width=width, joint='curve')

    def hline(self, x1, x2, y):
        n = 8; self.stroke([(x1 + (x2 - x1) * i / n, y + math.sin(i) * 1.2 + (i * 0.25)) for i in range(n + 1)], 3)

    def frac(self, num, den, x, base, size=32):
        L = lambda t: (F(size).getlength(t.replace('−', '-').replace('²', '2')) * 1.1
                       + len(t) * 2.4 * size / 36 + t.count(' ') * 9 * size / 36)
        wn, wd = L(num), L(den)
        w = max(wn, wd) + 14
        self.text(num, x + (w - wn) / 2, base - size * 0.55, size)
        self.hline(x, x + w, base - size * 0.33)
        self.text(den, x + (w - wd) / 2, base + size * 0.62, size)
        return x + w + 6

    def expr(self, items, x, base, size=36):
        """items: строки и кортежи (числитель, знаменатель)"""
        for it in items:
            x = self.text(it, x, base, size) if isinstance(it, str) else self.frac(it[0], it[1], x, base, int(size * 0.88))
            x += 4
        return x

    def circle(self, cx, cy, rx, ry, color=(205, 40, 40)):
        pts, st = [], random.uniform(0, math.pi)
        for i in range(72):
            a = st + i / 60 * 2 * math.pi; r = 1 + random.uniform(-.05, .05)
            pts.append((cx + rx * r * math.cos(a), cy + ry * r * math.sin(a)))
        ImageDraw.Draw(self.layer).line(pts, fill=color + (235,), width=4, joint='curve')

    def save(self, path):
        lay = self.layer.filter(ImageFilter.GaussianBlur(0.35))
        Image.alpha_composite(self.img, lay).convert('RGB').save(path, quality=90)
