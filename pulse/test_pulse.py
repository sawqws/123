#!/usr/bin/env python3
"""Тесты «Пульса»: разбор журнала сессии (reporter.py), приём сводок, уведомления, окно контекста, карточки."""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("PULSE_TOKEN", "1:test")
os.environ["PULSE_DATA"] = tempfile.mkdtemp()
import pulse  # noqa: E402
import reporter  # noqa: E402

T0 = "2026-10-15T10:00:00.000Z"


def line(**d):
    d.setdefault("timestamp", T0)
    return json.dumps(d, ensure_ascii=False)


def assistant(content, stop="tool_use", tokens=1000, **kw):
    return line(type="assistant", message={"model": "claude-opus-5-5", "content": content, "stop_reason": stop,
                                           "usage": {"input_tokens": 2, "cache_read_input_tokens": tokens,
                                                     "cache_creation_input_tokens": 0, "output_tokens": 10}}, **kw)


def result(tid, text="ok", res=None):
    return line(type="user", message={"content": [{"type": "tool_result", "tool_use_id": tid, "content": text}]},
                toolUseResult=res)


def use(tid, name, inp):
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


JOURNAL = [
    line(type="attachment", attachment={"type": "remote_session_change", "url": "https://claude.ai/code/session_X"}),
    line(type="user", message={"content": "Сделай бота"}, promptId="p1"),
    line(type="ai-title", aiTitle="Бот мониторинга"),
    assistant([use("t1", "TaskCreate", {"subject": "Репортёр"})]),
    result("t1", "Task #1 created", {"task": {"id": "1", "subject": "Репортёр"}}),
    assistant([use("t2", "TaskCreate", {"subject": "Бот"})]),
    result("t2", "Task #2 created", {"task": {"id": "2", "subject": "Бот"}}),
    assistant([use("t3", "TaskUpdate", {"taskId": "1", "status": "completed"})]),
    result("t3", "Updated", {"taskId": "1", "statusChange": {"from": "pending", "to": "completed"}}),
    assistant([use("t4", "Edit", {"file_path": "/x/pulse.py"})]),
    result("t4"),
    assistant([use("t5", "Bash", {"command": "git commit -m x", "description": "Коммит"})], tokens=50_000),
    result("t5", "PR https://github.com/sawqws/123/pull/170 created"),
]


class Reporter(unittest.TestCase):
    def parse(self, lines):
        s = reporter.Session()
        for x in lines:
            s.feed(x)
        return s.summary()

    def test_working_with_tasks(self):
        r = self.parse(JOURNAL)
        self.assertEqual(r["title"], "Бот мониторинга")
        self.assertEqual(r["url"], "https://claude.ai/code/session_X")
        self.assertEqual(r["status"], "working")
        self.assertEqual([(t["id"], t["status"]) for t in r["tasks"]], [("1", "completed"), ("2", "pending")])
        self.assertEqual(r["ctx"]["tokens"], 50_012)
        self.assertEqual(r["turn"]["tools"], 5)
        self.assertEqual(r["turn"]["edits"], ["pulse.py"])
        self.assertEqual(r["turn"]["commits"], 1)
        self.assertEqual(r["turn"]["prs"], ["https://github.com/sawqws/123/pull/170"])
        self.assertEqual(r["prompt"]["text"], "Сделай бота")

    def test_done_and_question(self):
        r = self.parse(JOURNAL + [assistant([{"type": "text", "text": "Готово: бот работает"}], stop="end_turn")])
        self.assertEqual(r["status"], "waiting")
        self.assertIn("Готово", r["reply"])
        q = self.parse(JOURNAL + [assistant([use("q", "AskUserQuestion", {"questions": [{"question": "Какой цвет?"}]})])])
        self.assertEqual(q["status"], "question")
        self.assertEqual(q["question"], "Какой цвет?")

    def test_new_prompt_resets_turn_but_keeps_tasks(self):
        r = self.parse(JOURNAL + [assistant([{"type": "text", "text": "ok"}], stop="end_turn"),
                                  line(type="user", message={"content": "Ещё кнопку"})])
        self.assertEqual(r["status"], "working")
        self.assertEqual(r["turn"]["tools"], 0)
        self.assertEqual(r["nprompts"], 2)
        self.assertEqual(len(r["tasks"]), 2)

    def test_meta_and_system_text_are_not_prompts(self):
        r = self.parse([line(type="user", message={"content": "<system-reminder>x</system-reminder>"}),
                        line(type="user", isMeta=True, message={"content": "meta"})])
        self.assertEqual(r["nprompts"], 0)
        self.assertEqual(r["status"], "waiting")

    def test_compaction_and_todowrite(self):
        r = self.parse([line(type="user", message={"content": "x"}),
                        assistant([use("a", "TodoWrite", {})], tokens=170_000),
                        result("a", "ok", {"newTodos": [{"content": "A", "status": "completed"},
                                                        {"content": "B", "status": "in_progress"}]}),
                        line(type="system", subtype="compact_boundary", compactMetadata={"preTokens": 160_000})])
        self.assertEqual(r["ctx"], {"tokens": 0, "compacts": 1, "pre": 160_000})
        self.assertEqual([t["status"] for t in r["tasks"]], ["completed", "in_progress"])

    def test_garbage_lines(self):
        r = self.parse(["", "not json", "[1,2]", line(type="assistant", message=None)])
        self.assertEqual(r["status"], "waiting")

    def test_progress_of_current_prompt_and_agents(self):
        r = self.parse(JOURNAL + [assistant([{"type": "text", "text": "ok"}], stop="end_turn"),
                                  line(type="user", message={"content": "Ещё"}),
                                  assistant([use("a1", "Agent", {"description": "x", "run_in_background": True})]),
                                  result("a1", "Async agent launched", {"isAsync": True, "agentId": "ag1"}),
                                  assistant([{"type": "text", "text": "жду"}], stop="end_turn")])
        self.assertEqual(r["prog"], {"done": 0, "total": 1, "pct": 0})  # задача 1 сделана до запроса — не в счёт
        self.assertEqual(r["agents"], 1)
        self.assertEqual(r["status"], "working")  # фоновый агент ещё работает
        r = self.parse(JOURNAL + [line(type="attachment", attachment={
            "type": "queued_command", "prompt": "<task-notification><task-id>ag1</task-id><status>completed</status>"})])
        self.assertEqual(r["agents"], 0)

    def test_remote_id(self):
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_REMOTE_SESSION_ID": "cse_018Abc"}):
            self.assertEqual(reporter.remote_id(), "session_018Abc")


def summary(status="working", tokens=10_000, **kw):
    s = reporter.Session()
    for x in JOURNAL:
        s.feed(x)
    body = {"sid": "local", "rid": "session_A", **s.summary()}
    body.update({"status": status, "ctx": {"tokens": tokens, "compacts": 0, "pre": 0}, **kw})
    return body


class Bot(unittest.TestCase):
    def setUp(self):
        pulse.st.clear()
        pulse.st["owner"] = 42
        pulse.st["alerts"] = {k: True for k in pulse.ALERTS}  # в тестах уведомлений — включены
        self.sent = []
        self.p = mock.patch.object(pulse, "tg", side_effect=lambda m, **k: self.sent.append((m, k)) or
                                   {"ok": True, "result": {"message_id": 7}})
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def test_events(self):
        self.assertEqual([e[0] for e in pulse.ingest(summary())], ["new"])
        self.assertEqual(pulse.ingest(summary()), [])
        self.assertEqual([e[0] for e in pulse.ingest(summary("question", question="Да?"))], ["question"])
        self.assertEqual([e[0] for e in pulse.ingest(summary("waiting"))], ["done"])
        ev = pulse.ingest(summary("working", tokens=130_000))
        self.assertEqual([(e[0], e[2]) for e in ev], [("ctx", 60)])
        self.assertEqual(pulse.ingest(summary("working", tokens=131_000)), [])  # тот же порог второй раз не пишем

    def test_notify_texts(self):
        pulse.ingest(summary())
        pulse.notify(pulse.ingest(summary("waiting")))
        m, k = self.sent[-1]
        self.assertEqual(m, "sendMessage")
        self.assertIn("готово", k["text"])
        self.assertEqual(k["reply_markup"]["inline_keyboard"][0][0]["url"], "https://claude.ai/code/session_X")
        pulse.st["alerts"] = {"done": False}
        n = len(self.sent)
        pulse.ingest(summary("working"))
        pulse.notify(pulse.ingest(summary("waiting")))
        self.assertEqual(len(self.sent), n)  # выключено — молчим

    def test_done_message_is_clean_html(self):
        pulse.ingest(summary())
        reply = "Сделал **бота**.\n\n- пункт\n\n**Для тебя:** нажми /start."
        pulse.notify(pulse.ingest(summary("waiting", reply=reply)))
        text = self.sent[-1][1]["text"]
        self.assertNotIn("**", text)
        self.assertIn("<b>бота</b>", text)
        self.assertIn("👉 нажми /start.", text)
        self.assertIn("— готово", text)
        self.assertEqual(pulse.split_reply("a\n\n**Для тебя:** b"), ("a", "b"))
        self.assertEqual(pulse.split_reply("просто ответ"), ("просто ответ", ""))

    def test_start_shows_card_not_mode_question(self):
        pulse.ingest(summary())
        pulse.on_message({"chat": {"id": 42, "type": "private"}, "from": {"id": 42}, "text": "/start"})
        self.assertIn("📡 Пульс", self.sent[-1][1]["text"])
        self.assertIn("Память █░░░░░░░░░░░ 5%", self.sent[-1][1]["text"])

    def test_limits(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        now = datetime(2026, 10, 16, 8, 15, tzinfo=ZoneInfo("UTC")).timestamp()
        out = ("Current session: 23% used · resets Oct 16, 11:10am (UTC)\n"
               "Current week (all models): 20% used · resets Oct 21, 11pm (UTC)\n"
               "Current week (Fable): 0% used · resets Oct 21, 11pm (UTC)")
        got = pulse.parse_usage(out, now)
        self.assertEqual(got["session"], {"pct": 23, "reset": 1792149000.0})
        self.assertEqual(got["week"]["reset"], 1792623600.0)
        pulse.st["limits"] = {**got, "at": now}
        text = pulse.limits_text(now)
        self.assertIn("Лимит 5 ч ███░░░░░░░░░ 23% · <i>сброс в 14:10</i>", text)
        self.assertIn("Неделя ██░░░░░░░░░░ 20% · <i>сброс чт в 02:00</i>", text)
        self.assertNotIn("Fable", text)  # 0% — не показываем
        self.assertEqual(pulse.limits_text(now + 3600), "")  # устарело — не показываем
        self.assertEqual(pulse.parse_usage("ошибка"), {})

    def test_quiet_by_default(self):
        pulse.st.pop("alerts")
        self.assertFalse(any(pulse.alerts().values()))
        pulse.ingest(summary())
        pulse.notify(pulse.ingest(summary("waiting")) + pulse.ingest(summary("question", question="?")))
        self.assertEqual([m for m, _ in self.sent if m == "sendMessage"], [])

    def test_window_learning(self):
        w, sure = pulse.window("claude-opus-5-5", 100_000)
        self.assertEqual((w, sure), (200_000, False))
        self.assertEqual(pulse.window("claude-opus-5-5", 180_000)[0], 1_000_000)  # за порогом сжатия — окно больше
        pulse.ingest(summary(tokens=150_000))
        pulse.ingest(summary(ctx={"tokens": 0, "compacts": 1, "pre": 640_000}))
        self.assertEqual(pulse.window("claude-opus-5-5"), (800_000, True))

    def test_offline_and_hidden(self):
        pulse.ingest(summary(), now=time.time() - 600)
        v = pulse.visible()[0]
        self.assertEqual(v["status"], "offline")
        pulse.sessions()["session_A"]["hidden"] = time.time()
        self.assertEqual(pulse.visible(), [])
        self.assertEqual(pulse.hidden_count(), 1)
        pulse.ingest(summary(updated=time.time() + 5))  # новая активность — снова видно
        self.assertEqual(len(pulse.visible()), 1)

    def test_bad_id(self):
        with self.assertRaises(ValueError):
            pulse.ingest({"rid": "../etc"})

    def test_home_and_detail(self):
        self.assertIn("чатов нет", pulse.home_text())
        pulse.ingest(summary(tokens=150_000, prog={"done": 2, "total": 5, "pct": 40}, agents=2))
        text = pulse.home_text()
        self.assertIn("· работает · 🤖 2 помощника", text)
        self.assertIn("Сделано █████░░░░░░░ 40%", text)
        self.assertIn("Память █████████░░░ 75% ⚠️ <i>открой новый чат</i>", text)
        d = pulse.detail_text(pulse.visible()[0])
        self.assertIn("40% <i>(2 из 5)</i>", d)
        self.assertIn("✅ Репортёр", d)
        self.assertLessEqual(len(d), 4000)
        rows = pulse.home_buttons()
        self.assertTrue(all(len(b["text"]) <= 24 for r in rows for b in r))

    def test_no_warning_below_60_and_sleeping_done_chat(self):
        pulse.ingest(summary("waiting", tokens=100_000), now=time.time() - 3600)
        v = pulse.visible()[0]
        self.assertEqual(v["status"], "waiting")  # контейнер уснул, но чат закончил — «готово», а не «нет связи»
        self.assertNotIn("⚠️", pulse.block(v))
        self.assertIn("· готово", pulse.block(v))

    def test_callbacks(self):
        pulse.ingest(summary())
        cq = {"id": "1", "from": {"id": 42}, "message": {"chat": {"id": 42}, "message_id": 5}}
        for data in ("home", "s|session_A", "al", "at|done", "more", "h|session_A", "unhide", "zzz"):
            pulse.on_callback({**cq, "data": data})
        self.assertFalse(pulse.alerts()["done"])
        self.assertNotIn("hidden", pulse.sessions()["session_A"])
        self.sent.clear()
        pulse.on_callback({**cq, "from": {"id": 1}, "data": "home"})
        self.assertIn("личный", self.sent[-1][1]["text"])

    def test_start_owner_and_stranger(self):
        pulse.st.clear()
        pulse.on_message({"chat": {"id": 5, "type": "private"}, "from": {"id": 5}, "text": "/start"})
        self.assertEqual(pulse.st["owner"], 5)
        pulse.on_message({"chat": {"id": 6, "type": "private"}, "from": {"id": 6}, "text": "/start"})
        self.assertIn("личный", self.sent[-1][1]["text"])

    def test_api(self):
        pulse.ingest(summary())
        self.assertEqual(pulse.api_post("/api/alerts", {"key": "offline", "on": True})["alerts"]["offline"], True)
        pulse.api_post("/api/hide", {"id": "session_A"})
        self.assertEqual(pulse.app_state()["hidden"], 1)
        pulse.api_post("/api/unhide_all", {})
        self.assertEqual(len(pulse.app_state()["sessions"]), 1)
        with self.assertRaises(KeyError):
            pulse.api_post("/api/nope", {})

    def test_live_card(self):
        pulse.ingest(summary())
        pulse.home(42)
        self.assertEqual(pulse.st["live"]["42"], 7)
        self.sent.clear()
        pulse.refresh_live(force=True)
        self.assertEqual(self.sent[-1][0], "editMessageText")
        self.sent.clear()
        pulse.refresh_live(force=False)  # ничего не изменилось — не правим
        self.assertEqual(self.sent, [])


class Http(unittest.TestCase):
    def test_ingest_server_and_webapp_auth(self):
        from http.server import ThreadingHTTPServer
        pulse.st.clear()
        ing = ThreadingHTTPServer(("127.0.0.1", 0), pulse.Ingest)
        web = ThreadingHTTPServer(("127.0.0.1", 0), pulse.Web)
        for s in (ing, web):
            threading.Thread(target=s.serve_forever, daemon=True).start()
        with mock.patch.object(pulse, "notify"), mock.patch.object(pulse, "refresh_live"):
            req = urllib.request.Request(f"http://127.0.0.1:{ing.server_port}/", data=json.dumps(summary()).encode(),
                                         method="POST")
            self.assertTrue(json.load(urllib.request.urlopen(req))["ok"])
        self.assertIn("session_A", pulse.sessions())
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(f"http://127.0.0.1:{web.server_port}/api/state")
        self.assertEqual(e.exception.code, 401)
        self.assertEqual(urllib.request.urlopen(f"http://127.0.0.1:{web.server_port}/health").read(), b"ok")
        ing.shutdown()
        web.shutdown()


if __name__ == "__main__":
    unittest.main()
