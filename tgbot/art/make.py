"""Рисует аватар и баннер бота: python3 tgbot/art/make.py (нужны Pillow, numpy и шрифт Inter Display)."""
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
FONT = "/usr/share/fonts/opentype/inter/InterDisplay-{}.otf"
BG = (9, 11, 22)
GLOWS = [((124, 92, 255), 0.22, 0.30, 0.55), ((34, 211, 238), 0.85, 0.75, 0.50), ((236, 72, 153), 0.80, 0.10, 0.35)]


def aurora(w, h, glows=GLOWS, scale=1.0):
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    img = np.zeros((h, w, 3), np.float32) + BG
    for color, cx, cy, r in glows:
        d = ((x - cx * w) ** 2 + (y - cy * h) ** 2) / (r * max(w, h) * scale) ** 2
        a = np.exp(-d * 2.2)[..., None] * 0.95
        img = img * (1 - a) + np.array(color, np.float32) * a
    noise = np.random.default_rng(7).normal(0, 2.2, (h, w, 1))  # лёгкое зерно, чтобы не было полос
    return Image.fromarray(np.clip(img + noise, 0, 255).astype(np.uint8))


def sparkle(draw, cx, cy, r, fill, waist=0.16):
    """Четырёхлучевая звезда ✦ с вогнутыми сторонами."""
    pts = []
    for i in range(64):
        t = i / 64 * 2 * np.pi
        # астроида: |x|^(2/3) + |y|^(2/3) = r^(2/3), чуть «потолще» за счёт waist
        x = np.sign(np.cos(t)) * abs(np.cos(t)) ** 3
        y = np.sign(np.sin(t)) * abs(np.sin(t)) ** 3
        x = x * (1 - waist) + np.cos(t) * waist
        y = y * (1 - waist) + np.sin(t) * waist
        pts.append((cx + x * r, cy + y * r))
    draw.polygon(pts, fill=fill)


def glow_layer(size, painter, blur):
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    painter(ImageDraw.Draw(layer))
    return layer.filter(ImageFilter.GaussianBlur(blur))


def avatar(path, s=1024):
    img = aurora(s, s, [((124, 92, 255), 0.25, 0.20, 0.60), ((34, 211, 238), 0.90, 0.95, 0.55),
                        ((236, 72, 153), 0.95, 0.15, 0.30)]).convert("RGBA")
    c = s / 2
    ring = lambda d, w, a: d.ellipse((c - s * .33, c - s * .33, c + s * .33, c + s * .33), outline=(255, 255, 255, a), width=w)
    img.alpha_composite(glow_layer(img.size, lambda d: ring(d, 18, 120), 18))
    img.alpha_composite(glow_layer(img.size, lambda d: ring(d, 5, 150), 0))
    img.alpha_composite(glow_layer(img.size, lambda d: sparkle(d, c, c, s * .24, (255, 255, 255, 200)), 28))
    img.alpha_composite(glow_layer(img.size, lambda d: sparkle(d, c, c, s * .22, (255, 255, 255, 255)), 0))
    # маленькая «спутник»-звезда на орбите
    ox, oy = c + s * .33 * np.cos(-0.8), c + s * .33 * np.sin(-0.8)
    img.alpha_composite(glow_layer(img.size, lambda d: sparkle(d, ox, oy, s * .06, (255, 255, 255, 255)), 0))
    img.alpha_composite(glow_layer(img.size, lambda d: sparkle(d, ox, oy, s * .08, (190, 230, 255, 160)), 10))
    img.convert("RGB").resize((640, 640), Image.LANCZOS).save(path, quality=95)


def banner(path, w=1280, h=640):
    img = aurora(w, h).convert("RGBA")
    img.alpha_composite(glow_layer(img.size, lambda d: sparkle(d, 1010, 300, 150, (255, 255, 255, 170)), 30))
    img.alpha_composite(glow_layer(img.size, lambda d: sparkle(d, 1010, 300, 135, (255, 255, 255, 255)), 0))
    img.alpha_composite(glow_layer(img.size, lambda d: sparkle(d, 1150, 150, 36, (255, 255, 255, 230)), 0))
    img.alpha_composite(glow_layer(img.size, lambda d: sparkle(d, 880, 470, 22, (255, 255, 255, 200)), 0))
    text = Image.new("RGBA", img.size, (0, 0, 0, 0))  # полупрозрачное рисуем отдельным слоем и смешиваем
    d = ImageDraw.Draw(text)
    d.text((88, 150), "АГЕНТ", font=ImageFont.truetype(FONT.format("SemiBold"), 30), fill=(255, 255, 255, 150))
    d.text((84, 190), "Твой Telegram", font=ImageFont.truetype(FONT.format("Bold"), 92), fill="white")
    d.text((84, 292), "на автопилоте", font=ImageFont.truetype(FONT.format("Bold"), 92), fill=(255, 255, 255, 215))
    f = ImageFont.truetype(FONT.format("Medium"), 26)
    x = 88
    for chip in ("чаты", "группы", "папки", "расписание", "психолог"):
        tw = d.textlength(chip, font=f)
        d.rounded_rectangle((x, 450, x + tw + 40, 500), radius=25, fill=(255, 255, 255, 30), outline=(255, 255, 255, 70), width=2)
        d.text((x + 20, 459), chip, font=f, fill=(255, 255, 255, 235))
        x += tw + 56
    img.alpha_composite(text)
    img.convert("RGB").save(path, quality=92)


if __name__ == "__main__":
    avatar(os.path.join(HERE, "avatar.jpg"))
    banner(os.path.join(HERE, "banner.jpg"))
