"""Буквы Глеба с фото его бумажной тетради → horo/fonts/gleb_paper.npz (рисует horo/paper.py).
PAPER_SRC=<папка с фото notebook_1..4.jpg из Brain raw/images> python3 horo/live/paper_build.py [--tiles]
Нужен OpenCV: pip install opencv-python-headless.
Как: чернила — насколько пиксель темнее бумаги вокруг по красному каналу (клетка светлая, паста тёмная);
черты дробей убираются (длинные горизонтали), куски — связные области; какие куски какие знаки — paper_samples.py.
Каждый знак приводится к высоте своего класса в клетках (наполовину: его разброс размера остаётся)."""
import json, os, sys, collections
import cv2
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paper_samples import SEL, SIGNS, MESS, DROP

SRC = os.environ.get('PAPER_SRC', '.')
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'fonts', 'gleb_paper.npz')
REF = 40   # клетка банка, px (paper.REF)
TARGET = {'x': .88, 'c': .82, 'm': .86, 't': .98, 'y': 1.7, '+': .86, '№': 1.5, 'Лист': 2.0, '0': .92,
          'a': .86, 'z': .86, 'b': 1.45, 'b!': 1.45}
KEEP = {'(', ')', '=', '−', '·', 'bar', 'scribble'}   # эти — в его размере, без приведения к классу
DIG = .95


def photo(f):
    return os.path.join(SRC, 'notebook_%s.jpg' % f[1:-4])


def cell(d):
    """Шаг клетки, px — по автокорреляции слабых (светлых) линий."""
    g = ((d > 8) & (d < 45)).astype(np.float32)
    p = g.mean(axis=0); p = p - p.mean()
    ac = np.correlate(p, p, 'full')[len(p) - 1:]
    return int(np.argmax(ac[18:60]) + 18)


def pieces(f):
    im = cv2.imread(photo(f))
    bg = cv2.medianBlur(im, 61).astype(np.float32)
    d = (bg - im.astype(np.float32))[..., 2]
    c = cell(d)
    m = (d > 55).astype(np.uint8)
    bars = cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (int(c * 1.6), 1)))
    m = m * (1 - cv2.dilate(bars, np.ones((3, 3), np.uint8)))
    n, lab, st, _ = cv2.connectedComponentsWithStats(m, 8)
    comps = [dict(x=int(x), y=int(y), w=int(w), h=int(h), lab=i) for i, (x, y, w, h, a) in enumerate(st) if i and a >= 15]
    comps.sort(key=lambda r: (r['y'] // 40, r['x']))
    return d, lab, c, comps


def tiles(f, d, lab, comps, T=70, cols=20):
    rows = (len(comps) + cols - 1) // cols
    sh = np.full((rows * (T + 14), cols * T, 3), 255, np.uint8)
    for k, r in enumerate(comps):
        x, y, w, h = r['x'], r['y'], r['w'], r['h']
        a = np.where(lab[y:y + h, x:x + w] == r['lab'], np.clip(d[y:y + h, x:x + w], 0, 255), 0)
        s = min(1, (T - 4) / max(w, h))
        t = cv2.resize((255 - a).astype(np.uint8), (max(1, int(w * s)), max(1, int(h * s))))
        R, C = divmod(k, cols)
        sh[R * (T + 14) + 12:R * (T + 14) + 12 + t.shape[0], C * T + 2:C * T + 2 + t.shape[1]] = t[..., None]
        cv2.putText(sh, str(k), (C * T + 2, R * (T + 14) + 10), cv2.FONT_HERSHEY_SIMPLEX, 0.33, (0, 0, 255), 1)
    cv2.imwrite(f[:-4] + '_tiles.png', sh)


CLEAN = set('ab')   # у букв под/над ними бывает кусок черты дроби — отрезается


def clean(a):
    """Срезает с краёв буквы прилипшую черту: строку, где чернила идут почти через всю ширину."""
    h, w = a.shape
    for rows in (range(h - 1, int(h * 0.8), -1), range(0, int(h * 0.2))):
        cut_at = None
        for y in rows:
            if (a[y] > 0.3).mean() > 0.85:   # черта идёт через весь кусок, верх буквы — нет
                cut_at = y
        if cut_at is not None:
            if rows.step == -1:
                a = a[:max(1, cut_at - 2)]
            else:
                a = a[cut_at + 3:]
            h = a.shape[0]
    return a


def cut(d, lab, peak, rs, M=3):
    """Чернила куска (или нескольких — «=») по его маске, 0..1."""
    x0, y0 = max(0, min(r['x'] for r in rs) - M), max(0, min(r['y'] for r in rs) - M)
    x1, y1 = max(r['x'] + r['w'] for r in rs) + M, max(r['y'] + r['h'] for r in rs) + M
    m = np.zeros((y1 - y0, x1 - x0), np.uint8)
    for r in rs:
        m |= (lab[y0:y1, x0:x1] == r['lab']).astype(np.uint8)
    m = cv2.dilate(m, np.ones((5, 5), np.uint8))
    return np.clip((d[y0:y1, x0:x1] - 22) / (peak - 22), 0, 1) * m


def auto(d, c, peak):
    """Черты дробей (его прямые линии пером — ими же зачёркивает) и точки «·» — находятся сами."""
    m = (d > 55).astype(np.uint8)
    bars = cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (int(c * 1.6), 1)))
    n, lab, st, _ = cv2.connectedComponentsWithStats(bars, 8)
    out = {'bar': [], '·': []}
    for i, (x, y, w, h, a) in enumerate(st):
        if i and 1.8 * c <= w <= 9 * c and h <= 0.3 * c:
            mm = cv2.dilate((lab == i).astype(np.uint8)[max(0, y - 4):y + h + 4, x:x + w], np.ones((3, 3), np.uint8))
            out['bar'].append((np.clip((d[max(0, y - 4):y + h + 4, x:x + w] - 22) / (peak - 22), 0, 1) * mm, c))
    n, lab, st, _ = cv2.connectedComponentsWithStats(m, 8)
    big = [(x, y, w, h) for i, (x, y, w, h, a) in enumerate(st) if i and h > 0.45 * c]
    for i, (x, y, w, h, a) in enumerate(st):
        if not i or not (5 <= a and w <= 0.22 * c and h <= 0.22 * c):
            continue
        cy = y + h / 2
        side = lambda left: any(abs((by + bh / 2) - cy) < 0.5 * c and (0 < x - (bx + bw) < 0.9 * c if left else 0 < bx - (x + w) < 0.9 * c)
                                for bx, by, bw, bh in big)
        if side(True) and side(False):   # знак умножения: буквы и слева, и справа на той же высоте
            out['·'].append((np.clip((d[y - 3:y + h + 3, x - 3:x + w + 3] - 22) / (peak - 22), 0, 1), c))
    return out


def main():
    raw = collections.defaultdict(list)
    for f in sorted(set(SEL) | set(SIGNS) | set(MESS), key=lambda f: list(SEL).index(f) if f in SEL else 99):
        d, lab, c, comps = pieces(f)
        if '--tiles' in sys.argv:
            tiles(f, d, lab, comps)
        peak = np.percentile(d[d > 55], 90)
        for group in (SEL.get(f, {}), SIGNS.get(f, {}), MESS.get(f, {})):
            for ch, ids in group.items():
                for i in ids:
                    rs = [comps[j] for j in i] if isinstance(i, list) else [comps[i]]
                    a = cut(d, lab, peak, rs)
                    raw[ch].append((a, (max(r['y'] + r['h'] for r in rs) - min(r['y'] for r in rs)) / c, c))
        for ch, items in auto(d, c, peak).items():
            raw[ch] += [(a, a.shape[0] / cc, cc) for a, cc in items]
    arrs, meta = {}, []
    for ch, gs in raw.items():
        gs = [g for k, g in enumerate(gs) if k not in DROP.get(ch, [])]
        med = np.median([h for _, h, _ in gs])
        for a, h, c in gs:
            if ch not in KEEP and abs(h / med - 1) > 0.22:
                continue
            ys, xs = np.nonzero(a > 0.15)
            if not len(ys):
                continue
            a = a[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
            if ch in CLEAN:
                a = clean(a)
                ys, xs = np.nonzero(a > 0.15)
                a = a[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
            s = REF / c
            if ch not in KEEP:
                s *= (TARGET.get(ch, DIG) * REF / (a.shape[0] * s)) ** 0.6
            a2 = np.clip(cv2.resize(a.astype(np.float32), None, fx=s, fy=s,
                                    interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC), 0, 1)
            arrs['g%d' % len(meta)] = (a2 * 255).astype(np.uint8)
            meta.append(ch)
    arrs['meta'] = np.frombuffer(json.dumps(meta, ensure_ascii=False).encode(), np.uint8)
    np.savez_compressed(OUT, **arrs)
    print(len(meta), 'знаков:', dict(collections.Counter(meta)))


if __name__ == '__main__':
    main()
