"""Вес и состав тела: замеры с умных весов (вручную, скрином или текстом), изменения, тренд 30/90 дней.

Подключение — FEATURES в coach.py: route / extra / context_lines.
  POST /api/weight {}                  → payload: {items (новые сверху, ≤ 400), last, prev, change, trend, count, fields}
  POST /api/weight/save {…замер}       → новый замер; с id — правка этого замера (меняются присланные поля, "" / null — убрать)
  POST /api/weight/delete {id}         → payload
  POST /api/weight/photo {image}       → скрин приложения весов (JPEG/PNG base64 или data:URL) → Claude → предпросмотр
                                          {item, found, skipped, how}; НЕ сохраняет (сохраняет клиент через /save)
  POST /api/weight/text {text}         → то же из текста («вес 63,4 жир 14,2% мышцы 50,1 вода 58%»): свой разбор,
                                          вес не нашёлся — Claude
Замер: {id, day, time, kg, fat_pct, muscle_kg, lean_kg (сухая масса), water_pct, bone_kg, visceral (уровень), bmr (ккал),
bmi, protein_pct, note, src: manual|photo|text}; обязателен только kg (25–200), остальное — по желанию, в своих границах.
Хранение: meta weight:<uid> — список замеров (по возрастанию дня и времени), не больше MAX_ITEMS (старые уходят).

Свежий вес — в расчёты: whoop.body() добавляет body["scale"] (последний замер, если он не старше FRESH_DAYS), а
insights.weight_of() берёт его раньше веса из профиля и WHOOP (там вес введён руками и без даты, а это — взвешивание).
Для экрана «Тренды» — series(db, uid, since, until): по строке на день с замером.
"""
import datetime
import json
import os
import re
import secrets

MAX_ITEMS = 400
MAX_NOTE = 120
FRESH_DAYS = 60  # замер старше — нормы снова считаются по профилю / WHOOP
BACK_DAYS = 3 * 366  # задним числом — не дальше
SRC = ("manual", "photo", "text")
# поле: (мин, макс, знаков после запятой, подпись, единица)
FIELDS = {
    "kg": (25, 200, 2, "Вес", "кг"),
    "fat_pct": (2, 60, 1, "Жир", "%"),
    "muscle_kg": (10, 150, 1, "Мышцы", "кг"),
    "lean_kg": (15, 180, 1, "Сухая масса", "кг"),
    "water_pct": (30, 80, 1, "Вода", "%"),
    "bone_kg": (0.5, 10, 2, "Кости", "кг"),
    "visceral": (1, 30, 1, "Висцеральный жир", ""),
    "bmr": (700, 4500, 0, "Обмен веществ", "ккал"),
    "bmi": (10, 60, 1, "ИМТ", ""),
    "protein_pct": (5, 35, 1, "Белок", "%"),
}
OPTIONAL = [k for k in FIELDS if k != "kg"]
DAY_RX = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RX = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


# ---------- проверка ----------

def _num(v, key):
    """Число поля в своих границах; пусто — None; не число или вне границ — ValueError с понятным текстом."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if isinstance(v, bool):
        raise ValueError(f"{FIELDS[key][3]}: нужно число")
    lo, hi, digits, word, unit = FIELDS[key]
    try:
        x = float(str(v).strip().replace(",", ".").replace(" ", "").replace(" ", ""))
    except ValueError:
        raise ValueError(f"{word}: нужно число, например {str(lo + (hi - lo) / 4).replace('.', ',')}") from None
    if x != x or not lo <= x <= hi:  # NaN тоже сюда
        tail = f" {unit}" if unit else ""
        raise ValueError(f"{word}: от {_fmt(lo)} до {_fmt(hi)}{tail}")
    return round(x) if digits == 0 else round(x, digits)


def _fmt(x, digits=1):
    s = f"{x:.{digits}f}".rstrip("0").rstrip(".") if isinstance(x, float) else str(x)
    return s.replace(".", ",")


def _day(v, today):
    """today=None — своё, уже сохранённое: только формат, без сверки с «сегодня»."""
    if v in (None, "") and today is not None:
        return today.isoformat()
    s = str(v).strip()[:10]
    if not DAY_RX.match(s):
        raise ValueError("Дата: в виде ГГГГ-ММ-ДД")
    try:
        d = datetime.date.fromisoformat(s)
    except ValueError:
        raise ValueError("Дата: такого дня нет") from None
    if today is None:
        return d.isoformat()
    if d > today:
        raise ValueError("Дата: замер не может быть в будущем")
    if (today - d).days > BACK_DAYS:
        raise ValueError("Дата: не раньше чем 3 года назад")
    return d.isoformat()


def _time(v):
    if v in (None, ""):
        return ""
    m = TIME_RX.match(str(v).strip()[:5])
    if not m:
        raise ValueError("Время: в виде ЧЧ:ММ")
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def clean(src, today, now_time=""):
    """Замер из запроса → проверенный dict (без id). Неверное — ValueError."""
    if not isinstance(src, dict):
        raise ValueError("Замер: нужен объект")
    out = {"day": _day(src.get("day"), today)}
    t = _time(src.get("time"))
    out["time"] = t or (now_time if today is not None and out["day"] == today.isoformat() else "")
    kg = _num(src.get("kg"), "kg")
    if kg is None:
        raise ValueError("Вес: обязателен — например 63,4")
    out["kg"] = kg
    for k in OPTIONAL:
        out[k] = _num(src.get(k), k)
    _cross(out)
    note = " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", str(src.get("note") or "")).split())
    if len(note) > MAX_NOTE:
        raise ValueError(f"Заметка: не длиннее {MAX_NOTE} символов")
    out["note"] = note
    s = str(src.get("src") or "manual")
    out["src"] = s if s in SRC else "manual"
    return out


def _cross(m):
    kg = m["kg"]
    for k in ("muscle_kg", "lean_kg", "bone_kg"):
        if m.get(k) is not None and m[k] >= kg:
            raise ValueError(f"{FIELDS[k][3]}: должно быть меньше веса ({_fmt(kg)} кг)")


# ---------- хранение ----------

def _key(uid):
    return f"weight:{uid}"


def _sort_key(m):
    return (m["day"], m.get("time") or "")  # при равных — порядок добавления (сортировка устойчивая)


def load(db, uid):
    try:
        raw = json.loads(db.meta(_key(uid)) or "[]")
    except ValueError:
        raw = []
    out = []
    for x in raw if isinstance(raw, list) else []:
        if not isinstance(x, dict) or not x.get("id"):
            continue
        try:
            m = clean(x, None)  # своё, уже проверенное: дату не сверяем с «сегодня»
        except ValueError:
            continue
        m["id"] = str(x["id"])[:16]
        m["time"] = _time(x.get("time")) if x.get("time") else ""
        out.append(m)
    out.sort(key=_sort_key)
    return out


def store(db, uid, items):
    items = sorted(items, key=_sort_key)[-MAX_ITEMS:]
    db.meta(_key(uid), json.dumps(items, ensure_ascii=False))
    return items


def save(db, uid, b, today, now_time=""):
    """Новый замер или правка по id (меняются только присланные поля). → сохранённый замер."""
    items = load(db, uid)
    mid = str(b.get("id") or "")
    if mid:
        cur = next((x for x in items if x["id"] == mid), None)
        if not cur:
            raise ValueError("Замер не найден — обнови экран")
        merged = {**cur, **{k: v for k, v in b.items() if k in ("day", "time", "note", "src", *FIELDS)}}
        m = clean(merged, today)
        m["id"] = mid
        items = [m if x["id"] == mid else x for x in items]
    else:
        m = clean(b, today, now_time)
        m["id"] = secrets.token_hex(4)
        items.append(m)
    store(db, uid, items)
    return m


def delete(db, uid, mid):
    items = load(db, uid)
    left = [x for x in items if x["id"] != str(mid or "")]
    if len(left) == len(items):
        raise ValueError("Замер не найден — обнови экран")
    store(db, uid, left)


# ---------- расчёт ----------

def daily(items):
    """По замеру на день — самый ранний (утренний натощак сравним с утренним). Возрастание по дню."""
    out = {}
    for m in items:
        if m["day"] not in out:
            out[m["day"]] = m
    return [out[d] for d in sorted(out)]


def _lean(m):
    if m.get("lean_kg") is not None:
        return m["lean_kg"], False
    if m.get("fat_pct") is not None:
        return round(m["kg"] * (1 - m["fat_pct"] / 100), 1), True
    return None, False


def _diff(a, b, days):
    out = {"days": days}
    for k in ("kg", "fat_pct", "muscle_kg", "lean_kg"):
        out[k] = round(a[k] - b[k], 2) if a.get(k) is not None and b.get(k) is not None else None
    return out


def change(items):
    """К прошлому замеру и за 30 дней (к первому замеру в последние 30 дней до последнего)."""
    if len(items) < 2:
        return {"prev": None, "d30": None}
    last, prev = items[-1], items[-2]
    dl = datetime.date.fromisoformat(last["day"])
    days = lambda m: (dl - datetime.date.fromisoformat(m["day"])).days  # noqa: E731
    since = (dl - datetime.timedelta(days=30)).isoformat()
    ref = next((m for m in items[:-1] if m["day"] >= since and m["day"] < last["day"]), None)
    return {"prev": _diff(last, prev, days(prev)), "d30": _diff(last, ref, days(ref)) if ref else None}


def trend(items, today, n):
    """Тренд за n дней по утренним замерам: наклон (кг в неделю), разница первый → последний, мин/макс/среднее."""
    since = (today - datetime.timedelta(days=n - 1)).isoformat()
    rows = [m for m in daily(items) if since <= m["day"] <= today.isoformat()]
    if len(rows) < 2:
        return {"n": len(rows), "days": n}
    xs = [(datetime.date.fromisoformat(m["day"]) - today).days for m in rows]
    ys = [m["kg"] for m in rows]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    den = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den if den else 0.0
    per_week = round(slope * 7, 2)
    word = "держится ровно" if abs(per_week) < 0.2 else "растёт" if per_week > 0 else "снижается"
    return {"n": len(rows), "days": n, "span": xs[-1] - xs[0], "first": ys[0], "last": ys[-1],
            "delta": round(ys[-1] - ys[0], 2), "per_week": per_week, "word": word,
            "min": min(ys), "max": max(ys), "avg": round(my, 2)}


def payload(db, uid, today):
    items = load(db, uid)
    return {"items": list(reversed(items)), "count": len(items), "last": items[-1] if items else None,
            "prev": items[-2] if len(items) > 1 else None, "change": change(items),
            "trend": {"d30": trend(items, today, 30), "d90": trend(items, today, 90)},
            "fields": {k: {"min": v[0], "max": v[1], "label": v[3], "unit": v[4]} for k, v in FIELDS.items()},
            "today": today.isoformat()}


def series(db, uid, since, until):
    """Для «Трендов» и графиков: по строке на день с замером (самый ранний за день), по возрастанию.
    → [{day, time, kg, fat, muscle, lean, lean_est, water, bone, visceral, bmr, bmi, protein, n}]; lean_est — сухая масса
    посчитана из веса и % жира (весы её не дали). since / until — date или "ГГГГ-ММ-ДД" (включительно)."""
    a = since.isoformat() if hasattr(since, "isoformat") else str(since)
    z = until.isoformat() if hasattr(until, "isoformat") else str(until)
    items = load(db, uid)
    per_day = {}
    for m in items:
        per_day[m["day"]] = per_day.get(m["day"], 0) + 1
    out = []
    for m in daily(items):
        if not a <= m["day"] <= z:
            continue
        lean, est = _lean(m)
        out.append({"day": m["day"], "time": m["time"], "kg": m["kg"], "fat": m["fat_pct"], "muscle": m["muscle_kg"],
                    "lean": lean, "lean_est": est, "water": m["water_pct"], "bone": m["bone_kg"], "visceral": m["visceral"],
                    "bmr": m["bmr"], "bmi": m["bmi"], "protein": m["protein_pct"], "n": per_day[m["day"]]})
    return out


def latest(db, uid):
    items = load(db, uid)
    return items[-1] if items else None


def _today():  # тесты подменяют
    return datetime.date.today()


def with_scale(db, uid, body, today=None):
    """Тело WHOOP + свежий замер весов: body["scale"] = {kg, day, time, fat_pct, muscle_kg, lean_kg}.
    Замер старше FRESH_DAYS — не добавляем (нормы снова по профилю / WHOOP)."""
    body = dict(body) if isinstance(body, dict) else {}
    m = latest(db, uid)
    if not m:
        return body
    today = today or _today()
    age = (today - datetime.date.fromisoformat(m["day"])).days
    if age > FRESH_DAYS or age < -1:  # -1: у пользователя в часовом поясе уже завтра
        return body
    body["scale"] = {k: m[k] for k in ("kg", "day", "time", "fat_pct", "muscle_kg", "lean_kg")}
    return body


# ---------- разбор текста: «вес 63,4 жир 14,2% мышцы 50,1 вода 58%» ----------

NUM_RX = re.compile(r"(\d+(?:[.,]\d+)?)\s*(%|кг\b|kg\b|ккал\b|kcal\b|кал\b|cal\b|lbs?\b|фунт\w*)?", re.I)
IGNORE = re.compile(r"возраст|\bage\b|рост|height|пульс|heart|балл|score|подкож|subcut|идеал|ideal|цель|target|контрол|control|"
                    r"станд|standard|норм|оценк|шаг|\bsteps?\b")
KINDS = [  # порядок важен: «висцеральный жир» и «безжировая масса» — раньше «жира»
    ("visceral", ("висцер", "visceral")),
    ("bmr", ("bmr", "обмен", "метабол", "базал", "basal")),
    ("bmi", ("имт", "bmi", "индекс массы")),
    ("lean", ("сух", "безжир", "без жира", "обезжир", "fat-free", "fat free", "lean", "ffm")),
    ("fat", ("жир", "fat")),
    ("muscle_skel", ("скелет", "skeletal")),
    ("muscle", ("мышц", "мышеч", "muscle")),
    ("water", ("вод", "water", "жидк")),
    ("bone", ("кост", "bone")),
    ("protein", ("белок", "белк", "протеин", "protein")),
    ("kg", ("вес", "weight", "масса тела", "body mass", "масса")),
]
LB = 0.45359237


def _kind(label):
    s = label.lower().replace("ё", "е")
    chunks = [c for c in re.split(r"[\n;|]", s) if re.search(r"[a-zа-я]", c)]
    s = chunks[-1] if chunks else s  # подпись — в своей строке, ближняя к числу
    for kind, words in KINDS:
        if any(w in s for w in words):
            if kind != "visceral" and IGNORE.search(s):
                return None
            return kind
    return None


def parse_text(text):
    """Свой разбор без Claude → ({поле: значение (сырое число)}, [не понял / вне границ]). Вес и проценты в кг/% —
    пересчитываются (жир 9 кг → % от веса, фунты → кг)."""
    s = str(text or "")[:2000]
    s = re.sub(r"(\d)[   ](\d{3})(?=\s*(?:ккал|kcal|кал|cal))", r"\1\2", s, flags=re.I)  # «1 650 ккал»
    raw, pos, first = {}, 0, True
    nums = list(NUM_RX.finditer(s))
    for m in nums:
        label, pos = s[pos:m.start()], m.end()
        v = float(m.group(1).replace(",", "."))
        unit = (m.group(2) or "").lower()
        kind = _kind(label)
        if unit.startswith(("lb", "фунт")):
            v, unit = v * LB, "кг"
        if kind is None and first and (unit in ("кг", "kg") or len(nums) == 1) and 25 <= v <= 200:
            kind = "kg"  # «63,4 кг, жир 14%» или просто «63,4»
        first = False
        if kind is None:
            continue
        if kind in ("fat", "water", "protein", "muscle") and unit in ("кг", "kg"):
            kind += "@kg"
        elif kind in ("muscle", "muscle_skel") and unit == "%":
            kind += "@pct"
        raw.setdefault(kind, v)
    return _assemble(raw)


def _assemble(raw):
    out, bad = {}, []
    kg = raw.get("kg")
    if kg is not None:
        out["kg"] = kg
    conv = {"fat@kg": "fat_pct", "water@kg": "water_pct", "protein@kg": "protein_pct"}
    simple = {"fat": "fat_pct", "water": "water_pct", "protein": "protein_pct", "muscle": "muscle_kg", "lean": "lean_kg",
              "bone": "bone_kg", "visceral": "visceral", "bmr": "bmr", "bmi": "bmi"}
    for k, field in simple.items():
        if k in raw:
            out.setdefault(field, raw[k])
    for k, field in conv.items():
        if k in raw and field not in out and kg:
            out[field] = raw[k] / kg * 100
    if "muscle_kg" not in out:
        for k in ("muscle@kg", "muscle_skel", "muscle_skel@kg"):
            if k in raw:
                out["muscle_kg"] = raw[k]
                break
        else:
            for k in ("muscle@pct", "muscle_skel@pct"):
                if k in raw and kg:
                    out["muscle_kg"] = raw[k] * kg / 100
                    break
    item = {}
    for k, v in out.items():
        try:
            item[k] = _num(round(v, 3), k)
        except ValueError as e:
            bad.append(str(e))
    if item.get("kg") is not None:
        for k in ("muscle_kg", "lean_kg", "bone_kg"):
            if item.get(k) is not None and item[k] >= item["kg"]:
                bad.append(f"{FIELDS[k][3]}: больше веса — пропустил")
                item.pop(k)
    return item, bad


AI_FIELDS = '{"scale": true, "kg": 63.4, "fat_pct": 14.2, "muscle_kg": 50.1, "lean_kg": 54.4, "water_pct": 58.0, ' \
            '"bone_kg": 2.8, "visceral": 4, "bmr": 1650, "bmi": 20.1, "protein_pct": 18.0, "day": "ГГГГ-ММ-ДД", "time": "ЧЧ:ММ"}'
AI_RULES = (
    "Верни ТОЛЬКО JSON без пояснений вокруг, как в примере (числа — из источника, не из примера):\n" + AI_FIELDS + "\n"
    "Правила:\n"
    "- kg — масса тела; fat_pct — процент жира; muscle_kg — мышечная масса в кг; lean_kg — сухая (безжировая) масса в кг; "
    "water_pct — вода в %; bone_kg — костная масса в кг; visceral — уровень висцерального жира (число); bmr — базальный "
    "обмен (основной обмен, ккал); bmi — ИМТ; protein_pct — белок в %.\n"
    "- Чего нет — null. Ничего не выдумывай и не считай сам, кроме перевода единиц: фунты → кг; жир, вода или белок в кг → "
    "% от веса; мышцы в % → кг.\n"
    "- Если показано несколько замеров (история) — бери самый свежий или выделенный.\n"
    "- day и time — только если дата и время замера видны; год не виден — {year}. Не видно — null.\n"
    "- Оценки и слова вроде «норма», «высокий», «идеальный вес», «метаболический возраст» — не нужны.\n"
    'Если это не весы и не показатели тела — {"scale": false}.')


def from_ai(out, today):
    """Ответ Claude → (item, skipped). Числа — через те же границы, дата — не в будущем и не старше года."""
    item, bad = {}, []
    if not isinstance(out, dict) or out.get("scale") is False:
        return item, bad
    for k in FIELDS:
        v = out.get(k)
        if v in (None, ""):
            continue
        try:
            item[k] = _num(v, k)
        except ValueError as e:
            bad.append(str(e))
    if item.get("kg") is not None:
        for k in ("muscle_kg", "lean_kg", "bone_kg"):
            if item.get(k) is not None and item[k] >= item["kg"]:
                bad.append(f"{FIELDS[k][3]}: больше веса — пропустил")
                item.pop(k)
    try:
        d = _day(out.get("day"), today)
        if (today - datetime.date.fromisoformat(d)).days <= 366:
            item["day"] = d
    except ValueError:
        pass
    try:
        t = _time(out.get("time"))
        if t:
            item["time"] = t
    except ValueError:
        pass
    return item, bad


def _ask(app, prompt, image=None):
    return app.ai.parse_json(app.ai.claude(prompt, image=image, timeout=150))


def text_preview(app, text, today):
    text = str(text or "").strip()
    if not text:
        raise ValueError("Напиши цифры, например: вес 63,4 жир 14,2% мышцы 50,1")
    if len(text) > 2000:
        raise ValueError("Слишком длинный текст — оставь только цифры с весов")
    item, bad = parse_text(text)
    how = "parser"
    if item.get("kg") is None and re.search(r"\d", text) and re.search(r"[A-Za-zА-Яа-яЁё]", text):
        q = f"Текст из приложения умных весов (или как человек записал свои цифры):\n{text}\n\n"
        try:
            ai_item, ai_bad = from_ai(_ask(app, q + AI_RULES.replace("{year}", str(today.year))), today)
        except app.ai.AIError:
            ai_item, ai_bad = {}, []
        if ai_item.get("kg") is not None:
            item, bad, how = ai_item, ai_bad, "claude"
    if item.get("kg") is None:
        raise ValueError("Не нашёл вес — напиши, например: «вес 63,4 жир 14,2% мышцы 50,1»")
    item.setdefault("day", today.isoformat())
    item["src"] = "text"
    return {"item": item, "found": [k for k in FIELDS if item.get(k) is not None], "skipped": bad, "how": how}


PHOTO_ASK = ("На картинке — скрин приложения умных весов (Xiaomi Zepp Life / Mi Fit, Huawei Health, Picooc, Tanita, "
             "Withings, Garmin, Fitdays и др.) или фото экрана весов. Найди показатели последнего замера.\n")


def photo_preview(app, uid, b, today):
    raw, ext = app.decode_photo(b)
    folder = os.path.join(app.DATA, "photos", "weight")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{secrets.token_hex(8)}{ext}")
    try:
        with open(path, "wb") as f:
            f.write(raw)
        out = _ask(app, PHOTO_ASK + AI_RULES.replace("{year}", str(today.year)), image=path)
    finally:
        try:
            os.remove(path)  # скрин нужен только на время ответа
        except OSError:
            pass
    item, bad = from_ai(out, today)
    if item.get("kg") is None:
        raise ValueError("На скрине не нашёл вес — пришли скрин экрана с цифрами или введи вручную")
    item.setdefault("day", today.isoformat())
    item["src"] = "photo"
    return {"item": item, "found": [k for k in FIELDS if item.get(k) is not None], "skipped": bad, "how": "claude"}


# ---------- подключение ----------

def _now_time(app, uid):
    try:
        return app.user_now(app.db.user(uid)).strftime("%H:%M")
    except Exception:  # без часового пояса — без времени
        return ""


def route(app, uid, path, b, today):
    if not path.startswith("/api/weight"):
        return None
    db = app.db
    b = b if isinstance(b, dict) else {}
    if path == "/api/weight":
        return payload(db, uid, today)
    if path == "/api/weight/save":
        m = save(db, uid, b, today, _now_time(app, uid))
        return {**payload(db, uid, today), "saved": m}
    if path == "/api/weight/delete":
        delete(db, uid, b.get("id"))
        return payload(db, uid, today)
    if path == "/api/weight/text":
        return text_preview(app, b.get("text"), today)
    if path == "/api/weight/photo":
        return photo_preview(app, uid, b, today)
    return None


def extra(app, uid, today):
    """В /api/all (ext.weight): последний, прошлый, изменения и точки за 30 дней — карточке на главной."""
    items = load(app.db, uid)
    since = (today - datetime.timedelta(days=29)).isoformat()
    return {"count": len(items), "last": items[-1] if items else None, "prev": items[-2] if len(items) > 1 else None,
            "change": change(items), "spark": [{"day": m["day"], "kg": m["kg"]} for m in daily(items) if m["day"] >= since]}


def _dm(day):
    d = datetime.date.fromisoformat(day)
    return f"{d.day:02d}.{d.month:02d}"


def _signed(x, unit):
    if x is None:
        return ""
    return ("+" if x > 0 else "−" if x < 0 else "±") + _fmt(abs(round(x, 1))) + unit


def context_lines(app, uid, today):
    items = load(app.db, uid)
    if not items:
        return []
    m = items[-1]
    when = f"замер {_dm(m['day'])}" + (f" в {m['time']}" if m["time"] else "")
    if m["time"] and m["time"] < "11:00":
        when += ", утром"
    parts = [f"ВЕС (умные весы, вводит сам): {_fmt(m['kg'])} кг ({when})"]
    lab = [("fat_pct", "жир", " %"), ("muscle_kg", "мышцы", " кг"), ("lean_kg", "сухая масса", " кг"), ("water_pct", "вода", " %"),
           ("bone_kg", "кости", " кг"), ("visceral", "висцеральный жир", ""), ("bmr", "обмен", " ккал"), ("bmi", "ИМТ", ""),
           ("protein_pct", "белок", " %")]
    comp = [f"{w} {_fmt(m[k])}{u}" for k, w, u in lab if m.get(k) is not None]
    if comp:
        parts.append(", ".join(comp))
    ch = change(items)
    if ch["prev"] and ch["prev"]["kg"] is not None:
        parts.append(f"к прошлому замеру ({_dm(items[-2]['day'])}) {_signed(ch['prev']['kg'], ' кг')}")
    if ch["d30"] and ch["d30"]["kg"] is not None and ch["d30"]["days"] >= 7:
        bit = f"за {ch['d30']['days']} дн. {_signed(ch['d30']['kg'], ' кг')}"
        if ch["d30"]["fat_pct"] is not None:
            bit += f", жир {_signed(ch['d30']['fat_pct'], ' п.п.')}"
        if ch["d30"]["muscle_kg"] is not None:
            bit += f", мышцы {_signed(ch['d30']['muscle_kg'], ' кг')}"
        parts.append(bit)
    t = trend(items, today, 90)
    if t.get("n", 0) >= 3 and t.get("span", 0) >= 14:
        parts.append(f"тренд 90 дней: {t['word']} ({_signed(t['per_week'], ' кг')} в неделю, замеров {t['n']})")
    out = ["; ".join(parts)]
    out.append("  (подросток растёт и тренируется: вес меняется естественно — не советуй худеть, не называй вес лишним; "
               "нормы еды и воды уже считаются по этому весу)")
    return out


def trend_points(app, uid, since, until):
    """Для экрана «Тренды» (f_trends.weight_points): вес по дням [{day, kg}]."""
    return [{"day": r["day"], "kg": r["kg"]} for r in series(app.db, uid, since, until)]
