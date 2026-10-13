#!/usr/bin/env python3
"""«Студия» — бот, который делает ботов. Глеб пишет идею → Студия уточняет (кнопки) → он даёт имя → план →
«✅ Создать» или правки словами → Claude Code на сервере пишет бота на botkit (чат + мини-приложение), Студия
рисует аватар, создаёт бота в @BotFather через Секретаря (или просит токен) и запускает его в pm2.

Боты живут в /root/bots/<slug>/ (свой git), список — /root/bots/registry.json (его видит и Главный).
Мини-приложения всех ботов — https://apps.<ip>.sslip.io/b/<slug>/ (Caddy планера → сюда, порт 8795 → порт бота);
приложение самой Студии — в корне этого адреса.

Переменные (/etc/studio-bot.env): STUDIO_TOKEN, STUDIO_ALLOWED (id владельца, необязательно), STUDIO_URL (свой
https-адрес, необязательно), STUDIO_PORT (8795), STUDIO_BOTS_DIR (/root/bots), STUDIO_TALK_MODEL (модель для
вопросов и плана, по умолчанию sonnet; пусто — модель по умолчанию).
"""
import http.client
import hmac
import json
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
KIT = os.path.join(HERE, "kit")
sys.path.insert(0, KIT)
import botkit as kit  # noqa: E402

BOTS = os.environ.get("STUDIO_BOTS_DIR") or "/root/bots"
REGISTRY = os.path.join(BOTS, "registry.json")
PORT = int(os.environ.get("STUDIO_PORT") or 8795)
FIRST_BOT_PORT = 8800
TALK_MODEL = os.environ.get("STUDIO_TALK_MODEL", "sonnet")
SITES = os.environ.get("STUDIO_SITES_DIR") or "/root/sites"
SITES_REG = os.path.join(BOTS, "sites.json")  # сайты — отдельный файл: Главный читает registry.json только про ботов
BUILD_TIMEOUT = 40 * 60
BOT_TOKEN = re.compile(r"\d{6,12}:[A-Za-z0-9_-]{30,}")

bot = kit.Bot("Студия", emoji="🏗", app_text="Студия",
              about="Делаю красивых и простых ботов по твоей идее: уточню, покажу план, напишу код, "
                    "сделаю мини-приложение и запущу.",
              short="Бот, который делает ботов",
              commands=[("start", "Новый бот"), ("bots", "Мои боты"), ("cancel", "Отменить черновик")],
              token=os.environ.get("STUDIO_TOKEN", ""), port=PORT, app_url="",
              data=os.path.join(HERE, "data"), webapp=os.path.join(HERE, "webapp"),
              support={"name": "Студия", "folders": ["studio"], "pm2": "studio-bot", "cwd": REPO,
                       "tests": ["python3 studio/test_studio.py"]})
if os.environ.get("STUDIO_ALLOWED") and not bot.owner:
    bot.db.set("_owner", int(re.findall(r"\d+", os.environ["STUDIO_ALLOWED"])[0]))

job = threading.Lock()  # Claude пишет код одного бота за раз
reg_lock = threading.RLock()
esc, card, b = kit.esc, kit.card, kit.b


# ---------- реестр ботов ----------

def registry():
    try:
        with open(REGISTRY) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def reg_set(slug, info=None):
    with reg_lock:
        r = registry()
        if info is None:
            r.pop(slug, None)
        else:
            r[slug] = info
        os.makedirs(BOTS, exist_ok=True)
        tmp = REGISTRY + ".tmp"
        with open(tmp, "w") as f:
            json.dump(r, f, ensure_ascii=False, indent=1)
        os.replace(tmp, REGISTRY)


def free_port():
    used = {i.get("port") for i in registry().values()}
    p = FIRST_BOT_PORT
    while p in used:
        p += 1
    return p


def new_slug(want):
    slug = re.sub(r"[^a-z0-9-]", "", str(want or "").lower().replace("_", "-"))[:20].strip("-") or "bot"
    while slug in registry() or os.path.exists(os.path.join(BOTS, slug)):
        slug = f"{slug[:16]}-{secrets.randbelow(90) + 10}"
    return slug


# ---------- сервер ----------

def sh(*cmd, timeout=60, cwd=None, env=None):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd, env=env)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return 127, f"нет программы {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "не уложилось по времени"


def pm2_status():
    """{pm2-имя: статус}"""
    code, out = sh("pm2", "jlist", timeout=30)
    try:
        return {r.get("name"): (r.get("pm2_env") or {}).get("status") for r in json.loads(out[out.find("["):])}
    except ValueError:
        return {}


def server_ip():
    for u in ("https://api.ipify.org", "https://ipv4.icanhazip.com"):
        try:
            with urllib.request.urlopen(u, timeout=15) as r:
                ip = r.read().decode().strip()
            if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", ip):
                return ip
        except Exception:
            pass
    return None


def base_url():
    """https://apps.<ip>.sslip.io/ (держит Caddy планера) или STUDIO_URL."""
    u = os.environ.get("STUDIO_URL", "").strip()
    if not u:
        ip = os.environ.get("STUDIO_IP") or server_ip()
        u = f"https://apps.{ip.replace('.', '-')}.sslip.io/" if ip else ""
    return u if u.endswith("/") or not u else u + "/"


def app_url_of(slug):
    return (bot.db.get("base_url") or "") + f"b/{slug}/" if bot.db.get("base_url") else ""


def proxy(h, method):
    """/b/<slug>/… → мини-приложение бота на его порту (127.0.0.1); /s/<slug>/… → сайт (с проверкой доступа)."""
    if h.path.startswith("/s/"):
        return serve_site(h, method)
    m = re.match(r"^/b/([a-z0-9-]+)(/.*)?$", h.path.split("?")[0])
    if not m:
        return False
    info = registry().get(m.group(1))
    if not info:
        h.reply(404, b"no such bot", "text/plain")
        return True
    if not m.group(2):
        h.send_response(301)
        h.send_header("Location", f"/b/{m.group(1)}/")
        h.end_headers()
        return True
    rest = h.path[len(f"/b/{m.group(1)}"):]
    body = h.rfile.read(int(h.headers.get("Content-Length") or 0)) if method == "POST" else None
    headers = {k: h.headers[k] for k in ("Content-Type", "X-Init-Data") if h.headers.get(k)}
    try:
        c = http.client.HTTPConnection("127.0.0.1", info["port"], timeout=120)
        c.request(method, rest, body=body, headers=headers)
        r = c.getresponse()
        data = r.read()
        h.send_response(r.status)
        for k in ("Content-Type", "Cache-Control", "Location"):
            if r.getheader(k):
                h.send_header(k, r.getheader(k))
        h.send_header("Content-Length", str(len(data)))
        h.end_headers()
        h.wfile.write(data)
    except OSError:
        h.reply(502, {"error": f"«{info.get('name')}» сейчас не отвечает — он перезапускается?"})
    return True


bot.http_hook = proxy


# ---------- Claude ----------

TOOLS = ["Read", "Write", "Edit", "Glob", "Grep", "Bash(python3:*)", "Bash(node:*)", "Bash(ls:*)", "Bash(cat:*)",
         "Bash(sed -n:*)", "Bash(grep:*)", "Bash(head:*)", "Bash(tail:*)", "Bash(wc:*)", "Bash(find:*)",
         "Bash(git diff:*)", "Bash(git status:*)", "Bash(git log:*)", "Bash(mkdir:*)", "Bash(cp:*)"]


def claude(prompt, cwd, on_step=None, timeout=BUILD_TIMEOUT, tools=True, model=None):
    """(ok, итоговый текст). tools=False — только читать (картинки к идее). on_step(строка) — живой статус."""
    cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose"]
    if model:
        cmd += ["--model", model]
    if tools:
        cmd += ["--permission-mode", "acceptEdits", "--allowedTools", *TOOLS]
    else:
        cmd += ["--allowedTools", "Read", "--disallowedTools", "Bash", "Edit", "Write", "WebFetch", "WebSearch", "Task"]
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
    nice = {"bot.py": "логику бота", "test_bot.py": "тесты", "index.html": "экраны приложения",
            "app.css": "оформление", "app.js": "приложение", "README.md": "описание", "botkit.py": "набор Студии"}
    what = nice.get(path, path)
    if name == "Bash":
        c = str(inp.get("command", ""))
        return "проверяю тесты" if "test" in c else "проверяю код" if "py_compile" in c else "запускаю: " + c[:50]
    return {"Read": f"читаю {what}", "Write": f"пишу {what}", "Edit": f"правлю {what}",
            "Glob": "смотрю файлы", "Grep": "ищу в коде"}.get(name) or str(name)


def talk(prompt):
    """Быстрый вопрос Claude без правки файлов → dict из JSON или None."""
    cwd = BOTS if os.path.isdir(BOTS) else "/tmp"
    ok, out = claude(prompt, cwd, tools=False, timeout=240, model=TALK_MODEL or None)
    if not ok and TALK_MODEL:
        ok, out = claude(prompt, cwd, tools=False, timeout=240)
    if not ok:
        return None, out
    m = re.search(r"\{.*\}", out, re.S)
    try:
        return json.loads(m.group(0)), None
    except (ValueError, AttributeError):
        return None, "Claude ответил не по формату"


# ---------- черновик ----------

def draft():
    return bot.db.get("draft") or {}


def put(d):
    bot.db.set("draft", d)
    return d


def owner_chat():
    return bot.owner


def shots_note(d):
    paths = [p for p in d.get("shots") or [] if os.path.exists(p)]
    return ("\n\nК идее приложены картинки (примеры, скриншоты) — открой каждую инструментом Read:\n" +
            "\n".join(paths)) if paths else ""


def qa_text(d):
    return "\n".join(f"— {q['q']} → {q.get('a') or 'на твоё усмотрение'}" for q in d.get("qs") or [])


INTERVIEW = """Ты — «Студия»: делаешь личных Telegram-ботов для Глеба. Он написал идею:
«{idea}»{shots}

Задай от 0 до 3 уточняющих вопросов — только самых важных, без которых бот выйдет не таким, как он хочет.
Не спрашивай про оформление, технику, хранение данных и мини-приложение (оно будет у каждого бота) — это решаем сами.
Если идея и так ясна — вопросов нет. У каждого вопроса 2–4 варианта ответа, каждый до 22 символов, первый — самый
вероятный. Предложи 3 коротких имени для бота (по-русски, 1–2 слова, без слова «бот») и один подходящий эмодзи.
Определи вид: "site" — если Глеб просит сайт, лендинг, страницу, визитку, портфолио, интернет-магазин; иначе "bot".
Для сайта вопросы — про содержание и стиль (для кого, какие разделы, настроение), имена — названия сайта.
Ответь только JSON: {{"kind": "bot", "questions": [{{"q": "вопрос", "options": ["…", "…"]}}], "names": ["…", "…", "…"], "emoji": "💧"}}"""

PLAN = """Ты — «Студия»: делаешь личных Telegram-ботов для Глеба: простых, красивых и понятных.
Идея: «{idea}»
Уточнения:
{qa}
Имя бота: «{name}» {emoji}{fixes}{shots}

Составь короткий план бота. У каждого бота есть чат с кнопками и мини-приложение (кнопка слева от поля ввода) —
то же самое, но нагляднее. Делай минимум, который закрывает идею целиком: лучше 4 понятные функции, чем 10.
Ответь только JSON:
{{"summary": "одна строка: что делает бот",
"chat": ["что в чате — 3–6 коротких пунктов"],
"app": ["экраны мини-приложения — 2–4 коротких пункта"],
"auto": ["что бот присылает сам и когда; пустой список, если ничего"],
"commands": [{{"command": "start", "description": "Главное"}}],
"username": "латиница для @username, без bot на конце, 5–24 символа",
"slug": "латиница-через-дефис, 3–20 символов",
"colors": ["#rrggbb", "#rrggbb"]}}
colors — два цвета для аватара (градиент), под настроение бота."""

BUILD = """Ты делаешь Telegram-бота «{name}» {emoji} для Глеба в текущей папке ({dir}).
Здесь уже лежит заготовка Студии (рабочий пример — заметки):
- botkit.py — библиотека: Telegram, стиль, мини-приложение, данные, расписания, тесты. Прочитай её шапку. НЕ меняй.
- bot.py, test_bot.py, README.md — пример: перепиши под этого бота.
- webapp/kit.css и webapp/kit.js — общий дизайн и функции мини-приложений (шапки файлов — список классов и функций).
  НЕ меняй. webapp/index.html, app.css, app.js — пример: перепиши под этого бота.

Идея Глеба: «{idea}»
Уточнения:
{qa}
План (Глеб его подтвердил):
{plan}{fixes}{shots}

Как делать:
- Просто, красиво и понятно — это главное. Тексты короткие, по-русски, на «ты», без канцелярита.
- Стиль чата: заголовок жирным с одним эмодзи, содержимое — цитатой (kit.card), второстепенное — <i>…</i>, ошибки —
  «⚠️ …» (raise kit.Fail). Кнопки ≤ 24 символов, ≤ 3 в ряд (kit.rows), главное действие — style="primary"/"success",
  опасное — "danger" и с подтверждением. Колонки пробелами не выравнивать. Экран меняется на месте (c.show),
  а не новым сообщением.
- Главный экран — только через bot.menu(c, заголовок, строки, buttons=[ряды кнопок], quick=[1–2 ряда самого нужного]):
  он сам добавляет кнопку приложения, «⚙️ Режим» (выбор «💬 чат / 📱 приложение» встроен в botkit: /mode, /app) и
  «🛠 Поддержка». bot.app_button() сам никуда не добавляй. В режиме «чат» всё должно быть доступно кнопками в чате.
- Мини-приложение обязательно: то же, что в чате, но нагляднее (цифры крупно, списки, прогресс, вкладки .tabs, если
  экранов несколько). Только классы kit.css и функции kit.js; своё — немного в app.css. Кнопки в ряд — .actions
  или .grid (не свои flex/grid: у .btn + .btn есть отступ сверху). Пустые состояния —
  .empty с эмодзи. Действия — через @bot.api, после действия — свежее состояние в ответе.
- Данные — bot.db. Время — kit.now() (Москва). Напоминания — bot.daily / bot.every.
- Голосовые уже работают: распознанный текст приходит в @bot.text / @bot.reply. Фото — @bot.photo.
- Поддержка уже встроена (/fix в меню, Claude чинит бота по просьбе Глеба). В /help последней строкой:
  «🛠 Что-то не так или хочется улучшить — /fix».
- Нужен ИИ — kit.claude(prompt) внутри bot.later(...) (это долго), пока думает — c.typing().
- Только стандартная библиотека Python. Если без пакета никак — requirements.txt (Студия поставит), но лучше обойтись.
- test_bot.py через bot.testing(): главный экран, каждая кнопка, каждый api, чужой пользователь. Запусти
  python3 test_bot.py — должно быть OK. Проверь python3 -m py_compile bot.py.
- Не запускай bot.run(), не трогай .env и data/.
В конце ответь 2–4 строками по-русски для Глеба: что умеет бот (без кода)."""

PLAN_SITE = """Ты — «Студия»: делаешь личные сайты для Глеба: красивые, быстрые, понятные с телефона.
Идея: «{idea}»
Уточнения:
{qa}
Название: «{name}» {emoji}{fixes}{shots}

Составь короткий план сайта. Только статика (HTML, CSS, JS без сборки и серверной части). Делай минимум,
который закрывает идею целиком.
Ответь только JSON:
{{"summary": "одна строка: что за сайт",
"pages": ["разделы/страницы — 3–6 коротких пунктов"],
"style": ["стиль: цвета, шрифты, настроение — 2–3 пункта"],
"features": ["что умеет: форма, галерея, анимации… ; пустой список, если ничего"],
"slug": "латиница-через-дефис, 3–20 символов"}}"""

BUILD_SITE = """Ты делаешь сайт «{name}» {emoji} для Глеба в текущей папке ({dir}).

Идея Глеба: «{idea}»
Уточнения:
{qa}
План (Глеб его подтвердил):
{plan}{fixes}{shots}

Как делать:
- Статический сайт: index.html (+ style.css, script.js и при необходимости другие страницы и картинки в этой папке).
  Никакой сборки, серверной части и внешних зависимостей, кроме шрифтов Google Fonts и (если нужно) cdnjs.cloudflare.com.
  Все пути относительные (сайт открывается не из корня, а из /s/<имя>/).
- Красиво и просто: сначала мобильная версия (viewport, адаптивная сетка), потом большой экран. Свой характер:
  выразительная типографика, продуманная палитра, отступы, аккуратные анимации без перегруза. Не шаблонный вид.
- Тексты — настоящие, по-русски, по теме идеи (не lorem ipsum). Картинки — свои SVG/градиенты, внешние фото не подставляй.
- Заголовок <title>, описание, favicon (SVG в виде data: или файла), lang="ru".
- Проверь, что HTML валиден и ссылки на файлы существуют. Не запускай серверы.
В конце ответь 2–4 строками по-русски для Глеба: что на сайте (без кода)."""

FIX_SITE = """Ты дорабатываешь сайт «{name}» Глеба в текущей папке ({dir}). Статика: index.html, css, js.
Сначала прочитай файлы.

Что поменять (слова Глеба):
{text}{shots}

Сохрани стиль и мобильную версию, пути относительные. Если просьба непонятна — ничего не меняй и объясни, что уточнить.
В конце ответь 2–4 строками по-русски: что сделано."""

FIX = """Ты дорабатываешь Telegram-бота «{name}» Глеба в текущей папке ({dir}). Он сделан Студией на botkit.py
(не меняй botkit.py и webapp/kit.css, webapp/kit.js). Сначала прочитай README.md и bot.py.

Что поменять (слова Глеба):
{text}{shots}

Сохрани стиль: просто, красиво, коротко; чат и мини-приложение должны остаться согласованными — если новое
появилось в чате, добавь и в приложение. Обнови test_bot.py и запусти python3 test_bot.py — должно быть OK.
Обнови README.md, если поменялись команды. Не запускай bot.run(), не трогай .env и data/.
Если просьба непонятна — ничего не меняй и объясни, что уточнить.
В конце ответь 2–4 строками по-русски: что сделано и как проверить."""


# ---------- экраны чата ----------

EXAMPLES = ["бот-напоминалка пить воду", "сайт-визитка репетитора", "трекер привычек с серией дней",
            "лендинг для кофейни с меню и картой", "список покупок на двоих", "портфолио фотографа"]


@bot.home
def home(c):
    d = draft()
    if d.get("stage") and d["stage"] not in ("done",):
        return resume(c)
    n = len(registry())
    foot = "<i>Например: «" + EXAMPLES[int(time.time()) % len(EXAMPLES)] + "»</i>"
    if not n and not one_tap() and not secretary_ready():  # до первого бота: как он появится в Telegram
        foot += ("\n<i>Бота в Telegram создаю через @BotFather: в конце попрошу токен. Чтобы в один тап — включи мне "
                 "Bot Management Mode (@BotFather → Open).</i>")
    if not bot.app_url and bot.db.get("app_wait"):
        foot += "\n<i>📱 Приложение появится, когда заработает https (нужен Планер).</i>"
    mine = [[b(f"🧩 Мои боты · {n}", "bots")]] if n else []
    ns = len(sites())
    if ns:
        mine += [[b(f"🌐 Мои сайты · {ns}", "sites")]]
    bot.menu(c, "🏗 <b>Студия</b>", ["Напиши, какой бот или сайт нужен — текстом или голосом.",
                                    "Уточню, покажу план, сделаю сам."], buttons=mine, quick=mine, foot=foot)


_one_tap = {"at": 0, "ok": False}


def one_tap():
    """Включён ли у Студии Bot Management Mode (getMe раз в 10 минут, не на каждый /start)."""
    if time.time() - _one_tap["at"] > 600:
        _one_tap.update(at=time.time(), ok=bot.can_manage())
    return _one_tap["ok"]


@bot.command("cancel")
def cancel_cmd(c):
    cancel(c)


@bot.button("cancel")
def cancel(c):
    d = draft()
    if d.get("stage") == "build":
        return c.show(card("⏳ <b>Уже собираю</b>", ["Сборку не остановить — дождись, потом можно удалить."]))
    drop_unfinished(d)
    put({})
    c.toast("Черновик удалён")
    c.show(card("🗑 <b>Черновик удалён</b>", ["Напиши новую идею, когда захочешь."]))


def drop_unfinished(d):
    """Бот из черновика, который так и не запустили (нет username), — убрать из «Моих ботов» и с диска."""
    info = registry().get(d.get("slug") or "")
    if info and not info.get("username"):
        remove(d["slug"])


def resume(c):
    """/start посреди черновика — показать, где остановились."""
    d = draft()
    st = d.get("stage")
    if st == "ask":
        return ask_next(c.chat)
    if st == "name":
        return ask_name(c.chat)
    if st == "plan":
        return c.say(plan_card(d), plan_buttons())
    if st == "token":
        return ask_token(c.chat, d)
    if st == "failed":
        return c.say(card("😕 <b>Прошлая сборка не удалась</b>", [esc(d.get("error", ""))]),
                     [[b("🔁 Собрать ещё раз", "go", "primary")], [b("🗑 Удалить черновик", "cancel")]])
    c.say(card("⏳ <b>Работаю над «" + esc(d.get("name") or "ботом") + "»</b>", [esc(stage_text(d))]),
          [[bot.app_button("📱 Смотреть в приложении")]])


def stage_text(d):
    return {"think": "думаю над вопросами", "plan_wait": "составляю план", "build": "пишу код и запускаю"}.get(
        d.get("stage"), "работаю")


@bot.text
def on_text(c):
    d = draft()
    st = d.get("stage")
    if st == "ask":
        return answer(c.chat, c.text)
    if st == "name":
        return set_name(c.chat, c.text)
    if st == "plan":
        return correct(c.chat, c.text)
    if st == "token":
        return got_token(c)
    if st in ("think", "plan_wait"):
        c.say("⏳ Секунду, ещё думаю — напиши это, когда покажу результат.")
        return
    if st == "build":
        m = BOT_TOKEN.search(c.text)
        if m:
            bot.delete(c.chat, c.msg.get("message_id"))  # токен не должен висеть в чате
            d["token"] = m.group(0)
            put(d)
            return c.say("🔑 Токен принял — запущу бота, как только допишу код.")
        d.setdefault("queued", []).append(c.text)
        put(d)
        return c.say(card("📌 <b>Запомнил</b>", [esc(c.text[:300])], "<i>Доделаю сборку и сразу внесу эту правку.</i>"))
    if registry():
        bot.db.set("heard", c.text)
        rows = [[b("🆕 Новый бот", "new", "primary")]]
        rows += kit.rows([b(f"✏️ {i['name']}", f"fixh|{s}") for s, i in list(registry().items())[-6:]])
        return c.say(card("🤔 <b>Что это?</b>", [f"<i>{esc(c.text[:400])}</i>"], "Новый бот или правка одного из этих?"),
                     rows)
    start_idea(c.chat, c.text)


@bot.photo
def on_photo(c):
    """Скриншоты и примеры: к правке (если ждём её) или к идее. Подпись — сам текст."""
    if (bot.waits.get(c.chat) or ("",))[0] == "fix":
        bot.db.update("fix_shots", lambda v: v[-5:] + [c.photo], [])
    else:
        d = draft()
        if d.get("stage") in (None, "done"):
            d = {"shots": (d.get("shots") or [])[-5:]}
        d["shots"] = (d.get("shots") or [])[-5:] + [c.photo]
        put(d)
    if c.text:
        return bot._route_text(kit.Ctx(bot, c.chat, c.user, text=c.text, msg=c.msg))
    c.say("📎 Картинку приложу к задаче — теперь напиши, что сделать.")


@bot.button("new")
def new_from_heard(c):
    text = bot.db.get("heard")
    if not text:
        return c.toast("Пришли идею ещё раз")
    bot.db.delete("heard")
    c.show(f"👌 Новый бот: <i>{esc(text[:300])}</i>")
    start_idea(c.chat, text)


@bot.button("fixh")
def fix_from_heard(c):
    text = bot.db.get("heard")
    info = registry().get(c.args[0] if c.args else "")
    if not text or not info:
        return c.toast("Пришли правку ещё раз")
    bot.db.delete("heard")
    c.show(f"👌 Правка для «{esc(info['name'])}»: <i>{esc(text[:300])}</i>")
    bot.later(fix, c.chat, c.args[0], text)


# ---------- 1. идея → вопросы ----------

def start_idea(chat, text):
    old = draft()
    if old.get("stage") == "build":
        return bot.send(chat, "⏳ Сейчас собираю другого бота — пришли идею, когда закончу.")
    d = put({"id": secrets.token_hex(3), "idea": text.strip()[:3000], "stage": "think", "qs": [], "qi": 0,
             "fixes": [], "shots": old.get("shots") if not old.get("stage") else [], "started": time.time()})
    d["mid"] = bot.send(chat, "🧠 <b>Думаю, что уточнить…</b>")
    put(d)
    bot.later(interview, chat)


def interview(chat):
    d = draft()
    res, err = talk(INTERVIEW.format(idea=d["idea"], shots=shots_note(d)))
    d = draft()
    if d.get("stage") != "think":
        return
    res = res or {}
    qs = [{"q": str(q.get("q"))[:300], "options": [kit.clip(o, 30) for o in (q.get("options") or [])][:4]}
          for q in (res.get("questions") or [])[:3] if isinstance(q, dict) and q.get("q")]
    d.update(qs=qs, qi=0, names=[str(n)[:40] for n in (res.get("names") or [])][:3],
             emoji=str(res.get("emoji") or "✨")[:4], stage="ask" if qs else "name",
             kind="site" if res.get("kind") == "site" else "bot")
    put(d)
    lead = "👌 <b>Понял идею.</b> " + (f"{kit.plural(len(qs), 'вопрос', 'вопроса', 'вопросов')} — и начну."
                                       if qs else "Всё ясно — осталось имя.")
    if err and not res:
        lead = "👌 <b>Понял идею.</b> Сразу к имени."
    bot.edit(chat, d.get("mid"), lead)
    ask_next(chat)


def ask_next(chat):
    d = draft()
    qs, i = d.get("qs") or [], d.get("qi", 0)
    if i >= len(qs):
        d["stage"] = "name"
        put(d)
        return ask_name(chat)
    q = qs[i]
    opts = [b(o, f"ans|{d['id']}|{i}|{k}") for k, o in enumerate(q["options"])]
    rows = kit.rows(opts, 1 if any(len(o) > 14 for o in q["options"]) else 2)
    rows.append([b("Пропустить", f"skip|{d['id']}|{i}"), b("Хватит, дальше", f"enough|{d['id']}")])
    q["mid"] = bot.send(chat, card(f"❓ <b>Вопрос {i + 1} из {len(qs)}</b>", [esc(q["q"])],
                                   "<i>Нажми вариант или напиши свой.</i>"), rows)
    put(d)


def answer(chat, text, i=None):
    d = draft()
    qs = d.get("qs") or []
    i = d.get("qi", 0) if i is None else i
    if d.get("stage") != "ask" or i != d.get("qi") or i >= len(qs):
        return False
    qs[i]["a"] = text.strip()[:500]
    d["qi"] = i + 1
    put(d)
    if qs[i].get("mid"):
        bot.edit(chat, qs[i]["mid"], f"❓ {esc(qs[i]['q'])}\n<b>→ {esc(qs[i]['a'] or 'на твоё усмотрение')}</b>")
    ask_next(chat)
    return True


@bot.button("ans")
def on_answer(c):
    d = draft()
    if not c.args or c.args[0] != d.get("id"):
        return c.toast("Это старый вопрос")
    i, k = int(c.args[1]), int(c.args[2])
    opts = ((d.get("qs") or [{}] * (i + 1))[i] or {}).get("options") or []
    if k >= len(opts) or not answer(c.chat, opts[k], i):
        c.toast("Уже ответил")


@bot.button("skip")
def on_skip(c):
    d = draft()
    if c.args and c.args[0] == d.get("id") and answer(c.chat, "", int(c.args[1])):
        return
    c.toast("Уже ответил")


@bot.button("enough")
def on_enough(c):
    d = draft()
    if not c.args or c.args[0] != d.get("id") or d.get("stage") != "ask":
        return c.toast("Уже дальше")
    for q in d["qs"][d["qi"]:]:
        if q.get("mid"):
            bot.edit(c.chat, q["mid"], f"❓ {esc(q['q'])}\n<i>→ на твоё усмотрение</i>")
    d["qi"] = len(d["qs"])
    put(d)
    ask_next(c.chat)


# ---------- 2. имя ----------

def ask_name(chat):
    d = draft()
    names = d.get("names") or []
    rows = kit.rows([b(f"{d.get('emoji', '')} {n}".strip(), f"name|{d['id']}|{k}") for k, n in enumerate(names)], 3)
    d["name_mid"] = bot.send(chat, card("✏️ <b>Как назовём бота?</b>",
                                        ["Так он будет называться в Telegram."],
                                        "<i>Нажми вариант или напиши своё.</i>"), rows or None)
    put(d)


@bot.button("name")
def on_name(c):
    d = draft()
    if not c.args or c.args[0] != d.get("id") or d.get("stage") != "name":
        return c.toast("Имя уже выбрано")
    set_name(c.chat, (d.get("names") or [""])[int(c.args[1])])


def set_name(chat, name):
    d = draft()
    name = re.sub(r"\s+", " ", str(name)).strip().strip("«»\"'")[:40]
    if d.get("stage") != "name" or not name:
        return False
    d.update(name=name, stage="plan_wait")
    put(d)
    if d.get("name_mid"):
        bot.edit(chat, d["name_mid"], f"✏️ Имя: <b>{esc(name)}</b>")
    d["mid"] = bot.send(chat, f"📋 <b>Составляю план «{esc(name)}»…</b>")
    put(d)
    bot.later(make_plan, chat)
    return True


# ---------- 3. план ----------

def make_plan(chat):
    d = draft()
    fixes = ("\nПравки Глеба к прошлому плану (учти все):\n" + "\n".join(f"— {x}" for x in d["fixes"])
             if d.get("fixes") else "")
    if d.get("plan") and fixes:
        fixes += "\nПрошлый план: " + json.dumps(d["plan"], ensure_ascii=False)
    res, err = talk((PLAN_SITE if d.get("kind") == "site" else PLAN).format(
        idea=d["idea"], qa=qa_text(d) or "— нет", name=d["name"], emoji=d.get("emoji", ""),
        fixes=fixes, shots=shots_note(d)))
    d = draft()
    if d.get("stage") != "plan_wait":
        return
    if not res or not res.get("summary"):
        d["stage"] = "plan"
        put(d)
        if d.get("plan"):
            return bot.edit(chat, d.get("mid"), card("😕 <b>Не смог переделать план</b>", [esc(err or "")],
                                                    "Нажми «Создать» как есть или напиши правку ещё раз."),
                            plan_buttons())
        d["stage"] = "plan_wait"
        put(d)
        return bot.edit(chat, d.get("mid"), card("😕 <b>План не получился</b>", [esc(err or "")]),
                        [[b("🔁 Ещё раз", "replan", "primary"), b("Отмена", "cancel")]])
    res["username"] = re.sub(r"[^A-Za-z0-9_]", "", str(res.get("username") or ""))[:24] or "my_helper"
    if d.get("kind") == "site":
        res["slug"] = res.get("slug") or d["name"]
    res["colors"] = [x for x in res.get("colors") or [] if re.fullmatch(r"#[0-9a-fA-F]{6}", str(x))][:2] or \
        ["#7c5cff", "#22d3ee"]
    d.update(plan=res, stage="plan")
    put(d)
    bot.edit(chat, d.get("mid"), plan_card(d), plan_buttons())


def plan_card(d):
    p = d.get("plan") or {}
    if d.get("kind") == "site":
        lines = [f"<i>{esc(p.get('summary', ''))}</i>", "", "📄 <b>Страницы</b>"] + [f"• {esc(x)}" for x in p.get("pages") or []]
        lines += ["", "🎨 <b>Стиль</b>"] + [f"• {esc(x)}" for x in p.get("style") or []]
        if p.get("features"):
            lines += ["", "⚙️ <b>Умеет</b>"] + [f"• {esc(x)}" for x in p["features"]]
        return card(f"{d.get('emoji', '🌐')} <b>{esc(d.get('name', ''))}</b> — сайт", lines,
                    "<i>Всё так? «Создать» — и через несколько минут сайт готов. Потом предложу защитить его или выпустить для всех.</i>")
    lines = [f"<i>{esc(p.get('summary', ''))}</i>", "", "💬 <b>В чате</b>"]
    lines += [f"• {esc(x)}" for x in p.get("chat") or []]
    lines += ["", "📱 <b>Мини-приложение</b>"] + [f"• {esc(x)}" for x in p.get("app") or []]
    if p.get("auto"):
        lines += ["", "⏰ <b>Пишет сам</b>"] + [f"• {esc(x)}" for x in p["auto"]]
    lines += ["", f"🔗 @{esc(p.get('username', ''))}_bot <i>(если свободно)</i>"]
    return card(f"{d.get('emoji', '✨')} <b>{esc(d.get('name', ''))}</b> — план", lines,
                "<i>Всё так? «Создать» — и через несколько минут бот готов. Или напиши, что поменять.</i>")


def plan_buttons():
    return [[b("✅ Создать", "go", "success")], [b("✏️ Поправить", "edit"), b("✖️ Отмена", "cancel")]]


@bot.button("replan")
def on_replan(c):
    d = draft()
    if d.get("stage") != "plan_wait":
        return c.toast("Уже готово")
    c.show(f"📋 <b>Составляю план «{esc(d.get('name', ''))}»…</b>")
    bot.later(make_plan, c.chat)


@bot.button("edit")
def on_edit(c):
    if draft().get("stage") != "plan":
        return c.toast("План уже в работе")
    c.say("✏️ Что поменять? Пиши словами или голосом — переделаю план.")


def correct(chat, text):
    d = draft()
    if d.get("stage") != "plan":
        return False
    d["fixes"].append(text.strip()[:1000])
    d["stage"] = "plan_wait"
    if d.get("mid"):  # у старого плана убираем кнопки — новый придёт ниже
        bot.edit(chat, d["mid"], plan_card(d).rsplit("\n", 1)[0] + f"\n✏️ <i>{esc(text[:200])}</i>")
    d["mid"] = bot.send(chat, "✏️ <b>Переделываю план…</b>")
    put(d)
    bot.later(make_plan, chat)
    return True


@bot.button("go")
def on_go(c):
    d = draft()
    if d.get("stage") not in ("plan", "failed"):
        return c.toast("Уже собираю" if d.get("stage") == "build" else "Черновика нет")
    if c.mid and d.get("stage") == "plan":
        bot.edit(c.chat, c.mid, plan_card(d).rsplit("\n", 1)[0] + "\n✅ <b>Создаю</b>")
    confirm(c.chat)


def confirm(chat):
    d = draft()
    if d.get("stage") not in ("plan", "failed") or job.locked():
        return False
    d.update(stage="build", steps=[], started=time.time(), error=None)
    put(d)
    bot.later(build, chat)
    return True


# ---------- 4. сборка ----------

class Status:
    """Живой статус: в чате одно сообщение, в приложении — draft.steps."""

    def __init__(self, chat, title, track=True):
        self.chat, self.title, self.start, self.last, self.track = chat, title, time.time(), 0, track
        self.steps = []
        self.mid = bot.send(chat, f"⏳ <b>{esc(title)}</b>")

    def step(self, text, force=False):
        self.steps = steps = self.steps + [text]
        if self.track:  # сборка: шаги видны и в приложении
            d = draft()
            d["steps"] = steps[-30:]
            put(d)
        if force or time.time() - self.last > 8:
            self.last = time.time()
            mins = int(time.time() - self.start) // 60
            body = "\n".join(f"{'▸' if i == len(steps[-4:]) - 1 else '•'} {esc(s)}" for i, s in enumerate(steps[-4:]))
            try:
                bot.edit(self.chat, self.mid, f"⏳ <b>{esc(self.title)}</b> · {mins} мин\n<blockquote>{body}</blockquote>")
            except Exception:
                pass

    def done(self, text, buttons=None):
        bot.edit(self.chat, self.mid, text, buttons)


def build(chat):
    if not job.acquire(blocking=False):
        return bot.send(chat, "⏳ Уже собираю другого бота — подожди немного.")
    try:
        _build(chat)
    except Exception as e:
        fail(chat, f"Сломалось: {e}")
        raise
    finally:
        job.release()


def fail(chat, why, st=None):
    d = draft()
    d.update(stage="failed", error=str(why)[-600:])
    put(d)
    text = card("😕 <b>Не получилось</b>", [esc(d["error"])], "<i>Можно собрать ещё раз — черновик сохранён.</i>")
    btns = [[b("🔁 Собрать ещё раз", "go", "primary")], [b("🗑 Удалить черновик", "cancel")]]
    (st.done(text, btns) if st else bot.send(chat, text, btns))


def scaffold(slug, d):
    """Папка бота: заготовка + botkit.py, свой git."""
    path = os.path.join(BOTS, slug)
    shutil.copytree(os.path.join(KIT, "template"), path)
    shutil.copy(os.path.join(KIT, "botkit.py"), path)
    for root, _, files in os.walk(path):
        for f in files:
            if f.endswith((".py", ".md", ".html")):
                full = os.path.join(root, f)
                with open(full) as fh:
                    s = fh.read()
                with open(full, "w") as fh:
                    fh.write(s.replace("{{NAME}}", d["name"]).replace("{{SLUG}}", slug))
    os.makedirs(os.path.join(path, "data"), exist_ok=True)
    with open(os.path.join(path, ".gitignore"), "w") as f:
        f.write("data/\n.env\n.venv/\n__pycache__/\nrun.sh\n")
    git(path, "init", "-q")
    commit(path, "Заготовка Студии")
    return path


def git(cwd, *a, timeout=60):
    return sh("git", "-C", cwd, *a, timeout=timeout)


def commit(cwd, message):
    git(cwd, "add", "-A")
    code, _ = git(cwd, "-c", "user.name=Студия", "-c", "user.email=studio@localhost", "commit", "-q", "-m", message[:300])
    if code != 0:
        return None
    return git(cwd, "rev-parse", "--short=10", "HEAD")[1].strip()


def run_tests(path):
    py = venv_python(path)
    code, out = sh(py, "test_bot.py", cwd=path, timeout=300, env={**os.environ, "BOT_TOKEN": ""})
    code2, out2 = sh(py, "-m", "py_compile", "bot.py", cwd=path, timeout=60)
    return code == 0 and code2 == 0, (out + out2)[-1500:]


def venv_python(path):
    v = os.path.join(path, ".venv", "bin", "python")
    return v if os.path.exists(v) else "python3"


def plan_text(d):
    return json.dumps(d.get("plan") or {}, ensure_ascii=False, indent=1)


def _build(chat):
    d = draft()
    if d.get("kind") == "site":
        return build_site(chat, d)
    st = Status(chat, f"Создаю «{d['name']}»")
    st.step("готовлю папку", force=True)
    old = registry().get(d.get("slug") or "")
    if old and not old.get("username"):  # прошлая неудачная сборка этого черновика
        remove(d["slug"])
    slug = new_slug((d.get("plan") or {}).get("slug") or d["name"])
    path = scaffold(slug, d)
    info = {"name": d["name"], "emoji": d.get("emoji", "✨"), "slug": slug, "pm2": f"bot-{slug}", "dir": path,
            "port": free_port(), "summary": (d.get("plan") or {}).get("summary", "")[:200], "idea": d["idea"],
            "plan": d.get("plan"), "username": None, "created": time.time(), "by": "studio"}
    reg_set(slug, info)
    d.update(slug=slug)
    put(d)

    # параллельно, пока Claude пишет код: бот в Telegram и аватар.
    # Лучший путь — Managed Bots: Глеб жмёт кнопку, подтверждает окно Telegram, токен приходит сам (@bot.managed).
    got = {}
    t = None
    if bot.can_manage():
        d["newbot"] = bot.newbot_link(want_username(d), d["name"])
        put(d)
        bot.send(chat, card("⚡ <b>Нажми — и бот появится в Telegram</b>",
                            [f"Откроется окно с именем «{esc(d['name'])}» и адресом @{esc(want_username(d))} — "
                             "просто подтверди. Можно поменять адрес, если занят."],
                            "<i>Пока ты жмёшь, я пишу код.</i>"),
                 [[kit.url(f"⚡ Создать @{want_username(d)}", d["newbot"], "success")]])
    elif secretary_ready():
        t = threading.Thread(target=lambda: got.update(botfather=botfather(d["name"], d["plan"]["username"])),
                             daemon=True)
        t.start()
    else:  # ни одного тапа, ни Секретаря — пусть Глеб создаст бота в @BotFather, пока пишется код
        bot.send(chat, *token_card(d, early=True))
    try:
        avatar(path, d)
    except Exception as e:
        print("avatar:", e, flush=True)

    st.step("пишу код", force=True)
    ok, out = claude(BUILD.format(name=d["name"], emoji=d.get("emoji", ""), dir=path, idea=d["idea"],
                                  qa=qa_text(d) or "— нет", plan=plan_text(d), shots=shots_note(d),
                                  fixes="\nПравки к плану: " + "; ".join(d["fixes"]) if d.get("fixes") else ""),
                     path, on_step=st.step)
    if not ok:
        return fail(chat, out, st)
    st.step("проверяю тесты", force=True)
    if os.path.exists(os.path.join(path, "requirements.txt")):
        st.step("ставлю пакеты", force=True)
        sh("python3", "-m", "venv", os.path.join(path, ".venv"), timeout=300)
        sh(os.path.join(path, ".venv", "bin", "pip"), "install", "-q", "-r", os.path.join(path, "requirements.txt"),
           timeout=900)
    good, log = run_tests(path)
    if not good:
        st.step("чиню тесты", force=True)
        ok, out2 = claude(FIX.format(name=d["name"], dir=path, shots="",
                                     text="Тесты или проверка не проходят — почини код (не тесты, если они верны):\n"
                                          + log), path, on_step=st.step)
        good, log = run_tests(path)
        if not good:
            commit(path, "Первая версия (тесты не прошли)")
            return fail(chat, "Тесты не проходят:\n" + log, st)
    commit(path, "Первая версия: " + d["idea"][:200])
    info["about"] = brief(out)
    reg_set(slug, info)

    st.step("создаю бота в Telegram", force=True)
    if t:
        t.join(timeout=6 * 60)
    token, why = got.get("botfather") or (None, "Секретарь не ответил" if t else "")
    if got.get("botfather") and not got["botfather"][0]:
        why = got["botfather"][1]
    token = draft().get("token") or token  # пришёл через Managed Bots, пока писался код
    if not token:
        d = draft()
        d.update(stage="token", why=why)
        put(d)
        st.done(card(f"✅ <b>Код «{esc(d['name'])}» готов</b>", [esc(info["about"])]))
        return ask_token(chat, d)
    deploy(chat, slug, token, st)


def brief(text, n=600):
    """Начало ответа Claude для карточки «готово»: не больше n символов, обрезать по концу абзаца или предложения."""
    text = re.sub(r"\n{3,}", "\n\n", str(text).strip())
    if len(text) <= n:
        return text
    cut = text[:n]
    pos = max(cut.rfind("\n"), cut.rfind(". "))
    return (cut[:pos + 1] if pos > n // 3 else cut).rstrip() + "…"


def want_username(d):
    """Желаемый @username нового бота (оканчивается на bot)."""
    u = re.sub(r"_?bot$", "", (d.get("plan") or {}).get("username") or "my_helper", flags=re.I)[:28]
    return f"{u}_bot"


def ask_token(chat, d):
    if d.get("newbot"):
        return bot.send(chat, card(f"✅ <b>Код «{esc(d['name'])}» готов</b>",
                                   ["Осталось создать бота в Telegram: нажми кнопку и подтверди окно — дальше я сам."],
                                   "<i>Или создай в @BotFather вручную и пришли сюда токен.</i>"),
                        [[kit.url(f"⚡ Создать @{want_username(d)}", d["newbot"], "success")], [b("✖️ Отмена", "cancel")]])
    bot.send(chat, *token_card(d))


def token_card(d, early=False):
    """(текст, кнопки): один шаг для Глеба — создать бота в @BotFather и прислать токен."""
    title = "🔑 <b>Пока пишу код — создай бота</b>" if early else "🔑 <b>Код готов — нужен токен</b>"
    lines = [f"@BotFather → /newbot → имя <code>{esc(d.get('name', ''))}</code> → адрес "
             f"<code>{esc(want_username(d))}</code> (занят — допиши цифры) → токен пришли сюда, дальше я сам."]
    if d.get("why") and not early:
        lines.insert(0, f"<i>Сам создать не смог: {esc(d['why'])}</i>")
    return card(title, lines), [[kit.url("Открыть @BotFather", "https://t.me/BotFather", "primary")],
                                [b("✖️ Отмена", "cancel")]]


def got_token(c):
    m = BOT_TOKEN.search(c.text)
    if not m:
        return c.say("Это не токен. Скопируй его из @BotFather целиком (вида <code>123456:ABC…</code>).")
    bot.delete(c.chat, c.msg.get("message_id"))  # токен не должен висеть в чате
    d = draft()
    d["stage"] = "build"
    put(d)
    bot.later(deploy, c.chat, d["slug"], m.group(0))


@bot.managed
def on_managed(upd):
    """Глеб подтвердил создание бота (Managed Bots) — забираем токен и запускаем."""
    if (upd.get("user") or {}).get("id") != bot.owner:
        return
    new = upd.get("bot") or {}
    try:
        token = bot.managed_token(new["id"])
    except Exception as e:
        return bot.notify(kit.error(f"Бот @{esc(new.get('username'))} создан, но токен не выдали: {esc(e)}"))
    try:  # личный бот: в Telegram его откроет только Глеб (бот и сам пускает только владельца)
        bot.call("setManagedBotAccessSettings", user_id=new["id"], is_access_restricted=True)
    except Exception as e:
        print("access:", e, flush=True)
    d = draft()
    if d.get("stage") == "build":
        d["token"] = token
        put(d)
        bot.notify(f"🤖 @{esc(new.get('username'))} создан — запущу, как только допишу код.")
    elif d.get("stage") == "token":
        d.update(stage="build", token=token)
        put(d)
        bot.later(deploy, bot.owner, d["slug"], token)
    else:
        bot.notify(f"🤖 @{esc(new.get('username'))} создан, но я его сейчас не ждал. Опиши, каким он должен быть, "
                   "— сделаю нового бота (этого можно удалить в @BotFather).")


def to_folder(username):
    """Новый бот — сразу в папку «Bot» Глеба (через Секретаря; нет входа — молча пропускаем)."""
    if username and secretary_ready():
        bot.later(lambda: sh("python3", os.path.join(REPO, "tgbot", "tg.py"), "botsfolder", "--bots", username, "--auto",
                             timeout=120))


def secretary_ready():
    return os.path.exists(os.path.join(REPO, "tgbot", "tmp", "agent.sock"))


def botfather(name, want):
    """(токен, None) или (None, почему). Через Секретаря — аккаунт Глеба (tgbot/tg.py newbot --auto)."""
    if not secretary_ready():
        return None, "Секретарь не вошёл в аккаунт"
    code, out = sh("python3", os.path.join(REPO, "tgbot", "tg.py"), "newbot", name, want, "--auto", timeout=5 * 60)
    m = re.search(r"TOKEN (\S+) @(\w+)", out)
    if code == 0 and m:
        return m.group(1), None
    return None, "Секретарь не смог: " + out.replace("Ошибка: ", "").strip()[-200:]


def avatar(path, d):
    """data/avatar.png: градиент цветов плана и первая буква имени. Ставит его сам бот при запуске (botkit)."""
    out = os.path.join(path, "data", "avatar.png")
    colors = (d.get("plan") or {}).get("colors") or ["#7c5cff", "#22d3ee"]
    for py in pythons_with_pil() + ["python3"]:
        code, _ = sh(py, os.path.join(HERE, "avatar.py"), out, d["name"][:1].upper(), *colors, timeout=120)
        if code == 0:
            return out


def pythons_with_pil():
    res = []
    for d in ("common", "avito", "tgbot", "coach", "eye"):
        py = os.path.join(REPO, d, ".venv", "bin", "python")
        if os.path.exists(py):
            res.append(py)
    try:
        import PIL  # noqa: F401
        res.insert(0, sys.executable)
    except ImportError:
        pass
    return res


def deploy(chat, slug, token, st=None):
    info = registry().get(slug)
    if not info:
        return bot.send(chat, "Этого бота уже нет.")
    st = st or Status(chat, f"Запускаю «{info['name']}»", track=False)
    st.step("запускаю", force=True)
    path = info["dir"]
    env = os.path.join(path, ".env")
    fd = os.open(env, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(f"BOT_TOKEN={token}\nAPP_PORT={info['port']}\nAPP_URL={app_url_of(slug)}\nBOT_OWNER={bot.owner}\n"
                f"BOTKIT_COMMON={os.path.join(REPO, 'common')}\nBOT_PM2={info['pm2']}\nPYTHONUNBUFFERED=1\n")
    with open(os.path.join(path, "run.sh"), "w") as f:
        f.write(f"#!/usr/bin/env bash\n# Запуск для pm2 (сделано Студией): секреты из .env\nset -a; . '{env}'; set +a\n"
                f"cd '{path}' && exec {venv_python(path)} bot.py\n")
    sh("pm2", "delete", info["pm2"], timeout=30)
    code, out = sh("pm2", "start", os.path.join(path, "run.sh"), "--name", info["pm2"], "--interpreter", "bash",
                   "--cwd", path, "--restart-delay", "5000", timeout=60)
    sh("pm2", "save", timeout=30)
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getMe", timeout=20) as r:
            info["username"] = json.load(r)["result"].get("username")
    except Exception:
        pass
    reg_set(slug, info)
    time.sleep(6)
    if code != 0 or pm2_status().get(info["pm2"]) != "online":
        d = draft()
        d.update(stage="failed", error="бот не запустился")
        put(d)
        return st.done(card(f"⚠️ <b>«{esc(info['name'])}» не запустился</b>", [esc(logs(info['pm2'], 15))]),
                       [[b("✏️ Починить", f"fix|{slug}", "primary"), b("📜 Логи", f"logs|{slug}")]])
    d = draft()
    queued = d.get("queued") or []
    put({"stage": "done", "result": {"slug": slug}})
    st.done(done_card(info), done_buttons(info))
    to_folder(info.get("username"))
    if queued:
        bot.later(fix, chat, slug, "\n".join(queued))


def done_card(info):
    who = f"@{info['username']}" if info.get("username") else info["name"]
    return card(f"🎉 <b>Бот «{esc(info['name'])}» готов</b> — {esc(who)}", [esc(info.get("about") or info.get("summary", ""))],
                "<i>Открой и нажми «Начать» — бот спросит, как удобнее: в чате или в приложении. "
                "Что не так — просто напиши сюда.</i>")


def done_buttons(info):
    rows = [[kit.url("▶️ Открыть бота", f"https://t.me/{info['username']}?start=studio", "success")]] \
        if info.get("username") else []
    return rows + [[b("✏️ Правки", f"fix|{info['slug']}"), b("🧩 Мои боты", "bots")]]


def logs(pm2, lines=30):
    code, out = sh("pm2", "logs", pm2, "--lines", str(lines), "--nostream", "--raw", timeout=30)
    return out.strip()[-1500:] or "логов нет"


# ---------- правки ----------

@bot.button("fix")
def on_fix(c):
    info = registry().get(c.args[0] if c.args else "") or sites().get(c.args[0] if c.args else "")
    if not info:
        return c.toast("Бота уже нет")
    c.ask("fix", card(f"✏️ <b>{esc(info['name'])}</b>", ["Что поменять? Пиши как есть: что не так и как надо. "
                                                         "Можно голосом и со скриншотами."]), data=info["slug"])


@bot.reply("fix")
def got_fix(c):
    bot.later(fix, c.chat, c.data, c.text)


def fix_site(chat, info, text):
    slug = info["slug"]
    if not job.acquire(blocking=False):
        return bot.send(chat, "⏳ Claude занят — пришли правку чуть позже.")
    try:
        bot.db.set("busy", {"slug": slug, "text": text[:300], "at": time.time()})
        st = Status(chat, f"Дорабатываю сайт «{info['name']}»", track=False)
        st.step("читаю файлы", force=True)
        d = {"shots": bot.db.get("fix_shots") or []}
        bot.db.delete("fix_shots")
        ok, out = claude(FIX_SITE.format(name=info["name"], dir=info["dir"], text=text, shots=shots_note(d)),
                         info["dir"], on_step=st.step)
        if not ok:
            return st.done(card("😕 <b>Не получилось</b>", [esc(out)]), [[b("✏️ Ещё раз", f"fix|{slug}")]])
        sha = commit(info["dir"], "Правка: " + text[:200])
        if not sha:
            return st.done(card("ℹ️ <b>Сайт не менялся</b>", [esc(out[-1200:])]), [[b("✏️ Ещё правка", f"fix|{slug}")]])
        st.done(card(f"✅ <b>{esc(info['name'])} — готово</b>", [esc(out[-1200:])]),
                ([[kit.url("🌐 Открыть сайт", site_url(info), "success")]] if site_url(info) else [])
                + [[b("✏️ Ещё правка", f"fix|{slug}"), b("↩️ Откатить", f"undo|{slug}|{sha}", "danger")]])
    finally:
        bot.db.delete("busy")
        job.release()


def fix(chat, slug, text):
    if slug in sites():
        return fix_site(chat, sites()[slug], text)
    info = registry().get(slug)
    if not info:
        return bot.send(chat, "Этого бота уже нет.")
    if not job.acquire(blocking=False):
        return bot.send(chat, "⏳ Claude занят другим ботом — пришли правку чуть позже.")
    try:
        bot.db.set("busy", {"slug": slug, "text": text[:300], "at": time.time()})
        st = Status(chat, f"Дорабатываю «{info['name']}»", track=False)
        st.step("читаю код", force=True)
        d = {"shots": bot.db.get("fix_shots") or []}
        bot.db.delete("fix_shots")
        ok, out = claude(FIX.format(name=info["name"], dir=info["dir"], text=text, shots=shots_note(d)),
                         info["dir"], on_step=st.step)
        if not ok:
            return st.done(card("😕 <b>Не получилось</b>", [esc(out)]), [[b("✏️ Ещё раз", f"fix|{slug}")]])
        good, log = run_tests(info["dir"])
        sha = commit(info["dir"], "Правка: " + text[:200])
        if not sha:
            return st.done(card("ℹ️ <b>Код не менялся</b>", [esc(out[-1200:])]), [[b("✏️ Ещё правка", f"fix|{slug}")]])
        sh("pm2", "restart", info["pm2"], timeout=60)
        time.sleep(5)
        up = pm2_status().get(info["pm2"]) == "online"
        lines = [esc(out[-1200:])] + ([] if good else ["⚠️ Тесты после правки не прошли."]) + \
            ([] if up else ["⚠️ После перезапуска бот не поднялся — открой логи или откати."])
        st.done(card(f"✅ <b>{esc(info['name'])} — готово</b>" if up else f"⚠️ <b>{esc(info['name'])}</b>", lines),
                ([[kit.url("▶️ Открыть", f"https://t.me/{info['username']}", "success")]] if info.get("username") else [])
                + [[b("✏️ Ещё правка", f"fix|{slug}"), b("↩️ Откатить", f"undo|{slug}|{sha}", "danger")]])
    finally:
        bot.db.delete("busy")
        job.release()


@bot.button("undo")
def on_undo(c):
    slug = c.args[0] if c.args else ""
    info = registry().get(slug) or sites().get(slug)
    if not info:
        return c.toast("Бота уже нет")
    code, out = git(info["dir"], "-c", "user.name=Студия", "-c", "user.email=studio@localhost", "revert", "--no-edit",
                    c.args[1])
    if code != 0:
        git(info["dir"], "revert", "--abort")
        return c.say(kit.error(f"Не откатилось: <code>{esc(out[-300:])}</code>"))
    if info.get("kind") == "site":
        c.toast("Откатил")
        return c.say(card("↩️ <b>Правка отменена</b>", [f"Сайт «{esc(info['name'])}» вернулся к прошлой версии."]))
    sh("pm2", "restart", info["pm2"], timeout=60)
    c.toast("Откатил")
    c.say(card("↩️ <b>Правка отменена</b>", [f"«{esc(info['name'])}» перезапущен."]))


# ---------- мои боты ----------

def bots_list():
    status = pm2_status()
    res = []
    for slug, info in registry().items():
        res.append({"slug": slug, "name": info.get("name"), "emoji": info.get("emoji", "🧩"),
                    "username": info.get("username"), "summary": info.get("summary", ""),
                    "status": status.get(info.get("pm2")), "created": info.get("created")})
    for slug, info in sites().items():
        res.append({"slug": slug, "name": info.get("name"), "emoji": info.get("emoji", "🌐"), "kind": "site",
                    "username": None, "summary": info.get("summary", ""), "status": "online",
                    "created": info.get("created"), "access": info.get("access", "private"),
                    "url": site_url(info), "public_url": site_url(info, False),
                    "password": info.get("password") if info.get("access") == "password" else None})
    return sorted(res, key=lambda x: -(x["created"] or 0))


def mark(status):
    return "🟢" if status == "online" else "🔴" if status else "⚪️"


@bot.command("bots")
@bot.button("bots")
def show_bots(c):
    rows = bots_list()
    if not rows:
        return c.show(card("🧩 <b>Мои боты</b>", ["Пока ни одного — напиши идею, сделаю первого."]))
    lines = []
    for r in rows:
        lines.append(f"{mark(r['status'])} {r['emoji']} <b>{esc(r['name'])}</b>"
                     + (f" · @{esc(r['username'])}" if r["username"] else ""))
        if r["summary"]:
            lines.append(f"<i>{esc(r['summary'])}</i>")
    c.show(card(f"🧩 <b>Мои боты</b> · {len(rows)}", lines),
           kit.rows([b(f"{r['emoji']} {r['name']}", f"bot|{r['slug']}") for r in rows]) + [[b("‹ Главное", "home")]])


@bot.button("bot")
def show_bot(c):
    slug = c.args[0] if c.args else ""
    info = registry().get(slug)
    if not info:
        return c.toast("Бота уже нет")
    st = pm2_status().get(info["pm2"])
    c.show(card(f"{info.get('emoji', '🧩')} <b>{esc(info['name'])}</b>",
                [f"{mark(st)} {'работает' if st == 'online' else 'не работает' if st else 'не запущен'}"
                 + (f" · @{esc(info['username'])}" if info.get("username") else ""),
                 f"<i>{esc(info.get('summary', ''))}</i>"]),
           ([[kit.url("▶️ Открыть", f"https://t.me/{info['username']}", "success")]] if info.get("username") else [])
           + [[b("✏️ Правки", f"fix|{slug}", "primary"), b("📜 Логи", f"logs|{slug}")],
              [b("🔄 Перезапустить", f"restart|{slug}"), b("🗑 Удалить", f"del?|{slug}")],
              [b("‹ Мои боты", "bots")]])


@bot.button("logs")
def on_logs(c):
    info = registry().get(c.args[0] if c.args else "")
    if info:
        c.say(f"📜 <b>{esc(info['name'])}</b>\n<pre>{esc(logs(info['pm2']))}</pre>",
              [[b("✏️ Починить", f"fix|{info['slug']}", "primary")]])


@bot.button("restart")
def on_restart(c):
    info = registry().get(c.args[0] if c.args else "")
    if info:
        sh("pm2", "restart", info["pm2"], timeout=60)
        c.toast("Перезапустил")
        show_bot(c)


@bot.button("del?")
def on_del_ask(c):
    info = registry().get(c.args[0] if c.args else "")
    if info:
        c.show(card(f"🗑 <b>Удалить «{esc(info['name'])}»?</b>", ["Бот остановится, код и данные сотрутся."]),
               [[b("Да, удалить", f"del|{info['slug']}", "danger"), b("Нет", f"bot|{info['slug']}")]])


@bot.button("del")
def on_del(c):
    remove(c.args[0] if c.args else "")
    c.toast("Удалил")
    show_bots(c)


def remove(slug):
    info = registry().get(slug)
    if not info:
        return False
    sh("pm2", "delete", info["pm2"], timeout=30)
    sh("pm2", "save", timeout=30)
    if os.path.realpath(info["dir"]).startswith(os.path.realpath(BOTS) + os.sep):
        shutil.rmtree(info["dir"], ignore_errors=True)
    reg_set(slug, None)
    return True


# ---------- сайты ----------
# Сайт — статика в /root/sites/<slug>/, отдаётся этим же сервером: https://apps.<ip>.sslip.io/s/<slug>/.
# Доступ (info["access"]): "private" — только по ссылке с ключом (?k=…, дальше cookie), "password" — пароль
# (окно браузера), "public" — открыт всем. После сборки сайт закрыт; Глеб сам решает, защищать или выпускать.

ACCESS_NAMES = {"private": "🔑 по ссылке-ключу", "password": "🔒 по паролю", "public": "🌍 открыт всем"}


def sites():
    try:
        with open(SITES_REG) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def site_set(slug, info=None):
    with reg_lock:
        r = sites()
        if info is None:
            r.pop(slug, None)
        else:
            r[slug] = info
        os.makedirs(BOTS, exist_ok=True)
        tmp = SITES_REG + ".tmp"
        with open(tmp, "w") as f:
            json.dump(r, f, ensure_ascii=False, indent=1)
        os.replace(tmp, SITES_REG)


def new_site_slug(want):
    slug = re.sub(r"[^a-z0-9-]", "", str(want or "").lower().replace("_", "-"))[:20].strip("-") or "site"
    while slug in sites() or slug in registry() or os.path.exists(os.path.join(SITES, slug)):
        slug = f"{slug[:16]}-{secrets.randbelow(90) + 10}"
    return slug


def site_url(info, private_key=True):
    base = bot.db.get("base_url") or ""
    if not base:
        return ""
    u = f"{base}s/{info['slug']}/"
    return u + f"?k={info['key']}" if private_key and info.get("access") == "private" else u


def _eq(a, b):
    return hmac.compare_digest(str(a).encode(), str(b).encode())


def _site_page(h, code, title, text, headers=None):
    body = (f'<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{esc(title)}</title><body style="font:18px system-ui;display:grid;place-items:center;min-height:90vh;'
            f'text-align:center;color:#444;background:#fafafa"><div><div style="font-size:48px">🔒</div>'
            f'<h1 style="font-size:22px">{esc(title)}</h1><p>{esc(text)}</p></div>').encode()
    h.send_response(code)
    for k, v in (headers or {}).items():
        h.send_header(k, v)
    h.send_header("Content-Type", "text/html; charset=utf-8")
    h.send_header("Content-Length", str(len(body)))
    h.send_header("Cache-Control", "no-store")
    h.end_headers()
    h.wfile.write(body)


def serve_site(h, method):
    u = urllib.parse.urlsplit(h.path)
    m = re.match(r"^/s/([a-z0-9-]+)(/.*)?$", u.path)
    info = sites().get(m.group(1)) if m else None
    if not info:
        _site_page(h, 404, "Такого сайта нет", "Проверь ссылку.")
        return True
    slug = info["slug"]
    if method not in ("GET", "HEAD"):
        h.reply(405, b"method not allowed", "text/plain")
        return True
    if not m.group(2):
        h.send_response(301)
        h.send_header("Location", f"/s/{slug}/" + (f"?{u.query}" if u.query else ""))
        h.end_headers()
        return True
    access = info.get("access", "private")
    if access == "private":
        cookies = {}
        for part in (h.headers.get("Cookie") or "").split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                cookies[k] = v
        k = (urllib.parse.parse_qs(u.query).get("k") or [""])[0]
        if k and _eq(k, info["key"]):  # ссылка с ключом: запоминаем в cookie и убираем ключ из адреса
            h.send_response(302)
            h.send_header("Set-Cookie", f"sk_{slug}={info['key']}; Path=/s/{slug}/; Max-Age=31536000; "
                                        "HttpOnly; Secure; SameSite=Lax")
            h.send_header("Location", u.path)
            h.send_header("Cache-Control", "no-store")
            h.end_headers()
            return True
        if not _eq(cookies.get(f"sk_{slug}", ""), info["key"]):
            _site_page(h, 403, "Сайт закрыт", "Открой его по ссылке от владельца.")
            return True
    elif access == "password":
        import base64
        auth = h.headers.get("Authorization") or ""
        pw = ""
        if auth.startswith("Basic "):
            try:
                pw = base64.b64decode(auth[6:]).decode("utf-8", "replace").partition(":")[2]
            except ValueError:
                pass
        if not pw or not _eq(pw, info.get("password", "")):
            _site_page(h, 401, "Нужен пароль", "Введи пароль, который тебе дал владелец сайта.",
                       {"WWW-Authenticate": 'Basic realm="Site", charset="UTF-8"'})  # заголовки — только latin-1, иначе 502
            return True
    rel = urllib.parse.unquote(m.group(2))
    if rel.endswith("/"):
        rel += "index.html"
    root = os.path.realpath(os.path.join(SITES, slug))
    full = os.path.realpath(os.path.join(root, rel.lstrip("/")))
    parts = [x for x in rel.split("/") if x]
    if not (full.startswith(root + os.sep) and os.path.isfile(full)) or any(x.startswith(".") for x in parts):
        _site_page(h, 404, "Страницы нет", "Такой страницы на сайте нет.")
        return True
    with open(full, "rb") as f:
        data = f.read()
    ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
    if ctype.startswith("text/") or ctype in ("application/javascript", "application/json", "image/svg+xml"):
        ctype += "; charset=utf-8" if "charset" not in ctype else ""
    h.send_response(200)
    h.send_header("Content-Type", ctype)
    h.send_header("Content-Length", str(len(data)))
    h.send_header("Cache-Control", "public, max-age=300" if access == "public" else "no-store")
    if access != "public":
        h.send_header("X-Robots-Tag", "noindex, nofollow")
    h.end_headers()
    if method == "GET":
        h.wfile.write(data)
    return True


def build_site(chat, d):
    st = Status(chat, f"Создаю сайт «{d['name']}»")
    st.step("готовлю папку", force=True)
    slug = new_site_slug((d.get("plan") or {}).get("slug") or d["name"])
    path = os.path.join(SITES, slug)
    os.makedirs(path, exist_ok=True)
    git(path, "init", "-q")
    info = {"kind": "site", "name": d["name"], "emoji": d.get("emoji") or "🌐", "slug": slug, "dir": path,
            "summary": (d.get("plan") or {}).get("summary", "")[:200], "idea": d["idea"], "plan": d.get("plan"),
            "access": "private", "key": secrets.token_urlsafe(12), "created": time.time(), "by": "studio"}
    site_set(slug, info)
    d.update(slug=slug)
    put(d)
    st.step("пишу сайт", force=True)
    ok, out = claude(BUILD_SITE.format(name=d["name"], emoji=d.get("emoji", ""), dir=path, idea=d["idea"],
                                       qa=qa_text(d) or "— нет", plan=plan_text(d), shots=shots_note(d),
                                       fixes="\nПравки к плану: " + "; ".join(d["fixes"]) if d.get("fixes") else ""),
                     path, on_step=st.step)
    if not ok:
        site_set(slug, None)
        shutil.rmtree(path, ignore_errors=True)
        return fail(chat, out, st)
    if not os.path.isfile(os.path.join(path, "index.html")):
        site_set(slug, None)
        shutil.rmtree(path, ignore_errors=True)
        return fail(chat, "Сайт не получился: нет index.html", st)
    commit(path, "Первая версия: " + d["idea"][:200])
    info["about"] = brief(out)
    site_set(slug, info)
    put({"stage": "done", "result": {"slug": slug, "kind": "site"}})
    st.done(site_done_card(info), site_buttons(info))


def site_done_card(info):
    link = site_url(info)
    lines = [esc(info.get("about") or info.get("summary", "")), "", f"{ACCESS_NAMES[info.get('access', 'private')]}"]
    if link:
        lines.append(esc(link))
    return card(f"🌐 <b>Сайт «{esc(info['name'])}» готов</b>", lines,
                "<i>Пока он закрыт — видишь только ты (и тот, кому дашь ссылку). Защитить паролем или выпустить для всех?</i>")


def site_buttons(info):
    slug = info["slug"]
    link = site_url(info)
    rows = [[kit.url("🌐 Открыть сайт", link, "success")]] if link else []
    access = info.get("access", "private")
    row = []
    if access != "password":
        row.append(b("🔒 Защитить паролем", f"smode|{slug}|password"))
    if access != "public":
        row.append(b("🌍 Выпустить для всех", f"spub?|{slug}", "primary"))
    if access == "public":
        row.append(b("🔑 Закрыть снова", f"smode|{slug}|private"))
    return rows + [row, [b("✏️ Правки", f"fix|{slug}"), b("🌐 Мои сайты", "sites")]]


def site_card(info):
    link = site_url(info)
    lines = [ACCESS_NAMES[info.get("access", "private")], f"<i>{esc(info.get('summary', ''))}</i>"]
    if info.get("access") == "password":
        lines.append(f"Пароль: <code>{esc(info.get('password', ''))}</code>")
    if link:
        lines.append(esc(link))
    return card(f"{info.get('emoji', '🌐')} <b>{esc(info['name'])}</b>", lines)


@bot.command("sites")
@bot.button("sites")
def show_sites(c):
    rows = sorted(sites().values(), key=lambda x: -(x.get("created") or 0))
    if not rows:
        return c.show(card("🌐 <b>Мои сайты</b>", ["Пока ни одного — напиши «сделай сайт …»."]), [[b("‹ Главное", "home")]])
    lines = [f"{info.get('emoji', '🌐')} <b>{esc(info['name'])}</b> · {ACCESS_NAMES[info.get('access', 'private')]}"
             for info in rows]
    c.show(card(f"🌐 <b>Мои сайты</b> · {len(rows)}", lines),
           kit.rows([b(f"{i.get('emoji', '🌐')} {i['name']}", f"site|{i['slug']}") for i in rows]) + [[b("‹ Главное", "home")]])


@bot.button("site")
def show_site(c):
    info = sites().get(c.args[0] if c.args else "")
    if not info:
        return c.toast("Сайта уже нет")
    c.show(site_card(info), site_buttons(info) + [[b("🗑 Удалить", f"sdel?|{info['slug']}")]])


def set_access(slug, mode):
    info = sites().get(slug)
    if not info or mode not in ACCESS_NAMES:
        return None
    info["access"] = mode
    if mode == "password":
        info["password"] = "".join(secrets.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(8))
    if mode == "private":
        info["key"] = secrets.token_urlsafe(12)  # старая ссылка перестаёт работать
    site_set(slug, info)
    return info


@bot.button("smode")
def on_smode(c):
    if len(c.args) < 2:
        return
    info = set_access(c.args[0], c.args[1])
    if not info:
        return c.toast("Сайта уже нет")
    c.toast({"password": "Закрыл паролем", "private": "Закрыл ключом", "public": "Открыт всем"}[c.args[1]])
    c.show(site_card(info), site_buttons(info))


@bot.button("spub?")
def on_spub_ask(c):
    info = sites().get(c.args[0] if c.args else "")
    if not info:
        return c.toast("Сайта уже нет")
    c.show(card(f"🌍 <b>Выпустить «{esc(info['name'])}» для всех?</b>",
                ["Любой, у кого есть адрес, сможет открыть сайт без пароля, и его могут найти поисковики.",
                 esc(site_url(info, False))],
                "<i>Свой домен вроде name.ru — отдельная покупка; скажи, подключу, когда он будет.</i>"),
           [[b("🌍 Да, выпустить", f"smode|{info['slug']}|public", "success"), b("Отмена", f"site|{info['slug']}")]])


@bot.button("sdel?")
def on_sdel_ask(c):
    info = sites().get(c.args[0] if c.args else "")
    if info:
        c.show(card(f"🗑 <b>Удалить сайт «{esc(info['name'])}»?</b>", ["Файлы сайта сотрутся, ссылка перестанет работать."]),
               [[b("Да, удалить", f"sdel|{info['slug']}", "danger"), b("Нет", f"site|{info['slug']}")]])


@bot.button("sdel")
def on_sdel(c):
    remove_site(c.args[0] if c.args else "")
    c.toast("Удалил")
    show_sites(c)


def remove_site(slug):
    info = sites().get(slug)
    if not info:
        return False
    if os.path.realpath(info["dir"]).startswith(os.path.realpath(SITES) + os.sep):
        shutil.rmtree(info["dir"], ignore_errors=True)
    site_set(slug, None)
    return True


# ---------- мини-приложение Студии ----------

@bot.api("state")
def api_state(r):
    d = draft()
    pub = {k: d.get(k) for k in ("id", "idea", "stage", "qi", "names", "emoji", "name", "plan", "fixes", "steps",
                                  "error", "started", "result", "queued", "newbot", "kind")}  # токен — не отдаём
    pub["qs"] = [{"q": q["q"], "options": q["options"], "a": q.get("a")} for q in d.get("qs") or []]
    if pub.get("result"):
        slug = pub["result"].get("slug")
        info = registry().get(slug) or sites().get(slug) or {}
        pub["result"] = {"slug": info.get("slug"), "name": info.get("name"), "username": info.get("username"),
                         "about": info.get("about") or info.get("summary"), "kind": info.get("kind", "bot"),
                         "url": site_url(info) if info.get("kind") == "site" else None}
    return {"draft": pub, "bots": bots_list(), "busy": bot.db.get("busy"), "examples": EXAMPLES,
            "secretary": secretary_ready()}


def need(d, stage):
    if d.get("stage") != stage:
        raise kit.Fail("Черновик уже на другом шаге — обнови")


@bot.api("idea")
def api_idea(r):
    text = str(r.body.get("text") or "").strip()
    if len(text) < 3:
        raise kit.Fail("Опиши идею хотя бы парой слов")
    if draft().get("stage") == "build":
        raise kit.Fail("Сейчас собираю другого бота")
    start_idea(owner_chat(), text)
    return api_state(r)


@bot.api("answer")
def api_answer(r):
    d = draft()
    need(d, "ask")
    if not answer(owner_chat(), str(r.body.get("text") or ""), int(r.body.get("i", d.get("qi", 0)))):
        raise kit.Fail("Этот вопрос уже прошли")
    return api_state(r)


@bot.api("enough")
def api_enough(r):
    d = draft()
    need(d, "ask")
    on_enough(kit.Ctx(bot, owner_chat(), args=[d["id"]]))
    return api_state(r)


@bot.api("name")
def api_name(r):
    need(draft(), "name")
    if not set_name(owner_chat(), str(r.body.get("text") or "")):
        raise kit.Fail("Впиши имя")
    return api_state(r)


@bot.api("correct")
def api_correct(r):
    need(draft(), "plan")
    text = str(r.body.get("text") or "").strip()
    if not text:
        raise kit.Fail("Напиши, что поменять")
    correct(owner_chat(), text)
    return api_state(r)


@bot.api("confirm")
def api_confirm(r):
    d = draft()
    if d.get("stage") not in ("plan", "failed"):
        raise kit.Fail("Нечего создавать")
    if job.locked():
        raise kit.Fail("Claude занят другим ботом — чуть позже")
    bot.send(owner_chat(), f"✅ <b>Создаю «{esc(d.get('name', ''))}»</b> — нажато в приложении.")
    confirm(owner_chat())
    return api_state(r)


@bot.api("cancel")
def api_cancel(r):
    if draft().get("stage") == "build":
        raise kit.Fail("Сборку не остановить")
    put({})
    return api_state(r)


@bot.api("fix")
def api_fix(r):
    slug, text = str(r.body.get("slug") or ""), str(r.body.get("text") or "").strip()
    if (slug not in registry() and slug not in sites()) or not text:
        raise kit.Fail("Напиши, что поменять")
    if job.locked():
        raise kit.Fail("Claude занят — попробуй через пару минут")
    bot.later(fix, owner_chat(), slug, text)
    return api_state(r)


@bot.api("restart")
def api_restart(r):
    info = registry().get(str(r.body.get("slug") or ""))
    if not info:
        raise kit.Fail("Бота нет")
    sh("pm2", "restart", info["pm2"], timeout=60)
    return api_state(r)


@bot.api("delete")
def api_delete(r):
    slug = str(r.body.get("slug") or "")
    if not (remove_site(slug) if slug in sites() else remove(slug)):
        raise kit.Fail("Бота нет")
    return api_state(r)


@bot.api("access")
def api_access(r):
    slug, mode = str(r.body.get("slug") or ""), str(r.body.get("mode") or "")
    if not set_access(slug, mode):
        raise kit.Fail("Сайта нет или режим не тот")
    return api_state(r)


# ---------- запуск ----------

def find_url():
    """Адрес приложений: ждём IP и проверяем, что Caddy уже отдаёт наш /health — тогда ставим кнопку."""
    fails = 0
    while True:
        u = base_url()
        if u:
            try:
                with urllib.request.urlopen(u + "health", timeout=20) as r:
                    if r.read() == b"ok":
                        if bot.db.get("base_url") != u:
                            bot.db.set("base_url", u)  # отсюда берут адрес новые боты (app_url_of)
                        bot.db.delete("app_wait")
                        if bot.app_url != u:
                            bot.app_url = u
                            bot._menu()
                            print("Приложение:", u, flush=True)
                        return
            except Exception as e:
                print("https ещё не отвечает:", u, e, flush=True)
        fails += 1
        if fails == 3:
            bot.db.set("app_wait", True)
        time.sleep(60)


def main():
    if not bot.token:
        sys.exit("Нет STUDIO_TOKEN (/etc/studio-bot.env)")
    os.makedirs(BOTS, exist_ok=True)
    for f in ("kit.css", "kit.js"):  # общий дизайн — тот же, что у сделанных ботов
        shutil.copy(os.path.join(KIT, "template", "webapp", f), os.path.join(HERE, "webapp", f))
    d = draft()
    if d.get("stage") == "build":  # перезапуск посреди сборки — сборку не продолжить
        d.update(stage="failed", error="Студия перезапустилась во время сборки")
        put(d)
    elif d.get("stage") in ("think", "plan_wait"):
        d["stage"] = {"think": "name", "plan_wait": "name" if not d.get("plan") else "plan"}[d["stage"]]
        put(d)
    threading.Thread(target=find_url, daemon=True).start()
    bot.run()


if __name__ == "__main__":
    main()
