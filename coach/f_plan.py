"""Мой план (как WHOOP My Plan): цели недели и планер сна «Пик / Норма / Минимум».

Подключение — FEATURES в coach.py: route / extra / context_lines (см. там).
  GET  /api/plan                 — цели, прогресс недели пн–вс, планер сна, текстовые цели
  POST /api/plan/save {goals: {sleep: {on, kind, value}, …}, sleep_mode, wake}

День недели — как в WHOOP: сон и отбой относятся к дню, утром которого закончился сон.
Состояние точки дня: done — выполнено, miss — нет, today — сегодня ещё не выполнено, future — впереди, nodata — нет данных.
"""
import datetime
import json
import re
import statistics

FALL_ASLEEP = 15  # минут на засыпание в планере сна (наш расчёт; WHOOP своё число не отдаёт)
BED_WINDOW = 30  # «вовремя лёг» — ±30 минут к цели отбоя (наш расчёт)
MODES = {"peak": (1.0, "Пик"), "norm": (0.85, "Норма"), "min": (0.70, "Минимум")}  # доля потребности сна, как в WHOOP
GOALS = ("sleep", "workouts", "strain", "bedtime", "water", "protein")
TITLES = {"sleep": "Сон", "workouts": "Тренировки", "strain": "Нагрузка в цели", "bedtime": "Режим отбоя",
          "water": "Вода", "protein": "Белок"}
DEFAULTS = {  # наш расчёт: разумный старт, всё меняется в «Изменить цели»
    "sleep": {"on": True, "kind": "pct", "value": 85},
    "workouts": {"on": True, "n": None},  # None — из профиля или истории
    "strain": {"on": True, "n": 4},
    "bedtime": {"on": True, "n": 5, "time": None},  # None — по планеру сна
    "water": {"on": True, "n": 5},
    "protein": {"on": False, "n": 4},
}
LIMITS = {"workouts": (1, 14), "strain": (1, 7), "bedtime": (1, 7), "water": (1, 7), "protein": (1, 7)}
WD = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
WD_WORDS = (("пн", "понед"), ("вт", "вторн"), ("ср", "сред"), ("чт", "четв"), ("пт", "пятн"), ("сб", "суб"), ("вс", "воскр"))

_ready = set()


def _db(app):
    db = app.db
    if id(db) not in _ready:
        db.x("CREATE TABLE IF NOT EXISTS plan (user INTEGER PRIMARY KEY, data TEXT, updated TEXT)")
        _ready.add(id(db))
    return db


# ---------- настройки ----------

def load(app, uid):
    r = _db(app).q("SELECT data FROM plan WHERE user=?", (uid,))
    try:
        saved = json.loads(r[0]["data"]) if r else {}
    except ValueError:
        saved = {}
    goals = {k: {**v, **((saved.get("goals") or {}).get(k) or {})} for k, v in DEFAULTS.items()}
    return {"goals": goals, "sleep_mode": saved.get("sleep_mode") if saved.get("sleep_mode") in MODES else "norm",
            "wake": saved.get("wake")}


def _store(app, uid, cfg):
    _db(app).x("INSERT OR REPLACE INTO plan VALUES (?,?,?)",
               (uid, json.dumps(cfg, ensure_ascii=False), datetime.datetime.now().isoformat(timespec="seconds")))


def _clock(v, what):
    v = str(v or "").strip().replace(".", ":")
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", v)
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ValueError(f"{what}: время в виде ЧЧ:ММ, например 07:00")
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def _int(v, lo, hi, what):
    try:
        n = float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        raise ValueError(f"{what}: нужно число") from None
    if not lo <= n <= hi:
        raise ValueError(f"{what}: от {lo} до {hi}")
    return n


def save(app, uid, b):
    cfg = load(app, uid)
    for k, g in (b.get("goals") or {}).items():
        if k not in DEFAULTS or not isinstance(g, dict):
            continue
        cur = cfg["goals"][k]
        if "on" in g:
            cur["on"] = bool(g["on"])
        if k == "sleep":
            if g.get("kind") in ("pct", "hours"):
                cur["kind"] = g["kind"]
            if "value" in g:
                if cur["kind"] == "pct":
                    cur["value"] = round(_int(g["value"], 50, 100, "Сон, %"))
                else:
                    cur["value"] = round(_int(g["value"], 5, 12, "Сон, часов") * 2) / 2
        elif "n" in g:
            lo, hi = LIMITS[k]
            cur["n"] = None if g["n"] is None else round(_int(g["n"], lo, hi, TITLES[k]))
        if k == "bedtime" and "time" in g:
            cur["time"] = _clock(g["time"], "Цель отбоя") if g["time"] else None
    if "sleep_mode" in b:
        if b["sleep_mode"] not in MODES:
            raise ValueError("нет такого режима сна")
        cfg["sleep_mode"] = b["sleep_mode"]
    if "wake" in b:
        cfg["wake"] = _clock(b["wake"], "Подъём") if b["wake"] else None
    _store(app, uid, cfg)
    return cfg


# ---------- время ----------

def _mins(hhmm):
    try:
        h, m = (int(x) for x in str(hhmm).split(":"))
        return h * 60 + m
    except ValueError:
        return None


def _hhmm(m):
    m = round(m) % (24 * 60)
    return f"{m // 60:02d}:{m % 60:02d}"


def _diff(a, b):
    """Разница a − b в минутах по кругу суток (−720…720)."""
    return (a - b + 720) % 1440 - 720


def _h(x):
    m = round(x * 60)
    return f"{m // 60} ч {m % 60:02d} мин" if m % 60 else f"{m // 60} ч"


# ---------- планер сна ----------

def planner(t, cfg, profile):
    """Во сколько лечь: подъём минус потребность сна × режим и 15 минут на засыпание (наш расчёт, как WHOOP Sleep Planner)."""
    bt = t.get("bedtime") or {}
    slp = t.get("sleep") or {}
    age = profile.get("age")
    floor = 8.5 if age and age < 18 else 7.5  # как insights.bedtime: подростку не меньше 8,5 ч
    need = bt.get("need") or max(slp.get("need") or 0, floor)
    need_from = "WHOOP за прошлую ночь" if (slp.get("need") or 0) >= floor else "возраст: не меньше 8,5 ч" if floor > 8 else "минимум 7,5 ч"
    wake = cfg.get("wake") or bt.get("wake") or "07:00"
    w = _mins(wake)
    opts = []
    for key, (k, name) in MODES.items():
        hours = need * k
        bed = w - round(hours * 60) - FALL_ASLEEP
        bed -= bed % 5  # вниз до 5 минут
        opts.append({"mode": key, "name": name, "pct": round(k * 100), "hours": round(hours, 2), "bed": _hhmm(bed)})
    sel = next(o for o in opts if o["mode"] == cfg["sleep_mode"])
    return {"need": round(need, 2), "need_from": need_from, "wake": wake, "wake_auto": not cfg.get("wake"),
            "usual_wake": bt.get("wake"), "mode": cfg["sleep_mode"], "options": opts, "bed": sel["bed"],
            "hours": sel["hours"], "fall": FALL_ASLEEP}


# ---------- прогресс недели ----------

def workouts_default(app, uid, today, days, profile):
    """Сколько тренировок в неделю: из расписания в профиле, иначе медиана за 4 прошлые недели, иначе 4."""
    sch = (profile.get("schedule") or "").lower()
    m = re.search(r"(\d+)\s*(?:раз|трен)", sch)
    if m and 1 <= int(m.group(1)) <= 14:
        return int(m.group(1)), "из профиля"
    n = sum(1 for words in WD_WORDS if any(re.search(r"\b" + w, sch) for w in words))
    if n:
        return n, "из расписания в профиле"
    mon = today - datetime.timedelta(days=today.weekday())
    weeks = []
    for i in range(1, 5):
        a = mon - datetime.timedelta(days=7 * i)
        ds = [(a + datetime.timedelta(days=j)).isoformat() for j in range(7)]
        if sum(1 for d in ds if d in days) >= 5:  # неделя с данными WHOOP
            weeks.append(sum(len(days[d]["workouts"]) for d in ds if d in days))
    if weeks:
        return max(1, min(14, round(statistics.median(weeks)))), "по твоим прошлым неделям"
    return 4, "обычно 4"


def _dot(d, today, ok, has, final=False):
    """Точка дня: ok — выполнено ли, has — есть ли данные, final — данные дня уже окончательные (сон прошлой ночи)."""
    if d > today:
        return "future"
    if ok:
        return "done"
    if d == today and not (final and has):
        return "today"
    return "miss" if has else "nodata"


def week(app, uid, today, cfg=None, t=None):
    cfg = cfg or load(app, uid)
    profile, body = app.profile(uid), app.whoop.body(app.db, uid)
    ins = app.insights
    mon = today - datetime.timedelta(days=today.weekday())
    sun = mon + datetime.timedelta(days=6)
    dates = [mon + datetime.timedelta(days=i) for i in range(7)]
    days = ins.by_day(app.db, uid, (mon - datetime.timedelta(days=45)).isoformat(), today.isoformat())
    t = t or ins.today(app.db, uid, today, profile, body)
    plan = planner(t, cfg, profile)
    water = app.db.water_days(uid, mon.isoformat(), sun.isoformat())
    meals = {}
    for m in app.db.meals(uid, mon.isoformat(), sun.isoformat()):
        meals.setdefault(m["day"], []).append(m)
    out = []

    def day_of(d):
        return days.get(d.isoformat()) or {"recovery": None, "sleep": None, "cycle": None, "workouts": []}

    def norms(d):
        """Нормы воды и белка на день — тем же расчётом, что «Питание» (insights.nutrition)."""
        v = day_of(d)
        mins = sum(w["minutes"] or 0 for w in v["workouts"])
        nut = ins.nutrition(profile, body, ins.baseline(days, d), None, mins)
        return nut, app.water.norm(nut, mins)[0]

    def add(key, dots, count, need, target, value=None, note=""):
        g = cfg["goals"][key]
        if value is not None:  # цель по среднему (сон): держится или ниже
            status = "done" if value else ("behind" if any(x in ("done", "miss") for x in dots) else "ok")
            reach = None
        else:  # цель по числу дней: ещё успеть, если осталось нужное не больше, чем дней впереди (вместе с сегодня)
            reach = max(0, need - count)
            free = sum(1 for x in dots if x in ("today", "future"))
            status = "done" if not reach else "ok" if reach <= free else "behind"
            if reach and all(x in ("nodata", "today", "future") for x in dots) and key in ("water", "protein"):
                status = "nodata"  # ничего не записано — не «провал», а «нет записей»
        out.append({"id": key, "title": TITLES[key], "on": g["on"], "target": target, "count": count, "need": need,
                    "dots": dots, "status": status, "left": reach, "note": note, "value": value})

    g = cfg["goals"]
    # сон: выполнение сна (как WHOOP) или часы за ночь; неделя выполнена, если среднее ≥ цели
    s = g["sleep"]
    vals, dots = [], []
    for d in dates:
        sl = day_of(d)["sleep"] if d <= today else None
        v = None
        if sl:
            v = (sl.get("performance") if s["kind"] == "pct" else sl.get("hours"))
            if v is None and s["kind"] == "pct" and sl.get("need"):
                v = round(sl["hours"] / sl["need"] * 100)
        if v is not None:
            vals.append(v)
        dots.append(_dot(d, today, v is not None and v >= s["value"], v is not None, final=True))
    avg = round(statistics.mean(vals), 1) if vals else None
    unit = "%" if s["kind"] == "pct" else " ч"
    add("sleep", dots, sum(1 for x in dots if x == "done"), len(vals),
        f"в среднем ≥ {s['value']}{unit}" if s["kind"] == "pct" else f"в среднем ≥ {str(s['value']).replace('.', ',')} ч за ночь",
        value=avg is not None and avg >= s["value"],
        note=(f"среднее {round(avg) if s['kind'] == 'pct' else str(round(avg, 1)).replace('.', ',')}{unit}" if avg is not None
              else "сна за неделю ещё нет"))
    out[-1].update(avg=avg, kind=s["kind"])

    # тренировки: N в неделю (день с тренировкой — точка)
    n_auto, src = workouts_default(app, uid, today, days, profile)
    need = g["workouts"]["n"] or n_auto
    cnt = sum(len(day_of(d)["workouts"]) for d in dates if d <= today)
    dots = [_dot(d, today, bool(day_of(d)["workouts"]), d.isoformat() in days) for d in dates]
    dots = ["miss" if x == "nodata" else x for x in dots]  # нет тренировки — это не «нет данных»
    add("workouts", dots, cnt, need, f"{need} в неделю", note="" if g["workouts"]["n"] else src)
    out[-1]["auto"] = n_auto

    # нагрузка в целевом диапазоне — тем же правилом, что «Цель нагрузки» (insights.strain_band по восстановлению дня)
    dots = []
    for d in dates:
        v = day_of(d)
        band = ins.strain_band(v["recovery"])
        sv = (v["cycle"] or {}).get("strain")
        dots.append(_dot(d, today, bool(band and sv is not None and band[0] <= sv <= band[1]), bool(band and sv is not None)))
    add("strain", dots, dots.count("done"), g["strain"]["n"], f"{g['strain']['n']} {_days(g['strain']['n'])} в неделю")

    # режим отбоя: начало сна ±30 мин к цели (своё время или по планеру сна)
    bt_t = g["bedtime"].get("time") or plan["bed"]
    tm = _mins(bt_t)
    dots = []
    for d in dates:
        sl = day_of(d)["sleep"] if d <= today else None
        st = _mins(sl.get("start")) if sl else None
        dots.append(_dot(d, today, st is not None and abs(_diff(st, tm)) <= BED_WINDOW, st is not None, final=True))
    add("bedtime", dots, dots.count("done"), g["bedtime"]["n"], f"лечь в {bt_t} ±{BED_WINDOW} мин · {g['bedtime']['n']} {_days(g['bedtime']['n'])}",
        note="" if g["bedtime"].get("time") else "цель — из планера сна")
    out[-1]["time"] = bt_t

    # вода и белок — по записям; нет записей за день — «не записано», а не «не выполнено»
    wd, pd = [], []
    for d in dates:
        k = d.isoformat()
        if d > today:
            wd.append("future")
            pd.append("future")
            continue
        nut, wnorm = norms(d)
        drank = water.get(k)
        wd.append(_dot(d, today, bool(drank and drank >= wnorm), bool(drank)))
        eaten = meals.get(k) or []
        prot = sum(m.get("protein") or 0 for m in eaten)
        pd.append(_dot(d, today, bool(eaten and nut and prot >= nut["protein"]), bool(eaten)))
    add("water", wd, wd.count("done"), g["water"]["n"], f"норма {g['water']['n']} {_days(g['water']['n'])}",
        note="" if water else "воду на этой неделе не записывал")
    nut0, _ = norms(today)
    add("protein", pd, pd.count("done"), g["protein"]["n"],
        (f"≥ {nut0['protein']} г · " if nut0 else "") + f"{g['protein']['n']} {_days(g['protein']['n'])}",
        note="" if meals else "еду на этой неделе не записывал")
    on = [x for x in out if x["on"]]
    return {"week": [d.isoformat() for d in dates], "today": today.isoformat(), "from": mon.isoformat(), "to": sun.isoformat(),
            "goals": out, "met": sum(1 for x in on if x["status"] == "done"), "total": len(on), "planner": plan,
            "texts": [{"id": r["id"], "text": r["text"]} for r in app.db.goals(uid)], "config": cfg["goals"]}


def _days(n):
    return "день" if n % 10 == 1 and n % 100 != 11 else "дня" if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else "дней"


# ---------- подключение ----------

def route(app, uid, path, b, today):
    if path == "/api/plan":
        return week(app, uid, today)
    if path == "/api/plan/save":
        cfg = save(app, uid, b or {})
        return week(app, uid, today, cfg)
    return None


def extra(app, uid, today):
    return week(app, uid, today)


def _line(g):
    if g["value"] is not None:  # сон — по среднему
        return f"{g['title']} ({g['target']}): {g['note']}" + (" — цель держится" if g["status"] == "done" else " — ниже цели")
    st = {"done": "выполнено", "ok": f"осталось {g['left']}", "behind": f"осталось {g['left']} — уже не успеть",
          "nodata": "нет записей"}[g["status"]]
    return f"{g['title']} ({g['target']}): {g['count']} из {g['need']}, {st}" + (f" ({g['note']})" if g["note"] else "")


def context_lines(app, uid, today):
    w = week(app, uid, today)
    on = [g for g in w["goals"] if g["on"]]
    p = w["planner"]
    name = next(o["name"] for o in p["options"] if o["mode"] == p["mode"])
    lines = [f"Мой план на неделю ({WD[0]}–{WD[6]}, сегодня {WD[today.weekday()]}), выполнено целей {w['met']} из {w['total']}:"]
    lines += [f"- {_line(g)}" for g in on]
    lines.append(f"План сна на сегодня: режим «{name}» ({_h(p['hours'])} из нужных {_h(p['need'])}) — лечь в {p['bed']}, "
                 f"подъём {p['wake']} (+{p['fall']} мин на засыпание). Советуй с учётом этих целей.")
    return lines
