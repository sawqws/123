#!/usr/bin/env python3
"""Тесты бота «Принтер» без сети: python3 printer/test_printer.py"""
import datetime
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import printer_bot as pb  # noqa: E402

kit = pb.kit
OWNER = 777


def printing(layer=45, total=333, p=0.5, spent=3600, state="printing", file="box.gcode", message=""):
    return {
        "print_stats": {"state": state, "filename": file, "print_duration": spent, "message": message,
                        "info": {"current_layer": layer, "total_layer": total}},
        "virtual_sdcard": {"progress": p, "layer": layer, "layer_count": total},
        "display_status": {"progress": p},
        "extruder": {"temperature": 248.0, "target": 248.0},
        "heater_bed": {"temperature": 75.0, "target": 75.0},
        "webhooks": {"state": "ready", "state_message": ""},
    }


def idle(hot=False):
    st = printing(layer=0, total=0, p=0, spent=0, state="standby", file="")
    st["extruder"] = {"temperature": 120.0 if hot else 24.0, "target": 0}
    st["heater_bed"] = {"temperature": 60.0 if hot else 24.0, "target": 0}
    return st


class FakeMoon:
    def __init__(self):
        self.st = idle()
        self.offline = False
        self.calls = []
        self.items = [{"path": "box.gcode", "modified": 1700000000, "size": 2097152},
                      {"path": "stand.gcode", "modified": 1700000100, "size": 1048576}]
        self.metadata = {"box.gcode": {"estimated_time": 24889, "layer_count": 333, "filament_weight_total": 224.6}}

    def _check(self):
        if self.offline:
            raise pb.Offline("нет")

    def status(self):
        self._check()
        return self.st

    def files(self):
        self._check()
        return sorted(self.items, key=lambda f: f["modified"], reverse=True)

    def meta(self, name):
        self._check()
        return self.metadata.get(name, {})

    def start(self, name):
        self.calls.append(("start", name))

    def pause(self):
        self.calls.append(("pause",))

    def resume(self):
        self.calls.append(("resume",))

    def cancel(self):
        self.calls.append(("cancel",))

    def gcode(self, script):
        self.calls.append(("gcode", script))

    def delete(self, name):
        self.calls.append(("delete", name))
        self.items = [f for f in self.items if f["path"] != name]

    def upload(self, name, data):
        self.calls.append(("upload", name, data))
        self.items.append({"path": name, "modified": 1700000200, "size": len(data)})


class Base(unittest.TestCase):
    def setUp(self):
        self.moon = pb.moon = FakeMoon()
        self.t = pb.bot.testing(OWNER)
        self.t.bot.support = None

    def sent_buttons(self):
        """Каждая кнопка последнего сообщения укладывается в правила стиля."""
        m = self.t.messages[-1]
        for row in (m.get("reply_markup") or {}).get("inline_keyboard") or []:
            self.assertLessEqual(len(row), 3)
            for btn in row:
                self.assertLessEqual(len(btn["text"]), 24, btn["text"])
                self.assertLessEqual(len(btn.get("callback_data", "").encode()), 64)


class Screens(Base):
    def test_printing_shows_time_and_controls(self):
        self.moon.st = printing()
        self.moon.metadata["box.gcode"] = {"estimated_time": 7200}
        self.t.send("/start")
        text = self.t.last
        for part in ("Печатает · 50 %", "box.gcode", "Слой 45 из 333", "Прошло 1 ч 0 мин", "осталось 1 ч 0 мин",
                     "Конец около", "Сопло 248°", "стол 75°"):
            self.assertIn(part, text)
        for button in ("🔄 Обновить", "⏸ Пауза", "⛔ Отмена", "📁 Файлы"):
            self.assertIn(button, self.t.buttons)
        self.sent_buttons()

    def test_getting_ready_before_first_layer(self):
        self.moon.st = printing(layer=0, p=0, spent=0)
        self.t.send("/start")
        self.assertIn("Готовится", self.t.last)
        self.assertIn("Печатает · 0 %", self.t.last)

    def test_paused_offers_resume(self):
        self.moon.st = printing(state="paused")
        self.t.send("/start")
        self.assertIn("На паузе", self.t.last)
        self.assertNotIn("Конец около", self.t.last)
        self.assertIn("▶️ Продолжить", self.t.buttons)
        self.assertNotIn("⏸ Пауза", self.t.buttons)

    def test_offline_is_clear(self):
        self.moon.offline = True
        self.t.send("/start")
        self.assertIn("Нет связи", self.t.last)
        self.assertEqual(self.t.buttons[0], "🔄 Обновить")
        self.assertNotIn("⛔ Отмена", self.t.buttons)

    def test_idle_cool_button_only_when_hot(self):
        self.t.send("/start")
        self.assertIn("Свободен", self.t.last)
        self.assertNotIn("🧊 Остудить", self.t.buttons)
        self.moon.st = idle(hot=True)
        self.t.press("home|r")
        self.assertIn("🧊 Остудить", self.t.buttons)

    def test_klipper_not_ready_is_shown(self):
        st = idle()
        st["webhooks"] = {"state": "shutdown", "state_message": "MCU shutdown"}
        self.moon.st = st
        self.t.send("/start")
        self.assertIn("MCU shutdown", self.t.last)

    def test_stranger_is_refused(self):
        self.moon.st = printing()
        self.t.send("/start", uid=999)
        self.assertIn("личный бот", self.t.last)
        self.t.press("cancelok", uid=999)
        self.assertEqual(self.moon.calls, [])

    def test_help_and_gcode(self):
        self.t.send("/help")
        self.assertIn("Что умею", self.t.last)
        self.t.send("/gcode")
        self.assertIn("Пример", self.t.last)
        self.t.send("/gcode G28")
        self.assertIn(("gcode", "G28"), self.moon.calls)


class Controls(Base):
    def test_pause_and_resume(self):
        self.moon.st = printing()
        self.t.press("pause")
        self.assertIn(("pause",), self.moon.calls)
        self.moon.st = printing(state="paused")
        self.t.press("resume")
        self.assertIn(("resume",), self.moon.calls)

    def test_pause_when_not_printing_does_nothing(self):
        self.t.press("pause")
        self.t.press("resume")
        self.assertEqual(self.moon.calls, [])

    def test_cancel_needs_confirmation(self):
        self.moon.st = printing()
        self.t.press("cancel")
        self.assertEqual(self.moon.calls, [])
        self.assertIn("Отменить печать", self.t.last)
        self.sent_buttons()
        self.t.press("cancelok")
        self.assertEqual(self.moon.calls, [("cancel",)])

    def test_cancel_when_idle_does_nothing(self):
        self.t.press("cancel")
        self.t.press("cancelok")
        self.assertEqual(self.moon.calls, [])

    def test_cool_turns_off_heaters_then_tells_when_cold(self):
        self.moon.st = idle(hot=True)
        self.t.press("cool")
        self.assertIn(("gcode", "TURN_OFF_HEATERS\nM106 S255"), self.moon.calls)
        pb.watch()
        self.assertNotIn(("gcode", "M106 S0"), self.moon.calls)
        self.moon.st = idle()
        pb.watch()
        self.assertIn(("gcode", "M106 S0"), self.moon.calls)
        self.assertIn("Остыло", self.t.last)

    def test_cool_refused_while_printing(self):
        self.moon.st = printing()
        self.t.press("cool")
        self.assertEqual(self.moon.calls, [])

    def test_controls_report_offline(self):
        self.moon.offline = True
        self.t.press("pause")
        self.assertEqual(self.moon.calls, [])


class Files(Base):
    def test_list_newest_first(self):
        self.t.press("files")
        self.assertEqual(self.t.buttons[:2], ["stand.gcode", "box.gcode"])
        self.assertIn("2 файла", self.t.last)
        self.sent_buttons()

    def test_paging(self):
        self.moon.items = [{"path": f"m{i}.gcode", "modified": i, "size": 1} for i in range(8)]
        self.t.press("files")
        self.assertIn("›", self.t.buttons)
        self.assertNotIn("‹", self.t.buttons)
        self.t.press("files|1")
        self.assertIn("‹", self.t.buttons)
        self.assertNotIn("›", self.t.buttons)

    def test_long_name_fits_button_data(self):
        name = "очень_длинное_имя_файла_" * 5 + ".gcode"
        self.moon.items = [{"path": name, "modified": 1, "size": 1}]
        self.t.press("files")
        self.sent_buttons()
        self.t.press("f|" + pb.fid(name))
        self.assertIn(name, self.t.last)

    def test_file_card(self):
        self.t.press("f|" + pb.fid("box.gcode"))
        for part in ("box.gcode", "6 ч 54 мин", "225 г", "333 слоя", "2.0 МБ"):
            self.assertIn(part, self.t.last)

    def test_print_asks_about_clean_bed(self):
        i = pb.fid("box.gcode")
        self.t.press("go|" + i)
        self.assertIn("Стол чистый", self.t.last)
        self.assertEqual(self.moon.calls, [])
        self.sent_buttons()
        self.t.press("print|" + i)
        self.assertEqual(self.moon.calls, [("start", "box.gcode")])

    def test_print_refused_when_busy(self):
        self.moon.st = printing()
        i = pb.fid("stand.gcode")
        self.t.press("go|" + i)
        self.assertNotIn("Стол чистый", self.t.last)
        self.t.press("print|" + i)
        self.assertEqual(self.moon.calls, [])

    def test_screen_start_sends_stock_command(self):
        sent = []
        real_via, real_send = pb.START_VIA, pb.ws_send
        pb.START_VIA, pb.ws_send = "screen", sent.append
        try:
            self.t.press("print|" + pb.fid("box.gcode"))
        finally:
            pb.START_VIA, pb.ws_send = real_via, real_send
        self.assertEqual(sent[0]["params"]["opGcodeFile"], f"printprt:{pb.GCODES}/box.gcode")
        self.assertEqual(self.moon.calls, [])

    def test_delete_needs_confirmation_and_protects_current(self):
        i = pb.fid("stand.gcode")
        self.t.press("del|" + i)
        self.assertEqual(self.moon.calls, [])
        self.moon.st = printing(file="stand.gcode")
        self.t.press("delok|" + i)
        self.assertEqual(self.moon.calls, [])
        self.moon.st = idle()
        self.t.press("delok|" + i)
        self.assertEqual(self.moon.calls, [("delete", "stand.gcode")])

    def test_missing_file_is_explained(self):
        self.t.press("f|ffffffffffff")
        self.assertIn("уже нет", self.t.last)

    def document(self, name, size=100):
        return {"file_name": name, "file_size": size, "file_id": "x"}

    def test_gcode_is_uploaded(self):
        real = pb.tg_download
        pb.tg_download = lambda file_id: b"G28"
        try:
            self.t.send("", document=self.document("new one.gcode"))
        finally:
            pb.tg_download = real
        self.assertIn(("upload", "new one.gcode", b"G28"), self.moon.calls)
        self.assertIn("new one.gcode", self.t.last)
        self.assertIn("🖨 Печатать", self.t.buttons)

    def test_too_big_or_wrong_files_are_explained(self):
        self.t.send("", document=self.document("big.gcode", 30 * 1024 * 1024))
        self.assertIn("20 МБ", self.t.last)
        self.t.send("", document=self.document("model.stl"))
        self.assertIn("только .gcode", self.t.last)
        self.assertEqual(self.moon.calls, [])

    def test_stranger_document_ignored(self):
        self.t.send("", uid=999, document=self.document("x.gcode"))
        self.assertEqual(self.moon.calls, [])

    def test_safe_name(self):
        self.assertEqual(pb.safe_name("../../etc/passwd.gcode"), "passwd.gcode")
        self.assertEqual(pb.safe_name('a"b\r\n.gcode'), "a_b__.gcode")
        self.assertEqual(pb.safe_name(""), "model.gcode")


class Time(unittest.TestCase):
    def test_uses_slicer_estimate_early(self):
        st = printing(p=0.05, spent=100)
        self.assertEqual(pb.remaining(st, {"estimated_time": 1000}), 950)

    def test_uses_real_speed_later(self):
        st = printing(p=0.5, spent=3600)
        self.assertEqual(pb.remaining(st, {"estimated_time": 1000}), 3600)

    def test_unknown_without_data(self):
        self.assertIsNone(pb.remaining(printing(p=0, spent=0), {}))

    def test_formats(self):
        self.assertEqual(pb.hm(24889), "6 ч 54 мин")
        self.assertEqual(pb.hm(600), "10 мин")
        self.assertEqual(pb.hm(5), "меньше минуты")

    def test_finish_tomorrow(self):
        now = datetime.datetime(2026, 11, 8, 23, 0, tzinfo=pb.TZ)
        self.assertEqual(pb.finish_at(1800, now), "23:30")
        self.assertEqual(pb.finish_at(7200, now), "завтра 01:00")
        self.assertEqual(pb.finish_at(180000, now), "11.11 01:00")


class Events(Base):
    def snap(self, st, file="box.gcode"):
        return {"online": True, "st": st, "state": st["print_stats"]["state"], "file": file, "meta": {"estimated_time": 3600}}

    def run_events(self, prev, st, ts=1000):
        messages, new = pb.events(prev, self.snap(st), ts)
        return [m[0] for m in messages], new

    def test_first_look_is_silent(self):
        out, new = self.run_events({}, printing())
        self.assertEqual(out, [])
        self.assertEqual(new["state"], "printing")
        self.assertTrue(new["first"])

    def test_start_then_first_layer_once(self):
        out, new = self.run_events({"state": "standby", "file": ""}, printing(layer=0))
        self.assertIn("Печать пошла", out[0])
        out, new = self.run_events(new, printing(layer=1))
        self.assertEqual(out, [])
        out, new = self.run_events(new, printing(layer=2))
        self.assertIn("Первый слой", out[0])
        out, new = self.run_events(new, printing(layer=3))
        self.assertEqual(out, [])

    def test_endings(self):
        prev = {"state": "printing", "file": "box.gcode", "first": True}
        for state, word in (("complete", "Готово"), ("cancelled", "отменена"), ("error", "Ошибка"), ("standby", "оборвалась")):
            out, _ = self.run_events(prev, printing(state=state))
            self.assertIn(word, out[0], state)

    def test_error_reason_is_escaped(self):
        out, _ = self.run_events({"state": "printing", "file": "box.gcode"}, printing(state="error", message="<bad>"))
        self.assertIn("&lt;bad&gt;", out[0])

    def test_pause_and_resume(self):
        out, new = self.run_events({"state": "printing", "file": "box.gcode", "first": True}, printing(state="paused"))
        self.assertIn("на паузе", out[0])
        out, new = self.run_events(new, printing())
        self.assertIn("продолжается", out[0])

    def test_lost_link_during_print_then_back(self):
        prev = {"state": "printing", "file": "box.gcode", "first": True}
        messages, new = pb.events(prev, {"online": False}, 1000)
        self.assertEqual(messages, [])
        messages, new = pb.events(new, {"online": False}, 1000 + pb.OFF_ALERT)
        self.assertIn("пропал", messages[0][0])
        messages, new = pb.events(new, {"online": False}, 1000 + pb.OFF_ALERT + 60)
        self.assertEqual(messages, [])
        messages, new = pb.events(new, self.snap(printing()), 1000 + pb.OFF_ALERT + 120)
        self.assertIn("снова на связи", messages[0][0])
        self.assertFalse(new["told"])

    def test_idle_offline_is_quiet(self):
        messages, new = pb.events({"state": "standby"}, {"online": False}, 1000)
        messages, new = pb.events(new, {"online": False}, 1000 + pb.OFF_ALERT * 3)
        self.assertEqual(messages, [])

    def test_watch_sends_and_heals_tunnel(self):
        healed = []
        real = pb.heal_tunnel
        pb.heal_tunnel = lambda: healed.append(1)
        try:
            self.t.bot.db.set("watch", {"state": "printing", "file": "box.gcode", "off": 1})
            self.moon.offline = True
            pb.watch()
            self.assertEqual(len(healed), 1)
            self.assertIn("пропал", self.t.last)
            pb.watch()
            self.assertEqual(len(healed), 1)
        finally:
            pb.heal_tunnel = real


class Wire(unittest.TestCase):
    def test_ws_frame_is_masked_and_decodes(self):
        frame = pb.ws_frame("привет")
        self.assertEqual(frame[0], 0x81)
        self.assertTrue(frame[1] & 0x80)
        n = frame[1] & 0x7F
        mask, data = frame[2:6], frame[6:]
        self.assertEqual(len(data), n)
        self.assertEqual(bytes(b ^ mask[i % 4] for i, b in enumerate(data)).decode(), "привет")

    def test_ws_frame_long(self):
        frame = pb.ws_frame("x" * 300)
        self.assertEqual(frame[1] & 0x7F, 126)
        self.assertEqual(int.from_bytes(frame[2:4], "big"), 300)


if __name__ == "__main__":
    unittest.main()
