"""Самопроверка «Формы» (Глеб 03.11: «раз в день сам проверял всё приложение, от А до Я всё протыкивал, смотрел на кнопки,
как они расположены, на баги, и если что — исправлял»).

Когда: каждую ночь в 03:30 по времени владельца (`tick` из coach.tick, ключ `db.once("selfcheck:<день>")`), по `/selfcheck`
владельца в боте и кнопкой «Проверить приложение» в «Ещё» (`POST /api/selfcheck`, только владелец, не чаще раза в 30 мин).
Идёт в фоне (свой поток, тяжёлое — отдельными процессами с nice), не дольше 15 мин, два прогона сразу не бывает (flock).

Что: на ЛОКАЛЬНОЙ фикстуре (`shots_fixture.py --serve` на свободном порту 127.0.0.1, временная база — живые данные не
трогаются, после — гасится):
  - `coach/selfcheck.js` — каждая вкладка и экран, каждая кнопка, листы, крестики, «Назад», ошибки, раскладка;
    телефон 390×844 и ноутбук 1366×768, тёмная и светлая;
  - `common/devcheck.js` (11 размеров, тёмная и светлая) и `common/perfcheck.js` (плавность главной);
  - все `coach/test_*.py` (≤ 4 мин); логи pm2 `coach-bot` с прошлой проверки — Traceback/ERROR (без сетевых сбоев
    Telegram/WHOOP); статус pm2.
Итог — `coach/data/selfcheck/<время>/report.json` + `report.txt`. Чисто — молчит (раз в неделю «✅ Проверил приложение:
всё ок»; по кнопке/команде — сразу). Нашлось — одна ремонтная задача в сутки в 🛠 поддержку (`SUP.web`, тот же Claude,
что /fix) + «🛠 Самопроверка нашла N проблем — чиню»; поддержка занята — через час; ночная находка ждёт 07:00
(ремонт шлёт сообщения — не будить).

  python3 coach/selfcheck.py [папка]   # прогон руками (без Telegram и ремонта) — печатает итог, пишет отчёт
"""
import datetime
import fcntl
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
LOCK = os.environ.get("SELFCHECK_LOCK") or "/tmp/forma-selfcheck.lock"
ROOT = os.environ.get("SELFCHECK_DIR") or os.path.join(HERE, "data", "selfcheck")
AT = (3, 30)  # ночью, по времени владельца
LIMIT = 15 * 60  # весь прогон
CRAWL = 10 * 60  # обходчик кнопок (полный обход на фикстуре — ~9 мин; не успел — остаток в stats.skipped_time)
TESTS = 4 * 60  # все test_*.py
MANUAL_EVERY = 30 * 60  # кнопка/команда — не чаще
RETRY = 3600  # поддержка занята — ещё раз через час
MORNING = 7  # ночная находка уходит в ремонт не раньше 07:00
OK_EVERY = 7 * 86400  # «всё ок» — раз в неделю
KEEP = 7  # отчётов на диске
# сетевые сбои Telegram/WHOOP/Claude — не ошибка кода
NET = re.compile(r"time[d ]?\s?out|timeout|ConnectionReset|Connection (?:reset|refused|aborted)|RemoteDisconnected|"
                 r"URLError|urlopen error|Temporary failure|Name or service|Network is unreachable|IncompleteRead|"
                 r"SSL|HTTP Error (?:429|5\d\d)|Bad Gateway|Service Unavailable|getUpdates|BrokenPipe", re.I)
RUNNING = threading.Event()  # прогон идёт в этом процессе (вдобавок к flock между процессами)


class Busy(Exception):
    """Проверка уже идёт."""


def sh(cmd, timeout, cwd=REPO, env=None):
    """(код, вывод). 124 — не уложилось, 127 — нет программы."""
    try:
        p = subprocess.run(["nice", "-n", "10", *cmd] if shutil.which("nice") else cmd, capture_output=True,
                           text=True, timeout=max(5, timeout), cwd=cwd, env=env)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return 127, f"нет программы {cmd[0]}"
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        return 124, out + "\nне уложилось по времени"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 127.0.0.1 — мимо прокси


class Fixture:
    """`shots_fixture.py --serve` на свободном порту: нейтральные данные, временная база (COACH_DB во временной папке —
    test_coach ставит её сам, здесь — ещё раз на всякий случай), только 127.0.0.1."""

    def __init__(self, timeout=60):
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}/"
        self.tmp = tempfile.mkdtemp(prefix="forma-selfcheck-")
        env = {**os.environ, "COACH_DB": os.path.join(self.tmp, "fixture.db"), "COACH_TOKEN": "123:TEST"}
        for k in ("COACH_ALLOWED", "WHOOP_CLIENT_ID", "WHOOP_CLIENT_SECRET"):
            env.pop(k, None)
        self.proc = subprocess.Popen([sys.executable, os.path.join(HERE, "shots_fixture.py"), "--serve", str(self.port)],
                                     cwd=self.tmp, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     start_new_session=True)
        end = time.time() + timeout
        while time.time() < end:
            if self.proc.poll() is not None:  # порт занят или упала — это не наш сервер
                raise RuntimeError("фикстура не поднялась: " + (self.proc.stdout.read() or b"").decode(errors="replace")[-800:])
            try:
                if LOCAL.open(self.url, timeout=2).status == 200:
                    return
            except OSError:
                time.sleep(0.3)
        self.close()
        raise RuntimeError("фикстура не ответила за минуту")

    def close(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


# ---------- разбор выводов ----------

def parse_devcheck(out, scheme, found=None):
    """Вывод devcheck.js -> проблемы: «✗ Размер WxH» и строки «   - …» под ним. found — дописать в этот список
    (та же беда в тёмной и светлой — одна строка, темы — в configs)."""
    found, dev = [] if found is None else found, None
    for line in out.splitlines():
        m = re.match(r"\s*✗\s+(.+?)\s+(\d+×\d+)\s*$", line)
        if m:
            dev = f"{m.group(1)} {m.group(2)}"
            continue
        if re.match(r"\s*✓", line):
            dev = None
            continue
        m = re.match(r"\s+-\s+(.+)", line)
        if dev and m:
            what = m.group(1).strip()
            old = next((p for p in found if p["what"] == what), None)
            if old:
                if scheme not in old["configs"]:
                    old["configs"].append(scheme)
                if dev not in old["also"] and not old["where"].endswith(dev) and len(old["also"]) < 6:
                    old["also"].append(dev)
                continue
            found.append({"kind": "devcheck", "sev": "error" if what.startswith("ошибка") else "warn",
                          "where": f"Главная · {dev}", "what": what, "configs": [scheme], "also": [],
                          "steps": [f"node common/devcheck.js <фикстура> <папка> --scheme {scheme}"], "shot": ""})
    return found


def parse_logs(text):
    """Новые строки логов -> [(ошибка, сколько раз)] без сетевых сбоев. Traceback — по последней строке."""
    errs, lines, i = {}, text.splitlines(), 0
    while i < len(lines):
        line = lines[i]
        if "Traceback (most recent call last)" in line:
            block = [line]
            i += 1
            while i < len(lines) and (lines[i].startswith((" ", "\t")) or not lines[i].strip()
                                      or lines[i].startswith(("During handling", "The above exception"))
                                      or "Traceback (most recent" in lines[i]):
                block.append(lines[i])
                i += 1
            last = lines[i] if i < len(lines) else block[-1]
            i += 1
            key = last.strip()[:200]
            where = next((b.strip() for b in reversed(block) if b.strip().startswith('File "') and "/coach/" in b), "")
            if key and not NET.search(key):
                e = errs.setdefault(key, {"n": 0, "where": where, "trace": "\n".join(block[-8:] + [last])[-1500:]})
                e["n"] += 1
            continue
        if re.search(r"\bERROR\b|\bCRITICAL\b", line) and not NET.search(line):
            e = errs.setdefault(line.strip()[:200], {"n": 0, "where": "", "trace": ""})
            e["n"] += 1
        i += 1
    return errs


def read_new(path, offsets, first=200_000):
    """Что дописано в файл лога с прошлого раза (ротация — с начала; первый раз — последние first байт)."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            start = offsets.get(path)
            start = max(0, size - first) if start is None or start > size else start
            f.seek(start)
            data = f.read(5_000_000)
        offsets[path] = start + len(data)
        return data.decode(errors="replace")
    except OSError:
        return ""


def pm2_check(state, name="coach-bot"):
    """Статус pm2 и ошибки в логах бота с прошлой проверки -> проблемы."""
    code, out = sh(["pm2", "jlist"], 30)
    if code != 0:
        return [], {"pm2": "нет pm2" if code == 127 else "pm2 не ответил"}
    try:
        apps = json.loads(out[out.find("["):]) if "[" in out else []
    except ValueError:
        return [], {"pm2": "pm2 jlist не разобрался"}
    app = next((a for a in apps if a.get("name") == name), None)
    if not app:
        return [{"kind": "pm2", "sev": "error", "where": "сервер", "what": f"в pm2 нет {name}", "steps": ["pm2 status"],
                 "configs": [], "also": [], "shot": ""}], {"pm2": "нет процесса"}
    env = app.get("pm2_env") or {}
    found, status = [], env.get("status")
    if status != "online":
        found.append({"kind": "pm2", "sev": "error", "where": "сервер", "what": f"{name}: статус {status}",
                      "steps": ["pm2 status"], "configs": [], "also": [], "shot": ""})
    restarts, before = int(env.get("restart_time") or 0), state.get("restarts")
    if before is not None and restarts - int(before) >= 5:  # перезапуски поддержкой/обновлением бывают, 5+ за сутки — падает
        found.append({"kind": "pm2", "sev": "error", "where": "сервер", "what": f"{name} перезапускался {restarts - int(before)} раз с прошлой проверки",
                      "steps": ["pm2 status", f"pm2 logs {name}"], "configs": [], "also": [], "shot": ""})
    state["restarts"] = restarts
    offsets = state.setdefault("offsets", {})
    text = "".join(read_new(p, offsets) for p in (env.get("pm_err_log_path"), env.get("pm_out_log_path")) if p)
    for what, e in sorted(parse_logs(text).items(), key=lambda x: -x[1]["n"])[:8]:
        found.append({"kind": "log", "sev": "error", "where": e["where"] or f"логи {name}", "what": f"{what} (×{e['n']})",
                      "steps": [f"pm2 logs {name} --err"], "trace": e["trace"], "configs": [], "also": [], "shot": ""})
    return found, {"pm2": status, "restarts": restarts}


def run_tests(deadline):
    """Все coach/test_*.py по очереди, пока есть время. -> (проблемы, {файл: код})."""
    found, codes = [], {}
    for t in sorted(f for f in os.listdir(HERE) if re.fullmatch(r"test_\w+\.py", f)):
        left = deadline - time.time()
        if left < 5:
            codes[t] = "нет времени"
            continue
        code, out = sh([sys.executable, os.path.join("coach", t)], left)
        codes[t] = code
        if code != 0:
            tail = "\n".join(x for x in out.strip().splitlines()[-25:])
            found.append({"kind": "tests", "sev": "error", "where": f"coach/{t}", "what": "не уложился по времени" if code == 124
                          else "тесты упали", "steps": [f"python3 coach/{t}"], "trace": tail[-2000:], "configs": [], "also": [], "shot": ""})
    return found, codes


# ---------- прогон ----------

def run(out=None, crawl=CRAWL, limit=LIMIT, server=True):
    """Полная проверка -> отчёт (dict). Busy — уже идёт. server=False — без pm2 (руками на своей машине)."""
    if RUNNING.is_set():
        raise Busy("проверка уже идёт")
    lock = open(LOCK, "a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        raise Busy("проверка уже идёт")
    RUNNING.set()
    t0 = time.time()
    deadline = t0 + limit - 30
    out = out or os.path.join(ROOT, datetime.datetime.now().strftime("%Y-%m-%d-%H%M%S"))
    os.makedirs(out, exist_ok=True)
    rep = {"started": datetime.datetime.now().isoformat(timespec="seconds"), "dir": out, "problems": [], "notes": [], "stats": {}}
    try:
        state = load_state()
        th, box = [], {}
        def tests():  # тесты — параллельно с обходчиком (свои временные базы), но не с замером плавности
            if "tests" not in box:
                box["tests"] = None
                th.append(threading.Thread(target=lambda: box.__setitem__("tests", run_tests(min(deadline, time.time() + TESTS))), daemon=True))
                th[-1].start()
        try:
            with Fixture() as fx:
                # плавность — первой и одна (замер процессора: тесты или обходчик рядом исказили бы его — 04.11 ложное «не плавно»)
                code, perf = sh(["node", "common/perfcheck.js", fx.url], min(150, deadline - time.time()))
                tests()
                rep["stats"]["perf"] = perf.strip()[-600:]
                if code == 1:
                    rep["problems"].append({"kind": "perf", "sev": "warn", "where": "Главная · iPhone 14, CPU ×4",
                                            "what": "не плавно: " + " ".join(perf.split())[-300:], "steps": ["node common/perfcheck.js <фикстура>"],
                                            "configs": [], "also": [], "shot": ""})
                # обходчик, потом обе темы devcheck (браузеры по очереди — на сервере живут боты, процессор общий)
                budget = int(max(60, min(crawl, deadline - time.time() - 180)))
                box["crawl"] = sh(["node", "coach/selfcheck.js", fx.url, out, "--budget", str(budget)], budget + 90)
                jobs = {"dev-dark": ["node", "common/devcheck.js", fx.url, os.path.join(out, "dev-dark"), "--scheme", "dark"],
                        "dev-light": ["node", "common/devcheck.js", fx.url, os.path.join(out, "dev-light"), "--scheme", "light"]}
                for k, cmd in jobs.items():
                    th.append(threading.Thread(target=lambda k=k, cmd=cmd: box.__setitem__(k, sh(cmd, deadline - time.time())), daemon=True))
                    th[-1].start()
                for t in th:
                    t.join(max(1, deadline - time.time()))
        except RuntimeError as e:
            rep["problems"].append({"kind": "fixture", "sev": "error", "where": "фикстура", "what": str(e)[:400],
                                    "steps": ["python3 coach/shots_fixture.py --serve 8790"], "configs": [], "also": [], "shot": ""})
        tests()  # фикстура не поднялась — тесты всё равно прогнать и дождаться
        for t in th:
            t.join(max(1, deadline - time.time()))
        # обходчик
        code, txt = box.get("crawl", (None, ""))
        try:
            crawl_rep = load_json(os.path.join(out, "crawl.json"))
            rep["problems"] += crawl_rep.get("problems", [])
            rep["notes"] += crawl_rep.get("notes", [])
            rep["stats"].update(crawl_rep.get("stats", {}))
        except (OSError, ValueError):
            if "crawl" in box:
                rep["notes"].append("обходчик не отработал: " + txt.strip()[-300:])
        dev = []
        for k in ("dev-dark", "dev-light"):
            if k in box:
                parse_devcheck(box[k][1], k.split("-")[1], dev)
        rep["problems"] += dev
        found, codes = box.get("tests") or ([], {})
        rep["problems"] += found
        rep["stats"]["tests"] = codes
        if server:
            found, info = pm2_check(state)
            rep["problems"] += found
            rep["stats"].update(info)
        save_state(state)
    finally:
        rep["seconds"] = round(time.time() - t0)
        rep["count"] = len(real(rep))
        save(rep, out)
        cleanup()
        RUNNING.clear()
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
    return rep


def real(rep):
    """Проблемы, которые чинить (заметки обходчика — нет)."""
    return [p for p in rep.get("problems", []) if p.get("sev") in ("error", "warn")]


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_state():
    try:
        return load_json(os.path.join(ROOT, "state.json"))
    except (OSError, ValueError):
        return {}


def save_state(state):
    os.makedirs(ROOT, exist_ok=True)
    with open(os.path.join(ROOT, "state.json"), "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


def save(rep, out):
    with open(os.path.join(out, "report.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    with open(os.path.join(out, "report.txt"), "w", encoding="utf-8") as f:
        f.write(text(rep))


def cleanup():
    """Оставить KEEP последних отчётов."""
    try:
        dirs = sorted(d for d in os.listdir(ROOT) if re.fullmatch(r"\d{4}-\d\d-\d\d-\d{6}", d))
    except OSError:
        return
    for d in dirs[:-KEEP]:
        shutil.rmtree(os.path.join(ROOT, d), ignore_errors=True)


def line(p):
    more = f" (и ещё: {', '.join(p['also'][:3])})" if p.get("also") else ""
    cfg = f" [{', '.join(p['configs'])}]" if p.get("configs") else ""
    return f"{p['where']}{more}: {p['what']}{cfg}"


def text(rep):
    """Отчёт для людей и для поддержки."""
    st, probs = rep.get("stats", {}), real(rep)
    head = (f"Самопроверка Формы {rep.get('started', '')}: {len(probs)} проблем, {rep.get('seconds', 0)} с; экранов {st.get('screens', 0)}, "
            f"кнопок {st.get('buttons', 0)}, листов {st.get('sheets', 0)}"
            + (f" (не успел по времени: {st['skipped_time']})" if st.get("skipped_time") else "") + "; тесты: "
            + ", ".join(f"{k}={v}" for k, v in (st.get("tests") or {}).items()))
    body = []
    for i, p in enumerate(probs, 1):
        body.append(f"{i}. [{p['sev']}] {line(p)}")
        if p.get("steps"):
            body.append("   шаги: " + " → ".join(p["steps"]))
        if p.get("shot"):
            body.append("   скриншот: " + p["shot"])
        if p.get("trace"):
            body.append("   " + p["trace"].replace("\n", "\n   "))
    if rep.get("notes"):
        body.append("Заметки (не ошибки приложения): " + "; ".join(rep["notes"][:10]))
    return head + "\n" + "\n".join(body) + "\n"


def task(rep):
    """Текст ремонтной задачи для поддержки (≤ 4000 — SUP.web режет длиннее): кратко + путь к полному отчёту."""
    probs = real(rep)
    head = (f"Самопроверка Формы — чинить только найденное, тесты обязательны.\n"
            f"Ночью/по кнопке я сам обошёл приложение на локальной фикстуре (coach/shots_fixture.py --serve, обходчик coach/selfcheck.js), "
            f"прогнал devcheck/perfcheck, тесты coach/test_*.py и логи pm2. Нашлось {len(probs)}:\n")
    tail = (f"\nПолный отчёт с шагами и скриншотами: {os.path.join(rep['dir'], 'report.txt')} (и report.json) — открой Read, скриншоты тоже. "
            "Почини причины (не обходчик и не фикстуру, если ошибка в приложении); мелкий текст < 11 px и кнопки < 36 px на телефоне — "
            "по правилам Глеба. Ничего сверх списка не меняй. Проверь: python3 coach/test_coach.py и тесты затронутых модулей; "
            "повтор проверки — node coach/selfcheck.js <url фикстуры> <папка> --only phone-dark.")
    room = 3900 - len(head) - len(tail)
    items = []
    for i, p in enumerate(probs, 1):
        s = f"{i}. {line(p)}" + (f" · шаги: {' → '.join(p['steps'][-2:])}" if p.get("steps") else "") + "\n"
        if room - len(s) < 0:
            items.append(f"…и ещё {len(probs) - i + 1} — в отчёте\n")
            break
        room -= len(s)
        items.append(s)
    return head + "".join(items) + tail


# ---------- решение и связь с ботом ----------

def decide(rep, app, now, awake=False):
    """repair — в ремонт; later — поддержка занята или ночь (ремонт шлёт сообщения); quiet — молчать;
    ok — «всё ок» (раз в неделю); limit — ремонт сегодня уже был."""
    if not real(rep):
        return "ok" if time.time() - float(app.db.meta("selfcheck:ok") or 0) >= OK_EVERY else "quiet"
    if app.db.was_sent(f"selfcheck-repair:{now.date()}"):
        return "limit"
    if now.hour < MORNING and not awake:  # ночью сам не будит; попросил сам (кнопка/команда) — сразу
        return "later"
    sup = getattr(app, "SUP", None)
    if not sup or (sup.busy() if hasattr(sup, "busy") else None):
        return "later"
    return "repair"


def owner_now(app):
    uid = app.owner() or min(app.ALLOWED or {0})
    return uid, (app.user_now(app.db.user(uid)) if uid else datetime.datetime.now())


def esc(x):
    return str(x).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def short(rep, n=5):
    return "\n".join(f"• {esc(line(p))[:160]}" for p in real(rep)[:n]) + (f"\n…и ещё {len(real(rep)) - n}" if len(real(rep)) > n else "")


def stats_line(rep):
    st = rep.get("stats", {})
    tests = st.get("tests") or {}
    ok = tests and all(v == 0 for v in tests.values())
    return (f"Экранов {st.get('screens', 0)} · кнопок {st.get('buttons', 0)} · листов {st.get('sheets', 0)} · "
            f"тесты {'✓' if ok else '✗'} · {max(1, round(rep.get('seconds', 0) / 60))} мин")


def act(app, rep, chat=None):
    """Что делать с итогом. chat — кто просил (кнопка/команда): ему ответ всегда."""
    uid, now = owner_now(app)
    if not uid:
        return "quiet"
    what = decide(rep, app, now, awake=bool(chat))
    n = len(real(rep))
    if what == "repair":
        app.db.once(f"selfcheck-repair:{now.date()}")
        app.db.meta("selfcheck:pending", "")
        app.SUP.web(uid, {"text": task(rep)})
        app.send(chat or uid, app.box("🛠", f"Самопроверка нашла {n} {plural(n)} — чиню", short(rep),
                                      "Итог пришлёт поддержка.", expand=True))
    elif what == "later":
        at = time.time() + RETRY
        if now.hour < MORNING:
            at = now.replace(hour=MORNING, minute=0, second=0).timestamp() if now.tzinfo else time.time() + (MORNING - now.hour) * 3600
        app.db.meta("selfcheck:pending", json.dumps({"dir": rep["dir"], "at": at, "tries": 0}))
        if chat:
            app.send(chat, app.box("🛠", f"Самопроверка: {n} {plural(n)}", short(rep),
                                   "Поддержка сейчас занята — передам в ремонт через час.", expand=True))
    elif what == "limit":
        if chat:
            app.send(chat, app.box("⚠️", f"Самопроверка: {n} {plural(n)}", short(rep),
                                   "Ремонт сегодня уже был — ночью проверю снова.", expand=True))
    elif what == "ok" or chat:
        app.db.meta("selfcheck:ok", time.time())
        app.send(chat or uid, app.box("✅", "Проверил приложение: всё ок", esc(stats_line(rep))))
    return what


def plural(n):
    return "проблему" if n % 10 == 1 and n % 100 != 11 else "проблемы" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "проблем"


def retry(app):
    """Отложенный ремонт (поддержка была занята / ночь): тот же отчёт, без нового прогона."""
    raw = app.db.meta("selfcheck:pending")
    if not raw:
        return None
    try:
        p = json.loads(raw)
        rep = load_json(os.path.join(p["dir"], "report.json"))
    except (OSError, ValueError, KeyError, TypeError):
        app.db.meta("selfcheck:pending", "")
        return None
    if time.time() < float(p.get("at") or 0):
        return None
    _, now = owner_now(app)
    what = decide(rep, app, now)
    if what == "later":
        p["tries"] = int(p.get("tries") or 0) + 1
        if p["tries"] > 12:  # полсуток занята — бросаем, ночью будет новая проверка
            app.db.meta("selfcheck:pending", "")
            return "drop"
        p["at"] = time.time() + RETRY
        app.db.meta("selfcheck:pending", json.dumps(p))
        return "later"
    app.db.meta("selfcheck:pending", "")
    return act(app, rep) if what == "repair" else what


def start(app, chat=None, why="night"):
    """Проверка в фоне. -> False, если уже идёт."""
    if RUNNING.is_set():
        return False

    def go():
        try:
            rep = run()
        except Busy:
            return chat and app.send(chat, app.box("⏳", "Проверка уже идёт", note="Пришлю итог, как закончу."))
        except Exception as e:  # сама проверка не должна ронять бота
            print("selfcheck:", why, e)
            return chat and app.send(chat, app.err(f"Самопроверка сломалась: {esc(e)}"))
        print(f"selfcheck ({why}): {rep['count']} проблем, {rep['seconds']} с")
        try:
            act(app, rep, chat)
        except Exception as e:
            print("selfcheck act:", e)

    threading.Thread(target=go, daemon=True).start()
    return True


def manual(app, uid, chat=None):
    """/selfcheck и кнопка «Проверить приложение»: только владелец, не чаще раза в 30 мин."""
    if not app.is_admin(uid):
        raise PermissionError("Проверка приложения — только для владельца")
    if RUNNING.is_set():
        return {"ok": True, "running": True, "text": "Уже проверяю — пришлю итог в чат"}
    last = float(app.db.meta("selfcheck:manual") or 0)
    if time.time() - last < MANUAL_EVERY:
        wait = int((MANUAL_EVERY - (time.time() - last)) // 60) + 1
        raise ValueError(f"Недавно проверял — следующая через {wait} мин")
    app.db.meta("selfcheck:manual", time.time())
    start(app, chat or uid, "manual")
    return {"ok": True, "running": True, "text": "Проверяю приложение — минут 10, итог пришлю в чат"}


def tick(app):
    """Из coach.tick (раз в минуту): ночная проверка и отложенный ремонт."""
    if not getattr(app, "SUP", None):  # бот не запущен по-настоящему (тесты, импорт) — ни прогона, ни ремонта
        return None
    uid, now = owner_now(app)
    if not uid:
        return None
    if (now.hour, now.minute) >= AT and now.hour < AT[0] + 2 and app.db.once(f"selfcheck:{now.date()}"):
        return "start" if start(app, why="night") else "busy"
    if not RUNNING.is_set():
        return retry(app)
    return None


if __name__ == "__main__":
    r = run(sys.argv[1] if len(sys.argv) > 1 else None, server=shutil.which("pm2") is not None)
    print(text(r))
    print("Отчёт:", os.path.join(r["dir"], "report.txt"))
