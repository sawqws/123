"""Обход всех кнопок HDP-бота: /start, команды, кнопки клавиатуры и инлайн-кнопки — в режимах 💬 чат и 📱 приложение,
плюс все маршруты API приложения с пустыми и битыми телами.

Всё внешнее подменено: Telegram, `claude -p`, node-скрипты (horodigital.ru), python-скрипты почерка, git, сеть,
скачивание медиа. Обновления идут через bot.on_update — тот же вход, что в цикле main().
Падает, если: исключение (в том числе пойманное и напечатанное), нажатие без видимого ответа, кнопка длиннее 24
символов или больше 3 в ряду, callback_data больше 64 байт, неверный HTML Telegram, сообщение длиннее 4096,
кнопка приложения в режиме «чат» (кроме /app), ответ API 500.
usage: python3 bot/test_buttons.py
"""
import collections
import contextlib
import http.client
import io
import json
import os
import pathlib
import socket
import sys
import tempfile
import time
import unittest
from html.parser import HTMLParser
from http.server import ThreadingHTTPServer
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("TELEGRAM_TOKEN", "test")
import bot  # noqa: E402
import drafts  # noqa: E402
import web  # noqa: E402
import support  # noqa: E402  (bot.py уже добавил common/ в путь)
from test_bot import Sync, URL, TASK, CHAT, TOPICS, DETAILS, U  # noqa: E402

CAP = 400        # нажатий на один режим
DEPTH = 4        # глубина обхода от стартовых сообщений
APP = "https://app.example"
TEST_URL = URL.replace("22222222-2222-2222-2222-222222222222", "33333333-3333-3333-3333-333333333333")
REWORK_URL = URL.replace("22222222-2222-2222-2222-222222222222", "44444444-4444-4444-4444-444444444444")
NOW_MS = int(time.time() * 1000)
TASKS = [  # как horo/status.js collect (и overview.json → tasks); данные выдуманные
    {"subj": "Информатика", "icon": "💻", "st": "Пора начать", "auto": True, "type": "test", "title": "7. Циклы — тест",
     "late": False, "lateDays": -3, "dl": "20.10", "deadline": "2026-10-20", "topic": "Циклы", "topicId": U(10),
     "order": 1, "url": TEST_URL, "rawStatus": "appointed"},
    {"subj": "История", "icon": "🏛", "st": "На доработке", "auto": True, "type": "test", "title": "Северная война",
     "late": True, "lateDays": 2, "dl": "12.10", "deadline": "2026-10-12", "topic": "Пётр I", "topicId": U(5),
     "order": 2, "url": REWORK_URL, "rawStatus": "reworking"},
    {"subj": "Биология", "icon": "🧬", "st": "Пора начать", "auto": False, "type": "detailedAnswer",
     "title": "Конспект §5", "late": False, "lateDays": -1, "dl": "15.10", "deadline": "2026-10-15",
     "topic": "Клетка", "topicId": U(11), "order": 1, "url": URL, "rawStatus": "appointed"},
]
OVERVIEW = {
    "tasks": TASKS,
    "grades": [{"name": "Алгебра", "lvl": 2.7, "five": 4, "ia": 3, "next": [3.5, 5]},
               {"name": "История", "lvl": 1.8, "five": 2, "ia": None, "next": [2.0, 3]}],
    "comments": [{"subj": "Биология", "icon": "🧬", "title": "Конспект §4", "failed": True, "url": URL,
                  "comment": "Доделать № 3", "score": None, "attempts": ""}],
    "topics": TOPICS, "at": NOW_MS,
}
HTML_TAGS = {"b", "i", "u", "s", "a", "code", "pre", "blockquote", "tg-emoji", "tg-spoiler"}
BAD_LOG = ("Traceback", "Error", "Exception", "handle:", "callback:", "support:", "on_tool:")


class Exited(BaseException):
    """os._exit() из /update: бот перезапустился бы — здесь просто выходим из нажатия."""


def html_problem(text):
    """Почему текст не примет Telegram (parse_mode=HTML), или ""."""
    stack, bad = [], []

    class P(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag not in HTML_TAGS:
                bad.append(f"тег <{tag}>")
            stack.append(tag)

        def handle_endtag(self, tag):
            if not stack or stack.pop() != tag:
                bad.append(f"лишний </{tag}>")
    p = P()
    p.feed(text)
    p.close()
    if stack:
        bad.append("не закрыт " + ", ".join(stack))
    return "; ".join(bad)


class World:
    """Подменённый внешний мир: всё, что бот «отправил» и «запустил»."""

    def __init__(self, tmp):
        self.tmp = tmp
        self.calls = []   # (метод Telegram, параметры, id нового сообщения)
        self.mid = 1000
        self.looks = 0
        self.unknown = []  # скрипты, которых обход не ждал
        self.support = []
        self.fail = False  # всё внешнее отказывает: Claude, сайт, скачивание из Telegram

    # ---- Telegram ----
    def tg(self, method, **p):
        new = None
        if method in ("sendMessage", "sendPhoto", "sendDocument"):
            self.mid += 1
            new = self.mid
        self.calls.append((method, p, new))
        if method == "getFile":
            return {"ok": True, "result": {"file_path": "photos/file_1.jpg"}}
        return {"ok": True, "result": {"message_id": new} if new else True}

    def send_file(self, chat, path, caption="", as_file=False):
        return self.tg("sendDocument" if as_file else "sendPhoto", chat_id=chat, caption=caption, file=path)

    # ---- claude -p ----
    def claude(self, prompt, tools, timeout=0, on_tool=None):
        if self.fail:
            return False, "⚠️ Claude не справился с заданием. Попробуй ещё раз."
        if on_tool:
            on_tool("Bash", {"command": "node horo/fetch.js x"})
            on_tool("Read", {"file_path": "horo/tmp/x/1.png"})
        if prompt.startswith("Задание:"):
            if TASK in prompt:  # задание с проверкой учителем — черновик и картинка
                drafts.write(TASK, "Клетка делится митозом.\nЭто важно.")
                d = drafts.files_dir(TASK)
                os.makedirs(d, exist_ok=True)
                pathlib.Path(d, "hand.png").write_bytes(b"png")
                return True, "Сделал черновик"
            return True, "БАЛЛЫ: 5/5\n1. ответ А\n2. **ответ** `Б`"
        if prompt.startswith("Глеб прислал"):  # фото: то задание из списка, то условие без ссылки
            self.looks += 1
            if self.looks % 2:
                return True, json.dumps({"link": "", "match": URL, "title": "Конспект §5", "task": ""})
            return True, '```json\n{"link": "", "match": "", "title": "Уравнение", "task": "Реши 2x = 4 <и проверь>"}\n```'
        if prompt.startswith("На фото задание"):
            return True, "1) x = 2\n2) 0,5"
        if "Глеб написал про него" in prompt:
            drafts.write(TASK, "Клетка делится митозом.")
            return True, "Убрал второе предложение"
        if "репетитор Глеба" in prompt:
            n = 3 if "НОВЫЕ вопросы" in prompt else 2
            return True, json.dumps({
                "title": "Дроби <умножение>", "points": ["a/b · c/d = ac/bd", "**Сократи** заранее"],
                "sections": [{"title": "Умножение", "text": "Числитель на числитель"}],
                "mistakes": ["Забыл сократить"],
                "questions": [{"q": f"вопрос {i} <x>", "a": f"ответ {i}"} for i in range(1, n + 1)]},
                ensure_ascii=False)
        if prompt.startswith("Ответь одним словом"):
            return True, "ok"
        return True, "- писать короче"  # обобщение правок

    # ---- node / python3 ----
    def node(self, *args, timeout=0, prog="node"):
        a = list(args)
        if self.fail:
            return False, "⚠️ Не получилось: page.goto: net::ERR_CONNECTION_RESET <x>"
        if prog == "python3":
            if a[:2] == ["horo/alphabet.py", "template"] or a[:2] == ["horo/alphabet.py", "sample"] \
                    or a[:2] == ["horo/alphabet.py", "preview"]:
                pathlib.Path(a[-1]).parent.mkdir(parents=True, exist_ok=True)
                pathlib.Path(a[-1]).write_bytes(b"png")
                return True, ""
            if a[:2] == ["horo/alphabet.py", "ingest"]:
                return True, "Добавил: а б <в>\nСовет: пришли файлом"
            if a[:2] == ["horo/alphabet.py", "reset"]:
                return True, "Алфавит сброшен"
            if a[:1] == ["-c"]:
                return True, ""
        else:
            if a == ["horo/overview.js"]:
                return True, json.dumps(OVERVIEW, ensure_ascii=False)
            if a == ["horo/status.js", "--json"]:
                return True, json.dumps(TASKS, ensure_ascii=False)
            if a[:1] == ["horo/status.js"]:
                return True, '📋 <b>Задания</b>\n<blockquote>🆕 <a href="' + URL + '">Конспект §5</a> · 15.10</blockquote>'
            if a[:1] == ["horo/comments.js"]:
                return True, "💬 <b>Учителя</b>\n<blockquote>Доделать № 3</blockquote>"
            if a[:1] == ["horo/grades.js"]:
                return True, "📊 <b>Оценки</b>\n<blockquote>🟢 Алгебра — 4</blockquote>"
            if a[:1] == ["horo/prep.js"]:
                if len(a) == 1:
                    return True, json.dumps(TOPICS, ensure_ascii=False)
                if a[1] == "--theory":
                    return True, json.dumps([{"title": "Теория", "text": "a/b · c/d = ac/bd"}], ensure_ascii=False)
                return True, json.dumps(DETAILS, ensure_ascii=False)
            if a[:1] == ["horo/answer.js"]:
                return True, "Статус: checking"
        self.unknown.append((prog, *a))
        return True, ""


class CrawlTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        t = self.tmpdir.name
        self.w = w = World(t)
        sup = support.Support.__new__(support.Support)  # без потока _commands и без сети
        sup.token, sup.name, sup.folders, sup.pm2 = "test", "HDP", ["bot", "horo"], "horo-bot"
        sup.allowed, sup.tests, sup.cwd = (lambda uid: uid == bot.owner()), [], t
        sup.media_dir, sup.waiting, sup.plain = os.path.join(t, "support"), {}, False
        sup.tg = w.tg

        def start(chat, text, shots=None):  # настоящий run запускает Claude Code — здесь только первый статус
            w.support.append((chat, text, shots))
            sup.send(chat, f"🛠 <b>Поддержка «HDP»</b>\n<i>читаю код и логи</i>")
        sup.start = start
        self.sup = sup

        def no_net(*a, **k):
            raise OSError("сеть в тесте закрыта")

        def fake_run(cmd, **kw):
            return mock.Mock(returncode=0, stdout="", stderr="")

        def exit_(code=0):
            raise Exited()

        def download(fid):
            if w.fail:
                raise OSError("Telegram getFile: timed out")
            return b"jpg", "sheet.jpg"

        def voice(token, msg):
            if w.fail:
                raise bot.media.MediaError("в голосовом не слышно слов")
            return "Оценки."

        def photo(token, msg, folder=None):
            if w.fail:
                raise OSError("timed out")
            os.makedirs(folder, exist_ok=True)
            p = os.path.join(folder, "photo.jpg")
            pathlib.Path(p).write_bytes(b"jpg")
            return p

        def video(token, msg, folder=None, frames=4):
            if w.fail:
                raise bot.media.MediaError("видео больше 20 МБ")
            os.makedirs(folder, exist_ok=True)
            shots = [os.path.join(folder, f"frame-{i}.jpg") for i in (1, 2)]
            for s in shots:
                pathlib.Path(s).write_bytes(b"jpg")
            return {"frames": shots, "text": "", "seconds": 3}

        self.patches = [
            mock.patch.object(bot, "SETTINGS", os.path.join(t, "settings.json")),
            mock.patch.object(bot, "OVERVIEW", os.path.join(t, "overview.json")),
            mock.patch.object(bot, "PREP_DIR", os.path.join(t, "prep")),
            mock.patch.object(bot, "SITE_DIR", os.path.join(t, "site")),
            mock.patch.object(bot, "ALPHA_DIR", os.path.join(t, "alphabet")),
            mock.patch.object(bot, "GLEB_EXTRA", os.path.join(t, "fonts", "gleb_extra.npz")),
            mock.patch.object(bot, "HAND_STYLE", os.path.join(t, "hand_style.json")),
            mock.patch.object(bot, "INBOX", os.path.join(t, "inbox")),
            mock.patch.object(bot, "AGENT_ENV", os.path.join(t, "tg-agent.env")),
            mock.patch.object(bot, "HUB_ENV", os.path.join(t, "hub-bot.env")),
            mock.patch.object(bot, "APP_URL", APP),
            mock.patch.object(drafts, "TMP", t),
            mock.patch.object(drafts, "STYLE_DIR", os.path.join(t, "style")),
            mock.patch.object(drafts, "STYLE", os.path.join(t, "style", "style.md")),
            mock.patch.object(drafts, "EDITS", os.path.join(t, "style", "edits.jsonl")),
            mock.patch.object(web, "SESSIONS", os.path.join(t, "site_sessions.json")),
            mock.patch.object(bot.threading, "Thread", Sync),
            mock.patch.object(bot, "tg", w.tg),
            mock.patch.object(bot, "send_file", w.send_file),
            mock.patch.object(bot, "claude", w.claude),
            mock.patch.object(bot, "node", w.node),
            mock.patch.object(bot, "download", download),
            mock.patch.object(bot.media, "photo_path", photo),
            mock.patch.object(bot.media, "video", video),
            mock.patch.object(bot.media, "voice_text", voice),
            mock.patch.object(bot.subprocess, "run", fake_run),
            mock.patch.object(bot.urllib.request, "urlopen", no_net),
            mock.patch.object(bot.os, "_exit", exit_),
            mock.patch.dict(os.environ, {"HORO_SITE_URL": "https://hdp.example"}),
        ]
        for p in self.patches:
            p.start()
        os.makedirs(drafts.task_dir(TASK))
        with open(os.path.join(drafts.task_dir(TASK), "task.json"), "w") as f:
            json.dump({"data": {"type": "detailedAnswer", "title": "Конспект §5"}}, f)
        pathlib.Path(bot.OVERVIEW).write_text(json.dumps(OVERVIEW, ensure_ascii=False))
        self.reset_globals()
        self.uid = 0

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.reset_globals()
        self.tmpdir.cleanup()

    @staticmethod
    def reset_globals():
        for d in (bot.mode, bot.pending, bot.LAST, bot.PREP_CTX, bot.seen, bot.agent):
            d.clear()
        bot.PREP_JOB.update(running=False, result=None, error="", loading=False)
        bot.Live.active = None

    # ---- обновления Telegram ----
    def update(self, **body):
        self.uid += 1
        return {"update_id": self.uid, **body}

    def message(self, text=None, **extra):
        m = {"message_id": 500 + self.uid, "chat": {"id": CHAT, "type": "private"},
             "from": {"id": CHAT, "first_name": "Глеб"}, **extra}
        if text is not None:
            m["text"] = text
        return self.update(message=m)

    def callback(self, data, mid):
        return self.update(callback_query={"id": f"cq{self.uid}", "from": {"id": CHAT}, "data": data,
                                           "message": {"chat": {"id": CHAT, "type": "private"}, "message_id": mid}})

    def seeds(self):
        """Стартовые сообщения: /start, все команды меню, все кнопки клавиатуры, скрытые команды, медиа."""
        out = [("text", "/start")]
        out += [("text", f"/{c}") for c, _ in bot.COMMANDS if c not in ("start", "update")]
        out += [("text", b) for b in sorted(bot.BUTTONS)] + [("text", a) for a in sorted(bot.ALIASES)]
        out += [("text", "/alphabet"), ("msg", "document"), ("text", "готово"), ("text", "/style"), ("text", "тоньше"),
                ("msg", "photo"), ("msg", "video"), ("msg", "voice"),
                ("text", "что-то непонятное"), ("text", "подготовь к проверочной по истории"),
                ("text", "/start site"), ("text", "/emoji_reset"), ("text", "/alphabet_reset"),
                ("text", "/kpp"), ("text", "/hub"), ("text", "/coach"), ("text", "/planner"),
                ("text", "/agent"), ("text", bot.BTN_LIST), ("text", "/cancel"),
                ("text", URL), ("msg", "photo"), ("text", "убери второе"), ("text", "покажи " + TEST_URL),
                ("text", "/fix"), ("text", "кнопка Оценки молчит"),  # следующий текст после /fix — просьба поддержке
                ("text", "/update")]  # последним: «перезапуск»
        return out

    def as_update(self, kind, data, mid):
        if kind == "cb":
            return self.callback(data, mid)
        if data == "photo":
            return self.message(photo=[{"file_id": "p1", "width": 90}, {"file_id": "p2", "width": 1280}])
        if data == "video":
            return self.message(video={"file_id": "v1", "duration": 3})
        if data == "document":  # лист алфавита файлом (без сжатия)
            return self.message(document={"file_id": "d1", "file_name": "alphabet_1.png", "mime_type": "image/png"})
        if data == "voice":
            return self.message(voice={"file_id": "a1", "duration": 2})
        return self.message(data)

    # ---- одно нажатие ----
    def press(self, kind, data, mid=None):
        start = len(self.w.calls)
        out = io.StringIO()
        exited = False
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            try:
                bot.on_update(self.as_update(kind, data, mid), self.sup)
            except Exited:
                exited = True
            except Exception as e:  # on_update сам ловит всё; сюда — только поломка самого входа
                print("Traceback (crawler):", repr(e))
        return self.w.calls[start:], out.getvalue(), exited

    def check_markup(self, where, markup, problems, ui):
        if not markup:
            return []
        m = json.loads(markup) if isinstance(markup, str) else markup
        found = []
        for key in ("inline_keyboard", "keyboard"):
            for row in m.get(key) or []:
                if len(row) > 3:
                    problems.append(f"{where}: в ряду {len(row)} кнопок: {[b.get('text') for b in row]}")
                for b in row:
                    text = b.get("text") or ""
                    if not text or len(text) > 24:
                        problems.append(f"{where}: кнопка «{text}» — {len(text)} символов (> 24)")
                    if "callback_data" in b:
                        if len(b["callback_data"].encode()) > 64:
                            problems.append(f"{where}: callback_data > 64 байт: {b['callback_data']}")
                        found.append(("cb", b["callback_data"]))
                    elif "web_app" in b:
                        if not str(b["web_app"].get("url", "")).startswith("https://"):
                            problems.append(f"{where}: web_app без https: {b}")
                        if ui == "chat" and where not in ("text:/app", "text:" + bot.BTN_APP):
                            problems.append(f"{where}: в режиме «чат» кнопка приложения «{text}»")
                    elif "url" in b:
                        if not str(b["url"]).startswith(("https://", "tg://")):
                            problems.append(f"{where}: плохая ссылка {b['url']}")
                    elif key == "keyboard":
                        found.append(("text", text))
                    else:
                        problems.append(f"{where}: кнопка «{text}» ничего не делает: {b}")
        return found

    def crawl(self, ui):
        """BFS от стартовых сообщений; потом «перезапуск» бота (память процесса пропала, файлы остались)
        и каждая инлайн-кнопка ещё раз — как старые кнопки, которые висят в чате."""
        bot.save_settings({**bot.load_settings(), "owner": CHAT, "mode": ui})
        queue = collections.deque((k, d, None, 0) for k, d in self.seeds())
        done, problems, mids = set(), [], {}
        presses = 0

        def step(kind, data, mid, depth, where):
            calls, log, exited = self.press(kind, data, mid)
            now = bot.ui_mode()  # кнопка «📱 Приложение» сменила режим — её ответ проверяем по новому режиму,
            if now != ui:        # а обход продолжаем в своём
                bot.set_mode(ui)
            for bad in BAD_LOG:
                if bad in log:
                    problems.append(f"{where}: в логе «{bad}»:\n{log.strip()[-1500:]}")
                    break
            shown = [c for c in calls if c[0] in ("sendMessage", "editMessageText", "sendPhoto", "sendDocument")
                     or (c[0] == "editMessageReplyMarkup" and kind == "text")]
            if not shown and not exited:
                problems.append(f"{where}: нет видимого ответа ({[c[0] for c in calls]})")
            for method, p, new in calls:
                text = p.get("text")
                if isinstance(text, str):
                    if len(text) > 4096:
                        problems.append(f"{where}: {method} длиннее 4096 ({len(text)})")
                    if p.get("parse_mode") == "HTML" and html_problem(text):
                        problems.append(f"{where}: {method} HTML: {html_problem(text)}\n{text[:400]}")
                for nk, nd in self.check_markup(where, p.get("reply_markup"), problems, now):
                    if depth < DEPTH:
                        queue.append((nk, nd, new or p.get("message_id"), depth + 1))
                        mids.setdefault(nd, new or p.get("message_id"))

        while queue and presses < CAP:
            kind, data, mid, depth = queue.popleft()
            if depth and (kind, data) in done:  # стартовые сообщения — все, кнопки — по разу
                continue
            done.add((kind, data))
            n0 = len(self.w.calls)
            step(kind, data, mid, depth, f"{kind}:{data}")
            presses += 1
            if os.environ.get("CRAWL_LOG"):  # CRAWL_LOG=1 — что ответил бот на каждое нажатие
                print(f"{kind}:{data} -> " + " | ".join((c[1].get("text") or c[0])[:70].replace("\n", " ")
                                                       for c in self.w.calls[n0:] if c[0] != "sendChatAction"),
                      file=sys.__stderr__)
        self.reset_globals()  # перезапуск: pm2 поднял бота заново
        queue.clear()
        for kind, data in sorted(done):
            if kind == "cb" and presses < CAP:
                step(kind, data, mids.get(data), DEPTH, f"после перезапуска {kind}:{data}")
                presses += 1
        queue.clear()
        return presses, done, problems

    def run_mode(self, ui):
        presses, done, problems = self.crawl(ui)
        self.assertEqual(self.w.unknown, [], "скрипты, которых обход не ждал")
        self.assertEqual(problems, [], "\n\n".join(problems))
        cbs = {d for k, d in done if k == "cb"}
        # должны были дойти до всех веток: меню «Ещё», режим, подготовка, черновик, фото, автотесты, почерк, поддержка
        for need in ("m:more", "m:help", "m:mode", "m:late", "m:hand", "m:check", "mode:chat", "mode:app",
                     "pp:s:0", "pp:t:5", "pp:tz", "pp:back", "pp:more", "pp:deep", f"send:{TASK}", f"drop:{TASK}", f"ed:{TASK}",
                     "ph:do", "ph:show", "ph:solve", "all:yes", "all:no", "hand:ok", "hand:mess:1", "sup|ask", "sup|no"):
            self.assertIn(need, cbs, f"обход не дошёл до {need}")
        self.assertTrue(self.w.support, "поддержка не получила просьбу")
        print(f"\n[{ui}] нажатий: {presses} (разных инлайн-кнопок: {len(cbs)}, сообщений и кнопок клавиатуры: "
              f"{len(done) - len(cbs)}; инлайн-кнопки — ещё раз после перезапуска)", file=sys.__stderr__)
        return presses

    def test_chat_mode(self):
        self.run_mode("chat")

    def test_app_mode(self):
        self.run_mode("app")

    def test_everything_fails(self):
        """Claude, сайт и скачивание из Telegram отказывают: на каждое нажатие — понятная ошибка, без трассировок."""
        self.w.fail = True
        drafts.write(TASK, "Черновик")
        bot.save_settings({"owner": CHAT, "mode": "chat",
                           "draft": {"task": TASK, "url": URL, "title": "Конспект §5", "pairs": [], "own_files": False}})
        presses, done, problems = self.crawl("chat")
        self.assertEqual(problems, [], "\n\n".join(problems))
        self.assertGreater(presses, 60)
        bot.mode[CHAT] = "alphabet"  # лист алфавита не скачался
        calls, log, _ = self.press("msg", "document")
        self.assertEqual(log.strip(), "alphabet sheet: Telegram getFile: timed out")
        self.assertIn("Не получилось скачать лист", calls[-1][1]["text"])
        print(f"\n[всё падает] нажатий: {presses + 1}", file=sys.__stderr__)

    def test_first_start_without_mode(self):
        """Первый /start: режима ещё нет — карточка выбора; кнопки ведут дальше."""
        bot.save_settings({"owner": CHAT})
        calls, log, _ = self.press("text", "/start")
        self.assertIn("mode:chat", calls[-1][1]["reply_markup"])
        calls, log, _ = self.press("cb", "mode:app", calls[-1][2])
        self.assertEqual(bot.ui_mode(), "app")
        self.assertTrue(any(c[0] == "sendMessage" for c in calls))
        self.assertEqual(log.strip().count("Error"), 0)

    def said(self, calls, words):
        return any(words in (c[1].get("text") or "") for c in calls)

    def test_buttons_leave_waiting_steps(self):
        """Бот ждёт токен (/agent), эмодзи (/emoji) или «да» на автотесты, а Глеб жмёт кнопку клавиатуры или
        команду — выполняется кнопка, а не «пришли токен» / «эмодзи не нашёл» / «отменил»."""
        bot.save_settings({"owner": CHAT, "mode": "chat"})
        self.press("text", "/agent")
        calls, _, _ = self.press("text", bot.BTN_GRADES)
        self.assertTrue(self.said(calls, "📊 <b>Оценки</b>"), calls)
        self.assertNotIn(CHAT, bot.agent)
        self.press("text", "/agent")
        self.assertTrue(self.said(self.press("text", "/check")[0], "Всё работает"))
        self.assertNotIn(CHAT, bot.mode)  # шаг /agent закончен

        self.press("text", "/emoji")
        self.assertTrue(self.said(self.press("text", bot.BTN_COMM)[0], "💬 <b>Учителя</b>"))
        self.press("text", "/emoji")
        self.assertTrue(self.said(self.press("text", "просто текст")[0], "Премиум-эмодзи в сообщении не нашёл"))

        calls, _, _ = self.press("text", bot.BTN_ALL)
        mid = calls[-1][2]
        self.assertTrue(self.said(self.press("text", bot.BTN_LIST)[0], "📋 <b>Задания</b>"))
        calls, _, _ = self.press("cb", "all:yes", mid)  # «Сдать все» под списком по-прежнему сдаёт
        self.assertTrue(self.said(calls, "Автотесты сданы: 2"), calls)
        self.press("text", bot.BTN_ALL)
        self.assertTrue(self.said(self.press("text", "нет")[0], "Отменил"))
        self.assertNotIn(CHAT, bot.pending)


class RoutesTest(unittest.TestCase):
    """Все маршруты API приложения и сайта: пустые и битые тела — понятная ошибка (400/403), не 500."""

    BODIES = [None, {}, {"url": 123}, {"url": "https://example.com"}, {"url": URL, "show": True},
              {"text": ["a"]}, {"text": "Новый текст"}, {"minutes": "abc"}, {"minutes": [1]}, {"minutes": 7},
              {"minutes": 1, "subj": "Алгебра"}, {"subj": "Алгебра", "topics": 5}, {"subj": "Алгебра", "topics": [None]},
              {"topics": "abc", "tz": "дроби"}, {"more": True}, {"again": True}, {"mode": None}, {"mode": "chat"},
              {"mode": "app"}, {"uuid": {"a": 1}}, {"uuid": U(1)}, {"uuid": "../../etc"}, {"fresh": 1},
              {"photo": "!!!", "text": "x"}, {"photo": 5, "text": "x"}, {"text": 5}]

    def setUp(self):
        self.case = CrawlTest("test_chat_mode")
        self.case.setUp()
        bot.save_settings({"owner": CHAT, "mode": "app"})
        p = mock.patch.object(bot, "SUP", self.case.sup)
        p.start()
        self.addCleanup(p.stop)
        web._cfg.update(token="test", owner=bot.owner, routes=bot.ROUTES, version="t", site_version="s")
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), web.Web)
        bot.REAL_THREAD(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.case.tearDown()

    def init_data(self, uid=CHAT):
        import hashlib, hmac, urllib.parse
        pairs = {"auth_date": str(int(time.time())), "query_id": "q", "user": json.dumps({"id": uid})}
        secret = hmac.new(b"WebAppData", b"test", hashlib.sha256).digest()
        pairs["hash"] = hmac.new(secret, "\n".join(f"{k}={v}" for k, v in sorted(pairs.items())).encode(),
                                 hashlib.sha256).hexdigest()
        return urllib.parse.urlencode(pairs)

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_port, timeout=10)
        c.request(method, path, body, {"X-Init-Data": self.init_data(), **(headers or {})})
        r = c.getresponse()
        return r.status, r.read()

    def test_every_route_with_garbage(self):
        n, problems = 0, []
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            for path in sorted(bot.ROUTES):
                for body in self.BODIES:
                    if body is None:
                        st, raw = self.req("GET", path)
                    else:
                        st, raw = self.req("POST", path, json.dumps(body), {"Content-Type": "application/json"})
                    n += 1
                    ok_codes = (200, 400, 403) + ((405,) if body is None and path in web.WRITE else ())  # GET на действие — 405
                    if st not in ok_codes:
                        problems.append(f"{path} {body}: {st} {raw[:200]!r}")
                    try:
                        json.loads(raw)
                    except ValueError:
                        problems.append(f"{path} {body}: не JSON {raw[:200]!r}")
                for raw_body in (b"{{{", b"[1, 2]", b"\"str\"", b"null", "ё".encode()):
                    st, raw = self.req("POST", path, raw_body, {"Content-Type": "application/json"})
                    n += 1
                    if st not in (200, 400, 403):
                        problems.append(f"{path} {raw_body!r}: {st} {raw[:200]!r}")
                bot.PREP_JOB.update(running=False)
        print(f"\n[api] запросов: {n} к {len(bot.ROUTES)} маршрутам", file=sys.__stderr__)
        self.assertEqual(problems, [], "\n".join(problems))
        self.assertNotIn("Traceback", out.getvalue(), out.getvalue()[-3000:])

    def test_negative_content_length_does_not_hang(self):
        """Content-Length: -1 — сервер не ждёт тело вечно."""
        s = socket.create_connection(("127.0.0.1", self.srv.server_port), timeout=5)
        try:
            s.sendall(b"POST /api/state HTTP/1.1\r\nHost: x\r\nContent-Length: -1\r\nX-Init-Data: "
                      + self.init_data().encode() + b"\r\n\r\n")
            head = b""
            while chunk := s.recv(4096):  # ответ целиком: сервер закрывает связь после него
                head += chunk
        finally:
            s.close()
        self.assertTrue(head.startswith(b"HTTP/1.0 200") or head.startswith(b"HTTP/1.1 200")
                        or b" 400 " in head[:20], head)

    def test_unknown_and_foreign(self):
        self.assertEqual(self.req("GET", "/api/nope")[0], 404)
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_port, timeout=10)
        c.request("POST", "/api/solve", json.dumps({"url": URL}), {"X-Init-Data": self.init_data(uid=7)})
        self.assertEqual(c.getresponse().status, 403)


if __name__ == "__main__":
    unittest.main()
