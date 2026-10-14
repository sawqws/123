"""Тренер «Форма»: обход всех команд и кнопок, как будто их нажимает человек, и все API мини-приложения.

Без сети, Telegram, WHOOP, Claude и сервера: Telegram — запись вызовов, WHOOP — поддельные записи (test_coach.py),
Claude, голос, фото и видео — подмены, потоки выполняются сразу. /start, все команды (из setMyCommands и из кода),
тексты, медиа, утренний разбор, тренировка, вечер -> все кнопки из ответов нажимаются через тот же вход, что в
цикле getUpdates (поддержка -> on_callback/on_message), в ширину, глубина ≤ 4, до ~400 нажатий; в режимах
💬 чат и 📱 приложение, с данными WHOOP/едой/водой и с пустой базой.
Падает на: исключении (и на проглоченном — всё, что напечатано, проверяется), нажатии без видимого ответа,
кнопке длиннее 24 символов, ряде больше 3 кнопок, callback_data больше 64 байт, битом HTML, 500 у API.
usage: python3 coach/test_buttons.py
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
import time
import traceback
import unittest
import urllib.request
from collections import deque
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_coach as tc  # noqa: E402  окружение, поддельный WHOOP, подпись initData, сам тренер

coach, ai, insights, store, whoop = tc.coach, tc.ai, tc.insights, tc.store, tc.whoop
support = coach.support
U = tc.U
STRANGER = 99
DAY = tc.TODAY  # последний день поддельного WHOOP
NOW = datetime.datetime.combine(DAY, datetime.time(12, 0), coach.ZoneInfo("Europe/Moscow"))


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




COACH_GARBAGE = GARBAGE + [
    json.dumps({"age": "x", "weight": [1], "height": {}, "sex": 5, "goal": None, "morning": "25:00", "kcal": "abc",
                "protein": None, "day": "garbage", "ml": 99999, "period": "year", "make": 1, "mode": "ask",
                "image": "@@@", "text": 5}).encode(),
    json.dumps({"period": "week", "make": 1, "text": "банан", "ml": 300, "day": "2026-10-11", "title": "Омлет",
                "kcal": 300, "image": "data:image/jpeg;base64,/9j/", "mode": "food"}).encode(),
]


# ---------------------------------------------------------------- тренер

LONG = "\n".join(["**Итог** — восстановление <82%> & сон в норме."] +
                 [f"- пункт {i}: " + "слово " * 30 for i in range(14)])


def fake_claude(prompt, image=None, timeout=240, images=None):
    """Claude: еда — JSON, видео — JSON разбора, остальное — длинный Markdown (чтобы была кнопка «Полностью»)."""
    if "Пришло видео" in prompt:
        return json.dumps({"kind": "exercise", "title": "Джеб <левый> & кросс", "text": LONG})
    if '"food": true' in prompt:
        if "скрин" in prompt:
            return '{"food": false, "comment": "Это скрин WHOOP <не еда>"}'
        return ('{"food": true, "title": "Гречка <с курицей> & соус", "kcal": 520, "protein": 38, "fat": 12, '
                '"carbs": 60, "comment": "Отлично после бокса"}')
    return LONG


def seed(db):
    """Как у настоящего: 40 дней WHOOP (бокс 4 раза в неделю), профиль, цели, еда, вода, переписка, разборы."""
    db.user(U, "Глеб")
    db.save_token(U, "acc", "ref", time.time() + 3600)
    days = 42 + max(0, (datetime.date.today() - DAY).days)  # whoop.sync считает дни от настоящего «сегодня»
    with mock.patch.object(whoop.Client, "get", tc.FakeWhoop(tc.fake_whoop(40)).get):
        whoop.sync(db, U, "cid", "sec", days=days)
    db.meta("whoop_client_id", "cid")
    db.meta("whoop_client_secret", "sec")
    db.save_profile(U, {"age": 14, "sex": "m", "height": 172, "weight": 61.5, "goal": "mass",
                        "sport": "бокс <ринг> & зал", "notes": "колено <болит> " * 10, "morning": "07:30"})
    db.add_goal(U, "вес 60 кг к декабрю <без сгонки> & без травм")
    db.add_goal(U, "очень длинная цель " * 15)
    db.add_goal(U, "ложиться до 23:00")
    d = DAY.isoformat()
    for i, t in enumerate(["Омлет <с сыром> & хлеб", "Гречка с курицей", "Банан", "Творог", "Паста", "Кефир", "Суп"]):
        db.save_meal(U, {"day": d, "time": f"{8 + i:02d}:00", "title": t, "kcal": 300 + i * 50, "protein": 20,
                         "fat": 10, "carbs": 40})
    db.add_water(U, d, "08:00", 300)
    db.add_water(U, d, "10:30", 500)
    db.say(U, "user", "план на неделю?")
    db.say(U, "coach", LONG)
    db.brief(U, d, "progress", ai.to_html(LONG))
    db.brief(U, d, "week", ai.to_html(LONG))
    db.brief(U, d, "sum-week", LONG)


def at(hh, mm):
    return lambda u: NOW.replace(hour=hh, minute=mm)


class ButtonsTest(unittest.TestCase):
    """Обход в разных условиях; итог по каждому прогону — в конце вывода."""

    report = []

    @classmethod
    def tearDownClass(cls):
        print("\nТренер — обход кнопок:")
        for line in cls.report:
            print("  " + line)

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = store.Store(os.path.join(self.tmp, "c.db"))
        self.addCleanup(self.db.db.close)
        self.db.meta("owner", U)

    def start(self, mode, url="https://coach.example"):
        cr = Crawler(self.handle, U, save=self.save, load=self.load)
        sup = support.Support.__new__(support.Support)  # без потоков; Claude не зовём — только «принято»
        sup.token, sup.name, sup.folders, sup.pm2, sup.tests = "123:TEST", "Форма", ["coach"], "coach-bot", []
        sup.allowed, sup.media_dir, sup.waiting, sup.plain = coach.allowed, self.tmp, {}, False
        sup.tg = cr.tg
        sup.start = lambda chat, text, shots=None: cr.tg("sendMessage", chat_id=chat, text="🛠 <b>Принято</b>")
        self.sup = sup
        if mode:
            self.db.meta(f"mode:{U}", mode)
        git = subprocess.CompletedProcess(["git"], 1, "", "fatal: сети нет (тест)")
        voice = ["выпил 500 мл", "съел гречку с курицей", "почему упало восстановление?", "ложиться до 23:00"]

        def photo(tok, m, folder):
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, "p.jpg")
            open(path, "wb").close()
            return path

        patches = [
            mock.patch.object(coach, "db", self.db), mock.patch.object(coach, "public_url", url),
            mock.patch.object(coach, "ALLOWED", set()), mock.patch.object(coach, "tg", cr.tg),
            mock.patch.dict(coach.STYLES, {"ok": True}), mock.patch.object(coach, "SUP", sup),
            mock.patch.object(coach, "busy", set()), mock.patch.object(coach, "DATA", self.tmp),
            mock.patch.object(coach.threading, "Thread", tc.SyncThread),
            mock.patch.object(coach, "user_now", lambda u: NOW), mock.patch.object(ai, "claude", fake_claude),
            mock.patch.object(whoop.Client, "get", tc.FakeWhoop(tc.fake_whoop(40)).get),
            mock.patch.object(whoop, "_post_form", no_network),
            mock.patch.object(coach.media, "voice_text", side_effect=lambda tok, m: voice[(m.get("voice") or {}).get("n", 0)]),
            mock.patch.object(coach.media, "photo_path", side_effect=photo),
            mock.patch.object(coach.media, "video", side_effect=lambda tok, m, d: {
                "frames": [photo(tok, m, d)], "text": "смотри мой джеб", "seconds": 5}),
            mock.patch.object(coach.subprocess, "run", return_value=git),
            mock.patch.object(coach.os, "_exit", side_effect=AssertionError("os._exit")),
            mock.patch.object(urllib.request, "urlopen", no_network),
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
        coach.busy.clear()

    def handle(self, u):
        """Как цикл getUpdates в coach.main(): поддержка, потом кнопки и сообщения."""
        if self.sup.intercept(u):
            return
        if u.get("callback_query"):
            coach.on_callback(u["callback_query"])
        elif u.get("message"):
            coach.on_message(u["message"])

    def inputs(self, cr):
        cr.run("setup_bot", fn=coach.setup_bot, silent=True)
        registered = next((p["commands"] for m, p in cr.calls if m == "setMyCommands"), [])
        self.assertTrue(registered)
        cmds = commands_in(source(coach), registered)
        for c in ("/start", "/today", "/water", "/food", "/goals", "/profile", "/morning", "/sync", "/status", "/fix"):
            self.assertIn(c, cmds)
        m, data = cr.message, self.data
        wk = next(iter(self.db.records(U, "workout")), {"id": "w0", "sport_name": "boxing"})
        # то, что бот присылает сам (утро, тренировка, вечер, неделя) — первыми: дальше команды меняют данные
        out = [("утренний разбор", lambda: coach.morning_brief(U)), ("утро без сна", lambda: coach.morning_early(U)),
               ("утро по расписанию", lambda: coach.maybe_morning(self.db.user(U), NOW.replace(hour=7, minute=35)), True),
               ("после тренировки", lambda: coach.workout_note(U, insights.workout_of(wk))),
               ("вечер: питание", lambda: coach.evening(U, NOW.replace(hour=20, minute=40)), True),
               ("итог недели", lambda: coach.progress_report(U, U, True)),
               ("сводка за месяц", lambda: coach.summary_report(U, U, "month")),
               ("WHOOP подключён", lambda: coach.first_sync(U)),
               ("вход в WHOOP не прошёл", lambda: coach.finish_connect("нет-такого", "code"), True),
               ("расписание", lambda: coach.tick(), True)]
        out += [("/start", m("/start"))] + [(c, m(c)) for c in cmds if c != "/fix"]
        out += [(t, m(t)) for t in (
            "/profile вес 61,5, рост 172, спорт бокс, цель масса, пол парень, возраст 14", "/profile ерунда",
            "/profile возраст 3", "/water 500", "/water 1.5", "/water abc", "/goal ложиться до 23:00", "/goal " + "я" * 400,
            "/morning 8:30", "/morning 25:00", "/morning off", "/whoop один", "/start connect", "/nope",
            "/today@forma_bot", "/START")]
        out += [(t, m(t)) for t in ("выпил 2 стакана воды", "съел гречку с курицей", "Почему упало восстановление?",
                                    "привет <b>не тег</b> & ещё", "а" * 3000, "вода 9000", "👍")]
        out += [("голосовое: вода", m(voice={"file_id": "v", "duration": 3})),
                ("голосовое: еда", m(voice={"file_id": "v", "n": 1})),
                ("голосовое: вопрос", m(voice={"file_id": "v", "n": 2})),
                ("фото еды", m(photo=[{"file_id": "a"}, {"file_id": "b"}])),
                ("фото с вопросом", m(photo=[{"file_id": "a"}], caption="это нормально?")),
                ("фото-скрин", m(photo=[{"file_id": "a"}], caption="скрин")),
                ("кружок", m(video_note={"file_id": "c", "duration": 5})),
                ("видео", m(video={"file_id": "v", "duration": 6}, caption="техника?")),
                ("стикер", m(sticker={"file_id": "s"})),
                ("документ", m(document={"file_id": "d", "mime_type": "application/pdf"})),
                ("геопозиция", m(location={"latitude": 1, "longitude": 2})),
                ("группа", m("/start", chat_type="group"), True),
                ("чужой /start", m("/start", uid=STRANGER)), ("чужой текст", m("привет", uid=STRANGER)),
                ("чужая кнопка", cr.callback("food|", 1, uid=STRANGER)),
                ("/fix", m("/fix")), ("ответ поддержке", m("кольцо сна пустое"))]
        out += [("/whoop ключи", m("/whoop CID SECRET"))]  # в конце: в пустой базе сразу появятся ключи
        if not data:
            out = [(x[0], x[1], True) if x[0] in ("утро без сна", "после тренировки") else x for x in out]
        return out

    def crawl(self, name, mode, data=True, url="https://coach.example"):
        cr = self.start(mode, url)
        self.data = data
        if data:
            seed(self.db)
        cr.crawl(self.inputs(cr))
        s = cr.stats
        self.report.append(f"{name}: входов {s['inputs']}, нажатий {s['presses']} + повторных {s['double']} "
                           f"(кнопок-ссылок {s['links']}), проблем {len(cr.problems)}")
        self.assertGreater(s["presses"], 5 if not data else 30)
        # нажата хотя бы одна кнопка каждого вида, который разбирает on_callback («c» — подключить WHOOP —
        # есть только без WHOOP, остальные — только с данными)
        body = source(coach).split("def on_callback")[1].split("\ndef ")[0]
        missing = (callback_kinds(body, r"data\[0\]") if data else {"c", "mode"}) - cr.pressed
        # «c» с данными теперь бывает: «Вход истёк → 🔌 Подключить WHOOP» после неудачного обновления
        self.assertLessEqual(missing, {"c"} if data else set(), f"не нажаты виды кнопок: {missing}")
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

    def run_all(self, db):
        init = tc.signed({"id": U, "first_name": "Глеб"})
        routes = api_routes(source(coach))
        self.assertGreaterEqual(len(routes), 20)
        bad, n = [], 0
        reqs = [("GET", "", b""), ("GET", "?d=garbage&n=abc&period=year", b"")] + [("POST", "", g) for g in COACH_GARBAGE]
        for path in routes + ["/api/nope"]:
            for method, query, raw in reqs:
                out = io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                    r = http(coach.Web, path + query, raw, init, method)
                n += 1
                if r.code not in (200, 400, 401, 403, 404, 413, 503) or out.getvalue().strip():
                    bad.append(f"{method} {path}{query} {raw[:70]!r} -> {r.code} {r.body[:120]!r} {out.getvalue()[-300:]}")
        for q in ("", "?state=x&code=y", "?error=access_denied", "?state=&code=", "?state=a:b"):
            r = http(coach.Web, "/whoop/callback" + q, method="GET")
            if r.code != 200:
                bad.append(f"GET /whoop/callback{q} -> {r.code}")
        for path in ("/api/all", "/api/meal"):  # без подписи и с чужой
            self.assertEqual(http(coach.Web, path, b"{}", "").code, 401)
            self.assertEqual(http(coach.Web, path, b"{}", tc.signed({"id": STRANGER})).code, 403)
        self.assertEqual(http(coach.Web, "/api/photo", b"x" * 10, init, headers={"Content-Length": "999999999"}).code, 413)
        for path in ("/", "/health", "/app.js", "/kit.css", "/../coach.py", "/nope.txt"):
            self.assertIn(http(coach.Web, path, method="GET").code, (200, 404), path)
        return routes, n, bad

    def test_no_500(self):
        for name, filled in (("данные", True), ("пусто", False)):
            tmp = tempfile.mkdtemp()
            db = store.Store(os.path.join(tmp, "a.db"))
            self.addCleanup(db.db.close)
            db.meta("owner", U)
            cr = Crawler(None, U)
            sup = support.Support.__new__(support.Support)
            sup.allowed, sup.media_dir, sup.start = coach.allowed, tmp, lambda *a, **k: None
            with mock.patch.object(coach, "db", db), mock.patch.object(coach, "tg", cr.tg), \
                    mock.patch.object(coach, "ALLOWED", set()), mock.patch.object(coach, "SUP", sup), \
                    mock.patch.object(coach, "DATA", tmp), mock.patch.object(coach, "busy", set()), \
                    mock.patch.object(coach.threading, "Thread", tc.SyncThread), \
                    mock.patch.object(coach, "user_now", lambda u: NOW), mock.patch.object(ai, "claude", fake_claude), \
                    mock.patch.object(whoop.Client, "get", tc.FakeWhoop(tc.fake_whoop(40)).get), \
                    mock.patch.object(whoop, "_post_form", no_network), \
                    mock.patch.object(urllib.request, "urlopen", no_network), \
                    mock.patch.object(socket, "create_connection", no_network):
                if filled:
                    seed(db)
                routes, n, bad = self.run_all(db)
            print(f"\nТренер — API ({name}): маршрутов {len(routes)}, запросов {n}, проблем {len(bad)}")
            self.assertEqual(bad, [], "\n" + "\n".join(bad[:40]))


if __name__ == "__main__":
    unittest.main(verbosity=1)
