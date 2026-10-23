#!/usr/bin/env python3
"""Telegram-бот «Пульс»: все чаты Claude Code Глеба на одном экране.

Каждая облачная сессия запускает pulse/reporter.py: он читает журнал сессии и шлёт сводку в Главного
(POST /api/pulse с ключом входа Claude) → Главный пересылает её сюда, на 127.0.0.1:PULSE_INGEST_PORT.
Бот показывает по каждому чату: заполненность контекста, сколько задач из плана сделано, что делает сейчас,
закончил ли и не задал ли вопрос, — живой карточкой в чате и в мини-приложении; пишет, когда чат закончил,
спросил что-то или контекст подходит к концу.

Переменные окружения (на сервере — /etc/pulse-bot.env):
  PULSE_TOKEN        токен от @BotFather
  PULSE_ALLOWED      Telegram ID через запятую; если пусто — владельцем станет первый /start
  PULSE_DATA         папка с данными (по умолчанию pulse/data, вне git)
  PULSE_PORT         мини-приложение на 127.0.0.1 (8798; снаружи — pulse.<host> через Caddy планера)
  PULSE_INGEST_PORT  приём сводок от Главного на 127.0.0.1 (8799; наружу не открыт)
  PULSE_URL          свой https-адрес приложения (иначе https://pulse.<ip-через-дефисы>.sslip.io)
Только стандартная библиотека Python.
"""
import hashlib
import hmac
import html
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "common"))
import support  # noqa: E402  🛠 /fix — Claude чинит и дорабатывает этого бота
import menukb  # noqa: E402  ☰ меню-квадратик у поля ввода


def _load_ui():
    """Markdown → HTML Telegram из tgbot/ui.py (только стандартная библиотека), без tgbot в sys.path."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("tgbot_ui", os.path.join(REPO, "tgbot", "ui.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tui = _load_ui()

TOKEN = os.environ.get("PULSE_TOKEN", "")
API = f"https://api.telegram.org/bot{TOKEN}/"
ALLOWED = {int(x) for x in re.findall(r"\d+", os.environ.get("PULSE_ALLOWED", ""))}
HUB_ENV = os.environ.get("PULSE_HUB_ENV") or "/etc/hub-bot.env"  # владелец Главного — он же владелец Пульса
HUB_STATE = os.environ.get("PULSE_HUB_STATE") or os.path.join(REPO, "hub", "data", "state.json")
DATA = os.environ.get("PULSE_DATA") or os.path.join(HERE, "data")
STATE = os.path.join(DATA, "state.json")
PORT = int(os.environ.get("PULSE_PORT") or 8798)
INGEST_PORT = int(os.environ.get("PULSE_INGEST_PORT") or 8799)
WEB = os.path.join(HERE, "webapp")
TZ = ZoneInfo("Europe/Moscow")
public_url = (os.environ.get("PULSE_URL") or "").rstrip("/")
https_note = "ищу адрес"

OFFLINE = 180              # нет сводки 3 мин (сводка — минимум раз в минуту) — «нет связи»
KEEP = 6 * 3600            # на экране — чаты, где что-то было за 6 часов (остальные — в «скрытых» не числятся)
NEW_CHAT_PCT = 60          # с этого заполнения контекста — «пора в новый чат» (Глеб 16.10)
DEFAULT_WINDOW = 200_000   # окно контекста, пока не узнали точнее (см. window())
BIG_WINDOW = 1_000_000
COMPACT_PCT = 80           # автосжатие в облаке — на 80% окна (CLAUDE_AUTOCOMPACT_PCT_OVERRIDE)
CTX_STEPS = (60, 80, 90)   # пороги предупреждений о контексте
LIVE_EVERY = 6             # живую карточку правим не чаще раза в 6 с
# Сам бот ничего не присылает (Глеб 15.10: «чтобы бот сам мне не присылал без моей надобности») — всё выключено,
# включить можно в ⚙️ → «🔔 Уведомления». Карточка /start правится на месте, новых сообщений не шлёт.
ALERTS = {"done": ("✅ Закончил работу", False), "question": ("❓ Задал вопрос", False),
          "ctx": ("🧠 Контекст 60/80/90%", False), "offline": ("💤 Пропала связь", False)}
STATUS = {"question": ("❓", "ждёт ответа"), "working": ("⚙️", "работает"), "waiting": ("✅", "ждёт тебя"),
          "offline": ("💤", "нет связи"), "ended": ("⏹", "закрыт")}
ORDER = ["question", "working", "waiting", "offline", "ended"]
MODES = {"chat": "💬 чат", "app": "📱 приложение"}

LOCK = threading.RLock()
SUP = None
offset = 0
st = {}
_live_at = {}  # chat → когда правили живую карточку


def esc(x):
    return html.escape(str(x), quote=False)


# ---------- состояние ----------

def load():
    for path in (STATE, STATE + ".bak"):
        try:
            with open(path) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as e:
            print(f"состояние {os.path.basename(path)} не читается: {e}")
    return {}


def save():
    os.makedirs(DATA, exist_ok=True)
    tmp = STATE + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(st, f, ensure_ascii=False)
    try:
        if os.path.exists(STATE):
            shutil.copy2(STATE, STATE + ".bak")
    except OSError:
        pass
    os.replace(tmp, STATE)


def sessions():
    return st.setdefault("sessions", {})


def alerts():
    a = st.setdefault("alerts", {})
    return {k: a.get(k, dflt) for k, (_, dflt) in ALERTS.items()}


# ---------- Telegram ----------

def plain_markup(params):
    rm = params.get("reply_markup") or {}
    rows = rm.get("inline_keyboard")
    if not rows or not any("style" in b for row in rows for b in row):
        return None
    return dict(params, reply_markup={"inline_keyboard": [[{k: v for k, v in b.items() if k != "style"}
                                                            for b in row] for row in rows]})


def tg(method, **params):
    req = urllib.request.Request(API + method, data=json.dumps(params).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=70) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        if "message is not modified" in detail:
            return {"ok": True}
        plain = plain_markup(params)
        if e.code == 400 and plain and any(w in detail.lower() for w in ("button", "markup", "style")):
            return tg(method, **plain)
        print(method, e.code, detail[:300])
        raise


def send(chat, text, buttons=None):
    extra = {"reply_markup": {"inline_keyboard": buttons}} if buttons is not None else {}
    return tg("sendMessage", chat_id=chat, text=text, parse_mode="HTML",
              link_preview_options={"is_disabled": True}, **extra)


def edit(chat, mid, text, buttons=None):
    extra = {"reply_markup": {"inline_keyboard": buttons}} if buttons is not None else {}
    return tg("editMessageText", chat_id=chat, message_id=mid, text=text, parse_mode="HTML",
              link_preview_options={"is_disabled": True}, **extra)


def show(chat, mid, text, buttons=None):
    return edit(chat, mid, text, buttons) if mid else send(chat, text, buttons)


def clip(text, n=24):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def btn(text, data, style=None):
    b = {"text": clip(text), "callback_data": data}
    if style:
        b["style"] = style
    return b


def link(text, url):
    return {"text": clip(text), "url": url}


def quote(text, expandable=False):
    return f"<blockquote{' expandable' if expandable else ''}>{text}</blockquote>"


def fmt(title, body="", note=""):
    parts = [f"<b>{title}</b>"]
    if body:
        parts.append(quote(body, expandable=body.count("\n") >= 10 or len(body) > 700))
    if note:
        parts.append(f"<i>{note}</i>")
    return "\n".join(parts)


# ---------- доступ ----------

def hub_owner():
    """Владелец Главного на этом сервере: HUB_ALLOWED или первый /start там (hub/data/state.json); 0 — нет."""
    try:
        with open(HUB_ENV) as f:
            ids = re.findall(r"^HUB_ALLOWED=\D*(\d+)", f.read(), re.M)
        if ids:
            return int(ids[0])
    except OSError:
        pass
    try:
        with open(HUB_STATE) as f:
            return int(json.load(f).get("owner") or 0)
    except (OSError, ValueError, TypeError, AttributeError):
        return 0


def owner_id():
    # 23.10: владелец не записан (он не жал /start в Пульсе) — мини-приложение отвечало «Нет связи», сводки
    # не уходили. Теперь без записи владелец — тот же, что у Главного
    return st.get("owner") or min(ALLOWED or {0}) or hub_owner()


def allowed(uid):
    return bool(uid) and (uid in ALLOWED or uid == owner_id())


def claim(uid):
    """Первый /start — владелец (если PULSE_ALLOWED пуст и владельца Главного нет)."""
    if not ALLOWED and not st.get("owner"):
        st["owner"] = hub_owner() or uid
        save()
    return allowed(uid)


# ---------- сводки сессий ----------

KNOWN_WINDOWS = {"claude-opus-5-5": 1_000_000}  # «213.2k of 1M tokens» в Usage приложения Claude (Глеб, 16.10)


def window(model, tokens=0):
    """Окно контекста модели: узнаём по первому автосжатию (оно на COMPACT_PCT% окна). До этого — 200 тыс.;
    если чат ушёл за порог сжатия 200 тыс. и не сжался — окно больше (1 млн). -> (окно, точно ли)."""
    w = st.setdefault("windows", {}).get(model or "?") or KNOWN_WINDOWS.get(model or "?")
    if w:
        return w, True
    big = st.setdefault("big", {})  # модель, у которой чат уже ушёл за порог сжатия 200 тыс. — окно больше
    if tokens > DEFAULT_WINDOW * COMPACT_PCT / 100 * 1.05 and not big.get(model or "?"):
        big[model or "?"] = True
        st["_dirty"] = True
    return (BIG_WINDOW if big.get(model or "?") else DEFAULT_WINDOW), False


def learn_window(model, pre):
    """preTokens перед автосжатием ≈ COMPACT_PCT% окна → окно (округляем до 100 тыс.)."""
    if model and pre > 50_000:
        w = int(round(pre * 100 / COMPACT_PCT / 100_000) * 100_000)
        if w >= 100_000 and st.setdefault("windows", {}).get(model) != w:
            st["windows"][model] = w
            return True
    return False


def status_of(s, now=None):
    now = now or time.time()
    if s.get("gone"):
        return "ended"
    if now - (s.get("seen") or 0) > OFFLINE:
        # контейнер уснул: закончивший чат так и ждёт тебя, а «работал и пропал» — нет связи
        return "waiting" if s.get("status") == "waiting" else "offline"
    return s.get("status") if s.get("status") in STATUS else "waiting"


def ctx_of(s):
    c = s.get("ctx") or {}
    tok = int(c.get("tokens") or 0)
    w, sure = window(s.get("model"), tok)
    return {"tokens": tok, "window": w, "pct": min(100, round(tok * 100 / w)) if w else 0,
            "compacts": int(c.get("compacts") or 0), "sure": sure}


def view(s, now=None):
    """Сессия для экрана (и для api/state)."""
    stt = status_of(s, now)
    tasks = [t for t in s.get("tasks") or [] if isinstance(t, dict)]
    return {
        "id": s["id"], "title": s.get("title") or "Чат", "url": s.get("url") or "",
        "status": stt, "emoji": STATUS[stt][0], "label": STATUS[stt][1], "model": s.get("model") or "",
        "ctx": ctx_of(s), "prog": s.get("prog"), "agents": int(s.get("agents") or 0),
        "agent_list": [a for a in s.get("agent_list") or [] if isinstance(a, dict)], "tasks": tasks, "done": sum(1 for t in tasks if t.get("status") == "completed"),
        "total": len(tasks), "prompt": s.get("prompt"), "nprompts": s.get("nprompts") or 0,
        "prompts": s.get("prompts") or [], "turn": s.get("turn") or {},
        "action": s.get("action") or "", "question": s.get("question") or "", "options": s.get("options") or [],
        "reply": s.get("reply") or "",
        "started": s.get("started"), "updated": s.get("updated"), "seen": s.get("seen"),
        "repos": s.get("repos") or [],
    }


def visible(now=None):
    now = now or time.time()
    out = [s for s in sessions().values()
           if now - (s.get("seen") or 0) < KEEP and not (s.get("hidden") and s["hidden"] >= (s.get("updated") or 0))]
    out = [view(s, now) for s in out]
    out.sort(key=lambda v: (ORDER.index(v["status"]), -(v["updated"] or 0)))
    return out


def hidden_count(now=None):
    now = now or time.time()
    return sum(1 for s in sessions().values()
               if now - (s.get("seen") or 0) < KEEP and s.get("hidden") and s["hidden"] >= (s.get("updated") or 0))


FIELDS = ("title", "url", "model", "started", "updated", "status", "ctx", "prompt", "prompts", "nprompts", "tasks",
          "turn", "action", "question", "options", "reply", "repos", "prog", "agents", "agent_list")


def ingest(body, now=None):
    """Сводка от репортёра. -> список событий [(вид, сессия-view, доп.)] для уведомлений."""
    now = now or time.time()
    key = str(body.get("rid") or body.get("sid") or "")[:80]
    if not re.fullmatch(r"[\w-]+", key or "-"):
        raise ValueError("нет id сессии")
    with LOCK:
        old = sessions().get(key)
        s = dict(old or {"id": key, "first": now})
        prev = view(old, now) if old else None
        s["seen"] = now
        if body.get("gone"):
            s["gone"] = True
        else:
            s.pop("gone", None)
            for f in FIELDS:
                if f in body:
                    s[f] = body[f]
        sessions()[key] = s
        events = []
        ctx = s.get("ctx") or {}
        if int(ctx.get("compacts") or 0) > int(((old or {}).get("ctx") or {}).get("compacts") or 0):
            learn_window(s.get("model"), int(ctx.get("pre") or 0))
            s["ctx_alert"] = 0
        cur = view(s, now)
        if not old:
            events.append(("new", cur, None))
        elif prev["status"] != cur["status"]:
            if cur["status"] == "waiting" and prev["status"] in ("working", "question"):
                events.append(("done", cur, None))
            elif cur["status"] == "question":
                events.append(("question", cur, None))
        step = max([p for p in CTX_STEPS if cur["ctx"]["pct"] >= p] or [0])
        if step > int(s.get("ctx_alert") or 0):
            s["ctx_alert"] = step
            if old:
                events.append(("ctx", cur, step))
        elif cur["ctx"]["pct"] < CTX_STEPS[0]:
            s["ctx_alert"] = 0
        s.pop("offline_told", None)
        prune(now)
        if events or not old or (s.get("status") != (old or {}).get("status")):
            save()
        else:
            st["_dirty"] = True
        return events


def prune(now):
    ss = sessions()
    for k in sorted(ss, key=lambda k: ss[k].get("seen") or 0)[:-40]:
        ss.pop(k, None)
    for k in [k for k, s in ss.items() if now - (s.get("seen") or 0) > 3 * KEEP]:
        ss.pop(k, None)


# ---------- лимиты аккаунта (как «Usage» в приложении Claude) ----------
# `claude -p /usage` на сервере — тот же аккаунт Глеба; токенов не тратит. Раз в LIMITS_EVERY, пока есть живая карточка.

LIMITS_EVERY = 5 * 60
LIMIT_NAMES = {"session": "Лимит 5 ч", "week": "Неделя", "fable": "Fable"}
USAGE_RE = re.compile(r"Current (session|week \(all models\)|week \(Fable[^)]*\))\s*:\s*(\d+)% used(?:\s*·\s*resets\s*([^\n(]+?)\s*\(UTC\))?", re.I)
_limits_at = 0


def parse_usage(text, now=None):
    """Вывод /usage → {"session"|"week"|"fable": {"pct", "reset" (epoch или None)}}."""
    now = now or time.time()
    out = {}
    for m in USAGE_RE.finditer(text or ""):
        kind = "session" if m.group(1).lower() == "session" else "fable" if "fable" in m.group(1).lower() else "week"
        out[kind] = {"pct": int(m.group(2)), "reset": utc_time(m.group(3), now)}
    return out


def utc_time(text, now):
    """«Oct 16, 11:10am» / «11pm» (UTC) → epoch; не разобрали — None."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        return None
    year = datetime.fromtimestamp(now, ZoneInfo("UTC")).year
    for f in ("%b %d, %I:%M%p", "%b %d, %I%p", "%b %d %I:%M%p", "%b %d %I%p", "%I:%M%p", "%I%p"):
        try:
            d = datetime.strptime(text.replace(" am", "am").replace(" pm", "pm"), f)
        except ValueError:
            continue
        if "%b" not in f:
            today = datetime.fromtimestamp(now, ZoneInfo("UTC"))
            d = d.replace(year=today.year, month=today.month, day=today.day)
            t = d.replace(tzinfo=ZoneInfo("UTC")).timestamp()
            return t + 86400 if t < now - 60 else t
        return d.replace(year=year, tzinfo=ZoneInfo("UTC")).timestamp()
    return None


def fetch_limits():
    os.makedirs(DATA, exist_ok=True)
    try:
        r = subprocess.run(["claude", "-p", "/usage"], capture_output=True, text=True, timeout=90, cwd=DATA,
                           env={**os.environ, "CLAUDECODE": ""})
    except (OSError, subprocess.TimeoutExpired) as e:
        print("usage:", e)
        return None
    got = parse_usage(r.stdout)
    return got or None


def refresh_limits(force=False):
    global _limits_at
    if not force and (time.time() - _limits_at < LIMITS_EVERY or not st.get("live")):
        return
    _limits_at = time.time()
    got = fetch_limits()
    if got:
        with LOCK:
            st["limits"] = {**got, "at": time.time()}
            st["_dirty"] = True


DAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def when_reset(ts, now=None):
    """Время сброса по Москве: «в 14:10», «завтра в 02:00», «чт в 02:00»."""
    if not ts:
        return ""
    now = now or time.time()
    d, today = datetime.fromtimestamp(ts, TZ), datetime.fromtimestamp(now, TZ)
    days = (d.date() - today.date()).days
    day = "" if days <= 0 else "завтра " if days == 1 else DAYS[d.weekday()] + " "
    return f"сброс {day}в {d:%H:%M}"


def limits_text(now=None):
    """Две-три строки лимитов для карточки; старше 30 мин или нет — пусто."""
    now = now or time.time()
    lim = st.get("limits") or {}
    if not lim or now - (lim.get("at") or 0) > 1800:
        return ""
    out = []
    for k in ("session", "week", "fable"):
        x = lim.get(k)
        if not x or (k == "fable" and not x.get("pct")):
            continue
        r = when_reset(x.get("reset"), now)
        out.append(f"{LIMIT_NAMES[k]} {bar(x['pct'])} {x['pct']}%" + (f" · <i>{r}</i>" if r else ""))
    return "\n".join(out)


# ---------- вид в чате ----------

def ago(sec):
    sec = max(0, int(sec))
    if sec < 60:
        return "только что"
    if sec < 3600:
        return f"{sec // 60} мин"
    if sec < 86400:
        h, m = divmod(sec // 60, 60)
        return f"{h} ч {m} мин" if m and h < 6 else f"{h} ч"
    return f"{sec // 86400} д"


def kilo(n):
    return f"{n / 1000:.0f} тыс." if n < 1_000_000 else f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".") + " млн"


def bar(pct, n=12):
    """Сплошная полоска «████░░░░» (Глеб выбрал 16.10 из четырёх видов)."""
    full = max(0, min(n, round(pct * n / 100)))
    return "█" * full + "░" * (n - full)


def ctx_dot(pct):
    return "🟢" if pct < 60 else "🟡" if pct < 80 else "🔴"


def ctx_line(v):
    """🟢 Контекст ████░░░░░░░░ 30%  (≈ — окно модели ещё не подтверждено)."""
    c = v["ctx"]
    return f"{ctx_dot(c['pct'])} Контекст {bar(c['pct'])} {'' if c['sure'] else '≈'}{c['pct']}%"


def plan_line(v):
    if not v["total"]:
        return ""
    pct = round(v["done"] * 100 / v["total"])
    return f"{'✅' if v['done'] == v['total'] else '📋'} План {bar(pct)} {v['done']} из {v['total']}"


def current_task(v):
    for t in v["tasks"]:
        if t.get("status") == "in_progress":
            return t.get("subject") or ""
    return ""


FOR_YOU = re.compile(r"\**\s*Для тебя\s*:?\s*\**\s*:?\s*", re.I)


def split_reply(text):
    """Ответ Claude → (основное, «Для тебя: …»). Во всех чатах Глеба ответ кончается блоком «Для тебя»."""
    text = str(text or "").strip()
    m = None
    for m in FOR_YOU.finditer(text):
        pass
    if not m:
        return text, ""
    return text[:m.start()].rstrip(" \n-—*"), text[m.end():].strip()


def first_line(text, n=110):
    """Первая содержательная строка ответа, без Markdown — для карточки."""
    for line in str(text or "").split("\n"):
        line = re.sub(r"[*_`#>]+", "", line).strip(" -•")
        if line:
            return clip(line, n)
    return ""


def ctx_short(v):
    """«34% контекста»; от 60% — цветная точка, чтобы заметить."""
    pct = v["ctx"]["pct"]
    return ("" if pct < 60 else ctx_dot(pct) + " ") + f"{pct}% контекста"


def first_par(text, n=300):
    """Первый абзац ответа (Markdown) — коротко, для уведомления."""
    text = str(text or "").strip()
    par = re.split(r"\n\s*\n", text, 1)[0]
    if len(par) > n:
        cut = par.rfind(" ", 0, n)
        par = par[:cut if cut > n // 2 else n].rstrip(" ,.;:") + "…"
    return par


def state_word(v, now=None):
    """работает / ждёт ответа / готово — одним-двумя словами."""
    return {"working": "работает", "question": "ждёт ответа", "waiting": "готово", "offline": "нет связи"}.get(
        v["status"], "закрыт")


def agents_word(n):
    return f"🤖 {n} {'помощник' if n % 10 == 1 and n % 100 != 11 else 'помощника' if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else 'помощников'}"


def bars(v):
    """Вариант 2 «Полоски» (Глеб выбрал 16.10): «Сделано ████░░ 40%» и «Память ████░░ 37%», от 60% — «⚠️ открой новый чат»."""
    out = []
    p = v.get("prog")
    if p and p.get("total"):
        out.append(f"Сделано {bar(p['pct'])} {p['pct']}%")
    elif v["status"] == "waiting":  # без плана, но запрос выполнен и чат ждёт тебя
        out.append(f"Сделано {bar(100)} 100%")
    pct = v["ctx"]["pct"]
    out.append(f"Память {bar(pct)} {pct}%" + (" ⚠️ <i>открой новый чат</i>" if pct >= NEW_CHAT_PCT else ""))
    return out


def head(v, now=None, n=32):
    extra = f" · {agents_word(v['agents'])}" if v.get("agents") else ""
    title = f"<b>{esc(clip(v['title'], n))}</b>"
    if v.get("url"):  # название — ссылка: одно нажатие, и ты в этом чате Claude
        title = f'<a href="{esc(v["url"])}">{title}</a>'
    return f"{v['emoji']} {title} · {state_word(v, now)}{extra}"


def steps_word(n):
    return f"{n} {'шаг' if n % 10 == 1 and n % 100 != 11 else 'шага' if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else 'шагов'}"


def agent_lines(v, now=None):
    """Под чатом — каждый работающий агент (Глеб 16.10: «сколько процентов осталось сделать агенту»):
    по плану агента — «сделано 40% · осталось 60%», без плана — сколько работает и шагов (процент не выдумываем)."""
    now = now or time.time()
    out = []
    for a in v.get("agent_list") or []:
        p = a.get("prog") or {}
        name = esc(clip(a.get("name"), 30))
        if p.get("total"):
            out.append(f"🤖 {name} — сделано {p['pct']}% · осталось {100 - p['pct']}%")
        else:
            took = f"{ago(now - a['started'])} · " if a.get("started") else ""
            out.append(f"🤖 {name} — {took}{steps_word(int(a.get('steps') or 0))}")
    return out


def block(v, now=None):
    return "\n".join([head(v, now)] + bars(v) + agent_lines(v, now))


def counts(vs):
    w = sum(1 for v in vs if v["status"] == "working")
    q = sum(1 for v in vs if v["status"] == "question")
    d = sum(1 for v in vs if v["status"] == "waiting")
    parts = ([f"⚙️ {w} в работе"] if w else []) + ([f"❓ {q} спрашивают"] if q else []) + \
        ([f"✅ {d} ждут тебя"] if d else [])
    return " · ".join(parts) or "все чаты спят"


def mode_note():
    return f"Режим: {MODES.get(ui_mode() or 'chat')} · /mode"


def home_text(now=None):
    now = now or time.time()
    vs = visible(now)
    lim = limits_text(now)
    top = "<b>📡 Пульс</b>" + (f"\n{lim}" if lim else "")
    if not vs:
        return top + "\n\nСейчас чатов нет. Напиши в любом чате Claude — он появится здесь."
    hid = hidden_count(now)
    foot = f"\n\n<i>скрыто {hid}</i>" if hid else ""
    return top + "\n\n" + "\n\n".join(block(v, now) for v in vs[:8]) + foot


def home_buttons(vs=None):
    vs = visible() if vs is None else vs
    # кнопка чата — сразу в чат Claude (Глеб 16.10: «переход одним нажатием»); подробно — в «⚙️» → «📋 Подробно»
    chats = [link(f"{v['emoji']} {clip(v['title'], 12)}", v["url"]) if v["url"]
             else btn(f"{v['emoji']} {clip(v['title'], 12)}", f"s|{v['id']}") for v in vs[:9]]
    rows = [chats[i:i + 3] for i in range(0, len(chats), 3)]
    rows += app_buttons()
    rows.append([btn("🔄 Обновить", "home"), btn("⚙️", "more")])
    return rows


def ui_mode():
    return st.get("mode") if st.get("mode") in MODES else ""


def set_mode(m):
    if m not in MODES:
        raise ValueError("Режим — chat или app")
    st["mode"] = m
    save()


def app_buttons(always=False):
    if not public_url or (ui_mode() != "app" and not always):
        return []
    return [[{"text": "📱 Открыть Пульс", "web_app": {"url": public_url + "/"}}]]


def home(chat, mid=None, live=True):
    """Главная карточка; она же живая — правится сама, пока чаты что-то делают."""
    if time.time() - (st.get("limits") or {}).get("at", 0) > LIMITS_EVERY:
        threading.Thread(target=lambda: (refresh_limits(force=True), refresh_live(force=True)), daemon=True).start()
    vs = visible()
    r = show(chat, mid, home_text(), home_buttons(vs))
    if live:
        m = mid or ((r or {}).get("result") or {}).get("message_id")
        if m:
            st.setdefault("live", {})[str(chat)] = m
            _live_at[chat] = time.time()
            save()
    return r


def md(text, limit=None):
    """Markdown ответа Claude → HTML Telegram (tgbot/ui.py); длинное режем по строке, не по слову."""
    text = str(text or "").strip()
    if limit and len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        text = text[:cut if cut > limit // 2 else limit].rstrip() + "\n…"
    return tui.md(text)


def detail_text(v, now=None):
    c = v["ctx"]
    p = v.get("prog")
    top = [head(v, now, 60)] + bars(v)
    if p and p.get("total"):
        top[1] += f" <i>({p['done']} из {p['total']})</i>"
    parts = ["\n".join(top)]
    if v["status"] == "question" and v["question"]:
        parts.append("❓ " + esc(v["question"]) + "".join(f"\n• {esc(o)}" for o in v.get("options") or []))
    if v["prompt"]:
        parts.append("<b>Твой запрос:</b> " + esc(clip(v["prompt"].get("text"), 300)))
    if v["tasks"]:
        icon = {"completed": "✅", "in_progress": "▸"}
        parts.append("<b>План:</b>\n" + "\n".join(f"{icon.get(t.get('status'), '○')} {esc(clip(t.get('subject'), 70))}"
                                                  for t in v["tasks"][-12:]))
    if v["status"] == "working" and v["action"]:
        parts.append("<b>Сейчас:</b> " + esc(v["action"]))
    if v["reply"] and v["status"] != "working":
        main, you = split_reply(v["reply"])
        if main:
            parts.append("<b>Ответ:</b> " + md(first_par(main, 500)))
        if you:
            parts.append("👉 " + md(you, 400))
    if c["compacts"]:
        parts.append(f"Сжатий контекста: {c['compacts']}")
    text = "\n\n".join(parts)
    while len(text) > 4000 and len(parts) > 2:
        parts.pop()
        text = "\n\n".join(parts)
    return text[:4000]


def detail_buttons(v):
    rows = []
    if v["url"]:
        rows.append([link("↗️ Открыть чат", v["url"])])
    rows.append([btn("← Все", "home"), btn("🔄", f"s|{v['id']}"), btn("🙈 Скрыть", f"h|{v['id']}")])
    return rows


def detail(chat, mid, sid):
    s = sessions().get(sid)
    if not s:
        return home(chat, mid)
    v = view(s)
    return show(chat, mid, detail_text(v), detail_buttons(v))


def alerts_text():
    a = alerts()
    lines = [f"{'🔔' if a[k] else '🔕'} {name}" for k, (name, _) in ALERTS.items()]
    return fmt("🔔 Уведомления", "\n".join(lines), "По умолчанию всё выключено — сам ничего не пишу, "
               "только карточка по /start. Нажми, чтобы включить.")


def alerts_buttons():
    a = alerts()
    rows = [[btn(("✅ " if a[k] else "▫️ ") + name.split(" ", 1)[1], f"at|{k}")] for k, (name, _) in ALERTS.items()]
    rows.append([btn("← Назад", "home")])
    return rows


HELP = ("Каждый чат Claude Code сам присылает сюда сводку: сколько заполнен контекст, сколько задач из плана "
        "сделано, что делает прямо сейчас, закончил или ждёт твоего ответа.\n\n"
        "🟢 до 60% · 🟡 до 80% · 🔴 дальше — на 80% чат сам сжимает контекст (часть подробностей теряется), "
        "так что на 🔴 лучше начать новый чат (напиши в старом «121»).\n"
        "≈ — окно контекста модели ещё не подтверждено (узнаю после первого сжатия).")


def more_text():
    hid = hidden_count()
    return fmt("⚙️ Ещё", HELP, mode_note() + (f" · скрыто чатов: {hid}" if hid else ""))


def more_buttons():
    rows = [[btn("📋 Подробно по чатам", "dl")], [btn("🔔 Уведомления", "al"), btn("⚙️ Режим", "mode")],
            [support.Support.button()]]
    if hidden_count():
        rows.append([btn("👁 Показать скрытые", "unhide")])
    rows.append([btn("← Назад", "home")])
    return rows


def mode_card(chat, mid=None):
    return show(chat, mid, fmt("🧭 Как удобнее?", "💬 <b>В чате</b> — живая карточка и кнопки здесь\n"
                               "📱 <b>Приложение</b> — кольца контекста и планы в одном окне",
                               "Поменять можно в любой момент: /mode"),
                [[btn("💬 В чате", "mode:chat", "primary"), btn("📱 Приложение", "mode:app", "primary")]])


# ---------- уведомления и живая карточка ----------

def notify(events):
    """Уведомления в стиле карточки: заголовок — что случилось, 1 абзац ответа, «👉 Для тебя», кнопка «Открыть чат»."""
    chat = owner_id()
    if not chat:
        return
    a = alerts()
    for kind, v, extra in events:
        buttons = [[link("↗️ Открыть чат", v["url"])]] if v["url"] else [[btn("🔎 Подробно", f"s|{v['id']}")]]
        title = esc(clip(v["title"], 60))
        try:
            if kind == "done" and a["done"]:
                main, you = split_reply(v["reply"])
                lines = [f"✅ <b>{title}</b> — готово"]
                if main:
                    lines.append(md(first_par(main)))
                if you:
                    lines.append("👉 " + md(you, 400))
                send(chat, "\n".join(lines), buttons)
            elif kind == "question" and a["question"]:
                opts = "".join(f"\n• {esc(o)}" for o in v.get("options") or [])
                send(chat, f"❓ <b>{title}</b> — вопрос\n{esc(v['question'] or first_line(v['reply'], 300))}{opts}",
                     buttons)
            elif kind == "ctx" and a["ctx"]:
                tip = {60: "Большие задачи лучше начинать в новом чате.",
                       80: "Скоро автосжатие — часть подробностей забудется.",
                       90: "Почти полный — напиши в чате «121» и продолжи в новом."}[extra]
                send(chat, f"{ctx_dot(extra)} <b>{title}</b> — {extra}% контекста\n{tip}", buttons)
            elif kind == "offline" and a["offline"]:
                send(chat, f"💤 <b>{title}</b> — нет связи\nСводок нет больше 3 минут: чат закрыт или уснул.", buttons)
        except Exception as e:
            print("notify:", e)


def refresh_live(force=False):
    """Живая карточка /start: правим, если изменилось (не чаще LIVE_EVERY с)."""
    with LOCK:
        live = dict(st.get("live") or {})
        text_by = {}
    for chat, mid in live.items():
        chat_i = int(chat)
        if not force and time.time() - _live_at.get(chat_i, 0) < LIVE_EVERY:
            continue
        with LOCK:
            vs = visible()
            text = home_text()
            key = hashlib.sha1(re.sub(r"Обновлено [\d:]+", "", text).encode()).hexdigest()
            if not force and st.get("live_key", {}).get(chat) == key:
                continue
            st.setdefault("live_key", {})[chat] = key
            buttons = home_buttons(vs)
        _live_at[chat_i] = time.time()
        try:
            edit(chat_i, mid, text, buttons)
        except urllib.error.HTTPError as e:
            if e.code == 400:  # сообщение удалено или слишком старое — живой карточки больше нет
                with LOCK:
                    (st.get("live") or {}).pop(chat, None)
                    save()
        except Exception as e:
            print("live:", e)
        text_by[chat] = text
    return text_by


def watcher():
    """Раз в несколько секунд: живая карточка, «нет связи», сброс на диск."""
    while True:
        time.sleep(LIVE_EVERY)
        try:
            events = []
            now = time.time()
            with LOCK:
                for s in sessions().values():
                    if (not s.get("gone") and not s.get("offline_told") and s.get("status") == "working"
                            and OFFLINE < now - (s.get("seen") or 0) < 3600):
                        s["offline_told"] = True
                        events.append(("offline", view(s, now), None))
                if st.pop("_dirty", None) or events:
                    save()
            notify(events)
            refresh_limits()
            refresh_live()
        except Exception as e:
            print("watcher:", e)
            traceback.print_exc()


# ---------- обработка ----------

def setup_bot():
    commands = [{"command": "start", "description": "Все чаты Claude Code"},
                {"command": "app", "description": "Приложение"},
                {"command": "alerts", "description": "Уведомления"},
                {"command": "mode", "description": "Чат или приложение"},
                {"command": "help", "description": "Как это работает"}]
    desc = ("Все чаты Claude Code на одном экране: сколько заполнен контекст, сколько задач сделано, "
            "что делает сейчас, закончил ли. Пишет, когда чат закончил или ждёт ответа.")
    short = "Пульс чатов Claude Code: контекст, задачи, статус."
    key = hashlib.sha1(json.dumps([commands, desc, short], ensure_ascii=False).encode()).hexdigest()
    if st.get("bot_setup") != key:
        try:
            tg("setMyCommands", commands=commands)
            tg("setMyDescription", description=desc)
            tg("setMyShortDescription", short_description=short)
            st["bot_setup"] = key
            save()
        except Exception as e:
            print("setup:", e)


KB = menukb.MenuKB([["📡 Чаты", "📋 Подробно"], ["🔔 Уведомления", "🛠 Поддержка"], ["⚙️ Режим", "📱 Приложение"]],
                   version=1, placeholder="☰ меню — справа")


def send_kb(chat, text, markup):
    return tg("sendMessage", chat_id=chat, text=text, reply_markup=markup)


def ensure_kb(chat, force=False):
    KB.ensure(send_kb, chat, done=st.get("kb_ver"), mark=lambda v: (st.__setitem__("kb_ver", v), save()), force=force)


def chats_list(chat, mid=None):
    """«📋 Подробно»: какой чат показать подробно."""
    vs = visible()
    chats = [btn(f"{v['emoji']} {clip(v['title'], 20)}", f"s|{v['id']}") for v in vs[:9]]
    show(chat, mid, "<b>📋 Подробно</b>\nКакой чат?" if vs else "Сейчас чатов нет.",
         [chats[i:i + 2] for i in range(0, len(chats), 2)] + [[btn("← Назад", "home")]])


def on_kb(chat, text):
    """Кнопка меню-квадратика → тот же экран, что у кнопок в сообщениях."""
    if text == "📡 Чаты":
        home(chat)
    elif text == "📋 Подробно":
        chats_list(chat)
    elif text == "🔔 Уведомления":
        send(chat, alerts_text(), alerts_buttons())
    elif text == "🛠 Поддержка":
        SUP.ask(chat) if SUP else send(chat, "⚠️ Поддержка ещё запускается — попробуй через минуту")
    elif text == "⚙️ Режим":
        mode_card(chat)
    elif text == "📱 Приложение":
        on_message({"chat": {"id": chat, "type": "private"}, "from": {"id": owner_id()}, "text": "/app"})


def on_message(msg):
    chat = msg["chat"]["id"]
    uid = (msg.get("from") or {}).get("id")
    if msg["chat"].get("type") != "private":
        return
    text = (msg.get("text") or "").strip()
    cmd = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
    if cmd == "/start" and not claim(uid):
        send(chat, "🔒 Это личный бот.")
        return
    if not allowed(uid):
        send(chat, "🔒 Это личный бот.")
        return
    if KB.has(text):
        return on_kb(chat, text)
    if cmd == "/start":
        ensure_kb(chat, force=True)  # ☰ — на каждый /start: Telegram мог его потерять
    if cmd == "/mode":
        mode_card(chat)
    elif cmd == "/alerts":
        send(chat, alerts_text(), alerts_buttons())
    elif cmd == "/help":
        send(chat, fmt("📡 Как это работает", HELP), [[btn("← Все чаты", "home")]])
    elif cmd == "/app":
        rows = app_buttons(always=True)
        send(chat, fmt("📱 Приложение", "Кольца контекста, планы и ответы всех чатов.") if rows
             else f"⚠️ Приложение ещё настраивается: {esc(https_note)}", rows or None)
    elif cmd == "/update":
        update(chat)
    else:
        home(chat)


def on_callback(cq):
    uid = cq["from"]["id"]
    msg = cq.get("message") or {}
    chat, mid = msg.get("chat", {}).get("id"), msg.get("message_id")
    data = cq.get("data") or ""
    note = ""
    if not allowed(uid) or not chat:
        tg("answerCallbackQuery", callback_query_id=cq["id"], text="🔒 Это личный бот.")
        return
    try:
        kind, _, arg = data.partition("|")
        if data == "home":
            home(chat, mid)
        elif kind == "s":
            if (st.get("live") or {}).get(str(chat)) == mid:
                st["live"].pop(str(chat), None)
            detail(chat, mid, arg)
        elif kind == "h":
            if arg in sessions():
                sessions()[arg]["hidden"] = time.time()
                save()
            note = "Скрыл до новой активности"
            home(chat, mid)
        elif data == "unhide":
            for s in sessions().values():
                s.pop("hidden", None)
            save()
            home(chat, mid)
        elif data == "al":
            show(chat, mid, alerts_text(), alerts_buttons())
        elif kind == "at" and arg in ALERTS:
            a = alerts()
            st["alerts"][arg] = not a[arg]
            save()
            show(chat, mid, alerts_text(), alerts_buttons())
        elif data == "more":
            show(chat, mid, more_text(), more_buttons())
        elif data == "dl":
            chats_list(chat, mid)
        elif data == "mode":
            mode_card(chat, mid)
        elif data.startswith("mode:"):
            set_mode(data[5:])
            edit(chat, mid, fmt("✅ Режим: " + MODES[data[5:]], note="Сменить — /mode"), [])
            home(chat)
        else:
            note = "Кнопка устарела — /start"
    except Exception as e:
        print("callback:", e)
        traceback.print_exc()
        note = "⚠️ Не получилось — попробуй ещё раз"
    try:
        tg("answerCallbackQuery", callback_query_id=cq["id"], text=note[:190])
    except Exception:
        pass


def update(chat):
    res = subprocess.run(["git", "pull", "--ff-only", "origin", "main"], cwd=REPO, capture_output=True, text=True,
                         timeout=120)
    send(chat, fmt("🔄 Обновление", esc((res.stdout + res.stderr).strip()[-1500:] or "ok")))
    if res.returncode == 0:
        tg("getUpdates", offset=offset, timeout=0)
        os._exit(0)


# ---------- мини-приложение ----------

def check_init_data(init_data, max_age=86400):
    try:
        pairs = dict(urllib.parse.parse_qsl(init_data or "", keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None
    got = pairs.pop("hash", "")
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()

    def sign(d):
        check = "\n".join(f"{k}={v}" for k, v in sorted(d.items()))
        return hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    variants = [pairs] + ([{k: v for k, v in pairs.items() if k != "signature"}] if "signature" in pairs else [])
    if not got or not any(hmac.compare_digest(sign(d).encode(), got.encode()) for d in variants):
        return None
    try:
        if time.time() - int(pairs.get("auth_date", 0)) > max_age:
            return None
        return json.loads(pairs.get("user", "null"))
    except ValueError:
        return None


def app_state():
    return {"sessions": visible(), "hidden": hidden_count(), "alerts": alerts(), "mode": ui_mode(),
            "limits": st.get("limits") or {},
            "now": time.time()}


def api_post(path, body):
    if path == "/api/hide":
        sid = str(body.get("id") or "")
        if sid not in sessions():
            raise KeyError("нет такого чата")
        sessions()[sid]["hidden"] = time.time()
    elif path == "/api/unhide_all":
        for s in sessions().values():
            s.pop("hidden", None)
    elif path == "/api/alerts":
        key = str(body.get("key") or "")
        if key not in ALERTS:
            raise KeyError("нет такого уведомления")
        alerts()
        st["alerts"][key] = bool(body.get("on"))
    elif path == "/api/mode":
        set_mode(str(body.get("mode") or ""))
        chat = owner_id()
        if chat and body.get("mode") == "chat":
            threading.Thread(target=home, args=(chat,), daemon=True).start()
    else:
        raise KeyError("нет такого метода")
    save()
    return {"ok": True, "alerts": alerts()}


def asset_version():
    h = hashlib.sha1()
    if os.path.isdir(WEB):
        for f in sorted(os.listdir(WEB)):
            p = os.path.join(WEB, f)
            if os.path.isfile(p):
                with open(p, "rb") as fh:
                    h.update(fh.read())
    return h.hexdigest()[:10]


VERSION = asset_version()


def content_length(headers):
    try:
        return max(0, int(headers.get("Content-Length") or 0))
    except ValueError:
        return 0


class Web(BaseHTTPRequestHandler):
    server_version = "Pulse/1"
    timeout = 60

    def log_message(self, fmt, *args):
        pass

    def reply(self, code, body, ctype="application/json; charset=utf-8", cache="no-store"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def user(self):
        u = check_init_data(self.headers.get("X-Init-Data") or "")
        return u if isinstance(u, dict) and allowed(u.get("id")) else None

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/health":
            return self.reply(200, "ok", "text/plain")
        if path.startswith("/api/"):
            if not self.user():
                return self.reply(401, {"error": "Открой приложение из Telegram"})
            if path == "/api/state":
                with LOCK:
                    return self.reply(200, app_state())
            return self.reply(404, {"error": "Нет такого метода"})
        if path in ("/", "/index.html"):
            with open(os.path.join(WEB, "index.html"), encoding="utf-8") as f:
                return self.reply(200, f.read().replace("{{v}}", VERSION), "text/html; charset=utf-8")
        name = os.path.basename(path)
        file = os.path.join(WEB, name)
        if name and os.path.isfile(file):
            ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            with open(file, "rb") as f:
                return self.reply(200, f.read(), ctype, "public, max-age=31536000, immutable")
        self.reply(404, {"error": "not found"})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        user = self.user()
        if not user:
            return self.reply(401, {"error": "Открой приложение из Telegram"})
        n = content_length(self.headers)
        if n > (16_000_000 if path == "/api/support" else 64 * 1024):
            return self.reply(413, {"error": "Слишком большой запрос"})
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self.reply(400, {"error": "Плохой запрос"})
        if path == "/api/support":
            if not SUP:
                return self.reply(503, {"error": "Поддержка ещё запускается — попробуй через минуту"})
            try:
                return self.reply(200, SUP.web(user["id"], body))
            except PermissionError as e:
                return self.reply(403, {"error": str(e)})
            except ValueError as e:
                return self.reply(400, {"error": str(e)})
        try:
            with LOCK:
                res = api_post(path, body)
            refresh_live(force=True)
            self.reply(200, res)
        except (KeyError, ValueError) as e:
            self.reply(400, {"error": str(e).strip("'")})
        except Exception as e:
            traceback.print_exc()
            self.reply(500, {"error": str(e)[:200]})


class Ingest(BaseHTTPRequestHandler):
    """Сводки от Главного (только 127.0.0.1, наружу этот порт не открыт)."""
    server_version = "PulseIngest/1"
    timeout = 30

    def log_message(self, fmt, *args):
        pass

    def do_POST(self):
        try:
            n = content_length(self.headers)
            if n > 400_000:
                raise ValueError("слишком большая сводка")
            body = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("ждал объект")
            events = ingest(body)
            code, res = 200, {"ok": True}
        except ValueError as e:
            events, code, res = [], 400, {"error": str(e)}
        except Exception as e:
            traceback.print_exc()
            events, code, res = [], 500, {"error": str(e)[:200]}
        data = json.dumps(res, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        if events:
            threading.Thread(target=lambda: (notify(events), refresh_live(force=True)), daemon=True).start()


def serve(handler, port):
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    httpd.daemon_threads = True
    httpd.serve_forever()


# ---------- https-адрес (Caddy планера: pulse.<ip>.sslip.io → PULSE_PORT) ----------

def set_menu():
    if not public_url:
        return
    try:
        tg("setChatMenuButton", menu_button={"type": "web_app", "text": "Пульс", "web_app": {"url": public_url + "/"}})
    except Exception as e:
        print("menu:", e)


def healthy(url):
    try:
        with urllib.request.urlopen(url + "/health", timeout=20) as r:
            return r.read() == b"ok"
    except Exception:
        return False


def server_ip():
    for url in ("https://api.ipify.org", "https://ipv4.icanhazip.com", "https://ifconfig.me/ip"):
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                ip = r.read().decode().strip()
            if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", ip):
                return ip
        except Exception:
            pass
    return None


def https():
    global public_url, https_note
    ip = None
    while not ip:
        ip = server_ip()
        if not ip:
            https_note = "не узнал IP сервера"
            time.sleep(60)
    url = "https://pulse." + ip.replace(".", "-") + ".sslip.io"
    while True:
        if healthy(url):
            if public_url != url:
                public_url = url
                set_menu()
            https_note = "работает"
            time.sleep(600)
            continue
        https_note = f"{url} пока не открывается (нужен маршрут в Caddy планера)"
        time.sleep(60)


def main():
    global offset, st, SUP
    if not TOKEN:
        sys.exit("Нет PULSE_TOKEN (токен от @BotFather). Впиши его в /etc/pulse-bot.env")
    st = load()
    threading.Thread(target=serve, args=(Web, PORT), daemon=True).start()
    threading.Thread(target=serve, args=(Ingest, INGEST_PORT), daemon=True).start()
    threading.Thread(target=watcher, daemon=True).start()
    setup_bot()
    if public_url:
        set_menu()
    else:
        threading.Thread(target=https, daemon=True).start()
    SUP = sup = support.Support(TOKEN, "Пульс", folders=["pulse"], pm2="pulse-bot", allowed=lambda u: u == owner_id(),
                                tests=["python3 pulse/test_pulse.py"])
    with LOCK:
        ensure_kb(owner_id())  # меню-квадратик — один раз на версию, сам при запуске
    print("Пульс запущен")
    while True:
        try:
            upd = tg("getUpdates", offset=offset, timeout=50, allowed_updates=["message", "callback_query"])
        except Exception as e:
            print("getUpdates:", e)
            time.sleep(5)
            continue
        for u in upd.get("result", []):
            offset = u["update_id"] + 1
            if sup.intercept(u):
                continue
            try:
                with LOCK:
                    if u.get("callback_query"):
                        on_callback(u["callback_query"])
                    elif u.get("message"):
                        on_message(u["message"])
            except Exception as e:
                print("update:", e)
                traceback.print_exc()


if __name__ == "__main__":
    main()
