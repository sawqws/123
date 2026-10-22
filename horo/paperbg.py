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


def render(nb, i, path):
    """Переносит чернила nb на фото фона i так, чтобы не отличить от его записей на этом же фото:
    цвет ручки — по его кривой (pen), толщина, густота и мягкость края — подбираются, пока не совпадут с его
    штрихами (Глеб 22.10: «чтобы было не отличить, что текст наложен, и цвет ручки как я пишу»)."""
    import cv2
    b = BGS[i]
    img = cv2.imread(os.path.join(SRC, b['file']))
    f = img.astype(np.float32)
    bgm = cv2.medianBlur(img, 61).astype(np.float32)
    q = np.float32(b['quad'])
    area = np.zeros(img.shape[:2], np.uint8)
    cv2.fillConvexPoly(area, q.astype(np.int32), 1)
    curve = pen(img, bgm, area == 0)
    if curve is None:                                         # своих записей на фото нет — синяя паста по умолчанию
        curve = (np.array([0, 0.3, 0.6, 0.8]), np.array([[1, 1, 1], [0.86, 0.74, 0.7], [0.62, 0.42, 0.4], [0.45, 0.24, 0.2]]), 0.8)
    xs, ys, Lmax = curve
    Lhis = 1 - f[..., 2] / np.maximum(bgm[..., 2], 1)
    his = _feat(Lhis, area == 0, img) or (0.55, 1.6, 0.45)
    cells = np.float32([[0, 0], [b['cols'], 0], [b['cols'], b['rows']], [0, b['rows']]])
    Hc = cv2.getPerspectiveTransform(cells, q)
    cpx = np.linalg.norm(q[1] - q[0]) / b['cols']          # клетка на фото, px
    k = cpx / nb.C
    S = np.float32([[1 / (nb.C * k), 0, 0], [0, 1 / (nb.C * k), 0], [0, 0, 1]])
    rng = np.random.default_rng(nb.rnd.randrange(1 << 30))
    grain = rng.normal(0, 1, img.shape[:2]).astype(np.float32)
    warped = {}

    def comp(r, gain, sig):
        if r not in warped:
            se = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * abs(r) + 1, 2 * abs(r) + 1))
            ink = nb.ink if not r else (cv2.dilate(nb.ink, se) if r > 0 else cv2.erode(nb.ink, se))   # r < 0 — тоньше
            ink = cv2.resize(ink, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
            warped[r] = cv2.warpPerspective(ink, Hc @ S, (img.shape[1], img.shape[0]), flags=cv2.INTER_LINEAR)
        a = cv2.GaussianBlur(warped[r], (0, 0), sig)          # мягкость камеры, как у его штрихов на этом фото
        L = np.clip(a * gain, 0, 1) * Lmax
        L = np.clip(L * (1 + 0.06 * grain), 0, Lmax)           # паста ложится неровно, плюс шум камеры
        ratio = np.stack([np.interp(L, xs, ys[:, c]) for c in range(3)], -1)
        out = np.clip(f * ratio, 0, 255).astype(np.uint8)
        return out, L

    best = None
    for r in range(0, 4):              # толще — буквы слипаются в кляксы; тоньше — рвутся
        for gain in (1.25, 1.5, 1.8):     # бледнее — выходит серая «копия», не паста
            for sig in (0.35, 0.45):        # сильнее — видно, что размыто
                out, L = comp(r, gain, sig)
                st = _feat(L, area > 0, out)
                if not st:
                    continue
                # толщина на маленьком фото меряется грубо (целые px) — весит меньше густоты и края
                err = max(0.0, his[0] - st[0]) / 0.1 + max(0.0, st[0] - his[0] - 0.12) / 0.1 + abs(st[1] - his[1]) / 2.0 + abs(st[2] - his[2]) / 0.15 + 0.3 * (sig > 0.5)
                if best is None or err < best[0]:
                    best = (err, out, (r, gain, sig, st, his))
    out = best[1]
    if os.environ.get('PAPER_DEBUG'):
        print('render', b['file'], 'r, gain, blur, мои, его:', best[2])
    if b.get('crop'):
        x0, y0, x1, y1 = b['crop']
        out = out[y0:y1, x0:x1]
    cv2.imwrite(path, out, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return path
