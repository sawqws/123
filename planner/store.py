"""Данные планера: SQLite, только стандартная библиотека.

Задача: заголовок, дата (или без даты), время, повтор по дням недели, цель, напоминание, заметка.
Повторяющаяся задача хранится одной строкой: date — с какого дня, repeat — дни недели ("135" = пн, ср, пт),
отметки «сделано» по дням — в таблице completions, пропущенные дни — в skips.
Цель: горизонт (неделя / месяц / год / без срока) и начало периода, шаги — задачи с goal = id цели,
заметки — таблица notes.
"""
import datetime
import json
import os
import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY, name TEXT DEFAULT '', tz TEXT DEFAULT 'Europe/Moscow',
  morning TEXT DEFAULT '07:30', evening TEXT DEFAULT '21:30', remind INTEGER DEFAULT 15,
  created TEXT
);
CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY, user INTEGER NOT NULL, title TEXT NOT NULL, note TEXT DEFAULT '',
  date TEXT, time TEXT, repeat TEXT DEFAULT '', skips TEXT DEFAULT '', goal INTEGER,
  important INTEGER DEFAULT 0, remind INTEGER DEFAULT -1, done INTEGER DEFAULT 0, done_at TEXT,
  created TEXT, pos REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS completions (task INTEGER, date TEXT, PRIMARY KEY (task, date));
CREATE TABLE IF NOT EXISTS goals (
  id INTEGER PRIMARY KEY, user INTEGER NOT NULL, title TEXT NOT NULL, emoji TEXT DEFAULT '🎯',
  color TEXT DEFAULT 'violet', horizon TEXT DEFAULT 'month', start TEXT, due TEXT, why TEXT DEFAULT '',
  progress INTEGER DEFAULT 0, status TEXT DEFAULT 'active', done_at TEXT, created TEXT, pos REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS notes (
  id INTEGER PRIMARY KEY, user INTEGER NOT NULL, goal INTEGER, text TEXT NOT NULL, created TEXT, updated TEXT
);
CREATE TABLE IF NOT EXISTS sent (key TEXT PRIMARY KEY, at TEXT);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX IF NOT EXISTS tasks_user ON tasks (user, date);
CREATE INDEX IF NOT EXISTS notes_goal ON notes (goal);
"""

HORIZONS = ("week", "month", "year", "none")
COLORS = ("violet", "blue", "teal", "green", "amber", "orange", "rose", "pink")
TASK_FIELDS = ("title", "note", "date", "time", "repeat", "goal", "important", "remind")
GOAL_FIELDS = ("title", "emoji", "color", "horizon", "start", "due", "why", "progress")


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def parse_date(s):
    return datetime.date.fromisoformat(s) if s else None


def period_start(horizon, day):
    """Начало периода цели, в который попадает день."""
    if horizon == "week":
        return day - datetime.timedelta(days=day.weekday())
    if horizon == "month":
        return day.replace(day=1)
    if horizon == "year":
        return day.replace(month=1, day=1)
    return None


def occurs(task, day):
    """Есть ли задача в этот день (для повторяющихся — по дням недели)."""
    start = parse_date(task["date"])
    if not task["repeat"]:
        return start == day
    if start is None or day < start:
        return False
    return str(day.isoweekday()) in task["repeat"] and day.isoformat() not in (task["skips"] or "").split(",")


def _clean_time(t):
    if not t:
        return None
    h, m = str(t).split(":")[:2]
    h, m = int(h), int(m)
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError("время")
    return f"{h:02d}:{m:02d}"


def _clean_task(d):
    out = {}
    for k in TASK_FIELDS:
        if k not in d:
            continue
        v = d[k]
        if k == "title":
            v = str(v or "").strip()[:300]
            if not v:
                raise ValueError("пустое название")
        elif k == "note":
            v = str(v or "")[:5000]
        elif k == "date":
            v = parse_date(v).isoformat() if v else None
        elif k == "time":
            v = _clean_time(v)
        elif k == "repeat":
            v = "".join(sorted({c for c in str(v or "") if c in "1234567"}))
        elif k == "goal":
            v = int(v) if v else None
        elif k == "important":
            v = 1 if v else 0
        elif k == "remind":
            v = max(-1, min(int(v if v is not None else -1), 24 * 60))
        out[k] = v
    return out


def _clean_goal(d):
    out = {}
    for k in GOAL_FIELDS:
        if k not in d:
            continue
        v = d[k]
        if k == "title":
            v = str(v or "").strip()[:200]
            if not v:
                raise ValueError("пустое название")
        elif k == "emoji":
            v = str(v or "🎯")[:16]
        elif k == "color":
            v = v if v in COLORS else "violet"
        elif k == "horizon":
            v = v if v in HORIZONS else "none"
        elif k in ("start", "due"):
            v = parse_date(v).isoformat() if v else None
        elif k == "why":
            v = str(v or "")[:2000]
        elif k == "progress":
            v = max(0, min(100, int(v or 0)))
        out[k] = v
    return out


class Store:
    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.lock = threading.RLock()

    def close(self):
        self.db.close()

    # ---------- общее ----------

    def q(self, sql, *args):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def x(self, sql, *args):
        with self.lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur

    def meta(self, key, value=None):
        if value is None:
            r = self.q("SELECT value FROM meta WHERE key=?", key)
            return r[0]["value"] if r else None
        self.x("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", key, str(value))
        return value

    def mark_sent(self, key):
        """True, если ключ отмечен впервые (чтобы не прислать одно и то же дважды, даже после перезапуска)."""
        with self.lock:
            cur = self.db.execute("INSERT OR IGNORE INTO sent (key, at) VALUES (?, ?)", (key, now_iso()))
            self.db.commit()
            return cur.rowcount == 1

    def backup(self, folder, keep=14):
        os.makedirs(folder, exist_ok=True)
        dst = os.path.join(folder, f"planner-{datetime.date.today().isoformat()}.db")
        with self.lock:
            target = sqlite3.connect(dst)
            self.db.backup(target)
            target.close()
        files = sorted(f for f in os.listdir(folder) if f.startswith("planner-") and f.endswith(".db"))
        for f in files[:-keep]:
            os.remove(os.path.join(folder, f))
        return dst

    # ---------- пользователи ----------

    def user(self, uid, name=None):
        r = self.q("SELECT * FROM users WHERE id=?", uid)
        if not r:
            self.x("INSERT INTO users (id, name, created) VALUES (?, ?, ?)", uid, name or "", now_iso())
            r = self.q("SELECT * FROM users WHERE id=?", uid)
        elif name and r[0]["name"] != name:
            self.x("UPDATE users SET name=? WHERE id=?", name, uid)
            r[0]["name"] = name
        return r[0]

    def users(self):
        return self.q("SELECT * FROM users")

    def update_settings(self, uid, d):
        u = self.user(uid)
        sets = {}
        for k in ("morning", "evening"):
            if k in d:
                sets[k] = _clean_time(d[k]) or ""
        if "remind" in d:
            sets["remind"] = max(-1, min(int(d["remind"]), 24 * 60))
        if "tz" in d and d["tz"]:
            from zoneinfo import ZoneInfo
            ZoneInfo(str(d["tz"]))  # проверка: такой пояс существует
            sets["tz"] = str(d["tz"])
        for k, v in sets.items():
            self.x(f"UPDATE users SET {k}=? WHERE id=?", v, uid)
        u.update(sets)
        return u

    # ---------- задачи ----------

    def task(self, uid, tid):
        r = self.q("SELECT * FROM tasks WHERE id=? AND user=?", tid, uid)
        return r[0] if r else None

    def save_task(self, uid, d):
        clean = _clean_task(d)
        if clean.get("goal") and not self.goal(uid, clean["goal"]):
            clean["goal"] = None
        if clean.get("repeat") and not (clean.get("date") or (d.get("id") and (self.task(uid, int(d["id"])) or {}).get("date"))):
            clean["date"] = datetime.date.today().isoformat()  # повтор начинается сегодня, если дата не задана
        if d.get("id"):
            tid = int(d["id"])
            if not self.task(uid, tid):
                raise KeyError("нет такой задачи")
            if clean:
                cols = ", ".join(f"{k}=?" for k in clean)
                self.x(f"UPDATE tasks SET {cols} WHERE id=? AND user=?", *clean.values(), tid, uid)
        else:
            if "title" not in clean:
                raise ValueError("пустое название")
            clean.setdefault("remind", -1)
            cols = ", ".join(clean)
            marks = ", ".join("?" for _ in clean)
            pos = (self.q("SELECT MAX(pos) AS p FROM tasks WHERE user=?", uid)[0]["p"] or 0) + 1
            tid = self.x(f"INSERT INTO tasks (user, created, pos, {cols}) VALUES (?, ?, ?, {marks})",
                         uid, now_iso(), pos, *clean.values()).lastrowid
        return self.task(uid, tid)

    def delete_task(self, uid, tid, date=None):
        """Удаляет задачу; у повторяющейся с date — только этот день."""
        t = self.task(uid, tid)
        if not t:
            return False
        if t["repeat"] and date:
            skips = [s for s in (t["skips"] or "").split(",") if s]
            if date not in skips:
                skips.append(date)
            self.x("UPDATE tasks SET skips=? WHERE id=?", ",".join(skips), tid)
            self.x("DELETE FROM completions WHERE task=? AND date=?", tid, date)
        else:
            self.x("DELETE FROM tasks WHERE id=? AND user=?", tid, uid)
            self.x("DELETE FROM completions WHERE task=?", tid)
        return True

    def toggle(self, uid, tid, date=None, done=None):
        """Отметить «сделано» / снять отметку. Возвращает новое состояние."""
        t = self.task(uid, tid)
        if not t:
            raise KeyError("нет такой задачи")
        if t["repeat"]:
            if not date:
                raise ValueError("у повторяющейся задачи нужен день")
            has = bool(self.q("SELECT 1 FROM completions WHERE task=? AND date=?", tid, date))
            new = (not has) if done is None else bool(done)
            if new and not has:
                self.x("INSERT INTO completions (task, date) VALUES (?, ?)", tid, date)
            elif not new and has:
                self.x("DELETE FROM completions WHERE task=? AND date=?", tid, date)
            return new
        new = (not t["done"]) if done is None else bool(done)
        self.x("UPDATE tasks SET done=?, done_at=? WHERE id=?", int(new), now_iso() if new else None, tid)
        return new

    def move(self, uid, tid, date):
        t = self.task(uid, tid)
        if not t or t["repeat"]:
            return None
        return self.save_task(uid, {"id": tid, "date": date})

    def tasks(self, uid):
        return self.q("SELECT * FROM tasks WHERE user=? ORDER BY pos", uid)

    def completions(self, uid):
        return self.q("SELECT c.task, c.date FROM completions c JOIN tasks t ON t.id=c.task WHERE t.user=?", uid)

    def day(self, uid, day):
        """Задачи на день (с повторами), сортировка: со временем по времени, потом без времени."""
        iso = day.isoformat()
        done_rep = {r["task"] for r in self.q(
            "SELECT c.task FROM completions c JOIN tasks t ON t.id=c.task WHERE t.user=? AND c.date=?", uid, iso)}
        out = []
        for t in self.tasks(uid):
            if occurs(t, day):
                t = dict(t)
                if t["repeat"]:
                    t["done"] = int(t["id"] in done_rep)
                t["day"] = iso
                out.append(t)
        out.sort(key=lambda t: (t["time"] is None, t["time"] or "", -t["important"], t["pos"]))
        return out

    def overdue(self, uid, today):
        return self.q("SELECT * FROM tasks WHERE user=? AND repeat='' AND done=0 AND date IS NOT NULL AND date<? "
                      "ORDER BY date, time", uid, today.isoformat())

    # ---------- цели ----------

    def goal(self, uid, gid):
        r = self.q("SELECT * FROM goals WHERE id=? AND user=?", gid, uid)
        return r[0] if r else None

    def save_goal(self, uid, d):
        clean = _clean_goal(d)
        if "status" in d and d["status"] in ("active", "done", "archived"):
            clean["status"] = d["status"]
            clean["done_at"] = now_iso() if d["status"] == "done" else None
        if d.get("id"):
            gid = int(d["id"])
            if not self.goal(uid, gid):
                raise KeyError("нет такой цели")
            if clean:
                cols = ", ".join(f"{k}=?" for k in clean)
                self.x(f"UPDATE goals SET {cols} WHERE id=? AND user=?", *clean.values(), gid, uid)
        else:
            if "title" not in clean:
                raise ValueError("пустое название")
            hz = clean.setdefault("horizon", "month")
            if not clean.get("start") and hz != "none":
                clean["start"] = period_start(hz, datetime.date.today()).isoformat()
            cols = ", ".join(clean)
            marks = ", ".join("?" for _ in clean)
            pos = (self.q("SELECT MAX(pos) AS p FROM goals WHERE user=?", uid)[0]["p"] or 0) + 1
            gid = self.x(f"INSERT INTO goals (user, created, pos, {cols}) VALUES (?, ?, ?, {marks})",
                         uid, now_iso(), pos, *clean.values()).lastrowid
        return self.goal(uid, gid)

    def delete_goal(self, uid, gid):
        if not self.goal(uid, gid):
            return False
        self.x("DELETE FROM goals WHERE id=? AND user=?", gid, uid)
        self.x("DELETE FROM notes WHERE goal=? AND user=?", gid, uid)
        self.x("UPDATE tasks SET goal=NULL WHERE goal=? AND user=?", gid, uid)
        return True

    def goals(self, uid):
        return self.q("SELECT * FROM goals WHERE user=? ORDER BY pos", uid)

    def progress(self, uid, goal):
        """Прогресс цели в %: по шагам (разовым задачам цели), если они есть, иначе — ручной."""
        if goal["status"] == "done":
            return 100
        steps = self.q("SELECT done FROM tasks WHERE user=? AND goal=? AND repeat=''", uid, goal["id"])
        if steps:
            return round(100 * sum(s["done"] for s in steps) / len(steps))
        return goal["progress"] or 0

    def current_goals(self, uid, day):
        """Активные цели, чей период включает день (неделя, месяц, год)."""
        out = []
        for g in self.goals(uid):
            if g["status"] != "active" or g["horizon"] == "none":
                continue
            if g["start"] == period_start(g["horizon"], day).isoformat():
                out.append(g)
        return out

    # ---------- заметки ----------

    def note(self, uid, nid):
        r = self.q("SELECT * FROM notes WHERE id=? AND user=?", nid, uid)
        return r[0] if r else None

    def save_note(self, uid, d):
        text = str(d.get("text") or "").strip()[:10000]
        if not text:
            raise ValueError("пустая заметка")
        if d.get("id"):
            nid = int(d["id"])
            if not self.note(uid, nid):
                raise KeyError("нет такой заметки")
            self.x("UPDATE notes SET text=?, updated=? WHERE id=?", text, now_iso(), nid)
        else:
            goal = int(d["goal"]) if d.get("goal") else None
            if goal and not self.goal(uid, goal):
                raise KeyError("нет такой цели")
            nid = self.x("INSERT INTO notes (user, goal, text, created, updated) VALUES (?, ?, ?, ?, ?)",
                         uid, goal, text, now_iso(), now_iso()).lastrowid
        return self.note(uid, nid)

    def delete_note(self, uid, nid):
        return self.x("DELETE FROM notes WHERE id=? AND user=?", nid, uid).rowcount == 1

    def notes(self, uid):
        return self.q("SELECT * FROM notes WHERE user=? ORDER BY created DESC, id DESC", uid)

    # ---------- всё сразу (для приложения) ----------

    def everything(self, uid):
        return {
            "user": self.user(uid),
            "tasks": self.tasks(uid),
            "completions": self.completions(uid),
            "goals": self.goals(uid),
            "notes": self.notes(uid),
        }

    def export_json(self, uid):
        return json.dumps(self.everything(uid), ensure_ascii=False, indent=1)

