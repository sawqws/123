"""Оценки недели и месяца (как Weekly/Monthly Performance Assessment WHOOP) и календарь восстановления.

Подключение — FEATURES в coach.py: route / extra / context_lines (см. там).
  GET /api/assess?period=week|month — неделя: последние 7 полных дней (по вчера — сегодняшняя нагрузка ещё растёт)
      против 7 дней до них; месяц: 30 полных дней против 30 до них.
  GET /api/calendar?month=YYYY-MM — месяц сеткой: восстановление по дням (последние 180 дней).
Состояние тренированности и статус сна — наш расчёт (WHOOP API своих оценок недели не отдаёт), пороги ниже.
"""
import datetime
import statistics

CAL_DAYS = 180  # календарь листается на полгода назад
MONTHS = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
MON_S = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
WD_S = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
SPAN = {"week": 7, "month": 30}

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
            "hrv": rec.get("hrv"), "rhr": rec.get("rhr"), "workouts": len(v.get("workouts") or [])}


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
           "days": days, "cur": cur, "prev": prev, "enough": cur["days"] >= 3,
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
            weeks.append({"since": start.isoformat(), "until": end.isoformat(),
                          "label": span_label(start.isoformat(), end.isoformat()),
                          "sleep": st["sleep"], "need": st["need"], "restor": st["restor"],
                          "recovery": st["recovery"], "strain": st["strain"], "days": st["days"]})
            end = start - datetime.timedelta(days=1)
        out["weeks"] = weeks[::-1]
        out["state"] = training_state(app.insights.load_ratio(data, until), cur["recovery"])
    return out


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
        raise ValueError("Календарь показывает последние полгода")
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
                     "zones": [r["zone"] for r in w["days"]], "enough": w["enough"]}}


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
    return [line + "."]
