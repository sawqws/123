"""Решение в бумажной тетради «как у Глеба» (Глеб, 22.10: «научись делать, чтобы выглядело, как будто я писал»).

Учитель часто просит «выполнить в тетради» — тогда нужен не тёмный лист iPad (sheet.py), а фото листа в клетку:
синяя шариковая ручка, красные поля, лист лежит на столе.
- Буквы и цифры — его настоящие, вырезаны с фото его тетради (`horo/fonts/gleb_paper.npz`, собирает
  `horo/live/paper_build.py` с фото в Brain `raw/images/notebook_*.jpg`): x y c m t 0–9 + ( ) №, «Лист».
  Чего на фото нет (a b z, знаки − = · :) — его буквы из ровной азбуки (`hand.NORM`) или знаки, нарисованные пером
  той же толщины (штрих 0.095 клетки, как у него).
- Размер как у него в тетради: строчная ≈ 0.88 клетки, цифры 0.95, y с хвостом 1.7; черта дроби — по линии клетки,
  числитель в клетке над ней, знаменатель — под ней; степень — 0.6 размера, у верха буквы.
- Рука живая: каждый раз другой образец буквы, чуть гуляют размер, наклон, строка и промежутки.

Пример:
    import sys; sys.path.insert(0, 'horo')
    from paper import Notebook
    nb = Notebook(seed=7)
    nb.title('Лист 7')
    nb.solve(2, [[('z^2 + 6z + 9', 'z^3 + 27'), ' : ', ('3z + 9', 'z^2 − 3z + 9')], ['= ', ('1', '3')]])
    nb.save('2_tetrad.jpg')          # фото листа на столе; nb.page() — ровный лист без фото
Строка решения — список кусков; кусок — строки и дроби (числитель, знаменатель). Кусок, который не влезает
в строку, переносится на следующую (начинать такие куски с «= »). «^» — степень: «z^2», «a^{18}».
"""
import json
import math
import os
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

_here = os.path.dirname(os.path.abspath(__file__))
BANK_FILE = os.path.join(_here, 'fonts', 'gleb_paper.npz')
REF = 40          # клетка, в которой лежат штампы банка, px
PEN = 0.095       # толщина его штриха, в клетках
INK_DARK = np.array([26, 30, 72], np.float32)     # синяя паста на фото: густо
INK_LIGHT = np.array([92, 104, 178], np.float32)  # и там, где ручка едва касалась
PAPER = (226, 229, 233)
GRID = (120, 146, 196)
MARGIN = (226, 106, 92)

XH, DIG = 0.88, 0.95          # высота строчной и цифры, в клетках
SUP = 0.68                    # степень
GAP = 0.17                    # промежуток между знаками, в клетках (пишет свободно)
SPACE = 0.26                  # пробел (вокруг + − =)
BASE = {'y': 'desc', '(': 'paren', ')': 'paren', '+': 'axis'}

STAMPS = {}
if os.path.exists(BANK_FILE):
    _z = np.load(BANK_FILE)
    for _i, _ch in enumerate(json.loads(bytes(_z['meta']).decode())):
        STAMPS.setdefault(_ch, []).append(_z['g%d' % _i].astype(np.float32) / 255)

# знаки, которых нет на фото, — пером (в клетках: x вправо, y вверх от строки)
LINE = {
    '−': ([[(0.05, 0.42), (0.62, 0.45)]], 0.67),
    '-': ([[(0.05, 0.42), (0.62, 0.45)]], 0.67),
    '=': ([[(0.05, 0.56), (0.62, 0.58)], [(0.06, 0.28), (0.63, 0.30)]], 0.68),
    '·': ([[(0.08, 0.43), (0.13, 0.47), (0.1, 0.42)]], 0.24),
    ':': ([[(0.08, 0.65), (0.13, 0.69), (0.1, 0.64)], [(0.08, 0.2), (0.13, 0.24), (0.1, 0.19)]], 0.24),
    ',': ([[(0.12, 0.08), (0.04, -0.2)]], 0.2),
    '.': ([[(0.1, 0.04), (0.12, 0.06)]], 0.2),
}
# a b z на фото тетради нет: формы — как он пишет (b с прямой палочкой, чтобы не путалась с 6)
LINE.update({
    'a': ([[(0.52, 0.62), (0.32, 0.8), (0.1, 0.62), (0.05, 0.3), (0.18, 0.04), (0.4, 0.08), (0.53, 0.35)],
           [(0.55, 0.82), (0.52, 0.4), (0.56, 0.08), (0.68, 0.02)]], 0.72),
    'b': ([[(0.16, 1.38), (0.12, 0.8), (0.1, 0.04)],
           [(0.11, 0.42), (0.3, 0.72), (0.52, 0.66), (0.6, 0.36), (0.45, 0.06), (0.22, 0.02), (0.1, 0.1)]], 0.68),
    'z': ([[(0.06, 0.66), (0.22, 0.76), (0.58, 0.74), (0.28, 0.38), (0.05, 0.04), (0.34, 0.08), (0.66, 0.02)]], 0.72),
})
INK_PEN = 0.62      # густота пасты у знаков пером — как у его настоящих букв на фото
PAREN = 1.3         # высота обычной скобки, в клетках


def _spline(P, n=8):
    """Сглаживает ломаную (Катмулл — Ром), чтобы знаки пером не были угловатыми."""
    if len(P) < 3:
        return P
    Q = [P[0]] + list(P) + [P[-1]]
    out = []
    for i in range(1, len(Q) - 2):
        p0, p1, p2, p3 = Q[i - 1], Q[i], Q[i + 1], Q[i + 2]
        for k in range(n):
            t = k / n
            out.append(tuple(0.5 * (2 * p1[j] + (-p0[j] + p2[j]) * t + (2 * p0[j] - 5 * p1[j] + 4 * p2[j] - p3[j]) * t * t
                                    + (-p0[j] + 3 * p1[j] - 3 * p2[j] + p3[j]) * t ** 3) for j in (0, 1)))
    return out + [P[-1]]


def tokens(s):
    """Строку — в знаки: [(знак, степень?)]; «^2», «^{18}» — степень."""
    out, i = [], 0
    while i < len(s):
        if s[i] == '^':
            if s[i + 1] == '{':
                j = s.index('}', i)
                out += [(c, True) for c in s[i + 2:j]]
                i = j + 1
            else:
                out.append((s[i + 1], True))
                i += 2
            continue
        out.append((s[i], False))
        i += 1
    return out


class Notebook:
    def __init__(self, cols=30, rows=40, cell=40, seed=1, margin=4):
        self.C, self.cols, self.rows = cell, cols, rows
        self.W, self.H = cols * cell, rows * cell
        self.ink = np.zeros((self.H, self.W), np.float32)
        self.rnd = random.Random(seed)
        self.left = (margin + 0.8) * cell          # пишет сразу за красной линией полей
        self.right = self.W - 0.8 * cell
        self.margin = margin
        self.y = 1.2 * cell                         # низ последней написанной строки, px
        self._last = {}

    # ---------- перо и штампы ----------
    def _pen(self, pts, x, base, size=1.0, w=PEN):
        """Штрих пером: pts — в клетках (y вверх от строки)."""
        C, ss = self.C, 3
        P = _spline([(x + px * C * size, base - py * C * size) for px, py in pts])
        dense = [P[0]]
        for a, b in zip(P, P[1:]):
            n = max(2, int(math.hypot(b[0] - a[0], b[1] - a[1]) / 0.7))
            dense += [(a[0] + (b[0] - a[0]) * t / n, a[1] + (b[1] - a[1]) * t / n) for t in range(1, n + 1)]
        xs, ys = [p[0] for p in dense], [p[1] for p in dense]
        r = w * C / 2 * (0.85 + 0.3 * self.rnd.random())
        x0, y0 = int(min(xs) - r - 3), int(min(ys) - r - 3)
        x1, y1 = int(max(xs) + r + 3), int(max(ys) + r + 3)
        im = Image.new('L', ((x1 - x0) * ss, (y1 - y0) * ss))
        d = ImageDraw.Draw(im)
        ph = self.rnd.random() * 6
        for k, (px, py) in enumerate(dense):
            rr = r * (0.9 + 0.12 * math.sin(k / 9 + ph)) * ss   # нажим чуть гуляет
            cx, cy = (px - x0) * ss, (py - y0) * ss
            d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=int(255 * INK_PEN))
        a = np.asarray(im.resize((x1 - x0, y1 - y0), Image.LANCZOS), np.float32) / 255
        self._blit(a, x0, y0)

    def _blit(self, a, x0, y0):
        h, w = a.shape
        X0, Y0 = max(0, x0), max(0, y0)
        X1, Y1 = min(self.W, x0 + w), min(self.H, y0 + h)
        if X1 <= X0 or Y1 <= Y0:
            return
        sub = a[Y0 - y0:Y1 - y0, X0 - x0:X1 - x0]
        cur = self.ink[Y0:Y1, X0:X1]
        self.ink[Y0:Y1, X0:X1] = 1 - (1 - cur) * (1 - sub)

    def _stamp(self, ch, scale, height=None):
        """Случайный его образец знака (не тот же, что в прошлый раз), чуть повернутый и масштабированный;
        height — нужная высота в клетках (высокая скобка у дроби)."""
        opts = STAMPS[ch]
        i = self.rnd.randrange(len(opts))
        if len(opts) > 1 and i == self._last.get(ch):
            i = (i + 1) % len(opts)
        self._last[ch] = i
        a = opts[i]
        s = (height * self.C / a.shape[0] if height else scale * self.C / REF) * (1 + self.rnd.uniform(-0.05, 0.05))
        im = Image.fromarray((a * 255).astype(np.uint8))
        im = im.resize((max(1, round(a.shape[1] * s)), max(1, round(a.shape[0] * s))), Image.LANCZOS)
        im = im.rotate(self.rnd.uniform(-3, 3), Image.BICUBIC, expand=True)
        out = np.asarray(im, np.float32) / 255
        ys, xs = np.nonzero(out > 0.12)
        return out[ys.min():ys.max() + 1, xs.min():xs.max() + 1]

    # ---------- раскладка ----------
    def _glyph(self, ch, size, tall=None):
        """Знак → (функция рисования(x, base), ширина px)."""
        C = self.C
        if ch == ' ':
            return None, SPACE * C * size
        if ch in LINE:
            st, w = LINE[ch]
            jit = [[(px + self.rnd.uniform(-0.02, 0.02), py + self.rnd.uniform(-0.02, 0.02)) for px, py in s] for s in st]
            return (lambda x, b: [self._pen(s, x, b, size) for s in jit]), w * C * size
        if ch not in STAMPS:
            raise KeyError(f'нет знака {ch!r} в почерке тетради')
        kind = BASE.get(ch, 'base')
        if kind == 'paren' and tall:
            # высокая скобка у дроби — дугой его пера (растянутые штампы выходили квадратными «[»)
            h, k = tall / 2, (1 if ch == '(' else -1)
            xs = [0.34, 0.13, 0.07, 0.11, 0.33] if ch == '(' else [0.06, 0.27, 0.33, 0.29, 0.08]
            j = [self.rnd.uniform(-0.03, 0.03) for _ in xs]
            pts = [(x + d, 0.45 + y) for x, d, y in zip(xs, j, (h, h / 2, 0, -h / 2, -h))]
            return (lambda x, b: self._pen(pts, x, b)), 0.42 * C
        st = self._stamp(ch, size, PAREN * size if kind == 'paren' else None)
        h, w = st.shape
        dy = self.rnd.uniform(-0.04, 0.04) * C

        def draw(x, b):
            if kind == 'desc':
                top = b - XH * C * size
            elif kind == 'paren':
                top = b - 0.45 * C * size - h / 2
            elif kind == 'axis':
                top = b - 0.45 * C * size - h / 2
            else:
                top = b - h
            self._blit(st, int(round(x)), int(round(top + dy)))
        return draw, w

    def _run(self, s, size=1.0, tall=None):
        """Строку — в список (dx, dy, рисование); возвращает (список, ширина)."""
        C, x, out = self.C, 0.0, []
        toks = tokens(s)
        for k, (ch, sup) in enumerate(toks):
            sz = size * (SUP if sup else 1)
            t = tall if ch in '()' else None
            draw, w = self._glyph(ch, sz, t)
            if sup and k and not toks[k - 1][1]:
                x -= 0.02 * C
            if draw:
                out.append((x, -0.55 * C * size if sup else 0, draw))
            x += w + (GAP + self.rnd.uniform(-0.03, 0.04)) * C * (SUP if sup else 1)
        return out, x

    @staticmethod
    def _ext(s):
        """Сколько строка поднимается над своей строкой и опускается под неё, в клетках."""
        up, down = XH, 0.0
        for ch, sup in tokens(s):
            if sup:
                up = max(up, 0.55 + SUP * DIG)
            elif ch == 'b':
                up = max(up, 1.4)
            elif ch in '()':
                up, down = max(up, 0.45 + PAREN / 2), max(down, PAREN / 2 - 0.45)
            elif ch == 'y':
                down = max(down, 0.82)
            elif ch.isdigit() or ch == 't':
                up = max(up, DIG + 0.03)
        return up, down

    def _frac(self, num, den):
        C = self.C
        n, wn = self._run(num)
        d, wd = self._run(den)
        bw = max(wn, wd) + 0.3 * C
        return n, wn, d, wd, bw

    def _layout(self, items):
        """Кусок строки → список (x, тип, данные) от x=0; ширина. Ось — линия клетки (черта дроби)."""
        C, x, out = self.C, 0.0, []
        has = [isinstance(it, tuple) for it in items]
        for k, it in enumerate(items):
            if isinstance(it, tuple):
                n, wn, d, wd, bw = self._frac(*it)
                out.append((x, 'frac', (n, wn, d, wd, bw, it[0], it[1])))
                x += bw + 0.12 * C
                continue
            s = it
            # скобки вплотную к дроби — высокие, на всю дробь: «)^3 : (», «(−», «)^2 · (− »
            if s.lstrip().startswith(')') and k and has[k - 1]:
                draw, w = self._glyph(')', 1, 2.3)
                out.append((x - 0.05 * C, 'tall', draw))
                x += w + 0.04 * C
                s = s.lstrip()[1:]
                if s.startswith('^'):
                    j = s.index('}') + 1 if s[1] == '{' else 2
                    r, w = self._run(s[:j])
                    out.append((x, 'suptall', r))
                    x += w
                    s = s[j:]
            tail = ''
            if k + 1 < len(items) and has[k + 1] and '(' in s and not s[s.rindex('(') + 1:].strip(' −-'):
                s, tail = s[:s.rindex('(')], s[s.rindex('(') + 1:]
                if s:
                    r, w = self._run(s)
                    out.append((x, 'run', r))
                    x += w
                draw, w = self._glyph('(', 1, 2.3)
                out.append((x, 'tall', draw))
                x += w + 0.1 * C
                s = tail.strip()
            if s:
                r, w = self._run(s)
                out.append((x, 'run', r))
                x += w + (0.08 * C if tail else 0)
        return out, x

    def _draw(self, lay, x0, axis):
        C = self.C
        base = axis + 0.45 * C                       # строка обычных знаков: середина буквы — на линии
        for x, kind, data in lay:
            if kind == 'run':
                for dx, dy, draw in data:
                    draw(x0 + x + dx, base + dy)
            elif kind == 'suptall':
                for dx, dy, draw in data:
                    draw(x0 + x + dx, axis - 0.75 * C)
            elif kind == 'tall':
                data(x0 + x, base)
            else:
                n, wn, d, wd, bw = data[:5]
                xb = x0 + x
                # черта: по линии клетки, чуть вкось и с прогибом, как от руки
                y1, y2 = self.rnd.uniform(-0.05, 0.05), self.rnd.uniform(-0.07, 0.03)
                mid = self.rnd.uniform(-0.03, 0.03)
                L = bw / C
                self._pen([(0, y1), (L / 2, (y1 + y2) / 2 + mid), (L, y2)], xb, axis - 0.02 * C)
                # числитель — над чертой с зазором по хвостам (y, скобки), знаменатель — под ней по верху букв
                nb = axis - (0.14 + self._ext(data[5])[1]) * C
                db = axis + (0.16 + self._ext(data[6])[0]) * C
                for dx, dy, draw in n:
                    draw(xb + (bw - wn) / 2 + dx, nb + dy)
                for dx, dy, draw in d:
                    draw(xb + (bw - wd) / 2 + dx, db + dy)

    def _vext(self, items):
        """Сколько кусок строки занимает над линией (осью) и под ней, в клетках."""
        up = down = 0.0
        for it in items:
            if isinstance(it, tuple):
                nu, nd = self._ext(it[0])
                du, dd = self._ext(it[1])
                up, down = max(up, 0.14 + nd + nu), max(down, 0.16 + du + dd)
            else:
                u, d = self._ext(it)
                up, down = max(up, u - 0.45), max(down, 0.45 + d)
                if ('(' in it or ')' in it) and any(isinstance(j, tuple) for j in items):
                    up, down = max(up, 1.15), max(down, 1.15)    # высокая скобка у дроби
        return up, down

    # ---------- текст на листе ----------
    def title(self, text):
        """Заголовок по центру («Лист 7»)."""
        C = self.C
        word, rest = (text.split(' ', 1) + [''])[:2] if text.startswith('Лист') else ('', text)
        st = self._stamp('Лист', 1.3) if word == 'Лист' else None
        r, w = self._run(rest.strip(), 1.2)
        tw = (st.shape[1] + 0.45 * C if st is not None else 0) + w
        x = (self.left + self.right) / 2 - tw / 2
        base = round(self.y / C + 1.6) * C
        if st is not None:
            self._blit(st, int(x), int(base - st.shape[0] + 0.1 * C))
            x += st.shape[1] + 0.45 * C
        for dx, dy, draw in r:
            draw(x + dx, base + dy)
        self.y = base + 0.6 * C

    def solve(self, n, lines):
        """Номер: «n)» и строки решения. Строка — список кусков; кусок, который не влез, — на следующую строку.
        Высота строки — по содержимому: дробь с «b» в знаменателе занимает больше клеток."""
        C = self.C
        lab, lw = self._run(f'{n})')
        first = True
        for line in lines:
            chunks = line if line and isinstance(line[0], list) else [line]
            rows, cur = [], []
            x = self.left + (lw + 0.45 * C if first else 0.6 * C)
            for ch in chunks:
                lay, w = self._layout(ch)
                if cur and x + w > self.right:
                    rows.append(cur)
                    cur, x = [], self.left + 0.6 * C
                if x + w > self.right:
                    raise ValueError('кусок не влезает в строку тетради: ' + repr(ch))
                cur.append((x, lay, self._vext(ch)))
                x += w + 0.15 * C
            rows.append(cur)
            for r in rows:
                up = max(v[0] for _, _, v in r)
                down = max(v[1] for _, _, v in r)
                axis = math.ceil((self.y + up * C) / C) * C   # черта дроби — по линии клетки
                if first:
                    for dx, dy, draw in lab:
                        draw(self.left + dx, axis + 0.45 * C + dy)
                    first = False
                for x, lay, _ in r:
                    self._draw(lay, x, axis)
                self.y = axis + down * C
        self.y += 0.5 * C
        if self.y > self.H - 0.6 * C:
            raise ValueError('лист закончился — нужна вторая страница')

    def fits(self, n, lines):
        """Влезет ли номер на этот лист (пробно, на копии)."""
        import copy
        t = copy.deepcopy(self)
        try:
            t.solve(n, lines)
            return True
        except ValueError:
            return False

    # ---------- картинка ----------
    def page(self):
        """Ровный лист: бумага, клетка, поля, чернила."""
        C, W, H = self.C, self.W, self.H
        rng = np.random.default_rng(self.rnd.randrange(1 << 30))
        paper = np.ones((H, W, 3), np.float32) * np.array(PAPER, np.float32)
        paper += rng.normal(0, 2.0, (H, W, 1))
        grid = np.zeros((H, W), np.float32)
        for k in range(1, self.cols):          # линии клетки — в 2 px, чуть неровной яркости, как печать тетради
            xi = int(k * C + rng.normal(0, 0.3))
            grid[:, xi] = np.maximum(grid[:, xi], 0.62)
            grid[:, xi + 1] = np.maximum(grid[:, xi + 1], 0.38)
        for k in range(1, self.rows):
            yi = int(k * C)
            grid[yi, :] = np.maximum(grid[yi, :], 0.62)
            grid[min(H - 1, yi + 1), :] = np.maximum(grid[min(H - 1, yi + 1), :], 0.38)
        grid *= (0.85 + 0.15 * rng.random((H, 1))) * (0.85 + 0.15 * rng.random((1, W)))
        paper = paper * (1 - grid[..., None]) + np.array(GRID, np.float32) * grid[..., None]
        mx = int(self.margin * C - C * 0.25)
        for dx, al in ((0, 0.8), (1, 0.7), (2, 0.25), (-1, 0.25)):
            paper[:, mx + dx] = paper[:, mx + dx] * (1 - al) + np.array(MARGIN, np.float32) * al
        a = np.clip(self.ink, 0, 1) * (1 - 0.16 * rng.random((H, W)).astype(np.float32))   # зерно шарика
        col = INK_LIGHT[None, None] + (INK_DARK - INK_LIGHT)[None, None] * np.clip(a * 1.5, 0, 1)[..., None]
        k = np.clip(a * 1.35, 0, 1)[..., None]
        out = paper * (1 - k) + col * k
        return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))

    def photo(self, size=(2016, 1512)):
        """Лист, снятый телефоном на столе (как его фото): перспектива, поворот, свет пятном, тень, шум камеры."""
        rng = np.random.default_rng(self.rnd.randrange(1 << 30))
        W, H = size
        pg = self.page()
        ph = int(H * self.rnd.uniform(0.86, 0.9))
        pw = int(pg.size[0] * ph / pg.size[1])
        pg = pg.resize((pw, ph), Image.LANCZOS)
        # край листа: углы чуть скруглены, со стороны корешка — мелкие зубчики от вырванного листа
        mask = Image.new('L', (pw, ph), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, pw - 1, ph - 1], radius=10, fill=255)
        md = ImageDraw.Draw(mask)
        side = self.rnd.random() < 0.5
        y = 30
        while y < ph - 30:
            r = self.rnd.uniform(2, 5)
            cx = -r * 0.4 if side else pw + r * 0.4
            md.ellipse([cx - r, y - r, cx + r, y + r], fill=0)
            y += self.rnd.uniform(25, 90)
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        lx, ly = W * self.rnd.uniform(0.45, 0.75), H * self.rnd.uniform(0.15, 0.45)
        light = 1.0 - 0.22 * np.sqrt(((xx - lx) / W) ** 2 + ((yy - ly) / H) ** 2) * 1.6
        table = np.array([206, 200, 186], np.float32) * light[..., None] + rng.normal(0, 1.4, (H, W, 1))
        ang = math.radians(self.rnd.uniform(-2.5, 2.5))
        cx, cy = W / 2 + self.rnd.uniform(-90, 90), H / 2 + self.rnd.uniform(-15, 20)
        k = self.rnd.uniform(0.015, 0.045)                     # телефон чуть наклонён: верх листа уже низа
        corners = [(-pw / 2 * (1 - k), -ph / 2), (pw / 2 * (1 - k), -ph / 2), (pw / 2, ph / 2), (-pw / 2, ph / 2)]
        dst = [(cx + x * math.cos(ang) - y * math.sin(ang), cy + x * math.sin(ang) + y * math.cos(ang)) for x, y in corners]
        coef = _persp(dst, [(0, 0), (pw, 0), (pw, ph), (0, ph)])
        warped = pg.transform((W, H), Image.PERSPECTIVE, coef, Image.BICUBIC)
        wmask = mask.transform((W, H), Image.PERSPECTIVE, coef, Image.BICUBIC)
        m = np.asarray(wmask, np.float32)[..., None] / 255
        shadow = np.asarray(wmask.filter(ImageFilter.GaussianBlur(10)), np.float32)[..., None] / 255
        table = table * (1 - 0.13 * np.roll(np.roll(shadow, 5, 0), 3, 1))
        # лист не идеально ровный: широкие мягкие перепады света (изгиб бумаги)
        low = rng.normal(0, 1, (6, 8)).astype(np.float32)
        bend = np.asarray(Image.fromarray(low).resize((W, H), Image.BICUBIC), np.float32)
        sheet = np.asarray(warped, np.float32) * (light * 0.9 * (1 + 0.025 * bend))[..., None]
        out = table * (1 - m) + sheet * m
        out = out * np.array([0.985, 0.99, 1.0], np.float32) + rng.normal(0, 2.2, (H, W, 3))
        img = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))
        return img.filter(ImageFilter.GaussianBlur(0.5))

    def save(self, path, photo=True):
        (self.photo() if photo else self.page()).save(path, quality=90)
        return path


def _persp(dst, src):
    """Коэффициенты PIL PERSPECTIVE: точка результата (dst) → точка листа (src)."""
    A, B = [], []
    for (x, y), (u, v) in zip(dst, src):
        A.append([x, y, 1, 0, 0, 0, -u * x, -u * y]); B.append(u)
        A.append([0, 0, 0, x, y, 1, -v * x, -v * y]); B.append(v)
    return np.linalg.solve(np.array(A, float), np.array(B, float)).tolist()
