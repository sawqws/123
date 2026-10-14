"""Рисует аватар и баннер бота: python3 tgbot/art/make.py (нужен Pillow; для баннера — шрифт Inter Display).
Стиль «iPhone + пастель + люкс»: аватар — общий генератор common/avatar.py (палитра Главного: жемчуг и графит),
баннер — тёплый жемчужный фон, тёмный текст, тонкие звёзды ✦."""
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
FONT = "/usr/share/fonts/opentype/inter/InterDisplay-{}.otf"


def common_avatar():
    import importlib.util
    path = os.path.join(REPO, "common", "avatar.py")
    spec = importlib.util.spec_from_file_location("common_avatar", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def avatar(path):
    common_avatar().render("hub", pil=True).save(path, quality=95)


def banner(path, w=1280, h=640):
    av = common_avatar()
    bg1, bg2, ink, _ = av.palette("hub")
    lav = av.rgb("#e4dcf3")  # лёгкая лаванда справа — чтобы жемчуг не был серым
    small = Image.new("RGB", (64, 32))
    px = []
    for y in range(32):
        for x in range(64):
            t = x / 63 * 0.7 + y / 31 * 0.3
            base = av.mix(bg1, bg2, t * 0.55)
            g = max(0.0, 1 - ((x / 63 - 0.85) ** 2 + (y / 31 - 0.45) ** 2) ** 0.5 * 2.2)
            px.append(av.mix(base, lav, g * 0.8))
    small.putdata(px)
    img = small.resize((w, h), Image.BICUBIC).filter(ImageFilter.GaussianBlur(12)).convert("RGBA")

    # звёзды: большая контуром, две маленькие заливкой — тот же знак, что на аватаре
    k = 3
    mask = Image.new("L", (w * k, h * k), 0)
    d = ImageDraw.Draw(mask)
    big = [(x * k, y * k) for x, y in av.star(1010, 300, 150, 0.16)]
    big = big[9:] + big[:10]  # шов контура — на вогнутой стороне, не на острие
    d.line(big, fill=255, width=9 * k, joint="curve")
    d.polygon([(x * k, y * k) for x, y in av.star(1158, 150, 34, 0.17)], fill=255)
    d.polygon([(x * k, y * k) for x, y in av.star(872, 468, 20, 0.17)], fill=255)
    mask = mask.resize((w, h), Image.LANCZOS)
    shadow = mask.filter(ImageFilter.GaussianBlur(10)).point(lambda v: v * 0.14)
    img.alpha_composite(Image.merge("RGBA", [Image.new("L", (w, h), v) for v in ink] + [shadow.transform(
        (w, h), Image.AFFINE, (1, 0, 0, 0, 1, -8))]))
    img.alpha_composite(Image.merge("RGBA", [Image.new("L", (w, h), v) for v in ink] + [mask]))

    text = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(text)
    soft = (*ink, 150)
    d.text((88, 150), "АГЕНТ", font=ImageFont.truetype(FONT.format("Medium"), 26), fill=soft)
    d.text((84, 190), "Твой Telegram", font=ImageFont.truetype(FONT.format("SemiBold"), 88), fill=(*ink, 255))
    d.text((84, 290), "на автопилоте", font=ImageFont.truetype(FONT.format("Light"), 88), fill=(*ink, 230))
    f = ImageFont.truetype(FONT.format("Medium"), 25)
    x = 88
    for chip in ("чаты", "группы", "папки", "расписание", "психолог"):
        tw = d.textlength(chip, font=f)
        d.rounded_rectangle((x, 450, x + tw + 40, 498), radius=24, fill=(255, 255, 255, 170),
                            outline=(*ink, 28), width=2)
        d.text((x + 20, 459), chip, font=f, fill=(*ink, 220))
        x += tw + 54
    img.alpha_composite(text)
    img.convert("RGB").save(path, quality=92)


if __name__ == "__main__":
    avatar(os.path.join(HERE, "avatar.jpg"))
    banner(os.path.join(HERE, "banner.jpg"))
