"""Проверка бота без Telegram и сайта: черновик -> правка -> отправка -> правила, алфавит, /update.
usage: python3 bot/test_bot.py
"""
import json
import os
import pathlib
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("TELEGRAM_TOKEN", "test")
import bot  # noqa: E402
import drafts  # noqa: E402
import web  # noqa: E402

URL = "https://horodigital.ru/student/topic/11111111-1111-1111-1111-111111111111/task/22222222-2222-2222-2222-222222222222"
TASK = "22222222-2222-2222-2222-222222222222"
CHAT = 42


class Sync:
    """threading.Thread, который выполняет задачу сразу."""
    def __init__(self, target, args=(), daemon=None):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


class BotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.patches = [
            mock.patch.object(bot, "SETTINGS", os.path.join(t, "settings.json")),
            mock.patch.object(bot, "OVERVIEW", os.path.join(t, "overview.json")),
            mock.patch.object(drafts, "TMP", t),
            mock.patch.object(drafts, "STYLE_DIR", os.path.join(t, "style")),
            mock.patch.object(drafts, "STYLE", os.path.join(t, "style", "style.md")),
            mock.patch.object(drafts, "EDITS", os.path.join(t, "style", "edits.jsonl")),
            mock.patch.object(bot.threading, "Thread", Sync),
            mock.patch.object(bot, "tg", self.fake_tg),
            mock.patch.object(bot, "send_file", lambda chat, path, caption="", **kw: self.files.append(path)),
            mock.patch.object(bot, "claude", self.fake_claude),
            mock.patch.object(bot, "SEND_DELAY", "0"),  # отправка «как человек» с задержкой — в своём тесте
            mock.patch.object(bot, "node", self.fake_node),
            mock.patch.object(bot, "INBOX", os.path.join(t, "inbox")),
            mock.patch.object(bot.media, "photo_path", self.fake_photo),  # без сети: фото и видео из Telegram
            mock.patch.object(bot.media, "video", self.fake_video),
        ]
        for p in self.patches:
            p.start()
        self.msgs, self.files, self.prompts, self.nodes, self.reads = [], [], [], [], []
        self.ov = {"tasks": [], "grades": [], "comments": [], "at": 1}
        self.video = {"frames": [], "text": ""}
        os.makedirs(drafts.task_dir(TASK))
        with open(os.path.join(drafts.task_dir(TASK), "task.json"), "w") as f:
            json.dump({"data": {"type": "detailedAnswer", "title": "Конспект §5"}}, f)

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()
        bot.mode.clear()
        bot.LAST.clear()

    # ---- заглушки ----
    def fake_tg(self, method, **p):
        if method == "sendMessage":
            self.msgs.append((p["text"], p.get("reply_markup")))
        return {"ok": True, "result": []}

    def fake_claude(self, prompt, tools, timeout=0, on_tool=None):
        self.prompts.append(prompt)
        if prompt.startswith("Задание:"):  # решение: Claude пишет черновик и картинку
            drafts.write(TASK, "Клетка делится митозом.\nЭто важно.")
            d = drafts.files_dir(TASK)
            os.makedirs(d, exist_ok=True)
            pathlib.Path(d, "hand.png").write_bytes(b"png")
            return True, "Сделал черновик"
        if "Глеб написал про него" in prompt:  # просьба поправить черновик
            drafts.write(TASK, "Клетка делится митозом.")
            return True, "Убрал второе предложение"
        os.makedirs(drafts.STYLE_DIR, exist_ok=True)
        pathlib.Path(drafts.STYLE).write_text("- коротко\n")
        return True, "- писать короче"

    def fake_node(self, *args, timeout=0, prog="node"):
        if args[:1] == ("horo/overview.js",):  # чтение списка для приложения — не отправка
            self.reads.append(args)
            return True, json.dumps(self.ov)
        self.nodes.append(args)
        return True, "Статус: checking"

    def fake_photo(self, token, msg, folder=None):
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "photo.jpg")
        pathlib.Path(path).write_bytes(b"jpg")
        return path

    def fake_video(self, token, msg, folder=None, frames=4):
        os.makedirs(folder, exist_ok=True)
        shots = []
        for i in range(len(self.video["frames"])):
            shots.append(os.path.join(folder, f"frame-{i + 1}.jpg"))
            pathlib.Path(shots[-1]).write_bytes(b"jpg")
        if self.video.get("error"):
            raise bot.media.MediaError(self.video["error"])
        return {"frames": shots, "text": self.video["text"], "seconds": 3}

    def say(self, text="", **extra):
        bot.handle({"chat": {"id": CHAT}, "from": {"id": CHAT}, "text": text, **extra})

    def last(self):
        return self.msgs[-1]

    # ---- тесты ----
    def test_draft_edit_and_submit(self):
        self.say(URL)
        self.assertIn("horo/tmp/" + TASK + "/draft.txt", self.prompts[0])
        self.assertTrue(any("Черновик для учителя</b>" in m and "Конспект §5" in m and "Это важно" in m and kb
                            for m, kb in self.msgs))
        self.assertEqual(self.files[-1], os.path.join(drafts.files_dir(TASK), "hand.png"))
        self.assertEqual(self.nodes, [])  # без подтверждения ничего не отправлено

        self.say("Клетка делится митозом.")
        self.assertEqual(drafts.read(TASK), "Клетка делится митозом.")
        self.assertEqual(drafts.recent_edits()[-1]["before"], "Клетка делится митозом.\nЭто важно.")

        bot.on_callback({"id": "1", "data": f"send:{TASK}", "message": {"chat": {"id": CHAT}, "message_id": 5}})
        args = self.nodes[-1]
        self.assertEqual(args[:3], ("horo/answer.js", URL, drafts.text_path(TASK)))
        self.assertIn(os.path.join(drafts.files_dir(TASK), "hand.png"), args)
        self.assertIsNone(bot.draft())
        self.assertIn("БЫЛО:", self.prompts[-1])  # правки ушли на обобщение
        self.assertIn("Запомнил", self.last()[0])

    def test_short_message_is_a_request(self):
        self.say(URL)
        self.say("убери второе")
        self.assertIn("«убери второе»", self.prompts[-1])
        self.assertEqual(drafts.read(TASK), "Клетка делится митозом.")
        self.assertEqual(bot.draft()["pairs"][0][2], "убери второе")
        self.say("ок")
        self.assertIn("ЧТО ОН НАПИСАЛ: убери второе", self.prompts[-1])

    def test_own_photo_replaces_generated(self):
        self.say(URL)
        with mock.patch.object(bot, "download", lambda fid: (b"jpg", "photo.jpg")):
            self.say(photo=[{"file_id": "a"}, {"file_id": "b"}])
            self.say(photo=[{"file_id": "c"}])
        names = [os.path.basename(f) for f in drafts.files(TASK)]
        self.assertEqual(names, ["01_photo.jpg", "02_photo.jpg"])

    def test_album_photos_do_not_overwrite_each_other(self):
        """Фото приходят параллельно (альбом): под одним номером не пишем, второе не затирает первое."""
        self.say(URL)
        task = bot.draft()["task"]
        # настоящие потоки (Thread в тестах подменён на синхронный): проверяем номера под замком
        real = bot.REAL_THREAD
        ts = [real(target=drafts.add_file, args=(task, b"x%d" % i, "p.jpg")) for i in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        got = [os.path.basename(f) for f in drafts.files(task) if f.endswith("_p.jpg")]
        self.assertEqual(len(got), 8)
        self.assertEqual(len(set(got)), 8)

    def test_send_waits_like_a_human(self):
        self.say(URL)
        with mock.patch.object(bot, "SEND_DELAY", "5-5"):
            bot.on_callback({"id": "1", "data": f"send:{TASK}", "message": {"chat": {"id": CHAT}, "message_id": 5}})
        self.assertEqual(self.nodes, [])  # сразу не отправил
        self.assertIn("Отправлю в", self.last()[0])
        self.assertIn(f"now:{TASK}", self.last()[1])
        at = bot.draft(TASK)["send_at"]
        self.assertAlmostEqual(at - __import__("time").time(), 300, delta=5)
        bot.due_sends(at - 1)  # ещё рано
        self.assertEqual(self.nodes, [])
        bot.due_sends(at + 1)  # время пришло — отправил сам
        self.assertEqual(self.nodes[-1][0], "horo/answer.js")
        self.assertIsNone(bot.draft())

    def test_send_now_and_hold(self):
        self.say(URL)
        with mock.patch.object(bot, "SEND_DELAY", "4-12"):
            bot.on_callback({"id": "1", "data": f"send:{TASK}", "message": {"chat": {"id": CHAT}, "message_id": 5}})
        bot.on_callback({"id": "1", "data": f"hold:{TASK}", "message": {"chat": {"id": CHAT}, "message_id": 6}})
        self.assertNotIn("send_at", bot.draft(TASK))
        bot.due_sends(__import__("time").time() + 3600)  # отменён — не уйдёт и через час
        self.assertEqual(self.nodes, [])
        bot.on_callback({"id": "1", "data": f"now:{TASK}", "message": {"chat": {"id": CHAT}, "message_id": 7}})
        self.assertEqual(self.nodes[-1][0], "horo/answer.js")

    def test_delay_can_be_turned_off(self):
        self.say("/delay off")
        self.assertIn("сразу", self.last()[0])
        self.assertFalse(bot.human_on())
        self.say(URL)
        with mock.patch.object(bot, "SEND_DELAY", "5-5"):  # выключено — уходит сразу, без таймера
            bot.on_callback({"id": "1", "data": f"send:{TASK}", "message": {"chat": {"id": CHAT}, "message_id": 5}})
        self.assertEqual(self.nodes[-1][0], "horo/answer.js")
        bot.on_more(CHAT, 9, "1", "human")  # кнопка в «Ещё» открывает настройку отправки
        bot.on_callback({"id": "1", "data": "sd:tests", "message": {"chat": {"id": CHAT}, "message_id": 9}})
        self.assertTrue(bot.human_on())
        self.assertEqual(bot.ROUTES["/api/human"]({"on": False}), {"human": False})

    def test_send_setting_button_on_keyboard(self):
        self.assertIn("⏳ Отправка: сразу", bot.menu_kb())  # отдельная кнопка у поля ввода
        self.say("⏳ Отправка: сразу")
        self.assertIn("Отправка", self.last()[0])
        self.assertIn("sd:4-12", self.last()[1])
        bot.on_callback({"id": "1", "data": "sd:15-40", "message": {"chat": {"id": CHAT}, "message_id": 9}})
        self.assertEqual(bot.delay_range(), "15-40")
        self.assertIn("⏳ Отправка: 15–40 мин", bot.menu_kb())
        bot.on_callback({"id": "1", "data": "sd:tests", "message": {"chat": {"id": CHAT}, "message_id": 9}})
        self.assertFalse(bot.human_on())
        self.say(bot.BTN_DELAY_ON)  # старая надпись на клавиатуре тоже открывает настройку
        self.assertIn("Отправка", self.last()[0])

    def test_restart_resumes_tasks(self):
        bot.mark_active(TASK, {"chat": CHAT, "url": URL, "text": "", "show": False})  # решалось, когда бот перезапустили
        bot.resume_active()
        self.assertTrue(any("Продолжаю после перезапуска" in m for m, _ in self.msgs))
        self.assertTrue(any(p.startswith("Задание:") and URL in p for p in self.prompts))
        self.assertEqual(bot.load_settings().get("active"), {})  # решено — из списка ушло
        s = bot.load_settings()
        s["active"] = {TASK: {"chat": CHAT, "url": URL, "tries": 2}}  # роняет бота — третий раз не берём
        bot.save_settings(s)
        n = len(self.prompts)
        bot.resume_active()
        self.assertEqual(len(self.prompts), n)
        self.assertIn("Не доделал", self.last()[0])

    def test_table_is_filled_on_picture(self):
        p = bot.prompt_for(URL, "", False)
        self.assertIn("from table import Table", p)
        self.assertIn("ПРЯМО НА КАРТИНКЕ", p)
        self.assertIn(f"horo/tmp/{TASK}/draft_files/N_table.jpg", p)

    def test_no_personal_info_in_tasks(self):
        p = bot.prompt_for(URL, "", False)
        self.assertIn("ничего личного о Глебе", p)
        self.assertIn("не упоминай, что помогал ИИ".lower(), p.lower())

    def test_each_draft_chooses_how_to_send(self):
        self.say(URL)
        self.assertIn(f"later:{TASK}", self.last()[1])
        self.assertIn(f"atq:{TASK}", self.last()[1])
        # ⏳ С задержкой — даже если в настройке «сразу»
        bot.on_callback({"id": "1", "data": f"later:{TASK}", "message": {"chat": {"id": CHAT}, "message_id": 5}})
        self.assertEqual(self.nodes, [])
        self.assertIn("Отправлю", self.last()[0])
        left = bot.draft(TASK)["send_at"] - __import__("time").time()
        self.assertTrue(4 * 60 - 5 <= left <= 12 * 60 + 5)
        # 🕐 Ко времени: варианты кнопками и своё время текстом
        bot.on_callback({"id": "1", "data": f"atq:{TASK}", "message": {"chat": {"id": CHAT}, "message_id": 6}})
        self.assertIn("Ко скольки", self.last()[0])
        self.assertIn(f"at:{TASK}:", self.last()[1])
        self.say("в 23:59")
        self.assertIn("23:59", self.last()[0])
        self.assertEqual(self.nodes, [])  # текст ушёл во время, а не в черновик
        self.assertEqual(drafts.read(TASK), "Клетка делится митозом.\nЭто важно.")
        bot.due_sends(bot.draft(TASK)["send_at"] + 1)
        self.assertEqual(self.nodes[-1][0], "horo/answer.js")

    def test_time_buttons_and_parse(self):
        import datetime
        now = datetime.datetime(2026, 10, 14, 15, 10, tzinfo=bot.MSK)
        labels = [lab for lab, _ in bot.time_choices(now)]
        self.assertEqual(labels[:3], ["через 30 мин", "через 1 ч", "через 2 ч"])
        self.assertIn("в 19:00", labels)
        at = datetime.datetime.fromtimestamp(bot.parse_time("в 19.40", now), bot.MSK)
        self.assertEqual((at.day, at.hour, at.minute), (14, 19, 40))
        at = datetime.datetime.fromtimestamp(bot.parse_time("7:30", now), bot.MSK)
        self.assertEqual((at.day, at.hour), (15, 7))  # прошло — значит, завтра
        self.assertEqual(bot.parse_time("через 40 минут", now), int((now + datetime.timedelta(minutes=40)).timestamp()))
        self.assertIsNone(bot.parse_time("25:99", now))

    def test_drop_and_ok_word(self):
        self.say(URL)
        bot.on_callback({"id": "1", "data": "drop", "message": {"chat": {"id": CHAT}, "message_id": 5}})
        self.assertIsNone(bot.draft())
        self.assertEqual(self.nodes, [])
        self.say(URL)
        self.say("ок")
        self.assertEqual(self.nodes[-1][0], "horo/answer.js")
        self.assertEqual(len(self.prompts), 2)  # правок не было — обобщать нечего

    def test_autotest_has_no_draft(self):
        def solve_only(prompt, tools, timeout=0, on_tool=None):
            self.prompts.append(prompt)
            return True, "Баллы 5/5"
        with mock.patch.object(bot, "claude", solve_only):
            self.say(URL)
        self.assertIsNone(bot.draft())
        self.assertIn("Баллы 5/5", self.last()[0])

    def voice(self, heard=None, error=None, **extra):
        """Голосовое: распознавание подменено (без сети и whisper)."""
        def fake(token, msg):
            self.assertEqual(msg["voice"]["file_id"], "v1")
            if error:
                raise bot.media.MediaError(error)
            return heard
        with mock.patch.object(bot.media, "voice_text", fake):
            bot.handle({"chat": {"id": CHAT}, "from": {"id": CHAT}, "message_id": 3,
                        "voice": {"file_id": "v1", "duration": 2}, **extra})

    def test_voice_link_routed_like_text(self):
        """Ссылка голосом — тот же run_task, что и набранная; сначала эхо того, что услышал."""
        calls = []
        with mock.patch.object(bot, "run_task", lambda *a: calls.append(a)):
            self.voice(f"Реши {URL} <срочно>")
        self.assertEqual(calls, [(CHAT, URL, f"Реши {URL} <срочно>", False, 3)])
        texts = [m for m, _ in self.msgs]
        self.assertEqual(texts[0], "🎙 Слушаю…")
        self.assertIn(f"🎙 «Реши {URL} &lt;срочно&gt;»", texts)  # эхо экранировано

    def test_voice_edits_draft_and_presses_buttons(self):
        self.say(URL)
        self.voice("Убери второе.")
        self.assertIn("«Убери второе.»", self.prompts[-1])  # как набранная просьба
        self.assertEqual(drafts.read(TASK), "Клетка делится митозом.")
        self.voice("Ок.")
        self.assertEqual(self.nodes[-1][0], "horo/answer.js")  # «ок» голосом отправил черновик
        self.voice("Оценки.")
        self.assertEqual(self.nodes[-1], ("horo/grades.js", "--html"))  # кнопка голосом
        self.assertEqual(bot.spoken_button("решить и сдать"), bot.BTN_DO)
        self.assertEqual(bot.spoken_button("оценки за неделю"), "оценки за неделю")

    def test_voice_error_reported(self):
        with mock.patch.object(bot, "run_task", lambda *a: self.fail("не должно запускаться")):
            self.voice(error="в голосовом не слышно слов")
        self.assertEqual(self.last()[0], "⚠️ В голосовом не слышно слов.")
        self.assertNotIn("«", self.last()[0])

    def test_voice_kinds(self):
        self.assertEqual(bot.media.kind({"video_note": {"file_id": "x"}}), "video")  # кружок — теперь видео
        self.assertEqual(bot.media.kind({"audio": {"file_id": "x"}}), "voice")
        self.assertEqual(bot.media.kind({"photo": [{"file_id": "x"}]}), "photo")

    def looker(self, answer):
        """claude(), который «смотрит» на фото и отвечает JSON; решение по фото — ответами."""
        def fake(prompt, tools, timeout=0, on_tool=None):
            self.prompts.append(prompt)
            if prompt.startswith("Глеб прислал"):
                return True, answer
            if prompt.startswith("На фото задание"):
                return True, "1) x = 2\n2) 0,5"
            return self.fake_claude(prompt, tools, timeout, on_tool)
        return mock.patch.object(bot, "claude", fake)

    def press(self, data):
        bot.on_callback({"id": "1", "data": data, "message": {"chat": {"id": CHAT}, "message_id": 5}})

    def test_photo_finds_task_from_list(self):
        """Скриншот задания без ссылки: Claude узнаёт задание по списку несделанных, бот предлагает решить."""
        pathlib.Path(bot.OVERVIEW).write_text(json.dumps({"at": 1, "tasks": [
            {"url": URL, "title": "Конспект §5", "subj": "Биология"}], "comments": []}))
        calls = []
        with self.looker(json.dumps({"match": URL, "title": "Конспект §5", "task": ""})), \
             mock.patch.object(bot, "run_task", lambda *a: calls.append(a)):
            self.say(photo=[{"file_id": "a"}], message_id=3)
            self.assertIn("Конспект §5 — " + URL, self.prompts[-1])     # список заданий — в подсказке Claude
            self.assertIn(os.path.join(bot.INBOX), self.prompts[-1])     # и путь к фото
            text, markup = self.last()
            self.assertIn("Нашёл задание", text)
            self.assertIn("ph:do", markup)
            self.press("ph:do")
            self.press("ph:show")
        self.assertEqual(calls, [(CHAT, URL, "", False), (CHAT, URL, "", True)])
        self.assertEqual(self.nodes, [])

    def test_photo_condition_solved_by_photo(self):
        with self.looker('```json\n{"link": "", "match": "", "title": "Уравнение", "task": "Реши 2x = 4"}\n```'):
            self.say(photo=[{"file_id": "a"}], caption="помоги")
            text, markup = self.last()
            self.assertIn("Реши 2x = 4", text)
            self.assertIn("ph:solve", markup)
            self.assertIn("«помоги»", self.prompts[-1])
            self.press("ph:solve")
        self.assertIn("photo.jpg", self.prompts[-1])
        self.assertIn("На сайт ничего не отправляй", self.prompts[-1])
        self.assertIn("x = 2", self.last()[0])
        self.assertIn("на сайт не отправлял", self.last()[0])
        self.assertEqual(self.nodes, [])

    def test_photo_without_task(self):
        with self.looker("Это кот"):
            self.say(photo=[{"file_id": "a"}])
        self.assertIn("Задания на фото не нашёл", self.last()[0])
        self.press("ph:do")
        self.assertIn("неактуально", self.last()[0])

    def test_photo_caption_link_goes_like_text(self):
        calls = []
        with mock.patch.object(bot, "run_task", lambda *a: calls.append(a)), \
             mock.patch.object(bot.media, "photo_path", lambda *a: self.fail("фото не нужно")):
            self.say(photo=[{"file_id": "a"}], caption="вот " + URL, message_id=4)
        self.assertEqual(calls, [(CHAT, URL, "вот " + URL, False, 4)])

    def test_video_speech_is_text(self):
        """Видео со словами — как голосовое: «Оценки» нажимает кнопку, ссылка решается."""
        self.video = {"frames": ["f"], "text": "Оценки."}
        self.say(video={"file_id": "v", "duration": 3})
        self.assertEqual(self.nodes[-1], ("horo/grades.js", "--html"))
        self.assertIn("🎬 Смотрю видео…", [m for m, _ in self.msgs])
        self.video = {"frames": [], "text": f"реши {URL}"}
        calls = []
        with mock.patch.object(bot, "run_task", lambda *a: calls.append(a)):
            self.say(video_note={"file_id": "v", "duration": 3})
        self.assertEqual(calls[0][1], URL)

    def test_video_frames_are_looked_at(self):
        self.video = {"frames": ["a", "b"], "text": "что тут"}
        with self.looker(json.dumps({"task": "Найди площадь круга r = 2", "title": "Площадь"})):
            self.say(video={"file_id": "v", "duration": 5})
        self.assertIn("кадры из видео", self.prompts[-1])
        self.assertIn("frame-2.jpg", self.prompts[-1])
        self.assertIn("«что тут»", self.prompts[-1])
        self.assertIn("ph:solve", self.last()[1])

    def test_video_error(self):
        self.video = {"frames": [], "text": "", "error": "видео больше 20 МБ"}
        self.say(video={"file_id": "v"})
        self.assertTrue(self.last()[0].startswith("⚠️ видео больше 20 МБ"))

    def test_video_with_draft_edits_it(self):
        self.say(URL)
        self.video = {"frames": ["a"], "text": "убери второе"}
        self.say(video_note={"file_id": "v"})
        self.assertEqual(drafts.read(TASK), "Клетка делится митозом.")

    def test_late_list_sent_as_html(self):
        with mock.patch.object(bot, "node", lambda *a, timeout=0, prog="node": (True, '❗ <b>Просрочено: 1</b>\n🔁 <a href="u">Тест &amp; Ко</a>')):
            self.say(bot.BTN_LATE)
        self.assertEqual(self.last()[0], '❗ <b>Просрочено: 1</b>\n🔁 <a href="u">Тест &amp; Ко</a>')  # разметка не экранирована

    def test_handwriting_without_alphabet(self):
        # без алфавита рисует всё равно (цифры и латиница — его настоящие), только напоминает про алфавит
        with mock.patch.object(bot, "GLEB_EXTRA", "/nonexistent"):
            p = bot.prompt_for(URL, "", False)
            self.assertNotIn("НУЖЕН_АЛФАВИТ", p)
            self.assertIn("всё равно рисуй", p)
            # старый ответ Claude «НУЖЕН_АЛФАВИТ» по-прежнему понимается
            with mock.patch.object(bot, "claude", lambda p, t, timeout=0, on_tool=None: (True, "НУЖЕН_АЛФАВИТ")):
                self.say(URL)
        self.assertIn("алфавита у меня ещё нет", self.last()[0])
        with mock.patch.object(bot, "GLEB_EXTRA", __file__):
            self.assertNotIn("заполни алфавит", bot.prompt_for(URL, "", False))

    def test_rework_prompt(self):
        # доработка: прошлые файлы ответа, только то, что просит учитель; скрин — только ответы, решение — на листе
        p = bot.prompt_for(URL, "", False)
        self.assertIn(f"Work('horo/tmp/{TASK}')", p)
        self.assertIn("справа от каждого примера только ответ", p)
        self.assertNotIn("расширь картинку", p)
        self.assertIn("reworking", p)
        self.assertIn(f"node horo/shot.js {URL} horo/tmp/{TASK}/shot.png", p)
        self.assertIn("prev*.jpg", p)
        self.assertIn("ТОЛЬКО то, что просит", p)

    def test_handwriting_is_rechecked_for_grade8(self):
        self.assertIn("8 класса", bot.prompt_for(URL, "", False))  # решать методами 8 класса

        def claude(prompt, tools, timeout=0, on_tool=None):
            self.prompts.append(prompt)
            if prompt.startswith("Задание:"):  # решение от руки: draw.py + картинки
                d = drafts.files_dir(TASK)
                os.makedirs(d, exist_ok=True)
                pathlib.Path(d, "1_task.jpg").write_bytes(b"jpg")
                pathlib.Path(drafts.task_dir(TASK), "draw.py").write_text("# решение\n")
                drafts.write(TASK, "Решил № 1–2")
                return True, "Решил № 1–2"
            self.tools = tools
            return True, "Подставил x = 2, 3.\nПРОВЕРКА: исправил № 2 — потерян знак минус"
        with mock.patch.object(bot, "claude", claude):
            self.say(URL)
        check = [p for p in self.prompts if p.startswith("Ты проверяешь")]
        self.assertEqual(len(check), 1)
        self.assertIn("8 класса", check[0])
        self.assertTrue(any(t.endswith("/check.py)") for t in self.tools))
        self.assertTrue(any("Проверка: исправил № 2 — потерян знак минус" in m for m, _ in self.msgs))

    def test_no_recheck_without_handwriting(self):
        self.say(URL)  # обычный черновик без draw.py — без лишней проверки
        self.assertFalse(any(p.startswith("Ты проверяешь") for p in self.prompts))

    def test_errors_are_short(self):
        trace = "Error: page.goto: net::ERR_NAME_NOT_RESOLVED at https://x\n    at open (/root/horo/horo/lib.js:40:11)"
        self.assertEqual(bot.explain(trace), "⚠️ Не получилось: page.goto: net::ERR_NAME_NOT_RESOLVED at https://x")
        self.assertIn("долго не отвечает", bot.explain("TimeoutError: Timeout 30000ms exceeded"))

    def test_every_message_is_valid_telegram_html(self):
        """Все сообщения (они уходят с parse_mode=HTML) — только разрешённые теги, все закрыты."""
        from html.parser import HTMLParser
        allowed = {"b", "i", "u", "s", "a", "code", "pre", "blockquote", "tg-emoji"}

        class Check(HTMLParser):
            def __init__(self):
                super().__init__(); self.stack = []
            def handle_starttag(self, tag, attrs):
                assert tag in allowed, tag
                self.stack.append(tag)
            def handle_endtag(self, tag):
                assert self.stack and self.stack.pop() == tag, tag

        sent = []
        real_tg = self.fake_tg
        def tg(method, **p):
            if method in ("sendMessage", "editMessageText"):
                sent.append(p["text"])
            return real_tg(method, **p)
        with mock.patch.object(bot, "tg", tg), \
             mock.patch.object(bot, "py", lambda *a, timeout=0: (True, "Добавил: а <б>")), \
             mock.patch.object(bot, "download", lambda fid: (b"jpg", "s.jpg")), \
             mock.patch.object(bot, "ALPHA_DIR", self.tmp.name):
            self.say(photo=[{"file_id": "p"}])  # фото без черновика — подсказка
            self.voice("Что <это> & то")        # эхо голосового
            for t in ("/start", "/mode", bot.BTN_HELP, bot.BTN_CHECK, bot.BTN_DO, bot.BTN_SHOW,
                      "что-то непонятное", bot.BTN_MORE, bot.BTN_APP, "/kpp", "/emoji", bot.BTN_HAND):
                self.say(t)
            bot.on_callback({"id": "1", "data": "mode:chat", "message": {"chat": {"id": CHAT}, "message_id": 5}})
            self.say(photo=[{"file_id": "a"}])
            self.say("готово")
            self.say(URL)                      # черновик
            self.say("Новый <текст> & ещё")    # правка с символами HTML
            bot.on_callback({"id": "1", "data": "send", "message": {"chat": {"id": CHAT}, "message_id": 5}})
            for data in ("m:digest", "m:help", "m:more", "all:no"):
                bot.on_callback({"id": "1", "data": data, "message": {"chat": {"id": CHAT}, "message_id": 6}})
        self.assertGreater(len(sent), 15)
        for text in sent:
            c = Check(); c.feed(text); c.close()
            self.assertEqual(c.stack, [], text)
            self.assertNotIn("<текст>", text)  # пользовательский текст экранирован

    def test_hand_style_tuning(self):
        style = os.path.join(self.tmp.name, "hand_style.json")
        with mock.patch.object(bot, "HAND_STYLE", style), mock.patch.object(bot, "GLEB_EXTRA", __file__), \
             mock.patch.object(bot, "py", lambda *a, timeout=0: (True, "")), mock.patch.object(bot, "ALPHA_DIR", self.tmp.name):
            bot.mode[CHAT] = "alphabet"
            self.say("готово")
            self.assertEqual(bot.mode.get(CHAT), "hand_tune")
            self.assertIn("hand:mess:1", self.last()[1])           # кнопки настройки под примером
            bot.on_callback({"id": "1", "data": "hand:mess:1", "message": {"chat": {"id": CHAT}, "message_id": 5}})
            self.say("чуть тоньше и крупнее")
            st = json.loads(pathlib.Path(style).read_text())
            self.assertEqual(st, {"mess": 1.25, "width": 0.9, "size": 1.05})
            self.say("ок")
        self.assertNotIn(CHAT, bot.mode)
        self.assertIn("Запомнил стиль", self.last()[0])

    def test_alphabet_mode(self):
        with mock.patch.object(bot, "py", lambda *a, timeout=0: (True, "Добавил: а б")) as _, \
             mock.patch.object(bot, "download", lambda fid: (b"jpg", "s.jpg")), \
             mock.patch.object(bot, "ALPHA_DIR", self.tmp.name):
            self.say(bot.BTN_HAND)
            self.assertEqual(len(self.files), 3)  # русский, английский, цифры — одинаковые листы
            self.say(photo=[{"file_id": "a"}])
            self.assertTrue(any("Добавил знаков: 2" in m and "а б" in m for m, _ in self.msgs))
            self.say("готово")
        self.assertNotIn(CHAT, bot.mode)

    def test_score_card_and_confetti(self):
        sent = []
        def tg(method, **p):
            if method == "sendMessage":
                sent.append(p)
            if method == "setMessageReaction":
                sent.append({"reaction": p["reaction"]})
            return {"ok": True, "result": {"message_id": 7}}
        with mock.patch.object(bot, "tg", tg), \
             mock.patch.object(bot, "claude", lambda p, t, timeout=0, on_tool=None: (True, "БАЛЛЫ: 7/7\n1. ответ А")):
            bot.handle({"chat": {"id": CHAT}, "from": {"id": CHAT}, "text": URL, "message_id": 3})
        card = [p for p in sent if "text" in p][-1]
        self.assertIn("🏆 <b>7 / 7</b> · 100%", card["text"])
        self.assertNotIn("БАЛЛЫ", card["text"])
        self.assertEqual(card.get("message_effect_id"), bot.EFFECT_CONFETTI)
        self.assertEqual(json.loads([p for p in sent if "reaction" in p][-1]["reaction"]), [{"type": "emoji", "emoji": "🏆"}])
        self.assertEqual(bot.parse_score("БАЛЛЫ: 85%")[0], (85.0, None, 85))
        self.assertEqual(bot.parse_score("Сдал\n**БАЛЛЫ: 6/7**")[0][2], 86)
        self.assertIsNone(bot.parse_score("баллы не показали")[0])
        self.assertEqual(bot.md("**1.** ответ `a<b`\n- пункт\n```\nx\n```"), "<b>1.</b> ответ <code>a&lt;b</code>\n• пункт\nx")

    def test_effect_falls_back(self):
        """Неизвестный эффект Telegram не принял — сообщение уходит без него, с разметкой."""
        import io, urllib.error
        calls = []
        def tg(method, **p):
            calls.append(p)
            if "message_effect_id" in p:
                raise urllib.error.HTTPError("u", 400, "bad effect", {}, io.BytesIO(b""))
            return {"ok": True, "result": {"message_id": 1}}
        with mock.patch.object(bot, "tg", tg):
            self.assertEqual(bot.send(CHAT, "<b>ok</b>", raw_html=True, effect="1"), 1)
        self.assertEqual(calls[-1]["text"], "<b>ok</b>")
        self.assertEqual(calls[-1]["parse_mode"], "HTML")

    def test_split_keeps_quotes_whole(self):
        quote = "<blockquote>" + "\n".join(["строка задания"] * 100) + "</blockquote>"
        parts = bot.split("📋 <b>Надо сделать</b>\n\n" + "\n\n".join([quote] * 4))
        self.assertGreater(len(parts), 1)
        for p in parts:
            self.assertEqual(p.count("<blockquote>"), p.count("</blockquote>"))

    def test_steps_from_tools(self):
        self.assertEqual(bot.step_of("Bash", {"command": "node horo/fetch.js " + URL})[0], "Читаю условие")
        self.assertEqual(bot.step_of("Bash", {"command": "node horo/submit.js u horo/tmp/x/answers.json"})[0], "Сдаю на сайт")
        self.assertEqual(bot.step_of("Write", {"file_path": "horo/tmp/x/answers.json"})[0], "Записываю ответы")
        self.assertEqual(bot.step_of("Read", {"file_path": "horo/tmp/x/q1.png"})[0], "Разглядываю картинки")
        self.assertIsNone(bot.step_of("Glob", {"pattern": "*"}))

    def test_claude_stream(self):
        """Настоящий claude() на поддельном CLI: шаги из потока событий и итоговый текст."""
        d = tempfile.mkdtemp(dir=self.tmp.name)
        events = [
            {"type": "system", "subtype": "init"},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "node horo/fetch.js x"}}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "думаю"}]}},
            {"type": "result", "subtype": "success", "is_error": False, "result": "БАЛЛЫ: 3/4\nготово"},
        ]
        fake = pathlib.Path(d, "claude")
        fake.write_text("#!/bin/sh\ncat <<'EOF'\n" + "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\nEOF\n")
        fake.chmod(0o755)
        tools = []
        next(p for p in self.patches if p.attribute == "claude").stop()  # настоящий claude()
        try:
            with mock.patch.dict(os.environ, {"PATH": d + os.pathsep + os.environ["PATH"]}):
                ok, out = bot.claude("x", [], timeout=20, on_tool=lambda n, i: tools.append((n, i["command"])))
                fake.write_text('#!/bin/sh\necho \'{"type":"result","is_error":true,"result":"Usage limit reached"}\'\n')
                bad = bot.claude("x", [], timeout=20)
        finally:
            next(p for p in self.patches if p.attribute == "claude").start()
        self.assertEqual((ok, out), (True, "БАЛЛЫ: 3/4\nготово"))
        self.assertEqual(tools, [("Bash", "node horo/fetch.js x")])
        self.assertEqual(bad[0], False)
        self.assertIn("лимит", bad[1])

    def test_autotests_confirm_by_button(self):
        rows = [{"auto": True, "rawStatus": "appointed", "url": URL, "title": "Тест <1>", "subj": "Информатика", "icon": "💻"}]
        with mock.patch.object(bot, "node", lambda *a, timeout=0, prog="node": (True, json.dumps(rows))), \
             mock.patch.object(bot, "claude", lambda p, t, timeout=0, on_tool=None: (True, "БАЛЛЫ: 7/7\nвсё")):
            self.say(bot.BTN_ALL)
            self.assertIn("all:yes", self.last()[1])
            self.assertIn("Тест &lt;1&gt;", self.last()[0])
            bot.on_callback({"id": "1", "data": "all:yes", "message": {"chat": {"id": CHAT}, "message_id": 5}})
        self.assertIn("🏆 <b>7/7</b> · Тест &lt;1&gt;", self.last()[0])

    def test_auto_tests_include_reworking(self):
        """«Сдать все автотесты» берёт и «На доработке» (порог не добит), а не только «Пора начать»."""
        rows = [{"auto": True, "rawStatus": "appointed", "url": URL, "title": "Новый", "subj": "Информатика"},
                {"auto": True, "rawStatus": "reworking", "url": URL + "2", "title": "Пароли", "subj": "Информатика"},
                {"auto": True, "rawStatus": "failed", "url": URL + "3", "title": "Старый", "subj": "Информатика"},
                {"auto": False, "rawStatus": "reworking", "url": URL + "4", "title": "Конспект", "subj": "Биология"}]
        with mock.patch.object(bot, "node", return_value=(True, json.dumps(rows))):
            bot.find_auto_tests(CHAT)
        text = self.last()[0]
        self.assertIn("Нашёл автотесты: 2", text)
        self.assertIn("🔁 доработка", text)
        self.assertEqual([t["title"] for t in bot.pending[CHAT]], ["Новый", "Пароли"])
        with mock.patch.object(bot, "overview", return_value={"tasks": rows}), mock.patch.object(bot, "busy_check"), \
                mock.patch.object(bot.threading, "Thread") as th:
            self.assertEqual(bot.app_auto({})["n"], 2)
            self.assertEqual([t["title"] for t in th.call_args.kwargs["args"][1]], ["Новый", "Пароли"])

    def test_old_button_names_still_work(self):
        self.say("📊 Мои оценки")
        self.assertEqual(self.nodes[-1], ("horo/grades.js", "--html"))
        kb = json.loads(bot.menu_kb())
        shown = [b["text"] for b in sum(kb["keyboard"], [])]
        self.assertIn(bot.BTN_GRADES, shown)
        self.assertIn(bot.BTN_MODE, shown)     # чат или приложение — из меню
        self.assertNotIn(bot.BTN_APP, shown)   # в режиме чата приложение не предлагается
        self.assertLessEqual(max(map(len, kb["keyboard"])), 3)  # не больше 3 кнопок в ряд
        self.assertTrue(all(len(t) <= 24 for t in shown))
        for old in (bot.BTN_HELP, bot.BTN_CHECK, "🔕 Сводка", bot.BTN_LATE):  # кнопки старой клавиатуры работают
            self.say(old)
        self.assertEqual(self.nodes[-1], ("horo/status.js", "--late", "--html"))

    def test_more_menu_edits_in_place(self):
        """«⚙️ Ещё»: просрочки, почерк, проверка, помощь; помощь правит то же сообщение. Сводки нет."""
        calls = []
        real = self.fake_tg
        with mock.patch.object(bot, "tg", lambda m, **p: calls.append((m, p)) or real(m, **p)):
            self.say(bot.BTN_MORE)
            menu = self.last()[1]
            for data in ("m:late", "m:help", "m:check", "m:hand", "sup|ask"):  # sup|ask — 🛠 Поддержка
                self.assertIn(data, menu)
            self.assertNotIn("m:digest", menu)  # сводки больше нет: бот сам не пишет
            self.assertFalse(hasattr(bot, "digest_loop"))
            cb = lambda data: bot.on_callback({"id": "1", "data": data, "message": {"chat": {"id": CHAT}, "message_id": 8}})
            cb("m:digest")  # старая кнопка в истории чата — просто показывает меню
            self.assertFalse(bot.load_settings().get("digest"))
            edits = [p for m, p in calls if m == "editMessageText"]
            self.assertEqual(edits[-1]["message_id"], 8)
            self.assertNotIn("Сводка", edits[-1]["reply_markup"])
            cb("m:help")
            self.assertIn("Что я умею", [p for m, p in calls if m == "editMessageText"][-1]["text"])
            cb("m:late")
        self.assertEqual(self.nodes[-1], ("horo/status.js", "--late", "--html"))

    # ---- 💬 чат / 📱 приложение ----
    def cb(self, data, mid=8):
        bot.on_callback({"id": "1", "data": data, "message": {"chat": {"id": CHAT}, "message_id": mid}})

    def test_mode_choice_on_first_start(self):
        """Первый /start — «Как удобнее?»: 💬 В чате / 📱 Приложение. Выбор запоминается; /mode и «⚙️ Режим» меняют."""
        self.say("/start")
        text, markup = self.last()
        self.assertIn("Как удобнее?", text)
        self.assertIn("mode:chat", markup)
        self.assertIn("mode:app", markup)
        self.assertEqual(bot.ui_mode(), "")
        edits = []
        real = self.fake_tg
        with mock.patch.object(bot, "tg", lambda m, **p: edits.append(p) if m == "editMessageText" else real(m, **p)):
            self.cb("mode:chat")
        self.assertEqual(bot.ui_mode(), "chat")
        self.assertIn("Режим: 💬 чат", edits[-1]["text"])      # карточка выбора правится на месте
        text, markup = self.last()                              # и сразу главное меню чата
        self.assertIn("Режим: 💬 чат", text)
        self.assertIn(bot.BTN_DO, markup)
        self.assertNotIn("web_app", markup)
        self.say("/start")
        self.assertIn("Режим: 💬 чат", self.last()[0])
        for t in ("/mode", bot.BTN_MODE):
            self.say(t)
            self.assertIn("Как удобнее?", self.last()[0])
        self.cb("mode:app")
        self.assertEqual(bot.ui_mode(), "app")
        self.cb("mode:xxx")  # неизвестное — ничего не меняет
        self.assertEqual(bot.ui_mode(), "app")

    def test_chat_mode_has_no_app_buttons(self):
        """В режиме «чат» ни одной web_app-кнопки в сообщениях — кроме явной по /app и старой кнопке «✨ Приложение»."""
        bot.set_mode("chat")
        with mock.patch.object(bot, "APP_URL", "https://abc.trycloudflare.com"):
            for t in ("/start", bot.BTN_MORE, bot.BTN_LIST, bot.BTN_HELP, "что-то непонятное", "/prep"):
                self.say(t)
            self.assertFalse(any("web_app" in (m or "") for _, m in self.msgs), self.msgs)
            self.say(bot.BTN_MORE)
            self.assertIn("m:mode", self.last()[1])
            self.say("/app")
            self.assertIn("web_app", self.last()[1])
            self.say(bot.BTN_APP)
            self.assertIn("web_app", self.last()[1])

    def test_app_mode_offers_app(self):
        """В режиме «приложение» главная кнопка — открыть приложение (web_app) и 2–3 действия; нет адреса — клавиатура чата."""
        bot.set_mode("app")
        with mock.patch.object(bot, "APP_URL", "https://abc.trycloudflare.com"):
            self.say("/start")
            text, markup = self.last()
            self.assertIn("Режим: 📱 приложение", text)
            k = json.loads(markup)["keyboard"]
            self.assertEqual(k[0][0]["web_app"]["url"], "https://abc.trycloudflare.com/")
            self.assertLessEqual(len(sum(k, [])), 6)
            self.assertIn(bot.BTN_MODE, markup)
            self.say(bot.BTN_MORE)
            self.assertIn("web_app", self.last()[1])
        with mock.patch.object(bot, "APP_URL", ""):
            self.say("/start")
            self.assertNotIn("web_app", self.last()[1])
            self.assertIn(bot.BTN_DO, self.last()[1])

    def test_mode_from_app(self):
        """«💬 Перейти в чат» в приложении: POST /api/mode -> режим «чат», в чат приходит главное меню."""
        bot.save_settings({"owner": CHAT, "mode": "app"})
        r = bot.ROUTES["/api/mode"]({"mode": "chat"})
        self.assertEqual((r, bot.ui_mode()), ({"mode": "chat"}, "chat"))
        text, markup = self.last()
        self.assertIn("Режим: 💬 чат", text)
        self.assertIn(bot.BTN_DO, markup)
        self.assertNotIn("web_app", markup)
        self.assertEqual(bot.app_me({})["mode"], "chat")
        with self.assertRaises(ValueError):
            bot.ROUTES["/api/mode"]({"mode": "x"})
        n = len(self.msgs)
        bot.ROUTES["/api/mode"]({"mode": "app"})
        self.assertEqual((bot.ui_mode(), len(self.msgs)), ("app", n))  # обратно — молча

    def test_colors_fall_back(self):
        """Telegram не принял цветные кнопки (style) — то же сообщение уходит без цвета, и дальше без него."""
        import io, urllib.error
        calls = []
        def tg(method, **p):
            calls.append(p)
            if '"style"' in (p.get("reply_markup") or ""):
                raise urllib.error.HTTPError("u", 400, "Bad Request", {}, io.BytesIO(b"Bad Request: can't parse inline keyboard button"))
            return {"ok": True, "result": {"message_id": 3}}
        with mock.patch.object(bot, "tg", tg), mock.patch.object(bot, "COLORS", [True]):
            self.assertEqual(bot.send(CHAT, "x", markup=bot.draft_kb(TASK)), 3)
            self.assertNotIn("style", calls[-1]["reply_markup"])
            self.assertIn(f'"send:{TASK}"', calls[-1]["reply_markup"])
            n = len(calls)
            bot.send(CHAT, "y", markup=bot.draft_kb(TASK))
            self.assertEqual(len(calls), n + 1)  # второй раз сразу без цветов
        def html_error(method, **p):
            raise urllib.error.HTTPError("u", 400, "Bad Request", {}, io.BytesIO(b"can't parse entities"))
        with mock.patch.object(bot, "tg", html_error), self.assertRaises(urllib.error.HTTPError):
            bot.call("sendMessage", text="<b>", reply_markup=bot.draft_kb(TASK))  # не про кнопки — ошибка дальше

    def test_planner_install(self):
        tok = "8800000000:AAH" + "x" * 32
        runs = []

        class Resp:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self): return b'{"ok":true,"result":{"username":"plan_bot"}}'

        def fake_run(cmd, **kw):
            runs.append((cmd, kw["env"]))
            return mock.Mock(returncode=0, stdout="ok", stderr="")
        with mock.patch.object(bot.urllib.request, "urlopen", lambda *a, **k: Resp()), \
                mock.patch.object(bot.subprocess, "run", fake_run):
            self.say("/planner")
            self.assertIn("Пришли токен", self.last()[0])
            self.say(tok, message_id=9)
        self.assertTrue(runs[0][0][-1].endswith("planner/install.sh"))
        self.assertEqual(runs[0][1]["PLANNER_TOKEN"], tok)
        self.assertNotIn("TELEGRAM_TOKEN", runs[0][1])  # секреты HDP-бота установке не передаются
        self.assertIn("Планер запущен", self.last()[0])
        self.assertIn("t.me/plan_bot", self.last()[1])
        def bad(*a, **k):
            raise OSError("401")
        with mock.patch.object(bot.urllib.request, "urlopen", bad), mock.patch.object(bot.subprocess, "run", fake_run):
            self.say("/planner 1234567:" + "y" * 40)  # неверный токен — установка не запускается
        self.assertEqual(len(runs), 1)
        self.assertIn("не подошёл", self.last()[0])

    def test_kpp_install(self):
        tok = "8600000000:AAG" + "x" * 32
        runs = []

        class Resp:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self): return b'{"ok":true,"result":{"username":"kpp_bot"}}'

        def fake_run(cmd, **kw):
            runs.append((cmd, kw["env"]))
            return mock.Mock(returncode=0, stdout="ok", stderr="")
        with mock.patch.object(bot.urllib.request, "urlopen", lambda *a, **k: Resp()), \
                mock.patch.object(bot.subprocess, "run", fake_run):
            self.say("/kpp")
            self.assertIn("КПП", self.last()[0])
            self.say("/kpp " + tok, message_id=9)
        self.assertTrue(runs[0][0][-1].endswith("kpp/install.sh"))
        self.assertEqual(runs[0][1]["KPP_TOKEN"], tok)
        self.assertNotIn("PLANNER_TOKEN", runs[0][1])
        self.assertIn("КПП запущен", self.last()[0])
        self.assertIn("t.me/kpp_bot", self.last()[1])

    def test_coach_install(self):
        tok = "8730000000:AAH" + "z" * 32
        runs = []

        class Resp:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self): return b'{"ok":true,"result":{"username":"TrainerWhoopbot"}}'

        def fake_run(cmd, **kw):
            runs.append((cmd, kw["env"]))
            return mock.Mock(returncode=0, stdout="ok", stderr="")
        with mock.patch.object(bot.urllib.request, "urlopen", lambda *a, **k: Resp()), \
                mock.patch.object(bot.subprocess, "run", fake_run):
            self.say("/coach")
            self.assertIn("тренера", self.last()[0])
            self.say("/coach " + tok, message_id=9)
        self.assertTrue(runs[0][0][-1].endswith("coach/install.sh"))
        self.assertEqual(runs[0][1]["COACH_TOKEN"], tok)
        self.assertIn("Тренер запущен", self.last()[0])

    def test_hub_install(self):
        tok = "8960000000:AAF" + "h" * 32
        runs = []

        class Resp:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self): return b'{"ok":true,"result":{"username":"Setingss111bot"}}'

        def fake_run(cmd, **kw):
            runs.append((cmd, kw["env"]))
            return mock.Mock(returncode=0, stdout="ok", stderr="")
        with mock.patch.object(bot.urllib.request, "urlopen", lambda *a, **k: Resp()), \
                mock.patch.object(bot.subprocess, "run", fake_run):
            self.say("/hub " + tok, message_id=9)
        cmd, env = runs[0]
        self.assertTrue(cmd[-1].endswith("hub/install.sh"))
        self.assertEqual((env["HUB_TOKEN"], env["HUB_REMOTE"]), (tok, "1"))
        self.assertTrue(env["HUB_OWNER"].isdigit())
        self.assertIn("Главный запущен", self.last()[0])

    # ---- проверка «до конца»: отмена, устаревшие и чужие кнопки, сбои ----
    def all_calls(self):
        s = bot.load_settings(); s["owner"] = CHAT; bot.save_settings(s)  # on_update пускает только владельца
        calls, real = [], self.fake_tg
        return calls, mock.patch.object(bot, "tg", lambda m, **p: calls.append((m, p)) or real(m, **p))

    def test_waiting_cards_can_be_cancelled(self):
        """Карточка «жду ссылку / ТЗ / эмодзи» — с кнопкой «✕ Отмена»: бот перестаёт ждать, сообщение правится."""
        calls, patch = self.all_calls()
        with patch:
            self.say(bot.BTN_SHOW)
            self.assertIn(bot.CANCEL, self.last()[1])
            self.assertEqual(bot.mode.get(CHAT), "show")
            self.cb(bot.CANCEL, mid=12)
            self.assertNotIn(CHAT, bot.mode)
            self.assertIn("Отменил", [p for m, p in calls if m == "editMessageText"][-1]["text"])
            self.assertTrue(any(m == "answerCallbackQuery" for m, _ in calls))
            self.say("/emoji")
            self.assertIn(bot.CANCEL, self.last()[1])

    def test_show_mode_does_not_stick(self):
        """«👀 Только ответы», потом другая кнопка — следующая ссылка решается и сдаётся, а не только показывается."""
        self.say(bot.BTN_SHOW)
        with mock.patch.object(bot, "run_script", lambda *a: None):
            self.say(bot.BTN_LIST)
        self.assertNotIn(CHAT, bot.mode)
        with mock.patch.object(bot, "run_task", lambda chat, url, text, show=False, mid=None: self.prompts.append(show)):
            self.say(URL)
        self.assertEqual(self.prompts[-1], False)

    def test_draft_is_not_sent_twice(self):
        """Двойное «Отправить учителю» (или чат + приложение сразу): answer.js — один раз."""
        bot.set_draft({"task": TASK, "url": URL, "title": "Конспект §5", "pairs": [], "own_files": False}, active=True)
        drafts.write(TASK, "текст")
        bot.SENDING.add(TASK)  # первое нажатие ещё отправляет
        try:
            self.cb("send:" + TASK)
        finally:
            bot.SENDING.discard(TASK)
        self.assertEqual(self.nodes, [])
        self.assertIn("Уже отправляю", self.last()[0])
        self.cb("send:" + TASK)
        self.assertEqual([a[0] for a in self.nodes], ["horo/answer.js"])

    def test_stale_and_foreign_buttons_are_answered(self):
        """Чужая кнопка, кнопка без сообщения, старая кнопка почерка, повторное «Сдать все» — ответ, без падений."""
        calls, patch = self.all_calls()
        with patch:
            bot.on_update({"callback_query": {"id": "9", "data": "send", "from": {"id": 777},
                                              "message": {"chat": {"id": 777}, "message_id": 1}}})
            bot.on_update({"callback_query": {"id": "10", "data": "send", "from": {"id": CHAT}}})
            answers = [p for m, p in calls if m == "answerCallbackQuery"]
            self.assertEqual([a["callback_query_id"] for a in answers], ["9", "10"])
            self.assertIn("личный", answers[0]["text"])
            self.assertIn("устарела", answers[1]["text"])
            self.cb("hand:slant2:1")
            self.assertIn("Кнопка устарела", self.last()[0])
            n = len(self.msgs)
            bot.ALL_RUNNING.add(CHAT)
            try:
                self.cb("all:yes", mid=30)  # пачка уже сдаётся — «не сдаю» не пишем
            finally:
                bot.ALL_RUNNING.discard(CHAT)
            self.assertEqual(len(self.msgs), n)
            self.assertNotIn("не сдаю", str([p for m, p in calls if m == "editMessageText" and p["message_id"] == 30]))

    def edit_with(self, *errors):
        """Правка сообщения, на которую Telegram отвечает перечисленными ошибками 400 (потом — успехом)."""
        import io
        import urllib.error
        calls, queue = [], list(errors)

        def tg(method, **p):
            calls.append((method, p))
            if method == "editMessageText" and queue:
                raise urllib.error.HTTPError("u", 400, "Bad Request", {}, io.BytesIO(
                    json.dumps({"ok": False, "error_code": 400, "description": queue.pop(0)}).encode()))
            return {"ok": True, "result": {"message_id": 99}}
        return calls, mock.patch.object(bot, "tg", tg)

    def test_edit_failures_are_soft(self):
        """editMessageText 400: «не изменено» — молча; разметка не принята — тот же текст без неё; сообщения
        нет — новое сообщение; у живой карточки (resend=False) новое не шлём."""
        logs = []
        with mock.patch("builtins.print", lambda *a: logs.append(a)):
            calls, patch = self.edit_with("Bad Request: message is not modified: specified new message content")
            with patch:
                bot.edit(CHAT, 7, "<b>то же</b>")
            self.assertEqual([m for m, _ in calls], ["editMessageText"])
            self.assertEqual(logs, [])  # ничего в лог

            calls, patch = self.edit_with("Bad Request: can't parse entities: Unsupported start tag \"x\" at byte offset 3")
            with patch:
                bot.edit(CHAT, 7, "<b>жирный</b> и <x>тег", bot.kb([("Ок", "m:more")]))
            self.assertEqual([m for m, _ in calls], ["editMessageText", "editMessageText"])
            self.assertNotIn("parse_mode", calls[1][1])
            self.assertEqual(calls[1][1]["text"], "жирный и тег")
            self.assertIn("m:more", calls[1][1]["reply_markup"])  # кнопки остались

            calls, patch = self.edit_with("Bad Request: message to edit not found")
            with patch:
                bot.edit(CHAT, 7, bot.card("Готово", icon="✅"), bot.kb([("Ок", "m:more")]))
            self.assertEqual([m for m, _ in calls], ["editMessageText", "sendMessage"])
            self.assertIn("Готово", calls[1][1]["text"])
            self.assertIn("m:more", calls[1][1]["reply_markup"])

            calls, patch = self.edit_with("Bad Request: message to edit not found")
            with patch:
                bot.edit(CHAT, 7, "статус", resend=False)  # живая карточка уже удалена — не воскрешаем
            self.assertEqual([m for m, _ in calls], ["editMessageText"])
            self.assertEqual(logs, [])
            with patch:  # удалить то, чего нет, и снять кнопки у удалённого — тоже тихо
                pass
            calls, patch = self.edit_with()
            with patch:
                bot.edit(CHAT, 7, "x" * 9000)  # длиннее лимита — режется, а не 400
            self.assertLessEqual(len(calls[0][1]["text"]), 4096)

    def test_live_card_is_not_edited_after_done(self):
        """Тикер карточки не правит сообщение, которое уже удалено (иначе в логе 400 «message to edit not found»)."""
        calls, patch = self.edit_with()
        with patch:
            live = bot.Live(CHAT, "Решаю", progress=False)
            n = len(calls)
            live.refresh()
            self.assertEqual(len(calls), n + 1)
            live.done()
            calls_after = len(calls)
            live.refresh()
            live.step("ещё шаг", 0.5)
        self.assertEqual(len(calls), calls_after)
        self.assertEqual(calls[-1][0], "deleteMessage")

    def test_app_shows_progress_while_slots_are_free(self):
        """Приложение показывает живой ход, когда решается хоть одно задание (раньше — только когда заняты все 3 места)."""
        self.assertEqual((bot.app_state()["working"], bot.app_state()["live"]), (False, None))
        calls, patch = self.edit_with()
        with patch:
            live = bot.Live(CHAT, "Решаю задание", "Тест", progress=False)
            bot.lock.acquire()  # одно задание из трёх
            try:
                st = bot.app_state()
                self.assertEqual((st["busy"], st["working"], st["others"]), (False, True, 0))
                self.assertEqual(st["live"]["title"], "Решаю задание")
                second = bot.Live(CHAT, "Второе", progress=False)
                self.assertEqual(bot.app_state()["others"], 1)
                second.done()
                self.assertEqual(bot.app_state()["live"]["title"], "Решаю задание")  # осталось первое
            finally:
                bot.lock.release()
                live.done()
        self.assertEqual((bot.app_state()["working"], bot.app_state()["live"]), (False, None))

    def test_children_do_not_get_the_bot_token(self):
        """Скрипты и Claude запускаются без токена бота (задание с сайта — чужой текст)."""
        with mock.patch.dict(bot.os.environ, {"TELEGRAM_TOKEN": "secret", "HORO_LOGIN": "x"}):
            env = bot.child_env(A="1")
        self.assertNotIn("TELEGRAM_TOKEN", env)
        self.assertEqual((env["HORO_LOGIN"], env["A"]), ("x", "1"))

    def test_due_send_uses_fresh_draft(self):
        """Отложенная отправка берёт свежий черновик: отменили («hold») — не уходит."""
        bot.set_draft({"task": TASK, "url": URL, "title": "Т", "pairs": [], "own_files": False, "send_at": 100}, active=True)
        d = bot.draft(TASK)
        d.pop("send_at")
        bot.set_draft(d)
        bot.due_sends(now=200)
        self.assertEqual(self.nodes, [])

    def test_old_inbox_is_cleaned(self):
        import time
        old, new = os.path.join(bot.INBOX, "old"), os.path.join(bot.INBOX, "new")
        os.makedirs(old)
        os.makedirs(new)
        pathlib.Path(old, "a.jpg").write_bytes(b"x")
        t = time.time() - 20 * 86400
        os.utime(old, (t, t))
        self.assertEqual(bot.clean_inbox(14), 1)
        self.assertEqual((os.path.exists(old), os.path.exists(new)), (False, True))

    def test_time_words_are_bounded(self):
        self.assertIsNone(bot.parse_time("через 99999 ч"))
        self.assertIsNotNone(bot.parse_time("через 2 ч"))

    def test_crash_is_reported_not_silent(self):
        """Сбой в обработчике: крутилка у кнопки снимается, Глебу — «⚠️ Что-то сломалось», а не тишина."""
        calls, patch = self.all_calls()
        with patch, mock.patch.object(bot, "on_more", mock.Mock(side_effect=RuntimeError("boom"))), \
                mock.patch.object(bot, "handle", mock.Mock(side_effect=KeyError("x"))), \
                mock.patch("builtins.print"):
            bot.on_update({"callback_query": {"id": "5", "data": "m:help", "from": {"id": CHAT},
                                              "message": {"chat": {"id": CHAT}, "message_id": 3}}})
            self.assertIn("5", [p["callback_query_id"] for m, p in calls if m == "answerCallbackQuery"])
            self.assertIn("⚠️ Что-то сломалось: boom", self.last()[0])
            bot.on_update({"message": {"chat": {"id": CHAT}, "from": {"id": CHAT}, "text": "привет"}})
            self.assertIn("⚠️ Что-то сломалось", self.last()[0])
            args = mock.Mock(exc_type=ValueError, exc_value=ValueError("фон упал"), exc_traceback=None, thread=None)
            with mock.patch.object(bot.threading, "__excepthook__", lambda a: None):
                bot.thread_crash(args)
            self.assertIn("фон упал", self.last()[0])


class AppTest(BotTest):
    """Приложение (Mini App): подпись Telegram, доступ только владельцу, действия те же, что у кнопок."""

    def init_data(self, uid=CHAT, token="test", age=0):
        import hashlib, hmac, time, urllib.parse
        pairs = {"auth_date": str(int(time.time()) - age), "query_id": "q", "user": json.dumps({"id": uid, "first_name": "Глеб"})}
        secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        pairs["hash"] = hmac.new(secret, "\n".join(f"{k}={v}" for k, v in sorted(pairs.items())).encode(), hashlib.sha256).hexdigest()
        return urllib.parse.urlencode(pairs)

    def test_init_data_signature(self):
        self.assertEqual(web.check_init_data(self.init_data(), "test")["id"], CHAT)
        self.assertIsNone(web.check_init_data(self.init_data(token="other"), "test"))  # подпись чужим токеном
        self.assertIsNone(web.check_init_data(self.init_data(age=5 * 86400), "test"))  # устарело
        forged = self.init_data().replace("%3A+42", "%3A+43")  # подменили id пользователя
        self.assertNotEqual(forged, self.init_data())
        self.assertIsNone(web.check_init_data(forged, "test"))
        self.assertIsNone(web.check_init_data("", "test"))

    def test_server_lets_only_owner_in(self):
        import http.client
        from http.server import ThreadingHTTPServer
        bot.save_settings({"owner": CHAT})
        web._cfg.update(token="test", owner=bot.owner, routes=bot.ROUTES, version="t")
        srv = ThreadingHTTPServer(("127.0.0.1", 0), web.Web)
        bot.REAL_THREAD(target=srv.serve_forever, daemon=True).start()  # threading.Thread в тестах синхронный
        try:
            def get(path, data=None):
                c = http.client.HTTPConnection("127.0.0.1", srv.server_port, timeout=10)
                c.request("GET", path, headers={"X-Init-Data": data} if data else {})
                r = c.getresponse()
                return r.status, r.read()
            self.assertEqual(get("/api/state")[0], 401)
            self.assertEqual(get("/api/state", self.init_data(uid=7))[0], 403)
            code, body = get("/api/state", self.init_data())
            self.assertEqual((code, json.loads(body)["busy"]), (200, False))
            self.assertEqual(get("/")[0], 200)
            self.assertEqual(get("/app.js")[0], 200)
            self.assertEqual(get("/../bot.py")[0], 404)
            self.assertEqual(get("/%2e%2e%2fbot.py")[0], 404)
            self.assertEqual(get("/api/auto", self.init_data())[0], 405)  # действия — не по GET
            self.assertEqual(get("/api/draft/send", self.init_data())[0], 405)
            self.assertEqual(get("/api/overview", self.init_data())[0], 200)  # чтение — по GET
            self.assertEqual(get("/api/auto", None)[0], 405)  # 405 и без входа: даже не пробуем

            def post(path, body, uid=CHAT):
                c = http.client.HTTPConnection("127.0.0.1", srv.server_port, timeout=10)
                c.request("POST", path, json.dumps(body), {"X-Init-Data": self.init_data(uid=uid),
                                                           "Content-Type": "application/json"})
                r = c.getresponse()
                return r.status, json.loads(r.read() or b"{}")
            # 🛠 поддержка из приложения: тот же Support, start — заглушка; скриншот больше 1 МБ проходит
            import base64
            import support
            sup = support.Support.__new__(support.Support)
            sup.allowed, sup.media_dir, calls = (lambda u: u == bot.owner()), tempfile.mkdtemp(), []
            sup.start = lambda chat, text, shots=None: calls.append((chat, text, shots))
            self.assertEqual(post("/api/support", {"text": "x"})[0], 400)  # ещё не запущена
            with mock.patch.object(bot, "SUP", sup):
                shot = base64.b64encode(b"\xff\xd8" + bytes(1_100_000)).decode()
                self.assertEqual(post("/api/support", {"text": "черновик не открывается", "photo": shot}),
                                 (200, {"ok": True}))
                self.assertEqual((calls[-1][0], os.path.exists(calls[-1][2][0])), (CHAT, True))
                self.assertEqual(post("/api/support", {"text": "x"}, uid=7)[0], 403)
                self.assertEqual(post("/api/support", {"text": ""})[0], 400)
            self.assertEqual(len(calls), 1)
        finally:
            srv.shutdown()

    def test_solve_from_app_goes_like_link(self):
        bot.save_settings({"owner": CHAT})
        with self.assertRaises(ValueError):
            bot.app_solve({"url": "https://example.com"})
        self.assertEqual(bot.app_solve({"url": URL, "show": False}), {"ok": True})
        self.assertIn("horo/tmp/" + TASK + "/draft.txt", self.prompts[0])  # тот же run_task, что и по ссылке в чате
        st = bot.app_state()
        self.assertEqual(st["draft"]["text"], "Клетка делится митозом.\nЭто важно.")
        self.assertTrue(st["last"]["ok"] and st["last"]["draft"])
        self.assertEqual(self.reads, [("horo/overview.js",)])  # список в приложении обновился
        # правка текста в приложении запоминается как правка, отправка — тот же answer.js
        bot.ROUTES["/api/draft/save"]({"text": "Клетка делится митозом."})
        self.assertEqual(drafts.read(TASK), "Клетка делится митозом.")
        bot.ROUTES["/api/draft/send"]({})
        self.assertEqual(self.nodes[-1][:2], ("horo/answer.js", URL))
        self.assertIsNone(bot.draft())
        self.assertEqual(bot.LAST["kind"], "sent")

    def test_busy_and_autotests(self):
        bot.save_settings({"owner": CHAT})
        for _ in range(bot.MAX_PARALLEL):
            bot.lock.acquire()
        try:
            calls = []
            with mock.patch.object(bot, "run_task", lambda *a: calls.append(a)):
                bot.app_solve({"url": URL})  # все места заняты — не ошибка: задание встанет в очередь
            self.assertEqual(calls[0][1], URL)
            self.assertTrue(bot.app_state()["busy"])
            with self.assertRaises(ValueError):
                bot.busy_check()
        finally:
            for _ in range(bot.MAX_PARALLEL):
                bot.lock.release()
        with self.assertRaises(ValueError):
            bot.app_auto({})  # списка ещё нет — сдавать нечего
        pathlib.Path(bot.OVERVIEW).write_text(json.dumps({"at": 1, "tasks": [
            {"auto": True, "rawStatus": "appointed", "url": URL, "title": "Тест", "subj": "Информатика"},
            {"auto": True, "rawStatus": "failed", "url": URL + "x", "title": "Старый", "subj": "Информатика"}]}))
        self.assertEqual(bot.app_auto({})["n"], 1)
        self.assertIn("Автотесты сданы", self.msgs[-1][0])

    def test_overview_refresh_and_menu_button(self):
        r = bot.app_overview({})
        self.assertTrue(r["loading"] or bot.overview())  # данных не было — пошёл за ними
        self.assertEqual(bot.overview()["at"], 1)
        bot.save_settings({"owner": CHAT})
        calls = []
        with mock.patch.object(bot, "tg", lambda m, **p: calls.append((m, p)) or {"ok": True, "result": []}):
            bot.on_app_url("https://abc.trycloudflare.com")
            bot.handle({"chat": {"id": CHAT}, "from": {"id": CHAT}, "text": "/app"})
        m, p = calls[0]
        self.assertEqual((m, p["chat_id"]), ("setChatMenuButton", CHAT))
        self.assertEqual(json.loads(p["menu_button"])["web_app"]["url"], "https://abc.trycloudflare.com/")
        self.assertIn("web_app", calls[-1][1]["reply_markup"])



for _name in dir(BotTest):  # AppTest берёт у BotTest только заготовки, его тесты второй раз не гоняем
    if _name.startswith("test_") and _name not in AppTest.__dict__:
        setattr(AppTest, _name, None)

class SiteTest(BotTest):
    """🌐 Сайт в браузере: вход по одноразовой ссылке из бота, cookie, то же API, теория темы."""

    def setUp(self):
        super().setUp()
        self.sp = mock.patch.object(web, "SESSIONS", os.path.join(self.tmp.name, "site_sessions.json"))
        self.sp.start()
        bot.save_settings({"owner": CHAT})

    def tearDown(self):
        self.sp.stop()
        super().tearDown()

    def test_link_works_once_and_session_remembered(self):
        code = web.site_code()
        token = web.site_login(code)
        self.assertTrue(token and web.site_check(token))
        self.assertIsNone(web.site_login(code))            # ссылка одноразовая
        self.assertFalse(web.site_check(code))              # код — не сессия
        self.assertFalse(web.site_check("") or web.site_check("chuzhoy"))
        self.assertNotIn(token, pathlib.Path(web.SESSIONS).read_text())  # в файле только хэши
        with mock.patch.object(web.time, "time", lambda: 10 ** 12):
            self.assertFalse(web.site_check(token))         # через 30 дней вход забыт
        web.site_logout(token)
        self.assertFalse(web.site_check(token))
        late = web.site_code()
        with mock.patch.object(web.time, "time", lambda: 10 ** 12):
            self.assertIsNone(web.site_login(late))          # ссылка живёт 15 минут

    def test_server_site_login_and_api(self):
        import http.client
        from http.server import ThreadingHTTPServer
        web._cfg.update(token="test", owner=bot.owner, routes=bot.ROUTES, version="t", site_version="s")
        srv = ThreadingHTTPServer(("127.0.0.1", 0), web.Web)
        bot.REAL_THREAD(target=srv.serve_forever, daemon=True).start()
        try:
            def req(method, path, headers=None, body=None):
                c = http.client.HTTPConnection("127.0.0.1", srv.server_port, timeout=10)
                c.request(method, path, body, headers or {})
                r = c.getresponse()
                return r.status, r.getheader("Location"), r.getheader("Set-Cookie"), r.read()
            self.assertEqual(req("GET", "/site/")[0], 200)
            self.assertEqual(req("GET", "/site/site.js")[0], 200)
            self.assertEqual(req("GET", "/site/../bot.py")[0], 404)
            self.assertEqual(req("GET", "/", {"Host": "hdp.1-2-3-4.sslip.io"})[1], "/student")  # зеркало платформы
            self.assertEqual(req("GET", "/api/state", {"X-Site": "1"})[0], 401)
            # плохая ссылка — без входа
            st, loc, cookie, _ = req("GET", "/site/login?c=nope")
            self.assertEqual((st, loc, cookie), (302, "/site/#login-expired", None))
            st, loc, cookie, _ = req("GET", "/site/login?c=" + web.site_code())
            self.assertEqual((st, loc), (302, "/site/"))
            self.assertIn("HttpOnly", cookie)
            self.assertIn("Secure", cookie)
            self.assertIn("SameSite=Lax", cookie)
            jar = cookie.split(";")[0]
            code, _, _, body = req("GET", "/api/state", {"Cookie": jar, "X-Site": "1"})
            self.assertEqual((code, json.loads(body)["busy"]), (200, False))
            # без X-Site cookie не действует: чужая страница не может нажать «Сделать» от имени Глеба
            self.assertEqual(req("POST", "/api/solve", {"Cookie": jar}, json.dumps({"url": URL}))[0], 401)
            self.assertEqual(req("GET", "/api/state", {"Cookie": "hdp_site=forged", "X-Site": "1"})[0], 401)
            st, _, cookie, _ = req("GET", "/site/logout", {"Cookie": jar})
            self.assertIn("Max-Age=0", cookie)
            self.assertEqual(req("GET", "/api/state", {"Cookie": jar, "X-Site": "1"})[0], 401)
        finally:
            srv.shutdown()

    def test_site_command_sends_one_time_link(self):
        with mock.patch.dict(os.environ, {"HORO_SITE_URL": "https://hdp.example"}):
            self.say("/site")
            self.say("/start site")
        links = [m for m in self.msgs if "hdp.example/site/login?c=" in json.dumps(m, ensure_ascii=False)]
        self.assertEqual(len(links), 2)

    def test_theory_cached_and_uuid_checked(self):
        calls = []
        items = [{"title": "Pretérito perfecto", "text": "haber + participio"}]
        with mock.patch.object(bot, "SITE_DIR", os.path.join(self.tmp.name, "site")), \
                mock.patch.object(bot, "node", lambda *a, timeout=0: (calls.append(a), (True, json.dumps(items)))[1]):
            with self.assertRaises(ValueError):
                bot.app_site_theory({"uuid": "../../etc"})
            self.assertEqual(bot.app_site_theory({"uuid": TASK}), {"items": items})
            self.assertEqual(bot.app_site_theory({"uuid": TASK}), {"items": items})
            self.assertEqual(calls, [("horo/prep.js", "--theory", TASK)])  # второй раз — из кэша

    def test_running_shows_tasks_in_work(self):
        bot.running.add(TASK)
        try:
            self.assertEqual(bot.app_site_running({})["running"], [TASK])
        finally:
            bot.running.discard(TASK)


class AgentTest(BotTest):
    """/agent ТОКЕН -> /etc/tg-agent.env (только токен, старые ключи сохраняются) -> tgbot/install.sh."""

    TOK = "123456789:AAEn29QpYOZSANEg5Asv-eh9KlE3ASPVYtgXXXX"

    def start_agent(self, old_env=""):
        self.runs, self.deleted = [], []
        self.env = os.path.join(self.tmp.name, "tg-agent.env")
        if old_env:
            with open(self.env, "w") as f:
                f.write(old_env)
        getme = json.dumps({"ok": True, "result": {"username": "secretary_bot"}}).encode()
        patches = [
            mock.patch.object(bot, "AGENT_ENV", self.env),
            mock.patch.object(bot.urllib.request, "urlopen", lambda *a, **k: __import__("io").BytesIO(getme)),
            mock.patch.object(bot, "forget", lambda chat, mid: self.deleted.append(mid)),
            mock.patch.object(bot.subprocess, "run", lambda cmd, **k: self.runs.append(cmd) or mock.Mock(
                returncode=0, stdout="ok", stderr="")),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def say(self, text, mid=1):
        bot.handle({"chat": {"id": CHAT}, "from": {"id": CHAT}, "text": text, "message_id": mid})

    def env_text(self):
        with open(self.env) as f:
            return f.read()

    def test_one_message_installs(self):
        self.start_agent()
        self.say(f"/agent {self.TOK} 79138434865", mid=20)
        self.assertEqual(self.deleted, [20])  # токен убран из чата
        text = self.env_text()
        self.assertIn(f"TG_AGENT_TOKEN={self.TOK}\n", text)
        self.assertIn("TG_API_ID=\n", text)  # ключи не нужны для запуска
        self.assertEqual(oct(os.stat(self.env).st_mode & 0o777), "0o600")
        self.assertTrue(self.runs[-1][-1].endswith("tgbot/install.sh"))
        self.assertIn("Секретарь запущен", self.msgs[-1][0])
        self.assertNotIn(CHAT, bot.mode)

    def test_token_in_next_message_and_old_keys_kept(self):
        self.start_agent("TG_AGENT_TOKEN=old\nTG_API_ID=555\nTG_API_HASH=abc\nTG_ALLOWED_ID=42\n")
        self.say("/agent")
        self.assertEqual(bot.mode.get(CHAT), "agent_token")
        self.say(self.TOK, mid=7)
        text = self.env_text()
        self.assertIn("TG_API_ID=555\nTG_API_HASH=abc\n", text)
        self.assertIn(f"TG_AGENT_TOKEN={self.TOK}", text)

    def test_with_hub_no_token_needed(self):
        """Есть Главный — Секретарь его часть: /agent ничего не ставит и не просит токен."""
        self.start_agent()
        hub_env = os.path.join(self.tmp.name, "hub-bot.env")
        with open(hub_env, "w") as f:
            f.write("HUB_TOKEN=1:x\n")
        with mock.patch.object(bot, "HUB_ENV", hub_env):
            self.say(f"/agent {self.TOK}", mid=9)
        self.assertEqual(self.deleted, [9])
        self.assertIn("часть Главного", self.msgs[-1][0])
        self.assertEqual(self.runs, [])
        self.assertNotIn(CHAT, bot.mode)

    def test_cancel_and_this_bot_token(self):
        self.start_agent()
        self.say("/agent")
        with mock.patch.object(bot, "TOKEN", self.TOK):
            self.say(self.TOK)
        self.assertIn("не этого бота", self.msgs[-1][0])
        self.say("/cancel")
        self.assertNotIn(CHAT, bot.mode)
        self.assertEqual(self.runs, [])


for _name in list(vars(BotTest)):
    if _name.startswith("test_") and _name not in AgentTest.__dict__:
        setattr(AgentTest, _name, None)


class ParallelTest(BotTest):
    """Несколько заданий сразу: до MAX_PARALLEL одновременно, остальные в очереди, черновиков — сколько угодно, каждый правится."""
    URL2 = URL.replace("2222-222222222222", "2222-333333333333")
    TASK2 = "22222222-2222-2222-2222-333333333333"

    def test_two_tasks_run_at_the_same_time(self):
        import threading
        both = threading.Barrier(2, timeout=10)  # дойдут оба — значит, решаются одновременно

        def solve(url, text, show, on_tool=None):
            both.wait()
            return True, "Баллы 5/5"
        with mock.patch.object(bot, "solve", solve):
            ts = [bot.REAL_THREAD(target=bot.run_task, args=(CHAT, u, "")) for u in (URL, self.URL2)]
            for t in ts:
                t.start()
            for t in ts:
                t.join(15)
        self.assertFalse(both.broken)
        self.assertEqual(sum("Баллы 5/5" in m for m, _ in self.msgs), 2)
        self.assertFalse(any("Занят" in m for m, _ in self.msgs))
        self.assertEqual(bot.lock.busy(), 0)

    def test_queue_when_all_slots_busy_and_same_task_twice(self):
        import threading
        go = threading.Event()

        def solve(url, text, show, on_tool=None):
            go.wait(10)
            return True, "Баллы 5/5"
        with mock.patch.object(bot, "lock", bot.Slots(1)), mock.patch.object(bot, "solve", solve):
            first = bot.REAL_THREAD(target=bot.run_task, args=(CHAT, URL, ""))
            first.start()
            for _ in range(100):
                if bot.lock.busy():
                    break
                threading.Event().wait(0.02)
            bot.run_task(CHAT, URL, "")  # то же задание ещё раз — не запускаем
            self.assertIn("уже решаю", self.msgs[-1][0])
            second = bot.REAL_THREAD(target=bot.run_task, args=(CHAT, self.URL2, ""))
            second.start()
            for _ in range(100):
                if any("В очереди" in m for m, _ in self.msgs):
                    break
                threading.Event().wait(0.02)
            self.assertTrue(any("В очереди" in m for m, _ in self.msgs))
            go.set()
            first.join(10)
            second.join(10)
        self.assertEqual(sum("Баллы 5/5" in m for m, _ in self.msgs), 2)

    def task2(self):
        os.makedirs(drafts.task_dir(self.TASK2), exist_ok=True)
        with open(os.path.join(drafts.task_dir(self.TASK2), "task.json"), "w") as f:
            json.dump({"data": {"type": "detailedAnswer", "title": "Конспект §6"}}, f)
        drafts.write(self.TASK2, "Митоз — деление клетки.\nМейоз — деление половых клеток.")

    def numbered(self):
        """tg, у которого у каждого отправленного сообщения свой message_id (для ответов на карточку)."""
        n = iter(range(100, 10**6))

        def fake(method, **p):
            r = self.fake_tg(method, **p)
            return {"ok": True, "result": {"message_id": next(n)}} if method == "sendMessage" else r
        return mock.patch.object(bot, "tg", fake)

    def test_several_drafts_each_editable(self):
        self.task2()
        with self.numbered():
            bot.offer_draft(CHAT, TASK, URL)
            card1 = bot.draft(TASK)["mids"][-1]
            bot.offer_draft(CHAT, self.TASK2, self.URL2)
        self.assertEqual({d["task"] for d in bot.all_drafts()}, {TASK, self.TASK2})  # оба ждут, никто не в очереди
        self.assertFalse(any("в очереди" in m.lower() for m, _ in self.msgs))
        self.assertIn(f"send:{self.TASK2}", self.last()[1])  # у каждой карточки свои кнопки
        self.assertIn("черновиков: 2", self.last()[0])
        # правка без ответа — в последний показанный, ответом на карточку — в тот, на который ответил
        self.say("убери второе")
        self.assertIn("Конспект §6", self.prompts[-1])
        self.assertIn(self.TASK2 + "/draft.txt", self.prompts[-1])
        self.say("убери второе", reply_to_message={"message_id": card1})
        self.assertIn(TASK + "/draft.txt", self.prompts[-1])
        self.assertEqual(bot.draft()["task"], TASK)  # теперь текущий — он
        # «✏️ Править» делает черновик текущим, кнопки не пропадают
        bot.on_callback({"id": "1", "data": f"ed:{self.TASK2}", "message": {"chat": {"id": CHAT}, "message_id": 7}})
        self.assertEqual(bot.draft()["task"], self.TASK2)
        self.assertIn("Что поправить?", self.last()[0])
        # отправка одного не трогает другой
        bot.on_callback({"id": "1", "data": f"send:{TASK}", "message": {"chat": {"id": CHAT}, "message_id": 5}})
        self.assertEqual(self.nodes[-1][:2], ("horo/answer.js", URL))
        self.assertEqual([d["task"] for d in bot.all_drafts()], [self.TASK2])
        bot.on_callback({"id": "1", "data": f"drop:{self.TASK2}", "message": {"chat": {"id": CHAT}, "message_id": 6}})
        self.assertIsNone(bot.draft())
        self.assertEqual(len(self.nodes), 1)

    def test_old_settings_with_queue_are_kept(self):
        self.task2()
        bot.save_settings({"draft": {"task": TASK, "url": URL, "title": "Конспект §5", "pairs": [], "own_files": False},
                           "draft_queue": [{"task": self.TASK2, "url": self.URL2}]})
        self.assertEqual(bot.draft()["task"], TASK)
        self.assertEqual([d["title"] for d in bot.all_drafts()], ["Конспект §5", "Конспект §6"])
        bot.on_callback({"id": "1", "data": "drop", "message": {"chat": {"id": CHAT}, "message_id": 5}})  # старая кнопка
        self.assertEqual(bot.draft()["task"], self.TASK2)
        self.assertIn("Ещё черновики", self.last()[0])

    def test_several_links_in_one_message(self):
        self.say(f"сделай {URL} и {self.URL2} и ещё раз {URL}")
        solved = [p for p in self.prompts if p.startswith("Задание:")]
        self.assertEqual(len(solved), 2)
        self.assertTrue(any(URL in p for p in solved) and any(self.URL2 in p for p in solved))
        self.assertTrue(any("Беру 2 задания" in m for m, _ in self.msgs))

    def test_edit_redraws_handwriting(self):
        self.say(URL)
        pathlib.Path(drafts.task_dir(TASK), "draw.py").write_text("# рисует решение\n")
        seen = {}

        def claude(prompt, tools, timeout=0, on_tool=None):
            seen["tools"], seen["prompt"] = tools, prompt
            pathlib.Path(drafts.files_dir(TASK), "hand.png").write_bytes(b"png2")  # перерисовал
            os.utime(os.path.join(drafts.files_dir(TASK), "hand.png"), (1, 1))
            return True, "Поправил ответ в №3"
        sent = len(self.files)
        with mock.patch.object(bot, "claude", claude):
            self.say("в номере 3 ответ 7n/(n-m)")  # длиннее текста черновика — всё равно просьба, не новый текст
        self.assertTrue(any(t.startswith("Bash(python3 ") and t.endswith(f"{TASK}/draw.py)") for t in seen["tools"]))
        self.assertIn("draw.py", seen["prompt"])
        self.assertEqual(drafts.read(TASK), "Клетка делится митозом.\nЭто важно.")  # текст не затёрт просьбой
        self.assertGreater(len(self.files), sent)  # новые картинки пришли в чат
        self.assertIn("Черновик для учителя", self.last()[0])


def _day(n):
    import datetime
    return (datetime.date.today() + datetime.timedelta(days=n)).isoformat()


U = lambda n: f"{n:08d}-0000-0000-0000-000000000000"  # uuid темы
TOPICS = [  # как horo/prep.js без аргументов (и overview.json → topics)
    {"uuid": U(1), "subj": "Математика", "icon": "📐", "title": "А3. Умножение и деление алгебраических дробей",
     "start": _day(-6), "deadline": _day(6), "tasks": [
         {"title": "1. Умножение алгебраических дробей", "type": "detailedAnswer", "status": "approved"},
         {"title": "2. Возведение в степень", "type": "detailedAnswer", "status": "approved"},
         {"title": "Подтверждение 2.0", "type": "detailedAnswer", "status": "appointed"}]},
    {"uuid": U(2), "subj": "Математика", "icon": "📐", "title": "А2. Сложение и вычитание алгебраических дробей.",
     "start": _day(-24), "deadline": _day(-5), "tasks": [{"title": "Приведение к общему знаменателю"}]},
    {"uuid": U(3), "subj": "Математика", "icon": "📐", "title": "А1. Алгебраические дроби",
     "start": _day(-60), "deadline": _day(-40), "tasks": []},  # давно — не «недавно прошли»
    {"uuid": U(4), "subj": "Математика", "icon": "📐", "title": "Г1. Параллелограмм, его свойства и признаки",
     "start": _day(-30), "deadline": _day(-2), "tasks": [{"title": "4. Свойства параллелограмма"}]},
    {"uuid": U(5), "subj": "История", "icon": "🏛", "title": "2. Становление империи. Пётр I (1682-1721) (2026)",
     "start": _day(-14), "deadline": _day(4), "tasks": [{"title": "1. Начало Северной войны"}]},
    {"uuid": U(6), "subj": "Физическая культура", "icon": "🏃", "title": "Гимнастика", "start": _day(-40),
     "deadline": _day(10), "tasks": []},
    {"uuid": U(7), "subj": "История", "icon": "🏛", "title": "3. Эпоха дворцовых переворотов", "start": _day(5),
     "deadline": _day(30), "tasks": []},  # ещё не началась
    {"uuid": U(8), "subj": "Профильная технология", "icon": "📘", "title": "Проектная деятельность",
     "start": _day(-40), "deadline": _day(240), "tasks": []},  # курс на год
    {"uuid": U(9), "subj": "Экономика и менеджмент", "icon": "💰", "title": "Кто такой предприниматель?",
     "start": _day(-40), "deadline": _day(1), "tasks": []},
]
DETAILS = [{"uuid": U(1), "title": "А3", "materials": [{"title": "Умножение", "text": "Чтобы умножить дроби, "
            "перемножают числители и знаменатели"}], "checks": [], "questions": ["Найдите (2a/b)·(b/4)"]}]
SPOILER_OK = {"b", "i", "u", "s", "a", "code", "pre", "blockquote", "tg-emoji", "tg-spoiler"}


def check_html(test, text):
    """Сообщение — только теги, которые понимает Telegram, все закрыты."""
    from html.parser import HTMLParser

    class Check(HTMLParser):
        def __init__(self):
            super().__init__(); self.stack = []
        def handle_starttag(self, tag, attrs):
            test.assertIn(tag, SPOILER_OK)
            self.stack.append(tag)
        def handle_endtag(self, tag):
            test.assertTrue(self.stack and self.stack.pop() == tag, (tag, text))
    c = Check(); c.feed(text); c.close()
    test.assertEqual(c.stack, [], text)


class PrepTest(BotTest):
    """📖 Подготовка: предмет -> что прошли -> 1/5/10 минут -> Claude; своё ТЗ; голосом; приложение."""

    def setUp(self):
        super().setUp()
        self.ov["topics"] = TOPICS
        self.ov["at"] = int(__import__("time").time() * 1000)
        pathlib.Path(bot.OVERVIEW).write_text(json.dumps(self.ov))
        self.edits = []
        self.claude_out = None
        p = mock.patch.object(bot, "PREP_DIR", os.path.join(self.tmp.name, "prep"))
        p.start()
        self.patches.append(p)
        bot.PREP_CTX.clear()
        bot.PREP_JOB.update(running=False, result=None, error="")

    def fake_tg(self, method, **p):
        if method == "editMessageText":
            self.edits.append((p["text"], p.get("reply_markup")))
        return super().fake_tg(method, **p)

    def fake_node(self, *args, timeout=0, prog="node"):
        if args[:1] == ("horo/prep.js",):
            self.nodes.append(args)
            return True, json.dumps(DETAILS if len(args) > 1 else TOPICS)
        return super().fake_node(*args, timeout=timeout, prog=prog)

    def fake_claude(self, prompt, tools, timeout=0, on_tool=None):
        if "репетитор Глеба" not in prompt:
            return super().fake_claude(prompt, tools, timeout, on_tool)
        self.prompts.append(prompt)
        if self.claude_out is not None:
            return True, self.claude_out
        n = 5 if "НОВЫЕ вопросы" in prompt else 2 if "за 1 минуту" in prompt else 9 if "за 10 минут" in prompt else 5
        tag = "новый " if "НОВЫЕ" in prompt else ""
        return True, "```json\n" + json.dumps({
            "title": "Дроби <умножение>", "points": ["a/b · c/d = ac/bd", "**Сократи** до умножения"],
            "sections": [] if n == 2 else [{"title": "Умножение", "text": "Числитель на числитель\nПример: 2/3·3/4 = 1/2"}],
            "mistakes": [] if n == 2 else ["Забыл сократить → сокращай до умножения"],
            "questions": [{"q": f"{tag}вопрос {i} <x>", "a": f"ответ {i}"} for i in range(1, n + 1)]},
            ensure_ascii=False) + "\n```"

    def press(self, data, mid=7):
        bot.on_callback({"id": "1", "data": data, "message": {"chat": {"id": CHAT}, "message_id": mid}})

    def buttons(self, markup):
        return [b for row in json.loads(markup)["inline_keyboard"] for b in row]

    def test_subjects_and_what_was_studied(self):
        subs = bot.prep.subjects(TOPICS)
        names = [s["name"] for s in subs]
        self.assertEqual(names[:2], ["Алгебра", "История"])  # сначала, где тема началась позже всех
        self.assertIn("Геометрия", names)                      # математика — две кнопки
        self.assertNotIn("Математика", names)
        self.assertNotIn("Физическая культура", names)          # к физкультуре не готовим
        self.assertEqual(subs[0]["icon"], "➗")
        alg = [t["uuid"] for t in bot.prep.recent(TOPICS, "Алгебра")]
        self.assertEqual(alg, [U(1), U(2)])                     # текущая первой; А1 давно — нет
        self.assertEqual([t["uuid"] for t in bot.prep.recent(TOPICS, "История")], [U(5)])  # будущая — нет
        self.assertEqual([t["uuid"] for t in bot.prep.recent(TOPICS, "Профильная технология")], [U(8)])  # хоть что-то
        self.assertEqual(bot.prep.clean_title(TOPICS[4]["title"]), "2. Становление империи. Пётр I (1682-1721)")
        self.assertEqual(bot.prep.subtopics(TOPICS[0]), ["Умножение алгебраических дробей", "Возведение в степень"])
        self.assertTrue(all(len(f"{s['icon']} {bot.prep.short(s['name'])}") <= 24 for s in subs))

    def test_phrases(self):
        names = [s["name"] for s in bot.prep.subjects(TOPICS)]
        d = lambda t: bot.prep.detect(t, names)
        self.assertEqual(d("Подготовь к проверочной по алгебре за 5 минут."), ("Алгебра", 5, ""))
        self.assertEqual(d("подготовь меня по истории за десять минут"), ("История", 10, ""))
        self.assertEqual(d("натаскай по математике, быстро"), ("Алгебра", 1, ""))
        self.assertEqual(d("подготовь к контрольной по геометрии подробно")[:2], ("Геометрия", 10))
        self.assertEqual(d("подготовь по истории: Северная война и Полтава")[2],
                         "подготовь по истории: Северная война и Полтава")
        self.assertEqual(d("подготовь меня по квадратным уравнениям за 3 мин"),
                         (None, 5, "подготовь меня по квадратным уравнениям за 3 мин"))
        self.assertTrue(bot.prep.is_request("Повтори со мной историю"))
        self.assertFalse(bot.prep.is_request("покажи оценки"))

    def test_chat_subject_topics_time_result(self):
        self.say(bot.BTN_PREP)
        text, markup = self.last()
        self.assertIn("Подготовка к проверочной", text)
        rows = json.loads(markup)["inline_keyboard"]
        self.assertLessEqual(max(map(len, rows)), 3)
        self.assertTrue(all(len(b["text"]) <= 24 for b in self.buttons(markup)))
        alg = next(b["callback_data"] for b in self.buttons(markup) if "Алгебра" in b["text"])
        self.press(alg)
        topics, kb = self.edits[-1]
        self.assertIn("А3. Умножение и деление алгебраических дробей", topics)
        self.assertNotIn("А1.", topics)
        self.assertEqual([b["callback_data"] for b in self.buttons(kb)][:3], ["pp:t:1", "pp:t:5", "pp:t:10"])
        self.press("pp:t:5")
        self.assertEqual(self.nodes[-1], ("horo/prep.js", U(1), U(2)))  # теория тем с сайта, только чтение
        p = self.prompts[-1]
        self.assertIn("за 5 минут", p)
        self.assertIn("Предмет: Алгебра", p)
        self.assertIn("перемножают числители", p)  # теория с сайта в промпте
        self.assertIn("Найдите (2a/b)·(b/4)", p)
        text, markup = self.last()
        self.assertEqual(text.count("<tg-spoiler>"), 5)
        self.assertIn("Типовые ошибки", text)
        self.assertIn("вопрос 1 &lt;x&gt;", text)  # текст Claude экранирован
        self.assertIn("<b>Сократи</b>", text)
        self.assertEqual([b["callback_data"] for b in self.buttons(markup)], ["pp:more", "pp:deep"])
        for m, _ in self.msgs + self.edits:
            check_html(self, bot.premium(m, True))

    def test_formats_1_and_10_and_more(self):
        self.say("/prep")
        bot.prep_ctx(CHAT, "Алгебра")
        self.press("pp:t:1")
        p1, (text, markup) = self.prompts[-1], self.last()
        self.assertIn("за 1 минуту", p1)
        self.assertIn("ровно 2 быстрых вопроса", p1)
        self.assertEqual(text.count("<tg-spoiler>"), 2)
        self.assertNotIn("Типовые ошибки", text)
        self.press("pp:more")
        p = self.prompts[-1]
        self.assertIn("НОВЫЕ вопросы", p)
        self.assertIn("- вопрос 2 <x>", p)  # уже заданные — не повторять
        self.assertIn("Ещё вопросы", self.last()[0])
        self.assertEqual(len([a for a in self.nodes if a[0] == "horo/prep.js"]), 1)  # материалы — один раз
        self.press("pp:deep")
        self.assertIn("за 10 минут", self.prompts[-1])
        self.assertIn("8–10 задач", self.prompts[-1])
        self.assertEqual(self.last()[0].count("<tg-spoiler>"), 9)
        self.assertEqual([b["callback_data"] for b in self.buttons(self.last()[1])], ["pp:more"])  # уже 10 минут

    def test_long_result_splits_without_breaking_spoilers(self):
        res = {"title": "x", "points": ["п" * 300] * 6, "sections": [{"title": f"т{i}", "text": "с " * 600}
                                                                    for i in range(4)],
               "mistakes": ["ошибка"] * 4, "questions": [{"q": "вопрос " * 40, "a": "ответ " * 30}] * 10}
        html = bot.prep.render(res, "Алгебра", 10)
        parts = bot.split(html)
        self.assertGreater(len(parts), 1)
        for part in parts:
            self.assertLessEqual(len(part), 3500)
            check_html(self, part)

    def test_own_tz(self):
        self.say(bot.BTN_PREP)
        self.press("pp:tz")
        self.assertEqual(bot.mode[CHAT], "prep_tz")
        self.say("квадратные уравнения и теорема Виета")
        self.assertNotIn(CHAT, bot.mode)
        self.assertIn("Понял ТЗ", self.last()[0])
        self.press("pp:t:1")
        p = self.prompts[-1]
        self.assertIn("ТЗ от Глеба", p)
        self.assertIn("«квадратные уравнения и теорема Виета»", p)
        self.assertFalse([a for a in self.nodes if a[0] == "horo/prep.js" and len(a) > 1])  # предмета нет — тем нет
        self.assertIn("по ТЗ", self.last()[0])
        # ТЗ после выбора предмета: предмет остаётся, время прямо в ТЗ
        self.say(bot.BTN_PREP)
        self.press(next(b["callback_data"] for b in self.buttons(self.last()[1]) if "История" in b["text"]))
        self.press("pp:tz")
        self.say("только Полтавская битва, за 5 минут")
        p = self.prompts[-1]
        self.assertIn("Предмет: История", p)
        self.assertIn("Полтавская битва", p)
        self.assertIn("за 5 минут", p)

    def test_voice_request(self):
        self.voice("Подготовь к проверочной по алгебре за 5 минут.")
        p = self.prompts[-1]
        self.assertIn("Предмет: Алгебра", p)
        self.assertIn("за 5 минут", p)
        self.assertEqual(self.last()[0].count("<tg-spoiler>"), 5)
        n = len(self.prompts)
        self.voice("Подготовь меня по истории")  # времени нет — темы и кнопки времени
        self.assertEqual(len(self.prompts), n)
        text, markup = self.last()
        self.assertIn("Становление империи", text)
        self.assertIn("pp:t:10", markup)
        self.voice("Подготовка.")  # кнопка голосом
        self.assertIn("Подготовка к проверочной", self.last()[0])

    def test_not_json_and_errors(self):
        self.claude_out = "Главное: **дроби** <b>умножаются</b>"
        bot.prep_ctx(CHAT, "Алгебра")
        self.press("pp:t:5")
        text = self.last()[0]
        check_html(self, text)
        self.assertIn("<b>дроби</b> &lt;b&gt;умножаются", text)
        bot.PREP_CTX.clear()
        self.press("pp:more")  # бот перезапускался — подготовки уже нет
        self.assertTrue(any("неактуальна" in m for m, _ in self.msgs))

    def test_topics_from_site_when_overview_has_none(self):
        pathlib.Path(bot.OVERVIEW).write_text(json.dumps({"at": 1, "tasks": []}))
        self.say(bot.BTN_PREP)
        self.assertEqual(self.nodes[0], ("horo/prep.js",))
        self.assertIn("Алгебра", self.last()[1])
        self.say(bot.BTN_PREP)  # второй раз — из кэша, без сайта
        self.assertEqual(len(self.nodes), 1)

    def test_app(self):
        bot.save_settings({"owner": CHAT})
        subs = bot.app_prep_subjects({})["subjects"]
        alg = next(s for s in subs if s["name"] == "Алгебра")
        self.assertEqual(alg["recent"], [U(1), U(2)])
        self.assertEqual([t["uuid"] for t in alg["topics"]], [U(1), U(2), U(3)])
        for bad in ({"subj": "Алгебра", "minutes": 3}, {"minutes": 5}, {"more": True}):
            with self.assertRaises(ValueError):
                bot.app_prep_run(bad)
        self.assertEqual(bot.app_prep_run({"subj": "Алгебра", "topics": [U(3)], "minutes": 1}), {"ok": True})
        self.assertEqual(self.nodes[-1], ("horo/prep.js", U(3)))  # темы, выбранные в приложении
        st = bot.app_prep_state({})
        self.assertFalse(st["running"])
        self.assertEqual(len(st["result"]["questions"]), 2)
        self.assertEqual(st["result"]["topics"][0]["title"], "А1. Алгебраические дроби")
        self.assertIn("<tg-spoiler>", self.last()[0])  # результат — и в чат
        bot.app_prep_run({"more": True})
        r = bot.app_prep_state({})["result"]
        self.assertTrue(r["more"])
        self.assertEqual((len(r["questions"]), r["added"]), (7, 5))  # в приложении вопросы добавились к прежним
        bot.app_prep_run({"again": True, "minutes": 10})
        self.assertIn("за 10 минут", self.prompts[-1])
        self.assertEqual(bot.app_prep_run({"tz": "Северная война", "minutes": 5}), {"ok": True})
        self.assertIn("«Северная война»", self.prompts[-1])
        bot.PREP_JOB["result"] = None  # после перезапуска — последний результат из файла
        self.assertEqual(bot.app_prep_state({})["result"]["tz"], "Северная война")


for _name in dir(BotTest):  # заготовки BotTest без его тестов
    if _name.startswith("test_") and _name not in PrepTest.__dict__:
        setattr(PrepTest, _name, None)


class ExamsTest(unittest.TestCase):
    """🎯 Подтверждения: какие задания попадают в список, уровни, порядок и длина сообщений."""
    DATA = {"topics": [
        {"uuid": "t1", "subj": "Химия", "icon": "⚗️", "deadline": "2020-01-10", "tasks": [
            {"uuid": "a", "title": "Подтверждение 2.0", "status": "appointed", "lvl": 2},
            {"uuid": "b", "title": "Подготовка к подтверждению", "status": "appointed", "lvl": 2},
            {"uuid": "c", "title": "Irregular verbs test", "status": "approved", "lvl": 1},
            {"uuid": "d", "title": "Химическая символика 2.0", "status": "appointed", "lvl": 2}]},
        {"uuid": "t2", "subj": "Математика", "icon": "📐", "deadline": "2099-11-09", "tasks": [
            {"uuid": "e", "title": "Подтверждение 3.0", "status": "reworking", "lvl": 3, "pct": 55, "pass": 80},
            {"uuid": "f", "title": "Подтверждение 2.0", "status": "approved", "lvl": 2, "pct": 100},
            {"uuid": "g", "title": "Итоговая работа «Дроби»", "status": "reviewing", "lvl": 2},
            {"uuid": "h", "title": "Test 2.0", "status": "appointed", "lvl": 2}]},
    ]}

    def test_items(self):
        items = bot.exam_items(self.DATA)
        self.assertEqual([x["url"].rsplit("/", 1)[1] for x in items], ["h", "a", "g", "f", "e"])  # 2.0: несданное выше, по предмету
        self.assertEqual({x["url"].rsplit("/", 1)[1]: x["lvl"] for x in items}["e"], 3)

    def test_text(self):
        msgs = bot.exams_messages(self.DATA)
        text = "\n".join(msgs)
        self.assertIn("зачтено 1 из 5", text)
        self.assertIn("55% из 80%", text)                 # доработка: сколько есть и сколько нужно
        self.assertIn("❗ срок был 10.01", text)           # срок прошёл
        self.assertIn("до 09.11", text)
        self.assertIn("https://horodigital.ru/student/topic/t2/task/e", text)
        self.assertNotIn("Подготовка", text)
        self.assertNotIn("Irregular", text)
        self.assertNotIn("символика", text)
        self.assertTrue(text.index("<b>2.0</b>") < text.index("<b>3.0</b>"))

    def test_long_list_split(self):
        many = {"topics": [{"uuid": f"t{i}", "subj": "Предмет", "icon": "📘", "deadline": "", "tasks": [
            {"uuid": f"x{i}", "title": "Подтверждение 2.0", "status": "appointed", "lvl": 2}]} for i in range(80)]}
        msgs = bot.exams_messages(many)
        self.assertGreater(len(msgs), 1)
        for m in msgs:
            self.assertLessEqual(len(m), 3900)
            self.assertEqual(m.count("<blockquote"), m.count("</blockquote>"))   # цитаты не разрезаны

    def test_empty(self):
        self.assertIn("не нашёл", bot.exams_messages({"topics": []})[0])

    def test_one_level(self):
        two = "\n".join(bot.exams_messages(self.DATA, 2))
        three = "\n".join(bot.exams_messages(self.DATA, 3))
        self.assertIn("Подтверждения 2.0", two)
        self.assertNotIn("/task/e", two)                 # 3.0 в списке 2.0 нет
        self.assertIn("зачтено 1 из 4", two)
        self.assertIn("/task/e", three)
        self.assertIn("зачтено 0 из 1", three)
        self.assertNotIn("<b>3.0</b>", three)             # заголовок уровня лишний, он в шапке

    def test_choice_and_buttons(self):
        sent = []
        with mock.patch.object(bot, "send", lambda chat, text, **kw: sent.append((text, kw.get("markup")))), \
                mock.patch.object(bot, "overview", lambda: dict(self.DATA, at=time.time() * 1000)):
            bot.exams_choice(42)
            self.assertIn("ex:2", sent[-1][1]); self.assertIn("ex:3", sent[-1][1]); self.assertIn("ex:all", sent[-1][1])
            self.assertIn("2.0 · 3", sent[-1][1])           # не зачтено на 2.0: a, h, g
            bot.send_exams(42, 3)
            self.assertIn("Подтверждения 3.0", sent[-1][0])
            self.assertIn("ex:upd:3", sent[-1][1])


class WaitTest(BotTest):
    """⏳ Ожидание: отправки по таймеру (во сколько, ⚡/✖), черновики без таймера, задания в работе."""

    def test_empty(self):
        text, markup = bot.waiting_card()
        self.assertIn("Ничего не ждёт", text)
        self.assertIn("w:upd", markup)

    def test_lists(self):
        now = 1_800_000_000
        bot.set_draft({"task": "t1", "url": URL, "title": "Гринёв и Швабрин", "pairs": [], "own_files": False,
                       "send_at": now + 7 * 60 + 5})
        bot.set_draft({"task": "t2", "url": URL, "title": "Карта Северной войны", "pairs": [], "own_files": False})
        with bot.running_lock:
            bot.running.add(TASK)
        try:
            text, markup = bot.waiting_card(now)
        finally:
            with bot.running_lock:
                bot.running.discard(TASK)
        when = bot.datetime.datetime.fromtimestamp(now + 7 * 60 + 5, bot.MSK)
        self.assertIn(f"в {when:%H:%M}", text)
        self.assertIn("через 7 мин", text)
        self.assertIn("Ждут твоего «Отправить»", text)
        self.assertIn("Карта Северной войны", text)
        self.assertIn("Решаю или в очереди", text)
        self.assertIn("Конспект §5", text)                 # название задания в работе — из task.json
        for cb in ("now:t1", "hold:t1", "send:t2", "ed:t2"):
            self.assertIn(cb, markup)
        self.assertTrue(text.index("Отправлю учителю") < text.index("Ждут твоего"))

    def test_button(self):
        self.say(bot.BTN_WAIT)
        self.assertIn("Ожидание", self.last()[0])


if __name__ == "__main__":
    unittest.main()
