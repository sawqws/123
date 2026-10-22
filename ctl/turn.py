"""Общее для «Контролёра»: текущий ход чата из журнала Claude Code (.jsonl).

Ход — всё после последнего настоящего сообщения Глеба (не tool_result, не служебное <...>).
"""
import json
import os
import time
from datetime import datetime

IDLE = 5 * 60    # пауза длиннее — простой (ждал фоновых агентов), а не работа
TAIL = 4 << 20  # читаем только хвост журнала: файл бывает в десятки МБ


def ts(iso):
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return None


def entries(path, tail=TAIL):
    if not path or not os.path.exists(path):
        return []
    with open(path, "rb") as f:
        size = f.seek(0, 2)
        f.seek(max(0, size - tail))
        raw = f.read().decode("utf-8", "replace").splitlines()
    if size > tail:
        raw = raw[1:]  # первая строка обрезана
    out = []
    for line in raw:
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def is_prompt(d):
    """Настоящее сообщение человека: текст, а не результат инструмента и не служебная вставка."""
    if d.get("type") != "user" or d.get("isMeta") or d.get("isCompactSummary"):
        return False
    c = (d.get("message") or {}).get("content")
    if isinstance(c, str):
        return bool(c.strip()) and not c.lstrip().startswith("<")
    return any(isinstance(x, dict) and x.get("type") == "text" and (x.get("text") or "").strip()
               and not x["text"].lstrip().startswith("<") for x in c or [])


def text_of(d):
    c = (d.get("message") or {}).get("content")
    if isinstance(c, str):
        return c
    return "\n".join(x.get("text") or "" for x in c or [] if isinstance(x, dict) and x.get("type") == "text")


def short(v, n):
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    s = " ".join(s.split())
    return s if len(s) <= n else s[:n] + "…"


class Turn:
    def __init__(self, ents):
        start = max((i for i, d in enumerate(ents) if is_prompt(d)), default=-1)
        self.prompt = text_of(ents[start]) if start >= 0 else ""
        self.prompt_id = ents[start].get("uuid") if start >= 0 else None
        self.started = ts(ents[start].get("timestamp")) if start >= 0 else None
        self.found = start >= 0
        self.tools = []      # [{name, input, result, error, at}]
        self.final = ""      # текст ответа после последнего вызова инструмента
        self.gaps = []       # паузы «думал» перед вызовом: секунды
        self.active = 0      # время работы: паузы дольше IDLE (ждал агентов, Глеба) не в счёт
        by_id = {}
        prev = self.started
        for d in ents[start + 1:]:
            at = ts(d.get("timestamp"))
            c = (d.get("message") or {}).get("content")
            if d.get("type") == "assistant":
                for x in c if isinstance(c, list) else []:
                    if x.get("type") == "tool_use":
                        t = {"name": x.get("name"), "input": x.get("input") or {}, "result": "", "error": False, "at": at}
                        by_id[x.get("id")] = t
                        self.tools.append(t)
                        # агент сдаёт отчёт инструментом — это и есть его ответ
                        self.final = (x.get("input") or {}).get("message", "") if x.get("name") == "SubagentHandback" else ""
                        if at and prev:
                            self.gaps.append(at - prev)
                    elif x.get("type") == "text" and (x.get("text") or "").strip():
                        self.final = (self.final + "\n" + x["text"]).strip()
            elif d.get("type") == "user" and isinstance(c, list):
                for x in c:
                    if isinstance(x, dict) and x.get("type") == "tool_result" and x.get("tool_use_id") in by_id:
                        t = by_id[x["tool_use_id"]]
                        r = x.get("content")
                        if isinstance(r, list):
                            r = "\n".join(y.get("text") or "" for y in r if isinstance(y, dict))
                        t["result"] = r or ""
                        t["error"] = bool(x.get("is_error"))
            if at and prev:
                self.active += min(max(at - prev, 0), IDLE)
            if at:
                prev = at
        self.last_at = prev

    def elapsed(self, now=None):
        """Сколько работает над просьбой: активное время + текущая пауза (не больше IDLE)."""
        if not self.started:
            return 0
        return self.active + min(max((now or time.time()) - (self.last_at or self.started), 0), IDLE)

    def log(self, n=40, width=220):
        """Сжатый журнал хода для проверяющего: последние n вызовов."""
        lines = []
        for t in self.tools[-n:]:
            inp = t["input"]
            what = inp.get("description") or inp.get("command") or inp.get("file_path") or inp.get("prompt") or inp
            res = ("ОШИБКА: " if t["error"] else "") + short(t["result"], width)
            lines.append(f"- {t['name']}: {short(what, width)}\n  → {res}")
        return "\n".join(lines)


def transcript_for(inp):
    """Журнал, о котором хук: у агента — свой файл subagents/agent-<id>.jsonl."""
    p = inp.get("agent_transcript_path")
    if p:
        return p
    p = inp.get("transcript_path")
    aid = inp.get("agent_id")
    if p and aid:
        sub = os.path.join(os.path.splitext(p)[0], "subagents", f"agent-{aid}.jsonl")
        if os.path.exists(sub):
            return sub
    return p
