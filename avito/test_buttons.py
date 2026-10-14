#!/usr/bin/env python3
"""«Нажать каждую кнопку» Авито-бота без сети: python3 avito/test_buttons.py

Обходчик шлёт /start и все команды бота, собирает все inline-кнопки (callback_data) и кнопки клавиатуры из того,
что бот отправил (sendMessage / editMessageText / editMessageReplyMarkup), и нажимает каждую через ту же точку
входа, что цикл getUpdates (поддержка → on_callback / on_message). Обход в ширину, глубина ≤ 4, не больше ~400
нажатий, в обоих режимах (💬 чат / 📱 приложение): с объявлениями (черновик, на Авито, продано), чатами Авито,
с пустым состоянием и когда Авито/Claude падают. Падает на: исключении (и проглоченном с traceback в выводе),
нажатии без видимого ответа, тексте кнопки > 24 символов, > 3 кнопках в ряду, callback_data > 64 байт,
пустом/битом HTML. Плюс все маршруты мини-приложения с пустыми и мусорными телами — без 500.

Всё внешнее подменено: Telegram, Claude (ai.*), поиск Авито (market.collect), API чатов Авито (chats.http),
rembg, сеть (urlopen), subprocess.
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
os.environ["AVITO_TOKEN"] = "1:test"
os.environ["AVITO_DATA"] = tempfile.mkdtemp()
import ai  # noqa: E402
import avito  # noqa: E402
import chats  # noqa: E402
import market  # noqa: E402
import support  # noqa: E402  (common/ уже в sys.path через avito)

OWNER = 7


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


# ---------- Авито ----------

ITEMS = [{"title": "iPhone 13, 128 ГБ", "price": 35000, "url": "https://www.avito.ru/moskva/telefony/iphone_13_111"},
         {"title": "iPhone 13 синий", "price": 37000, "url": "https://www.avito.ru/moskva/telefony/iphone_13_222"},
         {"title": "iPhone 13 <б/у> & чехол", "price": 33000, "url": "https://www.avito.ru/moskva/telefony/x_333"}]


def photo_file(did, n, kind="orig"):
    folder = os.path.join(avito.DRAFTS, did)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{kind}_{n:02d}_abcd.jpg")
    with open(path, "wb") as f:
        f.write(b"\xff\xd8jpg")
    return path


def ad(did, **kw):
    photos = [{"orig": photo_file(did, 1), "clean": photo_file(did, 1, "white")},
              {"orig": photo_file(did, 2), "clean": None, "err": "не получилось"}]
    base = {"id": did, "photos": photos, "info": "айфон 13, 128 ГБ, батарея 89%",
            "title": "iPhone 13 128 ГБ — отличное состояние, полный комплект", "price": 35000,
            "description": "Отличный телефон <без царапин> & с коробкой.\n" * 3, "query": "iphone 13 128",
            "category": "Телефоны", "questions": ["Есть ли коробка?", "Какой цвет?"]}
    base.update(kw)
    return base


def full_state(mode, status=None):
    now = time.time()
    m = {"query": "iphone 13 128", "ts": now, "seen": 3, "stats": market.stats([{"price": i["price"]} for i in ITEMS]),
         "prices": [33000, 35000, 37000], "sample": [{"t": i["title"], "p": i["price"], "u": i["url"]} for i in ITEMS]}
    extra = {}
    if status:
        extra = {"status": status, "url": "https://www.avito.ru/moskva/telefony/iphone_13_4012345678"}
        if status == "sold":
            extra["sold_price"] = 33000
    cur = ad("20261014-120000-aaaa", market=m, floor=30000, **extra)
    st = {"owner": OWNER, "region": "moskva", "draft": cur,
          "ads": [ad("20261013-100000-bbbb", status="live", url="https://www.avito.ru/moskva/x_1"),
                  ad("20261012-100000-cccc", status="sold", sold_price=9000,
                     title="Велосипед Stels с очень длинным названием"),
                  {"id": "20261011-100000-dddd", "photos": [], "info": "кроссовки найк 42", "price": None}],
          "av_keys": {"id": "client123456", "secret": "secret987654321"}, "av_user": {"id": 1, "name": "Глеб"},
          "av_mode": "ask"}
    if mode:
        st["mode"] = mode
    return st


def photos_only(mode):
    did = "20261014-130000-eeee"
    return {"owner": OWNER, "mode": mode,
            "draft": {"id": did, "photos": [{"orig": photo_file(did, 1), "clean": None}], "info": "", "price": None}}


class Fakes:
    """Claude, поиск Авито и API чатов Авито понарошку. fail=True — всё внешнее падает понятными ошибками."""

    def __init__(self, fail=False, no_chats=False):
        self.fail, self.no_chats, self.sent_av = fail, no_chats, []

    def http(self, method, path, params=None, body=None, form=None, auth=True):
        if self.fail:
            raise chats.AvitoError("Авито просит подождать (тест)")
        if path == "/core/v1/accounts/self":
            return {"id": 1, "name": "Глеб"}
        chat = {"id": "u2i-abc", "users": [{"id": 1, "name": "Глеб"},
                                           {"id": 2, "name": "Ольга Очень-Длинная-Фамилия Покупателева"}],
                "context": {"type": "item", "value": {"id": 9, "title": "iPhone 13 128 ГБ",
                                                     "price_string": "35 000 ₽", "url": "https://avito.ru/9"}},
                "last_message": {"id": "m1", "direction": "in", "created": 100, "type": "text",
                                 "content": {"text": "Торг уместен? <скидка>"}}}
        chat2 = dict(chat, id="u2i-def", users=[{"id": 1}, {"id": 3, "name": "Пётр"}],
                     last_message={"id": "m9", "direction": "out", "created": 90, "type": "image", "content": {}})
        if path.endswith("/chats"):
            return {"chats": [] if self.no_chats else [chat, chat2]}
        if path.endswith("/messages/"):
            return {"messages": [{"id": "m0", "direction": "out", "author_id": 1, "created": 50, "type": "text",
                                  "content": {"text": "Здравствуйте"}},
                                 {"id": "m1", "direction": "in", "author_id": 2, "created": 100, "type": "text",
                                  "content": {"text": "Торг уместен? <скидка>"}}]}
        if path.endswith("/messages"):
            self.sent_av.append(body["message"]["text"])
            return {"id": "m2"}
        if "/chats/" in path and method == "GET":
            return chat
        return {"ok": True}

    def patches(self):
        me = self

        def write_ad(info, imgs=(), prev=None, req=""):
            if me.fail:
                raise ai.AIError("лимит Claude (тест)")
            return {"title": "iPhone 13 128 ГБ", "description": "Отличный телефон.\n" + info, "query": "iphone 13 128",
                    "category": "Телефоны", "questions": ["Есть ли коробка?"]}

        def reply_buyer(ad_, buyer, image=None, history=(), floor=None, notes=""):
            if me.fail:
                raise ai.AIError("лимит Claude (тест)")
            return {"reply": "Здравствуйте! Актуально, могу уступить <немного> & отдать сегодня.", "note": "торг"}

        def web_prices(item):
            raise ai.AIError("и в интернете нет (тест)")

        def collect(q, region="all"):
            if me.fail:
                raise market.Blocked("429 (тест)")
            return [dict(i) for i in ITEMS]

        def remove_bg(pairs):
            for s_, t in pairs:
                with open(t, "wb") as f:
                    f.write(b"jpg")
            return [{"src": s_, "dst": t, "ok": not me.fail, "error": "rembg (тест)"} for s_, t in pairs]
        return [mock.patch.object(ai, "write_ad", write_ad), mock.patch.object(ai, "reply_buyer", reply_buyer),
                mock.patch.object(ai, "web_prices", web_prices),
                mock.patch.object(ai, "pick_similar", lambda item, items: items[:2]),
                mock.patch.object(market, "collect", collect), mock.patch.object(avito, "remove_bg", remove_bg),
                mock.patch.object(chats, "http", self.http)]


class Harness:
    def __init__(self, state, public_url="https://avito.test", fakes=None):
        self.tg, self.starts = FakeTG(), []
        self.sup = support_stub(self.tg, avito.allowed, self.starts)
        self.mid = 0
        avito.st.clear()
        avito.st.update(state)
        avito.busy.clear()
        chats._token.update(value="tok", until=time.time() + 3600)
        self.fakes = fakes or Fakes()

        def album(chat, paths, caption=""):
            assert all(os.path.exists(p) for p in paths), paths
            self.tg("sendMediaGroup", chat_id=chat, caption=caption, media=list(paths))

        def run(chat, fn, *args, later=None):  # как avito.run, но без потока: исключение — в вывод, как там
            try:
                fn(*args)
            except Exception as e:
                print("task:", e)
                traceback.print_exc()
                avito.send(chat, avito.fail(f"Что-то сломалось: {avito.esc(e)}"))

        def download(fid, dst):
            with open(dst, "wb") as f:
                f.write(b"orig")
        self.patches = GUARDS + [
            mock.patch.object(avito, "tg", self.tg), mock.patch.object(avito, "public_url", public_url),
            mock.patch.object(avito, "send_album", album), mock.patch.object(avito, "download", download),
            mock.patch.object(avito, "run", run),
            mock.patch.object(avito, "schedule_photos", lambda chat, delay: avito.process_photos(chat)),
            mock.patch.object(avito, "spawn", lambda fn: fn()),
            mock.patch.object(avito, "update", lambda chat: avito.send(chat, "обновление (тест)")),
            mock.patch.object(avito, "SUP", self.sup)] + self.fakes.patches()

    def __enter__(self):
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *a):
        for p in reversed(self.patches):
            p.stop()

    def dispatch(self, upd):
        """Как цикл в avito.main(): сначала поддержка, потом обработчики."""
        if self.sup.intercept(upd):
            return
        if upd.get("callback_query"):
            avito.on_callback(upd["callback_query"])
        elif upd.get("message"):
            avito.on_message(upd["message"])

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
    """Команды, которые бот ставит в меню (setMyCommands), + /start, /fix и неизвестная."""
    tg, keep = FakeTG(), dict(avito.st)
    with mock.patch.object(avito, "tg", tg), mock.patch.object(avito, "save", lambda: None):
        avito.st.clear()
        avito.setup_bot()
    avito.st.clear()
    avito.st.update(keep)
    cmds = [c["command"] for m, p, _ in tg.calls if m == "setMyCommands" for c in p["commands"]]
    assert cmds, "бот не ставит команды"
    return ["/start"] + ["/" + c for c in cmds] + ["/fix", "/region all", "/nosuchcommand"]


SEED_TEXTS = ["айфон 13, 128, батарея 89%", "35к", "сколько такое стоит?", "короче",
              "вот https://www.avito.ru/moskva/telefony/iphone_13_4012345678", "продал за 33к",
              "/buyer", "Отдадите за 25? <торг>"]  # покупатель → ответ с «🔄 Другой вариант»


# все виды кнопок бота (первая часть callback_data) — каждую обходчик должен нажать хотя бы раз
ALL_BUTTONS = {"mode", "mode:chat", "mode:app", "gen", "re", "mk", "pr", "card", "cp", "ph", "new", "pub", "live",
               "sold", "ads", "open", "buy", "buyr", "floor", "nowait", "avl", "avk", "avo", "avg", "avs", "avw",
               "avm", "avmode", "sup"}


class Buttons(unittest.TestCase):
    report, pressed = {}, set()

    def crawl(self, label, state, seeds=None, **kw):
        """Обход с командами и с текстами (они двигают объявление: цена, на Авито, продано) — и с одними командами."""
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

    def test_no_description(self):
        st = full_state("chat")
        st["draft"].update(description="", price=None, market=None, questions=[])
        with Harness(json.loads(json.dumps(st))) as h:  # сначала то, что шлёт описание отдельным сообщением
            c = Crawler(h.tg, h.send, h.press, "без описания · сразу")
            for data in ("cp", "pub", "live", "buy"):
                c.press(data, OWNER, 1)
        self.assertEqual(c.problems, [], "\n\n".join(c.problems[:15]))
        self.crawl("без описания", st)

    def test_zz_all_buttons_pressed(self):
        if not Buttons.pressed:
            self.skipTest("нужен весь класс Buttons")
        self.assertEqual(ALL_BUTTONS - Buttons.pressed, set())

    def test_live_and_sold(self):
        for status in ("live", "sold"):
            for mode in ("chat", "app"):
                self.crawl(f"{status} · {mode}", full_state(mode, status), seeds=commands())

    def test_photos_only(self):
        self.crawl("только фото · чат", photos_only("chat"), seeds=commands())
        self.crawl("только фото · приложение", photos_only("app"), seeds=commands())

    def test_first_start(self):
        self.crawl("режим не выбран", full_state(None))

    def test_empty(self):
        for mode in ("chat", "app"):
            self.crawl(f"пусто · {mode}", {"owner": OWNER, "mode": mode}, seeds=commands(), fakes=Fakes(no_chats=True))
        self.crawl("вообще пусто", {}, fakes=Fakes(no_chats=True))

    def test_no_app_url(self):
        self.crawl("без https", full_state("app"), public_url="")

    def test_everything_fails(self):
        self.crawl("Авито и Claude падают", full_state("chat"), fakes=Fakes(fail=True))

    def test_stranger(self):
        with Harness(full_state("chat")) as h:
            c = Crawler(h.tg, lambda t: h.send(t, uid=99), lambda d, ch, mid: h.press(d, 99, mid, uid=99), "чужой")
            c.run(commands())
            for data in ("card", "new", "mode:app", "sup|ask", "avl", "pr|1"):
                c.press(data, 99, 5)
        bad = [p for p in c.problems if "нет видимого ответа" not in p]  # чужому кнопки молча гасятся
        self.assertEqual(bad, [], "\n\n".join(bad[:15]))
        self.assertEqual((avito.st["mode"], avito.st["draft"]["price"]), ("chat", 35000))

    @classmethod
    def tearDownClass(cls):
        print("\nАвито, обход кнопок (сообщений · нажатий):",
              "; ".join(f"{k}: {s}·{p}" for k, (s, p) in sorted(cls.report.items())), file=sys.stderr)


# ---------- мини-приложение: все маршруты с мусором ----------

WEIRD = [None, "", "x", 0, -1, 10 ** 20, 1.5, True, [], [1, "a"], {}, {"a": 1}, "<b>", "../../etc/passwd",
         "20261014-120000-aaaa", "20261013-100000-bbbb", "moskva", "chat", "35000"]
KEYS = ["id", "query", "force", "price", "info", "request", "region", "mode", "photo", "text"]
POST_ROUTES = ["/api/market", "/api/price", "/api/process", "/api/write", "/api/region", "/api/mode", "/api/send",
               "/api/support", "/api/nosuch", "/api/new"]


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

    def run_all(self, state, fakes=None):
        real_urlopen = urllib.request.urlopen
        with Harness(state, fakes=fakes):
            srv = ThreadingHTTPServer(("127.0.0.1", 0), avito.Web)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{srv.server_address[1]}"
            bodies = [b"", b"{}", b"[]", b'"x"', b"5", b"null", b"{bad json", b"\xff\xfe"]
            bodies += [json.dumps({k: v}).encode() for k in KEYS for v in WEIRD]
            bodies += [json.dumps({k: v for k in KEYS}).encode() for v in WEIRD]
            fails = []
            buf = io.StringIO()
            with mock.patch("urllib.request.urlopen", real_urlopen), \
                    contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                try:
                    did = (state.get("draft") or {}).get("id", "x")
                    for path in ("/api/state", "/api/nosuch", "/", "/health", f"/api/photo/{did}/0",
                                 f"/api/photo/{did}/1", f"/api/photo/{did}/99", "/api/photo/nope/0",
                                 "/api/photo/..%2F..%2Fstate/0", "/../avito.py", "/avito.py"):
                        code, raw = self.http(base, path)
                        if code == 500:
                            fails.append(f"GET {path}: {code} {raw[:200]!r}")
                    for path in POST_ROUTES:
                        for body in bodies:
                            if path == "/api/new" and body not in (b"", b"{}", b"[]"):
                                continue  # «новое» убирает черновик — остальным маршрутам он ещё нужен
                            code, raw = self.http(base, path, body)
                            if code == 500:
                                fails.append(f"POST {path} {body[:80]!r}: {code} {raw[:200]!r}")
                    for body, ctype in ((b"", "image/png"), (b"\xff\xd8" + bytes(200), "image/jpeg"),
                                        (b"garbage", "text/plain"), (b"{}", "application/json"), (b"x" * 150, "")):
                        code, raw = self.http(base, "/api/upload", body, ctype)
                        if code == 500:
                            fails.append(f"POST /api/upload {body[:20]!r}: {code} {raw[:200]!r}")
                finally:
                    srv.shutdown()
                    srv.server_close()
            if "Traceback" in buf.getvalue():
                fails.append("исключение в выводе:\n" + buf.getvalue()[-2500:])
        self.assertEqual(fails, [], "\n".join(fails[:20]))

    def test_routes_full(self):
        self.run_all(full_state("app"))

    def test_routes_empty(self):
        self.run_all({"owner": OWNER})

    def test_routes_photos_only(self):
        self.run_all(photos_only("app"))

    def test_routes_fail(self):
        self.run_all(full_state("app"), fakes=Fakes(fail=True))

    @classmethod
    def tearDownClass(cls):
        print(f"\nАвито, запросов к мини-приложению: {cls.count}", file=sys.stderr)


if __name__ == "__main__":
    unittest.main()
