"""Рисует аватар и баннер бота: python3 tgbot/art/make.py (нужен Pillow; для баннера — Inter и Liberation Serif).
Стиль «Оникс-стекло»: аватар — общий генератор common/avatar.py (палитра Главного: оникс и золото),
баннер — почти чёрный фон с тёплым свечением, золотая звезда ✦ на стекле, заголовок с засечками."""
import os

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
INTER = "/usr/share/fonts/opentype/inter/Inter-{}.otf"
SERIF = "/usr/share/fonts/truetype/liberation/LiberationSerif-{}.ttf"
CREAM = (246, 241, 231)


def common_avatar():
    import importlib.util
    path = os.path.join(REPO, "common", "avatar.py")
    spec = importlib.util.spec_from_file_location("common_avatar", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def avatar(path):
    common_avatar().render("hub", pil=True).save(path, quality=95)


def font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.truetype(INTER.format("Regular"), size)


def banner(path, w=1280, h=640):
    av = common_avatar()
    glow, ember, _ = av.palette("hub")
    small = Image.new("RGB", (64, 32))
    px = []
    for y in range(32):
        for x in range(64):
            c = list(av.ONYX)
            for col, cx, cy, r, k in ((glow, 0.8, 0.2, 0.55, 0.75), (ember, 0.05, 1.0, 0.5, 0.5)):
                d = (((x / 63 - cx) * 2) ** 2 + (y / 31 - cy) ** 2) ** 0.5 / (r * 2)
                a = max(0.0, 1 - d) ** 2.2 * k
                c = [c[i] + (col[i] - c[i]) * a for i in range(3)]
            px.append(tuple(int(v) for v in c))
    small.putdata(px)
    img = small.resize((w, h), Image.BICUBIC).filter(ImageFilter.GaussianBlur(14))
    noise = Image.effect_noise((w, h), 2.0).convert("L")
    img = ImageChops.add(img, Image.merge("RGB", [noise] * 3), 1.0, -128).convert("RGBA")

    # стеклянный диск под звездой: еле заметная заливка, серп блика и кромка (белая сверху, золотая снизу)
    k, cx, cy, r = 3, 1010, 320, 228
    disc = Image.new("L", (w * k, h * k), 0)
    ImageDraw.Draw(disc).ellipse(((cx - r) * k, (cy - r) * k, (cx + r) * k, (cy + r) * k), fill=255)
    rim = Image.new("L", (w * k, h * k), 0)
    ImageDraw.Draw(rim).ellipse(((cx - r) * k, (cy - r) * k, (cx + r) * k, (cy + r) * k), outline=255, width=2 * k)
    disc, rim = disc.resize((w, h), Image.LANCZOS), rim.resize((w, h), Image.LANCZOS)

    def vgrad(top, bottom, y0, y1):
        col = Image.new("L", (1, h))
        col.putdata([int(top + (bottom - top) * min(1, max(0, (y / h - y0) / (y1 - y0)))) for y in range(h)])
        return col.resize((w, h))
    white = Image.new("L", (w, h), 255)
    img.alpha_composite(Image.merge("RGBA", [white] * 3 + [ImageChops.multiply(disc, vgrad(26, 5, 0.15, 0.6))]))
    img.alpha_composite(Image.merge("RGBA", [white] * 3 + [ImageChops.multiply(rim, vgrad(150, 60, 0.15, 0.85))]))

    # звёзды: большая контуром, две маленькие заливкой — тот же знак, что на аватаре; золото сверху светлее
    mask = Image.new("L", (w * k, h * k), 0)
    d = ImageDraw.Draw(mask)
    big = [(x * k, y * k) for x, y in av.star(cx, cy, 140, 0.16)]
    big = big[9:] + big[:10]  # шов контура — на вогнутой стороне, не на острие
    d.line(big, fill=255, width=9 * k, joint="curve")
    d.polygon([(x * k, y * k) for x, y in av.star(1150, 150, 30, 0.17)], fill=255)
    d.polygon([(x * k, y * k) for x, y in av.star(866, 486, 18, 0.17)], fill=255)
    mask = mask.resize((w, h), Image.LANCZOS)
    black = Image.new("L", (w, h), 0)
    img.alpha_composite(Image.merge("RGBA", [black] * 3 + [ImageChops.offset(mask, 0, 8).filter(
        ImageFilter.GaussianBlur(10)).point(lambda v: int(v * 0.6))]))
    img.alpha_composite(Image.merge("RGBA", [Image.new("L", (w, h), v) for v in av.GOLD] + [
        mask.filter(ImageFilter.GaussianBlur(16)).point(lambda v: int(v * 0.32))]))
    gold = Image.new("RGB", (1, h))
    gold.putdata([av.gold_at((y - 120) / 400) for y in range(h)])
    img.alpha_composite(Image.merge("RGBA", list(gold.resize((w, h)).split()) + [mask]))
    edge = ImageChops.subtract(mask, ImageChops.offset(mask, 0, 3)).point(lambda v: int(v * 0.7))
    img.alpha_composite(Image.merge("RGBA", [white] * 3 + [edge]))

    text = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(text)
    f = font(INTER.format("Medium"), 22)
    x = 90
    for ch in "АГЕНТ":  # разрядка, как подписи разделов в приложении
        d.text((x, 150), ch, font=f, fill=(*av.GOLD, 230))
        x += d.textlength(ch, font=f) + 7
    d.text((84, 186), "Твой Telegram", font=font(SERIF.format("Regular"), 96), fill=(*CREAM, 255))
    d.text((86, 292), "на автопилоте", font=font(SERIF.format("Italic"), 96), fill=(*av.GOLD_HI, 240))
    f = font(INTER.format("Medium"), 24)
    x = 88
    for chip in ("чаты", "группы", "папки", "расписание", "психолог"):
        tw = d.textlength(chip, font=f)
        d.rounded_rectangle((x, 452, x + tw + 40, 500), radius=24, fill=(255, 255, 255, 22),
                            outline=(255, 255, 255, 52), width=2)
        d.text((x + 20, 462), chip, font=f, fill=(*CREAM, 225))
        x += tw + 54
    img.alpha_composite(text)
    img.convert("RGB").save(path, quality=92)


if __name__ == "__main__":
    avatar(os.path.join(HERE, "avatar.jpg"))
    banner(os.path.join(HERE, "banner.jpg"))
