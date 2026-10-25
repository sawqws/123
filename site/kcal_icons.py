#!/usr/bin/env python3
"""Иконки сайта «Калории» (docs/kcal/): кольцо из трёх долей — белки, жиры, углеводы.
usage: python3 site/kcal_icons.py
"""
import os

from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "kcal")


def icon(size):
    s = 1024
    img = Image.new("RGB", (s, s), (15, 24, 40))
    d = ImageDraw.Draw(img)
    box, w = [232, 232, 792, 792], 92
    d.arc(box, 0, 360, fill=(31, 44, 66), width=w)
    start = -90
    for share, color in ((0.18, (90, 169, 255)), (0.28, (246, 180, 69)), (0.36, (62, 214, 162))):
        end = start + 360 * share
        d.arc(box, start + 4, end - 4, fill=color, width=w)
        start = end
    d.ellipse([462, 462, 562, 562], fill=(255, 90, 95))
    return img.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    for n in (180, 192, 512):
        icon(n).save(os.path.join(OUT, f"icon-{n}.png"))
    print("ok", OUT)
