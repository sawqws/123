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


def pages(i):
    """Страницы фона i: [{quad, cols, rows, margin, right}] — у разворота две (левая, правая), у старых фонов одна."""
    b = BGS[i]
    if 'pages' in b:
        return b['pages']
    return [{'quad': b['quad'], 'cols': b['cols'], 'rows': b['rows']}]


def sheet(i, seed=1, page=0):
    """Notebook размером с пустую страницу page фона i."""
    p = pages(i)[page]
    sc = p.get('scale', 1.0)    # > 1 — пишет мельче клетки (у него размер гуляет 0.6…1.2 клетки), влезает больше
    return Notebook(cols=round(p['cols'] * sc), rows=round(p['rows'] * sc), seed=seed + 101 * page,
                    margin=p.get('margin', -0.3), right=p.get('right', 0.35), size=p.get('size', 1.0))


def pool():
    """Фоны для работы: кадры целиком (разворот/страница на столе, как он снимает) — без старых обрезанных."""
    return [i for i, b in enumerate(BGS) if 'pages' in b and not b.get('crop')]


def plan(sol, rnd, fits):
    """Раскладывает номера по случайным фонам (Глеб 22.10: «бери все фоны из моих работ и на рандом добавляй»):
    кадры в случайном порядке, без повторов, на каждый — сколько влезет. fits(i, page, nb) -> Notebook с номерами.
    Возвращает [(фон, [Notebook по страницам])]."""
    order = pool()
    rnd.shuffle(order)
    todo, shots = list(sol), []
    for i in order:
        nbs = []
        for pg in range(len(pages(i))):
            nb = sheet(i, seed=rnd.randrange(1 << 20), page=pg)
            fits(nb, todo, not shots and pg == 0)
            nbs.append(nb)
        if any(nb.y > 1.5 * nb.C for nb in nbs):
            shots.append((i, nbs))
        if not todo:
            return shots
    raise ValueError('фонов не хватило — не влезли номера %s' % [n for n, _ in todo])


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
    return (float(np.percentile(L[core > 0], 90)), float(2 * core.sum() / max(1.0, per)), float(hp[core > 0].std()),
            float(np.median(L[core > 0])))


BOLD_L = float(os.environ.get('PAPER_BOLD_L', 0.08))   # Глеб 23.10: «бледно» — замер его штрихов захватывает
BOLD_W = float(os.environ.get('PAPER_BOLD_W', 1.2))    # размытый край, на глаз его паста гуще и толще замера
PRESS = 0.22        # разброс нажима (доля густоты)
PRESS_SIGMA = 9.0   # px фото: нажим меняется от знака к знаку, не внутри штриха
UP = 1   # итоговое фото — в родном размере его фото (Глеб 22.10: при 2.5× с шумодавом и резкостью «фон видно ИИ»)


def render(nbs, i, path):
    """Переносит чернила на фото фона i так, чтобы не отличить от его записей (Глеб 22.10: «чтобы было не отличить,
    что текст наложен, и цвет ручки как я пишу»; «обрезанная фотка выдаёт — фон как у меня обычно»).
    nbs — Notebook или список по страницам фона (None — страница пустая). Кадр — целиком, как он снимает
    (разворот на столе), если у фона не задан crop. Цвет ручки — кривая (pen) с его штрихов на этом фото
    (у разворота со стёртой записью — с исходного фото, `ink_from`); толщина, тёмный тон и шум подбираются под них."""
    import cv2
    b = BGS[i]
    if not isinstance(nbs, (list, tuple)):
        nbs = [nbs]
    img = cv2.imread(os.path.join(SRC, b['file']))
    bgm = cv2.medianBlur(img, 61).astype(np.float32)
    area = np.zeros(img.shape[:2], np.uint8)
    for p in pages(i):
        cv2.fillConvexPoly(area, np.int32(p['quad']), 1)
    ref = cv2.imread(os.path.join(SRC, b['ink_from'])) if b.get('ink_from') else img   # где его штрихи
    rbg = cv2.medianBlur(ref, 61).astype(np.float32)
    if b.get('ink_from'):        # его прежняя запись на исходном фото (до того, как её закрыли клеткой)
        rm = np.zeros(area.shape, np.uint8)
        for p in b.get('ink_pages', []):
            cv2.fillConvexPoly(rm, np.int32(p), 1)
        rmask = rm > 0
    else:
        rmask = area == 0
    curve = pen(ref, rbg, rmask)
    if curve is None:                                         # своих записей на фото нет — синяя паста по умолчанию
        curve = (np.array([0, 0.3, 0.6, 0.8]), np.array([[1, 1, 1], [0.86, 0.74, 0.7], [0.62, 0.42, 0.4], [0.45, 0.24, 0.2]]), 0.8)
    xs, ys, Lmax = curve
    x0, y0, x1, y1 = b.get('crop') or (0, 0, img.shape[1], img.shape[0])
    big = (_clean(img, bgm, area) if not b.get('ink_from') else img)[y0:y1, x0:x1]   # фон — как есть, без фильтров
    f = big.astype(np.float32)
    SS = 4   # перенос: сначала в 4 раза крупнее фото (ровные края), потом вниз до фото — как оптика камеры
    Up = np.float32([[SS, 0, 0], [0, SS, 0], [0, 0, 1]])
    hi = np.zeros((big.shape[0] * SS, big.shape[1] * SS), np.float32)
    seed = 1
    for nb, p in zip(nbs, pages(i)):
        if nb is None:
            continue
        seed = nb.rnd.randrange(1 << 30)
        qb = np.float32(p['quad']) - np.float32([x0, y0])
        cells = np.float32([[0, 0], [nb.cols, 0], [nb.cols, nb.rows], [0, nb.rows]])   # лист nb — на всю страницу
        Hc = cv2.getPerspectiveTransform(cells, qb)
        k = np.linalg.norm(qb[1] - qb[0]) / nb.cols / nb.C   # клетка фото / клетка листа
        S = np.float32([[1 / (nb.C * k * SS), 0, 0], [0, 1 / (nb.C * k * SS), 0], [0, 0, 1]])
        ink = cv2.resize(nb.ink, None, fx=k * SS, fy=k * SS, interpolation=cv2.INTER_AREA if k * SS < 1 else cv2.INTER_CUBIC)
        hi = np.maximum(hi, cv2.warpPerspective(np.clip(ink, 0, 1), Up @ Hc @ S, (hi.shape[1], hi.shape[0]), flags=cv2.INTER_LINEAR))
    warped = {}
    _r = np.random.default_rng(seed + 7)
    wob = cv2.GaussianBlur(_r.normal(0, 1, big.shape[:2]).astype(np.float32), (0, 0), 4.0)
    wob = wob / max(1e-6, wob.std())

    def ink_at(thin):
        """Чернила на фото; thin — насколько тоньше линия (у него в разные дни разная ручка)."""
        if thin not in warped:
            a = hi
            if thin > 0:   # тоньше на thin/SS px фото с каждого края: перо у него тонкое (≈ 2 px на фото)
                se = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * thin + 1, 2 * thin + 1))
                a = cv2.erode(a, se)
            elif thin < 0:   # толще: у него ручка бывает жирнее (давит сильнее)
                se = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * -thin + 1, 2 * -thin + 1))
                a = cv2.dilate(a, se)
            # толщина штриха гуляет, как у шариковой ручки (ровная одинаковая линия выдаёт наложение — Глеб 23.10)
            fat = cv2.dilate(a, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
            k = np.clip(0.5 + 0.5 * cv2.resize(wob, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_LINEAR), 0, 1)
            a = a * (1 - k) + fat * k
            warped[thin] = np.clip(cv2.resize(a, (big.shape[1], big.shape[0]), interpolation=cv2.INTER_AREA), 0, 1)
        return warped[thin]

    paper = cv2.medianBlur(big, 9).astype(np.float32)
    rng = np.random.default_rng(seed)
    nz = cv2.GaussianBlur(rng.normal(0, 1, big.shape[:2]).astype(np.float32), (0, 0), 0.7)
    nz = nz / max(1e-6, nz.std())

    # нажим шариковой ручки гуляет: в одном знаке гуще, в соседнем бледнее (у него p50 густоты ≈ 0.6, p90 ≈ 0.85)
    press = cv2.GaussianBlur(rng.normal(0, 1, big.shape[:2]).astype(np.float32), (0, 0), PRESS_SIGMA)
    press = 1 + PRESS * press / max(1e-6, press.std())

    def comp(thin, gain, blur, noise):
        L = np.clip(cv2.GaussianBlur(ink_at(thin), (0, 0), blur) * gain * press, 0, 1) * Lmax
        ratio = np.stack([np.interp(L, xs, ys[:, c]) for c in range(3)], -1)
        # под пастой — бумага без клетки (иначе на пересечениях с клеткой «бусины»)
        w = np.clip(L / max(Lmax, 1e-6) * 2.5, 0, 1)[..., None]
        o = (f * (1 - w) + paper * w) * ratio
        # шум камеры: в тёмных штрихах матрица шумит сильнее, чем на бумаге
        o = o + (nz * noise * np.clip(L / max(Lmax, 1e-6), 0, 1))[..., None] * np.array([1.0, 0.9, 0.9], np.float32)
        return np.clip(o, 0, 255).astype(np.uint8)

    # подбор под его штрихи на этом же фото: толщина, тёмный тон, шум
    his = _look(ref, rbg, rmask)
    where = cv2.dilate((ink_at(0) > 0.02).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    best = None
    # (Глеб 23.10, обвёл свою запись на фото: «чтобы всё выглядело как это») — по густоте (p50 и p90) и толщине
    for thin in (-2, -1, 0, 1, 2, 3, 4):
        for gain in (0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 1.0, 1.3, 1.7):
            for blur in (0.45, 0.65, 0.9):
                o = comp(thin, gain, blur, 10.0)
                st = _look(o, cv2.medianBlur(o, 61).astype(np.float32), where)
                if not st or not his:
                    continue
                err = (abs(st[3] - (his[3] + BOLD_L)) / 0.04 + abs(st[0] - his[0]) / 0.04 + abs(st[1] - his[1] * BOLD_W) / 0.2
                       + 0.5 * blur + 0.15 * abs(thin))
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
