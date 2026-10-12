"""Проверка «Глаза» без Telegram, Claude и распознавания: чат, страница (API), разбор фото, ответы.
usage: python3 eye/test_eye.py
"""
import _thread
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.update(EYE_TOKEN="222:EYE")
os.environ.pop("EYE_ALLOWED", None)
os.environ.pop("EYE_URL", None)
import ai  # noqa: E402
import eye  # noqa: E402
import photos  # noqa: E402

U = 7
JPEG = (b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xdb\x00C\x00" + bytes(64)
        + b"\xff\xd9")


class Base(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.p = [mock.patch.object(eye, "DATA", d), mock.patch.object(eye, "STATE", os.path.join(d, "s.json")),
                  mock.patch.object(eye, "PICS", os.path.join(d, "photos")),
                  mock.patch.object(eye, "tg", side_effect=self.fake_tg),
                  mock.patch.object(eye, "tg_file", side_effect=self.fake_file),
                  mock.patch.object(eye.photos, "address", return_value="Дворцовая площадь, 2, Санкт-Петербург"),
                  mock.patch.object(eye.speech, "tts", return_value=b"ID3mp3"),
                  mock.patch.object(eye.speech, "to_ogg", return_value=b"OggS"),
                  mock.patch.object(eye.threading, "Thread",
                                    lambda target, args=(), daemon=None: mock.Mock(start=lambda: target(*args)))]
        for p in self.p:
            p.start()
        self.sent, self.files = [], []
        eye.events.clear()
        eye.audio.clear()
        eye.page_seen[0] = 0
        eye.on_message(self.msg("/start"))

    def tearDown(self):
        for p in self.p:
            p.stop()

    def fake_tg(self, method, **kw):
        self.sent.append((method, kw))
        if method == "getFile":
            return {"ok": True, "result": {"file_path": "photos/file_1.jpg"}}
        if method == "sendMessage":
            return {"ok": True, "result": {"message_id": len(self.sent)}}
        return {"ok": True, "result": {}}

    def fake_file(self, method, field, name, data, ctype, **kw):
        self.files.append((method, data, kw))
        return {"ok": True}

    def msg(self, text="", uid=U, **extra):
        m = {"chat": {"id": uid, "type": "private"}, "from": {"id": uid}, "message_id": 1}
        if text:
            m["text"] = text
        m.update(extra)
        return m

    def texts(self):
        return [kw["text"] for k, kw in self.sent if k in ("sendMessage", "editMessageText")]

    def methods(self):
        return [k for k, kw in self.sent if k not in ("sendChatAction", "getFile")]


class ChatTest(Base):
    def test_owner_only(self):
        self.assertEqual(eye.owner(), U)
        self.assertIn("Глаз", self.texts()[-1])
        eye.on_message(self.msg("привет", uid=99))
        self.assertIn("личный", self.texts()[-1])

    def test_photo_in_chat(self):
        look = {"say": "Это Зимний дворец, построен в 1762 году.", "text": "Зимний дворец — резиденция царей.",
                "title": "Зимний дворец"}
        eye.save_where(59.9398, 30.3146)
        with mock.patch.object(eye, "download", return_value=(JPEG, ".jpg")), \
                mock.patch.object(eye.ai, "look", return_value=look) as lk:
            eye.on_message(self.msg(photo=[{"file_id": "a"}, {"file_id": "big"}], caption="что это?"))
        path, place, hist, question = lk.call_args[0]
        self.assertTrue(os.path.exists(path))
        self.assertIn("Дворцовая площадь", place)
        self.assertEqual(question, "что это?")
        self.assertIn("<b>Зимний дворец</b>", self.texts()[-1])
        self.assertIn("<blockquote>Зимний дворец — резиденция царей.</blockquote>", self.texts()[-1])
        self.assertIn("📍", self.texts()[-1])
        # «Смотрю…» превращается в ответ на месте, а не новым сообщением
        self.assertEqual(self.methods()[-2:], ["sendMessage", "editMessageText"])
        self.assertIn("Смотрю", self.texts()[-2])
        wait_id = [kw for k, kw in self.sent if k == "editMessageText"][-1]["message_id"]
        self.assertEqual(self.sent[wait_id - 1][0], "sendMessage")
        self.assertEqual(self.files[0][0], "sendVoice")  # страница закрыта — ответ голосовым в чат
        ph = eye.recent_photos()
        self.assertEqual(ph[-1]["title"], "Зимний дворец")
        self.assertEqual([e["kind"] for e in eye.events], ["photo_new", "photo"])
        self.assertTrue(eye.events[-1]["audio"].startswith("/a/"))

    def test_no_voice_when_page_open(self):
        eye.page_seen[0] = time.time()
        with mock.patch.object(eye, "download", return_value=(JPEG, ".jpg")), \
                mock.patch.object(eye.ai, "look", return_value={"say": "Кот.", "text": "Кот.", "title": "Кот"}):
            eye.on_message(self.msg(photo=[{"file_id": "a"}]))
        self.assertEqual(self.files, [])

    def test_voice_and_text_share_context(self):
        with mock.patch.object(eye, "download", return_value=(b"ogg", ".oga")), \
                mock.patch.object(eye.speech, "hear", return_value="когда его построили"), \
                mock.patch.object(eye.ai, "talk", return_value="В 1762 году.") as talk:
            eye.on_message(self.msg(voice={"file_id": "v"}))
            self.assertIn("1762", self.texts()[-1])
            self.assertIn("«когда его построили»", self.texts()[-1])  # услышанное — в том же сообщении
            self.assertEqual(self.methods()[-2:], ["sendMessage", "editMessageText"])
            self.assertEqual(self.files[-1][0], "sendVoice")
            eye.on_message(self.msg("а кто архитектор?"))
        hist = talk.call_args[0][3]
        self.assertEqual(hist[-2:], [{"role": "user", "text": "когда его построили"},
                                     {"role": "assistant", "text": "В 1762 году."}])
        eye.on_message(self.msg("/new"))
        self.assertEqual(eye.history(), [])

    def test_location_and_status(self):
        eye.on_message(self.msg(location={"latitude": 55.75, "longitude": 37.62}))
        self.assertIn("Запомнил", self.texts()[-1])
        self.assertIn("Дворцовая", eye.place_now())
        eye.on_message(self.msg("/status"))
        self.assertIn("Проверка", self.texts()[-1])

    def test_ai_error_is_reported(self):
        with mock.patch.object(eye.ai, "talk", side_effect=ai.AIError("у Claude закончился лимит")):
            eye.on_message(self.msg("что это"))
        self.assertIn("лимит", self.texts()[-1])
        self.assertTrue(self.texts()[-1].startswith("⚠️ У Claude"))
        self.assertNotIn("\n", self.texts()[-1])

    def test_start_menu_and_callbacks(self):
        with mock.patch.object(eye, "public_url", "https://eye.example"):
            eye.on_message(self.msg("/start"))
        kb = [kw for k, kw in self.sent if k == "sendMessage"][-1]["reply_markup"]["inline_keyboard"]
        self.assertEqual(kb[0][0]["web_app"]["url"], "https://eye.example/")
        self.assertEqual(kb[0][0]["style"], "primary")
        self.assertTrue(all(len(row) <= 3 for row in kb))
        self.assertTrue(all(len(b["text"]) <= 24 for row in kb for b in row))
        eye.remember("user", "привет")
        n = len(self.texts())
        eye.on_callback({"id": "1", "from": {"id": U}, "data": "new", "message": {"chat": {"id": U}, "message_id": 5}})
        self.assertEqual(eye.history(), [])
        self.assertEqual(len(self.texts()), n)  # без нового сообщения — всплывашка
        self.assertIn("заново", self.sent[-1][1]["text"])
        eye.on_message(self.msg("/status"))
        self.assertEqual(self.sent[-1][1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"], "status")
        eye.on_callback({"id": "2", "from": {"id": U}, "data": "status",
                         "message": {"chat": {"id": U}, "message_id": 9}})
        edit = [kw for k, kw in self.sent if k == "editMessageText"][-1]
        self.assertEqual(edit["message_id"], 9)
        self.assertIn("Проверка", edit["text"])
        eye.remember("user", "секрет")
        eye.on_callback({"id": "3", "from": {"id": 99}, "data": "new", "message": {"chat": {"id": 99}, "message_id": 1}})
        self.assertIn("личный", self.sent[-1][1]["text"])
        self.assertEqual(len(eye.history()), 1)

    def test_button_style_fallback(self):
        def picky(method, **kw):
            self.sent.append((method, kw))
            rows = kw.get("reply_markup", {}).get("inline_keyboard", [])
            if any("style" in b for row in rows for b in row):
                e = urllib.error.HTTPError("u", 400, "Bad Request", {}, None)
                e.body = '{"ok":false,"description":"Bad Request: can\'t parse inline keyboard button: style"}'
                raise e
            return {"ok": True, "result": {"message_id": 1}}
        with mock.patch.object(eye, "tg", side_effect=picky), mock.patch.object(eye, "public_url", "https://e.x"):
            eye.on_message(self.msg("/app"))
        first, second = self.sent[-2][1], self.sent[-1][1]
        self.assertEqual(first["reply_markup"]["inline_keyboard"][0][0]["style"], "primary")
        self.assertNotIn("style", second["reply_markup"]["inline_keyboard"][0][0])
        self.assertEqual(second["reply_markup"]["inline_keyboard"][0][0]["text"], "👁 Открыть «Глаз»")


class WebTest(Base):
    def setUp(self):
        super().setUp()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), eye.Web)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        _thread.start_new_thread(self.httpd.serve_forever, ())  # threading.Thread подменён — запросы идут по очереди

    def tearDown(self):
        self.httpd.shutdown()
        super().tearDown()

    def call(self, path, body=None, ctype="application/json", key=True, headers=None):
        h = {"X-Key": eye.web_key()} if key else {}
        h.update(headers or {})
        if body is not None:
            h["Content-Type"] = ctype
        req = urllib.request.Request(self.url + path, data=body, headers=h, method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                data = r.read()
                return r.status, json.loads(data) if r.headers.get_content_type() == "application/json" else data
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_auth(self):
        self.assertEqual(self.call("/api/state", key=False)[0], 401)
        self.assertEqual(self.call("/api/state", headers={"X-Key": "0" * 32}, key=False)[0], 401)
        code, st = self.call("/api/state")
        self.assertEqual(code, 200)
        self.assertIn("#k=", st["safari"] or "#k=")
        self.assertEqual(self.call("/health")[1], b"ok")

    def test_init_data(self):
        import hashlib
        import hmac
        user = json.dumps({"id": U, "first_name": "Глеб"})
        pairs = {"auth_date": str(int(time.time())), "user": user}
        secret = hmac.new(b"WebAppData", b"222:EYE", hashlib.sha256).digest()
        check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        pairs["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        good = urllib.parse.urlencode(pairs)
        self.assertEqual(self.call("/api/state", key=False, headers={"X-Init-Data": good})[0], 200)
        self.assertEqual(self.call("/api/state", key=False, headers={"X-Init-Data": good.replace("7", "8")})[0], 401)

    def test_page(self):
        code, html = self.call("/")
        self.assertEqual(code, 200)
        self.assertIn(b"app.js?v=", html)
        self.assertNotIn(b"{{v}}", html)

    def test_upload_events_and_photo(self):
        look = {"say": "Это фонтан.", "text": "Фонтан на площади.", "title": "Фонтан"}
        with mock.patch.object(eye.ai, "look", return_value=look):
            code, r = self.call("/api/upload?lat=59.93&lon=30.31", JPEG, "image/jpeg")
        self.assertEqual(code, 200)
        code, ev = self.call("/api/events?after=0")
        kinds = [e["kind"] for e in ev["events"]]
        self.assertEqual(kinds, ["photo_new", "photo"])
        audio_url = ev["events"][-1]["audio"]
        self.assertEqual(self.call(audio_url, key=False)[1], b"ID3mp3")
        code, img = self.call(f"/api/photo/{r['id']}")
        self.assertEqual(code, 200)
        self.assertEqual(self.call(f"/api/photo/{r['id']}", key=False)[0], 401)
        st = self.call("/api/state")[1]
        self.assertEqual(st["photos"][0]["title"], "Фонтан")
        self.assertIn("Фонтан", self.texts()[-1])  # и в чат тоже

    def test_talk_audio_and_text(self):
        with mock.patch.object(eye.speech, "hear", return_value="что это за здание") as hear, \
                mock.patch.object(eye.ai, "talk", return_value="Это Исаакиевский собор.") as talk:
            code, r = self.call("/api/talk?lat=59.934&lon=30.306", b"RIFF....WAVE", "audio/wav")
            self.assertEqual(code, 200)
            self.assertEqual(r["heard"], "что это за здание")
            self.assertEqual(r["say"], "Это Исаакиевский собор.")
            self.assertTrue(r["audio"].startswith("/a/"))
            self.assertEqual(hear.call_args[0][1], ".wav")
            self.assertIn("Дворцовая", talk.call_args[0][2])
            code, r = self.call("/api/talk", json.dumps({"text": "спасибо"}).encode())
            self.assertEqual(r["heard"], "спасибо")
        with mock.patch.object(eye.speech, "hear", return_value=""):
            self.assertEqual(self.call("/api/talk", b"RIFF", "audio/wav")[1], {"heard": "", "say": ""})
        with mock.patch.object(eye.speech, "hear", side_effect=eye.speech.SpeechError("не разобрал звук")):
            code, r = self.call("/api/talk", b"RIFF", "audio/wav")
            self.assertEqual((code, r["error"]), (503, "не разобрал звук"))


class AITest(unittest.TestCase):
    def test_clean(self):
        self.assertEqual(ai.clean("**Это** [собор](http://x.ru) 🙂\n- построен в 1858"),
                         "Это собор построен в 1858")

    def test_context(self):
        ctx = ai.context([{"path": "/p/1.jpg", "ago": "2 мин", "text": "Собор", "place": "Невский, 1"}],
                         "Невский, 1", [{"role": "user", "text": "а это?"}])
        self.assertIn("/p/1.jpg", ctx)
        self.assertIn("Глеб: а это?", ctx)
        self.assertIn("GPS", ctx)

    def test_run_model_fallback(self):
        calls = []

        def fake(cmd, **kw):
            calls.append(cmd)
            if "--model" in cmd:
                return subprocess.CompletedProcess(cmd, 1, "", "Error: model haiku not found")
            return subprocess.CompletedProcess(cmd, 0, json.dumps({"result": "Ответ."}), "")
        with mock.patch.object(ai.subprocess, "run", fake):
            self.assertEqual(ai.talk("привет"), "Ответ.")
        self.assertEqual(len(calls), 2)
        self.assertIn("haiku", calls[0])

    def test_look_json(self):
        out = '```json\n{"say": "Это мост.", "text": "Дворцовый мост.", "title": "Дворцовый мост"}\n```'
        with mock.patch.object(ai, "run", return_value=out):
            self.assertEqual(ai.look("/p.jpg")["title"], "Дворцовый мост")
        with mock.patch.object(ai, "run", return_value="Просто текст **без** JSON"):
            self.assertEqual(ai.look("/p.jpg")["say"], "Просто текст без JSON")


@unittest.skipIf(photos.Image is None, "нет Pillow")
class PhotoTest(unittest.TestCase):
    def test_prepare_resizes_and_reads_gps(self):
        from PIL import Image
        img = Image.new("RGB", (4000, 3000), "red")
        exif = img.getexif()
        gps = exif.get_ifd(0x8825)
        gps.update({1: "N", 2: (59.0, 56.0, 23.28), 3: "E", 4: (30.0, 18.0, 52.56)})
        buf = io.BytesIO()
        img.save(buf, "JPEG", exif=exif)
        data, where = photos.prepare(buf.getvalue())
        self.assertEqual(Image.open(io.BytesIO(data)).size, (1600, 1200))
        self.assertAlmostEqual(where[0], 59.9398, places=3)
        self.assertAlmostEqual(where[1], 30.3146, places=3)

    def test_not_image(self):
        self.assertEqual(photos.prepare(b"nope"), (b"nope", None))


if __name__ == "__main__":
    unittest.main(verbosity=1)
