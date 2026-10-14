#!/usr/bin/env python3
"""«Нажать каждую кнопку» Ловца без сети и без Секретаря: python3 catcher/test_buttons.py

Обходчик шлёт /start и все команды бота (bot.commands), собирает все inline-кнопки (callback_data) и кнопки
клавиатуры из того, что бот отправил (sendMessage / editMessageText / editMessageReplyMarkup), и нажимает каждую
через ту же точку входа, что цикл getUpdates (bot.handle: поддержка → кнопки/команды). Обход в ширину, глубина ≤ 4,
не больше ~400 нажатий, в обоих режимах (💬 чат / 📱 приложение) и без приложения: с именами (слежу, пойман,
пауза, Fragment), ловлей по пожеланиям, своими каналами с русскими названиями, с пустым состоянием и когда
Секретарь недоступен. Падает на: исключении (и проглоченном с traceback в выводе), нажатии без видимого ответа,
тексте кнопки > 24 символов, > 3 кнопках в ряду, callback_data > 64 байт, пустом/битом HTML.
Плюс все действия мини-приложения (bot.call_api — его зовёт и HTTP-сервер) с пустыми и мусорными телами — без 500.

Всё внешнее подменено: Telegram (bot._call), Секретарь (FakeAgent из test_catcher.py), Claude (kit.claude),
сеть (urlopen), subprocess.
"""
import collections
import contextlib
import html.parser
import io
import json
import os
import sys
import tempfile
import traceback
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import catcher as app  # noqa: E402
import botkit as kit  # noqa: E402
from test_catcher import FakeAgent  # noqa: E402  Секретарь понарошку — тот же, что в тестах Ловца

sys.path.insert(0, os.path.join(os.path.dirname(HERE), "common"))
import support  # noqa: E402

OWNER = 1


# ---------- общий обходчик (такой же в avito/ и catcher/test_buttons.py) ----------

VISIBLE = {"sendMessage", "editMessageText", "editMessageReplyMarkup", "sendPhoto", "sendMediaGroup",
           "sendDocument", "deleteMessage"}
TAGS = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "code", "pre", "a", "blockquote",
        "tg-spoiler", "span"}
ERR_MARKS = ("Traceback", "Что-то сломалось", "Что-то пошло не так")


class _Html(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.bad = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in TAGS:
            self.bad.append(f"тег <{tag}> Telegram не знает")
        else:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        else:
            self.bad.append(f"лишний </{tag}>")


def html_problems(text):
    p = _Html()
    p.feed(text)
    p.close()
    return p.bad + [f"не закрыт <{t}>" for t in p.stack]


def buttons_of(params):
    """[(kind, value)] — inline callback_data ("cb") и тексты обычной клавиатуры ("text")."""
    rm = params.get("reply_markup") or {}
    out = [("cb", btn["callback_data"]) for row in rm.get("inline_keyboard") or [] for btn in row
           if "callback_data" in btn]
    out += [("text", btn if isinstance(btn, str) else btn.get("text", "")) for row in rm.get("keyboard") or []
            for btn in row]
    return out


def markup_problems(params):
    rm = params.get("reply_markup") or {}
    probs = []
    for kind in ("inline_keyboard", "keyboard"):
        for row in rm.get(kind) or []:
            if len(row) > 3:
                probs.append(f"{len(row)} кнопок в ряду: {[x if isinstance(x, str) else x.get('text') for x in row]}")
            for btn in row:
                text = btn if isinstance(btn, str) else btn.get("text") or ""
                if not text.strip():
                    probs.append(f"пустой текст кнопки: {btn}")
                if len(text) > 24:
                    probs.append(f"текст кнопки {len(text)} > 24: {text!r}")
                if kind == "inline_keyboard":
                    if "callback_data" in btn:
                        n = len(str(btn["callback_data"]).encode())
                        if not 1 <= n <= 64:
                            probs.append(f"callback_data {n} байт: {btn['callback_data']!r}")
                    elif not any(k in btn for k in ("url", "web_app", "copy_text", "login_url",
                                                    "switch_inline_query", "switch_inline_query_current_chat")):
                        probs.append(f"кнопка без действия: {btn}")
    return probs


def message_problems(method, p):
    probs = []
    if method in ("sendMessage", "editMessageText"):
        text = p.get("text")
        if not isinstance(text, str) or not text.strip():
            probs.append("пустой текст сообщения (Telegram ответит 400)")
        else:
            if len(text) > 4096:
                probs.append(f"текст {len(text)} > 4096")
            if p.get("parse_mode") == "HTML":
                probs += [f"HTML: {x}" for x in html_problems(text)]
            probs += [f"в тексте ошибка: {m!r} — {text[:200]!r}" for m in ERR_MARKS if m in text]
    if method.startswith("edit") and not p.get("message_id"):
        probs.append("правка без message_id")
    if method in VISIBLE and p.get("chat_id") is None:
        probs.append("нет chat_id")
    if method == "answerCallbackQuery" and len(p.get("text") or "") > 200:
        probs.append("всплывашка длиннее 200")
    return probs + markup_problems(p)


def visible(method, p):
    return method in VISIBLE or (method == "answerCallbackQuery" and bool((p.get("text") or "").strip()))


class FakeTG:
    """Telegram понарошку: копит вызовы [(метод, параметры, message_id ответа)]."""

    def __init__(self):
        self.calls, self.n = [], 100

    def __call__(self, method, **p):
        self.n += 1
        self.calls.append((method, p, self.n))
        if method == "getMe":
            return {"ok": True, "result": {"id": 9, "username": "test_bot"}}
        if method == "getFile":
            return {"ok": True, "result": {"file_path": "x.jpg"}}
        return {"ok": True, "result": {"message_id": self.n}}


class Crawler:
    """BFS по кнопкам. send(text) / press(data, chat, mid) — та же точка входа, что у бота в цикле."""

    def __init__(self, tg, send, press, label, cap=400, depth=4):
        self.tg, self.send_fn, self.press_fn, self.label = tg, send, press, label
        self.cap, self.depth = cap, depth
        self.problems, self.seen = [], set()
        self.n_press = self.n_send = 0
        self.pressed = []

    def act(self, what, fn):
        before = len(self.tg.calls)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            try:
                fn()
            except Exception:
                traceback.print_exc()
        new = self.tg.calls[before:]
        out = buf.getvalue()
        where = f"[{self.label}] {what}"
        if "Traceback" in out:
            self.problems.append(f"{where}: исключение\n{out[-2500:]}")
        if not any(visible(m, p) for m, p, _ in new):
            self.problems.append(f"{where}: нет видимого ответа (вызовы: {[m for m, _, _ in new]})")
        found = []
        for m, p, rid in new:
            self.problems += [f"{where} → {m}: {x}" for x in message_problems(m, p)]
            mid = p.get("message_id") if m.startswith("edit") else rid
            found += [(kind, val, p.get("chat_id"), mid) for kind, val in buttons_of(p)]
        return found

    def send(self, text):
        self.n_send += 1
        return self.act(f"сообщение {text!r}", lambda: self.send_fn(text))

    def press(self, data, chat, mid):
        self.n_press += 1
        self.pressed.append(data)
        return self.act(f"кнопка {data!r}", lambda: self.press_fn(data, chat, mid))

    def run(self, seeds):
        q = collections.deque()
        for text in seeds:
            self._add(q, self.send(text), 1)
        while q and self.n_press < self.cap:
            depth, kind, val, chat, mid = q.popleft()
            found = self.press(val, chat, mid) if kind == "cb" else self.send(val)
            if depth < self.depth:
                self._add(q, found, depth + 1)
        return self

    def _add(self, q, found, depth):
        for kind, val, chat, mid in found:
            if (kind, val) not in self.seen:
                self.seen.add((kind, val))
                q.append((depth, kind, val, chat, mid))


def support_stub(tg, allowed, starts):
    """🛠 поддержка как в main(), но без потоков, Claude и сети: start только записывает просьбу."""
    s = support.Support.__new__(support.Support)
    s.token, s.name, s.folders, s.pm2, s.allowed = "1:test", "Тест", ["x"], "test-bot", allowed
    s.tests, s.cwd, s.media_dir, s.waiting, s.plain = [], "/nonexistent", tempfile.mkdtemp(), {}, False
    s.tg = tg
    s.start = lambda chat, text, shots=None: starts.append((chat, text, shots))
    return s


def no_network(*a, **k):
    raise AssertionError("в тесте нет сети")


GUARDS = [mock.patch("urllib.request.urlopen", no_network), mock.patch("subprocess.run", no_network),
          mock.patch("subprocess.Popen", no_network)]


# ---------- Ловец ----------

NAMES = [{"name": "busyname", "status": "watch", "added": "14.10 10:00", "checks": 3, "note": "кто-то успел раньше"},
         {"name": "averyveryverylongusername_32ch", "status": "watch", "added": "14.10 10:00", "checks": 1},
         {"name": "pausedname", "status": "paused", "added": "14.10 10:00", "note": "лимит публичных каналов"},
         {"name": "goldie", "status": "fragment", "added": "14.10 10:00", "note": "продаётся на Fragment"},
         {"name": "bad__name", "status": "invalid", "added": "14.10 10:00"},
         {"name": "caughtone", "status": "caught", "added": "14.10 10:00", "link": "https://t.me/caughtone",
          "channel": -1001, "title": "Канал <пойман> & ок"}]
CHANNELS = [{"id": -1000000000000777, "title": "Мой очень длинный канал про космос и звёзды", "username": ""},
            {"id": -1000000000000778, "title": "Заметки", "username": ""},
            {"id": -1000000000000779, "title": "С юзернеймом", "username": "taken_one"}]
IDEAS = ["nexusorbit", "cosmo", "starlord7", "космос", "a__b", "zenith", "lunarix", "ab", "freebie"]


def full_state(mode, hunt=False):
    db = {"_owner": OWNER, "names": [dict(n) for n in NAMES],
          "settings": {"interval": 10, "target": -1000000000000778, "target_title": "Заметки"},
          "wish": {"len": "5", "style": "word", "theme": "космос и далёкие галактики", "letters": True, "count": 3},
          "hunt": {"on": hunt, "checked": 12, "caught": ["orbit"], "seen": ["nexus"], "queue": ["pulse", "frame"],
                   "started": "14.10 09:00"}}
    if mode:
        db["_mode"] = mode
    return db


class Harness:
    def __init__(self, db, app_url="https://catcher.test/", agent=None, claude=None):
        self.tg, self.starts = FakeTG(), []
        self.t = app.bot.testing(owner=OWNER)  # память вместо data/db.json, bot.later — сразу
        app.bot.db.data.clear()
        app.bot.db.data.update(json.loads(json.dumps(db)))
        app.bot.waits.clear()
        self.agent = agent or FakeAgent({"freebie": "free", "busyname": "taken", "goldie": "fragment",
                                         "nexusorbit": "free", "zenith": "free", "cosmo": "taken"})
        self.agent.channels = [dict(c) for c in CHANNELS]
        app.agent = self.agent
        app.watcher = app.Watcher()
        self.sup = support_stub(self.tg, lambda uid: bool(uid) and uid == app.bot.owner, self.starts)
        self.mid = 0

        def claude_fake(prompt, timeout=180, model=None, as_json=False):
            if claude is False:
                raise kit.Fail("ИИ на сервере не установлен")
            return list(IDEAS)
        self.patches = GUARDS + [mock.patch.object(app.bot, "_call", lambda method, params, files=None:
                                                   self.tg(method, **params)),
                                 mock.patch.object(app.bot, "app_url", app_url),
                                 mock.patch.object(app.bot, "support", self.sup),
                                 mock.patch.object(kit, "claude", claude_fake)]

    def __enter__(self):
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *a):
        for p in reversed(self.patches):
            p.stop()

    def send(self, text, uid=OWNER):
        self.mid += 1
        app.bot.handle({"update_id": self.mid, "message": {
            "message_id": self.mid, "chat": {"id": uid, "type": "private"},
            "from": {"id": uid, "first_name": "Глеб"}, "text": text}})

    def press(self, data, chat, mid, uid=OWNER):
        app.bot.handle({"update_id": 1, "callback_query": {
            "id": "q", "data": data, "from": {"id": uid},
            "message": {"message_id": mid or 1, "chat": {"id": chat or uid, "type": "private"}}}})


def commands():
    return [f"/{c}" for c, _ in app.bot.commands] + ["/app", "/fix", "/ideas крипта", "/nosuchcommand"]


SEED_TEXTS = ["@freebie", "busyname t.me/another_one, @Third_Name", "придумай крутые имена про космос", "@abc",
              "просто текст", "/wish", "космос", "/ideas космос и далёкие галактики, звёзды и чёрные дыры", "/ideas",
              "любые"]
# все виды кнопок бота (первая часть callback_data) — каждую обходчик должен нажать хотя бы раз
ALL_BUTTONS = {"list", "rm", "resume", "clear_caught", "add", "grab", "watch", "ideas", "wish", "wl", "ws", "wt", "wc",
               "wtheme", "wtheme0", "hunt", "settings", "iv", "target", "channels", "home", "mode", "mode:chat",
               "mode:app", "sup"}


class Buttons(unittest.TestCase):
    report, pressed = {}, set()

    def crawl(self, label, db, seeds=None, **kw):
        """Обход с командами и текстами (имена, идеи, пожелания) — и с одними командами."""
        runs = [(label, seeds or commands() + SEED_TEXTS)] + ([] if seeds else [(label + " · команды", commands())])
        for name, sd in runs:
            with Harness(db, **kw) as h:
                c = Crawler(h.tg, h.send, h.press, name).run(sd)
            self.assertEqual(c.problems, [], "\n\n".join(c.problems[:15]))
            self.assertGreater(c.n_press, 0)
            Buttons.report[name] = (c.n_send, c.n_press)
            Buttons.pressed |= {x.split("|")[0] for x in c.pressed}
        return c

    def test_full_chat(self):
        self.crawl("данные · чат", full_state("chat"))

    def test_full_app(self):
        self.crawl("данные · приложение", full_state("app"))

    def test_hunting(self):
        self.crawl("ловлю по пожеланиям", full_state("chat", hunt=True))

    def test_first_start(self):
        self.crawl("режим не выбран", full_state(None))

    def test_no_app_url(self):
        self.crawl("без https", full_state("app"), app_url="")

    def test_empty(self):
        self.crawl("пусто", {"_owner": OWNER, "_mode": "chat"}, claude=False)
        self.crawl("вообще пусто", {}, claude=False)

    def test_agent_down(self):
        self.crawl("Секретарь не запущен", full_state("chat"), agent=FakeAgent(down=True))

    def test_not_logged_in(self):
        self.crawl("Секретарь не вошёл", full_state("chat"), agent=FakeAgent(logged=False))

    def test_stranger(self):
        with Harness(full_state("chat")) as h:
            c = Crawler(h.tg, lambda t: h.send(t, uid=99), lambda d, ch, mid: h.press(d, 99, mid, uid=99), "чужой")
            c.run(commands())
            for data in ("list", "rm|busyname", "hunt|on", "mode:app", "sup|ask", "iv|3"):
                c.press(data, 99, 5)
        bad = [p for p in c.problems if "нет видимого ответа" not in p]  # чужому кнопки молча гасятся
        self.assertEqual(bad, [], "\n\n".join(bad[:15]))
        self.assertIsNotNone(app.find("busyname"))

    def test_zz_all_buttons_pressed(self):
        if not Buttons.pressed:
            self.skipTest("нужен весь класс Buttons")
        self.assertEqual(ALL_BUTTONS - Buttons.pressed, set())

    @classmethod
    def tearDownClass(cls):
        print("\nЛовец, обход кнопок (сообщений · нажатий):",
              "; ".join(f"{k}: {s}·{p}" for k, (s, p) in sorted(cls.report.items())), file=sys.stderr)


# ---------- мини-приложение: все действия с мусором ----------

WEIRD = [None, "", "x", 0, -1, 10 ** 20, 1.5, True, [], [1, "a"], {}, {"a": 1}, "<b>", "@freebie", "busyname",
         "on", "off", "chat", "5", 5, 30]
KEYS = ["text", "name", "interval", "target", "target_title", "len", "style", "theme", "letters", "count", "hunt",
        "mode", "photo"]


class WebApi(unittest.TestCase):
    count = 0

    def run_all(self, db, **kw):
        with Harness(db, **kw):
            names = sorted(app.bot.handlers["api"]) + ["support", "nosuch"]
            bodies = [{}, [], "x", 5, None, [{"text": "@freebie"}]]
            bodies += [{k: v} for k in KEYS for v in WEIRD] + [{k: v for k in KEYS} for v in WEIRD]
            fails = []
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                for name in names:
                    for body in bodies:
                        for user in ({"id": OWNER, "first_name": "Глеб"}, {"id": 99}, None):
                            code, res = app.bot.call_api(name, user, body)
                            WebApi.count += 1
                            if code == 500 or not isinstance(res, dict):
                                fails.append(f"api/{name} {json.dumps(body, ensure_ascii=False)[:80]} "
                                             f"от {user}: {code} {str(res)[:200]}")
                            json.dumps(res, ensure_ascii=False)  # ответ должен уйти в JSON
            if "Traceback" in buf.getvalue():
                fails.append("исключение в выводе:\n" + buf.getvalue()[-2500:])
        self.assertEqual(fails, [], "\n".join(fails[:20]))

    def test_api_full(self):
        self.run_all(full_state("app"))

    def test_api_empty(self):
        self.run_all({"_owner": OWNER}, claude=False)

    def test_api_agent_down(self):
        self.run_all(full_state("app"), agent=FakeAgent(down=True))

    @classmethod
    def tearDownClass(cls):
        print(f"\nЛовец, вызовов мини-приложения: {cls.count}", file=sys.stderr)


if __name__ == "__main__":
    unittest.main()
