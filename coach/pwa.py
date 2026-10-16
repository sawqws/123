"""Форма как отдельное приложение на экране «Домой» (без Telegram): coach.<host>/site/.

Вход: /site в боте → ссылка (сразу входит) и код из 6 цифр (ввести в приложении с экрана «Домой» —
у него на iPhone свои cookie, не как в Safari). Код живёт 15 минут и годится на 3 входа.
Сессия — cookie forma_site (HttpOnly, Secure, SameSite=Lax) на полгода; в базе хранится только sha256.
API с cookie принимается только со страницы самого приложения: есть заголовок X-Init-Data (его ставит
Kit.api, чужая страница без разрешения CORS его не пошлёт) и Sec-Fetch-Site не cross-site.
"""
import hashlib
import json
import re
import secrets
import threading
import time

COOKIE = "forma_site"
CODE_TTL = 15 * 60
CODE_USES = 3
SESSION_TTL = 180 * 86400
FAILS = (10, 600)  # не больше 10 неверных кодов за 10 минут на весь сервер — подбор 6 цифр бессмыслен
_fails = []
_lock = threading.Lock()


def _h(s):
    return hashlib.sha256(s.encode()).hexdigest()


def _load(db, key):
    try:
        v = json.loads(db.meta(key) or "{}")
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}


def new_code(db, uid, now=None):
    """Код и токен ссылки для uid. Старые коды этого человека сгорают."""
    now = now or time.time()
    with _lock:
        codes = {k: v for k, v in _load(db, "site:codes").items() if v["exp"] > now and v["uid"] != uid}
        code = f"{secrets.randbelow(10 ** 6):06d}"
        while _h(code) in codes:
            code = f"{secrets.randbelow(10 ** 6):06d}"
        link = secrets.token_urlsafe(24)
        rec = {"uid": uid, "exp": now + CODE_TTL, "uses": CODE_USES}
        codes[_h(code)] = rec
        codes[_h(link)] = dict(rec, link=True, uses=1)
        db.meta("site:codes", json.dumps(codes))
    return code, link


def login(db, code, now=None):
    """Код или токен ссылки → токен сессии (или None). Неверных попыток много — PermissionError."""
    now = now or time.time()
    code = re.sub(r"\s+", "", str(code or ""))[:64]
    with _lock:
        _fails[:] = [t for t in _fails if t > now - FAILS[1]]
        if len(_fails) >= FAILS[0]:
            raise PermissionError("Слишком много неверных кодов — подожди 10 минут")
        codes = {k: v for k, v in _load(db, "site:codes").items() if v["exp"] > now}
        rec = codes.get(_h(code)) if code else None
        if not rec:
            _fails.append(now)
            db.meta("site:codes", json.dumps(codes))
            return None
        rec["uses"] -= 1
        if rec["uses"] <= 0:
            codes.pop(_h(code))
        db.meta("site:codes", json.dumps(codes))
        token = secrets.token_urlsafe(32)
        sess = {k: v for k, v in _load(db, "site:sessions").items() if v["exp"] > now}
        sess[_h(token)] = {"uid": rec["uid"], "exp": now + SESSION_TTL}
        db.meta("site:sessions", json.dumps(sess))
    return token


def logout(db, token):
    with _lock:
        sess = _load(db, "site:sessions")
        if sess.pop(_h(token or ""), None):
            db.meta("site:sessions", json.dumps(sess))


def cookie(headers):
    m = re.search(rf"(?:^|;\s*){COOKIE}=([\w-]+)", headers.get("Cookie") or "")
    return m.group(1) if m else ""


def uid_for(db, headers, api=True, now=None):
    """uid по cookie сессии или None. api=True — ещё и защита от запросов с чужих страниц."""
    tok = cookie(headers)
    if not tok:
        return None
    if api and ("X-Init-Data" not in headers or (headers.get("Sec-Fetch-Site") or "same-origin") not in ("same-origin", "none")):
        return None
    rec = _load(db, "site:sessions").get(_h(tok))
    return rec["uid"] if rec and rec["exp"] > (now or time.time()) else None


def set_cookie(token, secure=True):
    age = SESSION_TTL if token else 0
    return f"{COOKIE}={token}; Path=/; Max-Age={age}; HttpOnly; SameSite=Lax" + ("; Secure" if secure else "")


HEAD = """<meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <meta name="apple-mobile-web-app-title" content="Форма">
  <meta name="format-detection" content="telephone=no">
  <link rel="manifest" href="/site/manifest.webmanifest">
  <link rel="apple-touch-icon" href="/site/icon-180.png">
  <link rel="icon" type="image/png" sizes="192x192" href="/site/icon-192.png">
  <link rel="stylesheet" href="site.css?v={v}">"""


def page(index_html, version, bot=""):
    """index.html мини-приложения → страница сайта: без скрипта Telegram, с манифестом; скрипты приложения
    грузит site.js после входа."""
    h = re.sub(r'\s*<script src="https://telegram\.org/[^"]*"></script>', "", index_html)
    h = h.replace("</head>", "  " + HEAD.format(v=version) + "\n</head>", 1)
    h = h.replace("<body ", '<body class="site" ', 1)
    tags = re.findall(r'\s*<script src="([^"]+)"></script>', h)
    rest = [s for s in tags if not s.startswith("kit.js")]
    for s in rest:
        h = h.replace(f'<script src="{s}"></script>', "", 1)
    boot = (f'<script>window.FORMA_SITE = {json.dumps({"scripts": rest, "bot": bot}, ensure_ascii=False)};</script>\n'
            f'  <script src="site.js?v={version}"></script>\n')
    h = h.replace("</body>", "  " + boot + "</body>", 1)
    return re.sub(r"\n\s*\n", "\n", h)


def manifest():
    return {
        "name": "Форма", "short_name": "Форма", "lang": "ru",
        "description": "Тренер по данным WHOOP: восстановление, сон, нагрузка, питание",
        "start_url": "/site/", "scope": "/site/", "id": "/site/",
        "display": "standalone", "orientation": "portrait",
        "background_color": "#101518", "theme_color": "#101518",
        "icons": [
            {"src": "/site/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/site/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ],
    }
