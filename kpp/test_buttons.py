#!/usr/bin/env python3
"""«Нажать каждую кнопку» КПП без сети: python3 kpp/test_buttons.py

Обходчик шлёт /start и все команды бота, собирает все inline-кнопки (callback_data) и кнопки клавиатуры из того,
что бот отправил (sendMessage / editMessageText / editMessageReplyMarkup), и нажимает каждую через ту же точку
входа, что цикл getUpdates (поддержка → on_callback / on_message под LOCK). Обход в ширину, глубина ≤ 4,
не больше ~400 нажатий, в обоих режимах (💬 чат / 📱 приложение), с данными и с пустым состоянием,
и отдельно — когда Домиленд падает. Падает на: исключении (и проглоченном с traceback в выводе), нажатии без
видимого ответа, тексте кнопки > 24 символов, > 3 кнопках в ряду, callback_data > 64 байт, пустом/битом HTML.
Плюс все маршруты мини-приложения с пустыми и мусорными телами — без 500.

Всё внешнее подменено: Telegram, Домиленд/Яндекс, claude -p (subprocess), сеть (urlopen).
"""
import collections
import contextlib
import html.parser
import io
import json
import os
import sys
import tempfile
import threading
import time
import traceback
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["KPP_DATA"] = tempfile.mkdtemp()
import domyland as dl  # noqa: E402
import kpp  # noqa: E402
import support  # noqa: E402  (common/ уже в sys.path через kpp)

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


# ---------- КПП ----------

PLATE, PLATE2 = "А123ВС777", "М456ОР50"
PLACES = [{"id": 5, "building": 6, "title": "Мосфильмовская ул, д. 70", "flat": True},
          {"id": 7, "building": 8, "title": "г. Москва, ЖК «Сад», корпус 2, подъезд 3", "flat": True},
          {"id": 11, "building": 8, "title": "Кладовая №4", "flat": False}]
PASSES = [{"id": 9, "title": "Такси", "status": {"title": "Активен"}, "subtitle": "А 123 ВС 777",
           "fields": [{"title": "Дата", "value": "сегодня, 12:00"}], "actions": [{"type": "cancel"}]},
          {"id": 10, "title": "Гостевой пропуск на длинное имя", "status": {"title": "Использован"},
           "subtitle": "Очень длинное описание пропуска <без номера>", "fields": [], "actions": [{"type": "cancelPass"}]},
          {"id": 12, "title": "Такси", "status": {}, "subtitle": "", "fields": [], "actions": []}]
AUTOPASS = {"screenTitle": "Автопропуск", "screenDescription": "Ворота откроются сами <для своих>",
            "places": [{"id": 5, "title": "Мосфильмовская ул, д. 70, подъезд 1, шлагбаум у въезда",
                        "isActive": True, "description": "Номера из профиля"},
                       {"id": 7, "title": "ЖК «Сад»", "isActive": False}]}


def full_state(mode):
    now = time.time()
    return {"owner": OWNER, "jwt": "j", "yandex_token": "y0_fake", "place_id": 5, "building_id": 6,
            "address": PLACES[0]["title"], "places": [dict(p) for p in PLACES], "taxi": [1, 5], "name": "Тест",
            "last": PLATE, "recent": {PLATE: {"ts": now - 60, "id": 9}, PLATE2: {"ts": now - 3600, "id": 10}},
            "guests": {"42": {"name": "Аня"}, "43": {"name": "Очень Длинное Имя Гостя Для Проверки Кнопки"}},
            "invites": {"abc": now}, "mode": mode}


class Domyland:
    """Домиленд понарошку. fail — что кидать из каждого вызова (dl.Error / dl.AuthError) или None."""

    def __init__(self, empty=False, fail=None):
        self.empty, self.fail, self.n = empty, fail, 20

    def patches(self):
        me = self

        def wrap(fn):
            def call(client, *a, **k):
                if me.fail:
                    raise me.fail("Домиленд не отвечает (тест)")
                return fn(client, *a, **k)
            return call

        def login(client):
            client.jwt = "j2"
            return "j2"

        def create(client, plate, *a, **k):
            me.n += 1
            return {"items": [{"id": me.n}]}
        fns = {"login": login, "me": lambda c: {"fullName": "Тест Тестов", "shortName": "Тест"},
               "places": lambda c: [] if me.empty else [
                   {"id": 5, "buildingId": 6, "placeTypeId": 1, "address": "Мосфильмовская ул, д. 70 кв 854"},
                   {"id": 7, "buildingId": 8, "placeTypeId": 1,
                    "address": "г. Москва, ЖК «Сад», корпус 2, подъезд 3, кв. 1"}],
               "calibrate": lambda c: (1, 5), "passes": lambda c, *a: [] if me.empty else [dict(x) for x in PASSES],
               "cancel": lambda c, pid: {}, "create_taxi_pass": create,
               "autopass": lambda c: {"places": []} if me.empty else json.loads(json.dumps(AUTOPASS)),
               "set_autopass": lambda c, places: {}}
        return [mock.patch.object(dl.Client, k, wrap(v)) for k, v in fns.items()]


class Harness:
    def __init__(self, state, public_url="https://kpp.test", dom=None):
        self.tg, self.starts = FakeTG(), []
        self.sup = support_stub(self.tg, kpp.is_owner, self.starts)
        self.mid = 0
        kpp.st = state
        self.patches = GUARDS + [mock.patch.object(kpp, "tg", self.tg), mock.patch.object(kpp, "save", lambda s: None),
                                 mock.patch.object(kpp, "public_url", public_url),
                                 mock.patch.object(kpp, "TOKEN", "1:test"),
                                 mock.patch.object(kpp, "spawn", lambda fn, *a: None),
                                 mock.patch.object(kpp, "update", lambda chat: kpp.send(chat, "обновление (тест)")),
                                 mock.patch.object(kpp, "SUP", self.sup)] + (dom or Domyland()).patches()

    def __enter__(self):
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *a):
        for p in reversed(self.patches):
            p.stop()

    def dispatch(self, upd):
        """Как цикл в kpp.main(): сначала поддержка, потом обработчики под LOCK."""
        if self.sup.intercept(upd):
            return
        with kpp.LOCK:
            if upd.get("callback_query"):
                kpp.on_callback(upd["callback_query"])
            elif upd.get("message"):
                kpp.on_message(upd["message"])

    def send(self, text, uid=OWNER):
        self.mid += 1
        self.dispatch({"update_id": self.mid, "message": {
            "message_id": self.mid, "chat": {"id": uid, "type": "private"},
            "from": {"id": uid, "first_name": "Глеб"}, "text": text}})

    def press(self, data, chat, mid, uid=OWNER):
        self.dispatch({"update_id": 1, "callback_query": {
            "id": "q", "data": data, "from": {"id": uid},
            "message": {"message_id": mid or 1, "chat": {"id": chat or uid, "type": "private"}}}})


def commands():
    """Команды, которые бот ставит в меню (setMyCommands), + /start, /fix и скрытые."""
    tg, keep = FakeTG(), kpp.st
    with mock.patch.object(kpp, "tg", tg), mock.patch.object(kpp, "save", lambda s: None):
        kpp.st = {}
        kpp.setup_bot()
    kpp.st = keep
    cmds = [c["command"] for m, p, _ in tg.calls if m == "setMyCommands" for c in p["commands"]]
    assert cmds, "бот не ставит команды"
    return ["/start"] + ["/" + c for c in cmds] + ["/fix", "/guests", "/nosuchcommand"]


SEED_TEXTS = [PLATE2, PLATE, "привет", "y0_" + "a" * 30]  # номер, тот же номер ещё раз, текст, вход по токену
# все виды кнопок бота (первая часть callback_data) — каждую обходчик должен нажать хотя бы раз
ALL_BUTTONS = {"x", "again", "rp", "pl", "ap", "gdel", "mode", "mode:chat", "mode:app", "m", "sup"}


class Buttons(unittest.TestCase):
    report, pressed = {}, set()

    def crawl(self, label, state, seeds=None, **kw):
        """Обход с командами и текстами (новые пропуска, вход) — и с одними командами (кнопки ближе к корню)."""
        runs = [(label, seeds or commands() + SEED_TEXTS)] + ([] if seeds else [(label + " · команды", commands())])
        for name, sd in runs:
            with Harness(json.loads(json.dumps(state)), **kw) as h:
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

    def test_zz_all_buttons_pressed(self):
        if not Buttons.pressed:
            self.skipTest("нужен весь класс Buttons")
        self.assertEqual(ALL_BUTTONS - Buttons.pressed, set())

    def test_first_start(self):
        st = full_state("chat")
        st.pop("mode")
        self.crawl("режим не выбран", st)

    def test_empty(self):
        self.crawl("пусто · чат", {"owner": OWNER, "mode": "chat"}, dom=Domyland(empty=True))
        self.crawl("пусто · приложение", {"owner": OWNER, "mode": "app"}, dom=Domyland(empty=True))
        self.crawl("вообще пусто", {}, dom=Domyland(empty=True))

    def test_no_place(self):
        st = full_state("chat")
        for k in ("place_id", "building_id", "address"):
            st.pop(k)
        self.crawl("без адреса", st)

    def test_no_app_url(self):
        self.crawl("без https", full_state("app"), public_url="")

    def test_domyland_down(self):
        self.crawl("Домиленд падает", full_state("chat"), dom=Domyland(fail=dl.Error))

    def test_domyland_auth(self):
        self.crawl("вход устарел", full_state("chat"), dom=Domyland(fail=dl.AuthError))

    def test_guest(self):
        """Гость нажимает всё, что ему показали: владельческое — «только владелец», без исключений."""
        st = full_state("chat")
        st["guests"]["42"] = {"name": "Аня"}
        with Harness(st) as h:
            c = Crawler(h.tg, lambda t: h.send(t, uid=42), lambda d, ch, mid: h.press(d, 42, mid, uid=42),
                        "гость").run(commands() + SEED_TEXTS + ["/login", "/place", "/auto", "/invite", "/logout"])
        self.assertEqual(c.problems, [], "\n\n".join(c.problems[:15]))
        self.assertEqual(kpp.st["jwt"], "j")  # гость ничего не сломал
        Buttons.report["гость"] = (c.n_send, c.n_press)

    def test_guest_own_login(self):
        """Гость со своим входом в Домиленд и своей историей (режим приложения): все его кнопки."""
        st = full_state("chat")
        now = time.time()
        st["users"] = {"42": {"yandex_token": "y0_guest", "jwt": "jg", "places": [dict(p) for p in PLACES[1:]],
                              "place_id": 7, "building_id": 8, "address": PLACES[1]["title"], "mode": "app",
                              "last": PLATE2, "recent": {PLATE2: {"ts": now - 30, "id": 10}},
                              "mine": [{"id": 10, "plate": PLATE2, "ts": int(now), "shared": False}]}}
        with Harness(st) as h:
            c = Crawler(h.tg, lambda t: h.send(t, uid=42), lambda d, ch, mid: h.press(d, 42, mid, uid=42),
                        "гость со своим входом").run(commands() + SEED_TEXTS + ["/login", "/logout"])
        self.assertEqual(c.problems, [], "\n\n".join(c.problems[:15]))
        self.assertEqual(kpp.st["jwt"], "j")
        Buttons.report["гость со своим входом"] = (c.n_send, c.n_press)

    def test_stranger(self):
        with Harness(full_state("chat")) as h:
            c = Crawler(h.tg, lambda t: h.send(t, uid=99), lambda d, ch, mid: h.press(d, 99, mid, uid=99), "чужой")
            c.run(commands())
            for data in ("m|passes", "x|9", "pl|5", "mode:app", "sup|ask"):
                c.press(data, 99, 5)
        bad = [p for p in c.problems if "нет видимого ответа" not in p]  # чужому кнопки молча гасятся
        self.assertEqual(bad, [], "\n\n".join(bad[:15]))
        self.assertEqual(kpp.st["mode"], "chat")

    @classmethod
    def tearDownClass(cls):
        print("\nКПП, обход кнопок (сообщений · нажатий):",
              "; ".join(f"{k}: {s}·{p}" for k, (s, p) in sorted(cls.report.items())), file=sys.stderr)


# ---------- мини-приложение: все маршруты с мусором ----------

WEIRD = [None, "", "x", 0, -1, 10 ** 20, 1.5, True, [], [1, "a"], {}, {"a": 1}, "А123ВС777", "<b>", "../../etc/passwd"]
KEYS = ["text", "id", "mode", "force", "photo"]
POST_ROUTES = ["/api/order", "/api/cancel", "/api/login", "/api/place", "/api/invite", "/api/mode",
               "/api/support", "/api/nosuch"]


def init_data(uid=OWNER):
    import hashlib
    import hmac
    import urllib.parse
    pairs = {"auth_date": str(int(time.time())), "user": json.dumps({"id": uid})}
    secret = hmac.new(b"WebAppData", b"1:test", hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, "\n".join(f"{k}={v}" for k, v in sorted(pairs.items())).encode(),
                             hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(pairs)


class WebApi(unittest.TestCase):
    count = 0

    def http(self, base, path, data=None, ctype="application/json", uid=OWNER):
        req = urllib.request.Request(base + path, data=data, headers={"X-Init-Data": init_data(uid),
                                                                      "Content-Type": ctype})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                code, raw = r.status, r.read()
        except urllib.error.HTTPError as e:
            code, raw = e.code, e.read()
        WebApi.count += 1
        return code, raw

    def run_all(self, state, dom=None):
        real_urlopen = urllib.request.urlopen
        with Harness(state, dom=dom) as h:
            srv = ThreadingHTTPServer(("127.0.0.1", 0), kpp.Web)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{srv.server_address[1]}"
            bodies = [b"", b"{}", b"[]", b'"x"', b"5", b"null", b"{bad json", "{\"text\": \"\u0000\"}".encode(),
                      b"\xff\xfe"]
            bodies += [json.dumps({k: v}).encode() for k in KEYS for v in WEIRD]
            bodies += [json.dumps({k: v for k in KEYS}).encode() for v in WEIRD]
            fails = []
            buf = io.StringIO()
            with mock.patch("urllib.request.urlopen", real_urlopen), \
                    mock.patch.object(kpp, "read_image", return_value="НЕТ"), \
                    contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                try:
                    for path in ("/api/state", "/api/nosuch", "/", "/health", "/../../kpp.py", "/kpp.py"):
                        code, raw = self.http(base, path)
                        if code == 500:
                            fails.append(f"GET {path}: {code} {raw[:200]!r}")
                    for uid in (OWNER, 42):  # владелец и гость (в full_state он есть)
                        for path in POST_ROUTES:
                            for body in bodies:
                                code, raw = self.http(base, path, body, uid=uid)
                                if code == 500:
                                    fails.append(f"POST {path} от {uid} {body[:80]!r}: {code} {raw[:200]!r}")
                    for body, ctype in ((b"", "image/png"), (b"\x89PNG" + bytes(200), "image/png"),
                                        (b"garbage", "text/plain"), (b"{}", "application/json")):
                        code, raw = self.http(base, "/api/photo", body, ctype)
                        if code == 500:
                            fails.append(f"POST /api/photo {body[:20]!r}: {code} {raw[:200]!r}")
                finally:
                    srv.shutdown()
                    srv.server_close()
            if "Traceback" in buf.getvalue():
                fails.append("исключение в выводе:\n" + buf.getvalue()[-2500:])
        self.assertEqual(fails, [], "\n".join(fails[:20]))

    def test_routes_full(self):
        self.run_all(full_state("app"))

    def test_routes_empty(self):
        self.run_all({"owner": OWNER}, dom=Domyland(empty=True))

    def test_routes_domyland_down(self):
        self.run_all(full_state("app"), dom=Domyland(fail=dl.Error))

    @classmethod
    def tearDownClass(cls):
        print(f"\nКПП, запросов к мини-приложению: {cls.count}", file=sys.stderr)


if __name__ == "__main__":
    unittest.main()
