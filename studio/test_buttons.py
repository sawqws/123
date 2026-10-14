"""Обход всех кнопок Студии (оба режима: боты и «Студия сайтов») и бота из заготовки Студии — без сети и Telegram.

Краулер шлёт /start, все команды, текст, голосовое и фото, собирает все inline-кнопки (callback_data) из ответов
и «нажимает» каждую через bot.handle (тот же вход, что у цикла getUpdates), в ширину до глубины 4, не больше 400
нажатий на обход; каждое нажатие — с того же состояния, в котором кнопка была показана (сброс и повтор пути).
Плюс «устаревшие» варианты кнопок (без аргументов, с мусором). Режимы 💬 чат / 📱 приложение / не выбран.
Падение: трассировка (даже пойманная и напечатанная), нажатие без видимого ответа, кнопка длиннее 24 символов,
больше 3 кнопок в ряд, callback_data длиннее 64 байт. И все api мини-приложений по HTTP с пустыми и мусорными
телами — без 500.
usage: python3 studio/test_buttons.py
"""
import contextlib
import copy
import http.client
import importlib.util
import io
import itertools
import json
import os
import shutil
import subprocess
import sys
import threading
import traceback
import unittest
from collections import deque
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "common"))
import test_studio as ts  # noqa: E402  (ставит окружение и импортирует studio)

studio, kit = ts.studio, ts.kit
OWNER = ts.OWNER
APP = "https://apps.1-2-3-4.sslip.io/"
VISIBLE = {"sendMessage", "editMessageText", "editMessageReplyMarkup", "sendPhoto", "sendDocument", "sendVoice",
           "deleteMessage"}
MARKUP = {"sendMessage", "editMessageText", "editMessageReplyMarkup", "sendPhoto", "sendDocument"}
GARBAGE = [b"", b"{}", b"[]", b"[1, 2]", b'"x"', b"1", b"null", b"not json", b"\xff\xfe",
           json.dumps({"text": 5, "i": "abc", "slug": ["x"], "mode": {"a": 1}, "index": "zz", "photo": "%%%"}).encode(),
           json.dumps({"text": "сделай бота для заметок", "i": -5, "slug": "voda", "mode": "public",
                       "index": 99}).encode(),
           json.dumps({"text": "x" * 20000, "slug": "../../etc", "mode": "password"}).encode()]


# ---------- краулер (общий для всех ботов на botkit) ----------

def variants(data):
    """«Устаревшие» кнопки: без аргументов, с первым аргументом, с мусором вместо аргументов."""
    parts = data.split("|")
    if len(parts) < 2:
        return []
    out = [parts[0], "|".join([parts[0]] + ["zz"] * (len(parts) - 1))]
    if len(parts) > 2:
        out.append("|".join(parts[:2]))
    return out


class Crawler:
    """restore() — сбросить состояние к исходному; do(action) — ("msg", текст | dict) или ("press", data);
    log — общий список вызовов Bot API (метод, параметры); waiting() — бот ждёт ответ текстом."""

    def __init__(self, name, restore, do, log, waiting=lambda: False, cap=400, depth=4, forbid=()):
        self.name, self.restore, self.do, self.log, self.waiting = name, restore, do, log, waiting
        self.cap, self.depth, self.forbid = cap, depth, forbid
        self.errors, self.presses, self.msgs, self.buttons = [], 0, 0, set()

    def act(self, action):
        start = len(self.log)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            try:
                self.do(action)
            except Exception:
                traceback.print_exc()
        return self.log[start:], out.getvalue()

    def check(self, path, new, out, malformed=False):
        where = f"[{self.name}] {' → '.join(str(a[1])[:40] for a in path)}"
        found, texts = [], []
        if "Traceback" in out:
            self.errors.append(f"{where}: исключение\n{out[-1500:]}")
        for method, p in new:
            text = str(p.get("text") or p.get("caption") or "")
            if "Что-то пошло не так" in text:
                self.errors.append(f"{where}: ошибка в чате: {text[:200]}")
            if method not in MARKUP:
                continue
            mk = p.get("reply_markup") or {}
            for kind in ("inline_keyboard", "keyboard"):
                for row in mk.get(kind) or []:
                    if len(row) > 3:
                        self.errors.append(f"{where}: {len(row)} кнопки в ряд: {[b.get('text') for b in row]}")
                    for btn in row:
                        btn = btn if isinstance(btn, dict) else {"text": btn}
                        t = str(btn.get("text") or "")
                        self.buttons.add(t)
                        if len(t) > 24:
                            self.errors.append(f"{where}: кнопка длиннее 24: «{t}» ({len(t)})")
                        for bad in self.forbid:
                            if bad(btn):
                                self.errors.append(f"{where}: запрещённая кнопка: {btn}")
                        data = btn.get("callback_data")
                        if data is not None:
                            if len(data.encode()) > 64:
                                self.errors.append(f"{where}: callback_data > 64 байт: {data}")
                            found.append(data)
                        elif kind == "keyboard":
                            texts.append(t)
        if path[-1][0] == "press" or path[-1][2:3] == ("root",):
            visible = any(m in VISIBLE for m, _ in new) or \
                any(m == "answerCallbackQuery" and p.get("text") for m, p in new)
            if not visible:
                self.errors.append(f"{where}: нет видимого ответа{' (устаревшая кнопка)' if malformed else ''}: "
                                   f"{[m for m, _ in new]}")
        return found, texts

    def run(self, roots):
        queue = deque(([("msg", r, "root")], False) for r in roots)
        seen = set()
        while queue and self.presses < self.cap:
            path, malformed = queue.popleft()
            self.restore()
            for a in path[:-1]:
                self.act(a)
            new, out = self.act(path[-1])
            if path[-1][0] == "press":
                self.presses += 1
            else:
                self.msgs += 1
            found, texts = self.check(path, new, out, malformed)
            if malformed or len(path) - 1 >= self.depth:
                continue
            if self.waiting() and ("reply", path[-1][1]) not in seen:  # бот спросил — отвечаем текстом
                seen.add(("reply", path[-1][1]))
                queue.append((path + [("msg", "Сделай кнопки зелёными")], False))
            for t in texts:
                if ("msg", t) not in seen:
                    seen.add(("msg", t))
                    queue.append((path + [("msg", t, "root")], False))
            for data in found:
                if data in seen:
                    continue
                seen.add(data)
                queue.append((path + [("press", data)], False))
                for v in variants(data):
                    if v not in seen:
                        seen.add(v)
                        queue.append((path + [("press", v)], True))
        return self


def http_call(port, path, body=None, headers=None, method=None):
    """(код, тело) или (None, ошибка), если сервер оборвал соединение."""
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        c.request(method or ("POST" if body is not None else "GET"), path, body=body, headers=headers or {})
        r = c.getresponse()
        return r.status, r.read()
    except (OSError, http.client.HTTPException) as e:
        return None, repr(e)
    finally:
        c.close()


def serve(bot):
    bot.port = ts.free_port()
    threading.Thread(target=bot.serve, daemon=True).start()
    for _ in range(100):
        if http_call(bot.port, "/health")[0] == 200:
            return bot.port
        threading.Event().wait(0.05)
    raise AssertionError("сервер не поднялся")


def sweep_api(tc, bot, port, prefix="", names=None):
    """Все api бота: GET и POST с пустыми и мусорными телами от владельца — ни одного 500 и оборванного ответа."""
    head = {"X-Init-Data": ts.signed(bot.token, {"id": OWNER, "first_name": "Тест"}), "Content-Type": "application/json"}
    names = sorted(set(names or bot.handlers["api"]) | {"support", "nope"})
    bad, n = [], 0
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        for name in names:
            for q in ("", "?i=abc&slug=..&mode=x&text="):
                code, body = http_call(port, f"{prefix}/api/{name}{q}", headers=head)
                n += 1
                if code in (None, 500):
                    bad.append(f"GET {name}{q}: {code} {body[:200]}")
            for raw in GARBAGE:
                code, body = http_call(port, f"{prefix}/api/{name}", raw, head)
                n += 1
                if code in (None, 500):
                    bad.append(f"POST {name} {raw[:40]!r}: {code} {body[:200]}")
        code, _ = http_call(port, f"{prefix}/api/state", b"{}", {"Content-Type": "application/json"})
        if code != 401:
            bad.append(f"без подписи — {code}, а не 401")
    if "Traceback" in out.getvalue():
        bad.append("трассировка:\n" + out.getvalue()[-1500:])
    tc.assertEqual(bad, [], "\n".join(bad))
    return n


def fake_support(bot, log):
    """Поддержка как в бою (bot._start_support → common/support.py), но без потоков, Telegram и Claude."""
    import support
    started = []

    def tg(self, method, **params):
        log.append((method, params))
        return {"ok": True, "result": {"message_id": 1}}
    patches = [mock.patch.object(support.Support, "_commands", lambda self: None),
               mock.patch.object(support.Support, "tg", tg),
               mock.patch.object(support.Support, "start", lambda self, chat, text, shots=None:
                                 (started.append((chat, text)), self.send(chat, "⏳ <b>Поддержка</b>"))),
               mock.patch.object(support.Support, "undo", lambda self, chat, sha: self.send(chat, "↩️ отменено")),
               mock.patch.object(support.Support, "restart", lambda self: None)]
    for p in patches:
        p.start()
    bot.support = None
    bot._start_support()
    return patches, started


# ---------- Студия ----------

class Fake(ts.Fake):
    def claude(self, prompt, cwd, on_step=None, timeout=0, tools=True, model=None):
        if "дорабатываешь Telegram-бота" in prompt:  # правка бота меняет файл — появится «↩️ Откатить»
            with open(os.path.join(cwd, "README.md"), "a") as f:
                f.write("\nправка\n")
            return True, "Добавил кнопку."
        return super().claude(prompt, cwd, on_step, timeout, tools, model)


def git_dir(path, files):
    os.makedirs(path, exist_ok=True)
    for name, text in files.items():
        with open(os.path.join(path, name), "w") as f:
            f.write(text)
    ts.REAL_SH("git", "-C", path, "init", "-q", "--template=")  # без образцов хуков: сброс состояния быстрее
    studio.commit(path, "init")


class StudioCrawl:
    """Состояние Студии: реестр ботов и сайтов на диске + data/db.json (черновик). Сид собирается один раз."""

    def __init__(self, mod, fake, root):
        self.mod, self.f, self.root = mod, fake, root
        self.log, self.t = [], None
        self.seeds = {}
        self.ids = itertools.count(1)

    def seed(self, key, draft, bots=True, sites=True):
        m = self.mod
        work = m.BOTS
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(m.SITES, ignore_errors=True)
        os.makedirs(work, exist_ok=True)
        if bots and not m.SITES_ONLY:
            for slug, name, user in (("voda", "Водичка", "voda_test_bot"), ("kino-spisok", "Очень длинное имя бота кино", None),
                                     ("novy", "Новый", None)):
                path = os.path.join(work, slug)
                git_dir(path, {"bot.py": "print(1)\n", "README.md": "# бот\n"})
                m.reg_set(slug, {"name": name, "emoji": "💧", "slug": slug, "pm2": f"bot-{slug}", "dir": path,
                                 "port": m.free_port(), "summary": "Считает воду", "username": user, "created": 1.0})
            self.f.pm2.append(("pm2", "start", "x", "--name", "bot-voda"))
        if sites:
            for slug, access in (("coffee", "private"), ("foto", "password"), ("cv", "public")):
                path = os.path.join(m.SITES, slug)
                git_dir(path, {"index.html": "<h1>Привет</h1>"})
                m.site_set(slug, {"kind": "site", "name": slug.title(), "emoji": "🌐", "slug": slug, "dir": path,
                                  "summary": "Сайт", "access": access, "key": "k" * 16, "password": "abcdefgh",
                                  "created": 2.0})
        self.seeds[key] = (os.path.join(self.root, key), draft)
        shutil.rmtree(self.seeds[key][0], ignore_errors=True)
        for d in self.dirs():
            os.makedirs(d, exist_ok=True)
            shutil.copytree(d, os.path.join(self.seeds[key][0], os.path.basename(d)), symlinks=True)

    def dirs(self):
        """Что сбрасывать: папка ботов (реестры) и папка сайтов, если она не внутри первой."""
        m = self.mod
        inside = os.path.realpath(m.SITES).startswith(os.path.realpath(m.BOTS) + os.sep)
        return [m.BOTS] + ([] if inside else [m.SITES])

    def restore(self, key, mode):
        m = self.mod
        src, draft = self.seeds[key]
        for d in self.dirs():
            shutil.rmtree(d, ignore_errors=True)
            shutil.copytree(os.path.join(src, os.path.basename(d)), d, symlinks=True)
        self.log.clear()
        self.t = m.bot.testing(owner=OWNER)
        self.t.calls = self.log
        m.bot.db.data["base_url"] = APP
        if mode:
            m.bot.db.data["_mode"] = mode
        if draft:
            m.bot.db.data["draft"] = copy.deepcopy(draft)
        m.bot.waits.clear()
        m._one_tap.update(at=0, ok=False)
        if m.bot.support:
            m.bot.support.waiting.clear()
        self.ids = itertools.count(1)

    def do(self, action):
        kind, x = action[0], action[1]
        if kind == "press":
            self.t.press(x)
        elif isinstance(x, dict):
            self.t.send("", **x)
        else:
            self.t.send(x)

    def waiting(self):
        sup = self.mod.bot.support
        return bool(self.mod.bot.waits) or bool(sup and sup.waiting)


DRAFTS = {
    "empty": None,
    "plan": {"id": "d1", "idea": "бот для воды", "stage": "plan", "qs": [], "qi": 0, "fixes": [], "name": "Вода",
             "emoji": "💧", "kind": "bot", "plan": dict(Fake().plan, username="voda_new")},
    "build": {"id": "d2", "idea": "трекер сна", "stage": "build", "name": "Сон", "steps": ["пишу код"], "kind": "bot",
              "plan": Fake().plan},
    "ask": {"id": "d3", "idea": "бот для покупок", "stage": "ask", "qi": 0, "names": ["Покупки", "Корзина", "Список"],
            "emoji": "🛒", "qs": [{"q": "Для кого?", "options": ["Для себя", "Для семьи"]},
                                 {"q": "Нужны напоминания о покупках по дням недели?",
                                  "options": ["Да, каждый день утром", "Нет"]}], "fixes": [], "kind": "bot"},
    "name": {"id": "d4", "idea": "дневник", "stage": "name", "qs": [], "qi": 0, "fixes": [], "kind": "bot",
             "names": ["Дневник", "Мысли", "Очень-очень длинное имя"], "emoji": "📓"},
    "token": {"id": "d5", "idea": "новый бот", "stage": "token", "slug": "novy", "name": "Новый", "kind": "bot",
              "plan": Fake().plan, "why": "Секретарь не вошёл", "newbot": "https://t.me/newbot/s/novy_bot"},
    "failed": {"id": "d6", "idea": "сайт кофейни", "stage": "failed", "name": "Кофейня", "kind": "site",
               "error": "Claude не закончил работу", "plan": {"summary": "Сайт", "slug": "kofe"}},
    "site-plan": {"id": "d7", "idea": "сайт кофейни", "stage": "plan", "name": "Кофейня", "kind": "site", "qs": [],
                  "fixes": [], "plan": {"summary": "Сайт кофейни", "pages": ["Меню"], "style": ["тёплый"],
                                        "features": [], "slug": "kofe"}},
}


class Base(unittest.TestCase):
    mod = studio
    roots_extra = ()

    def setUp(self):
        self.f = Fake()
        self.root = os.path.join(ts.TMP, "crawl-" + self.mod.__name__)
        self.c = StudioCrawl(self.mod, self.f, self.root)
        getme = lambda *a, **k: io.BytesIO(json.dumps({"result": {"username": "voda_test_bot"}}).encode())

        class Media:
            def voice_text(self, token, msg): return "бот, который считает шаги"
            def photo_path(self, token, msg, folder): return os.path.join(ts.TMP, "shot.jpg")
        self.patches = [mock.patch.object(self.mod, "sh", self.f.sh),
                        mock.patch.object(self.mod, "botfather", lambda n, w: (self.f.token, None)),
                        mock.patch.object(self.mod, "avatar", lambda p, d: None),
                        mock.patch.object(self.mod, "run_tests", lambda p: (True, "OK")),
                        mock.patch.object(self.mod.time, "sleep", lambda s: None),
                        mock.patch.object(self.mod.urllib.request, "urlopen", getme),
                        mock.patch.object(self.mod.secrets, "token_hex", lambda n=3: f"{next(self.c.ids):06x}"),
                        mock.patch.object(kit, "_media", Media())]
        self.patches += self.claude_patches()
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.sup_patches, self.sup_started = fake_support(self.mod.bot, self.c.log)
        for p in self.sup_patches:
            self.addCleanup(p.stop)

    def claude_patches(self):
        return [mock.patch.object(self.mod, "claude", self.f.claude),
                mock.patch.object(self.mod, "secretary_ready", lambda: True)]

    def tearDown(self):
        self.mod.bot.support = None
        self.mod.bot.app_url = ""

    def roots(self):
        cmds = [f"/{c}" for c, _ in self.mod.bot.commands] + ["/fix", "/app", "/help", "/неизвестная"]
        return cmds + ["сделай бота, который считает воду", {"voice": {"file_id": "v1"}},
                       {"photo": [{"file_id": "p1"}], "caption": "вот так хочу"}, {"photo": [{"file_id": "p2"}]},
                       {"video": {"file_id": "x1"}}, {"sticker": {"file_id": "s1"}}, *self.roots_extra]

    def crawl(self, seeds, modes=("", "chat", "app"), forbid=()):
        total = []
        for key in seeds:
            self.c.seed(key, DRAFTS[key], bots=key != "empty", sites=key != "empty")
            for mode in modes:
                self.mod.bot.app_url = APP
                cr = Crawler(f"{key}/{mode or 'без режима'}", lambda: self.c.restore(key, mode), self.c.do, self.c.log,
                             self.c.waiting, forbid=forbid).run(self.roots())
                total.append(cr)
                self.assertEqual(cr.errors, [], "\n\n".join(cr.errors[:15]))
                self.assertLessEqual(cr.presses, 400)
        return total


class StudioButtons(Base):
    def test_crawl_all_buttons(self):
        # главный экран зависит от режима — пустая Студия и с планом во всех трёх; остальные шаги — по одному режиму
        res = self.crawl(["empty", "plan"])
        for key, mode in (("build", "chat"), ("ask", "app"), ("name", ""), ("token", "chat"), ("failed", "app"),
                          ("site-plan", "")):
            res += self.crawl([key], modes=(mode,))
        presses = sum(c.presses for c in res)
        print(f"\nСтудия: {len(res)} обходов, нажатий {presses}, сообщений {sum(c.msgs for c in res)}, "
              f"разных кнопок {len(set().union(*(c.buttons for c in res)))}", file=sys.__stderr__)
        self.assertGreater(presses, 300)  # обход действительно жмёт кнопки, а не останавливается на /start

    def test_crawl_managed_bots_and_no_secretary(self):
        """Managed Bots (кнопка «⚡ Создать») и без Секретаря (токен руками) — те же правила для кнопок."""
        with mock.patch.object(self.mod.bot, "can_manage", lambda: True), \
                mock.patch.object(self.mod.bot, "managed_token", lambda i: self.f.token):
            self.mod.bot.username = "studio_test_bot"
            self.crawl(["plan", "token"], modes=("chat",))
        with mock.patch.object(self.mod, "secretary_ready", lambda: False):
            self.crawl(["plan", "empty"], modes=("app",))

    def test_api_garbage(self):
        port = serve(self.mod.bot)
        n = 0
        for key in ("plan", "ask", "name", "token", "failed"):
            self.c.seed(key, DRAFTS[key])
            self.c.restore(key, "app")
            n += sweep_api(self, self.mod.bot, port)
        # прокси до мини-приложения бота, которого нет на порту, и сайты — без 500
        for path in ("/b/voda/", "/b/voda/api/state", "/b/nope/", "/s/coffee/", "/s/coffee/?k=bad", "/s/nope/x",
                     "/s/foto/", "/s/cv/", "/s/cv/../../registry.json", "/s/cv/%2e%2e/registry.json"):
            code, body = http_call(port, path)
            self.assertNotIn(code, (None, 500), (path, body))
            self.assertNotIn(b'"bots"', body or b"")
        self.assertEqual(http_call(port, "/s/cv/")[0], 200)
        print(f"\nСтудия: api-вызовов {n + 10}", file=sys.__stderr__)


# ---------- «Студия сайтов» (STUDIO_MODE=sites) ----------

def load_sites_module():
    base = os.path.join(ts.TMP, "sites-mode")
    env = {"STUDIO_MODE": "sites", "STUDIO_BOTS_DIR": base, "STUDIO_SITES_DIR": os.path.join(base, "sites"),
           "STUDIO_DATA": os.path.join(base, "data"), "STUDIO_TG_NAME": "Site Studio"}
    with mock.patch.dict(os.environ, env):
        spec = importlib.util.spec_from_file_location("studio_sites", os.path.join(HERE, "studio.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    assert mod.SITES_ONLY
    return mod


SITES_MOD = load_sites_module()


class SitesButtons(Base):
    """Студия сайтов: тот же обход, но Claude — настоящая обёртка claude() (подменён только процесс), и каждая
    команда Claude проверяется на песочницу (SAFE_DENY, без Bash); поддержки (/fix) нет нигде."""
    mod = SITES_MOD

    def claude_patches(self):
        self.cmds = []
        fake = self.f

        real = subprocess.Popen

        class Proc:
            def __new__(cls, cmd, *a, **kw):  # настоящие команды (кроме claude) идут мимо подмены
                return super().__new__(cls) if cmd[0] == "claude" else real(cmd, *a, **kw)

            def __init__(proc, cmd, cwd=None, **kw):
                self.cmds.append(cmd)
                prompt = cmd[2]
                ok, out = fake.claude(cmd[2], cwd, tools="Write(./**)" in cmd)
                proc.stdout = iter([json.dumps({"type": "result", "result": out, "is_error": not ok}) + "\n"])
                proc.returncode = 0

            def wait(proc):
                return 0

            def kill(proc):
                pass
        return [mock.patch.object(self.mod.subprocess, "Popen", Proc)]

    def tearDown(self):
        super().tearDown()
        for cmd in self.cmds:
            self.assert_sandboxed(cmd)

    def assert_sandboxed(self, cmd):
        allowed = cmd[cmd.index("--allowedTools") + 1:cmd.index("--disallowedTools")]
        denied = cmd[cmd.index("--disallowedTools") + 1:]
        self.assertNotIn("Read", allowed)
        self.assertFalse([t for t in allowed if t.startswith("Bash") or t in ("Write", "Edit")], allowed)
        for must in ["Bash", "WebFetch", "Task", *self.mod.SAFE_DENY]:
            self.assertIn(must, denied)
        self.assertNotIn("--dangerously-skip-permissions", cmd)
        self.assertNotIn("Глеб", cmd[2])

    def test_crawl_sites_mode(self):
        self.assertIs(self.mod.bot.support_cfg, False)  # /fix не для чужого человека
        self.assertIsNone(self.mod.bot.support)
        no_sup = (lambda btn: str(btn.get("callback_data", "")).startswith("sup|"),
                  lambda btn: "Поддержка" in str(btn.get("text")))
        res = self.crawl(["empty", "site-plan", "failed"], forbid=no_sup)
        res += self.crawl(["ask", "name"], modes=("chat",), forbid=no_sup)
        presses = sum(c.presses for c in res)
        print(f"\nСтудия сайтов: {len(res)} обходов, нажатий {presses}, сообщений {sum(c.msgs for c in res)}, "
              f"вызовов Claude {len(self.cmds)}", file=sys.__stderr__)
        self.assertGreater(presses, 100)
        self.assertTrue(self.cmds)
        self.assertFalse(self.sup_started)

    def test_api_garbage_sites(self):
        port = serve(self.mod.bot)
        for key in ("site-plan", "name"):
            self.c.seed(key, DRAFTS[key])
            self.c.restore(key, "app")
            sweep_api(self, self.mod.bot, port)
        head = {"X-Init-Data": ts.signed(self.mod.bot.token, {"id": OWNER}), "Content-Type": "application/json"}
        code, body = http_call(port, "/api/support", json.dumps({"text": "почини сервер"}).encode(), head)
        self.assertEqual(code, 400, body)  # поддержки нет — Claude с правами на код сервера не запускается
        self.assertFalse(self.sup_started)


# ---------- бот из заготовки Студии ----------

class TemplateButtons(unittest.TestCase):
    """Бот, собранный Студией из studio/kit/template (scaffold), — как его запускает pm2, но без сети."""

    @classmethod
    def setUpClass(cls):
        d = os.path.join(ts.TMP, "tpl")
        shutil.rmtree(d, ignore_errors=True)
        with mock.patch.object(studio, "BOTS", d):
            path = studio.scaffold("zametki", {"name": "Заметки"})
        spec = importlib.util.spec_from_file_location("tpl_bot", os.path.join(path, "bot.py"))
        cls.app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.app)

    def setUp(self):
        self.log = []
        self.bot = self.app.bot
        self.sup_patches, self.sup_started = fake_support(self.bot, self.log)
        self.media = mock.patch.object(kit, "_media", type("M", (), {
            "voice_text": lambda s, t, m: "купить хлеб", "photo_path": lambda s, t, m, f: "/nonexistent.jpg"})())
        self.media.start()
        for p in self.sup_patches + [self.media]:
            self.addCleanup(p.stop)

    def tearDown(self):
        self.bot.support = None
        self.bot.app_url = ""

    def restore(self, notes, mode, app_url):
        self.log.clear()
        self.t = self.bot.testing(owner=OWNER)
        self.t.calls = self.log
        self.bot.app_url = app_url
        if notes:
            self.bot.db.data["notes"] = [{"text": f"заметка {i}", "at": "01.10 10:00"} for i in range(notes)]
        if mode:
            self.bot.db.data["_mode"] = mode
        self.bot.waits.clear()
        self.bot.support.waiting.clear()

    def do(self, action):
        x = action[1]
        if action[0] == "press":
            self.t.press(x)
        elif isinstance(x, dict):
            self.t.send("", **x)
        else:
            self.t.send(x)

    def test_crawl_template_bot(self):
        roots = [f"/{c}" for c, _ in self.bot.commands] + ["/app", "/fix", "/что-то", "купить молоко",
                                                           {"voice": {"file_id": "v"}}, {"photo": [{"file_id": "p"}]},
                                                           {"video": {"file_id": "x"}}, {"sticker": {"file_id": "s"}}]
        res = []
        for notes, mode, url in ((0, "", ""), (3, "", APP), (3, "chat", APP), (40, "app", APP)):
            cr = Crawler(f"заготовка/{notes}/{mode or '-'}/{bool(url)}", lambda: self.restore(notes, mode, url), self.do,
                         self.log, lambda: bool(self.bot.waits or self.bot.support.waiting)).run(roots)
            self.assertEqual(cr.errors, [], "\n\n".join(cr.errors[:15]))
            res.append(cr)
        print(f"\nЗаготовка: нажатий {sum(c.presses for c in res)}, сообщений {sum(c.msgs for c in res)}",
              file=sys.__stderr__)

    def test_api_garbage_template(self):
        port = serve(self.bot)
        self.restore(3, "app", APP)
        n = sweep_api(self, self.bot, port)
        print(f"\nЗаготовка: api-вызовов {n}", file=sys.__stderr__)


if __name__ == "__main__":
    unittest.main()
