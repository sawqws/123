"""🪞 Дозор зеркала (Глеб 23.10: «сделай что-то отдельное, что часто проверяет работу сайта-зеркала и сразу чинит»).

Лёгкая проверка за ~10 с, без браузера и без Claude; её каждые 5 минут зовёт 🛡 Страж (hub/guard.py), а найденное
он отдаёт Claude чинить. Заходит в зеркало как Глеб (свой вход только на время проверки, потом закрывается):
  • /health, вход по cookie (/api/state бота), каркас страницы платформы с кнопками бота, файлы кнопок (/_hdp/…);
  • API платформы через зеркало (предметы, темы) отвечает;
  • данные те же, что у самой платформы: темы, сроки, статусы заданий (зеркало держит часть ответов в памяти —
    расхождение проверяется ещё раз через 20 с, после фонового обновления);
  • ничего не отвечает дольше 10 с.
Ничего не меняет: только GET. Не судит, если сама платформа не отвечает (не наша ошибка).

  python3 bot/mirrorwatch.py <https://hdp.…>   → JSON {at, ok, secs, checks: [...], issues: [{text, detail}]}
Отчёт — и в horo/tmp/mirrorwatch/last.json.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import mirror  # noqa: E402
import web  # noqa: E402

LAST = os.path.join(os.path.dirname(HERE), "horo", "tmp", "mirrorwatch", "last.json")
EDU = "/api/education/v1/students/current"
TOPICS = EDU + "/topics?onlyDebts=false"
SLOW = 10         # секунд: дольше — «долго»
AGAIN = 20        # расхождение с платформой — ещё раз через столько (память зеркала обновляется в фоне за 15 с)
TIMEOUT = 40


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None  # редирект — тоже ответ: на вход выкинуло — это видно по коду и адресу


_open = urllib.request.build_opener(NoRedirect).open


def get(base, path, token, site=False):
    """GET через зеркало → (код, тело bytes, секунд, Location)."""
    headers = {"Cookie": "hdp_site=" + token, "Accept": "application/json, text/html, */*", "User-Agent": "HDP-mirrorwatch"}
    if site:
        headers["X-Site"] = "1"
    t = time.time()
    try:
        with _open(urllib.request.Request(base + path, headers=headers), timeout=TIMEOUT) as r:
            return r.status, r.read(), time.time() - t, ""
    except urllib.error.HTTPError as e:
        return e.code, e.read() or b"", time.time() - t, e.headers.get("Location") or ""
    except Exception as e:
        return 0, str(e).encode(), time.time() - t, ""


def data_of(x):
    return x.get("data", x) if isinstance(x, dict) else x


def topics_sig(raw):
    """Что должно совпадать с платформой: темы, сроки, уровень темы, статусы заданий. → {uuid темы: (...)}"""
    out = {}
    for tp in data_of(raw) or []:
        if not isinstance(tp, dict):
            continue
        sp = tp.get("studyPeriod") or {}
        lvl = (tp.get("achievedLevel") or {}).get("totalLevel")
        tasks = tuple(sorted((t.get("uuid"), ((t.get("progress") or {}).get("status") or {}).get("type"))
                             for t in tp.get("tasks") or []))
        out[tp.get("uuid")] = ((tp.get("title") or "").strip(), sp.get("deadlineDate"), sp.get("extraDeadlineDays"), lvl, tasks)
    return out


def diff(mine, theirs):
    """Расхождения зеркала с платформой — понятными строками (не больше 5)."""
    rows = []
    for k in sorted(set(mine) | set(theirs), key=str):
        a, b = mine.get(k), theirs.get(k)
        if a == b:
            continue
        name = (b or a)[0][:40]
        if not a:
            rows.append(f"нет темы «{name}»")
        elif not b:
            rows.append(f"лишняя тема «{name}»")
        elif a[1:3] != b[1:3]:
            rows.append(f"«{name}»: срок {a[1]}+{a[2]}, а на платформе {b[1]}+{b[2]}")
        elif a[3] != b[3]:
            rows.append(f"«{name}»: уровень {a[3]}, а на платформе {b[3]}")
        else:
            st = dict(b[4])
            bad = [(u, s) for u, s in a[4] if st.get(u) != s]
            rows.append(f"«{name}»: статусов не так {len(bad) or abs(len(a[4]) - len(b[4]))}"
                        + (f" (у {bad[0][0][:8]}: {bad[0][1]}, на платформе {st.get(bad[0][0])})" if bad else ""))
    return rows[:5]


def check(base):
    base = base.rstrip("/")
    t0 = time.time()
    checks, issues = [], []

    def note(name, ok, secs, why=""):
        checks.append({"name": name, "ok": ok, "ms": round(secs * 1000), "why": why[:200]})

    def bad(text, detail=""):
        issues.append({"text": "зеркало: " + text, "detail": detail[:2500]})

    def slow(name, secs):
        if secs > SLOW:
            bad(f"{name} отвечает долго ({round(secs)} с)", f"порог {SLOW} с")

    token = web.site_login(web.site_code())
    if not token:
        raise RuntimeError("не выдал себе вход в зеркало (site_login)")
    try:
        # 1. жив
        code, body, s, _ = get(base, "/health", token)
        note("health", code == 200, s, "" if code == 200 else f"ответ {code}")
        if code != 200:
            bad(f"/health — ответ {code}", body[:300].decode("utf-8", "replace"))
            return report(t0, checks, issues)
        # 2. вход по cookie: кнопки бота на сайте работают
        code, body, s, _ = get(base, "/api/state", token, site=True)
        note("вход", code == 200, s, "" if code == 200 else f"ответ {code}")
        if code != 200:
            bad(f"вход по cookie не работает: /api/state — ответ {code}", body[:300].decode("utf-8", "replace"))
        slow("/api/state", s)
        # 3. API платформы через зеркало и сама платформа (сессией бота) — чтобы не винить зеркало в сбое платформы
        direct = {p: mirror.get_json(p) for p in (EDU + "/disciplines", TOPICS)}
        for name, p in (("предметы", EDU + "/disciplines"), ("темы", TOPICS)):
            code, body, s, loc = get(base, p, token)
            try:
                got = json.loads(body) if code == 200 else None
            except ValueError:
                got = None
            ok = got is not None
            note(name, ok, s, "" if ok else f"ответ {code}" + (f" → {loc}" if loc else ""))
            if not ok and direct[p] is not None:  # платформа отвечает, а через зеркало — нет: наше
                bad(f"API {name} через зеркало — ответ {code}" + (f" → {loc}" if loc else ""),
                    f"GET {p}\n" + body[:500].decode("utf-8", "replace"))
            slow(f"API {name}", s)
        # 4. страница платформы с кнопками бота
        code, body, s, loc = get(base, "/student/", token)
        html = body.decode("utf-8", "replace") if code == 200 else ""
        ok = code == 200 and "/_hdp/overlay.js" in html and "/_hdp/guard.js" in html
        note("страница", ok, s, "" if ok else f"ответ {code}" + (f" → {loc}" if loc else "") if code != 200 else "нет кнопок бота")
        if not ok and (code != 200 or "<html" in html.lower()):
            bad("страница /student/ " + (f"— ответ {code}" + (f" → {loc}" if loc else "") if code != 200
                                          else "открылась без кнопок бота (overlay.js/guard.js)"), html[:500])
        slow("страница /student/", s)
        # 5. файлы кнопок бота
        for f in ("overlay.js", "overlay.css", "guard.js"):
            code, body, s, _ = get(base, "/_hdp/" + f, token)
            ok = code == 200 and len(body) > 100
            note(f, ok, s, "" if ok else f"ответ {code}, {len(body)} байт")
            if not ok:
                bad(f"/_hdp/{f} — ответ {code}, {len(body)} байт")
        # 6. данные те же, что на платформе
        if direct[TOPICS] is not None:
            rows = same(base, token, direct[TOPICS])
            note("данные", not rows, 0, "; ".join(rows))
            if rows:
                bad("данные расходятся с платформой", "\n".join(rows))
        else:
            note("данные", True, 0, "платформа не ответила боту — не сверял")
    finally:
        web.site_logout(token)
    return report(t0, checks, issues)


def same(base, token, theirs):
    """Темы через зеркало == темы платформы? → [] или строки расхождений (после второй попытки через AGAIN с)."""
    for attempt in range(2):
        code, body, _, _ = get(base, TOPICS, token)
        try:
            mine = json.loads(body) if code == 200 else None
        except ValueError:
            mine = None
        if mine is None:
            return []  # сам ответ уже проверен выше
        rows = diff(topics_sig(mine), topics_sig(theirs))
        if not rows or attempt:
            return rows
        time.sleep(AGAIN)  # память зеркала могла быть на шаг позади — она обновляется в фоне
        theirs = mirror.get_json(TOPICS) or theirs
    return rows


def report(t0, checks, issues):
    rep = {"at": round(time.time()), "ok": not issues, "secs": round(time.time() - t0, 1), "checks": checks, "issues": issues}
    try:
        os.makedirs(os.path.dirname(LAST), exist_ok=True)
        with open(LAST + ".part", "w") as f:
            json.dump(rep, f, ensure_ascii=False)
        os.replace(LAST + ".part", LAST)
    except OSError:
        pass
    return rep


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python3 bot/mirrorwatch.py <https://hdp.…>")
    print(json.dumps(check(sys.argv[1]), ensure_ascii=False))
