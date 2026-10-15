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
import base64
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
import check  # noqa: E402

check.ui, check.maker = sys.modules[__name__], maker

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
    "studio-sites": "🌐 Сайты (сестра)",
    "catcher-bot": "🎯 Catcher",
    SELF: "👑 Главный",
}
INSTALL = {  # что умеем ставить по одному токену: папка, переменная с токеном, подпись, что дальше
    "coach": ("COACH_TOKEN", "⚡ Тренер WHOOP", "Открой @{name}, нажми /start, потом /connect — подключим WHOOP."),
    "planner": ("PLANNER_TOKEN", "🗓 Планер", "Открой @{name} и нажми /start — через минуту появится кнопка «Планер»."),
    "kpp": ("KPP_TOKEN", "🚧 КПП", "Открой @{name}, нажми /start и войди через Яндекс."),
    "avito": ("AVITO_TOKEN", "🛍 Авито", "Открой @{name}, нажми /start и пришли фото вещи."),
    "eye": ("EYE_TOKEN", "👁 Глаз (очки)", "Открой @{name} и нажми /start — через минуту появится кнопка «Глаз»: "
            "там «🎙 Говорить» и «📷 Фото»."),
    "catcher": ("CATCHER_TOKEN", "🎯 Catcher юзернеймов", "Открой @{name}, нажми /start — пришли @имя или выбери «🎲 По пожеланиям». "
                "Ловит через Секретаря, он должен быть в аккаунте (/login)."),
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
    "catcher": ("Catcher", "username_catcher"),
}
TZ = datetime.timezone(datetime.timedelta(hours=3))  # Москва

lock = threading.RLock()
offset = 0


def esc(x):
    return html.escape(str(x), quote=False)


# ---------- состояние ----------

def load():
    """Состояние Главного. Файл повреждён — берём копию (.bak), а не пустое: иначе следующая запись стёрла бы
    владельца, ключ Claude, напоминания и срок оплаты."""
    for path in (STATE, STATE + ".bak"):
        try:
            with open(path) as f:
                st = json.load(f)
            if isinstance(st, dict):
                return st
        except (OSError, ValueError):
            continue
    return {}


def save(st):
    os.makedirs(DATA, exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    try:  # прошлая ЦЕЛАЯ версия — запасная (битую поверх хорошей копии не кладём)
        with open(STATE) as f:
            json.load(f)
        shutil.copyfile(STATE, STATE + ".bak")
    except (OSError, ValueError):
        pass
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


LIMIT = 4096  # длина сообщения в Telegram (после разбора разметки)


def plain(text):
    """HTML Telegram -> простой текст (тегов нет, &lt; -> <): запасной вариант, когда разметка не принялась."""
    return html.unescape(re.sub(r"</?[a-z-]+(?: [^>]*)?>", "", str(text)))


def send(chat, text, buttons=None):
    """Сообщение с HTML. 400 (битая разметка, слишком длинно) — повторяем простым текстом и резаным до 4096:
    важное сообщение не должно пропасть из-за тега."""
    extra = {"reply_markup": {"inline_keyboard": buttons}} if buttons else {}
    try:
        return tg("sendMessage", chat_id=chat, text=text, parse_mode="HTML",
                  link_preview_options={"is_disabled": True}, **extra)
    except urllib.error.HTTPError as e:
        if e.code != 400 or not re.search(r"parse entities|too long|entities|text is empty", getattr(e, "detail", ""), re.I):
            raise
        flat = plain(text)
        flat = flat if len(flat) <= LIMIT else flat[:LIMIT - 1] + "…"
        return tg("sendMessage", chat_id=chat, text=flat or "…", link_preview_options={"is_disabled": True}, **extra)


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


def oops(chat, e):
    """Ошибка действия — понятным сообщением в чат, а не тишиной (подробности — в лог pm2)."""
    why = str(getattr(e, "detail", "") or e).strip()[:300] or type(e).__name__
    try:
        send(chat, card("⚠️ <b>Не получилось</b>", [esc(why)], "<i>Попробуй ещё раз или открой /start</i>"),
             [[b("‹ Главная", "home")]])
    except Exception as e2:
        print("oops:", e2)


def bg(chat, fn, *args):
    """Долгое действие из чата — в фоне; упало — сказать в чат (иначе статус висит, а человек ждёт)."""
    def run():
        try:
            fn(chat, *args)
        except Exception as e:
            traceback.print_exc()
            if chat:
                oops(chat, e)
    threading.Thread(target=run, args=(), daemon=True).start()


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
            "exit": env.get("exit_code"),
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
        secretary({"cmd": "_hub", "update": update, "owner": owner(), "mode": ui_mode()})
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


# ---------- экраны. Режим: 💬 чат или 📱 приложение (как в bot/bot.py и planner/planner.py) ----------
# Выбирается при первом /start, меняется по /mode, кнопкой «⚙️ Режим» и из приложения («💬 Перейти в чат»).
# «чат» — всё кнопками здесь, приложение не предлагается; «приложение» — главная кнопка открывает его,
# но команды и текст в чате работают всё равно. Хранится в hub/data/state.json («mode»).

MODES = {"chat": "💬 чат", "app": "📱 приложение"}


def ui_mode():
    m = setting("mode")
    return m if m in MODES else ""


def set_mode(m):
    if m not in MODES:
        raise ValueError("Режим — chat или app")
    setting("mode", m)


def mode_note():
    """Подпись режима для /start — одной строкой, курсивом."""
    return f"<i>Режим: {MODES.get(ui_mode(), 'не выбран')} · сменить — /mode</i>"


def mode_card(chat, mid=None):
    btns = [[b("💬 В чате", "mode|chat", "primary"), b("📱 Приложение", "mode|app", "primary")]]
    if ui_mode():  # режим уже выбран (зашли из /mode или «⚙️ Режим») — можно вернуться, ничего не меняя
        btns.append([b("‹ Главная", "home")])
    return show(chat, mid, card("🧭 <b>Как удобнее?</b>",
                                ["💬 <b>В чате</b> — всё кнопками и командами здесь",
                                 "📱 <b>Приложение</b> — Секретарь, боты и дела в одном окне"],
                                "<i>Поменять можно в любой момент: /mode</i>"), btns)


TASK_BTN = "✨ Поручить Секретарю"
TASK_EXAMPLES = ["найди билеты в Китай на ноябрь", "забронируй стол на пятницу", "найди часы работы Декатлона",
                 "что я пропустил", "ответь Пете, что буду в 6"]


def task_card(chat, mid=None):
    """«✨ Поручить Секретарю»: ждём одно сообщение (текст, голос или фото) — оно уйдёт Секретарю как обычный запрос."""
    setting("wait", "task")
    lines = ["Напишите или надиктуйте одним сообщением, что сделать. Можно приложить фото.", ""]
    lines += [f"• {esc(x)}" for x in TASK_EXAMPLES]
    return show(chat, mid, card("✨ <b>Поручение Секретарю</b>", lines,
                                "<i>Ищет в интернете, ведёт ваш Telegram, отвечает на вопросы. Купить и оплатить за вас не может — это за вами.</i>"),
                [[b("‹ Отмена", "home")]])


def on_mode(chat, mid, key):
    """Кнопка на карточке «Как удобнее?»: запомнить и показать главный экран по режиму."""
    set_mode(key)
    edit(chat, mid, card("✅ <b>Режим: " + MODES[key] + "</b>", [], "<i>Сменить — /mode или кнопка «⚙️ Режим»</i>"))
    home(chat)


def app_mode(body):
    """«💬 Перейти в чат» из приложения (или обратно): запоминает режим; в чате сразу приходит главное меню."""
    m = str(body.get("mode") or "")
    set_mode(m)
    if m == "chat":
        send(owner(), card("✅ <b>Режим: " + MODES[m] + "</b>", ["Всё, что было в приложении, есть и здесь."],
                           "<i>Вернуть приложение — /mode</i>"))
        threading.Thread(target=home, args=(owner(),), daemon=True).start()
    return {"mode": m}


def home(chat, mid=None):
    """Главный экран (/start): коротко, как дела у ботов и у Секретаря, и главные кнопки.
    Режим «📱 приложение» — короче: главная кнопка открывает приложение, плюс 2–3 действия."""
    m = ui_mode()
    if not m:  # первый /start — сначала выбор, чат или приложение
        return mode_card(chat, mid)
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
    bots_btn = b("🤖 Боты", "bots", "danger" if rows and any(a["status"] != "online" for a in rows) else None)
    acc_btn = b("👤 Аккаунт", SEC_PREFIX + "m:home") if sec is not None else b("🔄 Секретарь", "srst", "primary")
    web = app_button() if m == "app" else None
    foot = mode_note()
    if m == "app" and not web:
        foot += "\n<i>https-адреса пока нет, поэтому всё здесь</i>"
    studio = studio_button(rows)
    task_btn = b(TASK_BTN, "task", "primary")  # поручение Секретарю — первой кнопкой, в обоих режимах
    if web:
        btns = [[web], [task_btn], [bots_btn, b("🧠 Психолог", "psy"), acc_btn],
                [b("🧪 Проверка", "chk"), b("⚙️ Режим", "mode|ask")]]
        foot += "\n<i>💬 Просто напишите задачу — Секретарь выполнит. Команды тоже работают.</i>"
    else:
        btns = [[task_btn],
                [bots_btn, acc_btn, b("🛠 Поддержка", "fix")],  # «Доработать» любого бота (maker.ask_fix)
                [b("🛠 Сделать бота", "mk"), b("⬆️ Обновить всех", "upd"), b("🧪 Проверка", "chk")],
                [b("🧠 Психолог", "psy"), b("⚙️ Режим", "mode|ask")]]
        foot += "\n<i>💬 Просто напишите или надиктуйте задачу — Секретарь выполнит. Фото и видео тоже.</i>"
    if studio:  # бот, который делает ботов, — в последний ряд (рядов не прибавляется: экран остаётся коротким)
        btns[-1].append(studio)
    return show(chat, mid, card("👑 <b>Главный</b>", lines, foot), btns)


# ☰ Полное меню — клавиатура у поля ввода: свёрнута в квадратик справа (не is_persistent), по нажатию — все
# действия; после выбора сама сворачивается (one_time). Кнопки Секретаря (📬, 🗂) — те же тексты, что у него.
KB_VERSION = 1
KB_ROWS = [["✨ Поручить", "🤖 Боты", "👤 Аккаунт"],
           ["🧪 Проверка", "🛠 Сделать бота", "✏️ Доработать"],
           ["📬 Что я пропустил", "🧠 Разбор чата", "🗂 Навести порядок"],
           ["⬆️ Обновить всех", "🖥 Сервер", "📂 Все боты"],
           ["⏰ Напоминания", "⚙️ Режим", "⋯ Ещё"]]
KB_TEXTS = {t for r in KB_ROWS for t in r}


def keyboard():
    return {"keyboard": [[{"text": t} for t in r] for r in KB_ROWS], "resize_keyboard": True,
            "one_time_keyboard": True, "is_persistent": False, "input_field_placeholder": "Напишите задачу или ☰ справа"}


def ensure_keyboard(chat, force=False):
    """Один раз на версию (и по /start): поставить клавиатуру-меню. Сообщение короткое — он видит, где меню."""
    if not force and setting("kb_ver") == KB_VERSION:
        return
    try:
        tg("sendMessage", chat_id=chat, text="☰ Меню — квадратик справа от поля ввода", reply_markup=keyboard())
        setting("kb_ver", KB_VERSION)
    except Exception as e:
        print("клавиатура:", repr(e), flush=True)


def on_kb(chat, text, msg):
    """Нажатие кнопки клавиатуры-меню → тот же экран, что у кнопок в сообщениях."""
    if text == "✨ Поручить":
        task_card(chat)
    elif text == "🤖 Боты":
        bots_screen(chat)
    elif text == "👤 Аккаунт":
        to_secretary({"message": {**msg, "text": "/menu"}}, chat)
    elif text == "🧪 Проверка":
        check.ask(chat)
    elif text == "🛠 Сделать бота":
        maker.ask_make(chat)
    elif text == "✏️ Доработать":
        maker.ask_fix(chat)
    elif text == "🧠 Разбор чата":
        psy_card(chat)
    elif text in ("📬 Что я пропустил", "🗂 Навести порядок"):
        to_secretary({"message": msg}, chat)
    elif text == "⬆️ Обновить всех":
        if updating():
            send(chat, "⏳ Уже обновляю — итог придёт сюда.")
        else:
            progress("Скачиваю свежий код из main…", kind="update")
            bg(chat, update_all)
    elif text == "🖥 Сервер":
        server_card(chat, None)
    elif text == "📂 Все боты":
        bg(chat, maker.show_all)
    elif text == "⏰ Напоминания":
        with lock:
            st = load()
            res = digest.callback(st, "rmd|list")
            save(st)
        send(chat, res[1], (res[2] or []) + [[b("‹ Назад", "more")]]) if res else send(chat, HELP)
    elif text == "⚙️ Режим":
        mode_card(chat)
    elif text == "⋯ Ещё":
        more(chat, None)


def full_menu(chat, mid=None):
    """Полное меню одним сообщением (/menu_all; основное полное меню — клавиатура-квадратик у поля ввода)."""
    rows = apps()
    sec = secretary_status()
    down = [a for a in rows or [] if a["status"] != "online"]
    lines = ["⚠️ pm2 не отвечает" if rows is None else
             ("🔴 Не работает: " + ", ".join(esc(short(a["label"])) for a in down)) if down else
             f"🟢 Все боты работают · {len(rows)}",
             "⚪️ Секретарь не отвечает" if sec is None else
             "👤 Аккаунт подключён" if sec.get("account") else "👤 Аккаунт не подключён"]
    btns = [[b(TASK_BTN, "task", "primary")],
            [b("🤖 Боты", "bots", "danger" if down else None),
             b("👤 Аккаунт", SEC_PREFIX + "m:home") if sec is not None else b("🔄 Секретарь", "srst", "primary"),
             b("🧠 Психолог", "psy")],
            [b("🛠 Сделать бота", "mk"), b("✏️ Доработать", "fix"), b("📂 Все боты", "all")],
            [b("⬆️ Обновить всех", "upd"), b("🖥 Сервер", "srv"), b("➕ Поставить", "add")],
            [b("🧪 Проверка ботов", "chk", "success"), b("⏰ Напоминания", "rmd|list")],
            [b("🤖 Claude", "cl"), b("⚙️ Режим", "mode|ask")]]
    last = [x for x in (studio_button(rows), app_button()) if x]
    if last:
        btns.append(last)
    return show(chat, mid, card("☰ <b>Полное меню</b>", lines), btns)


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
    web = app_button() if ui_mode() == "app" else None  # в режиме «чат» приложение не навязываем
    if web:
        btns.append([web])
    btns += pairs([b(clip(a["label"]), f"app|{a['name']}", None if a["status"] == "online" else "danger")
                   for a in rows if a["name"] != SELF])
    btns += [[b("⬆️ Обновить всех", "upd"), b("✏️ Доработать", "fix")],
             [b("📂 Все боты", "all"), b("🖥 Сервер", "srv"), b("🧪 Проверка", "chk")],
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
    """Карточка бота: состояние, перезапуск, логи и «✏️ Доработать» — Claude правит его код (чинит, добавляет функции)."""
    rows = apps()
    a = next((x for x in rows or [] if x["name"] == name), None)
    if not a:
        return show(chat, mid, card("🤷 <b>Нет такого бота</b>", [f"{esc(name)} нет в pm2."]), [[b("‹ Назад", "bots")]])
    ok = a["status"] == "online"
    about = (maker.CATALOG.get(name) or ())[5:6]
    lines = [esc(about[0])] if about else []
    lines += ["🟢 Работает " + ago(a["uptime"]) if ok else "🔴 Не работает: " + esc(a["status"]),
              f"• Память {a['mem']} МБ · CPU {a['cpu']}%",
              f"• Перезапусков: {a['restarts']}"]
    btns = []
    if name in dict(maker.targets(rows)):
        btns.append([b("✏️ Доработать", f"fix|{name}", "primary"), b("➕ Новая функция", f"fix|{name}")])
    btns += [[b("🔄 Перезапустить", f"rs|{name}", None if btns else "primary"), b("📜 Логи", f"lg|{name}")]]
    slug = next((s for s, i in maker.made().items() if i.get("pm2") == name), None)
    btns.append([b("‹ Назад", "bots")] + ([b("🗑 Удалить", f"del?|{slug}", "danger")] if slug else []))
    return show(chat, mid, card(f"<b>{esc(a['label'])}</b>", lines,
                                "<i>✏️ Доработать — напиши, что починить, поменять или добавить</i>" if len(btns) > 2 else ""),
                btns)


# ---------- 🧠 Психолог (разбор переписок делает Секретарь через аккаунт Глеба) ----------

PSY_TEXTS = {"🧠 Разбор чата"}  # кнопка клавиатуры Секретаря — та же карточка
PSY_SCOPES = {"week": "Все чаты · неделя", "month": "Все чаты · месяц"}


def psy_card(chat, mid=None):
    """/psy и кнопка «🧠 Психолог»: все чаты за неделю или месяц, один чат. Без входа в аккаунт — честно сказать."""
    sec = secretary_status()
    back = [b("‹ Главная", "home")]
    if sec is None:
        return show(chat, mid, card("🧠 <b>Психолог</b>", ["Разбор переписок делает Секретарь, а он не отвечает. "
                                                           "Поднимаю его — попробуй через минуту."]),
                    [[b("🔄 Перезапустить", "srst", "primary")], back])
    if not sec.get("account"):
        return show(chat, mid, card("🧠 <b>Психолог</b>",
                                    ["Разбираю переписки из твоего Telegram, а аккаунт пока не подключён.",
                                     "Войди — это минута — и разбор заработает: все чаты за неделю или месяц, или один чат."]),
                    [[b("🔑 Войти в аккаунт", SEC_PREFIX + "login:", "primary")],
                     [b("👤 Аккаунт", SEC_PREFIX + "m:home")] + back])
    lines = ["Разберу переписки как сильный практикующий психолог: отношения и роли, тон, скрытые конфликты "
             "и манипуляции, на кого уходят силы — и 3–5 конкретных советов.",
             "• Все чаты — общий разбор за период, с кнопками «Подробнее про …» по людям",
             "• Один чат — человек или группа"]
    if sec.get("busy"):
        lines.append("⏳ Секретарь сейчас занят поручением — дождись ответа.")
    return show(chat, mid, card("🧠 <b>Психолог</b>", lines, "<i>Это несколько минут, ответ придёт сюда.</i>"),
                [[b("📅 " + PSY_SCOPES["week"], "psy|week"), b("📆 " + PSY_SCOPES["month"], "psy|month")],
                 [b("💬 Один чат", "psy|one")], back])


def psy_one(chat, mid=None):
    """Выбор чата: последние диалоги кнопками (по 3 в ряд) или написать имя."""
    try:
        r = secretary({"cmd": "_dialogs", "args": {"limit": 9}}, timeout=20)
        ds = r.get("out") if r.get("ok") and isinstance(r.get("out"), list) else []
    except SecretaryDown:
        return secretary_down(chat)
    setting("wait", "psy")
    btns = pairs([b(clip(d.get("name") or "?", 24), f"psy|c|{d.get('id')}") for d in ds if d.get("id") is not None], 3)
    return show(chat, mid, card("🧠 <b>Один чат</b>", ["Выбери из последних или напиши имя человека или чата — "
                                                        "можно с уточнением: «8Б за месяц, как ко мне относятся»."],
                                "<i>/cancel — отмена</i>"), btns + [[b("‹ Назад", "psy")]])


def psy_run(chat, scope, target=None, mid=None):
    """Запустить разбор у Секретаря: он сам пишет живой статус и ответ в чат. Мы — только подтверждение."""
    try:
        r = secretary({"cmd": "_psy", "args": {"scope": scope, "chat": target}}, timeout=20)
    except SecretaryDown:
        return secretary_down(chat)
    if not r.get("ok"):
        why = str(r.get("out") or "не получилось")
        btns = [[b("🔑 Войти в аккаунт", SEC_PREFIX + "login:", "primary")]] if "аккаунт" in why else []
        return show(chat, mid, card("⚠️ <b>Разбор не запустился</b>", [esc(why)]), btns + [[b("‹ Назад", "psy")]])
    what = PSY_SCOPES.get(scope) or f"разбор: {esc(str((r.get('out') or {}).get('q') or target or ''))[:60]}"
    show(chat, mid, card("🧠 <b>Разбираю</b>", [esc(what), "Статус и ответ придут сюда — это несколько минут."]), [])


def log_text(name, lines=30, limit=3500):
    """Последние строки логов бота без пустых строк (для чата и приложения)."""
    code, out = sh("pm2", "logs", name, "--nostream", "--lines", str(lines), "--raw", timeout=30)
    return "\n".join(line for line in out.splitlines() if line.strip())[-limit:] or "пусто"


def fit_pre(text, limit=3800):
    """Текст для <pre>: после экранирования (& < > растут в 4–5 раз) влезает в сообщение Telegram (4096)."""
    while len(esc(text)) > limit:
        text = text[len(text) // 5 + 1:]
    return esc(text)


def logs(chat, name):
    send(chat, f"📜 <b>Логи · {esc(short(label(name)))}</b>\n<pre>{fit_pre(log_text(name))}</pre>",
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
    if name == SELF:  # после перезапуска Главный сам напишет «Снова на связи» в этот чат
        setting("updated_chat", chat)
        try:  # подтвердить нажатие у Telegram, иначе после перезапуска оно придёт снова — и так по кругу
            tg("getUpdates", offset=offset, timeout=0)
        except Exception:
            pass
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

HDP_SETTINGS = os.path.join(REPO, "horo", "tmp", "bot_settings.json")  # как SETTINGS в bot/bot.py
HDP_WAIT = 15 * 60  # дольше не ждём: задание, видимо, зависло — бот после перезапуска начнёт его заново


def hdp_busy():
    """Сколько заданий HDP-бот решает прямо сейчас (его bot_settings.json → active)."""
    try:
        with open(HDP_SETTINGS) as f:
            return len(json.load(f).get("active") or {})
    except (OSError, ValueError, AttributeError):
        return 0


def wait_hdp(limit=HDP_WAIT, pause=10):
    """Перезапуск посреди сдачи теста обрывает попытку на сайте — сначала ждём, пока HDP досдаст."""
    end = time.time() + limit
    while hdp_busy() and time.time() < end:
        progress(f"Жду, пока HDP досдаст задание ({hdp_busy()})…")
        time.sleep(pause)

def update_all(chat, reset=False):
    """Обновить всех из main. Упало посередине — живой статус закрываем, иначе «Обновляю…» висит полчаса."""
    try:
        _update_all(chat, reset)
    except Exception:
        progress("Обновление упало — подробности в чате", done=True, ok=False)
        raise


def _update_all(chat, reset=False):
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
    names.sort(key=lambda n: n == "horo-bot")  # HDP — последним: остальные обновятся, пока он досдаёт задание
    res = []
    for n in names:
        if n == "horo-bot":
            wait_hdp()
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


installing = set()  # что сейчас ставится: второе нажатие «Создать бота сам» не плодит второго бота


def botfather_install(chat, kind):
    if kind in installing:
        return send(chat, "⏳ Уже создаю этого бота — итог придёт сюда.")
    installing.add(kind)
    try:
        _botfather_install(chat, kind)
    finally:
        installing.discard(kind)


def _botfather_install(chat, kind):
    name, want = BOTFATHER.get(kind, (INSTALL[kind][1], kind))
    send(chat, card(f"🤖 <b>Создаю бота «{esc(name)}»</b>", ["В @BotFather через Секретаря…"]))
    token, why = maker.botfather(name, want)
    if not token:
        setting("wait", kind)
        return send(chat, card("😕 <b>Не создал</b>", [esc(why)], "Создай сам в @BotFather (/newbot) и пришли сюда токен."),
                    [[{"text": "Открыть @BotFather", "url": "https://t.me/BotFather"}], [b("Отмена", "home")]])
    install(chat, kind, token)


STUDIO_ENV = "/etc/studio-bot.env"
STUDIO_RETRY = 2 * 3600  # автоустановка Студии — не чаще раза в 2 часа
STUDIO_SEC_AGE = 120     # и только когда Секретарь проработал минуты две (сразу после рестарта аккаунт ещё подключается)


def studio_installed(rows=None):
    """Студия есть на сервере: запущена в pm2 (даже упавшая) или есть её файл с токеном."""
    rows = apps() if rows is None else rows
    return any(a["name"] == "studio-bot" for a in rows or []) or os.path.exists(STUDIO_ENV)


_studio_ask = [0.0]  # когда последний раз спрашивали Telegram об адресе Студии (не чаще раза в 10 минут)


def studio_username():
    """@username Студии: запоминаем при установке, у уже стоящей — спрашиваем Telegram по её токену."""
    name = setting("studio_user")
    if name:
        return name
    if time.time() - _studio_ask[0] < 600:
        return ""
    _studio_ask[0] = time.time()
    try:
        with open(STUDIO_ENV) as f:
            m = re.search(r"^STUDIO_TOKEN=(\S+)", f.read(), re.M)
        if m:
            with urllib.request.urlopen(f"https://api.telegram.org/bot{m.group(1).strip(chr(34) + chr(39))}/getMe",
                                        timeout=5) as r:
                name = json.load(r)["result"].get("username") or ""
            if name:
                setting("studio_user", name)
            return name
    except Exception:
        pass
    return ""


def studio_button(rows=None):
    """Кнопка на главном экране: «🏗 Студия» (открыть бота) или «🏗 Поставить Студию» (ещё нет)."""
    if not studio_installed(rows):
        return b("🏗 Поставить Студию", "stu", "primary")
    name = studio_username()
    return {"text": "🏗 Студия", "url": f"https://t.me/{name}", "style": "primary"} if name else None


def studio_auto(now=None):
    """Студии нет, а Секретарь уже вошёл в аккаунт Глеба — ставим её сами: бот в @BotFather → install.sh.
    Не чаще раза в 2 часа; не вышло — Главный просит токен, а кнопка «🏗 Поставить Студию» остаётся."""
    now = now or time.time()
    chat = owner()
    if not chat or now - float(setting("studio_try") or 0) < STUDIO_RETRY:
        return False
    if studio_installed():
        return False
    try:
        if now - os.path.getmtime(AGENT_SOCK) < STUDIO_SEC_AGE:
            return False
    except OSError:
        return False
    sec = secretary_status()
    if not (sec and sec.get("account")):
        return False
    setting("studio_try", now)
    send(chat, card("🏗 <b>Ставлю Студию</b>", ["Это бот, который делает ботов. Создам его сам через Секретаря — "
                                                 "через пару минут он появится."]))
    threading.Thread(target=botfather_install, args=(chat, "studio"), daemon=True).start()
    return True


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
        return send(chat, f"❌ <b>Не получилось</b>\n<pre>{esc(out.strip()[-1200:])}</pre>",
                    [[b("🔁 Ещё раз", f"add|{kind}"), b("‹ К ботам", "bots")]])
    if kind == "studio":
        setting("studio_user", name)  # кнопка «🏗 Студия» на главном экране
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
                # перезапуски по кругу: +3 падения за 10 минут. Плановый перезапуск (обновление, кнопка,
                # pm2 restart) выходит с кодом 0 — он не падение, иначе 3 обновления подряд = ложная тревога у всех
                last = s.get("last_r", a["restarts"])
                if a["restarts"] > last and a.get("exit") != 0:
                    s["crashes"] = s.get("crashes", 0) + a["restarts"] - last
                s["last_r"] = a["restarts"]
                s["hist"] = [h for h in s["hist"] if now - h[0] < 600] + [[now, s.get("crashes", 0)]]
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
                    try:  # одно не дошедшее сообщение не должно отнимать остальные (состояние уже записано)
                        send(owner(), text, btns)
                    except Exception as e:
                        print("watch send:", e, str(getattr(e, "detail", ""))[:200])
                studio_auto()
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
fails_lock = threading.Lock()
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
            "mode": ui_mode(),
            "check": check.app_state(),
            "support": [{"id": pm2, "name": short(lbl)} for pm2, lbl in maker.targets(rows or [])],
            "live": dict(live), "boot": BOOT, "now": time.time()}


def updating():
    """Обновление всех уже идёт (из чата или приложения) — второе не запускаем."""
    return live.get("kind") == "update" and not live.get("done") and time.time() - live.get("at", 0) < 1800


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
        if updating():
            raise ValueError("уже обновляю")
        progress("Скачиваю свежий код из main…", kind="update")
        threading.Thread(target=update_all, args=(owner(),), daemon=True).start()
        return {"ok": True, "text": "Обновляю…", "at": live["at"]}
    if action in ("check", "check_fix", "check_fixall"):  # 🧪 Проверка ботов
        return check.app_action(action, arg)
    if action == "mode":  # то же, что POST /app/api/mode {mode}
        return app_mode({"mode": arg})
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


def app_support(body):
    """🛠 Поддержка из приложения: бот + что поправить (+ скриншот) — та же доработка, что «✏️ Доработать» в чате."""
    pm2, text = str(body.get("bot") or ""), str(body.get("text") or "").strip()[:4000]
    if pm2 not in {p for p, _ in maker.targets()}:
        raise ValueError("Выбери бота")
    if not text:
        raise ValueError("Напиши, что поправить")
    if maker.job.locked():
        raise ValueError("Claude ещё занят прошлой задачей — пришли, когда закончит")
    if body.get("photo"):
        try:
            raw = base64.b64decode(str(body["photo"]).split(",", 1)[-1])
        except ValueError:
            raise ValueError("Скриншот не разобрался") from None
        if not raw or len(raw) > 10 * 1024 * 1024:
            raise ValueError("Скриншот пустой или больше 10 МБ")
        os.makedirs(os.path.join(DATA, "shots"), exist_ok=True)
        path = os.path.join(DATA, "shots", f"web-{int(time.time() * 1000)}" + (".png" if raw[:4] == b"\x89PNG" else ".jpg"))
        with open(path, "wb") as f:
            f.write(raw)
        add_shots([path])  # maker.fix заберёт их, как фото из чата
    threading.Thread(target=maker.fix, args=(owner(), pm2, text + "\n\n(написано в мини-приложении)"),
                     daemon=True).start()
    return {"ok": True}


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
        tell("🤖 Вход для Claude включён — он сам поставит и проверит ботов. Его команды не присылаю, "
             "список — /claude → «📜 Что делал». Выключить: /claude → «⛔ Выключить».")


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
        print("tell:", e, str(getattr(e, "detail", ""))[:200])


CMDLOG = []  # что делал Claude: (время, что, зачем) — последние 50, смотреть в /claude → «📜 Что делал»


def log_cmd(what, why=""):
    """Команда Claude: в журнал; в чат — только если владелец включил «🔔 Писать о командах» (Глеб 14.10:
    сообщения о каждой команде мешают)."""
    CMDLOG.append((time.time(), what, why))
    del CMDLOG[:-50]
    if setting("remote_notify"):
        tell("🤖 <b>Claude выполняет</b>" + (f" — {esc(why[:200])}" if why else "") + f"\n<pre>{esc(what[:600])}</pre>")


def run_job(cmd, timeout, why=""):
    """Команда в bash в папке репозитория; вывод копится в задаче, Claude забирает его по id."""
    if sum(1 for j in jobs.values() if not j["done"]) >= 8:  # не даём забить сервер горой одновременных команд
        raise ValueError("уже идёт 8 команд — дождись окончания")
    jid = secrets.token_hex(4)
    job = jobs[jid] = {"id": jid, "cmd": cmd, "done": False, "code": None, "out": "", "started": time.time()}
    for k in sorted(jobs, key=lambda k: jobs[k]["started"])[:-50]:
        jobs.pop(k, None)
    log_cmd(cmd, why)

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
        if job["code"] != 0 and setting("remote_notify"):
            tell(f"🤖 Команда Claude завершилась с кодом {job['code']}")
    threading.Thread(target=work, daemon=True).start()
    return job


def remote_api(action, body):
    if action == "status":
        return {"apps": apps(), "server": server(), "paid": setting("paid")}
    if action in ("logs", "restart"):  # имя — не флаг для pm2 (--update-env и т. п.)
        nm = str(body.get("name") or "")
        if not nm or nm.startswith("-") or len(nm) > 80:
            raise ValueError("неверное имя бота")
    if action == "logs":
        lines = max(10, min(int(body.get("lines") or 60), 1000))
        code, out = sh("pm2", "logs", str(body["name"]), "--nostream", "--lines", str(lines), "--raw", timeout=30)
        return {"code": code, "out": out[-100000:]}
    if action == "restart":
        code, out = sh("pm2", "restart", str(body["name"]), timeout=60)
        log_cmd(f"перезапуск {label(body['name'])}", str(body.get("why") or ""))
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
        log_cmd("обновить всех ботов", str(body.get("why") or ""))
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

    def length(self):
        """Content-Length; мусор или меньше нуля — ValueError (ответ 400): иначе read(-1) ждал бы конца соединения."""
        n = int(self.headers.get("Content-Length") or 0)
        if n < 0:
            raise ValueError("bad length")
        return n

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path.startswith("/app/agent/"):
            try:
                n = self.length()
            except ValueError:
                return self.reply(400, {"error": "bad length"})
            if n > 300_000:
                return self.reply(413, {"error": "слишком большой запрос"})
            return self.proxy(path[len("/app/agent"):], self.rfile.read(n) if n else b"")
        if path == "/app/api/support":  # 🛠 из приложения, со скриншотом
            try:
                n = self.length()
                if n > 16_000_000:
                    return self.reply(413, {"error": "слишком большой скриншот"})
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
            except ValueError:
                return self.reply(400, {"error": "bad json"})
            return self.app_api(app_support, body if isinstance(body, dict) else {})
        if path == "/app/api/mode":  # {mode: chat|app} — то же, что action=mode
            try:
                n = self.length()
                body = json.loads(self.rfile.read(min(n, 10_000)) or b"{}") if n else {}
            except ValueError:
                return self.reply(400, {"error": "bad json"})
            body = body if isinstance(body, dict) else {}
            return self.app_api(app_mode, body)
        if path == "/app/api/action":  # мини-приложение: вход по подписи Telegram
            try:
                n = self.length()
                body = json.loads(self.rfile.read(min(n, 100_000)) or b"{}") if n else {}
            except ValueError:
                return self.reply(400, {"error": "bad json"})
            body = body if isinstance(body, dict) else {}
            return self.app_api(lambda b_: app_action(b_.get("action"), b_.get("arg")), body)
        now = time.time()
        with fails_lock:
            fails[:] = [t for t in fails if now - t < 3600]
            locked = len(fails) >= 20
        if not remote_on():
            return self.reply(403, {"error": "вход для Claude выключен — включи в Главном: /claude"})
        # верный ключ проходит всегда (256 бит не подобрать): иначе любой, кто 20 раз ошибётся, запер бы владельца на час
        if not check_key(self.headers.get("Authorization") or ""):
            if locked:
                return self.reply(429, {"error": "слишком много неверных ключей, вход закрыт на час"})
            with fails_lock:
                fails.append(now)
                first = len(fails) == 20
            if first:
                tell("⚠️ 20 попыток с неверным ключом за час — неверные ключи отклоняются ещё час. "
                     "Если это не ты, нажми «♻️ Новый ключ» в /claude.")
            return self.reply(401, {"error": "неверный ключ"})
        try:
            n = self.length()
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
             "команды на сервере. " + ("О каждой команде пишу сюда." if setting("remote_notify")
                                       else "Команды не присылаю — список в «📜 Что делал»."), "",
             "🟢 Включён" if on else "⚪️ Выключен"]
    if url:
        lines.append(f"Адрес: <code>{esc(url)}</code>")
    if on:
        btns = [[b("📜 Что делал", "cl|log"),
                 b("🔕 Не писать" if setting("remote_notify") else "🔔 Писать о командах", "cl|ntf")],
                [b("🔑 Показать ключ", "cl|show"), b("♻️ Новый ключ", "cl|new")], [b("⛔ Выключить", "cl|off", "danger")]]
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
    elif arg == "ntf":
        setting("remote_notify", not setting("remote_notify"))
        claude_card(chat, mid)
    elif arg == "log":
        rows = [f"{datetime.datetime.fromtimestamp(t, TZ):%d.%m %H:%M} · {esc((why or what)[:90])}"
                for t, what, why in CMDLOG[-15:]]
        show(chat, mid, card("📜 <b>Что делал Claude</b>", rows or ["Пока ничего (с последнего перезапуска Главного)."]),
             [[b("‹ Назад", "cl|card")]])
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
    "• ✨ Просто напишите задачу текстом или голосом — Секретарь выполнит: «найди билеты в Китай», «что я пропустил». Кнопка — «✨ Поручить Секретарю» (/task).",
    "• 💬 Пиши или говори что угодно — отвечу: вопросы, учёба, поиск в интернете. Фото и видео тоже разберу.",
    "• 👤 Твой Telegram: что пропустил, ответы, папки, сообщения по расписанию — /login, /jobs, /new, /stop.",
    "• 🤖 /bots — боты: упадёт — напишу сам; перезапуск, логи, /update — обновить всех.",
    "• 🛠 /make — новый бот по ТЗ, ✏️ /fix — доработать любого, 📂 /all — все со ссылками.",
    "• 🧠 /psy — психолог: разбор всех чатов за неделю или месяц, или одного чата.",
    "• 📱 /app — приложение: секретарь, боты, дела (кнопка «Приложение» у поля ввода); ⚙️ /mode — чат или приложение.",
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
        if setting("wait") == "task":  # «Поручить Секретарю»: фото с подписью — задача
            setting("wait", None)
        if maker_wait():
            return threading.Thread(target=on_photo if kind == "photo" else on_video, args=(msg,), daemon=True).start()
        return to_secretary({"message": msg}, chat)
    cmd = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
    wait = setting("wait")
    if cmd == "/1":  # старая команда — то же полное меню
        setting("wait", None)
        full_menu(chat)
    elif not cmd and text in KB_TEXTS:  # клавиатура-меню (квадратик у поля ввода)
        setting("wait", None)
        on_kb(chat, text, msg)
    elif cmd in ("/start", "/bots", "/menu") or text in MENU_TEXTS:
        setting("wait", None)
        if cmd == "/start":
            ensure_keyboard(chat)
        bots_screen(chat) if cmd == "/bots" else home(chat)
    elif cmd == "/mode":
        setting("wait", None)
        mode_card(chat)
    elif cmd == "/psy" or text in PSY_TEXTS:
        setting("wait", None)
        psy_card(chat)
    elif not cmd and wait == "psy" and text:  # имя чата или человека для «Один чат»
        setting("wait", None)
        bg(chat, psy_run, "chat", text)
    elif not cmd and wait == "task" and text:  # поручение Секретарю: следующее сообщение — задача
        setting("wait", None)
        to_secretary({"message": msg}, chat)
    elif cmd == "/task":  # «/task найди билеты в Китай» — сразу Секретарю; просто «/task» — подсказка
        rest = text.split(None, 1)[1].strip() if len(text.split(None, 1)) > 1 else ""
        if rest:
            setting("wait", None)
            to_secretary({"message": {**msg, "text": rest}}, chat)
        else:
            task_card(chat)
    elif cmd == "/help":
        send(chat, HELP)
    elif cmd == "/app":
        open_app(chat)
    elif cmd == "/test":  # «/check» — проверка аккаунта у Секретаря, не путать
        setting("wait", None)
        check.ask(chat)
    elif cmd == "/server":
        server_card(chat, None)
    elif cmd == "/claude":
        claude_card(chat, None)
    elif cmd == "/update":
        if updating():
            send(chat, "⏳ Уже обновляю — итог придёт сюда.")
        else:
            progress("Скачиваю свежий код из main…", kind="update")
            bg(chat, update_all)
    elif cmd == "/all":
        bg(chat, maker.show_all)
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
        try:
            if to_secretary({"callback_query": cq}, chat):
                return
        except Exception as e:  # чат недоступен и т. п. — всё равно снять крутилку
            print("secretary callback:", e)
        try:
            tg("answerCallbackQuery", callback_query_id=cq["id"], text="Секретарь не отвечает")
        except Exception:
            pass
        return
    data = (cq.get("data") or "").split("|")
    note = ""
    try:  # что бы ни случилось, крутилка на кнопке не должна висеть: ответ — в finally
        note = callback(chat, mid, uid, data)
    except Exception as e:
        traceback.print_exc()
        note = "⚠️ Не получилось: " + str(getattr(e, "detail", "") or e)[:150]
        if chat:
            oops(chat, e)
    finally:
        try:
            tg("answerCallbackQuery", callback_query_id=cq["id"], text=note or "")
        except Exception:
            pass


STALE = "Кнопка устарела — открой /start"


def callback(chat, mid, uid, data):
    """Кнопки Главного -> текст всплывашки ('' — без неё). Незнакомая/устаревшая кнопка — подсказка, а не тишина."""
    arg = data[1] if len(data) > 1 else ""
    note = ""
    if not allowed(uid):
        note = "Нет доступа"
    elif not chat:  # сообщение слишком старое или удалено — Telegram не дал чат
        note = STALE
    elif data[0] == "home":
        setting("wait", None)
        home(chat, mid)
    elif data[0] == "bots":
        setting("wait", None)
        bots_screen(chat, mid)
    elif data[0] == "chk":
        note = check.on_callback(chat, mid, data)
        note = STALE if note is None else note
    elif data[0] == "full":
        setting("wait", None)
        full_menu(chat, mid)
    elif data[0] == "mode":  # 💬 чат / 📱 приложение
        setting("wait", None)
        on_mode(chat, mid, arg) if arg in MODES else mode_card(chat, mid)
    elif data[0] == "task":
        task_card(chat, mid)
    elif data[0] == "psy" and not arg:
        setting("wait", None)
        psy_card(chat, mid)
    elif data[0] == "psy" and arg == "one":
        bg(chat, psy_one, mid)
    elif data[0] == "psy" and arg in PSY_SCOPES:
        note = "Запускаю разбор…"
        bg(chat, psy_run, arg, None, mid)
    elif data[0] == "psy" and arg == "c" and len(data) > 2:
        setting("wait", None)
        note = "Запускаю разбор…"
        bg(chat, psy_run, "chat", data[2], mid)
    elif data[0] == "srst":
        note = "Поднимаю Секретаря…"
        bg(chat, revive, True)
    elif data[0] == "app":
        setting("wait", None)  # «‹ Отмена» из «Доработать» возвращает сюда
        app_card(chat, mid, arg)
    elif data[0] == "rs" and arg:
        note = "Перезапускаю…"
        bg(chat, restart, arg)
    elif data[0] == "lg" and arg:
        bg(chat, logs, arg)
    elif data[0] == "srv":
        server_card(chat, mid)
    elif data[0] == "more":
        more(chat, mid)
    elif data[0] == "cl":
        on_claude(chat, mid, arg)
    elif data[0] == "upd":
        if updating():
            note = "Уже обновляю — дождись итога"
        else:
            note = "Обновляю…"
            progress("Скачиваю свежий код из main…", kind="update")
            bg(chat, update_all, len(data) > 1)
    elif data[0] == "rmd":
        with lock:
            st = load()
            res = digest.callback(st, "|".join(data))
            save(st)
        if res:
            note = res[0]
            show(chat, mid, res[1], (res[2] or []) + [[b("‹ Назад", "more")]])
        else:
            note = STALE
    elif data[0] in ("all", "mk", "mkr", "fix", "rb", "del?", "del", "mkh", "fixh"):
        note = maker.on_callback(chat, mid, data)
        note = STALE if note is None else note
    elif data[0] == "add" and len(data) == 1:
        show(chat, mid, card("➕ <b>Какого бота поставить?</b>", ["Выбери — дальше пришлёшь токен от @BotFather "
                                                                 "или я создам бота сам через Секретаря."]),
             pairs([b(clip(INSTALL[k][1]), f"add|{k}") for k in INSTALL]) + [[b("‹ Назад", "more")]])
    elif data[0] == "add" and arg in INSTALL:
        ask_token(chat, mid, arg)
    elif data[0] == "stu":  # «🏗 Поставить Студию»: сам создам бота через Секретаря, иначе попрошу токен
        if studio_installed():
            note = "Студия уже стоит"
        elif maker.secretary_ready():
            setting("wait", None)
            note = "Ставлю…"
            bg(chat, botfather_install, "studio")
        else:
            ask_token(chat, mid, "studio")
    elif data[0] == "bf" and arg in INSTALL and arg in installing:
        note = "Уже создаю — подожди"
    elif data[0] == "bf" and arg in INSTALL:
        setting("wait", None)
        note = "Создаю…"
        bg(chat, botfather_install, arg)
    elif data[0] == "addt":
        token = setting("token_hint")
        setting("token_hint", None)
        if token and arg in INSTALL:
            bg(chat, install, arg, token)
        else:
            note = "Пришли токен ещё раз"
    else:
        note = STALE
    return note


COMMANDS = [("start", "Главная"), ("task", "Поручить Секретарю"), ("app", "Приложение"), ("mode", "Чат или приложение"), ("psy", "Психолог: разбор чатов"),
            ("bots", "Боты и сервер"), ("test", "Проверка всех ботов"), ("login", "Войти в аккаунт"),
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
    if owner():  # новая клавиатура-меню после обновления — один раз
        threading.Thread(target=ensure_keyboard, args=(owner(),), daemon=True).start()
    print("Главный запущен")
    fails_poll = 0
    while True:
        try:
            upd = tg("getUpdates", offset=offset, timeout=50, allowed_updates=["message", "callback_query"])
            fails_poll = 0
        except Exception as e:
            # 409 Conflict — старый опрос ещё доживает свои ≤50 с после перезапуска: ждём тихо, без шквала в логе
            conflict = getattr(e, "code", 0) == 409
            fails_poll = min(fails_poll + 1, 6)
            if not conflict or fails_poll == 1:
                print("getUpdates:", e)
            time.sleep(min(2 * fails_poll, 15) if conflict else min(5 * fails_poll, 60))
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
                chat = ((u.get("message") or {}).get("chat") or {}).get("id")
                if chat and allowed((u["message"].get("from") or {}).get("id")):
                    oops(chat, e)


if __name__ == "__main__":
    main()
