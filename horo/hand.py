"""Почерк Глеба поверх картинки (Apple Pencil на iPad, по его образцам).

Что видно на образцах: тонкая ровная линия (~3 px, нажим не меняется), синий цвет,
простые печатные буквы с наклоном вправо, буквы разного размера, строка слегка
уходит вверх или вниз, дробь — одна длинная чуть кривая черта, «=» короткое и косое.

Как сделано: буква берётся из шрифта Neucha, от неё остаётся только осевая линия
(скелет), потом каждая копия буквы по-своему искажается (наклон, масштаб, плавная
деформация) и обводится тонким «пером». Поэтому одинаковые буквы не повторяются.
Рисуем в 3-кратном разрешении и уменьшаем: края мягкие, как у скриншота с iPad.

Пример:
    h = Hand(img, seed=1)
    h.expr(['= ', ('a·a+2·b', 'ab'), ' = ', ('a²+2b', 'ab')], x, y, 44)
    h.circle(cx, cy, 34, 30)
    h.save('out.jpg')
"""
import math, os, random
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage
from skimage.morphology import skeletonize

FONT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fonts', 'Neucha.ttf')
R = 200   # размер шрифта, из которого берём скелет
SS = 3    # во сколько раз рисуем крупнее, чем картинка
INK = (25, 125, 240)  # цвет пера с его скриншотов

_font = ImageFont.truetype(FONT, R)
_skel = {}


def skeleton(ch):
    """Точки осевой линии буквы (x вправо, y вниз от базовой линии) и ширина буквы, в единицах R."""
    if ch not in _skel and ch in '()':
        # у шрифта скобки почти прямые, а у Глеба круглые: рисуем дугу сами
        t = np.linspace(0, 1, 500)
        b = np.sin(math.pi * t)
        u = 0.07 * R + 0.15 * R * ((1 - b) if ch == '(' else b)
        _skel[ch] = (np.stack([u, -0.8 * R + 1.02 * R * t], 1), 0.32 * R)
    if ch not in _skel and ch == '6':
        # шестёрка из шрифта после скелета похожа на «б»: дуга сверху вниз и петля
        t = np.linspace(0, 1, 200)[:, None]
        p0, p1, p2 = np.array([0.4, -0.72]), np.array([0.17, -0.48]), np.array([0.09, -0.2])
        top = (1 - t) ** 2 * p0 + 2 * (1 - t) * t * p1 + t ** 2 * p2  # круто вниз, без «флажка» как у «б»
        a = np.linspace(math.pi, 3 * math.pi, 300)
        loop = np.stack([0.27 + 0.18 * np.cos(a), -0.2 + 0.2 * np.sin(a)], 1)
        _skel[ch] = (np.vstack([top, loop]) * R, 0.5 * R)
    if ch not in _skel and ch == 'a':
        # «а» как у Глеба: кружок и палочка справа (у шрифта она невнятная)
        a = np.linspace(-0.3, -2 * math.pi + 0.1, 300)
        bowl = np.stack([0.18 + 0.15 * np.cos(a), -0.24 + 0.24 * np.sin(a)], 1)
        t = np.linspace(0, 1, 150)
        stem = np.stack([0.34 + 0.01 * t + 0.05 * t ** 6, -0.5 + 0.5 * t], 1)
        _skel[ch] = (np.vstack([bowl, stem]) * R, 0.45 * R)
    if ch not in _skel:
        asc, desc = _font.getmetrics()
        w = int(_font.getlength(ch)) + 40
        im = Image.new('L', (w, asc + desc + 40), 0)
        ImageDraw.Draw(im).text((20, 20 + asc), ch, font=_font, fill=255, anchor='ls')
        sk = skeletonize(np.array(im) > 110)
        ys, xs = np.nonzero(sk)
        pts = np.stack([xs - 20.0, ys - 20.0 - asc], 1)
        _skel[ch] = (pts, _font.getlength(ch))
    return _skel[ch]


class Noise:
    """Плавный шум без периода: сумма синусов со случайными несоизмеримыми частотами."""
    def __init__(self, n=3, f=(0.5, 2.5)):
        self.c = [(random.uniform(*f), random.uniform(0, 7), random.uniform(0.4, 1)) for _ in range(n)]
        self.k = 1 / sum(a for _, _, a in self.c)

    def __call__(self, t):
        return self.k * sum(a * math.sin(fr * t + ph) for fr, ph, a in self.c)


class Hand:
    def __init__(self, img, ink=INK, seed=1, width=2.8, mess=1.0, slant=0.19):
        """mess: насколько небрежно (0.4 аккуратно, 1 обычно, 2 очень криво); slant: наклон букв вправо."""
        random.seed(seed); np.random.seed(seed)
        self.m, self.sl = mess, slant
        self.img = img.convert('RGB')
        W, H = self.img.size
        self.mask = np.zeros((H * SS, W * SS), bool)
        self.ink = ink
        self.r = width * SS / 2

    # ---------- перо ----------
    def _dots(self, pts):
        """Отмечает точки (в координатах картинки) на холсте пера."""
        p = np.round(np.asarray(pts) * SS).astype(int)
        H, W = self.mask.shape
        ok = (p[:, 0] >= 0) & (p[:, 0] < W) & (p[:, 1] >= 0) & (p[:, 1] < H)
        self.mask[p[ok, 1], p[ok, 0]] = True

    def stroke(self, pts):
        """Линия пера через точки, плотно заполненная."""
        out = []
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            n = max(2, int(math.hypot(x2 - x1, y2 - y1) * SS))
            out += [(x1 + (x2 - x1) * i / n, y1 + (y2 - y1) * i / n) for i in range(n + 1)]
        self._dots(out)

    # ---------- буквы ----------
    def _layout(self, s, size):
        """Раскладка строки: список (символ, x, dy, масштаб, параметры искажения) и ширина."""
        k = size / R
        out, x = [], 0.0
        for ch in s:
            if ch == ' ':
                x += size * random.uniform(0.36 - 0.08 * self.m, 0.36 + 0.09 * self.m); continue
            sup = ch in '²³'
            c = {'²': '2', '³': '3', '−': '-', '–': '-'}.get(ch, ch)
            m = self.m
            sc = k * random.uniform(1 - 0.1 * m, 1 + 0.1 * m) * (0.62 if sup else 1)
            pts, adv = skeleton(c)
            dy = -size * 0.55 if sup else 0
            if sup: x += size * 0.08
            if c == '-': sc *= random.uniform(1.0, 1.25)        # минус у него длинный
            if c == '=': sc *= random.uniform(0.85, 1.0)
            prm = dict(sx=random.uniform(1 - 0.12 * m, 1 + 0.12 * m), sy=random.uniform(1 - 0.1 * m, 1 + 0.12 * m),
                       slant=self.sl + random.uniform(-0.07, 0.07) * m, rot=random.uniform(-0.06, 0.06) * m,
                       nx=Noise(2, (0.02, 0.06)), ny=Noise(2, (0.02, 0.06)),
                       amp=random.uniform(4, 9) * m * (0.25 if c in '()-=+' else 1))  # короткие штрихи не гнём
            out.append((c, x, dy, sc, prm))
            x += adv * sc * random.uniform(1.04 - 0.06 * m, 1.04 + 0.06 * m) + size * random.uniform(0.065 - 0.035 * m, 0.065 + 0.035 * m)
        return out, x

    def _draw(self, lay, x0, base, size):
        line = Noise(3, (0.004, 0.012))
        m = self.m
        tilt = random.uniform(-0.025, 0.02) * m
        amp = size * random.uniform(0.03, 0.07) * m
        for c, x, dy, sc, p in lay:
            pts, _ = skeleton(c)
            u, v = pts[:, 0], pts[:, 1]
            # плавная деформация буквы (в единицах R), своя у каждой копии
            u = u + p['amp'] * np.array([p['nx'](a + b) for a, b in zip(u, v)])
            v = v + p['amp'] * np.array([p['ny'](a - b) for a, b in zip(u, v)])
            u, v = u * p['sx'], v * p['sy']
            u = u - v * p['slant']                       # наклон вправо
            cr, sr = math.cos(p['rot']), math.sin(p['rot'])
            u, v = u * cr - v * sr, u * sr + v * cr
            gx = x0 + x
            gy = base + dy + amp * line(gx) + (gx - x0) * tilt + random.uniform(-0.04, 0.04) * m * size
            self._dots(np.stack([gx + u * sc, gy + v * sc], 1))

    def text(self, s, x, base, size=44):
        lay, w = self._layout(s, size)
        self._draw(lay, x, base, size)
        return x + w

    def width(self, s, size):
        st = random.getstate(); w = self._layout(s, size)[1]; random.setstate(st)
        return w

    # ---------- дроби ----------
    def frac(self, num, den, x, base, size=40):
        ln, wn = self._layout(num, size)
        ld, wd = self._layout(den, size)
        w = max(wn, wd) + size * random.uniform(0.25, 0.5)
        y = base - size * 0.3
        self._draw(ln, x + (w - wn) / 2 + random.uniform(-4, 4), y - size * 0.3, size)
        self._draw(ld, x + (w - wd) / 2 + random.uniform(-4, 4), y + size * 1.0, size)
        # черта: одна линия, чуть выгнутая и косая, концы неровные
        x1, x2 = x + random.uniform(-3, 3), x + w + random.uniform(-2, 6)
        y1, y2 = y + random.uniform(-2, 2), y + random.uniform(-3, 4)
        bow = random.uniform(-3, 3) * self.m
        self.stroke([(x1 + (x2 - x1) * t, y1 + (y2 - y1) * t + bow * math.sin(math.pi * t)) for t in np.linspace(0, 1, 30)])
        return x + w + size * 0.15

    def expr(self, items, x, base, size=44):
        """items: строки и кортежи (числитель, знаменатель)"""
        for it in items:
            x = self.text(it, x, base, size) if isinstance(it, str) else self.frac(it[0], it[1], x, base, int(size * 0.9))
        return x

    # ---------- обводка ----------
    def circle(self, cx, cy, rx, ry):
        """Небрежный овал одним движением, конец заходит за начало."""
        st = random.uniform(0, 2 * math.pi); turn = random.uniform(1.08, 1.25)
        wob = Noise(2, (1.5, 3.5)); rot = random.uniform(-0.3, 0.3)
        pts = []
        for t in np.linspace(0, 1, 160):
            a = st - t * turn * 2 * math.pi
            k = 1 + 0.06 * wob(t * 6) + 0.08 * t
            ex, ey = rx * k * math.cos(a), ry * k * math.sin(a)
            pts.append((cx + ex * math.cos(rot) - ey * math.sin(rot), cy + ex * math.sin(rot) + ey * math.cos(rot)))
        self.stroke(pts)

    # ---------- сохранение ----------
    def save(self, path):
        r = self.r
        yy, xx = np.mgrid[-int(r) - 1:int(r) + 2, -int(r) - 1:int(r) + 2]
        m = ndimage.binary_dilation(self.mask, structure=(xx ** 2 + yy ** 2) <= r * r)
        a = Image.fromarray((m * 255).astype(np.uint8)).resize(self.img.size, Image.BOX)
        a = np.asarray(a, dtype=np.float32)[..., None] / 255 * 0.97
        base = np.asarray(self.img, dtype=np.float32)
        out = base * (1 - a) + np.array(self.ink, np.float32) * a
        Image.fromarray(out.astype(np.uint8)).save(path, quality=92)
