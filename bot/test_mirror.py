"""Тесты зеркала платформы (bot/mirror.py + маршруты в bot/web.py): python3 bot/test_mirror.py

Платформу заменяет локальный HTTP-сервер: mirror.connect подменён, сеть не нужна."""

import gzip
import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mirror  # noqa: E402
import web  # noqa: E402


class Upstream(BaseHTTPRequestHandler):
    """Поддельная платформа: запоминает запросы и отвечает по пути."""
    seen = []
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def answer(self, code, body, ctype, extra=()):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def handle_any(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        Upstream.seen.append((self.command, self.path, dict(self.headers), body))
        if self.path == "/student":
            html = ('<!DOCTYPE html><html><head><!-- Yandex.Metrika Prod counter --><script>ym(1,"init")</script>'
                    '<!-- /Yandex.Metrika counter --><title>Платформа HDP</title></head><body>'
                    '<img src="https://horodigital.ru/student/static/a.png"></body></html>').encode()
            return self.answer(200, html, "text/html; charset=utf-8", [("Set-Cookie", "i18n=ru; Path=/")])
        if self.path == "/api/education/v1/x":
            data = gzip.compress(rb'{"url": "https:\/\/horodigital.ru\/store\/f.png", '
                                 rb'"u2": "https://horodigital.ru/store/g.png"}')
            return self.answer(200, data, "application/json", [("Content-Encoding", "gzip")])
        if self.path == "/api/auth/v1/expired":
            return self.answer(401, b"{}", "application/json")
        if self.path == "/student/assets/app.js":
            return self.answer(200, b"console.log('https://horodigital.ru')", "application/javascript",
                               [("Cache-Control", "public, max-age=31536000")])
        if self.path == "/go":
            return self.answer(302, b"", "text/plain", [("Location", "https://horodigital.ru/login")])
        self.answer(404, b"{}", "application/json")

    do_GET = do_POST = do_PUT = do_DELETE = handle_any


class MirrorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.up = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        threading.Thread(target=cls.up.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.up.shutdown()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        state = os.path.join(self.tmp.name, "state.json")
        with open(state, "w") as f:
            json.dump({"cookies": [
                {"name": "hdp-portal-session", "value": "SECRET", "domain": "horodigital.ru", "expires": -1},
                {"name": "_ym_uid", "value": "1", "domain": ".yandex.ru", "expires": -1}]}, f)
        port = self.up.server_port
        self.patches = [
            mock.patch.object(mirror, "connect", lambda: http.client.HTTPConnection("127.0.0.1", port, timeout=10)),
            mock.patch.object(mirror, "_jar", mirror.Jar(state)),
            mock.patch.object(mirror, "_pool", mirror.Pool()),
            mock.patch.object(mirror, "relogin", mock.Mock(return_value=False)),
            mock.patch.object(web, "SESSIONS", os.path.join(self.tmp.name, "sessions.json")),
        ]
        for p in self.patches:
            p.start()
        Upstream.seen.clear()
        web._cfg.update(token="t", owner=lambda: 1, routes={"/api/state": lambda b: {"bot": True}},
                        version="1", site_version="1", mirror_version="7")
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), web.Web)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.token = web.site_login(web.site_code())

    def tearDown(self):
        self.srv.shutdown()
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def req(self, method, path, host="hdp.1-2-3-4.sslip.io", cookie=True, headers=None, body=None):
        h = {"Host": host, **(headers or {})}
        if cookie:
            h["Cookie"] = f"hdp_site={self.token}"
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_port, timeout=10)
        c.request(method, path, body, h)
        r = c.getresponse()
        return r.status, dict(r.getheaders()), r.read()

    def test_page_is_real_platform_with_bot_buttons(self):
        code, h, body = self.req("GET", "/student", headers={"Accept": "text/html"})
        html = body.decode()
        self.assertEqual(code, 200)
        self.assertIn("<title>Платформа HDP</title>", html)                 # страница — настоящая
        self.assertNotIn("Yandex.Metrika", html)                           # без метрики
        self.assertNotIn("ym(1", html)
        self.assertIn('<head><script src="/_hdp/guard.js?v=7"></script>', html)  # guard — первым
        self.assertIn('/_hdp/overlay.js?v=7', html)
        self.assertIn('src="/student/static/a.png"', html)                  # ссылки — через зеркало
        self.assertNotIn("Set-Cookie", h)                                   # cookie платформы в браузер не уходят
        self.assertEqual(h.get("Cache-Control"), "no-store")
        method, path, sent, _ = Upstream.seen[-1]
        self.assertEqual(sent["Host"], "horodigital.ru")
        self.assertIn("hdp-portal-session=SECRET", sent["Cookie"])          # сессия бота
        self.assertNotIn("hdp_site", sent["Cookie"])                        # наша cookie на платформу не уходит
        self.assertNotIn("_ym_uid", sent["Cookie"])                         # чужие домены — нет
        self.assertIn("i18n", mirror._jar.c)                                # Set-Cookie платформы — в банку

    def test_json_gzip_rewritten(self):
        code, h, body = self.req("GET", "/api/education/v1/x", headers={"Origin": "https://hdp.1-2-3-4.sslip.io"})
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body), {"url": "/store/f.png", "u2": "/store/g.png"})
        self.assertNotIn("Content-Encoding", h)
        self.assertEqual(Upstream.seen[-1][2]["Origin"], "https://horodigital.ru")

    def test_assets_streamed_as_is(self):
        code, h, body = self.req("GET", "/student/assets/app.js")
        self.assertEqual((code, body), (200, b"console.log('https://horodigital.ru')"))
        self.assertEqual(h["Cache-Control"], "public, max-age=31536000")

    def test_redirect_location_relative(self):
        code, h, _ = self.req("GET", "/go")
        self.assertEqual((code, h["Location"]), (302, "/login"))

    def test_put_body_forwarded(self):
        self.req("PUT", "/api/drafts/v1/x", body=b'{"a":1}', headers={"Content-Type": "application/json",
                                                                        "Origin": "https://hdp.1-2-3-4.sslip.io"})
        self.assertEqual(Upstream.seen[-1][:2], ("PUT", "/api/drafts/v1/x"))
        self.assertEqual(Upstream.seen[-1][3], b'{"a":1}')

    def test_foreign_origin_rejected(self):
        code, _, _ = self.req("POST", "/api/education/v1/x", body=b"{}", headers={"Origin": "https://evil.example"})
        self.assertEqual(code, 403)
        self.assertEqual(Upstream.seen, [])

    def test_no_login_no_platform(self):
        code, h, _ = self.req("GET", "/student", cookie=False, headers={"Accept": "text/html"})
        self.assertEqual((code, h["Location"]), (302, "/site/"))
        code, _, _ = self.req("GET", "/api/education/v1/x", cookie=False)
        self.assertEqual(code, 401)
        self.assertEqual(Upstream.seen, [])

    def test_bot_api_and_files_stay_local(self):
        code, _, body = self.req("GET", "/api/state", headers={"X-Site": "1"})
        self.assertEqual((code, json.loads(body)), (200, {"bot": True}))
        code, h, body = self.req("GET", "/_hdp/guard.js")
        self.assertEqual(code, 200)
        self.assertIn("javascript", h["Content-Type"])
        self.assertEqual(self.req("GET", "/_hdp/../web.py")[0], 404)
        self.assertEqual(Upstream.seen, [])

    def test_root_and_login_go_to_platform_home(self):
        code, h, _ = self.req("GET", "/")
        self.assertEqual((code, h["Location"]), (302, "/student"))
        code, h, _ = self.req("GET", "/site/login?c=" + web.site_code(), cookie=False)
        self.assertEqual((code, h["Location"]), (302, "/student"))

    def test_old_site_goes_to_mirror_when_logged_in(self):
        """Прежний самодельный сайт /site/ на адресе зеркала: вошёл — сразу платформа, не вошёл — карточка входа."""
        for p in ("/site", "/site/", "/site/index.html"):
            code, h, _ = self.req("GET", p)
            self.assertEqual((code, h.get("Location")), (302, "/student"), p)
        code, _, body = self.req("GET", "/site/", cookie=False)
        self.assertEqual(code, 200)
        self.assertEqual(Upstream.seen, [])

    def test_tunnel_host_is_not_mirror(self):
        code, _, _ = self.req("GET", "/student", host="abc.trycloudflare.com")
        self.assertEqual(code, 404)  # там мини-приложение, а не платформа
        self.assertEqual(Upstream.seen, [])

    def test_expired_session_relogin_retry(self):
        code, _, _ = self.req("GET", "/api/auth/v1/expired")
        self.assertEqual(code, 401)
        mirror.relogin.assert_called_once()
        self.assertEqual(len(Upstream.seen), 1)  # вход не удался — второй раз не спрашиваем

    def test_logout_only_leaves_mirror(self):
        """«Выйти» на платформе в зеркале не разлогинивает бота: запрос не уходит, выходит только cookie зеркала."""
        code, h, body = self.req("POST", "/api/auth/v1/logout", body=b"{}",
                                 headers={"Origin": "https://hdp.1-2-3-4.sslip.io", "Content-Type": "application/json"})
        self.assertEqual(code, 200)
        self.assertIn("Max-Age=0", h.get("Set-Cookie", ""))
        self.assertEqual(Upstream.seen, [])
        self.assertFalse(web.site_check(self.token))

    def test_jar_update_and_reload(self):
        j = mirror._jar
        j.update(["hdp-portal-session=NEW; Path=/; HttpOnly", "gone=; Max-Age=0"])
        self.assertIn("hdp-portal-session=NEW", j.header())
        self.assertTrue(j.has_session())

    @unittest.skipUnless(shutil.which("node"), "нужен node")
    def test_guard_keeps_hdp_brand(self):
        """На адресе зеркала платформа включала бренд «Perlem»: её проверка /horodigital.ru$/ должна быть «да»."""
        js = ("const location={host:'hdp.1-2-3-4.sslip.io',href:'https://hdp.1-2-3-4.sslip.io/'};"
              "const window=globalThis;window.location=location;globalThis.HTMLIFrameElement=function(){};"
              f"{open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'site', 'mirror', 'guard.js')).read()}"
              "console.log(/horodigital.ru$/.test(window.location.host), /horodigital.ru$/.test('evil.com'), /a/.test('a'))")
        out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.stdout.strip(), "true false true", out.stderr)

    def test_inject_without_head(self):
        out = mirror.inject("<p>x</p>", "3")
        self.assertIn("/_hdp/overlay.css?v=3", out)


if __name__ == "__main__":
    unittest.main()
