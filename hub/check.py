"""🧪 Проверка всех ботов (и Главного тоже) по кнопке — от А до Я и как можно быстрее, всё параллельно.

⚡ Быстро (1–3 мин): у каждого бота — pm2 (работает? падал с прошлой проверки?), новые ошибки в его логе
   (Traceback с прошлой проверки), все его тесты: юнит-тесты и test_buttons.py (обход всех кнопок без сети).
🔬 Полностью: то же + живой обход всех кнопок в Telegram через Секретаря (tgbot/botcrawl.py с аккаунта Глеба;
   опасные кнопки — отправить, заказать, создать, «🧪 Проверка»… — не жмутся, Студия сайтов не трогается).
Итог — одна карточка: что в порядке, что сломано; у каждого сломанного «🛠 Починить» (Claude, maker.fix) и «Починить всё».
Подключается из hub.py: check.ui = модуль hub.
"""
import glob
import json
import os
import re
import subprocess
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

ui = None
maker = None
TEST_TIMEOUT = 420
CRAWL = dict(depth=3, max=60, wait=6)
PARALLEL_TESTS = 3  # у сервера 1 ядро: больше — только толкотня
PARALLEL_CRAWL = 4
# тяжёлые тесты почерка HDP (~5 мин на одном ядре сервера) — только в «🔬 Полностью»
SLOW = {"horo/test_alphabet.py", "horo/test_sheet.py", "horo/test_table.py"}
COMMON = "common"  # общие модули (kit, поддержка, медиа) — проверяются отдельной строкой
running = threading.Lock()
TRACE = re.compile(r"^Traceback \(most recent call last\):", re.M)
EXC = re.compile(r"^\s*([\w.]*(?:Error|Exception|Interrupt|Exit)\b.*)$", re.M)
# так бот останавливает pm2 (перезапуск, «Обновить всех») — не ошибка
NOT_BUGS = re.compile(r"^(?:KeyboardInterrupt|SystemExit|asyncio\.exceptions\.CancelledError|concurrent\.futures\._base\.CancelledError)\b")
TOKEN = re.compile(r"\d{6,12}:[A-Za-z0-9_-]{20,}")
# тестам не видно секретов: токены ботов и список ботов Студии закрыты пустым файлом (свой mount namespace)
SECRETS = "/etc/*.env /root/bots/*/.env /root/bots/*/data/*.env /root/bots/registry.json"


# ---------- что проверять ----------

def jlist():
    code, out = ui.sh("pm2", "jlist", timeout=30)
    try:
        return json.loads(out[out.find("["):]) if code == 0 else []
    except ValueError:
        return []


def python_of(proc):
    """Интерпретатор бота из pm2 (у кого свой .venv — тот же и для тестов), иначе python3."""
    env = proc.get("pm2_env") or {}
    py = env.get("exec_interpreter") or env.get("interpreter") or ""
    return py if "python" in os.path.basename(py) and (os.path.isabs(py) is False or os.path.exists(py)) else "python3"


def targets(procs=None):
    """[{pm2, label, folders, cwd, python, username?}] — все боты из каталога, что стоят в pm2, и сделанные здесь."""
    procs = jlist() if procs is None else procs
    by = {p.get("name"): p for p in procs}
    res = []
    for name, v in maker.CATALOG.items():
        if name in by:
            res.append({"pm2": name, "label": v[0], "folders": v[1], "cwd": ui.REPO, "python": python_of(by[name])})
    for slug, info in maker.made().items():
        if info.get("pm2") in by and info.get("dir"):
            res.append({"pm2": info["pm2"], "label": "🧩 " + info.get("name", slug), "folders": ["."],
                        "cwd": info["dir"], "python": python_of(by[info["pm2"]])})
    return res


def tests_of(t):
    """Команды тестов бота: все <папка>/test_*.py. Для tgbot и обхода Главного — venv Секретаря (telethon), если есть."""
    venv = os.path.join(ui.REPO, "tgbot", ".venv", "bin", "python")
    cmds = []
    for folder in t["folders"]:
        for path in sorted(glob.glob(os.path.join(t["cwd"], folder, "test_*.py"))):
            rel = os.path.relpath(path, t["cwd"])
            py = t["python"]
            if (folder == "tgbot" or rel == os.path.join("hub", "test_buttons.py")) and os.path.exists(venv):
                py = venv
            cmds.append((rel, [py, rel]))
    return cmds


# ---------- части проверки ----------

def mask(text):
    return TOKEN.sub("‹токен›", text or "")


_sandbox = []


def sandboxed(cmd):
    """Команда теста в своём mount namespace, где /etc/*.env и т. п. — пустые: тест не прочитает настоящие токены
    и не зависит от того, какие боты стоят на сервере. Нет unshare/прав — как есть. Плюс nice: боты важнее."""
    if not _sandbox:
        try:
            ok = subprocess.run(["unshare", "-m", "--propagation", "private", "true"], capture_output=True,
                                timeout=10).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            ok = False
        _sandbox.append(ok)
    if not _sandbox[0]:
        return ["nice", "-n", "10", *cmd]
    hide = f'for f in {SECRETS}; do [ -f "$f" ] && mount --bind /dev/null "$f"; done; exec nice -n 10 "$@"'
    return ["unshare", "-m", "--propagation", "private", "sh", "-c", hide, "sh", *cmd]


def worktree():
    """Чистая копия кода (HEAD, с локальными правками «Доработать») — тесты не трогают живые данные ботов
    (horo/tmp, */data — их в копии нет) и не мешают друг другу. Не вышло — тесты идут в самом репозитории."""
    path = os.path.join(ui.DATA, "check", "src")
    ui.sh("git", "-C", ui.REPO, "worktree", "remove", "--force", path, timeout=60)
    ui.sh("rm", "-rf", path, timeout=60)
    ui.sh("git", "-C", ui.REPO, "worktree", "prune", timeout=60)
    code, _ = ui.sh("git", "-C", ui.REPO, "worktree", "add", "--detach", "--force", path, "HEAD", timeout=120)
    return path if code == 0 and os.path.isdir(path) else None


def drop_worktree(path):
    if path:
        ui.sh("git", "-C", ui.REPO, "worktree", "remove", "--force", path, timeout=60)
        ui.sh("git", "-C", ui.REPO, "worktree", "prune", timeout=60)


def TEST_ENV():
    """Окружение тестов без токенов и ключей Главного: тест не должен достучаться до настоящего Telegram."""
    keep = ("PATH", "LANG", "LC_ALL", "TZ", "PLAYWRIGHT_BROWSERS_PATH", "NODE_PATH")
    return {**{k: os.environ[k] for k in keep if k in os.environ}, "HOME": "/root", "PYTHONDONTWRITEBYTECODE": "1"}


def run_test(cwd, cmd):
    """(ok, хвост вывода) одного файла тестов."""
    try:
        p = subprocess.run(sandboxed(cmd), cwd=cwd, capture_output=True, text=True, timeout=TEST_TIMEOUT,
                           stdin=subprocess.DEVNULL, env=TEST_ENV())
    except subprocess.TimeoutExpired:
        return False, f"не уложился в {TEST_TIMEOUT // 60} мин"
    except OSError as e:
        return False, f"не запустился: {e}"
    out = (p.stdout or "") + (p.stderr or "")
    return p.returncode == 0, out


def failure(out, limit=500):
    """Суть упавшего теста: заголовки FAIL/ERROR и последняя строка с исключением — без простыни."""
    heads = re.findall(r"^(?:FAIL|ERROR): (\S+)", out, re.M)
    exc = EXC.findall(out)
    msg = (", ".join(dict.fromkeys(heads[:4])) + (": " if heads else "")) + (exc[-1].strip() if exc else
                                                                               out.strip().splitlines()[-1] if out.strip() else "")
    return mask(msg)[:limit]


def log_errors(proc, seen):
    """Новые ошибки в логе бота с прошлой проверки: (сколько Traceback, последняя ошибка). seen — {путь: размер}."""
    path = (proc.get("pm2_env") or {}).get("pm_err_log_path")
    if not path or not os.path.exists(path):
        return 0, ""
    size = os.path.getsize(path)
    start = seen.get(path)
    if start is None or start > size:  # первый раз или лог обрезали — только свежее (сутки, до 64 КБ)
        start = max(0, size - 65536) if time.time() - os.path.getmtime(path) < 86400 else size
    seen[path] = size
    try:
        with open(path, "rb") as f:
            f.seek(start)
            text = f.read().decode("utf-8", "replace")
    except OSError:
        return 0, ""
    errs = []
    for block in TRACE.split(text)[1:]:  # каждый Traceback: итог — первая строка без отступа
        last = next((ln.strip() for ln in block.splitlines() if ln.strip() and not ln[:1].isspace()), "")
        if not NOT_BUGS.match(last):
            errs.append(last)
    return len(errs), mask(errs[-1])[:240] if errs else ""


def crawl(username, out_path, on_line=None):
    """Живой обход кнопок бота через Секретаря. → {"clicks", "problems": [строки], "fail"?}."""
    py = os.path.join(ui.REPO, "tgbot", ".venv", "bin", "python")
    py = py if os.path.exists(py) else "python3"
    cmd = [py, "tgbot/botcrawl.py", username, "--depth", str(CRAWL["depth"]), "--max", str(CRAWL["max"]),
           "--wait", str(CRAWL["wait"]), "--out", out_path]
    try:
        p = subprocess.Popen(cmd, cwd=ui.REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             stdin=subprocess.DEVNULL, env={**os.environ, "HOME": "/root",
                                                            "PYTHONPATH": os.path.join(ui.REPO, "tgbot")})
    except OSError as e:
        return {"fail": str(e)}
    tail = []
    deadline = time.time() + 20 * 60
    for line in p.stdout:
        tail = (tail + [line.rstrip()])[-20:]
        if on_line and line[:1] in "✅❌":
            on_line(line)
        if time.time() > deadline:
            p.kill()
            break
    p.wait()
    try:
        with open(out_path) as f:
            rep = json.load(f)
    except (OSError, ValueError):
        return {"fail": "обход не закончился: " + " / ".join(tail[-3:])[:300]}
    probs = [f"{e['path']}: {'; '.join(e['problems'])}" for e in rep.get("log", []) if e.get("problems")]
    return {"clicks": rep.get("clicks", 0), "problems": probs, "secs": rep.get("secs")}


# ---------- весь прогон ----------

class Live:
    """Одна карточка «⏳ Проверяю…», которая правится на месте не чаще раза в 4 с."""

    def __init__(self, chat, mid):
        self.chat, self.mid, self.at, self.lock = chat, mid, 0, threading.Lock()
        self.tests_done = self.tests_all = self.clicks = 0
        self.crawling = {}

    def show(self, force=False):
        with self.lock:
            if not force and time.time() - self.at < 4:
                return
            self.at = time.time()
            lines = [f"🧪 Тесты: {self.tests_done} из {self.tests_all}"]
            if self.crawling:
                lines.append(f"👆 Нажато кнопок: {self.clicks} · сейчас: " + ", ".join(
                    ui.esc(ui.short(x)) for x in list(self.crawling.values())[:4]))
        try:
            if self.mid:
                ui.edit(self.chat, self.mid, ui.card("⏳ <b>Проверяю всех ботов</b>", lines))
            else:
                r = ui.send(self.chat, ui.card("⏳ <b>Проверяю всех ботов</b>", lines))
                self.mid = ((r or {}).get("result") or {}).get("message_id")
        except Exception:
            pass


def check_all(full=False, live=None, now=time.time):
    """Прогон. → {"at", "full", "secs", "bots": [{pm2, label, ok, problems: [..], tests: "k/n", clicks}]}."""
    t0 = now()
    procs = jlist()
    by = {p.get("name"): p for p in procs}
    bots = targets(procs)
    state = {b["pm2"]: {"pm2": b["pm2"], "label": b["label"], "problems": [], "tests": [0, 0], "clicks": None}
             for b in bots}
    seen = ui.setting("check_logs") or {}
    restarts = ui.setting("check_restarts") or {}
    # 1. pm2 и логи — мгновенно
    for b in bots:
        s, p = state[b["pm2"]], by[b["pm2"]]
        env = p.get("pm2_env") or {}
        if env.get("status") != "online":
            s["problems"].append(f"не работает (pm2: {env.get('status')})")
        r = env.get("restart_time") or 0
        if b["pm2"] in restarts and r > restarts[b["pm2"]]:
            s["problems"].append(f"перезапускался {r - restarts[b['pm2']]} раз с прошлой проверки")
        restarts[b["pm2"]] = r
        n, last = log_errors(p, seen)
        if n:
            s["problems"].append(f"в логе {n} ошибок" + (f", последняя: {last}" if last else ""))
    ui.setting("check_logs", seen)
    ui.setting("check_restarts", restarts)
    # 2. тесты (и общих модулей) параллельно; 3. живой обход — параллельно с ними
    src = worktree()
    base = src or ui.REPO
    for b in bots:
        if b["cwd"] == ui.REPO:
            b["cwd"] = base
    jobs = [(b["pm2"], b["cwd"], rel, cmd) for b in bots for rel, cmd in tests_of(b) if full or rel not in SLOW]
    common = [(COMMON, base, os.path.relpath(p, base), ["python3", os.path.relpath(p, base)])
              for p in sorted(glob.glob(os.path.join(base, COMMON, "test_*.py")))]
    if common:
        state[COMMON] = {"pm2": COMMON, "label": "🧱 Общие модули", "problems": [], "tests": [0, 0], "clicks": None}
    jobs += common
    for pm2, *_ in jobs:
        state[pm2]["tests"][1] += 1
    if live:
        live.tests_all = len(jobs)
        live.show(force=True)

    mu = threading.Lock()

    def one_test(job):
        pm2, cwd, rel, cmd = job
        ok, out = run_test(cwd, cmd)
        with mu:
            state[pm2]["tests"][0] += ok
            if not ok:
                state[pm2]["problems"].append(f"тест {rel}: {failure(out)}")
            if live:
                live.tests_done += 1
        if live:
            live.show()

    def chain(group):  # тесты одной папки делят её файлы — по очереди
        for j in group:
            one_test(j)

    groups = {}
    for j in jobs:
        groups.setdefault((j[1], os.path.dirname(j[2])), []).append(j)

    def one_crawl(b):
        u = b.get("username")
        if live:
            live.crawling[b["pm2"]] = b["label"]
        out = os.path.join(ui.DATA, "check", f"crawl_{b['pm2']}.json")
        os.makedirs(os.path.dirname(out), exist_ok=True)

        def tick(line):
            if live:
                with mu:
                    live.clicks += 1
                live.show()
        try:
            res = crawl(u, out, tick)
        finally:
            if live:
                live.crawling.pop(b["pm2"], None)
        s = state[b["pm2"]]
        if res.get("fail"):
            s["problems"].append("живой обход: " + res["fail"])
        else:
            s["clicks"] = res["clicks"]
            s["problems"] += [("кнопка " + x)[:300] for x in res["problems"]]

    crawls = []
    if full:
        names = {r["pm2"]: r.get("username") for r in maker.all_bots([{"name": k, "status": "online"} for k in by])}
        for b in bots:
            if names.get(b["pm2"]) and b["pm2"] != "tg-agent":  # Секретарь — часть Главного: его кнопки проходят через него
                crawls.append({**b, "username": names[b["pm2"]]})
        # Главный — последним: его чат с Глебом тот же, где идёт проверка
        crawls.sort(key=lambda b: b["pm2"] == ui.SELF)
    try:
        with ThreadPoolExecutor(PARALLEL_TESTS) as tp, ThreadPoolExecutor(PARALLEL_CRAWL) as cp:
            futs = [tp.submit(chain, g) for g in groups.values()] + [cp.submit(one_crawl, b) for b in crawls]
            for f in futs:
                try:
                    f.result()
                except Exception as e:
                    traceback.print_exc()
                    print("проверка:", e, file=sys.stderr)
    finally:
        drop_worktree(src)
    res = {"at": now(), "full": full, "secs": round(now() - t0), "bots": []}
    for s in state.values():
        res["bots"].append({**s, "ok": not s["problems"], "tests": f"{s['tests'][0]}/{s['tests'][1]}"})
    ui.setting("check_last", res)
    return res


# ---------- экраны ----------

def ask(chat, mid=None):
    """Выбор: ⚡ быстро (тесты, логи, pm2) или 🔬 полностью (+ живой обход всех кнопок через аккаунт)."""
    last = ui.setting("check_last")
    foot = ""
    if last:
        bad = sum(1 for x in last["bots"] if not x["ok"])
        foot = (f"<i>Прошлая: {time.strftime('%d.%m %H:%M', time.localtime(last['at']))} · "
                + (f"проблемы у {bad}" if bad else "всё в порядке") + "</i>")
    lines = ["⚡ Быстро — ~3 мин: работают ли, логи, все тесты и кнопки без сети",
             "🔬 Полностью — 10–15 мин: + все кнопки вживую с твоего аккаунта (опасные не жму)"]
    btns = [[ui.b("⚡ Быстро", "chk|run", "primary"), ui.b("🔬 Полностью", "chk|full")]]
    if last:
        btns.append([ui.b("📋 Прошлый отчёт", "chk|last")])
    btns.append([ui.b("‹ Главная", "home")])
    return ui.show(chat, mid, ui.card("🧪 <b>Проверка ботов</b>", lines, foot), btns)


KINDS = [("не работает", "не работает"), ("перезапускался", "падал"), ("в логе", "ошибки в логе"),
         ("тест ", "тесты"), ("кнопка ", "кнопки"), ("живой обход", "обход")]


def gist(problems):
    """«тесты 2 · кнопки 1» — что сломано, без подробностей (они — в «📋 Подробно» и в задаче для Claude)."""
    n = {}
    for p in problems:
        k = next((name for pre, name in KINDS if p.startswith(pre)), "другое")
        n[k] = n.get(k, 0) + 1
    return " · ".join(k + (f" {v}" if v > 1 else "") for k, v in n.items())


def took(secs):
    return f"{secs // 60} мин {secs % 60} с" if secs >= 60 else f"{secs} с"


def report(res, details=False):
    """(текст, кнопки) — коротко: всё ли в порядке и у кого что сломано одной строкой. details — с подробностями."""
    bad = [x for x in res["bots"] if not x["ok"]]
    if not bad:
        text = f"✅ <b>Все {len(res['bots'])} в порядке</b> · {took(res['secs'])}"
    else:
        lines = []
        for x in bad:
            lines.append(f"❌ {ui.esc(ui.short(x['label']))} — {gist(x['problems'])}")
            if details:
                lines += ["<i>" + ui.esc(p[:200]) + "</i>" for p in x["problems"][:5]]
        text = ui.card(f"🧪 <b>Сломано {len(bad)} из {len(res['bots'])}</b> · {took(res['secs'])}", lines,
                       expand=details)
        while len(text) > 4000 and lines:
            lines = lines[:-1]
            text = ui.card(f"🧪 <b>Сломано {len(bad)} из {len(res['bots'])}</b>", lines, expand=True)
    btns = ui.pairs([ui.b(ui.clip("🛠 " + ui.short(x["label"])), f"chk|fix|{x['pm2']}")
                     for x in bad if x["pm2"] != COMMON], 3)
    tail = [ui.b("🛠 Починить всё", "chk|fixall", "primary")] if len(bad) > 1 else []
    if bad and not details:
        tail.append(ui.b("📋 Подробно", "chk|more"))
    if tail:
        btns.append(tail)
    btns.append([ui.b("🔁 Ещё раз", "chk")])
    return text, btns


def run(chat, mid=None, full=False):
    if not running.acquire(blocking=False):
        return ui.send(chat, "⏳ Проверка уже идёт — отчёт придёт сюда.")
    try:
        if full and not (ui.secretary_status() or {}).get("account"):
            ui.send(chat, ui.card("ℹ️ <b>Живой обход недоступен</b>",
                                  ["Секретарь не вошёл в аккаунт — проверяю быстро: тесты, логи и pm2."]))
            full = False
        live = Live(chat, mid)
        res = check_all(full, live)
        if live.mid:  # вместо «⏳ Проверяю…» — отчёт новым сообщением (с уведомлением)
            try:
                ui.tg("deleteMessage", chat_id=chat, message_id=live.mid)
            except Exception:
                pass
        ui.send(chat, *report(res))
    except Exception as e:
        traceback.print_exc()
        ui.send(chat, ui.card("⚠️ <b>Проверка упала</b>", [ui.esc(str(e)[:300])]), [[ui.b("🔁 Ещё раз", "chk")]])
    finally:
        running.release()


FIX_TZ = ("Автопроверка («🧪 Проверка ботов» в Главном) нашла проблемы у этого бота. Найди причину каждой и почини "
          "(тест падает из-за бага — чини код, а не тест; тест устарел — обнови его). Проблемы:\n")


def fix_one(chat, pm2):
    last = ui.setting("check_last") or {}
    x = next((b for b in last.get("bots", []) if b["pm2"] == pm2), None)
    if not x or x["ok"]:
        return ui.send(chat, "Проблем у этого бота в прошлой проверке нет — запусти проверку ещё раз.")
    maker.fix(chat, pm2, FIX_TZ + "\n".join("- " + p for p in x["problems"]))


def fix_all(chat):
    last = ui.setting("check_last") or {}
    for x in last.get("bots", []):
        if not x["ok"] and x["pm2"] != COMMON:
            fix_one(chat, x["pm2"])  # по одному: Claude работает над одним ботом за раз
    ui.send(chat, ui.card("🛠 <b>Починка закончена</b>", ["Проверю ещё раз, что всё зелёное?"]),
            [[ui.b("⚡ Проверить", "chk|run", "primary")]])


def on_callback(chat, mid, data):
    """chk, chk|run, chk|full, chk|last, chk|fix|<pm2>, chk|fixall → текст всплывашки."""
    arg = data[1] if len(data) > 1 else ""
    if not arg:
        ask(chat, mid)
        return ""
    if arg in ("run", "full"):
        if running.locked():
            return "Проверка уже идёт"
        threading.Thread(target=run, args=(chat, mid, arg == "full"), daemon=True).start()
        return "Проверяю…"
    if arg in ("last", "more"):
        last = ui.setting("check_last")
        if not last:
            return "Отчёта ещё нет"
        ui.show(chat, mid, *report(last, details=arg == "more"))
        return ""
    if arg == "fix" and len(data) > 2:
        threading.Thread(target=fix_one, args=(chat, data[2]), daemon=True).start()
        return "Чиню…"
    if arg == "fixall":
        threading.Thread(target=fix_all, args=(chat,), daemon=True).start()
        return "Чиню по очереди…"
    return None
