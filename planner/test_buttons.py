"""Планер «Ритм»: обход всех команд и кнопок, как будто их нажимает человек, и все API мини-приложения.

Без сети, Telegram, Claude и сервера: Telegram — запись вызовов, голос/фото/видео и Claude — подмены.
/start, все команды (из setMyCommands и из кода), тексты, медиа, утро/вечер/напоминание -> все кнопки из ответов
нажимаются через тот же вход, что в цикле getUpdates (поддержка -> on_callback/on_message), в ширину,
глубина ≤ 4, до ~400 нажатий; в режимах 💬 чат и 📱 приложение, с данными и с пустой базой.
Падает на: исключении (и на проглоченном — всё, что напечатано, проверяется), нажатии без видимого ответа,
кнопке длиннее 24 символов, ряде больше 3 кнопок, callback_data больше 64 байт, битом HTML, 500 у API.
usage: python3 planner/test_buttons.py
"""
import contextlib
import datetime
import io
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import traceback
import unittest
import urllib.request
from collections import deque
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_planner as tp  # noqa: E402  окружение, подпись initData, сам планер

planner, store, support = tp.planner, tp.store, tp.support
U = 7
STRANGER = 99
NOW = datetime.datetime(2026, 10, 14, 12, 0, tzinfo=planner.ZoneInfo("Europe/Moscow"))  # среда
TODAY = NOW.date()


# ---------------------------------------------------------------- обходчик (одинаковый у ботов)

VISIBLE = {"sendMessage", "editMessageText", "editMessageReplyMarkup", "editMessageCaption", "sendDocument",
           "sendPhoto", "sendMediaGroup", "sendVoice", "deleteMessage"}
TAGS = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "code", "pre", "a", "blockquote", "tg-spoiler",
        "span", "tg-emoji"}
TOKEN_RX = re.compile(r"<(/?)([a-zA-Z][\w-]*)([^<>]*)>|&(#\d+|#x[0-9a-fA-F]+|[a-zA-Z]+);|([<>&])")


def html_problems(text):
    """Что не примет Telegram в parse_mode=HTML: чужие теги, незакрытые, «<», «&» без экранирования, > 4096."""
    out, stack, plain, pos = [], [], 0, 0
    for m in TOKEN_RX.finditer(text):
        plain += m.start() - pos
        pos = m.end()
        close, tag, _, ent, stray = m.groups()
        if stray:
            out.append(f"неэкранированный «{stray}» у …{text[max(0, m.start() - 30):m.start() + 10]!r}")
        elif ent:
            plain += 1
            if not ent.startswith("#") and ent not in ("lt", "gt", "amp", "quot"):
                out.append(f"сущность &{ent}; Telegram не знает")
        elif tag.lower() not in TAGS:
            out.append(f"тег <{tag}> Telegram не знает")
        elif close:
            if not stack or stack[-1] != tag.lower():
                out.append(f"</{tag}> не к месту (открыты: {stack})")
            else:
                stack.pop()
        else:
            stack.append(tag.lower())
    plain += len(text) - pos
    if stack:
        out.append(f"не закрыты теги {stack}")
    if plain > 4096:
        out.append(f"текст длиннее 4096 ({plain})")
    if not text.strip():
        out.append("пустой текст")
    return out


class Crawler:
    """Нажимает всё подряд: входы (команды, тексты, медиа, функции) -> кнопки из ответов -> их кнопки…
    save/load — снимок состояния бота (база): каждая кнопка нажимается в том состоянии, в каком её показали,
    иначе соседняя кнопка (например, «Открыть» рядом с «Отменить») заменяет сообщение и до второй не дойти."""

    def __init__(self, handle, uid, ok_output=(), cap=400, depth=4, save=None, load=None):
        self.handle, self.uid, self.ok_output, self.cap, self.depth = handle, uid, ok_output, cap, depth
        self.save, self.load = save, load
        self.calls, self.msgs, self.mids, self.problems = [], {}, {}, []
        self.queue, self.deferred, self.seen, self.reply_seen = deque(), [], set(), set()
        self.mid = 1000
        self.upd = 0
        self.stats = {"inputs": 0, "presses": 0, "double": 0, "reply": 0, "links": 0, "gone": 0}
        self.pressed = set()  # виды нажатых кнопок: «t» из «t|5|2026-10-14»

    # ---- поддельный Telegram ----
    def tg(self, method, **p):
        self.calls.append((method, p))
        if method in ("sendMessage", "sendDocument", "sendPhoto"):
            self.mid += 1
            self.msgs[self.mid] = p.get("reply_markup")
            self.mids[id(p)] = self.mid
            return {"ok": True, "result": {"message_id": self.mid, "chat": {"id": p.get("chat_id")}, "text": p.get("text", "")}}
        if method in ("editMessageText", "editMessageReplyMarkup", "editMessageCaption"):
            self.msgs[p.get("message_id")] = p.get("reply_markup")  # без reply_markup Telegram кнопки убирает
            return {"ok": True, "result": {"message_id": p.get("message_id")}}
        return {"ok": True, "result": {"getUpdates": [], "getMyCommands": [], "getMe": {"username": "test_bot"}}.get(method, True)}

    # ---- проверки одного вызова ----
    def check(self, method, p, label):
        bad = []
        if method in ("sendMessage", "editMessageText") or (method in VISIBLE and "caption" in p):
            text = p.get("text", p.get("caption", ""))
            bad += html_problems(text) if p.get("parse_mode") == "HTML" else (
                ["текст длиннее 4096"] if len(text) > 4096 else ["пустой текст"] if not text.strip() else [])
            leak = re.search(r"\b(None|nan|NaN|undefined|null)\b|\{\w*\}", text)
            if leak:
                bad.append(f"в тексте «{leak.group(0)}»: …{text[max(0, leak.start() - 40):leak.end() + 20]!r}")
        if method == "answerCallbackQuery" and len(p.get("text") or "") > 200:
            bad.append("ответ на кнопку длиннее 200")
        mk = p.get("reply_markup") or {}
        for key in ("inline_keyboard", "keyboard"):
            for row in mk.get(key) or []:
                if len(row) > 3:
                    bad.append(f"в ряду {len(row)} кнопок: {[b.get('text') for b in row]}")
                for b in row:
                    b = b if isinstance(b, dict) else {"text": b}
                    t = b.get("text", "")
                    if not t.strip() or len(t) > 24:
                        bad.append(f"подпись кнопки {len(t)} симв.: «{t}»")
                    if key == "keyboard":
                        continue
                    acts = [k for k in ("callback_data", "url", "web_app", "switch_inline_query", "copy_text") if k in b]
                    if len(acts) != 1:
                        bad.append(f"у кнопки «{t}» действий {acts}")
                    d = b.get("callback_data")
                    if d is not None and not 1 <= len(d.encode()) <= 64:
                        bad.append(f"callback_data {len(d.encode())} байт: {d!r}")
                    url = b.get("url") or (b.get("web_app") or {}).get("url")
                    if url is not None and not re.match(r"^(https://|tg://)" if "web_app" not in b else r"^https://", url):
                        bad.append(f"плохой адрес у «{t}»: {url!r}")
        for x in bad:
            self.problems.append(f"{label} -> {method}: {x}")

    # ---- входы ----
    def message(self, text=None, uid=None, chat_type="private", **extra):
        uid = uid or self.uid
        self.upd += 1
        m = {"message_id": self.upd, "date": 0, "chat": {"id": uid if chat_type == "private" else -100, "type": chat_type},
             "from": {"id": uid, "is_bot": False, "first_name": "Глеб"}, **extra}
        if text is not None:
            m["text"] = text
        return {"update_id": self.upd, "message": m}

    def callback(self, data, mid, uid=None):
        uid = uid or self.uid
        self.upd += 1
        return {"update_id": self.upd, "callback_query": {
            "id": f"cq{self.upd}", "chat_instance": "1", "data": data, "from": {"id": uid, "is_bot": False, "first_name": "Глеб"},
            "message": {"message_id": mid, "date": 0, "chat": {"id": uid, "type": "private"}, "text": "…"}}}

    def run(self, label, upd=None, fn=None, silent=False, depth=0, double=False):
        """Одно действие: обновление через handle или функция бота. Проверяет вывод, ответы, кнопки."""
        start, out = len(self.calls), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            try:
                fn() if fn else self.handle(upd)
            except Exception:
                print("НЕПОЙМАННОЕ ИСКЛЮЧЕНИЕ")
                traceback.print_exc()
        new = self.calls[start:]
        log = [x for x in out.getvalue().splitlines() if x.strip()]
        noise = [x for x in log if not any(re.search(r, x) for r in self.ok_output)]
        if noise:
            self.problems.append(f"{label}: напечатано (ошибка?):\n    " + "\n    ".join(noise[-12:]))
        for m, p in new:
            self.check(m, p, label)
        cq = upd and upd.get("callback_query")
        visible = [m for m, p in new if m in VISIBLE]
        if cq:
            answers = [p for m, p in new if m == "answerCallbackQuery" and p.get("callback_query_id") == cq["id"]]
            if not answers:
                self.problems.append(f"{label}: нет answerCallbackQuery — кнопка «крутится»")
            elif len(answers) > 1:
                self.problems.append(f"{label}: answerCallbackQuery {len(answers)} раза")
            if not double and not visible and not any((a.get("text") or "").strip() for a in answers):
                self.problems.append(f"{label}: нажатие без видимого ответа")
        elif not visible and not silent:
            self.problems.append(f"{label}: нет ответа")
        if depth < self.depth:
            self.collect(new, depth + 1)
        return new

    def collect(self, new, depth):
        snap = []  # снимок — один на действие и только если появились кнопки

        def state():
            if not snap:
                snap.append((self.save() if self.save else None, dict(self.msgs)))
            return snap[0]
        for m, p in new:
            mk = p.get("reply_markup") or {}
            mid = p.get("message_id") if m.startswith("edit") else self.mids.get(id(p))
            for row in mk.get("inline_keyboard") or []:
                for b in row:
                    d = b.get("callback_data")
                    if d is None:
                        self.stats["links"] += 1
                    elif d not in self.seen:
                        self.seen.add(d)
                        (self.deferred if d.startswith("mode|") else self.queue).append((d, mid, depth, state()))
            for row in mk.get("keyboard") or []:
                for b in row:
                    t = b if isinstance(b, str) else b.get("text")
                    if t and t not in self.reply_seen:
                        self.reply_seen.add(t)
                        self.queue.append(("\0" + t, None, depth, state()))

    def holder(self, data, mid):
        """Сообщение, где кнопка есть сейчас (после правок старые кнопки пропадают, как в Telegram)."""
        def has(m):
            mk = self.msgs.get(m) or {}
            return any(b.get("callback_data") == data for row in mk.get("inline_keyboard") or [] for b in row)
        if mid is not None and has(mid):
            return mid
        return next((m for m in reversed(list(self.msgs)) if has(m)), None)

    def crawl(self, inputs):
        """inputs: [(подпись, update | функция, ждать ли ответа)]."""
        for label, x, *rest in inputs:
            self.stats["inputs"] += 1
            silent = bool(rest and rest[0])
            self.run(label, fn=x, silent=silent) if callable(x) else self.run(label, upd=x, silent=silent)
        while (self.queue or self.deferred) and self.stats["presses"] < self.cap:
            if not self.queue:
                self.queue.extend(self.deferred)
                self.deferred = []
            data, mid, depth, (db, msgs) = self.queue.popleft()
            self.stats["presses"] += 1
            if self.load:
                self.load(db)
                self.msgs = dict(msgs)
            if data.startswith("\0"):
                self.stats["reply"] += 1
                self.run(f"клавиатура «{data[1:]}»", upd=self.message(data[1:]), depth=depth)
                continue
            where = self.holder(data, mid)
            if where is None:
                self.stats["gone"] += 1  # кнопку уже заменили правкой — нажать её нельзя
                continue
            self.pressed.add(data.split("|")[0])
            self.run(f"кнопка {data!r}", upd=self.callback(data, where), depth=depth)
            # двойное нажатие: второй тап приходит со старым сообщением, пока первый ещё не обновил его
            self.stats["double"] += 1
            self.run(f"кнопка {data!r} ×2", upd=self.callback(data, where), depth=self.depth, double=True)
        return self


def no_network(*a, **k):
    raise AssertionError(f"сеть в тесте: {a[:1]}")


def source(mod):
    with open(mod.__file__, encoding="utf-8") as f:
        return f.read()


def callback_kinds(src, var):
    """Виды кнопок, которые разбирает on_callback: kind == "x" / kind in ("x", "y")."""
    found = set(re.findall(var + r'\s*==\s*"([\w-]+)"', src))
    for grp in re.findall(var + r"\s+in\s+\(([^)]*)\)", src):
        found |= set(re.findall(r'"([\w-]+)"', grp))
    return found


def commands_in(source, registered):
    """Все команды: из setMyCommands и все, что разбирает код (cmd == "/x", cmd in ("/x", …))."""
    found = set(re.findall(r'cmd\s*==\s*"(/\w+)"', source))
    for grp in re.findall(r"cmd\s+in\s+\(([^)]*)\)", source):
        found |= set(re.findall(r'"(/\w+)"', grp))
    return sorted(found | {"/" + c["command"] for c in registered} | {"/fix"})


def api_routes(source):
    return sorted(set(re.findall(r'path\s*==\s*"(/api/[\w/]+)"', source)))


class Resp:
    code, body = None, b""


def http(web_cls, path, raw=b"", init="", method="POST", headers=None):
    """Запрос к веб-серверу бота без сети: обработчик с подменёнными потоками."""
    h = web_cls.__new__(web_cls)
    h.headers = {"X-Init-Data": init, "Content-Length": str(len(raw)), **(headers or {})}
    h.rfile, h.wfile, h.path = io.BytesIO(raw), io.BytesIO(), path
    r = Resp()
    h.send_response = lambda c, *a: setattr(r, "code", c)
    h.send_header = lambda *a: None
    h.end_headers = lambda: None
    (h.do_POST if method == "POST" else h.do_GET)()
    r.body = h.wfile.getvalue()
    return r


GARBAGE = [b"", b"{}", b"not json", b"[]", b"[1,2]", b'"str"', b"5", b"null", b'{"id": "abc"}', b'{"id": null}',
           b'{"id": 999999}', b'{"id": -1, "date": "2026-13-45"}',
           json.dumps({"id": None, "date": 5, "title": ["x"], "text": {"a": 1}, "mode": 7, "goal": "x", "horizon": 3,
                       "remind": "x", "morning": 25, "tz": "Nowhere/Nope", "done": "maybe", "repeat": 1234567,
                       "time": "99:99", "ml": "x", "period": None, "d": 3, "n": "z", "image": 5, "photo": [1],
                       "progress": "x", "important": "x", "status": "x", "emoji": 5}).encode(),
           json.dumps({"title": "<b>&", "text": "x" * 20000, "date": "2026-10-14", "time": "18:00"}).encode()]


# ---------------------------------------------------------------- планер

def seed(db):
    """Похоже на настоящее: сегодня, вчера (просрочено), завтра, повторы, важное, цели всех сроков, заметки."""
    db.user(U, "Глеб")
    db.update_settings(U, {"morning": "07:30", "evening": "21:30", "remind": 30})
    iso = lambda d: (TODAY + datetime.timedelta(days=d)).isoformat()  # noqa: E731
    g_week = db.save_goal(U, {"title": "Спарринг <в субботу> & без травм", "emoji": "🥊", "horizon": "week",
                              "why": "Хочу на соревнования весной " * 8, "start": store.period_start("week", TODAY).isoformat()})
    g_month = db.save_goal(U, {"title": "Прочитать 2 книги", "emoji": "📚", "horizon": "month", "progress": 40,
                               "start": store.period_start("month", TODAY).isoformat()})
    db.save_goal(U, {"title": "Выучить английский до B2 — очень длинное название цели", "horizon": "year",
                     "start": store.period_start("year", TODAY).isoformat()})
    db.save_goal(U, {"title": "Когда-нибудь — прыжок с парашютом", "horizon": "none"})
    done = db.save_goal(U, {"title": "Сдать нормы ГТО", "emoji": "🏅", "horizon": "month"})
    db.save_goal(U, {"id": done["id"], "status": "done"})
    tasks = [
        {"title": "Бокс <в зале> & растяжка", "date": iso(0), "time": "18:00", "remind": 30, "goal": g_week["id"]},
        {"title": "Контрольная по алгебре", "date": iso(0), "time": "09:00", "important": 1},
        {"title": "Купить молоко", "date": iso(0)},
        {"title": "Очень-очень длинное название задачи про подготовку к контрольной по геометрии и физике",
         "date": iso(0), "time": "20:00"},
        {"title": "Английский", "date": iso(-7), "time": "19:00", "repeat": "135"},
        {"title": "Зарядка", "date": iso(-30), "time": "07:00", "repeat": "1234567"},
        {"title": "Вернуть книгу", "date": iso(-1)},
        {"title": "Сделать проект", "date": iso(-3), "important": 1},
        {"title": "Кино с друзьями", "date": iso(1), "time": "19:30"},
        {"title": "Лапы на тренировке", "goal": g_week["id"]},
        {"title": "Глава 3", "goal": g_month["id"], "date": iso(2)},
        {"title": "Без даты: идея", "date": None},
    ] + [{"title": f"Мелочь {i}", "date": iso(0)} for i in range(12)]
    ids = [db.save_task(U, t)["id"] for t in tasks]
    db.toggle(U, ids[2])
    db.toggle(U, ids[5], iso(0))
    db.save_note(U, {"text": "Руки выше, подбородок ниже <важно> & помнить", "goal": g_week["id"]})
    db.save_note(U, {"text": "Позвонить Саше про билеты"})
    db.save_note(U, {"text": "Длинная мысль " * 60})
    for i in range(9):
        db.save_note(U, {"text": f"Заметка {i}"})


def fake_claude(prompt, cwd, timeout=240):
    return "1. 15.10 18:00 Бокс\n- Купить кроссовки!\n• по пн и ср 19:00 Английский <b>&\nНЕТ"


def at(hh, mm):
    return lambda u: NOW.replace(hour=hh, minute=mm)


class ButtonsTest(unittest.TestCase):
    """Обход в разных условиях; итог по каждому прогону — в конце вывода."""

    report = []

    @classmethod
    def tearDownClass(cls):
        print("\nПланер — обход кнопок:")
        for line in cls.report:
            print("  " + line)

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = store.Store(os.path.join(self.tmp, "p.db"))
        self.addCleanup(self.db.close)
        self.db.meta("owner", U)

    def start(self, mode, url="https://planner.example"):
        cr = Crawler(self.handle, U, save=self.save, load=self.load)
        sup = support.Support.__new__(support.Support)  # без потоков; Claude не зовём — только «принято»
        sup.token, sup.name, sup.folders, sup.pm2, sup.tests = "123:TEST", "Планер", ["planner"], "planner-bot", []
        sup.allowed, sup.media_dir, sup.waiting, sup.plain = planner.can_support, self.tmp, {}, False
        sup.tg = cr.tg
        sup.start = lambda chat, text, shots=None: cr.tg("sendMessage", chat_id=chat, text="🛠 <b>Принято</b>")
        self.sup = sup
        if mode:
            self.db.meta("mode", mode)

        def export(req, *a, **k):  # /export шлёт файл сам, через urlopen — считаем это ответом бота
            cr.tg("sendDocument", url=getattr(req, "full_url", req))
            return io.BytesIO(b'{"ok":true}')
        git = subprocess.CompletedProcess(["git"], 1, "", "fatal: сети нет (тест)")
        voice = ["завтра в 18:00 бокс", "заметка: идея <для> проекта", "цель на месяц: 10 подтягиваний", "э-э"]
        patches = [
            mock.patch.object(planner, "db", self.db), mock.patch.object(planner, "public_url", url),
            mock.patch.object(planner, "PUBLIC", False), mock.patch.object(planner, "ALLOWED", set()),
            mock.patch.object(planner, "tg", cr.tg), mock.patch.object(planner, "STYLE_OK", True),
            mock.patch.object(planner, "SUP", sup), mock.patch.object(planner, "spawn", lambda fn, *a: fn(*a)),
            mock.patch.object(planner, "user_now", lambda u: NOW), mock.patch.object(planner, "claude", fake_claude),
            mock.patch.object(planner, "DATA", self.tmp),
            mock.patch.object(planner.media, "voice_text", side_effect=lambda tok, m: voice[m["voice"].get("n", 0)]),
            mock.patch.object(planner.media, "photo_path", side_effect=lambda tok, m, d: os.path.join(d, "p.jpg")),
            mock.patch.object(planner.media, "video", side_effect=lambda tok, m, d: {
                "frames": [os.path.join(d, "f1.jpg")], "text": "в пятницу кино", "seconds": 5}),
            mock.patch.object(planner.subprocess, "run", return_value=git),
            mock.patch.object(planner.os, "_exit", side_effect=AssertionError("os._exit")),
            mock.patch.object(urllib.request, "urlopen", side_effect=export),
            mock.patch.object(socket, "create_connection", no_network),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return cr

    def save(self):
        snap = sqlite3.connect(":memory:")
        self.db.db.backup(snap)
        self.addCleanup(snap.close)  # закрыть в конце, а не сборщиком мусора посреди обхода
        return snap, {k: dict(v) for k, v in self.sup.waiting.items()}

    def load(self, state):
        snap, waiting = state
        snap.backup(self.db.db)
        self.sup.waiting = {k: dict(v) for k, v in waiting.items()}

    def handle(self, u):
        """Как цикл getUpdates в planner.main(): поддержка, потом кнопки и сообщения."""
        if self.sup.intercept(u):
            return
        if u.get("callback_query"):
            planner.on_callback(u["callback_query"])
        elif u.get("message"):
            planner.on_message(u["message"])

    def inputs(self, cr):
        cr.run("setup_bot", fn=planner.setup_bot, silent=True)
        registered = next((p["commands"] for m, p in cr.calls if m == "setMyCommands"), [])
        self.assertTrue(registered)
        cmds = commands_in(source(planner), registered)
        for c in ("/start", "/today", "/week", "/goals", "/notes", "/settings", "/help", "/tz", "/export", "/fix"):
            self.assertIn(c, cmds)
        m = cr.message
        # утро, напоминание и вечер — первыми: дальше команды поменяют настройки (/morning off)
        out = [("утро 07:40", lambda: self.with_now(7, 40), not self.data),
               ("напоминание 17:31", lambda: self.with_now(17, 31), not self.data),
               ("вечер 21:35", lambda: self.with_now(21, 35), not self.data)]
        out += [("/start", m("/start"))] + [(c, m(c)) for c in cmds if c != "/fix"]
        out += [(t, m(t)) for t in ("/tz Asia/Tokyo", "/tz Nowhere/Nope", "/tz Europe/Moscow", "/morning 8:05", "/morning утром",
                                    "/morning off", "/evening 25:99", "/evening 21:30", "/today@planner_bot", "/nope",
                                    "/start ref", "/START")]
        out += [(t, m(t)) for t in ("завтра в 18:00 бокс", "Заметка: купить подарок <маме> & папе",
                                    "цель на неделю: выйти на спарринг", "по пн и ср английский в 19:00",
                                    "12.10 контрольная!", "через 3 дня проект", "!!!", "👍", "<b>не тег</b> & ещё",
                                    "а" * 3000, "цель: ", "заметка:")]
        out += [("голосовое", m(voice={"file_id": "v", "duration": 3})),
                ("голосовое-заметка", m(voice={"file_id": "v", "n": 1})),
                ("голосовое-цель", m(voice={"file_id": "v", "n": 2})),
                ("голосовое без задачи", m(voice={"file_id": "v", "n": 3})),
                ("фото", m(photo=[{"file_id": "a"}, {"file_id": "b"}], caption="расписание")),
                ("кружок", m(video_note={"file_id": "c", "duration": 5})),
                ("видео", m(video={"file_id": "v", "duration": 6}, caption="план")),
                ("стикер", m(sticker={"file_id": "s"})),
                ("документ", m(document={"file_id": "d", "mime_type": "application/pdf"})),
                ("геопозиция", m(location={"latitude": 1, "longitude": 2})),
                ("пустое сообщение", m(), True),  # служебное (без текста и медиа) — молчать нормально
                ("группа", m("/start", chat_type="group"), True),
                ("чужой /start", m("/start", uid=STRANGER)), ("чужой текст", m("привет", uid=STRANGER)),
                ("/fix", m("/fix")), ("ответ поддержке", m("неделя не листается"))]
        return out

    def with_now(self, hh, mm):
        with mock.patch.object(planner, "user_now", at(hh, mm)):
            planner.tick()

    def crawl(self, name, mode, data=True, url="https://planner.example"):
        cr = self.start(mode, url)
        self.data = data
        if data:
            seed(self.db)
        cr.crawl(self.inputs(cr))
        s = cr.stats
        self.report.append(f"{name}: входов {s['inputs']}, нажатий {s['presses']} + повторных {s['double']} "
                           f"(кнопок-ссылок {s['links']}), проблем {len(cr.problems)}")
        self.assertGreater(s["presses"], 5 if not data else 30)
        if data:  # с данными нажата хотя бы одна кнопка каждого вида, который разбирает on_callback
            body = source(planner).split("def on_callback")[1].split("\ndef ")[0]
            missing = callback_kinds(body, "kind") - cr.pressed
            self.assertEqual(missing, set(), f"не нажаты виды кнопок: {missing}")
        self.assertEqual(cr.problems, [], "\n" + "\n".join(cr.problems[:40]))
        return cr

    def test_chat_mode_with_data(self):
        self.crawl("💬 чат, данные", "chat")

    def test_app_mode_with_data(self):
        self.crawl("📱 приложение, данные", "app")

    def test_first_start_empty(self):
        self.crawl("первый /start, пусто", "", data=False)

    def test_chat_mode_empty_no_url(self):
        self.crawl("💬 чат, пусто, без адреса", "chat", data=False, url="")

    def test_app_mode_empty_no_url(self):
        self.crawl("📱 приложение, пусто, без адреса", "app", data=False, url="")


class ApiTest(unittest.TestCase):
    """Каждый маршрут мини-приложения — пустое и мусорное тело, GET и POST: никаких 500."""

    def test_no_500(self):
        db = store.Store(os.path.join(tempfile.mkdtemp(), "a.db"))
        self.addCleanup(db.close)
        db.meta("owner", U)
        cr = Crawler(None, U)
        sup = support.Support.__new__(support.Support)
        sup.allowed, sup.media_dir, sup.start = planner.can_support, tempfile.mkdtemp(), lambda *a, **k: None
        with mock.patch.object(planner, "db", db), mock.patch.object(planner, "tg", cr.tg), \
                mock.patch.object(planner, "PUBLIC", False), mock.patch.object(planner, "ALLOWED", set()), \
                mock.patch.object(planner, "SUP", sup), mock.patch.object(urllib.request, "urlopen", no_network), \
                mock.patch.object(socket, "create_connection", no_network):
            seed(db)
            routes = api_routes(source(planner))
            self.assertGreaterEqual(len(routes), 10)
            init = tp.signed({"id": U, "first_name": "Глеб"})
            bad, n = [], 0
            for path in routes + ["/api/nope"]:
                for method, raw in [("GET", b"")] + [("POST", g) for g in GARBAGE]:
                    out = io.StringIO()
                    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                        r = http(planner.Web, path, raw, init, method)
                    n += 1
                    if r.code not in (200, 400, 401, 403, 404, 413) or out.getvalue().strip():
                        bad.append(f"{method} {path} {raw[:60]!r} -> {r.code} {r.body[:120]!r} {out.getvalue()[-300:]}")
            for path in ("/api/all", "/api/task"):  # без подписи и с чужой
                self.assertEqual(http(planner.Web, path, b"{}", "").code, 401)
                self.assertEqual(http(planner.Web, path, b"{}", tp.signed({"id": STRANGER})).code, 403)
            for path in ("/", "/health", "/app.js", "/kit.css", "/../planner.py", "/nope.txt"):
                self.assertIn(http(planner.Web, path, method="GET").code, (200, 404), path)
            print(f"\nПланер — API: маршрутов {len(routes)}, запросов {n}, проблем {len(bad)}")
            self.assertEqual(bad, [], "\n" + "\n".join(bad[:40]))


if __name__ == "__main__":
    unittest.main(verbosity=1)
