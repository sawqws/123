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


class Bot(unittest.TestCase):
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
        self.assertIn("🎙 «Номер А, 123, ВС, 777 &lt;b&gt;»", texts)
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

    def test_photo_screenshot(self):
        with mock.patch.object(kpp, "read_photo", return_value="М456ОР50") as rp, \
                mock.patch.object(dl.Client, "create_taxi_pass", return_value={"items": [{"id": 4}]}):
            kpp.on_message({"chat": {"id": 1, "type": "private"}, "from": {"id": 1},
                            "document": {"file_id": "d", "mime_type": "image/png"}})
        rp.assert_called_once()
        self.assertIn("Пропуск заказан", self.texts()[-1])

    def test_needs_login(self):
        kpp.st = {"owner": 1}
        self.msg("А123ВС777")
        self.assertIn("войди", self.texts()[0])


if __name__ == "__main__":
    unittest.main()
