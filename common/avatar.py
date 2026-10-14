"""Аватары ботов в стиле «iPhone + пастель + люкс»: мягкий пастельный градиент и тонкий знак по центру.

  python3 common/avatar.py preview out.png          лист со всеми аватарами (крупно и как в списке чатов)
  python3 common/avatar.py make <бот> out.png       один аватар (боты — BOTS ниже, `list` — список)
  python3 common/avatar.py custom out.png <буква> [#цвет1 #цвет2]   для ботов Студии: монограмма, цвета смягчаются
  python3 common/avatar.py apply [бот ...] [--dry-run]   поставить аватары ботам на этом сервере (токены из /etc/*.env)

Знаки рисуются векторно (никаких эмодзи и шрифтов), поэтому на сервере выглядят так же, как здесь. 640×640 —
Telegram сам скруглит в круг, знак держится в центральной половине. С Pillow — сглаживание 3×, мягкая тень и
светлый «тиснёный» край; без Pillow — тот же рисунок своим растеризатором (стандартная библиотека, медленнее)."""
import importlib.util
import json
import math
import os
import random
import struct
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
import zlib

SIZE = 640
SCALE = 0.235         # единица знака = четверть стороны: знак радиусом 1 занимает центральную половину
STROKE = 0.08         # толщина линии в единицах знака (≈12 px на 640)
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

# Палитры: bg — две пастели (светлая сверху слева → глубже снизу справа), ink — цвет знака (глубокий тон того же цвета).
BOTS = {
    "hdp": dict(title="HDP · задания", bg=("#eceaff", "#c9c8f2"), ink="#4a4aa0", mark="book",
                env=("/etc/horo-bot.env", "TELEGRAM_TOKEN")),
    "planner": dict(title="Ритм · планер", bg=("#fff1e8", "#f9c9ae"), ink="#b0583a", mark="sunrise",
                    env=("/etc/planner-bot.env", "PLANNER_TOKEN")),
    "coach": dict(title="Форма · тренер", bg=("#eef3ea", "#c3d5bd"), ink="#4c6b4d", mark="pulse",
                  env=("/etc/coach-bot.env", "COACH_TOKEN")),
    "kpp": dict(title="КПП · пропуск", bg=("#fff6dc", "#f3d891"), ink="#93660f", mark="ticket",
                env=("/etc/kpp-bot.env", "KPP_TOKEN")),
    "avito": dict(title="Авито · продажи", bg=("#e6f7ef", "#b5e0cd"), ink="#2b7559", mark="tag",
                  env=("/etc/avito-bot.env", "AVITO_TOKEN")),
    "eye": dict(title="Глаз · очки", bg=("#f2edfc", "#d3c6f0"), ink="#64499f", mark="eye",
                env=("/etc/eye-bot.env", "EYE_TOKEN")),
    "hub": dict(title="Главный · Секретарь", bg=("#f7f6f3", "#d6d3cd"), ink="#43403c", mark="sparkle",
                env=("/etc/hub-bot.env", "HUB_TOKEN")),
    "agent": dict(title="Секретарь (свой бот)", like="hub", env=("/etc/tg-agent.env", "TG_AGENT_TOKEN")),
    "studio": dict(title="Студия", bg=("#f3e9fb", "#f2cde0"), ink="#7f4b86", mark="wand",
                   env=("/etc/studio-bot.env", "STUDIO_TOKEN")),
    "catcher": dict(title="Ловец · имена", bg=("#e9f2fc", "#bfd5ee"), ink="#3a5f8c", mark="at",
                    env=("/etc/catcher-bot.env", "CATCHER_TOKEN")),
    "sites": dict(title="Студия сайтов", bg=("#fff0f3", "#f5c8d4"), ink="#a84a68", mark="globe",
                  env=("/etc/studio-sites.env", "STUDIO_TOKEN")),
}
ALIASES = {"horo": "hdp", "bot": "hdp", "ritm": "planner", "форма": "coach", "trainer": "coach", "главный": "hub",
           "secretary": "hub", "tgbot": "agent", "studio-sites": "sites", "site": "sites"}

# Шрифты для монограмм ботов Студии (у именных ботов знаки векторные). Тонкие и с кириллицей — первыми.
FONTS = ["/usr/share/fonts/opentype/inter/InterDisplay-Light.otf", "/usr/share/fonts/opentype/inter/Inter-Light.otf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-ExtraLight.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
         "/usr/share/fonts/truetype/freefont/FreeSerif.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/freefont/FreeSans.ttf"]


# ---------- цвета ----------

def rgb(h):
    h = str(h).lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def mix(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def lum(c):
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(c[0]) + 0.7152 * ch(c[1]) + 0.0722 * ch(c[2])


def pastelize(c1, c2):
    """Любые два цвета (из плана Студии) → пастельный фон и глубокий цвет знака того же оттенка."""
    a, b = rgb(c1), rgb(c2)
    white, ink_dark = (255, 255, 255), (28, 24, 22)
    bg1, bg2 = mix(a, white, 0.86), mix(b, white, 0.66)
    ink = mix(a, ink_dark, 0.42)
    for _ in range(6):  # знак должен читаться: контраст к светлой части фона не меньше ~4.5
        if (lum(bg1) + 0.05) / (lum(ink) + 0.05) >= 4.5:
            break
        ink = mix(ink, ink_dark, 0.25)
    return bg1, bg2, ink


def palette(name):
    p = BOTS[name]
    if p.get("like"):
        p = {**BOTS[p["like"]], **{k: v for k, v in p.items() if k != "like"}}
    return rgb(p["bg"][0]), rgb(p["bg"][1]), rgb(p["ink"]), p["mark"]


# ---------- геометрия знаков (единицы: центр 0,0, радиус знака ≈ 1, y вниз) ----------

def arc(cx, cy, r, a0, a1, n=None, ry=None):
    ry = r if ry is None else ry
    n = n or max(8, int(abs(a1 - a0) / 4))
    return [(cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
             cy + ry * math.sin(math.radians(a0 + (a1 - a0) * i / n))) for i in range(n + 1)]


def bez(p0, p1, p2, p3, n=28):
    out = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        out.append((u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
                    u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1]))
    return out


def rot(pts, deg, cx=0.0, cy=0.0):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return [(cx + (x - cx) * c - (y - cy) * s, cy + (x - cx) * s + (y - cy) * c) for x, y in pts]


def rounded(pts, r, n=8):
    """Замкнутый многоугольник со скруглёнными углами (радиус r в единицах)."""
    out = []
    k = len(pts)
    for i in range(k):
        p0, p1, p2 = pts[i - 1], pts[i], pts[(i + 1) % k]
        v1 = (p0[0] - p1[0], p0[1] - p1[1])
        v2 = (p2[0] - p1[0], p2[1] - p1[1])
        l1, l2 = math.hypot(*v1), math.hypot(*v2)
        rr = min(r, l1 / 2, l2 / 2)
        a = (p1[0] + v1[0] / l1 * rr, p1[1] + v1[1] / l1 * rr)
        b = (p1[0] + v2[0] / l2 * rr, p1[1] + v2[1] / l2 * rr)
        for j in range(n + 1):  # квадратичная кривая через вершину — мягкий угол
            t = j / n
            out.append(((1 - t) ** 2 * a[0] + 2 * (1 - t) * t * p1[0] + t * t * b[0],
                        (1 - t) ** 2 * a[1] + 2 * (1 - t) * t * p1[1] + t * t * b[1]))
    return out + out[:1]


def star(cx, cy, r, waist=0.2, n=72):
    """Четырёхлучевая звезда ✦ с вогнутыми сторонами (заливка)."""
    pts = []
    for i in range(n):
        t = i / n * 2 * math.pi
        x = math.copysign(abs(math.cos(t)) ** 3, math.cos(t)) * (1 - waist) + math.cos(t) * waist
        y = math.copysign(abs(math.sin(t)) ** 3, math.sin(t)) * (1 - waist) + math.sin(t) * waist
        pts.append((cx + x * r, cy + y * r))
    return pts


def mark_shapes(mark):
    """Список фигур: ("line", точки) — линия, ("fill", точки) — заливка, ("dot", x, y, r) — точка."""
    L, F, D = "line", "fill", "dot"
    if mark == "book":  # раскрытая книга
        left = bez((0, -0.42), (-0.28, -0.66), (-0.66, -0.66), (-0.96, -0.54)) + [(-0.96, 0.5)] + \
            bez((-0.96, 0.5), (-0.66, 0.38), (-0.28, 0.4), (0, 0.62))
        right = [(-x, y) for x, y in left]
        lines = [[(-0.74, -0.30), (-0.24, -0.26)], [(-0.74, -0.06), (-0.24, -0.02)],
                 [(-0.74, 0.18), (-0.40, 0.20)]]
        return [(L, left), (L, right), (L, [(0, -0.42), (0, 0.62)])] + \
            [(L, [(a, b), (c, d)]) for (a, b), (c, d) in lines] + \
            [(L, [(-a, b), (-c, d)]) for (a, b), (c, d) in lines[:2]]
    if mark == "sunrise":  # солнце над горизонтом
        y0 = 0.28
        rays = [(L, [(0.66 * math.cos(math.radians(a)), y0 + 0.66 * math.sin(math.radians(a))),
                     (0.9 * math.cos(math.radians(a)), y0 + 0.9 * math.sin(math.radians(a)))])
                for a in (198, 234, 270, 306, 342)]
        return [(L, arc(0, y0, 0.46, 180, 360)), (L, [(-1.0, y0), (1.0, y0)]),
                (L, [(-0.56, y0 + 0.26), (0.56, y0 + 0.26)]), (L, [(-0.24, y0 + 0.5), (0.24, y0 + 0.5)])] + rays
    if mark == "pulse":  # кольцо активности и пульс
        return [(L, arc(0, 0, 0.96, -235, 55)),
                (L, [(-0.72, 0.06), (-0.36, 0.06), (-0.2, -0.3), (0.0, 0.46), (0.2, -0.56), (0.36, 0.06),
                     (0.72, 0.06)]),
                (D, 0.96 * math.cos(math.radians(55)), 0.96 * math.sin(math.radians(55)), 0.075)]
    if mark == "ticket":  # пропуск-билет с перфорацией
        w, h, r, nr = 0.98, 0.6, 0.14, 0.16
        path = arc(-w + r, -h + r, r, 180, 270, 10) + arc(w - r, -h + r, r, 270, 360, 10) + \
            arc(w, 0, nr, -90, -270, 18) + arc(w - r, h - r, r, 0, 90, 10) + arc(-w + r, h - r, r, 90, 180, 10) + \
            arc(-w, 0, nr, 90, -90, 18)
        path = path + path[:1]
        dash = [(L, [(0.34, -h + 0.18 + i * 0.25), (0.34, -h + 0.28 + i * 0.25)]) for i in range(4)]
        star_pts = star(-0.3, 0, 0.26, 0.18)
        shapes = [(L, path)] + dash + [(F, star_pts)]
        return [(k, rot(p, -14)) if k != D else (k,) + p for k, p in shapes]
    if mark == "tag":  # ценник
        body = rounded([(0, -1.0), (0.5, -0.5), (0.5, 0.92), (-0.5, 0.92), (-0.5, -0.5)], 0.14)
        shapes = [(L, body), (L, arc(0, -0.46, 0.13, 0, 360, 36)),
                  (L, [(-0.24, 0.2), (0.24, 0.2)]), (L, [(-0.24, 0.48), (0.1, 0.48)])]
        return [(k, [(x * 0.92, y * 0.92 + 0.02) for x, y in rot(p, 40)]) for k, p in shapes]
    if mark == "eye":  # глаз
        up = bez((-1.0, 0), (-0.52, -0.66), (0.52, -0.66), (1.0, 0))
        down = bez((1.0, 0), (0.52, 0.66), (-0.52, 0.66), (-1.0, 0))
        return [(L, up + down[1:]), (L, arc(0, 0, 0.34, 0, 360, 48)), (D, 0, 0, 0.13)]
    if mark == "sparkle":  # звезда ✦ и спутник
        big = star(-0.08, 0.08, 0.9, 0.16)
        big = big[9:] + big[:10]  # шов контура — на вогнутой стороне, не на острие
        return [(L, big), (F, star(0.66, -0.64, 0.24, 0.17)), (D, 0.72, 0.6, 0.065)]
    if mark == "wand":  # волшебная палочка
        return [(L, [(-0.78, 0.78), (0.12, -0.12)]), (L, [(0.02, -0.02), (0.18, -0.18)]),
                (F, star(0.46, -0.46, 0.42, 0.17)), (F, star(0.86, 0.12, 0.15, 0.2)),
                (F, star(-0.12, -0.84, 0.13, 0.2)), (D, 0.86, -0.86, 0.06)]
    if mark == "at":  # @ — юзернеймы
        stem = [(0.34, -0.34), (0.34, 0.14)]
        tail = bez((0.34, 0.14), (0.34, 0.44), (0.88, 0.44), (0.88, 0.0), 24)
        outer = arc(0, 0, 0.88, 0, -300, 90)
        return [(L, arc(0, 0, 0.34, 0, 360, 48)), (L, stem), (L, tail + outer[1:])]
    if mark == "globe":  # глобус — сайты
        r = 0.92
        lat = math.sqrt(r * r - 0.44 ** 2)
        return [(L, arc(0, 0, r, 0, 360, 96)), (L, arc(0, 0, 0.4, 0, 360, 72, ry=r)),
                (L, [(-r, 0), (r, 0)]), (L, [(-lat, -0.44), (lat, -0.44)]), (L, [(-lat, 0.44), (lat, 0.44)])]
    raise ValueError("нет знака " + mark)


# ---------- рисование с Pillow ----------

def _pil():
    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageFont  # noqa: F401
        return True
    except ImportError:
        return False


def _background(c1, c2):
    from PIL import Image, ImageFilter
    n = 48
    small = Image.new("RGB", (n, n))
    px = []
    for y in range(n):
        for x in range(n):
            t = (x * 0.55 + y * 0.45) / (n - 1)          # диагональ, чуть круче вниз
            t = t * t * (3 - 2 * t)                         # мягкие края перехода
            base = [c1[i] + (c2[i] - c1[i]) * t for i in range(3)]
            d = math.hypot(x / n - 0.26, y / n - 0.18)      # жемчужный блик слева сверху
            g = max(0.0, 1 - d * 1.7) ** 2 * 0.55
            px.append(tuple(int(v + (255 - v) * g) for v in base))
    small.putdata(px)
    img = small.resize((SIZE, SIZE), Image.BICUBIC).filter(ImageFilter.GaussianBlur(10))
    from PIL import ImageChops
    noise = Image.effect_noise((SIZE, SIZE), 2.2).convert("L")  # зерно ±2 против полос градиента
    grain = Image.merge("RGB", [noise] * 3)
    return ImageChops.add(img, grain, 1.0, -128)


def _mask_pil(shapes, ss=3, stroke=STROKE, offset=(0.0, 0.0)):
    """Маска знака: рисуем в ss× и уменьшаем — тонкие линии гладкие."""
    from PIL import Image, ImageDraw
    big = SIZE * ss
    m = Image.new("L", (big, big), 0)
    d = ImageDraw.Draw(m)
    k = SIZE * SCALE * ss
    cx, cy = big / 2 + offset[0] * ss, big / 2 + offset[1] * ss
    w = stroke * k
    for s in shapes:
        if s[0] == "dot":
            x, y, r = cx + s[1] * k, cy + s[2] * k, s[3] * k
            d.ellipse((x - r, y - r, x + r, y + r), fill=255)
            continue
        pts = [(cx + x * k, cy + y * k) for x, y in s[1]]
        if s[0] == "fill":
            d.polygon(pts, fill=255)
            continue
        d.line(pts, fill=255, width=max(1, round(w)), joint="curve")
        for x, y in (pts[0], pts[-1]) if len(pts) > 2 else pts:  # круглые концы
            d.ellipse((x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill=255)
    return m.resize((SIZE, SIZE), Image.LANCZOS)


def _letter_mask(letter, ss=3):
    """Монограмма шрифтом (только для ботов Студии). None — подходящего шрифта нет."""
    from PIL import Image, ImageDraw, ImageFont
    big = SIZE * ss
    for path in FONTS:
        if not os.path.exists(path):
            continue
        try:
            font = ImageFont.truetype(path, int(big * 0.42))
        except OSError:
            continue
        if not font.getmask(letter).getbbox():
            continue
        m = Image.new("L", (big, big), 0)
        d = ImageDraw.Draw(m)
        x0, y0, x1, y1 = d.textbbox((0, 0), letter, font=font)
        d.text(((big - (x1 - x0)) / 2 - x0, (big - (y1 - y0)) / 2 - y0), letter, font=font, fill=255)
        return m.resize((SIZE, SIZE), Image.LANCZOS)
    return None


def _compose_pil(c1, c2, ink, mask):
    from PIL import Image, ImageChops, ImageFilter
    img = _background(c1, c2).convert("RGBA")
    # мягкая тень знака (тем же цветом, очень прозрачно) и светлый край сверху — как тиснение на бумаге
    shadow = ImageChops.offset(mask, 0, 9).filter(ImageFilter.GaussianBlur(12)).point(lambda v: int(v * 0.16))
    img.alpha_composite(Image.merge("RGBA", [Image.new("L", mask.size, v) for v in ink] + [shadow]))
    light = ImageChops.offset(mask, 0, -3).point(lambda v: int(v * 0.55))
    img.alpha_composite(Image.merge("RGBA", [Image.new("L", mask.size, 255)] * 3 + [light]))
    img.alpha_composite(Image.merge("RGBA", [Image.new("L", mask.size, v) for v in ink] + [mask]))
    return img.convert("RGB")


# ---------- без Pillow: свой растеризатор ----------

class Raster:
    """Маска 640×640 (0..1) и PNG без сторонних библиотек: линии — по расстоянию до отрезка, заливки — сканлайном."""

    def __init__(self):
        self.a = [0.0] * (SIZE * SIZE)

    def _put(self, x, y, v):
        i = y * SIZE + x
        if v > self.a[i]:
            self.a[i] = v

    def segment(self, x0, y0, x1, y1, w):
        h = w / 2
        dx, dy = x1 - x0, y1 - y0
        ll = dx * dx + dy * dy or 1e-9
        for y in range(max(0, int(min(y0, y1) - h - 1)), min(SIZE, int(max(y0, y1) + h + 2))):
            py = y + 0.5
            for x in range(max(0, int(min(x0, x1) - h - 1)), min(SIZE, int(max(x0, x1) + h + 2))):
                px = x + 0.5
                t = ((px - x0) * dx + (py - y0) * dy) / ll
                t = 0.0 if t < 0 else 1.0 if t > 1 else t
                d = math.hypot(px - x0 - t * dx, py - y0 - t * dy)
                v = h - d + 0.5
                if v > 0:
                    self._put(x, y, 1.0 if v >= 1 else v)

    def dot(self, cx, cy, r):
        for y in range(max(0, int(cy - r - 1)), min(SIZE, int(cy + r + 2))):
            for x in range(max(0, int(cx - r - 1)), min(SIZE, int(cx + r + 2))):
                v = r - math.hypot(x + 0.5 - cx, y + 0.5 - cy) + 0.5
                if v > 0:
                    self._put(x, y, 1.0 if v >= 1 else v)

    def polygon(self, pts, sub=4):
        ys = [p[1] for p in pts]
        n = len(pts)
        acc = {}
        for y in range(max(0, int(min(ys))), min(SIZE, int(max(ys)) + 1)):
            row = [0.0] * SIZE
            for s in range(sub):
                sy = y + (s + 0.5) / sub
                xs = []
                for i in range(n):
                    (ax, ay), (bx, by) = pts[i], pts[(i + 1) % n]
                    if (ay <= sy < by) or (by <= sy < ay):
                        xs.append(ax + (sy - ay) * (bx - ax) / (by - ay))
                xs.sort()
                for a, b in zip(xs[::2], xs[1::2]):
                    ia, ib = max(0, int(a)), min(SIZE - 1, int(b))
                    if ia == ib:
                        row[ia] += (b - a) / sub
                        continue
                    row[ia] += (ia + 1 - a) / sub
                    for x in range(ia + 1, ib):
                        row[x] += 1 / sub
                    if ib < SIZE:
                        row[ib] += (b - ib) / sub
            acc[y] = row
        for y, row in acc.items():
            for x, v in enumerate(row):
                if v > 0:
                    self._put(x, y, min(1.0, v))


def _plain(c1, c2, ink, shapes):
    r = Raster()
    k = SIZE * SCALE
    c = SIZE / 2
    w = STROKE * k
    for s in shapes:
        if s[0] == "dot":
            r.dot(c + s[1] * k, c + s[2] * k, s[3] * k)
        elif s[0] == "fill":
            r.polygon([(c + x * k, c + y * k) for x, y in s[1]])
        else:
            pts = [(c + x * k, c + y * k) for x, y in s[1]]
            for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
                r.segment(x0, y0, x1, y1, w)
    rows = []
    rnd = random.Random(7)
    for y in range(SIZE):
        row = bytearray(b"\x00")
        for x in range(SIZE):
            t = (x * 0.55 + y * 0.45) / (SIZE - 1)
            t = t * t * (3 - 2 * t)
            g = max(0.0, 1 - math.hypot(x / SIZE - 0.26, y / SIZE - 0.18) * 1.7) ** 2 * 0.55
            a = r.a[y * SIZE + x]
            n = rnd.uniform(-1.2, 1.2)
            for i in range(3):
                v = c1[i] + (c2[i] - c1[i]) * t
                v = v + (255 - v) * g + n
                row.append(max(0, min(255, int(v + (ink[i] - v) * a))))
        rows.append(bytes(row))
    return _png(b"".join(rows))


def _png(raw):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", SIZE, SIZE, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


# ---------- публичные функции ----------

def _save(img_or_bytes, out):
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    if isinstance(img_or_bytes, bytes):
        with open(out, "wb") as f:
            f.write(img_or_bytes)
    else:
        img_or_bytes.save(out, "PNG", optimize=True)
    return out


def render(name, pil=None):
    """PIL.Image (если есть Pillow) или байты PNG — аватар бота по имени из BOTS."""
    c1, c2, ink, mark = palette(resolve(name))
    shapes = mark_shapes(mark)
    if pil if pil is not None else _pil():
        return _compose_pil(c1, c2, ink, _mask_pil(shapes))
    return _plain(c1, c2, ink, shapes)


def make(name, out, pil=None):
    return _save(render(name, pil), out)


def render_custom(letter="", c1="#7c5cff", c2="#22d3ee", pil=None):
    """Для ботов Студии: цвета плана смягчаются до пастели, по центру тонкая буква (нет шрифта — знак ✦)."""
    bg1, bg2, ink = pastelize(c1, c2)
    letter = (letter or "").strip()[:1].upper()
    if pil if pil is not None else _pil():
        mask = _letter_mask(letter) if letter else None
        if mask is None:
            mask = _mask_pil(mark_shapes("sparkle"))
        return _compose_pil(bg1, bg2, ink, mask)
    return _plain(bg1, bg2, ink, mark_shapes("sparkle"))


def custom(out, letter="", c1="#7c5cff", c2="#22d3ee", pil=None):
    return _save(render_custom(letter, c1, c2, pil), out)


def resolve(name):
    n = (name or "").strip().lower()
    n = ALIASES.get(n, n)
    if n not in BOTS:
        raise SystemExit(f"нет такого бота: {name}. Есть: {', '.join(BOTS)}")
    return n


def preview(out):
    """Лист: каждый аватар крупно (кругом, как в профиле) и маленьким, как в списке чатов."""
    from PIL import Image, ImageDraw, ImageFont
    items = [(BOTS[n]["title"], render(n, pil=True)) for n in BOTS if n != "agent"]
    items += [("Бот Студии: «Вода»", render_custom("В", "#0ea5e9", "#6366f1", pil=True)),
              ("Бот Студии: «Заметки»", render_custom("З", "#f97316", "#ec4899", pil=True))]
    cell, big, small, cols = 300, 236, 56, 4
    rows = (len(items) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell, rows * cell + 40), (246, 243, 238))
    d = ImageDraw.Draw(sheet)
    font = None
    for p in ("/usr/share/fonts/opentype/inter/Inter-Medium.otf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if os.path.exists(p):
            font = ImageFont.truetype(p, 17)
            break
    font = font or ImageFont.load_default()
    for i, (title, img) in enumerate(items):
        x, y = (i % cols) * cell, (i // cols) * cell + 20
        for size, (ox, oy) in ((big, ((cell - big) // 2 - 18, 6)), (small, (cell - small - 14, big - small + 6))):
            a = img.resize((size * 2, size * 2), Image.LANCZOS)
            m = Image.new("L", a.size, 0)
            ImageDraw.Draw(m).ellipse((0, 0, a.size[0] - 1, a.size[1] - 1), fill=255)
            a, m = a.resize((size, size), Image.LANCZOS), m.resize((size, size), Image.LANCZOS)
            sheet.paste(a, (x + ox, y + oy), m)
        tw = d.textlength(title, font=font)
        d.text((x + (cell - tw) / 2 - 18, y + big + 18), title, font=font, fill=(60, 55, 50))
    return _save(sheet, out)


# ---------- установка на сервере ----------

def read_env(path):
    env = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip().removeprefix("export ").strip()] = v.strip().strip("'\"")
    return env


def set_photo(token, png_path, timeout=60):
    """Bot API setMyProfilePhoto (InputProfilePhotoStatic). Возвращает (ok, текст ошибки без токена)."""
    boundary = uuid.uuid4().hex
    with open(png_path, "rb") as f:
        data = f.read()
    photo = json.dumps({"type": "static", "photo": "attach://avatar"})
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"\r\n\r\n{photo}\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"avatar\"; filename=\"avatar.png\"\r\n"
            f"Content-Type: image/png\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/setMyProfilePhoto", body,
                                 {"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            res = json.load(r)
        return bool(res.get("ok")), res.get("description", "")
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e).get("description") or str(e)
        except Exception:
            msg = str(e)
        return False, msg.replace(token, "***")
    except Exception as e:
        return False, repr(e).replace(token, "***")


def studio_bots():
    """Боты, которые сделала Студия: (slug, имя, цвета плана, папка, токен) — токен из <папка>/.env."""
    root = os.environ.get("STUDIO_BOTS_DIR") or "/root/bots"
    try:
        with open(os.path.join(root, "registry.json"), encoding="utf-8") as f:
            reg = json.load(f)
    except (OSError, ValueError):
        return []
    out = []
    for slug, info in sorted(reg.items()):
        folder = info.get("dir") or os.path.join(root, slug)
        try:
            token = read_env(os.path.join(folder, ".env")).get("BOT_TOKEN", "")
        except OSError:
            token = ""
        colors = ((info.get("plan") or {}).get("colors") or ["#7c5cff", "#22d3ee"])[:2]
        out.append((slug, info.get("name") or slug, colors, folder, token))
    return out


def apply(names=None, dry=False, log=print):
    """Ставит аватары всем ботам, у которых есть env-файл с токеном. Итог — {бот: "ок"/"пропущен…"/"ошибка: …"}."""
    res = {}
    tmp = tempfile.mkdtemp(prefix="avatars-")
    want = [resolve(n) for n in names] if names else list(BOTS)
    for n in want:
        path, var = BOTS[n]["env"]
        try:
            env = read_env(path)
        except OSError:
            res[n] = "пропущен (нет " + path + ")"
            continue
        token = env.get(var, "")
        if not token:
            res[n] = f"пропущен (нет {var})"
            continue
        if n == "agent" and env.get("TG_MODE", "") != "own":
            res[n] = "пропущен (Секретарь работает через Главного)"
            continue
        png = make(n, os.path.join(tmp, n + ".png"))
        if dry:
            res[n] = "готов (dry-run, не отправлял)"
            continue
        ok, err = set_photo(token, png)
        res[n] = "ок" if ok else "ошибка: " + (err or "нет ответа")
    if not names:  # боты Студии: аватар в data/avatar.png (botkit держит его сам) и сразу в Telegram
        for slug, title, colors, folder, token in studio_bots():
            key = "studio/" + slug
            if not token:
                res[key] = "пропущен (нет BOT_TOKEN)"
                continue
            out = os.path.join(tmp, slug + ".png") if dry else os.path.join(folder, "data", "avatar.png")
            custom(out, title[:1], *colors)
            if dry:
                res[key] = "готов (dry-run, не отправлял)"
                continue
            ok, err = set_photo(token, out)
            res[key] = "ок" if ok else "ошибка: " + (err or "нет ответа")
    for k, v in res.items():
        log(f"{k}: {v}")
    return res


# ---------- запуск ----------

def _python_with_pil():
    """Системный python3 без Pillow — берём venv любого бота, где он есть (рисунок будет с тенью и сглаживанием)."""
    for d in ("common", "avito", "tgbot", "coach", "eye", "hub", "planner"):
        py = os.path.join(REPO, d, ".venv", "bin", "python")
        if os.path.exists(py) and os.path.realpath(py) != os.path.realpath(sys.executable):
            if subprocess.run([py, "-c", "import PIL"], capture_output=True).returncode == 0:
                return py
    return None


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if not _pil() and not os.environ.get("AVATAR_NO_REEXEC"):
        py = _python_with_pil()
        if py:
            return subprocess.run([py, os.path.abspath(__file__)] + argv,
                                  env={**os.environ, "AVATAR_NO_REEXEC": "1"}).returncode
    cmd, rest = argv[0], argv[1:]
    if cmd == "list":
        for n, p in BOTS.items():
            print(f"{n}: {p['title']} — {p['env'][0]}")
    elif cmd == "preview":
        print(preview(rest[0] if rest else "avatars.png"))
    elif cmd == "make":
        print(make(rest[0], rest[1]))
    elif cmd == "custom":
        out, letter = rest[0], (rest[1] if len(rest) > 1 else "")
        colors = rest[2:4] if len(rest) >= 4 else ["#7c5cff", "#22d3ee"]
        print(custom(out, letter, *colors))
    elif cmd == "apply":
        dry = "--dry-run" in rest
        res = apply([a for a in rest if not a.startswith("--")] or None, dry=dry)
        return 1 if any(v.startswith("ошибка") for v in res.values()) else 0
    else:
        print(__doc__)
        return 2
    return 0


def load():
    """Импорт этого файла из других папок (studio/avatar.py, tgbot/art/make.py) без конфликта имён «avatar»."""
    spec = importlib.util.spec_from_file_location("common_avatar", os.path.abspath(__file__))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
