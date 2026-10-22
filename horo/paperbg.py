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


def _ink_ratio(img, d):
    """Во сколько раз его паста темнее бумаги под ней на этом фото (по каналам)."""
    import cv2
    bg = cv2.medianBlur(img, 41).astype(np.float32)
    m = (d > 85) & _blue(img)   # вся его паста (синяя!), не тени и края тетради — иначе выходит чёрная
    if m.sum() < 200:
        return np.array([0.3, 0.32, 0.5], np.float32)
    return np.percentile(img.astype(np.float32)[m] / np.maximum(bg[m], 1), 20, axis=0)   # цвет густой пасты


def _blue(img):
    """Синяя паста: синий канал заметно выше красного (тени, края тетради, стол — серые)."""
    f = img.astype(np.int16)
    return (f[..., 0] - f[..., 2]) > 30


def _stats(d, m, img):
    """Его паста на фото: насколько темнее бумаги и насколько толстая линия (p90 расстояния до края, px)."""
    import cv2
    ink = ((d > 55) & m & _blue(img)).astype(np.uint8)
    if ink.sum() < 1500:
        return None
    dt = cv2.distanceTransform(ink, cv2.DIST_L2, 5)
    return float(d[ink > 0].mean()), float(np.percentile(dt[ink > 0], 90))


def render(nb, i, path):
    """Переносит чернила nb на фото фона i. Толщину линии и густоту пасты подбирает так, чтобы совпали
    с его же записями на этом фото (Глеб давит на ручку по-разному в разные дни)."""
    import cv2
    b = BGS[i]
    img = cv2.imread(os.path.join(SRC, b['file']))
    bgm = cv2.medianBlur(img, 61).astype(np.float32)
    d = (bgm - img.astype(np.float32))[..., 2]
    T = _ink_ratio(img, d)
    q = np.float32(b['quad'])
    area = np.zeros(d.shape, np.uint8)
    cv2.fillConvexPoly(area, q.astype(np.int32), 1)
    his = _stats(d, area == 0, img) or (115.0, 2.0)
    cells = np.float32([[0, 0], [b['cols'], 0], [b['cols'], b['rows']], [0, b['rows']]])
    Hc = cv2.getPerspectiveTransform(cells, q)
    cpx = np.linalg.norm(q[1] - q[0]) / b['cols']          # клетка на фото, px
    k = cpx / nb.C
    S = np.float32([[1 / (nb.C * k), 0, 0], [0, 1 / (nb.C * k), 0], [0, 0, 1]])
    noise = np.random.default_rng(nb.rnd.randrange(1 << 30)).normal(0, 3, img.shape).astype(np.float32)

    def comp(r, gain):
        ink = nb.ink if not r else cv2.dilate(nb.ink, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)))
        ink = cv2.resize(ink, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
        a = cv2.warpPerspective(ink, Hc @ S, (img.shape[1], img.shape[0]), flags=cv2.INTER_LINEAR)
        a = cv2.GaussianBlur(a, (0, 0), 0.45)                # мягкость камеры
        kk = np.clip(a * gain, 0, 1)[..., None]
        out = np.clip(img.astype(np.float32) * (1 - kk * (1 - T[None, None])) + noise * kk, 0, 255)
        return out.astype(np.uint8)

    best = None
    for r in range(0, 4):              # толще — буквы слипаются в кляксы
        for gain in (1.3, 1.6, 1.9):
            out = comp(r, gain)
            o = out.astype(np.float32)
            dd = (bgm - o)[..., 2]
            st = _stats(dd, area > 0, out)
            if not st:
                continue
            err = abs(st[0] - his[0]) / 40 + abs(st[1] - his[1]) / 0.5
            if best is None or err < best[0]:
                best = (err, r, gain, out)
    out = best[3]
    if b.get('crop'):
        x0, y0, x1, y1 = b['crop']
        out = out[y0:y1, x0:x1]
    cv2.imwrite(path, out, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return path
