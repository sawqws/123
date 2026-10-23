"""🪞 Дозор зеркала (bot/mirrorwatch.py): python3 bot/test_mirrorwatch.py

Зеркало заменяет локальный HTTP-сервер, платформу — подменённый mirror.get_json: сеть не нужна."""
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mirrorwatch as mw  # noqa: E402

TOPIC = {"uuid": "t1", "title": "А3. Дроби ", "studyPeriod": {"deadlineDate": "2026-11-09", "extraDeadlineDays": 7},
         "achievedLevel": {"totalLevel": 1},
         "tasks": [{"uuid": "a", "progress": {"status": {"type": "approved"}}},
                   {"uuid": "b", "progress": {"status": {"type": "appointed"}}}]}
PAGE = ('<html><head><script src="/_hdp/guard.js?v=1"></script><link rel="stylesheet" href="/_hdp/overlay.css?v=1">'
        '<script defer src="/_hdp/overlay.js?v=1"></script></head><body></body></html>')


def topics(**kw):
    t = json.loads(json.dumps(TOPIC))
    t.update(kw)
    return {"success": True, "data": [t]}


class Fake(BaseHTTPRequestHandler):
    routes = {}
    seen = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        Fake.seen.append((self.path, self.headers.get("Cookie"), self.headers.get("X-Site")))
        r = Fake.routes.get(self.path.split("?")[0], (404, "text/plain", "nope"))
        if callable(r):
            r = r()
        code, ctype, body = r
        body = body if isinstance(body, bytes) else (json.dumps(body) if not isinstance(body, str) else body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if code in (301, 302):
            self.send_header("Location", "/site/login")
        self.end_headers()
        self.wfile.write(body)


class MirrorWatchTest(unittest.TestCase):
    def setUp(self):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_port}"
        self.dir = tempfile.TemporaryDirectory()
        self.platform = {mw.EDU + "/disciplines": {"data": [{"disciplineId": 1}]}, mw.TOPICS: topics()}
        self.logins, self.logouts = [], []
        Fake.seen = []
        Fake.routes = {"/health": (200, "application/json", {"ok": True}),
                       "/api/state": (200, "application/json", {"ok": True}),
                       mw.EDU + "/disciplines": (200, "application/json", {"data": [{"disciplineId": 1}]}),
                       mw.EDU + "/topics": (200, "application/json", topics()),
                       "/student/": (200, "text/html", PAGE),
                       **{f"/_hdp/{f}": (200, "text/plain", "x" * 500) for f in ("overlay.js", "overlay.css", "guard.js")}}
        self.p = [mock.patch.object(mw, "LAST", os.path.join(self.dir.name, "mw", "last.json")),
                  mock.patch.object(mw, "AGAIN", 0),
                  mock.patch.object(mw.web, "site_code", lambda pin=False: "code"),
                  mock.patch.object(mw.web, "site_login", lambda c: self.logins.append(c) or "tok"),
                  mock.patch.object(mw.web, "site_logout", lambda t: self.logouts.append(t)),
                  mock.patch.object(mw.mirror, "get_json", lambda p: self.platform.get(p))]
        for x in self.p:
            x.start()

    def tearDown(self):
        for x in self.p:
            x.stop()
        self.srv.shutdown()
        self.srv.server_close()
        self.dir.cleanup()

    def test_all_good_logs_in_and_out(self):
        rep = mw.check(self.base + "/")
        self.assertTrue(rep["ok"], rep["issues"])
        self.assertEqual([c["name"] for c in rep["checks"]],
                         ["health", "вход", "предметы", "темы", "страница", "overlay.js", "overlay.css", "guard.js", "данные"])
        self.assertEqual(self.logouts, ["tok"])  # вход только на время проверки
        self.assertTrue(all(ck == "hdp_site=tok" for p, ck, _ in Fake.seen))
        self.assertIn(("/api/state", "hdp_site=tok", "1"), Fake.seen)  # кнопкам бота нужен X-Site
        with open(mw.LAST) as f:
            self.assertTrue(json.load(f)["ok"])

    def test_page_without_bot_buttons(self):
        Fake.routes["/student/"] = (200, "text/html", "<html><head></head></html>")
        rep = mw.check(self.base)
        self.assertEqual([i["text"] for i in rep["issues"]],
                         ["зеркало: страница /student/ открылась без кнопок бота (overlay.js/guard.js)"])

    def test_login_broken_and_redirect(self):
        Fake.routes["/api/state"] = (401, "application/json", {"error": "Нужен вход"})
        Fake.routes["/student/"] = (302, "text/html", "")
        texts = [i["text"] for i in mw.check(self.base)["issues"]]
        self.assertIn("зеркало: вход по cookie не работает: /api/state — ответ 401", texts)
        self.assertIn("зеркало: страница /student/ — ответ 302 → /site/login", texts)
        self.assertEqual(self.logouts, ["tok"])

    def test_api_error_is_ours_only_when_platform_answers(self):
        Fake.routes[mw.EDU + "/disciplines"] = (502, "text/html", "Bad Gateway")
        self.assertIn("зеркало: API предметы через зеркало — ответ 502", [i["text"] for i in mw.check(self.base)["issues"]])
        self.platform[mw.EDU + "/disciplines"] = None  # сама платформа лежит — не наша ошибка
        self.assertFalse([i for i in mw.check(self.base)["issues"] if "предметы" in i["text"]])

    def test_data_mismatch_rechecked_then_reported(self):
        Fake.routes[mw.EDU + "/topics"] = (200, "application/json", topics(studyPeriod={"deadlineDate": "2026-11-02", "extraDeadlineDays": 7}))
        rep = mw.check(self.base)
        self.assertEqual(rep["issues"][0]["text"], "зеркало: данные расходятся с платформой")
        self.assertIn("срок 2026-11-02+7, а на платформе 2026-11-09+7", rep["issues"][0]["detail"])
        self.assertEqual(sum(p.startswith(mw.EDU + "/topics") for p, _, _ in Fake.seen), 3)  # проверка API + 2 сверки

    def test_stale_memory_recovers_on_second_look(self):
        """Память зеркала на шаг позади (обновляется в фоне) — второй взгляд через AGAIN с совпадает: не ошибка."""
        old = topics()
        old["data"][0]["tasks"][1]["progress"]["status"]["type"] = "reviewing"
        answers = iter([old, old, topics()])
        Fake.routes[mw.EDU + "/topics"] = lambda: (200, "application/json", next(answers))
        self.assertTrue(mw.check(self.base)["ok"])

    def test_diff_lines(self):
        a = mw.topics_sig(topics())
        b = mw.topics_sig({"data": [dict(TOPIC, achievedLevel={"totalLevel": 2})]})
        self.assertEqual(mw.diff(a, b), ["«А3. Дроби»: уровень 1, а на платформе 2"])
        c = json.loads(json.dumps(TOPIC))
        c["tasks"][0]["progress"]["status"]["type"] = "reworking"
        self.assertEqual(mw.diff(a, mw.topics_sig([c])), ["«А3. Дроби»: статусов не так 1 (у a: approved, на платформе reworking)"])
        self.assertEqual(mw.diff(a, {}), ["лишняя тема «А3. Дроби»"])
        self.assertEqual(mw.diff({}, a), ["нет темы «А3. Дроби»"])

    def test_slow_answer(self):
        with mock.patch.object(mw, "SLOW", -1):
            self.assertTrue(any("отвечает долго" in i["text"] for i in mw.check(self.base)["issues"]))

    def test_health_down_stops_early(self):
        Fake.routes["/health"] = (503, "text/plain", "down")
        rep = mw.check(self.base)
        self.assertEqual([i["text"] for i in rep["issues"]], ["зеркало: /health — ответ 503"])
        self.assertEqual(self.logouts, ["tok"])


if __name__ == "__main__":
    unittest.main()
