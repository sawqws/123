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
import time
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
        if self.path == "/student/assets/index-AbCd1234.js":
            Upstream.assets = getattr(Upstream, "assets", 0) + 1
            return self.answer(200, gzip.compress(b'import("./Topic-XyZ98765.js");'), "application/javascript",
                               [("Content-Encoding", "gzip")])
        if self.path == "/student/assets/Topic-XyZ98765.js":
            Upstream.chunk = getattr(Upstream, "chunk", 0) + 1
            return self.answer(200, b"export default 1", "application/javascript")
        if self.path == "/api/schools/v1/current/settings":
            Upstream.settings = getattr(Upstream, "settings", 0) + 1
            return self.answer(200, json.dumps({"n": Upstream.settings}).encode(), "application/json")
        if self.path == "/student/assets/app.js":
            return self.answer(200, b"console.log('https://horodigital.ru')", "application/javascript",
                               [("Cache-Control", "public, max-age=31536000")])
        if self.path.startswith("/store/t/"):
            Upstream.store = getattr(Upstream, "store", 0) + 1
            return self.answer(200, b"JPEGDATA", "image/jpeg", [("Cache-Control", "client,max-age=5356800")])
        if self.path == "/api/education/v1/files":
            return self.answer(200, b'{"img": "https://horodigital.ru/store/t/' + b"a" * 32 + b'"}', "application/json")
        edu = "/api/education/v1/students/current"
        if self.path == edu + "/topics/T1/tasks?taskUuid=X1":
            return self.answer(200, b'{"group": {"uuid": "G1"}, "tasks": [{"uuid": "X1", "type": "test"}]}', "application/json")
        if self.path in (edu + "/topics/T1/tasks/X1", "/api/profile/v1/users/preferences"):
            return self.answer(200, b'{"ok": 1}', "application/json")
        if self.path.startswith(edu + "/study-groups/G1/topics/T1/tasks/X1/test-attempts?"):
            return self.answer(200, b'[{"uuid": "A1"}]', "application/json")
        if self.path == edu + "/study-groups/G1/topics/T1/tasks/X1/test-attempts/A1":
            return self.answer(200, b'{"uuid": "A1", "answers": []}', "application/json")
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
            mock.patch.object(mirror, "_store", mirror.Store(os.path.join(self.tmp.name, "cache"))),
            mock.patch.object(mirror, "_swr", mirror.Swr()),
            mock.patch.object(web, "SESSIONS", os.path.join(self.tmp.name, "sessions.json")),
        ]
        for p in self.patches:
            p.start()
        Upstream.seen.clear()
        self.asked = []
        web._cfg.update(token="t", owner=lambda: 1, routes={"/api/state": lambda b: {"bot": True}},
                        version="1", site_version="1", mirror_version="7", login_ask=lambda *a: self.asked.append(a))
        web._reqs.clear(), web._req_times.clear(), web._fails.clear()
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

    def test_store_cached_and_warmed(self):
        """Картинки /store/…: из ответа API качаются заранее; браузер получает их из кэша, платформу второй раз не просят."""
        Upstream.store = 0
        self.req("GET", "/api/education/v1/files", headers={"Origin": "https://hdp.1-2-3-4.sslip.io"})
        path = "/store/t/" + "a" * 32
        for _ in range(50):
            if mirror._store.get(path):
                break
            time.sleep(0.05)
        self.assertTrue(mirror._store.get(path))  # скачано заранее, до запроса браузера
        for _ in range(2):
            code, h, body = self.req("GET", path)
            self.assertEqual((code, body, h["Content-Type"]), (200, b"JPEGDATA", "image/jpeg"))
            self.assertIn("max-age", h["Cache-Control"])
        self.assertEqual(Upstream.store, 1)  # платформу спросили один раз
        code, _, _ = self.req("GET", "/store/t/" + "b" * 32, cookie=False)
        self.assertEqual(code, 401)  # без входа — ничего

    def test_assets_cached_gzip_immutable_and_chunks_warmed(self):
        """Файлы сайта: с сервера, сжатые, с кэшем на год; соседние скрипты скачиваются заранее."""
        Upstream.assets = Upstream.chunk = 0
        for _ in range(2):
            code, h, body = self.req("GET", "/student/assets/index-AbCd1234.js")
            self.assertEqual((code, gzip.decompress(body)), (200, b'import("./Topic-XyZ98765.js");'))
            self.assertEqual(h["Content-Encoding"], "gzip")
            self.assertIn("immutable", h["Cache-Control"])
        self.assertEqual(Upstream.assets, 1)
        for _ in range(50):
            if mirror._store.get("/student/assets/Topic-XyZ98765.js"):
                break
            time.sleep(0.05)
        self.assertTrue(mirror._store.get("/student/assets/Topic-XyZ98765.js"))  # найден в скрипте и уже скачан
        self.assertEqual(self.req("GET", "/student/assets/Topic-XyZ98765.js")[2], b"export default 1")
        self.assertEqual(Upstream.chunk, 1)

    def test_settings_from_memory_then_refreshed(self):
        """Редкие ответы (настройки школы): второй раз — сразу из памяти, свежие берутся в фоне."""
        Upstream.settings = 0
        h = {"Origin": "https://hdp.1-2-3-4.sslip.io"}
        self.assertEqual(json.loads(self.req("GET", "/api/schools/v1/current/settings", headers=h)[2]), {"n": 1})
        with mock.patch.object(mirror, "SWR_FRESH", -1):
            self.assertEqual(json.loads(self.req("GET", "/api/schools/v1/current/settings", headers=h)[2]), {"n": 1})
            for _ in range(50):
                if Upstream.settings == 2:
                    break
                time.sleep(0.05)
        time.sleep(0.1)
        self.assertEqual(json.loads(self.req("GET", "/api/schools/v1/current/settings", headers=h)[2]), {"n": 2})
        self.req("PUT", "/api/drafts/v1/x", body=b"{}", headers={"Content-Type": "application/json", **h})
        self.assertTrue(mirror._swr.mem)  # черновик поля ответа — служебное, память не трогаем
        self.req("POST", "/api/education/v1/attempts", body=b"{}", headers={"Content-Type": "application/json", **h})
        self.assertEqual(mirror._swr.mem, {})  # сдал/начал на платформе — память сброшена
        code, _, _ = self.req("GET", "/api/education/v1/x")
        code2, _, _ = self.req("GET", "/api/education/v1/x")
        self.assertEqual(sum(1 for m, pth, *_ in Upstream.seen if pth == "/api/education/v1/x"), 2)  # данные — всегда свежие

    def test_plain_switch_off_and_on(self):
        """«⏻ Выключить всё»: cookie hdp_plain — страница платформы без кнопок бота (guard.js остаётся); /_hdp/on — назад."""
        code, h, _ = self.req("GET", "/_hdp/off?to=/student/topic/x")
        self.assertEqual((code, h["Location"]), (302, "/student/topic/x"))
        self.assertIn("hdp_plain=1", h["Set-Cookie"])
        self.assertNotIn("Max-Age=0", h["Set-Cookie"])
        _, _, body = self.req("GET", "/student", cookie=False,
                             headers={"Accept": "text/html", "Cookie": f"hdp_site={self.token}; hdp_plain=1"})
        html = body.decode()
        self.assertIn("/_hdp/guard.js", html)
        self.assertNotIn("overlay.js", html)
        self.assertNotIn("overlay.css", html)
        code, h, _ = self.req("GET", "/_hdp/on?to=//evil.example")
        self.assertEqual((code, h["Location"]), (302, "/student"))  # чужой адрес — нет
        self.assertIn("Max-Age=0", h["Set-Cookie"])

    def test_json_gzip_rewritten(self):
        code, h, body = self.req("GET", "/api/education/v1/x", headers={"Origin": "https://hdp.1-2-3-4.sslip.io"})
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body), {"url": "/store/f.png", "u2": "/store/g.png"})
        self.assertNotIn("Content-Encoding", h)
        self.assertEqual(Upstream.seen[-1][2]["Origin"], "https://horodigital.ru")

    def test_assets_streamed_as_is(self):
        code, h, body = self.req("GET", "/student/assets/app.js")
        self.assertEqual((code, body), (200, b"console.log('https://horodigital.ru')"))
        self.assertEqual(h["Cache-Control"], "public, max-age=31536000, immutable")  # имя с хэшем — не меняется

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

    def post(self, path, body, origin=None):
        return self.req("POST", path, cookie=False, body=json.dumps(body).encode(),
                        headers={"Content-Type": "application/json", **({"Origin": origin} if origin else {})})

    def test_login_by_pin_on_site(self):
        """Вход прямо в Safari кодом из 6 цифр (/site): cookie на полгода, код срабатывает один раз."""
        pin = web.site_code(pin=True)
        self.assertRegex(pin, r"^\d{6}$")
        code, h, body = self.post("/site/api/login/code", {"code": pin[:3] + " " + pin[3:]})
        self.assertEqual((code, json.loads(body)["home"]), (200, "/student"))
        cookie = h["Set-Cookie"]
        self.assertIn(f"Max-Age={180 * 86400}", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertTrue(web.site_check(cookie.split(";")[0].split("=", 1)[1]))
        self.assertEqual(self.post("/site/api/login/code", {"code": pin})[0], 400)  # второй раз — нет
        self.assertEqual(Upstream.seen, [])

    def test_pin_bruteforce_locked(self):
        for _ in range(10):
            self.post("/site/api/login/code", {"code": "000000"})
        pin = web.site_code(pin=True)
        code, h, _ = self.post("/site/api/login/code", {"code": pin})
        self.assertEqual(code, 429)  # даже верный код — после 10 неверных пауза
        self.assertNotIn("Set-Cookie", h)

    def test_login_via_telegram_button(self):
        """«Войти через Telegram»: бот пишет «✅ Войти», страница ждёт; после нажатия — cookie один раз."""
        code, _, body = self.post("/site/api/login/request", {})
        req = json.loads(body)["req"]
        short, ua, pin = self.asked[-1]
        self.assertRegex(pin, r"^\d{6}$")
        code, h, body = self.post("/site/api/login/poll", {"req": req})
        self.assertEqual((code, json.loads(body)), (200, {"wait": True}))
        self.assertNotIn("Set-Cookie", h)
        self.assertIsNone(web.site_approve("чужой"))
        self.assertTrue(web.site_approve(short))
        code, h, body = self.post("/site/api/login/poll", {"req": req})
        self.assertEqual(code, 200)
        self.assertTrue(web.site_check(h["Set-Cookie"].split(";")[0].split("=", 1)[1]))
        self.assertEqual(self.post("/site/api/login/poll", {"req": req})[0], 410)  # второй раз — нет
        self.assertEqual(self.post("/site/api/login/poll", {"req": "выдумка"})[0], 410)

    def test_login_request_limits(self):
        self.assertEqual(self.post("/site/api/login/request", {}, origin="https://evil.example")[0], 403)
        for _ in range(5):
            self.assertEqual(self.post("/site/api/login/request", {})[0], 200)
        self.assertEqual(self.post("/site/api/login/request", {})[0], 429)  # чат не засыпать
        self.assertEqual(len(self.asked), 5)

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

    def test_task_page_chain_fetched_ahead(self):
        """Страница задания: сервер сам берёт цепочку (задание, попытки, попытка) — браузер получает её из памяти."""
        edu = "/api/education/v1/students/current"
        with mock.patch.object(mirror, "_ahead", mirror.Ahead()):
            code, _, body = self.req("GET", edu + "/topics/T1/tasks?taskUuid=X1", headers={"X-Site": "1"})
            self.assertEqual(code, 200)
            att = edu + "/study-groups/G1/topics/T1/tasks/X1/test-attempts"
            chain = [edu + "/topics/T1/tasks/X1", att + "?status=completed",
                     att + "?status=started&status=created&view=full", att + "/A1"]
            for _ in range(100):  # цепочка качается в фоне
                if all(any(p == c for _, p, _, _ in Upstream.seen) for c in chain):
                    break
                time.sleep(0.05)
            before = len(Upstream.seen)
            for c in chain:
                code, _, body = self.req("GET", c, headers={"X-Site": "1"})
                self.assertEqual(code, 200, c)
            self.assertEqual(len(Upstream.seen), before)  # всё — из памяти, к платформе не ходили
            self.assertEqual(json.loads(body)["uuid"], "A1")
            # второй раз — уже с платформы (отдаётся один раз), а действие сбрасывает загруженное наперёд
            self.req("GET", chain[0], headers={"X-Site": "1"})
            self.assertEqual(Upstream.seen[-1][1], chain[0])
            mirror._ahead.want(["/api/profile/v1/users/preferences"])
            time.sleep(0.3)
            mirror._swr.clear()
            self.assertIsNone(mirror._ahead.take("/api/profile/v1/users/preferences"))

    def test_ahead_on_touch_needs_login(self):
        """Касание карточки (/_hdp/ahead) запускает цепочку задания — только со входом и с uuid-подобными параметрами."""
        with mock.patch.object(mirror, "_ahead", mirror.Ahead()):
            code, _, _ = self.req("GET", "/_hdp/ahead?t=T1xxxxxxx&x=X1xxxxxxx", cookie=False)
            self.assertEqual(code, 204)
            code, _, _ = self.req("GET", "/_hdp/ahead?t=../../x&x=X1xxxxxxx")
            self.assertEqual(code, 204)
            time.sleep(0.2)
            self.assertFalse([p for _, p, _, _ in Upstream.seen if "taskUuid" in p])
            self.req("GET", "/_hdp/ahead?t=T1xxxxxxx&x=X1xxxxxxx")
            for _ in range(60):
                if any("taskUuid=X1xxxxxxx" in p for _, p, _, _ in Upstream.seen):
                    break
                time.sleep(0.05)
            self.assertTrue(any(p.endswith("/topics/T1xxxxxxx/tasks?taskUuid=X1xxxxxxx") for _, p, _, _ in Upstream.seen))

    def test_bundle_for_service_worker(self):
        """sw.js: вся цепочка задания одним ответом (без первого звена — его браузер берёт сам); sw.js — на весь сайт."""
        edu = "/api/education/v1/students/current"
        with mock.patch.object(mirror, "_ahead", mirror.Ahead()):
            code, _, _ = self.req("GET", "/_hdp/bundle?t=T1xxxxxxx&x=X1", cookie=False)
            self.assertEqual(code, 403)
            code, _, body = self.req("GET", "/_hdp/bundle?t=T1&x=X1")
            self.assertEqual(code, 403)  # короткие/чужие параметры
        Upstream.seen.clear()
        with mock.patch.object(mirror, "_ahead", mirror.Ahead()), \
                mock.patch.object(mirror, "TASKS_OF", mirror.TASKS_OF):
            out = mirror._ahead.bundle("T1", "X1")
        att = edu + "/study-groups/G1/topics/T1/tasks/X1/test-attempts"
        self.assertEqual(set(out), {edu + "/topics/T1/tasks/X1", "/api/profile/v1/users/preferences",
                                    att + "?status=completed", att + "?status=started&status=created&view=full",
                                    att + "/A1"})
        self.assertEqual(out[att + "/A1"][0], 200)
        self.assertIn('"A1"', out[att + "/A1"][2])
        code, h, body = self.req("GET", "/_hdp/sw.js", cookie=False)
        self.assertEqual((code, h.get("Service-Worker-Allowed"), h.get("Cache-Control")), (200, "/", "no-cache"))
        self.assertIn(b"/_hdp/bundle", body)

    def test_i18n_cached_like_assets(self):
        self.assertTrue(mirror.ASSET.fullmatch("/student/i18n/ru/taskPage.json?v=prod-react@ddf9ecfc"))
        self.assertFalse(mirror.ASSET.fullmatch("/student/i18n/ru/taskPage.json"))  # без версии — не навсегда

    def test_inject_without_head(self):
        out = mirror.inject("<p>x</p>", "3")
        self.assertIn("/_hdp/overlay.css?v=3", out)


if __name__ == "__main__":
    unittest.main()
