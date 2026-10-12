"""Аватар бота: python3 avatar.py out.png <буква> <#цвет1> <#цвет2>
Градиент двух цветов по диагонали, мягкий блик и крупная белая буква. С Pillow — с буквой (шрифт Inter/DejaVu);
без Pillow — только градиент с кольцом (стандартная библиотека). 640×640, Telegram сам скруглит."""
import math
import os
import struct
import sys
import zlib

SIZE = 640
FONTS = ["/usr/share/fonts/opentype/inter/InterDisplay-Bold.otf", "/usr/share/fonts/opentype/inter/Inter-Bold.otf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"]


def rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def color_at(x, y, c1, c2):
    t = (x + y) / (2 * SIZE)
    base = [c1[i] + (c2[i] - c1[i]) * t for i in range(3)]
    d = math.hypot(x - SIZE * 0.28, y - SIZE * 0.22) / SIZE  # блик слева сверху
    glow = max(0.0, 1 - d * 1.8) ** 2 * 0.35
    return [min(255, v + (255 - v) * glow) for v in base]


def with_pil(out, letter, c1, c2):
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    small = Image.new("RGB", (64, 64))
    small.putdata([tuple(int(v) for v in color_at(x * 10, y * 10, c1, c2)) for y in range(64) for x in range(64)])
    img = small.resize((SIZE, SIZE), Image.BICUBIC).filter(ImageFilter.GaussianBlur(4))
    font = next((ImageFont.truetype(f, 330) for f in FONTS if os.path.exists(f)), None)
    if font and letter.strip():
        shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(shadow).text((SIZE / 2, SIZE / 2 + 10), letter, font=font, anchor="mm", fill=(0, 0, 0, 70))
        img = Image.alpha_composite(img.convert("RGBA"), shadow.filter(ImageFilter.GaussianBlur(14)))
        ImageDraw.Draw(img).text((SIZE / 2, SIZE / 2), letter, font=font, anchor="mm", fill=(255, 255, 255, 255))
    else:
        ring(ImageDraw.Draw(img))
    img.convert("RGB").save(out, "PNG")


def ring(draw):
    r = SIZE * 0.22
    draw.ellipse((SIZE / 2 - r, SIZE / 2 - r, SIZE / 2 + r, SIZE / 2 + r), outline=(255, 255, 255), width=36)


def plain(out, c1, c2):
    """Без Pillow: градиент и белое кольцо, PNG вручную."""
    rows = []
    r_out, r_in = SIZE * 0.22 + 18, SIZE * 0.22 - 18
    for y in range(SIZE):
        row = bytearray(b"\x00")
        for x in range(SIZE):
            d = math.hypot(x - SIZE / 2, y - SIZE / 2)
            px = [255, 255, 255] if r_in <= d <= r_out else color_at(x, y, c1, c2)
            row += bytes(int(v) for v in px)
        rows.append(bytes(row))
    raw = zlib.compress(b"".join(rows), 9)

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    with open(out, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", SIZE, SIZE, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", raw) + chunk(b"IEND", b""))


def main():
    out, letter = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "")[:1].upper()
    c1 = rgb(sys.argv[3]) if len(sys.argv) > 3 else (124, 92, 255)
    c2 = rgb(sys.argv[4]) if len(sys.argv) > 4 else (34, 211, 238)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    try:
        with_pil(out, letter, c1, c2)
    except ImportError:
        plain(out, c1, c2)


if __name__ == "__main__":
    main()
