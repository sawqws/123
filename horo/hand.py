"""Почерк Глеба поверх картинки (Apple Pencil на iPad, по его образцам).

Как он пишет (заметки на iPad и скриншоты HDP):
- тонкая ровная линия, синий цвет, нажим не меняется;
- буквы почти прямые, широкие и низкие, между ними большие промежутки;
- «x» как «ɔc» (две дуги спиной друг к другу), «a» кружок с палочкой,
  «4» открытая как «Ч», «2» угловатая как «z», «7» с перечёркиванием, «1» с носиком;
- «=» две короткие чёрточки, «+» маленький;
- дробь: числитель прижат к черте, черта длинная, выходит далеко вправо и чуть загибается;
- строка плавает вверх-вниз, буквы разного размера.

Буквы для математики нарисованы здесь же как линии (GLYPHS); остальные (русские слова)
берутся из скелета шрифта Neucha и растягиваются вширь. Каждая копия буквы по-своему
искажается, поэтому одинаковых букв нет. Рисуем в 3-кратном разрешении и уменьшаем.

Пример:
    h = Hand(img, seed=1)              # ширина пера width — в пикселях картинки
    h.expr(['= ', ('a·a+2·b', 'ab'), ' = ', ('a²+2b', 'ab')], x, y, 44)
    h.circle(cx, cy, 34, 30)
    h.save('out.jpg')
"""
import math, os, random
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

FONT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fonts', 'Neucha.ttf')
SS = 3                 # во сколько раз рисуем крупнее, чем картинка
INK = (25, 125, 240)   # цвет пера на его скриншотах HDP; в заметках iPad светлее: (90, 160, 240)
XH = 0.45              # высота строчной буквы в долях size

# ---------- рисованные буквы: штрихи в единицах высоты строчной буквы, y вверх ----------

def _arc(cx, cy, rx, ry, a0, a1, n=60):
    a = np.radians(np.linspace(a0, a1, n))
    return np.stack([cx + rx * np.cos(a), cy + ry * np.sin(a)], 1)

def _ln(*p, n=30):
    out = []
    for p0, p1 in zip(p, p[1:]):
        t = np.linspace(0, 1, n)[:, None]
        out.append(np.array(p0) * (1 - t) + np.array(p1) * t)
    return np.vstack(out)

def _bz(p0, p1, p2, n=50):
    t = np.linspace(0, 1, n)[:, None]
    p0, p1, p2 = map(np.array, (p0, p1, p2))
    return (1 - t) ** 2 * p0 + 2 * (1 - t) * t * p1 + t ** 2 * p2

def _hump(x0):
    """дуга буквы m/n: вверх от основы и вниз"""
    return np.vstack([_bz((x0, 0.55), (x0 + 0.05, 1.05), (x0 + 0.3, 1.0)), _bz((x0 + 0.3, 1.0), (x0 + 0.58, 0.95), (x0 + 0.58, 0))])

GLYPHS = {  # символ: (список штрихов, ширина)
    'a': ([_arc(0.45, 0.5, 0.45, 0.5, 25, 385), _ln((0.93, 1.0), (0.95, 0.05), (1.02, 0))], 1.05),
    'b': ([_ln((0.1, 1.8), (0.06, 0.0)), _arc(0.44, 0.45, 0.38, 0.45, 165, -175)], 0.9),
    'c': ([_arc(0.45, 0.5, 0.45, 0.5, 40, 320)], 0.85),
    'd': ([_arc(0.45, 0.5, 0.45, 0.5, 25, 385), _ln((0.93, 1.8), (0.95, 0.05), (1.02, 0))], 1.05),
    'm': ([np.vstack([_ln((0, 1.0), (0, 0)), _hump(0), _hump(0.58)])], 1.18),
    'n': ([np.vstack([_ln((0, 1.0), (0, 0)), _hump(0)])], 0.62),
    'p': ([_ln((0.05, 1.05), (0.05, -0.85)), _arc(0.45, 0.52, 0.4, 0.48, 175, 535)], 0.9),
    'q': ([_arc(0.42, 0.52, 0.42, 0.48, 20, 380), _ln((0.86, 1.0), (0.84, -0.85))], 0.92),
    'x': ([_arc(0.02, 0.5, 0.32, 0.5, 100, -100), _arc(0.78, 0.5, 0.32, 0.5, 80, 280)], 1.05),
    'y': ([_ln((0.0, 1.0), (0.12, 0.3), (0.42, 0.1), (0.82, 1.0)), np.vstack([_ln((0.82, 1.0), (0.74, -0.5)), _bz((0.74, -0.5), (0.6, -0.85), (0.2, -0.65))])], 0.9),
    '0': ([_arc(0.4, 0.65, 0.38, 0.65, 100, 480)], 0.85),
    '1': ([_ln((0.08, 0.95), (0.42, 1.3), (0.42, 0))], 0.62),
    '2': ([np.vstack([_arc(0.4, 0.95, 0.36, 0.33, 165, -10), _ln((0.75, 0.88), (0.02, 0.0), (0.82, 0.03))])], 0.9),
    '3': ([np.vstack([_arc(0.38, 1.0, 0.34, 0.3, 150, -90), _arc(0.4, 0.36, 0.4, 0.36, 90, -150)])], 0.88),
    '4': ([_bz((0.1, 1.3), (0.0, 0.6), (0.75, 0.6)), _ln((0.68, 1.3), (0.66, 0))], 0.88),
    '5': ([np.vstack([_ln((0.78, 1.3), (0.15, 1.28), (0.1, 0.78)), _arc(0.4, 0.43, 0.38, 0.43, 130, -150)])], 0.9),
    '6': ([np.vstack([_bz((0.72, 1.3), (0.1, 1.1), (0.06, 0.42)), _arc(0.42, 0.42, 0.36, 0.42, 180, 540)])], 0.88),
    '7': ([_ln((0.0, 1.28), (0.8, 1.3), (0.28, 0)), _ln((0.25, 0.65), (0.75, 0.68))], 0.88),
    '8': ([_arc(0.4, 1.0, 0.28, 0.3, -90, 270), _arc(0.4, 0.35, 0.37, 0.36, 90, 450)], 0.85),
    '9': ([_arc(0.42, 0.95, 0.36, 0.35, 0, 365), _ln((0.78, 0.95), (0.7, 0))], 0.88),
    '+': ([_ln((0.0, 0.5), (0.6, 0.52)), _ln((0.3, 0.85), (0.29, 0.15))], 0.65),
    '-': ([_ln((0.0, 0.5), (0.55, 0.52))], 0.6),
    '=': ([_ln((0.0, 0.72), (0.62, 0.74)), _ln((0.0, 0.28), (0.6, 0.31))], 0.7),
    '±': ([_ln((0.0, 0.65), (0.6, 0.67)), _ln((0.3, 1.0), (0.29, 0.3)), _ln((0.0, 0.05), (0.6, 0.07))], 0.65),
    '(': ([_bz((0.38, 1.55), (-0.12, 0.6), (0.38, -0.35))], 0.42),
    ')': ([_bz((0.04, 1.55), (0.54, 0.6), (0.04, -0.35))], 0.42),
    '·': ([_arc(0.15, 0.5, 0.05, 0.05, 0, 360, 12)], 0.32),
    ',': ([_ln((0.12, 0.12), (0.02, -0.28))], 0.3),
    '.': ([_arc(0.1, 0.05, 0.04, 0.04, 0, 360, 12)], 0.25),
    ';': ([_arc(0.15, 0.8, 0.04, 0.04, 0, 360, 12), _ln((0.15, 0.12), (0.05, -0.28))], 0.32),
    ':': ([_arc(0.12, 0.8, 0.04, 0.04, 0, 360, 12), _arc(0.12, 0.1, 0.04, 0.04, 0, 360, 12)], 0.3),
}

# ---------- настоящие буквы Глеба (вырезаны с его скриншотов, см. horo/fonts/gleb.npz) ----------
BANK = {}
_here = os.path.dirname(os.path.abspath(__file__))
# gleb_extra.npz — буквы из его алфавита (horo/alphabet.py), лежат вне репозитория
for _bp in (os.path.join(_here, 'fonts', 'gleb.npz'), os.path.join(_here, 'tmp', 'fonts', 'gleb_extra.npz')):
    if os.path.exists(_bp):
        _z = np.load(_bp)
        for _k in _z.files:
            if not _k.endswith('w'):
                BANK.setdefault(chr(int(_k.split('_')[0])), []).append((_z[_k], float(_z[_k + 'w'])))

# ---------- запасные буквы из шрифта (русские слова и всё, чего нет в GLYPHS) ----------
_font = ImageFont.truetype(FONT, 200)
_cache = {}


def _dense(s, step=0.006):
    out = [s[:1]]
    for p0, p1 in zip(s, s[1:]):
        n = max(1, int(np.hypot(*(p1 - p0)) / step))
        out.append(p0 + (p1 - p0) * np.linspace(0, 1, n + 1)[1:, None])
    return np.vstack(out)


def glyph(ch):
    """Штрихи буквы (массивы точек в единицах высоты строчной, y вверх) и её ширина."""
    if ch in _cache:
        return _cache[ch]
    if ch in GLYPHS:
        strokes, w = GLYPHS[ch]
        pts = [_dense(s) for s in strokes]
    else:
        from skimage.morphology import skeletonize
        asc, desc = _font.getmetrics()
        im = Image.new('L', (int(_font.getlength(ch)) + 40, asc + desc + 40), 0)
        ImageDraw.Draw(im).text((20, 20 + asc), ch, font=_font, fill=255, anchor='ls')
        ys, xs = np.nonzero(skeletonize(np.array(im) > 110))
        k = 1 / 100  # у Neucha высота строчной ~100 при размере 200
        pts = [np.stack([(xs - 20.0) * k * 1.25, -(ys - 20.0 - asc) * k], 1)]  # шире, как у него
        w = _font.getlength(ch) * k * 1.25
    _cache[ch] = (pts, w)
    return _cache[ch]


class Noise:
    """Плавный шум без периода: сумма синусов со случайными несоизмеримыми частотами."""
    def __init__(self, n=3, f=(0.5, 2.5)):
        self.c = [(random.uniform(*f), random.uniform(0, 7), random.uniform(0.4, 1)) for _ in range(n)]
        self.k = 1 / sum(a for _, _, a in self.c)

    def __call__(self, t):
        return self.k * sum(a * np.sin(fr * t + ph) for fr, ph, a in self.c)


class Hand:
    def __init__(self, img, ink=INK, seed=1, width=2.8, mess=1.0, slant=0.02):
        """mess: насколько небрежно (0.5 аккуратно, 1 как он, 1.5 на скорость); slant: наклон вправо (у него почти 0)."""
        random.seed(seed); np.random.seed(seed)
        self.m, self.sl = mess, slant
        self.img = img.convert('RGB')
        W, H = self.img.size
        self.mask = np.zeros((H * SS, W * SS), bool)
        self.ink = ink
        self.r = width * SS / 2
        self.last = {}

    # ---------- перо ----------
    def _dots(self, pts):
        p = np.round(np.asarray(pts) * SS).astype(int)
        H, W = self.mask.shape
        ok = (p[:, 0] >= 0) & (p[:, 0] < W) & (p[:, 1] >= 0) & (p[:, 1] < H)
        self.mask[p[ok, 1], p[ok, 0]] = True

    def stroke(self, pts):
        self._dots(_dense(np.asarray(pts, float), 0.3))

    # ---------- буквы ----------
    def _layout(self, s, size):
        """Раскладка строки: (символ, x, подъём, масштаб, искажение) и ширина в px."""
        xh, m = size * XH, self.m
        out, x = [], 0.0
        for ch in s:
            if ch == ' ':
                x += xh * random.uniform(0.55, 0.85); continue
            sup = ch in '²³⁴⁵'
            c = {'²': '2', '³': '3', '⁴': '4', '⁵': '5', '−': '-', '–': '-'}.get(ch, ch)
            real = ch in BANK or c in BANK
            if real:                                     # его настоящая буква: случайный из вариантов, не тот же подряд
                vs = BANK[ch] if ch in BANK else BANK[c]
                i = random.choice([j for j in range(len(vs)) if j != self.last.get(ch)] or [0])
                self.last[ch] = i
                pts, w = vs[i]
                if ch in BANK and sup: sup = False       # у надстрочных своя высота в банке
            else:
                pts, w = glyph(c)
            k = xh * random.uniform(1 - 0.1 * m, 1 + 0.12 * m) * (0.68 if sup else 1)
            up = xh * random.uniform(0.75, 0.9) if sup else 0
            if sup: x -= xh * 0.08
            if real:
                prm = dict(real=pts, sx=random.uniform(0.94, 1.06), sy=random.uniform(0.94, 1.06), slant=0,
                           rot=random.uniform(-0.03, 0.03) * m, amp=0)
                out.append((c, x, up, k, prm))
                x += w * k * prm['sx'] + xh * random.uniform(0.32 - 0.1 * m, 0.32 + 0.14 * m) * (0.5 if sup else 1)
                continue
            prm = dict(sx=random.uniform(1 - 0.12 * m, 1 + 0.15 * m), sy=random.uniform(1 - 0.12 * m, 1 + 0.1 * m),
                       slant=self.sl + random.uniform(-0.06, 0.08) * m, rot=random.uniform(-0.07, 0.07) * m,
                       nx=Noise(2, (1.5, 3.5)), ny=Noise(2, (1.5, 3.5)), tx=Noise(2, (6, 10)), ty=Noise(2, (6, 10)),
                       amp=random.uniform(0.04, 0.09) * m * (0.4 if c in '()-=+·' else 1))
            out.append((c, x, up, k, prm))
            x += w * k * prm['sx'] + xh * random.uniform(0.32 - 0.1 * m, 0.32 + 0.14 * m) * (0.5 if sup else 1)
        return out, x

    def _draw(self, lay, x0, base, size):
        xh, m = size * XH, self.m
        line = Noise(3, (0.004, 0.012))
        tilt = random.uniform(-0.03, 0.03) * m
        amp = xh * random.uniform(0.06, 0.14) * m
        for c, x, up, k, p in lay:
            gx = x0 + x
            gy = base - up + amp * line(gx) + (gx - x0) * tilt + random.uniform(-0.08, 0.08) * m * xh
            for st in ([p['real']] if 'real' in p else glyph(c)[0]):
                n = len(st)
                if c in GLYPHS and 'real' not in p and n > 40:               # штрих то не дотянут, то короче: кружки не всегда замкнуты
                    st = st[int(n * random.uniform(0, 0.07) * m): n - int(n * random.uniform(0, 0.07) * m)]
                u, v = st[:, 0], st[:, 1]
                if 'real' not in p:
                  u = u + p['amp'] * p['nx'](u + 2 * v) + 0.012 * m * p['tx'](u - v)   # плавная деформация + дрожание
                  v = v + p['amp'] * p['ny'](2 * u - v) + 0.012 * m * p['ty'](u + v)
                u, v = u * p['sx'], v * p['sy']
                u = u + v * p['slant']
                cr, sr = math.cos(p['rot']), math.sin(p['rot'])
                u, v = u * cr - v * sr, u * sr + v * cr
                self._dots(np.stack([gx + u * k, gy - v * k], 1))

    def text(self, s, x, base, size=44):
        lay, w = self._layout(s, size)
        self._draw(lay, x, base, size)
        return x + w

    # ---------- дроби ----------
    def frac(self, num, den, x, base, size=40):
        xh, m = size * XH, self.m
        ln, wn = self._layout(num, size)
        ld, wd = self._layout(den, size)
        w = max(wn, wd)
        y = base - xh * 0.55                             # черта на уровне середины строки
        x1 = x + random.uniform(-0.1, 0.15) * xh
        x2 = x + w + xh * random.uniform(0.5, 1.2)       # черта уходит дальше записи
        sup = any(ch in '²³⁴⁵' for ch in den)
        self._draw(ln, x1 + (x2 - x1 - wn) / 2 + random.uniform(-0.3, 0.3) * xh, y - xh * random.uniform(0.4, 0.55), size)
        self._draw(ld, x1 + (x2 - x1 - wd) / 2 + random.uniform(-0.3, 0.3) * xh, y + xh * (random.uniform(1.65, 1.8) + 0.35 * sup), size)
        y1, y2 = y + random.uniform(-0.1, 0.1) * xh, y + random.uniform(-0.15, 0.1) * xh * m
        lift = random.uniform(0, 0.2) * xh * m            # кончик черты чуть загибается вверх
        t = np.linspace(0, 1, 40)
        self.stroke(np.stack([x1 + (x2 - x1) * t, y1 + (y2 - y1) * t - lift * t ** 4], 1))
        return x2 + xh * 0.3

    def expr(self, items, x, base, size=44):
        """items: строки и кортежи (числитель, знаменатель)"""
        for it in items:
            x = self.text(it, x, base, size) if isinstance(it, str) else self.frac(it[0], it[1], x, base, size)
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
