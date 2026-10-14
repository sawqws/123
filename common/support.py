"""🛠 Поддержка в каждом боте: Глеб пишет боту, что сломалось или что улучшить, — Claude Code на сервере правит
код этого бота, гоняет его тесты, коммитит (локально) и перезапускает бота. «↩️ Откатить» — git revert.

Подключение (одна строка в цикле getUpdates, до своих обработчиков):

    import support
    SUP = support.Support(TOKEN, "Авито", folders=["avito"], pm2="avito-bot", allowed=allowed,
                          tests=["python3 avito/test_avito.py"])
    ...
    if SUP.intercept(update):   # /fix, ответ на «что поправить?», кнопки sup|…
        continue

Кнопка для своих меню — SUP.button(). Команду /fix модуль сам добавляет в меню команд бота.
Из мини-приложения (Kit.support в common/webapp/kit.js): POST /api/support -> SUP.web(uid, body).
Голосовые и скриншоты к просьбе понимает (common/media.py). Одна задача Claude за раз на весь сервер (файловая
блокировка): если другой бот уже чинится, просьба ждёт очереди. Только стандартная библиотека.
"""
import fcntl
import html
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CLAUDE_TIMEOUT = 40 * 60
WAIT_TTL = 15 * 60
LOCK = os.environ.get("SUPPORT_LOCK") or "/tmp/horo-support.lock"
TOOLS = ["Read", "Write", "Edit", "Glob", "Grep", "Bash(python3:*)", "Bash(node:*)", "Bash(ls:*)", "Bash(cat:*)",
         "Bash(git diff:*)", "Bash(git status:*)", "Bash(git log:*)", "Bash(mkdir:*)"]

PROMPT = """Ты — поддержка Telegram-бота «{name}» Глеба. Код бота: {where}.
Сначала прочитай README (и CLAUDE.md в корне репозитория, если есть) и код, чтобы понять устройство и правила.

Глеб пишет (ошибка, которую он заметил, или что улучшить):
{text}{shots}{logs}

Что делать:
- Ошибка — найди причину (логи выше, код) и почини её, а не симптом. Улучшение — сделай аккуратно, в стиле кода:
  просто, красиво, коротко; если у бота есть мини-приложение — то же самое и там.
- Меняй только код этого бота ({folders}). Не трогай секреты (.env, /etc/*.env), данные (data/, tmp/) и чужих ботов.
- Тесты бота: {tests}. Добавь тест на новое поведение и прогони — должны пройти.
- Не запускай сам бот и не перезапускай pm2 — это сделает поддержка.
- Если это вопрос, а не просьба что-то поменять, — ничего не меняй и просто ответь.
- Если просьба непонятна или опасна — ничего не меняй и объясни, что уточнить.
В конце ответь Глебу 2–5 строками по-русски, на «ты», без кода и без слов про перезапуск: что было не так
и что сделано (или ответ на вопрос)."""


def esc(x):
    return html.escape(str(x), quote=False)


class Support:
    def __init__(self, token, name, folders, pm2, allowed, tests=(), cwd=REPO, media_dir=None):
        self.token, self.name, self.folders, self.pm2 = token, name, list(folders), pm2
        self.allowed, self.tests, self.cwd = allowed, list(tests), cwd
        self.media_dir = media_dir or os.path.join("/tmp", f"support-{pm2}")
        self.waiting = {}  # chat -> {"at", "shots"}
        self.plain = False  # Telegram не принял цвет кнопок
        threading.Thread(target=self._commands, daemon=True).start()

    # ---------- Telegram ----------

    def tg(self, method, **params):
        def call(p):
            req = urllib.request.Request(f"https://api.telegram.org/bot{self.token}/{method}",
                                         data=json.dumps(p, ensure_ascii=False).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        mk = params.get("reply_markup")
        if self.plain and mk:
            params["reply_markup"] = mk = _no_style(mk)
        try:
            return call(params)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            if "not modified" in detail:
                return {"ok": True, "result": {}}
            if e.code == 400 and mk and _no_style(mk) != mk:
                self.plain = True
                return call({**params, "reply_markup": _no_style(mk)})
            raise

    def send(self, chat, text, rows=None):
        r = self.tg("sendMessage", chat_id=chat, text=text[:4096], parse_mode="HTML",
                    link_preview_options={"is_disabled": True},
                    **({"reply_markup": {"inline_keyboard": rows}} if rows else {}))
        return (r.get("result") or {}).get("message_id")

    def edit(self, chat, mid, text, rows=None):
        try:
            self.tg("editMessageText", chat_id=chat, message_id=mid, text=text[:4096], parse_mode="HTML",
                    link_preview_options={"is_disabled": True}, reply_markup={"inline_keyboard": rows or []})
        except Exception:
            self.send(chat, text, rows)

    @staticmethod
    def button(text="🛠 Поддержка"):
        return {"text": text, "callback_data": "sup|ask"}

    def _commands(self):
        """Добавить /fix в меню команд (бот ставит свои при запуске — ждём и дописываем; раз в час проверяем)."""
        time.sleep(30)
        while True:
            try:
                cur = self.tg("getMyCommands").get("result") or []
                if not any(c.get("command") == "fix" for c in cur):
                    self.tg("setMyCommands", commands=cur + [{"command": "fix",
                                                             "description": "Поддержка: ошибка или доработка"}])
            except Exception as e:
                print("support commands:", e, flush=True)
            time.sleep(3600)

    # ---------- вход ----------

    def intercept(self, upd):
        """True — обновление про поддержку и уже обработано."""
        try:
            cq = upd.get("callback_query")
            if cq:
                data = (cq.get("data") or "").split("|")
                if data[0] != "sup":
                    return False
                note = None
                if not self.allowed((cq.get("from") or {}).get("id")):
                    note = "🔒 Поддержка — только для владельца бота"  # гость (КПП) видит кнопку в меню
                elif not self.on_button(cq, data):
                    note = "Кнопка устарела — /fix"  # старая или битая кнопка: не молчим
                try:
                    self.tg("answerCallbackQuery", callback_query_id=cq["id"], **({"text": note} if note else {}))
                except Exception:
                    pass
                return True
            msg = upd.get("message") or {}
            chat = (msg.get("chat") or {}).get("id")
            if not chat or (msg.get("chat") or {}).get("type") != "private" or \
                    not self.allowed((msg.get("from") or {}).get("id")):
                return False
            text = (msg.get("text") or msg.get("caption") or "").strip()
            cmd = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
            if cmd in ("/fix", "/support"):
                rest = text[len(text.split()[0]):].strip()
                if rest:
                    self.waiting.pop(chat, None)
                    self.start(chat, rest)
                else:
                    self.ask(chat)
                return True
            w = self.waiting.get(chat)
            if not w or time.time() - w["at"] > WAIT_TTL:
                self.waiting.pop(chat, None)
                return False
            if cmd:  # любая другая команда — выходим из поддержки
                self.waiting.pop(chat, None)
                return False
            kind = self._kind(msg)
            if kind == "photo":
                threading.Thread(target=self._photo, args=(chat, msg, text), daemon=True).start()
                return True
            if kind == "voice":
                threading.Thread(target=self._voice, args=(chat, msg), daemon=True).start()
                return True
            if text:
                self.waiting.pop(chat, None)
                self.start(chat, text, w.get("shots"))
                return True
            return False
        except Exception as e:
            print("support:", e, flush=True)
            return False

    @staticmethod
    def _kind(msg):
        if msg.get("voice") or msg.get("audio") or msg.get("video_note"):
            return "voice"
        if msg.get("photo") or ((msg.get("document") or {}).get("mime_type") or "").startswith("image/"):
            return "photo"
        return None

    def ask(self, chat, mid=None):
        self.waiting[chat] = {"at": time.time(), "shots": []}
        text = (f"🛠 <b>Поддержка «{esc(self.name)}»</b>\n<blockquote>Напиши, что сломалось или чего не хватает — "
                "починю/добавлю. Можно текстом или голосом, со скриншотами: сам поправлю код, проверю тестами "
                "и перезапущу бота.</blockquote>\n<i>Например: «кнопка Неделя ничего не показывает» или «добавь итог за месяц».</i>")
        rows = [[{"text": "Отмена", "callback_data": "sup|no"}]]
        (self.edit(chat, mid, text, rows) if mid else self.send(chat, text, rows))

    def _media(self):
        sys.path.insert(0, HERE)
        import media
        return media

    def _photo(self, chat, msg, caption):
        try:
            path = self._media().photo_path(self.token, msg, self.media_dir)
        except Exception as e:
            return self.send(chat, f"⚠️ Скриншот не скачался: {esc(e)}")
        w = self.waiting.setdefault(chat, {"at": time.time(), "shots": []})
        w["shots"].append(path)
        if caption:
            self.waiting.pop(chat, None)
            return self.start(chat, caption, w["shots"])
        if len(w["shots"]) == 1:
            self.send(chat, "📎 Скриншот приложу — теперь напиши или надиктуй, что не так.")

    def _voice(self, chat, msg):
        try:
            text = self._media().voice_text(self.token, msg)
        except Exception as e:
            return self.send(chat, f"⚠️ Голосовое не разобрал: {esc(e)}")
        self.send(chat, f"🎙 <i>«{esc(text)}»</i>")
        w = self.waiting.pop(chat, None) or {}
        self.start(chat, text, w.get("shots"))

    def on_button(self, cq, data):
        """True — кнопка понятна и обработана; False — старая или битая."""
        msg = cq.get("message") or {}
        chat, mid = (msg.get("chat") or {}).get("id"), msg.get("message_id")
        if data[1:2] == ["ask"]:
            self.ask(chat, None)
        elif data[1:2] == ["no"]:
            self.waiting.pop(chat, None)
            self.edit(chat, mid, "🛠 Поддержка — отменено.")
        elif data[1:2] == ["undo"] and len(data) > 2 and re.fullmatch(r"[0-9a-f]{6,40}", data[2]):
            threading.Thread(target=self.undo, args=(chat, data[2]), daemon=True).start()
        else:
            return False
        return True

    def web(self, uid, body):
        """Просьба из мини-приложения (Kit.support): {"text", "photo": base64 картинки?} -> {"ok": True}.
        Чат владельца — его id (личка). PermissionError — не владелец, ValueError — пустой текст или битое фото."""
        if not self.allowed(uid):
            raise PermissionError("Поддержка — только для владельца")
        body = body if isinstance(body, dict) else {}  # список, число, мусор из JSON — как пустая просьба
        text = str(body.get("text") or "").strip()[:4000]
        if not text:
            raise ValueError("Напиши, что поправить")
        shots = []
        photo = body.get("photo")
        if photo:
            shots.append(self.save_photo(photo))
        self.start(uid, text + "\n\n(написано в мини-приложении)", shots)
        return {"ok": True}

    def save_photo(self, b64):
        """base64 (можно data:URL) -> путь к файлу в media_dir. Не больше 10 МБ."""
        import base64
        import binascii
        b64 = str(b64)
        if b64.startswith("data:"):
            b64 = b64.split(",", 1)[-1]
        try:
            raw = base64.b64decode(b64, validate=False)
        except (binascii.Error, ValueError):
            raise ValueError("Скриншот не разобрался")
        if not raw or len(raw) > 10 * 1024 * 1024:
            raise ValueError("Скриншот пустой или больше 10 МБ")
        ext = ".png" if raw[:4] == b"\x89PNG" else ".jpg"
        os.makedirs(self.media_dir, exist_ok=True)
        path = os.path.join(self.media_dir, f"web-{int(time.time() * 1000)}{ext}")
        with open(path, "wb") as f:
            f.write(raw)
        return path

    # ---------- работа ----------

    def start(self, chat, text, shots=None):
        threading.Thread(target=self.run, args=(chat, text, shots or []), daemon=True).start()

    def git(self, *a, timeout=120):
        return self.sh("git", "-C", self.cwd, *a, timeout=timeout)

    @staticmethod
    def sh(*cmd, timeout=120, cwd=None):
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
            return p.returncode, (p.stdout or "") + (p.stderr or "")
        except FileNotFoundError:
            return 127, f"нет программы {cmd[0]}"
        except subprocess.TimeoutExpired:
            return 124, "не уложилось по времени"

    def pathspec(self):
        """Только код этого бота: данные (data/, tmp/) и чужие папки — не «правка» и в коммит не попадают."""
        return [*self.folders, *(f":(exclude,glob)**/{d}/**" for d in ("data", "tmp", ".venv", "__pycache__"))]

    def logs(self):
        code, out = self.sh("pm2", "logs", self.pm2, "--lines", "40", "--nostream", "--raw", timeout=30)
        return out.strip()[-3000:] if code == 0 else ""

    def run(self, chat, text, shots):
        st = Status(self, chat, f"Поддержка «{self.name}»")
        lock = open(LOCK, "a")
        try:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                st.step("жду очереди: Claude чинит другого бота", force=True)
                fcntl.flock(lock, fcntl.LOCK_EX)
            self._run(st, text, shots)
        except Exception as e:
            st.done(f"⚠️ <b>Поддержка сломалась</b>\n<blockquote>{esc(e)}</blockquote>", [[self.button("🔁 Ещё раз")]])
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()

    def _run(self, st, text, shots):
        st.step("читаю код и логи", force=True)
        logs = self.logs()
        where = (f"репозиторий {self.cwd}, папки " + ", ".join(self.folders)) if self.folders != ["."] else \
            f"папка {self.cwd}"
        prompt = PROMPT.format(
            name=self.name, where=where, text=text, folders=", ".join(self.folders),
            tests=", ".join(self.tests) or "нет — проверь хотя бы python3 -m py_compile",
            shots=("\n\nСкриншоты от Глеба — открой каждый инструментом Read:\n" + "\n".join(shots)) if shots else "",
            logs=("\n\nПоследние строки логов бота (pm2):\n" + logs) if logs else "")
        ok, out = claude(prompt, self.cwd, st.step)
        if not ok:
            return st.done(f"😕 <b>Не получилось</b>\n<blockquote>{esc(out)}</blockquote>", [[self.button("🔁 Ещё раз")]])
        code, diff = self.git("status", "--porcelain", "--", *self.pathspec())
        if not diff.strip():
            return st.done(f"ℹ️ <b>{esc(self.name)}</b> — код не менялся\n<blockquote expandable>{esc(out[-1500:])}"
                           "</blockquote>", [[self.button("🛠 Ещё просьба")]])
        st.step("проверяю тестами", force=True)
        good, log = self.check()
        if not good:
            st.step("тесты упали — чиню", force=True)
            claude(f"Тесты бота «{self.name}» после твоей правки не проходят — почини код (не ослабляй тесты):\n"
                   + log[-3000:], self.cwd, st.step)
            good, log = self.check()
        if not good:
            self.git("checkout", "--", *self.pathspec())
            self.git("clean", "-fdq", "--", *self.pathspec())
            return st.done(f"😕 <b>Правка не прошла тесты — ничего не менял</b>\n<blockquote expandable>{esc(log[-1200:])}"
                           "</blockquote>\n<i>Опиши подробнее или по-другому — попробую ещё.</i>",
                           [[self.button("🛠 Ещё раз")]])
        self.git("add", "-A", "--", *self.pathspec())
        code, _ = self.git("-c", "user.name=Поддержка", "-c", "user.email=support@localhost", "commit", "-q",
                           "-m", f"Поддержка ({self.name}): {text[:200]}")
        sha = self.git("rev-parse", "--short=10", "HEAD")[1].strip() if code == 0 else ""
        st.done(f"✅ <b>{esc(self.name)} — готово</b>\n<blockquote expandable>{esc(out[-1500:])}</blockquote>\n"
                "<i>Перезапускаюсь — через пару секунд проверь.</i>",
                [[{"text": "↩️ Откатить", "callback_data": f"sup|undo|{sha}", "style": "danger"},
                  self.button("🛠 Ещё")]] if sha else None)
        self.restart()

    def check(self):
        if not self.tests:
            return True, ""
        logs = []
        for t in self.tests:
            code, out = self.sh("bash", "-c", t, timeout=900, cwd=self.cwd)
            logs.append(f"$ {t}\n{out[-2000:]}")
            if code != 0:
                return False, "\n".join(logs)
        return True, "\n".join(logs)

    def restart(self):
        """Перезапуск себя через pm2 — отдельным процессом, чтобы ответ успел уйти."""
        subprocess.Popen(["bash", "-c", f"sleep 3; pm2 restart {self.pm2} >/dev/null 2>&1"],
                         start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def undo(self, chat, sha):
        if not re.fullmatch(r"[0-9a-f]{6,40}", sha or ""):
            return
        code, out = self.git("-c", "user.name=Поддержка", "-c", "user.email=support@localhost", "revert",
                             "--no-edit", sha)
        if code != 0:
            self.git("revert", "--abort")
            return self.send(chat, f"⚠️ Не откатилось\n<pre>{esc(out[-500:])}</pre>")
        self.send(chat, f"↩️ <b>Правка отменена</b>\n<blockquote>«{esc(self.name)}» перезапускается.</blockquote>")
        self.restart()


class Status:
    """Живой статус в одном сообщении: что делает Claude и сколько прошло."""

    def __init__(self, sup, chat, title):
        self.sup, self.chat, self.title, self.start, self.last, self.steps = sup, chat, title, time.time(), 0, []
        self.mid = sup.send(chat, f"⏳ <b>{esc(title)}</b>")

    def step(self, text, force=False):
        self.steps = (self.steps + [text])[-4:]
        if force or time.time() - self.last > 8:
            self.last = time.time()
            mins = int(time.time() - self.start) // 60
            body = "\n".join(f"{'▸' if i == len(self.steps) - 1 else '•'} {esc(s)}" for i, s in enumerate(self.steps))
            self.sup.edit(self.chat, self.mid, f"⏳ <b>{esc(self.title)}</b> · {mins} мин\n<blockquote>{body}</blockquote>")

    def done(self, text, rows=None):
        self.sup.edit(self.chat, self.mid, text, rows)


def claude(prompt, cwd, on_step=None, timeout=CLAUDE_TIMEOUT):
    """(ok, итоговый текст). Claude Code на сервере уже вошёл в аккаунт."""
    cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose",
           "--permission-mode", "acceptEdits", "--allowedTools", *TOOLS]
    try:
        p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    except FileNotFoundError:
        return False, "Claude Code не установлен на сервере"
    timer = threading.Timer(timeout, p.kill)
    timer.start()
    result, err = "", ""
    try:
        for line in p.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                err += line
                continue
            if ev.get("type") == "assistant" and on_step:
                for c in (ev.get("message") or {}).get("content") or []:
                    if c.get("type") == "tool_use":
                        on_step(_step(c.get("name"), c.get("input") or {}))
            elif ev.get("type") == "result":
                result = ev.get("result") or ""
                if ev.get("is_error"):
                    err += result
                    result = ""
        p.wait()
    finally:
        timer.cancel()
    if p.returncode != 0 or not result:
        low = err.lower()
        if any(w in low for w in ("login", "api key", "401", "unauthorized")):
            return False, "Claude на сервере не вошёл в аккаунт"
        if any(w in low for w in ("usage limit", "rate limit", "limit reached", "429")):
            return False, "у Claude закончился лимит, попробуй позже"
        return False, "Claude не закончил работу" + (f": {err.strip()[-300:]}" if err.strip() else "")
    return True, result.strip()


def _step(name, inp):
    path = os.path.basename(str(inp.get("file_path") or inp.get("path") or ""))
    if name == "Bash":
        c = str(inp.get("command", ""))
        return "гоняю тесты" if "test" in c else "запускаю: " + c[:50]
    return {"Read": f"читаю {path}", "Write": f"пишу {path}", "Edit": f"правлю {path}",
            "Glob": "смотрю файлы", "Grep": "ищу в коде"}.get(name) or str(name)


def _no_style(mk):
    if not isinstance(mk, dict) or "inline_keyboard" not in mk:
        return mk
    return {**mk, "inline_keyboard": [[{k: v for k, v in b.items() if k != "style"} for b in row]
                                      for row in mk["inline_keyboard"]]}
