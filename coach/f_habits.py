"""Привычки и цели на день: что выполнено сегодня, серии, неделя, пропуски, разовые цели с переносом на завтра.

Подключение — FEATURES в coach.py: route / extra / context_lines.
  POST /api/habits {}                                    → payload (ниже)
  POST /api/habits/save {habit: {...}}                   → создать (без id) или изменить (с id, только присланные поля)
                        {delete: id} | {order: [id, …]}  → удалить / порядок
  POST /api/habits/mark {id, day?, value | done | skip}  → отметка (сегодня и до 7 дней назад); {skip, day?} без id — весь день
  POST /api/habits/goal {op: add, text, day?} | {op: done, id, done} | {op: del, id} | {op: move, id?}
                                                          → цели на день; move — невыполненные (или одну) на завтра
  POST /api/habits/import {text, preview?}               → список строками → привычки; preview — только разобрать
Ответ всех — payload: {habits, today: {day, items, goals, done, total, …}, week: [7 дней с процентом], stats}
(+ saved / created / skipped у save и import).

Хранение (meta): habits:<uid> — {"items": [привычка…], "n": счётчик id};
habits:d:<uid>:<ГГГГ-ММ-ДД> — {"v": {id: значение}, "s": [id пропусков | "*" — весь день], "g": [{id, text, done}]}.
Привычка: {id, title, icon, kind: check|count, target, unit, days: [0..6] (пн = 0), weekly: N | null, time, archived, created}.
"""
import datetime
import json
import re
import secrets

MAX_HABITS = 40
MAX_GOALS = 30
MAX_TEXT = 80
BACK_DAYS = 7  # отмечать можно сегодня и неделю назад
HISTORY = 400  # серии считаем не дальше
ICONS = ("check", "water", "book", "walk", "run", "sleep", "food", "pill", "mind", "teeth", "phone", "sun", "dumbbell",
         "pen", "heart", "lang", "music", "leaf", "moon", "clean")
TIMES = ("утро", "день", "вечер")
ALL_DAYS = [0, 1, 2, 3, 4, 5, 6]
WD_SHORT = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")


# ---------- проверка ----------

def _text(v, what="Название"):
    s = " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", str(v if v is not None else "")).split())
    if not s:
        raise ValueError(f"{what}: пусто")
    if len(s) > MAX_TEXT:
        raise ValueError(f"{what}: не длиннее {MAX_TEXT} символов")
    return s


def _int(v, lo, hi, what):
    if isinstance(v, bool):
        raise ValueError(f"{what}: нужно число")
    try:
        n = float(str(v).replace(",", ".").replace(" ", ""))
    except (TypeError, ValueError):
        raise ValueError(f"{what}: нужно число") from None
    if n != n or not lo <= n <= hi:
        raise ValueError(f"{what}: от {lo} до {hi}")
    return round(n)


def _days(v):
    if not isinstance(v, list) or not v or len(v) > 7:
        raise ValueError("Дни: выбери хотя бы один день")
    out = set()
    for x in v:
        if isinstance(x, bool) or not isinstance(x, (int, float)) or int(x) != x or not 0 <= x <= 6:
            raise ValueError("Дни: числа 0 (пн) – 6 (вс)")
        out.add(int(x))
    return sorted(out)


def _time(v):
    if v in (None, ""):
        return None
    s = str(v).strip().lower().replace(".", ":")
    if s in TIMES:
        return s
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", s)
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ValueError("Время: утро, день, вечер или ЧЧ:ММ")
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def _day(v, today):
    if v in (None, ""):
        return today
    try:
        d = datetime.date.fromisoformat(str(v))
    except ValueError:
        raise ValueError("День: дата ГГГГ-ММ-ДД") from None
    if not today - datetime.timedelta(days=BACK_DAYS) <= d <= today:
        raise ValueError(f"Отмечать можно сегодня и до {BACK_DAYS} дней назад")
    return d


def clean_habit(src, cur=None):
    """Присланные поля поверх cur (или новой привычки) с проверкой. Неверное — ValueError."""
    if not isinstance(src, dict):
        raise ValueError("Привычка: нужен объект")
    h = dict(cur or {"title": "", "icon": "check", "kind": "check", "target": 1, "unit": "", "days": list(ALL_DAYS),
                     "weekly": None, "time": None, "archived": False})
    if "title" in src or not cur:
        h["title"] = _text(src.get("title"))
    if "icon" in src:
        h["icon"] = src["icon"] if src["icon"] in ICONS else "check"
    if "kind" in src:
        if src["kind"] not in ("check", "count"):
            raise ValueError("Тип: check или count")
        h["kind"] = src["kind"]
    if "target" in src:
        h["target"] = _int(src["target"], 1, 100000, "Цель")
    if "unit" in src:
        h["unit"] = " ".join(str(src["unit"] or "").split())[:12]
    if "days" in src:
        h["days"] = _days(src["days"])
    if "weekly" in src:
        w = src["weekly"]
        h["weekly"] = None if w in (None, "", 0) else _int(w, 1, 7, "Раз в неделю")
        if h["weekly"] == 7:
            h["weekly"], h["days"] = None, list(ALL_DAYS)
    if "time" in src:
        h["time"] = _time(src["time"])
    if "archived" in src:
        h["archived"] = bool(src["archived"])
    if h["kind"] == "check":
        h["target"], h["unit"] = 1, ""
    if h["weekly"]:
        h["days"] = list(ALL_DAYS)
    return h


# ---------- хранение ----------

def _load_json(raw, default):
    try:
        v = json.loads(raw or "null")
    except ValueError:
        return default
    return v if isinstance(v, type(default)) else default


def load(db, uid):
    st = _load_json(db.meta(f"habits:{uid}"), {})
    items = []
    for h in st.get("items") or []:
        try:
            x = clean_habit(h)
        except ValueError:
            continue
        x["id"], x["created"] = str(h.get("id")), str(h.get("created") or "2000-01-01")
        items.append(x)
    return {"items": items, "n": int(st.get("n") or len(items))}


def store(db, uid, st):
    db.meta(f"habits:{uid}", json.dumps(st, ensure_ascii=False))


def _dkey(uid, day):
    return f"habits:d:{uid}:{day}"


def _clean_doc(v):
    v = v if isinstance(v, dict) else {}
    vals = {str(k): x for k, x in (v.get("v") or {}).items() if isinstance(x, (int, float)) and not isinstance(x, bool)}
    skips = [str(x) for x in (v.get("s") or []) if isinstance(x, str)]
    goals = [g for g in (v.get("g") or []) if isinstance(g, dict) and g.get("id") and g.get("text")]
    return {"v": vals, "s": skips, "g": goals}


def day_doc(db, uid, day):
    return _clean_doc(_load_json(db.meta(_dkey(uid, day)), {}))


def put_doc(db, uid, day, doc):
    db.meta(_dkey(uid, day), json.dumps(doc, ensure_ascii=False))


def history(db, uid, start, end):
    """{день: doc} за [start, end] одним запросом."""
    rows = db.q("SELECT key, value FROM meta WHERE key >= ? AND key <= ?", (_dkey(uid, start), _dkey(uid, end)))
    out = {}
    for r in rows:
        day = r["key"].rsplit(":", 1)[-1]
        out[day] = _clean_doc(_load_json(r["value"], {}))
    return out


# ---------- расчёт ----------

def _mon(d):
    return d - datetime.timedelta(days=d.weekday())


class Calc:
    """Всё по истории отметок: выполнено ли, серии, неделя."""

    def __init__(self, items, hist, today):
        self.items, self.hist, self.today = items, hist, today

    def doc(self, d):
        return self.hist.get(d.isoformat()) or {"v": {}, "s": [], "g": []}

    def value(self, h, d):
        return self.doc(d)["v"].get(h["id"], 0)

    def done(self, h, d):
        return self.value(h, d) >= h["target"]

    def skipped(self, h, d):
        s = self.doc(d)["s"]
        return "*" in s or h["id"] in s

    def alive(self, h, d):
        return d.isoformat() >= h["created"][:10]

    def scheduled(self, h, d):
        return self.alive(h, d) and (bool(h["weekly"]) or d.weekday() in h["days"])

    def week_count(self, h, d, upto=None):
        """Сколько дней недели d выполнено (до upto включительно) и сколько пропущено."""
        m, last = _mon(d), upto or _mon(d) + datetime.timedelta(days=6)
        n = sk = 0
        for i in range(7):
            x = m + datetime.timedelta(days=i)
            if x > last:
                break
            if self.done(h, x):
                n += 1
            elif self.skipped(h, x):
                sk += 1
        return n, sk

    def need(self, h, d):
        """Норма недели для weekly с учётом пропусков."""
        _, sk = self.week_count(h, d)
        return max(1, min(h["weekly"], 7 - sk))

    def due(self, h, d):
        """Идёт ли привычка в счёт дня d (в «N из M»)."""
        if h["archived"] or not self.scheduled(h, d) or self.skipped(h, d):
            return False
        if h["weekly"]:
            if self.done(h, d):
                return True
            before, _ = self.week_count(h, d, d - datetime.timedelta(days=1)) if d.weekday() else (0, 0)
            return before < self.need(h, d) and d >= self.today  # прошлые дни: weekly — только если сделал
        return True

    def streak(self, h):
        """Текущая серия: дни подряд (weekly — недели подряд с нормой). Сегодня ещё идёт — не обрывает."""
        if h["weekly"]:
            n, w = 0, _mon(self.today)
            if self.week_count(h, w)[0] >= self.need(h, w):
                n += 1
            w -= datetime.timedelta(days=7)
            while n < 60 and w + datetime.timedelta(days=6) >= _date(h["created"]):
                if self.week_count(h, w)[0] < self.need(h, w):
                    break
                n += 1
                w -= datetime.timedelta(days=7)
            return n
        n, d = 0, self.today
        for i in range(HISTORY):
            if not self.alive(h, d):
                break
            if self.scheduled(h, d) and not self.skipped(h, d):
                if self.done(h, d):
                    n += 1
                elif i:
                    break
            d -= datetime.timedelta(days=1)
        return n

    def best(self, h):
        """Лучшая серия в днях (weekly — в неделях) за историю."""
        if h["weekly"]:
            best = cur = 0
            w = max(_mon(_date(h["created"])), _mon(self.today - datetime.timedelta(days=HISTORY)))
            while w <= _mon(self.today):
                if self.week_count(h, w)[0] >= self.need(h, w):
                    cur += 1
                    best = max(best, cur)
                elif w < _mon(self.today):
                    cur = 0
                w += datetime.timedelta(days=7)
            return best
        best = cur = 0
        d = max(_date(h["created"]), self.today - datetime.timedelta(days=HISTORY))
        while d <= self.today:
            if self.scheduled(h, d) and not self.skipped(h, d):
                if self.done(h, d):
                    cur += 1
                    best = max(best, cur)
                elif d < self.today:
                    cur = 0
            d += datetime.timedelta(days=1)
        return best

    def cell(self, h, d):
        """Точка недельной сетки: d — сделано, p — частично, s — пропуск, m — не сделано, o — не по плану, f — впереди."""
        if d > self.today or not self.alive(h, d):
            return "f"
        if self.done(h, d):
            return "d"
        if self.skipped(h, d):
            return "s"
        if self.value(h, d) > 0:
            return "p"
        if not self.scheduled(h, d) or (h["weekly"] and not self.due(h, d)):
            return "o"
        return "m"

    def day_score(self, d):
        hs = [h for h in self.items if self.due(h, d)]
        gs = self.doc(d)["g"]
        done = sum(self.done(h, d) for h in hs) + sum(bool(g.get("done")) for g in gs)
        return done, len(hs) + len(gs)


def _date(s):
    return datetime.date.fromisoformat(str(s)[:10])


def step_of(target, unit=""):
    """Шаг кнопки «+» у count: 8 стаканов — по 1, 20 минут — по 5, 10 000 шагов — по 1000, 2000 мл — по 250."""
    if unit == "мл" and target >= 500:
        return 250
    if target <= 12:
        return 1
    if target <= 60:
        return 5
    if target <= 200:
        return 10
    return 10 ** (len(str(target)) - 2) if target >= 1000 else 50


def slot_of(time):
    if time in TIMES:
        return time
    if time and re.fullmatch(r"\d\d:\d\d", time):
        h = int(time[:2])
        return "утро" if 4 <= h < 12 else "день" if 12 <= h < 17 else "вечер"
    return ""


def item_of(c, h, d):
    v = c.value(h, d)
    out = {"id": h["id"], "title": h["title"], "icon": h["icon"], "kind": h["kind"], "target": h["target"],
           "unit": h["unit"], "value": v, "done": v >= h["target"], "skip": c.skipped(h, d),
           "due": c.due(h, d), "streak": c.streak(h), "streak_unit": "week" if h["weekly"] else "day",
           "time": h["time"], "slot": slot_of(h["time"]), "weekly": h["weekly"], "step": step_of(h["target"], h["unit"])}
    if h["weekly"]:
        out["week_done"], _ = c.week_count(h, d)
        out["week_need"] = c.need(h, d)
    return out


def payload(db, uid, today):
    st = load(db, uid)
    items = st["items"]
    hist = history(db, uid, today - datetime.timedelta(days=HISTORY), today)
    c = Calc(items, hist, today)
    active = [h for h in items if not h["archived"]]
    days7 = [today - datetime.timedelta(days=6 - i) for i in range(7)]
    habits = []
    for h in items:
        x = dict(h)
        x["streak"], x["best"] = c.streak(h), c.best(h)
        x["grid"] = "".join(c.cell(h, d) for d in days7)
        habits.append(x)
    shown = [h for h in active if c.scheduled(h, today) or c.skipped(h, today)]
    tdoc = c.doc(today)
    goals = [{"id": g["id"], "text": g["text"], "done": bool(g.get("done"))} for g in tdoc["g"]]
    its = [item_of(c, h, today) for h in shown]
    hd = sum(1 for x in its if x["due"] and x["done"])
    ht = sum(1 for x in its if x["due"])
    gd = sum(1 for g in goals if g["done"])
    week = []
    for d in days7:
        dn, tt = c.day_score(d)
        week.append({"day": d.isoformat(), "wd": d.weekday(), "done": dn, "total": tt,
                     "pct": round(dn / tt * 100) if tt else None})
    month = [c.day_score(today - datetime.timedelta(days=i)) for i in range(30)]
    pcts = [dn / tt for dn, tt in month if tt]
    wp = [w["pct"] for w in week if w["pct"] is not None]
    stats = {"streak_best": max([x["best"] for x in habits if not x["archived"]] or [0]),
             "streak_now": max([x["streak"] for x in habits if not x["archived"]] or [0]),
             "week_pct": round(sum(wp) / len(wp)) if wp else None,
             "month_pct": round(sum(pcts) / len(pcts) * 100) if pcts else None,
             "closed_30": sum(1 for dn, tt in month if tt and dn == tt),
             "skip_day": "*" in tdoc["s"]}
    return {"habits": habits,
            "today": {"day": today.isoformat(), "items": its, "goals": goals, "done": hd + gd, "total": ht + len(goals),
                      "habits_done": hd, "habits_total": ht, "goals_done": gd, "goals_total": len(goals)},
            "week": week, "stats": stats, "limits": {"habits": MAX_HABITS, "goals": MAX_GOALS, "text": MAX_TEXT}}


# ---------- изменения ----------

def save(db, uid, b, today):
    st = load(db, uid)
    items = st["items"]
    if "order" in b:
        ids = b["order"]
        if not isinstance(ids, list):
            raise ValueError("Порядок: список id")
        pos = {str(x): i for i, x in enumerate(ids)}
        was = {h["id"]: i for i, h in enumerate(items)}
        items.sort(key=lambda h: pos.get(h["id"], len(pos) + was[h["id"]]))
        store(db, uid, st)
        return None
    if "delete" in b:
        hid = str(b["delete"])
        if not any(h["id"] == hid for h in items):
            raise ValueError("нет такой привычки")
        st["items"] = [h for h in items if h["id"] != hid]
        store(db, uid, st)
        return None
    src = b.get("habit") if isinstance(b.get("habit"), dict) else None
    if src is None:
        raise ValueError("Привычка: нужен habit, delete или order")
    hid = src.get("id")
    if hid:
        cur = next((h for h in items if h["id"] == str(hid)), None)
        if not cur:
            raise ValueError("нет такой привычки")
        new = clean_habit(src, cur)
        new["id"], new["created"] = cur["id"], cur["created"]
        items[items.index(cur)] = new
    else:
        new = add(st, src, today)
    store(db, uid, st)
    return new["id"]


def add(st, src, today):
    if len(st["items"]) >= MAX_HABITS:
        raise ValueError(f"Не больше {MAX_HABITS} привычек — убери ненужные")
    h = clean_habit(src)
    st["n"] += 1
    h["id"], h["created"] = f"h{st['n']}", today.isoformat()
    st["items"].append(h)
    return h


def mark(db, uid, b, today):
    d = _day(b.get("day"), today)
    doc = day_doc(db, uid, d.isoformat())
    hid = b.get("id")
    if not hid:  # пропуск всего дня (болею, в дороге) — серии не рвутся
        if "skip" not in b:
            raise ValueError("Отметка: нужен id")
        doc["s"] = ["*"] if b["skip"] else [x for x in doc["s"] if x != "*"]
        put_doc(db, uid, d.isoformat(), doc)
        return
    st = load(db, uid)
    h = next((x for x in st["items"] if x["id"] == str(hid)), None)
    if not h:
        raise ValueError("нет такой привычки")
    if d.isoformat() < h["created"][:10]:  # отметил день до создания — привычка, значит, старше
        h["created"] = d.isoformat()
        store(db, uid, st)
    if "skip" in b:
        doc["s"] = [x for x in doc["s"] if x != h["id"]] + ([h["id"]] if b["skip"] else [])
        if b["skip"]:
            doc["v"].pop(h["id"], None)
    else:
        if "value" in b:
            v = _int(b["value"], 0, min(h["target"] * 10, 10 ** 6), "Значение")
        elif "done" in b:
            v = h["target"] if b["done"] else 0
        else:
            raise ValueError("Отметка: value или done")
        if h["kind"] == "check":
            v = 1 if v else 0
        if v:
            doc["v"][h["id"]] = v
        else:
            doc["v"].pop(h["id"], None)
        doc["s"] = [x for x in doc["s"] if x != h["id"]]  # отметил — уже не пропуск
    put_doc(db, uid, d.isoformat(), doc)


def goal(db, uid, b, today):
    op = b.get("op")
    d = _day(b.get("day"), today) if op == "add" else today
    doc = day_doc(db, uid, d.isoformat())
    gs = doc["g"]
    if op == "add":
        if len(gs) >= MAX_GOALS:
            raise ValueError(f"Не больше {MAX_GOALS} целей на день")
        gs.append({"id": "g" + secrets.token_hex(4), "text": _text(b.get("text"), "Цель"), "done": False})
    elif op in ("done", "del", "edit"):
        g = next((x for x in gs if x["id"] == str(b.get("id"))), None)
        if not g:
            raise ValueError("нет такой цели")
        if op == "done":
            g["done"] = bool(b.get("done"))
        elif op == "edit":
            g["text"] = _text(b.get("text"), "Цель")
        else:
            gs.remove(g)
    elif op == "move":  # невыполненные (или одну) — на завтра
        nxt = (today + datetime.timedelta(days=1)).isoformat()
        tdoc = day_doc(db, uid, nxt)
        pick = [g for g in gs if not g.get("done") and (not b.get("id") or g["id"] == str(b["id"]))]
        if not pick:
            raise ValueError("Переносить нечего — всё выполнено")
        if len(tdoc["g"]) + len(pick) > MAX_GOALS:
            raise ValueError(f"На завтра уже {len(tdoc['g'])} целей — больше {MAX_GOALS} нельзя")
        tdoc["g"] += [{"id": g["id"], "text": g["text"], "done": False} for g in pick]
        doc["g"] = [g for g in gs if g not in pick]
        put_doc(db, uid, nxt, tdoc)
        put_doc(db, uid, d.isoformat(), doc)
        return len(pick)
    else:
        raise ValueError("Цель: op = add, done, del, edit или move")
    put_doc(db, uid, d.isoformat(), doc)
    return None


# ---------- «Добавить списком» ----------

WD_RX = (r"пн|понедельник\w*", r"вт|вторник\w*", r"ср|сред[аыу]\w*", r"чт|четверг\w*", r"пт|пятниц\w*",
         r"сб|суббот\w*", r"вс|воскресень\w*")
NUM = r"(\d{1,3}(?:[  ]\d{3})+|\d+(?:[.,]\d+)?)"
UNIT_NORM = ((r"мин\w*", "мин"), (r"час\w*|ч", "ч"), (r"литр\w*|л", "л"), (r"мл", "мл"), (r"километр\w*|км", "км"),
             (r"стакан\w*", None), (r"шаг\w*", None), (r"страниц\w*|стр", "стр"), (r"отжиман\w*", None),
             (r"приседан\w*", None), (r"подтягиван\w*", None), (r"слов\w*", None), (r"глав\w*", None),
             (r"раз(?:а)?", "раз"), (r"кругов|круга|круг", None), (r"таблет\w*", None), (r"порци\w*", None))
UNIT_RX = "|".join(f"(?:{u})" for u, _ in UNIT_NORM)
ICON_WORDS = (("water", r"вод|стакан|пить"), ("book", r"чита|книг|страниц|стр\b|глав"), ("walk", r"шаг|прогул|ходьб"),
              ("run", r"бег|пробеж"), ("sleep", r"сон|спать|отбой|лечь|лож"), ("pill", r"витамин|таблет|лекарств|омега"),
              ("mind", r"медит|дыхан|аффирм"), ("teeth", r"зуб|нить"), ("phone", r"телефон|соцсет|экран|youtube|тикток"),
              ("dumbbell", r"отжим|присед|подтяг|зарядк|трениров|планк|пресс|растяж|спорт"),
              ("pen", r"дневник|запис|писать|план"), ("lang", r"англ|язык|слов|испан"), ("food", r"завтрак|ед[аы]|сладк|сахар|овощ|фрукт"),
              ("clean", r"убор|душ|умыв|кровать|постел"), ("sun", r"солнц|свеж|воздух"), ("music", r"музык|гитар|пиани"),
              ("heart", r"благодар|спасибо|родн|звон"))


def _num(s):
    return float(re.sub(r"[  ]", "", s).replace(",", "."))


def parse_line(line):
    """Строка списка → поля привычки (без id) или None, если строки пустая. Расписание и время из названия убираем,
    количество оставляем («Пить 8 стаканов воды» — так и написано, цель 8 стаканов)."""
    s = re.sub(r"^\s*(?:[-–—*•·✓✔☐□▪►>]+|\d{1,2}[.)](?!\d))\s*", "", str(line)).strip()
    s = re.sub(r"^[^\w«\"(]+", "", s).strip()  # эмодзи и значки в начале
    if not s:
        return None
    low = s.lower().replace("ё", "е")
    if len(low) != len(s):  # редкие буквы меняют длину при lower — режем по нижнему регистру
        s = low
    rest, orig = low, s
    h = {"kind": "check", "target": 1, "unit": "", "days": list(ALL_DAYS), "weekly": None, "time": None}

    def cut(rx, keep=False):
        """Найти rx в rest; без keep — вырезать и из названия (длины строк сохраняем пробелами)."""
        nonlocal rest, orig
        m = re.search(rx, rest)
        if m and not keep:
            a, b = m.span()
            rest = rest[:a] + " " * (b - a) + rest[b:]
            orig = orig[:a] + " " * (b - a) + orig[b:]
        return m

    m = cut(r"(?<!\d)(\d)\s*(?:раз[а]?|р\.?)\s*(?:в|за|/)\s*нед(?:елю|\.)?|(?<!\d)(\d)\s*/\s*нед\w*|(?<!\d)(\d)\s*р/н")
    if m:
        n = int(next(g for g in m.groups() if g))
        if 1 <= n < 7:
            h["weekly"] = n
    m = None if m else cut(r"(?:(один|дважды|трижды)\s+|(?<![а-я\d]))(?:раз\s+)?в\s+неделю")
    if m:  # «раз в неделю», «дважды в неделю»
        h["weekly"] = {"дважды": 2, "трижды": 3}.get(m.group(1), 1)
    if cut(r"(?:раз\s+)?(?:каждый\s+день|ежедневно)"):
        h["days"] = list(ALL_DAYS)
    if cut(r"(?:по\s+)?будн(?:ям|и|ий\s+день)"):
        h["days"] = [0, 1, 2, 3, 4]
    if cut(r"(?:по\s+)?выходн(?:ым|ые)"):
        h["days"] = [5, 6]
    if not h["weekly"]:
        wd_any = "|".join(f"(?:{x})" for x in WD_RX)
        one = rf"(?<![а-я])(?:{wd_any})(?![а-я])"
        mm = re.search(rf"(?:(?:по|в|во)\s+)?{one}(?:\s*(?:,|и|/|-|–)?\s*(?:(?:по|в|во)\s+)?{one})*", rest)
        if mm:
            seg = mm.group(0)
            idx = [next(i for i, rx in enumerate(WD_RX) if re.fullmatch(rx, w.group(0)))
                   for w in re.finditer(one, seg)]
            if len(idx) == 2 and re.search(r"[а-я]\s*[-–]\s*[а-я]", seg):  # «пн-пт»
                a, b = idx
                idx = list(range(a, b + 1)) if a <= b else list(range(a, 7)) + list(range(0, b + 1))
            if idx:
                h["days"] = sorted(set(idx))
                cut(re.escape(seg))
    m = cut(r"(?:^|\s)(?:в|во)\s+(\d{1,2})[:.](\d{2})(?!\d)")  # «в 22:00» — время, из названия убираем
    m = m or cut(r"(?:до|к|после)\s+(\d{1,2})[:.](\d{2})(?!\d)", keep=True)  # «лечь до 23:00» — оставляем как написано
    if m and int(m.group(1)) <= 23 and int(m.group(2)) <= 59:
        h["time"] = f"{int(m.group(1)):02d}:{m.group(2)}"
    for word, rx in (("утро", r"(?<![а-я])(?:утр(?:ом|а)(?![а-я])|с\s+утра|после\s+пробуждения|натощак)"),
                     ("вечер", r"(?<![а-я])(?:вечер(?:ом)?(?![а-я])|перед\s+сном|на\s+ночь)"),
                     ("день", r"(?<![а-я])(?:дн[её]м(?![а-я])|в\s+обед|после\s+обеда)")):
        if cut(rx):
            h["time"] = h["time"] or word
            break
    m = cut(rf"(?<![\d.,]){NUM}\s*(тыс(?:\.|яч\w*)?\s*)?({UNIT_RX})(?![а-я])", keep=True)
    if m:
        n = _num(m.group(1)) * (1000 if m.group(2) else 1)
        word = m.group(3)
        unit = next((norm if norm is not None else word for u, norm in UNIT_NORM if re.fullmatch(u, word)), word)
        if unit == "л" and n < 20:  # «2 л воды» — считаем в мл, кнопка «+250»
            n, unit = n * 1000, "мл"
        n = max(1, min(100000, round(n)))
        if n > 1:
            h["kind"], h["target"], h["unit"] = "count", n, ("" if unit == "раз" else unit)
    title = " ".join(orig.split()).strip(" ,.;:-–—")
    title = re.sub(r"\s+(?:по|в|во|на|и)$", "", title, flags=re.I).strip(" ,.;:-–—")
    title = re.sub(r"\s+([,.;:])", r"\1", title)
    if len(re.sub(r"[^а-яa-z]", "", title.lower())) < 2:
        title = s
    title = (title[:1].upper() + title[1:])[:MAX_TEXT].rstrip()
    h["title"] = title
    h["icon"] = next((ic for ic, rx in ICON_WORDS if re.search(rx, low)), "check")
    return h


def import_text(db, uid, text, today, preview=False):
    text = str(text or "")
    if len(text) > 8000:
        raise ValueError("Слишком длинный список — до 8000 символов")
    lines = [x for x in text.replace("\r", "").split("\n") if x.strip()]
    if not lines:
        raise ValueError("Список пуст — каждая привычка с новой строки")
    parsed, skipped = [], []
    for ln in lines[:MAX_HABITS + 10]:
        h = parse_line(ln)
        if h:
            h["line"] = ln.strip()[:200]  # для предпросмотра: убрать строку и прислать список снова
            parsed.append(h)
        else:
            skipped.append(ln.strip()[:MAX_TEXT])
    st = load(db, uid)
    room = MAX_HABITS - len(st["items"])
    have = {h["title"].lower() for h in st["items"]}
    out, dup = [], []
    for h in parsed:
        if h["title"].lower() in have:
            dup.append(h["title"])
            continue
        have.add(h["title"].lower())
        out.append(h)
    over = out[room:] if room >= 0 else out
    out = out[:max(0, room)]
    if preview:
        return {"habits": out, "skipped": skipped, "dup": dup, "over": [h["title"] for h in over]}
    created = [add(st, h, today) for h in out]
    if created:
        store(db, uid, st)
    return {"created": created, "skipped": skipped, "dup": dup, "over": [h["title"] for h in over]}


# ---------- подключение ----------

def route(app, uid, path, b, today):
    if not path.startswith("/api/habits"):
        return None
    db, b = app.db, b or {}
    if path == "/api/habits":
        return payload(db, uid, today)
    if path == "/api/habits/save":
        hid = save(db, uid, b, today)
        return {**payload(db, uid, today), "saved": hid}
    if path == "/api/habits/mark":
        mark(db, uid, b, today)
        return payload(db, uid, today)
    if path == "/api/habits/goal":
        moved = goal(db, uid, b, today)
        return {**payload(db, uid, today), **({"moved": moved} if moved else {})}
    if path == "/api/habits/import":
        r = import_text(db, uid, b.get("text"), today, bool(b.get("preview")))
        return r if b.get("preview") else {**payload(db, uid, today), **r}
    return None


def extra(app, uid, today):
    """В /api/all (ext.habits): только сегодня (главная — карточка «Привычки и цели») и неделя процентами."""
    p = payload(app.db, uid, today)
    return {"today": p["today"], "week": p["week"], "has": bool(p["habits"]),
            "stats": {k: p["stats"][k] for k in ("streak_now", "week_pct")}}


def left_titles(p, n=4):
    """Что осталось сегодня — короткими названиями (count — с прогрессом)."""
    out = []
    for x in p["today"]["items"]:
        if x["due"] and not x["done"]:
            out.append(x["title"] + (f" {x['value']}/{x['target']}" if x["kind"] == "count" and x["value"] else ""))
    out += [g["text"] for g in p["today"]["goals"] if not g["done"]]
    return out[:n] + ([f"ещё {len(out) - n}"] if len(out) > n else [])


def brief_line(db, uid, today):
    """Строка для утреннего разбора: «Привычки сегодня: 6 · цели: 2» — или ""."""
    p = payload(db, uid, today)
    t = p["today"]
    if not t["habits_total"] and not t["goals_total"]:
        return ""
    return f"✅ Привычки сегодня: {t['habits_total']}" + (f" · цели: {t['goals_total']}" if t["goals_total"] else "")


def remind_line(db, uid, day):
    """Строка в вечернее напоминание об отбое: «Осталось: …» — или "" (всё сделано / привычек нет)."""
    left = left_titles(payload(db, uid, day), 3)
    return ("Осталось: " + ", ".join(left)) if left else ""


def context_lines(app, uid, today):
    p = payload(app.db, uid, today)
    t = p["today"]
    if not p["habits"] and not t["goals"]:
        return []
    done = [x["title"] for x in t["items"] if x["due"] and x["done"]]
    left = left_titles(p, 8)
    lines = [f"ПРИВЫЧКИ И ЦЕЛИ (отмечает сам): сегодня {t['done']} из {t['total']}"
             + (f"; сделано: {', '.join(done[:8])}" if done else "") + (f"; осталось: {', '.join(left)}" if left else "")]
    wk = p["stats"]["week_pct"]
    streaks = sorted(((x["streak"], x) for x in p["habits"] if not x["archived"] and x["streak"] >= 3),
                     key=lambda z: -z[0])[:3]
    tail = []
    if wk is not None:
        tail.append(f"за 7 дней выполнено в среднем {wk}%")
    if streaks:
        tail.append("серии: " + ", ".join(f"{x['title']} {n} {'нед.' if x['weekly'] else 'дн.'}" for n, x in streaks))
    if tail:
        lines.append("Привычки: " + "; ".join(tail) + ". Хвали за серии, мягко напоминай о невыполненном.")
    return lines
