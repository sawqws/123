"""🧪 Проверка сайта-зеркала «от А до Я» (Глеб 19.10: «добавь в бота проверку сайта от А до Я»).

Бот выдаёт себе вход в зеркало на время проверки, node bot/sitecheck.js обходит страницы платформы через зеркало
(ошибки JS, ответы 5xx/4xx, битые картинки, вылезание вбок на компьютере/iPad/телефоне, долгие запросы, панель бота),
сверяет ошибки с настоящей платформой и пишет отчёт в horo/tmp/sitecheck/last.json. Вход после проверки закрывается.
Найденное «наше» можно отдать Claude (поддержка HDP: правит код, тесты, перезапуск) — fix_text().
"""
import json
import os
import subprocess
import threading
import time

import web

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DIR = os.path.join(REPO, "horo", "tmp", "sitecheck")
LAST = os.path.join(DIR, "last.json")
TIMEOUT = 30 * 60
KIND = {"js": "ошибка JS", "console": "ошибка в консоли", "http": "ответ сервера", "failed": "запрос оборвался",
        "img": "картинка", "wide": "вылезает вбок", "empty": "пустая страница", "nav": "не открылась",
        "login": "вход", "bot": "кнопки бота", "slow": "долго"}
VIEW = {"desktop": "компьютер", "ipad": "iPad", "phone": "телефон"}
lock = threading.Lock()


def running():
    return lock.locked()


def last():
    try:
        with open(LAST) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def run(max_pages=45, orig=True):
    """Полная проверка. -> отчёт (dict). RuntimeError — уже идёт, сайт не готов или скрипт упал."""
    if not lock.acquire(blocking=False):
        raise RuntimeError("Проверка уже идёт")
    token = None
    try:
        base = web.site_url()
        if not base:
            raise RuntimeError("Сайт ещё запускается — попробуй через минуту")
        os.makedirs(DIR, exist_ok=True)
        token = web.site_login(web.site_code())  # вход только на время проверки
        out = os.path.join(DIR, "run.json")
        cmd = ["node", os.path.join(HERE, "sitecheck.js"), base, out, "--max", str(max_pages)] + (["--orig"] if orig else [])
        try:
            r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=TIMEOUT,
                               env={**os.environ, "SITE_TOKEN": token})
        except subprocess.TimeoutExpired:
            raise RuntimeError("Проверка не уложилась в 30 минут")
        try:  # отчёт записан — он и есть результат (Chromium на сервере иногда падает уже при выходе)
            with open(out) as f:
                rep = json.load(f)
        except (OSError, ValueError):
            raise RuntimeError(((r.stderr or r.stdout or "").strip().splitlines() or ["проверка упала"])[-1][:300])
        os.replace(out, LAST)
        return rep
    finally:
        if token:
            web.site_logout(token)
        lock.release()


def ours(rep):
    """Ошибки зеркала (без тех, что есть и на самой платформе, и без «долго»)."""
    return [i for i in (rep or {}).get("issues", []) if not i.get("platform") and i.get("kind") != "slow"]


def line(i):
    where = ", ".join(VIEW.get(v, v) for v in i.get("views", []) if v != "desktop")
    pages = ", ".join(i.get("pages", [])[:2])
    return f"{KIND.get(i['kind'], i['kind'])}: {i['text']}" + (f" ({where})" if where else "") + (f" — {pages}" if pages else "")


def summary(rep):
    """-> (заголовок, строки, ошибки наши) для Telegram и сайта."""
    mine = ours(rep)
    other = [i for i in rep.get("issues", []) if i.get("platform")]
    slow = [i for i in rep.get("issues", []) if i.get("kind") == "slow" and not i.get("platform")]
    head = (f"Сайт: проверил {rep['pages']} стр. — ошибок нет" if not mine
            else f"Сайт: проверил {rep['pages']} стр. — ошибок {len(mine)}")
    rows = [line(i) for i in mine[:8]]
    if len(mine) > 8:
        rows.append(f"…и ещё {len(mine) - 8}")
    rows.append(f"Страница в среднем {rep.get('avg_ms', 0) / 1000:.1f} с · проверка {round(rep.get('secs', 0) / 60)} мин")
    if slow:
        rows.append(f"Долгих запросов: {len(slow)}")
    if other:
        rows.append(f"Ошибок самой платформы (не наши): {len(other)}")
    return head, rows, mine


def fix_text(rep):
    """Задание для Claude (поддержка HDP): что починить в зеркале."""
    mine = ours(rep)
    items = "\n".join(f"- {line(i)}" for i in mine[:30])
    return ("🧪 Проверка сайта-зеркала HDP нашла ошибки (их нет на настоящей платформе — значит, наши: bot/mirror.py, "
            "bot/web.py, bot/site/mirror/overlay.js|css|guard.js). Найди причину каждой и почини в корне, добавь тест, "
            "если можно. Отчёт целиком: horo/tmp/sitecheck/last.json. Ошибки:\n" + items)


def age(rep):
    t = (rep or {}).get("at")
    return int(time.time() - t) if t else None
