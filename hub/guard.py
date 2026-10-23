"""🛡 Страж HDP — отдельный постоянный надзор за HDP-ботом (он чаще всех падает и ломается).

Каждую минуту (тихо, без сети и без Claude):
  • pm2: не работает → перезапуск; падает по кругу → ошибка;
  • «пульс» цикла приёма сообщений (horo/tmp/heartbeat) старше 10 мин → бот завис → перезапуск;
  • его сервер (127.0.0.1:8788/health — приложение и сайт) не отвечает 2 мин → перезапуск, ещё 3 мин → Claude чинит
    сразу (Глеб 19.10: «если сайт упал — автоматом поднимает сразу же Claude»), даже если HDP занят;
  • сайт снаружи (https://hdp.<ip>.sslip.io/health, через Caddy планера) не открывается, а сам HDP жив → перезапуск
    planner-bot (он держит Caddy), не помогло — Claude чинит planner-bot;
  • журнал ошибок HDP (horo/tmp/journal.jsonl: упавший код с трассировкой, скрипты сайта, Claude, сеть) и
    Traceback в логе pm2;
  • зависшие дела: задание решается дольше 45 мин, тест не сдан в срок, ответ учителю не ушёл в срок.
Раз в 2 часа (и по кнопке) — глубокая проверка: вход на сайт, Claude отвечает, тесты HDP.
🪞 Дозор зеркала (Глеб 23.10: «часто проверяет работу сайта-зеркала и сразу чинит»): каждые 5 мин bot/mirrorwatch.py
заходит в зеркало hdp как Глеб (вход, страница с кнопками бота, файлы кнопок, API, данные == платформа); раз в 6 ч —
полный обход сайта браузером (bot/sitecheck.py). Ошибка повторилась (лёгкая — 2 проверки подряд, обход — сразу) —
Claude чинит зеркало (MIRROR_TZ), как и прочие баги: когда HDP свободен.

Нашёл баг — чинит сам: Claude (maker.fix) правит код HDP, гоняет тесты, перезапускает; только когда HDP ничего
не решает и не сдаёт, не чаще раза в 12 ч на одну ошибку и 4 раз в сутки. Сеть, лимит Claude, вход на сайт —
не баги кода: только сказать. Пишет Глебу коротко и только о новом. Подключается из hub.py: guard.ui = модуль hub.
"""
import json
import os
import re
import signal
import subprocess
import threading
import time
import traceback
import urllib.error
import urllib.request

ui = None
maker = None
check = None

PM2 = "horo-bot"
CADDY = "planner-bot"  # держит Caddy: https-адреса всех ботов, и hdp.<ip>.sslip.io тоже
EVERY = 60
DEEP_EVERY = 2 * 3600
HANG = 10 * 60           # «пульс» старше — завис (getUpdates ждёт до 60 с; обработка сообщения — не дольше минут)
WARMUP = 3 * 60          # сразу после старта не судим: бот ещё поднимается
STUCK = 45 * 60          # задание решается дольше (у HDP предел 20 мин на попытку)
LATE = 15 * 60           # тест или ответ учителю опоздал на столько — цикл отправки встал
RESTARTS_PER_HOUR = 3
FIX_PER_DAY = 4
FIX_AGAIN = 12 * 3600    # одну и ту же ошибку чиним не чаще
TELL_AGAIN = 12 * 3600   # и напоминаем о ней не чаще
FRESH = 6 * 3600         # чиним и напоминаем, только если ошибка была недавно: дольше не повторялась — уже починена
                         # (16.10 нарочные сбои тестов из журнала ждали в очереди сутки, и Claude звали чинить призрак)
KEEP = 3 * 86400         # закрытые ошибки и события храним столько
WEB = "http://127.0.0.1:8788/health"
URGENT_AGAIN = 3600      # сайт лежит: Claude зовём снова не раньше чем через час (а не через 12 ч)
MIRROR_EVERY = 5 * 60    # 🪞 Дозор зеркала: лёгкая проверка сайта hdp (bot/mirrorwatch.py, ~3 с)
MIRROR_AZ_EVERY = 6 * 3600  # и полный обход сайта браузером (bot/sitecheck.py, до 30 мин) — когда HDP свободен
AZ = "сайт А–Я: "         # начало текста ошибок полного обхода (лёгкая проверка пишет «зеркало: …»)

lock = threading.RLock()  # hub.py ставит сюда свой lock: state.json один на всех
deep_lock = threading.Lock()
mirror_lock = threading.Lock()
az_lock = threading.Lock()  # полный обход идёт до 30 мин — лёгкие проверки в это время не ждут
# не баги кода — чинить нечего, только сказать (и то если повторяется)
NOT_CODE = re.compile(r"timed? ?out|timeout|не уложился|network|unreachable|econn|enotfound|temporary failure|"
                      r"name resolution|connection (reset|refused|aborted)|bad gateway|50[234]|429|rate limit|"
                      r"usage limit|limit reached|overloaded|не вош|login|логин|unauthori|401|403|"
                      r"прерван сигналом", re.I)
KIND_ICON = {"bug": "🐞", "script": "🌐", "claude": "🤖", "net": "📡", "down": "🔴", "hang": "🧊", "web": "🕸",
             "crash": "🟠", "stuck": "⏳", "deep": "🔍", "log": "📜", "mirror": "🪞"}


def tmp(*p):
    return os.path.join(ui.REPO, "horo", "tmp", *p)


def sig_of(kind, text):
    """Подпись ошибки: без чисел, uuid и ссылок — одна и та же ошибка в разных заданиях одинакова."""
    t = re.sub(r"https?://\S+|[0-9a-f]{8}-[0-9a-f-]{27,}|\d+", "#", text or "")
    return (kind + ":" + re.sub(r"\s+", " ", t).strip())[:120]


def state(st):
    g = st.setdefault("guard", {})
    g.setdefault("auto", True)
    g.setdefault("issues", {})
    g.setdefault("events", [])
    g.setdefault("restarts", [])
    g.setdefault("fixes", [])
    g.setdefault("seen", {})
    return g


def event(g, now, kind, text):
    g["events"] = [e for e in g["events"] if now - e[0] < KEEP][-59:] + [[round(now), kind, text[:200]]]


def raise_issue(g, now, kind, text, detail="", fixable=True):
    """Ошибка увидена (новая или повтор). → запись ошибки."""
    s = sig_of(kind, text)
    it = g["issues"].get(s)
    if not it or it.get("closed"):
        it = {"sig": s, "kind": kind, "text": text[:300], "first": round(now), "count": 0}
        g["issues"][s] = it
        event(g, now, kind, text)
    it.update(last=round(now), count=it["count"] + 1, fixable=fixable, closed=False)
    if detail:
        it["detail"] = detail[-3000:]
    if it.get("fixed_at") and now - it["fixed_at"] > 120:
        it["after_fix"] = True  # повторилась после починки
    return it


def close(g, kind):
    """Состояние-ошибка (не работает, завис, не отвечает…) прошла сама."""
    for it in g["issues"].values():
        if it["kind"] == kind and not it.get("closed"):
            it["closed"] = True


# ---------- что видно каждую минуту ----------

def proc_of(procs):
    return next((p for p in procs or [] if p.get("name") == PM2), None)


def site_url():
    ip = os.environ.get("HUB_IP") or (ui.server_ip() if hasattr(ui, "server_ip") else None)
    return f"https://hdp.{ip.replace('.', '-')}.sslip.io/health" if ip else ""


def site_ok():
    """Сайт HDP снаружи (через Caddy): True / False, None — адрес неизвестен (не судим)."""
    url = site_url()
    if not url:
        return None
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return r.status < 500
    except urllib.error.HTTPError as e:
        return e.code < 500
    except Exception:
        return False


def heartbeat_age(now):
    try:
        return now - os.path.getmtime(tmp("heartbeat"))
    except OSError:
        return None  # старый код HDP без «пульса» — не судим


def web_ok():
    try:
        with urllib.request.urlopen(WEB, timeout=10) as r:
            return r.status < 500
    except urllib.error.HTTPError as e:
        return e.code < 500
    except Exception:
        return False


def read_journal(g):
    """Новые строки журнала HDP с прошлого раза (журнал переименовывается в .1 на 512 КБ — начинаем сначала)."""
    path = tmp("journal.jsonl")
    try:
        st = os.stat(path)
    except OSError:
        return []
    pos = g["seen"].get("journal")
    if pos is None or pos > st.st_size or g["seen"].get("journal_ino") != st.st_ino:
        pos = 0 if pos is not None or time.time() - st.st_mtime < 600 else st.st_size  # первый раз — без старого
    g["seen"]["journal_ino"] = st.st_ino
    try:
        with open(path, "rb") as f:
            f.seek(pos)
            data = f.read(1 << 20)
    except OSError:
        return []
    end = data.rfind(b"\n") + 1
    g["seen"]["journal"] = pos + end
    out = []
    for line in data[:end].decode("utf-8", "replace").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def hdp_settings():
    try:
        with open(ui.HDP_SETTINGS) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def stuck(s, now):
    """[(текст, подробности)] — что в HDP зависло: задание, сдача теста, отправка учителю."""
    res = []
    for task, a in (s.get("active") or {}).items():
        if a.get("since") and now - a["since"] > STUCK:
            res.append((f"задание решается {round((now - a['since']) / 60)} мин", f"active[{task}] = {json.dumps(a, ensure_ascii=False)[:500]}"))
    for task, t in (s.get("tests") or {}).items():
        if t.get("at") and now - t["at"] > LATE:
            res.append((f"тест не сдан в срок ({t.get('title') or task[:8]})", json.dumps(t, ensure_ascii=False)[:500]))
    for task, d in (s.get("drafts") or {}).items():
        if isinstance(d, dict) and d.get("send_at") and now - d["send_at"] > LATE:
            res.append((f"ответ учителю не ушёл в срок ({d.get('title') or task[:8]})", ""))
    return res


def tick(now=None, procs=None):
    """Одна минутная проверка. → [(текст, кнопки)] для Глеба и [действия] (restart, fix: sig) — их делает loop()."""
    now = now or time.time()
    procs = check.jlist() if procs is None else procs
    acts = []
    # медленное (сеть, файлы HDP) — до блокировки: state.json Главного не держим занятым
    web = web_ok()
    site = site_ok() if web else None
    age = heartbeat_age(now)
    jams = stuck(hdp_settings(), now)
    logs = []

    def tail():
        if not logs:
            logs.append(ui.log_text(PM2, 80, 3000))
        return logs[0]
    with lock:
        st = ui.load()
        g = state(st)
        p = proc_of(procs)
        if p is None:
            if procs:  # pm2 ответил, а HDP в нём нет — не стоит
                raise_issue(g, now, "down", "HDP нет в pm2", fixable=False)
            ui.save(st)
            return acts
        env = p.get("pm2_env") or {}
        online = env.get("status") == "online"
        up = now - (env.get("pm_uptime") or now * 1000) / 1000 if online else 0
        busy_update = ui.updating()
        # 1. не работает
        if not online and not busy_update:
            g["down"] = g.get("down", 0) + 1
            if g["down"] >= 2:
                raise_issue(g, now, "down", f"не работает (pm2: {env.get('status')})", fixable=False)
                acts.append(("restart", "не работал"))
        else:
            g["down"] = 0
            close(g, "down")
        # 2. падает по кругу: перезапуски не по нашей команде (код выхода не 0)
        r = env.get("restart_time") or 0
        last_r = g["seen"].get("restarts", r)
        if r > last_r and env.get("exit_code") not in (0, None) and not busy_update:
            g.setdefault("crash", [])
            g["crash"] = [t for t in g["crash"] if now - t < 600] + [now] * (r - last_r)
            if len(g["crash"]) >= 3:
                tail = tail()
                raise_issue(g, now, "crash", f"падает: {len(g['crash'])} раз за 10 мин", tail)
        g["seen"]["restarts"] = r
        if online and up > WARMUP and not busy_update:
            # 3. завис
            if age is not None and age > HANG:
                it = raise_issue(g, now, "hang", f"завис: не принимает сообщения {round(age / 60)} мин", tail())
                it["fixable_later"] = True  # перезапуск обычно помогает; Claude — если завис снова сразу
                acts.append(("restart", "завис"))
            else:
                close(g, "hang")
            # 4. сервер приложения и сайта: 2 мин — перезапуск, ещё 3 мин — Claude чинит сразу
            if not web:
                g["web"] = g.get("web", 0) + 1
                if g["web"] == 2:
                    raise_issue(g, now, "web", "сайт и приложение не отвечают", fixable=False)
                    acts.append(("restart", "сайт не отвечал"))
                if g["web"] >= 4:
                    it = raise_issue(g, now, "web", "сайт не поднимается после перезапуска", tail())
                    it["urgent"] = True
            else:
                g["web"] = 0
                if site is not False:
                    close(g, "web")
            # 4б. сайт снаружи (Caddy планера): HDP жив, а адрес не открывается
            if web and site is False:
                g["site"] = g.get("site", 0) + 1
                if g["site"] == 2:
                    raise_issue(g, now, "web", "сайт снаружи не открывается (Caddy)", fixable=False)
                    acts.append(("caddy", "сайт снаружи не открывался"))
                if g["site"] >= 4:
                    it = raise_issue(g, now, "web", "сайт снаружи не открывается и после перезапуска Caddy",
                                     ui.log_text(CADDY, 60, 2500) if hasattr(ui, "log_text") else "")
                    it["urgent"], it["target"] = True, CADDY
            elif site:
                g["site"] = 0
            # 5. зависшие дела
            for text, detail in jams:
                raise_issue(g, now, "stuck", text, detail)
            now_stuck = {sig_of("stuck", t) for t, _ in jams}
            for it in g["issues"].values():
                if it["kind"] == "stuck" and it["sig"] not in now_stuck:
                    it["closed"] = True
        # 6. журнал ошибок HDP
        net = [x for x in g.get("net", []) if now - x < 600]
        for rec in read_journal(g):
            kind, text = rec.get("kind") or "bug", rec.get("text") or ""
            if kind == "net":
                net.append(now)
                continue
            code_bug = kind == "bug" or not NOT_CODE.search(text + " " + (rec.get("detail") or "")[-400:])
            it = raise_issue(g, now, kind, text, rec.get("detail") or "", fixable=code_bug)
            if kind in ("script", "claude") and it["count"] < 2:
                it["fixable_later"] = True  # разовый сбой скрипта/Claude — бывает; чиним, если повторится
        g["net"] = net
        if len(net) >= 10:
            raise_issue(g, now, "net", "нет связи с Telegram (10+ сбоев за 10 мин)", fixable=False)
        # 7. Traceback в логе pm2 (то, что бот не поймал сам)
        n, last = check.log_errors(p, g["seen"].setdefault("errlog", {}))
        if n:
            raise_issue(g, now, "log", last or "Traceback в логе", tail())
        # перезапуск — не чаще 3 раз в час
        g["restarts"] = [t for t in g["restarts"] if now - t < 3600]
        if any(a[0] == "restart" for a in acts):
            acts = [a for a in acts if a[0] != "restart"] + [next(a for a in acts if a[0] == "restart")]
            if len(g["restarts"]) >= RESTARTS_PER_HOUR:
                acts = [a for a in acts if a[0] != "restart"]
                raise_issue(g, now, "crash", "не поднимается: перезапускал 3 раза за час",
                            tail())
            else:
                g["restarts"].append(now)
        for it in g["issues"].values():  # разовая ошибка сутки не повторялась — закрыта
            if it["kind"] in ("bug", "script", "claude", "log", "net", "crash") and now - it["last"] > 86400:
                it["closed"] = True
        acts += decide(g, now)
        g["issues"] = {k: v for k, v in g["issues"].items() if not v.get("closed") or now - v["last"] < KEEP}
        ui.save(st)
    return acts


def fix_allowed(g, now):
    return g.get("auto") and len([t for t in g["fixes"] if now - t < 86400]) < FIX_PER_DAY


def decide(g, now):
    """Что делать с открытыми ошибками: починить сам или сказать. → [("fix", sig) | ("tell", sig)]."""
    acts = []
    for it in g["issues"].values():
        if it.get("closed") or it.get("muted") or now - it.get("last", now) > FRESH:
            continue
        urgent = it.get("urgent")
        can_fix = urgent or it.get("fixable") and not (it.get("fixable_later") and it["count"] < 2)
        again = URGENT_AGAIN if urgent else FIX_AGAIN
        if (can_fix and fix_allowed(g, now) and now - it.get("fix_at", 0) > again
                and not any(a[0] == "fix" for a in acts)):
            acts.append(("fix", it["sig"]))
            it["fix_at"] = round(now)  # пока не начали — пометка, чтобы не звать дважды
            continue
        if now - it.get("told", 0) > TELL_AGAIN and not (it.get("fixable_later") and it["count"] < 2):
            it["told"] = round(now)
            acts.append(("tell", it["sig"]))
    return acts


# ---------- действия ----------

FIX_TZ = ("🛡 Страж HDP нашёл ошибку в HDP-боте. Найди причину по коду, журналу (horo/tmp/journal.jsonl) и логам pm2 "
          "horo-bot и почини её в корне, чтобы она не повторялась (не прячь ошибку try/except, не трогай тесты ради "
          "зелёного; добавь тест на этот случай). Если причина вне кода (сайт, сеть, лимит) — сделай, чтобы бот "
          "переживал это мягко. Ошибка: {text} (раз: {count})\nПодробности:\n{detail}")


MIRROR_TZ = ("🪞 Дозор зеркала (🛡 Страж HDP) нашёл ошибку сайта-зеркала HDP (https://hdp.…, код: bot/mirror.py, bot/web.py, "
             "bot/site/mirror/overlay.js|css|guard.js). Найди причину и почини в корне (не прячь ошибку, не трогай тесты "
             "ради зелёного; добавь тест на этот случай). Проверь себя: python3 bot/mirrorwatch.py <адрес зеркала> "
             "(отчёт — horo/tmp/mirrorwatch/last.json, полный обход — horo/tmp/sitecheck/last.json). Если причина — "
             "сама платформа horodigital.ru, а не зеркало, ничего не ломай и так и напиши. Ошибка: {text} (раз: {count})\n"
             "Подробности:\n{detail}")


def busy():
    return bool(ui.hdp_busy()) or maker.job.locked()


def restart(why):
    ok, text = ui.restart_app(PM2, wait=8)
    with lock:
        st = ui.load()
        event(state(st), time.time(), "down" if ok else "crash", f"перезапустил ({why})" if ok else text)
        ui.save(st)
    return ok, text


def run_fix(chat, sig, by_hand=False):
    """Claude чинит одну ошибку (maker.fix — тот же, что «✏️ Доработать»: тесты, коммит, перезапуск, откат кнопкой)."""
    with lock:
        st = ui.load()
        g = state(st)
        it = g["issues"].get(sig)
        if not it:
            ui.save(st)
            return ui.send(chat, "Этой ошибки уже нет — открой 🛡 Страж HDP.")
        it["fix_at"] = round(time.time())
        if not by_hand:
            g["fixes"].append(round(time.time()))
        event(g, time.time(), "bug", "чиню: " + it["text"])
        ui.save(st)
    if not by_hand:
        ui.send(chat, ui.card("🛡 <b>HDP: нашёл ошибку — чиню сам</b>", [ui.esc(it["text"][:200])]),
                [[ui.b("🛡 Страж", "grd")]])
    tz = MIRROR_TZ if it["kind"] == "mirror" else FIX_TZ
    maker.fix(chat, it.get("target") or PM2, tz.format(text=it["text"], count=it["count"], detail=(it.get("detail") or "—")[-2500:]))
    with lock:
        st = ui.load()
        x = state(st)["issues"].get(sig)
        if x:
            x["fixed_at"] = round(time.time())
            x["closed"] = True  # повторится — откроется снова с пометкой «после починки»
        ui.save(st)


def tell(chat, sigs):
    with lock:
        g = state(ui.load())
    items = [g["issues"][s] for s in sigs if s in g["issues"]]
    if not items:
        return
    lines = [f"{KIND_ICON.get(it['kind'], '⚠️')} {ui.esc(it['text'][:160])}"
             + (" · <i>повторилась после починки</i>" if it.get("after_fix") else "") for it in items[:5]]
    btns = []
    fixable = [it for it in items if it.get("fixable")]
    if len(fixable) == 1:
        btns.append([ui.b("🛠 Починить", "grd|fix|" + key(fixable[0]["sig"]), "primary"),
                     ui.b("🙈 Не важно", "grd|mute|" + key(fixable[0]["sig"]))])
    elif fixable:
        btns.append([ui.b("🛠 Починить всё", "grd|fixall", "primary")])
    btns.append([ui.b("🛡 Страж", "grd"), ui.b("📜 Логи", f"lg|{PM2}")])
    ui.send(chat, ui.card("🛡 <b>HDP: " + ("проблема</b>" if len(items) == 1 else f"проблем {len(items)}</b>"), lines), btns)


def key(sig):
    """Короткий ключ ошибки для кнопки (callback_data ≤ 64 байт)."""
    import hashlib
    return hashlib.sha1(sig.encode()).hexdigest()[:10]


def by_key(g, k):
    return next((s for s in g["issues"] if key(s) == k), None)


def handle(acts, now=None):
    chat = ui.owner()
    if not chat:
        return
    tells = []
    for kind, arg in acts:
        if kind == "restart":
            ok, text = restart(arg)
            ui.send(chat, f"🛡 HDP {arg} — перезапустил" if ok else ui.card("🛡 <b>HDP не поднимается</b>", [ui.esc(text)]),
                    None if ok else [[ui.b("📜 Логи", f"lg|{PM2}"), ui.b("🛡 Страж", "grd")]])
        elif kind == "caddy":
            ok, text = ui.restart_app(CADDY, wait=8)
            with lock:
                st = ui.load()
                event(state(st), time.time(), "web", f"перезапустил Caddy ({arg})" if ok else text)
                ui.save(st)
            ui.send(chat, f"🛡 Сайт HDP: {arg} — перезапустил Caddy" if ok else ui.card("🛡 <b>Caddy не поднимается</b>", [ui.esc(text)]))
        elif kind == "fix":
            with lock:
                urgent = (state(ui.load())["issues"].get(arg) or {}).get("urgent")
            if busy() and not urgent:  # HDP решает или сдаёт — подождём: починка его перезапустит (сайт лежит — не ждём)
                with lock:
                    st = ui.load()
                    it = state(st)["issues"].get(arg)
                    if it:
                        it["fix_at"] = 0
                    ui.save(st)
                continue
            threading.Thread(target=run_fix, args=(chat, arg), daemon=True).start()
        elif kind == "tell":
            tells.append(arg)
    if tells:
        tell(chat, tells)


def loop():
    time.sleep(90)
    while True:
        try:
            if ui.owner():
                handle(tick())
                with lock:
                    last = state(ui.load()).get("deep", {}).get("at", 0)
                if time.time() - last > DEEP_EVERY and not deep_lock.locked() and not ui.hdp_busy():
                    threading.Thread(target=deep, daemon=True).start()
                mirror_due()
        except Exception as e:
            print("guard:", e)
            traceback.print_exc()
        time.sleep(EVERY)


# ---------- глубокая проверка ----------

def why_failed(code, err):
    """Почему команда не прошла: последняя строка stderr, иначе код выхода или сигнал.
    Не последняя строка stdout — там обычный вывод (у grades.js — легенда «Уровень → оценка…»), а не ошибка."""
    lines = [s for s in (err or "").strip().splitlines() if s.strip()]
    if lines:
        return lines[-1].strip()
    if code < 0:
        try:
            name = signal.Signals(-code).name
        except ValueError:
            name = str(-code)
        return f"прерван сигналом {name} (память или перезапуск), ошибки не написал"
    return f"код выхода {code}, ошибки не написал"


def child_env():
    """Окружение для node и claude. Канал pm2 (NODE_CHANNEL_FD) убираем: с ним node, всё выведя, падает при
    выходе (SIGABRT, код 134) — так «сайт сломан» оказался ложной тревогой (в HDP то же, bot.py)."""
    env = {k: v for k, v in os.environ.items() if k not in ("NODE_CHANNEL_FD", "NODE_CHANNEL_SERIALIZATION_MODE")}
    return {**env, "HOME": "/root"}


def run(cmd, timeout):
    """→ (прошла, вывод, почему не прошла)"""
    try:
        p = subprocess.run(cmd, cwd=ui.REPO, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, env=child_env())
        ok = p.returncode == 0
        return ok, (p.stdout or "") + (p.stderr or ""), "" if ok else why_failed(p.returncode, p.stderr)
    except subprocess.TimeoutExpired:
        return False, "", f"не уложился в {timeout} с"
    except OSError as e:
        return False, "", str(e)


def deep(chat=None):
    """Сайт (вход и оценки — только чтение), Claude, тесты HDP. chat — прислать итог (по кнопке)."""
    if not deep_lock.acquire(blocking=False):
        return chat and ui.send(chat, "⏳ Уже проверяю — итог придёт сюда.")
    try:
        res = {}
        env = "/etc/horo-bot.env"
        site = f"set -a; [ -f {env} ] && . {env}; set +a; exec node horo/grades.js"
        ok, out, why = run(["bash", "-c", site], 200)
        res["site"] = [ok, check.mask(why)[:200]]
        ok, out, why = run(["claude", "-p", "Ответь одним словом: ok", "--output-format", "text"], 150)
        res["claude"] = [ok and bool(out.strip()), check.mask(why or ("" if out.strip() else "пустой ответ"))[:200]]
        src = check.worktree()
        try:
            base = src or ui.REPO
            fails = []
            for rel in ("bot/test_bot.py", "bot/test_buttons.py", "bot/test_mirror.py", "bot/test_mirrorwatch.py"):
                if os.path.exists(os.path.join(base, rel)):
                    t_ok, t_out = check.run_test(base, ["python3", rel])
                    if not t_ok:
                        fails.append(f"{rel}: {check.failure(t_out)}")
            res["tests"] = [not fails, "; ".join(fails)[:600]]
        finally:
            check.drop_worktree(src)
        now = time.time()
        acts = []
        with lock:
            st = ui.load()
            g = state(st)
            g["deep"] = {"at": round(now), **res}
            names = {"site": "сайт", "claude": "Claude", "tests": "тесты"}
            for k, (ok, why) in res.items():
                kind = "deep"
                text = f"глубокая проверка: {names[k]} — {why or 'не прошла'}"
                if ok:
                    for it in g["issues"].values():
                        if it["kind"] == kind and it["text"].startswith(f"глубокая проверка: {names[k]}"):
                            it["closed"] = True
                else:
                    it = raise_issue(g, now, kind, text, why,
                                     fixable=k == "tests" or (k == "site" and not NOT_CODE.search(why)))
                    if k == "site" and it["count"] < 2:
                        it["fixable_later"] = True  # разовый сбой сайта — как у скрипта в журнале: чиним, если повторится
            acts = decide(g, now)
            ui.save(st)
        if chat:
            ui.send(chat, *screen())
            acts = [a for a in acts if a[0] != "tell"]  # итог уже на экране
        handle(acts)
    except Exception as e:
        traceback.print_exc()
        if chat:
            ui.send(chat, ui.card("⚠️ <b>Проверка упала</b>", [ui.esc(str(e)[:300])]))
    finally:
        deep_lock.release()


# ---------- 🪞 Дозор зеркала ----------

def mirror_base():
    return site_url()[:-len("/health")] if site_url() else ""


def mirror_due(now=None):
    """Из loop(): пора ли лёгкой проверке зеркала (5 мин) или полному обходу (6 ч, только когда HDP свободен)."""
    now = now or time.time()
    if not mirror_base() or not web_ok():
        return None  # сайт лежит — это уже ловит минутная проверка (перезапуск, Claude)
    with lock:
        m = state(ui.load()).get("mirror", {})
    started = []
    if now - m.get("at", 0) > MIRROR_EVERY and not mirror_lock.locked():
        threading.Thread(target=mirror_check, daemon=True).start()
        started.append("watch")
    if now - m.get("az_at", 0) > MIRROR_AZ_EVERY and not ui.hdp_busy() and not az_lock.locked():
        threading.Thread(target=mirror_az, daemon=True).start()
        started.append("az")
    return started


def last_json(out):
    """Последняя строка вывода — JSON отчёта (выше могут быть предупреждения)."""
    for ln in reversed((out or "").strip().splitlines()):
        if ln.startswith("{"):
            try:
                return json.loads(ln)
            except ValueError:
                return None
    return None


def mirror_apply(rep, why, now, az=False):
    """Итог проверки зеркала → ошибки Стража. rep — отчёт (None — проверка не прошла: why). → действия decide()."""
    with lock:
        st = ui.load()
        g = state(st)
        m = g.setdefault("mirror", {})
        mine = lambda it: it["kind"] == "mirror" and it["text"].startswith(AZ) == az
        if az:
            m["az_at"] = round(now)
            m["az"] = None if rep is None else len(rep.get("ours") or [])
        else:
            m.update(at=round(now), ok=bool(rep and rep.get("ok")), secs=(rep or {}).get("secs"))
        if rep is None:
            text = (AZ if az else "зеркало: ") + "проверка не прошла — " + (why or "без ответа")[:150]
            it = raise_issue(g, now, "mirror", text, why, fixable=not NOT_CODE.search(why or ""))
            if it["count"] < 2:
                it["fixable_later"] = True
        else:
            found = ([{"text": f"{AZ}ошибок {len(rep['ours'])} (первая: {rep['ours'][0][:90]})", "detail": rep.get("fix", "")}]
                     if az and rep.get("ours") else [] if az else rep.get("issues") or [])
            sigs = set()
            for x in found:
                it = raise_issue(g, now, "mirror", x["text"], x.get("detail") or "")
                sigs.add(it["sig"])
                # лёгкая проверка: разовый сбой бывает (сеть, перезапуск) — чиним, если повторилась через 5 мин;
                # полный обход сам сверяет с платформой и идёт раз в 6 ч — чиним сразу
                it["fixable_later"] = not az and it["count"] < 2
            for it in g["issues"].values():
                if mine(it) and it["sig"] not in sigs:
                    it["closed"] = True
        acts = decide(g, now)
        ui.save(st)
    return acts


def mirror_check(chat=None):
    """Лёгкая проверка (bot/mirrorwatch.py, секунды). chat — прислать итог (по кнопке)."""
    if not mirror_lock.acquire(blocking=False):
        return chat and ui.send(chat, "⏳ Уже проверяю зеркало — итог придёт сюда.")
    try:
        ok, out, why = run(["python3", "bot/mirrorwatch.py", mirror_base()], 150)
        rep = last_json(out) if ok else None
        acts = mirror_apply(rep, check.mask(why or "нет отчёта")[:300], time.time())
        if chat:
            ui.send(chat, *mirror_card(rep, why))
            acts = [a for a in acts if a[0] != "tell"]
        handle(acts)
    except Exception as e:
        traceback.print_exc()
        if chat:
            ui.send(chat, ui.card("⚠️ <b>Проверка зеркала упала</b>", [ui.esc(str(e)[:300])]))
    finally:
        mirror_lock.release()


def mirror_az():
    """Полный обход сайта браузером (bot/sitecheck.py, до 30 мин) — раз в 6 ч, когда HDP свободен."""
    if not az_lock.acquire(blocking=False):
        return
    try:
        ok, out, why = run(["python3", "bot/sitecheck.py", mirror_base(), "--max", "30"], 35 * 60)
        rep = last_json(out) if ok else None
        handle(mirror_apply(rep, check.mask(why or "нет отчёта")[:300], time.time(), az=True))
    except Exception:
        traceback.print_exc()
    finally:
        az_lock.release()


def mirror_card(rep, why=""):
    if rep is None:
        return ui.card("🪞 <b>Зеркало: проверка не прошла</b>", [ui.esc((why or "без ответа")[:300])]), [[ui.b("🛡 Страж", "grd")]]
    lines = [("✅ " if c["ok"] else "❌ ") + ui.esc(c["name"]) + (f" · {c['ms'] / 1000:.1f} с" if c.get("ms") else "")
             + (f" — {ui.esc(c['why'][:80])}" if c.get("why") else "") for c in rep.get("checks", [])]
    head = "🪞 <b>Зеркало: всё работает</b>" if rep.get("ok") else f"🪞 <b>Зеркало: проблем {len(rep.get('issues', []))}</b>"
    foot = f"<i>{rep.get('secs', 0)} с · " + ("найденное починю сам, когда HDP свободен</i>" if not rep.get("ok") else "проверяю каждые 5 мин</i>")
    return ui.card(head, lines, foot, expand=len(lines) > 6), [[ui.b("🔁 Ещё раз", "grd|mir"), ui.b("🛡 Страж", "grd")]]


# ---------- экран ----------

def when(ts):
    return time.strftime("%H:%M", time.localtime(ts)) if time.time() - ts < 86400 else time.strftime("%d.%m %H:%M", time.localtime(ts))


def summary():
    """Одна строка для главного экрана Главного или None, если всё спокойно."""
    with lock:
        g = state(ui.load())
    now = time.time()  # 23.10: «проблем 6» висело сутки после починки — считаем только свежие (FRESH)
    open_ = [it for it in g["issues"].values()
             if not it.get("closed") and not it.get("muted") and now - it.get("last", now) <= FRESH]
    return f"🛡 HDP: проблем {len(open_)}" if open_ else None


def screen():
    """(текст, кнопки) экрана «🛡 Страж HDP»."""
    now = time.time()
    with lock:
        g = state(ui.load())
    a = next((x for x in ui.apps() or [] if x["name"] == PM2), None)
    lines = []
    if not a:
        lines.append("⚪️ HDP нет в pm2")
    elif a["status"] != "online":
        lines.append(f"🔴 Не работает · pm2: {ui.esc(a['status'])}")
    else:
        age = heartbeat_age(now)
        lines.append(f"🟢 Работает {ui.ago(a['uptime'])}" + (" · отвечает" if age is None or age < HANG else
                                                              f" · 🧊 молчит {round(age / 60)} мин"))
    day = [e for e in g["events"] if now - e[0] < 86400]
    fixed = sum(1 for e in day if e[2].startswith("чиню"))
    rs = sum(1 for e in day if e[2].startswith("перезапустил"))
    errs = sum(1 for e in day if not e[2].startswith(("чиню", "перезапустил")))
    lines.append(f"🧯 За сутки: ошибок {errs} · чинил {fixed} · перезапусков {rs}")
    d = g.get("deep")
    if d:
        mark = lambda k: "✅" if (d.get(k) or [False])[0] else "❌"
        lines.append(f"🔍 {when(d['at'])}: сайт {mark('site')} · Claude {mark('claude')} · тесты {mark('tests')}")
    m = g.get("mirror") or {}
    if m.get("at"):
        az = m.get("az")
        lines.append(f"🪞 Зеркало {when(m['at'])}: " + ("✅" if m.get("ok") else "❌")
                     + (f" · обход {when(m['az_at'])}: " + ("✅" if az == 0 else f"ошибок {az}" if az else "❌")
                        if m.get("az_at") else ""))
    open_ = sorted((it for it in g["issues"].values() if not it.get("closed") and not it.get("muted")),
                   key=lambda it: -it["last"])
    for it in open_[:4]:
        lines.append(f"{KIND_ICON.get(it['kind'], '⚠️')} {ui.esc(it['text'][:120])}"
                     + (f" ×{it['count']}" if it["count"] > 1 else ""))
    foot = ("<i>Слежу каждую минуту, зеркало — каждые 5 мин, раз в 2 ч — сайт, Claude и тесты. "
            + ("Баг — чиню сам, когда HDP свободен.</i>" if g.get("auto") else "Баги только показываю.</i>"))
    btns = [[ui.b("🔍 Проверить сейчас", "grd|deep", "primary"), ui.b("🪞 Зеркало", "grd|mir")]]
    if any(it.get("fixable") for it in open_):
        btns.insert(1, [ui.b("🛠 Починить", "grd|fixall")])
    btns.append([ui.b("🤖 Чиню сам: " + ("вкл" if g.get("auto") else "выкл"), "grd|auto"),
                 ui.b("📋 Журнал", "grd|log")])
    btns.append([ui.b("‹ Главная", "home"), ui.b("📜 Логи", f"lg|{PM2}")])
    return ui.card("🛡 <b>Страж HDP</b>", lines, foot), btns


def journal_screen():
    with lock:
        g = state(ui.load())
    ev = g["events"][-12:][::-1]
    lines = [f"{when(e[0])} {KIND_ICON.get(e[1], '•')} {ui.esc(e[2][:120])}" for e in ev] or ["Пока ничего — всё спокойно."]
    return ui.card("📋 <b>Журнал Стража</b>", lines, expand=len(lines) > 6), [[ui.b("‹ Страж", "grd")]]


def on_callback(chat, mid, data):
    """grd, grd|deep, grd|mir, grd|auto, grd|log, grd|fix|<ключ>, grd|mute|<ключ>, grd|fixall → текст всплывашки."""
    arg = data[1] if len(data) > 1 else ""
    if not arg:
        ui.show(chat, mid, *screen())
        return ""
    if arg == "deep":
        if deep_lock.locked():
            return "Уже проверяю"
        threading.Thread(target=deep, args=(chat,), daemon=True).start()
        return "Проверяю сайт, Claude и тесты — минуты 3…"
    if arg == "mir":
        if mirror_lock.locked():
            return "Уже проверяю зеркало"
        threading.Thread(target=mirror_check, args=(chat,), daemon=True).start()
        return "Проверяю зеркало — секунд 10…"
    if arg == "auto":
        with lock:
            st = ui.load()
            g = state(st)
            g["auto"] = not g.get("auto")
            ui.save(st)
        ui.show(chat, mid, *screen())
        return "Чиню сам" if g["auto"] else "Только показываю"
    if arg == "log":
        ui.show(chat, mid, *journal_screen())
        return ""
    if arg in ("fix", "mute") and len(data) > 2:
        with lock:
            st = ui.load()
            g = state(st)
            sig = by_key(g, data[2])
            if sig and arg == "mute":
                g["issues"][sig]["muted"] = True
            ui.save(st)
        if not sig:
            return "Этой ошибки уже нет"
        if arg == "mute":
            return "Больше не напомню"
        if maker.job.locked():
            return "Claude занят — нажми чуть позже"
        threading.Thread(target=run_fix, args=(chat, sig, True), daemon=True).start()
        return "Чиню…"
    if arg == "fixall":
        with lock:
            g = state(ui.load())
        sigs = [s for s, it in g["issues"].items() if it.get("fixable") and not it.get("closed") and not it.get("muted")]
        if not sigs:
            return "Чинить нечего"

        def all_():
            for s in sigs:
                run_fix(chat, s, True)
        threading.Thread(target=all_, daemon=True).start()
        return "Чиню по очереди…"
    return None


def app_state():
    """Для приложения: коротко."""
    with lock:
        g = state(ui.load())
    open_ = [it for it in g["issues"].values() if not it.get("closed") and not it.get("muted")]
    return {"auto": bool(g.get("auto")), "deep": g.get("deep"), "mirror": g.get("mirror"),
            "issues": [{"text": it["text"][:160], "kind": it["kind"], "count": it["count"]} for it in open_[:6]]}


def app_action(arg):
    """Из приложения: deep (проверить сейчас), fixall (починить открытые), auto (вкл/выкл). Итог — и в чат."""
    chat = ui.owner()
    if arg == "deep":
        if deep_lock.locked():
            raise ValueError("уже проверяю")
        threading.Thread(target=deep, args=(chat,), daemon=True).start()
        return {"ok": True, "text": "Проверяю сайт, Claude и тесты — итог в чате"}
    if arg == "auto":
        with lock:
            st = ui.load()
            g = state(st)
            g["auto"] = not g.get("auto")
            ui.save(st)
        return {"ok": True, "text": "Баги чиню сам" if g["auto"] else "Баги только показываю"}
    if arg == "fixall":
        note = on_callback(chat, None, ["grd", "fixall"])
        if note == "Чинить нечего":
            raise ValueError(note)
        return {"ok": True, "text": "Чиню — ход и итог в чате"}
    raise ValueError("нет такого действия")
