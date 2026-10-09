"""Алфавит почерком Глеба: шаблон для заполнения и разбор заполненного.

Шаблон — тёмный лист (как заметки на iPad) с клетками. В каждой клетке серая подсказка-буква,
линия строки и пунктир высоты строчной буквы. Буквы идут парами в рамке: слева большая, справа маленькая.
Одинаковые на вид латинские и русские буквы (a/а, o/о, x/х…) пишутся один раз — hand.py берёт русскую.
По углам малиновые метки, по ним лист находится на любом скриншоте или фото (даже если Telegram
его сжал или iPad добавил поля), а маленькие метки сверху — номер листа.

Разобранные буквы дописываются в horo/tmp/fonts/gleb_extra.npz (не в репозиторий: он публичный).
Формат как у horo/fonts/gleb.npz: ключ '<код буквы>_<номер>' — точки скелета в единицах
высоты строчной буквы (x от левого края, y вверх от строки), ключ + 'w' — ширина.

usage:
  python3 horo/alphabet.py template <лист 1..4> out.png
  python3 horo/alphabet.py ingest img1.png [img2.jpg ...]   печатает, какие буквы добавлены
  python3 horo/alphabet.py preview out.png                  пример текста его почерком
  python3 horo/alphabet.py sample out.png                   пример решённого задания (со стилем из hand_style.json)
  python3 horo/alphabet.py renorm                           один раз подогнать по буквам старый банк (бот делает при старте)
  python3 horo/alphabet.py reset                            удалить добавленные буквы
"""
import functools
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

HERE = os.path.dirname(os.path.abspath(__file__))
EXTRA = os.path.join(HERE, 'tmp', 'fonts', 'gleb_extra.npz')
FONT = os.path.join(HERE, 'fonts', 'Neucha.ttf')
FONT2 = os.path.join(HERE, 'fonts', 'Caveat.ttf')  # в Neucha нет ² ³ ≠ ≤ ≥ × ÷

PAGES = [  # (заголовок, символы, парами: большая+маленькая) — не больше 48 клеток на лист
    ('русские буквы А–П', 'АаБбВвГгДдЕеЁёЖжЗзИиЙйКкЛлМмНнОоПп', True),
    ('русские буквы Р–Я', 'РрСсТтУуФфХхЦцЧчШшЩщЪъЫыЬьЭэЮюЯя', True),
    # a c e o p x — как русские а с е о р х, их не повторяем
    ('латинские буквы', 'BbDdFfGgHhIiJjKkLlMmNnQqRrSsTtUuVvWwYyZz', True),
    # ² ³ рисуются уменьшенными 2 и 3, · — точкой, их тоже не повторяем
    ('цифры и знаки', '0123456789+-=()/<>.,:;!?%√≠≤≥×÷', False),
]
CODE0 = 4                  # номер листа на метках = индекс + CODE0 (1..3 были у старого алфавита)
# старый лист 2 (цифры и знаки, сетка 6×6) тоже годится: там в каждой клетке один знак
OLD_DIGITS = ('0123456789+-=()/<>.,:;!?%', 6)  # ² ³ √ ≠ ≤ ≥ · × ÷ со старого листа не берём: там они были не на своих местах
W, H = 1640, 2360          # лист как скриншот iPad
COLS, ROWS = 8, 6
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
    """Центры меток номера листа (двоичный код page+CODE0)."""
    return [(W / 2 + (i - 1) * 90, 20 + MARK / 2) for i in range(3)]


def cell(i, cols=COLS, rows=ROWS):
    """Клетка i: левый край, правый край, верх, линия строки (y)."""
    r, c = divmod(i, cols)
    cw, ch = (W - 2 * SIDE) / cols, (H - TOP - BOTTOM) / rows
    x0 = SIDE + c * cw
    y0 = TOP + r * ch
    return x0, x0 + cw, y0, y0 + ch * 0.68


def template(page, out):
    head, chars, paired = PAGES[page]
    im = Image.new('RGB', (W, H), BG)
    d = ImageDraw.Draw(im)
    for cx, cy in corners():
        d.rectangle([cx - MARK / 2, cy - MARK / 2, cx + MARK / 2, cy + MARK / 2], fill=MAGENTA)
    code = page + CODE0
    for k, (cx, cy) in enumerate(bits()):
        if code >> k & 1:
            d.rectangle([cx - BIT / 2, cy - BIT / 2, cx + BIT / 2, cy + BIT / 2], fill=MAGENTA)
    small = ImageFont.truetype(FONT, 34)
    big, big2 = ImageFont.truetype(FONT, 44), ImageFont.truetype(FONT2, 50)
    blank = big.getmask('\uffff').getbbox(), bytes(big.getmask('\uffff'))
    d.text((W / 2, 125), f'Лист {page + 1} из {len(PAGES)}: {head}', font=small, fill=HINT, anchor='mm')
    d.text((W / 2, 175), 'в рамке слева — большая буква, справа — маленькая' if paired else 'каждый знак в своей клетке',
           font=small, fill=HINT, anchor='mm')
    for i, ch in enumerate(chars):
        x0, x1, y0, base = cell(i)
        d.rectangle([x0 + 6, y0 + 6, x1 - 6, y0 + CELL_H - 6], outline=LINE, width=2)
        if ch == '√':  # его нет ни в одном из шрифтов — рисуем линиями
            d.line([(x0 + 18, y0 + 40), (x0 + 26, y0 + 36), (x0 + 34, y0 + 58), (x0 + 44, y0 + 18), (x0 + 72, y0 + 18)], fill=HINT, width=3)
        else:
            has = (big.getmask(ch).getbbox(), bytes(big.getmask(ch))) != blank
            d.text((x0 + 18, y0 + 14), ch, font=big if has else big2, fill=HINT, anchor='la')
        d.line([x0 + 14, base, x1 - 14, base], fill=LINE, width=3)
        for x in np.arange(x0 + 14, x1 - 14, 22):  # пунктир высоты строчной
            d.line([x, base - XH, x + 10, base - XH], fill=LINE, width=2)
        if paired and i % 2:  # рамка вокруг пары «большая | маленькая»
            px0 = cell(i - 1)[0]
            d.rectangle([px0 + 1, y0 + 1, x1 - 1, y0 + CELL_H - 1], outline=HINT, width=4)
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
    if code == 2:
        return out, 'old-digits'
    if 1 <= code < CODE0:
        raise ValueError('это лист старого алфавита — нажми «✍️ Мой почерк» и заполни новые листы')
    if not CODE0 <= code < CODE0 + len(PAGES):
        raise ValueError('не понял, какой это лист алфавита')
    return out, code - CODE0


def ink(a):
    """Маска написанного: цветное или светлое, в отличие от серого шаблона."""
    a = a.astype(int)
    chroma = a.max(-1) - a.min(-1)
    return ((chroma > 55) & ~_magenta(a)) | (a.mean(-1) > 185)


def glyph_from(mask, base, tiny=False):
    """Маска клетки -> (точки скелета в единицах XH, ширина) или None, если пусто.
    tiny: знак из точек (. , : ;) — мелкие пятна не выбрасываем."""
    from skimage.morphology import skeletonize
    lab, n = ndimage.label(mask, np.ones((3, 3)))
    if not n:
        return None
    area = ndimage.sum(mask, lab, range(1, n + 1))
    keep = np.isin(lab, 1 + np.nonzero(area >= (4 if tiny else 25))[0])   # убрать пылинки от сжатия
    if keep.sum() < (6 if tiny else 60):
        return None
    ys, xs = np.nonzero(skeletonize(keep))
    x0 = xs.min()
    pts = np.stack([(xs - x0) / XH, (base - ys) / XH], 1).astype(np.float32)
    return pts, float((xs.max() - x0) / XH)


# Пишут кто крупнее, кто мельче линеек, и одну букву больше, другую меньше. Поэтому каждая буква
# подгоняется отдельно к размерам из horo/fonts/gleb.npz: основная часть строчной — высотой 0.9,
# заглавные и цифры — 1.25, строка (низ основной части) — на 0.1. Хвосты и надстрочные части
# (р, у, б, й, Д…) в высоту не считаются: где они у буквы, видно по шрифту подсказок (Neucha).
# Знаки (+ = ( …) берут средний масштаб цифр и букв листа, а не растягиваются до высоты цифры.
DIGIT_H, SHORT_H, BOTTOM_Y = 1.25, 0.9, 0.1


@functools.lru_cache(None)
def extent(ch):
    """(низ, верх) знака в Neucha в долях его основной высоты (x-height или высота заглавной),
    0 — строка. У простых букв (0, 1); у «р» низ около -0.57, у «б» верх около 1.7."""
    f = ImageFont.truetype(FONT, 200)

    def tb(c):
        b = f.getbbox(c, anchor='ls')
        return -b[3], -b[1]
    ref = tb('х' if ch.islower() else 'Н')[1]
    bot, top = (v / ref for v in tb(ch))
    return (0.0 if bot > -0.12 else bot), (1.0 if top < 1.12 else top)


def normalize(glyphs):
    """[(знак, точки, ширина)] одного листа -> те же знаки в масштабе банка букв, каждый подогнан отдельно."""
    flat = [float(p[:, 1].min()) for c, p, _ in glyphs if c.isalnum() and extent(c) == (0.0, 1.0)]
    line = float(np.median(flat)) if flat else None  # где он пишет строку относительно линейки

    def body(c, p):
        """(высота основной части, где у неё строка) в координатах листа."""
        bot, top = extent(c)
        lo, hi = float(p[:, 1].min()), float(p[:, 1].max())
        if bot < 0 and top == 1 and line is not None:  # хвост вниз: основная часть — от верха до строки
            h = hi - line
            if 0.6 < h * (top - bot) / (hi - lo) < 1.6:
                return h, line
        h = (hi - lo) / (top - bot)
        return h, lo - bot * h

    out, ks = [None] * len(glyphs), []
    for i, (c, p, w) in enumerate(glyphs):
        if c.isalnum():
            h, base = body(c, p)
            k = (SHORT_H if c.islower() else DIGIT_H) / h
            out[i] = (k, base)
            ks.append(k)
    kmed = float(np.median(ks)) if ks else 1.0
    res = []
    for (c, p, w), kb in zip(glyphs, out):
        k, base = kb or (kmed, line if line is not None else 0.0)
        k = min(max(k, kmed / 2.5), kmed * 2.5)  # клякса или обрывок буквы не раздувается в гиганта
        res.append((c, ((p - [0, base]) * k + [0, BOTTOM_Y]).astype(np.float32), w * k))
    return res


def ingest(paths):
    """Разбирает листы, дописывает буквы в EXTRA. Возвращает (добавленные буквы, ошибки)."""
    bank = dict(np.load(EXTRA)) if os.path.exists(EXTRA) else {}
    added, errors = [], []
    for path in paths:
        try:
            img = Image.open(path)
        except Exception:
            errors.append(f'{os.path.basename(path)}: не открывается как картинка — пришли как фото, а не файлом')
            continue
        try:
            sheet, page = straighten(img)
        except Exception as e:
            errors.append(f'{os.path.basename(path)}: {e}')
            continue
        m = ink(sheet)
        if page == 'old-digits':
            chars, cols = OLD_DIGITS
        else:
            chars, cols = PAGES[page][1], COLS
        rows = ROWS if cols == COLS else 6
        cell_h = (H - TOP - BOTTOM) / rows
        glyphs = []
        for i, ch in enumerate(chars):
            x0, x1, y0, base = cell(i, cols, rows)
            # подсказку в левом верхнем углу не берём: обрезаем верх клетки; края — рамки
            y_top, y_bot = int(y0 + 70), int(y0 + cell_h - 14)
            sub = m[y_top:y_bot, int(x0 + 14):int(x1 - 14)]
            g = glyph_from(sub, base - y_top, tiny=ch in '.,:;')
            if g is not None:
                glyphs.append((ch, g[0], g[1]))
        for ch, pts, w in normalize(glyphs):
            k = ord(ch)
            n = sum(1 for key in bank if key.split('_')[0] == str(k) and not key.endswith('w'))
            bank[f'{k}_{n}'], bank[f'{k}_{n}w'] = pts, np.float32(w)
            added.append(ch)
    if added:
        os.makedirs(os.path.dirname(EXTRA), exist_ok=True)
        np.savez_compressed(EXTRA, **bank)
        open(EXTRA + '.v2', 'w').close()
    return added, errors


def renorm():
    """Буквы, разобранные до подгонки каждой буквы отдельно (весь лист одним масштабом), подогнать заново.
    Один раз: потом рядом лежит метка EXTRA.v2. Возвращает число подогнанных знаков."""
    if not os.path.exists(EXTRA) or os.path.exists(EXTRA + '.v2'):
        return 0
    bank = dict(np.load(EXTRA))
    keys = [k for k in bank if not k.endswith('w')]
    glyphs = normalize([(chr(int(k.split('_')[0])), bank[k], float(bank[k + 'w'])) for k in keys])
    for k, (_, pts, w) in zip(keys, glyphs):
        bank[k], bank[k + 'w'] = pts, np.float32(w)
    np.savez_compressed(EXTRA, **bank)
    open(EXTRA + '.v2', 'w').close()
    return len(keys)


def preview(out):
    sys.path.insert(0, HERE)
    from hand import Hand
    img = Image.new('RGB', (1100, 330), BG)
    h = Hand(img, ink=(90, 160, 240), width=4)
    h.text('привет, это мой почерк', 40, 110, 56)
    h.expr(['x² + 2x = ', ('7', 'b'), ' ≠ 0'], 40, 250, 56)
    h.save(out)


def sample(out):
    """Пример решённого задания: условие «как на сайте» и решение его почерком (стиль из hand_style.json)."""
    sys.path.insert(0, HERE)
    from hand import Hand
    img = Image.new('RGB', (1200, 900), (37, 38, 40))
    d = ImageDraw.Draw(img)
    f = ImageFont.truetype(FONT2, 46)
    d.text((40, 30), '1. Выполните деление дробей:', font=f, fill=(200, 200, 215))
    d.text((40, 95), 'а) 6a/b : 3a/b        б) 5c/2d : (−15c/d)', font=f, fill=(200, 200, 215))
    d.text((40, 470), '2. Решите уравнение: 2x + 3 = 11', font=f, fill=(200, 200, 215))
    h = Hand(img, ink=(90, 160, 240), width=4, seed=3)
    h.circle(62, 125, 30, 28)
    h.expr([('6a', 'b'), ' · ', ('b', '3a'), ' = 2'], 70, 260, 50)
    h.expr([('5c', '2d'), ' · ', ('−d', '15c'), ' = ', ('−1', '6')], 600, 260, 50)
    h.text('2x = 11 − 3', 70, 610, 50)
    h.text('2x = 8', 70, 700, 50)
    h.text('Ответ: x = 4', 70, 800, 50)
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
    elif cmd == 'sample':
        sample(args[0])
    elif cmd == 'renorm':
        print('Подогнал знаков:', renorm())
    elif cmd == 'reset':
        for p in (EXTRA, EXTRA + '.v2'):
            if os.path.exists(p):
                os.remove(p)
        print('Добавленные буквы удалены')
    else:
        sys.exit(__doc__)
