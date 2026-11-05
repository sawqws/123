"""Анализы крови: значения по дням, ориентир нормы, флаги «ниже / выше нормы», динамика с прошлой сдачи.

Подключение — FEATURES в coach.py (ключ "labs"): route / extra / context_lines.
  POST /api/labs {}                          → payload (см. payload())
  POST /api/labs/save {day, values}          → проверка → payload + saved. values — {key: число} или
                                               [{key, value, lo?, hi?}] (lo/hi — границы с бланка лаборатории, главнее наших)
  POST /api/labs/delete {day, key?}          → payload (key — одно значение, без key — вся сдача за день)
Хранение: meta labs:<uid> = {"ГГГГ-ММ-ДД": {key: число | {"v": число, "lo"?: число, "hi"?: число}}}.
Хранится как ввели (в единицах бланка); перевод в наши единицы — при чтении (to_std: г/дл → г/л, мг/дл → ммоль/л…),
поэтому старые записи не переписываются. Неизвестные ключи и дни не трогаем: при записи они остаются как были.
Нормы — общий ориентир для подростка (не по конкретной лаборатории): у бланка свои границы — они главнее (lo/hi).
Это не диагноз: тренер не назначает добавок, лекарств и диет — с этим к врачу (см. RULE).
"""
import datetime
import json
import math
import re

# key: (подпись, единица, низ нормы | None, верх нормы | None, знаков, (мин, макс) правдоподобного, группа, что это)
KNOWN = {
    "hemoglobin": ("Гемоглобин", "г/л", 120, 160, 0, (40, 250), "blood", "Переносит кислород. Мало — быстрее устаёшь."),
    "iron": ("Железо", "мкмоль/л", 11, 30, 1, (1, 80), "iron", "Железо в крови сейчас — скачет за день, смотреть вместе с ферритином."),
    "ferritin": ("Ферритин", "нг/мл", 20, 150, 0, (1, 2000), "iron", "Запас железа в организме."),
    "tsat": ("Насыщение трансферрина", "%", 20, 50, 0, (1, 100), "iron", "Сколько переносчика железа заполнено."),
    "protein": ("Общий белок", "г/л", 60, 80, 0, (30, 120), "protein", "Белки крови — питание и восстановление."),
    "albumin": ("Альбумин", "г/л", 35, 52, 0, (15, 70), "protein", "Главный белок крови."),
    "cholesterol": ("Холестерин", "ммоль/л", 3.0, 5.2, 1, (1, 15), "lipids", "Общий холестерин."),
    "ldl": ("ЛПНП", "ммоль/л", None, 3.35, 2, (0.2, 10), "lipids", "«Плохой» холестерин: чем ниже, тем лучше."),
    "hdl": ("ЛПВП", "ммоль/л", 1.0, None, 2, (0.2, 5), "lipids", "«Хороший» холестерин: растёт от движения."),
    "glucose": ("Глюкоза", "ммоль/л", 3.3, 5.6, 1, (1, 30), "sugar", "Сахар крови натощак."),
    "magnesium": ("Магний", "ммоль/л", 0.7, 1.05, 2, (0.2, 3), "minerals", "Мышцы, нервы, сон."),
    "vitd": ("Витамин D", "нг/мл", 30, 100, 0, (2, 200), "vitamins", "25-OH витамин D: кости, мышцы, иммунитет."),
    "b12": ("Витамин B12", "пг/мл", 190, 900, 0, (20, 3000), "vitamins", "Кровь и нервы."),
    "folate": ("Фолиевая кислота", "нг/мл", 3, 20, 1, (0.3, 60), "vitamins", "Витамин B9: кровь и рост."),
    "tsh": ("ТТГ", "мЕд/л", 0.5, 4.5, 2, (0.005, 100), "hormones", "Гормон, который управляет щитовидкой."),
    "crp": ("С-реактивный белок", "мг/л", None, 5, 1, (0, 300), "inflammation", "Признак воспаления: выше после болезни или травмы."),
}
ORDER = list(KNOWN)
GROUPS = {"blood": "Кровь", "iron": "Обмен железа", "protein": "Белок", "lipids": "Холестерин", "sugar": "Сахар",
          "minerals": "Минералы", "vitamins": "Витамины", "hormones": "Гормоны", "inflammation": "Воспаление"}
# другие единицы на бланках: (условие по сырому числу, множитель в наши единицы, как назвать)
ALT = {
    "hemoglobin": (lambda x: x < 30, 10, "г/дл"),
    "protein": (lambda x: x < 15, 10, "г/дл"),
    "albumin": (lambda x: x < 10, 10, "г/дл"),
    "iron": (lambda x: x > 60, 0.179, "мкг/дл"),
    "cholesterol": (lambda x: x > 20, 1 / 38.67, "мг/дл"),
    "ldl": (lambda x: x > 15, 1 / 38.67, "мг/дл"),
    "hdl": (lambda x: x > 8, 1 / 38.67, "мг/дл"),
    "glucose": (lambda x: x > 35, 1 / 18.02, "мг/дл"),
    "magnesium": (lambda x: x > 1.5, 0.4114, "мг/дл"),
    "vitd": (lambda x: x > 160, 1 / 2.496, "нмоль/л"),
    "tsat": (lambda x: x <= 1, 100, "доля"),
}
DISCLAIMER = ("Это не диагноз. Нормы — общий ориентир для подростка, у лаборатории свои — смотри бланк. "
              "Что-то вне нормы — обсуди с врачом.")
RULE = ("  (анализы — не диагноз: не называй болезней и не пугай. Добавки, витамины, лекарства и любые жалобы — только "
        "к врачу (педиатру), сам их не назначай и дозы не называй. {diet} Можно: обычная разнообразная еда, сон, режим, "
        "вода; вне нормы — «покажи врачу».)")
DAY_RX = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_DAYS = 200      # сдач в хранилище
MAX_VALUES = 40     # значений в одном сохранении
BACK_DAYS = 10 * 366
SAME = 0.03         # изменение меньше 3 % — «без изменений»


# ---------- числа ----------

def _fmt(x, digits=1):
    s = f"{x:.{digits}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s.replace(".", ",")


def _float(v):
    """Число из числа или строки («5,4», «1 650», «< 0,5» → 0,5); не число — None. bool — не число."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        x = float(v)
    else:
        s = str(v).strip().replace("−", "-").replace(",", ".").replace(" ", "").replace(" ", "")
        s = re.sub(r"^[<>≤≥]=?", "", s)
        try:
            x = float(s)
        except ValueError:
            return None
    return x if math.isfinite(x) else None


def to_std(key, raw):
    """Сырое число с бланка → (число в наших единицах, единица бланка | ""). 14,8 г/дл → 148 г/л."""
    alt = ALT.get(key)
    if alt and alt[0](raw):
        return raw * alt[1], alt[2]
    return raw, ""


def _rnd(key, x):
    d = KNOWN[key][4]
    return round(x) if d == 0 else round(x, d)


def flag_of(v, lo, hi):
    if lo is not None and v < lo:
        return "low"
    if hi is not None and v > hi:
        return "high"
    return "ok"


def _dist(v, lo, hi):
    """Насколько вне нормы, в долях границы (0 — в норме)."""
    if lo is not None and v < lo:
        return (lo - v) / (abs(lo) or 1)
    if hi is not None and v > hi:
        return (v - hi) / (abs(hi) or 1)
    return 0.0


# ---------- хранение ----------

def _key(uid):
    return f"labs:{uid}"


def load_raw(db, uid):
    """Как лежит в meta: {день: {key: значение}}. Сломанное — {}."""
    try:
        raw = json.loads(db.meta(_key(uid)) or "{}")
    except ValueError:
        return {}
    return {d: v for d, v in raw.items() if isinstance(v, dict)} if isinstance(raw, dict) else {}


def _store(db, uid, raw):
    days = sorted(raw)
    raw = {d: raw[d] for d in days[-MAX_DAYS:] if raw[d]}
    db.meta(_key(uid), json.dumps(raw, ensure_ascii=False, sort_keys=True))
    return raw


def _cell(key, val):
    """Одно хранимое значение → {v, lo, hi, raw, unit_in} в наших единицах; не разобрать — None."""
    lo_lab = hi_lab = None
    if isinstance(val, dict):
        x = _float(val.get("v"))
        lo_lab, hi_lab = _float(val.get("lo")), _float(val.get("hi"))
    else:
        x = _float(val)
    if x is None or key not in KNOWN:
        return None
    v, unit_in = to_std(key, x)
    k = KNOWN[key]
    lo, hi = k[2], k[3]
    if lo_lab is not None or hi_lab is not None:  # границы с бланка — в тех же единицах, что и число
        f = v / x if x else 1
        lo = lo_lab * f if lo_lab is not None else None
        hi = hi_lab * f if hi_lab is not None else None
    return {"v": _rnd(key, v), "lo": lo, "hi": hi, "raw": x, "unit_in": unit_in, "lab": lo_lab is not None or hi_lab is not None}


def rows(raw):
    """{день: {key: значение}} → список {day, key, v, lo, hi, flag, …} по возрастанию дня, только известное."""
    out = []
    for day in sorted(raw):
        if not DAY_RX.match(day):
            continue
        for key in ORDER:
            if key not in raw[day]:
                continue
            c = _cell(key, raw[day][key])
            if c:
                out.append({"day": day, "key": key, **c, "flag": flag_of(c["v"], c["lo"], c["hi"])})
    return out


# ---------- проверка ----------

def _day(v, today):
    s = str(v if v is not None else "").strip()[:10]
    if not s:
        return today.isoformat()
    if not DAY_RX.match(s):
        raise ValueError("Дата: в виде ГГГГ-ММ-ДД")
    try:
        d = datetime.date.fromisoformat(s)
    except ValueError:
        raise ValueError("Дата: такого дня нет") from None
    if d > today:
        raise ValueError("Дата: анализ не может быть из будущего")
    if (today - d).days > BACK_DAYS:
        raise ValueError("Дата: не раньше чем 10 лет назад")
    return d.isoformat()


def clean_value(key, value, lo=None, hi=None):
    """Одно значение из запроса → что положить в хранилище (число или {v, lo, hi}). Неверное — ValueError."""
    key = str(key or "").strip().lower()
    if key not in KNOWN:
        raise ValueError("Показатель: неизвестный — обнови приложение")
    k = KNOWN[key]
    x = _float(value)
    if x is None:
        raise ValueError(f"{k[0]}: нужно число")
    std, _ = to_std(key, x)
    pmin, pmax = k[5]
    if not pmin <= std <= pmax:
        raise ValueError(f"{k[0]}: проверь число — бывает от {_fmt(pmin, 3)} до {_fmt(pmax, 3)} {k[1]}")
    x = int(x) if x == int(x) and abs(x) < 1e9 else round(x, 4)
    lo, hi = _float(lo), _float(hi)
    if lo is None and hi is None:
        return key, x
    if lo is not None and hi is not None and lo >= hi:
        raise ValueError(f"{k[0]}: нижняя граница нормы должна быть меньше верхней")
    out = {"v": x}
    if lo is not None:
        out["lo"] = lo
    if hi is not None:
        out["hi"] = hi
    return key, out


def _values(b):
    vals = b.get("values")
    if isinstance(vals, dict):
        items = [{"key": k, "value": v} for k, v in vals.items()]
    elif isinstance(vals, list):
        items = [x for x in vals if isinstance(x, dict)]
    else:
        items = []
    if not items:
        raise ValueError("Нет значений — впиши хотя бы одно")
    if len(items) > MAX_VALUES:
        raise ValueError(f"Слишком много значений за раз — не больше {MAX_VALUES}")
    return items


def save(db, uid, b, today):
    """{day, values} → сохранённые ключи. Всё проверяется до записи: одно неверное — ничего не пишем.
    Тот же день — значения дописываются и заменяют прежние по тем же ключам; остальное за день не трогаем."""
    b = b if isinstance(b, dict) else {}
    day = _day(b.get("day"), today)
    clean = dict(clean_value(x.get("key"), x.get("value"), x.get("lo"), x.get("hi")) for x in _values(b))
    raw = load_raw(db, uid)
    raw[day] = {**raw.get(day, {}), **clean}
    _store(db, uid, raw)
    return {"day": day, "keys": [k for k in ORDER if k in clean]}


def delete(db, uid, b):
    b = b if isinstance(b, dict) else {}
    day = str(b.get("day") or "")
    raw = load_raw(db, uid)
    if day not in raw:
        raise ValueError("Сдача не найдена — обнови экран")
    key = str(b.get("key") or "")
    if key:
        if key not in raw[day]:
            raise ValueError("Значение не найдено — обнови экран")
        raw[day] = {k: v for k, v in raw[day].items() if k != key}
    else:
        raw.pop(day)
    _store(db, uid, raw)


# ---------- ответы ----------

def trend_of(prev, cur):
    """Как изменилось: up / down / same и лучше ли (ближе к норме) — better / worse / ""."""
    a, b = prev["v"], cur["v"]
    base = abs(a) or 1
    trend = "same" if abs(b - a) / base < SAME else "up" if b > a else "down"
    da, db_ = _dist(a, cur["lo"], cur["hi"]), _dist(b, cur["lo"], cur["hi"])
    better = "" if trend == "same" or (da == 0 and db_ == 0) else "better" if db_ < da else "worse" if db_ > da else ""
    return trend, better


def latest_of(rs):
    """По одной строке на показатель (самая свежая) + прошлая сдача и динамика, в порядке ORDER."""
    by = {}
    for r in rs:  # по возрастанию дня
        by.setdefault(r["key"], []).append(r)
    out = []
    for key in ORDER:
        if key not in by:
            continue
        cur, prev = by[key][-1], (by[key][-2] if len(by[key]) > 1 else None)
        k = KNOWN[key]
        row = {"key": key, "label": k[0], "unit": k[1], "group": k[6], "v": cur["v"], "day": cur["day"],
               "lo": _norm_bound(key, cur["lo"]), "hi": _norm_bound(key, cur["hi"]), "flag": cur["flag"], "lab": cur["lab"],
               "unit_in": cur["unit_in"], "prev": None, "delta": None, "trend": "", "better": "", "n": len(by[key])}
        if prev:
            trend, better = trend_of(prev, cur)
            row.update(prev={"v": prev["v"], "day": prev["day"]}, delta=_rnd(key, cur["v"] - prev["v"]),
                       trend=trend, better=better)
        out.append(row)
    return out


def _norm_bound(key, x):
    return None if x is None else _rnd(key, x) if KNOWN[key][4] else round(x, 1)


def payload(db, uid, today=None):
    rs = rows(load_raw(db, uid))
    latest = latest_of(rs)
    days = {}
    for r in rs:
        days.setdefault(r["day"], []).append({"key": r["key"], "v": r["v"], "flag": r["flag"]})
    series = {}
    for r in rs:
        series.setdefault(r["key"], []).append({"day": r["day"], "v": r["v"], "flag": r["flag"]})
    return {"latest": latest, "series": series,
            "days": [{"day": d, "values": days[d]} for d in sorted(days, reverse=True)],
            "count": len(days), "summary": summary(latest),
            "known": {k: {"label": v[0], "unit": v[1], "lo": v[2], "hi": v[3], "digits": v[4], "group": v[6], "about": v[7],
                          "min": v[5][0], "max": v[5][1]} for k, v in KNOWN.items()},
            "groups": GROUPS, "disclaimer": DISCLAIMER}


def summary(latest):
    return {"total": len(latest), "ok": sum(1 for x in latest if x["flag"] == "ok"),
            "low": [x["key"] for x in latest if x["flag"] == "low"], "high": [x["key"] for x in latest if x["flag"] == "high"],
            "day": max((x["day"] for x in latest), default="")}


# ---------- подключение ----------

def route(app, uid, path, b, today):
    if path != "/api/labs" and not path.startswith("/api/labs/"):
        return None
    db = app.db
    b = b if isinstance(b, dict) else {}
    if path == "/api/labs":
        return payload(db, uid, today)
    if path == "/api/labs/save":
        saved = save(db, uid, b, today)
        return {**payload(db, uid, today), "saved": saved}
    if path == "/api/labs/delete":
        delete(db, uid, b)
        return payload(db, uid, today)
    return None


EXTRA_KEYS = ("key", "label", "unit", "v", "day", "flag", "lo", "hi", "trend", "better")


def extra(app, uid, today):
    """В /api/all (ext.labs): последние значения — карточке «Анализы» во «Здоровье»."""
    latest = latest_of(rows(load_raw(app.db, uid)))
    return {"latest": [{k: x[k] for k in EXTRA_KEYS} for x in latest], "summary": summary(latest)}


def _dmy(day):
    d = datetime.date.fromisoformat(day)
    return f"{d.day:02d}.{d.month:02d}.{d.year}"


def _norm_text(x):
    lo, hi, d = x["lo"], x["hi"], max(KNOWN[x["key"]][4], 1)
    if lo is not None and hi is not None:
        return f"{_fmt(lo, d)}–{_fmt(hi, d)}"
    return f"от {_fmt(lo, d)}" if lo is not None else f"до {_fmt(hi, d)}"


def _diet(app, uid):
    try:
        age = int(float(app.profile(uid).get("age")))
    except (TypeError, ValueError, AttributeError, KeyError):
        age = None
    if age is not None and age < 18:
        return f"Диеты в {age} лет не назначай (ни «без углеводов», ни голодания, ни урезания калорий)."
    return "Диет не назначай."


def context_lines(app, uid, today):
    latest = latest_of(rows(load_raw(app.db, uid)))
    if not latest:
        return []
    main = max(x["day"] for x in latest)
    word = {"low": "НИЖЕ НОРМЫ", "high": "ВЫШЕ НОРМЫ", "ok": "норма"}
    parts = []
    for x in latest:
        d = KNOWN[x["key"]][4]
        s = f"{x['label'].lower() if x['key'] not in ('ldl', 'hdl', 'tsh') else x['label']} {_fmt(x['v'], d)} {x['unit']}"
        s += f" (норма {_norm_text(x)}{', по бланку' if x['lab'] else ''}) — {word[x['flag']]}"
        if x["day"] != main:
            s += f", сдано {_dmy(x['day'])}"
        if x["prev"]:
            how = {"better": ", ближе к норме", "worse": ", дальше от нормы"}.get(x["better"], "")
            s += f"; было {_fmt(x['prev']['v'], d)} ({_dmy(x['prev']['day'])}){how}" if x["trend"] != "same" else \
                f"; как в прошлый раз ({_dmy(x['prev']['day'])})"
        parts.append(s)
    out = [f"АНАЛИЗЫ КРОВИ (последние, сдано {_dmy(main)}; норма — ориентир для подростка): " + " · ".join(parts)]
    out.append(RULE.replace("{diet}", _diet(app, uid)))
    return out
