"""Обход всех кнопок «Главного» и Секретаря — как будто Глеб сам нажал каждую. Без сети, Telegram, аккаунта и сервера.

usage: tgbot/.venv/bin/python hub/test_buttons.py   — с настоящим Секретарём (tgbot/agent.py, нужен telethon)
       python3 hub/test_buttons.py                  — Секретарь — имитация его сокета (кнопки «s:» только доходят до него)

Краулер шлёт /start, все команды и тексты кнопок клавиатуры через те же входы, что цикл getUpdates
(hub.on_message / hub.on_callback; кнопки «s:…» Главный сам отдаёт Секретарю в сокет), собирает все кнопки из
ответов (sendMessage / editMessageText / editMessageReplyMarkup / sendPhoto) и нажимает их в ширину: глубина ≤ 4,
не больше ~400 нажатий на сценарий; сценарии — 💬 чат и 📱 приложение, Секретарь в аккаунте и без него, Секретарь лежит.
Падает, если: исключение (в том числе пойманное и напечатанное), нажатие без видимого ответа (нет сообщения, правки,
answerCallbackQuery), текст кнопки длиннее 24 символов, больше 3 кнопок в ряд, callback_data длиннее 64 байт,
действие с аккаунтом Telegram без подтверждения (кроме задокументированных — ALLOWED_AUTO).
Плюс все маршруты мини-приложения (/app/api/*, /app/agent/api/*) с пустыми и мусорными телами — без 500.
"""
import asyncio
import collections
import datetime
import io
import json
import os
import re
import socket
import sys
import tempfile
import threading
import time
import traceback
import types as pytypes
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.join(REPO, "tgbot"))
os.environ.update(HUB_TOKEN="111:HUB")
for _k in ("HUB_ALLOWED", "HUB_URL", "HUB_IP", "HUB_REMOTE"):
    os.environ.pop(_k, None)
import hub  # noqa: E402
from test_hub import FakeSecretary, init_data  # noqa: E402

try:  # настоящий Секретарь — только если есть telethon (tgbot/.venv)
    import actions  # noqa: E402
    import agent  # noqa: E402
    import botapi  # noqa: E402
    import ui as agent_ui  # noqa: E402
    import web as agent_web  # noqa: E402
    from telethon.tl import types as tl  # noqa: E402
except ImportError as _e:
    agent = None
    AGENT_SKIP = f"нет telethon ({_e}) — запусти tgbot/.venv/bin/python hub/test_buttons.py"

U = 7
MAX_DEPTH, MAX_PRESSES = 4, 400
REAL_URLOPEN = urllib.request.urlopen
REAL_SLEEP = time.sleep
HUB_URL = "https://hub.1-2-3-4.sslip.io"
VISIBLE = {"sendMessage", "editMessageText", "editMessageReplyMarkup", "sendPhoto", "answerCallbackQuery",
           "deleteMessage"}
WITH_MARKUP = {"sendMessage", "editMessageText", "editMessageReplyMarkup", "sendPhoto"}
BAD_OUTPUT = re.compile(r"Traceback|\b\w*(?:Error|Exception|Fail|Cancelled|Exit)\(|^(?:handler|update|watch|web|"
                        r"bots_folder|status|ask|scheduler|moved|visual|history|say):", re.M)
FAKE_TOKEN = "8000000001:AA" + "t" * 33
TEXT_FOR_WAIT = "Тест: добавь кнопку «Привет» и команду /hello"

# Действия с аккаунтом без кнопки «Да» — задокументированные решения (CLAUDE.md, hub/README.md), не баги:
ALLOWED_AUTO = {
    # «🛠 Сделать бота» / «🤖 Создать бота сам» / «🏗 Поставить Студию» — Глеб сам нажал создать: бот в @BotFather
    "tg.py newbot --auto": "создать бота в @BotFather (Глеб нажал «Создать сам» / «Сделать бота» / «Студия»)",
    # новый бот — сразу «Начать» и в папку «Bot» (maker.to_folder)
    "tg.py botsfolder --auto": "новый бот → «Начать» и в папку «Bot» (maker.to_folder)",
    # сразу после входа в аккаунт — «Начать» во всех ботах и папка «Bot» (agent.bots_folder)
    "send_message /start": "после входа: «/start» в своих ботах (agent.bots_folder)",
    "UpdateDialogFilterRequest": "после входа: папка «Bot» (agent.bots_folder)",
}
LOGIN = {"send_code_request", "sign_in"}  # шаги входа: номер и код вводит сам Глеб


class Capture(io.TextIOBase):
    """stdout/stderr всего процесса (и потоков): ловим напечатанные и проглоченные исключения."""

    def __init__(self):
        self.parts, self.lock = [], threading.Lock()

    def write(self, s):
        with self.lock:
            self.parts.append(str(s))
        return len(s)

    def text(self):
        with self.lock:
            return "".join(self.parts)


class SyncThreading:
    """threading для hub/maker: фоновые потоки выполняются сразу — исключения видны нажатию, ответ — до проверки."""
    Thread = None

    def __getattr__(self, name):
        return getattr(threading, name)


class _SyncThread:
    def __init__(self, target=None, args=(), kwargs=None, daemon=None, **_):
        self.target, self.args, self.kwargs = target, args, kwargs or {}

    def start(self):
        self.target(*self.args, **self.kwargs)


SyncThreading.Thread = _SyncThread


class NoSleep:
    """time для hub/maker без ожиданий (рестарт ждёт 4–6 с, деплой 5 с)."""

    def sleep(self, s):
        pass

    def __getattr__(self, name):
        return getattr(time, name)


class Resp(io.BytesIO):
    status = 200
    headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def jlist(pm2):
    return json.dumps([{"name": n, "pm2_env": {"status": s, "pm_uptime": (time.time() - 7200) * 1000, "restart_time": r},
                        "monit": {"memory": 60 * 1048576, "cpu": 2}} for n, (s, r) in pm2.items()])


def msg(text=None, mid=1, **extra):
    m = {"message_id": mid, "date": int(time.time()), "chat": {"id": U, "type": "private"},
         "from": {"id": U, "first_name": "Глеб"}, **extra}
    if text is not None:
        m["text"] = text
    return m


Act = collections.namedtuple("Act", "kind data mid depth path label")


class Crawl:
    """Общая часть: подмены, нажатие, проверки, обход в ширину. Сценарий — mode × аккаунт × Секретарь."""
    https = True  # есть https-адрес — кнопки мини-приложения (web_app) появляются

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hubcrawl-")
        self.root = os.path.join(self.tmp, "repo")  # REPO для hub/maker: тут сокет Секретаря, сюда «ставятся» боты
        os.makedirs(os.path.join(self.root, "tgbot", "tmp"))
        self.calls, self.acc, self.net, self.exits, self.problems, self.runs = [], [], [], [], [], []
        self.lock = threading.Lock()
        self.mid = 1000
        self.current = None
        self.presses = collections.Counter()
        self.pm2 = {"horo-bot": ("online", 1), "coach-bot": ("errored", 7), "planner-bot": ("online", 0),
                    "tg-agent": ("online", 2), "hub-bot": ("online", 0), "bot-quiz": ("online", 0)}
        self.bots_dir = os.path.join(self.tmp, "bots")
        os.makedirs(os.path.join(self.bots_dir, "quiz"))
        with open(os.path.join(self.bots_dir, "registry.json"), "w") as f:  # бот Студии
            json.dump({"quiz": {"name": "Викторина", "pm2": "bot-quiz", "dir": os.path.join(self.bots_dir, "quiz"),
                                "username": "quiz_test_bot", "summary": "викторина по истории"}}, f)
        with open(os.path.join(self.bots_dir, "quiz", ".env"), "w") as f:
            f.write("BOT_TOKEN=8000000002:AA" + "q" * 33 + "\n")
        self.cap = Capture()
        self.old_out, self.old_err, self.old_hook = sys.stdout, sys.stderr, threading.excepthook
        sys.stdout = sys.stderr = self.cap
        threading.excepthook = lambda a: self.cap.write("Traceback (поток): " + "".join(
            traceback.format_exception(a.exc_type, a.exc_value, a.exc_traceback)))
        state = os.path.join(self.tmp, "state.json")
        self.patches = [
            mock.patch.object(hub, "DATA", self.tmp), mock.patch.object(hub, "STATE", state),
            mock.patch.object(hub, "REPO", self.root),
            mock.patch.object(hub, "AGENT_SOCK", os.path.join(self.root, "tgbot", "tmp", "agent.sock")),
            mock.patch.object(hub, "STUDIO_ENV", os.path.join(self.tmp, "studio-bot.env")),
            mock.patch.object(hub, "tg", side_effect=self.fake_tg),
            mock.patch.object(hub, "sh", side_effect=self.fake_sh),
            mock.patch.object(hub, "server", return_value={"mem_total": 2000, "mem_free": 700, "disk_total": 40.0,
                                                           "disk_free": 25.0, "load": "0.4", "uptime": 90000}),
            mock.patch.object(hub, "threading", SyncThreading()), mock.patch.object(hub.maker, "threading", SyncThreading()),
            mock.patch.object(hub, "time", NoSleep()), mock.patch.object(hub.maker, "time", NoSleep()),
            mock.patch.object(hub.maker, "BOTS", self.bots_dir),
            mock.patch.object(hub.maker, "claude", side_effect=self.fake_claude),
            mock.patch.object(hub.media, "voice_text", return_value="голосом: привет"),  # common/media.py — общий
            mock.patch.object(hub.media, "photo_path", side_effect=self.fake_photo),
            mock.patch.object(hub.media, "video", side_effect=lambda token, m, folder=None, **kw: {
                "frames": [self.fake_photo(token, m, folder)], "text": "речь из видео", "seconds": 3}),
            mock.patch.object(urllib.request, "urlopen", self.fake_urlopen),
            mock.patch.object(os, "_exit", self.fake_exit),
            mock.patch.dict(os.environ, {"HUB_URL": HUB_URL if self.https else ""}),
            mock.patch.object(hub, "public_ip", None), mock.patch.object(hub, "plain_buttons", False),
            mock.patch.object(hub, "revived_at", 0.0), mock.patch.object(hub, "_studio_ask", [0.0]),
        ]
        for p in self.patches:
            p.start()
        hub.live.clear()
        hub.setting("owner", U)
        hub.setting("intro", 1)
        hub.setting("paid", (datetime.date.today() + datetime.timedelta(days=5)).isoformat())
        hub.setting("made", {"remind": {"name": "Напоминалка", "pm2": "bot-remind", "dir": os.path.join(self.bots_dir, "remind"),
                                        "username": None, "want": "remind", "summary": "напоминает", "tz": "напоминалка",
                                        "created": time.time()}})
        os.makedirs(os.path.join(self.bots_dir, "remind"), exist_ok=True)
        day = (datetime.date.today() + datetime.timedelta(days=20)).strftime("%d.%m")
        for cmd in ("/remind 09:00 Выпить таблетки", "/remind пн-пт 18:30 Тренировка в зале по расписанию",
                    f"/remind {day} 10:00 Подарок маме"):
            with hub.lock:
                st = hub.load()
                hub.digest.command(st, cmd, time.time())
                hub.save(st)

    def tearDown(self):
        self.stop_secretary()
        for p in reversed(self.patches):
            p.stop()
        sys.stdout, sys.stderr, threading.excepthook = self.old_out, self.old_err, self.old_hook

    # ---------- подмены внешнего мира ----------

    def record(self, who, method, params):
        with self.lock:
            self.mid += 1
            self.calls.append((who, method, params, self.mid))
            return self.mid

    def fake_tg(self, method, **params):
        mid = self.record("hub", method, params)
        return {"ok": True, "result": {"message_id": mid, "chat": {"id": U}} if method == "sendMessage" else True}

    def account(self, what, auto=False):
        """Действие с аккаунтом Глеба (через Секретаря): кто его вызвал и было ли подтверждение."""
        self.acc.append({"what": what, "auto": auto, "act": self.current})

    INSTALLED = {"coach": "coach-bot", "planner": "planner-bot", "kpp": "kpp-bot", "avito": "avito-bot", "eye": "eye-bot",
                 "catcher": "catcher-bot", "studio": "studio-bot", "tgbot": "tg-agent"}

    def fake_sh(self, *cmd, timeout=60, env=None):
        c = [str(x) for x in cmd]
        self.runs.append(c)
        if c[0] == "bash" and c[1].endswith("install.sh"):  # «поставил» — бот появляется в pm2
            name = self.INSTALLED.get(os.path.basename(os.path.dirname(c[1])))
            if name:
                self.pm2[name] = ("online", 0)
            return 0, "ok"
        if c[:2] == ["pm2", "jlist"]:
            return 0, jlist(self.pm2)
        if c[0] == "pm2" and len(c) > 2:
            name = c[c.index("--name") + 1] if "--name" in c else c[2]
            if c[1] in ("restart", "start"):
                self.pm2[name] = ("online", (self.pm2.get(name) or ("", 0))[1] + 1)
            elif c[1] == "delete":
                self.pm2.pop(name, None)
            elif c[1] == "logs":
                return 0, f"{name}: запущен\n\n{name}: всё хорошо <ok>"
            return 0, "ok"
        if c[0] == "git":
            return (0, "abc1234567\n") if "rev-parse" in c else (0, "")
        if any(x.endswith("tg.py") for x in c):
            i = next(i for i, x in enumerate(c) if x.endswith("tg.py"))
            self.account("tg.py " + c[i + 1] + (" --auto" if "--auto" in c else ""), auto="--auto" in c)
            if c[i + 1] == "newbot":
                return 0, f"TOKEN {FAKE_TOKEN} @test_x_bot"
            return 0, "✅ готово"
        return 0, "ok"

    def fake_claude(self, prompt, cwd, on_step=None, timeout=0, tools=True):
        if on_step:
            on_step("читаю README.md")
        if not tools:
            return True, '{"name": "Тестовый", "username": "test_x", "slug": "test-x", "summary": "проверка"}'
        if prompt.startswith("Ты пишешь"):
            with open(os.path.join(cwd, "bot.py"), "w") as f:
                f.write("print('hi')\n")
        return True, "Сделал: кнопка «Привет» и /hello."

    def fake_urlopen(self, req, timeout=None, **kw):
        url = req.full_url if isinstance(req, urllib.request.Request) else str(req)
        if urllib.parse.urlparse(url).hostname in ("127.0.0.1", "localhost"):
            return REAL_URLOPEN(req, timeout=timeout, **kw)
        self.net.append(url)
        m = re.search(r"api\.telegram\.org/bot(\d+):[^/]+/getMe", url)
        if m:
            return Resp(json.dumps({"ok": True, "result": {"id": int(m.group(1)), "is_bot": True,
                                                           "username": f"b{m.group(1)[-4:]}_bot"}}).encode())
        raise urllib.error.URLError("сеть в тесте выключена")

    def fake_photo(self, token, m, folder=None):
        folder = folder or os.path.join(self.tmp, "media")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"p{len(self.calls)}.jpg")
        with open(path, "wb") as f:
            f.write(b"\xff\xd8\xff")
        return path

    def fake_exit(self, code=0):
        self.exits.append((code, self.current))

    # ---------- Секретарь: в наследниках ----------

    def start_secretary(self):
        pass

    def stop_secretary(self):
        pass

    def busy(self):
        return False

    def restore(self):
        """Нажатия не уводят сценарий: режим и вход в аккаунт возвращаются после каждого."""
        hub.setting("mode", self.mode)

    def settle(self, quiet=0.05, limit=8):
        """Ждём, пока Главный и Секретарь закончат отвечать: тишина quiet секунд."""
        end, last, calm = time.monotonic() + limit, None, time.monotonic()
        while time.monotonic() < end:
            mark = (len(self.calls), len(self.cap.text()), self.busy())
            if mark != last or mark[2]:
                last, calm = mark, time.monotonic()
            elif time.monotonic() - calm > quiet:
                return
            REAL_SLEEP(0.01)
        self.problems.append(f"{self.where()}: ответ не затих за {limit} с")

    def where(self, act=None):
        act = act or self.current
        return f"[{self.name}] {act.kind} {act.label or act.data!r} (путь: {' → '.join(act.path) or '—'})"

    # ---------- нажатие и проверки ----------

    def press(self, act):
        self.current = act
        self.presses[act.kind] += 1
        n0, out0, acc0 = len(self.calls), len(self.cap.text()), len(self.acc)
        routed0 = self.routed()
        try:
            if act.kind == "cb":
                hub.on_callback({"id": f"cq{n0}", "from": {"id": U, "first_name": "Глеб"}, "chat_instance": "1",
                                 "data": act.data, "message": msg("карточка", act.mid)})
            elif act.kind == "voice":
                hub.on_message(msg(mid=n0, voice={"file_id": "V", "duration": 2}))
            elif act.kind == "photo":
                hub.on_message(msg(mid=n0, photo=[{"file_id": "P", "width": 9, "height": 9}], caption=act.data))
            elif act.kind == "video":
                hub.on_message(msg(mid=n0, video_note={"file_id": "VN", "duration": 3, "length": 9}))
            elif act.kind == "contact":
                hub.on_message(msg(mid=n0, contact={"phone_number": "+70000000000", "user_id": U, "first_name": "Г"}))
            else:
                hub.on_message(msg(act.data, mid=n0))
        except Exception:
            self.problems.append(f"{self.where()}: исключение\n{traceback.format_exc()}")
        self.settle()
        self.restore()
        new = self.calls[n0:]
        out = self.cap.text()[out0:]
        for line in out.splitlines():
            if BAD_OUTPUT.search(line):
                self.problems.append(f"{self.where()}: в логе: {line[:300]}")
        if not any(m in VISIBLE for _, m, _, _ in new) and self.routed() == routed0:
            self.problems.append(f"{self.where()}: нет видимого ответа")
        for a in self.acc[acc0:]:
            a["act"] = act
        return new

    def routed(self):
        """Сколько обновлений имитация Секретаря получила (без настоящего Секретаря — это и есть ответ на «s:»)."""
        return 0

    def check_markup(self, method, mk):
        """Правила оформления: ≤ 3 кнопки в ряд, текст ≤ 24 символов, callback_data ≤ 64 байт."""
        rows = (mk or {}).get("inline_keyboard") or (mk or {}).get("keyboard") or []
        for row in rows:
            if len(row) > 3:
                self.problems.append(f"{self.where()}: {len(row)} кнопки в ряд ({method}): "
                                     + ", ".join(b.get("text", "?") for b in row))
            for btn in row:
                text = btn.get("text") or ""
                if len(text) > 24:
                    self.problems.append(f"{self.where()}: длинная кнопка ({len(text)}): «{text}»")
                if not text.strip():
                    self.problems.append(f"{self.where()}: кнопка без текста: {btn}")
                data = btn.get("callback_data")
                if data is not None and len(data.encode()) > 64:
                    self.problems.append(f"{self.where()}: callback_data {len(data.encode())} байт: {data!r}")

    def found(self, new, act):
        """Кнопки из ответов: [(вид, данные, id сообщения, подпись)]."""
        out = []
        for who, method, params, mid in new:
            if method not in WITH_MARKUP:
                continue
            mk = params.get("reply_markup")
            if isinstance(mk, str):
                mk = json.loads(mk)
            self.check_markup(method, mk)
            where = params.get("message_id") or mid
            for row in (mk or {}).get("inline_keyboard") or []:
                for btn in row:
                    if "callback_data" in btn:
                        out.append(("cb", btn["callback_data"], where, btn.get("text")))
            for row in (mk or {}).get("keyboard") or []:
                for btn in row:
                    out.append(("contact" if btn.get("request_contact") else "kb", btn["text"], where, btn["text"]))
        return out

    def wait_text(self, before):
        """Главный начал ждать ввод (ТЗ, токен, имя чата) — вводим, как Глеб."""
        wait = hub.setting("wait") or ""
        if not wait or wait == before:
            return None
        if wait in hub.INSTALL or wait.startswith("mtoken|"):
            return FAKE_TOKEN
        return TEXT_FOR_WAIT

    def commands(self):
        cmds = ["/start", "/menu"] + ["/" + c for c, _ in hub.COMMANDS]
        cmds += ["/help", "/server", "/all", "/psy", "/remind", "/reminders", "/digest", "/paid", "/memory", "/logout",
                 "/check", "/cancel", "✦ Меню", "🧠 Разбор чата", "привет, что нового?", "/task найди часы работы Декатлона",
                 "/remind 23:59 тест", "/digest 21:30", "/paid 07.11", "/coach", "/studio", "/eye"]
        return list(dict.fromkeys(cmds))

    def crawl(self):
        self.start_secretary()
        self.restore()
        queue = collections.deque(Act("cmd", c, 0, 0, (), None) for c in self.commands())
        queue += [Act("voice", "", 0, 0, (), "🎙 голосовое"), Act("photo", "что это?", 0, 0, (), "📷 фото"),
                  Act("photo", "", 0, 0, (), "📷 фото без подписи"), Act("video", "", 0, 0, (), "🎬 кружок")]
        seen = {("cmd", c) for c in self.commands()}
        typed = set()
        total = 0
        while queue and total < MAX_PRESSES:
            act = queue.popleft()
            wait0 = hub.setting("wait") or ""
            new = self.press(act)
            total += 1
            kids = self.found(new, act)
            txt, wait = self.wait_text(wait0), hub.setting("wait")
            if txt and wait not in typed:  # Главный ждёт ввод — Глеб вводит сразу, следующим сообщением
                typed.add(wait)
                queue.appendleft(Act("text", txt, 0, act.depth, act.path + ((act.label or act.data)[:40],),
                                     f"ввод при ожидании «{wait}»"))
            if act.depth >= MAX_DEPTH:
                continue
            for kind, data, mid, label in kids:
                key = (kind if kind != "contact" else "kb", data)
                if key in seen:
                    continue
                seen.add(key)
                queue.append(Act(kind, data, mid, act.depth + 1, act.path + ((act.label or act.data)[:40],), label))
        self.total = total
        self.unique = len(seen)
        return total

    def report(self):
        bad = [a for a in self.acc if not self.confirmed(a)]
        lines = [f"[{self.name}] нажатий {self.total} (команды {self.presses['cmd']}, кнопки {self.presses['cb']}, "
                 f"клавиатура {self.presses['kb'] + self.presses['contact']}, ввод {self.presses['text']}, "
                 f"медиа {self.presses['voice'] + self.presses['photo'] + self.presses['video']}), "
                 f"уникальных кнопок и команд {self.unique}, сеть (подменена) {len(self.net)}, "
                 f"действий с аккаунтом {len(self.acc)}, из них без «Да» {len(bad)}"]
        groups = collections.defaultdict(list)
        for a in bad:
            groups[a["what"]].append((a["act"].label or a["act"].data)[:30] if a["act"] else "?")
        for what, by in groups.items():
            note = ALLOWED_AUTO.get(what, "НЕ РАЗРЕШЕНО")
            lines.append(f"    без «Да» ×{len(by)}: {what} — {note}; нажатия: {', '.join(dict.fromkeys(by))}")
        return "\n".join(lines)

    @staticmethod
    def confirmed(a):
        act = a["act"]
        if a["what"] in LOGIN:
            return True
        if act and act.kind == "cb" and re.match(r"(s:)?(ok|all|m:logout!|login:desktop!)", act.data):
            return True
        return bool(act and act.kind == "cb" and (act.label or "").startswith(("Да", "⚡ Да")))

    def check(self):
        self.crawl()
        print(self.report(), file=self.old_err)
        unknown = [a for a in self.acc if not self.confirmed(a) and a["what"] not in ALLOWED_AUTO]
        for a in unknown:
            self.problems.append(f"действие с аккаунтом без подтверждения: {a['what']} ← "
                                 f"{self.where(a['act']) if a['act'] else '?'}")
        self.assertGreater(self.total, 30)
        self.assertFalse(self.problems, "\n" + "\n".join(dict.fromkeys(self.problems)))


# ---------- Секретарь — имитация сокета (без telethon) ----------

class HubOnly(Crawl):
    account_ = {"name": "Глеб Тест", "username": "gleb_test"}

    def start_secretary(self):
        self.sec = FakeSecretary(hub.AGENT_SOCK, self.account_) if self.account_ is not False else None

    def stop_secretary(self):
        if getattr(self, "sec", None):
            self.sec.close()

    def routed(self):
        return len(self.sec.updates()) if getattr(self, "sec", None) else 0


class HubOnlyChatIn(HubOnly, unittest.TestCase):
    name, mode = "имитация · чат · в аккаунте", "chat"

    def test_crawl(self):
        self.check()


class HubOnlyAppOut(HubOnly, unittest.TestCase):
    """Режим «приложение», а https-адреса нет: всё кнопками в чате."""
    name, mode, account_, https = "имитация · приложение без https · без аккаунта", "app", None, False

    def test_crawl(self):
        self.check()


class HubOnlySecretaryDown(HubOnly, unittest.TestCase):
    """Секретарь лежит: всё, что ему, — «⚠️ Секретарь не отвечает» и Главный поднимает его сам."""
    name, mode, account_ = "Секретарь лежит · чат", "chat", False

    def test_crawl(self):
        self.check()
        self.assertIn(["pm2", "restart", "tg-agent"], self.runs)  # «🔄 Перезапустить» поднимает Секретаря


# ---------- настоящий Секретарь (tgbot/agent.py) ----------

class FakeClient:
    """Аккаунт Глеба (Telethon) без сети: чтение отвечает правдоподобно, всё, что меняет Telegram, — записывается."""
    READ = {"connect", "disconnect", "is_user_authorized", "get_me", "get_dialogs", "get_entity", "get_input_entity",
            "get_messages", "iter_messages", "is_connected"}

    def __init__(self, test, authorized):
        self.test, self.authorized = test, authorized
        self.api_id, self.api_hash, self.session = 12345, "a" * 32, mock.Mock()
        now = datetime.datetime.now(datetime.timezone.utc)
        self.me = tl.User(id=100, is_self=True, first_name="Глеб", last_name="Тест", username="gleb_test")
        people = [tl.User(id=5, first_name="Петя"), tl.User(id=6, first_name="Саша", last_name="Очень-Длинная-Фамилия"),
                  tl.User(id=8, first_name="Мама"), tl.User(id=9, first_name="Робот", bot=True, username="b0001_bot")]
        chats = [tl.Chat(id=91, title="8Б класс — очень длинное название группы", photo=tl.ChatPhotoEmpty(),
                         participants_count=25, date=now, version=1),
                 tl.Channel(id=11, title="Новости", photo=tl.ChatPhotoEmpty(), date=now, broadcast=True)]
        self.dialogs = []
        for i, e in enumerate(people + chats):
            title = getattr(e, "title", None) or " ".join(x for x in (e.first_name, e.last_name) if x)
            self.dialogs.append(pytypes.SimpleNamespace(
                id=actions.utils.get_peer_id(e), name=title, entity=e, unread_count=i % 3, unread_mentions_count=i % 2,
                archived=False, is_user=isinstance(e, tl.User), date=now))

    def is_connected(self):
        return True

    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def is_user_authorized(self):
        return self.authorized

    async def get_me(self):
        return self.me

    async def get_dialogs(self, limit=None):
        return list(self.dialogs)

    async def get_entity(self, x):
        for d in self.dialogs:
            if x is d.entity or x in (d.id, d.entity.id) or (isinstance(x, str) and x.lstrip("@").lower() ==
                                             (getattr(d.entity, "username", "") or "").lower()):
                return d.entity
        if isinstance(x, str) and x.startswith("@"):
            return tl.User(id=abs(hash(x)) % 10 ** 9, first_name=x, bot=True, username=x[1:])
        raise ValueError(f"нет {x}")

    get_input_entity = get_entity

    async def iter_participants(self, chat, search="", limit=50):
        for d in self.dialogs:
            if d.is_user and search.casefold() in d.name.casefold():
                yield d.entity

    async def send_code_request(self, phone):
        self.test.account("send_code_request")
        return pytypes.SimpleNamespace(phone_code_hash="h", type=pytypes.SimpleNamespace(length=5))

    async def sign_in(self, *a, **kw):
        self.test.account("sign_in")
        self.authorized = True
        return self.me

    async def log_out(self):
        self.test.account("log_out")
        self.authorized = False

    async def __call__(self, req, *a):
        name = type(req).__name__
        if name.startswith(("Get", "Check")):
            return pytypes.SimpleNamespace(filters=[])
        self.test.account(name)
        return pytypes.SimpleNamespace(chats=[self.dialogs[-1].entity], updates=[])

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        async def write(*a, **kw):
            self.test.account(name + (" /start" if a[1:2] == ("/start",) else ""))
            return pytypes.SimpleNamespace(id=1)
        return write


class Joint(Crawl):
    """Главный и настоящий Секретарь в одном процессе: hub.on_* → сокет → agent.from_hub → «Bot API» (запись)."""
    logged_in = True

    def start_secretary(self):
        t = os.path.dirname(hub.AGENT_SOCK)
        etc = os.path.join(self.tmp, "etc")
        os.makedirs(etc)
        with open(os.path.join(etc, "horo-bot.env"), "w") as f:
            f.write("TELEGRAM_TOKEN=8000000003:AA" + "h" * 33 + "\n")
        with open(os.path.join(t, "settings.json"), "w") as f:
            json.dump({"owner": U, "api_id": "12345", "api_hash": "a" * 32, "bots_folder": not self.logged_in}, f)
        with open(os.path.join(t, "memory.md"), "w") as f:
            f.write("Тренировки по вторникам в 18:00.\n")
        later = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=2)
        with open(os.path.join(t, "jobs.json"), "w") as f:  # 5 расписаний — карточка «Расписание» с кнопками удаления
            json.dump([{"id": f"j{i}", "chats": [{"id": 5, "name": "Петя"}], "time": "07:00", "days": [0, 1, 2, 3, 4],
                        "text": f"доброе утро {i}", "ai": None, "until": None, "sent": i, "next": later.isoformat()}
                       for i in range(5)], f)
        self.client = FakeClient(self, self.logged_in)
        self.inflight = 0
        test = self

        async def safe(handler, ev):
            test.inflight += 1
            try:
                await handler(ev)
            except Exception:
                test.problems.append(f"{test.where()}: исключение у Секретаря\n{traceback.format_exc()}")
            finally:
                test.inflight -= 1

        async def run_claude(self_, text, resume=True, on_step=None):
            if on_step:
                on_step("🔎 ищу в чатах")
            return True, "**Готово.** Всё посмотрел.\nПодробнее: Петя; 8Б класс; Саша Очень-Длинная-Фамилия-Человека"

        async def check_card(self_):
            return agent_ui.card("🩺 Проверка", "✓ Аккаунт\n✓ Claude — отвечает")

        def make_site():
            site = mock.Mock()
            site.send_code.side_effect = agent.get_api.Fail("ERROR")
            return site

        self.patches2 = [
            mock.patch.object(agent, "TMP", t), mock.patch.object(agent, "WORK", os.path.join(t, "work")),
            mock.patch.object(agent, "SETTINGS", os.path.join(t, "settings.json")),
            mock.patch.object(agent, "SOCK", hub.AGENT_SOCK), mock.patch.object(agent, "MEMORY", os.path.join(t, "memory.md")),
            mock.patch.object(agent, "PORT", 0), mock.patch.object(agent, "REPO", self.root),
            mock.patch.object(agent, "HUB_ENV", os.path.join(etc, "hub-bot.env")),
            mock.patch.object(agent, "AGENT_ENV", os.path.join(etc, "tg-agent.env")),
            mock.patch.object(agent.Agent, "app_link", mock.AsyncMock()),
            mock.patch.object(agent.Agent, "run_claude", run_claude),
            mock.patch.object(agent.Agent, "check_card", check_card),
            mock.patch.object(agent.Agent, "make_client", staticmethod(lambda i, h: self.client)),
            mock.patch.object(agent.get_api, "Site", make_site),
            mock.patch.object(botapi.Bot, "_safe", staticmethod(safe)),
            mock.patch.object(actions, "ENV_FILES", [os.path.join(etc, "horo-bot.env")]),
            mock.patch.object(actions, "MADE_BOTS", self.bots_dir),
            mock.patch.object(agent_web, "HUB_PORT", 1),
        ]
        for p in self.patches2:
            p.start()
        self.agent = a = agent.Agent(hub.TOKEN, allowed=0, via_hub=True)
        a.client = self.client
        if self.mode == "app":
            a.app_url = HUB_URL + "/app"

        def post(method, params=None, files=None, timeout=60):
            mid = self.record("sec", method, params or {})
            if method == "getMe":
                return {"id": 111, "is_bot": True, "username": "glavny_test_bot"}
            if method in ("sendMessage", "sendPhoto"):
                return {"message_id": mid, "chat": {"id": U}}
            if method == "getFile":
                raise botapi.BotError("Bad Request: file not found")
            return True
        a.bot._post = post
        self.loop = asyncio.new_event_loop()
        ready = threading.Event()

        def run():
            asyncio.set_event_loop(self.loop)
            self.main = self.loop.create_task(a.main())
            self.loop.call_soon(ready.set)
            try:
                self.loop.run_until_complete(self.main)
            except (asyncio.CancelledError, Exception):
                pass
            rest = asyncio.all_tasks(self.loop)
            for task in rest:
                task.cancel()
            self.loop.run_until_complete(asyncio.gather(*rest, return_exceptions=True))
            self.loop.close()
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        ready.wait(5)
        for _ in range(500):
            if os.path.exists(hub.AGENT_SOCK) and a.web is not None:
                break
            REAL_SLEEP(0.01)
        self.agent_port = a.web.port
        self.patches2.append(mock.patch.object(hub, "AGENT_PORT", self.agent_port))
        self.patches2[-1].start()

    def stop_secretary(self):
        if getattr(self, "loop", None):
            self.loop.call_soon_threadsafe(self.main.cancel)
            self.thread.join(5)
        for p in reversed(getattr(self, "patches2", [])):
            p.stop()
        if agent:
            botapi.PREFIX = ""

    def busy(self):
        return bool(self.inflight or self.agent.busy)

    def restore(self):
        super().restore()
        self.client.authorized = self.logged_in


@unittest.skipIf(agent is None, globals().get("AGENT_SKIP", ""))
class JointChatIn(Joint, unittest.TestCase):
    name, mode, logged_in = "чат · в аккаунте", "chat", True

    def test_crawl(self):
        self.check()


@unittest.skipIf(agent is None, globals().get("AGENT_SKIP", ""))
class JointChatOut(Joint, unittest.TestCase):
    name, mode, logged_in = "чат · без аккаунта", "chat", False

    def test_crawl(self):
        self.check()
        self.assertTrue(any(a["what"] == "sign_in" for a in self.acc))  # прошёл вход кнопками до конца


@unittest.skipIf(agent is None, globals().get("AGENT_SKIP", ""))
class JointAppIn(Joint, unittest.TestCase):
    name, mode, logged_in = "приложение · в аккаунте", "app", True

    def test_crawl(self):
        self.check()


@unittest.skipIf(agent is None, globals().get("AGENT_SKIP", ""))
class JointAppOut(Joint, unittest.TestCase):
    name, mode, logged_in = "приложение · без аккаунта", "app", False

    def test_crawl(self):
        self.check()


@unittest.skipIf(agent is None, globals().get("AGENT_SKIP", ""))
class AccountWritesNeedYes(Joint, unittest.TestCase):
    """Claude просит Секретаря что-то изменить в Telegram (как tg.py через сокет): пока Глеб не нажал «Да» —
    в аккаунте ничего не происходит; «Нет» — не происходит совсем."""
    name, mode, logged_in = "подтверждения", "chat", True

    def ask(self, cmd, args):
        box = {}
        th = threading.Thread(target=lambda: box.update(r=hub.secretary({"cmd": cmd, "args": args}, timeout=20)))
        th.start()
        for _ in range(300):
            cards = [(p, mid) for who, m, p, mid in self.calls if m == "sendMessage" and "Да на всё" in json.dumps(
                p.get("reply_markup"), ensure_ascii=False)]
            if len(cards) > self.cards:
                self.cards = len(cards)
                return th, box, cards[-1]
            REAL_SLEEP(0.01)
        th.join(5)
        self.fail(f"{cmd}: нет карточки «Да/Нет», ответ: {box}")

    def test_writes_wait_for_yes(self):
        self.start_secretary()
        self.restore()
        self.cards = 0
        cases = [("send", {"chat": "Петя", "text": "буду в 6"}), ("leave", {"chat": "8Б класс"}),
                 ("folder", {"name": "Учёба", "chats": ["8Б класс"]}), ("newbot", {"name": "Тест", "username": "t_x"}),
                 ("botsfolder", {"bots": ["b0001_bot"]}), ("reserve", {"title": "Про запас"}),
                 ("grab", {"username": "coolname"}), ("archive", {"chats": ["Петя"]}),
                 ("kick", {"chat": "8Б класс", "users": ["Петя"]}), ("notify", {"chats": ["Петя"], "mode": "off"}),
                 ("job", {"chats": ["Петя"], "time": "07:00", "days": "будни", "text": "доброе утро"})]
        for cmd, args in cases:
            th, box, (params, mid) = self.ask(cmd, args)
            self.assertEqual(self.acc, [], f"{cmd}: изменил аккаунт до «Да»")
            data = next(b["callback_data"] for row in params["reply_markup"]["inline_keyboard"] for b in row
                        if b["text"] == "Нет")
            self.current = Act("cb", data, mid, 1, (cmd,), "Нет")
            hub.on_callback({"id": "x", "from": {"id": U}, "data": data, "message": msg("карточка", mid)})
            th.join(10)
            self.assertTrue(box.get("r", {}).get("ok"), box)
            self.assertIn("не подтвердил", str(box["r"]["out"]), cmd)
            self.assertEqual(self.acc, [], f"{cmd}: изменил аккаунт после «Нет»")
        th, box, (params, mid) = self.ask("send", {"chat": "Петя", "text": "буду в 6"})
        data = params["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        hub.on_callback({"id": "y", "from": {"id": U}, "data": data, "message": msg("карточка", mid)})
        th.join(10)
        self.assertEqual([a["what"] for a in self.acc], ["send_message"])  # «Да» — отправил
        self.settle()
        self.assertFalse(self.problems, self.problems)


# ---------- мини-приложение: все маршруты с мусором ----------

class WebMixin:
    def web_setup(self):
        self.httpd = hub.ThreadingHTTPServer(("127.0.0.1", 0), hub.Web)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def raw(self, method, path, body=b"", headers=None, length=None, timeout=5):
        """Запрос «руками»: свой Content-Length (в том числе мусорный). → (код, тело) или (None, причина)."""
        h = {"Host": "127.0.0.1", "X-Init-Data": init_data(), "Connection": "close", **(headers or {})}
        h["Content-Length"] = str(len(body)) if length is None else length
        req = f"{method} {path} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in h.items()) + "\r\n"
        s = socket.create_connection(("127.0.0.1", self.port), timeout=timeout)
        try:
            s.sendall(req.encode() + body)
            data = b""
            while True:
                try:
                    chunk = s.recv(65536)
                except socket.timeout:
                    return None, "нет ответа (завис)"
                if not chunk:
                    break
                data += chunk
        finally:
            s.close()
        if not data:
            return None, "соединение закрыто без ответа"
        head, _, rest = data.partition(b"\r\n\r\n")
        return int(head.split()[1]), rest

    BODIES = [None, b"", b"garbage", b"[]", b"null", b"{}", b'"x"', b"{\"a\":", b"\xff\xfe", b"1e999",
              b'{"action": ["x"], "arg": {"a": 1}}', b'{"mode": 5}', b'{"mode": "chat"}', b'{"mode": "app"}',
              b'{"bot": {}, "text": [], "photo": 123}', b'{"bot": "coach-bot", "text": "x", "photo": "!!!"}',
              b'{"bot": "coach-bot", "text": "x", "photo": "data:image/png;base64,iVBORw0KGgo="}',
              b'{"text": "", "preset": "psy"}', '{"preset": "psy", "chat": "Петя", "id": true}'.encode(), b'{"id": null}',
              b'{"text": "' + b"x" * 5000 + b'"}', b'{"fresh": "yes", "history": 1}']
    ACTIONS = ["restart", "logs", "update", "mode", "secretary", "reminder_delete", "nope", "", None]
    ARGS = ["coach-bot", "hub-bot", "tg-agent", "нет-такого", "../../etc", "1", "999", "", None, 5, ["x"]]

    def sweep(self, routes):
        bad = []
        for path in routes:
            for method in ("GET", "POST"):
                for body in self.BODIES if method == "POST" else [None]:
                    code, res = self.raw(method, path, body or b"")
                    if code is None or code >= 500 and code != 502:
                        bad.append(f"{method} {path} {body[:60] if body else body!r} → {code} {res[:200]!r}")
            for length in ("abc", "-5"):
                code, res = self.raw("POST", path, b"{}", length=length, timeout=3)
                if code is None or code >= 500 and code != 502:
                    bad.append(f"POST {path} Content-Length={length} → {code} {res!r}")
        return bad

    def sweep_actions(self):
        bad = []
        for action in self.ACTIONS:
            for arg in self.ARGS:
                body = json.dumps({"action": action, "arg": arg}).encode()
                code, res = self.raw("POST", "/app/api/action", body)
                if code is None or code >= 500:
                    bad.append(f"action={action!r} arg={arg!r} → {code} {res[:200]!r}")
        return bad


class WebApiHub(Crawl, WebMixin, unittest.TestCase):
    """/app/api/* Главного; Секретаря нет — /app/agent/api/* отвечают 502, а не падают."""
    name, mode = "мини-приложение", "chat"

    def test_routes(self):
        self.web_setup()
        self.current = Act("web", "", 0, 0, (), "мини-приложение")
        out0 = len(self.cap.text())
        routes = ["/app/api/state", "/app/api/action", "/app/api/mode", "/app/api/support", "/app/api/nope",
                  "/app/agent/api/state", "/app/agent/api/ask", "/app/agent/health", "/app/health", "/app", "/app/app.js",
                  "/app/../hub.py", "/app/agent/../x", "/health"]
        bad = self.sweep(routes) + self.sweep_actions()
        for wrong in (init_data(uid=99), init_data(bad=True), "", "garbage"):  # чужой и битая подпись — 401/403
            for path in ("/app/api/state", "/app/api/action", "/app/api/support", "/app/api/mode"):
                code, _ = self.raw("POST", path, b'{"action": "restart", "arg": "coach-bot"}', {"X-Init-Data": wrong})
                if code not in (401, 403):
                    bad.append(f"{path} с подписью {wrong[:20]!r} → {code}")
        self.settle()
        bad += [ln for ln in self.cap.text()[out0:].splitlines() if BAD_OUTPUT.search(ln)]
        self.assertFalse(bad, "\n" + "\n".join(bad))


@unittest.skipIf(agent is None, globals().get("AGENT_SKIP", ""))
class WebApiJoint(Joint, WebMixin, unittest.TestCase):
    """Вкладка «Секретарь»: /app/agent/api/* через Главного в настоящий tgbot/web.py."""
    name, mode, logged_in = "мини-приложение + Секретарь", "app", True

    def test_routes(self):
        self.start_secretary()
        self.web_setup()
        self.current = Act("web", "", 0, 0, (), "мини-приложение")
        out0 = len(self.cap.text())
        with mock.patch.object(agent_web, "HUB_PORT", self.port):
            routes = ["/app/agent/api/" + r for r in ("state", "run", "ask", "stop", "new", "login", "jobs/delete",
                                                      "memory", "memory/save", "support", "nope")]
            bad = self.sweep(routes + ["/app/api/state", "/app/api/action", "/app/api/support"])
            self.settle()
        bad += [f"{t}" for t in self.problems]
        bad += [ln for ln in self.cap.text()[out0:].splitlines() if BAD_OUTPUT.search(ln)]
        self.assertFalse(bad, "\n" + "\n".join(bad))


if __name__ == "__main__":
    unittest.main()
