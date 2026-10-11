"""Данные тренера: SQLite, только стандартная библиотека.

Записи WHOOP (cycle, recovery, sleep, workout) храним как есть, в JSON, с днём и временем начала —
поля считает insights.py. Так новые поля WHOOP не ломают базу.
Остальное: профиль, цели (свободным текстом), токены WHOOP, приёмы пищи, переписка с тренером, разборы дня, служебное.
"""
import datetime
import json
import os
import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY, name TEXT DEFAULT '', tz TEXT DEFAULT 'Europe/Moscow',
  profile TEXT DEFAULT '{}', created TEXT
);
CREATE TABLE IF NOT EXISTS tokens (
  user INTEGER PRIMARY KEY, access TEXT, refresh TEXT, expires REAL, whoop_id TEXT, synced TEXT
);
CREATE TABLE IF NOT EXISTS records (
  user INTEGER NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL, day TEXT, start TEXT, data TEXT,
  PRIMARY KEY (user, kind, id)
);
CREATE TABLE IF NOT EXISTS meals (
  id INTEGER PRIMARY KEY, user INTEGER NOT NULL, day TEXT NOT NULL, time TEXT DEFAULT '',
  title TEXT NOT NULL, kcal REAL DEFAULT 0, protein REAL DEFAULT 0, fat REAL DEFAULT 0, carbs REAL DEFAULT 0,
  note TEXT DEFAULT '', created TEXT
);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY, user INTEGER NOT NULL, role TEXT NOT NULL, text TEXT NOT NULL, created TEXT
);
CREATE TABLE IF NOT EXISTS briefs (user INTEGER, day TEXT, kind TEXT, text TEXT, created TEXT,
  PRIMARY KEY (user, day, kind));
CREATE TABLE IF NOT EXISTS goals (
  id INTEGER PRIMARY KEY, user INTEGER NOT NULL, text TEXT NOT NULL, created TEXT
);
CREATE TABLE IF NOT EXISTS sent (key TEXT PRIMARY KEY, at TEXT);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX IF NOT EXISTS records_day ON records (user, kind, day);
CREATE INDEX IF NOT EXISTS meals_day ON meals (user, day);
CREATE INDEX IF NOT EXISTS messages_user ON messages (user, id);
"""

KINDS = ("cycle", "recovery", "sleep", "workout")
PROFILE_FIELDS = {
    "age": int, "sex": str, "height": float, "weight": float, "goal": str, "sport": str,
    "schedule": str, "notes": str, "morning": str, "diet": str,
}
GOALS = ("form", "mass", "dry", "weight")  # форма, набор мышц, рельеф, держать вес к соревнованиям
MORNING_DEFAULT = "07:00"  # утренний разбор: не раньше этого времени; "off" — не присылать
MAX_GOALS = 10
MEAL_FIELDS = ("title", "kcal", "protein", "fat", "carbs", "note", "time", "day")


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def morning_value(v):
    """Время утреннего разбора: "7:30" -> "07:30", "выкл"/"off" -> "off". Иначе ValueError."""
    v = str(v).strip().lower().replace(".", ":").replace("-", ":")
    if v in ("off", "выкл", "нет", "0", "выключить"):
        return "off"
    if v in ("on", "вкл", "да", "включить"):
        return MORNING_DEFAULT
    parts = v.split(":")
    if len(parts) == 2 and all(p.isdigit() for p in parts):
        h, m = int(parts[0]), int(parts[1])
        if 3 <= h <= 13 and 0 <= m <= 59:
            return f"{h:02d}:{m:02d}"
    raise ValueError("время утра в виде 07:30 (с 03:00 до 13:59) или «выкл»")


class Store:
    def __init__(self, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.path = path
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.db.commit()

    def q(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def x(self, sql, args=()):
        with self.lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur

    # ---------- служебное ----------

    def meta(self, key, value=None):
        if value is None:
            r = self.q("SELECT value FROM meta WHERE key=?", (key,))
            return r[0]["value"] if r else None
        self.x("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, str(value)))
        return str(value)

    def once(self, key):
        """True, если по этому ключу ещё ничего не отправляли (и помечает как отправленное)."""
        with self.lock:
            cur = self.db.execute("INSERT OR IGNORE INTO sent VALUES (?,?)", (key, now_iso()))
            self.db.commit()
            return cur.rowcount == 1

    def was_sent(self, key):
        return bool(self.q("SELECT 1 FROM sent WHERE key=?", (key,)))

    def unsend(self, key):
        """Снять отметку once — отправка не удалась, можно повторить."""
        self.x("DELETE FROM sent WHERE key=?", (key,))

    # ---------- пользователь и профиль ----------

    def user(self, uid, name=None):
        r = self.q("SELECT * FROM users WHERE id=?", (uid,))
        if not r:
            self.x("INSERT INTO users (id, name, created) VALUES (?,?,?)", (uid, name or "", now_iso()))
            r = self.q("SELECT * FROM users WHERE id=?", (uid,))
        elif name and r[0]["name"] != name:
            self.x("UPDATE users SET name=? WHERE id=?", (name, uid))
            r[0]["name"] = name
        u = r[0]
        u["profile"] = json.loads(u.get("profile") or "{}")
        return u

    def users(self):
        return [self.user(r["id"]) for r in self.q("SELECT id FROM users")]

    def save_profile(self, uid, data):
        u = self.user(uid)
        prof = dict(u["profile"])
        for k, cast in PROFILE_FIELDS.items():
            if k not in data:
                continue
            v = data[k]
            if v in (None, ""):
                prof.pop(k, None)
                continue
            try:
                v = cast(str(v).replace(",", ".")) if cast is not str else str(v).strip()[:600]
            except ValueError:
                raise ValueError(f"не похоже на число: {k}")
            if k == "age" and not 8 <= v <= 100:
                raise ValueError("возраст от 8 до 100")
            if k == "weight" and not 25 <= v <= 250:
                raise ValueError("вес от 25 до 250 кг")
            if k == "height" and not 100 <= v <= 230:
                raise ValueError("рост от 100 до 230 см")
            if k == "goal" and v not in GOALS:
                raise ValueError("нет такой цели")
            if k == "morning":
                v = morning_value(v)
            prof[k] = v
        self.x("UPDATE users SET profile=? WHERE id=?", (json.dumps(prof, ensure_ascii=False), uid))
        if "tz" in data and data["tz"]:
            self.x("UPDATE users SET tz=? WHERE id=?", (str(data["tz"])[:64], uid))
        return self.user(uid)

    # ---------- WHOOP ----------

    def token(self, uid):
        r = self.q("SELECT * FROM tokens WHERE user=?", (uid,))
        return r[0] if r else None

    def save_token(self, uid, access, refresh, expires, whoop_id=None):
        old = self.token(uid) or {}
        self.x("INSERT OR REPLACE INTO tokens (user, access, refresh, expires, whoop_id, synced) VALUES (?,?,?,?,?,?)",
               (uid, access, refresh, expires, whoop_id or old.get("whoop_id"), old.get("synced")))

    def mark_synced(self, uid):
        self.x("UPDATE tokens SET synced=? WHERE user=?", (now_iso(), uid))

    def drop_token(self, uid):
        self.x("DELETE FROM tokens WHERE user=?", (uid,))

    def put(self, uid, kind, rid, day, start, data):
        self.x("INSERT OR REPLACE INTO records VALUES (?,?,?,?,?,?)",
               (uid, kind, str(rid), day, start, json.dumps(data, ensure_ascii=False)))

    def records(self, uid, kind, since=None, until=None):
        sql, args = "SELECT day, data FROM records WHERE user=? AND kind=?", [uid, kind]
        if since:
            sql += " AND day>=?"
            args.append(str(since))
        if until:
            sql += " AND day<=?"
            args.append(str(until))
        rows = self.q(sql + " ORDER BY day, start", args)
        return [dict(json.loads(r["data"]), _day=r["day"]) for r in rows]

    def record_count(self, uid):
        return self.q("SELECT COUNT(*) n FROM records WHERE user=?", (uid,))[0]["n"]

    # ---------- питание ----------

    def save_meal(self, uid, data):
        vals = {}
        for k in MEAL_FIELDS:
            if k in data and data[k] is not None:
                vals[k] = data[k]
        for k in ("kcal", "protein", "fat", "carbs"):
            if k in vals:
                vals[k] = max(0.0, min(float(str(vals[k]).replace(",", ".") or 0), 10000.0))
        if "title" in vals:
            vals["title"] = str(vals["title"]).strip()[:120]
        mid = data.get("id")
        if mid:
            if not vals:
                return self.meal(uid, int(mid))
            sets = ", ".join(f"{k}=?" for k in vals)
            self.x(f"UPDATE meals SET {sets} WHERE id=? AND user=?", (*vals.values(), int(mid), uid))
            return self.meal(uid, int(mid))
        if not vals.get("title"):
            raise ValueError("что съел?")
        if not vals.get("day"):
            raise ValueError("нет дня")
        cols = ["user", "created", *vals]
        cur = self.x(f"INSERT INTO meals ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                     (uid, now_iso(), *vals.values()))
        return self.meal(uid, cur.lastrowid)

    def meal(self, uid, mid):
        r = self.q("SELECT * FROM meals WHERE id=? AND user=?", (mid, uid))
        return r[0] if r else None

    def meals(self, uid, since, until=None):
        until = until or since
        return self.q("SELECT * FROM meals WHERE user=? AND day>=? AND day<=? ORDER BY day, time, id",
                      (uid, str(since), str(until)))

    def delete_meal(self, uid, mid):
        return self.x("DELETE FROM meals WHERE id=? AND user=?", (mid, uid)).rowcount > 0

    # ---------- тренер ----------

    def say(self, uid, role, text):
        self.x("INSERT INTO messages (user, role, text, created) VALUES (?,?,?,?)", (uid, role, text[:8000], now_iso()))
        # храним последние 200 сообщений
        self.x("DELETE FROM messages WHERE user=? AND id NOT IN "
               "(SELECT id FROM messages WHERE user=? ORDER BY id DESC LIMIT 200)", (uid, uid))

    def history(self, uid, n=12):
        return list(reversed(self.q("SELECT role, text, created FROM messages WHERE user=? ORDER BY id DESC LIMIT ?",
                                    (uid, n))))

    def clear_history(self, uid):
        self.x("DELETE FROM messages WHERE user=?", (uid,))

    def brief(self, uid, day, kind="morning", text=None):
        if text is None:
            r = self.q("SELECT text, created FROM briefs WHERE user=? AND day=? AND kind=?", (uid, str(day), kind))
            return r[0] if r else None
        self.x("INSERT OR REPLACE INTO briefs VALUES (?,?,?,?,?)", (uid, str(day), kind, text, now_iso()))
        return {"text": text}

    # ---------- цели ----------

    def goals(self, uid):
        return self.q("SELECT id, text, created FROM goals WHERE user=? ORDER BY id", (uid,))

    def add_goal(self, uid, text):
        text = " ".join(str(text or "").split())[:300]
        if len(text) < 3:
            raise ValueError("напиши цель словами, например «ложиться до 23:00»")
        if len(self.goals(uid)) >= MAX_GOALS:
            raise ValueError(f"целей уже {MAX_GOALS} — удали лишние")
        cur = self.x("INSERT INTO goals (user, text, created) VALUES (?,?,?)", (uid, text, now_iso()))
        return {"id": cur.lastrowid, "text": text}

    def delete_goal(self, uid, gid):
        return self.x("DELETE FROM goals WHERE id=? AND user=?", (int(gid), uid)).rowcount > 0
