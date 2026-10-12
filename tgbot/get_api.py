#!/usr/bin/env python3
"""Получить api_id и api_hash с my.telegram.org без браузера — прямо с сервера.

Зачем: из России сайт открывается только через VPN, а с VPN кнопка «Create application»
часто отвечает «ERROR». С сервера VPN не нужен. Делает то же, что сайт:
  номер -> код (приходит в Telegram от «Telegram») -> вход -> создать приложение -> ключи.

  python3 tgbot/get_api.py          спросит номер и код, напечатает «api_id api_hash» последней строкой
Только стандартная библиотека Python. Вопросы пишет в терминал (/dev/tty), так что работает и внутри curl | bash.
"""
import http.client
import json
import os
import random
import re
import sys
import ssl
import time
import urllib.parse

BASE = "https://my.telegram.org"


class Fail(Exception):
    pass


def tty():
    """Ввод-вывод с терминалом, даже если stdin занят (curl ... | bash)."""
    try:
        return open("/dev/tty", "r"), open("/dev/tty", "w")
    except OSError:
        return sys.stdin, sys.stderr


def ask(prompt, io):
    inp, out = io
    out.write(prompt)
    out.flush()
    return inp.readline().strip()


def say(text, io):
    io[1].write(text + "\n")
    io[1].flush()


UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"


class Site:
    """Куки храним сами (http.cookiejar не принял куку входа). Все запросы — через ОДНО соединение keep-alive:
    вход на сайте привязан к IP, а через прокси с несколькими выходными IP каждый новый запрос мог уйти
    с другого адреса (сайт «забывал» вход и отвечал ERROR на создание приложения)."""

    def __init__(self, base=BASE):
        self.base = base
        u = urllib.parse.urlparse(base)
        self.host, self.port, self.secure = u.hostname, u.port or (443 if u.scheme == "https" else 80), u.scheme == "https"
        self.cookies = {}
        self.tries = 20  # повторы, если сайт всё же «забыл» вход (см. keys)
        self.conn = None

    def remember(self, headers):
        for h in headers.get_all("Set-Cookie") or []:
            name, _, value = h.split(";", 1)[0].strip().partition("=")
            if name:
                self.cookies[name] = value

    def connect(self):
        proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") if self.secure else None
        ctx = ssl.create_default_context(cafile=os.environ.get("SSL_CERT_FILE") or None) if self.secure else None
        if proxy:
            p = urllib.parse.urlparse(proxy)
            c = http.client.HTTPSConnection(p.hostname, p.port, context=ctx, timeout=30)
            c.set_tunnel(self.host, self.port)
        elif self.secure:
            c = http.client.HTTPSConnection(self.host, self.port, context=ctx, timeout=30)
        else:
            c = http.client.HTTPConnection(self.host, self.port, timeout=30)
        self.conn = c

    def req(self, path, data=None, _redirects=3):
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        headers = {"User-Agent": UA, "Referer": self.base + "/auth", "Connection": "keep-alive"}
        if body is not None:  # как jQuery на сайте
            headers["X-Requested-With"] = "XMLHttpRequest"
            headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        if self.cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        for attempt in range(3):
            if self.conn is None:
                self.connect()
            try:
                self.conn.request("POST" if body is not None else "GET", path, body, headers)
                r = self.conn.getresponse()
                text = r.read().decode("utf-8", "replace")
                break
            except (OSError, http.client.HTTPException) as e:  # соединение порвалось — новое (IP может смениться)
                self.conn.close()
                self.conn = None
                if attempt == 2:
                    raise Fail(f"нет связи с {self.base}: {e}")
                time.sleep(2)
        self.remember(r.headers)
        if r.status in (301, 302, 303) and r.getheader("Location") and _redirects:
            loc = urllib.parse.urlparse(r.getheader("Location"))
            return self.req(loc.path + (f"?{loc.query}" if loc.query else ""), None, _redirects - 1)
        if r.status >= 400:
            raise Fail(text.strip() or f"HTTP {r.status}")
        return text

    def send_code(self, phone):
        self.req("/auth")  # как браузер: сначала страница входа
        text = self.req("/auth/send_password", {"phone": phone})
        try:
            return json.loads(text)["random_hash"]
        except (ValueError, KeyError):
            raise Fail(text.strip() or "сайт не прислал код")

    def login(self, phone, random_hash, code):
        before = set(self.cookies)
        text = self.req("/auth/login", {"phone": phone, "random_hash": random_hash, "password": code, "remember": "1"})
        if text.strip() != "true":
            raise Fail(text.strip() or "код не подошёл")
        if not set(self.cookies) - before and "stel_token" not in self.cookies:
            raise Fail(f"вход прошёл, но сайт не выдал куку (есть: {', '.join(self.cookies) or 'ничего'})")

    def keys(self):
        """(api_id, api_hash), если приложение уже есть, иначе (None, hash формы создания)."""
        for attempt in range(self.tries):
            page = self.last_page = self.req("/apps")
            if "my_login_form" not in page:
                break
            if self.conn is not None:  # вход привязан к другому IP — новое соединение, другой выходной адрес
                self.conn.close()
                self.conn = None
            time.sleep(1)  # выкинуло на вход: выход в интернет через несколько IP, а вход привязан к одному
        found = parse_keys(page)
        if found:
            return found
        form_hash = parse_form_hash(page)
        if not form_hash:
            raise Fail("не нашёл ни ключей, ни формы создания на /apps — похоже, вход не прошёл")
        return None, form_hash

    # Варианты полей: сайт на создание отвечает просто «ERROR» без причины — пробуем по-разному.
    VARIANTS = [("Gleb Agent", "desktop", "", "personal assistant"),
                ("Personal Notes", "android", "https://telegram.org", "Personal notes and reminders app"),
                ("Daily Helper", "other", "", "Personal desktop helper for messages and reminders")]

    def create(self, form_hash):
        tried = []
        for title, platform, url, desc in self.VARIANTS:
            for attempt in range(2):
                try:
                    text = self._create(form_hash, title, platform, url, desc)
                except Fail as e:
                    text = str(e)
                tried.append(f"{title}/{platform}: {text.strip()[:40] or 'ok'}")
                if not any(w in text for w in ("Unauthorized", "my_login_form", "ERROR")):
                    return
                time.sleep(2)
        self.tried = tried
        raise Fail("сайт ответил ERROR при создании приложения на все варианты полей")

    def _create(self, form_hash, title="Gleb Agent", platform="desktop", url="", desc="personal assistant"):
        short = re.sub(r"[^a-z]", "", title.lower())[:20] + str(random.randint(1000, 999999))
        return self.req("/apps/create", {"hash": form_hash, "app_title": title, "app_shortname": short,
                                         "app_url": url, "app_platform": platform, "app_desc": desc})


def parse_form_hash(page):
    """value скрытого поля name="hash" формы создания (атрибуты в любом порядке, кавычки любые)."""
    for tag in re.findall(r"<input\b[^>]*>", page, re.I):
        if re.search(r"""name\s*=\s*["']?hash["'\s/>]""", tag, re.I):
            m = re.search(r"""value\s*=\s*["']([^"']+)["']""", tag, re.I)
            if m:
                return m.group(1)
    return None


def parse_keys(page):
    api_id = re.search(r"api_id.{0,600}?>\s*(\d{3,12})\s*<", page, re.S | re.I)
    api_hash = re.search(r"api_hash.{0,600}?\b([0-9a-f]{32})\b", page, re.S | re.I)
    if api_id and api_hash:
        return api_id.group(1), api_hash.group(1)
    return None


def normalize(phone):
    """«8 999 123-45-67», «79991234567», «+7 (999)…» -> «+79991234567»."""
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    return "+" + digits


def run(site, io):
    say("\n🔑 Получаю ключи Telegram с сервера (без браузера и VPN).", io)
    phone = normalize(ask("Номер телефона аккаунта (+79991234567): ", io))
    random_hash = site.send_code(phone)
    say("Telegram прислал код в приложение — в чат «Telegram» (это код для сайта my.telegram.org).", io)
    for attempt in range(3):
        code = ask("Код: ", io).strip()
        try:
            site.login(phone, random_hash, code)
            break
        except Fail as e:
            if attempt == 2:
                raise
            say(f"✕ {e}. Попробуй ещё раз.", io)
    api_id, rest = site.keys()
    if api_id is None:
        site.create(rest)
        api_id, rest = site.keys()
        if api_id is None:
            raise Fail("приложение не создалось")
    say("✓ Ключи получены.", io)
    return api_id, rest


def main():
    io = tty()
    try:
        api_id, api_hash = run(Site(), io)
    except Fail as e:
        say(f"✕ Не получилось: {e}", io)
        return 1
    print(api_id, api_hash)  # последняя строка stdout — для install.sh
    return 0


if __name__ == "__main__":
    sys.exit(main())
