"""Рулетка iPhone на фото: стирает линейку в дюймах и рисует на том же месте сантиметровую той же длины.

Запускается в avito/.venv (numpy, scipy и Pillow приезжают вместе с rembg):
  ruler.py label <фото> <куда>       → {"found": true}: подпись рулетки крупно, чтобы Claude прочитал число
  ruler.py cm <фото> <куда> <дюймы>  → {"ok": true, "cm": "30,5"}
Точность та же, что у самой рулетки (она округляет до ½ дюйма, это ±0,6 см), поэтому на фото стоит «≈».
"""
import json
import os
import sys

WHITE = (245, 230, 215, 200)  # насколько белым считать пиксель линейки
MIN_TICKS = 12     # настоящая рулетка — длинная линия с частыми делениями
LABEL_REACH = 3.2  # как далеко от оси конца линии (в радиусах кружка) может стоять число рулетки
SIZE = 1200        # итоговый квадрат — как у остальных кадров (photos.py)
FILL = 0.86        # какую часть кадра занимает линейка
TAPE_BACK = 0.4    # лента от оси назад, в радиусах кружка: только старая линия, логотип на стельке виден
TAPE_FRONT = 1.9   # лента от оси вперёд: закрывает обычные деления старой рулетки
FONTS = ["DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf", "arialbd.ttf", "C:/Windows/Fonts/arialbd.ttf"]


def _axis(ys, xs):
    """Центр и единичный вектор главной оси облака точек (вектор направлен вниз по кадру)."""
    import numpy as np
    pts = np.stack([xs, ys], axis=1).astype(float)
    center = pts.mean(axis=0)
    _, vec = np.linalg.eigh(np.cov((pts - center).T))
    d = vec[:, 1]
    if d[1] < 0 or (d[1] == 0 and d[0] < 0):
        d = -d
    return center, d


def _shape(comp):
    """Связная белая область → геометрия рулетки или None, если это не рулетка."""
    import numpy as np
    from scipy import ndimage
    filled = ndimage.binary_fill_holes(comp)
    dist = ndimage.distance_transform_edt(filled)
    radius = dist.max()
    if radius < 6:
        return None
    thick = dist >= max(2.5, radius * 0.12)  # линия, кружки и подпись — без тонких делений
    ys, xs = np.nonzero(thick)
    if len(ys) < 100:
        return None
    center, d = _axis(ys, xs)
    n = np.array([-d[1], d[0]])
    s = (np.stack([xs, ys], axis=1) - center) @ d
    t = (np.stack([xs, ys], axis=1) - center) @ n
    length = s.max() - s.min()
    if length < radius * 8 or np.percentile(np.abs(t), 90) > length * 0.06:
        return None
    # кружки на концах: толстые места у самых краёв линии
    ky, kx = np.nonzero(dist >= radius * 0.75)
    ks = (np.stack([kx, ky], axis=1) - center) @ d
    near_a, near_b = ks <= ks.min() + radius, ks >= ks.max() - radius
    # у края может прилипать число над кружком, поэтому запас больше радиуса
    if ks.min() - s.min() > radius * 1.6 or s.max() - ks.max() > radius * 1.6:
        return None
    a = np.stack([kx, ky], axis=1)[near_a].mean(axis=0)  # центр кружка — середина его толстой части
    b = np.stack([kx, ky], axis=1)[near_b].mean(axis=0)
    # деления: тонкие белые полоски поперёк линии
    line_half = np.median(dist[thick & (dist < radius * 0.6)]) if (thick & (dist < radius * 0.6)).any() else 3
    ty, tx = np.nonzero(filled)
    ps = (np.stack([tx, ty], axis=1) - a) @ d
    pt = (np.stack([tx, ty], axis=1) - a) @ n
    span = np.linalg.norm(b - a)
    off = np.abs(pt) > line_half * 2.5
    bins = np.zeros(int(span) + 2)
    inside = off & (ps >= 0) & (ps <= span)
    np.maximum.at(bins, ps[inside].astype(int), np.abs(pt[inside]))
    marks = ndimage.label(bins > 0)[0]
    # подпись посередине закрывает несколько делений, поэтому считаем только те, что вне кружков и подписи
    ticks = int(marks.max())
    if ticks < MIN_TICKS:
        return None
    # самое длинное деление — середина длины (в перспективе она не в середине отрезка)
    lo, hi = int(span * 0.3), int(span * 0.7)
    mid = (lo + int(np.argmax(bins[lo:hi]))) / span if bins[lo:hi].max() > 0 else 0.5
    side = 1.0 if pt[inside].mean() > 0 else -1.0
    return {"a": a, "b": b, "d": d, "n": n, "radius": float(radius), "line": float(line_half * 2),
            "mid": float(mid), "side": side, "ticks": ticks, "filled": filled}


def find(rgb):
    """Рулетка на кадре (массив HxWx3) или None."""
    import numpy as np
    from scipy import ndimage
    best, best_span = None, 0
    for level in WHITE:  # линейка полупрозрачная: на тёмной стельке белый темнее, поэтому порог не один
        white = rgb.min(axis=2) >= level
        lab, count = ndimage.label(white, structure=np.ones((3, 3)))
        if not count:
            continue
        sizes = ndimage.sum_labels(white, lab, range(1, count + 1))
        for i in np.argsort(sizes)[::-1][:3]:
            found = _shape(lab == i + 1) if sizes[i] >= 500 else None
            if found and np.linalg.norm(found["b"] - found["a"]) > best_span:
                best, best_span = found, np.linalg.norm(found["b"] - found["a"])
        if best and best["ticks"] >= 20:  # линия цела, ниже порог опускать незачем
            break
    return best


def mask_of(rgb, r):
    """Всё, что нарисовала рулетка: линия, деления, кружки, подпись посередине и числа за концами линии."""
    import numpy as np
    from scipy import ndimage
    h, w = rgb.shape[:2]
    radius, span = r["radius"], np.linalg.norm(r["b"] - r["a"])
    ys, xs = np.mgrid[0:h, 0:w]
    pts = np.stack([xs, ys], axis=-1).astype(float) - r["a"]
    s, t = pts @ r["d"], pts @ (r["n"] * r["side"])  # t > 0 — сторона, куда смотрят деления
    area = (s >= -radius * 4.6) & (s <= span + radius * 4.6) & (np.abs(t) <= radius * LABEL_REACH)
    area &= ~((s >= 0) & (s <= span) & (t < -radius * 1.3))
    # часть делений полупрозрачна и не связана с линией: ищем то, что заметно светлее своего окружения.
    # Сравнение с медианой, а не простой порог: светлая стелька и светлые доски остаются нетронутыми
    level = rgb.min(axis=2)
    yy, xx = np.nonzero(area)
    y0, y1, x0, x1 = yy.min(), yy.max() + 1, xx.min(), xx.max() + 1
    part = level[y0:y1, x0:x1].astype(int)
    around = ndimage.median_filter(part[::2, ::2], size=31)
    around = np.repeat(np.repeat(around, 2, axis=0), 2, axis=1)[:y1 - y0, :x1 - x0]
    drawn = np.zeros((h, w), bool)
    drawn[y0:y1, x0:x1] = (part >= 205) & (part - around >= 18)
    mask = r["filled"] | (area & drawn)
    grow = max(3, int(round(radius * 0.1)))
    return ndimage.binary_dilation(mask, iterations=grow)


def fill(rgb, mask, rounds=150):
    """Закрашивает маску цветом соседних пикселей: сначала ближайшим, потом сглаживает внутри маски."""
    import numpy as np
    from scipy import ndimage
    ys, xs = np.nonzero(mask)
    pad = 12
    y0, y1, x0, x1 = max(ys.min() - pad, 0), ys.max() + pad, max(xs.min() - pad, 0), xs.max() + pad
    part, m = rgb[y0:y1, x0:x1].astype(float), mask[y0:y1, x0:x1]
    idx = ndimage.distance_transform_edt(m, return_distances=False, return_indices=True)
    part = part[idx[0], idx[1]]
    for _ in range(rounds):
        part[m] = ndimage.uniform_filter(part, size=(3, 3, 1))[m]
    out = rgb.copy()
    out[y0:y1, x0:x1] = np.clip(part, 0, 255).astype(rgb.dtype)
    return out


def font(size):
    from PIL import ImageFont
    for name in FONTS:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    raise RuntimeError("нет шрифта с кириллицей (нужен DejaVu Sans)")


def nice_cm(cm):
    """30,48 → «30,5», 29,21 → «29»: рулетка не точнее, полсантиметра — достаточно."""
    value = round(cm * 2) / 2
    return (f"{value:.1f}" if value % 1 else f"{value:.0f}").replace(".", ",")


def backdrop(img, side):
    """Поле вокруг снимка цветом пола: края кадра без резких белых полос и без размазанной руки."""
    import numpy as np
    from PIL import Image
    rgb = np.asarray(img)
    band = max(1, img.width // 12)
    edges = np.concatenate([rgb[:, :band].reshape(-1, 3), rgb[:, -band:].reshape(-1, 3)])
    return Image.new("RGB", (side, side), tuple(int(v) for v in np.median(edges, axis=0)))


def frame(img, r):
    """Квадрат как у остальных кадров объявления: линейка видна целиком, снимок плавно переходит в цвет пола."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter
    side = int(np.linalg.norm(r["b"] - r["a"]) / FILL)
    center = (r["a"] + r["b"]) / 2

    def start(length, mid):  # снимок уже квадрата — по центру, снимок шире — вокруг линейки
        return (length - side) // 2 if length <= side else int(min(max(mid - side / 2, 0), length - side))

    origin = np.array([start(img.width, center[0]), start(img.height, center[1])])
    canvas = backdrop(img, side)
    soft = max(4, side // 45)  # мягкий переход от снимка к полю
    fade = Image.new("L", img.size, 0)
    ImageDraw.Draw(fade).rectangle([soft * 2 if origin[0] < 0 else -1, soft * 2 if origin[1] < 0 else -1,
                                    img.width - (soft * 2 if origin[0] < 0 else -1),
                                    img.height - (soft * 2 if origin[1] < 0 else -1)], fill=255)
    fade = fade.filter(ImageFilter.GaussianBlur(soft))
    canvas.paste(img, (int(-origin[0]), int(-origin[1])), fade)
    k = SIZE / side
    return canvas.resize((SIZE, SIZE), Image.LANCZOS), dict(r, a=(r["a"] - origin) * k, b=(r["b"] - origin) * k,
                                                            radius=r["radius"] * k, line=r["line"] * k)


def draw(img, r, cm):
    """Сантиметровая лента той же длины: непрозрачная, поэтому след прежней рулетки под ней не виден.
    От нижнего конца вверх, деления через 1 см, числа через 5 см."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter
    k = 2  # рисуем вдвое крупнее и уменьшаем: края получаются гладкими
    layer = Image.new("RGBA", (img.width * k, img.height * k), (0, 0, 0, 0))
    pen = ImageDraw.Draw(layer)
    a, b = r["a"] * k, r["b"] * k  # a — верхний конец, b — нижний
    side = r["n"] * r["side"]
    along = (a - b) / np.linalg.norm(a - b)  # снизу вверх
    span = np.linalg.norm(b - a)
    unit = span / 100
    mid = 1 - r["mid"]  # доля длины от нижнего конца, где на кадре середина
    # перспектива: u → доля отрезка; через концы и настоящую середину длины
    w = (2 * mid - 1) / (1 - mid)

    def at(u):
        return b + (a - b) * ((1 + w) * u / (1 + w * u))

    radius = r["radius"] * k
    back, front = radius * TAPE_BACK, radius * TAPE_FRONT  # от оси: кружки назад, деления вперёд
    over = radius * 1.1  # лента длиннее линии: закрывает кружки на концах
    tape = [tuple(a + along * over - side * back), tuple(a + along * over + side * front),
            tuple(b - along * over + side * front), tuple(b - along * over - side * back)]
    shade = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    ImageDraw.Draw(shade).polygon(tape, fill=(0, 0, 0, 110))
    layer = Image.alpha_composite(layer, shade.filter(ImageFilter.GaussianBlur(radius * 0.25)))
    pen = ImageDraw.Draw(layer)
    ink = (20, 24, 30, 255)
    pen.polygon(tape, fill=(255, 255, 255, 255), outline=(0, 0, 0, 120))
    wide = back + front
    start = -back  # деления идут от заднего края ленты
    line = max(k * 2, int(radius * 0.09))
    ticks = [(at(n / cm), n) for n in range(0, int(cm) + 1)]
    for p, n in ticks:
        length = wide * (0.42 if n % 5 == 0 else 0.24)
        pen.line([tuple(p + side * start), tuple(p + side * (start + length))], fill=ink, width=line)
    big = font(int(unit * 4.2))
    text = f"≈{nice_cm(cm)} см"
    c = at(0.5)
    x0, y0, x1, y1 = pen.textbbox(tuple(c), text, font=big, anchor="mm")
    pad_x, pad_y = unit * 1.3, unit * 0.8
    small = font(int(unit * 2.5))
    for p, n in ticks[1:]:
        if n % 5:
            continue
        q = p + side * (start + wide * 0.7)
        bx0, by0, bx1, by1 = pen.textbbox(tuple(q), str(n), font=small, anchor="mm")
        if bx1 > x0 - pad_x and bx0 < x1 + pad_x and by1 > y0 - pad_y and by0 < y1 + pad_y:
            continue  # число не вылезает из-за подписи с длиной
        pen.text(tuple(q), str(n), font=small, fill=ink, anchor="mm")
    pen.rounded_rectangle([x0 - pad_x, y0 - pad_y, x1 + pad_x, y1 + pad_y], radius=(y1 - y0) / 2 + pad_y,
                          fill=(255, 255, 255, 255), outline=(0, 0, 0, 150), width=k)
    pen.text(tuple(c), text, font=big, fill=(0, 0, 0, 255), anchor="mm")
    layer = layer.resize(img.size, Image.LANCZOS)
    return Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")


def to_cm(src, dst, inches):
    """Перерисовывает рулетку в сантиметрах. False, если рулетки на кадре нет."""
    import numpy as np
    from PIL import Image, ImageOps
    with Image.open(src) as raw:
        img = ImageOps.exif_transpose(raw).convert("RGB")
    rgb = np.asarray(img)
    r = find(rgb)
    if not r:
        return False
    square, r = frame(Image.fromarray(fill(rgb, mask_of(rgb, r))), r)
    draw(square, r, float(inches) * 2.54).save(dst, "JPEG", quality=93)
    return True


def label(src, dst):
    """Подпись посередине рулетки крупным планом. False, если рулетки нет."""
    import numpy as np
    from PIL import Image, ImageOps
    with Image.open(src) as raw:
        img = ImageOps.exif_transpose(raw).convert("RGB")
    r = find(np.asarray(img))
    if not r:
        return False
    c = (r["a"] + r["b"]) / 2
    half = r["radius"] * 5
    box = [int(c[0] - half), int(c[1] - half), int(c[0] + half), int(c[1] + half)]
    img.crop(box).save(dst, "JPEG", quality=92)
    return True


def main(argv):
    if len(argv) >= 3 and argv[0] == "label":
        return {"found": label(argv[1], argv[2])}
    if len(argv) >= 4 and argv[0] == "cm":
        ok = to_cm(argv[1], argv[2], float(argv[3].replace(",", ".")))
        return {"ok": ok, "cm": nice_cm(float(argv[3].replace(",", ".")) * 2.54)}
    raise SystemExit("ruler.py label <фото> <куда> | ruler.py cm <фото> <куда> <дюймы>")


if __name__ == "__main__":
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    print(json.dumps(main(sys.argv[1:]), ensure_ascii=False))
