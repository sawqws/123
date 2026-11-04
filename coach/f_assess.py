"""Оценки недели и месяца (как Weekly/Monthly Performance Assessment WHOOP) и календарь восстановления.

Подключение — FEATURES в coach.py: route / extra / context_lines (см. там).
  GET /api/assess?period=week|month — неделя: последние 7 полных дней (по вчера — сегодняшняя нагрузка ещё растёт)
      против 7 дней до них; месяц: 30 полных дней против 30 до них.
  GET /api/calendar?month=YYYY-MM — месяц сеткой: восстановление по дням (последние 180 дней).
Состояние тренированности и статус сна — наш расчёт (WHOOP API своих оценок недели не отдаёт), пороги ниже.

Главное на экране — «story» (Глеб 03.11: «раздел оценок вообще непонятный»): всё готовыми строками простыми словами,
без %, мс, «нагрузки из 21» и английских слов — итог одной фразой, оценка 1–5 точек, 3–5 строк «что было» со сравнением
словами, лучший и самый тяжёлый день с причиной, 2–3 совета (только сон, еда, вода, восстановление, режим — не
тренировки: расписание у него своё), кружки дней. Цифры и графики (cur/prev/days/…) — для «Подробнее».
"""
import datetime
import statistics

CAL_DAYS = 180  # календарь листается на полгода назад
MONTHS = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
MON_S = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
WD_S = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
SPAN = {"week": 7, "month": 30}
MIN_DAYS = {"week": 4, "month": 10}  # меньше — честно «мало данных», без оценки

# наш расчёт: состояние тренированности по отношению нагрузки 7/28 дней и среднему восстановлению недели
OVER_RATIO, OVER_SOFT, OVER_REC = 1.3, 1.1, 50       # ≥1,3 — перегрузка; ≥1,1 при восстановлении < 50% — тоже
REST_RATIO, REST_SOFT, REST_REC = 0.8, 0.95, 67      # ≤0,8 — недогруз; <0,95 при зелёном восстановлении — тоже
# наш расчёт: сон в порядке — ≥90% потребности и отбой гуляет не больше ±45 мин; меньше 80% — не хватает
SLEEP_GOOD, SLEEP_BAD, SPREAD_OK = 90, 80, 0.75

STATE = {
    "over": "Перегрузка",
    "optimal": "Оптимально",
    "restore": "Недогруз",
}
SLEEP = {"good": "В порядке", "ok": "Можно лучше", "bad": "Не хватает"}


def _d(s):
    return datetime.date.fromisoformat(s)


def _mean(xs, n=1):
    xs = [x for x in xs if x is not None]
    return round(statistics.mean(xs), n) if xs else None


def hm(h):
    if h is None:
        return "—"
    m = round(h * 60)
    return f"{m // 60}:{m % 60:02d}"


def fnum(x, n=0):
    if x is None:
        return "—"
    return (f"{x:.{n}f}" if n else str(round(x))).replace(".", ",")


def _clock(x):
    """Часы от полудня (как в insights) -> «23:40»."""
    if x is None:
        return None
    m = round(x * 60) % (24 * 60)
    return f"{(m // 60 + 12) % 24:02d}:{m % 60:02d}"


def span_label(since, until):
    a, b = _d(since), _d(until)
    if a.month == b.month:
        return f"{a.day}–{b.day} {MON_S[b.month - 1]}"
    return f"{a.day} {MON_S[a.month - 1]} – {b.day} {MON_S[b.month - 1]}"


def _restor(s):
    """Восстанавливающий сон = глубокий (SWS) + REM, часы."""
    if not s or s.get("deep") is None or s.get("rem") is None:
        return None
    return round(s["deep"] + s["rem"], 2)


def stats(app, data, since, until):
    """Средние за since..until (включительно): то, что считает insights, плюс восстанавливающий сон,
    режим сна (consistency WHOOP) и минуты в пульсовых зонах Z1–Z5 по тренировкам."""
    st = app.insights.period_stats(data, since, until)
    vs = [v for d, v in data.items() if since <= d <= until]
    slp = [v["sleep"] for v in vs if v["sleep"]]
    zones = [0] * 5
    for v in vs:
        for w in v["workouts"]:
            for i, m in enumerate((w.get("zones") or [])[1:6]):
                zones[i] += m or 0
    restor = _mean((_restor(s) for s in slp), 2)
    st.update(restor=restor, restor_pct=round(restor / st["sleep"] * 100) if restor and st["sleep"] else None,
              consistency=_mean((s.get("consistency") for s in slp), 0), zones=zones)
    st.pop("sports", None)
    return st


def training_state(ratio, rec):
    """Оптимально / Перегрузка / Недогруз — наш расчёт (пороги вверху файла) и объяснение простыми словами."""
    if ratio is None:
        return {"key": None, "word": "Мало данных", "ratio": None, "recovery": rec,
                "text": "Нужно хотя бы 2 недели с браслетом, чтобы сравнить нагрузку недели с обычной."}
    x = f"×{fnum(ratio, 2)}"
    r = f"{fnum(rec)}%" if rec is not None else "—"
    if ratio >= OVER_RATIO or (ratio >= OVER_SOFT and rec is not None and rec < OVER_REC):
        key, text = "over", (f"Нагрузка недели выше обычной ({x} к среднему за 4 недели), а восстановление {r} — "
                             "организм не успевает. 1–2 лёгких дня и сон подольше — и форма вырастет.")
    elif ratio <= REST_RATIO or (ratio < REST_SOFT and rec is not None and rec >= REST_REC):
        key, text = "restore", (f"Нагрузка ниже обычной ({x}), восстановление {r}. Для отдыха это хорошо, "
                                "но если так ещё неделю, форма начнёт уходить — можно добавить тренировку.")
    else:
        key, text = "optimal", (f"Нагрузка примерно как обычно ({x}), восстановление {r} — "
                                "организм успевает. Так форма и растёт: держи этот ритм.")
    return {"key": key, "word": STATE[key], "ratio": ratio, "recovery": rec, "text": text}


def sleep_status(st):
    """Сон против потребности, режим (разброс отбоя) и восстанавливающий сон — наш расчёт."""
    s, need = st.get("sleep"), st.get("need")
    if not s:
        return {"key": None, "word": "Нет данных", "text": "Сон за этот период WHOOP ещё не прислал."}
    pct = round(s / need * 100) if need else None
    spread = st.get("bed_spread")
    if pct is not None and pct >= SLEEP_GOOD and (spread is None or spread <= SPREAD_OK):
        key = "good"
    elif pct is not None and pct < SLEEP_BAD:
        key = "bad"
    else:
        key = "ok"
    parts = [f"Спал в среднем {hm(s)}" + (f" из {hm(need)} нужных ({pct}%)." if need else ".")]
    if spread is not None:
        mins = round(spread * 60)
        parts.append("Ложишься в одно и то же время — это помогает восстановлению." if spread <= SPREAD_OK
                     else f"Время отбоя гуляет (±{mins} мин) — организму легче, когда режим ровный.")
    if st.get("restor"):
        parts.append(f"Глубокий сон и REM (восстанавливают мышцы и голову) — {hm(st['restor'])} за ночь"
                     + (f", {st['restor_pct']}% сна." if st.get("restor_pct") else "."))
    return {"key": key, "word": SLEEP[key], "sleep": s, "need": need, "pct": pct, "spread": spread,
            "consistency": st.get("consistency"), "restor": st.get("restor"), "restor_pct": st.get("restor_pct"),
            "bed": _clock(st.get("bed")), "text": " ".join(parts)}


def _day_row(data, d):
    v = data.get(d) or {}
    rec, slp, cyc = v.get("recovery") or {}, v.get("sleep") or {}, v.get("cycle") or {}
    return {"day": d, "wd": _d(d).weekday(), "recovery": rec.get("score"), "zone": rec.get("zone"),
            "strain": cyc.get("strain"), "sleep": slp.get("hours"), "need": slp.get("need"), "restor": _restor(slp),
            "hrv": rec.get("hrv"), "rhr": rec.get("rhr"), "workouts": len(v.get("workouts") or []),
            "minutes": sum(w.get("minutes") or 0 for w in v.get("workouts") or [])}


def _load(app, uid, since, until):
    return app.insights.by_day(app.db, uid, since, until)


def assess(app, uid, today, period="week"):
    if period not in SPAN:
        raise ValueError("Оценка бывает за неделю или за месяц")
    n = SPAN[period]
    until = today - datetime.timedelta(days=1)            # полные дни: сегодняшняя нагрузка ещё набирается
    since = until - datetime.timedelta(days=n - 1)
    p_until = since - datetime.timedelta(days=1)
    p_since = p_until - datetime.timedelta(days=n - 1)
    lo = min(p_since, until - datetime.timedelta(days=27))  # 28 дней — для отношения нагрузки 7/28
    data = _load(app, uid, lo.isoformat(), until.isoformat())
    days = [_day_row(data, (since + datetime.timedelta(days=i)).isoformat()) for i in range(n)]
    cur = stats(app, data, since.isoformat(), until.isoformat())
    prev = stats(app, data, p_since.isoformat(), p_until.isoformat())
    out = {"period": period, "title": "Оценка недели" if period == "week" else "Оценка месяца",
           "since": since.isoformat(), "until": until.isoformat(), "label": span_label(since.isoformat(), until.isoformat()),
           "prev_label": span_label(p_since.isoformat(), p_until.isoformat()), "span": n,
           "days": days, "cur": cur, "prev": prev, "enough": cur["days"] >= MIN_DAYS[period],
           "sleep": sleep_status(cur)}
    # нагрузка по дням недели пн–вс: за неделю — сам день, за месяц — среднее по всем таким дням
    wd = []
    for i in range(7):
        rows = [r for r in days if r["wd"] == i]
        last = rows[-1]["day"] if rows else None
        wd.append({"wd": i, "label": WD_S[i], "day": last, "n": sum(r["strain"] is not None for r in rows),
                   "strain": _mean((r["strain"] for r in rows), 1), "recovery": _mean((r["recovery"] for r in rows), 0)})
    out["by_weekday"] = wd
    if period == "week":
        ratio = app.insights.load_ratio(data, until)
        out["state"] = training_state(ratio, cur["recovery"])
        out["prev_state"] = training_state(app.insights.load_ratio(data, p_until), prev["recovery"]) if prev["days"] else None
    else:
        # недели месяца: по 7 дней от конца, остаток (2 дня) — к самой ранней
        weeks, end = [], until
        while end >= since:
            start = max(since, end - datetime.timedelta(days=6))
            if (start - since).days < 7 and start != since:
                start = since
            st = stats(app, data, start.isoformat(), end.isoformat())
            weeks.append({**week_brief(st, app.insights.load_ratio(data, end)), "since": start.isoformat(), "until": end.isoformat(),
                          "label": span_label(start.isoformat(), end.isoformat()),
                          "sleep": st["sleep"], "need": st["need"], "restor": st["restor"],
                          "recovery": st["recovery"], "strain": st["strain"], "days": st["days"]})
            end = start - datetime.timedelta(days=1)
        out["weeks"] = weeks[::-1]
        out["state"] = training_state(app.insights.load_ratio(data, until), cur["recovery"])
    out["story"] = story(app, uid, period, cur, prev, days, out["state"]["key"], since.isoformat(), until.isoformat(),
                         p_since.isoformat(), p_until.isoformat())
    return out


# ---------- «story»: понятная оценка готовыми строками (Глеб 03.11) ----------

NB = " "
WD_FULL = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
WD_PO = ["по понедельникам", "по вторникам", "по средам", "по четвергам", "по пятницам", "по субботам", "по воскресеньям"]
WD_IN = ["в понедельник", "во вторник", "в среду", "в четверг", "в пятницу", "в субботу", "в воскресенье"]
GRADE_W = {"week": {5: "Отличная", 4: "Хорошая", 3: "Обычная", 2: "Тяжёлая", 1: "Очень тяжёлая"},
           "month": {5: "Отличный", 4: "Хороший", 3: "Обычный", 2: "Тяжёлый", 1: "Очень тяжёлый"}}
HEAD_W = {"week": {5: "Неделя отличная", 4: "Неделя хорошая", 3: "Неделя обычная", 2: "Неделя тяжелее обычной",
                   1: "Неделя тяжёлая"},
          "month": {5: "Месяц отличный", 4: "Месяц хороший", 3: "Месяц обычный", 2: "Месяц тяжелее обычного",
                    1: "Месяц тяжёлый"}}
THAN = {"week": "чем на прошлой неделе", "month": "чем в прошлом месяце"}
SAME = {"week": "как на прошлой неделе", "month": "как в прошлом месяце"}
# уровень сил по зоне восстановления — для кружков дней (заполненность: 3 — полный кружок)
LEVEL = {"green": (3, "много сил"), "yellow": (2, "средне"), "red": (1, "мало сил")}
# наш расчёт оценки 1–5: сон к потребности, среднее восстановление, без перегрузки, ровный отбой
G_SLEEP, G_SLEEP_OK, G_REC, G_REC_OK = 0.92, 0.82, 67, 45
WATER_LOW = 1500  # мл в день — меньше считаем «мало воды» (подростку-спортсмену нужно 2–2,5 л)


def dur(h):
    """Часы → «7 ч 40 мин» / «45 мин» / «8 ч» (неразрывные пробелы)."""
    if h is None:
        return "—"
    H, mm = divmod(round(abs(h) * 60), 60)
    if not H:
        return f"{mm}{NB}мин"
    return f"{H}{NB}ч {mm}{NB}мин" if mm else f"{H}{NB}ч"


def _plural(n, one, few, many):
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    return few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many


def _days(n):
    return f"{n} {_plural(n, 'день', 'дня', 'дней')}"


def _liters(ml):
    if ml >= 1000:
        return f"{ml / 1000:.1f}".rstrip("0").rstrip(".").replace(".", ",") + f"{NB}л"
    return f"{int(round(ml, -1))}{NB}мл"


def _thousands(x):
    return f"{int(round(x, -1)):,}".replace(",", NB)


def _day_title(d):
    x = _d(d)
    return f"{WD_FULL[x.weekday()]}, {x.day} {MON_S[x.month - 1]}"


def _cmp(diff, eps, more, less, fmt, period, good_up=None):
    """Сравнение с прошлым периодом словами и числом: {text, dir: up|down|same, good: True|False|None}."""
    if diff is None:
        return None
    if abs(diff) < eps:
        return {"text": SAME[period], "dir": "same", "good": None}
    up = diff > 0
    return {"text": f"на {fmt(abs(diff))} {more if up else less}, {THAN[period]}", "dir": "up" if up else "down",
            "good": None if good_up is None else good_up == up}


def _word_cmp(diff, eps, more, less, period, good_up=None):
    """То же без числа: «лучше, чем на прошлой неделе»."""
    if diff is None:
        return None
    if abs(diff) < eps:
        return {"text": SAME[period], "dir": "same", "good": None}
    up = diff > 0
    return {"text": f"{more if up else less}, {THAN[period]}", "dir": "up" if up else "down",
            "good": None if good_up is None else good_up == up}


def grade(st, state_key):
    """Оценка периода 1–5 (наш расчёт): сон к потребности 0–2, восстановление 0–2, без перегрузки 1, ровный отбой 1."""
    pts = 0
    s, need = st.get("sleep"), st.get("need") or 9
    if s:
        pts += 2 if s / need >= G_SLEEP else 1 if s / need >= G_SLEEP_OK else 0
    rec = st.get("recovery")
    if rec is not None:
        pts += 2 if rec >= G_REC else 1 if rec >= G_REC_OK else 0
    pts += state_key != "over"
    spread = st.get("bed_spread")
    pts += spread is None or spread <= SPREAD_OK
    return {0: 1, 1: 1, 2: 2, 3: 3, 4: 4, 5: 4, 6: 5}[pts]


def food_water(app, uid, since, until):
    """Вода и еда (Форма + «Калории») по дням: {water: {день: мл}, kcal: {день: ккал}}."""
    db, out = getattr(app, "db", None), {"water": {}, "kcal": {}}
    if db is None:
        return out
    try:
        out["water"] = {d: ml for d, ml in db.water_days(uid, since, until).items() if ml}
        kc = {}
        for m in db.meals(uid, since, until):
            kc[m["day"]] = kc.get(m["day"], 0) + (m["kcal"] or 0)
        for d, data in db.kcal_days(uid, since, until).items():
            s = sum(x.get("kcal") or 0 for x in data.get("items") or [] if isinstance(x, dict))
            if s:
                kc[d] = kc.get(d, 0) + s
        out["kcal"] = {d: v for d, v in kc.items() if v}
    except (AttributeError, TypeError, ValueError):
        pass
    return out


def _avg_in(dct, since, until):
    xs = [v for d, v in dct.items() if since <= d <= until]
    return (sum(xs) / len(xs), len(xs)) if xs else (None, 0)


def _when(day, period):
    """«в среду» — для недели, «14 сен» — для месяца."""
    x = _d(day)
    return WD_IN[x.weekday()] if period == "week" else f"{x.day} {MON_S[x.month - 1]}"


def _best_day(rows, cur):
    """Лучший день — больше всего сил с утра; причина — сон накануне или лёгкий день перед ним."""
    ok = [r for r in rows if r["recovery"] is not None]
    if not ok:
        return None
    b = max(ok, key=lambda r: (r["recovery"], r["day"]))
    i, why = rows.index(b), []
    if b["sleep"] and cur.get("sleep") and b["sleep"] - cur["sleep"] >= 1 / 3:
        why.append(f"выспался — {dur(b['sleep'])} сна")
    prev = rows[i - 1] if i > 0 else None
    if prev and not prev["workouts"] and prev["strain"] is not None:
        why.append("накануне был день отдыха")
    elif prev and prev["strain"] is not None and cur.get("strain") and prev["strain"] <= cur["strain"] - 1.5:
        why.append("накануне был лёгкий день")
    if not why:
        why.append("утром было больше всего сил — организм хорошо восстановился")
    return {"day": b["day"], "title": _day_title(b["day"]), "word": LEVEL.get(b["zone"], (0, ""))[1],
            "why": ", ".join(why).capitalize() + "."}


def _hard_day(rows):
    """Самый тяжёлый день — самая большая нагрузка; причина — тренировки, сон накануне, силы на следующее утро."""
    ok = [r for r in rows if r["strain"] is not None]
    if not ok:
        return None
    h = max(ok, key=lambda r: (r["strain"], r["day"]))
    i, why = rows.index(h), []
    if h["workouts"] == 1:
        why.append(f"тренировка {dur(h['minutes'] / 60)}" if h["minutes"] else "тренировка")
    elif h["workouts"] > 1:
        why.append(f"{h['workouts']} {_plural(h['workouts'], 'тренировка', 'тренировки', 'тренировок')}"
                   + (f" — {dur(h['minutes'] / 60)}" if h["minutes"] else ""))
    else:
        why.append("много движения за день")
    if h["sleep"] and h["need"] and h["need"] - h["sleep"] >= 1:
        why.append(f"а спал накануне всего {dur(h['sleep'])}")
    nxt = rows[i + 1] if i + 1 < len(rows) else None
    if nxt and nxt["zone"] in ("red", "yellow"):
        why.append("на следующее утро сил было " + ("мало" if nxt["zone"] == "red" else "средне"))
    return {"day": h["day"], "title": _day_title(h["day"]), "why": ", ".join(why).capitalize() + "."}


def _tips(period, cur, state_key, hard, rows, water):
    """2–3 совета по фактам периода — только сон, еда, вода, восстановление, режим (тренировки не трогаем:
    расписание у него своё)."""
    out = []
    s, need, bed, spread = cur.get("sleep"), cur.get("need"), cur.get("bed"), cur.get("bed_spread")
    lack = need - s if s and need else 0
    if lack >= 1 / 3:
        shift = min(60, max(15, round(lack * 4) * 15))
        when = f"в {_clock(round((bed - shift / 60) * 4) / 4)}" if bed is not None else "пораньше"
        out.append(f"Ложись {when} — на {dur(shift / 60)} раньше: сна не хватало около {dur(lack)} за ночь.")
    if spread is not None and spread > SPREAD_OK:
        out.append(f"Ложись и вставай в одно время, даже в выходные: отбой гулял на {round(spread * 60)}{NB}мин.")
    tired = sum(r["zone"] == "red" for r in rows)
    if hard and (state_key == "over" or tired):
        out.append(f"После тяжёлых дней (как {_when(hard['day'], period)}) — плотный ужин: каша и мясо или рыба, "
                   "2–3 стакана воды и без телефона за час до сна.")
    elif tired >= 2:
        out.append(f"Дней с красным восстановлением — {tired}. В такие дни ложись пораньше и убирай телефон за час до сна.")
    w = water.get("avg") if water.get("days", 0) >= 3 else None
    if w is not None and w < WATER_LOW:
        out.append(f"Пей больше воды: в среднем {_liters(w)} в день, а нужно хотя бы 2{NB}л. Бутылку — с собой в школу.")
    if len(out) < 2 and not water.get("days") and not water.get("food_days"):
        out.append("Записывай воду и еду — тогда подскажу и по питанию.")
    if len(out) < 2 and s and lack < 1 / 3:
        out.append(f"Сохраняй режим: {dur(s)} сна" + (f" и отбой около {_clock(round(bed * 4) / 4)}" if bed is not None else "")
                   + " — на этом держится гибкость пульса.")
    if len(out) < 2:
        out.append("Ешь в одно и то же время, а после тренировки в течение часа — белок и вода.")
    return out[:3]


def _headline(period, score, cur, prev, state_key):
    """Одна фраза-итог: «Неделя хорошая: спал больше, восстанавливался лучше, но сна не хватало»."""
    pros, cons = [], []
    has_prev = (prev.get("days") or 0) >= MIN_DAYS[period]
    s, need = cur.get("sleep"), cur.get("need")
    ps = prev.get("sleep") if has_prev else None
    if s and need and s / need < G_SLEEP_OK:
        cons.append("сна не хватало")
    if s and ps and s - ps >= .25:
        pros.append("спал больше")
    elif s and ps and ps - s >= .25 and not cons:
        cons.append("спал меньше")
    if s and need and s / need >= G_SLEEP and not pros and not cons:
        pros.append("высыпался")
    r = cur.get("recovery")
    pr = prev.get("recovery") if has_prev else None
    if r is not None and pr is not None and r - pr >= 5:
        pros.append("восстанавливался лучше")
    elif r is not None and pr is not None and pr - r >= 5:
        cons.append("восстанавливался хуже")
    if state_key == "over":
        cons.insert(0, "много нагрузки")
    if (cur.get("red") or 0) >= 2:
        cons.append("были дни без сил")
    spread = cur.get("bed_spread")
    if spread is not None and spread > SPREAD_OK:
        cons.append("ложился в разное время")
    elif spread is not None and spread <= .5:
        pros.append("ложился в одно время")
    if r is not None and cur.get("days") and cur.get("green") == cur["days"]:
        pros.insert(0, "каждый день был с силами")
    head = HEAD_W[period][score]
    main, other, joint = (pros, cons, "но") if score >= 3 else (cons, pros, "зато")
    if not main:
        main, other = other, []
    # не длиннее двух частей: «A, B» или «A, но C»
    main, extra = (main[:1], other[:1]) if other else (main[:2], [])
    if not main:
        return f"{head}: всё ровно, {SAME[period]}" if has_prev else head
    return f"{head}: {', '.join(main)}" + (f", {joint} {extra[0]}" if extra else "")


def _rows(period, cur, prev, state_key, days, water):
    """3–5 строк «что было»: что, крупная цифра в привычных единицах, подпись, сравнение словами."""
    P = prev if (prev.get("days") or 0) >= MIN_DAYS[period] else {}
    out = []
    s, need = cur.get("sleep"), cur.get("need")
    if s:
        diff = round((s - P["sleep"]) * 60) if P.get("sleep") else None
        out.append({"key": "sleep", "name": "Сон", "value": dur(s),
                    "sub": "в среднем за ночь" + (f" · нужно около {dur(round(need * 4) / 4)}" if need else ""),
                    "cmp": _cmp(diff, 10, "больше", "меньше", lambda m: dur(m / 60), period, True)})
    r = cur.get("recovery")
    if r is not None:
        n = cur.get("days") or 0
        out.append({"key": "rec", "name": "Восстановление", "value": "Хорошее" if r >= 67 else "Среднее" if r >= 34 else "Низкое",
                    "sub": f"много сил — {cur.get('green') or 0} из {_days(n)}",
                    "cmp": _word_cmp(r - P["recovery"] if P.get("recovery") is not None else None, 5, "лучше", "хуже", period, True)})
    st = cur.get("strain")
    if st is not None:
        word = {"over": "Больше обычной", "restore": "Меньше обычной", "optimal": "Как обычно"}.get(
            state_key, "Лёгкая" if st < 10 else "Средняя" if st < 14 else "Большая")
        top = max((d for d in days if d["strain"] is not None), key=lambda d: d["strain"], default=None)
        if period == "week":
            sub = f"тяжелее всего — {WD_IN[_d(top['day']).weekday()]}" if top else ""
        else:
            by = {}
            for d in days:
                if d["strain"] is not None:
                    by.setdefault(_d(d["day"]).weekday(), []).append(d["strain"])
            wd = max(by, key=lambda k: statistics.mean(by[k])) if by else None
            sub = f"тяжелее всего — {WD_PO[wd]}" if wd is not None else ""
        out.append({"key": "load", "name": "Нагрузка", "value": word, "sub": sub,
                    "cmp": _word_cmp(st - P["strain"] if P.get("strain") is not None else None, 1, "тяжелее", "легче", period)})
    wk, mins = cur.get("workouts") or 0, cur.get("minutes") or 0
    val = f"{wk} {_plural(wk, 'тренировка', 'тренировки', 'тренировок')}" if wk else "Не было"
    sub = (f"всего {dur(mins / 60)}" if mins else "за период") if wk else "отдыхал от тренировок"
    c = None
    if P and (P.get("days") or 0) >= .8 * (cur.get("days") or 0):  # суммы за неполный период не сравниваем
        pw, pm = P.get("workouts") or 0, P.get("minutes") or 0
        c = (_cmp(wk - pw, 1, "больше", "меньше", lambda x: str(int(x)), period) if wk != pw
             else _cmp(mins - pm, 20, "дольше", "короче", lambda m: dur(m / 60), period))
    out.append({"key": "train", "name": "Тренировки", "value": val, "sub": sub, "cmp": c})
    if water.get("days", 0) >= 3:
        pw = water.get("prev_avg")
        out.append({"key": "food", "name": "Питание и вода", "value": f"{_liters(water['avg'])} воды",
                    "sub": "в день в среднем" + (f" · еда — около {_thousands(water['kcal'])}{NB}ккал" if water.get("kcal") else ""),
                    "cmp": _cmp(water["avg"] - pw, 200, "больше", "меньше", _liters, period, True) if pw is not None else None})
    elif water.get("kcal"):
        out.append({"key": "food", "name": "Питание", "value": f"{_thousands(water['kcal'])}{NB}ккал",
                    "sub": "еды в день в среднем", "cmp": None})
    return out


def _circles(days):
    """Кружки дней: заполненность = восстановление (3 — много сил, 2 — средне, 1 — мало, 0 — нет данных)."""
    out = []
    for r in days:
        lv, word = LEVEL.get(r["zone"], (0, "нет данных"))
        out.append({"day": r["day"], "wd": WD_S[_d(r["day"]).weekday()], "date": _d(r["day"]).day, "level": lv, "word": word})
    return out


def story(app, uid, period, cur, prev, days, state_key, since, until, p_since, p_until):
    """Всё для главной части экрана — готовыми строками."""
    n, need, have = SPAN[period], MIN_DAYS[period], cur.get("days") or 0
    if have < need:
        tail = f" Пока есть {_days(have)}." if have else " Пока данных нет."
        return {"enough": False, "need": need, "have": have, "days": _circles(days),
                "empty": f"Мало данных за {'неделю' if period == 'week' else 'месяц'} — нужно хотя бы {_days(need)} с браслетом.{tail}"}
    fw = food_water(app, uid, p_since, until)
    w_avg, w_n = _avg_in(fw["water"], since, until)
    pw_avg, pw_n = _avg_in(fw["water"], p_since, p_until)
    k_avg, k_n = _avg_in(fw["kcal"], since, until)
    water = {"avg": w_avg, "days": w_n, "prev_avg": pw_avg if pw_n >= 3 else None,
             "kcal": k_avg if k_n >= 3 else None, "food_days": k_n}
    score, hard = grade(cur, state_key), _hard_day(days)
    out = {"enough": True, "need": need, "have": have,
           "grade": {"score": score, "word": GRADE_W[period][score]},
           "headline": _headline(period, score, cur, prev, state_key),
           "rows": _rows(period, cur, prev, state_key, days, water),
           "best": _best_day(days, cur), "hard": hard,
           "tips": _tips(period, cur, state_key, hard, days, water), "days": _circles(days)}
    # для экрана: крупно «Неделя хорошая», под ним — «Восстанавливался лучше, но сна не хватало.»
    title, _, rest = out["headline"].partition(": ")
    out["title"], out["summary"] = title, (rest[:1].upper() + rest[1:] + ".") if rest else ""
    if have < n:
        out["partial"] = f"Данные есть за {have} из {n} дней — оценка примерная."
    if (prev.get("days") or 0) < need:
        out["no_prev"] = ("За прошлую неделю" if period == "week" else "За прошлый месяц") + " мало данных — сравнивать не с чем."
    return out


def week_brief(st, ratio):
    """Неделя внутри месяца: оценка точками и коротко «сон · силы · тренировки»."""
    have = st.get("days") or 0
    if have < MIN_DAYS["week"]:
        return {"score": None, "word": "Мало данных", "line": f"данные есть за {have} из 7 дней"}
    sc = grade(st, training_state(ratio, st.get("recovery"))["key"])
    wk = st.get("workouts") or 0
    parts = ([f"сон {dur(st['sleep'])}"] if st.get("sleep") else []) + [
        f"много сил — {st.get('green') or 0} из {_days(have)}",
        f"{wk} {_plural(wk, 'тренировка', 'тренировки', 'тренировок')}" if wk else "без тренировок"]
    return {"score": sc, "word": GRADE_W["week"][sc], "line": " · ".join(parts)}


def _month_bounds(month):
    y, m = (int(x) for x in month.split("-"))
    first = datetime.date(y, m, 1)
    nxt = datetime.date(y + (m == 12), m % 12 + 1, 1)
    return first, nxt - datetime.timedelta(days=1)


def calendar(app, uid, today, month=None):
    month = month or today.strftime("%Y-%m")
    try:
        first, last = _month_bounds(month)
    except (ValueError, TypeError):
        raise ValueError("Месяц пишется так: 2026-10")
    lo = today - datetime.timedelta(days=CAL_DAYS)
    if last < lo or first > today:
        raise ValueError("Календарь показывает последние 6 месяцев")
    data = _load(app, uid, first.isoformat(), min(last, today).isoformat())
    days = []
    for i in range((last - first).days + 1):
        d = first + datetime.timedelta(days=i)
        row = _day_row(data, d.isoformat())
        row.update(future=d > today, old=d < lo, today=d == today)
        days.append(row)
    st = app.insights.period_stats(data, first.isoformat(), min(last, today).isoformat())
    prev_last = first - datetime.timedelta(days=1)
    nxt_first = last + datetime.timedelta(days=1)
    return {"month": month, "title": f"{MONTHS[first.month - 1]} {first.year}", "offset": first.weekday(),
            "days": days, "today": today.isoformat(),
            "prev": prev_last.strftime("%Y-%m") if prev_last >= lo else None,
            "next": nxt_first.strftime("%Y-%m") if nxt_first <= today else None,
            "stats": {"days": st["days"], "recovery": st["recovery"], "green": st["green"], "yellow": st["yellow"],
                      "red": st["red"], "workouts": st["workouts"], "strain": st["strain"], "sleep": st["sleep"]}}


def route(app, uid, path, b, today):
    if path == "/api/assess":
        return assess(app, uid, today, str(b.get("period") or "week"))
    if path == "/api/calendar":
        return calendar(app, uid, today, b.get("month") or None)
    return None


def extra(app, uid, today):
    """Для плиток во «Здоровье»: слово-итог недели и цвета дней (без лишних расчётов месяца)."""
    w = assess(app, uid, today, "week")
    return {"week": {"label": w["label"], "state": {k: w["state"][k] for k in ("key", "word")},
                     "sleep": {k: w["sleep"].get(k) for k in ("key", "word", "pct")},
                     "zones": [r["zone"] for r in w["days"]], "enough": w["enough"],
                     "grade": w["story"].get("grade"), "headline": w["story"].get("headline"),
                     "summary": w["story"].get("summary")}}


def context_lines(app, uid, today):
    w = assess(app, uid, today, "week")
    if not w["enough"]:
        return []
    s, sl = w["state"], w["sleep"]
    line = f"Оценка недели ({w['label']}, наш расчёт): тренированность — {s['word'].lower()}"
    if s["ratio"] is not None:
        line += f" (нагрузка 7/28 ×{fnum(s['ratio'], 2)}, восстановление {fnum(s['recovery'])}%)"
    line += f"; сон — {sl['word'].lower()}"
    if sl.get("sleep"):
        line += f" ({hm(sl['sleep'])}" + (f" из {hm(sl['need'])}, {sl['pct']}%" if sl.get("need") else "")
        if sl.get("spread") is not None:
            line += f", разброс отбоя ±{round(sl['spread'] * 60)} мин"
        if sl.get("restor"):
            line += f", глубокий+REM {hm(sl['restor'])}"
        line += ")"
    st = w["story"]
    if st.get("headline"):
        line += f". Итог на экране оценок: «{st['headline']}» ({st['grade']['word'].lower()}, {st['grade']['score']} из 5)"
    return [line + "."]
