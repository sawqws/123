"""Готовит разворот для письма (Brain raw/images/spread_<n>.jpg): закрывает его прежнюю запись на странице чистой
клеткой этой же страницы ниже — полосами, со сдвигом, подобранным по линиям клетки (Глеб 22.10: «обрезанная фотка
выдаёт — фон как у меня обычно»: кадр целиком, разворот на столе). Ниже — как сделан spread_1 из blank_1."""
import cv2, numpy as np, sys
def clean(img, poly, ref_y=(305, 345), band=36, dyr=(160, 240)):
    """Закрывает запись внутри poly чистой клеткой этой же страницы ниже: для каждой вертикальной полосы — свой сдвиг
    (dx, dy), подобранный так, чтобы линии клетки совпали (по чистой полосе ref_y сразу под заплаткой)."""
    f = img.astype(np.float32); g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    h, w = g.shape
    mask = np.zeros((h, w), np.uint8); cv2.fillPoly(mask, [np.int32(poly)], 1)
    xs = np.nonzero(mask.any(0))[0]; x0, x1 = xs.min(), xs.max() + 1
    y0r, y1r = ref_y
    shifts = []
    for bx in range(x0, x1, band):
        bx1 = min(x1, bx + band)
        ref = g[y0r:y1r, bx:bx1]; ref = ref - ref.mean()
        best = None
        for dy in range(dyr[0], dyr[1]):
            for dx in range(-4, 5):
                if bx + dx < 0 or bx1 + dx > w: continue
                s = g[y0r + dy:y1r + dy, bx + dx:bx1 + dx]; s = s - s.mean()
                e = ((s - ref) ** 2).mean()
                if best is None or e < best[0]: best = (e, dx, dy)
        shifts.append(((bx + bx1) / 2, best[1], best[2]))
    cx = np.array([s[0] for s in shifts]); dxs = np.array([s[1] for s in shifts], float); dys = np.array([s[2] for s in shifts], float)
    # сдвиг плавно по x (медиана соседей против выбросов)
    from scipy.ndimage import median_filter
    dxs, dys = median_filter(dxs, 3, mode='nearest'), median_filter(dys, 3, mode='nearest')
    X = np.arange(w, dtype=np.float32)
    DX = np.interp(X, cx, dxs).astype(np.float32); DY = np.interp(X, cx, dys).astype(np.float32)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    src = cv2.remap(img, xx + DX[None], yy + DY[None], cv2.INTER_LINEAR).astype(np.float32)
    m = cv2.GaussianBlur(mask.astype(np.float32), (0, 0), 2.5)
    return (f * (1 - m[..., None]) + src * m[..., None]).astype(np.uint8), shifts
if __name__ == '__main__':
    img = cv2.imread('/home/user/Brain/raw/images/blank_1.jpg')
    poly = [[22, 166], [400, 164], [600, 179], [660, 188], [706, 200], [706, 300], [22, 300]]
    out, sh = clean(img, poly, dyr=(183, 205))
    print([(int(a), b, c) for a, b, c in sh])
    cv2.imwrite('/home/user/Brain/raw/images/spread_1.jpg', out, [cv2.IMWRITE_JPEG_QUALITY, 97])
    cv2.imwrite('er.png', cv2.resize(out[110:340, 0:760], None, fx=1.35, fy=1.35))
