"""Режим дня: отбой и подъём (с плавным сдвигом сна к учёбе), тренировка и её интенсивность, массаж,
калории/белок/вода на день, вечерний чек-лист и короткие советы.

Подключение — FEATURES в coach.py: route / extra / context_lines (см. там). Формат — coach/SYNC.md, п. 4.
  POST /api/regime {}                    → {cfg, today, week: 7 дней с сегодня (как today)}
  POST /api/regime/save {cfg} | {поля}   → то же после сохранения (меняются только присланные поля, с проверкой)
  POST /api/regime/check {id, done}      → то же; отметка пункта вечернего чек-листа на сегодня
Настройки — meta regime:<uid> (пока не сохранял — общие значения по умолчанию, бот не напоминает и тренер их не видит),
отметки чек-листа — meta regime:check:<uid>:<день>.

День d: wake — подъём утром дня d, bed — отбой вечером дня d (перед d+1).
Сдвиг сна (shift): от from отбой и подъём каждые every дней на step минут раньше, пока не дойдут до bed/wake;
вечер перед первым учебным днём — уже цель (последний шаг может быть меньше). С первого учебного дня сдвиг закончен.
"""
import copy
import datetime
import json
import re

LIGHT_RECOVERY = 34  # восстановление ниже — тренировка лёгкая (красная зона WHOOP)
STREAK_LIGHT = 3  # 3-й день тренировок подряд — лёгкая (техника)
WD_WORDS = (("пн", "понед"), ("вт", "вторн"), ("ср", "сред"), ("чт", "четв"), ("пт", "пятн"), ("сб", "суб"), ("вс", "воскр"))
MODES = ("vacation", "school", "weekend")
DEFAULTS = {  # общий разумный старт — всё меняется в приложении
    "train_days": [], "train_time": "18:00", "train_minutes": 90,
    "school_days": [0, 1, 2, 3, 4], "school_start": "09:00", "school_end": "16:00",
    "wake": "07:00", "bed": "22:00", "weekend_wake": "08:00", "massage_days": [],
    "vacation": None, "shift": None,
    "evening": ["Душ", "Собрать вещи на завтра", "Телефон в сторону"],
    "remind": 45,
}
CLOCKS = ("train_time", "school_start", "school_end", "wake", "bed", "weekend_wake")
DAY_LISTS = ("train_days", "school_days", "massage_days")
MAX_EVENING = 12


# ---------- время ----------

def _mins(hhmm):
    h, m = (int(x) for x in str(hhmm).split(":"))
    return h * 60 + m


def _hhmm(m):
    m = round(m) % (24 * 60)
    return f"{m // 60:02d}:{m % 60:02d}"


def _diff(a, b):
    """a − b в минутах по кругу суток (−720…720)."""
    return (a - b + 720) % 1440 - 720


def _date(v):
    return v if isinstance(v, datetime.date) else datetime.date.fromisoformat(str(v))


# ---------- проверка настроек ----------

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
    return round(n)


def _days(v, what):
    if not isinstance(v, list) or len(v) > 7:
        raise ValueError(f"{what}: список дней недели 0–6")
    out = set()
    for x in v:
        if isinstance(x, bool) or not isinstance(x, (int, float)) or int(x) != x or not 0 <= x <= 6:
            raise ValueError(f"{what}: дни недели числами 0 (пн) – 6 (вс)")
        out.add(int(x))
    return sorted(out)


def _iso(v, what):
    v = str(v or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        raise ValueError(f"{what}: дата в виде ГГГГ-ММ-ДД")
    try:
        return datetime.date.fromisoformat(v).isoformat()
    except ValueError:
        raise ValueError(f"{what}: нет такой даты") from None


def _vacation(v):
    if not v:
        return None
    if not isinstance(v, dict):
        raise ValueError("Каникулы: нужны даты from и to")
    a, b = _iso(v.get("from"), "Каникулы с"), _iso(v.get("to"), "Каникулы по")
    if b < a:
        raise ValueError("Каникулы: конец раньше начала")
    if (_date(b) - _date(a)).days > 120:
        raise ValueError("Каникулы: не длиннее 120 дней")
    out = {"from": a, "to": b}
    if v.get("train_time"):
        out["train_time"] = _clock(v["train_time"], "Тренировка в каникулы")
    for k in ("massage_days", "train_days"):
        if v.get(k) is not None:
            out[k] = _days(v[k], "Каникулы: " + ("массаж" if k == "massage_days" else "тренировки"))
    return out


def _shift(v):
    if not v:
        return None
    if not isinstance(v, dict):
        raise ValueError("Сдвиг сна: нужны from, bed, wake")
    return {"from": _iso(v.get("from"), "Сдвиг сна с"), "bed": _clock(v.get("bed"), "Сдвиг: отбой сейчас"),
            "wake": _clock(v.get("wake"), "Сдвиг: подъём сейчас"),
            "step": _int(v.get("step", 30), 5, 120, "Сдвиг: шаг, мин"),
            "every": _int(v.get("every", 2), 1, 14, "Сдвиг: каждые N дней")}


def _evening(v):
    if not isinstance(v, list):
        raise ValueError("Вечер: список дел")
    out = []
    for x in v:
        s = " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", str(x if x is not None else "")).split())[:80]
        if s:
            out.append(s)
    if len(out) > MAX_EVENING:
        raise ValueError(f"Вечер: не больше {MAX_EVENING} дел")
    return out


def clean(src, cfg):
    """Присланные поля (src) поверх cfg — с проверкой. Неизвестные поля пропускаем, неверные — ValueError."""
    cfg = copy.deepcopy(cfg)
    for k in CLOCKS:
        if k in src:
            cfg[k] = _clock(src[k], {"train_time": "Тренировка", "school_start": "Учёба с", "school_end": "Учёба до",
                                     "wake": "Подъём", "bed": "Отбой", "weekend_wake": "Подъём в выходные"}[k])
    for k in DAY_LISTS:
        if k in src:
            cfg[k] = _days(src[k], {"train_days": "Тренировки", "school_days": "Учёба", "massage_days": "Массаж"}[k])
    if "train_minutes" in src:
        cfg["train_minutes"] = _int(src["train_minutes"], 10, 300, "Тренировка, мин")
    if "remind" in src:
        cfg["remind"] = _int(src["remind"] or 0, 0, 240, "Напомнить за, мин")
    if "vacation" in src:
        cfg["vacation"] = _vacation(src["vacation"])
    if "shift" in src:
        cfg["shift"] = _shift(src["shift"])
    if "evening" in src:
        cfg["evening"] = _evening(src["evening"])
    return cfg


# ---------- хранение ----------

def profile_days(profile):
    """Дни и время тренировок из расписания в профиле («пн ср пт 18:00») — или ([], None)."""
    sch = str((profile or {}).get("schedule") or "").lower()
    days = [i for i, words in enumerate(WD_WORDS) if any(re.search(r"\b" + w, sch) for w in words)]
    m = re.search(r"\b(\d{1,2})[:.](\d{2})\b", sch)
    tm = None
    if m and int(m.group(1)) <= 23 and int(m.group(2)) <= 59:
        tm = f"{int(m.group(1)):02d}:{m.group(2)}"
    return days, tm


def _saved(db, uid):
    try:
        v = json.loads(db.meta(f"regime:{uid}") or "null")
    except ValueError:
        return None
    return v if isinstance(v, dict) else None


def configured(db, uid):
    """Сохранял ли режим сам (только тогда бот напоминает и тренер видит режим)."""
    return _saved(db, uid) is not None


def load(db, uid, profile=None):
    cfg = copy.deepcopy(DEFAULTS)
    days, tm = profile_days(profile)
    cfg["train_days"] = days
    if tm:
        cfg["train_time"] = tm
    saved = _saved(db, uid) or {}
    try:
        return clean(saved, cfg)
    except ValueError:  # старые/битые настройки — не роняем приложение
        return cfg


def save(db, uid, b, profile=None):
    src = b.get("cfg") if isinstance(b.get("cfg"), dict) else b
    cfg = clean(src, load(db, uid, profile))
    db.meta(f"regime:{uid}", json.dumps(cfg, ensure_ascii=False))
    return cfg


def checks(db, uid, day):
    try:
        v = json.loads(db.meta(f"regime:check:{uid}:{day}") or "[]")
    except ValueError:
        return set()
    return {x for x in v if isinstance(x, str)} if isinstance(v, list) else set()


def check(db, uid, day, cfg, cid, done):
    cid = str(cid or "")
    if not re.fullmatch(r"e\d{1,2}", cid) or int(cid[1:]) >= len(cfg["evening"]):
        raise ValueError("нет такого пункта")
    cur = checks(db, uid, day)
    (cur.add if done else cur.discard)(cid)
    db.meta(f"regime:check:{uid}:{day}", json.dumps(sorted(cur)))
    return cur


# ---------- расчёт ----------

def mode_of(cfg, d):
    v = cfg.get("vacation")
    if v and v["from"] <= d.isoformat() <= v["to"]:
        return "vacation"
    return "school" if d.weekday() in cfg["school_days"] else "weekend"


def _first_school(cfg, start):
    for i in range(400):
        d = start + datetime.timedelta(days=i)
        if mode_of(cfg, d) == "school":
            return d
    return None


def _shifted(cfg, d):
    """(отбой, подъём) по сдвигу сна в день d или None (сдвига нет, ещё не начался или уже закончился)."""
    sh = cfg.get("shift")
    if not sh:
        return None
    start = _date(sh["from"])
    if d < start:
        return None
    first = _first_school(cfg, start)
    if first and d >= first:
        return None  # с первого учебного дня — обычный режим
    tb, tw = _mins(cfg["bed"]), _mins(cfg["wake"])
    if first and d + datetime.timedelta(days=1) == first:
        return cfg["bed"], cfg["wake"]  # вечер перед учёбой — уже цель
    k = (d - start).days // sh["every"]
    gb, gw = max(0, _diff(_mins(sh["bed"]), tb)), max(0, _diff(_mins(sh["wake"]), tw))
    return _hhmm(tb + max(0, gb - k * sh["step"])), _hhmm(tw + max(0, gw - k * sh["step"]))


def bed_wake(cfg, d):
    """(отбой вечером дня d, подъём утром дня d)."""
    sh = _shifted(cfg, d)
    if sh:
        return sh
    wake = cfg["wake"] if mode_of(cfg, d) == "school" else cfg["weekend_wake"]
    if mode_of(cfg, d + datetime.timedelta(days=1)) == "school":
        bed = cfg["bed"]
    else:  # завтра не учиться — ложится позже на столько же, на сколько позже встанет (сон той же длины)
        bed = _hhmm(_mins(cfg["bed"]) + _diff(_mins(cfg["weekend_wake"]), _mins(cfg["wake"])))
    return bed, wake


def _vac_or(cfg, d, key):
    v = cfg.get("vacation")
    if mode_of(cfg, d) == "vacation" and v.get(key) is not None:
        return v[key]
    return cfg[key]


def train_time(cfg, d):
    return _vac_or(cfg, d, "train_time")


def train_on(cfg, d):
    return d.weekday() in _vac_or(cfg, d, "train_days")


def streak(cfg, d):
    """Какой по счёту день тренировок подряд — d (0, если в d тренировки нет)."""
    n = 0
    while n < 7 and train_on(cfg, d - datetime.timedelta(days=n)):
        n += 1
    return n


def intensity(cfg, d, recovery=None):
    """(full|light|rest, почему)."""
    if not train_on(cfg, d):
        return "rest", "День отдыха — прогулка и растяжка"
    if recovery is not None and recovery < LIGHT_RECOVERY:
        return "light", f"Восстановление {recovery}% — лёгкая тренировка, без тяжёлой работы"
    n = streak(cfg, d)
    if n >= STREAK_LIGHT:
        return "light", f"{n}-й день тренировок подряд — техника, без тяжёлого"
    return "full", "Можно в полную силу"


def food(cfg, d, kcal_goals=None, nutrition=None, water_norm=None):
    """(ккал, белок, вода мл): цели «Калорий» на день тренировки/отдыха, иначе наш расчёт (insights.nutrition)."""
    kind = "train" if train_on(cfg, d) else "rest"
    g = ((kcal_goals or {}).get("goals") or {}).get(kind) or {}
    w = ((kcal_goals or {}).get("water") or {}).get(kind)
    n = nutrition or {}
    return (g.get("kcal") or n.get("kcal"), g.get("p") or n.get("protein"), w or water_norm or n.get("water"))


def plural_days(n):
    return "день" if n % 10 == 1 and n % 100 != 11 else "дня" if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else "дней"


def day_plan(cfg, d, recovery=None, kcal_goals=None, nutrition=None, water_norm=None, done=()):
    """План дня d по договору (SYNC.md, п. 4). recovery — только для сегодняшнего дня."""
    bed, wake = bed_wake(cfg, d)
    mode = mode_of(cfg, d)
    on = train_on(cfg, d)
    level, why = intensity(cfg, d, recovery)
    massage = d.weekday() in _vac_or(cfg, d, "massage_days")
    kcal, protein, water = food(cfg, d, kcal_goals, nutrition, water_norm)
    tips = []
    if recovery is not None and recovery < LIGHT_RECOVERY:
        tips.append(f"Сегодня восстановление {recovery}% — без тяжёлой работы")
    pb, _ = bed_wake(cfg, d - datetime.timedelta(days=1))
    if _shifted(cfg, d) and _diff(_mins(pb), _mins(bed)) > 0:
        tips.append(f"Отбой на {_diff(_mins(pb), _mins(bed))} мин раньше вчерашнего — {bed}")
    if mode == "vacation":
        first = _first_school(cfg, d)
        left = (first - d).days if first else None
        if left and left <= 7:
            tips.append(f"До учёбы {left} {plural_days(left)} — подъём сдвигаем к {cfg['wake']}")
    if massage:
        tips.append("Сегодня массаж" + (" — после тренировки" if on else ""))
    if not tips:
        tips.append(f"Отбой в {bed} — телефон в сторону за 30 минут")
    return {
        "day": d.isoformat(), "mode": mode, "bed": bed, "wake": wake,
        "train": {"on": on, "time": train_time(cfg, d) if on else None,
                  "minutes": cfg["train_minutes"] if on else 0, "intensity": level, "why": why},
        "massage": massage, "kcal": kcal, "protein": protein, "water": water,
        "checklist": [{"id": f"e{i}", "text": t, "done": f"e{i}" in done} for i, t in enumerate(cfg["evening"])],
        "tips": tips[:3],
    }


# ---------- с данными Формы ----------

def _inputs(app, uid, day, t=None):
    profile, body = app.profile(uid), app.whoop.body(app.db, uid)
    t = t or app.insights.today(app.db, uid, day, profile, body)
    return profile, body, t


def plans(app, uid, day, days=1, t=None, cfg=None):
    """Планы на days дней с day (у сегодняшнего — восстановление WHOOP и отметки чек-листа)."""
    profile, body, t = _inputs(app, uid, day, t)
    cfg = cfg or load(app.db, uid, profile)
    goals = app.db.kcal_goals(uid)
    out = []
    for i in range(days):
        d = day + datetime.timedelta(days=i)
        mins = cfg["train_minutes"] if train_on(cfg, d) else 0
        nut = app.insights.nutrition(profile, body, t.get("base"), None, mins)
        rec = (t.get("recovery") or {}).get("score") if i == 0 else None
        out.append(day_plan(cfg, d, rec, goals, nut, app.water.norm(nut, mins)[0], checks(app.db, uid, d.isoformat())))
    return cfg, out


def today(app, uid, day, t=None):
    return plans(app, uid, day, 1, t)[1][0]


def payload(app, uid, day, cfg=None):
    cfg, week = plans(app, uid, day, 7, cfg=cfg)
    return {"cfg": cfg, "today": week[0], "week": week}


INT_WORD = {"full": "в полную силу", "light": "лёгкая", "rest": "отдых"}
MODE_WORD = {"vacation": "каникулы", "school": "учёба", "weekend": "выходной"}


def brief_lines(p):
    """Строки режима для утреннего разбора в чате (HTML не нужен — экранировать нечего)."""
    lines = [f"🗓 Отбой {p['bed']} · подъём {p['wake']} · {MODE_WORD[p['mode']]}"]
    tr = p["train"]
    if tr["on"]:
        lines.append(f"🏋️ Тренировка {tr['time']} · {INT_WORD[tr['intensity']]}" + (" · массаж" if p["massage"] else ""))
    elif p["massage"]:
        lines.append("💆 Сегодня массаж")
    return lines


def due(cfg, now):
    """День, отбой которого через ≤ remind минут (now — время пользователя с часовым поясом), или None."""
    if not cfg.get("remind"):
        return None
    for d in (now.date(), now.date() - datetime.timedelta(days=1)):
        bed, _ = bed_wake(cfg, d)
        at = datetime.datetime.combine(d, datetime.time(*divmod(_mins(bed), 60)), tzinfo=now.tzinfo)
        if _mins(bed) < 12 * 60:  # отбой после полуночи — уже следующие сутки
            at += datetime.timedelta(days=1)
        if at - datetime.timedelta(minutes=cfg["remind"]) <= now < at:
            return d
    return None


# ---------- подключение ----------

def route(app, uid, path, b, today_):
    if path == "/api/regime":
        return payload(app, uid, today_)
    if path == "/api/regime/save":
        cfg = save(app.db, uid, b or {}, app.profile(uid))
        return payload(app, uid, today_, cfg)
    if path == "/api/regime/check":
        cfg = load(app.db, uid, app.profile(uid))
        check(app.db, uid, today_.isoformat(), cfg, (b or {}).get("id"), bool((b or {}).get("done")))
        return payload(app, uid, today_, cfg)
    return None


def extra(app, uid, today_):
    """В /api/all (ext.regime): cfg, today и week — главная показывает «Режим на сегодня»."""
    return payload(app, uid, today_)


def context_lines(app, uid, today_):
    if not configured(app.db, uid):
        return []
    p = today(app, uid, today_)
    tr = p["train"]
    line = (f"РЕЖИМ НА СЕГОДНЯ ({MODE_WORD[p['mode']]}, настроил сам): подъём {p['wake']}, отбой {p['bed']}; "
            + (f"тренировка {tr['time']}, {tr['minutes']} мин — {INT_WORD[tr['intensity']]} ({tr['why']})" if tr["on"]
               else "тренировки нет")
            + ("; массаж" if p["massage"] else "")
            + (f"; цель {p['kcal']} ккал, белок {p['protein']} г, вода {p['water']} мл" if p["kcal"] else ""))
    nxt = bed_wake(load(app.db, uid, app.profile(uid)), today_ + datetime.timedelta(days=1))[1]
    cfg = load(app.db, uid, app.profile(uid))
    shift = (f" Сон сдвигается постепенно (на {cfg['shift']['step']} мин раз в {cfg['shift']['every']} дн.) к отбою {cfg['bed']} — "
             "не советуй ложиться намного раньше сегодняшнего отбоя по плану: резкий сдвиг хуже засыпается; можно на 15–30 мин раньше."
             if cfg.get("shift") else "")
    return [line, f"Завтра подъём {nxt}. Советуй с учётом режима (отбой, интенсивность тренировки).{shift}"]
