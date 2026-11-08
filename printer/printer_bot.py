#!/usr/bin/env python3
"""«Принтер» — личный бот 3D-принтера: статус, запуск, пауза, отмена, файлы, остывание, уведомления.

Принтер дома за роутером, бот на сервере. Связь — обратный SSH-туннель, который поднимает сам принтер
(printer/tunnel/): порт 17125 на сервере ведёт в Moonraker, порт 17999 — в штатный websocket экрана.

Окружение (/etc/printer-bot.env): PRINTER_TOKEN, BOT_OWNER, PRINTER_URL (http://127.0.0.1:17125),
PRINTER_WS_PORT (17999), PRINTER_START (moonraker | screen — чем запускать печать), PRINTER_TZ (часов от UTC).
Тесты без сети: python3 printer/test_printer.py.
"""
import base64
import datetime
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "studio", "kit"))
import botkit as kit  # noqa: E402

URL = (os.environ.get("PRINTER_URL") or "http://127.0.0.1:17125").rstrip("/")
WS_PORT = int(os.environ.get("PRINTER_WS_PORT") or 17999)
START_VIA = os.environ.get("PRINTER_START") or "moonraker"
TZ = datetime.timezone(datetime.timedelta(hours=float(os.environ.get("PRINTER_TZ") or 3)))
GCODES = "/usr/data/printer_data/gcodes"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
ACTIVE = ("printing", "paused")
OFF_ALERT = 180    # секунд без связи во время печати до сообщения
HEAL_AFTER = 120   # секунд без связи до сброса зависшего туннеля
HEAL_EVERY = 300
PAGE = 6
TG_FILE_LIMIT = 20 * 1024 * 1024  # больше Telegram не отдаёт ботам
STATES = {"printing": "🟢 Печатает", "paused": "⏸ На паузе", "complete": "✅ Готово", "cancelled": "⛔ Отменена",
          "error": "⚠️ Ошибка", "standby": "⚪️ Свободен"}


class Offline(Exception):
    """Принтер не отвечает: нет туннеля, сети или питания."""


class Moon:
    """Moonraker через туннель. Ошибка самого Moonraker — kit.Fail с его текстом, нет связи — Offline."""

    def __init__(self, base=URL):
        self.base = base

    def request(self, method, path, body=None, headers=None, timeout=15):
        req = urllib.request.Request(self.base + path, data=body, method=method, headers=headers or {})
        try:
            with OPENER.open(req, timeout=timeout) as r:
                return json.load(r).get("result")
        except urllib.error.HTTPError as e:
            raise kit.Fail(self._reason(e)) from None
        except (OSError, ValueError) as e:
            raise Offline(str(e)) from None

    @staticmethod
    def _reason(e):
        try:
            return str(json.load(e)["error"]["message"])[:200]
        except Exception:
            return f"Принтер ответил ошибкой {e.code}"

    def status(self):
        query = ("print_stats&virtual_sdcard=progress,layer,layer_count&display_status=progress&webhooks"
                 "&extruder=temperature,target&heater_bed=temperature,target")
        return self.request("GET", "/printer/objects/query?" + query, timeout=6)["status"]

    def files(self):
        out = self.request("GET", "/server/files/list?root=gcodes") or []
        return sorted(out, key=lambda f: f.get("modified") or 0, reverse=True)

    def meta(self, name):
        try:
            return self.request("GET", "/server/files/metadata?filename=" + urllib.parse.quote(name)) or {}
        except kit.Fail:
            return {}

    def start(self, name):
        self.request("POST", "/printer/print/start?filename=" + urllib.parse.quote(name), timeout=30)

    def pause(self):
        self.request("POST", "/printer/print/pause", timeout=45)

    def resume(self):
        self.request("POST", "/printer/print/resume", timeout=45)

    def cancel(self):
        self.request("POST", "/printer/print/cancel", timeout=45)

    def gcode(self, script):
        self.request("POST", "/printer/gcode/script?script=" + urllib.parse.quote(script), timeout=45)

    def delete(self, name):
        self.request("DELETE", "/server/files/gcodes/" + urllib.parse.quote(name))

    def upload(self, name, data):
        boundary = uuid.uuid4().hex
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
        self.request("POST", "/server/files/upload", body,
                     {"Content-Type": f"multipart/form-data; boundary={boundary}"}, timeout=300)


moon = Moon()


def ws_frame(text):
    """Текстовый кадр websocket от клиента (с маской)."""
    data = text.encode()
    mask = os.urandom(4)
    head = bytes([0x81])
    if len(data) < 126:
        head += bytes([0x80 | len(data)])
    else:
        head += bytes([0x80 | 126]) + len(data).to_bytes(2, "big")
    return head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data))


def ws_send(payload, port=WS_PORT, host="127.0.0.1"):
    """Команда штатному websocket экрана: так печать запускает родное приложение Creality."""
    try:
        with socket.create_connection((host, port), timeout=8) as s:
            key = base64.b64encode(os.urandom(16)).decode()
            s.sendall((f"GET / HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                       f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
            answer = b""
            while b"\r\n\r\n" not in answer:
                chunk = s.recv(1024)
                if not chunk:
                    raise OSError("обрыв при рукопожатии")
                answer += chunk
            if b" 101 " not in answer.split(b"\r\n")[0]:
                raise OSError("экран не принял соединение")
            s.sendall(ws_frame(json.dumps(payload)))
            time.sleep(0.3)
    except OSError as e:
        raise Offline(str(e)) from None


def start_print(name):
    if START_VIA == "screen":
        ws_send({"method": "set", "params": {"opGcodeFile": f"printprt:{GCODES}/{name}"}})
    else:
        moon.start(name)


# ---------- что сейчас с принтером ----------

def snapshot():
    """online, state, file, st (объекты Klipper), meta (данные файла), klippy (текст, если Klipper не готов)."""
    try:
        st = moon.status()
    except Offline:
        return {"online": False}
    except kit.Fail as e:
        return {"online": True, "klippy": str(e)}
    hooks = st.get("webhooks") or {}
    if hooks.get("state") not in (None, "ready"):
        return {"online": True, "klippy": hooks.get("state_message") or hooks.get("state"), "st": st}
    stats = st.get("print_stats") or {}
    name = stats.get("filename") or ""
    return {"online": True, "st": st, "state": stats.get("state") or "standby", "file": name,
            "meta": moon.meta(name) if name else {}}


def layers(st):
    sd, info = st.get("virtual_sdcard") or {}, (st.get("print_stats") or {}).get("info") or {}
    return (info.get("current_layer") or sd.get("layer") or 0), (info.get("total_layer") or sd.get("layer_count") or 0)


def progress(st):
    """Доля готового: M73 из файла идёт по времени и точнее, чем позиция в файле."""
    return (st.get("display_status") or {}).get("progress") or (st.get("virtual_sdcard") or {}).get("progress") or 0


def remaining(st, meta):
    """Сколько секунд осталось: сначала по оценке нарезки, потом по реальной скорости."""
    done = (st.get("print_stats") or {}).get("print_duration") or 0
    p = progress(st)
    total = (meta or {}).get("estimated_time") or 0
    if total and p < 0.1:
        return total * (1 - p)
    if p > 0:
        return done * (1 - p) / p
    return None


def hm(sec):
    sec = int(sec or 0)
    h, m = sec // 3600, sec % 3600 // 60
    if h:
        return f"{h} ч {m} мин"
    return f"{m} мин" if m else "меньше минуты"


def local_now(ts=None):
    return datetime.datetime.fromtimestamp(time.time() if ts is None else ts, TZ)


def finish_at(sec, now=None):
    now = now or local_now()
    end = now + datetime.timedelta(seconds=sec)
    days = (end.date() - now.date()).days
    day = "" if days == 0 else "завтра " if days == 1 else end.strftime("%d.%m ")
    return f"{day}{end.strftime('%H:%M')}"


def temps(st):
    noz, bed = st.get("extruder") or {}, st.get("heater_bed") or {}
    return f"🌡 Сопло {noz.get('temperature', 0):.0f}°, стол {bed.get('temperature', 0):.0f}°"


def is_hot(st):
    noz, bed = st.get("extruder") or {}, st.get("heater_bed") or {}
    return (noz.get("temperature", 0) > 50 or bed.get("temperature", 0) > 40
            or noz.get("target", 0) > 0 or bed.get("target", 0) > 0)


def status_lines(snap):
    st, state, meta = snap["st"], snap["state"], snap.get("meta") or {}
    stats = st.get("print_stats") or {}
    label = STATES.get(state, kit.esc(state))
    layer, total = layers(st)
    lines = []
    if state in ACTIVE:
        lines.append(f"{label} · {progress(st) * 100:.0f} %")
    else:
        lines.append(label)
    if snap.get("file"):
        lines.append(f"📄 {kit.esc(snap['file'])}")
    if state == "error" and stats.get("message"):
        lines.append(kit.esc(stats["message"])[:300])
    if state in ACTIVE:
        if state == "printing" and not layer:
            lines.append("🔥 Готовится: нагрев и карта стола")
        elif total:
            lines.append(f"📚 Слой {layer} из {total}")
        left = remaining(st, meta)
        done = hm(stats.get("print_duration"))
        lines.append(f"⏱ Прошло {done}" + (f", осталось {hm(left)}" if left is not None else ""))
        if state == "printing" and left is not None:
            lines.append(f"🏁 Конец около {finish_at(left)}")
    elif state == "complete" and stats.get("print_duration"):
        lines.append(f"⏱ Заняло {hm(stats['print_duration'])}")
    lines.append(temps(st))
    return lines


# ---------- бот ----------

class PrinterBot(kit.Bot):
    def _message(self, msg):
        uid = (msg.get("from") or {}).get("id")
        if msg.get("document") and msg["chat"].get("type") == "private" and self.allowed(uid):
            c = kit.Ctx(self, msg["chat"]["id"], msg.get("from"), msg=msg)
            return self.later(got_document, c)
        return super()._message(msg)


bot = PrinterBot("Принтер", emoji="🖨", about="Твой 3D-принтер в Telegram: сколько осталось, пауза, отмена, запуск файлов, "
                                         "сообщения о готовности и сбоях.",
                 short="3D-принтер в Telegram",
                 commands=[("start", "Статус"), ("files", "Файлы"), ("help", "Что умею")],
                 token=os.environ.get("PRINTER_TOKEN", ""),
                 support={"name": "Принтер", "folders": ["printer"], "pm2": "printer-bot", "cwd": REPO,
                          "tests": ["python3 printer/test_printer.py"]},
                 kb=[[kit.b("🖨 Статус", "home"), kit.b("📁 Файлы", "files")], [kit.KB_SUPPORT]],
                 kb_version=1, kb_placeholder="Пришли G-code или ☰ справа")

OFFLINE_LINES = ["🔴 Нет связи с принтером", "Он выключен или без интернета. Если включён, туннель поднимется сам за минуту."]


def home_buttons(snap):
    if not snap.get("online"):
        return [[kit.b("🔄 Обновить", "home|r")]]
    state = snap.get("state")
    if state == "printing":
        return [[kit.b("🔄 Обновить", "home|r"), kit.b("⏸ Пауза", "pause"), kit.b("⛔ Отмена", "cancel")],
                [kit.b("📁 Файлы", "files")]]
    if state == "paused":
        return [[kit.b("▶️ Продолжить", "resume", "success"), kit.b("⛔ Отмена", "cancel")],
                [kit.b("🔄 Обновить", "home|r")]]
    row = [kit.b("📁 Файлы", "files", "primary")]
    if snap.get("st") and is_hot(snap["st"]):
        row.append(kit.b("🧊 Остудить", "cool"))
    return [row, [kit.b("🔄 Обновить", "home|r")]]


@bot.home
def home(c):
    snap = snapshot()
    title = "🖨 <b>Принтер</b>"
    foot = f"<i>Обновлено в {local_now().strftime('%H:%M:%S')}</i>"
    if not snap["online"]:
        lines = OFFLINE_LINES
    elif snap.get("klippy"):
        lines = [f"⚠️ {kit.esc(snap['klippy'])[:300]}"]
    else:
        lines = status_lines(snap)
    bot.menu(c, title, lines, home_buttons(snap), foot=foot)


@bot.command("help")
def help_(c):
    c.say(kit.card("🖨 <b>Что умею</b>", [
        "Показываю, сколько печатать осталось и когда конец.",
        "Пауза, отмена, запуск файлов с принтера, остывание.",
        "Пишу сам: старт, первый слой, готово, ошибка, пропала связь.",
        "Пришли .gcode до 20 МБ — положу на принтер.",
        "/gcode G28 — любая команда принтеру."]), [[kit.b("‹ Главное", "home")]])


def need(snap):
    if not snap["online"]:
        raise kit.Fail("Нет связи с принтером")
    if snap.get("klippy"):
        raise kit.Fail(snap["klippy"])
    return snap


@bot.button("pause")
def pause(c):
    if need(snapshot()).get("state") != "printing":
        c.toast("Сейчас не печатает")
        return home(c)
    c.ack("Ставлю на паузу")
    moon.pause()
    home(c)


@bot.button("resume")
def resume(c):
    if need(snapshot()).get("state") != "paused":
        c.toast("Сейчас не на паузе")
        return home(c)
    c.ack("Продолжаю")
    moon.resume()
    home(c)


@bot.button("cancel")
def cancel(c):
    snap = need(snapshot())
    if snap.get("state") not in ACTIVE:
        c.toast("Сейчас нечего отменять")
        return home(c)
    layer, total = layers(snap["st"])
    c.show(kit.card("⛔ <b>Отменить печать?</b>", [f"📄 {kit.esc(snap['file'])}", f"📚 Слой {layer} из {total}",
                                                  "Продолжить потом будет нельзя."]),
           [[kit.b("⛔ Да, отменить", "cancelok", "danger"), kit.b("‹ Нет", "home")]])


@bot.button("cancelok")
def cancel_ok(c):
    if need(snapshot()).get("state") not in ACTIVE:
        c.toast("Уже не печатает")
        return home(c)
    c.ack("Отменяю")
    moon.cancel()
    home(c)


@bot.button("cool")
def cool(c):
    snap = need(snapshot())
    if snap.get("state") in ACTIVE:
        c.toast("Идёт печать, сначала отмени её", alert=True)
        return home(c)
    moon.gcode("TURN_OFF_HEATERS\nM106 S255")
    bot.db.set("cooling", True)
    c.toast("Нагрев выключен, обдув включён. Напишу, когда остынет", alert=True)
    home(c)


@bot.command("gcode")
def gcode(c):
    if not c.text:
        return c.say(kit.card("⌨️ <b>Команда принтеру</b>", ["Пример: /gcode G28"]))
    need(snapshot())
    moon.gcode(c.text)
    c.say(kit.card("⌨️ <b>Отправил</b>", [kit.esc(c.text)[:300]]), [[kit.b("‹ Главное", "home")]])


# ---------- файлы ----------

def fid(name):
    return hashlib.sha1(name.encode()).hexdigest()[:12]


def find(file_id):
    for f in moon.files():
        if fid(f["path"]) == file_id:
            return f
    raise kit.Fail("Этого файла на принтере уже нет")


@bot.command("files")
@bot.button("files")
def files(c):
    page = int(c.args[0]) if c.args and c.args[0].isdigit() else 0
    try:
        items = moon.files()
    except Offline:
        return c.show(kit.card("📁 <b>Файлы</b>", OFFLINE_LINES), [[kit.b("‹ Главное", "home")]])
    chunk = items[page * PAGE:(page + 1) * PAGE]
    rows = [[kit.b(f["path"], f"f|{fid(f['path'])}")] for f in chunk]
    nav = [kit.b("‹", f"files|{page - 1}") if page else None,
           kit.b("›", f"files|{page + 1}") if (page + 1) * PAGE < len(items) else None]
    rows += [[x for x in nav if x], [kit.b("‹ Главное", "home")]]
    lines = [f"Всего {kit.plural(len(items), 'файл', 'файла', 'файлов')}, новые сверху."] if items else ["Пока пусто."]
    c.show(kit.card("📁 <b>Файлы на принтере</b>", lines, "<i>Новый файл: просто пришли .gcode сюда</i>"),
           [r for r in rows if r])


def file_lines(f, meta):
    lines = [f"📄 {kit.esc(f['path'])}"]
    if meta.get("estimated_time"):
        lines.append(f"⏱ {hm(meta['estimated_time'])}")
    if meta.get("filament_weight_total"):
        lines.append(f"🧵 {meta['filament_weight_total']:.0f} г")
    if meta.get("layer_count"):
        lines.append(f"📚 {kit.plural(meta['layer_count'], 'слой', 'слоя', 'слоёв')}")
    lines.append(f"📦 {(f.get('size') or 0) / 1048576:.1f} МБ, загружен {local_now(f.get('modified') or 0).strftime('%d.%m %H:%M')}")
    return lines


def show_file(c, f):
    file_id = fid(f["path"])
    c.show(kit.card("📄 <b>Файл</b>", file_lines(f, moon.meta(f["path"]))),
           [[kit.b("🖨 Печатать", f"go|{file_id}", "success"), kit.b("🗑 Удалить", f"del|{file_id}")],
            [kit.b("‹ Файлы", "files")]])


@bot.button("f")
def file_card(c):
    show_file(c, find(c.args[0]))


@bot.button("go")
def go(c):
    f = find(c.args[0])
    if need(snapshot()).get("state") in ACTIVE:
        c.toast("Принтер занят: сначала дождись конца или отмени", alert=True)
        return show_file(c, f)
    c.show(kit.card("🧹 <b>Стол чистый?</b>", [f"📄 {kit.esc(f['path'])}", "На столе ничего не должно остаться."]),
           [[kit.b("✅ Чистый, печатать", f"print|{c.args[0]}", "success")], [kit.b("‹ Назад", f"f|{c.args[0]}")]])


@bot.button("print")
def print_file(c):
    f = find(c.args[0])
    if need(snapshot()).get("state") in ACTIVE:
        c.toast("Принтер уже занят", alert=True)
        return home(c)
    c.ack("Запускаю")
    start_print(f["path"])
    bot.db.delete("cooling")
    time.sleep(0 if bot.testing_mode else 2)
    home(c)


@bot.button("del")
def delete_ask(c):
    f = find(c.args[0])
    c.show(kit.card("🗑 <b>Удалить файл?</b>", [f"📄 {kit.esc(f['path'])}"]),
           [[kit.b("🗑 Да, удалить", f"delok|{c.args[0]}", "danger"), kit.b("‹ Нет", f"f|{c.args[0]}")]])


@bot.button("delok")
def delete_ok(c):
    f = find(c.args[0])
    snap = need(snapshot())
    if snap.get("state") in ACTIVE and snap.get("file") == f["path"]:
        c.toast("Этот файл сейчас печатается", alert=True)
        return show_file(c, f)
    moon.delete(f["path"])
    c.toast("Удалил")
    c.args = []
    files(c)


# ---------- присланные файлы ----------

def safe_name(name):
    base = os.path.basename((name or "").replace("\\", "/"))
    base = re.sub(r"[^\w.\- ]", "_", base).strip(" .")
    return base or "model.gcode"


def tg_download(file_id):
    path = bot.call("getFile", file_id=file_id)["result"]["file_path"]
    url = f"https://api.telegram.org/file/bot{bot.token}/{path}"
    with OPENER.open(url, timeout=120) as r:
        return r.read()


def got_document(c):
    doc = c.msg["document"]
    name = safe_name(doc.get("file_name"))
    low = name.lower()
    if not low.endswith((".gcode", ".gco")):
        return c.say(kit.error("Пока принимаю только .gcode. Нарезать STL можно в чате с Claude на компьютере."))
    if (doc.get("file_size") or 0) > TG_FILE_LIMIT:
        return c.say(kit.error("Telegram отдаёт ботам файлы только до 20 МБ. Этот больше, его положу с компьютера."))
    c.typing()
    moon.upload(name, tg_download(doc["file_id"]))
    show_file(c, find(fid(name)))


# ---------- сам пишет ----------

def events(prev, snap, ts):
    """Что сообщить при переходе из состояния prev в snap. Возвращает ([(текст, кнопки)], новое состояние)."""
    out, new = [], dict(prev)
    open_home = [[kit.b("🖨 Статус", "home")]]
    if not snap["online"]:
        new["off"] = prev.get("off") or ts
        if prev.get("state") in ACTIVE and ts - new["off"] >= OFF_ALERT and not prev.get("told"):
            out.append((kit.card("🔴 <b>Принтер пропал со связи</b>",
                                 [f"📄 {kit.esc(prev.get('file') or '')}", "Нет питания или интернета. Печать могла остановиться."]),
                        open_home))
            new["told"] = True
        return out, new
    if prev.get("told"):
        out.append((kit.card("🟢 <b>Принтер снова на связи</b>"), open_home))
    new.update(off=None, told=False)
    state = snap.get("state")
    if not state:
        return out, new
    was, file = prev.get("state"), snap.get("file") or ""
    new.update(state=state, file=file)
    st = snap["st"]
    layer, _ = layers(st)
    name = f"📄 {kit.esc(file)}"
    if was is None:
        new["first"] = layer >= 2
        return out, new
    if state == "printing" and (was != "printing" or file != prev.get("file")):
        if was == "paused":
            out.append((kit.card("▶️ <b>Печать продолжается</b>", [name]), open_home))
        else:
            est = (snap.get("meta") or {}).get("estimated_time")
            out.append((kit.card("🖨 <b>Печать пошла</b>", [name, f"⏱ {hm(est)}" if est else None]), open_home))
            new["first"] = False
    elif state == "paused" and was == "printing":
        out.append((kit.card("⏸ <b>Печать на паузе</b>", [name, "Проверь пластик и стол."]),
                    [[kit.b("▶️ Продолжить", "resume", "success"), kit.b("🖨 Статус", "home")]]))
    elif was in ACTIVE and state == "complete":
        spent = (st.get("print_stats") or {}).get("print_duration")
        out.append((kit.card("🎉 <b>Готово</b>", [name, f"⏱ Заняло {hm(spent)}", "Дай столу остыть, потом снимай."]), open_home))
    elif was in ACTIVE and state == "cancelled":
        out.append((kit.card("⛔ <b>Печать отменена</b>", [name]), open_home))
    elif was in ACTIVE and state == "error":
        reason = kit.esc((st.get("print_stats") or {}).get("message") or "")[:300]
        out.append((kit.card("⚠️ <b>Ошибка печати</b>", [name, reason or None]), open_home))
    elif was in ACTIVE and state not in ACTIVE:
        out.append((kit.card("⚠️ <b>Печать оборвалась</b>", [name, f"Остановилась на слое {layer}."]), open_home))
    if state == "printing" and layer >= 2 and not new.get("first"):
        new["first"] = True
        out.append((kit.card("👀 <b>Первый слой готов</b>", [name, "Взгляни, как лёг: если отходит от стола, отмени."]),
                    [[kit.b("🖨 Статус", "home"), kit.b("⛔ Отмена", "cancel")]]))
    return out, new


def heal_tunnel():
    """Туннель мог зависнуть: сессия на сервере жива, а порта нет. Сбрасываем, принтер подключится заново."""
    try:
        subprocess.run(["pkill", "-KILL", "-u", "printertunnel"], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


def check_cooling(snap):
    if not bot.db.get("cooling") or not snap.get("st"):
        return
    st = snap["st"]
    if snap.get("state") in ACTIVE:
        bot.db.delete("cooling")
    elif (st.get("heater_bed") or {}).get("temperature", 99) <= 40 and (st.get("extruder") or {}).get("temperature", 99) <= 50:
        moon.gcode("M106 S0")
        bot.db.delete("cooling")
        bot.notify(kit.card("🧊 <b>Остыло</b>", [temps(st), "Обдув выключен, можно снимать."]), [[kit.b("🖨 Статус", "home")]])


def watch():
    ts = time.time()
    snap = snapshot()
    prev = bot.db.get("watch") or {}
    messages, new = events(prev, snap, ts)
    if not snap["online"] and new.get("off") and ts - new["off"] >= HEAL_AFTER and ts - (prev.get("healed") or 0) >= HEAL_EVERY:
        heal_tunnel()
        new["healed"] = ts
    bot.db.set("watch", new)
    for text, buttons in messages:
        bot.notify(text, buttons)
    try:
        check_cooling(snap)
    except (Offline, kit.Fail) as e:
        print("cooling:", e, flush=True)


bot.every(1, watch)


if __name__ == "__main__":
    bot.run()
