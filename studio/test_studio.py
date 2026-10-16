"""Тесты Студии и botkit без сети и без Telegram: python3 studio/test_studio.py"""
import hashlib
import hmac
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
import urllib.request
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="studio-test-")
os.environ["STUDIO_BOTS_DIR"] = os.path.join(TMP, "bots")
os.environ["STUDIO_SITES_DIR"] = os.path.join(TMP, "sites")
os.environ["STUDIO_TOKEN"] = "123456:TESTTOKENTESTTOKENTESTTOKENTESTTOKEN"
sys.path.insert(0, HERE)
import studio  # noqa: E402

kit = studio.kit
OWNER = 42


def signed(token, user):
    fields = {"auth_date": str(int(time.time())), "user": json.dumps(user)}
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(fields)


class Fake:
    """Подменяет Claude, pm2, @BotFather и getMe."""

    def __init__(self):
        self.prompts, self.pm2, self.pm2_all = [], [], []
        self.interview = {"questions": [{"q": "Сколько стаканов в день?", "options": ["8", "10"]},
                                        {"q": "Когда напоминать?", "options": ["Каждый час", "Утром"]}],
                          "names": ["Вода", "Глоток", "Аква"], "emoji": "💧"}
        self.plan = {"summary": "Считает воду", "chat": ["кнопки +250 мл"], "app": ["прогресс за день"],
                     "auto": [], "commands": [{"command": "start", "description": "Главное"}],
                     "username": "voda_test", "slug": "voda", "colors": ["#0ea5e9", "#6366f1"]}
        self.token = "777777:BOTTOKENBOTTOKENBOTTOKENBOTTOKENBOT"

    def claude(self, prompt, cwd, on_step=None, timeout=0, tools=True, model=None):
        self.prompts.append(prompt)
        if "уточняющих" in prompt:
            return True, json.dumps(self.interview, ensure_ascii=False)
        if "план бота" in prompt:
            return True, json.dumps(self.plan, ensure_ascii=False)
        if "план сайта" in prompt:
            return True, json.dumps({"summary": "Сайт кофейни", "pages": ["Главная", "Меню"], "style": ["тёплый"],
                                     "features": [], "slug": "coffee"}, ensure_ascii=False)
        if "Статический сайт" in prompt or "дорабатываешь сайт" in prompt:
            with open(os.path.join(cwd, "index.html"), "w") as f:
                f.write("<!doctype html><title>Кофейня</title><h1>Привет</h1>")
            return True, "Сайт кофейни: главная и меню."
        if on_step:
            on_step("пишу bot.py")
        return True, "Считает воду и напоминает."

    def sh(self, *cmd, timeout=60, cwd=None, env=None):
        self.pm2_all.append(cmd)
        if cmd[1:2] and "tg.py" in cmd[1]:
            return 0, "ok"
        if cmd[0] == "pm2":
            self.pm2.append(cmd)
            if cmd[1] == "jlist":
                names = [c[c.index("--name") + 1] for c in self.pm2 if c[1] == "start"]
                return 0, json.dumps([{"name": n, "pm2_env": {"status": "online"}} for n in names])
            return 0, ""
        return REAL_SH(*cmd, timeout=timeout, cwd=cwd, env=env)


REAL_SH = studio.sh


class StudioFlow(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(os.path.join(TMP, "bots"), ignore_errors=True)
        self.t = studio.bot.testing(owner=OWNER)
        self.f = Fake()
        self.base = "https://apps.1-2-3-4.sslip.io/"
        studio.bot.db.data["base_url"] = self.base
        getme = io.BytesIO(json.dumps({"result": {"username": "voda_test_bot"}}).encode())
        self.patches = [mock.patch.object(studio, "claude", self.f.claude), mock.patch.object(studio, "sh", self.f.sh),
                        mock.patch.object(studio, "botfather", lambda n, w: (self.f.token, None)),
                        mock.patch.object(studio, "secretary_ready", lambda: True),  # есть Секретарь — @BotFather сам
                        mock.patch.object(studio, "avatar", lambda p, d: None),
                        mock.patch.object(studio.time, "sleep", lambda s: None),
                        mock.patch.object(studio.urllib.request, "urlopen", lambda *a, **k: getme)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def texts(self):
        return "\n".join(p.get("text") or "" for p in self.t.messages)

    def test_full_flow_chat(self):
        self.t.send("/start")
        self.assertIn("Студия", self.t.last)
        self.t.send("бот, который напоминает пить воду")
        self.assertEqual(studio.draft()["stage"], "ask")
        self.assertIn("Вопрос 1 из 2", self.t.last)
        self.assertEqual(self.t.buttons[:2], ["8", "10"])
        d = studio.draft()
        self.t.press(f"ans|{d['id']}|0|1")  # «10»
        self.assertIn("Вопрос 2 из 2", self.t.last)
        self.t.send("вечером в 21")  # свой вариант текстом
        self.assertEqual(studio.draft()["stage"], "name")
        self.assertIn("Как назовём", self.t.last)
        self.t.send("Водичка")
        d = studio.draft()
        self.assertEqual(d["stage"], "plan")
        self.assertEqual(d["name"], "Водичка")
        self.assertEqual([q["a"] for q in d["qs"]], ["10", "вечером в 21"])
        self.assertIn("план", self.t.last)
        self.assertIn("✅ Создать", self.t.buttons)
        # правка плана
        self.t.send("добавь статистику за неделю")
        self.assertIn("добавь статистику", self.f.prompts[-1])
        self.assertEqual(studio.draft()["stage"], "plan")
        # создать
        self.t.press("go")
        info = studio.registry()["voda"]
        self.assertEqual(info["username"], "voda_test_bot")
        self.assertEqual(studio.draft()["stage"], "done")
        self.assertIn("Бот «Водичка» готов", self.texts())
        # сразу после запуска — автопроверка (Секретаря нет: тесты и лог, кнопки вживую — пропуск с пометкой)
        self.assertIn("проверил тесты и логи — всё в порядке", self.t.last)
        self.assertIn("Секретарь не вошёл", self.t.last)
        env = read(os.path.join(info["dir"], ".env"))
        self.assertIn(f"APP_URL={self.base}b/voda/", env)
        self.assertIn(f"BOT_OWNER={OWNER}", env)
        self.assertEqual(oct(os.stat(os.path.join(info["dir"], ".env")).st_mode & 0o777), "0o600")
        self.assertTrue(os.path.exists(os.path.join(info["dir"], "botkit.py")))
        self.assertIn("Водичка", read(os.path.join(info["dir"], "bot.py")))
        self.assertTrue(any(c[1] == "start" and "bot-voda" in c for c in self.f.pm2))
        build = next(p for p in self.f.prompts if "Ты делаешь Telegram-бота" in p)
        self.assertIn("botkit.py", build)
        self.assertIn("Мини-приложение обязательно", build)

    def test_flow_from_app_and_skip(self):
        code, s = self.t.api("idea", {"text": "список покупок"})
        self.assertEqual(code, 200)
        self.assertEqual(s["draft"]["stage"], "ask")
        code, s = self.t.api("enough")
        self.assertEqual(s["draft"]["stage"], "name")
        self.assertEqual(s["draft"]["names"], ["Вода", "Глоток", "Аква"])
        self.assertEqual(self.t.api("answer", {"text": "x"})[0], 400)  # шаг уже другой
        code, s = self.t.api("name", {"text": "Покупки"})
        self.assertEqual(s["draft"]["stage"], "plan")
        self.assertEqual(s["draft"]["plan"]["username"], "voda_test")
        code, s = self.t.api("confirm")
        self.assertEqual(code, 200)
        self.assertEqual(s["draft"]["stage"], "done")
        self.assertEqual(s["draft"]["result"]["name"], "Покупки")
        self.assertEqual(len(s["bots"]), 1)
        self.assertEqual(s["bots"][0]["status"], "online")

    def test_secretary_failed_asks_token(self):
        with mock.patch.object(studio, "botfather", lambda n, w: (None, "Секретарь не смог: BotFather молчит")):
            self.t.api("idea", {"text": "дневник"})
            self.t.api("enough")
            self.t.api("name", {"text": "Дневник"})
            self.t.api("confirm")
        self.assertEqual(studio.draft()["stage"], "token")
        self.assertIn("нужен токен", self.t.last)
        self.assertIn("BotFather молчит", self.t.last)
        self.assertIn("voda_test_bot", self.t.last)  # желаемый адрес — одним шагом
        self.t.send("вот: " + self.f.token)
        self.assertEqual(studio.draft()["stage"], "done")
        self.assertTrue(any(m == "deleteMessage" for m, _ in self.t.calls))  # токен стёрт из чата

    def test_no_secretary_no_onetap_token_during_build(self):
        """Ни Секретаря, ни Managed Bots: Студия сразу (пока пишет код) просит создать бота в @BotFather одним
        шагом; токен, присланный во время сборки, — не правка, а токен; дальше запуск сама."""
        with mock.patch.object(studio, "secretary_ready", lambda: False), \
                mock.patch.object(studio, "botfather", lambda n, w: self.fail("Секретаря нет")):
            self.t.api("idea", {"text": "дневник"})
            self.t.api("enough")
            self.t.api("name", {"text": "Дневник"})
            orig = studio.claude

            def build_and_token(prompt, cwd, **k):
                self.assertIn("Пока пишу код — создай бота", self.texts())
                self.assertIn("Открыть @BotFather", [x["text"] for p in self.t.messages for row in
                                                      (p.get("reply_markup") or {}).get("inline_keyboard", []) for x in row])
                self.t.send("держи " + self.f.token)
                self.assertEqual(studio.draft().get("token"), self.f.token)
                self.assertNotIn("queued", studio.draft())
                return orig(prompt, cwd, **k)
            with mock.patch.object(studio, "claude", build_and_token):
                self.t.api("confirm")
        self.assertEqual(studio.draft()["stage"], "done")
        self.assertIn(f"BOT_TOKEN={self.f.token}", read(os.path.join(studio.registry()["voda"]["dir"], ".env")))
        self.assertTrue(any(m == "deleteMessage" for m, _ in self.t.calls))

    def test_cancel_drops_unfinished_bot(self):
        """Отмена на шаге «токен»: недоделанный бот не висит в «Моих ботах» и на диске."""
        with mock.patch.object(studio, "botfather", lambda n, w: (None, "нет")):
            self.t.api("idea", {"text": "дневник"})
            self.t.api("enough")
            self.t.api("name", {"text": "Дневник"})
            self.t.api("confirm")
        path = studio.registry()["voda"]["dir"]
        self.assertTrue(os.path.isdir(path))
        self.t.press("cancel")
        self.assertEqual(studio.registry(), {})
        self.assertFalse(os.path.exists(path))
        self.assertEqual(studio.draft(), {})

    def test_cancel_from_app_drops_unfinished_bot(self):
        """Та же отмена из мини-приложения (api cancel) — тоже убирает недоделанного бота."""
        with mock.patch.object(studio, "botfather", lambda n, w: (None, "нет")):
            self.t.api("idea", {"text": "дневник"})
            self.t.api("enough")
            self.t.api("name", {"text": "Дневник"})
            self.t.api("confirm")
        path = studio.registry()["voda"]["dir"]
        self.assertEqual(self.t.api("cancel")[0], 200)
        self.assertEqual(studio.registry(), {})
        self.assertFalse(os.path.exists(path))

    def test_voice_and_photo_start_idea(self):
        """Голосовое → текст идеи; фото с подписью → картинка к идее и сама идея."""
        class Media:
            def voice_text(self, token, msg): return "бот для списка покупок"
            def photo_path(self, token, msg, folder): return os.path.join(TMP, "shot.jpg")
        with mock.patch.object(kit, "_media", Media()):
            self.t.send("", voice={"file_id": "v1"})
            self.assertEqual(studio.draft()["idea"], "бот для списка покупок")
            self.assertIn("«бот для списка покупок»", self.texts())
            studio.put({})
            self.t.send("", photo=[{"file_id": "p1"}], caption="сделай как на картинке: трекер сна")
        d = studio.draft()
        self.assertEqual(d["idea"], "сделай как на картинке: трекер сна")
        self.assertEqual(d["shots"], [os.path.join(TMP, "shot.jpg")])

    def test_resume_on_start(self):
        """/start посреди черновика показывает текущий шаг, а не начинает заново."""
        self.t.send("бот-напоминалка")
        d = studio.draft()
        self.t.send("/start")
        self.assertIn("Вопрос 1 из 2", self.t.last)
        self.assertEqual(studio.draft()["id"], d["id"])
        self.t.api("enough")
        self.t.send("/start")
        self.assertIn("Как назовём", self.t.last)
        self.t.api("name", {"text": "Х"})
        self.t.send("/start")
        self.assertIn("✅ Создать", self.t.buttons)

    def test_mode_chat_or_app_in_studio(self):
        """Студия с https: первый /start — «Как удобнее?»; в чате — без web_app-кнопок; API mode из приложения."""
        studio.bot.app_url = "https://apps.1-2-3-4.sslip.io/"
        try:
            self.t.send("/start")
            self.assertIn("Как удобнее", self.t.last)
            self.t.press("mode:chat")
            self.assertIn("Студия", self.t.last)
            self.assertIn("Режим: 💬 чат", self.t.last)
            self.assertFalse(any("web_app" in x for x in self.t.keyboard))
            self.assertIn("⚙️ Режим", self.t.buttons)
            self.t.press("mode")
            self.t.press("mode:app")
            self.assertIn("Режим: 📱 приложение", self.t.last)
            self.assertTrue(any("web_app" in x for x in self.t.keyboard))
            self.assertEqual(self.t.api("mode", {"mode": "chat"})[1], {"mode": "chat"})
            self.assertIn("Студия", self.t.last)
            self.assertEqual(studio.bot.ui_mode(), "chat")
        finally:
            studio.bot.app_url = ""

    def test_one_tap_managed_bot(self):
        """Managed Bots: кнопка «⚡ Создать», подтверждение в Telegram → токен сам → запуск."""
        created = {"username": "voda_test_bot", "id": 555}
        with mock.patch.object(studio.bot, "can_manage", lambda: True), \
                mock.patch.object(studio.bot, "managed_token", lambda i: self.f.token if i == 555 else None), \
                mock.patch.object(studio, "botfather", lambda n, w: self.fail("Секретарь не нужен")):
            studio.bot.username = "studio_test_bot"
            self.t.api("idea", {"text": "вода"})
            self.t.api("enough")
            self.t.api("name", {"text": "Вода"})
            orig = studio.claude

            def build_and_tap(prompt, cwd, **k):  # пока пишется код, Глеб жмёт кнопку и подтверждает
                link = studio.draft()["newbot"]
                self.assertTrue(link.startswith("https://t.me/newbot/studio_test_bot/voda_test_bot?name="))
                self.assertEqual(self.t.api("state")[1]["draft"]["newbot"], link)
                self.assertNotIn("token", self.t.api("state")[1]["draft"])
                self.t.bot.handle({"update_id": 2, "managed_bot": {"user": {"id": OWNER}, "bot": created}})
                return orig(prompt, cwd, **k)
            with mock.patch.object(studio, "claude", build_and_tap):
                self.t.api("confirm")
        self.assertIn("⚡ Создать @voda_test_bot", "\n".join(
            b["text"] for p in self.t.messages for row in (p.get("reply_markup") or {}).get("inline_keyboard", [])
            for b in row))
        self.assertEqual(studio.draft()["stage"], "done")
        self.assertIn(f"BOT_TOKEN={self.f.token}", read(os.path.join(studio.registry()["voda"]["dir"], ".env")))

    def test_managed_bot_after_code_ready(self):
        with mock.patch.object(studio.bot, "can_manage", lambda: True), \
                mock.patch.object(studio.bot, "managed_token", lambda i: self.f.token):
            studio.bot.username = "studio_test_bot"
            self.t.api("idea", {"text": "вода"})
            self.t.api("enough")
            self.t.api("name", {"text": "Вода"})
            self.t.api("confirm")
            self.assertEqual(studio.draft()["stage"], "token")
            self.assertIn("⚡ Создать @voda_test_bot", self.t.buttons)
            self.t.bot.handle({"update_id": 3, "managed_bot": {"user": {"id": 999}, "bot": {"id": 1}}})  # чужой
            self.assertEqual(studio.draft()["stage"], "token")
            self.t.bot.handle({"update_id": 4, "managed_bot": {"user": {"id": OWNER}, "bot": {"id": 1}}})
        self.assertEqual(studio.draft()["stage"], "done")
        access = [p for m, p in self.t.calls if m == "setManagedBotAccessSettings"]
        self.assertEqual(access, [{"user_id": 1, "is_access_restricted": True}])  # чужие бота не откроют

    def test_home_tip_and_folder(self):
        with mock.patch.object(studio.bot, "can_manage", lambda: False), \
                mock.patch.object(studio, "secretary_ready", lambda: False):
            studio._one_tap["at"] = 0
            self.t.send("/start")
        self.assertIn("Bot Management Mode", self.t.last)
        with mock.patch.object(studio.bot, "can_manage", lambda: True):
            studio._one_tap["at"] = 0
            self.t.send("/start")
        self.assertNotIn("Bot Management Mode", self.t.last)
        with mock.patch.object(studio, "secretary_ready", lambda: True):
            self.t.api("idea", {"text": "вода"})
            self.t.api("enough")
            self.t.api("name", {"text": "Вода"})
            self.t.api("confirm")
        self.assertTrue(any(c[0] == "python3" and "botsfolder" in c and "voda_test_bot" in c for c in self.f.pm2_all))

    def test_text_during_build_is_queued_and_free_text_asks(self):
        self.t.api("idea", {"text": "трекер"})
        self.t.api("enough")
        self.t.api("name", {"text": "Трекер"})
        d = studio.draft()
        d["stage"] = "build"
        studio.put(d)
        self.t.send("сделай кнопки зелёными")
        self.assertEqual(studio.draft()["queued"], ["сделай кнопки зелёными"])
        studio.put({})
        studio.reg_set("x", {"name": "Икс", "slug": "x", "pm2": "bot-x", "dir": "/nonexistent", "port": 8800})
        self.t.send("что-то новое")
        self.assertIn("Новый бот", self.t.buttons[0])
        self.assertIn("✏️ Икс", self.t.buttons)

    def test_bad_interview_still_goes_to_name(self):
        with mock.patch.object(studio, "talk", lambda p: (None, "Claude не закончил работу")):
            self.t.send("какой-то бот")
        self.assertEqual(studio.draft()["stage"], "name")

    def test_fix_and_delete(self):
        self.t.api("idea", {"text": "вода"})
        self.t.api("enough")
        self.t.api("name", {"text": "Вода"})
        self.t.api("confirm")
        slug = "voda"
        path = studio.registry()[slug]["dir"]

        def edit(prompt, cwd, on_step=None, **k):
            with open(os.path.join(cwd, "README.md"), "a") as f:
                f.write("\nправка\n")
            return True, "Добавил"
        with mock.patch.object(studio, "claude", edit):
            self.t.press(f"fix|{slug}")
            self.t.send("добавь кнопку сброса")
        self.assertIn("готово", self.t.last)
        self.assertIn("↩️ Откатить", self.t.buttons)
        self.assertIn("правка", read(os.path.join(path, "README.md")))
        code, s = self.t.api("delete", {"slug": slug})
        self.assertEqual(s["bots"], [])
        self.assertFalse(os.path.exists(path))

    def test_support_button_on_home(self):
        self.t.send("/start")
        self.assertIn("🛠 Поддержка", self.t.buttons)
        self.assertNotIn("Bot Management Mode", self.t.last)  # Секретарь есть — подсказка про токен не нужна

    def test_undo_after_fix(self):
        self.t.api("idea", {"text": "вода"})
        self.t.api("enough")
        self.t.api("name", {"text": "Вода"})
        self.t.api("confirm")
        path = studio.registry()["voda"]["dir"]
        before = read(os.path.join(path, "README.md"))

        def edit(prompt, cwd, on_step=None, **k):
            with open(os.path.join(cwd, "README.md"), "a") as f:
                f.write("\nправка\n")
            return True, "Добавил"
        with mock.patch.object(studio, "claude", edit):
            self.t.api("fix", {"slug": "voda", "text": "добавь кнопку"})
        undo = next(x["callback_data"] for x in self.t.keyboard if x["text"] == "↩️ Откатить")
        self.t.press(undo)
        self.assertIn("Правка отменена", self.t.last)
        self.assertEqual(read(os.path.join(path, "README.md")), before)
        self.assertTrue(any(c[:2] == ("pm2", "restart") for c in self.f.pm2))

    def test_webapp_files(self):
        """Приложение Студии: <body data-support> — признак, а не кнопка (клик по body не открывает поддержку);
        в kit.js — «Перейти в чат»."""
        self.assertIn('<body data-support="/api/support">', read(os.path.join(HERE, "webapp", "index.html")))
        self.assertIn("el !== document.body", read(os.path.join(HERE, "kit", "template", "webapp", "kit.js")))
        js = read(os.path.join(HERE, "kit", "template", "webapp", "kit.js"))
        self.assertIn("data-mode", js)
        self.assertIn("Перейти в чат", js)
        self.assertIn("api('mode', { mode: 'chat' })", js)
        self.assertIn("bot.menu(", read(os.path.join(HERE, "kit", "template", "bot.py")))

    def test_bots_list_opens_sites_too(self):
        """В «Моих ботах» есть и сайты: их кнопка ведёт на экран сайта, а не в «Бота уже нет»."""
        studio.reg_set("water", {"name": "Вода", "slug": "water", "pm2": "bot-water", "dir": "/nope", "created": 2})
        studio.site_set("cafe", {"kind": "site", "name": "Кофейня", "slug": "cafe", "dir": "/nope", "created": 1,
                                 "access": "private", "key": "k"})
        try:
            self.t.send("/bots")
            data = [btn["callback_data"] for btn in self.t.keyboard if "callback_data" in btn]
            self.assertIn("site|cafe", data)
            self.assertIn("bot|water", data)
            self.t.press("site|cafe")
            self.assertIn("Кофейня", self.t.last)
            for gone in ("logs|nope", "restart|nope", "del?|nope", "sdel?|nope", "undo|water", "smode|cafe|xxx",
                         "ans|zz|9|9", "name|zz|9", "skip|zz"):
                self.t.press(gone)
                self.assertTrue(self.t.toasts[-1], gone)  # на каждую устаревшую кнопку — ответ
        finally:
            studio.site_set("cafe", None)
            studio.reg_set("water", None)

    def test_go_while_claude_busy_says_so(self):
        studio.put({"id": "ab", "stage": "plan", "name": "Вода", "idea": "вода", "qs": [], "plan": {"summary": "x"}})
        self.assertTrue(studio.job.acquire(blocking=False))
        try:
            self.t.press("go")
            self.assertIn("дорабатывает другого бота", self.t.toasts[-1])
            self.assertEqual(studio.draft()["stage"], "plan")
        finally:
            studio.job.release()

    def test_cancel_in_app_drops_unfinished_bot(self):
        studio.reg_set("half", {"name": "Половина", "slug": "half", "pm2": "bot-half",
                                "dir": os.path.join(studio.BOTS, "half"), "username": None})
        studio.put({"id": "cd", "stage": "failed", "slug": "half", "name": "Половина", "qs": []})
        code, _ = self.t.api("cancel")
        self.assertEqual(code, 200)
        self.assertNotIn("half", studio.registry())
        self.assertEqual(self.t.api("answer", {"i": "x"})[0], 400)  # кривой номер вопроса — понятная ошибка

    def test_token_in_chat_is_not_an_idea(self):
        """Токен, присланный без просьбы, не должен стать «идеей» (уйдёт в Claude, базу и приложение)."""
        self.t.send("123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw")
        self.assertIn("не нужен", self.t.last)
        self.assertIn("deleteMessage", [m for m, _ in self.t.calls])
        self.assertFalse(studio.draft().get("idea"))

    def test_stranger(self):
        self.t.send("/start", uid=7)
        self.assertIn("личный", self.t.last)
        self.assertEqual(self.t.api("state", uid=7)[0], 403)


class AutoCheck(unittest.TestCase):
    """Сразу после запуска нового бота: обход всех его кнопок, тесты, лог → Claude исправляет и улучшает → тесты →
    перезапуск. Claude, botcrawl и Telegram — подменены."""
    setUp, tearDown, texts = StudioFlow.setUp, StudioFlow.tearDown, StudioFlow.texts

    def build(self):
        self.t.api("idea", {"text": "вода"})
        self.t.api("enough")
        self.t.api("name", {"text": "Вода"})
        self.t.api("confirm")
        return studio.registry()["voda"]["dir"]

    def test_quality_in_prompts(self):
        for prompt in (studio.BUILD, studio.FIX):
            for need in ("1–5 строк", "до 24 символов", "не больше 3 в ряд", "плавно", "мёртвых кнопок",
                         "Оникс-стекло", "отклик"):
                self.assertIn(need, prompt)
            self.assertNotIn("{quality}", prompt)
        studio.BUILD.format(name="x", emoji="", dir="d", idea="i", qa="q", plan="p", shots="", fixes="")
        studio.FIX.format(name="x", dir="d", text="t", shots="")
        self.assertIn(".fade", studio.BUILD)

    def test_crawls_new_bot_fixes_and_restarts(self):
        crawled, prompts = [], []
        db = []

        def crawl(username, out, tick=None, params=None):
            crawled.append((username, params))
            tick and tick("✅ /start")
            db.append(os.path.join(studio.registry()["voda"]["dir"], "data", "db.json"))
            with open(db[0], "w") as f:  # обход выбрал «💬 В чате» — это выбор владельца, после проверки его нет
                json.dump({"_mode": "chat", "water": 3}, f)
            return {"clicks": 12, "problems": ["/start › 📊 Неделя: тишина — бот не ответил",
                                               "/start: в ряду 4 кнопок: a, b, c, d"]}
        orig = self.f.claude

        def claude(prompt, cwd, on_step=None, **k):
            if "автопроверка" in prompt:
                prompts.append(prompt)
                with open(os.path.join(cwd, "bot.py"), "a") as f:
                    f.write("\n# неделя\n")
                return True, "Починил «Неделю», ряды по 3."
            return orig(prompt, cwd, on_step=on_step, **k)
        with mock.patch.object(studio.botcheck, "crawl", crawl), mock.patch.object(studio, "claude", claude), \
                mock.patch.object(studio.botcheck, "secretary_account", lambda root=None: True):
            path = self.build()
        self.assertEqual(crawled, [("voda_test_bot", studio.botcheck.NEW_CRAWL)])  # только новый бот
        self.assertEqual((crawled[0][1]["max"], crawled[0][1]["depth"]), (80, 3))
        self.assertEqual(len(prompts), 1)
        for need in ("тишина", "в ряду 4 кнопок", "красивее, проще, короче", "1–5 строк", "дорабатываешь"):
            self.assertIn(need, prompts[0])
        self.assertIn("Бот «Вода» готов", self.texts())
        self.assertIn("проверил 12 кнопок, исправил 2", self.t.last)
        self.assertIn("↩️ Откатить", self.t.buttons)
        self.assertIn("неделя", read(os.path.join(path, "bot.py")))
        self.assertTrue(any(c[:2] == ("pm2", "restart") and "bot-voda" in c for c in self.f.pm2))
        with open(db[0]) as f:
            self.assertEqual(json.load(f), {"water": 3})
        log = REAL_SH("git", "-C", path, "log", "--format=%s")[1]
        self.assertIn("Автопроверка", log)

    def test_no_account_no_crawl(self):
        with mock.patch.object(studio.botcheck, "crawl", lambda *a, **k: self.fail("без входа кнопки не жмём")):
            self.build()
        self.assertIn("Кнопки вживую не жал: Секретарь не вошёл в аккаунт", self.t.last)
        self.assertFalse(any(c[:2] == ("pm2", "restart") for c in self.f.pm2))  # проблем нет — не трогаем

    def test_tests_still_fail_after_fix_rolls_back(self):
        path = self.build()
        before = read(os.path.join(path, "bot.py"))

        def claude(prompt, cwd, on_step=None, **k):
            with open(os.path.join(cwd, "bot.py"), "a") as f:
                f.write("\n# сломал\n")
            return True, "Поправил."
        with mock.patch.object(studio, "run_tests", lambda p: (False, "FAIL: test_home\nAssertionError: нет кнопки")), \
                mock.patch.object(studio, "claude", claude), \
                mock.patch.object(studio.botcheck, "crawl", lambda u, o, t=None, p=None: {"clicks": 5, "problems": []}), \
                mock.patch.object(studio.botcheck, "secretary_account", lambda root=None: True):
            studio.after_check(OWNER, "voda")
        self.assertIn("проверил 5 кнопок, осталось 1", self.t.last)
        self.assertIn("откатил", self.t.last)
        self.assertNotIn("↩️ Откатить", self.t.buttons)
        self.assertEqual(read(os.path.join(path, "bot.py")), before)
        self.assertFalse(studio.job.locked())


class FakeReq:
    def __init__(self, path, headers=None):
        self.path, self.headers, self.code, self.out, self.sent = path, headers or {}, None, io.BytesIO(), {}
        self.wfile = self.out

    def send_response(self, code):
        self.code = code

    def send_header(self, k, v):
        (k + v).encode("latin-1")  # как настоящий http.server: не-latin-1 в заголовке роняет ответ (был 502 на пароле)
        self.sent[k] = v

    def end_headers(self):
        pass

    def reply(self, code, body, ctype="", cache=""):
        self.code = code


class Sites(StudioFlow):
    def setUp(self):
        shutil.rmtree(os.path.join(TMP, "sites"), ignore_errors=True)
        super().setUp()

    def make(self):
        self.f.interview = {"kind": "site", "questions": [], "names": ["Кофейня"], "emoji": "☕"}
        self.t.send("сделай сайт для кофейни")
        self.assertEqual(studio.draft()["kind"], "site")
        self.t.send("Кофейня")
        self.assertEqual(studio.draft()["stage"], "plan")
        self.assertIn("сайт", self.t.last)
        self.t.press("go")
        return studio.sites()["coffee"]

    def test_build_site_private_then_protect_and_publish(self):
        info = self.make()
        self.assertEqual(info["access"], "private")
        self.assertTrue(os.path.isfile(os.path.join(info["dir"], "index.html")))
        self.assertEqual(studio.draft()["stage"], "done")
        self.assertIn("Сайт «Кофейня» готов", self.t.last)
        self.assertIn("🔒 Защитить паролем", self.t.buttons)
        self.assertIn("🌍 Выпустить для всех", self.t.buttons)
        self.assertNotIn("coffee", studio.registry())  # Главный видит только ботов
        self.t.press("smode|coffee|password")
        info = studio.sites()["coffee"]
        self.assertEqual((info["access"], len(info["password"])), ("password", 8))
        self.t.press("spub?|coffee")
        self.assertIn("для всех", self.t.last)
        self.t.press("smode|coffee|public")
        self.assertEqual(studio.sites()["coffee"]["access"], "public")
        self.t.press("sites")
        self.assertIn("Кофейня", self.t.last)

    def test_serve_access_modes(self):
        info = self.make()
        key, root = info["key"], f"/s/coffee/"
        get = lambda path, **h: (lambda r: (studio.serve_site(r, "GET"), r)[1])(FakeReq(path, h))
        self.assertEqual(get(root).code, 403)  # без ключа
        self.assertEqual(get(root + "?k=bad").code, 403)
        r = get(root + f"?k={key}")  # ключ → cookie и редирект
        self.assertEqual((r.code, r.sent["Location"]), (302, root))
        self.assertIn(f"sk_coffee={key}", r.sent["Set-Cookie"])
        r = get(root, Cookie=f"sk_coffee={key}")
        self.assertEqual(r.code, 200)
        self.assertIn("Привет".encode(), r.out.getvalue())
        self.assertEqual(get(root + "../../etc/passwd", Cookie=f"sk_coffee={key}").code, 404)
        self.assertEqual(get(root + ".git/config", Cookie=f"sk_coffee={key}").code, 404)
        self.assertEqual(get("/s/nope/").code, 404)
        # пароль
        import base64
        pw = studio.set_access("coffee", "password")["password"]
        self.assertEqual(get(root).code, 401)
        bad = base64.b64encode(b"x:wrong").decode()
        ok = base64.b64encode(f"x:{pw}".encode()).decode()
        self.assertEqual(get(root, Authorization=f"Basic {bad}").code, 401)
        self.assertEqual(get(root, Authorization=f"Basic {ok}").code, 200)
        # все
        studio.set_access("coffee", "public")
        r = get(root)
        self.assertEqual(r.code, 200)
        self.assertNotIn("X-Robots-Tag", r.sent)
        # закрыть снова — ключ новый, старая ссылка не работает
        studio.set_access("coffee", "private")
        self.assertEqual(get(root + f"?k={key}").code, 403)

    def test_site_headers_are_latin1_and_bad_paths_404(self):
        """Кириллица в заголовке (realm, Location) раньше роняла ответ: UnicodeEncodeError → 502. Теперь %XX."""
        r = FakeReq("/s/x/")
        studio._site_page(r, 401, "Нужен пароль", "текст", {"WWW-Authenticate": 'Basic realm="Сайт «Кофейня»"',
                                                          "X-Test": "a\r\nSet-Cookie: x=1"})
        self.assertEqual(r.code, 401)
        self.assertIn("realm=", r.sent["WWW-Authenticate"])
        self.assertNotIn("\n", r.sent["X-Test"])  # перевод строки в заголовке — не вставить второй заголовок
        self.assertIn("Нужен пароль".encode(), r.out.getvalue())  # тело — в UTF-8, как и было
        info = self.make()
        studio.set_access("coffee", "public")
        for bad in ("/s/coffee/%00", "/s/coffee/%00.html", "/s/coffee/..%2f..%2fetc/passwd", "/s/coffee/%2e%2e/%2e%2e/x"):
            r = FakeReq(bad)
            self.assertTrue(studio.serve_site(r, "GET"))
            self.assertEqual(r.code, 404, bad)
        r = FakeReq("/s/coffee/\u0434\u0430")  # русские символы в пути запроса
        self.assertTrue(studio.serve_site(r, "GET"))
        self.assertEqual(r.code, 404)

    def test_password_brute_force_is_throttled(self):
        import base64
        self.make()
        pw = studio.set_access("coffee", "password")["password"]
        studio._pw_fails.clear()
        bad = base64.b64encode(b"x:wrong").decode()
        ok = base64.b64encode(f"x:{pw}".encode()).decode()
        codes = []
        for _ in range(studio.PW_TRIES + 3):
            r = FakeReq("/s/coffee/", {"Authorization": f"Basic {bad}"})
            studio.serve_site(r, "GET")
            codes.append(r.code)
        self.assertEqual(codes[:studio.PW_TRIES], [401] * studio.PW_TRIES)
        self.assertEqual(set(codes[studio.PW_TRIES:]), {429})
        r = FakeReq("/s/coffee/", {"Authorization": f"Basic {ok}"})  # пока блок — и верный пароль ждёт минуту
        studio.serve_site(r, "GET")
        self.assertEqual(r.code, 429)
        studio._pw_fails.clear()
        r = FakeReq("/s/coffee/", {"Authorization": f"Basic {ok}"})
        studio.serve_site(r, "GET")
        self.assertEqual(r.code, 200)
        self.assertEqual(r.sent["X-Content-Type-Options"], "nosniff")

    def test_proxy_rejects_huge_or_bad_content_length(self):
        studio.reg_set("demo", {"name": "Демо", "slug": "demo", "port": 1})
        try:
            for cl in ("999999999", "-5", "abc"):
                r = FakeReq("/b/demo/api/x", {"Content-Length": cl})
                self.assertTrue(studio.proxy(r, "POST"))
                self.assertEqual(r.code, 413, cl)
        finally:
            studio.reg_set("demo", None)

    def test_fix_site_and_delete(self):
        info = self.make()
        studio.fix(1, "coffee", "сделай шапку тёмной")
        self.assertIn("дорабатываешь сайт", self.f.prompts[-1])
        self.assertTrue(studio.remove_site("coffee"))
        self.assertFalse(os.path.exists(info["dir"]))
        self.assertEqual(studio.sites(), {})


def boot(bot):
    """bot.run() до первого getUpdates — то, что бот делает при запуске (без сети, профиля и поддержки)."""
    fake = bot._call

    def stop(method, params, files=None):
        if method == "getUpdates":
            raise SystemExit
        return fake(method, params, files)
    with mock.patch.object(bot, "setup"), mock.patch.object(bot, "_start_support"), \
            mock.patch.object(bot, "port", 0), mock.patch.object(bot, "jobs", []), mock.patch.object(bot, "_call", stop):
        try:
            bot.run()
        except SystemExit:
            pass


def kb_sent(t):
    """Сколько раз ставили меню-квадратик (sendMessage с обычной клавиатурой)."""
    return sum(1 for m, p in t.calls if m == "sendMessage" and "keyboard" in (p.get("reply_markup") or {}))


class StudioMenu(unittest.TestCase):
    """☰ Меню-квадратик Студии: свои кнопки, один раз на версию, /start — снова, нажатие — действие, а не идея."""
    setUp, tearDown = StudioFlow.setUp, StudioFlow.tearDown  # то же окружение, без повтора тестов StudioFlow

    def test_once_per_version_start_and_buttons(self):
        boot(studio.bot)
        self.assertEqual(kb_sent(self.t), 1)
        for btn in ("🆕 Новый бот", "🧩 Мои боты", "🌐 Мои сайты", "⚙️ Режим", "🛠 Поддержка"):
            self.assertIn(btn, self.t.kb)
        boot(studio.bot)
        self.assertEqual(kb_sent(self.t), 1)  # та же версия — при запуске не пишем
        self.t.send("/start")
        self.assertEqual(kb_sent(self.t), 2)
        self.assertIn("Студия", self.t.last)

    def test_press_runs_action_not_idea(self):
        self.t.send("🧩 Мои боты")
        self.assertIn("Мои боты", self.t.last)
        self.t.send("🆕 Новый бот")
        self.assertIn("Какой бот или сайт нужен", self.t.last)
        self.assertFalse(studio.draft().get("stage"))  # текст кнопки не стал идеей
        self.assertEqual(self.f.prompts, [])  # и в Claude не ушёл
        self.t.send("бот, который напоминает пить воду")  # ответ на вопрос — идея
        self.assertEqual(studio.draft()["stage"], "ask")
        self.t.send("🆕 Новый бот")  # черновик в работе — показать, где остановились
        self.assertIn("Вопрос 1 из 2", self.t.last)
        self.assertEqual(studio.draft()["stage"], "ask")

    def test_press_drops_text_wait(self):
        self.t.send("бот, который напоминает пить воду")
        d = studio.draft()
        self.t.press(f"ans|{d['id']}|0|1")
        self.t.press(f"ans|{d['id']}|1|0")
        self.assertEqual(studio.draft()["stage"], "name")
        self.t.send("🌐 Мои сайты")  # кнопка меню — не имя бота
        self.assertNotEqual(studio.draft().get("name"), "🌐 Мои сайты")
        self.assertIn("Мои сайты", self.t.last)


class KitMenu(unittest.TestCase):
    """☰ Меню-квадратик в botkit — у всех ботов Студии: из главного экрана, свои ряды, без меню."""

    def make(self, **kw):
        b = kit.Bot("T", token="1:x", data=tempfile.mkdtemp(), support={}, app_url="https://apps.x/b/t/", **kw)
        t = b.testing(owner=5)
        b.db.data["_mode"] = "chat"
        self.got = []

        @b.home
        def home(c):
            b.menu(c, "🧪 <b>T</b>", ["главный"],
                   buttons=[[kit.b("➕ Добавить", "add", "primary"), kit.url("🔗 Сайт", "https://x.ru")],
                            [kit.b("📊 Неделя", "week"), kit.b("📅 Месяц", "month"), kit.b("🏆 Цели", "goals")],
                            [kit.b("🔔 Напоминания", "rem"), kit.b("📤 Экспорт", "exp"), kit.b("🗑 Очистить", "clr")]])
        for name in ("add", "week", "month", "goals", "rem", "exp", "clr"):
            b.button(name)(lambda c, n=name: (self.got.append(n), c.say(f"экран {n}")))
        b.button("quiet")(lambda c: c.toast("Сделал"))
        b.reply("name")(lambda c: self.got.append("reply:" + c.text))
        b.text(lambda c: self.got.append("text:" + c.text))
        return b, t

    def test_default_from_main_menu(self):
        b, t = self.make()
        boot(b)
        self.assertEqual(kb_sent(t), 1)
        self.assertEqual(t.kb, ["➕ Добавить", "📊 Неделя", "📅 Месяц", "🏆 Цели", "🔔 Напоминания", "📤 Экспорт",
                                "⚙️ Режим", "🛠 Поддержка"])  # первые 6 (без url) + режим и поддержка
        self.assertFalse([p for m, p in t.calls if m == "sendMessage" and "inline_keyboard" in
                          (p.get("reply_markup") or {})])  # главный экран при запуске — вхолостую, в чат не ушёл
        mk = [p for m, p in t.calls if m == "sendMessage"][-1]["reply_markup"]
        self.assertEqual([len(r) for r in mk["keyboard"]], [3, 3, 2])
        self.assertTrue(mk["one_time_keyboard"] and mk["resize_keyboard"] and not mk["is_persistent"])
        boot(b)
        self.assertEqual(kb_sent(t), 1)  # та же версия — второй раз не ставим
        t.send("/start")
        self.assertEqual(kb_sent(t), 2)  # /start — всегда
        self.assertIn("главный", t.last)
        t.send("📊 Неделя")
        self.assertEqual(self.got, ["week"])  # то же, что inline-кнопка; не в @bot.text
        self.assertEqual(t.last, "экран week")
        t.send("⚙️ Режим")
        self.assertIn("Как удобнее", t.last)

    def test_new_version_once_and_old_texts_still_work(self):
        b, t = self.make(kb_version=1)
        boot(b)
        b.kb_version = 2
        boot(b)
        boot(b)
        self.assertEqual(kb_sent(t), 2)
        b.kb_cfg = [[kit.b("🏆 Цели", "goals")]]  # меню поменялось — старая кнопка на руках ещё работает
        t.send("📊 Неделя")
        self.assertEqual(self.got, ["week"])

    def test_press_drops_wait_and_toast_becomes_message(self):
        b, t = self.make(kb=[[kit.b("🤫 Тихо", "quiet"), kit.b("➕ Добавить", "add")], [kit.KB_SUPPORT]])
        c = kit.Ctx(b, 5, {"id": 5})
        c.ask("name", "Как назвать?")
        t.send("➕ Добавить")
        t.send("просто текст")
        self.assertEqual(self.got, ["add", "text:просто текст"])  # ожидание ответа снято нажатием
        t.send("🤫 Тихо")
        self.assertEqual(t.last, "Сделал")  # всплывашки нет — тот же текст сообщением
        t.send("🛠 Поддержка")
        self.assertIn("Поддержка", t.last)
        self.assertNotIn("text:🛠 Поддержка", self.got)

    def test_support_button_opens_fix(self):
        b, t = self.make(kb=[[kit.KB_SUPPORT]])
        seen = []
        b.support = mock.Mock(waiting={5: {"at": time.time()}})
        b.support.intercept.side_effect = lambda upd: seen.append(upd["message"]["text"]) or True
        t.send("🛠 Поддержка")
        self.assertEqual(seen, ["/fix"])
        self.assertNotIn(5, b.support.waiting)

    def test_no_menu_and_no_boot(self):
        b, t = self.make(kb=False)
        boot(b)
        t.send("/start")
        self.assertEqual(kb_sent(t), 0)
        t.send("📊 Неделя")
        self.assertEqual(self.got, ["text:📊 Неделя"])  # без меню — обычный текст
        b2, t2 = self.make(kb_boot=False)
        boot(b2)
        self.assertEqual(kb_sent(t2), 0)  # при запуске не пишет
        t2.send("/start")
        self.assertEqual(kb_sent(t2), 1)  # а на /start — ставит


class KitSync(unittest.TestCase):
    """Новый общий вид доходит до уже сделанных ботов: только kit.css/kit.js, только внутри папки ботов."""
    def setUp(self):
        shutil.rmtree(os.path.join(TMP, "bots"), ignore_errors=True)
        self.src = os.path.join(HERE, "kit", "template", "webapp")

    def make(self, slug, git=True, **info):
        path = os.path.join(studio.BOTS, slug)
        os.makedirs(os.path.join(path, "webapp"))
        for name, text in (("kit.css", "/* старый */"), ("kit.js", "// старый"), ("app.css", ":root{--accent:#f00}")):
            with open(os.path.join(path, "webapp", name), "w") as f:
                f.write(text)
        if git:
            studio.git(path, "init", "-q")
            studio.commit(path, "Первая версия")
        studio.reg_set(slug, {"name": slug, "slug": slug, "dir": path, **info})
        return path

    def test_copies_only_kit_and_commits(self):
        path = self.make("water")
        with open(os.path.join(path, "bot.py"), "w") as f:
            f.write("# правка в работе")  # чужая незакоммиченная правка — не в коммит Студии
        self.assertEqual(studio.sync_kits(), ["water"])
        for name in ("kit.css", "kit.js"):
            self.assertEqual(read(os.path.join(path, "webapp", name)), read(os.path.join(self.src, name)))
        self.assertEqual(read(os.path.join(path, "webapp", "app.css")), ":root{--accent:#f00}")
        self.assertIn("Общий дизайн", studio.git(path, "log", "-1", "--format=%s")[1])
        self.assertEqual(studio.git(path, "show", "--name-only", "--format=", "HEAD")[1].split(),
                         ["webapp/kit.css", "webapp/kit.js"])
        self.assertIn("bot.py", studio.git(path, "status", "--porcelain")[1])
        self.assertEqual(studio.sync_kits(), [])  # второй раз — уже одинаковые, без коммита
        self.assertEqual(studio.git(path, "rev-list", "--count", "HEAD")[1].strip(), "2")

    def test_botkit_updated_only_if_present_and_bot_restarted(self):
        path = self.make("water", pm2="bot-water")
        with open(os.path.join(path, "botkit.py"), "w") as f:
            f.write("# старый botkit")
        studio.git(path, "add", "botkit.py")
        studio.commit(path, "botkit")
        nokit = self.make("nokit", pm2="bot-nokit")
        calls = []
        fake = lambda *a, **k: (calls.append(a), (0, ""))[1] if a[0] == "pm2" else REAL_SH(*a, **k)  # noqa: E731
        with mock.patch.object(studio, "sh", fake):
            self.assertEqual(sorted(studio.sync_kits()), ["nokit", "water"])
        self.assertEqual(read(os.path.join(path, "botkit.py")), read(os.path.join(HERE, "kit", "botkit.py")))
        self.assertFalse(os.path.exists(os.path.join(nokit, "botkit.py")))  # чего не было — не создаём
        self.assertIn("botkit.py", studio.git(path, "show", "--name-only", "--format=", "HEAD")[1].split())
        self.assertEqual(calls, [("pm2", "restart", "bot-water")])  # новый botkit — перезапуск, только у него

    def test_skips_outside_sites_and_links(self):
        outside = os.path.join(TMP, "outside")
        os.makedirs(os.path.join(outside, "webapp"), exist_ok=True)
        with open(os.path.join(outside, "webapp", "kit.css"), "w") as f:
            f.write("чужое")
        studio.reg_set("evil", {"name": "evil", "dir": outside})
        site = self.make("site", git=False, kind="site")
        nogit = self.make("nogit", git=False)
        self.assertEqual(studio.sync_kits(), ["nogit"])
        self.assertEqual(read(os.path.join(outside, "webapp", "kit.css")), "чужое")
        self.assertEqual(read(os.path.join(site, "webapp", "kit.css")), "/* старый */")
        self.assertEqual(read(os.path.join(nogit, "webapp", "kit.js")), read(os.path.join(self.src, "kit.js")))


class Kit(unittest.TestCase):
    def test_style_helpers(self):
        self.assertEqual(kit.clip("x" * 30), "x" * 23 + "…")
        self.assertIn("<blockquote>a</blockquote>", kit.card("🧪 <b>T</b>", ["a"]))
        self.assertIn("expandable", kit.card("t", ["x" * 800]))
        self.assertEqual(kit.plural(3, "раз", "раза", "раз"), "3 раза")
        self.assertEqual(kit.plural(11, "раз", "раза", "раз"), "11 раз")
        self.assertEqual(kit.parse_days("пн-пт"), {0, 1, 2, 3, 4})
        self.assertEqual(kit.parse_days("сб,вс"), {5, 6})
        self.assertEqual(kit.rows([1, None, 2, 3], 2), [[1, 2], [3]])

    def test_callback_data_fits_64_bytes(self):
        """Кириллица в callback_data — 2 байта на букву: длиннее 64 байт Telegram не принимает всё сообщение."""
        data = kit.b("x", "ideas|" + "космос" * 20)["callback_data"]
        self.assertLessEqual(len(data.encode()), 64)
        self.assertTrue(data.startswith("ideas|косм"))

    def test_buttons_always_answered_and_waits_dropped(self):
        b = kit.Bot("T", token="1:x", data=tempfile.mkdtemp(), support=False)
        t = b.testing(owner=5)

        @b.button("boom")
        def boom(c):
            raise RuntimeError("сломалось")

        @b.button("bad")
        def bad(c):
            raise kit.Fail("Нет такого")

        @b.button("ask")
        def ask(c):
            c.ask("name", "Как назвать?", [[kit.b("Отмена", "back")]])

        got = []
        b.reply("name")(lambda c: got.append(c.text))
        b.text(lambda c: got.append("free:" + c.text))
        b.button("back")(lambda c: c.toast("ок"))

        t.press("boom")
        self.assertEqual(t.toasts[-1], "⚠️ Не получилось — попробуй ещё раз")  # крутилка не висит
        self.assertIn("Что-то пошло не так", t.last)
        self.assertEqual(t.buttons, ["‹ Главное"])
        t.press("bad")
        self.assertEqual(t.toasts[-1], "⚠️ Нет такого")
        self.assertIn("⚠️ Нет такого", t.last)
        t.press("old|1")
        self.assertIn("устарела", t.toasts[-1])
        t.press("ask")
        t.press("back")  # своя «Отмена» тоже снимает ожидание
        t.send("Вася")
        self.assertEqual(got, ["free:Вася"])
        t.press("ask")
        t.send("Петя")
        self.assertEqual(got[-1], "Петя")

    def test_background_error_is_said(self):
        b = kit.Bot("T", token="1:x", data=tempfile.mkdtemp(), support=False)
        t = b.testing(owner=5)

        def slow(chat):
            raise RuntimeError("сеть")
        b._safe(slow, 5)  # фон (bot.later вне тестов): ошибка — в чат владельца, а не тишина
        self.assertIn("Что-то пошло не так", t.last)
        self.assertEqual(b.call_api("mode", {"id": 5}, ["не", "dict"])[0], 400)  # тело не объект — понятный отказ, не 500

    def test_profile_not_reset_on_every_start(self):
        """setMyName у Telegram — пара раз в сутки, иначе 429 на ~20 ч: при каждом старте бот спрашивал и ставил имя
        заново (десятки 429 в логах). Теперь — только если отличается, и после 429 ждём retry_after."""
        d = tempfile.mkdtemp()
        calls, state = [], {"name": "Старое", "flood": True}

        def make():
            b = kit.Bot("T", token="1:x", data=d, tg_name="Catcher", about="О боте", short="Кратко",
                        commands=[("start", "Главное")], support=False)

            def fake(method, params, files=None):
                calls.append(method)
                if method == "getMyName":
                    return {"ok": True, "result": {"name": state["name"]}}
                if method == "getMyDescription":
                    return {"ok": True, "result": {"description": "О боте"}}
                if method == "getMyShortDescription":
                    return {"ok": True, "result": {"short_description": "Кратко"}}
                if method == "getMyCommands":
                    return {"ok": True, "result": [{"command": "start", "description": "Главное"},
                                                   {"command": "mode", "description": "Чат или приложение"}]}
                if method == "setMyName":
                    if state["flood"]:
                        e = urllib.error.HTTPError("u", 429, "Too Many Requests", {}, None)
                        e.detail = '{"ok":false,"error_code":429,"parameters":{"retry_after":72000}}'
                        raise e
                    state["name"] = params["name"]
                return {"ok": True, "result": {"username": "t_bot"}}
            b._call = fake
            return b

        make().setup()
        self.assertEqual(calls.count("setMyName"), 1)       # попробовал, получил 429
        self.assertNotIn("setMyDescription", calls)         # описание уже такое — не трогаем
        make().setup()
        make().setup()
        self.assertEqual(calls.count("setMyName"), 1)       # дальше — тишина до retry_after, а не 429 на каждом старте
        self.assertEqual(calls.count("getMyName"), 1)
        # срок вышел и 429 больше нет — ставим один раз, потом не повторяем
        db = kit.Store(os.path.join(d, "db.json"))
        db.set("_tg_wait", {"setMyName": time.time() - 1})
        state["flood"] = False
        make().setup()
        self.assertEqual(calls.count("setMyName"), 2)
        make().setup()
        self.assertEqual(calls.count("setMyName"), 2)

    def test_server_errors_are_500_not_dropped(self):
        """Обработчик упал — клиент получает 500 с понятным текстом, а не обрыв соединения."""
        import http.client
        port = free_port()
        b = kit.Bot("T", token="1:x", data=tempfile.mkdtemp(), port=port, support=False)
        b.http_hook = lambda h, m: 1 / 0
        threading.Thread(target=b.serve, daemon=True).start()
        time.sleep(0.4)
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("GET", "/x")
        r = c.getresponse()
        self.assertEqual(r.status, 500)
        self.assertIn("Ошибка", r.read().decode())

    def test_style_fallback(self):
        b = kit.Bot("T", token="1:x", data=tempfile.mkdtemp())
        calls = []

        def fake(method, params, files=None):
            calls.append(params)
            if any("style" in btn for row in params["reply_markup"]["inline_keyboard"] for btn in row):
                e = urllib.error.HTTPError("u", 400, "bad", {}, None)
                e.detail = "Bad Request: can't parse inline keyboard button"
                raise e
            return {"ok": True, "result": {"message_id": 5}}
        b._call = fake
        self.assertEqual(b.send(1, "hi", [[kit.b("a", "a", "primary")]]), 5)
        self.assertTrue(b.plain_buttons)

    def test_template_and_http(self):
        """Заготовка: тесты проходят, сервер отдаёт страницу и api с подписью Telegram; прокси Студии до неё."""
        d = tempfile.mkdtemp()
        path = os.path.join(d, "demo")
        shutil.copytree(os.path.join(HERE, "kit", "template"), path)
        shutil.copy(os.path.join(HERE, "kit", "botkit.py"), path)
        code, out = REAL_SH(sys.executable, "test_bot.py", cwd=path, timeout=120, env={**os.environ, "BOT_TOKEN": ""})
        self.assertEqual(code, 0, out)

        b = kit.Bot("Demo", token="1:abc", port=free_port(), data=os.path.join(path, "data"),
                    webapp=os.path.join(path, "webapp"))
        b.db.set("_owner", OWNER)

        @b.api("ping")
        def ping(r):
            return {"pong": r.body.get("x")}
        threading.Thread(target=b.serve, daemon=True).start()
        base = f"http://127.0.0.1:{b.port}"
        wait_up(base)
        html = urllib.request.urlopen(base + "/").read().decode()
        self.assertIn("kit.css?v=", html)
        self.assertNotIn("{{v}}", html)
        self.assertIn(".tabs", urllib.request.urlopen(base + "/kit.css").read().decode())
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + "/../botkit.py")
        self.assertEqual(e.exception.code, 404)
        req = urllib.request.Request(base + "/api/ping", data=b'{"x": 1}', headers={
            "X-Init-Data": signed("1:abc", {"id": OWNER}), "Content-Type": "application/json"})
        self.assertEqual(json.load(urllib.request.urlopen(req)), {"pong": 1})
        # 🛠 встроенный api("support"): просьба со скриншотом → Support.web → start (заглушка), только владельцу
        sys.path.insert(0, os.path.join(os.path.dirname(HERE), "common"))
        import support
        sup = support.Support.__new__(support.Support)
        sup.allowed, sup.media_dir, calls = (lambda u: u == OWNER), tempfile.mkdtemp(), []
        sup.start = lambda chat, text, shots=None: calls.append((chat, text, shots))
        self.assertEqual(b.call_api("support", {"id": OWNER}, {"text": "x"})[0], 400)  # поддержка не запущена
        b.support = sup
        shot = __import__("base64").b64encode(b"\x89PNG" + bytes(2_000_000)).decode()  # больше 1 МБ
        req = urllib.request.Request(base + "/api/support", data=json.dumps({"text": "кнопка не жмётся", "photo": shot})
                                     .encode(), headers={"X-Init-Data": signed("1:abc", {"id": OWNER}),
                                                         "Content-Type": "application/json"})
        self.assertEqual(json.load(urllib.request.urlopen(req)), {"ok": True})
        self.assertEqual((calls[-1][0], os.path.exists(calls[-1][2][0])), (OWNER, True))
        self.assertEqual(b.call_api("support", {"id": 999}, {"text": "x"})[0], 403)
        self.assertEqual(b.call_api("support", {"id": OWNER}, {"text": ""})[0], 400)
        self.assertEqual(len(calls), 1)
        self.assertIn("data-support", urllib.request.urlopen(base + "/kit.js").read().decode())  # строка в приложении
        bad = urllib.request.Request(base + "/api/ping", headers={"X-Init-Data": signed("1:other", {"id": OWNER})})
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(bad)
        self.assertEqual(e.exception.code, 401)

        # прокси Студии: /b/demo/… → этот бот
        studio.reg_set("demo", {"name": "Demo", "slug": "demo", "port": b.port, "dir": path, "pm2": "bot-demo"})
        sport = free_port()
        studio.bot.port = sport
        threading.Thread(target=studio.bot.serve, daemon=True).start()
        sbase = f"http://127.0.0.1:{sport}"
        wait_up(sbase)
        self.assertIn("kit.css", urllib.request.urlopen(sbase + "/b/demo/").read().decode())
        req = urllib.request.Request(sbase + "/b/demo/api/ping", data=b'{"x": 2}', headers={
            "X-Init-Data": signed("1:abc", {"id": OWNER}), "Content-Type": "application/json"})
        self.assertEqual(json.load(urllib.request.urlopen(req)), {"pong": 2})
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(sbase + "/b/nope/")
        self.assertEqual(e.exception.code, 404)
        studio.reg_set("demo", None)

    def test_avatar(self):
        out = os.path.join(tempfile.mkdtemp(), "a.png")
        code, msg = REAL_SH(sys.executable, os.path.join(HERE, "avatar.py"), out, "В", "#0ea5e9", "#6366f1", timeout=120)
        self.assertEqual(code, 0, msg)
        with open(out, "rb") as f:
            self.assertEqual(f.read(4), b"\x89PNG")


def read(path):
    with open(path) as f:
        return f.read()


def free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class SitesOnlyMode(unittest.TestCase):
    """STUDIO_MODE=sites — «Студия сайтов» для другого человека: только сайты, без /fix, Claude только в своей папке."""

    def test_sites_mode_is_locked_down(self):
        import subprocess
        tmp = tempfile.mkdtemp(prefix="studio-sites-")
        code = r"""
import json, os, subprocess, sys
sys.path.insert(0, os.environ["HERE"])
import studio
seen = []; envs = []; cwds = []
class P:
    def __init__(self, cmd, **kw):
        seen.append(cmd); envs.append(kw.get("env")); cwds.append(kw.get("cwd")); self.stdout = iter([json.dumps({"type": "result", "result": "{}"}) + "\n"]); self.returncode = 0
    def wait(self): return 0
    def kill(self): pass
orig, studio.subprocess.Popen = studio.subprocess.Popen, P
studio.claude("сделай сайт для Глеба", os.environ["STUDIO_BOTS_DIR"])
studio.claude("вопросы", os.environ["STUDIO_BOTS_DIR"], tools=False)
studio.talk("вопросы")
studio.subprocess.Popen = orig
st = studio.api_state(None)
from unittest import mock
t = studio.bot.testing(owner=77)
fake = studio.bot._call
def stop(method, params, files=None):
    if method == "getUpdates":
        raise SystemExit
    return fake(method, params, files)
with mock.patch.object(studio.bot, "setup"), mock.patch.object(studio.bot, "port", 0), mock.patch.object(studio.bot, "_call", stop):
    try:
        studio.bot.run()
    except SystemExit:
        pass
kbs = lambda: [p for m, p in t.calls if m == "sendMessage" and "keyboard" in (p.get("reply_markup") or {})]
boot_kb = len(kbs())
t.send("/start")
start_kb = [b["text"] for r in kbs()[-1]["reply_markup"]["keyboard"] for b in r] if kbs() else []
t.send("🆕 Новый сайт")
ask = t.last
print(json.dumps({"support": studio.bot.support is None and studio.bot.support_cfg is False,
                  "boot_kb": boot_kb, "start_kb": start_kb, "ask": ask,
                  "build": seen[0], "talk": seen[1], "tg_name": studio.bot.tg_name,
                  "sites_only": st["sites_only"], "examples": st["examples"], "secretary": st["secretary"],
                  "leak": [k for e in envs for k in (e or {}) if k == "STUDIO_TOKEN" or k.startswith(("HORO_", "HUB_"))], "no_env": any(e is None for e in envs),
                  "talk_cwd": cwds[-1]}))
"""
        env = {**os.environ, "HERE": HERE, "STUDIO_MODE": "sites", "STUDIO_TG_NAME": "Site Studio",
               "STUDIO_BOTS_DIR": os.path.join(tmp, "work"), "STUDIO_SITES_DIR": os.path.join(tmp, "work", "sites"),
               "STUDIO_DATA": os.path.join(tmp, "data"), "STUDIO_PORT": "0"}
        os.makedirs(env["STUDIO_BOTS_DIR"], exist_ok=True)
        out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr[-2000:])
        r = json.loads(out.stdout.strip().splitlines()[-1])
        self.assertTrue(r["support"])  # /fix выключен: правка кода сервера недоступна
        self.assertTrue(r["sites_only"])
        self.assertFalse(r["secretary"])
        self.assertEqual(r["leak"], [])  # токен бота из /etc/studio-sites.env Claude не передаётся
        self.assertFalse(r["no_env"])
        self.assertTrue(r["talk_cwd"].endswith("/talk"))  # не папка с реестром сайтов (пароли) и базой
        self.assertEqual(r["boot_kb"], 0)  # ☰ меню при запуске сестре не пишем — только на её /start
        self.assertEqual(r["start_kb"], ["🆕 Новый сайт", "🌐 Мои сайты", "🏠 Главное"])
        self.assertIn("Какой сайт нужен", r["ask"])
        deny = " ".join(r["build"])
        self.assertIn("Read(//srv/studio-sites/sites.json)", deny)
        self.assertEqual(r["tg_name"], "Site Studio")
        self.assertFalse([x for x in r["examples"] if any(w.startswith("бот") for w in x.split())])
        build, talk = r["build"], r["talk"]
        for cmd in (build, talk):
            allowed = cmd[cmd.index("--allowedTools") + 1:cmd.index("--disallowedTools")]
            denied = cmd[cmd.index("--disallowedTools") + 1:]
            self.assertNotIn("Read", allowed)  # голый Read читал бы что угодно
            self.assertFalse([t for t in allowed if t.startswith("Bash")])
            for must in ("Bash", "WebFetch", "Read(//root/**)", "Read(//etc/**)", "Edit(//root/**)"):
                self.assertIn(must, denied)
            self.assertNotIn("Глеб", cmd[2])  # чужой человек: не «Глеб»
            self.assertIn("Студия сайтов", cmd[2])
        self.assertIn("Write(./**)", build)
        self.assertNotIn("Write(./**)", talk)
        self.assertIn("Write", talk[talk.index("--disallowedTools") + 1:])


def wait_up(base):
    for _ in range(50):
        try:
            urllib.request.urlopen(base + "/health")
            return
        except Exception:
            time.sleep(0.05)


if __name__ == "__main__":
    import urllib.error  # noqa: F401
    unittest.main()
