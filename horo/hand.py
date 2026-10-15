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
    '∠': ([_ln((0.95, 1.05), (0.0, 0.02), (1.05, 0.0))], 1.1),           # угол (геометрия)
    '⊥': ([_ln((0.45, 1.15), (0.47, 0.03)), _ln((0.0, 0.0), (0.95, 0.02))], 1.0),  # перпендикулярно
    '∥': ([_ln((0.15, 1.1), (0.12, -0.05)), _ln((0.45, 1.1), (0.42, -0.05))], 0.6),  # параллельно
    ',': ([_ln((0.12, 0.12), (0.02, -0.28))], 0.3),
    '.': ([_arc(0.1, 0.05, 0.04, 0.04, 0, 360, 12)], 0.25),
    ';': ([_arc(0.15, 0.8, 0.04, 0.04, 0, 360, 12), _ln((0.15, 0.12), (0.05, -0.28))], 0.32),
    ':': ([_arc(0.12, 0.8, 0.04, 0.04, 0, 360, 12), _arc(0.12, 0.1, 0.04, 0.04, 0, 360, 12)], 0.3),
}

# ---------- настоящие буквы Глеба (вырезаны с его скриншотов, см. horo/fonts/gleb.npz) ----------
BANK = {}
STAMPS = {}  # буква: [(альфа 0..1, левый край, верх, размер пикселя)] — его буквы с листа без перерисовки
GAP = 0.22   # промежуток между его буквами, в высотах строчной — всегда одинаковый (Глеб: «± одинаковое расстояние»)
_here = os.path.dirname(os.path.abspath(__file__))
def _step(pts):
    """Шаг сетки, на которой снят скелет буквы (1/80 у старого банка, 1/320 — у снятого с увеличением)."""
    d = np.abs(np.diff(np.sort(pts[:, 0])))
    d = d[d > 1e-6]
    return float(d.min()) if len(d) else 1 / 80


def _strokes(pts, step):
    """Точки скелета (пиксели вперемешку) -> штрихи по порядку: от концов линии по соседним пикселям."""
    q = {tuple(p) for p in np.round(pts / step).astype(int)}
    nb = lambda p: [(p[0] + dx, p[1] + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                    if (dx or dy) and (p[0] + dx, p[1] + dy) in q]
    seen, out = set(), []
    for s in sorted(q, key=lambda p: (len(nb(p)) != 1, p)):  # сначала концы штрихов
        if s in seen:
            continue
        path, cur = [s], s
        seen.add(s)
        while True:
            nxt = sorted((n for n in nb(cur) if n not in seen), key=lambda n: abs(n[0] - cur[0]) + abs(n[1] - cur[1]))
            if not nxt:
                break
            cur = nxt[0]
            seen.add(cur)
            path.append(cur)
        out.append(np.array(path, float) * step)
    return out


def smooth(pts, win=7):
    """Буква из алфавита: скелет мелкой буквы с листа зубчатый — сглаживаем каждый штрих, как ровный росчерк пера."""
    out, step = [], _step(pts)
    win = max(3, int(win * (1 / 80) / step) // 2 * 2 + 1)  # окно сглаживания — одной длины на любой сетке
    for p in _strokes(pts, step):
        k = min(win, (len(p) - 1) // 2 * 2 + 1)
        if k >= 3:
            pad = np.concatenate([np.repeat(p[:1], k // 2, 0), p, np.repeat(p[-1:], k // 2, 0)])
            sm = np.stack([np.convolve(pad[:, i], np.ones(k) / k, 'valid') for i in (0, 1)], 1)
            sm[0], sm[-1] = p[0], p[-1]
            n = np.maximum(1, (np.hypot(*np.diff(sm, axis=0).T) / (step / 2)).astype(int))  # плотно: перо без дыр
            p = np.vstack([sm[:1]] + [sm[i] + (sm[i + 1] - sm[i]) * np.linspace(0, 1, n[i] + 1)[1:, None]
                                      for i in range(len(sm) - 1)])
        out.append(p)
    return np.concatenate(out).astype(np.float32)

# gleb_extra.npz — буквы из его алфавита (horo/alphabet.py), лежат вне репозитория
for _bp in (os.path.join(_here, 'fonts', 'gleb.npz'), os.path.join(_here, 'tmp', 'fonts', 'gleb_extra.npz')):
    if os.path.exists(_bp):
        _z = np.load(_bp)
        for _k in _z.files:
            if _k.split('_')[1].isdigit():
                _p = _z[_k] if _bp.endswith('gleb.npz') else smooth(_z[_k])
                BANK.setdefault(chr(int(_k.split('_')[0])), []).append((_p, float(_z[_k + 'w'])))
                if _k + 's' in _z.files:  # его буква картинкой (алфавит с 14.10): ставится как есть
                    _a = _z[_k + 's'].astype(np.float32) / 255
                    _l, _t, _px = map(float, _z[_k + 'm'])
                    # ширина для промежутка — по основной части буквы (от строки до высоты строчной):
                    # хвостик «б» сверху или петля «у» снизу заходят на соседнюю букву, как у него
                    _rows = (_t - np.arange(_a.shape[0]) * _px)
                    _body = _a[(_rows > 0.05) & (_rows < 1.0)]
                    _cols = np.nonzero((_body if len(_body) else _a).max(0) > 0.3)[0]
                    _bw = (_cols.max() + 1) * _px if len(_cols) else _a.shape[1] * _px
                    STAMPS.setdefault(chr(int(_k.split('_')[0])), []).append((_a, _l, _t, _px, _bw))

# gleb_live.npz — буквы, вырезанные из его настоящих записей на скриншотах заданий (Глеб, 15.10: «посмотри, как я пишу,
# и сделай такой же почерк, без выдумок»): ставятся картинкой, как есть, раньше букв с листа алфавита.
LIVE = {}
LIVE_GAP = 0.12   # промежуток между буквами у него в записях, в высотах строчной
LIVE_SPACE = 0.85  # пробел между словами
_lp = os.path.join(_here, 'fonts', 'gleb_live.npz')
if os.path.exists(_lp):
    _z = np.load(_lp)
    for _k in sorted({k[:-1] for k in _z.files}, key=lambda k: (int(k.split('_')[0]), int(k.split('_')[1]))):
        _a = _z[_k + 's'].astype(np.float32) / 255
        _l, _t, _px = map(float, _z[_k + 'm'])
        _rows = (_t - np.arange(_a.shape[0]) * _px)
        _body = _a[(_rows > 0.05) & (_rows < 1.0)]
        _cols = np.nonzero((_body if len(_body) else _a).max(0) > 0.3)[0]
        _bw = (_cols.max() + 1) * _px if len(_cols) else _a.shape[1] * _px
        LIVE.setdefault(chr(int(_k.split('_')[0])), []).append((_a, _l, _t, _px, _bw))

# gleb_norm.npz — одна ровная азбука для текста (horo/live/norm.py): его буквы (из записей и с листа) приведены
# к одной высоте по классу буквы, стоят ровно на строке и обведены одним пером одной толщины
# (Глеб 15.10: «буквы разного размера и как будто криво вырезаны»).
NORM = {}
NORM_GAP = 0.16    # просвет между буквами (от края штриха до края штриха), в высотах строчной — везде одинаковый
NORM_SPACE = 0.6   # просвет между словами
PROFILE_Y = np.linspace(0.1, 1.4, 14)   # уровни, по которым меряем просвет (как кернинг у шрифта); хвосты вниз не считаем
NORM_PEN = 0.17    # толщина пера, в высотах строчной (как в его записях)
_np_ = os.path.join(_here, 'fonts', 'gleb_norm.npz')
if os.path.exists(_np_):
    _z = np.load(_np_)
    for _k in _z.files:
        if _k.endswith('w'):
            continue
        _flat = _z[_k]
        _cut = np.nonzero(np.isnan(_flat[:, 0]))[0]
        _parts = [p for p in np.split(_flat, _cut) if len(p)]
        _parts = [p[~np.isnan(p[:, 0])] for p in _parts]
        _st = [p for p in _parts if len(p)]
        _P = np.concatenate(_st)
        _lp, _rp = [], []   # профиль буквы: левый и правый край штриха на уровнях полосы строчной
        for _y in PROFILE_Y:
            _r = _P[np.abs(_P[:, 1] - _y) < 0.07]
            _lp.append(_r[:, 0].min() if len(_r) else np.nan)
            _rp.append(_r[:, 0].max() if len(_r) else np.nan)
        NORM[chr(int(_k))] = (_st, float(_z[_k + 'w']), np.array(_lp), np.array(_rp))

BOLD = 0.2   # толщина его пера в записях — в высотах строчной (на листе алфавита он писал тоньше, ~0.11)
_bolded = {}


def _bold(st):
    """Буква с листа алфавита пером его записей: штрих утолщается до BOLD, форма та же."""
    a, left, top, px, bw = st
    if id(a) not in _bolded:
        m = a > 0.5
        dt = ndimage.distance_transform_edt(m)
        cur = 2 * np.percentile(dt[m], 90) if m.any() else 1
        add = max(0.0, (BOLD / px - cur) / 2)
        pad = int(add) + 2
        out = ndimage.distance_transform_edt(~np.pad(m, pad))
        b = np.maximum(np.clip(add + 0.7 - out, 0, 1), np.pad(a, pad)).astype(np.float32)
        _bolded[id(a)] = (b, left - pad * px, top + pad * px, px, bw)
    return _bolded[id(a)]

# латинские буквы, которые пишутся как русские (их нет в алфавите отдельно), и наоборот
ALIAS = dict(zip('aceopxyACEHKMOPTXB', 'асеорхуАСЕНКМОРТХВ'))
ALIAS.update({v: k for k, v in ALIAS.items()})

# стиль, который Глеб настроил в боте: множители к небрежности, толщине и размеру, добавка к наклону
STYLE_FILE = os.path.join(_here, 'tmp', 'hand_style.json')


def style():
    try:
        import json
        with open(STYLE_FILE) as f:
            return {k: float(v) for k, v in json.load(f).items()}
    except Exception:
        return {}


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


def _even(ch, a, top, px):
    """Латиница Apple Pencil-режима: у него на листе буквы то выше, то ниже — выравниваем (Глеб, 15.10: «разные буквы»):
    строчные «тела» (a c e m n o r s u v w x z) до одной высоты, с выносными (b d f h k l t) до одной, заглавные до одной."""
    if not ('a' <= ch <= 'z' or 'A' <= ch <= 'Z'):
        return 1.0
    ys = np.flatnonzero(a.max(1) > 0.3)
    t = top - ys[0] * px
    if ch in 'gjpqy':   # с хвостом — как у него (хвост тянет размер)
        return 1.0
    tgt = {'t': 1.4, 'i': 1.6}.get(ch)
    if tgt is None:
        tgt = 2.0 if ch.isupper() else 1.6 if ch in 'bdfhkl' else 1.1
    return min(1.15, max(0.75, tgt / t))   # не раздуваем сильно — штрих толстеет


class Noise:
    """Плавный шум без периода: сумма синусов со случайными несоизмеримыми частотами."""
    def __init__(self, n=3, f=(0.5, 2.5)):
        self.c = [(random.uniform(*f), random.uniform(0, 7), random.uniform(0.4, 1)) for _ in range(n)]
        self.k = 1 / sum(a for _, _, a in self.c)

    def __call__(self, t):
        return self.k * sum(a * np.sin(fr * t + ph) for fr, ph, a in self.c)


class Hand:
    def __init__(self, img, ink=INK, seed=1, width=2.8, mess=1.0, slant=0.02, exact=True):
        """mess: насколько небрежно (0.5 аккуратно, 1 как он, 1.5 на скорость); slant: наклон вправо (у него почти 0).
        Настройки из бота (hand_style.json) умножают mess, width и размер букв."""
        random.seed(seed); np.random.seed(seed)
        st = style()
        mess *= st.get('mess', 1.0)
        width *= st.get('width', 1.0)
        slant += st.get('slant', 0.0)
        self.k = st.get('size', 1.0)
        if exact:   # Apple Pencil, не рука (Глеб, 15.10): ни дрожи, ни наклона, ни случайных искажений
            mess, slant = 0.0, 0.0
        self.m, self.sl = mess, slant
        self.img = img.convert('RGB')
        W, H = self.img.size
        self.mask = np.zeros((H * SS, W * SS), bool)
        self.ink = ink
        self.r = width * SS / 2
        self.exact = exact   # его буквы из алфавита — как на листе, без случайного размера и промежутков
        self.stamps = None
        self.layer = None
        self.last = {}
        self.turn = {}

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
        live = False
        norm = self.exact and bool(NORM)
        prev = None   # предыдущая буква ровной азбуки: (x, масштаб, правый профиль, был ли пробел)
        for ch in s:
            if ch == ' ':
                if norm and prev is not None:
                    prev = prev[:3] + (True,)
                    continue
                x += xh * ((NORM_SPACE if norm else 0.7) if self.exact else random.uniform(0.55, 0.85)); continue
            sup0 = ch in '²³⁴⁵'
            nc = {'²': '2', '³': '3', '⁴': '4', '⁵': '5', '−': '-', '–': '-', '—': '-', 'ё': 'е', '«': '', '»': ''}.get(ch, ch)
            if norm and nc == '':
                continue
            if norm and nc in NORM:   # его буква из ровной азбуки: один размер, ровно на строке, одно перо
                st, w, lp, rp = NORM[nc]
                kk = xh * (0.68 if sup0 else 1.0)
                up = xh * 0.8 if sup0 else 0.0
                pen = NORM_PEN * xh
                if prev is None:   # первая буква: хвост «у» не вылезает левее начала строки
                    gx = x - min(0.0, min(float(q[:, 0].min()) for q in st)) * kk
                else:   # просвет между краями штрихов одинаковый — по профилю, как кернинг у шрифта
                    px_, pk, prp, sp = prev
                    gap = (NORM_SPACE if sp else NORM_GAP) * xh + pen
                    both = ~np.isnan(prp) & ~np.isnan(lp) & (not sup0) & (pk == kk)
                    if nc in ',.;:!?)':   # знаки — сразу за буквой, не подныривая под неё
                        gx = px_ + np.nanmax(prp) * pk + 0.08 * xh + pen - np.nanmin(lp) * kk
                    elif sp:   # пробел — от края до края слов, одинаковый
                        gx = px_ + np.nanmax(prp) * pk + gap - np.nanmin(lp) * kk
                    elif both.any():
                        gx = max(px_ + prp[both] * pk + gap - lp[both] * kk)
                    else:
                        gx = px_ + np.nanmax(prp) * pk + gap - np.nanmin(lp) * kk
                out.append((nc, gx, up, kk, dict(norm=st)))
                prev = (gx, kk, rp, False)
                x = gx + w * kk
                continue
            prev = None
            sup = ch in '²³⁴⁵'
            dot = ch == '·' and '·' not in BANK and '.' in BANK  # точка умножения — его точка, поднятая к середине
            c = {'²': '2', '³': '3', '⁴': '4', '⁵': '5', '−': '-', '–': '-', '·': '.' if dot else '·'}.get(ch, ch)
            if c not in BANK and ch not in BANK and ALIAS.get(c) in BANK:
                c = ALIAS[c]
            st = (STAMPS.get(ch) or STAMPS.get(c)) if self.exact else None
            if st:  # его буква как на листе: тот же размер, ровно на строке, одинаковый промежуток
                a, left, top, px, bw = _bold(st[0]) if live or LIVE else st[0]
                kk = xh * (0.68 if sup else 1.0) * _even(c, a, top, px)   # ² ³ — та же его цифра, поменьше и выше строки
                up = xh * 0.8 if sup else 0.0
                if sup:
                    x -= xh * 0.06
                out.append((c, x, up, kk, dict(stamp=(a, left, top, px))))
                x += (left + bw) * kk + xh * GAP * (0.5 if sup else 1.0)
                continue
            real = ch in BANK or c in BANK
            if real:                                     # его настоящая буква: случайный из вариантов, не тот же подряд
                vs = BANK[ch] if ch in BANK else BANK[c]
                i = random.choice([j for j in range(len(vs)) if j != self.last.get(ch)] or [0])
                self.last[ch] = i
                pts, w = vs[i]
                if ch in BANK and sup: sup = False       # у надстрочных своя высота в банке
            else:
                pts, w = glyph(c)
            k = xh * (random.uniform(1 - 0.04 * m, 1 + 0.05 * m) if real else random.uniform(1 - 0.1 * m, 1 + 0.12 * m)) \
                * (0.68 if sup else 1)  # его буквы одного размера: прыгающий размер и делал почерк корявым
            up = xh * random.uniform(0.75, 0.9) if sup else xh * 0.4 if dot else 0
            if sup: x -= xh * 0.08
            if real:
                prm = dict(real=pts, sx=random.uniform(0.97, 1.03), sy=random.uniform(0.97, 1.03), slant=0,
                           rot=random.uniform(-0.02, 0.02) * m, amp=0)
                out.append((c, x, up, k, prm))
                x += w * k * prm['sx'] + xh * random.uniform(0.26, 0.26 + 0.08 * m) * (0.5 if sup else 1)
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
        if lay:  # длинная строка (числитель дроби) не должна уехать за свою высоту: иначе черта дроби режет буквы
            span = max(lay[-1][1], xh)
            tilt = max(-0.3 * xh / span, min(0.3 * xh / span, tilt))
        amp = xh * random.uniform(0.04, 0.09) * m
        if any('stamp' in p or 'norm' in p for *_, p in lay):  # строка его буквами — ровная, без волны и наклона
            amp, tilt = 0.0, 0.0
        for c, x, up, k, p in lay:
            gx = x0 + x
            if 'stamp' in p:
                self._stamp(p['stamp'], gx, base - up, k)
                continue
            if 'norm' in p:
                self._norm(p['norm'], gx, base - up, k)
                continue
            gy = base - up + amp * line(gx) + (gx - x0) * tilt + random.uniform(-0.035, 0.035) * m * xh
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

    def _norm(self, st, x, base, xh):
        """Буква ровной азбуки: штрихи одним пером толщиной NORM_PEN·xh."""
        if self.layer is None:
            H, W = self.mask.shape
            self.layer = Image.new('L', (W, H), 0)
        d = ImageDraw.Draw(self.layer)
        w = max(1, int(round(NORM_PEN * xh * SS)))
        r = w / 2
        for p in st:
            pts = [((x + u * xh) * SS, (base - v * xh) * SS) for u, v in p]
            if len(pts) > 1:
                d.line(pts, fill=255, width=w, joint='curve')
            for q in (pts[0], pts[-1]):
                d.ellipse([q[0] - r, q[1] - r, q[0] + r, q[1] + r], fill=255)

    def _stamp(self, st, x, base, xh):
        """Поставить его букву картинкой: левый верх — (x + left·xh, base − top·xh), пиксель альфы — px·xh."""
        a, left, top, px = st
        k = px * xh * SS
        w, h = max(1, round(a.shape[1] * k)), max(1, round(a.shape[0] * k))
        im = np.asarray(Image.fromarray((a * 255).astype(np.uint8)).resize((w, h), Image.LANCZOS), np.float32) / 255
        X, Y = round((x + left * xh) * SS), round((base - top * xh) * SS)
        if self.stamps is None:
            self.stamps = np.zeros(self.mask.shape, np.float32)
        H, W = self.stamps.shape
        y0, x0, y1, x1 = max(0, Y), max(0, X), min(H, Y + h), min(W, X + w)
        if y1 > y0 and x1 > x0:
            sub = self.stamps[y0:y1, x0:x1]
            np.maximum(sub, im[y0 - Y:y1 - Y, x0 - X:x1 - X], out=sub)

    def text(self, s, x, base, size=44):
        size *= self.k
        lay, w = self._layout(s, size)
        self._draw(lay, x, base, size)
        return x + w

    def _ext(self, lay, xh):
        """(верх, низ) раскладки в px от строки, y вверх: по его буквам-штампам, у остальных знаков — 1.3 и -0.25 xh."""
        top, bot = 0.0, 0.0
        for c, x, up, k, p in lay:
            if 'stamp' in p:
                a, left, t, px = p['stamp']
                top = max(top, up + t * k)
                bot = min(bot, up + (t - a.shape[0] * px) * k)
            elif 'norm' in p:
                v = np.concatenate([q[:, 1] for q in p['norm']])
                top = max(top, up + (v.max() + NORM_PEN / 2) * k)
                bot = min(bot, up + (v.min() - NORM_PEN / 2) * k)
            else:
                top = max(top, up + 1.3 * xh)
                bot = min(bot, up - 0.25 * xh)
        return top, bot

    # ---------- дроби ----------
    def frac(self, num, den, x, base, size=40):
        size *= self.k
        xh, m = size * XH, self.m
        ln, wn = self._layout(num, size)
        ld, wd = self._layout(den, size)
        w = max(wn, wd)
        y = base - xh * 0.55                             # черта на уровне середины строки
        sup = any(ch in '²³⁴⁵' for ch in den)
        if self.exact:  # Apple Pencil: черта ровная и горизонтальная, числитель и знаменатель точно по центру
            x1, x2 = x, x + w + xh * 0.7
            gap = 0.3 * xh
            _, nb = self._ext(ln, xh)                       # хвосты числителя (у, р, д) не должны доставать до черты
            nt, _ = self._ext(ld, xh)                       # а верх знаменателя (цифры, скобки, б, д) — не упираться в неё
            self._draw(ln, x1 + (x2 - x1 - wn) / 2, y - gap + min(0.0, nb), size)
            self._draw(ld, x1 + (x2 - x1 - wd) / 2, y + gap + nt, size)
            t = np.linspace(0, 1, 40)
            self.stroke(np.stack([x1 + (x2 - x1) * t, np.full(40, y)], 1))
            return x2 + xh * 0.3
        x1 = x + random.uniform(-0.1, 0.15) * xh
        x2 = x + w + xh * random.uniform(0.5, 1.2)       # черта уходит дальше записи
        self._draw(ln, x1 + (x2 - x1 - wn) / 2 + random.uniform(-0.3, 0.3) * xh, y - xh * (random.uniform(0.4, 0.55) + 0.01 * min(len(num), 16)), size)  # длинный числитель чуть выше: черта его не задевает
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
        if self.exact:   # овал одним движением, как стилусом: чуть наклонён, к концу шире, конец заходит за начало
            rot = -0.18
            pts = []
            for t in np.linspace(0, 1, 160):
                a = math.radians(-110) - t * 1.12 * 2 * math.pi
                k = 1 + 0.07 * t + 0.03 * math.sin(3 * a)
                ex, ey = rx * k * math.cos(a), ry * k * math.sin(a)
                pts.append((cx + ex * math.cos(rot) - ey * math.sin(rot), cy + ex * math.sin(rot) + ey * math.cos(rot)))
            self.stroke(pts)
            return
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
        if self.stamps is not None:  # его буквы-картинки поверх линий пера
            st = Image.fromarray((self.stamps * 255).astype(np.uint8)).resize(self.img.size, Image.BOX)
            a = np.maximum(a, np.asarray(st, np.float32)[..., None] / 255 * 0.97)
        if self.layer is not None:  # буквы ровной азбуки
            ly = self.layer.resize(self.img.size, Image.BOX)
            a = np.maximum(a, np.asarray(ly, np.float32)[..., None] / 255 * 0.97)
        base = np.asarray(self.img, dtype=np.float32)
        out = base * (1 - a) + np.array(self.ink, np.float32) * a
        Image.fromarray(out.astype(np.uint8)).save(path, quality=92)
