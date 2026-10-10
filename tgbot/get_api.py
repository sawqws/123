#!/usr/bin/env python3
"""Получить api_id и api_hash с my.telegram.org без браузера — прямо с сервера.

Зачем: из России сайт открывается только через VPN, а с VPN кнопка «Create application»
часто отвечает «ERROR». С сервера VPN не нужен. Делает то же, что сайт:
  номер -> код (приходит в Telegram от «Telegram») -> вход -> создать приложение -> ключи.

  python3 tgbot/get_api.py          спросит номер и код, напечатает «api_id api_hash» последней строкой
Только стандартная библиотека Python. Вопросы пишет в терминал (/dev/tty), так что работает и внутри curl | bash.
"""
import json
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

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
    """Куки храним сами: http.cookiejar не принял куку входа сайта (вход «true», а /apps — Unauthorized)."""

    def __init__(self, base=BASE):
        self.base = base
        self.cookies = {}
        self.http = urllib.request.build_opener()

    def remember(self, headers):
        for h in headers.get_all("Set-Cookie") or []:
            name, _, value = h.split(";", 1)[0].strip().partition("=")
            if name:
                self.cookies[name] = value

    def req(self, path, data=None):
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        headers = {"User-Agent": UA, "Referer": self.base + "/auth"}
        if body is not None:  # как jQuery на сайте
            headers["X-Requested-With"] = "XMLHttpRequest"
            headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        if self.cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        for attempt in range(3):  # прокси иногда рвёт соединение
            try:
                with self.http.open(urllib.request.Request(self.base + path, body, headers), timeout=30) as r:
                    self.remember(r.headers)
                    return r.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                self.remember(e.headers)
                raise Fail(e.read().decode("utf-8", "replace").strip() or f"HTTP {e.code}")
            except (urllib.error.URLError, ConnectionError) as e:
                if attempt == 2:
                    raise Fail(f"нет связи с {self.base}: {getattr(e, 'reason', e)}")
                time.sleep(2)

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
        page = self.req("/apps")
        found = parse_keys(page)
        if found:
            return found
        m = re.search(r'name="hash"\s+value="([0-9a-f]+)"', page)
        if not m:
            raise Fail("не нашёл ни ключей, ни формы создания на /apps — похоже, вход не прошёл")
        return None, m.group(1)

    def create(self, form_hash):
        try:
            text = self._create(form_hash)
        except Fail as e:
            text = str(e)
        if text.strip().upper().startswith("ERROR"):
            raise Fail("сайт ответил ERROR при создании приложения (с сервера тоже) — попробуй через час")

    def _create(self, form_hash):
        return self.req("/apps/create", {
            "hash": form_hash, "app_title": "Gleb Agent", "app_shortname": f"glebagent{random.randint(1000, 999999)}",
            "app_url": "", "app_platform": "desktop", "app_desc": "personal assistant"})


def parse_keys(page):
    api_id = re.search(r"api_id.{0,400}?<strong>\s*(\d+)\s*</strong>", page, re.S | re.I)
    api_hash = re.search(r"api_hash.{0,400}?>\s*([0-9a-f]{32})\s*<", page, re.S | re.I)
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
