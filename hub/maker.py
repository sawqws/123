"""Главный делает и дорабатывает ботов по ТЗ: Claude Code на сервере пишет код, Главный запускает его в pm2.

🛠 Сделать бота: ТЗ → Claude придумывает имя и пишет код в /root/bots/<slug>/ → бот создаётся в @BotFather
   через Секретаря (tgbot newbot, Глеб подтверждает кнопкой) или Глеб присылает токен сам → запуск в pm2.
✏️ Доработать: любой бот (из репозитория или сделанный здесь) + что поменять → Claude правит код, гоняет
   тесты, коммитит (локально) → перезапуск. «↩️ Откатить» — git revert этого коммита.

Правки ботов из репозитория — локальные коммиты в /root/horo; «Обновить всех» переносит их поверх main (rebase).
Модуль подключается из hub.py: maker.ui = модуль hub (send, edit, b, esc, sh, setting, owner, REPO, BOT_TOKEN).
"""
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import urllib.request

ui = None
BOTS = os.environ.get("HUB_BOTS_DIR") or "/root/bots"
CLAUDE_TIMEOUT = 40 * 60
job = threading.Lock()  # Claude работает над одним ботом за раз

# боты из репозитория: pm2-имя -> (подпись, папки с кодом, файл с токеном, переменная токена, что ставит Главный)
CATALOG = {
    "horo-bot": ("📚 HDP", ["bot", "horo"], "/etc/horo-bot.env", "TELEGRAM_TOKEN", None,
                 "задания HDP, оценки, черновики учителю"),
    "planner-bot": ("🗓 Планер «Ритм»", ["planner"], "/etc/planner-bot.env", "PLANNER_TOKEN", "planner",
                    "день, неделя, цели"),
    "coach-bot": ("⚡ Тренер «Форма»", ["coach"], "/etc/coach-bot.env", "COACH_TOKEN", "coach",
                  "WHOOP: сон, восстановление, тренировки, еда"),
    "kpp-bot": ("🚧 КПП", ["kpp"], "/etc/kpp-bot.env", "KPP_TOKEN", "kpp", "пропуск для такси в ЖК"),
    "avito-bot": ("🛍 Авито", ["avito"], "/etc/avito-bot.env", "AVITO_TOKEN", "avito",
                  "фото на белом фоне, описание, средняя цена"),
    "eye-bot": ("👁 Глаз", ["eye"], "/etc/eye-bot.env", "EYE_TOKEN", "eye",
                "Claude в очках Ray-Ban Meta: фото и разговор голосом"),
    "tg-agent": ("🤖 Секретарь", ["tgbot"], "/etc/tg-agent.env", "TG_AGENT_TOKEN", "tgbot",
                 "твой Telegram: чаты, ответы, расписания"),
    "hub-bot": ("👑 Главный", ["hub"], "/etc/hub-bot.env", "HUB_TOKEN", None, "следит за ботами, делает новых"),
}


def made():
    return ui.setting("made") or {}


def set_made(slug, info=None):
    with ui.lock:
        m = made()
        if info is None:
            m.pop(slug, None)
        else:
            m[slug] = info
        ui.setting("made", m)


def label(name):
    if name in CATALOG:
        return CATALOG[name][0]
    for info in made().values():
        if info.get("pm2") == name:
            return "🧩 " + info.get("name", name)
    return None


# ---------- имена ботов (getMe по токену, кэш) ----------

def env_value(path, var):
    try:
        with open(path) as f:
            for line in f:
                k, _, v = line.strip().partition("=")
                if k == var and v:
                    return v.strip()
    except OSError:
        pass
    return None


def username(token):
    if not token:
        return None
    cache = ui.setting("usernames") or {}
    key = token.split(":")[0]
    if key in cache:
        return cache[key]
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getMe", timeout=20) as r:
            name = json.load(r)["result"].get("username")
    except Exception:
        return None
    cache[key] = name
    ui.setting("usernames", cache)
    return name


def all_bots(rows=None):
    """[{pm2, label, about, status, username, install}] — все боты: из репозитория и сделанные здесь.
    rows — уже полученный ui.apps() (чтобы не спрашивать pm2 второй раз)."""
    running = {a["name"]: a["status"] for a in (ui.apps() if rows is None else rows) or []}
    res = []
    for pm2, (lbl, _, env, var, kind, about) in CATALOG.items():
        tok = ui.TOKEN if pm2 == ui.SELF else env_value(env, var)
        res.append({"pm2": pm2, "label": lbl, "about": about, "status": running.get(pm2),
                    "username": username(tok), "install": kind})
    for slug, info in made().items():
        res.append({"pm2": info["pm2"], "label": "🧩 " + info["name"], "about": info.get("summary", ""),
                    "status": running.get(info["pm2"]), "username": info.get("username"), "install": None,
                    "slug": slug})
    return res


FOLDER_HELP = ("<blockquote expandable>📂 <b>Папка «Боты»</b> — один раз, 30 секунд:\n"
               "Настройки → Папки с чатами → Создать папку → название «Боты» → «Добавить чаты» → "
               "в «Типах чатов» отметь <b>Боты</b> → Сохранить.\n"
               "Туда сами попадут все боты, в том числе новые. Только сначала открой каждого и нажми «Начать»."
               "</blockquote>")


def bots_text(rows, intro=False):
    """Две строки на бота: «🟢 HDP · @user» и что он делает курсивом. Без отступов пробелами — шрифт пропорциональный."""
    lines = []
    for r in rows:
        mark = "🟢" if r["status"] == "online" else "🔴" if r["status"] else "⚪️"
        who = f" · @{ui.esc(r['username'])}" if r["username"] else ""
        state = "" if r["status"] == "online" else " · не работает" if r["status"] else " · не поставлен"
        lines.append(f"{mark} <b>{ui.esc(ui.short(r['label']))}</b>{who}{state}")
        if r["about"]:
            lines.append(f"<i>{ui.esc(r['about'])}</i>")
    title = ("👋 <b>Начнём!</b> Вот все твои боты — открой каждого и нажми «Начать»" if intro
             else "📂 <b>Все мои боты</b>")
    return ui.card(title, lines) + "\n" + FOLDER_HELP


def bots_buttons(rows):
    btns = []
    for r in rows:
        if r["username"] and r["status"] == "online" and r["pm2"] != ui.SELF:
            btns.append({"text": ui.clip(f"▶️ {ui.short(r['label'])}"), "url": f"https://t.me/{r['username']}?start=hub"})
        elif not r["status"] and r["install"]:
            btns.append(ui.b(ui.clip(f"➕ {ui.short(r['label'])}"), f"add|{r['install']}", "success"))
    btns = ui.pairs(btns)
    btns.append([ui.b("🛠 Сделать бота", "mk"), ui.b("✏️ Доработать", "fix")])
    btns.append([ui.b("‹ К ботам", "home")])
    return btns


def show_all(chat, mid=None, intro=False):
    rows = all_bots()
    return ui.show(chat, mid, bots_text(rows, intro), bots_buttons(rows))


# ---------- Claude ----------

TOOLS = ["Read", "Write", "Edit", "Glob", "Grep", "Bash(python3:*)", "Bash(node:*)", "Bash(ls:*)", "Bash(cat:*)",
         "Bash(git diff:*)", "Bash(git status:*)", "Bash(git log:*)", "Bash(mkdir:*)"]


def claude(prompt, cwd, on_step=None, timeout=CLAUDE_TIMEOUT, tools=True):
    """(ok, итоговый текст). on_step(строка) — что Claude делает сейчас (для живого статуса)."""
    cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose"]
    if tools:
        cmd += ["--permission-mode", "acceptEdits", "--allowedTools", *TOOLS]
    else:
        cmd += ["--disallowedTools", "Bash", "Edit", "Write", "WebFetch", "WebSearch", "Task"]
    try:
        p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    except FileNotFoundError:
        return False, "Claude Code не установлен на сервере"
    timer = threading.Timer(timeout, p.kill)
    timer.start()
    result, err = "", ""
    try:
        for line in p.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                err += line
                continue
            if ev.get("type") == "assistant" and on_step:
                for c in (ev.get("message") or {}).get("content") or []:
                    if c.get("type") == "tool_use":
                        on_step(step_text(c.get("name"), c.get("input") or {}))
            elif ev.get("type") == "result":
                result = ev.get("result") or ""
                if ev.get("is_error"):
                    err += result
                    result = ""
        p.wait()
    finally:
        timer.cancel()
    if p.returncode != 0 or not result:
        low = err.lower()
        if any(w in low for w in ("login", "api key", "401", "unauthorized")):
            return False, "Claude на сервере не вошёл в аккаунт"
        if any(w in low for w in ("usage limit", "rate limit", "limit reached", "429")):
            return False, "у Claude закончился лимит, попробуй позже"
        return False, "Claude не закончил работу" + (f": {err.strip()[-300:]}" if err.strip() else "")
    return True, result.strip()


def step_text(name, inp):
    path = os.path.basename(str(inp.get("file_path") or inp.get("path") or ""))
    return {"Read": f"читаю {path}", "Write": f"пишу {path}", "Edit": f"правлю {path}",
            "Glob": "ищу файлы", "Grep": "ищу в коде"}.get(name) or (
        "запускаю: " + str(inp.get("command", ""))[:60] if name == "Bash" else str(name))


class Status:
    """Живой статус в одном сообщении: что делается и сколько прошло."""

    def __init__(self, chat, title):
        self.chat, self.title, self.start, self.steps = chat, title, time.time(), []
        self.mid = (ui.send(chat, f"⏳ {title}").get("result") or {}).get("message_id")
        self.last = 0

    def step(self, text, force=False):
        self.steps = (self.steps + [text])[-4:]
        if force or time.time() - self.last > 8:
            self.last = time.time()
            mins = int(time.time() - self.start) // 60
            body = "\n".join(f"{'▸' if i == len(self.steps) - 1 else '•'} {ui.esc(s)}" for i, s in enumerate(self.steps))
            try:
                ui.edit(self.chat, self.mid, f"⏳ <b>{ui.esc(self.title)}</b> · {mins} мин\n<blockquote>{body}</blockquote>")
            except Exception:
                pass

    def done(self, text, buttons=None):
        ui.edit(self.chat, self.mid, text, buttons)


def json_from(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except ValueError:
        return None


def git(cwd, *a, timeout=60):
    return ui.sh("git", "-C", cwd, *a, timeout=timeout)


def commit(cwd, message):
    """Закоммитить все правки; вернуть короткий sha или None, если правок нет."""
    git(cwd, "add", "-A")
    code, _ = git(cwd, "-c", "user.name=Главный", "-c", "user.email=hub@localhost", "commit", "-q", "-m", message)
    if code != 0:
        return None
    _, sha = git(cwd, "rev-parse", "--short=10", "HEAD")
    return sha.strip()


# ---------- 🛠 сделать бота ----------

MAKE_ASK = ("🛠 <b>Новый бот</b>\n<blockquote>Опиши, что он должен делать — как ТЗ, своими словами: для чего, какие "
            "кнопки и команды, что присылает сам и когда. Чем подробнее, тем лучше с первого раза.</blockquote>\n"
            "<i>Дальше я сам: придумаю имя, напишу код, создам бота в @BotFather и запущу. Потом пишешь правки.</i>")

SPEC = """Глеб хочет Telegram-бота. ТЗ:
{tz}

Придумай боту короткое имя по-русски и username латиницей. Ответь только JSON:
{{"name": "имя в чате, до 30 символов", "username": "латиница_без_bot_на_конце", "slug": "латиница-через-дефис, 3-20 символов",
"summary": "одна строка: что делает", "commands": [{{"command": "start", "description": "..."}}]}}"""

CODE = """Ты пишешь Telegram-бота для Глеба с нуля в текущей папке ({dir}). ТЗ:
{tz}

Имя бота: {name}. Требования:
- Python 3, только стандартная библиотека (urllib, json, sqlite3, threading). Если без пакета никак — перечисли его в
  requirements.txt (Главный сам поставит в .venv), но лучше обойтись.
- Главный файл bot.py. Токен — из переменной окружения BOT_TOKEN. Long polling getUpdates, без вебхуков.
- Владелец — первый, кто нажал /start (храни в data/state.json); остальным «🔒 Это личный бот».
- Данные — в папке data/ рядом с bot.py. Время — Москва (Europe/Moscow).
- Красиво и просто: parse_mode HTML, эмодзи к месту, inline-кнопки, короткие понятные тексты по-русски.
- setMyCommands и setMyDescription при запуске. /help — что умеет бот.
- Ошибки сети не должны ронять бота: try/except вокруг обработки апдейта, повтор getUpdates через 5 с.
- Голосовые и фото: бот должен их понимать. Готовый помощник — {common}/media.py
  (sys.path.insert(0, "{common}"); import media): media.kind(msg) -> "voice"|"photo"|None,
  media.voice_text(BOT_TOKEN, msg) -> текст (голосовое → дальше как обычный текст, сначала ответь «🎙 «…»»),
  media.photo_path(BOT_TOKEN, msg, папка) -> путь к картинке. Ошибки — media.MediaError (покажи её текст).
  Распознавание занимает секунды — делай его в отдельном потоке.
- Если боту нужен ИИ — вызывай `claude -p "<промпт>" --output-format json` через subprocess (Claude Code на сервере
  уже вошёл в аккаунт), поле result. Без интернета и инструментов, если не нужно.
- Напиши test_bot.py (unittest, без сети: подмени функцию отправки в Telegram) и прогони: python3 test_bot.py.
  Проверь и python3 -m py_compile bot.py.
- README.md: 5–10 строк — что умеет, команды.
В конце ответь 2–4 строками по-русски: что умеет бот. Ничего не запускай надолго (сам бот не запускай)."""


def ask_make(chat, mid=None):
    ui.setting("wait", "make")
    ui.show(chat, mid, MAKE_ASK, [[ui.b("Отмена", "home")]])


def make(chat, tz):
    if not job.acquire(blocking=False):
        return ui.send(chat, "⏳ Claude ещё занят прошлым ботом — пришли ТЗ, когда закончит.")
    try:
        _make(chat, tz)
    finally:
        job.release()


def _make(chat, tz):
    st = Status(chat, "Делаю бота")
    st.step("придумываю имя", force=True)
    ok, out = claude(SPEC.format(tz=tz), BOTS if os.path.isdir(BOTS) else "/tmp", tools=False, timeout=300)
    spec = json_from(out) if ok else None
    if not spec:
        return st.done(ui.card("😕 <b>Не получилось начать</b>",
                               [ui.esc(out if not ok else "Claude ответил не по формату")]), [[ui.b("🔁 Ещё раз", "mk")]])
    slug = re.sub(r"[^a-z0-9-]", "", str(spec.get("slug") or "bot").lower())[:20].strip("-") or "bot"
    while slug in made() or os.path.exists(os.path.join(BOTS, slug)):
        slug = f"{slug}-{secrets.randbelow(90) + 10}"
    d = os.path.join(BOTS, slug)
    os.makedirs(os.path.join(d, "data"), exist_ok=True)
    with open(os.path.join(d, ".gitignore"), "w") as f:
        f.write("data/\n.env\n.venv/\n__pycache__/\n")
    git(d, "init", "-q")
    info = {"name": str(spec.get("name") or slug)[:40], "username": None, "pm2": f"bot-{slug}", "dir": d,
            "want": re.sub(r"[^A-Za-z0-9_]", "", str(spec.get("username") or slug)) or "my",
            "summary": str(spec.get("summary") or "")[:150], "tz": tz, "created": time.time()}
    set_made(slug, info)
    st.title = f"Делаю бота «{info['name']}»"
    st.step("пишу код", force=True)
    shots = shots_note(take_shots(os.path.join(d, "data", "shots")))
    ok, out = claude(CODE.format(dir=d, tz=tz, name=info["name"], common=os.path.join(ui.REPO, "common")) + shots,
                     d, on_step=st.step)
    if not ok or not os.path.exists(os.path.join(d, "bot.py")):
        return st.done(ui.card("😕 <b>Код не получился</b>", [ui.esc(out[-500:])]),
                       [[ui.b("🔁 Ещё раз", f"mkr|{slug}"), ui.b("🗑 Удалить", f"del?|{slug}")]])
    commit(d, "Первая версия: " + tz[:200])
    info["about"] = out[-600:]
    set_made(slug, info)
    st.step("создаю бота в @BotFather", force=True)
    token, why = botfather(info["name"], info["want"])
    if not token:
        ui.setting("wait", f"mtoken|{slug}")
        return st.done(
            ui.card(f"✅ <b>Код готов: {ui.esc(info['name'])}</b>", [ui.esc(out[-600:])],
                    f"Осталось создать бота в Telegram ({ui.esc(why)}):\n"
                    f"1. Открой @BotFather → /newbot\n2. Имя: <code>{ui.esc(info['name'])}</code>\n"
                    f"3. Username: <code>{ui.esc(info['want'])}_bot</code> (занят — допиши цифры)\n"
                    "4. Пришли сюда токен — сам запущу."),
            [[{"text": "Открыть @BotFather", "url": "https://t.me/BotFather"}], [ui.b("Отмена", "home")]])
    deploy(chat, slug, token, st)


def secretary_ready():
    """Секретарь подключён к аккаунту Глеба — может сам создать бота в @BotFather."""
    return os.path.exists(os.path.join(ui.REPO, "tgbot", "tmp", "agent.sock"))


def botfather(name, want):
    """(токен, None) или (None, почему не вышло). Через Секретаря — аккаунт Глеба, он подтверждает кнопкой."""
    tg_py = os.path.join(ui.REPO, "tgbot", "tg.py")
    if not secretary_ready():
        return None, "Секретарь не запущен — поставь его: «📂 Все боты» → «➕ Поставить Секретарь»"
    code, out = ui.sh("python3", tg_py, "newbot", name, want, "--auto", timeout=5 * 60)
    m = re.search(r"TOKEN (\S+) @(\w+)", out)
    if code == 0 and m:
        return m.group(1), None
    return None, "Секретарь не смог: " + out.replace("Ошибка: ", "").strip()[-200:]


def deploy(chat, slug, token, st=None):
    info = made().get(slug)
    if not info:
        return ui.send(chat, "Этого бота уже нет.")
    d = info["dir"]
    st = st or Status(chat, f"Запускаю «{info['name']}»")
    st.step("запускаю", force=True)
    env = os.path.join(d, ".env")
    fd = os.open(env, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(f"BOT_TOKEN={token}\nPYTHONUNBUFFERED=1\n")
    py = "python3"
    if os.path.exists(os.path.join(d, "requirements.txt")):
        st.step("ставлю пакеты", force=True)
        ui.sh("python3", "-m", "venv", os.path.join(d, ".venv"), timeout=300)
        ui.sh(os.path.join(d, ".venv", "bin", "pip"), "install", "-q", "-r", os.path.join(d, "requirements.txt"),
              timeout=900)
        py = os.path.join(d, ".venv", "bin", "python")
    with open(os.path.join(d, "run.sh"), "w") as f:
        f.write(f"#!/usr/bin/env bash\n# Запуск для pm2: секреты из .env\nset -a; . '{env}'; set +a\n"
                f"cd '{d}' && exec {py} bot.py\n")
    ui.sh("pm2", "delete", info["pm2"], timeout=30)
    code, out = ui.sh("pm2", "start", os.path.join(d, "run.sh"), "--name", info["pm2"], "--interpreter", "bash",
                      "--cwd", d, "--restart-delay", "5000", timeout=60)
    ui.sh("pm2", "save", timeout=30)
    info["username"] = username(token)
    set_made(slug, info)
    time.sleep(5)
    a = next((x for x in ui.apps() or [] if x["name"] == info["pm2"]), {})
    if code != 0 or a.get("status") != "online":
        return st.done(f"⚠️ <b>«{ui.esc(info['name'])}» не запустился</b>\n<pre>{ui.esc(out[-600:])}</pre>",
                       [[ui.b("📜 Логи", f"lg|{info['pm2']}"), ui.b("✏️ Исправить", f"fix|{info['pm2']}", "primary")]])
    who = f"@{info['username']}" if info["username"] else info["name"]
    st.done(ui.card(f"✅ <b>{ui.esc(info['name'])}</b> работает — {ui.esc(who)}", [ui.esc(info.get("about", ""))],
                    "<i>Открой, нажми «Начать» и проверь. Что не так — «✏️ Правки», опиши словами.</i>"),
            ([[{"text": "▶️ Открыть бота", "url": f"https://t.me/{info['username']}?start=hub", "style": "success"}]]
             if info["username"] else []) +
            [[ui.b("✏️ Правки", f"fix|{info['pm2']}"), ui.b("📜 Логи", f"lg|{info['pm2']}")],
             [ui.b("🗑 Удалить", f"del?|{slug}")]])


# ---------- ✏️ доработать ----------

FIX = """Ты дорабатываешь Telegram-бота «{label}» Глеба по его просьбе. Код бота: {where}.
Сначала прочитай README (и CLAUDE.md в корне, если есть), чтобы понять устройство и правила.

Что поменять (слова Глеба):
{tz}

Сделай это аккуратно, в стиле существующего кода. Не трогай секреты (/etc/*.env, .env), tgbot/tmp/ и данные в data/.
Если есть тесты для этого бота — запусти их и почини, если упали из-за правки; добавь тест на новое поведение.
Обнови README, если поменялись команды. Не запускай сам бот и не перезапускай pm2 — это сделает Главный.
Если просьба непонятна или опасна — ничего не меняй и объясни, что уточнить.
В конце ответь 2–5 строками по-русски для Глеба: что сделано и как проверить (без кода)."""


def targets():
    """Кого можно дорабатывать: запущенные боты из репозитория и все сделанные здесь."""
    running = {a["name"] for a in ui.apps() or []}
    res = [(pm2, v[0]) for pm2, v in CATALOG.items() if pm2 in running]
    res += [(info["pm2"], "🧩 " + info["name"]) for info in made().values()]
    return res


def ask_fix(chat, mid=None, pm2=None):
    if pm2:
        ui.setting("wait", f"fix|{pm2}")
        return ui.show(chat, mid, ui.card(f"✏️ <b>{ui.esc(ui.short(label(pm2) or pm2))}</b>",
                                          ["Что поменять? Пиши как ТЗ: что не так и как должно быть. "
                                           "Можно несколько пунктов, голосом или со скриншотами."]),
                       [[ui.b("Отмена", "home")]])
    rows = ui.pairs([ui.b(ui.clip(lbl), f"fix|{pm2}") for pm2, lbl in targets()])
    ui.show(chat, mid, "✏️ <b>Какого бота доработать?</b>", rows + [[ui.b("‹ Назад", "home")]])


def fix(chat, pm2, tz):
    if not job.acquire(blocking=False):
        return ui.send(chat, "⏳ Claude ещё занят прошлой задачей — пришли правку, когда закончит.")
    try:
        _fix(chat, pm2, tz)
    finally:
        job.release()


def _fix(chat, pm2, tz):
    lbl = label(pm2) or pm2
    slug = next((s for s, i in made().items() if i["pm2"] == pm2), None)
    if slug:
        cwd = made()[slug]["dir"]
        where = f"папка {cwd} (bot.py)"
    elif pm2 in CATALOG:
        cwd = ui.REPO
        where = f"репозиторий {cwd}, папки " + ", ".join(CATALOG[pm2][1])
    else:
        return ui.send(chat, f"Не знаю, где код {ui.esc(pm2)}.")
    st = Status(chat, f"Дорабатываю {lbl}")
    st.step("читаю код", force=True)
    shots = shots_note(take_shots(os.path.join(ui.DATA, "shots", "task")))
    ok, out = claude(FIX.format(label=lbl, where=where, tz=tz) + shots, cwd, on_step=st.step)
    if not ok:
        return st.done(ui.card("😕 <b>Не получилось</b>", [ui.esc(out)]), [[ui.b("🔁 Ещё раз", f"fix|{pm2}")]])
    sha = commit(cwd, f"Главный ({lbl}): {tz[:200]}")
    if not sha:
        return st.done(ui.card("ℹ️ <b>Код не менялся</b>", [ui.esc(out[-1500:])]),
                       [[ui.b("✏️ Ещё правка", f"fix|{pm2}")]])
    btns = [[ui.b("✏️ Ещё правка", f"fix|{pm2}"), ui.b("📜 Логи", f"lg|{pm2}")],
            [ui.b("↩️ Откатить", f"rb|{pm2}|{sha}", "danger")]]
    st.done(ui.card(f"✅ <b>{ui.esc(ui.short(lbl))} — готово</b>", [ui.esc(out[-1500:])], "<i>Перезапускаю…</i>"), btns)
    restart_after(chat, pm2)


def restart_after(chat, pm2):
    if pm2 == ui.SELF:
        ui.setting("updated_chat", chat)
        ui.tg("getUpdates", offset=ui.offset, timeout=0)
        os._exit(0)  # pm2 поднимет Главного с новым кодом
    code, out = ui.sh("pm2", "restart", pm2, timeout=60)
    time.sleep(5)
    a = next((x for x in ui.apps() or [] if x["name"] == pm2), {})
    if code != 0 or a.get("status") != "online":
        ui.send(chat, ui.card(f"⚠️ <b>{ui.esc(ui.short(label(pm2) or pm2))} не поднялся</b>",
                              ["После правки бот не запустился — смотри логи или откати."]),
                [[ui.b("📜 Логи", f"lg|{pm2}", "primary")]])


def rollback(chat, pm2, sha):
    slug = next((s for s, i in made().items() if i["pm2"] == pm2), None)
    cwd = made()[slug]["dir"] if slug else ui.REPO
    code, out = git(cwd, "-c", "user.name=Главный", "-c", "user.email=hub@localhost", "revert", "--no-edit", sha)
    if code != 0:
        git(cwd, "revert", "--abort")
        return ui.send(chat, f"⚠️ <b>Не откатилось</b>\n<pre>{ui.esc(out[-500:])}</pre>")
    ui.send(chat, ui.card("↩️ <b>Откатил правку</b>", [f"{ui.esc(ui.short(label(pm2) or pm2))} — перезапускаю."]))
    restart_after(chat, pm2)


def delete(chat, slug):
    info = made().get(slug)
    if not info:
        return ui.send(chat, "Уже удалён.")
    ui.sh("pm2", "delete", info["pm2"], timeout=30)
    ui.sh("pm2", "save", timeout=30)
    shutil.rmtree(info["dir"], ignore_errors=True)
    set_made(slug, None)
    ui.send(chat, ui.card(f"🗑 <b>«{ui.esc(info['name'])}» удалён</b>",
                          ["Остановлен, код и данные стёрты. В @BotFather его можно удалить командой /deletebot."]),
            [[ui.b("‹ К ботам", "home")]])


# ---------- скриншоты к ТЗ и «что сделать?» ----------

def take_shots(dst):
    """Фото, присланные к ТЗ: копируем в dst (Claude откроет их через Read) и забываем."""
    with ui.lock:
        shots = ui.setting("shots") or []
        ui.setting("shots", [])
    res = []
    for p in shots:
        if os.path.exists(p):
            os.makedirs(dst, exist_ok=True)
            res.append(shutil.copy(p, dst))
    return res


def shots_note(paths):
    return ("\n\nК задаче Глеб приложил скриншоты/фото — открой каждый инструментом Read:\n" + "\n".join(paths)
            if paths else "")


def ask_what(chat, text):
    """Свободный текст или голосовое без команды: новый бот или правка какого-то из ботов?"""
    ui.setting("heard", text)
    rows = [[ui.b("🛠 Сделать нового бота", "mkh", "primary")]]
    rows += ui.pairs([ui.b(ui.clip(f"✏️ {ui.short(lbl)}"), f"fixh|{pm2}") for pm2, lbl in targets()])
    ui.send(chat, ui.card("📝 <b>Что с этим сделать?</b>", [f"<i>{ui.esc(text[:600])}</i>"],
                          "Сделать нового бота или доработать одного из этих:"),
            rows + [[ui.b("Ничего", "home")]])


# ---------- входы из hub.py ----------

def on_text(chat, wait, text, msg):
    """Сообщение, когда Главный ждёт ТЗ или токен. True — обработано."""
    if wait == "make":
        ui.setting("wait", None)
        threading.Thread(target=make, args=(chat, text), daemon=True).start()
        return True
    if wait.startswith("fix|"):
        ui.setting("wait", None)
        threading.Thread(target=fix, args=(chat, wait[4:], text), daemon=True).start()
        return True
    if wait.startswith("mtoken|"):
        m = ui.BOT_TOKEN.search(text)
        if not m:
            ui.send(chat, "Это не токен. Скопируй его из @BotFather целиком (вида <code>123456:ABC…</code>).")
            return True
        try:
            ui.tg("deleteMessage", chat_id=chat, message_id=msg["message_id"])
        except Exception:
            pass
        ui.setting("wait", None)
        threading.Thread(target=deploy, args=(chat, wait[7:], m.group(0)), daemon=True).start()
        return True
    return False


def on_callback(chat, mid, data):
    """Кнопки мастерской. Возвращает текст всплывашки или None, если кнопка не наша."""
    if data[0] == "all":
        show_all(chat, mid)
    elif data[0] == "mk":
        ask_make(chat, mid)
    elif data[0] == "mkr":
        info = made().get(data[1])
        if info:
            set_made(data[1], None)
            shutil.rmtree(info["dir"], ignore_errors=True)
            threading.Thread(target=make, args=(chat, info["tz"]), daemon=True).start()
    elif data[0] == "fix":
        ask_fix(chat, mid, data[1] if len(data) > 1 else None)
    elif data[0] in ("mkh", "fixh"):
        text = ui.setting("heard")
        if not text:
            return "Пришли задачу ещё раз"
        ui.setting("heard", None)
        ui.edit(chat, mid, f"👌 Взял в работу: <i>{ui.esc(text[:300])}</i>")
        if data[0] == "mkh":
            threading.Thread(target=make, args=(chat, text), daemon=True).start()
        else:
            threading.Thread(target=fix, args=(chat, data[1], text), daemon=True).start()
    elif data[0] == "rb":
        threading.Thread(target=rollback, args=(chat, data[1], data[2]), daemon=True).start()
        return "Откатываю…"
    elif data[0] == "del?":
        info = made().get(data[1]) or {}
        ui.show(chat, mid, ui.card(f"🗑 <b>Удалить «{ui.esc(info.get('name', data[1]))}»?</b>", ["Код и данные сотрутся."]),
                [[ui.b("Да, удалить", f"del|{data[1]}", "danger"), ui.b("Нет", "home")]])
    elif data[0] == "del":
        delete(chat, data[1])
    else:
        return None
    return ""
