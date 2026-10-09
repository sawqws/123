"""Алфавит почерком Глеба: шаблон для заполнения и разбор заполненного.

Шаблон — тёмный лист (как заметки на iPad) с клетками. В каждой клетке серая подсказка-буква,
линия строки и пунктир высоты строчной буквы. По углам малиновые метки, по ним лист находится
на любом скриншоте или фото (даже если Telegram его сжал или iPad добавил поля), а маленькие
метки сверху — номер страницы.

Разобранные буквы дописываются в horo/tmp/fonts/gleb_extra.npz (не в репозиторий: он публичный).
Формат как у horo/fonts/gleb.npz: ключ '<код буквы>_<номер>' — точки скелета в единицах
высоты строчной буквы (x от левого края, y вверх от строки), ключ + 'w' — ширина.

usage:
  python3 horo/alphabet.py template <страница 1..3> out.png
  python3 horo/alphabet.py ingest img1.png [img2.jpg ...]   печатает, какие буквы добавлены
  python3 horo/alphabet.py preview out.png                  пример текста его почерком
  python3 horo/alphabet.py reset                            удалить добавленные буквы
"""
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

HERE = os.path.dirname(os.path.abspath(__file__))
EXTRA = os.path.join(HERE, 'tmp', 'fonts', 'gleb_extra.npz')
FONT = os.path.join(HERE, 'fonts', 'Neucha.ttf')

PAGES = [
    'абвгдеёжзийклмнопрстуфхцчшщъыьэюя',
    'abcdefghijklmnopqrstuvwxyz0123456789',
    '+-=()/<>.,:;!?%²³√≠≤≥·×÷|[]{}',
]
W, H = 1640, 2360          # лист как скриншот iPad
COLS, ROWS = 6, 6
TOP, SIDE, BOTTOM = 230, 70, 110
CELL_W = (W - 2 * SIDE) / COLS
CELL_H = (H - TOP - BOTTOM) / ROWS
XH = 80                    # высота строчной буквы в шаблоне, px
MARK = 60                  # размер угловой метки
BIT = 34                   # размер метки номера страницы
BG, LINE, HINT, MAGENTA = (30, 30, 32), (78, 78, 82), (120, 120, 126), (230, 0, 200)


def corners():
    """Центры угловых меток: левый верх, правый верх, правый низ, левый низ."""
    m = 20 + MARK / 2
    return np.array([(m, m), (W - m, m), (W - m, H - m), (m, H - m)], float)


def bits():
    """Центры меток номера страницы (двоичный код page+1)."""
    return [(W / 2 + (i - 1) * 90, 20 + MARK / 2) for i in range(3)]


def cell(i):
    """Клетка i: левый край, правый край, верх, линия строки (y)."""
    r, c = divmod(i, COLS)
    x0 = SIDE + c * CELL_W
    y0 = TOP + r * CELL_H
    return x0, x0 + CELL_W, y0, y0 + CELL_H * 0.68


def template(page, out):
    chars = PAGES[page]
    im = Image.new('RGB', (W, H), BG)
    d = ImageDraw.Draw(im)
    for cx, cy in corners():
        d.rectangle([cx - MARK / 2, cy - MARK / 2, cx + MARK / 2, cy + MARK / 2], fill=MAGENTA)
    code = page + 1
    for k, (cx, cy) in enumerate(bits()):
        if code >> k & 1:
            d.rectangle([cx - BIT / 2, cy - BIT / 2, cx + BIT / 2, cy + BIT / 2], fill=MAGENTA)
    small = ImageFont.truetype(FONT, 34)
    big = ImageFont.truetype(FONT, 44)
    d.text((W / 2, 140), f'Страница {page + 1} из {len(PAGES)}: напиши каждую букву в своей клетке, как обычно',
           font=small, fill=HINT, anchor='mm')
    for i, ch in enumerate(chars):
        x0, x1, y0, base = cell(i)
        d.rectangle([x0 + 6, y0 + 6, x1 - 6, y0 + CELL_H - 6], outline=LINE, width=2)
        d.text((x0 + 18, y0 + 14), ch, font=big, fill=HINT, anchor='la')
        d.line([x0 + 14, base, x1 - 14, base], fill=LINE, width=3)
        for x in np.arange(x0 + 14, x1 - 14, 22):  # пунктир высоты строчной
            d.line([x, base - XH, x + 10, base - XH], fill=LINE, width=2)
    im.save(out)


# ---------- разбор заполненного листа ----------

def _magenta(a):
    r, g, b = a[..., 0].astype(int), a[..., 1].astype(int), a[..., 2].astype(int)
    return (r > 150) & (b > 120) & (g < 110) & (r - g > 90) & (b - g > 60)


def _homography(src, dst):
    """Матрица 3x3, переводящая точки src в dst (4 пары)."""
    A = []
    for (x, y), (u, v) in zip(src, dst):
        A.append([x, y, 1, 0, 0, 0, -u * x, -u * y, -u])
        A.append([0, 0, 0, x, y, 1, -v * x, -v * y, -v])
    h = np.linalg.svd(np.array(A))[2][-1]
    return (h / h[-1]).reshape(3, 3)


def _find_marks(a):
    """Центры угловых меток на картинке (в порядке corners()) или None."""
    lab, n = ndimage.label(_magenta(a))
    if n < 4:
        return None
    idx = range(1, n + 1)
    area = np.array(ndimage.sum(np.ones(lab.shape), lab, idx))
    cent = np.array(ndimage.center_of_mass(np.ones(lab.shape), lab, idx))[:, ::-1]  # (x, y)
    big = area >= 0.45 * area.max()
    h, w = lab.shape
    out = []
    for cx, cy in [(0, 0), (w, 0), (w, h), (0, h)]:
        dist = np.hypot(cent[:, 0] - cx, cent[:, 1] - cy) + np.where(big, 0, 1e9)
        out.append(cent[dist.argmin()])
    out = np.array(out)
    if len({tuple(np.round(p)) for p in out}) < 4:
        return None
    return out


def straighten(img):
    """Картинку -> лист в координатах шаблона (W x H, RGB) и номер страницы."""
    a = np.asarray(img.convert('RGB'))
    marks = _find_marks(a)
    if marks is None:
        raise ValueError('не нашёл 4 малиновые метки по углам — пришли лист целиком')
    Hm = _homography(corners(), marks)              # из шаблона в картинку
    yy, xx = np.mgrid[0:H, 0:W].astype(float)
    p = Hm @ np.stack([xx.ravel(), yy.ravel(), np.ones(xx.size)])
    px, py = p[0] / p[2], p[1] / p[2]
    out = np.stack([ndimage.map_coordinates(a[..., k].astype(float), [py, px], order=1, cval=BG[k]).reshape(H, W)
                    for k in range(3)], -1).astype(np.uint8)
    code = 0
    for k, (cx, cy) in enumerate(bits()):
        box = out[int(cy - BIT / 4):int(cy + BIT / 4), int(cx - BIT / 4):int(cx + BIT / 4)]
        if _magenta(box).mean() > 0.5:
            code |= 1 << k
    if not 1 <= code <= len(PAGES):
        raise ValueError('не понял, какая это страница алфавита')
    return out, code - 1


def ink(a):
    """Маска написанного: цветное или светлое, в отличие от серого шаблона."""
    a = a.astype(int)
    chroma = a.max(-1) - a.min(-1)
    return ((chroma > 55) & ~_magenta(a)) | (a.mean(-1) > 185)


def glyph_from(mask, base):
    """Маска клетки -> (точки скелета в единицах XH, ширина) или None, если пусто."""
    from skimage.morphology import skeletonize
    lab, n = ndimage.label(mask, np.ones((3, 3)))
    if not n:
        return None
    area = ndimage.sum(mask, lab, range(1, n + 1))
    keep = np.isin(lab, 1 + np.nonzero(area >= 25)[0])   # убрать пылинки от сжатия
    if keep.sum() < 60:
        return None
    ys, xs = np.nonzero(skeletonize(keep))
    x0 = xs.min()
    pts = np.stack([(xs - x0) / XH, (base - ys) / XH], 1).astype(np.float32)
    return pts, float((xs.max() - x0) / XH)


def ingest(paths):
    """Разбирает листы, дописывает буквы в EXTRA. Возвращает (добавленные буквы, ошибки)."""
    bank = dict(np.load(EXTRA)) if os.path.exists(EXTRA) else {}
    added, errors = [], []
    for path in paths:
        try:
            sheet, page = straighten(Image.open(path))
        except Exception as e:
            errors.append(f'{os.path.basename(path)}: {e}')
            continue
        m = ink(sheet)
        for i, ch in enumerate(PAGES[page]):
            x0, x1, y0, base = cell(i)
            # подсказку в левом верхнем углу не берём: обрезаем верх клетки
            y_top, y_bot = int(y0 + 70), int(y0 + CELL_H - 12)
            sub = m[y_top:y_bot, int(x0 + 12):int(x1 - 12)]
            g = glyph_from(sub, base - y_top)
            if g is None:
                continue
            k = ord(ch)
            n = sum(1 for key in bank if key.split('_')[0] == str(k) and not key.endswith('w'))
            bank[f'{k}_{n}'], bank[f'{k}_{n}w'] = g[0], np.float32(g[1])
            added.append(ch)
    if added:
        os.makedirs(os.path.dirname(EXTRA), exist_ok=True)
        np.savez_compressed(EXTRA, **bank)
    return added, errors


def preview(out):
    sys.path.insert(0, HERE)
    from hand import Hand
    img = Image.new('RGB', (1100, 330), BG)
    h = Hand(img, ink=(90, 160, 240), width=4)
    h.text('привет, это мой почерк', 40, 110, 56)
    h.expr(['x² + 2x = ', ('7', 'b'), ' ≠ 0'], 40, 250, 56)
    h.save(out)


if __name__ == '__main__':
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == 'template':
        template(int(args[0]) - 1, args[1])
    elif cmd == 'ingest':
        added, errors = ingest(args)
        print('Добавил:', ' '.join(added) if added else 'ничего')
        for e in errors:
            print('Ошибка:', e)
        sys.exit(0 if added or not errors else 1)
    elif cmd == 'preview':
        preview(args[0])
    elif cmd == 'reset':
        if os.path.exists(EXTRA):
            os.remove(EXTRA)
        print('Добавленные буквы удалены')
    else:
        sys.exit(__doc__)
