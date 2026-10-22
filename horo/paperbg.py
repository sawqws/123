"""Письмо на настоящем пустом листе с его фото (Глеб 22.10: «пока видно, что написано ИИ»).

Рисованный лист выдаёт подделку: свет, изгиб, клетка, шум камеры — всё «слишком». Поэтому пишем прямо на его
фото тетради, где страница пустая: `horo/live/paper_bg.json` — какой файл (Brain `raw/images/blank_<n>.jpg`,
папка — `PAPER_BG`), где на фото углы пишущей части страницы (подогнаны по клетке, `fit`), сколько там клеток,
как обрезать кадр. Notebook (paper.py) пишет в клетках этой страницы, его чернила переносятся на фото с той же
перспективой, а цвет пасты и её густота берутся с его же записей на этом фото.

    from paperbg import sheet, render
    nb = sheet(0, seed=7)          # Notebook размером с пустую страницу фона 0
    nb.title('Лист 7'); nb.solve(...)
    render(nb, 0, '1_tetrad.jpg')
"""
import json
import os

import numpy as np

from paper import Notebook

_here = os.path.dirname(os.path.abspath(__file__))
BG_FILE = os.path.join(_here, 'live', 'paper_bg.json')
SRC = os.environ.get('PAPER_BG', '/home/user/Brain/raw/images')
BGS = json.load(open(BG_FILE))


def sheet(i, seed=1):
    b = BGS[i]
    return Notebook(cols=b['cols'], rows=b['rows'], seed=seed, margin=-0.3, right=0.35)


def _blue(img):
    """Синяя паста: синий канал заметно выше красного (тени, края тетради, стол — серые)."""
    f = img.astype(np.int16)
    return (f[..., 0] - f[..., 2]) > 25


def pen(img, bgm, mask):
    """Цвет его ручки на этом фото — кривая: насколько штрих темнее бумаги по красному (L = 0…1) → во сколько
    раз темнее каждый канал. Слабый нажим у шариковой ручки синее, густой — тёмно-синий; кривая снята с его же
    штрихов вне пустой страницы."""
    f = img.astype(np.float32)
    r = f / np.maximum(bgm, 1)
    L = 1 - r[..., 2]
    m = mask & _blue(img) & (L > 0.12)
    if m.sum() < 800:
        return None
    Ls, rs = L[m], r[m]
    edges = np.quantile(Ls, np.linspace(0, 1, 16))
    xs, ys = [0.0], [np.ones(3)]
    for lo, hi in zip(edges, edges[1:]):
        sel = (Ls >= lo) & (Ls <= hi)
        if sel.sum() > 20:
            xs.append(float(np.median(Ls[sel])))
            ys.append(np.median(rs[sel], axis=0))
    return np.array(xs), np.array(ys), float(np.percentile(Ls, 99.5))   # самый густой нажим его ручки


def _feat(L, m, img):
    """Его штрихи числами: густота (средний L), толщина (p90 расстояния до края, px), мягкость края
    (доля бледных пикселей среди всех пикселей штриха)."""
    import cv2
    core = (m & _blue(img) & (L > 0.3)).astype(np.uint8)
    if core.sum() < 600:
        return None
    near = cv2.dilate(core, np.ones((5, 5), np.uint8)) > 0      # только у штрихов — бледная клетка не в счёт
    ink = m & near & (L > 0.12)
    dt = cv2.distanceTransform(core, cv2.DIST_L2, 5)
    return float(L[core > 0].mean()), float(np.percentile(dt[core > 0], 90)), float(1 - core.sum() / max(1, ink.sum()))


def _clean(img, bgm, area):
    """Стирает с пустой страницы случайные следы его ручки (точки, черточки) — клетку не трогает (она бледнее)."""
    import cv2
    d = (bgm - img.astype(np.float32))[..., 2]
    ink = ((d > 55) & _blue(img) & (area > 0)).astype(np.uint8)
    if not ink.any():
        return img
    ink = cv2.dilate(ink, np.ones((5, 5), np.uint8))
    return cv2.inpaint(img, ink, 4, cv2.INPAINT_TELEA)


BLUR = 0.75       # σ размытия пасты, px фото
GAIN = 1.0        # густота пасты (подогнано: средняя густота и цвет как у его штрихов на том же фото)
NOISE_INK = 9.0   # шум камеры в штрихах (замер: на бумаге σ≈6, в его тёмных штрихах — ещё сильнее)
def _look(img, bgm, mask):
    """Как выглядит паста на фото: самый тёмный тон (p90 густоты), толщина линии (площадь / половина контура, px),
    шум в штрихе. По средней густоте подбирать нельзя: тонкая чёткая линия и толстая размытая дают одно среднее."""
    import cv2
    f = img.astype(np.float32)
    L = 1 - f[..., 2] / np.maximum(bgm[..., 2], 1)
    blue = (f[..., 0] - f[..., 2]) > 25
    core = (mask & blue & (L > 0.3)).astype(np.uint8)
    if core.sum() < 400:
        return None
    cs, _ = cv2.findContours(core, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    per = sum(cv2.arcLength(c, True) for c in cs)
    hp = f[..., 1] - cv2.GaussianBlur(f[..., 1], (0, 0), 1.5)
    return float(np.percentile(L[core > 0], 90)), float(2 * core.sum() / max(1.0, per)), float(hp[core > 0].std())


UP = 1   # итоговое фото — в родном размере его фото (Глеб 22.10: при 2.5× с шумодавом и резкостью «фон видно ИИ»)


def render(nb, i, path):
    """Переносит чернила nb на фото фона i так, чтобы не отличить от его записей на этом же фото
    (Глеб 22.10: «чтобы было не отличить, что текст наложен, и цвет ручки как я пишу»): цвет ручки — по его
    кривой (pen) с этого фото; фото — в UP раз крупнее, штрихи рисуются сразу в этом размере, край ровный."""
    import cv2
    b = BGS[i]
    img = cv2.imread(os.path.join(SRC, b['file']))
    bgm = cv2.medianBlur(img, 61).astype(np.float32)
    q = np.float32(b['quad'])
    area = np.zeros(img.shape[:2], np.uint8)
    cv2.fillConvexPoly(area, q.astype(np.int32), 1)
    curve = pen(img, bgm, area == 0)
    if curve is None:                                         # своих записей на фото нет — синяя паста по умолчанию
        curve = (np.array([0, 0.3, 0.6, 0.8]), np.array([[1, 1, 1], [0.86, 0.74, 0.7], [0.62, 0.42, 0.4], [0.45, 0.24, 0.2]]), 0.8)
    xs, ys, Lmax = curve
    x0, y0, x1, y1 = b.get('crop') or (0, 0, img.shape[1], img.shape[0])
    big = _clean(img, bgm, area)[y0:y1, x0:x1]               # фон — как есть, без увеличения и фильтров
    f = big.astype(np.float32)
    qb = (q - np.float32([x0, y0])) * UP
    cells = np.float32([[0, 0], [b['cols'], 0], [b['cols'], b['rows']], [0, b['rows']]])
    Hc = cv2.getPerspectiveTransform(cells, qb)
    cpx = np.linalg.norm(qb[1] - qb[0]) / b['cols']          # клетка на итоговом фото, px
    k = cpx / nb.C
    # перенос: сначала в 4 раза крупнее фото (ровные края), потом вниз до фото — как оптика камеры
    SS = 4
    S = np.float32([[1 / (nb.C * k * SS), 0, 0], [0, 1 / (nb.C * k * SS), 0], [0, 0, 1]])
    Up = np.float32([[SS, 0, 0], [0, SS, 0], [0, 0, 1]])
    warped = {}

    def ink_at(thin):
        """Чернила на фото; thin — насколько тоньше линия (у него в разные дни разная ручка)."""
        if thin not in warped:
            ink = cv2.resize(nb.ink, None, fx=k * SS, fy=k * SS, interpolation=cv2.INTER_AREA if k * SS < 1 else cv2.INTER_CUBIC)
            a = cv2.warpPerspective(np.clip(ink, 0, 1), Up @ Hc @ S, (big.shape[1] * SS, big.shape[0] * SS), flags=cv2.INTER_LINEAR)
            if thin:   # тоньше на thin/SS px фото: размытие + порог по краю, форма штриха сохраняется
                a = np.clip((cv2.GaussianBlur(a, (0, 0), 1.0) - 0.12 * thin) / (1 - 0.12 * thin), 0, 1)
            warped[thin] = np.clip(cv2.resize(a, (big.shape[1], big.shape[0]), interpolation=cv2.INTER_AREA), 0, 1)
        return warped[thin]

    paper = cv2.medianBlur(big, 9).astype(np.float32)
    rng = np.random.default_rng(nb.rnd.randrange(1 << 30))
    nz = cv2.GaussianBlur(rng.normal(0, 1, big.shape[:2]).astype(np.float32), (0, 0), 0.7)
    nz = nz / max(1e-6, nz.std())

    def comp(thin, gain, blur, noise):
        L = np.clip(cv2.GaussianBlur(ink_at(thin), (0, 0), blur) * gain, 0, 1) * Lmax
        ratio = np.stack([np.interp(L, xs, ys[:, c]) for c in range(3)], -1)
        # под пастой — бумага без клетки (иначе на пересечениях с клеткой «бусины»)
        w = np.clip(L / max(Lmax, 1e-6) * 2.5, 0, 1)[..., None]
        o = (f * (1 - w) + paper * w) * ratio
        # шум камеры: в тёмных штрихах матрица шумит сильнее, чем на бумаге
        o = o + (nz * noise * np.clip(L / max(Lmax, 1e-6), 0, 1))[..., None] * np.array([1.0, 0.9, 0.9], np.float32)
        return np.clip(o, 0, 255).astype(np.uint8)

    # подбор под его штрихи вне пустой страницы на этом же фото: толщина, тёмный тон, шум
    his = _look(img, bgm, area == 0)
    where = cv2.dilate((ink_at(0) > 0.02).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    best = None
    for thin in (0, 1, 2, 3, 4):
        for gain in ((1.0, 1.3, 1.7, 2.2) if not thin else (1.6, 2.0, 2.5)):   # тонкая линия — густая, не серая
            for blur in (0.45, 0.65, 0.9):
                o = comp(thin, gain, blur, 10.0)
                st = _look(o, cv2.medianBlur(o, 61).astype(np.float32), where)
                if not st or not his:
                    continue
                err = abs(st[0] - his[0]) / 0.04 + abs(st[1] - his[1]) / 0.25 + 0.5 * blur
                if best is None or err < best[0]:
                    best = (err, (thin, gain, blur), st)
    if best:
        thin, gain, blur = best[1]
        noise = min((abs(_look(o2 := comp(thin, gain, blur, n), cv2.medianBlur(o2, 61).astype(np.float32), where)[2] - his[2]), n)
                    for n in (4.0, 8.0, 12.0, 16.0))[1]
        best = (best[0], comp(thin, gain, blur, noise), (thin, gain, blur, noise, best[2], his))
    out = best[1] if best else comp(0, GAIN, BLUR, NOISE_INK)
    if os.environ.get('PAPER_DEBUG') and best:
        print('render', b['file'], 'тоньше, густота, мягкость, шум / мои / его:', best[2])
    cv2.imwrite(path, out, [cv2.IMWRITE_JPEG_QUALITY, 90])   # одно сжатие поверх его (телефонного) — как пересланное фото
    return path
