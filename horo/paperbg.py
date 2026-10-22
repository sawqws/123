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


UP = 2.5   # во сколько раз итоговое фото крупнее исходного (Глеб 22.10: «улучшай качество, убери размытость»)


def _upscale(img):
    """Фон крупнее без мыла и шума: Lanczos, лёгкое шумоподавление (шум камеры при увеличении — «грязь»)
    и мягкая резкость — клетка и край листа остаются чёткими."""
    import cv2
    big = cv2.resize(img, None, fx=UP, fy=UP, interpolation=cv2.INTER_LANCZOS4)
    big = cv2.fastNlMeansDenoisingColored(big, None, 4, 4, 7, 21)
    soft = cv2.GaussianBlur(big, (0, 0), 1.6)
    return cv2.addWeighted(big, 1.45, soft, -0.45, 0)


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
    big = _upscale(_clean(img, bgm, area)[y0:y1, x0:x1])
    f = big.astype(np.float32)
    qb = (q - np.float32([x0, y0])) * UP
    cells = np.float32([[0, 0], [b['cols'], 0], [b['cols'], b['rows']], [0, b['rows']]])
    Hc = cv2.getPerspectiveTransform(cells, qb)
    cpx = np.linalg.norm(qb[1] - qb[0]) / b['cols']          # клетка на итоговом фото, px
    k = cpx / nb.C
    ink = cv2.resize(nb.ink, None, fx=k, fy=k, interpolation=cv2.INTER_AREA if k < 1 else cv2.INTER_CUBIC)
    S = np.float32([[1 / (nb.C * k), 0, 0], [0, 1 / (nb.C * k), 0], [0, 0, 1]])
    a = cv2.warpPerspective(np.clip(ink, 0, 1), Hc @ S, (big.shape[1], big.shape[0]), flags=cv2.INTER_CUBIC)
    a = cv2.GaussianBlur(np.clip(a, 0, 1), (0, 0), 0.55)      # чуть мягкости, как у камеры, без мыла
    rng = np.random.default_rng(nb.rnd.randrange(1 << 30))
    grain = cv2.GaussianBlur(rng.normal(0, 1, a.shape).astype(np.float32), (0, 0), 1.2)
    L = np.clip(a * 1.15, 0, 1) * Lmax
    L = np.clip(L * (1 + 0.05 * grain), 0, Lmax)             # паста ложится чуть неровно
    ratio = np.stack([np.interp(L, xs, ys[:, c]) for c in range(3)], -1)
    # под пастой — бумага без клетки: иначе на каждом пересечении с линией клетки черта темнее («бусины»)
    paper = cv2.medianBlur(big, 9).astype(np.float32)
    w = np.clip(L / max(Lmax, 1e-6) * 2.5, 0, 1)[..., None]
    under = f * (1 - w) + paper * w
    out = np.clip(under * ratio, 0, 255).astype(np.uint8)
    cv2.imwrite(path, out, [cv2.IMWRITE_JPEG_QUALITY, 94])
    return path
