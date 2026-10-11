#!/usr/bin/env python3
"""Telegram-бот «Главный»: следит за остальными ботами на сервере.

- список ботов с состоянием (pm2), перезапуск и последние строки логов одной кнопкой;
- сам пишет, если бот упал или постоянно перезапускается, и когда он снова заработал;
- «Обновить всех»: свежий код из main и перезапуск;
- «Поставить бота»: тренер, планер, КПП — по токену от @BotFather;
- сервер: память, диск, нагрузка; предупреждает, если кончается место или подходит срок оплаты (/paid ДД.ММ).

Переменные окружения (на сервере — /etc/hub-bot.env):
  HUB_TOKEN    токен от @BotFather
  HUB_ALLOWED  Telegram ID владельца; если пусто — владелец тот, кто первым нажал /start

Только стандартная библиотека Python.
"""
import datetime
import html
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DATA = os.path.join(HERE, "data")
STATE = os.path.join(DATA, "state.json")
TOKEN = os.environ.get("HUB_TOKEN", "")
API = f"https://api.telegram.org/bot{TOKEN}/"
ALLOWED = {int(x) for x in re.findall(r"\d+", os.environ.get("HUB_ALLOWED", ""))}
SELF = "hub-bot"
BOT_TOKEN = re.compile(r"\d{6,12}:[A-Za-z0-9_-]{30,}")

APPS = {  # имя в pm2 -> подпись
    "horo-bot": "📚 HDP",
    "coach-bot": "⚡ Тренер «Форма»",
    "planner-bot": "🗓 Планер «Ритм»",
    "kpp-bot": "🚧 КПП",
    "tg-agent": "🤖 Секретарь",
    SELF: "👑 Главный",
}
INSTALL = {  # что умеем ставить по одному токену: папка, переменная с токеном, подпись, что дальше
    "coach": ("COACH_TOKEN", "⚡ Тренер WHOOP", "Открой @{name}, нажми /start, потом /connect — подключим WHOOP."),
    "planner": ("PLANNER_TOKEN", "🗓 Планер", "Открой @{name} и нажми /start — через минуту появится кнопка «Планер»."),
    "kpp": ("KPP_TOKEN", "🚧 КПП", "Открой @{name}, нажми /start и войди через Яндекс."),
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
            "name": r.get("name"), "label": APPS.get(r.get("name"), "🔹 " + str(r.get("name"))),
            "status": env.get("status") or "?", "restarts": env.get("restart_time") or 0,
            "uptime": max(0, time.time() - up / 1000) if up and env.get("status") == "online" else 0,
            "mem": round((mon.get("memory") or 0) / 1048576), "cpu": mon.get("cpu") or 0,
        })
    order = list(APPS)
    return sorted(res, key=lambda a: order.index(a["name"]) if a["name"] in order else 99)


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
    btns.append([b("⬆️ Обновить всех", "upd"), b("➕ Поставить", "add")])
    btns.append([b("🖥 Сервер", "srv"), b("🔄", "home")])
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
    send(chat, f"📜 <b>{esc(APPS.get(name, name))}</b>\n<pre>{esc(out)}</pre>",
         [[b("🔄 Перезапустить", f"rs|{name}"), b("‹ К ботам", "home")]])


def restart(chat, name):
    code, out = sh("pm2", "restart", name, timeout=60)
    if name == SELF:
        return
    time.sleep(4)
    a = next((x for x in apps() or [] if x["name"] == name), {})
    ok = code == 0 and a.get("status") == "online"
    send(chat, f"{'✅' if ok else '⚠️'} {esc(APPS.get(name, name))} — "
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

def update_all(chat):
    def git(*a):
        return sh("git", "-C", REPO, *a, timeout=120)
    _, old = git("rev-parse", "HEAD")
    code, out = git("fetch", "origin", "main")
    if code == 0:
        code, out = git("checkout", "-B", "main", "origin/main")
    if code != 0:
        return send(chat, f"⚠️ Не скачал код:\n<pre>{esc(out[-800:])}</pre>")
    _, new = git("rev-parse", "HEAD")
    if old.strip() == new.strip():
        return send(chat, "✅ Уже последняя версия, перезапускать никого не нужно.", [[b("‹ К ботам", "home")]])
    _, changed = git("diff", "--name-only", old.strip(), new.strip())
    if "package.json" in changed or "package-lock.json" in changed:
        sh("npm", "install", "--no-audit", "--no-fund", "--prefix", REPO, timeout=600)
    names = [a["name"] for a in apps() or [] if a["name"] in APPS and a["name"] != SELF]
    res = []
    for n in names:
        code, _ = sh("pm2", "restart", n, timeout=60)
        res.append(("✅ " if code == 0 else "⚠️ ") + APPS[n])
    setting("updated_chat", chat)
    send(chat, "⬆️ <b>Обновил и перезапустил</b>\n" + "\n".join(esc(x) for x in res) + "\n\nПерезапускаюсь сам…")
    tg("getUpdates", offset=offset, timeout=0)
    os._exit(0)  # pm2 поднимет заново


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
    elif cmd == "/update":
        threading.Thread(target=update_all, args=(chat,), daemon=True).start()
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
    else:
        home(chat)


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
    elif data[0] == "upd":
        note = "Обновляю…"
        threading.Thread(target=update_all, args=(chat,), daemon=True).start()
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
    threading.Thread(target=watch, daemon=True).start()
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
