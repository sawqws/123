"""Тест get_api.py на имитации my.telegram.org: python3 tgbot/test_get_api.py"""
import http.server
import io
import json
import os
import sys
import threading
import unittest
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import get_api  # noqa: E402

FORM = '<form id="app_create_form"><input type="hidden" name="hash" value="ab12cd34"/></form>'
KEYS = ('<label for="app_id">App api_id:</label><div><span class="uneditable-input"><strong>1234567</strong></span></div>'
        '<label for="app_hash">App api_hash:</label><div><span class="uneditable-input">0123456789abcdef0123456789abcdef</span></div>')


def site(create_answer=""):
    state = {"logged": False, "app": False, "created": None}

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def reply(self, code, body, cookie=None):
            self.send_response(code)
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()
            self.wfile.write(body.encode())

        def do_GET(self):
            if self.path == "/auth":
                return self.reply(200, "<form>login</form>", "stel_ssid=s1; expires=Sat, 10 Oct 2027 15:00:00 GMT; "
                                                             "path=/; samesite=None; secure; HttpOnly")
            if self.path == "/apps":
                ok = "stel_token=t" in (self.headers.get("Cookie") or "")
                return self.reply(200, (KEYS if state["app"] else FORM) if ok else "<a href='/auth'>login</a>")
            self.reply(404, "")

        def do_POST(self):
            data = dict(urllib.parse.parse_qsl(self.rfile.read(int(self.headers["Content-Length"])).decode()))
            if self.path == "/auth/send_password":
                if data["phone"] != "+79991234567":
                    return self.reply(400, "Invalid phone number")
                return self.reply(200, json.dumps({"random_hash": "rh"}))
            if self.path == "/auth/login":
                if data.get("password") != "AbC1d" or data.get("random_hash") != "rh":
                    return self.reply(400, "Invalid confirmation code!")
                return self.reply(200, "true", "stel_token=t; path=/")
            if self.path == "/apps/create":
                state["created"] = data
                if create_answer:
                    return self.reply(400, create_answer)
                state["app"] = True
                return self.reply(200, "")
            self.reply(404, "")

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, state


def io_with(*answers):
    return io.StringIO("".join(a + "\n" for a in answers)), io.StringIO()


class GetApi(unittest.TestCase):
    def run_site(self, create_answer="", answers=("8 999 123-45-67", "AbC1d")):
        srv, state = site(create_answer)
        try:
            io_ = io_with(*answers)
            res = get_api.run(get_api.Site(f"http://127.0.0.1:{srv.server_port}"), io_)
            return res, state, io_[1].getvalue()
        finally:
            srv.shutdown()

    def test_creates_app_and_returns_keys(self):
        (api_id, api_hash), state, out = self.run_site()
        self.assertEqual((api_id, api_hash), ("1234567", "0123456789abcdef0123456789abcdef"))
        self.assertEqual(state["created"]["hash"], "ab12cd34")
        self.assertEqual(state["created"]["app_platform"], "desktop")
        self.assertTrue(5 <= len(state["created"]["app_shortname"]) <= 32 and state["created"]["app_shortname"].isalnum())
        self.assertIn("✓ Ключи получены", out)

    def test_wrong_code_then_right(self):
        (api_id, _), _, out = self.run_site(answers=("+79991234567", "zzz", "AbC1d"))
        self.assertEqual(api_id, "1234567")
        self.assertIn("Invalid confirmation code", out)

    def test_create_error_is_reported(self):
        with self.assertRaises(get_api.Fail) as e:
            self.run_site(create_answer="ERROR")
        self.assertIn("ERROR", str(e.exception))

    def test_normalize(self):
        for p in ("8 999 123-45-67", "+7 (999) 123-45-67", "79991234567"):
            self.assertEqual(get_api.normalize(p), "+79991234567")

    def test_parse_keys(self):
        self.assertEqual(get_api.parse_keys(KEYS), ("1234567", "0123456789abcdef0123456789abcdef"))
        self.assertIsNone(get_api.parse_keys(FORM))


if __name__ == "__main__":
    unittest.main()
