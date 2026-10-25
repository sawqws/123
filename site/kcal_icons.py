#!/usr/bin/env python3
"""Иконки сайта «Калории» (docs/kcal/): чёрный фон, тонкое белое кольцо, почти замкнутое — как на главном экране.
usage: python3 site/kcal_icons.py
"""
import math
import os

from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "kcal")


def icon(size):
    s = 2048  # рисуем крупно и уменьшаем — ровные края
    img = Image.new("RGB", (s, s), (0, 0, 0))
    d = ImageDraw.Draw(img)
    box, w = [524, 524, 1524, 1524], 112
    d.arc(box, 0, 360, fill=(38, 38, 40), width=w)
    d.arc(box, -90, 200, fill=(255, 255, 255), width=w)
    for ang in (-90, 200):  # скруглённые концы дуги
        r = (box[2] - box[0]) / 2 - w / 2
        cx = s / 2 + r * math.cos(math.radians(ang))
        cy = s / 2 + r * math.sin(math.radians(ang))
        d.ellipse([cx - w / 2, cy - w / 2, cx + w / 2, cy + w / 2], fill=(255, 255, 255))
    return img.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    for n in (180, 192, 512):
        icon(n).save(os.path.join(OUT, f"icon-{n}.png"))
    print("ok", OUT)
