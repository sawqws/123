"""Фото с очков: привести к JPEG до 1600 px (Claude читает быстрее), достать GPS из EXIF, адрес по координатам.

Pillow (и pillow-heif для HEIC с iPhone) — в eye/.venv. Без них фото сохраняется как есть.
Адрес — OpenStreetMap Nominatim (бесплатно, не чаще раза в секунду; результаты кэшируются).
"""
import io
import json
import threading
import time
import urllib.parse
import urllib.request

MAX_SIDE = 1600
try:
    from PIL import Image, ImageOps
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
    except ImportError:
        pass
except ImportError:
    Image = None


def _deg(v):
    d, m, s = (float(x) for x in v)
    return d + m / 60 + s / 3600


def gps(exif):
    """(lat, lon) из EXIF Pillow или None."""
    try:
        g = exif.get_ifd(0x8825)
        lat, lon = _deg(g[2]), _deg(g[4])
        if g.get(1) == "S":
            lat = -lat
        if g.get(3) == "W":
            lon = -lon
        return (round(lat, 6), round(lon, 6)) if lat or lon else None
    except Exception:
        return None


def prepare(data):
    """(байты JPEG, (lat, lon) | None). Если Pillow нет или файл не картинка — (исходные байты, None)."""
    if Image is None:
        return data, None
    try:
        img = Image.open(io.BytesIO(data))
        where = gps(img.getexif())
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((MAX_SIDE, MAX_SIDE))
        out = io.BytesIO()
        img.save(out, "JPEG", quality=85)
        return out.getvalue(), where
    except Exception as e:
        print("photo:", repr(e)[:200])
        return data, None


_cache = {}
_lock = threading.Lock()
_last = [0.0]


def address(lat, lon):
    """Короткий адрес по координатам («Невский проспект, 28, Санкт-Петербург») или ''."""
    key = (round(lat, 4), round(lon, 4))
    if key in _cache:
        return _cache[key]
    q = urllib.parse.urlencode({"lat": lat, "lon": lon, "format": "jsonv2", "accept-language": "ru", "zoom": 18})
    req = urllib.request.Request("https://nominatim.openstreetmap.org/reverse?" + q,
                                 headers={"User-Agent": "eye-bot/1 (personal assistant, github.com/sawqws/123)"})
    try:
        with _lock:
            time.sleep(max(0.0, 1.1 - (time.time() - _last[0])))
            _last[0] = time.time()
            with urllib.request.urlopen(req, timeout=10) as r:
                d = json.load(r)
    except Exception as e:
        print("geo:", repr(e)[:200])
        return ""
    a = d.get("address") or {}
    name = d.get("name") or ""
    street = " ".join(x for x in (a.get("road") or a.get("pedestrian") or "", a.get("house_number") or "") if x)
    city = a.get("city") or a.get("town") or a.get("village") or a.get("state") or ""
    parts = [p for p in (name, street, a.get("suburb") or "", city) if p]
    text = ", ".join(dict.fromkeys(parts)) or d.get("display_name", "")
    _cache[key] = text
    return text


def place(where):
    """Строка для Claude: адрес и координаты."""
    if not where:
        return ""
    lat, lon = where
    addr = address(lat, lon)
    return f"{addr} ({lat:.5f}, {lon:.5f})" if addr else f"координаты {lat:.5f}, {lon:.5f}"
