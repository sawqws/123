"""Тесты бота «КПП» без сети: python3 kpp/test_kpp.py"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["KPP_DATA"] = tempfile.mkdtemp()
import domyland as dl  # noqa: E402
import kpp  # noqa: E402


class Plates(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(dl.plates("А123ВС777"), ["А123ВС777"])

    def test_spaces_and_latin(self):
        self.assertEqual(dl.plates("белый Kia Rio, a 123 bc 77"), ["А123ВС77"])
        self.assertEqual(dl.plates("Номер: Х-777-ХХ-199"), ["Х777ХХ199"])

    def test_taxi_yellow(self):
        self.assertEqual(dl.plates("ек 123 77"), ["ЕК12377"])

    def test_no_plate(self):
        self.assertEqual(dl.plates("привет, как дела 12345"), [])
        self.assertEqual(dl.plates(""), [])

    def test_two(self):
        self.assertEqual(dl.plates("А123ВС777 и М456ОР50"), ["А123ВС777", "М456ОР50"])

    def test_pretty(self):
        self.assertEqual(dl.pretty("А123ВС777"), "А 123 ВС 777")
        self.assertEqual(dl.pretty("ЕК12377"), "ЕК 123 77")


class Token(unittest.TestCase):
    TOK = "y0_AgAAAAAAbcdefghijklmnopqrstuvwxyz123456"

    def test_url(self):
        url = f"https://oauth.yandex.ru/verification_code#access_token={self.TOK}&token_type=bearer&expires_in=31536000"
        self.assertEqual(dl.extract_token(url), self.TOK)

    def test_scheme(self):
        self.assertEqual(dl.extract_token(f"domyland://auth#state=&access_token={self.TOK}&expires_in=1"), self.TOK)

    def test_bare(self):
        self.assertEqual(dl.extract_token("  " + self.TOK + " "), self.TOK)

    def test_not_token(self):
        self.assertIsNone(dl.extract_token("А123ВС777"))


class Request(unittest.TestCase):
    def test_taxi_body_and_headers(self):
        seen = {}

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({"success": True, "data": {"title": "ok", "items": [{"id": 42}]}}).encode()

        def fake(req, timeout=0):
            seen["url"], seen["method"] = req.full_url, req.get_method()
            seen["headers"] = {k.lower(): v for k, v in req.header_items()}
            seen["body"] = json.loads(req.data)
            return Resp()

        c = dl.Client("y0_tok", "jwt", 11, 22)
        with mock.patch("urllib.request.urlopen", fake):
            resp = c.create_taxi_pass("А123ВС777", when=1700000000)
        self.assertEqual(seen["url"], "https://customer-api.domyland.ru/v2/pass")
        self.assertEqual(seen["method"], "POST")
        self.assertEqual(seen["headers"]["authorization"], "jwt")
        self.assertEqual(seen["headers"]["placeid"], "11")
        self.assertEqual(seen["headers"]["buildingid"], "22")
        b = seen["body"]
        self.assertEqual((b["type"], b["subType"], b["arriveDate"], b["comment"]), (1, 5, 1700000000000, ""))
        self.assertEqual(b["passes"][0]["regNumber"], "А123ВС777")
        self.assertTrue(b["passes"][0]["entityId"])
        self.assertEqual(dl.created_id(resp), 42)


class BotBase(unittest.TestCase):
    def setUp(self):
        self.sent = []
        kpp.st = {"owner": 1, "jwt": "j", "yandex_token": "y", "place_id": 5, "building_id": 6, "address": "ЖК"}

        def tg(method, **p):
            self.sent.append((method, p))
            return {"ok": True, "result": {"message_id": 7}}
        self.p = [mock.patch.object(kpp, "tg", tg), mock.patch.object(kpp, "save", lambda s: None)]
        for x in self.p:
            x.start()

    def tearDown(self):
        for x in self.p:
            x.stop()

    def msg(self, text, uid=1):
        kpp.on_message({"chat": {"id": 1, "type": "private"}, "from": {"id": uid}, "text": text})

    def texts(self):
        return [p.get("text", "") for m, p in self.sent if m in ("sendMessage", "editMessageText")]


class Bot(BotBase):
    def test_order_and_repeat(self):
        with mock.patch.object(dl.Client, "create_taxi_pass", return_value={"items": [{"id": 9}]}) as cr:
            self.msg("водитель на белом Kia, а 123 вс 777")
            self.msg("А123ВС777")
        self.assertEqual(cr.call_count, 1)  # второй раз не дублирует
        self.assertIn("Пропуск заказан", self.texts()[1])
        self.assertIn("уже заказан", self.texts()[2])
        self.assertEqual(kpp.st["recent"]["А123ВС777"]["id"], 9)

    def test_stranger(self):
        self.msg("А123ВС777", uid=2)
        self.assertIn("личный", self.texts()[0])

    def test_no_plate(self):
        self.msg("привет")
        self.assertIn("Не вижу номера", self.texts()[0])

    def test_auth_refresh(self):
        calls = []

        def create(self_, plate, *a, **k):
            calls.append(self_.jwt)
            if len(calls) == 1:
                raise dl.AuthError("401")
            return {"items": [{"id": 1}]}
        with mock.patch.object(dl.Client, "create_taxi_pass", create), \
                mock.patch.object(dl.Client, "login", lambda self_: setattr(self_, "jwt", "new") or "new"):
            self.msg("М456ОР50")
        self.assertEqual(calls, ["j", "new"])
        self.assertEqual(kpp.st["jwt"], "new")
        self.assertIn("Пропуск заказан", self.texts()[-1])

    def voice(self, transcript=None, error=None, spoken=""):
        vt = mock.patch.object(kpp.media, "voice_text", side_effect=error, return_value=transcript)
        with mock.patch.object(kpp, "spawn", lambda fn, *a: fn(*a)), vt as v, \
                mock.patch.object(kpp, "spoken_plate", return_value=spoken) as sp, \
                mock.patch.object(dl.Client, "create_taxi_pass", return_value={"items": [{"id": 3}]}) as cr:
            kpp.on_message({"chat": {"id": 1, "type": "private"}, "from": {"id": 1}, "voice": {"file_id": "v"}})
        v.assert_called_once()
        return sp, cr

    def test_voice_plate_in_text(self):
        sp, cr = self.voice("Номер А, 123, ВС, 777 <b>")
        sp.assert_not_called()
        self.assertEqual(cr.call_args[0][0], "А123ВС777")
        texts = self.texts()
        self.assertIn("Слушаю", texts[0])
        self.assertIn("🎙 <i>«Номер А, 123, ВС, 777 &lt;b&gt;»</i>", texts)
        self.assertIn("Пропуск заказан", texts[-1])

    def test_voice_spoken_words(self):
        sp, cr = self.voice("а сто двадцать три вэ эс семьсот семьдесят семь", spoken="А123ВС777\n")
        sp.assert_called_once_with("а сто двадцать три вэ эс семьсот семьдесят семь")
        self.assertEqual(cr.call_args[0][0], "А123ВС777")
        self.assertIn("Пропуск заказан", self.texts()[-1])

    def test_voice_no_plate(self):
        sp, cr = self.voice("привет", spoken="НЕТ")
        cr.assert_not_called()
        self.assertIn("Не расслышал", self.texts()[-1])

    def test_voice_error(self):
        sp, cr = self.voice(error=kpp.media.MediaError("в голосовом не слышно слов"))
        cr.assert_not_called()
        self.assertIn("не слышно слов", self.texts()[-1])

    def test_voice_in_thread(self):
        started = []
        with mock.patch.object(kpp.threading, "Thread", side_effect=lambda **k: started.append(k) or mock.Mock()):
            kpp.on_message({"chat": {"id": 1, "type": "private"}, "from": {"id": 1}, "voice": {"file_id": "v"}})
        self.assertEqual(len(started), 1)
        self.assertEqual(self.sent, [])

    def media_msg(self, **kw):
        return {"chat": {"id": 1, "type": "private"}, "from": {"id": 1}, **kw}

    def run_inline(self, msg, read="", video=None, spoken=""):
        """Медиа без потоков: картинки «читает» read, видео — словарь video, продиктованное — spoken."""
        with mock.patch.object(kpp, "spawn", lambda fn, *a: fn(*a)), \
                mock.patch.object(kpp.media, "photo_path", return_value="/tmp/x/photo.jpg"), \
                mock.patch.object(kpp.media, "video", return_value=video or {}) as mv, \
                mock.patch.object(kpp, "read_image", return_value=read) as ri, \
                mock.patch.object(kpp, "spoken_plate", return_value=spoken) as sp, \
                mock.patch.object(dl.Client, "create_taxi_pass", return_value={"items": [{"id": 4}]}) as cr:
            kpp.on_message(msg)
        return ri, cr, sp, mv

    def test_photo_screenshot(self):
        ri, cr, _, _ = self.run_inline(self.media_msg(document={"file_id": "d", "mime_type": "image/png"}),
                                       read="М456ОР50")
        ri.assert_called_once_with(["/tmp/x/photo.jpg"])
        self.assertEqual(cr.call_args[0][0], "М456ОР50")
        self.assertIn("Ищу номер", self.texts()[0])
        self.assertIn("Пропуск заказан", self.texts()[-1])

    def test_photo_caption_plate_no_claude(self):
        ri, cr, _, _ = self.run_inline(self.media_msg(photo=[{"file_id": "p"}], caption="А123ВС777"))
        ri.assert_not_called()  # номер уже в подписи
        self.assertEqual(cr.call_args[0][0], "А123ВС777")

    def test_photo_no_plate_and_no_claude(self):
        _, cr, _, _ = self.run_inline(self.media_msg(photo=[{"file_id": "p"}]), read="НЕТ")
        cr.assert_not_called()
        self.assertIn("Не разглядел", self.texts()[-1])
        self.sent.clear()
        self.run_inline(self.media_msg(photo=[{"file_id": "p"}]), read=None)
        self.assertIn("нет Claude", self.texts()[-1])

    def test_photo_and_video_in_thread(self):
        for kw in ({"photo": [{"file_id": "p"}]}, {"video": {"file_id": "v"}}):
            started = []
            with mock.patch.object(kpp.threading, "Thread", side_effect=lambda **k: started.append(k) or mock.Mock()):
                kpp.on_message(self.media_msg(**kw))
            self.assertEqual(len(started), 1)
        self.assertEqual(self.sent, [])  # поток опроса ничего не ждал

    def test_video_frames(self):
        v = {"frames": ["/tmp/v/frame-1.jpg", "/tmp/v/frame-2.jpg"], "text": "", "seconds": 8}
        ri, cr, sp, mv = self.run_inline(self.media_msg(video={"file_id": "v"}), read="Х777ХХ199", video=v)
        self.assertEqual(mv.call_args.kwargs.get("frames"), 6)
        ri.assert_called_once_with(v["frames"])
        sp.assert_not_called()
        self.assertEqual(cr.call_args[0][0], "Х777ХХ199")
        self.assertIn("Смотрю видео", self.texts()[0])
        self.assertIn("Нашёл", self.texts()[1])

    def test_video_speech_first(self):
        v = {"frames": ["/tmp/v/frame-1.jpg"], "text": "номер е к 123 77", "seconds": 5}
        ri, cr, _, _ = self.run_inline(self.media_msg(video={"file_id": "v"}), read="", video=v)
        ri.assert_not_called()  # номер нашёлся в речи — кадры Claude не читает
        self.assertEqual(cr.call_args[0][0], "ЕК12377")
        self.assertIn("🎙 <i>«номер е к 123 77»</i>", self.texts()[1])

    def test_video_note_voice(self):
        """Кружок теперь «video»: голос в нём тоже понимается, продиктованный словами номер — через Claude."""
        v = {"frames": ["/tmp/v/frame-1.jpg"], "text": "а сто двадцать три вэ эс семьсот семьдесят семь"}
        ri, cr, sp, _ = self.run_inline(self.media_msg(video_note={"file_id": "n"}), read="НЕТ", video=v,
                                        spoken="А123ВС777")
        self.assertEqual(kpp.media.kind(self.media_msg(video_note={"file_id": "n"})), "video")
        sp.assert_called_once_with(v["text"])
        self.assertEqual(cr.call_args[0][0], "А123ВС777")

    def test_video_nothing(self):
        _, cr, _, _ = self.run_inline(self.media_msg(video={"file_id": "v"}), read="НЕТ",
                                      video={"frames": ["/tmp/f.jpg"], "text": ""})
        cr.assert_not_called()
        self.assertIn("Не нашёл номер в видео", self.texts()[-1])
        self.sent.clear()
        with mock.patch.object(kpp, "spawn", lambda fn, *a: fn(*a)), \
                mock.patch.object(kpp.media, "video", side_effect=kpp.media.MediaError("видео больше 20 МБ")):
            kpp.on_message(self.media_msg(video={"file_id": "v"}))
        self.assertIn("⚠️ Видео не открылось: видео больше 20 МБ", self.texts()[-1])

    def test_repeat_last(self):
        with mock.patch.object(dl.Client, "create_taxi_pass", return_value={"items": [{"id": 9}]}) as cr:
            self.msg("М456ОР50")
            self.assertEqual(kpp.st["last"], "М456ОР50")
            kpp.st["recent"] = {}  # прошло больше 20 минут
            self.msg("/start")
            rows = self.sent[-1][1]["reply_markup"]["inline_keyboard"]
            rp = [b for r in rows for b in r if b.get("callback_data", "").startswith("rp|")]
            self.assertEqual(rp[0]["text"], "🔁 М 456 ОР 50")
            kpp.on_callback({"id": "c", "from": {"id": 1}, "data": rp[0]["callback_data"],
                             "message": {"chat": {"id": 1}, "message_id": 3}})
        self.assertEqual(cr.call_count, 2)

    def test_passes_view_compact(self):
        items = [{"id": 5, "title": "Такси", "status": {"title": "Активен"}, "subtitle": "А 123 ВС 777",
                  "fields": [{"title": "Дата", "value": "12.10"}], "actions": [{"type": "cancel"}]},
                 {"id": 4, "title": "Гость", "status": {"title": "Использован"}, "subtitle": "", "fields": []}]
        with mock.patch.object(dl.Client, "passes", return_value=items):
            text, rows = kpp.passes_view()
        self.assertIn("<code>А\u00a0123\u00a0ВС\u00a0777</code> · Активен · 12.10", text)
        self.assertIn("<b>Гость</b> · Использован", text)
        self.assertEqual([b["callback_data"] for r in rows for b in r], ["x|5", "m|home"])

    def test_needs_login(self):
        kpp.st = {"owner": 1}
        self.msg("А123ВС777")
        self.assertIn("войди", self.texts()[0])


def init_data(token, uid=1, age=0, **extra):
    """initData, подписанный как в Telegram (ключ HMAC("WebAppData", токен))."""
    import hashlib
    import hmac
    import time
    import urllib.parse
    pairs = {"auth_date": str(int(time.time()) - age), "query_id": "q",
             "user": json.dumps({"id": uid, "first_name": "Глеб"}), **extra}
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    pairs["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(pairs)


class InitData(unittest.TestCase):
    def setUp(self):
        self.p = mock.patch.object(kpp, "TOKEN", "1:test")
        self.p.start()
        kpp.st = {"owner": 1}

    def tearDown(self):
        self.p.stop()

    def test_valid(self):
        self.assertEqual(kpp.check_init_data(init_data("1:test"))["id"], 1)
        self.assertEqual(kpp.web_user({"X-Init-Data": init_data("1:test")})["id"], 1)

    def test_rejected(self):
        self.assertIsNone(kpp.check_init_data(init_data("2:other")))         # чужой бот
        self.assertIsNone(kpp.check_init_data(init_data("1:test", age=90000)))  # старше суток
        self.assertIsNone(kpp.check_init_data(init_data("1:test") + "x"))    # подделана подпись
        self.assertIsNone(kpp.check_init_data(""))
        self.assertIsNone(kpp.web_user({"X-Init-Data": init_data("1:test", uid=2)}))  # не владелец


class App(BotBase):
    """API мини-приложения: те же действия, что в чате."""

    def test_state(self):
        items = [{"id": 5, "title": "Такси", "status": {"title": "Активен"}, "subtitle": "А 123 ВС 777",
                  "fields": [{"title": "Дата", "value": "сегодня"}], "actions": [{"type": "cancel"}]}]
        with mock.patch.object(dl.Client, "passes", return_value=items):
            s = kpp.app_state()
        self.assertTrue(s["login"])
        self.assertEqual(s["address"], "ЖК")
        self.assertEqual(s["passes"][0]["status"], "Активен")
        self.assertTrue(s["passes"][0]["cancel"])
        kpp.st = {"owner": 1}
        self.assertFalse(kpp.app_state()["login"])

    def test_order_repeat_cancel(self):
        with mock.patch.object(dl.Client, "create_taxi_pass", return_value={"items": [{"id": 9}]}) as cr:
            code, res = kpp.api_post(1, "/api/order", {"text": "а 123 вс 777"})
            self.assertEqual((code, res["ok"], res["plate"], res["id"]), (200, True, "А123ВС777", 9))
            code, res = kpp.api_post(1, "/api/order", {"text": "А123ВС777"})
            self.assertTrue(res["repeat"])
            self.assertEqual(cr.call_count, 1)  # не дублирует
            kpp.api_post(1, "/api/order", {"text": "А123ВС777", "force": True})
            self.assertEqual(cr.call_count, 2)
        self.assertIn("Пропуск заказан", self.texts()[0])  # карточка ушла и в чат
        with mock.patch.object(dl.Client, "cancel") as cancel:
            code, _ = kpp.api_post(1, "/api/cancel", {"id": 9})
        cancel.assert_called_once_with("9")
        self.assertNotIn("А123ВС777", kpp.st["recent"])

    def test_order_errors(self):
        self.assertEqual(kpp.api_post(1, "/api/order", {"text": "привет"})[0], 400)
        with mock.patch.object(dl.Client, "create_taxi_pass", side_effect=dl.Error("занято")):
            code, res = kpp.api_post(1, "/api/order", {"text": "М456ОР50"})
        self.assertEqual(code, 502)
        self.assertIn("занято", res["error"])
        kpp.st = {"owner": 1}
        code, res = kpp.api_post(1, "/api/order", {"text": "М456ОР50"})
        self.assertEqual((code, res["login"]), (401, True))
        self.assertEqual(kpp.api_post(1, "/api/login", {"text": "не токен"})[0], 400)

    def test_api_photo(self):
        with mock.patch.object(kpp, "read_image", return_value="А123ВС777\n") as ri:
            code, res = kpp.api_photo(b"png", "image/png")
        self.assertEqual((code, res["plates"], res["pretty"]), (200, ["А123ВС777"], ["А 123 ВС 777"]))
        self.assertTrue(ri.call_args[0][0][0].endswith(".png"))
        with mock.patch.object(kpp, "read_image", return_value="НЕТ"):
            self.assertEqual(kpp.api_photo(b"jpg")[0], 422)
        with mock.patch.object(kpp, "read_image", return_value=None):
            self.assertEqual(kpp.api_photo(b"jpg")[0], 503)
        self.assertEqual(kpp.api_photo(b"")[0], 400)
        self.assertEqual(self.texts(), [])  # пропуск сам не заказывает

    def test_state_last(self):
        kpp.st["last"] = "ЕК12377"
        with mock.patch.object(dl.Client, "passes", return_value=[]):
            self.assertEqual(kpp.app_state()["last"], {"plate": "ЕК12377", "pretty": "ЕК 123 77"})

    def test_http(self):
        import threading
        import urllib.error
        import urllib.request
        from http.server import ThreadingHTTPServer
        srv = ThreadingHTTPServer(("127.0.0.1", 0), kpp.Web)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            with urllib.request.urlopen(base + "/health") as r:
                self.assertEqual(r.read(), b"ok")
            with urllib.request.urlopen(base + "/") as r:
                self.assertIn("kit.css?v=", r.read().decode())
            with urllib.request.urlopen(base + "/kit.css") as r:  # общий набор — ссылка на common/webapp
                self.assertIn("--accent", r.read().decode())
            with self.assertRaises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(base + "/api/state")
            self.assertEqual(e.exception.code, 401)
            with mock.patch.object(kpp, "TOKEN", "1:test"), mock.patch.object(dl.Client, "passes", return_value=[]):
                req = urllib.request.Request(base + "/api/state", headers={"X-Init-Data": init_data("1:test")})
                with urllib.request.urlopen(req) as r:
                    self.assertTrue(json.load(r)["login"])
                big = b"x" * (70 * 1024)  # картинка больше обычного лимита 64 КБ
                req = urllib.request.Request(base + "/api/photo", data=big, headers={
                    "X-Init-Data": init_data("1:test"), "Content-Type": "image/jpeg"})
                with mock.patch.object(kpp, "read_image", return_value="М456ОР50"), urllib.request.urlopen(req) as r:
                    self.assertEqual(json.load(r)["plates"], ["М456ОР50"])
        finally:
            srv.shutdown()
            srv.server_close()


class Style(unittest.TestCase):
    def test_style_fallback(self):
        """Цветные кнопки: если Telegram их не знает (400 про кнопки) — те же кнопки без цвета."""
        import io
        import urllib.error
        bodies = []

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake(req, timeout=0):
            body = json.loads(req.data)
            bodies.append(body)
            if "style" in json.dumps(body):
                raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {},
                                             io.BytesIO(b'{"description":"Bad Request: invalid button style"}'))
            return Resp(b'{"ok": true, "result": {"message_id": 1}}')
        with mock.patch("urllib.request.urlopen", fake):
            res = kpp.send(1, "x", [[kpp.btn("✖️ Отменить", "x|1", "danger")]])
        self.assertTrue(res["ok"])
        self.assertEqual(len(bodies), 2)
        self.assertEqual(bodies[1]["reply_markup"]["inline_keyboard"][0][0], {"text": "✖️ Отменить", "callback_data": "x|1"})


if __name__ == "__main__":
    unittest.main()
