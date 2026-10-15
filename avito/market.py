"""Средняя цена на Авито: до 100 похожих объявлений из поиска → медиана, «быстро продать», «дороже».

Поиск — обычная страница www.avito.ru/<регион>?q=…, разбор HTML (официального API поиска у Авито нет).
Если Авито не пускает простой запрос (429/403, «Доступ ограничен»), пробуем настоящим Chromium (search.js).
Только стандартная библиотека Python.
"""
import html as htmllib
import os
import re
import statistics
import subprocess
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0.0.0 Safari/537.36")
WANT = 100   # сколько объявлений собираем
PAGES = 3    # на странице поиска ~50 объявлений


class Blocked(Exception):
    """Авито не пустил (ограничение по IP / капча)."""


def search_url(query, region="all", page=1):
    q = urllib.parse.urlencode({"q": query, **({"p": page} if page > 1 else {})})
    return f"https://www.avito.ru/{urllib.parse.quote(region or 'all')}?{q}"


def blocked_page(text):
    return "Доступ ограничен" in text or "firewall-title" in text or "captcha" in text.lower()[:20000]


def fetch_plain(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            text = r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            raise Blocked(f"HTTP {e.code}")
        raise
    if blocked_page(text):
        raise Blocked("страница блокировки")
    return text


def fetch_browser(url):
    try:
        p = subprocess.run(["node", os.path.join(HERE, "search.js"), url], capture_output=True, text=True,
                           timeout=90, cwd=os.path.dirname(HERE))
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Blocked(f"браузер: {e}")
    if p.returncode == 2 or blocked_page(p.stdout or ""):
        raise Blocked("страница блокировки (браузер)")
    if p.returncode != 0:
        raise Blocked("браузер: " + (p.stderr or "").strip()[-200:])
    return p.stdout


def fetch(url, mode):
    """mode: [способ] — запоминаем, какой сработал, чтобы на следующих страницах не тратить время."""
    if mode and mode[0] == "browser":
        return fetch_browser(url)
    try:
        return fetch_plain(url)
    except (Blocked, urllib.error.URLError, OSError):
        mode[:] = ["browser"]
        return fetch_browser(url)


def _attr(chunk, *patterns):
    for p in patterns:
        m = re.search(p, chunk, re.S)
        if m:
            return htmllib.unescape(m.group(1)).strip()
    return ""


def safe_url(href):
    """Ссылка на объявление — только https на avito.ru (из разобранной страницы в приложение попадает как есть)."""
    if not href:
        return ""
    url = urllib.parse.urljoin("https://www.avito.ru", href)
    host = urllib.parse.urlparse(url).hostname or ""
    return url if url.startswith("https://") and (host == "avito.ru" or host.endswith(".avito.ru")) else ""


def parse(page):
    """HTML поиска → [{id, title, price, url}]. Карточки — блоки data-marker="item"."""
    items = []
    parts = re.split(r'(?=<div[^>]*data-marker="item"[^>]*>)', page)
    for chunk in parts[1:]:
        chunk = chunk[:20000]
        price = _attr(chunk, r'itemprop="price"\s+content="(\d[\d\s\u00a0]*)"', r'content="(\d[\d\s\u00a0]*)"\s+itemprop="price"',
                      r'data-marker="item-price"[^>]*>.*?(\d[\d\s ]*)\s*₽')
        title = _attr(chunk, r'data-marker="item-title"[^>]*\btitle="([^"]+)"',
                      r'\btitle="([^"]+)"[^>]*data-marker="item-title"', r'itemprop="name"[^>]*>([^<]+)<')
        href = _attr(chunk, r'data-marker="item-title"[^>]*\bhref="([^"]+)"',
                     r'\bhref="([^"]+)"[^>]*data-marker="item-title"', r'\bhref="(/[^"]+_\d+)"')
        iid = _attr(chunk, r'data-item-id="(\d+)"', r'_(\d{6,})(?:\?|")')
        price = re.sub(r"\D", "", price)
        if not price or not title:
            continue
        items.append({"id": iid or href, "title": title, "price": int(price),
                      "url": safe_url(href)})
    return items


def collect(query, region="all", want=WANT):
    """До want объявлений по запросу. Blocked — если Авито не пустил ни одним способом."""
    seen, out, mode = set(), [], []
    for page in range(1, PAGES + 1):
        try:
            items = parse(fetch(search_url(query, region, page), mode))
        except Blocked:
            if out:
                break
            raise
        fresh = [i for i in items if i["id"] not in seen]
        for i in fresh:
            seen.add(i["id"])
        out += fresh
        if len(out) >= want or not fresh:
            break
    return out[:want]


WORD = re.compile(r"[a-zа-яё0-9]+", re.I)


def relevant(query, items):
    """Грубый отбор похожих: в названии есть большинство значимых слов запроса."""
    words = [w for w in WORD.findall(query.lower()) if len(w) > 1 or w.isdigit()]
    if not words:
        return items
    need = max(1, round(len(words) * 0.6))
    res = []
    for i in items:
        title = i["title"].lower().replace("ё", "е")
        if sum(1 for w in words if w.replace("ё", "е") in title) >= need:
            res.append(i)
    return res


def trim_outliers(prices):
    """Отбрасываем явные выбросы (аксессуары за 300 ₽ среди телефонов, «обмен» за 1 ₽ и т.п.)."""
    prices = sorted(p for p in prices if p > 1)
    if len(prices) < 5:
        return prices
    q1, _, q3 = statistics.quantiles(prices, n=4)
    lo, hi = q1 - 1.5 * (q3 - q1), q3 + 1.5 * (q3 - q1)
    return [p for p in prices if lo <= p <= hi]


def nice(x):
    """Красивая цена: 34 870 → 34 900, 1 240 → 1 250, 470 → 470."""
    x = float(x)
    step = 1000 if x >= 100000 else 100 if x >= 5000 else 50 if x >= 1000 else 10
    return int(round(x / step) * step)


def stats(items):
    prices = trim_outliers([i["price"] for i in items])
    if not prices:
        return None
    q = statistics.quantiles(prices, n=4) if len(prices) >= 4 else [min(prices), statistics.median(prices),
                                                                      max(prices)]
    return {"n": len(prices), "total": len(items), "median": nice(statistics.median(prices)),
            "fast": nice(q[0]), "high": nice(q[2]), "min": min(prices), "max": max(prices)}


def fmt_rub(x):
    return f"{int(x):,}".replace(",", " ") + " ₽"
