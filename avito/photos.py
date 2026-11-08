#!/usr/bin/env python3
"""Убирает фон с фото вещи: остаётся только вещь на чисто белом квадрате (для Авито).

Запуск (из venv бота, где стоят rembg и Pillow):
  avito/.venv/bin/python avito/photos.py вход1.jpg выход1.jpg [вход2.jpg выход2.jpg ...]
  avito/.venv/bin/python avito/photos.py --square вход1.jpg выход1.jpg ...  — крупные планы: фон на месте, только обрезка
  до такого же квадрата там, где больше всего вещи (вырезать бирку или стельку rembg толком не умеет).
Печатает JSON: [{"src", "dst", "ok", "error"?}]. Модель — AVITO_BG_MODEL (по умолчанию isnet-general-use),
скачивается rembg один раз в ~/.u2net. Отдельный процесс: память модели освобождается сразу после обработки.
"""
import json
import os
import sys

SIZE = 1200      # сторона итогового квадрата, px
FILL = 0.86      # какую часть кадра занимает вещь
MAX_IN = 1600    # большие фото уменьшаем до этого размера перед обработкой — быстрее на слабом сервере


def place_on_white(cut, size=SIZE, fill=FILL):
    """RGBA-вырезка → вещь по центру белого квадрата. Пустая маска → None."""
    from PIL import Image
    alpha = cut.getchannel("A")
    box = alpha.point(lambda a: 255 if a > 24 else 0).getbbox()
    if not box:
        return None
    obj = cut.crop(box)
    w, h = obj.size
    k = size * fill / max(w, h)
    obj = obj.resize((max(1, round(w * k)), max(1, round(h * k))), Image.LANCZOS)
    canvas = Image.new("RGB", (size, size), (255, 255, 255))
    canvas.paste(obj, ((size - obj.width) // 2, (size - obj.height) // 2), obj)
    return canvas


def best_square(alpha):
    """Маска вещи → квадрат (left, top, right, bottom) по короткой стороне там, где вещи больше всего.
    Равноценные места — ближе к центру кадра: так почти пустая маска даёт обычную обрезку по центру."""
    import numpy as np
    w, h = alpha.size
    side = min(w, h)
    if w == h:
        return (0, 0, w, h)
    profile = (np.asarray(alpha) > 24).sum(axis=1 if h > w else 0).astype(np.int64)
    total = np.concatenate([[0], np.cumsum(profile)])
    sums = total[side:] - total[:-side]
    good = np.nonzero(sums >= sums.max() * 0.98)[0]
    start = int(good[np.argmin(np.abs(good - (max(w, h) - side) / 2))])
    return (0, start, w, start + side) if h > w else (start, 0, start + side, h)


def to_square(im, session, remove):
    from PIL import Image
    alpha = remove(im, session=session, post_process_mask=True, only_mask=True).convert("L")
    return im.crop(best_square(alpha)).resize((SIZE, SIZE), Image.LANCZOS)


def main(argv):
    from PIL import Image, ImageOps
    from rembg import new_session, remove
    square = argv[0] == "--square"
    argv = argv[1:] if square else argv
    pairs = list(zip(argv[0::2], argv[1::2]))
    session = new_session(os.environ.get("AVITO_BG_MODEL") or "isnet-general-use")
    out = []
    for src, dst in pairs:
        try:
            im = ImageOps.exif_transpose(Image.open(src)).convert("RGB")
            im.thumbnail((MAX_IN, MAX_IN), Image.LANCZOS)
            if square:
                to_square(im, session, remove).save(dst, "JPEG", quality=92, optimize=True)
                out.append({"src": src, "dst": dst, "ok": True})
                continue
            cut = remove(im, session=session, post_process_mask=True)
            res = place_on_white(cut.convert("RGBA"))
            if res is None:
                out.append({"src": src, "dst": dst, "ok": False, "error": "вещь на фото не нашлась"})
                continue
            res.save(dst, "JPEG", quality=92, optimize=True)
            out.append({"src": src, "dst": dst, "ok": True})
        except Exception as e:  # одно плохое фото не должно ломать остальные
            out.append({"src": src, "dst": dst, "ok": False, "error": str(e)[:200]})
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) < 3 or len(sys.argv[1:]) % 2 != (1 if sys.argv[1] == "--square" else 0):
        sys.exit("photos.py [--square] вход выход [вход выход ...]")
    main(sys.argv[1:])
