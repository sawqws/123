#!/usr/bin/env python3
"""Убирает фон с фото вещи: остаётся только вещь на чисто белом квадрате (для Авито).

Запуск (из venv бота, где стоят rembg и Pillow):
  avito/.venv/bin/python avito/photos.py вход1.jpg выход1.jpg [вход2.jpg выход2.jpg ...]
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


def main(argv):
    from PIL import Image, ImageOps
    from rembg import new_session, remove
    pairs = list(zip(argv[0::2], argv[1::2]))
    session = new_session(os.environ.get("AVITO_BG_MODEL") or "isnet-general-use")
    out = []
    for src, dst in pairs:
        try:
            im = ImageOps.exif_transpose(Image.open(src)).convert("RGB")
            im.thumbnail((MAX_IN, MAX_IN), Image.LANCZOS)
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
    if len(sys.argv) < 3 or len(sys.argv) % 2 == 0:
        sys.exit("photos.py вход выход [вход выход ...]")
    main(sys.argv[1:])
