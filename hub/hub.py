#!/usr/bin/env python3
"""Telegram-бот «Главный»: следит за остальными ботами на сервере, и он же — личный Секретарь (один бот на двоих).

Один бот: Telegram опрашивает только Главный. Свои команды, кнопки и ожидания ввода (токен, ТЗ) он обрабатывает сам,
а всё остальное — свободный текст, голос (сначала расшифровывает), фото, видео, /login и прочие команды Секретаря,
кнопки с префиксом «s:» — передаёт процессу Секретаря (tgbot/agent.py, pm2 tg-agent) через его сокет
tgbot/tmp/agent.sock. Секретарь отвечает тем же токеном. Не запущен — Главный говорит об этом и поднимает его сам.

- список ботов с состоянием (pm2), перезапуск и последние строки логов одной кнопкой;
- сам пишет, если бот упал или постоянно перезапускается, и когда он снова заработал;
- «Обновить всех»: свежий код из main и перезапуск;
- «Поставить бота»: тренер, планер, КПП — по токену от @BotFather;
- сервер: память, диск, нагрузка; предупреждает, если кончается место или подходит срок оплаты (/paid ДД.ММ);
- вход для Claude (/claude): Claude из облачной сессии по https с ключом смотрит состояние, логи,
  перезапускает и выполняет команды на сервере (hub/remote.py). Включает и выключает владелец,
  о каждой команде Главный пишет ему в чат.

Переменные окружения (на сервере — /etc/hub-bot.env):
  HUB_TOKEN    токен от @BotFather
  HUB_ALLOWED  Telegram ID владельца; если пусто — владелец тот, кто первым нажал /start
  HUB_PORT     порт входа для Claude на 127.0.0.1 (по умолчанию 8790; снаружи — Caddy планера)
  HUB_REMOTE   1 — вход для Claude включён сразу, ключ = HMAC(токен) (см. token_key)
  HUB_URL      https-адрес Главного для мини-приложения (необязательно; иначе https://hub.<ip>.sslip.io)

Только стандартная библиотека Python.
"""
import datetime
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "common"))
import digest  # noqa: E402
import maker  # noqa: E402
import media  # noqa: E402

maker.ui = sys.modules[__name__]

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DATA = os.path.join(HERE, "data")
STATE = os.path.join(DATA, "state.json")
TOKEN = os.environ.get("HUB_TOKEN", "")
API = f"https://api.telegram.org/bot{TOKEN}/"
ALLOWED = {int(x) for x in re.findall(r"\d+", os.environ.get("HUB_ALLOWED", ""))}
SELF = "hub-bot"
BOT_TOKEN = re.compile(r"\d{6,12}:[A-Za-z0-9_-]{30,}")
PORT = int(os.environ.get("HUB_PORT") or 8790)
SECRETARY = "tg-agent"  # Секретарь — часть Главного: свой процесс, но без своего бота
AGENT_SOCK = os.path.join(REPO, "tgbot", "tmp", "agent.sock")
AGENT_PORT = int(os.environ.get("TG_AGENT_PORT") or 8794)
SEC_PREFIX = "s:"  # данные кнопок Секретаря

APPS = {  # имя в pm2 -> подпись
    "horo-bot": "📚 HDP",
    "coach-bot": "⚡ Тренер «Форма»",
    "planner-bot": "🗓 Планер «Ритм»",
    "kpp-bot": "🚧 КПП",
    "avito-bot": "🛍 Авито",
    SECRETARY: "🤖 Секретарь",
    "eye-bot": "👁 Глаз",
    "studio-bot": "🏗 Студия",
    SELF: "👑 Главный",
}
INSTALL = {  # что умеем ставить по одному токену: папка, переменная с токеном, подпись, что дальше
    "coach": ("COACH_TOKEN", "⚡ Тренер WHOOP", "Открой @{name}, нажми /start, потом /connect — подключим WHOOP."),
    "planner": ("PLANNER_TOKEN", "🗓 Планер", "Открой @{name} и нажми /start — через минуту появится кнопка «Планер»."),
    "kpp": ("KPP_TOKEN", "🚧 КПП", "Открой @{name}, нажми /start и войди через Яндекс."),
    "avito": ("AVITO_TOKEN", "🛍 Авито", "Открой @{name}, нажми /start и пришли фото вещи."),
    "eye": ("EYE_TOKEN", "👁 Глаз (очки)", "Открой @{name} и нажми /start — через минуту появится кнопка «Глаз»: "
            "там «🎙 Говорить» и «📷 Фото»."),
    "studio": ("STUDIO_TOKEN", "🏗 Студия", "Открой @{name}, нажми /start и напиши, какой бот нужен — "
                                         "Студия уточнит детали, покажет план и сделает бота сама."),
}
BOTFATHER = {  # имя и желаемый username, если бота создаёт Секретарь (без «bot» на конце)
    "coach": ("Форма", "forma_coach"),
    "planner": ("Ритм", "ritm_planner"),
    "kpp": ("КПП пропуск", "kpp_pass"),
    "avito": ("Авито помощник", "avito_helper"),
    "eye": ("Глаз", "glaz_eye"),
    "studio": ("Студия ботов", "bot_studio"),
}
TZ = datetime.timezone(datetime.timedelta(hours=3))  # Москва

lock = threading.RLock()
offset = 0


def esc(x):
    return html.escape(str(x), quote=False)


# ---------- состояние ----------

def load():
    try:
        with open(STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(st):
    os.makedirs(DATA, exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, ensure_ascii=False)
    os.replace(tmp, STATE)


_GET = object()


def setting(key, value=_GET):
    """setting(k) — прочитать, setting(k, v) — записать, setting(k, None) — стереть."""
    with lock:
        st = load()
        if value is _GET:
            return st.get(key)
        if value is None:
            st.pop(key, None)
        else:
            st[key] = value
        save(st)
        return value


def owner():
    return int(setting("owner") or 0) or (min(ALLOWED) if ALLOWED else 0)


def allowed(uid):
    return uid in ALLOWED if ALLOWED else uid == owner()


# ---------- Telegram ----------

plain_buttons = False  # Telegram не принял цвет кнопок (style) — дальше шлём без цвета


def no_style(markup):
    if not isinstance(markup, dict) or "inline_keyboard" not in markup:
        return markup
    return {**markup, "inline_keyboard": [[{k: v for k, v in btn.items() if k != "style"} for btn in row]
                                          for row in markup["inline_keyboard"]]}


def _tg(method, params):
    req = urllib.request.Request(API + method, data=json.dumps(params).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=70) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        if "message is not modified" in detail:
            return {"ok": True}
        print(method, e.code, detail[:300])
        e.detail = detail
        raise


def tg(method, **params):
    """Bot API. Цветные кнопки (style) — новинка Telegram: если сервер ответит 400 из-за кнопок,
    повторяем те же кнопки без цвета и дальше цвет не шлём (как tgbot/botapi.py _with_markup)."""
    global plain_buttons
    mk = params.get("reply_markup")
    if plain_buttons and mk:
        params["reply_markup"] = mk = no_style(mk)
    try:
        return _tg(method, params)
    except urllib.error.HTTPError as e:
        if e.code != 400 or not mk or no_style(mk) == mk or \
                not re.search(r"button|markup|keyboard|style", getattr(e, "detail", ""), re.I):
            raise
        plain_buttons = True
        return _tg(method, {**params, "reply_markup": no_style(mk)})


def short(label):
    """«📚 HDP» → «HDP»: в строках и на кнопках — без второго эмодзи."""
    return re.sub(r"^[^\w«\"]+", "", str(label)).strip() or str(label)


def clip(text, n=24):
    """Текст кнопки не длиннее ~24 символов — иначе Telegram обрежет его посередине."""
    text = str(text)
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def card(title, lines=(), foot="", expand=False):
    """Сообщение в одном стиле: заголовок (HTML, жирный, один эмодзи), содержимое — цитатой, подсказка — ниже.
    Колонки пробелами не выравниваем: шрифт в Telegram пропорциональный."""
    body = "\n".join(x for x in lines if x is not None)
    text = title
    if body:
        text += f"\n<blockquote{' expandable' if expand else ''}>{body}</blockquote>"
    if foot:
        text += "\n" + foot
    return text


def send(chat, text, buttons=None):
    extra = {"reply_markup": {"inline_keyboard": buttons}} if buttons else {}
    return tg("sendMessage", chat_id=chat, text=text, parse_mode="HTML",
              link_preview_options={"is_disabled": True}, **extra)


def edit(chat, mid, text, buttons=None):
    try:
        return tg("editMessageText", chat_id=chat, message_id=mid, text=text, parse_mode="HTML",
                  link_preview_options={"is_disabled": True},
                  reply_markup={"inline_keyboard": buttons or []})
    except Exception:
        return send(chat, text, buttons)


def show(chat, mid, text, buttons):
    return edit(chat, mid, text, buttons) if mid else send(chat, text, buttons)


def b(text, data, style=None):
    """Кнопка; style: primary (синяя) | success (зелёная) | danger (красная)."""
    btn = {"text": text, "callback_data": data}
    if style:
        btn["style"] = style
    return btn


def pairs(btns, n=2):
    """Кнопки по n в ряд."""
    return [btns[i:i + n] for i in range(0, len(btns), n)]


# ---------- pm2 и сервер ----------

def sh(*cmd, timeout=60, env=None):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return 127, f"нет программы {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "не уложилось по времени"


def apps():
    """[{name, label, status, uptime (сек), restarts, mem (МБ), cpu}] из pm2 jlist."""
    code, out = sh("pm2", "jlist", timeout=30)
    if code != 0:
        return None
    try:
        rows = json.loads(out[out.find("["):])
    except ValueError:
        return None
    res = []
    for r in rows:
        env = r.get("pm2_env") or {}
        mon = r.get("monit") or {}
        up = env.get("pm_uptime")
        res.append({
            "name": r.get("name"), "label": label(r.get("name")),
            "status": env.get("status") or "?", "restarts": env.get("restart_time") or 0,
            "uptime": max(0, time.time() - up / 1000) if up and env.get("status") == "online" else 0,
            "mem": round((mon.get("memory") or 0) / 1048576), "cpu": mon.get("cpu") or 0,
        })
    order = list(APPS)
    return sorted(res, key=lambda a: order.index(a["name"]) if a["name"] in order else 99)


def label(name):
    return APPS.get(name) or maker.label(name) or "🔹 " + str(name)


def ago(sec):
    sec = int(sec)
    if sec < 3600:
        return f"{max(1, sec // 60)} мин"
    if sec < 86400:
        return f"{sec // 3600} ч"
    return f"{sec // 86400} дн"


def server():
    info = {}
    try:
        with open("/proc/meminfo") as f:
            m = {k: int(v.split()[0]) for k, v in (line.split(":", 1) for line in f if ":" in line)}
        info["mem_total"] = round(m["MemTotal"] / 1024)
        info["mem_free"] = round(m.get("MemAvailable", m.get("MemFree", 0)) / 1024)
    except (OSError, KeyError, ValueError):
        pass
    try:
        du = shutil.disk_usage("/")
        info["disk_total"] = round(du.total / 2**30, 1)
        info["disk_free"] = round(du.free / 2**30, 1)
    except OSError:
        pass
    try:
        with open("/proc/loadavg") as f:
            info["load"] = f.read().split()[0]
        with open("/proc/uptime") as f:
            info["uptime"] = float(f.read().split()[0])
    except (OSError, ValueError, IndexError):
        pass
    return info


# ---------- Секретарь (часть Главного) ----------

class SecretaryDown(Exception):
    pass


def secretary(req, timeout=5):
    """Запрос в сокет Секретаря (тот же, что у tgbot/tg.py): строка JSON туда, строка JSON обратно."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(AGENT_SOCK)
        s.sendall(json.dumps(req, ensure_ascii=False).encode() + b"\n")
        data = b""
        while not data.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
        return json.loads(data)
    except (OSError, ValueError) as e:
        raise SecretaryDown(str(e)) from None
    finally:
        s.close()


def secretary_status():
    """{"account": {"name", "username"} | None, "busy", "jobs", ...} или None, если Секретарь не отвечает."""
    try:
        r = secretary({"cmd": "_status"}, timeout=3)
        return r.get("out") if r.get("ok") else None
    except SecretaryDown:
        return None


def to_secretary(update, chat):
    """Обновление Telegram — Секретарю. Не отвечает — объясняем и поднимаем его сами."""
    try:
        secretary({"cmd": "_hub", "update": update, "owner": owner()})
        return True
    except SecretaryDown as e:
        print("secretary:", e)
    secretary_down(chat)
    return False


def secretary_down(chat):
    send(chat, card("⚠️ <b>Секретарь не отвечает</b>", ["Поднимаю его сам — через минуту напиши ещё раз."]),
         [[b("🔄 Перезапустить", "srst", "primary")]])
    threading.Thread(target=revive, daemon=True).start()


revive_lock = threading.Lock()
revived_at = 0.0


def revive(chat=None, force=False):
    """Поднять Секретаря: перезапуск в pm2, а если его там нет — поставить (tgbot/install.sh, режим «через Главного»).
    Не чаще раза в 2 минуты (кроме кнопки). chat — куда сказать, чем кончилось."""
    global revived_at
    if not revive_lock.acquire(blocking=False):
        return False, "уже поднимаю"
    try:
        if not force and time.time() - revived_at < 120:
            return False, "недавно поднимал"
        revived_at = time.time()
        if SECRETARY in {a["name"] for a in apps() or []}:
            ok, text = restart_app(SECRETARY, wait=6)
        else:
            ok, text = install_secretary()
        if chat:
            send(chat, card(f"{'✅' if ok else '⚠️'} <b>{'Секретарь работает' if ok else 'Секретарь не поднялся'}</b>",
                            [] if ok else [esc(text)]), [[b("‹ Главная", "home")]])
        return ok, text
    finally:
        revive_lock.release()


def install_secretary():
    """Секретарь внутри Главного: tgbot/install.sh без токена (он берёт токен Главного)."""
    env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin") + ":/usr/local/bin", "HOME": "/root",
           "LANG": "C.UTF-8", "DEBIAN_FRONTEND": "noninteractive", "TG_MODE": "hub"}
    code, out = sh("bash", os.path.join(REPO, "tgbot", "install.sh"), timeout=1800, env=env)
    return code == 0, ("Секретарь поставлен" if code == 0 else out.strip()[-600:])


def check_secretary():
    """После запуска: Секретаря нет в pm2 — ставим сам (не чаще раза в 6 часов, если не выходит)."""
    time.sleep(60)
    rows = apps()
    if rows is None or not owner() or SECRETARY in {a["name"] for a in rows}:
        return
    if time.time() - (setting("sec_try") or 0) < 6 * 3600:
        return
    setting("sec_try", time.time())
    send(owner(), card("🤖 <b>Ставлю Секретаря</b>", ["Он теперь часть Главного: отвечает на вопросы, голос, фото "
                                                     "и ведёт твой Telegram. Это 3–7 минут."]))
    revive(owner(), force=True)


# ---------- экраны ----------

def home(chat, mid=None):
    """Главный экран (/start): коротко, как дела у ботов и у Секретаря, и главные кнопки."""
    rows = apps()
    sec = secretary_status()
    lines = []
    if rows is None:
        lines.append("⚠️ pm2 не отвечает")
    else:
        down = [a for a in rows if a["status"] != "online"]
        if down:
            lines.append("🔴 Не работает: " + ", ".join(esc(short(a["label"])) for a in down))
            lines.append(f"🟢 Работают {len(rows) - len(down)} из {len(rows)}")
        else:
            lines.append(f"🟢 Все боты работают · {len(rows)}")
    acc = (sec or {}).get("account")
    if sec is None:
        lines.append("⚪️ Секретарь не отвечает")
    elif acc:
        lines.append("👤 " + esc(acc.get("name") or "Аккаунт") + (f" · @{esc(acc['username'])}" if acc.get("username") else ""))
    else:
        lines.append("👤 Аккаунт не подключён")
    if sec and sec.get("busy"):
        lines.append("⏳ Секретарь занят поручением")
    btns = []
    web = app_button()
    if web:
        btns.append([web])
    btns += [[b("🤖 Боты", "bots", "danger" if rows and any(a["status"] != "online" for a in rows) else None),
              b("👤 Аккаунт", SEC_PREFIX + "m:home") if sec is not None else b("🔄 Секретарь", "srst", "primary")],
             [b("🛠 Сделать бота", "mk"), b("⬆️ Обновить всех", "upd")]]
    return show(chat, mid, card("👑 <b>Главный</b>", lines,
                                "<i>💬 Пиши или говори что угодно — отвечу. Фото и видео тоже.</i>"), btns)


def bots_screen(chat, mid=None):
    rows = apps()
    if rows is None:
        return show(chat, mid, card("⚠️ <b>pm2 не отвечает</b>", ["Попробуй через минуту."]),
                    [[b("🔄 Ещё раз", "bots"), b("‹ Главная", "home")]])
    up = sum(1 for a in rows if a["status"] == "online")
    lines = []
    for a in rows:
        ok = a["status"] == "online"
        lines.append(f"{'🟢' if ok else '🔴'} {esc(short(a['label']))} — "
                     + (f"{ago(a['uptime'])} · {a['mem']} МБ" if ok else f"<b>{esc(a['status'])}</b>"))
    btns = []
    web = app_button()
    if web:
        btns.append([web])
    btns += pairs([b(clip(a["label"]), f"app|{a['name']}", None if a["status"] == "online" else "danger")
                   for a in rows if a["name"] != SELF])
    btns += [[b("⬆️ Обновить всех", "upd"), b("✏️ Доработать", "fix")],
             [b("📂 Все боты", "all"), b("🖥 Сервер", "srv")],
             [b("‹ Главная", "home"), b("⋯ Ещё", "more")]]
    return show(chat, mid, card(f"🤖 <b>Боты</b> · работает {up} из {len(rows)}", lines), btns)


def more(chat, mid):
    """Редкие действия — отдельным экраном, чтобы на главном было меньше кнопок."""
    return show(chat, mid, card("⋯ <b>Ещё</b>", ["• ➕ Поставить — готовый бот по токену",
                                                 "• 🤖 Claude — вход для Claude из облака",
                                                 "• ⏰ Напоминания и вечерняя отбивка"]),
                [[b("➕ Поставить", "add"), b("🤖 Claude", "cl")],
                 [b("⏰ Напоминания", "rmd|list"), b("🔄 Обновить", "bots")],
                 [b("‹ Назад", "bots")]])


def app_card(chat, mid, name):
    a = next((x for x in apps() or [] if x["name"] == name), None)
    if not a:
        return show(chat, mid, card("🤷 <b>Нет такого бота</b>", [f"{esc(name)} нет в pm2."]), [[b("‹ Назад", "bots")]])
    ok = a["status"] == "online"
    lines = ["🟢 Работает " + ago(a["uptime"]) if ok else "🔴 Не работает: " + esc(a["status"]),
             f"• Память {a['mem']} МБ · CPU {a['cpu']}%",
             f"• Перезапусков: {a['restarts']}"]
    return show(chat, mid, card(f"<b>{esc(a['label'])}</b>", lines),
                [[b("🔄 Перезапустить", f"rs|{name}", "primary"), b("📜 Логи", f"lg|{name}")],
                 [b("‹ Назад", "bots")]])


def log_text(name, lines=30, limit=3500):
    """Последние строки логов бота без пустых строк (для чата и приложения)."""
    code, out = sh("pm2", "logs", name, "--nostream", "--lines", str(lines), "--raw", timeout=30)
    return "\n".join(line for line in out.splitlines() if line.strip())[-limit:] or "пусто"


def logs(chat, name):
    send(chat, f"📜 <b>Логи · {esc(short(label(name)))}</b>\n<pre>{esc(log_text(name))}</pre>",
         [[b("🔄 Перезапустить", f"rs|{name}"), b("‹ К ботам", "bots")]])


def restart_app(name, wait=4):
    """Перезапуск в pm2 и проверка, что поднялся: (ok, текст для человека)."""
    code, out = sh("pm2", "restart", name, timeout=60)
    time.sleep(wait)
    a = next((x for x in apps() or [] if x["name"] == name), {})
    ok = code == 0 and a.get("status") == "online"
    return ok, (f"{short(label(name))} перезапущен" if ok else
                f"{short(label(name))} не поднялся: {out.strip()[-300:] or a.get('status')}")


def restart(chat, name):
    if name == SELF:
        sh("pm2", "restart", name, timeout=60)
        return
    ok, text = restart_app(name)
    head, _, why = text.partition(": ")
    send(chat, card(f"{'✅' if ok else '⚠️'} <b>{esc(head)}</b>", [esc(why)] if why else []),
         [[b("📜 Логи", f"lg|{name}"), b("‹ К ботам", "bots")]])


def paid_left(paid, today=None):
    return (datetime.date.fromisoformat(paid) - (today or datetime.datetime.now(TZ).date())).days


def usage(s, cpus=None):
    """Сколько занято, в процентах: {"mem": 27, "disk": 17, "cpu": 23}. Процессор — средняя нагрузка
    за минуту на все ядра (load 1.0 на одном ядре = 100%)."""
    out = {}
    if s.get("mem_total"):
        out["mem"] = round((s["mem_total"] - s["mem_free"]) / s["mem_total"] * 100)
    if s.get("disk_total"):
        out["disk"] = round((s["disk_total"] - s["disk_free"]) / s["disk_total"] * 100)
    try:
        out["cpu"] = round(float(s["load"]) / (cpus or os.cpu_count() or 1) * 100)
    except (KeyError, TypeError, ValueError):
        pass
    return out


def level(p):
    return "🟢" if p < 70 else "🟡" if p < 90 else "🔴"


def server_card(chat, mid):
    s = server()
    u = usage(s)
    lines = []
    if "mem" in u:
        lines.append(f"{level(u['mem'])} Память: занято {u['mem']}% "
                     f"({s['mem_total'] - s['mem_free']} из {s['mem_total']} МБ)")
    if "disk" in u:
        lines.append(f"{level(u['disk'])} Диск: занято {u['disk']}% "
                     f"({round(s['disk_total'] - s['disk_free'], 1)} из {s['disk_total']} ГБ)")
    if "cpu" in u:
        lines.append(f"{level(u['cpu'])} Процессор: занят {u['cpu']}%")
    if s.get("uptime"):
        lines.append(f"• Работает без перезагрузки {ago(s['uptime'])}")
    paid = setting("paid")
    if paid:
        left = paid_left(paid)
        lines.append(f"• Оплачен до {fmt_date(paid)} — " + (f"осталось {left} дн" if left > 0 else
                                                            "срок сегодня!" if left == 0 else "срок прошёл!"))
    worst = max(u.values(), default=0)
    title = ("🖥 <b>Сервер: всё в порядке</b>" if worst < 70 else
             "🖥 <b>Сервер: тесновато</b>" if worst < 90 else "🖥 <b>Сервер: перегружен</b>")
    foot = "<i>🟢 до 70% — нормально · 🟡 70–90% — тесно · 🔴 больше 90% — боты могут падать</i>"
    if not paid:
        foot += "\n<i>Срок оплаты не указан — пришли /paid ДД.ММ, напомню заранее.</i>"
    return show(chat, mid, card(title, lines, foot), [[b("‹ Назад", "home")]])


def fmt_date(iso):
    d = datetime.date.fromisoformat(iso)
    return f"{d.day:02d}.{d.month:02d}.{d.year}"


# ---------- обновление и установка ----------

def update_all(chat, reset=False):
    def git(*a):
        return sh("git", "-C", REPO, *a, timeout=120)
    progress("Скачиваю свежий код из main…", kind="update")
    _, old = git("rev-parse", "HEAD")
    code, out = git("fetch", "origin", "main")
    if code == 0:
        code, out = sync_main(git, reset)
    if code != 0:
        progress("Не скачал код — подробности в чате", done=True, ok=False)
        return send(chat, f"⚠️ <b>Не скачал код</b>\n<pre>{esc(out[-800:])}</pre>",
                    [[b("🧹 Сбросить правки", "upd|reset", "danger")]] if "rebase" in out else None)
    _, new = git("rev-parse", "HEAD")
    if old.strip() == new.strip():
        progress("Уже последняя версия", done=True)
        return send(chat, card("✅ <b>Уже последняя версия</b>", ["Перезапускать никого не нужно."]),
                    [[b("‹ К ботам", "bots")]])
    _, changed = git("diff", "--name-only", old.strip(), new.strip())
    if "package.json" in changed or "package-lock.json" in changed:
        progress("Ставлю пакеты npm…")
        sh("npm", "install", "--no-audit", "--no-fund", "--prefix", REPO, timeout=600)
    if not os.path.exists(os.path.join(REPO, "common", ".venv", "bin", "python")):
        progress("Ставлю распознавание голосовых — 2–5 минут…")
        send(chat, card("🎙 <b>Ставлю распознавание голосовых</b>", ["Для всех ботов, один раз — 2–5 минут…"]))
        code, out = sh("bash", os.path.join(REPO, "common", "setup.sh"), timeout=1800)
        if code != 0:
            send(chat, f"⚠️ <b>Голосовые не поставились</b>\n<pre>{esc(out[-600:])}</pre>")
    names = [a["name"] for a in apps() or [] if a["name"] in APPS and a["name"] != SELF]
    res = []
    for n in names:
        progress(f"Перезапускаю {short(APPS[n])}…")
        code, _ = sh("pm2", "restart", n, timeout=60)
        res.append(("✅ " if code == 0 else "⚠️ ") + short(APPS[n]))
    setting("updated_chat", chat)
    progress("Перезапускаюсь сам…")
    send(chat, card("⬆️ <b>Обновил и перезапустил</b>", [esc(x) for x in res], "<i>Перезапускаюсь сам…</i>"))
    tg("getUpdates", offset=offset, timeout=0)
    os._exit(0)  # pm2 поднимет заново


def sync_main(git, reset=False):
    """Код = свежий main. Локальные коммиты «✏️ Доработать» переносятся поверх него (rebase);
    конфликт — откат rebase и ошибка (reset=True — выбросить локальные правки)."""
    _, ahead = git("rev-list", "origin/main..HEAD")
    if reset or not ahead.strip():
        return git("checkout", "-B", "main", "origin/main")
    git("add", "-A")
    git("-c", "user.name=Главный", "-c", "user.email=hub@localhost", "commit", "-q", "-m", "Главный: правки на сервере")
    code, out = git("-c", "user.name=Главный", "-c", "user.email=hub@localhost", "rebase", "origin/main")
    if code != 0:
        git("rebase", "--abort")
        return code, "Правки, сделанные здесь через «✏️ Доработать», конфликтуют с обновлением (rebase).\n" + out
    return 0, out


def ask_token(chat, mid, kind):
    """Просим токен; если Секретарь подключён к аккаунту — можно не ходить в @BotFather самому."""
    setting("wait", kind)
    rows = [[b("🤖 Создать бота сам", f"bf|{kind}", "primary")]] if kind in BOTFATHER and maker.secretary_ready() else []
    lines = ["Пришли токен бота от @BotFather — поставлю сам."]
    if rows:
        lines.append("Или нажми «🤖 Создать бота сам» — создам его в @BotFather через Секретаря.")
    show(chat, mid, card(f"➕ <b>{esc(short(INSTALL[kind][1]))}</b>", lines), rows + [[b("Отмена", "home")]])


def botfather_install(chat, kind):
    name, want = BOTFATHER.get(kind, (INSTALL[kind][1], kind))
    send(chat, card(f"🤖 <b>Создаю бота «{esc(name)}»</b>", ["В @BotFather через Секретаря…"]))
    token, why = maker.botfather(name, want)
    if not token:
        setting("wait", kind)
        return send(chat, card("😕 <b>Не создал</b>", [esc(why)], "Создай сам в @BotFather (/newbot) и пришли сюда токен."),
                    [[{"text": "Открыть @BotFather", "url": "https://t.me/BotFather"}], [b("Отмена", "home")]])
    install(chat, kind, token)


def install(chat, kind, token, msg_id=None):
    var, label, after = INSTALL[kind]
    if msg_id:
        try:
            tg("deleteMessage", chat_id=chat, message_id=msg_id)  # токен не должен висеть в чате
        except Exception:
            pass
    if token == TOKEN:
        return send(chat, card("🤔 <b>Это токен Главного</b>", ["Нужен токен нового бота от @BotFather."]))
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getMe", timeout=30) as r:
            name = json.load(r)["result"].get("username") or kind
    except Exception:
        return send(chat, card("❌ <b>Токен не подошёл</b>", ["Скопируй его из @BotFather целиком и пришли ещё раз."]))
    send(chat, card(f"⏳ <b>Ставлю {esc(short(label))}</b>", [f"@{esc(name)} · это 1–3 минуты…"]))
    env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin") + ":/usr/local/bin", "HOME": "/root",
           "LANG": "C.UTF-8", "DEBIAN_FRONTEND": "noninteractive", var: token}
    code, out = sh("bash", os.path.join(REPO, kind, "install.sh"), timeout=1200, env=env)
    if code != 0:
        return send(chat, f"❌ <b>Не получилось</b>\n<pre>{esc(out.strip()[-1200:])}</pre>")
    maker.to_folder(name)  # сразу в папку «Bot» (если Секретарь вошёл в аккаунт)
    send(chat, card(f"✅ <b>{esc(short(label))} запущен</b>", [esc(after.format(name=name))]),
         [[{"text": "▶️ Открыть бота", "url": f"https://t.me/{name}", "style": "success"}], [b("‹ К ботам", "bots")]])


# ---------- присмотр ----------

def watch_once(now=None):
    """Одна проверка: что упало, что поднялось, что перезапускается по кругу, место, память, оплата.
    Возвращает список сообщений [(текст, кнопки)] — их отправляет watch()."""
    now = now or time.time()
    out = []
    rows = apps()
    with lock:
        st = load()
        seen = st.setdefault("seen", {})
        if rows is not None:
            for a in rows:
                n, s = a["name"], seen.setdefault(a["name"], {"bad": 0, "alerted": False, "hist": []})
                if a["status"] != "online":
                    s["bad"] += 1
                    if s["bad"] >= 2 and not s["alerted"]:
                        s["alerted"] = True
                        out.append((card(f"🔴 <b>{esc(short(a['label']))} не работает</b>",
                                         [f"Статус в pm2: {esc(a['status'])}"]),
                                    [[b("🔄 Перезапустить", f"rs|{n}", "primary"), b("📜 Логи", f"lg|{n}")]]))
                else:
                    if s["alerted"]:
                        out.append((f"🟢 <b>{esc(short(a['label']))} снова работает</b>", None))
                    s["bad"], s["alerted"] = 0, False
                # перезапуски по кругу: +3 за 10 минут
                s["hist"] = [h for h in s["hist"] if now - h[0] < 600] + [[now, a["restarts"]]]
                if s["hist"][-1][1] - s["hist"][0][1] >= 3 and now - s.get("loop_at", 0) > 3600:
                    s["loop_at"] = now
                    out.append((card(f"🟠 <b>{esc(short(a['label']))} падает и перезапускается</b>",
                                     ["Посмотри логи — там причина."]),
                                [[b("📜 Логи", f"lg|{n}", "primary"), b("🔄 Перезапустить", f"rs|{n}")]]))
        srv = server()
        day = datetime.datetime.fromtimestamp(now, TZ).date().isoformat()
        if srv.get("disk_total") and srv["disk_free"] / srv["disk_total"] < 0.1 and st.get("disk_day") != day:
            st["disk_day"] = day
            out.append((card("💽 <b>На диске мало места</b>",
                             [f"Осталось {srv['disk_free']} ГБ — скоро боты не смогут писать данные."]), None))
        if srv.get("mem_total") and srv["mem_free"] / srv["mem_total"] < 0.07 and now - st.get("mem_at", 0) > 6 * 3600:
            st["mem_at"] = now
            out.append((card("🧠 <b>Мало памяти</b>", [f"Свободно всего {srv['mem_free']} из {srv['mem_total']} МБ."]),
                        None))
        paid = st.get("paid")
        hour = datetime.datetime.fromtimestamp(now, TZ).hour
        if paid and hour >= 10 and st.get("paid_day") != day:
            left = paid_left(paid, datetime.date.fromisoformat(day))
            if left in (7, 3, 1, 0) or left < 0:
                st["paid_day"] = day
                out.append((card("💳 <b>Оплата сервера</b>", [f"Оплачен до {fmt_date(paid)} — " + (
                    f"осталось {left} дн. Продли, иначе все боты выключатся." if left > 0 else
                    "срок сегодня! Продли сейчас." if left == 0 else "срок прошёл! Продли и пришли новый /paid ДД.ММ.")]),
                    None))
        out += digest.due(st, now, rows, srv, st.get("paid"))
        save(st)
    return out


def watch():
    time.sleep(30)
    while True:
        try:
            if owner():
                for text, btns in watch_once():
                    send(owner(), text, btns)
        except Exception as e:
            print("watch:", e)
            traceback.print_exc()
        time.sleep(60)


# ---------- вход для Claude ----------
# Claude в облачной сессии не может написать боту в Telegram (боты не видят сообщений ботов), поэтому
# у Главного есть https-вход: POST /api/<действие> с заголовком «Authorization: Bearer <ключ>».
# Ключ создаёт владелец кнопкой, выключить можно в любой момент; о каждой команде Главный пишет владельцу.
jobs = {}
fails = []
public_ip = None


def server_ip():
    global public_ip
    if public_ip:
        return public_ip
    for url in ("https://api.ipify.org", "https://ipv4.icanhazip.com"):
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                ip = r.read().decode().strip()
            if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", ip):
                public_ip = ip
                return ip
        except Exception:
            pass
    return None


def hub_url():
    ip = os.environ.get("HUB_IP") or server_ip()
    return f"https://hub.{ip.replace('.', '-')}.sslip.io" if ip else ""


# ---------- мини-приложение (Telegram Mini App) ----------
# Тот же https-адрес, что у входа Claude (Caddy планера → 127.0.0.1:HUB_PORT), путь /app.
# Доступ — только владельцу: подпись initData от Telegram (заголовок X-Init-Data).
WEB = os.path.join(HERE, "webapp")
WEB_FILES = ("index.html", "kit.css", "app.css", "kit.js", "app.js")
BOOT = time.time()  # приложение видит, что Главный перезапустился (после «Обновить всех»)
live = {}  # живой статус долгого действия: {kind, text, done, ok, at}
NO_APP = ("📱 <b>Приложение пока не открыть</b>\n<blockquote>Ему нужен https-адрес сервера, а его нет: Главный не узнал "
          "свой IP или не запущен планер (его Caddy даёт https).</blockquote>\n"
          "Поставь планер или впиши <code>HUB_URL=https://…</code> в /etc/hub-bot.env. Кнопки в чате работают как раньше.")


def app_url(lookup=False):
    """Адрес мини-приложения: HUB_URL или https://hub.<ip>.sslip.io, плюс /app. Без https — "".
    lookup=False — не ходить в сеть за IP (экраны чата), только то, что уже известно."""
    base = os.environ.get("HUB_URL", "").strip().rstrip("/")
    if not base and (lookup or public_ip or os.environ.get("HUB_IP")):
        base = hub_url()
    return base + "/app" if base.startswith("https://") else ""


def app_button(text="📱 Приложение"):
    url = app_url()
    return {"text": text, "web_app": {"url": url}, "style": "primary"} if url else None


def set_menu(lookup=False):
    """Кнопка «Приложение» слева от поля ввода — в чате владельца."""
    url, chat = app_url(lookup), owner()
    if not (url and chat):
        return False
    try:
        tg("setChatMenuButton", chat_id=chat, menu_button={"type": "web_app", "text": "Приложение", "web_app": {"url": url}})
        return True
    except Exception as e:
        print("menu:", e)
        return False


def open_app(chat):
    btn = app_button("📱 Открыть приложение")
    if not btn:
        return send(chat, NO_APP)
    send(chat, card("📱 <b>Приложение Главного</b>", ["• Секретарь: вопросы, поручения, непрочитанное",
                                                     "• Боты и сервер: перезапуск, логи, обновить всех",
                                                     "• Дела: расписание, напоминания, память"],
                    "<i>Оно всегда под кнопкой «Приложение» слева от поля ввода.</i>"), [[btn]])


def check_init_data(init_data, token=None, max_age=86400):
    """Подпись данных Mini App (core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app):
    HMAC-SHA256 строки «k=v» по алфавиту с ключом HMAC("WebAppData", токен); auth_date не старше суток.
    Возвращает пользователя (dict) или None."""
    token = token or TOKEN
    try:
        pairs_ = dict(urllib.parse.parse_qsl(init_data or "", keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None
    got = pairs_.pop("hash", "")
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()

    def sign(d):
        check = "\n".join(f"{k}={v}" for k, v in sorted(d.items()))
        return hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    # поле signature по документации входит в строку проверки; на всякий случай принимаем и без него
    variants = [pairs_] + ([{k: v for k, v in pairs_.items() if k != "signature"}] if "signature" in pairs_ else [])
    if not got or not any(hmac.compare_digest(sign(d), got) for d in variants):
        return None
    try:
        if time.time() - int(pairs_.get("auth_date", 0)) > max_age:
            return None
        user = json.loads(pairs_.get("user", "null"))
    except ValueError:
        return None
    return user if isinstance(user, dict) and isinstance(user.get("id"), int) else None


def progress(text, done=False, ok=True, kind=None):
    """Живой статус для приложения (чат об этом пишет сам)."""
    live.update(text=text, done=done, ok=ok, at=time.time())
    if kind:
        live["kind"] = kind


def app_state():
    rows = apps()
    run = {a["name"]: a for a in rows or []}
    run_all = dict(run)
    bots = []
    for r in maker.all_bots(rows or []):
        a = run.pop(r["pm2"], None) or {}
        bots.append({"name": r["pm2"], "label": r["label"], "title": short(r["label"]), "about": r["about"],
                     "username": r["username"], "status": a.get("status"), "uptime": round(a.get("uptime") or 0),
                     "mem": a.get("mem") or 0, "cpu": a.get("cpu") or 0, "restarts": a.get("restarts") or 0,
                     "self": r["pm2"] == SELF, "part": r["pm2"] == SECRETARY})
    for a in run.values():  # что-то ещё в pm2, чего нет в каталоге
        bots.append({"name": a["name"], "label": a["label"], "title": short(a["label"]), "about": "", "username": None,
                     "status": a["status"], "uptime": round(a["uptime"]), "mem": a["mem"], "cpu": a["cpu"],
                     "restarts": a["restarts"], "self": False})
    with lock:
        st = load()
    paid = st.get("paid")
    rs = sorted(st.get("reminders") or [], key=lambda r: (r.get("date") or "", r["time"]))
    return {"pm2": rows is not None, "bots": bots, "server": {**server(), "cpus": os.cpu_count() or 1},
            "paid": {"date": paid, "text": fmt_date(paid), "left": paid_left(paid)} if paid else None,
            "reminders": [{"id": r["id"], "time": r["time"], "when": digest.describe(r), "text": r["text"]} for r in rs],
            "digest": st.get("digest_time") or digest.DEFAULT_TIME,
            "secretary": {"running": (run_all.get(SECRETARY) or {}).get("status") == "online"},
            "live": dict(live), "boot": BOOT, "now": time.time()}


def app_action(action, arg):
    """Действия из приложения — те же функции, что у кнопок чата."""
    action, arg = str(action or ""), str(arg or "").strip()
    if action in ("restart", "logs"):
        if arg not in {a["name"] for a in apps() or []}:
            raise KeyError("такого бота нет в pm2")
        if action == "logs":
            return {"ok": True, "text": log_text(arg, 120, 30000)}
        if arg == SELF:  # перезапуск самого себя оборвал бы ответ — чуть позже, в фоне
            progress("Главный перезапускается…", kind="restart")
            threading.Thread(target=lambda: (time.sleep(1), sh("pm2", "restart", SELF, timeout=60)), daemon=True).start()
            return {"ok": True, "text": "Перезапускаюсь…", "at": live["at"]}
        ok, text = restart_app(arg)
        return {"ok": ok, "text": text}
    if action == "update":
        if live.get("kind") == "update" and not live.get("done") and time.time() - live.get("at", 0) < 1800:
            raise ValueError("уже обновляю")
        progress("Скачиваю свежий код из main…", kind="update")
        threading.Thread(target=update_all, args=(owner(),), daemon=True).start()
        return {"ok": True, "text": "Обновляю…", "at": live["at"]}
    if action == "secretary":  # поднять Секретаря (перезапуск или установка) — в фоне, итог придёт в чат
        threading.Thread(target=revive, args=(owner(), True), daemon=True).start()
        return {"ok": True, "text": "Поднимаю Секретаря…"}
    if action == "reminder_delete":
        if not arg.isdigit():
            raise ValueError("нет такого напоминания")
        with lock:
            st = load()
            note = digest.callback(st, f"rmd|{arg}")[0]
            save(st)
        return {"ok": note == "🗑 Удалено", "text": "Удалено" if note == "🗑 Удалено" else "Уже удалено"}
    raise KeyError("нет такого действия")


def remote_on():
    return bool(setting("remote") and setting("key"))


def token_key(token=None):
    """Ключ входа, выведенный из токена Главного: его знает тот, кому владелец дал токен (Claude)."""
    return hmac.new((token or TOKEN).encode(), b"claude-remote-v1", hashlib.sha256).hexdigest()


def bootstrap_remote():
    """HUB_REMOTE=1 (ставит HDP-бот по просьбе владельца): вход для Claude включён с первого запуска.
    «♻️ Новый ключ» в /claude заменяет его случайным, «⛔ Выключить» — закрывает."""
    if os.environ.get("HUB_REMOTE") == "1" and not setting("key"):
        setting("key", token_key())
        setting("remote", True)
        tell("🤖 Вход для Claude включён — он сам поставит и проверит ботов. О каждой команде напишу сюда. "
             "Выключить: /claude → «⛔ Выключить».")


def check_key(header):
    key = setting("key") or ""
    got = header[7:].strip() if header.startswith("Bearer ") else ""
    return bool(key and got) and hmac.compare_digest(hashlib.sha256(got.encode()).digest(),
                                                     hashlib.sha256(key.encode()).digest())


def tell(text):
    """Сообщение владельцу о том, что делает Claude."""
    try:
        if owner():
            send(owner(), text)
    except Exception as e:
        print("tell:", e)


def run_job(cmd, timeout, why=""):
    """Команда в bash в папке репозитория; вывод копится в задаче, Claude забирает его по id."""
    jid = secrets.token_hex(4)
    job = jobs[jid] = {"id": jid, "cmd": cmd, "done": False, "code": None, "out": "", "started": time.time()}
    for k in sorted(jobs, key=lambda k: jobs[k]["started"])[:-50]:
        jobs.pop(k, None)
    tell("🤖 <b>Claude выполняет</b>" + (f" — {esc(why[:200])}" if why else "") + f"\n<pre>{esc(cmd[:600])}</pre>")

    def work():
        try:
            p = subprocess.Popen(["bash", "-lc", cmd], cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, text=True, env={**os.environ, "HOME": "/root"})
            timer = threading.Timer(timeout, p.kill)
            timer.start()
            for line in p.stdout:
                job["out"] = (job["out"] + line)[-200000:]
            p.wait()
            p.stdout.close()
            timer.cancel()
            job["code"] = p.returncode
        except Exception as e:
            job["out"] += f"\n{e}"
            job["code"] = -1
        job["done"] = True
        if job["code"] != 0:
            tell(f"🤖 Команда Claude завершилась с кодом {job['code']}")
    threading.Thread(target=work, daemon=True).start()
    return job


def remote_api(action, body):
    if action == "status":
        return {"apps": apps(), "server": server(), "paid": setting("paid")}
    if action == "logs":
        lines = max(10, min(int(body.get("lines") or 60), 1000))
        code, out = sh("pm2", "logs", str(body["name"]), "--nostream", "--lines", str(lines), "--raw", timeout=30)
        return {"code": code, "out": out[-100000:]}
    if action == "restart":
        code, out = sh("pm2", "restart", str(body["name"]), timeout=60)
        tell(f"🤖 Claude перезапустил {esc(label(body['name']))}"
             + (f": {esc(str(body['why'])[:300])}" if body.get("why") else ""))
        return {"code": code, "out": out[-5000:]}
    if action == "exec":
        cmd = str(body.get("cmd") or "").strip()
        if not cmd:
            raise ValueError("пустая команда")
        job = run_job(cmd, max(5, min(int(body.get("timeout") or 600), 3600)), str(body.get("why") or ""))
        return {"id": job["id"]}
    if action == "job":
        job = jobs.get(str(body.get("id")))
        if not job:
            raise KeyError("нет такой задачи (Главный перезапускался?)")
        start = max(0, int(body.get("from") or 0))
        return {"done": job["done"], "code": job["code"], "out": job["out"][start:], "len": len(job["out"])}
    if action == "say":
        text = str(body.get("text") or "").strip()[:3500]
        if not text:
            raise ValueError("пустое сообщение")
        tell("🤖 <b>Claude:</b> " + esc(text))
        return {"ok": True}
    if action == "update":
        tell("🤖 Claude обновляет всех ботов")
        threading.Thread(target=lambda: (time.sleep(1), update_all(owner())), daemon=True).start()
        return {"ok": True, "note": "Главный перезапустится в конце"}
    raise KeyError("нет такого действия")


class Web(BaseHTTPRequestHandler):
    server_version = "Hub/1"

    def log_message(self, fmt, *args):
        pass

    def reply(self, code, body, ctype="application/json; charset=utf-8", cache="no-store"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/health":
            return self.reply(200, "ok")
        if path in ("/app/health", "/app/agent/health"):
            return self.reply(200, b"ok", "text/plain; charset=utf-8") if path == "/app/health" else self.proxy("/health")
        if path.startswith("/app/agent/"):
            return self.proxy(path[len("/app/agent"):])
        if path in ("/app", "/app/", "/app/index.html"):
            files = [os.path.join(WEB, f) for f in WEB_FILES]
            ver = str(int(max(os.path.getmtime(f) for f in files if os.path.exists(f))))
            with open(files[0], encoding="utf-8") as f:
                return self.reply(200, f.read().replace("{{v}}", ver).encode(), "text/html; charset=utf-8")
        if path == "/app/api/state":
            return self.app_api(lambda body: app_state())
        name = path[5:] if path.startswith("/app/") else ""
        ctype = {"app.css": "text/css; charset=utf-8", "app.js": "text/javascript; charset=utf-8",
                 "kit.css": "text/css; charset=utf-8", "kit.js": "text/javascript; charset=utf-8"}.get(name)
        if ctype:
            with open(os.path.join(WEB, name), "rb") as f:
                return self.reply(200, f.read(), ctype, "public, max-age=31536000, immutable")
        self.reply(404, {"error": "not found"})

    def proxy(self, path, body=None):
        """Вкладка «Секретарь» мини-приложения: /app/agent/api/* → сервер приложения Секретаря (127.0.0.1:AGENT_PORT).
        Подпись Telegram проверяет он сам (тот же токен), здесь — только пересылка."""
        if not (path.startswith("/api/") or path == "/health"):
            return self.reply(404, {"error": "not found"})
        headers = {k: self.headers[k] for k in ("X-Init-Data", "Content-Type") if self.headers.get(k)}
        req = urllib.request.Request(f"http://127.0.0.1:{AGENT_PORT}{path}", data=body,
                                     method="POST" if body is not None else "GET", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return self.reply(r.status, r.read(), r.headers.get("Content-Type") or "application/json")
        except urllib.error.HTTPError as e:
            return self.reply(e.code, e.read(), e.headers.get("Content-Type") or "application/json")
        except (OSError, ValueError):
            return self.reply(502, {"error": "Секретарь не запущен", "down": True})

    def app_api(self, handler, body=None):
        user = check_init_data(self.headers.get("X-Init-Data") or "")
        if not user:
            return self.reply(401, {"error": "Открой приложение через Telegram"})
        if not allowed(user["id"]):
            return self.reply(403, {"error": "Это личный бот"})
        try:
            self.reply(200, handler(body or {}))
        except (KeyError, ValueError) as e:
            self.reply(400, {"error": str(e).strip("'")})
        except Exception as e:
            traceback.print_exc()
            self.reply(500, {"error": str(e)})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path.startswith("/app/agent/"):
            n = int(self.headers.get("Content-Length") or 0)
            if n > 300_000:
                return self.reply(413, {"error": "слишком большой запрос"})
            return self.proxy(path[len("/app/agent"):], self.rfile.read(n) if n else b"")
        if path == "/app/api/action":  # мини-приложение: вход по подписи Telegram
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(min(n, 100_000)) or b"{}") if n else {}
            except ValueError:
                return self.reply(400, {"error": "bad json"})
            body = body if isinstance(body, dict) else {}
            return self.app_api(lambda b_: app_action(b_.get("action"), b_.get("arg")), body)
        now = time.time()
        fails[:] = [t for t in fails if now - t < 3600]
        if len(fails) >= 20:
            return self.reply(429, {"error": "слишком много неверных ключей, вход закрыт на час"})
        if not remote_on():
            return self.reply(403, {"error": "вход для Claude выключен — включи в Главном: /claude"})
        if not check_key(self.headers.get("Authorization") or ""):
            fails.append(now)
            if len(fails) == 20:
                tell("⚠️ 20 попыток с неверным ключом за час — вход для Claude закрыт на час. "
                     "Если это не ты, нажми «♻️ Новый ключ» в /claude.")
            return self.reply(401, {"error": "неверный ключ"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(min(n, 1_000_000)) or b"{}") if n else {}
        except ValueError:
            return self.reply(400, {"error": "bad json"})
        action = self.path.rstrip("/").rsplit("/", 1)[-1]
        try:
            self.reply(200, remote_api(action, body if isinstance(body, dict) else {}))
        except (KeyError, ValueError) as e:
            self.reply(400, {"error": str(e).strip("'")})
        except Exception as e:
            traceback.print_exc()
            self.reply(500, {"error": str(e)})


def serve():
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Web)
    httpd.daemon_threads = True
    httpd.serve_forever()


def claude_card(chat, mid):
    on = remote_on()
    url = hub_url()
    lines = ["Claude из облачной сессии сам смотрит ботов и логи, перезапускает, обновляет и выполняет "
             "команды на сервере. О каждой команде я пишу сюда.", "",
             "🟢 Включён" if on else "⚪️ Выключен"]
    if url:
        lines.append(f"Адрес: <code>{esc(url)}</code>")
    if on:
        btns = [[b("🔑 Показать ключ", "cl|show"), b("♻️ Новый ключ", "cl|new")], [b("⛔ Выключить", "cl|off", "danger")]]
    else:
        btns = [[b("🔑 Включить", "cl|on", "success")]]
    btns.append([b("‹ Назад", "more")])
    return show(chat, mid, card("🤖 <b>Вход для Claude</b>", lines), btns)


def show_key(chat):
    send(chat, card("🔑 <b>Ключ для Claude</b>",
                    ["Добавь две переменные в настройки облачной среды Claude (меню среды в заголовке сессии → Edit → "
                     "переменные окружения) и начни новую сессию:", "",
                     f"<code>HUB_URL={esc(hub_url())}</code>", f"<code>HUB_KEY={esc(setting('key'))}</code>"],
                    "<i>Потом удали это сообщение. Выключить вход — «⛔ Выключить» в /claude.</i>"))


def on_claude(chat, mid, arg):
    if arg in ("on", "new"):
        if arg == "new" or not setting("key"):
            setting("key", secrets.token_urlsafe(32))
        setting("remote", True)
        claude_card(chat, mid)
        show_key(chat)
    elif arg == "off":
        setting("remote", False)
        claude_card(chat, mid)
    elif arg == "show":
        show_key(chat)
    else:
        claude_card(chat, mid)


# ---------- сообщения ----------

def parse_paid(text, today=None):
    today = today or datetime.datetime.now(TZ).date()
    m = re.search(r"(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?", text)
    if not m:
        return None
    d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
    year = (int(y) + 2000 if len(y) == 2 else int(y)) if y else today.year
    try:
        date = datetime.date(year, mo, d)
    except ValueError:
        return None
    if not y and date < today - datetime.timedelta(days=30):
        date = date.replace(year=year + 1)
    return date.isoformat()


HELP = card("👑 <b>Главный</b> — боты и секретарь в одном", [
    "• 💬 Пиши или говори что угодно — отвечу: вопросы, учёба, поиск в интернете. Фото и видео тоже разберу.",
    "• 👤 Твой Telegram: что пропустил, ответы, папки, сообщения по расписанию — /login, /jobs, /new, /stop.",
    "• 🤖 /bots — боты: упадёт — напишу сам; перезапуск, логи, /update — обновить всех.",
    "• 🛠 /make — новый бот по ТЗ, ✏️ /fix — доработать любого, 📂 /all — все со ссылками.",
    "• 📱 /app — приложение: секретарь, боты, дела (кнопка «Приложение» у поля ввода).",
    "• /paid 07.11 — срок оплаты сервера, /server — сервер, /claude — вход для Claude.",
    digest.HELP_LINES.rstrip("\n")], expand=True)

MENU_TEXTS = {"✦ Меню"}  # кнопка клавиатуры Секретаря «Меню» — главный экран


def maker_wait():
    """Главный ждёт ТЗ для «Сделать бота» / «Доработать»: фото и видео — к задаче, а не Секретарю."""
    wait = setting("wait") or ""
    return wait == "make" or wait.startswith("fix|")


def on_message(msg):
    chat = msg["chat"]["id"]
    uid = (msg.get("from") or {}).get("id")
    text = (msg.get("text") or "").strip()
    if msg["chat"].get("type") != "private":
        return
    if text.startswith("/start") and not ALLOWED and not setting("owner"):
        setting("owner", uid)
        print("Владелец:", uid)
        set_menu()
    if not allowed(uid):
        return send(chat, "🔒 Это личный бот.")
    kind = media.kind(msg)
    if kind == "voice":  # сначала текст — дальше как обычное сообщение (Главному или Секретарю)
        return threading.Thread(target=hear, args=(msg,), daemon=True).start()
    if kind in ("photo", "video"):
        if maker_wait():
            return threading.Thread(target=on_photo if kind == "photo" else on_video, args=(msg,), daemon=True).start()
        return to_secretary({"message": msg}, chat)
    cmd = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
    wait = setting("wait")
    if cmd in ("/start", "/bots", "/menu") or text in MENU_TEXTS:
        setting("wait", None)
        bots_screen(chat) if cmd == "/bots" else home(chat)
    elif cmd == "/help":
        send(chat, HELP)
    elif cmd == "/app":
        open_app(chat)
    elif cmd == "/server":
        server_card(chat, None)
    elif cmd == "/claude":
        claude_card(chat, None)
    elif cmd == "/update":
        threading.Thread(target=update_all, args=(chat,), daemon=True).start()
    elif cmd == "/all":
        threading.Thread(target=maker.show_all, args=(chat,), daemon=True).start()
    elif cmd == "/make":
        maker.ask_make(chat)
    elif cmd == "/fix":
        maker.ask_fix(chat)
    elif cmd in ("/remind", "/reminders", "/digest"):
        with lock:
            st = load()
            res = digest.command(st, text, time.time())
            save(st)
        if res and cmd == "/reminders":
            res = (res[0], (res[1] or []) + [[b("‹ Назад", "more")]])
        send(chat, *res) if res else send(chat, HELP)
    elif cmd == "/cancel" and wait:
        setting("wait", None)
        send(chat, "👌 Отменил")
    elif not cmd and wait and maker.on_text(chat, wait, text, msg):
        pass
    elif cmd == "/paid":
        iso = parse_paid(text)
        if not iso:
            return send(chat, card("💳 <b>Срок оплаты</b>", ["Пришли так: <code>/paid 07.11</code>"]))
        setting("paid", iso)
        with lock:
            st = load()
            st.pop("paid_day", None)
            save(st)
        send(chat, card("💳 <b>Запомнил</b>", [f"Сервер оплачен до {fmt_date(iso)}. Напомню за 7, 3 и 1 день."]))
    elif cmd in {"/" + k for k in INSTALL} or (wait and BOT_TOKEN.search(text)):
        kind = cmd[1:] if cmd else wait
        m = BOT_TOKEN.search(text)
        if not m:
            return ask_token(chat, None, kind)
        setting("wait", None)
        threading.Thread(target=install, args=(chat, kind, m.group(0), msg.get("message_id")), daemon=True).start()
    elif BOT_TOKEN.search(text):
        try:
            tg("deleteMessage", chat_id=chat, message_id=msg["message_id"])
        except Exception:
            pass
        setting("token_hint", BOT_TOKEN.search(text).group(0))
        send(chat, card("🔑 <b>Вижу токен</b>", ["Сообщение с ним удалил. Какого бота поставить?"]),
             pairs([b(clip(INSTALL[k][1]), f"addt|{k}") for k in INSTALL]))
    else:  # свободный текст, команды Секретаря, номер телефона для входа — Секретарю
        if cmd and wait:  # /login и т. п. отменяют ожидание Главного: номер и код не должны стать ТЗ
            setting("wait", None)
        to_secretary({"message": msg}, chat)


def hear(msg):
    """Голосовое → текст → дальше как обычное сообщение."""
    chat = msg["chat"]["id"]
    try:
        tg("sendChatAction", chat_id=chat, action="typing")
        text = media.voice_text(TOKEN, msg)
    except media.MediaError as e:
        return send(chat, f"⚠️ 🎙 {esc(e)}")
    except Exception as e:
        return send(chat, f"⚠️ 🎙 Не получилось: {esc(e)}")
    send(chat, f"🎙 <i>«{esc(text)}»</i>")
    rest = {k: v for k, v in msg.items() if k not in ("voice", "audio", "video_note", "document", "caption")}
    on_message({**rest, "text": text})


def add_shots(paths):
    with lock:
        st = load()
        st["shots"] = ((st.get("shots") or []) + list(paths))[-10:]
        save(st)


def on_photo(msg):
    """Фото и скриншоты прикладываются к ТЗ «Сделать бота» / «Доработать» (Claude их посмотрит). Подпись — текст ТЗ."""
    chat = msg["chat"]["id"]
    try:
        path = media.photo_path(TOKEN, msg, os.path.join(DATA, "shots"))
    except Exception as e:
        return send(chat, f"⚠️ Не скачал фото: {esc(e)}")
    add_shots([path])
    cap = (msg.get("caption") or "").strip()
    if cap:
        rest = {k: v for k, v in msg.items() if k not in ("photo", "document", "caption")}
        return on_message({**rest, "text": cap})
    if not msg.get("media_group_id") or len(setting("shots")) == 1:
        send(chat, "📎 Фото приложу к задаче — теперь напиши или надиктуй, что сделать.")


def on_video(msg):
    """Видео к ТЗ: кадры — как скриншоты, речь или подпись — как текст задачи."""
    chat = msg["chat"]["id"]
    try:
        v = media.video(TOKEN, msg, os.path.join(DATA, "shots", f"v{int(time.time())}"))
    except Exception as e:
        return send(chat, f"⚠️ Видео: {esc(e)}")
    add_shots(v["frames"])
    said = "\n".join(x for x in ((msg.get("caption") or "").strip(), v.get("text") or "") if x)
    if said:
        send(chat, f"🎬 <i>«{esc(said[:600])}»</i>")
        rest = {k: v_ for k, v_ in msg.items() if k not in ("video", "video_note", "animation", "document", "caption")}
        return on_message({**rest, "text": said})
    send(chat, f"📎 Приложу к задаче {len(v['frames'])} кадр(а) из видео — теперь напиши, что сделать.")


def on_callback(cq):
    uid = cq["from"]["id"]
    msg = cq.get("message") or {}
    chat, mid = msg.get("chat", {}).get("id"), msg.get("message_id")
    if (cq.get("data") or "").startswith(SEC_PREFIX) and allowed(uid):  # кнопка Секретаря — ему, он и ответит
        if to_secretary({"callback_query": cq}, chat):
            return
        return tg("answerCallbackQuery", callback_query_id=cq["id"], text="Секретарь не отвечает")
    data = (cq.get("data") or "").split("|")
    arg = data[1] if len(data) > 1 else ""
    note = ""
    if not allowed(uid):
        note = "Нет доступа"
    elif data[0] == "home":
        setting("wait", None)
        home(chat, mid)
    elif data[0] == "bots":
        setting("wait", None)
        bots_screen(chat, mid)
    elif data[0] == "srst":
        note = "Поднимаю Секретаря…"
        threading.Thread(target=revive, args=(chat, True), daemon=True).start()
    elif data[0] == "app":
        app_card(chat, mid, arg)
    elif data[0] == "rs" and arg:
        note = "Перезапускаю…"
        threading.Thread(target=restart, args=(chat, arg), daemon=True).start()
    elif data[0] == "lg" and arg:
        threading.Thread(target=logs, args=(chat, arg), daemon=True).start()
    elif data[0] == "srv":
        server_card(chat, mid)
    elif data[0] == "more":
        more(chat, mid)
    elif data[0] == "cl":
        on_claude(chat, mid, arg)
    elif data[0] == "upd":
        note = "Обновляю…"
        threading.Thread(target=update_all, args=(chat, len(data) > 1), daemon=True).start()
    elif data[0] == "rmd":
        with lock:
            st = load()
            res = digest.callback(st, "|".join(data))
            save(st)
        if res:
            note = res[0]
            show(chat, mid, res[1], (res[2] or []) + [[b("‹ Назад", "more")]])
    elif data[0] in ("all", "mk", "mkr", "fix", "rb", "del?", "del", "mkh", "fixh"):
        note = maker.on_callback(chat, mid, data) or ""
    elif data[0] == "add" and len(data) == 1:
        show(chat, mid, card("➕ <b>Какого бота поставить?</b>", ["Выбери — дальше пришлёшь токен от @BotFather "
                                                                 "или я создам бота сам через Секретаря."]),
             pairs([b(clip(INSTALL[k][1]), f"add|{k}") for k in INSTALL]) + [[b("‹ Назад", "more")]])
    elif data[0] == "add" and arg in INSTALL:
        ask_token(chat, mid, arg)
    elif data[0] == "bf" and arg in INSTALL:
        setting("wait", None)
        note = "Создаю…"
        threading.Thread(target=botfather_install, args=(chat, arg), daemon=True).start()
    elif data[0] == "addt":
        token = setting("token_hint")
        setting("token_hint", None)
        if token and arg in INSTALL:
            threading.Thread(target=install, args=(chat, arg, token), daemon=True).start()
        else:
            note = "Пришли токен ещё раз"
    try:
        tg("answerCallbackQuery", callback_query_id=cq["id"], text=note)
    except Exception:
        pass


COMMANDS = [("start", "Главная"), ("app", "Приложение"), ("bots", "Боты и сервер"), ("login", "Войти в аккаунт"),
            ("jobs", "Расписание"), ("new", "Новый разговор"), ("stop", "Остановить ответ"), ("make", "Сделать бота"),
            ("fix", "Доработать бота"), ("update", "Обновить всех"), ("claude", "Вход для Claude"), ("help", "Что я умею")]
ABOUT = "Боты на сервере и личный секретарь: пишите или говорите что угодно."
DESCRIPTION = ("👑 Главный — один бот на всё.\n\nОтвечаю на вопросы, понимаю голос, фото и видео, веду ваш Telegram. "
               "Слежу за ботами на сервере, чиню и делаю новых.")


def main():
    global offset
    if not TOKEN:
        sys.exit("Нет HUB_TOKEN (токен от @BotFather). Впиши его в /etc/hub-bot.env")
    try:
        tg("setMyCommands", commands=[{"command": c, "description": d} for c, d in COMMANDS])
        tg("setMyShortDescription", short_description=ABOUT)
        tg("setMyDescription", description=DESCRIPTION)
    except Exception as e:
        print("setup:", e)
    chat = setting("updated_chat")
    if chat:
        setting("updated_chat", None)
        try:
            send(chat, "👑 <b>Снова на связи</b>")
            home(chat)
        except Exception as e:
            print("notify:", e)
    bootstrap_remote()
    if owner() and setting("intro") != 1:  # один раз: список всех ботов и как собрать их в папку
        setting("intro", 1)
        threading.Thread(target=maker.show_all, args=(owner(), None, True), daemon=True).start()
    threading.Thread(target=watch, daemon=True).start()
    threading.Thread(target=serve, daemon=True).start()
    threading.Thread(target=set_menu, args=(True,), daemon=True).start()  # узнаёт IP — дальше кнопка и в меню
    threading.Thread(target=check_secretary, daemon=True).start()  # Секретаря нет — поставить
    print("Главный запущен")
    while True:
        try:
            upd = tg("getUpdates", offset=offset, timeout=50, allowed_updates=["message", "callback_query"])
        except Exception as e:
            print("getUpdates:", e)
            time.sleep(5)
            continue
        for u in upd.get("result", []):
            offset = u["update_id"] + 1
            try:
                if u.get("callback_query"):
                    on_callback(u["callback_query"])
                elif u.get("message"):
                    on_message(u["message"])
            except Exception as e:
                print("update:", e)
                traceback.print_exc()


if __name__ == "__main__":
    main()
