"""Тренды — как Trend View в WHOOP: любой показатель за неделю / месяц / полгода, среднее, % к прошлому такому же
периоду, полоса «обычно у тебя» и фраза простыми словами.

Подключение — FEATURES в coach.py (route).
  POST /api/trends {}                               → {groups: [{key, title, items: [{key, title, unit, note}]}], missing}
  POST /api/trends {metric, period: w|m|6m, end?}   → {metric, title, unit, points: [{day, v}], avg, prev_avg, change,
        change_pct, typical: [lo, hi] | null, text, since, until, label, prev_label, first, can_back, can_next, …}
  Неизвестный показатель, период или дата — ValueError (400).

Показатели — только то, что отдаёт WHOOP API v2 (cycle, recovery, sleep, workout) и вес. Сон — только основной (не nap),
как в insights.by_day. Сегодняшний цикл ещё идёт (нет end) — точка рисуется («день ещё идёт»), но в среднее не входит.
Одна выборка records на нужный вид за max(2 периода, 30 дней): прошлый период и «обычно» — из неё же.
«Обычно» — середина твоих дней (от 25-го до 75-го процентиля) за 30 дней по конец периода; меньше 7 дней — нет полосы.
"""
import datetime
import statistics

import f_assess
import insights

PERIODS = {"w": 7, "m": 30, "6m": 180}
PER_WORD = {"w": "за неделю", "m": "за месяц", "6m": "за 6 месяцев"}
PREV_WORD = {"w": "за прошлые 7 дней", "m": "за прошлые 30 дней", "6m": "за прошлые 6 месяцев"}
VS_WORD = {"w": "к прошлой неделе", "m": "к прошлому месяцу", "6m": "к прошлым 6 месяцам"}
TYPICAL_DAYS, TYPICAL_MIN = 30, 7
MISSING = "Нет в WHOOP API: VO₂ max, стресс, силовые (время и объём), сухая масса — WHOOP не отдаёт их другим приложениям"
GROUPS = (("load", "Нагрузка"), ("recovery", "Восстановление"), ("sleep", "Сон"), ("other", "Другое"))


def _m(key, group, title, unit, src, say, *, dec=0, chart="bars", good=None, more=False, note="", top=None, absdiff=False):
    """Описание показателя: src — виды записей WHOOP; say — как назвать в фразе («В среднем за месяц {say} — …»
    или со своим местом для числа: «ты проходил {v} в день»);
    good — что лучше (True — больше, False — меньше, None — без оценки); more — «больше/меньше» вместо «выше/ниже»;
    top — потолок шкалы графика; absdiff — сравнивать разницей, а не процентом (температура)."""
    return key, {"key": key, "group": group, "title": title, "unit": unit, "src": src, "say": say, "dec": dec,
                 "chart": chart, "good": good, "more": more, "note": note, "top": top, "absdiff": absdiff}


METRICS = dict([
    # нагрузка (cycle: strain, step_count, average_heart_rate, kilojoule; workout: zone_durations)
    _m("strain", "load", "Нагрузка дня", "из 21", ("cycle",), "нагрузка дня", dec=1, top=21, note="шкала 0–21"),
    _m("steps", "load", "Шаги", "шагов", ("cycle",), "ты проходил {v} в день", more=True),
    _m("zones_13", "load", "Пульсовые зоны 1–3", "мин", ("cycle", "workout"), "ты был в зонах 1–3 по {v} в день",
       more=True, note="на тренировках"),
    _m("zones_45", "load", "Пульсовые зоны 4–5", "мин", ("cycle", "workout"), "ты был в зонах 4–5 по {v} в день",
       more=True, note="на тренировках"),
    _m("zones_all", "load", "Пульсовые зоны, все", "мин", ("cycle", "workout"), "ты был в пульсовых зонах по {v} в день",
       more=True, note="зоны 1–5, на тренировках"),
    _m("avg_hr", "load", "Средний пульс", "уд/мин", ("cycle",), "пульс за день", chart="line", note="за весь день"),
    _m("kcal", "load", "Калории", "ккал", ("cycle",), "ты тратил {v} в день", more=True, note="сколько потратил за день"),
    # восстановление (recovery; дыхание — из сна)
    _m("recovery", "recovery", "Восстановление", "%", ("recovery",), "восстановление", good=True, top=100),
    _m("hrv", "recovery", "Запас сил", "мс", ("recovery",), "запас сил", chart="line", good=True,
       note="вариабельность пульса во сне"),
    _m("rhr", "recovery", "Пульс покоя", "уд/мин", ("recovery",), "пульс покоя", chart="line", good=False),
    _m("resp", "recovery", "Частота дыхания", "вдохов/мин", ("sleep",), "частота дыхания во сне", dec=1, chart="line",
       note="во сне"),
    _m("spo2", "recovery", "Кислород в крови", "%", ("recovery",), "кислород в крови", dec=1, chart="line", note="ночью"),
    _m("skin_temp", "recovery", "Температура кожи", "°C", ("recovery",), "температура кожи", dec=1, chart="line",
       absdiff=True, note="запястье, ночью"),
    # сон (только основной, не дневной)
    _m("sleep_perf", "sleep", "Оценка сна", "%", ("sleep",), "оценка сна", good=True, top=100,
       note="сколько поспал от нужного, по WHOOP"),
    _m("in_bed", "sleep", "Время в кровати", "ч", ("sleep",), "время в кровати", dec=2, more=True),
    _m("sleep_hours", "sleep", "Часы сна", "ч", ("sleep",), "сон", dec=2, good=True, more=True, note="сон без пробуждений против нужного по WHOOP"),
    _m("sleep_need_pct", "sleep", "Сон от нужного", "%", ("sleep",), "сон от нужного", good=True,
       note="сон без пробуждений к тому, сколько было нужно"),
    _m("consistency", "sleep", "Постоянство сна", "%", ("sleep",), "постоянство сна", good=True, top=100,
       note="ложишься и встаёшь в одно время"),
    _m("restor", "sleep", "Восстанавливающий сон", "ч", ("sleep",), "восстанавливающий сон", dec=2, good=True,
       more=True, note="глубокий + быстрый"),
    _m("restor_pct", "sleep", "Восстанавливающий сон, %", "%", ("sleep",), "доля восстанавливающего сна", good=True,
       top=100, note="глубокий + быстрый от всего сна"),
    _m("efficiency", "sleep", "Эффективность сна", "%", ("sleep",), "эффективность сна", good=True, top=100,
       note="сколько из времени в кровати спал"),
    _m("debt", "sleep", "Долг сна", "ч", ("sleep",), "долг сна", dec=2, good=False, more=True,
       note="недосып прошлых ночей"),
    # другое
    _m("weight", "other", "Вес", "кг", ("weight",), "вес", dec=1, chart="line"),
])
CYCLE_METRICS = {"strain", "steps", "avg_hr", "kcal", "zones_13", "zones_45", "zones_all"}  # день ещё идёт — не в среднее


# ---------- источники ----------

def weight_points(app, uid, since, until):
    """Вес по дням [(день, кг)]. Раздел «Вес» (FEATURES["weight"]) подключается сам, если у него есть
    trend_points(app, uid, since, until) -> [(день, кг)] или [{day, kg|v}]. Без него — пусто: WHOOP отдаёт
    только текущий вес (body measurement), истории в базе нет."""
    mod = getattr(app, "FEATURES", {}).get("weight")
    fn = getattr(mod, "trend_points", None) if mod else None
    if not fn:
        return []
    out = []
    for x in fn(app, uid, since, until) or []:
        day, v = (x.get("day"), x.get("kg", x.get("v"))) if isinstance(x, dict) else x
        if day and v is not None and since <= str(day) <= until:
            out.append((str(day), float(v)))
    return out


def weight_now(app, uid):
    """Текущий вес: профиль или WHOOP body measurement (для подписи, когда истории нет)."""
    try:
        return insights.weight_of(app.profile(uid), app.whoop.body(app.db, uid))
    except Exception:  # профиль или база без веса — просто без подписи
        return None


def _score(r):
    return (r or {}).get("score") or {}


def sleep_need(r):
    """Сколько было нужно спать, ч: базовая потребность + долг + после нагрузки − дневной сон
    (WHOOP присылает need_from_recent_nap_milli отрицательным — вычитаем по модулю, с любым знаком)."""
    n = _score(r).get("sleep_needed") or {}
    if not n:
        return None
    ms = (n.get("baseline_milli") or 0) + (n.get("need_from_sleep_debt_milli") or 0) \
        + (n.get("need_from_recent_strain_milli") or 0) - abs(n.get("need_from_recent_nap_milli") or 0)
    return round(ms / 3_600_000, 2) if ms > 0 else None


def zone_minutes(workouts):
    """Минуты тренировок дня в зонах [1–3, 4–5, 1–5] (workout.score.zone_durations)."""
    z = [0.0] * 6
    for w in workouts:
        zd = _score(w).get("zone_durations") or {}
        for i, name in enumerate(("zero", "one", "two", "three", "four", "five")):
            z[i] += (zd.get(f"zone_{name}_milli") or 0) / 60000
    return round(sum(z[1:4])), round(sum(z[4:6])), round(sum(z[1:6]))


def collect(db, uid, kinds, since, until):
    """{день: {cycle, recovery, sleep, sleep_raw, workouts}} за since..until — одна выборка на вид записей."""
    days = {}

    def slot(d):
        return days.setdefault(d, {"cycle": None, "recovery": None, "sleep": None, "sleep_raw": None, "workouts": []})
    if "cycle" in kinds:
        for r in db.records(uid, "cycle", since, until):
            slot(r["_day"])["cycle"] = r
    if "recovery" in kinds:
        for r in db.records(uid, "recovery", since, until):
            v = insights.recovery_of(r)
            if v:
                slot(r["_day"])["recovery"] = v
    if "sleep" in kinds:
        for r in db.records(uid, "sleep", since, until):
            v = insights.sleep_of(r)
            if not v or v["nap"]:
                continue
            s = slot(r["_day"])
            if not s["sleep"] or v["hours"] > s["sleep"]["hours"]:
                s["sleep"], s["sleep_raw"] = v, r
    if "workout" in kinds:
        for r in db.records(uid, "workout", since, until):
            if r.get("score_state") == "SCORED":
                slot(r["_day"])["workouts"].append(r)
    return days


def value(key, v):
    """Значение показателя за день (v — запись collect) или None."""
    c, rec, s = v.get("cycle"), v.get("recovery"), v.get("sleep")
    if key in ("strain", "avg_hr", "kcal"):
        sc = _score(c)
        if key == "strain":
            return round(sc["strain"], 1) if sc.get("strain") is not None else None
        if key == "avg_hr":
            return sc.get("average_heart_rate")
        return round(sc["kilojoule"] / 4.184) if sc.get("kilojoule") else None
    if key == "steps":  # step_count — поле самого цикла (на всякий случай смотрим и в score)
        n = (c or {}).get("step_count")
        n = _score(c).get("step_count") if n is None else n
        return int(n) if n is not None else None
    if key.startswith("zones_"):
        if not c and not v.get("workouts"):
            return None  # браслет не носил — не «0 минут»
        return zone_minutes(v.get("workouts") or [])[{"zones_13": 0, "zones_45": 1, "zones_all": 2}[key]]
    if key in ("recovery", "hrv", "rhr", "spo2", "skin_temp"):
        if not rec:
            return None
        return rec.get("score") if key == "recovery" else rec.get(key)
    if not s:
        return None
    if key == "resp":
        return s.get("resp")
    if key == "sleep_perf":
        return s.get("performance")
    if key == "in_bed":
        return s.get("in_bed")
    if key == "sleep_hours":
        return s.get("hours")
    if key == "sleep_need_pct":
        need = sleep_need(v.get("sleep_raw"))
        return round(s["hours"] / need * 100) if need and s.get("hours") is not None else None
    if key == "consistency":
        return s.get("consistency")
    if key in ("restor", "restor_pct"):
        if s.get("deep") is None or s.get("rem") is None:
            return None
        r = round(s["deep"] + s["rem"], 2)
        return r if key == "restor" else (round(r / s["hours"] * 100) if s.get("hours") else None)
    if key == "efficiency":
        return s.get("efficiency")
    if key == "debt":
        return s.get("debt")
    return None


# ---------- расчёты ----------

def _day(s):
    return datetime.date.fromisoformat(s)


def mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.mean(xs) if xs else None


def typical(xs):
    """«Обычно у тебя» — от 25-го до 75-го процентиля; меньше TYPICAL_MIN дней — None."""
    xs = sorted(x for x in xs if x is not None)
    if len(xs) < TYPICAL_MIN:
        return None
    q = statistics.quantiles(xs, n=4, method="inclusive")
    return [q[0], q[2]]


def change(cur, prev, absdiff=False):
    """(разница, % к прошлому периоду) — % только когда прошлое не 0; для absdiff (температура) — без %."""
    if cur is None or prev is None:
        return None, None
    d = cur - prev
    return d, (None if absdiff or not prev else d / abs(prev) * 100)


def rnd(x, dec):
    if x is None:
        return None
    return round(x, dec) if dec else round(x)


NB = " "


def fnum(x, dec=0):
    """8 400 / 15,2 — как пишут по-русски (пробел в тысячах неразрывный)."""
    if dec:
        return f"{x:.{dec}f}".replace(".", ",")
    return f"{round(x):,}".replace(",", NB)


def fval(m, x):
    """Значение с единицей простыми словами: «7 ч 40 мин», «85%», «8 400 шагов», «33,4 °C»."""
    if x is None:
        return "—"
    u = m["unit"]
    if u == "ч":
        t = round(x * 60)
        h, mm = divmod(abs(t), 60)
        return f"{h}{NB}ч{NB}{mm:02d}{NB}мин" if h else f"{mm}{NB}мин"
    dec = 1 if m["dec"] == 1 else 0
    if u == "%":
        return f"{fnum(x, dec)}%"
    if u == "из 21":
        return f"{fnum(x, 1)} из 21"
    return f"{fnum(x, dec)}{NB}{u}"


def phrase(m, period, avg, prev, d, pct):
    """«В среднем за месяц запас сил — 115 мс: это выше, чем за прошлые 30 дней (106 мс)»."""
    if avg is None:
        return "За этот период данных нет — браслет не носился или WHOOP ещё не прислал."
    say = m["say"]
    head = f"В среднем {PER_WORD[period]} " + (say.format(v=fval(m, avg)) if "{v}" in say else f"{say} — {fval(m, avg)}")
    if prev is None:
        return head + f". {PREV_WORD[period].capitalize()} данных мало — сравнить не с чем."
    same = abs(d) < (0.1 if m["absdiff"] else 0.05 if m["unit"] == "ч" else 0.5) if pct is None else abs(pct) < 1
    if same:
        return head + f": примерно так же, как {PREV_WORD[period]} ({fval(m, prev)})."
    up = d > 0
    word = ("больше" if up else "меньше") if m["more"] else ("выше" if up else "ниже")
    return head + f": это {word}, чем {PREV_WORD[period]} ({fval(m, prev)})."


def span(since, until):
    a, b = _day(since), _day(until)
    lab = f_assess.span_label(since, until)
    return lab.replace(" – ", f" {a.year} – ") + f" {b.year}" if a.year != b.year else lab


def groups():
    out = []
    for g, title in GROUPS:
        items = [{"key": m["key"], "title": m["title"], "unit": m["unit"], "note": m["note"]}
                 for m in METRICS.values() if m["group"] == g]
        out.append({"key": g, "title": title, "items": items})
    return {"groups": out, "missing": MISSING}


def trend(app, uid, today, metric, period="m", end=None):
    m = METRICS.get(str(metric or ""))
    if not m:
        raise ValueError("нет такого показателя")
    if period not in PERIODS:
        raise ValueError("период: w, m или 6m")
    try:
        until_d = min(_day(str(end)), today) if end else today
    except ValueError:
        raise ValueError("дата конца периода: ГГГГ-ММ-ДД") from None
    n = PERIODS[period]
    since_d = until_d - datetime.timedelta(days=n - 1)
    prev_until = since_d - datetime.timedelta(days=1)
    prev_since = prev_until - datetime.timedelta(days=n - 1)
    lo_d = until_d - datetime.timedelta(days=max(2 * n, TYPICAL_DAYS) - 1)  # одна выборка: прошлый период и «обычно»
    since, until, lo = since_d.isoformat(), until_d.isoformat(), lo_d.isoformat()

    vals, partial, extra = {}, set(), {}
    if m["key"] == "weight":
        vals = dict(weight_points(app, uid, lo, until))
    else:
        data = collect(app.db, uid, set(m["src"]), lo, until)
        for d, v in data.items():
            x = value(m["key"], v)
            if x is None:
                continue
            vals[d] = x
            if m["key"] in CYCLE_METRICS and v.get("cycle") and not v["cycle"].get("end"):
                partial.add(d)  # день ещё идёт — нагрузка и шаги растут
            if m["key"] == "sleep_hours":
                extra[d] = sleep_need(v.get("sleep_raw"))

    points = []
    for i in range(n):
        d = (since_d + datetime.timedelta(days=i)).isoformat()
        p = {"day": d, "v": rnd(vals.get(d), m["dec"])}
        if d in partial:
            p["partial"] = True
        if extra.get(d) is not None:
            p["need"] = extra[d]
        points.append(p)

    def window(a, b):
        return [x for d, x in vals.items() if a <= d <= b and d not in partial]
    cur = window(since, until)
    prev = window(prev_since.isoformat(), prev_until.isoformat())
    avg = mean(cur)
    prev_avg = mean(prev) if len(prev) >= max(2, min(n // 2, 15)) and cur else None
    d, pct = change(avg, prev_avg, m["absdiff"])
    typ = typical(window((until_d - datetime.timedelta(days=TYPICAL_DAYS - 1)).isoformat(), until))
    dec = m["dec"]
    text = phrase(m, period, avg, prev_avg, d, pct)
    if m["key"] == "sleep_hours" and avg is not None:
        need = mean(x for dd, x in extra.items() if since <= dd <= until)
        if need:
            text += f" Нужно было в среднем {fval(m, need)} — ты спал {round(avg / need * 100)}% от нужного."
    if m["key"] == "weight" and not vals:
        now = weight_now(app, uid)
        text = ("Вес по дням пока не записывается: WHOOP отдаёт только текущий"
                + (f" ({fnum(now, 1)}{NB}кг)" if now else "") + ". Записывай вес — здесь появится график.")
    first = app.db.first_day(uid)
    return {
        "metric": m["key"], "title": m["title"], "unit": m["unit"], "group": m["group"], "note": m["note"],
        "chart": m["chart"], "dec": dec, "good": m["good"], "top": m["top"], "absdiff": m["absdiff"],
        "period": period, "since": since, "until": until, "label": span(since, until),
        "prev_label": span(prev_since.isoformat(), prev_until.isoformat()), "vs": VS_WORD[period],
        "points": points, "days": len(cur),
        "avg": rnd(avg, dec if dec else (1 if m["unit"] == "ч" else 0)) if avg is not None else None,
        "prev_avg": rnd(prev_avg, dec if dec else 0) if prev_avg is not None else None,
        "change": rnd(d, max(dec, 1)) if d is not None else None,
        "change_pct": round(pct) if pct is not None else None,
        "typical": [rnd(typ[0], dec), rnd(typ[1], dec)] if typ else None,
        "text": text, "first": first, "today": today.isoformat(),
        "can_back": bool(first and first < since), "can_next": until_d < today,
    }


def route(app, uid, path, b, today):
    if path != "/api/trends":
        return None
    if not b.get("metric"):
        return groups()
    return trend(app, uid, today, b.get("metric"), str(b.get("period") or "m"), b.get("end") or None)
