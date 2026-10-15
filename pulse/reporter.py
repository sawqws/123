#!/usr/bin/env python3
"""«Пульс»: репортёр облачной сессии Claude Code → Главный → бот «Пульс».

Читает журнал своей сессии (~/.claude/projects/*/<CLAUDE_CODE_SESSION_ID>.jsonl) и раз в несколько секунд
шлёт сводку: название чата, заполненность контекста, задачи (TaskCreate/TaskUpdate, TodoWrite), статус
(работает / ждёт Глеба / задал вопрос), что делает сейчас, последний ответ. Только стандартная библиотека.

usage:
  python3 pulse/reporter.py start    запустить в фоне (повторный запуск ничего не делает)
  python3 pulse/reporter.py once     одна сводка в stdout (проверка), без отправки
  python3 pulse/reporter.py stop     остановить
Адрес и ключ — HUB_URL и HUB_KEY (или HUB_TOKEN), как у hub/remote.py. Ключ не печатается.
"""
import calendar
import glob
import hashlib
import hmac
import json
import os
import re
import signal
import sys
import time
import urllib.error
import urllib.request

PERIOD = 4          # как часто смотреть журнал, с
HEARTBEAT = 60      # без изменений — «жив» раз в минуту
TEXT = 600          # обрезка длинных текстов
EDIT_TOOLS = ("Edit", "Write", "NotebookEdit", "MultiEdit")


def clip(s, n=TEXT):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[:n - 1] + "…"


def ts(iso):
    """2026-10-15T13:33:07.523Z → epoch."""
    try:
        return float(calendar.timegm(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")))
    except (TypeError, ValueError):
        return None


def keep(s, n):
    """Обрезка с сохранением переносов строк (Markdown ответа: списки, абзацы)."""
    s = re.sub(r"\n{3,}", "\n\n", str(s or "").strip())
    return s if len(s) <= n else s[:n - 1].rstrip() + "…"


def ru(s):
    return bool(re.search(r"[а-яё]", str(s or ""), re.I))


def action_text(name, inp):
    """Короткое «что делает» по вызову инструмента."""
    inp = inp if isinstance(inp, dict) else {}
    if name == "Bash":
        d = inp.get("description") or ""
        return "⌨️ " + (clip(d, 90) if ru(d) else "команда в терминале")
    if name in EDIT_TOOLS:
        return "✏️ " + os.path.basename(str(inp.get("file_path") or inp.get("notebook_path") or ""))
    if name == "Read":
        return "👀 " + os.path.basename(str(inp.get("file_path") or ""))
    if name in ("Grep", "Glob"):
        return "🔎 ищет в коде"
    if name in ("Agent", "Task"):
        d = inp.get("description") or ""
        return "🤖 агент" + (": " + clip(d, 60) if ru(d) else "")
    if name in ("WebSearch", "WebFetch"):
        return "🌐 ищет в интернете"
    if name == "AskUserQuestion":
        return "❓ вопрос тебе"
    if name in ("TaskCreate", "TaskUpdate", "TodoWrite", "TaskList", "TaskGet"):
        return "📋 обновляет план"
    if name.startswith("mcp__github__"):
        return "🐙 GitHub"
    if name.startswith("mcp__"):
        return "🔌 " + name.split("__")[1].replace("_", " ")
    return "🛠 " + {"Skill": "навык", "Artifact": "страница", "ToolSearch": "инструменты"}.get(name, name)


class Session:
    """Сводка по журналу; feed() принимает строки по одной (читаем журнал с места, где остановились)."""

    def __init__(self):
        self.title = ""
        self.url = ""
        self.model = ""
        self.started = None
        self.updated = None
        self.tokens = 0
        self.compacts = 0
        self.pre_tokens = 0
        self.prompts = []          # [{text, at}]
        self.tasks = {}            # id → {id, subject, status, active}
        self.todos = None          # TodoWrite (старые версии) — список целиком
        self.reply = ""
        self.last = None           # 'end' | 'tool' | 'result' | 'prompt' — последнее событие главной ветки
        self.pending = {}          # tool_use id → имя (ещё без результата)
        self.question = ""
        self.options = []
        self.action = ""
        self.turn = self._turn()
        self.errors = 0

    @staticmethod
    def _turn():
        return {"tools": 0, "edits": [], "commits": 0, "prs": [], "started": None}

    # ---- разбор строки журнала
    def feed(self, line):
        try:
            d = json.loads(line)
        except ValueError:
            return
        if not isinstance(d, dict):
            return
        t = d.get("type")
        at = ts(d.get("timestamp"))
        if at:
            self.started = self.started or at
            self.updated = at
        if t == "ai-title":
            self.title = clip(d.get("aiTitle"), 120)
        elif t == "custom-title" or t == "summary":
            self.title = clip(d.get("customTitle") or d.get("summary"), 120) or self.title
        elif t == "attachment":
            a = d.get("attachment") or {}
            if a.get("type") == "remote_session_change" and a.get("url"):
                self.url = a["url"]
            elif a.get("type") == "model":
                self.model = (a.get("identity") or {}).get("modelId") or self.model
        elif t == "system" and d.get("subtype") == "compact_boundary":
            self.compacts += 1
            self.pre_tokens = int((d.get("compactMetadata") or {}).get("preTokens") or 0)
            self.tokens = 0
        elif t == "assistant" and not d.get("isSidechain"):
            self._assistant(d, at)
        elif t == "user" and not d.get("isSidechain"):
            self._user(d, at)

    def _assistant(self, d, at):
        m = d.get("message") or {}
        self.model = m.get("model") or self.model
        u = m.get("usage") or {}
        tok = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens",
                                                "cache_creation_input_tokens", "output_tokens"))
        if tok and m.get("model") != "<synthetic>":
            self.tokens = tok
        for c in m.get("content") or []:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "text" and c.get("text", "").strip():
                self.reply = keep(c["text"], 3000)
                self.last = "end" if m.get("stop_reason") == "end_turn" else self.last
            elif c.get("type") == "tool_use":
                name, inp = c.get("name") or "", c.get("input") or {}
                self.pending[c.get("id")] = name
                self.turn["tools"] += 1
                self.action = action_text(name, inp)
                self.last = "tool"
                if name in EDIT_TOOLS:
                    f = str(inp.get("file_path") or inp.get("notebook_path") or "")
                    if f and f not in self.turn["edits"]:
                        self.turn["edits"].append(f)
                elif name == "Bash" and re.search(r"\bgit\b[^\n|;&]*\bcommit\b", str(inp.get("command"))):
                    self.turn["commits"] += 1
                elif name == "AskUserQuestion":
                    qs = inp.get("questions") or []
                    q = qs[0] if qs and isinstance(qs[0], dict) else {}
                    self.question = clip(q.get("question"), 300)
                    self.options = [clip(o.get("label"), 40) for o in q.get("options") or [] if isinstance(o, dict)][:4]
        if m.get("stop_reason") == "end_turn":
            self.last = "end"

    def _user(self, d, at):
        m = d.get("message") or {}
        c = m.get("content")
        if isinstance(c, str):
            if d.get("isMeta") or d.get("isCompactSummary") or c.startswith("<"):
                return
            self._prompt(c, at)
            return
        texts = []
        for x in c or []:
            if not isinstance(x, dict):
                continue
            if x.get("type") == "tool_result":
                self._result(x, d.get("toolUseResult"))
            elif x.get("type") == "text" and not d.get("isMeta"):
                texts.append(x.get("text") or "")
        text = "\n".join(t for t in texts if t.strip() and not t.lstrip().startswith("<"))
        if text.strip():
            self._prompt(text, at)

    def _prompt(self, text, at):
        self.prompts.append({"text": keep(text, 1500), "at": at})
        del self.prompts[:-8]
        self.turn = self._turn()
        self.turn["started"] = at
        self.last = "prompt"
        self.question = ""
        self.options = []
        self.action = ""
        self.pending.clear()

    def _result(self, x, res):
        name = self.pending.pop(x.get("tool_use_id"), "")
        self.last = "result"
        if name == "AskUserQuestion":
            self.question = ""
        res = res if isinstance(res, dict) else {}
        task = res.get("task") if isinstance(res.get("task"), dict) else None
        if name == "TaskCreate" or task:
            if task and task.get("id"):
                self.tasks[str(task["id"])] = {"id": str(task["id"]), "subject": clip(task.get("subject"), 120),
                                               "status": "pending"}
        elif name == "TaskUpdate" or res.get("statusChange") or (res.get("taskId") and res.get("updatedFields")):
            tid = str(res.get("taskId") or "")
            t = self.tasks.setdefault(tid, {"id": tid, "subject": f"Задача {tid}", "status": "pending"})
            ch = res.get("statusChange") or {}
            if ch.get("to"):
                t["status"] = ch["to"]
            if t["status"] == "deleted":
                self.tasks.pop(tid, None)
        elif isinstance(res.get("newTodos"), list):  # TodoWrite
            self.todos = [{"id": str(i + 1), "subject": clip(t.get("content"), 120), "status": t.get("status")}
                          for i, t in enumerate(res["newTodos"]) if isinstance(t, dict)]
        content = x.get("content")
        s = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
        for url in re.findall(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+", s or ""):
            if url not in self.turn["prs"]:
                self.turn["prs"].append(url)
        if x.get("is_error"):
            self.errors += 1

    # ---- сводка
    def status(self):
        if self.question and any(n == "AskUserQuestion" for n in self.pending.values()):
            return "question"
        if self.last in ("end", None) or not self.prompts:
            return "waiting"
        return "working"

    def summary(self):
        tasks = self.todos if self.todos is not None and not self.tasks else list(self.tasks.values())
        agents = sum(1 for n in self.pending.values() if n in ("Agent", "Task"))
        st = self.status()
        return {
            "v": 1,
            "title": self.title or (clip(self.prompts[0]["text"], 60) if self.prompts else "Новый чат"),
            "url": self.url,
            "model": self.model,
            "started": self.started,
            "updated": self.updated,
            "status": st,
            "ctx": {"tokens": self.tokens, "compacts": self.compacts, "pre": self.pre_tokens},
            "prompt": self.prompts[-1] if self.prompts else None,
            "prompts": self.prompts[-5:],
            "nprompts": len(self.prompts),
            "tasks": tasks[:40],
            "turn": {**self.turn, "edits": [os.path.basename(f) for f in self.turn["edits"]][-30:],
                     "nedits": len(self.turn["edits"]), "agents": agents},
            "action": self.action if st == "working" else "",
            "question": self.question if st == "question" else "",
            "options": self.options if st == "question" else [],
            "reply": self.reply if st != "working" else keep(self.reply, 300),
        }


def session_id():
    return os.environ.get("CLAUDE_CODE_SESSION_ID", "")


def transcript(sid):
    hits = glob.glob(os.path.expanduser(f"~/.claude/projects/*/{sid}.jsonl")) if sid else []
    return max(hits, key=os.path.getmtime) if hits else None


def hub_key():
    key = os.environ.get("HUB_KEY", "")
    if not key and os.environ.get("HUB_TOKEN"):
        key = hmac.new(os.environ["HUB_TOKEN"].encode(), b"claude-remote-v1", hashlib.sha256).hexdigest()
    return key


def post(body):
    url, key = os.environ.get("HUB_URL", "").rstrip("/"), hub_key()
    if not (url and key):
        return False
    req = urllib.request.Request(f"{url}/api/pulse", data=json.dumps(body, ensure_ascii=False).encode(),
                                 method="POST", headers={"Authorization": "Bearer " + key,
                                                         "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            r.read()
        return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def remote_id():
    """Ид сессии в claude.ai (одинаковый у всех запусков контейнера этой сессии)."""
    r = os.environ.get("CLAUDE_CODE_REMOTE_SESSION_ID", "")
    return re.sub(r"^cse_", "session_", r) if r else ""


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def run(sid):
    path, pos, s = None, 0, Session()
    sent, sent_at, buf = None, 0, b""
    claude = int(os.environ.get("CLAUDE_PID") or 0)
    while True:
        if claude and not alive(claude):
            post({"sid": sid, "rid": remote_id(), "gone": True})
            return
        if not path or not os.path.exists(path):
            path, pos, s, buf = transcript(sid), 0, Session(), b""
        if path:
            try:
                with open(path, "rb") as f:
                    f.seek(pos)
                    chunk = f.read()
                    pos = f.tell()
            except OSError:
                chunk = b""
            buf += chunk
            *lines, buf = buf.split(b"\n")
            for line in lines:
                if line.strip():
                    s.feed(line.decode("utf-8", "replace"))
            body = {"sid": sid, "rid": remote_id(), **s.summary()}
            body["repos"] = sorted(os.path.basename(os.path.dirname(g.rstrip("/")))
                                   for g in glob.glob("/home/user/*/.git/"))
            digest = hashlib.sha1(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            if digest != sent or time.time() - sent_at > HEARTBEAT:
                body["now"] = time.time()
                if post(body):
                    sent, sent_at = digest, time.time()
        time.sleep(PERIOD)


def pidfile(sid):
    return f"/tmp/pulse-{sid[:36]}.pid"


def start():
    sid = session_id()
    if not sid:
        sys.exit("нет CLAUDE_CODE_SESSION_ID — запускать из сессии Claude Code")
    pf = pidfile(sid)
    try:
        if alive(int(open(pf).read())):
            print("Пульс уже работает")
            return
    except (OSError, ValueError):
        pass
    if os.fork():
        time.sleep(0.3)
        print("Пульс запущен" + ("" if hub_key() and os.environ.get("HUB_URL") else " (нет HUB_URL/HUB_KEY — отправлять некуда)"))
        return
    os.setsid()
    if os.fork():
        os._exit(0)
    devnull = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2):
        os.dup2(devnull, fd)
    with open(pf, "w") as f:
        f.write(str(os.getpid()))
    try:
        run(sid)
    finally:
        try:
            os.unlink(pf)
        except OSError:
            pass
        os._exit(0)


def main(argv):
    cmd = argv[0] if argv else "start"
    if cmd == "start":
        start()
    elif cmd == "once":
        p = transcript(argv[1] if len(argv) > 1 else session_id())
        if not p:
            sys.exit("журнал сессии не найден")
        s = Session()
        with open(p, encoding="utf-8", errors="replace") as f:
            for line in f:
                s.feed(line)
        print(json.dumps({"rid": remote_id(), **s.summary()}, ensure_ascii=False, indent=1))
    elif cmd == "stop":
        try:
            pid = int(open(pidfile(session_id())).read())
            os.kill(pid, signal.SIGTERM)
            for _ in range(30):  # дождаться выхода — сразу следом может идти start
                if not alive(pid):
                    break
                time.sleep(0.1)
            print("остановлен")
        except (OSError, ValueError):
            print("не запущен")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
