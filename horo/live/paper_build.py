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
from paper_samples import SEL, DROP

SRC = os.environ.get('PAPER_SRC', '.')
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'fonts', 'gleb_paper.npz')
REF = 40   # клетка банка, px (paper.REF)
TARGET = {'x': .88, 'c': .82, 'm': .86, 't': .98, 'y': 1.7, '+': .86, '№': 1.5, 'Лист': 2.0, '0': .92}
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


def main():
    raw = collections.defaultdict(list)
    for f, chars in SEL.items():
        d, lab, c, comps = pieces(f)
        if '--tiles' in sys.argv:
            tiles(f, d, lab, comps)
        peak = np.percentile(d[d > 55], 90)
        for ch, ids in chars.items():
            for i in ids:
                r = comps[i]; M = 3
                x0, y0 = max(0, r['x'] - M), max(0, r['y'] - M)
                x1, y1 = r['x'] + r['w'] + M, r['y'] + r['h'] + M
                m = cv2.dilate((lab[y0:y1, x0:x1] == r['lab']).astype(np.uint8), np.ones((5, 5), np.uint8))
                a = np.clip((d[y0:y1, x0:x1] - 22) / (peak - 22), 0, 1) * m
                raw[ch].append((a, r['h'] / c, c))
    arrs, meta = {}, []
    for ch, gs in raw.items():
        med = np.median([h for _, h, _ in gs])
        keep = []
        for a, h, c in gs:
            if ch not in '()' and abs(h / med - 1) > 0.22:
                continue
            ys, xs = np.nonzero(a > 0.15)
            a = a[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
            s = REF / c
            if ch not in '()':
                s *= (TARGET.get(ch, DIG) * REF / (a.shape[0] * s)) ** 0.6
            keep.append(np.clip(cv2.resize(a.astype(np.float32), None, fx=s, fy=s,
                                           interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC), 0, 1))
        for i, a in enumerate(keep):
            if i in DROP.get(ch, []):
                continue
            arrs['g%d' % len(meta)] = (a * 255).astype(np.uint8)
            meta.append(ch)
    arrs['meta'] = np.frombuffer(json.dumps(meta, ensure_ascii=False).encode(), np.uint8)
    np.savez_compressed(OUT, **arrs)
    print(len(meta), 'знаков:', dict(collections.Counter(meta)))


if __name__ == '__main__':
    main()
