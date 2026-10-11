"""Расчёты по данным WHOOP без ИИ: сводка дня, обычный уровень (базовая линия), ориентир нагрузки,
питание на день, ряды для графиков и простые выводы. Это показывает приложение всегда,
а Claude получает как готовый контекст (ему не нужно пересчитывать цифры).

Пороги восстановления как в WHOOP: зелёная зона 67–100, жёлтая 34–66, красная 0–33.
Ориентиры нагрузки и питания — наши (общие спортивные рекомендации), не WHOOP.
"""
import datetime
import statistics

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
}
GOAL_NAMES = {"form": "Форма и результат", "mass": "Набор мышц", "dry": "Рельеф", "weight": "Держать вес"}


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
    return profile.get("weight") or body.get("weight_kilogram")


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
    goal = profile.get("goal") or "form"
    teen = bool(age and age < 18)
    k = {"form": 1.0, "weight": 1.0, "mass": 1.1, "dry": 0.95 if teen else 0.88}[goal]
    kcal = round(spend * k / 10) * 10
    protein = round(w * {"form": 1.6, "weight": 1.7, "mass": 1.8, "dry": 1.8 if teen else 2.0}[goal])
    fat = round(max(w * 0.9, kcal * 0.27 / 9))
    carbs = max(0, round((kcal - protein * 4 - fat * 9) / 4))
    water = round((w * 35 + train_minutes * 10) / 50) * 50
    return {"kcal": kcal, "protein": protein, "fat": fat, "carbs": carbs, "water": water, "spend": round(spend),
            "how": how, "goal": goal, "teen": teen}


def eaten(meals):
    t = {k: round(sum(m.get(k) or 0 for m in meals)) for k in ("kcal", "protein", "fat", "carbs")}
    t["count"] = len(meals)
    return t


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
            out.append({"tone": "bad", "text": f"ВСР на {abs(round(d))}% ниже твоей нормы — нервная система не восстановилась. "
                                               "Сегодня техника и лёгкая работа, без спаррингов на максимум."})
        elif d >= 10:
            out.append({"tone": "good", "text": f"ВСР на {round(d)}% выше нормы — хороший день для тяжёлой работы."})
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


# ---------- всё про день ----------

def today(db, uid, day, profile, body):
    since = day - datetime.timedelta(days=45)
    days = by_day(db, uid, since.isoformat(), day.isoformat())
    d = days.get(day.isoformat()) or {"recovery": None, "sleep": None, "naps": [], "cycle": None, "workouts": []}
    base = baseline(days, day)
    ratio = load_ratio(days, day)
    recent = [days[x] for x in sorted(days) if x > (day - datetime.timedelta(days=7)).isoformat()]
    train_min = sum(w["minutes"] or 0 for w in d["workouts"])
    food = db.meals(uid, day.isoformat())
    nut = nutrition(profile, body, base, (d["cycle"] or {}).get("kcal"), train_min)
    return {
        "day": day.isoformat(), **d, "base": base, "ratio": ratio, "band": strain_band(d["recovery"]),
        "tips": tips(d, base, ratio, profile, recent), "nutrition": nut, "eaten": eaten(food), "meals": food,
        "has_data": bool(days),
    }


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
    if t["nutrition"]:
        n_ = t["nutrition"]
        lines.append(f"ПИТАНИЕ НА ДЕНЬ (наш расчёт, {n_['how']}): {n_['kcal']} ккал, Б {n_['protein']} г, "
                     f"Ж {n_['fat']} г, У {n_['carbs']} г, вода {n_['water']} мл")
    e = t["eaten"]
    if e["count"]:
        lines.append(f"СЪЕДЕНО СЕГОДНЯ: {e['kcal']} ккал, Б {e['protein']}, Ж {e['fat']}, У {e['carbs']} — "
                     + "; ".join(f"{m['time']} {m['title']}" for m in t["meals"]))
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


def profile_line(p, body):
    parts = []
    if p.get("age"):
        parts.append(f"{p['age']} лет")
    if p.get("sex"):
        parts.append({"m": "парень", "f": "девушка"}.get(p["sex"], p["sex"]))
    w = weight_of(p, body)
    h = p.get("height") or ((body.get("height_meter") or 0) * 100) or None
    if w:
        parts.append(f"вес {w} кг")
    if h:
        parts.append(f"рост {round(h)} см")
    if body.get("max_heart_rate"):
        parts.append(f"макс. пульс {body['max_heart_rate']}")
    if p.get("sport"):
        parts.append(f"спорт: {p['sport']}")
    if p.get("schedule"):
        parts.append(f"расписание: {p['schedule']}")
    parts.append("цель: " + GOAL_NAMES.get(p.get("goal") or "form", "форма"))
    if p.get("diet"):
        parts.append(f"питание: {p['diet']}")
    if p.get("notes"):
        parts.append(f"заметки: {p['notes']}")
    return ", ".join(parts)
