"""Дневник (как WHOOP Journal): привычки «да/нет» за вчера и их влияние на восстановление.

Подключение — FEATURES в coach.py: route / extra / context_lines (см. там).

Как в WHOOP: утром отвечаешь про ВЧЕРА (день X: вечер, еда, экран перед сном). Это влияет на ночь X→X+1,
а восстановление этой ночи WHOOP относит к дню X+1 (whoop.day_of: recovery — день цикла, который начинается
со сна накануне; в insights.series у дня D — утреннее восстановление дня D). Поэтому ответ за день X
сравниваем с восстановлением дня X+1 — см. after().
"""
import datetime
import json

GROUPS = [("sleep", "Сон"), ("food", "Еда и вода"), ("train", "Тренировки и восстановление"), ("feel", "Самочувствие")]

# key, группа, вопрос (как в дневнике), коротко (для тренера и подписей)
HABITS = [
    ("screen", "sleep", "Телефон или экран в последний час перед сном", "экран перед сном"),
    ("bed23", "sleep", "Лёг до 23:00", "лёг до 23:00"),
    ("cool", "sleep", "Спал в прохладе, проветрил комнату", "прохлада в комнате"),
    ("nap", "sleep", "Спал днём", "дневной сон"),
    ("caffeine", "food", "Кофеин или энергетик после 14:00", "кофеин после 14:00"),
    ("late_meal", "food", "Плотный ужин меньше чем за 2 часа до сна", "поздний плотный ужин"),
    ("sweets", "food", "Сладкое вечером", "сладкое вечером"),
    ("water2", "food", "Выпил 2 литра воды или больше", "2+ л воды"),
    ("protein", "food", "Ел достаточно белка", "достаточно белка"),
    ("breakfast", "food", "Завтракал", "завтрак"),
    ("stretch", "train", "Растяжка или заминка", "растяжка, заминка"),
    ("sauna", "train", "Баня или сауна", "баня, сауна"),
    ("double", "train", "Вторая тренировка за день", "вторая тренировка"),
    ("sick", "feel", "Болею, простыл", "болел"),
    ("stress", "feel", "Стресс: учёба, контрольная", "стресс"),
    ("outside", "feel", "Был на улице час или больше", "1+ ч на улице"),
    ("travel", "feel", "Дорога или перелёт", "дорога, перелёт"),
    ("injury", "feel", "Травма, что-то болит", "травма, боль"),
]
KEYS = [h[0] for h in HABITS]
BY_KEY = {h[0]: h for h in HABITS}
DEFAULT = ["screen", "bed23", "caffeine", "late_meal", "water2", "stretch", "stress", "sick"]  # самые частые в WHOOP
MIN_EACH = 3      # наш расчёт: влияние показываем, если есть ≥3 дня «да» и ≥3 дня «нет» с восстановлением наутро
WINDOW = 90       # наш расчёт: влияние — по ответам за последние 90 дней
BACK = 30         # отвечать и листать можно на 30 дней назад

SCHEMA = """
CREATE TABLE IF NOT EXISTS journal (
  user INTEGER, day TEXT, key TEXT, value INTEGER, at TEXT, PRIMARY KEY (user, day, key)
);
CREATE TABLE IF NOT EXISTS journal_setup (user INTEGER PRIMARY KEY, keys TEXT);
"""


def _ready(db):
    if not getattr(db, "_journal_ready", False):
        with db.lock:
            db.db.executescript(SCHEMA)
            db.db.commit()
        db._journal_ready = True
    return db


def _iso(d):
    return d.isoformat() if isinstance(d, datetime.date) else str(d)


def _day(s):
    try:
        return datetime.date.fromisoformat(str(s))
    except ValueError:
        raise ValueError("неверная дата") from None


# ---------- хранение ----------

def selected(db, uid):
    r = _ready(db).q("SELECT keys FROM journal_setup WHERE user=?", (uid,))
    if not r:
        return list(DEFAULT)
    keys = [k for k in json.loads(r[0]["keys"] or "[]") if k in BY_KEY]
    return keys or list(DEFAULT)


def setup(db, uid, keys):
    keys = [k for k in KEYS if k in set(keys or [])]  # порядок — как в списке
    if not keys:
        raise ValueError("выбери хотя бы один вопрос")
    _ready(db).x("INSERT OR REPLACE INTO journal_setup VALUES (?,?)", (uid, json.dumps(keys)))
    return keys


def answer(db, uid, day, key, value, today):
    if key not in BY_KEY:
        raise ValueError("нет такого вопроса")
    d = _day(day)
    if d >= today:
        raise ValueError("дневник заполняется про прошедший день")
    if d < today - datetime.timedelta(days=BACK):
        raise ValueError(f"ответить можно только за последние {BACK} дней")
    _ready(db)
    if value is None or value == "":
        db.x("DELETE FROM journal WHERE user=? AND day=? AND key=?", (uid, d.isoformat(), key))
    else:
        v = 1 if value in (True, 1, "1", "true", "yes", "да") else 0
        now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        db.x("INSERT OR REPLACE INTO journal VALUES (?,?,?,?,?)", (uid, d.isoformat(), key, v, now))


def answers(db, uid, since, until):
    """{день: {key: True/False}} за период (включительно)."""
    out = {}
    for r in _ready(db).q("SELECT day, key, value FROM journal WHERE user=? AND day>=? AND day<=? ORDER BY day",
                          (uid, _iso(since), _iso(until))):
        out.setdefault(r["day"], {})[r["key"]] = bool(r["value"])
    return out


# ---------- влияние на восстановление ----------

def after(day):
    """День, чьё восстановление показывает последствия ответа за day: следующее утро."""
    return (_day(day) + datetime.timedelta(days=1)).isoformat()


def impact(ans, rec):
    """ans — {день: {key: bool}}, rec — {день: % восстановления}. Средний % наутро после «да» минус после «нет»."""
    out = []
    for key in KEYS:
        yes, no = [], []
        for day, a in ans.items():
            if key not in a:
                continue
            r = rec.get(after(day))
            if r is None:
                continue
            (yes if a[key] else no).append(r)
        if not yes and not no:
            continue
        enough = len(yes) >= MIN_EACH and len(no) >= MIN_EACH
        ya = round(sum(yes) / len(yes), 1) if yes else None
        na = round(sum(no) / len(no), 1) if no else None
        out.append({"key": key, "title": BY_KEY[key][2], "short": BY_KEY[key][3], "yes": len(yes), "no": len(no),
                    "yes_avg": ya, "no_avg": na, "enough": enough,
                    "impact": round(ya - na) if enough else None})
    out.sort(key=lambda x: (not x["enough"], -abs(x["impact"] or 0), -(x["yes"] + x["no"])))
    return out


def impacts(app, uid, today):
    since = today - datetime.timedelta(days=WINDOW)
    ans = answers(app.db, uid, since, today - datetime.timedelta(days=1))
    rec = {r["day"]: r["recovery"] for r in app.insights.series(app.db, uid, today, WINDOW + 1) if r["recovery"] is not None}
    return impact(ans, rec)


# ---------- подсказки из честных данных за день ----------

def hints(app, uid, day):
    out = {}
    d = _iso(day)
    ml = app.db.water_total(uid, d)
    if ml:
        out["water2"] = f"по учёту воды: {ml / 1000:.1f} л".replace(".", ",")
    days = app.insights.by_day(app.db, uid, d, d).get(d) or {}
    w = len(days.get("workouts") or [])
    if w:
        out["double"] = f"WHOOP: {w} {'тренировка' if w == 1 else 'тренировки' if w < 5 else 'тренировок'}"
    if days.get("naps"):
        out["nap"] = "WHOOP записал дневной сон"
    return out


# ---------- API ----------

def payload(app, uid, today, day=None):
    y = today - datetime.timedelta(days=1)
    d = min(_day(day), y) if day else y
    d = max(d, today - datetime.timedelta(days=BACK))
    sel = selected(app.db, uid)
    recent = answers(app.db, uid, today - datetime.timedelta(days=BACK), y)
    return {
        "day": d.isoformat(), "yesterday": y.isoformat(), "first": (today - datetime.timedelta(days=BACK)).isoformat(),
        "groups": [{"key": k, "title": t} for k, t in GROUPS],
        "habits": [{"key": k, "group": g, "title": t} for k, g, t, _ in HABITS],
        "selected": sel, "default": DEFAULT, "answers": recent.get(d.isoformat(), {}),
        "hints": hints(app, uid, d), "recent": recent,
        "impact": impacts(app, uid, today), "min": MIN_EACH, "ext": extra(app, uid, today),
    }


def route(app, uid, path, b, today):
    if path == "/api/journal":
        return payload(app, uid, today, b.get("d"))
    if path == "/api/journal/answer":
        answer(app.db, uid, b.get("day"), str(b.get("key") or ""), b.get("value"), today)
        return payload(app, uid, today, b.get("day"))
    if path == "/api/journal/setup":
        setup(app.db, uid, b.get("keys") or [])
        return payload(app, uid, today, b.get("day"))
    return None


def extra(app, uid, today):
    y = today - datetime.timedelta(days=1)
    sel = selected(app.db, uid)
    got = answers(app.db, uid, y, y).get(y.isoformat(), {})
    done = len([k for k in sel if k in got])
    top = [{k: x[k] for k in ("key", "short", "impact", "yes", "no")} for x in impacts(app, uid, today) if x["enough"]][:3]
    return {"day": y.isoformat(), "total": len(sel), "answered": done, "pending": len(sel) - done, "top": top}


def context_lines(app, uid, today):
    y = today - datetime.timedelta(days=1)
    got = answers(app.db, uid, y, y).get(y.isoformat(), {})
    out = []
    if got:
        out.append(f"Дневник за вчера ({y.strftime('%d.%m')}): "
                   + "; ".join(f"{BY_KEY[k][3]} — {'да' if v else 'нет'}" for k, v in got.items() if k in BY_KEY))
    top = [x for x in impacts(app, uid, today) if x["enough"]][:3]
    if top:
        out.append("Влияние на восстановление по дневнику (наш расчёт: средний % наутро после «да» минус после «нет»): "
                   + "; ".join(f"{x['short']}: {x['impact']:+d}% ({x['yes']} да / {x['no']} нет)" for x in top))
    return out
