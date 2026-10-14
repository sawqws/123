"""Обход всех кнопок «Глаза» без Telegram, Claude, распознавания и сети.

Краулер шлёт /start, все команды из меню, текст, геопозицию, фото, голосовое, видео, кружок и стикер через
eye.handle (тот же вход, что у цикла getUpdates, вместе с 🛠 поддержкой), собирает все inline-кнопки из ответов
и «нажимает» каждую в ширину до глубины 4 (не больше 400 нажатий на обход); каждое нажатие — с того же
состояния, в котором кнопка была показана (сброс и повтор пути). Плюс «устаревшие» варианты кнопок.
Режимы 💬 чат / 📱 приложение / не выбран, со страницей (https) и без; с фото в памяти и пустой.
Падение: трассировка (даже пойманная и напечатанная), нажатие без видимого ответа, кнопка длиннее 24 символов,
больше 3 кнопок в ряд, callback_data длиннее 64 байт. И все api страницы с пустыми и мусорными телами — без 500.
usage: python3 eye/test_buttons.py
"""
import _thread
import contextlib
import http.client
import io
import json
import os
import shutil
import sys
import time
import traceback
import unittest
from collections import deque
from http.server import ThreadingHTTPServer
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_eye as te  # noqa: E402  (ставит окружение и импортирует eye)

eye, U, JPEG = te.eye, te.U, te.JPEG
URL = "https://eye.1-2-3-4.sslip.io"
VISIBLE = {"sendMessage", "editMessageText", "editMessageReplyMarkup", "sendPhoto", "sendDocument", "sendVoice",
           "deleteMessage"}
MARKUP = {"sendMessage", "editMessageText", "editMessageReplyMarkup", "sendPhoto", "sendDocument"}


# ---------- краулер (как в studio/test_buttons.py) ----------

def variants(data):
    """«Устаревшие» кнопки: без аргументов, с мусором вместо аргументов, с первым аргументом."""
    parts = data.split("|")
    if len(parts) < 2:
        return []
    out = [parts[0], "|".join([parts[0]] + ["zz"] * (len(parts) - 1))]
    if len(parts) > 2:
        out.append("|".join(parts[:2]))
    return out


class Crawler:
    def __init__(self, name, restore, do, log, waiting=lambda: False, cap=400, depth=4):
        self.name, self.restore, self.do, self.log, self.waiting = name, restore, do, log, waiting
        self.cap, self.depth = cap, depth
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
        found = []
        if "Traceback" in out:
            self.errors.append(f"{where}: исключение\n{out[-1500:]}")
        for method, p in new:
            if method not in MARKUP:
                continue
            for kind in ("inline_keyboard", "keyboard"):
                for row in (p.get("reply_markup") or {}).get(kind) or []:
                    if len(row) > 3:
                        self.errors.append(f"{where}: {len(row)} кнопки в ряд: {[b.get('text') for b in row]}")
                    for btn in row:
                        btn = btn if isinstance(btn, dict) else {"text": btn}
                        t = str(btn.get("text") or "")
                        self.buttons.add(t)
                        if len(t) > 24:
                            self.errors.append(f"{where}: кнопка длиннее 24: «{t}» ({len(t)})")
                        data = btn.get("callback_data")
                        if data is not None:
                            if len(data.encode()) > 64:
                                self.errors.append(f"{where}: callback_data > 64 байт: {data}")
                            found.append(data)
        if path[-1][0] == "press" or path[-1][2:3] == ("root",):
            visible = any(m in VISIBLE for m, _ in new) or \
                any(m == "answerCallbackQuery" and p.get("text") for m, p in new)
            if not visible:
                self.errors.append(f"{where}: нет видимого ответа{' (устаревшая кнопка)' if malformed else ''}: "
                                   f"{[m for m, _ in new]}")
        return found

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
            found = self.check(path, new, out, malformed)
            if malformed or len(path) - 1 >= self.depth:
                continue
            if self.waiting() and ("reply", path[-1][1]) not in seen:  # бот спросил — отвечаем текстом
                seen.add(("reply", path[-1][1]))
                queue.append((path + [("msg", "Кнопка не жмётся")], False))
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


def fake_support(log):
    """Поддержка как в бою (common/support.py), но без потоков, Telegram и Claude."""
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
    sup = support.Support(eye.TOKEN, "Глаз", folders=["eye"], pm2="eye-bot", allowed=eye.allowed,
                          tests=["python3 eye/test_eye.py"])
    return sup, patches, started


# ---------- «Глаз» ----------

LOOK = {"say": "Это Зимний дворец.", "text": "Зимний дворец — резиденция царей, 1762 год.", "title": "Зимний дворец"}


class Seeded(te.Base):
    """Подмены как в test_eye.Base плюс фото, история и место в памяти; restore() — сброс к этому состоянию."""

    def setUp(self):
        super().setUp()  # подменяет tg, tg_file, адрес, озвучку и потоки (всё идёт сразу)
        self.data = os.path.dirname(eye.STATE)

        def video(token, msg, folder=None):
            path = os.path.join(folder, "frame-1.jpg")
            with open(path, "wb") as f:
                f.write(JPEG)
            return {"frames": [path], "text": "что это за мост", "seconds": 3.0}
        more = [mock.patch.object(eye, "download", return_value=(JPEG, ".jpg")),
                mock.patch.object(eye.speech, "hear", return_value="что это за здание"),
                mock.patch.object(eye.ai, "look", return_value=LOOK),
                mock.patch.object(eye.ai, "talk", return_value="Это Исаакиевский собор."),
                mock.patch.object(eye.media, "video", side_effect=video),
                mock.patch.object(eye.media, "whisper_python", return_value=None),
                mock.patch.object(eye.urllib.request, "urlopen", side_effect=AssertionError("сеть в тесте"))]
        for p in more:
            p.start()
            self.addCleanup(p.stop)
        self.sup, patches, self.sup_started = fake_support(self.sent)
        for p in patches:
            self.addCleanup(p.stop)
        sp = mock.patch.object(eye, "SUP", self.sup)
        sp.start()
        self.addCleanup(sp.stop)
        self.seed_photos = []
        for i in range(3):
            rec = eye.add_photo(JPEG, (59.94, 30.31), source="chat", video=i == 2)
            eye.update_photo(rec["id"], title=f"Фото {i}" if i else "", say="Это фонтан." if i < 2 else "")
        eye.remember("user", "что это")
        eye.remember("assistant", "Фонтан.")
        eye.save_where(59.94, 30.31)
        with open(eye.STATE) as f:
            self.full = json.load(f)

    def restore(self, state, mode, url):
        st = json.loads(json.dumps(state))
        st["owner"] = U
        if mode:
            st["mode"] = mode
        else:
            st.pop("mode", None)
        with open(eye.STATE, "w") as f:
            json.dump(st, f)
        eye.events.clear()
        eye.audio.clear()
        eye.page_seen[0] = 0
        eye.public_url = url
        self.sup.waiting.clear()
        self.sent.clear()
        self.files.clear()

    def do(self, action):
        kind, x = action[0], action[1]
        if kind == "press":
            eye.handle({"update_id": 1, "callback_query": {"id": "1", "data": x, "from": {"id": U},
                                                           "message": {"message_id": 5, "chat": {"id": U, "type": "private"}}}})
        else:
            eye.handle({"update_id": 1, "message": self.msg(x) if isinstance(x, str) else self.msg(**x)})

    def roots(self):
        cmds = ["/start", "/help", "/app", "/mode", "/link", "/new", "/status", "/fix", "/неизвестная"]
        return cmds + ["что это за здание", {"location": {"latitude": 59.93, "longitude": 30.31}},
                       {"photo": [{"file_id": "a"}], "caption": "что это?"}, {"voice": {"file_id": "v"}},
                       {"video": {"file_id": "v"}}, {"video_note": {"file_id": "n"}},
                       {"document": {"file_id": "d", "mime_type": "image/jpeg"}},
                       {"document": {"file_id": "d", "mime_type": "audio/mpeg"}}, {"sticker": {"file_id": "s"}},
                       {"photo": [{"file_id": "a"}], "media_group_id": "g1"}]



class Crawl(Seeded):
    def test_crawl_all_buttons(self):
        res = []
        empty = {}
        for state, mode, url in ((empty, "", ""), (empty, "chat", ""), (self.full, "", URL), (self.full, "chat", URL),
                                 (self.full, "app", URL), (self.full, "app", "")):
            name = f"{'фото' if state else 'пусто'}/{mode or 'без режима'}/{'https' if url else 'нет https'}"
            orig = eye.public_url
            try:
                cr = Crawler(name, lambda: self.restore(state, mode, url), self.do, self.sent,
                             lambda: bool(self.sup.waiting)).run(self.roots())
            finally:
                eye.public_url = orig
            self.assertEqual(cr.errors, [], "\n\n".join(cr.errors[:15]))
            res.append(cr)
        print(f"\nГлаз: {len(res)} обходов, нажатий {sum(c.presses for c in res)}, сообщений "
              f"{sum(c.msgs for c in res)}, разных кнопок {len(set().union(*(c.buttons for c in res)))}",
              file=sys.__stderr__)
        self.assertTrue(self.sup_started)  # /fix → ответ → поддержка (заглушка)


class Api(Seeded):
    def setUp(self):
        super().setUp()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), eye.Web)
        self.port = self.httpd.server_address[1]
        _thread.start_new_thread(self.httpd.serve_forever, ())

    def tearDown(self):
        self.httpd.shutdown()
        super().tearDown()

    def call(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            c.request(method, path, body=body, headers=headers or {})
            r = c.getresponse()
            return r.status, r.read()
        except (OSError, http.client.HTTPException) as e:
            return None, repr(e)
        finally:
            c.close()

    def test_api_garbage(self):
        eye.public_url = ""
        pid = (eye.load().get("photos") or [{}])[0].get("id", "x")
        gets = ["/api/state", "/api/events", "/api/events?after=abc", "/api/events?after=-1", "/api/events?after=9e99",
                f"/api/photo/{pid}", "/api/photo/nope", "/api/photo/..%2f..%2fs.json", "/api/nope", "/a/" + "0" * 32 + ".mp3",
                "/", "/index.html", "/app.js", "/nope.js", "/../eye.py", "/%2e%2e/eye.py", "/health"]
        bodies = [(b"", ""), (b"{}", "application/json"), (b"[]", "application/json"), (b"[1, 2]", "application/json"),
                  (b'"x"', "application/json"), (b"1", "application/json"), (b"null", "application/json"),
                  (b"not json", "application/json"), (b"\xff\xfe", "application/json"),
                  (json.dumps({"text": 5, "mode": {"a": 1}, "photo": "%%%"}).encode(), "application/json"),
                  (json.dumps({"text": "что это", "mode": "chat"}).encode(), "application/json"),
                  (b"RIFF....WAVE", "audio/wav"), (b"junk", "audio/x-unknown"), (b"junk", "application/octet-stream"),
                  (JPEG, "image/jpeg"), (b"not an image", "image/png"), (b"junk", "video/mp4"), (b"junk", "text/plain")]
        posts = ["/api/upload", "/api/upload?lat=abc&lon=1", "/api/upload?lat=91&lon=500&q=%D1%87%D1%82%D0%BE", "/api/talk",
                 "/api/talk?lat=1e999&lon=nan", "/api/new", "/api/mode", "/api/where", "/api/support", "/api/nope"]
        key = {"X-Key": eye.web_key()}
        bad, n = [], 0
        with mock.patch.object(eye, "video_file", side_effect=eye.media.MediaError("не получилось открыть видео")):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                for path in gets:
                    code, body = self.call("GET", path, headers=key)
                    n += 1
                    if code in (None, 500):
                        bad.append(f"GET {path}: {code} {body[:200]}")
                for path in posts:
                    for raw, ctype in bodies:
                        code, body = self.call("POST", path, raw, {**key, "Content-Type": ctype} if ctype else key)
                        n += 1
                        if code in (None, 500):
                            bad.append(f"POST {path} {raw[:20]!r} {ctype}: {code} {body[:200]}")
                    code, _ = self.call("POST", path, b"{}", {"Content-Type": "application/json"})
                    if code != 401:
                        bad.append(f"POST {path} без ключа — {code}")
                code, _ = self.call("GET", "/api/state")
                if code != 401:
                    bad.append(f"GET /api/state без ключа — {code}")
            if "Traceback" in out.getvalue():
                bad.append("трассировка:\n" + out.getvalue()[-2000:])
        self.assertEqual(bad, [], "\n".join(bad))
        print(f"\nГлаз: api-вызовов {n}", file=sys.__stderr__)


if __name__ == "__main__":
    unittest.main()
