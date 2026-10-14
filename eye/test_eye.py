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
        eye.setting("mode", "app")  # режим выбран (📱): кнопки страницы в сообщениях есть
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
    def test_no_own_caddy_next_to_planner(self):
        with tempfile.NamedTemporaryFile() as f, mock.patch.object(eye, "PLANNER_ENV", f.name):
            self.assertTrue(eye.planner_here())  # есть Планер — https раздаёт его Caddy, свой не запускаем
        with mock.patch.object(eye, "PLANNER_ENV", "/nonexistent/planner-bot.env"):
            self.assertFalse(eye.planner_here())

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
        path, place, hist, question, heard = lk.call_args[0]
        self.assertEqual(heard, "")
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

    def fake_video(self, frames=2, text="что это за мост"):
        def go(token, msg, folder=None):
            self.video_dir = folder
            paths = []
            for i in range(frames):
                paths.append(os.path.join(folder, f"frame-{i + 1}.jpg"))
                with open(paths[-1], "wb") as f:
                    f.write(JPEG)
            return {"frames": paths, "text": text, "seconds": 8.0}
        return go

    def test_video_in_chat(self):
        look = {"say": "Это Дворцовый мост.", "text": "Дворцовый мост, разводной, 1916 год.", "title": "Дворцовый мост"}
        with mock.patch.object(eye.media, "video", side_effect=self.fake_video()) as vid, \
                mock.patch.object(eye.ai, "look", return_value=look) as lk:
            eye.on_message(self.msg(video={"file_id": "v", "duration": 8}, caption="а это?"))
        self.assertEqual(vid.call_args[0][0], eye.TOKEN)
        frames, place, hist, question, heard = lk.call_args[0]
        self.assertEqual([os.path.basename(f) for f in frames], ["frame-1.jpg", "frame-2.jpg"])
        self.assertEqual((question, heard), ("а это?", "что это за мост"))
        self.assertIn("Смотрю видео", self.texts()[-2])
        self.assertEqual(self.methods()[-2:], ["sendMessage", "editMessageText"])
        last = self.texts()[-1]
        self.assertTrue(last.startswith("🎬 <b>Дворцовый мост</b>"))
        self.assertIn("<blockquote>Дворцовый мост, разводной", last)
        self.assertIn("«что это за мост»", last)
        self.assertEqual(eye.events[0]["kind"], "photo_new")
        self.assertTrue(eye.events[0]["video"])
        self.assertEqual(eye.events[-1]["kind"], "photo")
        rec = eye.recent_photos()[-1]
        self.assertTrue(rec["video"])
        self.assertEqual(eye.history()[-2]["text"], "[видео Дворцовый мост] а это?")
        self.assertFalse(os.path.exists(self.video_dir))  # временные кадры убраны
        self.assertEqual(self.files[-1][0], "sendVoice")

    def test_video_note_is_video_not_voice(self):
        look = {"say": "Кот.", "text": "Рыжий кот.", "title": "Кот"}
        with mock.patch.object(eye.media, "video", side_effect=self.fake_video(1, "")) as vid, \
                mock.patch.object(eye.speech, "hear") as hear, \
                mock.patch.object(eye.ai, "look", return_value=look) as lk:
            eye.on_message(self.msg(video_note={"file_id": "n", "duration": 2}))
        self.assertTrue(vid.called)
        self.assertFalse(hear.called)
        self.assertIsInstance(lk.call_args[0][0], list)
        self.assertIn("<b>Кот</b>", self.texts()[-1])
        self.assertNotIn("🎙", self.texts()[-1])

    def test_video_errors(self):
        with mock.patch.object(eye.media, "video", side_effect=eye.media.MediaError("видео больше 20 МБ")):
            eye.on_message(self.msg(video={"file_id": "v"}))
        self.assertEqual(self.texts()[-1], "⚠️ Видео больше 20 МБ")
        self.assertEqual(self.methods()[-1], "editMessageText")
        # кадров нет, а речь есть — отвечаем как на голосовой вопрос
        with mock.patch.object(eye.media, "video", side_effect=self.fake_video(0, "привет")), \
                mock.patch.object(eye.ai, "talk", return_value="Привет!") as talk:
            eye.on_message(self.msg(video={"file_id": "v"}))
        self.assertEqual(talk.call_args[0][0], "привет")
        self.assertIn("Привет!", self.texts()[-1])

    def test_audio_document_and_unknown(self):
        with mock.patch.object(eye, "download", return_value=(b"mp3", ".mp3")), \
                mock.patch.object(eye.speech, "hear", return_value="который час"), \
                mock.patch.object(eye.ai, "talk", return_value="Скоро вечер."):
            eye.on_message(self.msg(document={"file_id": "d", "mime_type": "audio/mpeg"}))
        self.assertIn("Скоро вечер.", self.texts()[-1])
        eye.on_message(self.msg(sticker={"file_id": "s"}))
        self.assertIn("Это не разберу", self.texts()[-1])

    def test_help_is_short(self):
        eye.on_message(self.msg("/help"))
        self.assertLess(len(self.texts()[-1]), 400)
        self.assertIn("видео", self.texts()[-1])

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
        self.assertEqual(kb[-1][0]["callback_data"], "sup|ask")  # 🛠 Поддержка
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

    def markups(self):
        return [kw.get("reply_markup") or {} for k, kw in self.sent if k in ("sendMessage", "editMessageText")]

    @staticmethod
    def has_page(markup):
        return any("web_app" in b or "url" in b for row in markup.get("inline_keyboard", []) for b in row)

    @staticmethod
    def datas(markup):
        return [b.get("callback_data") for row in markup.get("inline_keyboard", []) for b in row]

    def test_mode_choice_on_first_start(self):
        """Первый /start — карточка «Как удобнее?»; выбор запоминается, /mode и «⚙️ Режим» показывают её снова."""
        eye.setting("mode", delete=True)
        with mock.patch.object(eye, "public_url", "https://eye.example"):
            eye.on_message(self.msg("/start"))
            self.assertIn("Как удобнее?", self.texts()[-1])
            self.assertEqual(self.datas(self.markups()[-1]), ["mode|chat", "mode|app"])
            self.assertFalse(self.has_page(self.markups()[-1]))
            eye.on_callback({"id": "1", "from": {"id": U}, "data": "mode|chat", "message": {"chat": {"id": U}, "message_id": 3}})
            self.assertEqual(eye.ui_mode(), "chat")
            edit = [kw for k, kw in self.sent if k == "editMessageText"][-1]
            self.assertEqual((edit["message_id"], "Режим: 💬 чат" in edit["text"]), (3, True))
            self.assertIn("Режим: 💬 чат", self.texts()[-1])  # и сразу главное меню чата с подписью режима
            self.assertRegex(self.texts()[-1], r"<i>[^<]*Режим: 💬 чат[^<]*</i>")
            menu = self.markups()[-1]
            self.assertFalse(self.has_page(menu))
            for d in ("new", "seen", "status", "mode|ask", "sup|ask"):
                self.assertIn(d, self.datas(menu))
            self.assertTrue(all(len(row) <= 3 and all(len(b["text"]) <= 24 for b in row) for row in menu["inline_keyboard"]))
            self.sent.clear()
            eye.on_message(self.msg("/start"))  # режим выбран — сразу меню, без карточки
            self.assertNotIn("Как удобнее?", self.texts()[-1])
            eye.on_message(self.msg("/mode"))
            self.assertIn("Как удобнее?", self.texts()[-1])
            eye.on_callback({"id": "2", "from": {"id": U}, "data": "mode|ask", "message": {"chat": {"id": U}, "message_id": 4}})
            self.assertIn("Как удобнее?", self.texts()[-1])
            eye.on_callback({"id": "3", "from": {"id": U}, "data": "mode|app", "message": {"chat": {"id": U}, "message_id": 5}})
            self.assertEqual(eye.ui_mode(), "app")
            self.assertIn("Режим: 📱 приложение", self.texts()[-1])
            menu = self.markups()[-1]
            self.assertEqual(menu["inline_keyboard"][0][0]["web_app"]["url"], "https://eye.example/")
            self.assertIn("mode|ask", self.datas(menu))
            self.assertLessEqual(sum(len(r) for r in menu["inline_keyboard"]), 6)
            eye.on_callback({"id": "4", "from": {"id": 99}, "data": "mode|chat", "message": {"chat": {"id": 99}, "message_id": 1}})
            self.assertEqual(eye.ui_mode(), "app")  # чужой — не меняет

    def test_chat_mode_has_no_page_buttons(self):
        """В режиме «чат» ни web_app, ни ссылок на страницу в сообщениях — кроме явного /app; «👁 Что видел» — в чате."""
        eye.set_mode("chat")
        self.sent.clear()
        with mock.patch.object(eye, "public_url", "https://eye.example"), \
                mock.patch.object(eye.ai, "look", return_value={"title": "Зимний дворец", "say": "Это Зимний дворец.", "text": ""}), \
                mock.patch.object(eye.ai, "talk", return_value="Архитектор — Растрелли."), \
                mock.patch.object(eye, "download", return_value=(JPEG, "a.jpg")):
            for t in ("/start", "/help", "/status", "привет"):
                eye.on_message(self.msg(t))
            eye.on_message(self.msg(photo=[{"file_id": "a"}]))
            eye.use_url("https://eye.example")
            for m in self.markups():
                self.assertFalse(self.has_page(m), m)
            self.assertIn("Режим: 💬 чат", self.texts()[0])
            self.assertIn("Зимний дворец", "".join(self.texts()))  # фото в чате разобрано
            self.assertIn("sendVoice", [f[0] for f in self.files])  # и озвучено в чат (страница закрыта)
            eye.on_callback({"id": "1", "from": {"id": U}, "data": "seen", "message": {"chat": {"id": U}, "message_id": 3}})
            self.assertIn("Зимний дворец", self.texts()[-1])
            self.assertTrue(self.texts()[-1].startswith("👁 <b>Что видел</b>"))
            self.sent.clear()
            eye.on_message(self.msg("/app"))  # явная просьба — кнопка страницы есть и в режиме «чат»
            self.assertEqual(self.markups()[-1]["inline_keyboard"][0][0]["web_app"]["url"], "https://eye.example/")

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


    def cb(self, data, mid=5, uid=U):
        eye.on_callback({"id": "c", "from": {"id": uid}, "data": data, "message": {"chat": {"id": uid}, "message_id": mid}})
        return [kw for k, kw in self.sent if k == "answerCallbackQuery"]

    def test_every_callback_answered_once(self):
        for data in ("new", "status", "seen", "home", "mode|ask", "mode|chat", "mode|app", "zzz", ""):
            self.sent.clear()
            self.assertEqual(len(self.cb(data)), 1, data)
        self.sent.clear()
        self.assertIn("устарела", self.cb("old|1")[0]["text"])
        self.sent.clear()
        with mock.patch.object(eye, "status_text", side_effect=RuntimeError("диск")), \
                mock.patch.object(eye.traceback, "print_exc"), mock.patch("builtins.print"):
            a = self.cb("status")
        self.assertEqual(len(a), 1)  # упало внутри — нажатие всё равно отвечено
        self.assertTrue(a[0]["show_alert"])

    def test_seen_and_back_edit_in_place(self):
        self.sent.clear()
        self.cb("seen", mid=12)
        self.assertEqual([k for k, _ in self.sent][:1], ["editMessageText"])  # меню правится, не новое сообщение
        edit = self.sent[0][1]
        self.assertEqual(edit["message_id"], 12)
        self.assertIn("home", self.datas(edit["reply_markup"]))
        self.sent.clear()
        self.cb("home", mid=12)
        self.assertIn("seen", self.datas(self.sent[0][1]["reply_markup"]))
        self.sent.clear()
        self.cb("mode|ask", mid=12)
        self.assertEqual(self.sent[0][0], "editMessageText")
        self.assertIn("Как удобнее?", self.sent[0][1]["text"])
        eye.on_message(self.msg("/status"))
        self.assertIn("home", self.datas(self.sent[-1][1]["reply_markup"]))  # из проверки есть выход

    def test_text_reply_shows_thinking_then_answer(self):
        self.sent.clear()
        with mock.patch.object(eye.ai, "talk", return_value="Это Нева."):
            eye.on_message(self.msg("что за река"))
        self.assertIn("Думаю", self.texts()[0])
        self.assertEqual(self.sent[-1][0], "editMessageText")  # заглушка стала ответом
        self.assertIn("Это Нева.", self.texts()[-1])
        self.sent.clear()
        with mock.patch.object(eye.ai, "talk", side_effect=OSError("нет места")), \
                mock.patch.object(eye.traceback, "print_exc"):
            eye.on_message(self.msg("а мост"))
        self.assertTrue(self.texts()[-1].startswith("⚠️ Не получилось ответить"))

    def test_photo_download_error(self):
        self.sent.clear()
        with mock.patch.object(eye, "download", side_effect=OSError("timed out")), mock.patch("builtins.print"):
            eye.on_message(self.msg(photo=[{"file_id": "a"}]))
        self.assertTrue(self.texts()[-1].startswith("⚠️ Не открыл фото"))

    def test_unhandled_error_answered(self):
        self.sent.clear()
        eye.oops({"callback_query": {"id": "c"}})
        eye.oops({"message": {"chat": {"id": U, "type": "private"}}})
        self.assertEqual(self.sent[0][0], "answerCallbackQuery")
        self.assertIn("Что-то пошло не так", self.texts()[-1])


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

    def test_support_from_page(self):
        import support
        sup = support.Support.__new__(support.Support)  # без потоков и Telegram; start — заглушка
        sup.allowed, sup.media_dir, calls = eye.allowed, tempfile.mkdtemp(), []
        sup.start = lambda chat, text, shots=None: calls.append((chat, text, shots))
        body = lambda d: json.dumps(d).encode()
        self.assertEqual(self.call("/api/support", body({"text": "x"}))[0], 503)  # ещё не запущена
        with mock.patch.object(eye, "SUP", sup):
            self.assertEqual(self.call("/api/support", body({"text": "x"}), key=False)[0], 401)
            code, r = self.call("/api/support", body({"text": "не слышу ответ", "photo": "iVBORw0KGgo="}))
            self.assertEqual((code, r), (200, {"ok": True}))
            self.assertEqual(calls[-1][0], U)  # по ключу Safari — чат владельца
            self.assertTrue(os.path.exists(calls[-1][2][0]))
            self.assertEqual(self.call("/api/support", body({"text": " "}))[0], 400)
        self.assertEqual(len(calls), 1)

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
        self.assertLess(html.index(b'"kit.css'), html.index(b'"app.css'))
        self.assertLess(html.index(b'"kit.js'), html.index(b'"app.js'))
        code, css = self.call("/kit.css")  # общий набор — по ссылке из common/webapp
        self.assertEqual(code, 200)
        self.assertIn(b"--accent", css)

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

    def test_upload_video(self):
        look = {"say": "Это Нева.", "text": "Река Нева у Эрмитажа.", "title": "Нева"}

        def frames(src, folder, n, seconds):
            with open(src, "rb") as f:
                self.assertEqual(f.read(), b"MP4DATA")
            out = []
            for i in range(n):
                out.append(os.path.join(folder, f"frame-{i}.jpg"))
                with open(out[-1], "wb") as f:
                    f.write(JPEG)
            return out
        with mock.patch.object(eye.media, "_seconds", return_value=12.0), \
                mock.patch.object(eye.media, "_frames", side_effect=frames), \
                mock.patch.object(eye.media, "transcribe", return_value="красиво"), \
                mock.patch.object(eye.ai, "look", return_value=look) as lk:
            code, r = self.call("/api/upload?lat=59.94&lon=30.31", b"MP4DATA", "video/mp4")
        self.assertEqual((code, r["video"]), (200, True))
        self.assertEqual(len(lk.call_args[0][0]), 4)
        self.assertEqual(lk.call_args[0][4], "красиво")
        ev = self.call("/api/events?after=0")[1]["events"]
        self.assertEqual([e["kind"] for e in ev], ["photo_new", "photo"])
        st = self.call("/api/state")[1]
        self.assertTrue(st["photos"][0]["video"])
        self.assertEqual(st["photos"][0]["title"], "Нева")
        with mock.patch.object(eye.media, "_seconds", return_value=0.0), \
                mock.patch.object(eye.media, "_frames", return_value=[]), \
                mock.patch.object(eye.media, "transcribe", side_effect=eye.media.MediaError("нет слов")):
            self.call("/api/upload", b"junk", "video/quicktime")
        ev = self.call("/api/events?after=0")[1]["events"]
        self.assertEqual((ev[-1]["kind"], ev[-1]["text"]), ("error", "не получилось открыть видео"))

    def test_mode_api(self):
        """«💬 Перейти в чат» на странице: POST /api/mode → режим «чат», в чат приходит главное меню без кнопок страницы."""
        self.assertEqual(self.call("/api/state")[1]["mode"], "app")
        n = len(self.texts())
        with mock.patch.object(eye, "public_url", "https://eye.example"):
            code, r = self.call("/api/mode", json.dumps({"mode": "chat"}).encode())
        self.assertEqual((code, r), (200, {"mode": "chat"}))
        self.assertEqual(eye.ui_mode(), "chat")
        self.assertEqual(len(self.texts()), n + 1)
        self.assertIn("Режим: 💬 чат", self.texts()[-1])
        kb = self.sent[-1][1]["reply_markup"]["inline_keyboard"]
        self.assertFalse(any("web_app" in b or "url" in b for row in kb for b in row))
        self.assertIn("mode|ask", [b.get("callback_data") for row in kb for b in row])
        self.assertEqual(self.call("/api/state")[1]["mode"], "chat")
        self.assertEqual(self.call("/api/mode", json.dumps({"mode": "pdf"}).encode())[0], 400)
        self.assertEqual(self.call("/api/mode", json.dumps({"mode": "app"}).encode())[1], {"mode": "app"})
        self.assertEqual(len(self.texts()), n + 1)  # обратно в приложение — без сообщения в чат
        self.assertEqual(self.call("/api/mode", json.dumps({"mode": "chat"}).encode(), key=False)[0], 401)

    def test_mode_api_chat_send_fails(self):
        def flaky(method, **kw):
            if method == "sendMessage":
                raise urllib.error.URLError("сеть")
            return {"ok": True, "result": {}}
        with mock.patch.object(eye, "tg", side_effect=flaky), mock.patch("builtins.print"):
            code, r = self.call("/api/mode", json.dumps({"mode": "chat"}).encode())
        self.assertEqual((code, r), (200, {"mode": "chat"}))  # режим сменён, хоть чат и не ответил
        self.assertEqual(eye.ui_mode(), "chat")

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

    def test_look_video_prompt(self):
        seen = {}

        def fake(prompt, tools, model, turns, timeout):
            seen.update(prompt=prompt, turns=turns)
            return '{"say": "Мост.", "text": "Мост.", "title": "Мост"}'
        with mock.patch.object(ai, "run", side_effect=fake):
            ai.look(["/v/1.jpg", "/v/2.jpg", "/v/3.jpg"], heard="что это")
        self.assertIn("видео", seen["prompt"])
        self.assertIn("/v/3.jpg", seen["prompt"])
        self.assertIn("«что это»", seen["prompt"])
        self.assertEqual(seen["turns"], 10)

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
