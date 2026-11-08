#!/usr/bin/env python3
"""Тесты бота «Принтер» без сети: python3 printer/test_printer.py"""
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

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

    def details(self):
        return {}

    def history(self):
        return []

    def journal(self):
        return []


def cube(w=10.0, d=10.0, h=10.0):
    """Плоский список координат коробки: 12 треугольников."""
    c = [(0, 0, 0), (w, 0, 0), (w, d, 0), (0, d, 0), (0, 0, h), (w, 0, h), (w, d, h), (0, d, h)]
    faces = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    return [x for f in faces for i in f for x in c[i]]


class Base(unittest.TestCase):
    def setUp(self):
        self.moon = pb.moon = FakeMoon()
        self.t = pb.bot.testing(OWNER)
        self.t.bot.support = None
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, True)
        patcher = mock.patch.object(pb.slicer, "HOME", self.home)
        patcher.start()
        self.addCleanup(patcher.stop)

    def add_model(self, name="box", w=10.0, d=10.0, h=10.0):
        os.makedirs(pb.slicer.models_dir("stl"), exist_ok=True)
        pb.slicer.Mesh(cube(w, d, h)).save(pb.slicer.stl_path(name))
        return pb.fid(name)

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
        self.t.send("", document=self.document("model.3mf"))
        self.assertIn("Понимаю .stl и .gcode", self.t.last)
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


class Ask(Base):
    """Вопросы к Claude: сам Claude подменён, принтер — FakeMoon."""

    def setUp(self):
        super().setUp()
        self.asked = []
        self.reply = "Всё идёт нормально."
        patcher = mock.patch.object(pb.ai, "ask", self.fake_ask)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fake_ask(self, prompt, session=None, timeout=300):
        self.asked.append((prompt, session))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply, "sid-1"

    def test_question_gets_answer_with_live_data(self):
        self.moon.st = printing()
        self.t.send("сколько осталось?")
        prompt = self.asked[0][0]
        for part in ("сколько осталось?", "Печатает · 50 %", "Слой 45 из 333", "Сопло 248°", "box.gcode"):
            self.assertIn(part, prompt)
        self.assertNotIn("<b>", prompt)
        self.assertIn("Думаю", self.t.messages[0]["text"])
        self.assertEqual(self.t.last, "Всё идёт нормально.")
        self.assertEqual(self.t.calls[-1][0], "editMessageText")

    def test_extra_data_reaches_prompt(self):
        self.moon.details = lambda: {"gcode_move": {"speed_factor": 1.0, "extrude_factor": 0.95}, "fan": {"speed": 0.5},
                                     "print_stats": {"filament_used": 12500.0}}
        self.moon.history = lambda: [{"filename": "old.gcode", "status": "cancelled", "print_duration": 3600,
                                      "end_time": 1700000000}]
        self.moon.journal = lambda: [{"type": "response", "message": "!! Move out of range", "time": 1700000000},
                                     {"type": "response", "message": "T:250.6 /248.0 B:75.0 /75.0"},
                                     {"type": "response", "message": "ok T:250.0 /248.0"},
                                     {"type": "response", "message": "ok"}, {"type": "command", "message": "G28"}]
        self.t.send("что было?")
        prompt = self.asked[0][0]
        for part in ("Скорость 100 %, поток 95 %", "Обдув 50 %", "12.5 м", "old.gcode: cancelled, 1 ч 0 мин",
                     "!! Move out of range", "stand.gcode"):
            self.assertIn(part, prompt)
        self.assertNotIn("G28", prompt)
        self.assertNotIn("250.6", prompt)  # старый замер не должен выглядеть текущим
        self.assertNotIn("250.0", prompt)

    def test_missing_extra_data_does_not_stop_answer(self):
        def broken():
            raise pb.Offline("нет")
        self.moon.details = self.moon.history = self.moon.journal = broken
        self.t.send("как дела?")
        self.assertEqual(self.t.last, "Всё идёт нормально.")

    def test_history_does_not_invent_durations(self):
        self.moon.history = lambda: [{"filename": "now.gcode", "status": "in_progress", "print_duration": 0, "start_time": 1700000000},
                                     {"filename": "cut.gcode", "status": "interrupted", "print_duration": 0, "start_time": 1700000000}]
        self.t.send("что было?")
        prompt = self.asked[0][0]
        self.assertNotIn("now.gcode", prompt)
        self.assertIn("cut.gcode: interrupted,", prompt)
        self.assertNotIn("меньше минуты", prompt)

    def test_null_values_from_real_printer(self):
        """08.11 настоящий принтер прислал fan.speed = null, и ответ падал."""
        self.moon.details = lambda: {"gcode_move": {"speed_factor": None, "extrude_factor": None}, "fan": {"speed": None},
                                     "output_pin fan0": {"value": 0.6}, "print_stats": {"filament_used": None}}
        self.t.send("как дела?")
        self.assertEqual(self.t.last, "Всё идёт нормально.")
        self.assertIn("Обдув 60 %", self.asked[0][0])

    def test_broken_extra_data_shape_does_not_stop_answer(self):
        self.moon.history = lambda: [None]
        self.t.send("как дела?")
        self.assertEqual(self.t.last, "Всё идёт нормально.")

    def test_offline_printer_is_told_to_claude(self):
        self.moon.offline = True
        self.t.send("как дела?")
        self.assertIn("не на связи", self.asked[0][0])
        self.assertEqual(self.t.last, "Всё идёт нормально.")

    def test_stranger_never_reaches_claude(self):
        self.t.send("расскажи всё", uid=999)
        self.assertEqual(self.asked, [])
        self.assertIn("личный бот", self.t.last)

    def test_conversation_continues_and_resets(self):
        self.t.send("раз")
        self.t.send("два")
        self.assertEqual([s for _, s in self.asked], [None, "sid-1"])
        self.t.send("/new")
        self.assertIn("Новый разговор", self.t.last)
        self.t.send("три")
        self.assertIsNone(self.asked[-1][1])

    def test_old_conversation_is_forgotten(self):
        self.t.send("раз")
        self.t.bot.db.set("ai", {"sid": "sid-1", "ts": time.time() - pb.AI_TTL - 1})
        self.t.send("два")
        self.assertIsNone(self.asked[-1][1])

    def test_status_and_files_buttons_do_not_go_to_claude(self):
        self.t.send("/start")
        self.t.press("home|r")
        self.t.press("files")
        self.assertEqual(self.asked, [])

    def test_tags_become_buttons_but_nothing_happens_by_itself(self):
        self.moon.st = printing()
        self.reply = "Поставь на паузу и проверь пластик. [[pause]] [[status]]"
        self.t.send("пропал пластик")
        self.assertEqual(self.t.last, "Поставь на паузу и проверь пластик.")
        self.assertEqual(self.t.buttons, ["⏸ Пауза", "🖨 Статус"])
        self.assertEqual(self.moon.calls, [])
        self.sent_buttons()

    def test_tag_impossible_now_gives_no_button(self):
        self.reply = "Нечего ставить на паузу. [[pause]] [[resume]] [[cancel]] [[nonsense]]"
        self.t.send("пауза")
        self.assertEqual(self.t.buttons, [])
        self.assertNotIn("[[", self.t.last)

    def test_cancel_tag_still_asks(self):
        self.moon.st = printing()
        self.reply = "Отменяй. [[cancel]]"
        self.t.send("отмени")
        self.t.press("cancel")
        self.assertIn("Отменить печать?", self.t.last)
        self.assertEqual(self.moon.calls, [])

    def test_file_tag_opens_card_and_does_not_print(self):
        self.reply = "Открывай файл. [[file:box.gcode]] [[file:no_such.gcode]]"
        self.t.send("поставь коробку")
        self.assertEqual(len(self.t.buttons), 1)
        self.sent_buttons()
        self.t.press(self.t.keyboard[0]["callback_data"])
        self.assertIn("🖨 Печатать", self.t.buttons)
        self.assertEqual(self.moon.calls, [])

    def test_answer_is_escaped_and_formatted(self):
        self.reply = "## Итог\nслой < 0,3 & **важно** `G28`\n- пункт"
        self.t.send("вопрос")
        self.assertEqual(self.t.last, "<b>Итог</b>\nслой &lt; 0,3 &amp; <b>важно</b> <code>G28</code>\n• пункт")

    def test_long_answer_is_split_and_buttons_go_last(self):
        self.moon.st = printing()
        self.reply = "\n".join(["строка ответа " * 10] * 60) + " [[pause]]"
        self.t.send("расскажи подробно")
        answers = self.t.messages[1:]
        self.assertGreater(len(answers), 1)
        for m in answers:
            self.assertLessEqual(len(m["text"]), 4096)
        self.assertFalse(answers[0].get("reply_markup", {}).get("inline_keyboard"))
        self.assertEqual(self.t.buttons, ["⏸ Пауза"])

    def test_error_replaces_thinking(self):
        self.reply = pb.ai.AIError("У Claude закончился лимит, попробуй позже")
        self.t.send("вопрос")
        self.assertIn("⚠️", self.t.last)
        self.assertIn("лимит", self.t.last)
        self.assertEqual(self.t.calls[-1][0], "editMessageText")

    def test_unexpected_error_replaces_thinking(self):
        self.reply = RuntimeError("сломалось")
        with mock.patch("traceback.print_exc"):
            self.t.send("вопрос")
        self.assertIn("⚠️", self.t.last)
        self.assertNotIn("сломалось", self.t.last)
        self.assertFalse(pb.AI_LOCK.locked())

    def test_photo_is_passed_to_claude(self):
        with mock.patch.object(pb.ai, "keep_photo", return_value="photos/a.jpg") as keep:
            pb.on_photo(kit.Ctx(pb.bot, OWNER, {"id": OWNER}, text="что с первым слоем?", photo="/tmp/a.jpg"))
        keep.assert_called_once_with("/tmp/a.jpg")
        for part in ("что с первым слоем?", "photos/a.jpg", "Read"):
            self.assertIn(part, self.asked[0][0])

    def test_photo_without_caption_still_asks(self):
        with mock.patch.object(pb.ai, "keep_photo", return_value="photos/a.jpg"):
            pb.on_photo(kit.Ctx(pb.bot, OWNER, {"id": OWNER}, text="", photo="/tmp/a.jpg"))
        self.assertIn("Посмотри фото", self.asked[0][0])

    def test_image_sent_as_file_goes_to_claude(self):
        fake = types.SimpleNamespace(photo_path=lambda token, msg, folder: "/tmp/a.png")
        with mock.patch.object(kit, "media", return_value=fake), mock.patch.object(pb.ai, "keep_photo", return_value="photos/a.png"):
            self.t.send("", document={"file_id": "1", "file_name": "a.png", "mime_type": "image/png"})
        self.assertIn("photos/a.png", self.asked[0][0])

    def test_gcode_file_still_goes_to_printer(self):
        with mock.patch.object(pb, "tg_download", return_value=b"G28"):
            self.t.send("", document={"file_id": "1", "file_name": "new.gcode", "file_size": 3})
        self.assertEqual(self.moon.calls, [("upload", "new.gcode", b"G28")])
        self.assertEqual(self.asked, [])


class Models(Base):
    """Каталог, картинки, нарезка и стол в боте. Слайсер подменён."""

    def setUp(self):
        super().setUp()
        self.cuts = []
        patcher = mock.patch.object(pb.slicer, "slice_plate", self.fake_cut)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fake_cut(self, names, profile, out):
        self.cuts.append((list(names), profile))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            f.write("; estimated printing time (normal mode) = 1h 2m 3s\nG28\n; total filament used [g] = 12.34\n")
        return 1.0

    def stl_message(self, name="деталь.stl", data=None):
        data = data if data is not None else b"x"
        with mock.patch.object(pb, "tg_download", return_value=data):
            self.t.send("", document={"file_name": name, "file_size": len(data), "file_id": "x"})

    def stl_bytes(self):
        path = os.path.join(self.home, "in.stl")
        pb.slicer.Mesh(cube(30, 20, 10)).save(path)
        with open(path, "rb") as f:
            return f.read()

    def test_stl_file_becomes_model_card(self):
        self.stl_message(data=self.stl_bytes())
        self.assertEqual(self.t.calls[-1][0], "sendPhoto")
        self.assertIn("деталь", self.t.last)
        self.assertIn("30 × 20 × 10 мм", self.t.last)
        for button in ("✂️ Нарезать", "➕ На стол", "‹ Модели", "🗑 Удалить"):
            self.assertIn(button, self.t.buttons)
        self.sent_buttons()
        self.assertEqual(self.moon.calls, [])
        self.assertEqual(os.listdir(os.path.join(self.home, "gcode")), [])  # временный файл не остался

    def test_broken_stl_is_explained(self):
        self.stl_message(data=b"not a model at all")
        self.assertIn("⚠️", self.t.last)
        self.assertIn("не STL", self.t.last)
        self.assertEqual(pb.slicer.catalog(), [])

    def test_too_big_model_warns(self):
        self.add_model("wide", w=300, d=10)
        pb.send_model(kit.Ctx(pb.bot, OWNER, {"id": OWNER}), "wide")
        self.assertIn("Не влезает", self.t.last)

    def test_models_list_and_pages(self):
        for i in range(pb.PAGE + 2):
            self.add_model(f"m{i}")
        self.t.send("/models")
        self.assertIn(f"Всего {pb.PAGE + 2}", self.t.last)
        self.assertIn("›", self.t.buttons)
        self.t.press("models|1")
        self.assertEqual(len([b for b in self.t.buttons if b.startswith("m")]), 2)
        self.sent_buttons()

    def test_empty_list_tells_what_to_do(self):
        self.t.send("/models")
        self.assertIn("Пока пусто", self.t.last)
        self.assertIn("Пришли .stl", self.t.last)

    def test_home_has_models_button(self):
        self.t.send("/start")
        self.assertIn("📐 Модели", self.t.buttons)
        self.moon.st = printing()
        self.t.press("home|r")
        self.assertIn("📐 Модели", self.t.buttons)
        self.sent_buttons()

    def test_project_part_cannot_be_deleted(self):
        i = self.add_model("big_box_left")
        with open(pb.slicer.models_dir("organizers_parts.json"), "w", encoding="utf-8") as f:
            json.dump(["big_box_left"], f)
        self.t.press("m|" + i)
        self.assertNotIn("🗑 Удалить", self.t.buttons)
        self.t.press("mdelok|" + i)
        self.assertIn("органайзера", self.t.last)
        self.assertTrue(os.path.exists(pb.slicer.stl_path("big_box_left")))

    def test_delete_asks_first(self):
        i = self.add_model()
        self.t.press("mdel|" + i)
        self.assertTrue(os.path.exists(pb.slicer.stl_path("box")))
        self.assertIn("Удалить модель?", self.t.last)
        self.t.press("mdelok|" + i)
        self.assertFalse(os.path.exists(pb.slicer.stl_path("box")))

    def test_cut_uploads_but_never_starts(self):
        i = self.add_model()
        self.t.press("cut|" + i)
        self.assertEqual(self.t.buttons[:3], ["⚖️ Баланс", "🛡 Прочнее", "⚡ Быстрее"])
        self.sent_buttons()
        self.t.press(f"go_cut|{i}|bal")
        self.assertEqual(self.cuts, [(["box"], "bal")])
        self.assertEqual([c[0] for c in self.moon.calls], ["upload"])
        self.assertEqual(self.moon.calls[0][1], "box_bal.gcode")
        for part in ("Нарезал", "1h 2m 3s", "12 г"):
            self.assertIn(part, self.t.last)
        self.assertEqual(self.t.buttons, ["📄 Открыть"])
        self.t.press(self.t.keyboard[0]["callback_data"])
        self.assertIn("🖨 Печатать", self.t.buttons)
        self.assertEqual([c[0] for c in self.moon.calls], ["upload"])
        self.assertEqual(os.listdir(os.path.join(self.home, "gcode")), [])

    def test_new_file_does_not_overwrite(self):
        self.moon.items.append({"path": "box_bal.gcode", "modified": 1, "size": 1})
        self.t.press(f"go_cut|{self.add_model()}|bal")
        self.assertEqual(self.moon.calls[0][1], "box_bal_2.gcode")

    def test_busy_printer_gets_file_after_print(self):
        self.moon.st = printing()
        self.t.press(f"go_cut|{self.add_model()}|bal")
        self.assertEqual(self.moon.calls, [])  # передача грузит процессор принтера: во время печати нельзя
        self.assertIn("Принтер печатает", self.t.last)
        pb.watch()
        self.assertEqual(self.moon.calls, [])
        self.moon.st = idle()
        pb.watch()
        self.assertEqual([c[0] for c in self.moon.calls], ["upload"])
        self.assertIn("Файл на принтере", self.t.last)
        self.assertEqual(self.t.bot.db.get("pending"), [])
        self.assertEqual(os.listdir(os.path.join(self.home, "gcode")), [])
        pb.watch()
        self.assertEqual(len(self.moon.calls), 1)

    def test_gcode_sent_during_print_waits_too(self):
        self.moon.st = printing()
        self.stl_message("new.gcode", b"G28")
        self.assertEqual(self.moon.calls, [])
        self.assertIn("ждёт принтер", self.t.last)

    def test_offline_printer_gets_file_later(self):
        self.moon.offline = True
        self.t.press(f"go_cut|{self.add_model()}|bal")
        self.assertIn("не на связи", self.t.last)
        self.moon.offline = False
        pb.watch()
        self.assertEqual([c[0] for c in self.moon.calls], ["upload"])

    def test_failed_upload_stays_in_queue(self):
        def broken(name, data):
            raise pb.Offline("обрыв")
        self.moon.upload = broken
        self.t.press(f"go_cut|{self.add_model()}|bal")
        self.assertEqual(len(self.t.bot.db.get("pending")), 1)
        pb.flush_pending()
        self.assertEqual(len(self.t.bot.db.get("pending")), 1)
        self.assertEqual(len(os.listdir(os.path.join(self.home, "gcode"))), 1)
        self.assertFalse(pb.PENDING_LOCK.locked())

    def test_slicer_error_is_shown(self):
        with mock.patch.object(pb.slicer, "slice_plate", side_effect=pb.slicer.ModelError("Слайсер не смог нарезать (код 3)")):
            self.t.press(f"go_cut|{self.add_model()}|bal")
        self.assertIn("код 3", self.t.last)
        self.assertEqual(self.moon.calls, [])

    def test_second_cut_waits_for_first(self):
        i = self.add_model()
        with pb.SLICE_LOCK:
            self.t.press(f"go_cut|{i}|bal")
        self.assertEqual(self.cuts, [])

    def test_plate_collects_parts_and_clears_after_cut(self):
        i = self.add_model("a", 50, 50, 10)
        j = self.add_model("b", 40, 40, 10)
        self.t.press("add|" + i)
        self.t.press("add|" + i)
        self.t.press("add|" + j)
        self.t.send("/models")
        self.assertIn("🍽 Стол: 3", self.t.buttons)
        self.t.press("plate")
        self.assertIn("a × 2", self.t.last)
        self.assertIn("⚖️ Баланс", self.t.buttons)
        self.sent_buttons()
        self.t.press("cutplate|bal")
        self.assertEqual(self.cuts, [(["a", "a", "b"], "bal")])
        self.assertIn("plate_a_x3_bal.gcode", self.moon.calls[0][1])
        self.assertIsNone(self.t.bot.db.get("plate"))

    def test_plate_that_does_not_fit_cannot_be_cut(self):
        i = self.add_model("a", 200, 200, 10)
        self.t.press("add|" + i)
        self.t.press("add|" + i)
        self.t.press("plate")
        self.assertIn("не влезают", self.t.last)
        self.assertNotIn("⚖️ Баланс", self.t.buttons)
        self.t.press("plateclear")
        self.assertIsNone(self.t.bot.db.get("plate"))


class Build(Base):
    """Claude правит исходник, бот собирает, Claude проверяет. Claude и сборка подменены."""

    def setUp(self):
        super().setUp()
        self.replies, self.prompts, self.built = [], [], []
        for target, fake in ((pb.ai, "ask"), (pb.slicer, "build")):
            patcher = mock.patch.object(target, fake, getattr(self, "fake_" + fake))
            patcher.start()
            self.addCleanup(patcher.stop)

    def fake_ask(self, prompt, session=None, timeout=600):
        self.prompts.append(prompt)
        return self.replies.pop(0), "sid"

    def fake_build(self, name):
        self.built.append(name)
        if name == "broken":
            raise pb.slicer.ModelError("OpenSCAD не собрал модель:\nERROR: syntax error")
        self.add_model(name, 30, 20, 10 + len(self.built))
        return [name]

    def test_build_review_then_show(self):
        self.replies = ["Рисую. [[build:cup]]", "Готово, чашка 30 мм. [[model:cup]]"]
        self.t.send("нарисуй чашку")
        self.assertEqual(self.built, ["cup"])
        review = self.prompts[1]
        for part in ("cup: 30.0 × 20.0 × 11.0 мм", "models/png/cup.png", "Read"):
            self.assertIn(part, review)
        self.assertEqual(self.t.calls[-1][0], "sendPhoto")
        self.assertIn("✂️ Нарезать", self.t.buttons)
        texts = [m.get("text") or "" for m in self.t.messages]
        self.assertTrue(any("Готово, чашка 30 мм." == x for x in texts), texts)
        self.assertFalse(any("[[" in x for x in texts))
        self.assertEqual(self.moon.calls, [])

    def test_build_error_goes_back_to_claude(self):
        self.replies = ["[[build:broken]]", "Исправил. [[build:cup]]", "Готово. [[model:cup]]"]
        self.t.send("нарисуй")
        self.assertIn("syntax error", self.prompts[1])
        self.assertEqual(self.built, ["broken", "cup"])
        self.assertNotIn("syntax error", " ".join(m.get("text") or "" for m in self.t.messages))

    def test_endless_rebuild_stops(self):
        self.replies = [f"[[build:cup{i}]]" for i in range(pb.BUILD_ROUNDS + 1)]
        self.t.send("нарисуй")
        self.assertEqual(len(self.built), pb.BUILD_ROUNDS)
        self.assertIn("не собралось", self.t.last)

    def test_unchanged_build_is_reported(self):
        self.add_model("same", 30, 20, 11)
        self.replies = ["[[build:same]]", "Ничего не изменилось."]
        self.t.send("собери")
        self.assertIn("не изменилась", self.prompts[1])

    def test_unknown_model_tag_does_not_break_answer(self):
        self.replies = ["Вот. [[model:nothing]]"]
        self.t.send("покажи")
        self.assertIn("нет", self.t.last)

    def test_no_tag_can_print(self):
        self.moon.items.append({"path": "x.gcode", "modified": 1, "size": 1})
        self.replies = ["Запускаю. [[print:x.gcode]] [[go:x.gcode]] [[start]]"]
        self.t.send("запусти печать")
        self.assertEqual(self.t.buttons, [])
        self.assertEqual(self.moon.calls, [])


class Slicing(Base):
    """printer/slicer.py: разбор STL, раскладка, песочница, сборка и вызов слайсера. Процессы подменены."""

    def stl(self, name="m", **kw):
        self.add_model(name, **kw)
        return pb.slicer.stl_path(name)

    def test_binary_and_text_stl(self):
        mesh = pb.slicer.Mesh.load(self.stl(w=30, d=20, h=10))
        self.assertEqual(len(mesh), 12)
        self.assertEqual(tuple(round(x) for x in mesh.size()), (30, 20, 10))
        path = os.path.join(self.home, "t.stl")
        with open(path, "w", encoding="ascii") as f:
            f.write("solid x\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\nendloop\nendfacet\nendsolid x\n")
        self.assertEqual(len(pb.slicer.Mesh.load(path)), 1)

    def test_garbage_is_refused(self):
        path = os.path.join(self.home, "g.stl")
        for data in (b"", b"garbage", b"solid x\nendsolid x\n", b"solid x\nvertex a b c\n"):
            with open(path, "wb") as f:
                f.write(data)
            with self.assertRaises(pb.slicer.ModelError, msg=data):
                pb.slicer.Mesh.load(path)

    def test_nan_is_refused(self):
        path = os.path.join(self.home, "n.stl")
        with open(path, "wb") as f:
            f.write(b" " * 80 + (1).to_bytes(4, "little") + struct_nan() + b"\0\0")
        with self.assertRaises(pb.slicer.ModelError):
            pb.slicer.Mesh.load(path)

    def test_placement_stands_on_bed(self):
        mesh = pb.slicer.Mesh([x + 7 for x in cube(30, 20, 10)])
        x0, y0, z0, x1, y1, z1 = mesh.placed(50, 60).bbox()
        self.assertEqual((x0, y0, z0, x1, y1, z1), (50, 60, 0, 80, 80, 10))
        x0, y0, z0, x1, y1, z1 = mesh.placed(50, 60, rotate=True).bbox()
        self.assertEqual((x0, y0, z0, x1, y1, z1), (50, 60, 0, 70, 90, 10))

    def test_rotation_keeps_faces_outward(self):
        rotated = pb.slicer.Mesh(cube(30, 20, 10)).placed(0, 0, rotate=True)
        path = os.path.join(self.home, "r.stl")
        rotated.save(path)
        with open(path, "rb") as f:
            data = f.read()
        first_normal = pb.slicer.struct.unpack("<3f", data[84:96])
        self.assertEqual(tuple(round(x) for x in first_normal), (0, 0, -1))  # нижняя грань смотрит вниз

    def test_degenerate_triangles_are_dropped(self):
        path = os.path.join(self.home, "d.stl")
        pb.slicer.Mesh(cube() + [1, 1, 1, 1, 1, 1, 1, 1, 1]).save(path)
        self.assertEqual(len(pb.slicer.Mesh.load(path)), 12)

    def test_arrange_centers_one_part(self):
        self.assertEqual(pb.slicer.arrange([(100, 50)]), [(60, 85, False)])

    def test_arrange_turns_long_side_along_x(self):
        self.assertEqual(pb.slicer.arrange([(50, 100)]), [(60, 85, True)])

    def test_arrange_many_without_overlap(self):
        sizes = [(120, 60), (120, 60), (60, 40), (30, 30), (30, 30)]
        spots = pb.slicer.arrange(sizes)
        boxes = []
        for (w, d), (x, y, rot) in zip(sizes, spots):
            w, d = (d, w) if rot else (w, d)
            self.assertGreaterEqual(min(x, y), pb.slicer.MARGIN)
            self.assertLessEqual(max(x + w, y + d), pb.slicer.BED - pb.slicer.MARGIN)
            boxes.append((x, y, x + w, y + d))
        for i, a in enumerate(boxes):
            for b in boxes[i + 1:]:
                apart = a[2] + pb.slicer.GAP <= b[0] or b[2] + pb.slicer.GAP <= a[0] or a[3] + pb.slicer.GAP <= b[1] or b[3] + pb.slicer.GAP <= a[1]
                self.assertTrue(apart, (a, b))

    def test_arrange_refuses_what_does_not_fit(self):
        for sizes in ([(215, 10)], [(200, 120), (200, 120)]):
            with self.assertRaises(pb.slicer.ModelError):
                pb.slicer.arrange(sizes)

    def test_picture_has_model(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("нет Pillow")
        out = os.path.join(self.home, "p.png")
        pb.slicer.render(pb.slicer.Mesh(cube(30, 20, 10)), out, 300, 200)
        with Image.open(out) as img:
            self.assertEqual(img.size, (300, 200))
            self.assertGreater(len(img.convert("L").getcolors(100000)), 3)  # не пустой фон

    def test_add_stl_cleans_name_and_keeps_old(self):
        path = self.stl("source", w=20, d=20, h=20)
        self.assertEqual(pb.slicer.add_stl(path, "../../etc/моя деталь!.STL"), "моя_деталь")
        self.assertEqual(pb.slicer.add_stl(path, "моя деталь!.stl"), "моя_деталь_2")

    def test_bad_names_never_reach_disk(self):
        for name in ("../x", "a/b", "", "a b", "..", "x" * 61):
            with self.assertRaises(pb.slicer.ModelError):
                pb.slicer.build(name)
            with self.assertRaises(pb.slicer.ModelError):
                pb.slicer.describe(name)

    def test_sandbox_sees_only_its_job_and_has_no_network(self):
        cmd = pb.slicer.sandbox(["node", "x.js"], "/job-dir", 800)
        text = " ".join(cmd)
        for part in ("MemoryMax=800M", "env -i", "--unshare-all", "--ro-bind /opt/orca/root /", "--bind /job-dir /job",
                     "--die-with-parent", "nice -n 19 node x.js"):
            self.assertIn(part, text)
        self.assertNotIn("/root ", text.replace("/opt/orca/root ", ""))
        self.assertNotIn("/etc", text)
        self.assertNotIn("--share-net", text)
        self.assertEqual(sum(a in ("--bind", "--ro-bind") for a in cmd), 2)

    def fake_run(self, make=None, code=0, out=""):
        calls = []

        def run(cmd, job, memory, timeout):
            calls.append((cmd, job, memory))
            for rel, data in (make or {}).items():
                with open(os.path.join(job, rel), "wb") as f:
                    f.write(data)
            return code, out
        self.clear_run()
        self.run_patch = mock.patch.object(pb.slicer, "run", run)
        self.run_patch.start()
        self.addCleanup(self.clear_run)
        return calls

    def clear_run(self):
        if getattr(self, "run_patch", None):
            self.run_patch.stop()
            self.run_patch = None

    def scad(self, name="cup"):
        os.makedirs(pb.slicer.models_dir("src"), exist_ok=True)
        with open(os.path.join(pb.slicer.models_dir("src"), name + ".scad"), "w", encoding="utf-8") as f:
            f.write("cube(10);")

    def stl_data(self):
        with open(self.stl("tmp_source"), "rb") as f:
            return f.read()

    def jobs(self):
        return os.listdir(os.path.join(self.home, "build"))

    def test_build_scad(self):
        self.scad()
        calls = self.fake_run({"cup.stl": self.stl_data()})
        self.assertEqual(pb.slicer.build("cup"), ["cup"])
        self.assertTrue(os.path.exists(pb.slicer.stl_path("cup")))
        self.assertEqual(calls[0][0][:2], ["openscad", "-o"])
        self.assertEqual(self.jobs(), [])

    def test_build_scad_error_keeps_output_for_claude(self):
        self.scad()
        self.fake_run(code=1, out="ERROR: Parser error in line 3")
        with self.assertRaises(pb.slicer.ModelError) as e:
            pb.slicer.build("cup")
        self.assertIn("Parser error", str(e.exception))
        self.assertEqual(self.jobs(), [])

    def test_build_without_source(self):
        self.fake_run()
        with self.assertRaises(pb.slicer.ModelError):
            pb.slicer.build("nothing")

    def test_build_result_must_be_real_stl(self):
        self.scad()
        self.fake_run({"cup.stl": b"not stl"})
        with self.assertRaises(pb.slicer.ModelError):
            pb.slicer.build("cup")
        self.assertFalse(os.path.exists(pb.slicer.stl_path("cup")))

    def test_build_project_marks_parts(self):
        os.makedirs(pb.slicer.models_dir("src"), exist_ok=True)
        with open(os.path.join(pb.slicer.models_dir("src"), "organizers.html"), "w", encoding="utf-8") as f:
            f.write("<script></script>")
        data = self.stl_data()
        calls = self.fake_run({"stl/box_a.stl": data, "stl/box_b.stl": data})
        self.assertEqual(pb.slicer.build("organizers"), ["box_a", "box_b"])
        self.assertEqual(calls[0][0][0], "node")
        marks = {m["name"]: m["project"] for m in pb.slicer.catalog()}
        self.assertTrue(marks["box_a"])
        self.assertFalse(marks["tmp_source"])
        self.assertEqual(self.jobs(), [])

    def test_slice_uses_our_profile_and_placed_parts(self):
        self.add_model("a", 60, 60, 10)
        self.add_model("b", 40, 40, 10)
        seen = {}

        def run(cmd, job, memory, timeout):
            seen["cmd"], seen["memory"] = cmd, memory
            seen["boxes"] = [pb.slicer.Mesh.load(os.path.join(job, f"part{i}.stl")).bbox() for i in range(2)]
            seen["files"] = sorted(os.listdir(job))
            os.makedirs(os.path.join(job, "out"), exist_ok=True)
            with open(os.path.join(job, "out", "plate_1.gcode"), "w", encoding="utf-8") as f:
                f.write("G28\n")
            return 0, ""
        out = os.path.join(self.home, "gcode", "x.gcode")
        with mock.patch.object(pb.slicer, "run", run):
            pb.slicer.slice_plate(["a", "b"], "bal", out)
        with open(out, encoding="utf-8") as f:
            self.assertEqual(f.read(), "G28\n")
        text = " ".join(seen["cmd"])
        self.assertIn("--arrange 0", text)
        self.assertIn("machine.json;process_bal.json", text)
        self.assertIn("--load-filaments filament_mid.json", text)
        self.assertIn("filament_mid.json", seen["files"])
        for box in seen["boxes"]:
            self.assertGreaterEqual(min(box[0], box[1]), pb.slicer.MARGIN)
            self.assertLessEqual(max(box[3], box[4]), pb.slicer.BED - pb.slicer.MARGIN)
            self.assertEqual(box[2], 0)
        self.assertEqual(self.jobs(), [])

    def test_slice_failures(self):
        self.add_model("a")
        out = os.path.join(self.home, "gcode", "x.gcode")
        self.fake_run(code=137)
        with self.assertRaises(pb.slicer.ModelError) as e:
            pb.slicer.slice_plate(["a"], "bal", out)
        self.assertIn("памяти", str(e.exception))
        self.fake_run(code=206, out="Nothing to be sliced")
        with mock.patch("builtins.print"), self.assertRaises(pb.slicer.ModelError) as e:
            pb.slicer.slice_plate(["a"], "bal", out)
        self.assertIn("код 206", str(e.exception))
        with self.assertRaises(pb.slicer.ModelError):
            pb.slicer.slice_plate(["a"], "nope", out)
        self.add_model("tall", h=300)
        with self.assertRaises(pb.slicer.ModelError) as e:
            pb.slicer.slice_plate(["tall"], "bal", out)
        self.assertIn("по высоте", str(e.exception))
        self.assertEqual(self.jobs(), [])
        self.assertFalse(os.path.exists(out))

    def test_all_profiles_exist_and_are_for_this_printer(self):
        for label, process, filament in pb.slicer.PROFILES.values():
            for name in ("machine.json", process, filament):
                with open(os.path.join(pb.slicer.PROFILES_DIR, name), encoding="utf-8") as f:
                    data = json.load(f)
                self.assertIn("name", data, name)
            with open(os.path.join(pb.slicer.PROFILES_DIR, process), encoding="utf-8") as f:
                self.assertIn("Creality Ender-3 V3 KE 0.4 nozzle", json.load(f)["compatible_printers"])


def struct_nan():
    return pb.slicer.struct.pack("<12f", 0, 0, 1, float("nan"), 0, 0, 1, 0, 0, 0, 1, 0)


class Claude(unittest.TestCase):
    """printer/ai.py: как зовётся Claude Code. Сам процесс подменён."""

    @staticmethod
    def done(result="Ответ", sid="s1", code=0, is_error=False, err=""):
        out = json.dumps({"result": result, "session_id": sid, "is_error": is_error})
        return subprocess.CompletedProcess([], code, out, err)

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        patcher = mock.patch.object(pb.ai, "WORK", self.tmp)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_cannot_control_anything_and_keeps_no_secrets(self):
        env = {"PRINTER_TOKEN": "1:x", "HUB_KEY": "k", "BOT_OWNER": "1", "PATH": "/bin", "CLAUDE_CODE_OAUTH_TOKEN": "t"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(subprocess, "run", return_value=self.done()) as run:
            self.assertEqual(pb.ai.ask("вопрос"), ("Ответ", "s1"))
        cmd, kw = run.call_args[0][0], run.call_args[1]
        denied = cmd[cmd.index("--disallowedTools") + 1:cmd.index("--allowedTools")]
        allowed = cmd[cmd.index("--allowedTools") + 1:]
        for tool in ("Bash", "NotebookEdit", "Task", "Agent"):
            self.assertIn(tool, denied)
        # Read без условий открыл бы /etc/*.env, Edit без условий дал бы править что угодно
        self.assertEqual(allowed, ["WebSearch", "WebFetch", "Edit(./models/src/**)", "Write(./models/src/**)"])
        self.assertNotIn("--resume", cmd)
        self.assertEqual(kw["cwd"], self.tmp)
        self.assertEqual(kw["stdin"], subprocess.DEVNULL)  # иначе claude -p допишет к вопросу чужой ввод
        self.assertEqual(set(kw["env"]), {"PATH", "CLAUDE_CODE_OAUTH_TOKEN"})

    def test_prompt_lists_actions_and_forbids_pretending(self):
        rules = pb.ai.rules()
        for tag in ("[[status]]", "[[files]]", "[[pause]]", "[[resume]]", "[[cancel]]", "[[cool]]", "[[file:"):
            self.assertIn(tag, rules)
        self.assertIn("Никогда не пиши", rules)

    def test_resumes_conversation(self):
        with mock.patch.object(subprocess, "run", return_value=self.done(sid="s2")) as run:
            self.assertEqual(pb.ai.ask("вопрос", "s1"), ("Ответ", "s2"))
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[cmd.index("--resume") + 1], "s1")

    def test_lost_conversation_starts_new(self):
        lost = subprocess.CompletedProcess([], 1, "", "No conversation found with session ID: s1")
        with mock.patch.object(subprocess, "run", side_effect=[lost, self.done(sid="s9")]) as run, mock.patch("builtins.print"):
            self.assertEqual(pb.ai.ask("вопрос", "s1"), ("Ответ", "s9"))
        self.assertNotIn("--resume", run.call_args[0][0])

    def test_failures_have_clear_text(self):
        cases = [(subprocess.CompletedProcess([], 1, "", "Please run /login"), "не вошёл"),
                 (subprocess.CompletedProcess([], 1, "", "OAuth session expired"), "не вошёл"),
                 (subprocess.CompletedProcess([], 1, "", "usage limit reached"), "лимит"),
                 (self.done(result="", is_error=True), "не ответил"),
                 (subprocess.CompletedProcess([], 0, "не json", ""), "не ответил")]
        for result, text in cases:
            with mock.patch.object(subprocess, "run", return_value=result), mock.patch("builtins.print"):
                with self.assertRaises(pb.ai.AIError) as e:
                    pb.ai.ask("вопрос")
            self.assertIn(text, str(e.exception))

    def test_timeout_and_no_claude(self):
        with mock.patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired("claude", 1)):
            with self.assertRaises(pb.ai.AIError):
                pb.ai.ask("вопрос")
        with mock.patch.object(subprocess, "run", side_effect=FileNotFoundError()):
            with self.assertRaises(pb.ai.AIError):
                pb.ai.ask("вопрос")

    def test_photo_moves_inside_work_folder_and_old_ones_go(self):
        src = os.path.join(self.tmp, "in.jpg")
        open(src, "wb").close()
        rel = pb.ai.keep_photo(src)
        self.assertTrue(rel.startswith("photos/") and rel.endswith(".jpg"))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, rel)))
        self.assertFalse(os.path.exists(src))
        src2 = os.path.join(self.tmp, "in2.jpg")
        open(src2, "wb").close()
        rel2 = pb.ai.keep_photo(src2, now=time.time() + 8 * 86400)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, rel)))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, rel2)))


if __name__ == "__main__":
    unittest.main()
