"""Расчёты по данным WHOOP без ИИ: сводка дня, обычный уровень (базовая линия), ориентир нагрузки,
питание на день, ряды для графиков и простые выводы. Это показывает приложение всегда,
а Claude получает как готовый контекст (ему не нужно пересчитывать цифры).

Пороги восстановления как в WHOOP: зелёная зона 67–100, жёлтая 34–66, красная 0–33.
Ориентиры нагрузки и питания — наши (общие спортивные рекомендации), не WHOOP.
"""
import datetime
import json
import statistics

import water
import whoop

ZONES = ((67, "green"), (34, "yellow"), (0, "red"))
STRAIN_BAND = {"green": (14, 18), "yellow": (10, 14), "red": (4, 10)}
SPORTS = {
    "boxing": "Бокс", "kickboxing": "Кикбоксинг", "mma": "ММА", "martial-arts": "Единоборства",
    "running": "Бег", "cycling": "Велосипед", "walking": "Ходьба", "hiking": "Поход",
    "weightlifting": "Силовая", "powerlifting": "Пауэрлифтинг", "functional-fitness": "Функционалка",
    "hiit": "ВИИТ", "swimming": "Плавание", "soccer": "Футбол", "basketball": "Баскетбол",
    "volleyball": "Волейбол", "tennis": "Теннис", "yoga": "Йога", "stretching": "Растяжка",
    "jumping-rope": "Скакалка", "spin": "Сайкл", "rowing": "Гребля", "skiing": "Лыжи",
    "snowboarding": "Сноуборд", "hockey": "Хоккей", "dance": "Танцы", "activity": "Активность",
    "wrestling": "Борьба", "jiu-jitsu": "Джиу-джитсу", "elliptical": "Эллипс", "climbing": "Скалолазание",
    "massage-therapy": "Массаж", "meditation": "Медитация", "sauna": "Сауна", "ice-bath": "Ледяная ванна",
    "breathwork": "Дыхание", "cleaning": "Уборка", "strength-trainer": "Силовая", "pilates": "Пилатес",
    "box-fitness": "Бокс-фитнес", "assault-bike": "Эйр-байк", "stairmaster": "Степпер", "treadmill": "Беговая дорожка",
}
GOAL_NAMES = {"form": "Форма и результат", "mass": "Набор мышц", "dry": "Рельеф", "weight": "Держать вес"}


def goal_code(goal):
    """Код цели; старый профиль мог хранить цель своими словами («вырасти, профицит под рост») — угадать по словам."""
    goal = goal or "form"
    if goal in GOAL_NAMES:
        return goal
    g = str(goal).lower()
    if any(w in g for w in ("профицит", "масс", "набор", "набрать", "вырас")):
        return "mass"
    if any(w in g for w in ("рельеф", "суш", "похуд")):
        return "dry"
    return "form"


def zone(score):
    if score is None:
        return None
    for lo, name in ZONES:
        if score >= lo:
            return name
    return "red"


def sport_name(name):
    key = (name or "activity").lower().strip()
    return SPORTS.get(key) or key.replace("-", " ").capitalize()


def ms_h(ms):
    return round((ms or 0) / 3_600_000, 2)


def _score(r):
    return (r or {}).get("score") or {} if (r or {}).get("score_state") == "SCORED" else {}


def _local_time(ts, off):
    t = whoop._local(ts, off or "+03:00")
    return t.strftime("%H:%M") if t else ""


# ---------- одна запись -> плоские поля ----------

def recovery_of(r):
    s = _score(r)
    if not s:
        return None
    rec = s.get("recovery_score")
    return {
        "score": round(rec) if rec is not None else None, "zone": zone(rec),
        "hrv": round(s["hrv_rmssd_milli"], 1) if s.get("hrv_rmssd_milli") is not None else None,
        "rhr": round(s["resting_heart_rate"]) if s.get("resting_heart_rate") is not None else None,
        "spo2": round(s["spo2_percentage"], 1) if s.get("spo2_percentage") is not None else None,
        "skin_temp": round(s["skin_temp_celsius"], 2) if s.get("skin_temp_celsius") is not None else None,
        "calibrating": bool(s.get("user_calibrating")),
    }


def sleep_of(r):
    s = _score(r)
    if not s:
        return None
    st = s.get("stage_summary") or {}
    need = s.get("sleep_needed") or {}
    light, deep, rem = (ms_h(st.get(k)) for k in ("total_light_sleep_time_milli",
                                                  "total_slow_wave_sleep_time_milli", "total_rem_sleep_time_milli"))
    off = r.get("timezone_offset")
    return {
        "hours": round(light + deep + rem, 2), "in_bed": ms_h(st.get("total_in_bed_time_milli")),
        "awake": ms_h(st.get("total_awake_time_milli")), "light": light, "deep": deep, "rem": rem,
        "need": round(sum(ms_h(need.get(k)) for k in ("baseline_milli", "need_from_sleep_debt_milli",
                                                      "need_from_recent_strain_milli", "need_from_recent_nap_milli")), 2),
        "debt": ms_h(need.get("need_from_sleep_debt_milli")),
        "performance": s.get("sleep_performance_percentage"), "efficiency": _r(s.get("sleep_efficiency_percentage")),
        "consistency": s.get("sleep_consistency_percentage"), "resp": _r(s.get("respiratory_rate"), 1),
        "cycles": st.get("sleep_cycle_count"), "disturbances": st.get("disturbance_count"),
        "start": _local_time(r.get("start"), off), "end": _local_time(r.get("end"), off), "nap": bool(r.get("nap")),
    }


def cycle_of(r):
    s = (r or {}).get("score") or {}
    if not s:
        return None
    return {"strain": _r(s.get("strain"), 1), "kcal": round((s.get("kilojoule") or 0) / 4.184),
            "avg_hr": s.get("average_heart_rate"), "max_hr": s.get("max_heart_rate"),
            "done": bool(r.get("end")), "scored": r.get("score_state") == "SCORED"}


# Не тренировка (Глеб 03.11: массаж 89 мин добавил к норме воды почти литр): в норму воды и еды не идут
REST_SPORTS = {"massage-therapy", "meditation", "sauna", "ice-bath", "breathwork", "cleaning"}


def train_minutes(workouts):
    """Минуты настоящих тренировок за день — без массажа, сауны и т. п. (REST_SPORTS)."""
    return sum(w.get("minutes") or 0 for w in workouts or [] if not w.get("rest"))


def workout_of(r):
    s = _score(r)
    t0, t1 = whoop._parse(r.get("start")), whoop._parse(r.get("end"))
    z = s.get("zone_durations") or {}
    names = ("zero", "one", "two", "three", "four", "five")
    return {
        "id": r.get("id"), "sport": sport_name(r.get("sport_name")), "sport_key": r.get("sport_name") or "",
        "start": _local_time(r.get("start"), r.get("timezone_offset")),
        "minutes": round((t1 - t0).total_seconds() / 60) if t0 and t1 else None,
        "strain": _r(s.get("strain"), 1), "kcal": round((s.get("kilojoule") or 0) / 4.184) if s else None,
        "avg_hr": s.get("average_heart_rate"), "max_hr": s.get("max_heart_rate"),
        "zones": [round((z.get(f"zone_{n}_milli") or 0) / 60000) for n in names] if z else [],
        "distance": s.get("distance_meter"), "scored": bool(s),
        "rest": (r.get("sport_name") or "") in REST_SPORTS,
    }


def _r(x, n=0):
    if x is None:
        return None
    return round(x, n) if n else round(x)


# ---------- по дням ----------

def by_day(db, uid, since, until):
    """{день: {recovery, sleep, naps, cycle, workouts}} за период."""
    days = {}

    def slot(d):
        return days.setdefault(d, {"recovery": None, "sleep": None, "naps": [], "cycle": None, "workouts": []})
    for r in db.records(uid, "recovery", since, until):
        v = recovery_of(r)
        if v:
            slot(r["_day"])["recovery"] = v
    for r in db.records(uid, "sleep", since, until):
        v = sleep_of(r)
        if not v:
            continue
        s = slot(r["_day"])
        if v["nap"]:
            s["naps"].append(v)
        elif not s["sleep"] or v["hours"] > s["sleep"]["hours"]:
            s["sleep"] = v
    for r in db.records(uid, "cycle", since, until):
        v = cycle_of(r)
        if v:
            slot(r["_day"])["cycle"] = v
    for r in db.records(uid, "workout", since, until):
        slot(r["_day"])["workouts"].append(workout_of(r))
    return days


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.mean(xs), 2) if xs else None


def baseline(days, before, n=30):
    """Обычный уровень за n дней до дня before (сам день не входит)."""
    lo = (before - datetime.timedelta(days=n)).isoformat()
    sel = [v for d, v in days.items() if lo <= d < before.isoformat()]
    rec = [v["recovery"] for v in sel if v["recovery"]]
    slp = [v["sleep"] for v in sel if v["sleep"]]
    cyc = [v["cycle"] for v in sel if v["cycle"] and v["cycle"]["done"]]
    return {
        "days": len(sel), "hrv": _mean(r["hrv"] for r in rec), "rhr": _mean(r["rhr"] for r in rec),
        "recovery": _mean(r["score"] for r in rec), "skin_temp": _mean(r["skin_temp"] for r in rec),
        "sleep": _mean(s["hours"] for s in slp), "resp": _mean(s["resp"] for s in slp),
        "strain": _mean(c["strain"] for c in cyc), "kcal": _mean(c["kcal"] for c in cyc if c["kcal"]),
    }


def load_ratio(days, day):
    """Средняя нагрузка за 7 дней к средней за 28 (резкий рост > 1,3 — риск перетренированности и травм)."""
    def avg(n):
        lo = (day - datetime.timedelta(days=n - 1)).isoformat()
        return _mean(v["cycle"]["strain"] for d, v in days.items() if lo <= d <= day.isoformat() and v["cycle"])
    a, c = avg(7), avg(28)
    return round(a / c, 2) if a and c else None


# ---------- питание ----------

def weight_of(profile, body):
    """Свежий замер умных весов (body["scale"], f_weight) — раньше веса из профиля и WHOOP: те введены руками без даты."""
    scale = body.get("scale") or {}
    return scale.get("kg") or profile.get("weight") or body.get("weight_kilogram")


def nutrition(profile, body, base, today_kcal=None, train_minutes=0):
    """Калории и БЖУ на день. Траты — средние по WHOOP за прошлые дни (в них уже есть обмен веществ),
    без данных — формула Миффлина — Сан Жеора × 1,6. Подросткам не урезаем калории сильнее 5 %."""
    w = weight_of(profile, body)
    if not w:
        return None
    age, h = profile.get("age"), profile.get("height") or ((body.get("height_meter") or 0) * 100) or None
    spend = base.get("kcal") if base else None
    how = "по тратам WHOOP за прошлые дни"
    if not spend:
        if not (age and h):
            spend = 33 * w
            how = "примерно, по весу (добавь рост и возраст)"
        else:
            bmr = 10 * w + 6.25 * h - 5 * age + (-161 if profile.get("sex") == "f" else 5)
            spend = bmr * 1.6
            how = "по формуле, пока мало данных WHOOP"
    if today_kcal and today_kcal > spend:
        spend = today_kcal  # сегодня уже потратил больше обычного
        how = "по сегодняшним тратам WHOOP"
    goal = goal_code(profile.get("goal"))
    teen = bool(age and age < 18)
    # подросток растёт: траты WHOOP часто занижены (браслет не всё время на руке, вес в профиле WHOOP мог быть меньше),
    # поэтому не ниже формулы Миффлина — Сан Жеора × 1,6 + ~6 ккал за минуту тренировки (Глеб 03.11: Форма показывала 1960)
    if teen and h:
        floor = (10 * w + 6.25 * h - 5 * age + (-161 if profile.get("sex") == "f" else 5)) * 1.6 + train_minutes * 6
        if floor > spend:
            spend, how = floor, "по росту, весу и возрасту (траты WHOOP ниже — для роста мало)"
    k = {"form": 1.0, "weight": 1.0, "mass": 1.1, "dry": 0.95 if teen else 0.88}[goal]
    kcal = round(spend * k / 10) * 10
    protein = round(w * {"form": 1.6, "weight": 1.7, "mass": 1.8, "dry": 1.8 if teen else 2.0}[goal])
    fat = round(max(w * 0.9, kcal * 0.27 / 9))
    carbs = max(0, round((kcal - protein * 4 - fat * 9) / 4))
    # вода (Глеб 02.11: «точно ли мне 3 л?»): подростку не меньше нормы напитков по возрасту (IOM: юноши 14–18 — 2,6 л,
    # девушки — 2,1 л), взрослому — 35 мл на кг; тренировка — +10 мл в минуту (~0,6 л пота в час)
    floor = (2100 if profile.get("sex") == "f" else 2600) if teen else 0
    water = round((max(w * 35, floor) + train_minutes * 10) / 50) * 50
    return {"kcal": kcal, "protein": protein, "fat": fat, "carbs": carbs, "water": water, "spend": round(spend),
            "how": how, "goal": goal, "teen": teen,
            "weight": round(float(w), 1), "weight_day": (body.get("scale") or {}).get("day") if (body.get("scale") or {}).get("kg") else None}


def eaten(meals, kcal=None):
    """Съедено за день: еда Формы + еда «Калорий» (kcal — поле дня t.kcal или None); соль — тоже из обеих (Глеб 03.11)."""
    t = {k: round(sum(m.get(k) or 0 for m in meals)) for k in ("kcal", "protein", "fat", "carbs")}
    t["count"] = len(meals)
    t["salt"] = round(sum(float(m.get("salt") or 0) for m in meals), 1)
    if kcal:
        for k in ("kcal", "protein", "fat", "carbs"):
            t[k] = round(t[k] + kcal["eaten"][k])
        t["salt"] = round(t["salt"] + float(kcal["eaten"]["salt"] or 0), 1)
        t["count"] += len(kcal["items"])
    return t


KCAL_KEYS = (("kcal", "kcal"), ("protein", "p"), ("fat", "f"), ("carbs", "c"), ("salt", "salt"))


def kcal_goals_for(goals, kind):
    """Цели «Калорий» на тип дня (train/rest) → {kcal, protein, fat, carbs, salt, water} или None."""
    g = ((goals or {}).get("goals") or {}).get(kind)
    if not g:
        return None
    out = {k: g.get(src) for k, src in KCAL_KEYS if k != "salt"}
    out["salt"] = (goals or {}).get("salt")
    out["water"] = ((goals or {}).get("water") or {}).get(kind)
    return out


def kcal_of(data, goals, trained=False):
    """День «Калорий» (store.kcal_day) → поле дня t.kcal (договор coach/SYNC.md, п. 3) или None."""
    if not data:
        return None
    items = [dict(x) for x in data.get("items") or [] if isinstance(x, dict)]
    sums = {k: round(sum(x.get(src) or 0 for x in items), 1) for k, src in KCAL_KEYS}
    for k in ("kcal", "protein", "fat", "carbs"):
        sums[k] = round(sums[k])
    kind = data.get("type") if data.get("type") in ("train", "rest") else ("train" if trained else "rest")
    return {"items": items, "eaten": sums, "water": int(sum(data.get("water") or [])),
            "goals": kcal_goals_for(goals, kind), "type": kind, "updated": data.get("updated")}


# ---------- выводы ----------

def tips(day, base, ratio, profile, recent):
    """Короткие выводы по правилам: [{tone, text}]. tone: good / warn / bad."""
    out = []
    rec, slp = day.get("recovery"), day.get("sleep")
    age = profile.get("age")
    if rec and rec.get("calibrating"):
        out.append({"tone": "warn", "text": "WHOOP ещё калибруется: первые недели цифры восстановления неточные."})
    if rec and base.get("hrv") and rec.get("hrv"):
        d = (rec["hrv"] - base["hrv"]) / base["hrv"] * 100
        if d <= -15:
            out.append({"tone": "bad", "text": f"Гибкость пульса на {abs(round(d))}% ниже твоей нормы — нервная система не восстановилась. "
                                               "Сегодня техника и лёгкая работа, без спаррингов на максимум."})
        elif d >= 10:
            out.append({"tone": "good", "text": f"Гибкость пульса на {round(d)}% выше нормы — организм хорошо восстановился."})
    if rec and base.get("rhr") and rec.get("rhr") and rec["rhr"] - base["rhr"] >= 5:
        out.append({"tone": "bad", "text": f"Пульс покоя {rec['rhr']} — на {round(rec['rhr'] - base['rhr'])} выше обычного. "
                                           "Так бывает при начале болезни, недосыпе, обезвоживании или перегрузке."})
    if rec and base.get("skin_temp") and rec.get("skin_temp") and rec["skin_temp"] - base["skin_temp"] >= 0.6:
        out.append({"tone": "bad", "text": "Температура кожи выше обычной — следи за самочувствием, при недомогании отдыхай."})
    if slp and base.get("resp") and slp.get("resp") and slp["resp"] - base["resp"] >= 1:
        out.append({"tone": "warn", "text": "Частота дыхания во сне выше нормы — ранний признак простуды или перегрузки."})
    need_h = 8.5 if age and age < 18 else 7.5
    if slp:
        if slp["hours"] < need_h - 1:
            out.append({"tone": "bad" if slp["hours"] < 6.5 else "warn",
                        "text": f"Сон {fmt_h(slp['hours'])} — мало. "
                                + ("В твоём возрасте нужно 8–10 часов: на сне растут мышцы и скорость реакции."
                                   if age and age < 18 else "Цель — 7,5–9 часов.")})
        if slp.get("consistency") is not None and slp["consistency"] < 70:
            out.append({"tone": "warn", "text": f"Режим сна {slp['consistency']}% — ложись и вставай в одно время, "
                                                "это быстрее всего поднимает восстановление."})
    if ratio and ratio > 1.3:
        out.append({"tone": "warn", "text": f"Нагрузка за неделю в {str(ratio).replace('.', ',')} раза выше обычной — "
                                            "выше риск травм. Добавь лёгкий день."})
    reds = sum(1 for v in recent if v.get("recovery") and v["recovery"]["zone"] == "red")
    if reds >= 3:
        out.append({"tone": "bad", "text": f"{reds} красных дня за неделю — нужен разгрузочный день и больше сна."})
    if not out and rec and rec["zone"] == "green":
        out.append({"tone": "good", "text": "Всё в норме — можно тренироваться в полную силу."})
    return out


def fmt_h(h):
    if h is None:
        return "—"
    m = round(h * 60)
    return f"{m // 60} ч {m % 60:02d} мин"


def strain_band(rec):
    z = (rec or {}).get("zone")
    return STRAIN_BAND.get(z) if z else None


# ---------- отбой ----------

def _mins(hhmm):
    try:
        h, m = (int(x) for x in hhmm.split(":"))
        return h * 60 + m
    except (AttributeError, ValueError):
        return None


def _hhmm(mins):
    mins %= 24 * 60
    return f"{mins // 60:02d}:{mins % 60:02d}"


def sleep_eff(days, day):
    """Доля чистого сна от времени в кровати (Глеб 04.11: «время в кровати — не то же самое, что чистый сон»):
    медиана эффективности сна WHOOP за 14 ночей, 0,80–0,98; данных нет — 0,9."""
    lo = (day - datetime.timedelta(days=13)).isoformat()
    xs = [v["sleep"]["efficiency"] for d, v in days.items()
          if lo <= d <= day.isoformat() and v.get("sleep") and v["sleep"].get("efficiency")]
    return round(min(0.98, max(0.80, statistics.median(xs) / 100)), 3) if xs else 0.9


def in_bed_min(net_hours, eff):
    """Сколько минут лежать в кровати, чтобы чистого сна было net_hours: сон / эффективность (без засыпания)."""
    return round(net_hours * 60 / (eff or 0.9))


def bedtime(days, day, profile):
    """Во сколько лечь сегодня: обычный подъём (медиана за 14 дней; будни и выходные отдельно) минус нужный сон
    и 20 минут на засыпание. Нужный сон — из WHOOP за прошлую ночь, но не меньше 8,5 ч до 18 лет и 7,5 ч взрослым."""
    tomorrow = day + datetime.timedelta(days=1)
    lo = (day - datetime.timedelta(days=13)).isoformat()
    ends = [(d, _mins(v["sleep"]["end"])) for d, v in days.items()
            if lo <= d <= day.isoformat() and v.get("sleep") and _mins(v["sleep"].get("end")) is not None]
    ends = [(d, m) for d, m in ends if 3 * 60 <= m <= 13 * 60]  # подъём утром, а не дневной сон
    same = [m for d, m in ends if (datetime.date.fromisoformat(d).weekday() >= 5) == (tomorrow.weekday() >= 5)]
    pick = same if len(same) >= 2 else [m for _, m in ends]
    if not pick:
        return None
    wake = round(statistics.median(pick))
    age = profile.get("age")
    floor = 8.5 if age and age < 18 else 7.5
    last = (days.get(day.isoformat()) or {}).get("sleep") or {}
    need = max(last.get("need") or 0, floor)
    eff = sleep_eff(days, day)
    bed = wake - in_bed_min(need, eff) - 20  # нужен ЧИСТЫЙ сон: в кровати дольше на просыпания
    bed -= bed % 10  # округляем вниз до 10 минут
    return {"bed": _hhmm(bed), "wake": _hhmm(wake), "need": round(need, 2), "weekend": tomorrow.weekday() >= 5, "eff": eff}


# ---------- всё про день ----------

def today(db, uid, day, profile, body):
    since = day - datetime.timedelta(days=45)
    days = by_day(db, uid, since.isoformat(), day.isoformat())
    d = days.get(day.isoformat()) or {"recovery": None, "sleep": None, "naps": [], "cycle": None, "workouts": []}
    base = baseline(days, day)
    ratio = load_ratio(days, day)
    recent = [days[x] for x in sorted(days) if x > (day - datetime.timedelta(days=7)).isoformat()]
    train_min = train_minutes(d["workouts"])
    food = db.meals(uid, day.isoformat())
    nut = nutrition(profile, body, base, (d["cycle"] or {}).get("kcal"), train_min)
    y = days.get((day - datetime.timedelta(days=1)).isoformat()) or {}
    target, why = water.norm(nut, train_min)
    yday = (day - datetime.timedelta(days=1)).isoformat()
    y_train = train_minutes(y.get("workouts"))
    y_target, _ = water.norm(nutrition(profile, body, base, None, y_train), y_train)
    drinks = db.water(uid, day.isoformat())
    kd = db.kcal_day(uid, day.isoformat())
    kc = kcal_of(kd, db.kcal_goals(uid), bool(train_min))
    # вода «Калорий» — в общий журнал с пометкой src: удаляется только на сайте «Калории»
    log = drinks + [{"id": None, "time": "", "ml": int(ml), "src": "kcal"} for ml in (kd or {}).get("water") or [] if ml]
    return {
        "day": day.isoformat(), **d, "base": base, "ratio": ratio, "band": strain_band(d["recovery"]),
        "tips": tips(d, base, ratio, profile, recent), "nutrition": nut, "eaten": eaten(food, kc), "meals": food,
        "kcal": kc,
        "yesterday": {"strain": (y.get("cycle") or {}).get("strain"), "workouts": y.get("workouts") or []},
        "bedtime": bedtime(days, day, profile), "sleep_eff": sleep_eff(days, day), "has_data": bool(days),
        "water": {"drank": sum(w["ml"] for w in log), "target": target, "why": why, "log": log,
                  "trained": bool(train_min), "yesterday": db.water_total(uid, yday), "y_target": y_target},
    }


STREAK_MAX = 3650  # якорь серии из приложения WHOOP: 0–3650 дней


def wear_days(db, uid, day):
    """Дни (ISO) до day включительно, когда браслет носили: день покрыт циклом WHOOP. Цикл идёт от пробуждения
    до пробуждения и бывает длиннее суток — покрыты все календарные дни от его начала до конца по местному
    времени цикла (timezone_offset); цикл без конца (идёт сейчас) — до day. Так серия совпадает со «стриком»
    в приложении WHOOP (сверено на живых данных 03.11: ~726 дней)."""
    out = set()
    for r in db.q("SELECT data FROM records WHERE user=? AND kind='cycle'", (uid,)):
        try:
            c = json.loads(r["data"])
            off = c.get("timezone_offset") or "+03:00"
            t0 = whoop._local(c.get("start"), off)
            t1 = whoop._local(c.get("end"), off) if c.get("end") else None
        except (ValueError, TypeError, AttributeError):
            continue
        if not t0:
            continue
        d, last = t0.date(), min(t1.date(), day) if t1 else day
        while d <= last:
            out.add(d.isoformat())
            d += datetime.timedelta(days=1)
    return out


def streak_anchor(db, uid):
    """Якорь серии {day, n} — число «стрик» из самого приложения WHOOP (в API v2 его нет), или None."""
    try:
        a = json.loads(db.meta(f"streak:anchor:{uid}") or "null")
        datetime.date.fromisoformat(a["day"])
        n = int(a["n"])
    except (ValueError, TypeError, KeyError):
        return None
    return {"day": a["day"], "n": n} if 0 <= n <= STREAK_MAX else None


def set_streak_anchor(db, uid, day, n):
    """Запомнить «стрик» из WHOOP на день day (n 0–3650, иначе ValueError) -> новая серия (wear_streak)."""
    try:
        n = None if isinstance(n, (bool, float)) else int(n)
    except (TypeError, ValueError):
        n = None
    if n is None or not 0 <= n <= STREAK_MAX:
        raise ValueError("серия — число дней от 0 до 3650")
    db.meta(f"streak:anchor:{uid}", json.dumps({"day": day.isoformat(), "n": n}))
    return wear_streak(db, uid, day)


def wear_streak(db, uid, day):
    """Сколько дней подряд браслет на руке (день покрыт циклом — wear_days) по всей истории, как «Streak» в WHOOP.
    Сегодня WHOOP мог ещё не прислать — серия не рвётся (и не прибавляется).
    Якорь (streak_anchor: число из приложения WHOOP на его день) — если после него разрывов не было, серия =
    его число + засчитанные дни после него; был разрыв — считаем по правилу с конца.
    Глеб 02.11: «стрик не актуальный» (раньше считался по 30 дням); 03.11: по дню начала цикла выходило ~395 дней —
    длинный цикл (больше суток) оставлял день без «своего» цикла, хотя браслет носили.
    -> {n, since, anchored}."""
    days = wear_days(db, uid, day)
    a = streak_anchor(db, uid)
    if a and a["day"] <= day.isoformat():
        ad = datetime.date.fromisoformat(a["day"])
        after = [ad + datetime.timedelta(days=i) for i in range(1, (day - ad).days + 1)]
        if all(d.isoformat() in days for d in after if d != day):  # без разрывов (сегодня может быть ещё пусто)
            got = [d for d in after if d.isoformat() in days]
            n = a["n"] + len(got)
            start = ad  # начало серии — первый день подряд покрытых (как «Streak started» в WHOOP), не n дней назад
            while (start - datetime.timedelta(days=1)).isoformat() in days:
                start -= datetime.timedelta(days=1)
            by_n = (got[-1] if got else ad) - datetime.timedelta(days=n - 1) if n else ad
            # раньше из двух: у WHOOP серия могла начаться до первых наших данных — тогда счёт по n
            return {"n": n, "since": min(start, by_n).isoformat() if n else None, "anchored": True}
    d = day if day.isoformat() in days else day - datetime.timedelta(days=1)
    n = 0
    while d.isoformat() in days:
        n += 1
        d -= datetime.timedelta(days=1)
    return {"n": n, "since": (d + datetime.timedelta(days=1)).isoformat() if n else None, "anchored": False}


def series(db, uid, day, n):
    since = day - datetime.timedelta(days=n - 1)
    days = by_day(db, uid, since.isoformat(), day.isoformat())
    out = []
    for i in range(n):
        d = (since + datetime.timedelta(days=i)).isoformat()
        v = days.get(d) or {}
        rec, slp, cyc = v.get("recovery") or {}, v.get("sleep") or {}, v.get("cycle") or {}
        out.append({"day": d, "recovery": rec.get("score"), "zone": rec.get("zone"), "hrv": rec.get("hrv"),
                    "rhr": rec.get("rhr"), "sleep": slp.get("hours"), "need": slp.get("need"),
                    "perf": slp.get("performance"), "strain": cyc.get("strain"), "kcal": cyc.get("kcal"),
                    "workouts": len(v.get("workouts") or [])})
    return out


def text_context(db, uid, day, profile, body, n=14):
    """Сжатый текст для Claude: профиль, сегодня, обычный уровень, последние n дней, тренировки, питание."""
    t = today(db, uid, day, profile, body)
    lines = ["ПРОФИЛЬ: " + profile_line(profile, body)]
    goals = db.goals(uid)
    if goals:
        lines.append("ЦЕЛИ (написал сам; советы привязывай к ним, но безопасность важнее): "
                     + "; ".join(f"{i}) {g['text']} (с {g['created'][:10]})" for i, g in enumerate(goals, 1)))
    lines.append(f"СЕГОДНЯ {day.isoformat()} ({['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс'][day.weekday()]}):")
    r, s, c = t["recovery"], t["sleep"], t["cycle"]
    lines.append("  восстановление: " + (f"{r['score']}% ({r['zone']}), ВСР {r['hrv']} мс, пульс покоя {r['rhr']}, "
                                         f"SpO2 {r['spo2']}, t кожи {r['skin_temp']}" + (" [калибровка]" if r["calibrating"] else "")
                                         if r else "ещё нет"))
    lines.append("  сон: " + (f"{fmt_h(s['hours'])} из нужных {fmt_h(s['need'])}, {s['start']}–{s['end']}, "
                              f"эффективность {s['efficiency']}%, режим {s['consistency']}%, глубокий {fmt_h(s['deep'])}, "
                              f"REM {fmt_h(s['rem'])}, дыхание {s['resp']}" if s else "ещё нет"))
    if c:
        lines.append(f"  нагрузка дня пока: {c['strain']}, траты {c['kcal']} ккал")
    for w in t["workouts"]:
        lines.append(f"  тренировка: {w['sport']} {w['start']}, {w['minutes']} мин, нагрузка {w['strain']}, "
                     f"пульс ср/макс {w['avg_hr']}/{w['max_hr']}, зоны (мин) {w['zones']}")
    b = {k: ("—" if v is None else v) for k, v in t["base"].items()}
    lines.append(f"ОБЫЧНО (30 дн, дней с данными: {b['days']}): ВСР {b['hrv']}, пульс покоя {b['rhr']}, "
                 f"восстановление {b['recovery']}, сон {fmt_h(t['base']['sleep'])}, нагрузка {b['strain']}, "
                 f"траты {b['kcal']} ккал; нагрузка 7/28 = {t['ratio'] or '—'}")
    if t["band"]:
        lines.append(f"ОРИЕНТИР НАГРУЗКИ НА СЕГОДНЯ (наш): {t['band'][0]}–{t['band'][1]}")
    if t["bedtime"]:
        bt = t["bedtime"]
        lines.append(f"ОТБОЙ СЕГОДНЯ (наш расчёт): лечь до {bt['bed']} — подъём обычно {bt['wake']}"
                     f"{' (выходной)' if bt['weekend'] else ''}, нужно сна {fmt_h(bt['need'])}")
    if t["nutrition"]:
        n_ = t["nutrition"]
        lines.append(f"ПИТАНИЕ НА ДЕНЬ (наш расчёт, {n_['how']}): {n_['kcal']} ккал, Б {n_['protein']} г, "
                     f"Ж {n_['fat']} г, У {n_['carbs']} г, вода {n_['water']} мл")
    wt = t["water"]
    lines.append(f"ВОДА СЕГОДНЯ (записывает сам): выпито {water.day_line(wt['drank'], wt['target'])}"
                 + (f"; вчера {water.day_line(wt['yesterday'], wt['y_target'])}" if wt["yesterday"] else "; вчера не записывал"))
    e = t["eaten"]
    if e["count"]:
        lines.append(f"СЪЕДЕНО СЕГОДНЯ (Форма + «Калории»): {e['kcal']} ккал, Б {e['protein']}, Ж {e['fat']}, У {e['carbs']}"
                     + (" — " + "; ".join(f"{m['time']} {m['title']}" for m in t["meals"]) if t["meals"] else ""))
    kl = kcal_line(t.get("kcal"))
    if kl:
        lines.append(kl)
    lines.append(f"ПОСЛЕДНИЕ {n} ДНЕЙ (день: восст% / ВСР / пульс покоя / сон ч / нагрузка / тренировки):")
    days = by_day(db, uid, (day - datetime.timedelta(days=n)).isoformat(), (day - datetime.timedelta(days=1)).isoformat())
    for d in sorted(days):
        v = days[d]
        r, s, c = v["recovery"] or {}, v["sleep"] or {}, v["cycle"] or {}
        ws = ", ".join(f"{w['sport']} {w['minutes']}м/{w['strain']}" for w in v["workouts"])
        lines.append(f"  {d}: {r.get('score', '—')} / {r.get('hrv', '—')} / {r.get('rhr', '—')} / "
                     f"{s.get('hours', '—')} / {c.get('strain', '—')} / {ws or '—'}")
    if t["tips"]:
        lines.append("ВЫВОДЫ ПО ПРАВИЛАМ: " + " | ".join(x["text"] for x in t["tips"]))
    return "\n".join(lines), t


def kcal_line(k):
    """Строка для Claude: еда, соль и вода из «Калорий» (ведёт сам на сайте «Калории»)."""
    if not k:
        return ""
    e, g = k["eaten"], k.get("goals") or {}

    def of(v, key, unit=""):
        return f"{v}{unit}" + (f" из {g[key]}{unit}" if g.get(key) else "")
    names = "; ".join(str(x.get("name") or "")[:40] for x in k["items"][:12])
    return (f"«КАЛОРИИ» СЕГОДНЯ (день {'тренировки' if k['type'] == 'train' else 'отдыха'}): {of(e['kcal'], 'kcal', ' ккал')}, "
            f"Б {of(e['protein'], 'protein')}, Ж {of(e['fat'], 'fat')}, У {of(e['carbs'], 'carbs')} г, "
            f"соль {of(str(e['salt']).replace('.', ','), 'salt', ' г')}, вода {of(k['water'], 'water', ' мл')}"
            + (f" — {names}" if names else ""))


def _bed_hour(s):
    """Время отхода ко сну в часах от полудня (23:30 -> 11.5, 01:00 -> 13), чтобы считать средние через полночь."""
    if not s or not s.get("start"):
        return None
    h, m = (int(x) for x in s["start"].split(":"))
    return (h - 12) % 24 + m / 60


def _clock(x):
    if x is None:
        return "—"
    m = round(x * 60) % (24 * 60)
    return f"{(m // 60 + 12) % 24:02d}:{m % 60:02d}"


def progress(db, uid, day, days=60):
    """Сводка прогресса за days дней: по неделям, первая половина против второй, сон, спорт, связи."""
    since = day - datetime.timedelta(days=days - 1)
    data = by_day(db, uid, since.isoformat(), day.isoformat())
    weeks = {}
    for d in sorted(data):
        dt = datetime.date.fromisoformat(d)
        weeks.setdefault((dt - datetime.timedelta(days=dt.weekday())).isoformat(), []).append(data[d])
    rows = []
    for w, vs in sorted(weeks.items()):
        rec = [v["recovery"] for v in vs if v["recovery"]]
        slp = [v["sleep"] for v in vs if v["sleep"]]
        wk = [x for v in vs for x in v["workouts"]]
        rows.append({
            "week": w, "days": len(rec), "recovery": _mean(r["score"] for r in rec),
            "green": sum(r["zone"] == "green" for r in rec), "red": sum(r["zone"] == "red" for r in rec),
            "hrv": _mean(r["hrv"] for r in rec), "rhr": _mean(r["rhr"] for r in rec),
            "sleep": _mean(s["hours"] for s in slp), "need": _mean(s["need"] for s in slp if s.get("need")),
            "perf": _mean(s["performance"] for s in slp), "bed": _mean(_bed_hour(s) for s in slp), "strain": _mean(v["cycle"]["strain"] for v in vs if v["cycle"]),
            "workouts": len(wk), "minutes": sum(x["minutes"] or 0 for x in wk),
        })
    keys = sorted(data)
    half = len(keys) // 2

    def part(ks):
        vs = [data[k] for k in ks]
        rec = [v["recovery"] for v in vs if v["recovery"]]
        slp = [v["sleep"] for v in vs if v["sleep"]]
        return {"recovery": _mean(r["score"] for r in rec), "hrv": _mean(r["hrv"] for r in rec),
                "rhr": _mean(r["rhr"] for r in rec), "sleep": _mean(s["hours"] for s in slp),
                "strain": _mean(v["cycle"]["strain"] for v in vs if v["cycle"])}
    sports = {}
    for v in data.values():
        for x in v["workouts"]:
            s = sports.setdefault(x["sport"], {"count": 0, "minutes": 0, "strain": []})
            s["count"] += 1
            s["minutes"] += x["minutes"] or 0
            s["strain"].append(x["strain"])
    sports = {k: {"count": s["count"], "minutes": s["minutes"], "strain": _mean(s["strain"])}
              for k, s in sorted(sports.items(), key=lambda kv: -kv[1]["minutes"])}
    # связи: восстановление после длинного и короткого сна, после тяжёлого и лёгкого дня
    pairs = []
    for k in keys:
        prev = (datetime.date.fromisoformat(k) - datetime.timedelta(days=1)).isoformat()
        if data[k]["recovery"] and prev in data:
            pairs.append((data[k], data[prev]))
    after = {
        "sleep_8plus": _mean(v["recovery"]["score"] for v, _ in pairs if v["sleep"] and v["sleep"]["hours"] >= 8),
        "sleep_under7": _mean(v["recovery"]["score"] for v, _ in pairs if v["sleep"] and v["sleep"]["hours"] < 7),
        "strain_14plus": _mean(v["recovery"]["score"] for v, p in pairs if p["cycle"] and (p["cycle"]["strain"] or 0) >= 14),
        "strain_under10": _mean(v["recovery"]["score"] for v, p in pairs if p["cycle"] and (p["cycle"]["strain"] or 0) < 10),
    }
    beds = [b for b in (_bed_hour(v["sleep"]) for v in data.values()) if b is not None]
    gaps = sum(1 for i in range(days) if not (data.get((since + datetime.timedelta(days=i)).isoformat()) or {}).get("recovery"))
    return {"weeks": rows, "first": part(keys[:half]), "second": part(keys[half:]), "sports": sports, "after": after,
            "bed_mean": _mean(beds), "bed_spread": round(statistics.pstdev(beds), 2) if len(beds) > 2 else None,
            "gaps": gaps, "days": days, "since": since.isoformat()}


def progress_context(db, uid, day, profile, body, days=60):
    p = progress(db, uid, day, days)
    lines = ["ПРОФИЛЬ: " + profile_line(profile, body),
             f"ПЕРИОД: {p['since']} — {day.isoformat()} ({days} дн), дней без данных восстановления: {p['gaps']}",
             "ПО НЕДЕЛЯМ (неделя с: дней / восст% / зел-красн / ВСР / пульс покоя / сон ч / нужно сна ч / сон % / отбой / "
             "нагрузка / тренировок, мин):"]
    for w in p["weeks"]:
        lines.append(f"  {w['week']}: {w['days']} / {w['recovery']} / {w['green']}-{w['red']} / {w['hrv']} / {w['rhr']} / "
                     f"{w['sleep']} / {w.get('need') or '—'} / {w['perf']} / {_clock(w['bed'])} / {w['strain']} / "
                     f"{w['workouts']}, {w['minutes']}")
    f, s = p["first"], p["second"]
    lines.append(f"ПЕРВАЯ ПОЛОВИНА → ВТОРАЯ: восст {f['recovery']} → {s['recovery']}, ВСР {f['hrv']} → {s['hrv']}, "
                 f"пульс покоя {f['rhr']} → {s['rhr']}, сон {f['sleep']} → {s['sleep']} ч, нагрузка {f['strain']} → {s['strain']}")
    lines.append(f"ОТБОЙ: в среднем {_clock(p['bed_mean'])}, разброс ±{p['bed_spread']} ч")
    a = p["after"]
    lines.append(f"ВОССТАНОВЛЕНИЕ ПОСЛЕ: сна ≥8 ч — {a['sleep_8plus']}%, сна <7 ч — {a['sleep_under7']}%; "
                 f"после дня с нагрузкой ≥14 — {a['strain_14plus']}%, <10 — {a['strain_under10']}%")
    lines.append("СПОРТ ЗА ПЕРИОД: " + "; ".join(f"{k}: {v['count']} раз, {v['minutes']} мин, ср. нагрузка {v['strain']}"
                                                for k, v in p["sports"].items()))
    return "\n".join(lines), p


def food_period(db, uid, day, n=14):
    """Еда и вода за n дней до вчера (Форма + «Калории») одной строкой для разбора прогресса: сколько дней записывал,
    в среднем ккал и белок по дням с записями, вода в среднем по дням с записями. Нет записей — пусто."""
    since, until = (day - datetime.timedelta(days=n)).isoformat(), (day - datetime.timedelta(days=1)).isoformat()
    per = {}
    for m in db.meals(uid, since, until):
        d = per.setdefault(m["day"], {"kcal": 0, "protein": 0})
        d["kcal"] += m.get("kcal") or 0
        d["protein"] += m.get("protein") or 0
    for d, data in db.kcal_days(uid, since, until).items():
        items = [x for x in data.get("items") or [] if isinstance(x, dict)]
        if items:
            x = per.setdefault(d, {"kcal": 0, "protein": 0})
            x["kcal"] += sum(i.get("kcal") or 0 for i in items)
            x["protein"] += sum(i.get("p") or 0 for i in items)
    water_ = {d: ml for d, ml in db.water_days(uid, since, until).items() if ml}
    parts = []
    if per:
        parts.append(f"еду записывал {len(per)} из {n} дней, в эти дни в среднем {round(_mean(v['kcal'] for v in per.values()))} ккал, "
                     f"белок {round(_mean(v['protein'] for v in per.values()))} г (записи могут быть неполными)")
    if water_:
        parts.append(f"воду записывал {len(water_)} из {n} дней, в среднем {round(_mean(water_.values()))} мл в день")
    return f"ЕДА И ВОДА ЗА {n} ДНЕЙ (записывает сам): " + "; ".join(parts) if parts else ""


# ---------- сводка за период ----------

PERIODS = {"day": "День", "week": "Неделя", "month": "Месяц", "all": "Всё время"}


def period_stats(data, since, until):
    """Средние и итоги за дни since..until (включительно) из by_day()."""
    vs = [v for d, v in data.items() if since <= d <= until]
    rec = [v["recovery"] for v in vs if v["recovery"]]
    slp = [v["sleep"] for v in vs if v["sleep"]]
    cyc = [v["cycle"] for v in vs if v["cycle"]]
    wk = [x for v in vs for x in v["workouts"]]
    beds = [b for b in (_bed_hour(x) for x in slp) if b is not None]
    sports = {}
    for x in wk:
        sports.setdefault(x["sport"], [0, 0])
        sports[x["sport"]][0] += 1
        sports[x["sport"]][1] += x["minutes"] or 0
    ranked = sorted(((d, v["recovery"]["score"]) for d, v in data.items() if since <= d <= until and v["recovery"]),
                    key=lambda x: x[1])
    return {
        "days": len(rec), "recovery": _mean(r["score"] for r in rec),
        "green": sum(r["zone"] == "green" for r in rec), "yellow": sum(r["zone"] == "yellow" for r in rec),
        "red": sum(r["zone"] == "red" for r in rec),
        "hrv": _mean(r["hrv"] for r in rec), "rhr": _mean(r["rhr"] for r in rec),
        "sleep": _mean(x["hours"] for x in slp), "need": _mean(x["need"] for x in slp),
        "perf": _mean(x["performance"] for x in slp),
        "bed": _mean(beds), "bed_spread": round(statistics.pstdev(beds), 2) if len(beds) > 2 else None,
        "strain": _mean(c["strain"] for c in cyc), "kcal": _mean(c["kcal"] for c in cyc if c["kcal"]),
        "workouts": len(wk), "minutes": sum(x["minutes"] or 0 for x in wk),
        "sports": [{"sport": k, "count": c, "minutes": m} for k, (c, m) in
                   sorted(sports.items(), key=lambda kv: -kv[1][1])[:4]],
        "best": {"day": ranked[-1][0], "recovery": ranked[-1][1]} if ranked else None,
        "worst": {"day": ranked[0][0], "recovery": ranked[0][1]} if ranked else None,
    }


def summary(db, uid, day, period, first=None):
    """Сводка за день / неделю / месяц / всё время и с чем сравнить:
    день — с обычным уровнем за 30 дней до него, неделя и месяц — с прошлой неделей / месяцем,
    всё время — первый месяц с браслетом против последнего."""
    if period not in PERIODS:
        raise ValueError("нет такого периода")
    iso = day.isoformat()
    first = first or db.first_day(uid) or iso
    if period == "day":
        since = iso
    elif period == "week":
        since = (day - datetime.timedelta(days=6)).isoformat()
    elif period == "month":
        since = (day - datetime.timedelta(days=29)).isoformat()
    else:
        since = first
    n = (day - datetime.date.fromisoformat(since)).days + 1
    lo = min(since, (datetime.date.fromisoformat(since) - datetime.timedelta(days=max(n, 30))).isoformat())
    data = by_day(db, uid, lo, iso)
    cur = period_stats(data, since, iso)
    if period == "day":
        prev, prev_label = baseline(data, day), "обычно (30 дней)"
    elif period == "all":
        f = datetime.date.fromisoformat(first)
        end_first = min(f + datetime.timedelta(days=29), day).isoformat()
        prev, prev_label = period_stats(data, first, end_first), "первый месяц с WHOOP"
        cur = dict(cur, last=period_stats(data, (day - datetime.timedelta(days=29)).isoformat(), iso))
    else:
        p_until = (datetime.date.fromisoformat(since) - datetime.timedelta(days=1))
        p_since = (p_until - datetime.timedelta(days=n - 1)).isoformat()
        prev = period_stats(data, p_since, p_until.isoformat())
        prev_label = "прошлая неделя" if period == "week" else "прошлый месяц"
    one = data.get(iso) or {}
    return {"period": period, "title": PERIODS[period], "since": since, "until": iso, "span": n, "first": first,
            "stats": cur, "prev": prev, "prev_label": prev_label,
            "day": {"recovery": one.get("recovery"), "sleep": one.get("sleep"), "cycle": one.get("cycle"),
                    "workouts": one.get("workouts") or []} if period == "day" else None}


def summary_context(s, profile, body):
    """Сводка текстом для Claude — цифры уже посчитаны."""
    def line(x):
        if not x:
            return "нет данных"
        return (f"дней с данными {x.get('days')}, восстановление {x.get('recovery')}% "
                f"(зелёных {x.get('green', '—')}, жёлтых {x.get('yellow', '—')}, красных {x.get('red', '—')}), "
                f"ВСР {x.get('hrv')} мс, пульс покоя {x.get('rhr')}, сон {x.get('sleep')} ч (нужно {x.get('need', '—')}, "
                f"выполнение {x.get('perf', '—')}%), отбой {_clock(x.get('bed'))} ±{x.get('bed_spread', '—')} ч, "
                f"нагрузка {x.get('strain')}, траты {x.get('kcal')} ккал, тренировок {x.get('workouts', '—')} "
                f"({x.get('minutes', '—')} мин)")
    st = s["stats"]
    lines = ["ПРОФИЛЬ: " + profile_line(profile, body),
             f"ПЕРИОД: {s['title']} ({s['since']} — {s['until']}, {s['span']} дн; WHOOP с {s['first']})",
             "ЗА ПЕРИОД: " + line(st),
             f"СРАВНЕНИЕ — {s['prev_label']}: " + line(s["prev"])]
    if st.get("last"):
        lines.append("ПОСЛЕДНИЕ 30 ДНЕЙ: " + line(st["last"]))
    if st.get("sports"):
        lines.append("СПОРТ: " + "; ".join(f"{x['sport']} {x['count']} раз, {x['minutes']} мин" for x in st["sports"]))
    if st.get("best"):
        lines.append(f"ЛУЧШИЙ ДЕНЬ: {st['best']['day']} ({st['best']['recovery']}%), "
                     f"ХУДШИЙ: {st['worst']['day']} ({st['worst']['recovery']}%)")
    d = s.get("day") or {}
    for w in d.get("workouts") or []:
        lines.append(f"ТРЕНИРОВКА: {w['sport']} {w['minutes']} мин, нагрузка {w['strain']}")
    return "\n".join(lines)


def profile_line(p, body):
    parts = []
    if p.get("age"):
        parts.append(f"{p['age']} лет")
    if p.get("sex"):
        parts.append({"m": "парень", "f": "девушка"}.get(p["sex"], p["sex"]))
    w = weight_of(p, body)
    h = p.get("height") or ((body.get("height_meter") or 0) * 100) or None
    if w:
        sc = body.get("scale") or {}
        parts.append(f"вес {w} кг" + (f" (умные весы, {sc['day'][8:10]}.{sc['day'][5:7]})" if sc.get("kg") and sc.get("day") else ""))
    if h:
        parts.append(f"рост {round(h)} см")
    if body.get("max_heart_rate"):
        parts.append(f"макс. пульс {body['max_heart_rate']}")
    if p.get("sport"):
        parts.append(f"спорт: {p['sport']}")
    if p.get("schedule"):
        parts.append(f"расписание: {p['schedule']}")
    g = p.get("goal") or "form"
    parts.append("цель: " + GOAL_NAMES.get(g, str(g)))
    if p.get("diet"):
        parts.append(f"питание: {p['diet']}")
    if p.get("notes"):
        parts.append(f"заметки: {p['notes']}")
    return ", ".join(parts)
