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
from paper_samples import SEL, SIGNS, MESS, DROP, BAD
from paper_samples2 import SEL2

SEL = {**SEL, **SEL2}   # + 13 его фото алгебры с сайта (notebook_7..19)

SRC = os.environ.get('PAPER_SRC', '.')
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'fonts', 'gleb_paper.npz')
REF = 40   # клетка банка, px (paper.REF)
TARGET = {'x': .88, 'c': .82, 'm': .86, 't': .98, 'y': 1.7, '+': .86, '№': 1.5, 'Лист': 2.0, '0': .92,
          'a': .86, 'z': .86, 'b': 1.45, 'b!': 1.45}
TARGET['y'] = 1.55   # хвост y короче (было 1.7): не достаёт до черты дроби, числитель не висит высоко
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


_BLUE = {}   # f -> где на фото синяя паста (клетка серо-голубая и бледная — её в штамп не берём)


def pieces(f):
    im = cv2.imread(photo(f))
    fi = im.astype(np.int16)
    _BLUE[f] = (fi[..., 0] - fi[..., 2]) > 20
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


CLEAN = set('ab4y')
NOBAR = set('abcmxy0134689+')   # с куском черты у верха или низа — образец не берём (их хватает)


def barred(a, c):
    m = (a > 0.25).astype(np.uint8)
    run = cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, int(0.6 * c)), 1)))
    h = a.shape[0]
    return bool(run[:int(h * 0.25)].any() or run[int(h * 0.75):].any())   # у букв под/над ними бывает кусок черты дроби — отрезается


def clean(a, c):
    """Срезает с буквы прилипший кусок черты дроби: длинный ровный горизонтальный штрих у её верха или низа
    (у a b 4 8 x y своих таких нет; c — клетка на фото, px)."""
    m = (a > 0.25).astype(np.uint8)
    run = cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, int(0.65 * c)), 1)))
    h = a.shape[0]
    band = np.zeros_like(m)
    band[:int(h * 0.3)] = 1
    band[int(h * 0.7):] = 1
    kill = cv2.dilate(run * band, np.ones((3, 3), np.uint8))
    return a * (1 - kill)


SOLO = {'=', '−', '·', 'bar', 'scribble', '№', 'Лист', 'b!', '8!'}


def strip(a, c):
    """Убирает отдельные плоские обрывки у верха и низа знака (кусок черты дроби, минус соседа)."""
    m = (a > 0.2).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(m, 8)
    if n <= 2:
        return a
    main = 1 + int(np.argmax(st[1:, 4]))
    h = a.shape[0]
    for i in range(1, n):
        x, y, w, hh, ar = st[i]
        if i == main:
            continue
        flat = w > 2.2 * hh and hh < 0.25 * c
        edge = y + hh < 0.35 * h or y > 0.65 * h
        tiny = ar < 0.04 * st[main, 4]
        speck = ar < 0.15 * st[main, 4] and edge   # точка у верха/низа — обрывок черты
        if (flat and edge) or tiny or speck:
            a = a * (lab != i)
    return a


def smooth_bar(a):
    """Его черта без «бусин»: где линию пересекала клетка, на фото она толще — после резкости это точки.
    Ход линии (изгиб, наклон, концы) — его, а толщина и густота по длине — ровные, как у шариковой ручки."""
    h, w = a.shape
    ys = np.arange(h)[:, None]
    mass = a.sum(0)
    cy = (a * ys).sum(0) / np.maximum(mass, 1e-6)
    on = mass > 0.3
    if on.sum() < 5:
        return a
    xs = np.arange(w)
    cy = np.interp(xs, xs[on], cy[on])
    cy = np.convolve(np.pad(cy, 6, mode='edge'), np.ones(13) / 13, 'valid')
    th = float(np.median(mass[on]))                      # толщина линии, px (масса столбца при густоте 1)
    sig = max(0.6, th / 2.5)
    out = np.exp(-0.5 * ((ys - cy[None]) / sig) ** 2)
    out = out / out.sum(0, keepdims=True) * th
    taper = np.clip(np.minimum(xs - xs[on].min(), xs[on].max() - xs) / 3 + 0.4, 0, 1)
    return np.clip(out * taper[None] * on.astype(float).cumsum().clip(0, 1)[None], 0, 1).astype(np.float32)


PEN_R = 1.75        # полутолщина его линии, px при клетке REF=40 (замер: штрих ≈ 0.09 клетки)
RESTROKE_SKIP = {'scribble', 'bar', 'Лист', '·'}


def _prune(sk, n):
    """Обрезает у скелета «усики» короче n px (на изгибах толстого штриха скелет даёт отростки), не укорачивая
    настоящие концы линий: срезаем концы n раз, потом отращиваем назад только от оставшихся концов."""
    from scipy import ndimage
    k = np.ones((3, 3)); k[1, 1] = 0
    cur = sk.copy()
    for _ in range(n):
        nb = ndimage.convolve(cur.astype(np.uint8), k, mode='constant')
        ends = cur & (nb == 1)
        if not ends.any():
            break
        cur[ends] = False
    if not cur.any():
        return sk
    nb = ndimage.convolve(cur.astype(np.uint8), k, mode='constant')
    grow = cur & (nb == 1)
    rest = sk & ~cur
    for _ in range(n + 1):
        new = ndimage.binary_dilation(grow, np.ones((3, 3))) & rest & ~grow
        if not new.any():
            break
        grow |= new
    return cur | grow


def restroke(a, r=PEN_R, up=4, keep=0.12, thr=0.35):
    """Его знак — заново одной ровной линией по середине его штриха (Глеб 22.10: «выглядит как будто грязно»):
    образцы с разных фото разной толщины, с рваным краем и пятнами; форма — его, линия — как у шариковой ручки."""
    from skimage.morphology import skeletonize
    from scipy import ndimage
    h, w = a.shape
    pad = int(r * 2 + 3)
    A = np.pad(a, pad)
    big = cv2.resize(A, None, fx=up, fy=up, interpolation=cv2.INTER_CUBIC)
    big = cv2.GaussianBlur(big, (0, 0), up * 0.6)
    m = big > thr
    lab, n = ndimage.label(m, structure=np.ones((3, 3)))
    if n == 0:
        return a
    sizes = ndimage.sum(m, lab, range(1, n + 1))
    m = np.isin(lab, 1 + np.nonzero(sizes >= keep * sizes.max())[0])   # точки и обрывки рядом — прочь
    sk = _prune(skeletonize(m), int(r * up * 2.2))
    dist = ndimage.distance_transform_edt(~sk) / up
    alpha = np.clip((r + 0.5 - dist) / 1.0, 0, 1).astype(np.float32)
    out = cv2.resize(alpha, (A.shape[1], A.shape[0]), interpolation=cv2.INTER_AREA)
    return np.clip(out, 0, 1)


def cut(d, lab, peak, rs, M=3, f=None):
    """Чернила куска (или нескольких — «=») по его маске, 0..1."""
    x0, y0 = max(0, min(r['x'] for r in rs) - M), max(0, min(r['y'] for r in rs) - M)
    x1, y1 = max(r['x'] + r['w'] for r in rs) + M, max(r['y'] + r['h'] for r in rs) + M
    m = np.zeros((y1 - y0, x1 - x0), np.uint8)
    for r in rs:
        m |= (lab[y0:y1, x0:x1] == r['lab']).astype(np.uint8)
    m = cv2.dilate(m, np.ones((5, 5), np.uint8))
    if f in _BLUE:   # кусочки клеток внутри маски: бледные и не синие — прочь
        sub = d[y0:y1, x0:x1]
        m = m * (_BLUE[f][y0:y1, x0:x1] | (sub > 70)).astype(np.uint8)
    return np.clip((d[y0:y1, x0:x1] - 32) / (peak - 32), 0, 1) * m   # бледнее — это уже клетка


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
                    a = cut(d, lab, peak, rs, f=f)
                    raw[ch].append((a, (max(r['y'] + r['h'] for r in rs) - min(r['y'] for r in rs)) / c, c,
                                    '%s:%s' % (f[1:-4], '+'.join(map(str, i)) if isinstance(i, list) else i)))
        for ch, items in auto(d, c, peak).items():
            raw[ch] += [(a, a.shape[0] / cc, cc, '%s:%s%d' % (f[1:-4], ch, k)) for k, (a, cc) in enumerate(items)]
    # его настоящие зачёркивания описок (штриховка поверх неверного) с 75 фото сданных работ — paper_scribbles.json
    for k, it in enumerate(json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'paper_scribbles.json')))):
        im = cv2.imread(os.path.join(SRC, it['file']))
        fi = im.astype(np.int16)
        bg = cv2.medianBlur(im, 61).astype(np.float32)
        dd = (bg - im.astype(np.float32))[..., 2]
        x, y, w, h = it['box']
        m = ((dd > 40) & ((fi[..., 0] - fi[..., 2]) > 20))[y:y + h, x:x + w].astype(np.uint8)
        m = cv2.dilate(m, np.ones((3, 3), np.uint8))
        a = np.clip((dd[y:y + h, x:x + w] - 30) / 90, 0, 1) * m
        raw['scribble'].append((a, h / it['cell'], it['cell'], 'x:%d' % k))
    arrs, meta, srcs = {}, [], []
    for ch, gs in raw.items():
        gs = [g for k, g in enumerate(gs) if k not in DROP.get(ch, [])]
        med = np.median([g[1] for g in gs])
        for a, h, c, src in gs:
            if src in BAD:
                continue
            if ch not in KEEP and abs(h / med - 1) > 0.22:
                continue
            if ch in '()':                # у скобки — только она сама: мелкие отдельные точки рядом прочь
                m = (a > 0.2).astype(np.uint8)
                n, lab, st, _ = cv2.connectedComponentsWithStats(m, 8)
                if n > 2:
                    big = st[1:, 4].max()
                    for i in range(1, n):
                        if st[i, 4] < 0.12 * big:
                            a = a * (lab != i)
            if ch not in SOLO:
                a = strip(a, c)
            elif ch == 'bar':             # у черты — только сама линия: бугорки букв, касавшихся её, после резкости — точки
                m = (a > 0.2).astype(np.uint8)
                n, lab, st, _ = cv2.connectedComponentsWithStats(m, 8)
                if n > 2:
                    a = a * (lab == 1 + int(np.argmax(st[1:, 4])))
                prof = (a > 0.3).sum(0)
                med = np.median(prof[prof > 0]) if (prof > 0).any() else 0
                a = a * (np.abs(np.arange(a.shape[0])[:, None] - (a * np.arange(a.shape[0])[:, None]).sum(0) / np.maximum(a.sum(0), 1e-6)) <= med * 0.9 + 1)
            ys, xs = np.nonzero(a > 0.15)
            if not len(ys):
                continue
            a = a[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
            if ch in CLEAN:
                a = clean(a, c)
                ys, xs = np.nonzero(a > 0.15)
                a = a[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
            if ch in NOBAR and barred(a, c):
                continue
            s = REF / c
            if ch not in KEEP:
                s *= (TARGET.get(ch, DIG) * REF / (a.shape[0] * s))   # к высоте класса полностью: степени с фото не мельчат
            a2 = np.clip(cv2.resize(a.astype(np.float32), None, fx=s, fy=s,
                                    interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC), 0, 1)
            if ch not in RESTROKE_SKIP and not os.environ.get('PAPER_RAW'):
                thin = ch in '+=−'   # у «+» и «=» штрихи раздельные и бледнее — порог ниже, обрывки не выкидывать
                a2 = restroke(a2, keep=0.03 if thin else 0.12, thr=0.2 if thin else 0.35)
                ys2, xs2 = np.nonzero(a2 > 0.1)
                a2 = a2[ys2.min():ys2.max() + 1, xs2.min():xs2.max() + 1]
            g = (a2 * 255).astype(np.uint8)
            if ch == 'bar':
                th = (a2 > 0.3).sum(0)
                if th.sum() / a2.shape[1] > 6.0 or np.percentile(th, 98) > 2.2 * max(1, np.median(th)):
                    continue              # жирные или с утолщением — выходят «маркером»/кляксой
                g = (smooth_bar(a2) * 255).astype(np.uint8)
            arrs['g%d' % len(meta)] = g
            meta.append(ch)
            srcs.append(src)
    arrs['meta'] = np.frombuffer(json.dumps(meta, ensure_ascii=False).encode(), np.uint8)
    arrs['src'] = np.frombuffer(json.dumps(srcs).encode(), np.uint8)   # откуда штамп: «фото:кусок»
    np.savez_compressed(OUT, **arrs)
    print(len(meta), 'знаков:', dict(collections.Counter(meta)))


if __name__ == '__main__':
    main()
