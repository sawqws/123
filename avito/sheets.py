#!/usr/bin/env python3
"""Обзорные листы для пачки фото: кадры сеткой, на каждом крупный номер. По ним Claude раскладывает фото по вещам.

Запуск (из venv бота, где стоит Pillow):
  avito/.venv/bin/python avito/sheets.py папка_для_листов фото1.jpg фото2.jpg ...
Печатает JSON: ["путь_к_листу", ...]. Номера кадров — с 1, в порядке аргументов.
"""
import json
import os
import sys

PER_SHEET = 10
COLS = 5
CELL = 360


def build(out_dir, paths, per=PER_SHEET):
    from PIL import Image, ImageDraw, ImageFont, ImageOps
    os.makedirs(out_dir, exist_ok=True)
    font = ImageFont.load_default(size=30)
    sheets = []
    for start in range(0, len(paths), per):
        part = paths[start:start + per]
        rows = (len(part) + COLS - 1) // COLS
        sheet = Image.new("RGB", (COLS * CELL, rows * CELL), (255, 255, 255))
        draw = ImageDraw.Draw(sheet)
        for i, path in enumerate(part):
            x, y = (i % COLS) * CELL, (i // COLS) * CELL
            try:
                im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
            except OSError:  # битое фото: пустая клетка с номером, остальные не теряем
                im = Image.new("RGB", (CELL, CELL), (200, 200, 200))
            im.thumbnail((CELL - 8, CELL - 8))
            sheet.paste(im, (x + (CELL - im.width) // 2, y + (CELL - im.height) // 2))
            draw.rectangle((x, y, x + 62, y + 42), fill=(255, 225, 0))
            draw.text((x + 6, y + 4), str(start + i + 1), fill=(0, 0, 0), font=font)
        dst = os.path.join(out_dir, f"sheet_{start // per + 1:02d}.jpg")
        sheet.save(dst, "JPEG", quality=85)
        sheets.append(dst)
    return sheets


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit("sheets.py папка фото [фото ...]")
    print(json.dumps(build(sys.argv[1], sys.argv[2:]), ensure_ascii=False))
