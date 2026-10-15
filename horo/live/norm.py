"""Одна ровная азбука Глеба для текста (Глеб 15.10: «буквы разного размера и как будто криво вырезаны»).

Берёт его буквы — с листа алфавита (horo/tmp/fonts/gleb_extra.npz) и из настоящих записей (horo/fonts/gleb_live.npz) —
и для каждой буквы:
  1) убирает обрезки соседних букв и мусор;
  2) приводит к одной высоте по классу буквы (строчная, с выносным элементом вверх/вниз, заглавная, цифра)
     и ставит ровно на строку;
  3) проводит по скелету буквы одним пером — толщина у всех букв одинаковая, края чистые.
Итог — horo/fonts/gleb_norm.npz: штрихи каждой буквы в высотах строчной (x вправо, y вверх от строки).

    python3 horo/live/norm.py [выход.npz] [--sheet проверка.png]
"""
import os, sys
import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage
from skimage.morphology import skeletonize

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import hand  # noqa: E402
from alphabet import prune  # noqa: E402

# высота по классу, в высотах строчной (верх буквы над строкой)
ASC = set('бвдbdfhkl')
DESC = set('руфцщgpqyj')
TARGET = {'x': 1.0, 'asc': 1.6, 'desc': 1.0, 'cap': 1.6, 'digit': 1.45, 't': 1.3, 'т': 1.0}
DOTTED = set('ijйёЁ!?:;%')   # точки и дужки — маленькие отдельные части, их не выбрасывать

# какую букву брать: 'live' — из его записей (если есть), иначе с листа алфавита
PREFER_LIVE = set('DPSTabcdeghilmoprstuwxy')   # проверено глазами: чистые, без обрезков


def cls(ch):
    if ch.isdigit():
        return 'digit'
    if ch.isupper():
        return 'cap'
    if ch == 't':
        return 't'
    if ch in ASC:
        return 'asc'
    if ch in DESC:
        return 'desc'
    return 'x'


def binary(st):
    """(маска, x0, y_top, px) — маска буквы без мелкого мусора; координаты в высотах строчной."""
    a, left, top, px = st[:4]
    m = a > 0.45
    lab, n = ndimage.label(m, structure=np.ones((3, 3)))
    if not n:
        return None
    sz = ndimage.sum(m, lab, range(1, n + 1))
    keep = np.isin(lab, [i + 1 for i in range(n) if sz[i] >= 0.06 * sz.max()])
    return keep, left, top, px


def binary_for(ch, st):
    a, left, top, px = st[:4]
    if ch in DOTTED:   # точки оставляем
        m = a > 0.45
        lab, n = ndimage.label(m, structure=np.ones((3, 3)))
        sz = ndimage.sum(m, lab, range(1, n + 1))
        return np.isin(lab, [i + 1 for i in range(n) if sz[i] >= 0.01 * sz.max()]), left, top, px
    return binary(st)


def body(mask, ch):
    """Строки основной части (без точки над i, дужки й)."""
    lab, n = ndimage.label(mask, structure=np.ones((3, 3)))
    sz = ndimage.sum(mask, lab, range(1, n + 1))
    if ch in DOTTED and n > 1:   # мерим по самой букве, без точки/дужки
        main = lab == 1 + int(np.argmax(sz))
    else:
        main = mask
    rows = np.nonzero(main.any(1))[0]
    return rows[0], rows[-1]


def strokes(mask, px, k, keep_small=False):
    """Скелет маски → штрихи (каждый — массив точек в пикселях маски, увеличенной в k раз)."""
    big = ndimage.zoom(mask.astype(np.float32), k, order=1)
    big = ndimage.gaussian_filter(big, k * 0.6) > 0.5
    dt = ndimage.distance_transform_edt(big)
    sk = skeletonize(big)
    w = 2 * np.median(dt[sk]) if sk.any() else 4
    sk = prune(sk, int(w * 1.2))
    pts = np.argwhere(sk)[:, ::-1].astype(float)            # (x, y)
    out = []
    for p in hand._strokes(pts, 1.0):
        if len(p) >= 3:
            win = max(3, int(w) // 2 * 2 + 1)
            if len(p) > win:
                pad = np.concatenate([np.repeat(p[:1], win // 2, 0), p, np.repeat(p[-1:], win // 2, 0)])
                sm = np.stack([np.convolve(pad[:, i], np.ones(win) / win, 'valid') for i in (0, 1)], 1)
                sm[0], sm[-1] = p[0], p[-1]
                p = sm
        out.append(p)
    # мелкие обрывки (меньше 6% общей длины) — обрезки соседних букв; точку над i оставляем
    ln = [max(1.0, np.hypot(*np.diff(p, axis=0).T).sum()) if len(p) > 1 else 1.0 for p in out]
    tot = sum(ln)
    if keep_small:
        return out
    return [p for p, l in zip(out, ln) if l >= 0.06 * tot] or out


# центральная линия пера (толщина пера добавит по ~0.085 сверху и снизу): низ, верх строчной, выносные, заглавные
LO, XTOP = 0.08, 0.92
TOPS = {'asc': 1.3, 't': 1.15, 'cap': 1.4, 'digit': 1.35}
WIDE = set('жмшщюыцтmwMWЖШЩЮ')    # широкие по природе — ширину не трогаем
NARROW = set("ilj1!.,:;'|()")
XW = 0.72                          # обычная ширина строчной (по основной части), к ней мягко подтягиваем


def glyph(ch, st):
    b = binary_for(ch, st)
    if b is None:
        return None
    mask, left, top, px = b
    k = 4                                    # увеличение маски перед скелетом
    out = []
    for p in strokes(mask, px, k, ch in DOTTED):
        x = (p[:, 0] / k) * px + left
        y = top - (p[:, 1] / k) * px
        out.append(np.stack([x, y], 1))
    if not out:
        return None
    # мерим по самой букве (без точки над i и дужки й)
    ln = [np.hypot(*np.diff(o, axis=0).T).sum() if len(o) > 1 else 0 for o in out]
    main = np.concatenate([o for o, l in zip(out, ln) if l >= 0.3 * max(ln)])
    c = cls(ch)
    ys = main[:, 1]
    if c == 'desc':
        hi = np.percentile(ys[ys > 0.1 * ys.max()], 98)
        lo = 0.0                              # строка с листа; хвост уходит ниже, как у него
    elif c == 'x':
        hi, lo = np.percentile(ys, 98), np.percentile(ys, 2)
    else:
        hi, lo = ys.max(), np.percentile(ys, 2)
    T = TOPS.get(c, XTOP)
    sc = (T - LO) / max(hi - lo, 1e-3)
    out = [np.stack([o[:, 0] * sc, LO + (o[:, 1] - lo) * sc], 1) for o in out]
    # ширина — по основной части буквы (полоса строчной; у заглавных и цифр — их высота):
    # хвост «у» влево, черта «б» вправо заходят к соседям и не раздвигают буквы
    allp = np.concatenate(out)
    top_band = T if c in ('cap', 'digit') else XTOP
    band = allp[(allp[:, 1] > LO + 0.02) & (allp[:, 1] < top_band)]
    if len(band) < 5:
        band = allp
    x0, x1 = band[:, 0].min(), band[:, 0].max()
    sx = 1.0
    if ch.islower() and ch not in WIDE and ch not in NARROW and x1 - x0 > 0.15:
        sx = float(np.clip((XW / (x1 - x0)) ** 0.5, 0.85, 1.25))   # слишком узкие и слишком широкие — ближе к обычной
    out = [np.stack([(o[:, 0] - x0) * sx, o[:, 1]], 1).astype(np.float32) for o in out]
    if ch in 'ij':   # точка — его точка, но ровно над палочкой, на одной высоте у всех i/j
        mx = max(len(o) for o in out)
        stem = next(o for o in out if len(o) == mx)
        tx = float(stem[np.argmax(stem[:, 1]), 0])
        out = [stem, np.array([[tx, 1.3], [tx + 0.01, 1.31]], np.float32)]
    if ch in 'йЙ':   # дужка — чуть выше, чтобы в мелком тексте «й» не сливалась с «и»
        mx = max(len(o) for o in out)
        out = [o if len(o) == mx else o + np.array([0, 0.12], np.float32) for o in out]
    return out, float((x1 - x0) * sx)


def pick(ch):
    if ch in PREFER_LIVE and ch in hand.LIVE:
        return hand.LIVE[ch][0]
    if ch in hand.STAMPS:
        return hand.STAMPS[ch][0]
    if ch in hand.LIVE:
        return hand.LIVE[ch][0]
    return None


def build():
    bank = {}
    chars = sorted(set(hand.STAMPS) | set(hand.LIVE))
    for ch in chars:
        if ch in ',.':
            continue
        st = pick(ch)
        g = glyph(ch, st) if st is not None else None
        if g:
            bank[ch] = g
    # точка, запятая, дефис — его пером (с листа их не брали: ставились криво)
    bank['.'] = ([np.array([[0.05, 0.05], [0.06, 0.06]], np.float32)], 0.1)
    bank[','] = ([np.array([[0.12, 0.08], [0.06, -0.12], [0.0, -0.25]], np.float32)], 0.15)
    bank['-'] = ([np.array([[0.0, 0.45], [0.45, 0.47]], np.float32)], 0.45)
    return bank


def save(bank, path):
    z = {}
    for ch, (st, w) in bank.items():
        flat = np.concatenate([np.vstack([s, [[np.nan, np.nan]]]) for s in st]).astype(np.float32)
        z[f'{ord(ch)}'] = flat
        z[f'{ord(ch)}w'] = np.float32(w)
    np.savez_compressed(path, **z)


def sheet(bank, path, pen=0.18):
    xh = 40
    chars = list(bank)
    cols = 16
    W, H = cols * 80, ((len(chars) + cols - 1) // cols) * 120
    im = Image.new('RGB', (W, H), 'white')
    d = ImageDraw.Draw(im)
    for i, ch in enumerate(chars):
        x0, base = (i % cols) * 80 + 15, (i // cols) * 120 + 85
        d.line([x0 - 10, base, x0 + 65, base], fill=(150, 220, 150))
        d.line([x0 - 10, base - xh, x0 + 65, base - xh], fill=(220, 240, 220))
        d.text((x0 - 12, base - 80), ch, fill=(220, 0, 0))
        for s in bank[ch][0]:
            pts = [(x0 + u * xh, base - v * xh) for u, v in s]
            r = pen * xh / 2
            if len(pts) > 1:
                d.line(pts, fill=(25, 125, 240), width=int(pen * xh), joint='curve')
            for p in (pts[0], pts[-1]):
                d.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=(25, 125, 240))
    im.save(path)


if __name__ == '__main__':
    out = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith('--') else os.path.join(HERE, '..', 'fonts', 'gleb_norm.npz')
    b = build()
    save(b, out)
    if '--sheet' in sys.argv:
        sheet(b, sys.argv[sys.argv.index('--sheet') + 1])
    print(len(b), 'букв →', out)
