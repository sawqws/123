"""Значок Формы для экрана «Домой» (как у WHOOP: тёмный фон, кольца сна, восстановления и нагрузки).
usage: python3 coach/make_icons.py  → coach/webapp/icon-180.png, icon-192.png, icon-512.png
"""
import os

from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
S = 2048  # рисуем крупно, потом уменьшаем — края гладкие


def icon():
    bg = Image.new("RGB", (S, S))
    top, bot = (40, 51, 57), (16, 21, 24)  # #283339 → #101518
    d = ImageDraw.Draw(bg)
    for y in range(S):
        t = y / (S - 1)
        d.line([(0, y), (S, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(top, bot)))
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([S * .22, S * .22, S * .78, S * .78], fill=(22, 236, 6, 28))
    bg.paste(glow.filter(ImageFilter.GaussianBlur(S * .14)), (0, 0), glow.filter(ImageFilter.GaussianBlur(S * .14)))
    d = ImageDraw.Draw(bg)
    c = S / 2
    rings = [  # радиус, толщина, цвет, доля круга
        (.335, .062, (123, 161, 187), .86),  # сон
        (.245, .062, (22, 236, 6), .78),    # восстановление
        (.155, .062, (0, 147, 231), .62),   # нагрузка
    ]
    for r, w, col, part in rings:
        R, W = S * r, S * w
        box = [c - R, c - R, c + R, c + R]
        d.arc(box, 0, 360, fill=tuple(int(v * .22 + 20) for v in col), width=int(W))
        d.arc(box, -90, -90 + 360 * part, fill=col, width=int(W))
        for a in (-90, -90 + 360 * part):  # круглые концы
            import math
            x, y = c + (R - W / 2) * math.cos(math.radians(a)), c + (R - W / 2) * math.sin(math.radians(a))
            d.ellipse([x - W / 2, y - W / 2, x + W / 2, y + W / 2], fill=col)
    return bg


def main():
    img = icon()
    out = os.path.join(HERE, "webapp")
    for n in (180, 192, 512):
        img.resize((n, n), Image.LANCZOS).save(os.path.join(out, f"icon-{n}.png"), optimize=True)


if __name__ == "__main__":
    main()
