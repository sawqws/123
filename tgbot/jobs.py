"""Сообщения по расписанию: «каждый день в 7:00 маме "доброе утро"», «по будням в 21:00 Саше — придумай сам».

Задача (job) — словарь в tgbot/tmp/jobs.json:
  id, chats [{id, name}], time "ЧЧ:ММ", days [0..6] (0 — пн) или once "ГГГГ-ММ-ДД",
  text (готовый текст) или ai (что написать — Claude сочиняет заново каждый раз),
  until "ГГГГ-ММ-ДД" (необязательно), next (ISO, когда следующий раз), sent (сколько раз отправлено).
Глеб подтверждает задачу один раз при создании; дальше агент отправляет сам и присылает ему отчёт.
"""
import datetime
import json
import os
import re
import uuid
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")
DAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
FULL = ["понед", "вторн", "сред", "четв", "пятн", "суббот", "воскр"]  # «пятница», «по средам»…
LATE = datetime.timedelta(minutes=30)  # агент лежал дольше — пропускаем, а не шлём невпопад


class Bad(ValueError):
    pass


def parse_days(s):
    """«каждый день», «будни», «выходные», «пн,ср,пт», «вт-чт» -> отсортированный список 0..6."""
    s = (s or "").casefold().replace(" ", "")
    if s in ("", "каждыйдень", "ежедневно", "daily", "все"):
        return list(range(7))
    if s in ("будни", "будние", "weekdays"):
        return list(range(5))
    if s in ("выходные", "weekend"):
        return [5, 6]
    out = set()
    for part in re.split(r"[,;]", s):
        if "-" in part:
            a, b = part.split("-", 1)
            if a not in DAYS or b not in DAYS:
                raise Bad(f"не понял дни «{part}»")
            i, j = DAYS.index(a), DAYS.index(b)
            out.update(range(i, j + 1) if i <= j else [*range(i, 7), *range(0, j + 1)])
        elif part in DAYS:
            out.add(DAYS.index(part))
        elif any(f in part for f in FULL):
            out.add(next(i for i, f in enumerate(FULL) if f in part))
        else:
            raise Bad(f"не понял день «{part}», пиши пн,вт,ср,чт,пт,сб,вс или «будни»")
    return sorted(out)


def parse_time(s):
    m = re.fullmatch(r"\s*(\d{1,2})[:.](\d{2})\s*", s or "")
    if not m or int(m[1]) > 23 or int(m[2]) > 59:
        raise Bad(f"не понял время «{s}», нужно ЧЧ:ММ")
    return f"{int(m[1]):02d}:{m[2]}"


def next_run(job, after):
    """Следующий момент отправки строго позже after (aware datetime) или None, если больше не нужно."""
    h, m = map(int, job["time"].split(":"))
    after = after.astimezone(MSK)
    until = datetime.date.fromisoformat(job["until"]) if job.get("until") else None
    if job.get("once"):
        t = datetime.datetime.combine(datetime.date.fromisoformat(job["once"]), datetime.time(h, m), MSK)
        return t if t > after else None
    for i in range(8):
        day = after.date() + datetime.timedelta(days=i)
        t = datetime.datetime.combine(day, datetime.time(h, m), MSK)
        if t > after and day.weekday() in job["days"]:
            return None if until and day > until else t
    return None


def describe(job):
    when = (f"{datetime.date.fromisoformat(job['once']):%d.%m.%Y}" if job.get("once")
            else "каждый день" if len(job["days"]) == 7 else "по будням" if job["days"] == list(range(5))
            else "по выходным" if job["days"] == [5, 6] else ", ".join(DAYS[d] for d in job["days"]))
    what = f"«{job['text']}»" if job.get("text") else f"Claude пишет сам: {job['ai']}"
    to = ", ".join(c["name"] for c in job["chats"])
    tail = f", до {job['until']}" if job.get("until") else ""
    return f"{when} в {job['time']}{tail} → {to}: {what}"


class Store:
    def __init__(self, path):
        self.path = path

    def load(self):
        try:
            with open(self.path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return []

    def save(self, jobs):
        tmp = self.path + ".new"
        with open(tmp, "w") as f:
            json.dump(jobs, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    def new(self, chats, time, days=None, once=None, text=None, ai=None, until=None, now=None):
        if bool(text) == bool(ai):
            raise Bad("нужен либо --text (готовый текст), либо --ai (что написать)")
        job = {"id": uuid.uuid4().hex[:6], "chats": chats, "time": parse_time(time), "text": text, "ai": ai,
               "until": until, "sent": 0}
        if once:
            job["once"] = datetime.date.fromisoformat(once).isoformat()
        else:
            job["days"] = parse_days(days)
        if until:
            datetime.date.fromisoformat(until)
        t = next_run(job, now or datetime.datetime.now(MSK))
        if t is None:
            raise Bad("это время уже прошло")
        job["next"] = t.isoformat()
        return job

    def add(self, job):
        jobs = self.load()
        jobs.append(job)
        self.save(jobs)

    def remove(self, jid):
        jobs = self.load()
        left = [j for j in jobs if j["id"] != jid]
        self.save(left)
        return len(left) != len(jobs)

    def due(self, now):
        """Задачи, которые пора отправить, и пропущенные (агент был выключен). Сразу сдвигает next."""
        jobs, run, missed = self.load(), [], []
        if not jobs:
            return run, missed
        keep = []
        for j in jobs:
            t = datetime.datetime.fromisoformat(j["next"])
            if t <= now:
                (run if now - t <= LATE else missed).append(dict(j))
                nxt = next_run(j, now)
                if nxt is None:
                    continue  # разовая или срок вышел — убираем
                j["next"] = nxt.isoformat()
            keep.append(j)
        if run or missed:
            self.save(keep)
        return run, missed

    def done(self, jid):
        jobs = self.load()
        for j in jobs:
            if j["id"] == jid:
                j["sent"] = j.get("sent", 0) + 1
        self.save(jobs)
