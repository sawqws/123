#!/usr/bin/env python3
"""Telegram-бот «Главный»: следит за остальными ботами на сервере.

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
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
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

APPS = {  # имя в pm2 -> подпись
    "horo-bot": "📚 HDP",
    "coach-bot": "⚡ Тренер «Форма»",
    "planner-bot": "🗓 Планер «Ритм»",
    "kpp-bot": "🚧 КПП",
    "avito-bot": "🛍 Авито",
    "tg-agent": "🤖 Секретарь",
    SELF: "👑 Главный",
}
INSTALL = {  # что умеем ставить по одному токену: папка, переменная с токеном, подпись, что дальше
    "coach": ("COACH_TOKEN", "⚡ Тренер WHOOP", "Открой @{name}, нажми /start, потом /connect — подключим WHOOP."),
    "planner": ("PLANNER_TOKEN", "🗓 Планер", "Открой @{name} и нажми /start — через минуту появится кнопка «Планер»."),
    "kpp": ("KPP_TOKEN", "🚧 КПП", "Открой @{name}, нажми /start и войди через Яндекс."),
    "avito": ("AVITO_TOKEN", "🛍 Авито", "Открой @{name}, нажми /start и пришли фото вещи."),
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


def setting(key, value=None):
    with lock:
        st = load()
        if value is None:
            return st.get(key)
        st[key] = value
        save(st)
        return value


def owner():
    return int(setting("owner") or 0) or (min(ALLOWED) if ALLOWED else 0)


def allowed(uid):
    return uid in ALLOWED if ALLOWED else uid == owner()


# ---------- Telegram ----------

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
        print(method, e.code, detail[:300])
        raise


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


def b(text, data):
    return {"text": text, "callback_data": data}


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


# ---------- экраны ----------

def home(chat, mid=None):
    rows = apps()
    if rows is None:
        return show(chat, mid, "⚠️ pm2 не отвечает. Попробуй через минуту.", [[b("🔄 Ещё раз", "home")]])
    up = sum(1 for a in rows if a["status"] == "online")
    lines = [f"👑 <b>Главный</b> · работает {up} из {len(rows)}\n"]
    for a in rows:
        ok = a["status"] == "online"
        lines.append(f"{'🟢' if ok else '🔴'} {esc(a['label'])} — "
                     + (f"{ago(a['uptime'])}, {a['mem']} МБ" if ok else f"<b>{esc(a['status'])}</b>"))
    btns = [[b(a["label"], f"app|{a['name']}")] for a in rows if a["name"] != SELF]
    btns.append([b("📂 Все боты", "all"), b("🛠 Сделать бота", "mk"), b("✏️ Доработать", "fix")])
    btns.append([b("⬆️ Обновить всех", "upd"), b("➕ Поставить", "add")])
    btns.append([b("🖥 Сервер", "srv"), b("🤖 Claude", "cl"), b("🔄", "home")])
    return show(chat, mid, "\n".join(lines), btns)


def app_card(chat, mid, name):
    a = next((x for x in apps() or [] if x["name"] == name), None)
    if not a:
        return show(chat, mid, f"Бота {esc(name)} нет в pm2.", [[b("‹ Назад", "home")]])
    ok = a["status"] == "online"
    text = (f"{esc(a['label'])}\n\n{'🟢 работает' if ok else '🔴 ' + esc(a['status'])}"
            + (f" {ago(a['uptime'])}" if ok else "")
            + f"\nПамять {a['mem']} МБ · CPU {a['cpu']}% · перезапусков {a['restarts']}")
    return show(chat, mid, text, [[b("🔄 Перезапустить", f"rs|{name}"), b("📜 Логи", f"lg|{name}")],
                                  [b("‹ Назад", "home")]])


def logs(chat, name):
    code, out = sh("pm2", "logs", name, "--nostream", "--lines", "30", "--raw", timeout=30)
    out = "\n".join(line for line in out.splitlines() if line.strip())[-3500:] or "пусто"
    send(chat, f"📜 <b>{esc(label(name))}</b>\n<pre>{esc(out)}</pre>",
         [[b("🔄 Перезапустить", f"rs|{name}"), b("‹ К ботам", "home")]])


def restart(chat, name):
    code, out = sh("pm2", "restart", name, timeout=60)
    if name == SELF:
        return
    time.sleep(4)
    a = next((x for x in apps() or [] if x["name"] == name), {})
    ok = code == 0 and a.get("status") == "online"
    send(chat, f"{'✅' if ok else '⚠️'} {esc(label(name))} — "
         + ("перезапущен" if ok else f"не поднялся: {esc(out.strip()[-300:] or a.get('status'))}"),
         [[b("📜 Логи", f"lg|{name}"), b("‹ К ботам", "home")]])


def server_card(chat, mid):
    s = server()
    lines = ["🖥 <b>Сервер</b>\n"]
    if "mem_total" in s:
        lines.append(f"Память: свободно {s['mem_free']} из {s['mem_total']} МБ")
    if "disk_total" in s:
        lines.append(f"Диск: свободно {s['disk_free']} из {s['disk_total']} ГБ")
    if "load" in s:
        lines.append(f"Нагрузка: {s['load']} · без перезагрузки {ago(s.get('uptime', 0))}")
    paid = setting("paid")
    if paid:
        left = (datetime.date.fromisoformat(paid) - datetime.datetime.now(TZ).date()).days
        lines.append(f"Оплачен до {fmt_date(paid)} ({left} дн)")
    else:
        lines.append("\nСрок оплаты не указан — пришли /paid ДД.ММ, напомню заранее.")
    return show(chat, mid, "\n".join(lines), [[b("‹ Назад", "home")]])


def fmt_date(iso):
    d = datetime.date.fromisoformat(iso)
    return f"{d.day:02d}.{d.month:02d}.{d.year}"


# ---------- обновление и установка ----------

def update_all(chat, reset=False):
    def git(*a):
        return sh("git", "-C", REPO, *a, timeout=120)
    _, old = git("rev-parse", "HEAD")
    code, out = git("fetch", "origin", "main")
    if code == 0:
        code, out = sync_main(git, reset)
    if code != 0:
        return send(chat, f"⚠️ Не скачал код:\n<pre>{esc(out[-800:])}</pre>",
                    [[b("🧹 Сбросить локальные правки и обновить", "upd|reset")]] if "rebase" in out else None)
    _, new = git("rev-parse", "HEAD")
    if old.strip() == new.strip():
        return send(chat, "✅ Уже последняя версия, перезапускать никого не нужно.", [[b("‹ К ботам", "home")]])
    _, changed = git("diff", "--name-only", old.strip(), new.strip())
    if "package.json" in changed or "package-lock.json" in changed:
        sh("npm", "install", "--no-audit", "--no-fund", "--prefix", REPO, timeout=600)
    if not os.path.exists(os.path.join(REPO, "common", ".venv", "bin", "python")):
        send(chat, "🎙 Ставлю распознавание голосовых для всех ботов — один раз, 2–5 минут…")
        code, out = sh("bash", os.path.join(REPO, "common", "setup.sh"), timeout=1800)
        if code != 0:
            send(chat, f"⚠️ Голосовые не поставились:\n<pre>{esc(out[-600:])}</pre>")
    names = [a["name"] for a in apps() or [] if a["name"] in APPS and a["name"] != SELF]
    res = []
    for n in names:
        code, _ = sh("pm2", "restart", n, timeout=60)
        res.append(("✅ " if code == 0 else "⚠️ ") + APPS[n])
    setting("updated_chat", chat)
    send(chat, "⬆️ <b>Обновил и перезапустил</b>\n" + "\n".join(esc(x) for x in res) + "\n\nПерезапускаюсь сам…")
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


def install(chat, kind, token, msg_id=None):
    var, label, after = INSTALL[kind]
    if msg_id:
        try:
            tg("deleteMessage", chat_id=chat, message_id=msg_id)  # токен не должен висеть в чате
        except Exception:
            pass
    if token == TOKEN:
        return send(chat, "🤔 Это токен Главного. Нужен токен нового бота от @BotFather.")
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getMe", timeout=30) as r:
            name = json.load(r)["result"].get("username") or kind
    except Exception:
        return send(chat, "❌ Токен не подошёл. Скопируй его из @BotFather целиком и пришли ещё раз.")
    send(chat, f"⏳ Ставлю {esc(label)} @{esc(name)}… Это 1–3 минуты.")
    env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin") + ":/usr/local/bin", "HOME": "/root",
           "LANG": "C.UTF-8", "DEBIAN_FRONTEND": "noninteractive", var: token}
    code, out = sh("bash", os.path.join(REPO, kind, "install.sh"), timeout=1200, env=env)
    if code != 0:
        return send(chat, f"❌ Не получилось:\n<pre>{esc(out.strip()[-1200:])}</pre>")
    send(chat, f"✅ <b>{esc(label)} запущен</b>\n\n" + esc(after.format(name=name)),
         [[{"text": f"Открыть @{name}", "url": f"https://t.me/{name}"}], [b("‹ К ботам", "home")]])


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
                        out.append((f"🔴 <b>{esc(a['label'])} не работает</b> ({esc(a['status'])})",
                                    [[b("🔄 Перезапустить", f"rs|{n}"), b("📜 Логи", f"lg|{n}")]]))
                else:
                    if s["alerted"]:
                        out.append((f"🟢 {esc(a['label'])} снова работает", None))
                    s["bad"], s["alerted"] = 0, False
                # перезапуски по кругу: +3 за 10 минут
                s["hist"] = [h for h in s["hist"] if now - h[0] < 600] + [[now, a["restarts"]]]
                if s["hist"][-1][1] - s["hist"][0][1] >= 3 and now - s.get("loop_at", 0) > 3600:
                    s["loop_at"] = now
                    out.append((f"🟠 <b>{esc(a['label'])} падает и перезапускается</b> — посмотри логи",
                                [[b("📜 Логи", f"lg|{n}"), b("🔄 Перезапустить", f"rs|{n}")]]))
        srv = server()
        day = datetime.datetime.fromtimestamp(now, TZ).date().isoformat()
        if srv.get("disk_total") and srv["disk_free"] / srv["disk_total"] < 0.1 and st.get("disk_day") != day:
            st["disk_day"] = day
            out.append((f"💽 На диске осталось {srv['disk_free']} ГБ — скоро боты не смогут писать данные.", None))
        if srv.get("mem_total") and srv["mem_free"] / srv["mem_total"] < 0.07 and now - st.get("mem_at", 0) > 6 * 3600:
            st["mem_at"] = now
            out.append((f"🧠 Свободно всего {srv['mem_free']} МБ памяти из {srv['mem_total']}.", None))
        paid = st.get("paid")
        hour = datetime.datetime.fromtimestamp(now, TZ).hour
        if paid and hour >= 10 and st.get("paid_day") != day:
            left = (datetime.date.fromisoformat(paid) - datetime.date.fromisoformat(day)).days
            if left in (7, 3, 1, 0) or left < 0:
                st["paid_day"] = day
                out.append((f"💳 Сервер оплачен до {fmt_date(paid)} — " + (
                    f"осталось {left} дн. Продли, иначе все боты выключатся." if left > 0 else
                    "срок сегодня! Продли сейчас." if left == 0 else "срок прошёл! Продли и пришли новый /paid ДД.ММ."), None))
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

    def reply(self, code, body):
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            return self.reply(200, "ok")
        self.reply(404, {"error": "not found"})

    def do_POST(self):
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
    lines = ["🤖 <b>Вход для Claude</b>\n",
             "Claude из облачной сессии сам смотрит ботов и логи, перезапускает, обновляет и выполняет "
             "команды на сервере. О каждой команде я пишу сюда.\n",
             "🟢 Включён" if on else "⚪️ Выключен"]
    if url:
        lines.append(f"Адрес: <code>{esc(url)}</code>")
    btns = [[b("⛔ Выключить", "cl|off")] if on else [b("🔑 Включить", "cl|on")]]
    if on:
        btns.append([b("🔑 Показать ключ", "cl|show"), b("♻️ Новый ключ", "cl|new")])
    btns.append([b("‹ Назад", "home")])
    return show(chat, mid, "\n".join(lines), btns)


def show_key(chat):
    send(chat, "🔑 <b>Ключ для Claude</b>\n\nДобавь две переменные в настройки облачной среды Claude "
               "(меню среды в заголовке сессии → Edit → переменные окружения) и начни новую сессию:\n\n"
               f"<code>HUB_URL={esc(hub_url())}</code>\n<code>HUB_KEY={esc(setting('key'))}</code>\n\n"
               "Потом удали это сообщение. Выключить вход — «⛔ Выключить» в /claude.")


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


HELP = ("👑 <b>Главный</b> — следит за ботами на сервере.\n\n"
        "• Сам напишет, если бот упал или перезапускается по кругу, и когда заработал снова.\n"
        "• Кнопки: перезапуск, логи, «Обновить всех», «Поставить» нового бота по токену.\n"
        "• /paid 07.11 — срок оплаты сервера, напомню за 7, 3 и 1 день.\n\n"
        "• /claude — вход для Claude: сам смотрит логи и чинит ботов, о каждой команде пишу сюда.\n"
        "• 🛠 /make — сделаю нового бота по ТЗ (код, @BotFather, запуск), ✏️ /fix — доработаю любого бота.\n"
        "• 📂 /all — все боты со ссылками.\n"
        + digest.HELP_LINES + "\n"
        "/bots — боты · /server — сервер · /update — обновить всех")


def on_message(msg):
    chat = msg["chat"]["id"]
    uid = (msg.get("from") or {}).get("id")
    text = (msg.get("text") or "").strip()
    if msg["chat"].get("type") != "private":
        return
    if text.startswith("/start") and not ALLOWED and not setting("owner"):
        setting("owner", uid)
        print("Владелец:", uid)
    if not allowed(uid):
        return send(chat, "🔒 Это личный бот.")
    kind = media.kind(msg)
    if kind == "voice":
        return threading.Thread(target=hear, args=(msg,), daemon=True).start()
    if kind == "photo":
        return on_photo(msg)
    cmd = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
    wait = setting("wait")
    if cmd in ("/start", "/bots", "/menu"):
        setting("wait", None)
        if cmd == "/start":
            send(chat, HELP)
        home(chat)
    elif cmd == "/help":
        send(chat, HELP)
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
        send(chat, *res) if res else send(chat, HELP)
    elif not cmd and wait and maker.on_text(chat, wait, text, msg):
        pass
    elif cmd == "/paid":
        iso = parse_paid(text)
        if not iso:
            return send(chat, "Пришли так: <code>/paid 07.11</code>")
        setting("paid", iso)
        with lock:
            st = load()
            st.pop("paid_day", None)
            save(st)
        send(chat, f"💳 Запомнил: сервер оплачен до {fmt_date(iso)}. Напомню за 7, 3 и 1 день.")
    elif cmd in {"/" + k for k in INSTALL} or (wait and BOT_TOKEN.search(text)):
        kind = cmd[1:] if cmd else wait
        m = BOT_TOKEN.search(text)
        if not m:
            setting("wait", kind)
            return send(chat, f"Пришли токен бота от @BotFather — поставлю {esc(INSTALL[kind][1])}.",
                        [[b("Отмена", "home")]])
        setting("wait", None)
        threading.Thread(target=install, args=(chat, kind, m.group(0), msg.get("message_id")), daemon=True).start()
    elif BOT_TOKEN.search(text):
        try:
            tg("deleteMessage", chat_id=chat, message_id=msg["message_id"])
        except Exception:
            pass
        setting("token_hint", BOT_TOKEN.search(text).group(0))
        send(chat, "🔑 Вижу токен (сообщение удалил). Какого бота поставить?",
             [[b(INSTALL[k][1], f"addt|{k}")] for k in INSTALL])
    elif text and not cmd:
        maker.ask_what(chat, text)
    else:
        home(chat)


def hear(msg):
    """Голосовое → текст → дальше как обычное сообщение."""
    chat = msg["chat"]["id"]
    tg("sendChatAction", chat_id=chat, action="typing")
    try:
        text = media.voice_text(TOKEN, msg)
    except media.MediaError as e:
        return send(chat, f"🎙 {esc(e)}")
    except Exception as e:
        return send(chat, f"🎙 Не получилось: {esc(e)}")
    send(chat, f"🎙 <i>«{esc(text)}»</i>")
    rest = {k: v for k, v in msg.items() if k not in ("voice", "audio", "video_note", "document")}
    on_message({**rest, "text": text})


def on_photo(msg):
    """Фото и скриншоты прикладываются к ТЗ (Claude их посмотрит). Подпись — сам текст ТЗ."""
    chat = msg["chat"]["id"]
    try:
        path = media.photo_path(TOKEN, msg, os.path.join(DATA, "shots"))
    except Exception as e:
        return send(chat, f"😕 Не скачал фото: {esc(e)}")
    with lock:
        st = load()
        st["shots"] = (st.get("shots") or [])[-9:] + [path]
        save(st)
    cap = (msg.get("caption") or "").strip()
    if cap:
        rest = {k: v for k, v in msg.items() if k not in ("photo", "document", "caption")}
        return on_message({**rest, "text": cap})
    if not msg.get("media_group_id") or len(setting("shots")) == 1:
        send(chat, "📎 Фото приложу к задаче — теперь напиши или надиктуй, что сделать.")


def on_callback(cq):
    uid = cq["from"]["id"]
    msg = cq.get("message") or {}
    chat, mid = msg.get("chat", {}).get("id"), msg.get("message_id")
    data = (cq.get("data") or "").split("|")
    note = ""
    if not allowed(uid):
        note = "Нет доступа"
    elif data[0] == "home":
        setting("wait", None)
        home(chat, mid)
    elif data[0] == "app":
        app_card(chat, mid, data[1])
    elif data[0] == "rs":
        note = "Перезапускаю…"
        threading.Thread(target=restart, args=(chat, data[1]), daemon=True).start()
    elif data[0] == "lg":
        threading.Thread(target=logs, args=(chat, data[1]), daemon=True).start()
    elif data[0] == "srv":
        server_card(chat, mid)
    elif data[0] == "cl":
        on_claude(chat, mid, data[1] if len(data) > 1 else "")
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
            show(chat, mid, res[1], res[2])
    elif data[0] in ("all", "mk", "mkr", "fix", "rb", "del?", "del", "mkh", "fixh"):
        note = maker.on_callback(chat, mid, data) or ""
    elif data[0] == "add" and len(data) == 1:
        show(chat, mid, "➕ <b>Какого бота поставить?</b>\n\nСначала создай бота в @BotFather и скопируй токен.",
             [[b(INSTALL[k][1], f"add|{k}")] for k in INSTALL] + [[b("‹ Назад", "home")]])
    elif data[0] == "add":
        setting("wait", data[1])
        show(chat, mid, f"Пришли токен бота от @BotFather — поставлю {esc(INSTALL[data[1]][1])}.", [[b("Отмена", "home")]])
    elif data[0] == "addt":
        token = setting("token_hint")
        setting("token_hint", None)
        if token:
            threading.Thread(target=install, args=(chat, data[1], token), daemon=True).start()
        else:
            note = "Пришли токен ещё раз"
    try:
        tg("answerCallbackQuery", callback_query_id=cq["id"], text=note)
    except Exception:
        pass


def main():
    global offset
    if not TOKEN:
        sys.exit("Нет HUB_TOKEN (токен от @BotFather). Впиши его в /etc/hub-bot.env")
    try:
        tg("setMyCommands", commands=[{"command": "bots", "description": "Мои боты"},
                                      {"command": "server", "description": "Сервер"},
                                      {"command": "update", "description": "Обновить всех"},
                                      {"command": "all", "description": "Все боты"},
                                      {"command": "make", "description": "Сделать бота"},
                                      {"command": "fix", "description": "Доработать бота"},
                                      {"command": "reminders", "description": "Напоминания"},
                                      {"command": "claude", "description": "Вход для Claude"},
                                      {"command": "help", "description": "Что я умею"}])
    except Exception as e:
        print("setup:", e)
    chat = setting("updated_chat")
    if chat:
        setting("updated_chat", None)
        try:
            send(chat, "👑 Снова на связи.")
            home(chat)
        except Exception as e:
            print("notify:", e)
    bootstrap_remote()
    if owner() and setting("intro") != 1:  # один раз: список всех ботов и как собрать их в папку
        setting("intro", 1)
        threading.Thread(target=maker.show_all, args=(owner(), None, True), daemon=True).start()
    threading.Thread(target=watch, daemon=True).start()
    threading.Thread(target=serve, daemon=True).start()
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
