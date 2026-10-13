#!/usr/bin/env python3
"""«Ловец» — отдельный бот: ловит крутые юзернеймы Telegram на каналы Глеба.

Два способа:
- Конкретные имена: Глеб присылает @name (можно списком) — бот сразу проверяет: свободно → занимает тут же,
  продаётся на Fragment → говорит об этом, занято → следит и, как только освободится, ставит на канал.
- 🎲 По пожеланиям: Глеб выбирает длину, стиль, тему, сколько поймать → бот сам придумывает кандидатов (Claude,
  без него — свой генератор), проверяет их по кругу и занимает свободные, пока не наберёт нужное число.
«✨ Идеи» — показать придуманные имена и что из них свободно прямо сейчас.

Занять юзернейм через Bot API нельзя — это умеет только аккаунт человека, поэтому проверка и захват идут через
Секретаря (tgbot/agent.py, сокет tgbot/tmp/agent.sock, команды username/grab/reserve/mychannels в tgbot/actions.py).
Секретарь не вошёл в аккаунт — Ловец скажет об этом и будет ждать. Всё остальное у бота своё: токен, pm2
`catcher-bot`, данные `catcher/data/`, мини-приложение `catcher/webapp/` (порт CATCHER_PORT, адрес catcher.<host>).

Окружение: CATCHER_TOKEN (обязательно), CATCHER_PORT (8796), CATCHER_URL (https-адрес приложения; пусто —
https://catcher.<ip-через-дефисы>.sslip.io через Caddy планера), CATCHER_IP, TG_AGENT_SOCK.
Тесты без сети: python3 catcher/test_catcher.py.
"""
import json
import os
import random
import re
import socket
import sys
import threading
import time
import traceback
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "studio", "kit"))
import botkit as kit  # noqa: E402

PORT = int(os.environ.get("CATCHER_PORT") or 8796)
SOCK = os.environ.get("TG_AGENT_SOCK") or os.path.join(REPO, "tgbot", "tmp", "agent.sock")
INTERVALS = [3, 5, 10, 30, 60]  # секунд между проверками
FLOOD_NOTE = 120  # о паузе дольше этой — пишем в чат
MAX_NAMES = 50
NAME = re.compile(r"(?<![\w/])@?([A-Za-z][A-Za-z0-9_]{3,30}[A-Za-z0-9])\b")  # Telegram: от 5 символов
STATUS = {  # статус имени -> значок, подпись
    "watch": ("👀", "слежу"), "caught": ("🎯", "поймал"), "fragment": ("💎", "продаётся на Fragment"),
    "invalid": ("🚫", "такое имя нельзя"), "paused": ("⏸", "пауза"),
}
# пожелания к имени: длина, стиль, тема, только буквы, сколько поймать
LENGTHS = [("5", "5 символов", (5, 5)), ("6-7", "6–7", (6, 7)), ("8-10", "8–10", (8, 10)), ("any", "любая", (5, 12))]
STYLES = [("word", "одно слово"), ("two", "два слова"), ("digit", "слово + цифра"), ("name", "как имя"), ("any", "любой")]
COUNTS = [1, 3, 5, 10]
DEFAULT_WISH = {"len": "6-7", "style": "any", "theme": "", "letters": True, "count": 1}

bot = kit.Bot("Ловец", emoji="🎯", about="Ловлю крутые юзернеймы Telegram на твои каналы: пришли имя или выбери "
                                        "пожелания — займу, как только найду свободное.",
              short="Ловит юзернеймы на каналы",
              commands=[("start", "Главное"), ("wish", "Ловить по пожеланиям"), ("list", "Список имён"),
                        ("ideas", "Идеи крутых имён"), ("settings", "Как ловить"), ("help", "Что умею")],
              app_text="Ловец", token=os.environ.get("CATCHER_TOKEN", ""), port=PORT,
              app_url=os.environ.get("CATCHER_URL", ""),
              support={"name": "Ловец", "folders": ["catcher", "tgbot"], "pm2": "catcher-bot", "cwd": REPO,
                       "tests": ["python3 catcher/test_catcher.py", "python3 tgbot/test_tgbot.py"]})


# ---------- Секретарь: аккаунт Глеба ----------

class Agent:
    """Запросы к Секретарю по его сокету (как tgbot/tg.py). call(cmd, args) -> (ok, out): out — dict/list/str."""

    def __init__(self, path=SOCK):
        self.path = path

    def call(self, cmd, args=None, timeout=60):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect(self.path)
        except OSError:
            s.close()
            return False, "Секретарь не запущен (часть Главного: /start → «🔄 Секретарь»)"
        with s:
            s.sendall(json.dumps({"cmd": cmd, "args": args or {}}, ensure_ascii=False).encode() + b"\n")
            buf = b""
            try:
                while not buf.endswith(b"\n"):
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
            except OSError as e:
                return False, f"Секретарь не ответил: {e}"
        try:
            r = json.loads(buf)
            return bool(r["ok"]), r["out"]
        except (ValueError, KeyError, TypeError):
            return False, "Секретарь оборвал ответ"


agent = Agent()


# ---------- данные ----------

def stamp():
    return kit.now().strftime("%d.%m %H:%M")


def names():
    return bot.db.get("names", [])


def settings():
    return {"interval": 5, "target": 0, "target_title": "", **bot.db.get("settings", {})}


def set_setting(key, value):
    bot.db.update("settings", lambda s: {**s, key: value}, {})


def wish():
    return {**DEFAULT_WISH, **bot.db.get("wish", {})}


def set_wish(key, value):
    bot.db.update("wish", lambda w: {**w, key: value}, {})
    bot.db.update("hunt", lambda h: {**h, "queue": []}, {})  # пожелания поменялись — кандидаты заново


def hunt():
    return {"on": False, "checked": 0, "caught": [], "seen": [], "queue": [], "started": "", **bot.db.get("hunt", {})}


def set_hunt(**kw):
    return bot.db.update("hunt", lambda h: {**hunt(), **h, **kw}, {})


def find(name):
    return next((n for n in names() if n["name"] == name), None)


def put(entry):
    """Записать имя (новое или обновлённое) — по name."""
    def upd(items):
        return [x for x in items if x["name"] != entry["name"]] + [entry]
    bot.db.update("names", upd, [])
    return entry


def drop(name):
    bot.db.update("names", lambda items: [x for x in items if x["name"] != name], [])


def parse_names(text):
    """Юзернеймы из текста: @name, t.me/name, просто name. Повторы убираются, порядок сохраняется."""
    out = []
    for m in NAME.finditer(str(text or "").replace("https://", " ").replace("t.me/", " @")):
        u = m.group(1).lower()
        if "__" not in u and u not in out:
            out.append(u)
    return out


def looks_like_names(text):
    """Весь текст — только юзернеймы (а не просьба «придумай имена про космос»)."""
    words = re.sub(r"https?://|t\.me/", " ", str(text or "")).replace(",", " ").split()
    return bool(words) and all(re.fullmatch(r"@?[A-Za-z0-9_]{5,32}", w) for w in words)


# ---------- проверка и захват ----------

def check(name):
    """(ok, dict) — статус имени у Секретаря: free|taken|fragment|invalid|flood(+wait)|error."""
    ok, out = agent.call("username", {"username": name}, timeout=40)
    if ok and not isinstance(out, dict):
        return False, str(out)
    return ok, out


def target_channel():
    """Куда ставить имя: свой канал из настроек, иначе запасной (создаётся один раз), иначе новый при захвате."""
    s = settings()
    if s["target"]:
        return s["target"]
    r = bot.db.get("reserve")
    if r:
        return r
    ok, out = agent.call("reserve", {"title": "Ловец", "auto": True}, timeout=60)
    if ok and isinstance(out, dict) and out.get("id"):
        bot.db.set("reserve", out["id"])
        return out["id"]
    return 0


def caught_card(name, link, title, how=""):
    return kit.card(f"🎯 <b>Поймал @{kit.esc(name)}</b>" + (f" · {how}" if how else ""),
                    [f"Теперь это канал «{kit.esc(title or name)}».", f"<a href=\"{link}\">{link}</a>"]), \
        [[kit.url("▶️ Открыть канал", link, "success")], [kit.b("📋 Список", "list"), kit.b("‹ Главное", "home")]]


def grab(name, say=True, how=""):
    """Занять имя прямо сейчас. Возвращает статус (ok|taken|too_many|fragment|invalid|error); пишет Глебу, что вышло."""
    entry = find(name) or {"name": name, "status": "watch", "added": stamp(), "checks": 0}
    args = {"username": name, "title": name, "auto": True}
    ch = target_channel()
    if ch:
        args["channel"] = ch
    ok, out = agent.call("grab", args, timeout=90)
    if not ok or not isinstance(out, dict):
        entry.update(status="watch", note=f"не вышло: {out}")
        put(entry)
        if say:
            bot.notify(kit.error(f"@{kit.esc(name)}: {kit.esc(out)}"))
        return "error"
    st = out.get("status")
    if out.get("created") and st != "ok" and out.get("channel"):
        bot.db.set("reserve", out["channel"])  # канал создан, имя не досталось — пригодится в следующий раз
    if st == "ok":
        if bot.db.get("reserve") == out.get("channel"):
            bot.db.delete("reserve")
        if settings()["target"] == out.get("channel"):
            set_setting("target", 0)  # свой канал занят пойманным именем — следующее пойдёт на новый
            set_setting("target_title", "")
        entry.update(status="caught", link=out["link"], channel=out.get("channel"), title=out.get("title"),
                     caught=stamp(), note="", how=how)
        put(entry)
        if say:
            bot.notify(*caught_card(name, out["link"], out.get("title"), how))
        return "ok"
    if st == "too_many":
        entry.update(status="paused", note="лимит публичных каналов (обычно 10) — сделай какой-нибудь закрытым")
        put(entry)
        if say:
            bot.notify(kit.error(f"@{kit.esc(name)} освободился, но у тебя лимит публичных каналов (обычно 10). "
                                 "Сделай какой-нибудь канал закрытым или удали — и нажми «▶️ Снова» в списке."))
        return st
    if st in ("fragment", "invalid"):
        entry.update(status=st, note=STATUS[st][1])
        put(entry)
        if say:
            bot.notify(f"{STATUS[st][0]} @{kit.esc(name)} — {STATUS[st][1]}.")
        return st
    entry.update(status="watch", note="кто-то успел раньше" if st == "taken" else f"ошибка: {out.get('error') or st}")
    put(entry)
    if say and st != "taken":
        bot.notify(kit.error(f"@{kit.esc(name)}: {kit.esc(out.get('error') or st)}"))
    return st


def add(name, say=True):
    """Новое имя: проверить сейчас → занять / следить / отказать. Возвращает (статус, подпись для Глеба)."""
    if find(name) and find(name)["status"] == "caught":
        return "caught", f"🎯 @{name} уже пойман"
    if len(names()) >= MAX_NAMES:
        return "full", f"⚠️ Больше {MAX_NAMES} имён не слежу — убери лишние"
    ok, out = check(name)
    if not ok:
        put({"name": name, "status": "watch", "added": stamp(), "checks": 0, "note": "Секретарь недоступен"})
        return "watch", f"👀 @{name} — слежу (Секретарь пока недоступен: {out})"
    st = out.get("status")
    if st == "free":
        r = grab(name, say=say)
        return r, f"🎯 @{name} был свободен — занял!" if r == "ok" else f"⚠️ @{name} свободен, но занять не вышло"
    if st in ("fragment", "invalid"):
        put({"name": name, "status": st, "added": stamp(), "checks": 1, "note": STATUS[st][1]})
        return st, f"{STATUS[st][0]} @{name} — {STATUS[st][1]}"
    put({"name": name, "status": "watch", "added": stamp(), "checks": 1, "checked": time.time(),
         "note": "подожду" if st == "flood" else ""})
    return "watch", f"👀 @{name} занят — слежу, займу, как освободится"


# ---------- кандидаты: идеи и пожелания ----------

PREFIXES = ["neo", "dark", "pure", "zen", "max", "sky", "nova", "ultra", "iron", "void", "lux", "axon", "echo", "flux",
            "top", "one", "pro", "big", "go"]
WORDS = ["vibe", "pulse", "frame", "orbit", "spark", "blaze", "drift", "pixel", "storm", "quartz", "nexus", "prime",
         "glitch", "mirage", "cipher", "halo", "onyx", "vertex", "zenith", "aura", "flare", "titan", "atlas", "rune",
         "comet", "lunar", "ember", "frost", "prism", "sonic", "vapor", "nomad", "pilot", "rebel", "ghost", "delta",
         "omega", "sigma", "karma", "mojo", "hype", "flow", "loop", "core", "edge", "wave", "zone", "club", "lab"]
NAMES = ["alex", "max", "leo", "nik", "dan", "mark", "tim", "ivan", "gleb", "sam", "ben", "ray", "kai", "ron", "art"]
SUFFIX = ["ov", "in", "ski", "son", "man", "er", "ly", "io", "ix", "us"]


def wish_text(w=None):
    w = w or wish()
    ln = next((cap for k, cap, _ in LENGTHS if k == w["len"]), "любая")
    st = next((cap for k, cap in STYLES if k == w["style"]), "любой")
    return f"{ln}, {st}" + (f", тема «{w['theme']}»" if w.get("theme") else "") + \
        (", только буквы" if w.get("letters") else "") + f", поймать {w.get('count', 1)}"


def fits(u, w):
    lo, hi = next((rng for k, _, rng in LENGTHS if k == w["len"]), (5, 12))
    if not lo <= len(u) <= hi or not NAME.fullmatch(u) or "__" in u:
        return False
    if w.get("letters") and not u.isalpha():
        return False
    st = w.get("style", "any")
    if st == "digit" and not re.search(r"\d$", u):
        return False
    if st in ("word", "name") and not u.isalpha():
        return False
    return True


def offline_candidates(w, n=40):
    """Без Claude: сочетания из своих списков под пожелания. Тема влияет только на случайность."""
    rnd = random.Random(f"{w.get('theme')}{time.time() // 3600}")
    out, tries = [], 0
    while len(out) < n and tries < 2000:
        tries += 1
        st = w.get("style", "any")
        if st == "any":
            st = rnd.choice(["word", "two", "digit", "name"])
        if st == "word":
            u = rnd.choice(WORDS) + (rnd.choice(SUFFIX) if rnd.random() < 0.3 else "")
        elif st == "two":
            u = rnd.choice(PREFIXES) + rnd.choice(WORDS)
        elif st == "digit":
            u = rnd.choice(WORDS + PREFIXES) + str(rnd.choice([1, 7, 9, 10, 21, 42, 77, 99, 2026]))
        else:
            u = rnd.choice(NAMES) + rnd.choice(SUFFIX + ["", "", "x", "y"])
        if fits(u, w) and u not in out:
            out.append(u)
    return out


def claude_candidates(w, n=40):
    """Крутые имена от Claude под пожелания (claude -p на сервере). Нет Claude — []."""
    lo, hi = next((rng for k, _, rng in LENGTHS if k == w["len"]), (5, 12))
    style = {"word": "одно короткое слово", "two": "два слова слитно", "digit": "слово и цифра в конце",
             "name": "похоже на имя или фамилию", "any": "разные стили: одно слово, два слова слитно, "
             "слово с цифрой, неологизм, имя"}[w.get("style", "any")]
    prompt = (f"Придумай {n} крутых, коротких и запоминающихся юзернеймов Telegram для канала"
              + (f" на тему «{w['theme']}»" if w.get("theme") else "") + f". Длина {lo}–{hi} символов, стиль: {style}. "
              + ("Только латинские буквы, без цифр и _. " if w.get("letters") else "Латиница, цифры и _. ")
              + "Без слов official, channel, bot, telegram. Ответь только JSON-списком строк.")
    try:
        got = kit.claude(prompt, timeout=90, as_json=True) or []
    except kit.Fail:
        got = []
    out = []
    for s in got if isinstance(got, list) else []:
        for u in parse_names(str(s)):
            if fits(u, w) and u not in out:
                out.append(u)
    return out


def candidates(w=None, n=40, exclude=()):
    """Claude + свой генератор, без повторов и уже проверенных."""
    w = w or wish()
    seen = set(exclude) | {x["name"] for x in names()}
    out = []
    for u in claude_candidates(w, n) + offline_candidates(w, n):
        if u not in seen and u not in out:
            out.append(u)
    return out[:n]


def ideas(theme="", n=20):
    """«✨ Идеи»: имена на тему (любой длины и стиля, с цифрами тоже)."""
    return candidates({**DEFAULT_WISH, "len": "any", "style": "any", "theme": theme, "letters": False}, n)


def check_ideas(items, limit=10):
    """Проверить идеи у Секретаря по очереди (с паузой между запросами): [(имя, статус)]. Flood — остальные «?»."""
    res, wait = [], 0
    for i, u in enumerate(items[:limit]):
        if wait:
            res.append((u, "?"))
            continue
        ok, out = check(u)
        st = out.get("status") if ok and isinstance(out, dict) else "?"
        if st == "flood":
            wait = out.get("wait") or 1
            st = "?"
        res.append((u, st))
        if i < limit - 1 and not bot.testing_mode:
            time.sleep(1.5)
    return res


IDEA_MARK = {"free": "✅ свободно", "taken": "❌ занято", "fragment": "💎 Fragment", "invalid": "🚫 нельзя", "?": "❔ не проверил"}


def ideas_card(theme, res):
    lines = [f"{IDEA_MARK.get(st, st)} — @{kit.esc(u)}" for u, st in res] or ["<i>Ничего не придумал — попробуй другую тему.</i>"]
    free = [u for u, st in res if st == "free"][:6]
    taken = [u for u, st in res if st == "taken"][:6]
    rows = kit.rows([kit.b(f"🎯 Занять @{u}", f"grab|{u}", "success") for u in free], 2)
    rows += kit.rows([kit.b(f"👀 Следить @{u}", f"watch|{u}") for u in taken], 2)
    rows += [[kit.b("🔁 Ещё идеи", f"ideas|{theme[:40]}", "primary"), kit.b("‹ Главное", "home")]]
    return kit.card(f"✨ <b>Идеи{(' · ' + kit.esc(theme)) if theme else ''}</b>", lines,
                    "<i>Свободные можно занять сразу, занятые — поставить на слежение.</i>"), rows


def run_ideas(c, theme):
    mid = c.say(kit.card("✨ <b>Думаю…</b>", ["Придумываю имена и проверяю, что свободно."]))
    res = check_ideas(ideas(theme))
    text, rows = ideas_card(theme, res)
    bot.edit(c.chat, mid, text, rows)


# ---------- фоновый цикл ----------

class Watcher:
    """По кругу проверяет имена со статусом watch (свободно → grab) и, если включена ловля по пожеланиям, —
    кандидатов из очереди (свободно → grab, пока не наберёт нужное число). FloodWait — пауза.
    tick() делает один шаг и возвращает, сколько секунд спать (в тестах зовётся руками)."""

    def __init__(self):
        self.i = 0
        self.agent_down_said = -1e9  # когда последний раз писали, что Секретарь недоступен
        self.last = ""  # что происходило в последний раз — для экрана
        self.pause_until = 0

    def tick(self, now=None):
        now = now or time.time()
        if now < self.pause_until:
            return min(self.pause_until - now, 60)
        watch = [n for n in names() if n["status"] == "watch"]
        h = hunt()
        self.i += 1
        if h["on"] and (not watch or self.i % 2):
            return self.hunt_step(h, now)
        if not watch:
            return 30
        n = watch[(self.i // 2) % len(watch)]
        ok, out = check(n["name"])
        if not ok:
            return self.agent_down(out, now)
        self.agent_down_said = -1e9
        st = out.get("status")
        entry = find(n["name"]) or n
        entry.update(checked=now, checks=entry.get("checks", 0) + 1)
        if st == "free":
            put(entry)
            r = grab(n["name"])
            self.last = f"@{n['name']}: " + ("поймал" if r == "ok" else r)
            return 1
        if st == "flood":
            put(entry)
            return self.flood(out, now)
        if st in ("fragment", "invalid"):
            entry.update(status=st, note=STATUS[st][1])
            put(entry)
            bot.notify(f"{STATUS[st][0]} @{kit.esc(n['name'])} — {STATUS[st][1]}, больше не слежу.")
            self.last = f"@{n['name']}: {STATUS[st][1]}"
            return settings()["interval"]
        entry["note"] = f"ошибка: {out.get('error')}" if st == "error" else ""
        put(entry)
        self.last = f"@{n['name']}: занят"
        return settings()["interval"]

    def hunt_step(self, h, now):
        queue = list(h["queue"])
        if not queue:
            queue = candidates(wish(), 40, exclude=h["seen"])
            if not queue:
                self.last = "кандидаты кончились — поменяй пожелания"
                set_hunt(queue=[])
                return 300
        u = queue.pop(0)
        ok, out = check(u)
        if not ok:
            set_hunt(queue=[u] + queue)
            return self.agent_down(out, now)
        self.agent_down_said = -1e9
        st = out.get("status")
        if st == "flood":
            set_hunt(queue=[u] + queue)
            return self.flood(out, now)
        seen = (h["seen"] + [u])[-500:]
        set_hunt(queue=queue, seen=seen, checked=h["checked"] + 1)
        if st == "free":
            w = wish()
            r = grab(u, how="по пожеланиям")
            self.last = f"@{u}: " + ("поймал" if r == "ok" else r)
            if r == "ok":
                caught = h["caught"] + [u]
                done = len(caught) >= int(w.get("count") or 1)
                set_hunt(caught=caught, on=not done)
                if done:
                    bot.notify(kit.card("🏁 <b>Ловля по пожеланиям закончена</b>",
                                        ["Поймал: " + ", ".join("@" + x for x in caught)]),
                               [[kit.b("🎲 Ещё раз", "wish"), kit.b("📋 Список", "list")]])
            elif r == "too_many":
                set_hunt(on=False)
            return 1
        self.last = f"@{u}: " + {"taken": "занят", "fragment": "Fragment", "invalid": "нельзя"}.get(st, str(st))
        return settings()["interval"]

    def agent_down(self, out, now):
        self.last = f"Секретарь недоступен: {out}"
        if now - self.agent_down_said > 3600:
            self.agent_down_said = now
            bot.notify(kit.error(f"Не могу проверять имена: {kit.esc(out)}. Войди в аккаунт в Главном (/login) — "
                                 "продолжу сам."))
        return 60

    def flood(self, out, now):
        wait = int(out.get("wait") or 60)
        self.pause_until = now + wait
        self.last = f"Telegram просит подождать {wait} с"
        if wait >= FLOOD_NOTE:
            bot.notify(f"⏸ Telegram просит подождать {wait // 60} мин — проверки продолжу сам.")
        return min(wait, 60)

    def loop(self):
        while True:
            try:
                delay = self.tick()
            except Exception:
                traceback.print_exc()
                delay = 30
            time.sleep(max(1, delay))


watcher = Watcher()


# ---------- экраны ----------

def status_line():
    ok, out = agent.call("_status", timeout=8)
    if not ok or not isinstance(out, dict):
        return "🔴 Секретарь не запущен — ловить не смогу"
    acc = out.get("account")
    if not acc:
        return "🔴 Секретарь не вошёл в аккаунт — в Главном /login"
    return f"🟢 Аккаунт: {kit.esc(acc.get('name') or '')}" + (f" (@{kit.esc(acc['username'])})" if acc.get("username") else "")


def hunt_line():
    h = hunt()
    if not h["on"]:
        return None
    return (f"🎲 По пожеланиям: {kit.esc(wish_text())} · проверил {h['checked']}, "
            f"поймал {len(h['caught'])}/{wish().get('count', 1)}")


def home_text():
    items = names()
    watch = [n for n in items if n["status"] == "watch"]
    caught = [n for n in items if n["status"] == "caught"]
    s = settings()
    lines = [status_line(),
             f"👀 Слежу: {len(watch)}" + (" — " + ", ".join("@" + n["name"] for n in watch[:5]) +
                                        (" …" if len(watch) > 5 else "") if watch else ""),
             hunt_line(),
             f"🎯 Поймал: {len(caught)}" + (" — " + ", ".join("@" + n["name"] for n in caught[-3:]) if caught else ""),
             f"⏱ Проверка раз в {s['interval']} с · куда: " + (kit.esc(s["target_title"]) if s["target"] else "новый канал")]
    if watcher.last:
        lines.append(f"<i>Последнее: {kit.esc(watcher.last)}</i>")
    return kit.card("🎯 <b>Ловец</b>", lines, "<i>Пришли @имя (можно списком) — проверю и займу или буду следить.</i>")


def home_rows():
    h = hunt()
    return [[bot.app_button()],
            [kit.b("⏹ Стоп по пожеланиям" if h["on"] else "🎲 По пожеланиям", "hunt|off" if h["on"] else "wish",
                   "danger" if h["on"] else "primary"), kit.b("➕ Имя", "add")],
            [kit.b("✨ Идеи", "ideas|"), kit.b("📋 Список", "list"), kit.b("⚙️ Настройки", "settings")],
            [kit.support_button()]]


@bot.home
def home(c):
    c.show(home_text(), home_rows())


@bot.command("help")
def help_(c):
    c.say(kit.card("💡 <b>Что я умею</b>", [
        "• Пришли @имя или несколько через пробел — проверю: свободно → займу на канал сразу, занято → слежу и займу, "
        "как освободится, продаётся на Fragment → скажу (такие не ловятся).",
        "• «🎲 По пожеланиям» — выбери длину, стиль, тему и сколько поймать: сам придумываю имена, проверяю "
        "по кругу и занимаю свободные.",
        "• «✨ Идеи» или текст «крутые имена про космос» — придумаю и покажу, что свободно.",
        "• «⚙️ Настройки» — как часто проверять и на какой канал ставить (новый или свой).",
        "• Лимит Telegram: до 10 публичных каналов на аккаунт (у Premium больше).",
        "• Ловлю через твой аккаунт (Секретарь в Главном) — он должен быть в аккаунте.",
    ]), [[kit.b("‹ Главное", "home")]])


@bot.command("list")
@bot.button("list")
def list_(c):
    items = sorted(names(), key=lambda n: ({"watch": 0, "paused": 1, "caught": 2}.get(n["status"], 3), n["name"]))
    lines = []
    for n in items:
        ico, cap = STATUS.get(n["status"], ("•", n["status"]))
        extra = f" — <a href=\"{n['link']}\">канал</a>" if n.get("link") else (f" — {kit.esc(n['note'])}" if n.get("note") else "")
        lines.append(f"{ico} @{kit.esc(n['name'])} · {cap}{extra}")
    rows = kit.rows([kit.b(f"✖ @{n['name']}", f"rm|{n['name']}") for n in items if n["status"] != "caught"][:12], 3)
    if any(n["status"] == "paused" for n in items):
        rows = [[kit.b("▶️ Снова следить", "resume", "primary")]] + rows
    if any(n["status"] == "caught" for n in items):
        rows.append([kit.b("🧹 Убрать пойманные", "clear_caught")])
    rows.append([kit.b("➕ Добавить", "add", "primary"), kit.b("‹ Главное", "home")])
    c.show(kit.card(f"📋 <b>Имена</b> · {len(items)}", lines or ["<i>Пока пусто — пришли @имя.</i>"],
                    "<i>Проверяю занятые по кругу, свободные занимаю сразу.</i>" if items else ""), rows)


@bot.button("rm")
def remove(c):
    drop(c.args[0])
    c.toast(f"@{c.args[0]} убрал")
    list_(c)


def resume_paused():
    for n in names():
        if n["status"] == "paused":
            n.update(status="watch", note="")
            put(n)


@bot.button("resume")
def resume(c):
    resume_paused()
    c.toast("Слежу дальше")
    list_(c)


@bot.button("clear_caught")
def clear_caught(c):
    bot.db.update("names", lambda items: [x for x in items if x["status"] != "caught"], [])
    list_(c)


@bot.button("add")
def add_btn(c):
    c.ask("names", "✍️ Какие имена ловить? Напиши @имя — можно несколько через пробел или строкой.")


@bot.button("grab")
def grab_btn(c):
    u = c.args[0]
    c.toast(f"Занимаю @{u}…")
    r = grab(u, say=False)
    if r == "ok":
        n = find(u)
        c.say(*caught_card(u, n["link"], n.get("title")))
    elif r == "taken":
        c.say(f"👀 @{kit.esc(u)} только что заняли — буду следить.")
    else:
        c.say(kit.error(f"@{kit.esc(u)}: " + kit.esc((find(u) or {}).get("note") or r)))


@bot.button("watch")
def watch_btn(c):
    st, line = add(c.args[0])
    c.toast(line[:190])
    c.say(kit.esc(line))


def add_many(c, text):
    found = parse_names(text)
    if not found:
        return c.say(kit.error("Не вижу юзернеймов. Пример: <code>@coolname</code> или <code>t.me/coolname</code> "
                               "(от 5 символов, латиница)"))
    results = [(u, *add(u, say=False)) for u in found[:MAX_NAMES]]
    c.say(kit.card("📥 <b>Принял</b>", [kit.esc(line) for _, _, line in results],
                   "<i>Занятые проверяю по кругу; как освободятся — займу и напишу.</i>"),
          [[kit.b("📋 Список", "list"), kit.b("‹ Главное", "home")]])
    for u, st, _ in results:  # пойманные прямо сейчас — отдельной карточкой со ссылкой
        n = find(u)
        if st == "ok" and n and n.get("link"):
            c.say(*caught_card(u, n["link"], n.get("title")))


@bot.reply("names")
def got_names(c):
    add_many(c, c.text)


@bot.command("ideas")
def ideas_cmd(c):
    if c.text.strip():
        return bot.later(run_ideas, c, c.text.strip()[:60])
    c.ask("theme", "✨ На какую тему имена? Напиши тему («космос», «финансы», «мемы») или «любые».")


@bot.button("ideas")
def ideas_btn(c):
    theme = c.args[0] if c.args else ""
    if theme:
        return bot.later(run_ideas, c, theme)
    c.ask("theme", "✨ На какую тему имена? Напиши тему («космос», «финансы», «мемы») или «любые».")


@bot.reply("theme")
def got_theme(c):
    theme = c.text.strip()
    bot.later(run_ideas, c, "" if theme.lower() in ("любые", "любая", "без темы", "-") else theme[:60])


@bot.text
def on_text(c):
    text = c.text.strip()
    if looks_like_names(text):
        return add_many(c, text)
    low = text.lower()
    if any(w in low for w in ("иде", "придум", "крут", "название", "назван", "имена", "имя")):
        theme = re.sub(r"\b(придумай|предложи|идеи|идей|крут\w*|имена|имён|имя|названи\w*|для|про|на тему|тема|"
                       r"юзернейм\w*|ник\w*|канал\w*|мне|пожалуйста)\b", " ", low)
        return bot.later(run_ideas, c, " ".join(theme.split()).strip(" :«»\"")[:60])
    if parse_names(text) or "@" in text or "t.me" in text:
        return add_many(c, text)
    home(c)


# --- 🎲 по пожеланиям ---

def mark(on, text):
    return ("• " if on else "") + text


def wish_text_card():
    w, h = wish(), hunt()
    lines = [f"Сейчас: <b>{kit.esc(wish_text(w))}</b>"]
    if h["on"]:
        lines.append(f"🟢 Ловлю: проверил {h['checked']}, поймал {len(h['caught'])}/{w.get('count', 1)}")
    elif h["caught"]:
        lines.append("Прошлый раз поймал: " + ", ".join("@" + x for x in h["caught"]))
    return kit.card("🎲 <b>По пожеланиям</b>", lines,
                    "<i>Сам придумываю имена под пожелания, проверяю по кругу и занимаю свободные.</i>")


def wish_rows():
    w, h = wish(), hunt()
    rows = [kit.rows([kit.b(mark(w["len"] == k, cap), f"wl|{k}") for k, cap, _ in LENGTHS], 2)[0],
            kit.rows([kit.b(mark(w["len"] == k, cap), f"wl|{k}") for k, cap, _ in LENGTHS], 2)[1]]
    rows += kit.rows([kit.b(mark(w["style"] == k, cap), f"ws|{k}") for k, cap in STYLES], 3)
    rows.append([kit.b(("🔤 Только буквы ✓" if w["letters"] else "🔤 Только буквы"), "wt"),
                 kit.b(f"✍️ Тема{': ' + w['theme'][:10] if w['theme'] else ''}", "wtheme")])
    rows.append(kit.rows([kit.b(mark(w["count"] == n, f"поймать {n}"), f"wc|{n}") for n in COUNTS], 4)[0])
    rows.append([kit.b("⏹ Стоп", "hunt|off", "danger") if h["on"] else kit.b("🚀 Ловить", "hunt|on", "success"),
                 kit.b("‹ Главное", "home")])
    return rows


@bot.command("wish")
@bot.button("wish")
def wish_(c):
    c.show(wish_text_card(), wish_rows())


@bot.button("wl")
def wish_len(c):
    if c.args[0] in {k for k, _, _ in LENGTHS}:
        set_wish("len", c.args[0])
    wish_(c)


@bot.button("ws")
def wish_style(c):
    if c.args[0] in {k for k, _ in STYLES}:
        set_wish("style", c.args[0])
    wish_(c)


@bot.button("wt")
def wish_letters(c):
    set_wish("letters", not wish()["letters"])
    wish_(c)


@bot.button("wc")
def wish_count(c):
    if int(c.args[0]) in COUNTS:
        set_wish("count", int(c.args[0]))
    wish_(c)


@bot.button("wtheme")
def wish_theme(c):
    c.ask("wtheme", "✍️ Тема имён («космос», «крипта», «мемы») или «любая».", [[kit.b("Без темы", "wtheme0"), kit.b("Отмена", "wish")]])


@bot.button("wtheme0")
def wish_theme_none(c):
    set_wish("theme", "")
    wish_(c)


@bot.reply("wtheme")
def got_wish_theme(c):
    t = c.text.strip()
    set_wish("theme", "" if t.lower() in ("любая", "любые", "без темы", "-") else t[:60])
    wish_(kit.Ctx(bot, c.chat, c.user))


def hunt_on():
    set_hunt(on=True, checked=0, caught=[], queue=[], started=stamp())


@bot.button("hunt")
def hunt_btn(c):
    if c.args[0] == "on":
        hunt_on()
        c.toast("Ловлю!")
    else:
        set_hunt(on=False)
        c.toast("Остановил")
    wish_(c)


# --- ⚙️ настройки ---

def settings_text():
    s = settings()
    return kit.card("⚙️ <b>Как ловить</b>", [
        f"⏱ Проверка раз в <b>{s['interval']} с</b> (чаще — Telegram попросит подождать)",
        "📣 Куда ставить имя: <b>" + (kit.esc(s["target_title"]) if s["target"] else "новый канал (создам сам)") + "</b>",
    ], "<i>Пойманное имя — это канал: потом переименуешь или переставишь имя куда нужно.</i>")


def settings_rows():
    s = settings()
    iv = kit.rows([kit.b(mark(s["interval"] == i, f"{i} с"), f"iv|{i}") for i in INTERVALS], 3)
    return iv + [[kit.b("📣 Новый канал", "target|0", "primary" if not s["target"] else None),
                  kit.b("📂 Свой канал…", "channels")],
                 [kit.b("‹ Главное", "home")]]


@bot.command("settings")
@bot.button("settings")
def settings_(c):
    c.show(settings_text(), settings_rows())


@bot.button("iv")
def interval(c):
    v = int(c.args[0])
    if v in INTERVALS:
        set_setting("interval", v)
    c.toast(f"Раз в {v} с")
    settings_(c)


@bot.button("target")
def target(c):
    cid = int(c.args[0] or 0)
    title = c.args[1] if len(c.args) > 1 else ""
    set_setting("target", cid)
    set_setting("target_title", title)
    c.toast("Имя пойдёт на " + (f"«{title}»" if cid else "новый канал"))
    settings_(c)


def own_channels():
    """Свои каналы без юзернейма (пойманное имя не должно заменить чужое)."""
    ok, out = agent.call("mychannels", {"fresh": True}, timeout=60)
    if not ok or not isinstance(out, list):
        raise kit.Fail(str(out))
    return [ch for ch in out if not ch.get("username")]


@bot.button("channels")
def channels(c):
    try:
        free = own_channels()
    except kit.Fail as e:
        return c.show(kit.error(kit.esc(e)), [[kit.b("‹ Назад", "settings")]])
    if not free:
        return c.show(kit.card("📂 <b>Свои каналы</b>", ["У всех твоих каналов уже есть юзернейм — пойманное имя "
                                                       "заменило бы его. Оставлю «новый канал»."]),
                      [[kit.b("‹ Назад", "settings")]])
    rows = kit.rows([kit.b(ch["title"], f"target|{ch['id']}|{ch['title'][:30]}") for ch in free[:12]], 2)
    c.show(kit.card("📂 <b>На какой канал?</b>", ["Каналы без юзернейма, где ты создатель:"]),
           rows + [[kit.b("‹ Назад", "settings")]])


# ---------- мини-приложение ----------

@bot.api("state")
def api_state(r):
    ok, out = agent.call("_status", timeout=8)
    account = out.get("account") if ok and isinstance(out, dict) else None
    return {"names": sorted(names(), key=lambda n: ({"watch": 0, "paused": 1, "caught": 2}.get(n["status"], 3), n["name"])),
            "settings": settings(), "intervals": INTERVALS, "wish": wish(), "wish_text": wish_text(), "hunt": hunt(),
            "options": {"len": [[k, cap] for k, cap, _ in LENGTHS], "style": [list(x) for x in STYLES], "count": COUNTS},
            "account": account, "agent": ok, "last": watcher.last, "status": {k: v[1] for k, v in STATUS.items()}}


@bot.api("add")
def api_add(r):
    found = parse_names(r.body.get("text") or "")
    if not found:
        raise kit.Fail("Не вижу юзернеймов: @имя, от 5 символов, латиница")
    added = []
    for u in found[:MAX_NAMES]:
        st, line = add(u, say=True)
        added.append({"name": u, "status": st, "line": line})
    return {"added": added, **api_state(r)}


@bot.api("remove")
def api_remove(r):
    drop(str(r.body.get("name") or "").lstrip("@").lower())
    return api_state(r)


@bot.api("grab")
def api_grab(r):
    u = str(r.body.get("name") or "").lstrip("@").lower()
    if not parse_names(u):
        raise kit.Fail("Не похоже на юзернейм")
    return {"result": grab(u, say=True), **api_state(r)}


@bot.api("resume")
def api_resume(r):
    resume_paused()
    return api_state(r)


@bot.api("settings")
def api_settings(r):
    if "interval" in r.body:
        v = int(r.body["interval"])
        if v not in INTERVALS:
            raise kit.Fail("Такого интервала нет")
        set_setting("interval", v)
    if "target" in r.body:
        set_setting("target", int(r.body["target"] or 0))
        set_setting("target_title", str(r.body.get("target_title") or "")[:60])
    return api_state(r)


@bot.api("wish")
def api_wish(r):
    b = r.body
    if b.get("len") in {k for k, _, _ in LENGTHS}:
        set_wish("len", b["len"])
    if b.get("style") in {k for k, _ in STYLES}:
        set_wish("style", b["style"])
    if "theme" in b:
        set_wish("theme", str(b["theme"] or "").strip()[:60])
    if "letters" in b:
        set_wish("letters", bool(b["letters"]))
    if b.get("count") in COUNTS:
        set_wish("count", int(b["count"]))
    if b.get("hunt") == "on":
        hunt_on()
    elif b.get("hunt") == "off":
        set_hunt(on=False)
    return api_state(r)


@bot.api("channels")
def api_channels(r):
    return {"channels": own_channels()}


@bot.api("ideas")
def api_ideas(r):
    theme = str(r.body.get("theme") or "").strip()[:60]
    res = check_ideas(ideas(theme))
    return {"theme": theme, "ideas": [{"name": u, "status": st, "mark": IDEA_MARK.get(st, st)} for u, st in res]}


# ---------- https-адрес (как у КПП): catcher.<ip-через-дефисы>.sslip.io через Caddy планера ----------

def server_ip():
    if os.environ.get("CATCHER_IP"):
        return os.environ["CATCHER_IP"]
    for url in ("https://api.ipify.org", "https://ipv4.icanhazip.com", "https://ifconfig.me/ip"):
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                ip = r.read().decode().strip()
            if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", ip):
                return ip
        except Exception:
            pass
    return None


def healthy(url):
    try:
        with urllib.request.urlopen(url + "health", timeout=20) as r:
            return r.read() == b"ok"
    except Exception:
        return False


def https():
    """Адреса нет в окружении — ждём, пока catcher.<host> ответит, и включаем кнопку приложения."""
    while True:
        ip = server_ip()
        if ip:
            url = f"https://catcher.{ip.replace('.', '-')}.sslip.io/"
            if healthy(url):
                if bot.app_url != url:
                    bot.app_url = url
                    bot._menu()
                time.sleep(600)
                continue
        time.sleep(60)


if __name__ == "__main__":
    if not bot.token:
        sys.exit("Нет CATCHER_TOKEN (токен от @BotFather). Впиши его в /etc/catcher-bot.env")
    threading.Thread(target=watcher.loop, daemon=True).start()
    if not bot.app_url:
        threading.Thread(target=https, daemon=True).start()
    bot.run()
