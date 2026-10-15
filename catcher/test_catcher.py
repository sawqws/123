"""Тесты Ловца без сети и без Секретаря: python3 catcher/test_catcher.py"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import catcher as app  # noqa: E402
import botkit as kit  # noqa: E402


class FakeAgent:
    """Секретарь понарошку: статусы имён в self.names (free|taken|fragment|invalid|flood), записывает запросы."""

    def __init__(self, names=None, down=False, logged=True):
        self.names, self.down, self.logged = dict(names or {}), down, logged
        self.calls, self.next_id = [], 500
        self.channels = []

    def call(self, cmd, args=None, timeout=60):
        args = args or {}
        self.calls.append((cmd, args))
        if self.down:
            return False, "Секретарь не запущен"
        if cmd == "_status":
            return True, {"account": {"name": "Глеб", "username": "gleb"} if self.logged else None}
        if not self.logged:
            return False, "аккаунт не подключён"
        if cmd == "username":
            st = self.names.get(args["username"], "taken")
            return True, {"username": args["username"], "status": st, **({"wait": 300} if st == "flood" else {})}
        if cmd == "reserve":
            self.next_id += 1
            self.channels.append({"id": -1000000000000 - self.next_id, "title": args.get("title") or "Ловец", "username": ""})
            return True, {"id": self.channels[-1]["id"], "title": self.channels[-1]["title"]}
        if cmd == "mychannels":
            return True, list(self.channels)
        if cmd == "grab":
            u = args["username"]
            created = not args.get("channel")
            if created:
                self.next_id += 1
                cid = -1000000000000 - self.next_id
                self.channels.append({"id": cid, "title": args.get("title") or u, "username": ""})
            else:
                cid = args["channel"]
            st = self.names.get(u, "taken")
            out = {"username": u, "channel": cid, "title": args.get("title") or u, "created": created}
            if st == "free":
                self.names[u] = "taken"
                for ch in self.channels:
                    if ch["id"] == cid:
                        ch["username"] = u
                return True, {**out, "status": "ok", "link": f"https://t.me/{u}"}
            return True, {**out, "status": st}
        return False, f"нет команды {cmd}"


class Base(unittest.TestCase):
    def setUp(self):
        self.t = app.bot.testing()
        self.agent = FakeAgent({"freebie": "free", "busyname": "taken", "goldie": "fragment"})
        app.agent = self.agent
        app.watcher = app.Watcher()
        p = mock.patch.object(kit, "claude", side_effect=kit.Fail("нет"))
        p.start()
        self.addCleanup(p.stop)

    def texts(self):
        return [m.get("text") or "" for m in self.t.messages]


class Chat(Base):
    def test_start(self):
        self.t.send("/start")
        self.assertIn("Catcher", self.t.last)
        self.assertIn("Аккаунт: Глеб", self.t.last)
        for btn in ("🎲 По пожеланиям", "📋 Список", "🛠 Поддержка"):
            self.assertIn(btn, self.t.buttons)

    def test_stranger_locked(self):
        self.t.send("/start", uid=999)
        self.assertIn("личный", self.t.last)
        self.assertEqual(self.t.api("state", uid=999)[0], 403)

    def test_add_free_grabs_now(self):
        self.t.send("@freebie")
        self.assertIn("занял", "".join(self.texts()))
        self.assertIn("https://t.me/freebie", self.t.last)
        n = app.find("freebie")
        self.assertEqual(n["status"], "caught")
        # сначала создан запасной канал, потом имя поставлено на него
        self.assertEqual([c for c, _ in self.agent.calls], ["username", "reserve", "grab"])
        self.assertEqual(self.agent.calls[-1][1]["channel"], self.agent.channels[0]["id"])
        self.assertIsNone(app.bot.db.get("reserve"))  # запас потрачен

    def test_add_taken_watches(self):
        self.t.send("busyname t.me/another_one, @Third_Name")
        self.assertEqual({n["name"] for n in app.names()}, {"busyname", "another_one", "third_name"})
        self.assertTrue(all(n["status"] == "watch" for n in app.names()))
        self.assertIn("слежу", self.t.last)

    def test_fragment_not_watched(self):
        self.t.send("@goldie")
        self.assertEqual(app.find("goldie")["status"], "fragment")
        self.assertIn("Fragment", self.t.last)

    def test_bad_text_is_not_a_name(self):
        self.t.send("@abc")  # короче 5 — не юзернейм
        self.assertIn("Не вижу юзернеймов", self.t.last)
        self.assertEqual(app.names(), [])

    def test_list_remove_and_clear(self):
        self.t.send("@busyname @freebie")
        self.t.press("list")
        self.assertIn("@busyname · слежу", self.t.last)
        self.assertIn("@freebie · поймал", self.t.last)
        self.t.press("rm|busyname")
        self.assertIsNone(app.find("busyname"))
        self.t.press("clear_caught")
        self.assertEqual(app.names(), [])

    def test_ideas_from_text(self):
        self.agent.names["nexus"] = "free"
        self.t.send("придумай крутые имена про космос")
        self.assertIn("Идеи · космос", self.t.last)
        self.assertTrue(any(b.startswith("🎯 Занять @") or b.startswith("👀 Следить @") for b in self.t.buttons))

    def test_grab_button(self):
        self.t.press("grab|freebie")
        self.assertIn("Поймал @freebie", self.t.last)
        self.assertEqual(app.find("freebie")["status"], "caught")

    def test_settings_interval_and_target(self):
        self.t.press("settings")
        self.t.press("iv|10")
        self.assertEqual(app.settings()["interval"], 10)
        self.agent.channels.append({"id": -1000000000000099, "title": "Мой канал", "username": ""})
        self.t.press("channels")
        self.assertIn("Мой канал", self.t.buttons)
        self.t.press("target|-1000000000000099|Мой канал")
        self.assertEqual(app.settings()["target"], -1000000000000099)
        self.t.send("@freebie")
        self.assertEqual(self.agent.calls[-1][1]["channel"], -1000000000000099)
        self.assertEqual(app.settings()["target"], 0)  # канал занят пойманным именем — дальше на новый

    def test_agent_down(self):
        self.agent.down = True
        self.t.send("/start")
        self.assertIn("не запущен", self.t.last)
        self.t.send("@busyname")
        self.assertEqual(app.find("busyname")["status"], "watch")
        self.assertIn("недоступен", self.t.last)


class WatcherTest(Base):
    def test_catches_when_freed(self):
        self.t.send("@busyname")
        w = app.watcher
        self.assertEqual(w.tick(1000), 5)  # занят — ждём интервал
        self.assertEqual(app.find("busyname")["checks"], 2)
        self.agent.names["busyname"] = "free"
        w.tick(1010)
        self.assertEqual(app.find("busyname")["status"], "caught")
        self.assertIn("Поймал @busyname", self.t.last)
        self.assertEqual(self.t.messages[-1]["chat_id"], 1)  # владельцу

    def test_flood_pauses(self):
        self.t.send("@busyname")
        self.agent.names["busyname"] = "flood"
        w = app.watcher
        before = len(self.t.messages)
        self.assertEqual(w.tick(1000), 60)
        self.assertIn("подождать", w.last)  # статус виден в приложении, а в чат — ни слова
        self.assertEqual(len(self.t.messages), before)
        self.assertEqual(w.tick(1100), 60)  # пауза держится без запросов
        self.assertEqual(sum(1 for c, _ in self.agent.calls if c == "username"), 2)

    def test_nothing_to_do(self):
        self.assertEqual(app.watcher.tick(1000), 30)
        self.assertEqual(self.agent.calls, [])

    def test_too_many_pauses_and_resume(self):
        self.t.send("@busyname")
        self.agent.names["busyname"] = "too_many"
        self.agent.names["busyname"] = "free"
        orig = self.agent.call

        def call(cmd, args=None, timeout=60):
            if cmd == "grab":
                return True, {"status": "too_many", "channel": args.get("channel"), "created": False}
            return orig(cmd, args, timeout)
        self.agent.call = call
        app.watcher.tick(1000)
        self.assertEqual(app.find("busyname")["status"], "paused")
        self.assertIn("лимит", self.t.last)
        self.t.press("list")
        self.assertIn("▶️ Снова следить", self.t.buttons)
        self.t.press("resume")
        self.assertEqual(app.find("busyname")["status"], "watch")

    def test_agent_down_is_silent_in_chat(self):
        """Секретарь недоступен — в чат не пишем (было: «Не могу проверять имена…» раз в час)."""
        self.t.send("@busyname")
        self.agent.down = True
        w = app.watcher
        before = len(self.t.messages)
        w.tick(1000)
        w.tick(1000 + 3700)
        self.assertEqual(len(self.t.messages), before)
        self.assertIn("недоступен", w.last)

    def test_bot_name_is_english(self):
        self.assertEqual((app.bot.name, app.bot.tg_name), ("Catcher", "Catcher"))


class Wish(Base):
    def test_fits_and_offline_candidates(self):
        w = {**app.DEFAULT_WISH, "len": "5", "style": "word", "letters": True}
        got = app.offline_candidates(w, 10)
        self.assertTrue(got)
        self.assertTrue(all(len(u) == 5 and u.isalpha() for u in got))
        w = {**app.DEFAULT_WISH, "len": "6-7", "style": "digit", "letters": False}
        got = app.offline_candidates(w, 10)
        self.assertTrue(all(6 <= len(u) <= 7 and u[-1].isdigit() for u in got))
        self.assertFalse(app.fits("abc", w))
        self.assertFalse(app.fits("abcde1", {**w, "letters": True}))

    def test_wish_screen_buttons(self):
        self.t.send("/wish")
        self.assertIn("По пожеланиям", self.t.last)
        self.t.press("wl|5")
        self.t.press("ws|word")
        self.t.press("wc|3")
        self.t.press("wtheme")
        self.t.send("космос")
        w = app.wish()
        self.assertEqual((w["len"], w["style"], w["count"], w["theme"]), ("5", "word", 3, "космос"))
        self.assertIn("5 символов, одно слово, тема «космос», только буквы, поймать 3", self.t.last)

    def test_buttons_fit_telegram(self):
        """Каждый экран: не больше 3 кнопок в ряд, callback_data до 64 байт (кириллица — 2 байта)."""
        app.set_wish("theme", "космические приключения")
        self.agent.channels.append({"id": -1000000000000123, "title": "Очень длинное название моего канала", "username": ""})
        for step in (lambda: self.t.send("/start"), lambda: self.t.send("/wish"), lambda: self.t.send("/settings"),
                     lambda: self.t.press("channels"), lambda: self.t.send("/list"),
                     lambda: self.t.send("придумай имена про космические приключения")):
            step()
            kb = (self.t.messages[-1].get("reply_markup") or {}).get("inline_keyboard") or []
            for row in kb:
                self.assertLessEqual(len(row), 3, [b["text"] for b in row])
                for btn in row:
                    self.assertLessEqual(len(btn.get("callback_data", "").encode()), 64)

    def test_wish_theme_cancel_drops_wait(self):
        self.t.send("/wish")
        self.t.press("wtheme")
        self.t.press("wish")  # «Отмена»
        self.t.send("просто текст")
        self.assertEqual(app.wish()["theme"], "")  # не записалось в тему

    def test_ideas_error_does_not_hang(self):
        with mock.patch.object(app, "check_ideas", side_effect=RuntimeError("сеть")):
            self.t.press("ideas|космос")
        self.assertIn("Не получилось придумать", self.t.last)
        self.assertIn("🔁 Ещё раз", self.t.buttons)

    def test_stale_grab_watch_buttons(self):
        self.t.press("grab|")
        self.assertIn("устарела", self.t.toasts[-1])
        self.t.press("watch|busyname")
        self.assertEqual(self.t.toasts[-1], "Проверяю…")
        self.assertIn("слежу", self.t.last)

    def test_double_grab_keeps_caught(self):
        """Второе нажатие «Занять» (или фоновая проверка) видело «занято» — имя уже наше — и затирало «поймал» на «слежу»."""
        self.t.press("grab|freebie")
        self.assertEqual(app.find("freebie")["status"], "caught")
        n = len([c for c in self.agent.calls if c[0] == "grab"])
        self.t.press("grab|freebie")
        self.assertEqual(app.find("freebie")["status"], "caught")
        self.assertEqual(len([c for c in self.agent.calls if c[0] == "grab"]), n)  # лишнего запроса к Telegram нет

    def test_parallel_grab_of_same_name_is_refused(self):
        app._grabbing.add("freebie")
        try:
            self.assertEqual(app.grab("freebie", say=False), "busy")
            self.t.press("grab|freebie")
            self.assertIn("уже занимаю", self.t.last)
            code, res = self.t.api("grab", {"name": "freebie"})
            self.assertEqual(res["result"], "busy")
            st, line = app.add("freebie", say=False)
            self.assertEqual(st, "busy")
        finally:
            app._grabbing.discard("freebie")

    def test_removed_name_is_not_resurrected_by_watcher(self):
        app.put({"name": "busyname", "status": "watch", "added": "x", "checks": 0})

        def check_and_remove(name):
            app.drop(name)  # Глеб нажал «Убрать», пока шла проверка
            return True, {"status": "taken"}
        with mock.patch.object(app, "check", side_effect=check_and_remove):
            app.watcher.tick(1000)
        self.assertIsNone(app.find("busyname"))

    def test_stop_during_grab_stays_stopped(self):
        self.t.press("wl|5")
        self.t.press("wc|3")  # ловим три: после одного поимки ловля должна продолжаться — если не нажали «Стоп»
        self.t.press("hunt|on")
        w = app.watcher
        w.tick(1000)
        u = app.hunt()["queue"][0]
        self.agent.names[u] = "free"
        orig = app._grab

        def grab_and_stop(*a, **k):
            app.set_hunt(on=False)  # «Стоп» в середине захвата (он идёт до полутора минут)
            return orig(*a, **k)
        with mock.patch.object(app, "_grab", side_effect=grab_and_stop):
            w.tick(1010)
            w.tick(1020)
        self.assertFalse(app.hunt()["on"])
        self.assertIn(u, app.hunt()["caught"])

    def test_hunt_catches_until_count(self):
        self.t.press("wl|5")
        self.t.press("ws|word")
        self.t.press("hunt|on")
        self.assertTrue(app.hunt()["on"])
        self.assertIn("⏹ Стоп", self.t.buttons)
        # первые два кандидата заняты, третий свободен
        w = app.watcher
        w.tick(1000)
        queue = app.hunt()["queue"]
        self.assertTrue(queue)
        self.agent.names[queue[1]] = "free"
        w.tick(1010)
        self.assertEqual(w.tick(1020), 1)
        h = app.hunt()
        self.assertEqual(h["caught"], [queue[1]])
        self.assertFalse(h["on"])  # поймать 1 — готово
        self.assertEqual(h["checked"], 3)
        self.assertIn("закончена", self.t.last)
        self.assertEqual(app.find(queue[1])["how"], "по пожеланиям")
        self.t.send("/start")
        self.assertIn("🎲 По пожеланиям", self.t.buttons)

    def test_hunt_alternates_with_watch_list(self):
        self.t.send("@busyname")
        self.t.press("hunt|on")
        w = app.watcher
        for i in range(4):
            w.tick(1000 + i * 10)
        checked = [a["username"] for c, a in self.agent.calls if c == "username"]
        self.assertEqual(checked.count("busyname"), 3)  # 1 при добавлении + 2 из 4 тиков
        self.assertEqual(app.hunt()["checked"], 2)

    def test_stop(self):
        self.t.press("hunt|on")
        self.t.press("hunt|off")
        self.assertFalse(app.hunt()["on"])
        self.assertEqual(app.watcher.tick(1000), 30)


class Api(Base):
    def test_state_and_add(self):
        code, s = self.t.api("state")
        self.assertEqual(code, 200)
        self.assertEqual(s["account"]["name"], "Глеб")
        self.assertEqual(s["wish"]["count"], 1)
        code, r = self.t.api("add", {"text": "@freebie busyname"})
        self.assertEqual(code, 200)
        self.assertEqual({x["name"]: x["status"] for x in r["added"]}, {"freebie": "ok", "busyname": "watch"})
        self.assertEqual(len(r["names"]), 2)
        self.assertEqual(self.t.api("add", {"text": "abc"})[0], 400)

    def test_wish_api(self):
        code, s = self.t.api("wish", {"len": "8-10", "style": "two", "theme": "крипта", "letters": False, "count": 5, "hunt": "on"})
        self.assertEqual(code, 200)
        self.assertEqual(s["wish"], {"len": "8-10", "style": "two", "theme": "крипта", "letters": False, "count": 5})
        self.assertTrue(s["hunt"]["on"])
        self.assertFalse(self.t.api("wish", {"hunt": "off"})[1]["hunt"]["on"])

    def test_remove_grab_settings_channels(self):
        self.t.api("add", {"text": "busyname"})
        self.assertEqual(self.t.api("remove", {"name": "@busyname"})[1]["names"], [])
        self.assertEqual(self.t.api("grab", {"name": "freebie"})[1]["result"], "ok")
        self.assertEqual(self.t.api("settings", {"interval": 30})[1]["settings"]["interval"], 30)
        self.assertEqual(self.t.api("settings", {"interval": 4})[0], 400)
        self.assertEqual(self.t.api("channels")[1]["channels"], [])

    def test_ideas_api(self):
        self.agent.names["nexus"] = "free"
        code, r = self.t.api("ideas", {"theme": "x"})
        self.assertEqual(code, 200)
        self.assertTrue(r["ideas"])
        self.assertTrue(all(i["mark"] for i in r["ideas"]))


class Parse(unittest.TestCase):
    def test_parse_names(self):
        self.assertEqual(app.parse_names("@Cool_Name, https://t.me/second и third5 но не @abc"), ["cool_name", "second", "third5"])
        self.assertEqual(app.parse_names("a__b__c"), [])

    def test_looks_like_names(self):
        self.assertTrue(app.looks_like_names("@coolname second"))
        self.assertFalse(app.looks_like_names("придумай имена про космос"))
        self.assertFalse(app.looks_like_names(""))


if __name__ == "__main__":
    unittest.main()
