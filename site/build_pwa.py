#!/usr/bin/env python3
"""Собирает сайт «Ритм» как веб-приложение (PWA) для GitHub Pages: docs/.
Исходник один — site/ritm.html (формат артефакта claude.ai: без <html>/<head>).
Здесь он оборачивается в полный документ с манифестом, иконками и офлайн-кэшем.
usage: python3 site/build_pwa.py
"""
import hashlib
import json
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "docs")

HEAD = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Ритм">
<meta name="theme-color" content="#000000">
<link rel="manifest" href="manifest.webmanifest">
<link rel="apple-touch-icon" href="icon-180.png">
<link rel="icon" type="image/png" href="icon-192.png">
<style id="pwa">
/* приложение на весь экран: шапка уходит под статус-бар, низ — над полоской «Домой» */
.topbar { top: 0 !important; padding-top: calc(12px + env(safe-area-inset-top, 0px)) !important; }
[hidden] { display: none !important; }
</style>
</head>
<body>
"""
TAIL = """
<script>
if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js').catch(() => {});
</script>
</body>
</html>
"""


def icon(size):
    s = 1024
    img = Image.new("RGB", (s, s), (0, 0, 0))
    d = ImageDraw.Draw(img)
    bars = [0.40, 1.0, 0.62, 0.84]  # как логотип: четыре столбика ритма
    w, gap, top, bottom = 104, 64, 250, 774
    x = (s - (4 * w + 3 * gap)) // 2
    for i, h in enumerate(bars):
        y = bottom - (bottom - top) * h
        c = (123 + i * 6, 127 + i * 10, 255)
        d.rounded_rectangle([x, y, x + w, bottom], radius=w // 2, fill=c)
        x += w + gap
    return img.resize((size, size), Image.LANCZOS)


def main():
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(HERE, "ritm.html"), encoding="utf-8") as f:
        body = f.read()
    cut = body.index('<div class="app">')  # заголовок, шрифты и стили — в <head>
    page = HEAD.replace("</head>", body[:cut] + "</head>") + body[cut:] + TAIL
    with open(os.path.join(OUT, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)
    for n in (180, 192, 512):
        icon(n).save(os.path.join(OUT, f"icon-{n}.png"))
    manifest = {
        "name": "Ритм", "short_name": "Ритм", "lang": "ru",
        "description": "Привычки, задачи и цели",
        "start_url": "./", "scope": "./", "display": "standalone",
        "background_color": "#000000", "theme_color": "#000000",
        "icons": [{"src": "icon-192.png", "sizes": "192x192", "type": "image/png"},
                  {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}],
    }
    with open(os.path.join(OUT, "manifest.webmanifest"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    version = hashlib.sha1(page.encode()).hexdigest()[:10]
    sw = f"""// офлайн: страница и иконки в кэше; при сети — всегда свежая версия
const CACHE = 'ritm-{version}';
const FILES = ['./', 'index.html', 'manifest.webmanifest', 'icon-180.png', 'icon-192.png', 'icon-512.png'];
self.addEventListener('install', e => {{ e.waitUntil(caches.open(CACHE).then(c => c.addAll(FILES)).then(() => self.skipWaiting())); }});
self.addEventListener('activate', e => {{ e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim())); }});
self.addEventListener('fetch', e => {{
  if (e.request.method !== 'GET') return;
  e.respondWith(fetch(e.request).then(r => {{
    if (r.ok || r.type === 'opaque') {{ const copy = r.clone(); caches.open(CACHE).then(c => c.put(e.request, copy)); }}
    return r;
  }}).catch(() => caches.match(e.request, {{ ignoreSearch: true }}).then(r => r || caches.match('index.html'))));
}});
"""
    with open(os.path.join(OUT, "sw.js"), "w", encoding="utf-8") as f:
        f.write(sw)
    with open(os.path.join(OUT, ".nojekyll"), "w") as f:
        f.write("")
    print("docs/ собран, версия", version)


if __name__ == "__main__":
    main()
