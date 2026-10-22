"""Решение в бумажной тетради «как у Глеба» (Глеб, 22.10: «научись делать, чтобы выглядело, как будто я писал»).

Учитель часто просит «выполнить в тетради» — тогда нужен не тёмный лист iPad (sheet.py), а фото листа в клетку:
синяя шариковая ручка, красные поля, лист лежит на столе.
- Буквы и цифры — его настоящие, вырезаны с фото его тетради (`horo/fonts/gleb_paper.npz`, собирает
  `horo/live/paper_build.py` с фото в Brain `raw/images/notebook_*.jpg`): a b c m t x y z 0–9 + − = · ( ) №, «Лист»,
  черты дробей, зачёркивания. Ничего не дорисовывается (Глеб 22.10: «точь-в-точь, без твоих выдумок»): нет знака — ошибка.
- Как он: сокращаемое зачёркивает косой линией (его же черта дроби, повёрнутая) — «~(z + 3)~»;
  описку закрашивает своим зачёркиванием и пишет дальше — «#2z#».
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
INK_DARK = np.array([26, 30, 72], np.float32)     # синяя паста на фото: густо
INK_LIGHT = np.array([92, 104, 178], np.float32)  # и там, где ручка едва касалась
PAPER = (226, 229, 233)
GRID = (120, 146, 196)
MARGIN = (226, 106, 92)

XH, DIG = 0.88, 0.95          # высота строчной и цифры, в клетках
SUP = 0.68                    # степень
GAP = 0.17                    # промежуток между знаками, в клетках (пишет свободно)
# его рука по замерам 75 фото тетради (Глеб 22.10: «пока видно, что написано ИИ»):
JIT_Y = 0.07       # низ соседних букв гуляет: σ ≈ 0.15 клетки между соседями → у каждой ±0.07
JIT_S = 0.09       # размер соседних букв: 0.78…1.24 друг от друга
JIT_GAP = 0.08     # промежуток: медиана 0.22, бывает вплотную и до 0.5
SLOPE = 1.3        # строка в целом чуть уходит вверх/вниз, °
WAVE = 0.1         # и плывёт волной, клетки
FRAC_GAP = 0.08    # от черты до числителя/знаменателя (у него почти вплотную)
ROW_GAP = 0.12     # между строками решения (шаг строк дробей ≈ 2.6 клетки, не по линиям клетки)
SPACE = 0.26                  # пробел (вокруг + − =)
BASE = {'y': 'desc', '(': 'paren', ')': 'paren', '+': 'axis', '=': 'axis', '−': 'axis', '·': 'axis', ':': 'colon'}

STAMPS = {}
if os.path.exists(BANK_FILE):
    _z = np.load(BANK_FILE)
    for _i, _ch in enumerate(json.loads(bytes(_z['meta']).decode())):
        STAMPS.setdefault(_ch, []).append(_z['g%d' % _i].astype(np.float32) / 255)

PAREN = 1.3         # высота обычной скобки, в клетках
TALL = 1.75         # скобки выше этого (в клетках) — его высокие скобки у дробей
ALIAS = {'-': '−'}


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
    def __init__(self, cols=30, rows=40, cell=40, seed=1, margin=4, right=0.8):
        self.C, self.cols, self.rows = cell, cols, rows
        self.W, self.H = cols * cell, rows * cell
        self.ink = np.zeros((self.H, self.W), np.float32)
        self.rnd = random.Random(seed)
        self.left = (margin + 0.8) * cell          # пишет сразу за красной линией полей
        self.right = self.W - right * cell
        self.drift = None                           # плывущая строка: y-сдвиг от x, задаёт solve
        self.margin = margin
        self.y = 1.2 * cell                         # низ последней написанной строки, px
        self._last = {}

    # ---------- его штампы ----------
    def _line(self, cx, cy, length, angle=0.0):
        """Его прямая линия пером (черта дроби с фото) длиной length px, повернутая на angle°, центр (cx, cy)."""
        opts = STAMPS['bar']
        want = length / self.C * REF
        cand = sorted(range(len(opts)), key=lambda i: abs(opts[i].shape[1] - want))[:6]
        a = opts[self.rnd.choice(cand)]
        k = self.C / REF
        im = Image.fromarray((a * 255).astype(np.uint8)).resize((max(2, round(length)), max(2, round(a.shape[0] * k))), Image.LANCZOS)
        im = im.rotate(angle + self.rnd.uniform(-1.2, 1.2), Image.BICUBIC, expand=True)
        st = np.asarray(im, np.float32) / 255
        self._blit(st, int(round(cx - st.shape[1] / 2)), int(round(cy - st.shape[0] / 2)))

    def _strike(self, x0, x1, cy):
        """Сокращение: косая линия через множитель снизу слева вверх направо, как у него."""
        w = x1 - x0
        h = min(1.6 * self.C, max(0.9 * self.C, 0.5 * w)) * self.rnd.uniform(0.9, 1.1)   # круче, как у него
        ang = math.degrees(math.atan2(h, w))
        self._line((x0 + x1) / 2, cy, math.hypot(w * 1.12, h), ang)

    def _scribble(self, x0, x1, cy):
        """Описка: его зачёркивание с фото, растянутое на написанное."""
        a = self.rnd.choice(STAMPS['scribble'])
        w = max(x1 - x0, 0.6 * self.C) * 1.1
        h = self.C * 1.25
        st = np.asarray(Image.fromarray((a * 255).astype(np.uint8)).resize((round(w), round(h)), Image.LANCZOS), np.float32) / 255
        self._blit(st, int(round((x0 + x1) / 2 - w / 2)), int(round(cy - h / 2)))

    def _blit(self, a, x0, y0):
        h, w = a.shape
        X0, Y0 = max(0, x0), max(0, y0)
        X1, Y1 = min(self.W, x0 + w), min(self.H, y0 + h)
        if X1 <= X0 or Y1 <= Y0:
            return
        if self.drift:
            dy = int(round(self.drift(x0 + w / 2)))
            y0 += dy
            Y0, Y1 = max(0, y0), min(self.H, y0 + h)
            if Y1 <= Y0:
                return
        sub = a[Y0 - y0:Y1 - y0, X0 - x0:X1 - x0]
        cur = self.ink[Y0:Y1, X0:X1]
        self.ink[Y0:Y1, X0:X1] = 1 - (1 - cur) * (1 - sub)

    def _stamp(self, ch, scale, height=None, opts=None):
        """Случайный его образец знака (не тот же, что в прошлый раз), чуть повернутый и масштабированный;
        height — нужная высота в клетках (высокая скобка у дроби)."""
        opts = opts or STAMPS[ch]
        i = self.rnd.randrange(len(opts))
        if len(opts) > 1 and i == self._last.get(ch):
            i = (i + 1) % len(opts)
        self._last[ch] = i
        a = opts[i]
        j = max(-2 * JIT_S, min(2 * JIT_S, self.rnd.gauss(0, JIT_S)))
        s = (height * self.C / a.shape[0] if height else scale * self.C / REF) * (1 + j)
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
        ch = ALIAS.get(ch, ch)
        kind = BASE.get(ch, 'base')
        if kind == 'colon':                       # «:» — две его точки одна над другой
            d1, d2 = self._stamp('·', size), self._stamp('·', size)
            w = max(d1.shape[1], d2.shape[1])

            def colon(x, b):
                self._blit(d1, int(x), int(b - 0.68 * C * size - d1.shape[0] / 2))
                self._blit(d2, int(x), int(b - 0.22 * C * size - d2.shape[0] / 2))
            return colon, w
        if ch not in STAMPS:
            raise KeyError(f'нет знака {ch!r} в почерке тетради — нужен его образец с фото')
        if kind == 'paren' and tall:              # его высокие скобки у дробей (с фото), до нужной высоты
            opts = [a for a in STAMPS[ch] if a.shape[0] / REF >= TALL] or STAMPS[ch]
            st = self._stamp(ch, 1, tall, opts)
            h, w = st.shape
            return (lambda x, b: self._blit(st, int(round(x)), int(round(b - 0.45 * C - h / 2)))), w
        tall = None
        opts = [a for a in STAMPS[ch] if a.shape[0] / REF < TALL] if kind == 'paren' else None
        st = self._stamp(ch, size, PAREN * size if kind == 'paren' else None, opts)
        h, w = st.shape
        dy = max(-2 * JIT_Y, min(2 * JIT_Y, self.rnd.gauss(0, JIT_Y))) * C

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
        """Строку — в список (dx, dy, рисование); возвращает (список, ширина).
        «~…~» — зачеркнуть косой линией (сократил), «#…#» — описка, закрашена его зачёркиванием."""
        C, x, out, marks = self.C, 0.0, [], []   # marks — зачёркивания, рисуются поверх букв
        toks = tokens(s)
        open_ = {}
        for k, (ch, sup) in enumerate(toks):
            if ch in '~#':
                if ch not in open_:
                    open_[ch] = x
                    continue
                x0, x1 = open_.pop(ch), x - GAP * C
                mark = self._strike if ch == '~' else self._scribble
                marks.append((x0, 0, lambda X, b, w=x1 - x0, m=mark: m(X, X + w, b - 0.45 * C * size)))
                if ch == '#':
                    x += 0.25 * C
                continue
            sz = size * (SUP if sup else 1)
            t = tall if ch in '()' else None
            draw, w = self._glyph(ch, sz, t)
            if sup and k and not toks[k - 1][1]:
                x -= 0.02 * C
            if draw:
                out.append((x, -0.55 * C * size if sup else 0, draw))
            g = max(-0.04, min(0.4, self.rnd.gauss(GAP, JIT_GAP)))
            x += w + g * C * (SUP if sup else 1)
        return out + marks, x

    @staticmethod
    def _ext(s):
        """Сколько строка поднимается над своей строкой и опускается под неё, в клетках."""
        up, down = XH, 0.0
        for ch, sup in tokens(s):
            if sup:
                up = max(up, 0.55 + SUP * DIG)
            elif ch == 'b':
                up = max(up, 1.5)
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
                # черта — его линия с фото, по линии клетки
                self._line(xb + bw / 2, axis - 0.02 * C + self.rnd.uniform(-0.04, 0.04) * C, bw)
                # числитель — над чертой с зазором по хвостам (y, скобки), знаменатель — под ней по верху букв
                nb = axis - (FRAC_GAP + self._ext(data[5])[1]) * C
                db = axis + (FRAC_GAP + 0.02 + self._ext(data[6])[0]) * C
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
                up, down = max(up, FRAC_GAP + nd + nu), max(down, FRAC_GAP + 0.02 + du + dd)
            else:
                u, d = self._ext(it)
                up, down = max(up, u - 0.45), max(down, 0.45 + d)
                if ('(' in it or ')' in it) and any(isinstance(j, tuple) for j in items):
                    up, down = max(up, 1.15), max(down, 1.15)    # высокая скобка у дроби
        return up, down

    def _row_drift(self, row, axis):
        """На сколько px плывущая строка ушла вниз к своему правому краю (чтобы следующая не наехала)."""
        return max(self.drift(x) for x in (self.left, self.right)) if self.drift else 0

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
                    if isinstance(ch[0], str) and ch[0].lstrip().startswith('='):
                        # как он: строка, оборванная на «=», кончается «=», и следующая начинается с «=»
                        eq, ew = self._layout(['='])
                        cur.append((x, eq, self._vext(['='])))
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
                axis = self.y + (up + ROW_GAP + self.rnd.uniform(0, 0.25)) * C   # не по линиям клетки — как у него
                k, A, ph = math.tan(math.radians(self.rnd.gauss(0, SLOPE / 2))), self.rnd.uniform(0, WAVE), self.rnd.uniform(0, 6.3)
                x0r = self.left
                self.drift = lambda X, k=k, A=A, ph=ph, x0r=x0r: k * (X - x0r) + A * C * math.sin((X - x0r) / (5 * C) + ph)
                if first:
                    for dx, dy, draw in lab:
                        draw(self.left + dx, axis + 0.45 * C + dy)
                    first = False
                for x, lay, _ in r:
                    self._draw(lay, x, axis)
                self.y = axis + down * C + max(0, self._row_drift(r, axis))
                self.drift = None
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
