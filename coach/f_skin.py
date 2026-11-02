"""Кожа: мой уход (утро, вечер, процедуры по расписанию, заметки о коже), отметки на сегодня, серия, дневник кожи 1–5.

Подключение — FEATURES в coach.py: route / extra / context_lines.
  POST /api/skin {}                                       → payload (ниже)
  POST /api/skin/save {routine: {am?, pm?, extra?}}       → заменить присланные части ухода (шаги с id сохраняют отметки)
                      {notes: {type?, react?, avoid?}}    → заметки о коже (меняются только присланные поля)
  POST /api/skin/mark {id, done, day?}                    → отметка шага или процедуры (сегодня и до 7 дней назад)
  POST /api/skin/rate {rating: 1–5 | null, note?, day?}   → оценка кожи за день и короткая заметка
  POST /api/skin/import {text, preview?, drop?: [ключ…]}  → «Заполнить списком»: preview — только разобрать
                                                             (ключи частей «am:0», «pm:1», «extra:0» — для drop)
  POST /api/skin/photo {image, thumb?}                    → фото лба за сегодня (JPEG base64 / data:URL; одно на день,
                                                             повтор заменяет) → payload + photos
  POST /api/skin/photo {day, thumb?} (или /photo/get, GET ?day=) → {day, image: data:URL} — только своё (по сессии)
  POST /api/skin/photos {}                                → {photos: [{day}] (новые сверху), count}
  POST /api/skin/photo/delete {day}                       → payload + photos
Ответ всех, кроме preview и фото, — payload: {has, routine, notes, today, streak, best, diary (30 дней), stats, limits};
today.photo — {due, done, count, last, next, days, every}: «Фото лба» раз в 3 дня (через 2 дня после последнего).

Хранение (meta): skin:<uid> — {"am": [шаг], "pm": [шаг], "extra": [процедура], "notes": {type, react, avoid}, "n", "created"};
skin:d:<uid>:<ГГГГ-ММ-ДД> — {"m": [id отмеченных], "r": 1–5 | null, "t": заметка, "a": утро сделано, "p": вечер сделан,
"f": фото лба сделано}. Фото — файлы <DATA>/photos/skin/<uid>/<день>.jpg (+ <день>.t.jpg — миниатюра), только владельцу;
в репозиторий и тренеру (Claude) не попадают — тренер видит лишь даты и число.
Флаги «a»/«p» считаются в момент отметки по тогдашнему уходу — правка ухода не переписывает прошлые дни.
Шаг: {id, title, product}; процедура: + days [0..6] (пн = 0) или weekly N (N раз в неделю, в любой день).
Серия — дни подряд, когда сделан весь утренний и вечерний уход (сегодня ещё идёт — не обрывает).
"""
import base64
import datetime
import json
import os
import re
import tempfile

import f_habits as H  # общий разбор строк списка (дни недели, «N раз в неделю», время) и проверки

PARTS = ("am", "pm", "extra")
PART_WORD = {"am": "утро", "pm": "вечер", "extra": "процедуры"}
MAX_STEPS = 12  # в утре и в вечере
MAX_EXTRA = 12
MAX_TEXT = 80
MAX_NOTES = 300
MAX_DAY_NOTE = 200
BACK_DAYS = H.BACK_DAYS  # отмечать и оценивать — сегодня и неделю назад
DIARY_DAYS = 30
HISTORY = 400  # серию считаем не дальше
NOTE_KEYS = ("type", "react", "avoid")
NOTE_WORD = {"type": "тип и особенности", "react": "на что реагирует", "avoid": "что не подходит"}
RATE_WORD = {1: "плохо", 2: "так себе", 3: "нормально", 4: "хорошо", 5: "отлично"}
WD = H.WD_SHORT
PHOTO_EVERY = 3  # «Фото лба» — раз в 3 дня: через 2 дня после последнего фото (пропустил — напоминаем каждый день)
MAX_PHOTO = 640 * 1024  # JPEG из приложения ≤ 1280 px, обычно 150–400 КБ; тело POST в coach.py — до 1 МБ (base64 ×4/3)
MAX_THUMB = 48 * 1024  # миниатюра для сетки «Фото лба»
MAX_SIDE = 2048
PHOTO_LINE = "📸 Сегодня фото лба после умывания"
DAY_RX = re.compile(r"\d{4}-\d{2}-\d{2}")
FILE_RX = re.compile(r"(\d{4}-\d{2}-\d{2})\.jpg")


# ---------- проверка ----------

def _plain(v, limit, what):
    s = " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", str(v if v is not None else "")).split())
    if len(s) > limit:
        raise ValueError(f"{what}: не длиннее {limit} символов")
    return s


def clean_step(src, part):
    """Шаг (am/pm) или процедура (extra) с проверкой. Неверное — ValueError."""
    if not isinstance(src, dict):
        raise ValueError("Шаг: нужен объект")
    s = {"title": H._text(src.get("title"), "Шаг"), "product": _plain(src.get("product"), MAX_TEXT, "Средство")}
    if part == "extra":
        days, weekly = src.get("days"), src.get("weekly")
        if days not in (None, []):
            s["days"], s["weekly"] = H._days(days), None
            if len(s["days"]) == 7:
                raise ValueError("Каждый день — это шаг утра или вечера, а не процедура")
        else:
            s["days"], s["weekly"] = [], H._int(weekly if weekly not in (None, "") else 1, 1, 6, "Раз в неделю")
    return s


def clean_notes(src, cur=None):
    if not isinstance(src, dict):
        raise ValueError("Заметки: нужен объект")
    out = dict(cur or {k: "" for k in NOTE_KEYS})
    for k in NOTE_KEYS:
        if k in src:
            out[k] = _plain(src[k], MAX_NOTES, "Заметка")
    return out


def sched(x):
    """Расписание процедуры словами: «вт, сб» или «2 раза в неделю»."""
    if x.get("days"):
        return ", ".join(WD[i] for i in x["days"])
    n = x.get("weekly") or 1
    return f"{n} {'раза' if n in (2, 3, 4) else 'раз'} в неделю"


# ---------- хранение ----------

def _empty():
    return {"am": [], "pm": [], "extra": [], "notes": {k: "" for k in NOTE_KEYS}, "n": 0, "created": None}


def load(db, uid):
    raw = H._load_json(db.meta(f"skin:{uid}"), {})
    st = _empty()
    for part in PARTS:
        for x in raw.get(part) or []:
            try:
                s = clean_step(x, part)
            except ValueError:
                continue
            s["id"] = str(x.get("id") or "")
            if s["id"]:
                st[part].append(s)
    try:
        st["notes"] = clean_notes(raw.get("notes") or {})
    except ValueError:
        pass
    st["n"] = int(raw.get("n") or 0) if str(raw.get("n") or 0).isdigit() else 0
    st["created"] = str(raw["created"])[:10] if raw.get("created") else None
    return st


def store(db, uid, st):
    db.meta(f"skin:{uid}", json.dumps(st, ensure_ascii=False))


def has_routine(st):
    return bool(st["am"] or st["pm"] or st["extra"])


def _dkey(uid, day):
    return f"skin:d:{uid}:{day}"


def _clean_doc(v):
    v = v if isinstance(v, dict) else {}
    r = v.get("r")
    return {"m": [str(x) for x in (v.get("m") or []) if isinstance(x, str)],
            "r": r if isinstance(r, int) and not isinstance(r, bool) and 1 <= r <= 5 else None,
            "t": str(v.get("t") or "")[:MAX_DAY_NOTE], "a": bool(v.get("a")), "p": bool(v.get("p")), "ok": bool(v.get("ok")),
            "f": bool(v.get("f"))}


def day_doc(db, uid, day):
    return _clean_doc(H._load_json(db.meta(_dkey(uid, day)), {}))


def put_doc(db, uid, day, doc):
    db.meta(_dkey(uid, day), json.dumps(doc, ensure_ascii=False))


def history(db, uid, start, end):
    """{день: doc} за [start, end] одним запросом."""
    rows = db.q("SELECT key, value FROM meta WHERE key >= ? AND key <= ?", (_dkey(uid, start), _dkey(uid, end)))
    return {r["key"].rsplit(":", 1)[-1]: _clean_doc(H._load_json(r["value"], {})) for r in rows}


def _flags(st, doc):
    """Утро и вечер сделаны целиком (по нынешнему уходу); нет шагов в части — она не в счёт."""
    m = set(doc["m"])
    doc["a"] = bool(st["am"]) and all(s["id"] in m for s in st["am"])
    doc["p"] = bool(st["pm"]) and all(s["id"] in m for s in st["pm"])
    return doc


# ---------- расчёт ----------

def _mon(d):
    return d - datetime.timedelta(days=d.weekday())


class Calc:
    def __init__(self, st, hist, today):
        self.st, self.hist, self.today = st, hist, today

    def doc(self, d):
        return self.hist.get(d.isoformat()) or {"m": [], "r": None, "t": "", "a": False, "p": False, "ok": False, "f": False}

    def ok(self, d):
        doc = self.doc(d)
        if d == self.today:
            return day_ok(self.st, doc)
        return bool(doc.get("ok"))

    def start(self):
        return datetime.date.fromisoformat(self.st["created"]) if self.st["created"] else self.today

    def streak(self):
        n, d, first = 0, self.today, self.start()
        for i in range(HISTORY):
            if d < first:
                break
            if self.ok(d):
                n += 1
            elif i:
                break
            d -= datetime.timedelta(days=1)
        return n

    def best(self):
        best = cur = 0
        d = max(self.start(), self.today - datetime.timedelta(days=HISTORY))
        while d <= self.today:
            if self.ok(d):
                cur += 1
                best = max(best, cur)
            elif d < self.today:
                cur = 0
            d += datetime.timedelta(days=1)
        return best

    def week_done(self, x, d):
        """Сколько раз процедура сделана на неделе дня d до d (не включая)."""
        m, n = _mon(d), 0
        while m < d:
            if x["id"] in self.doc(m)["m"]:
                n += 1
            m += datetime.timedelta(days=1)
        return n

    def extra_item(self, x, d):
        done = x["id"] in self.doc(d)["m"]
        wd = self.week_done(x, d)
        due = d.weekday() in x["days"] if x["days"] else wd < x["weekly"]
        return {"id": x["id"], "title": x["title"], "product": x["product"], "days": x["days"], "weekly": x["weekly"],
                "sched": sched(x), "done": done, "due": due or done, "week_done": wd + done,
                "week_need": len(x["days"]) if x["days"] else x["weekly"]}


def day_ok(st, doc):
    if not (st["am"] or st["pm"]):
        return False
    m = set(doc["m"])
    return all(s["id"] in m for s in st["am"] + st["pm"])


def _avg(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 1) if xs else None


def fnum(x):
    return "—" if x is None else (f"{x:.1f}".rstrip("0").rstrip(".")).replace(".", ",")


def stats(c):
    days = [c.today - datetime.timedelta(days=i) for i in range(DIARY_DAYS)]
    rs = [(d, c.doc(d)["r"]) for d in days]
    rated = [r for _, r in rs if r]
    avg7, avg30 = _avg(r for d, r in rs[:7]), _avg(rated)
    prev7 = _avg(r for d, r in rs[7:14])
    trend = None
    if avg7 is not None and prev7 is not None:
        trend = "лучше" if avg7 - prev7 >= .3 else "хуже" if prev7 - avg7 >= .3 else "так же"
    with_, without = [], []
    first = c.start()
    for d, r in rs:
        if r and c.st["pm"] and d >= first:
            (with_ if (c.doc(d)["p"] if d != c.today else _part_done(c.st, c.doc(d), "pm")) else without).append(r)
    link = ({"with": _avg(with_), "without": _avg(without)} if len(with_) >= 3 and len(without) >= 3 else None)
    if link and abs(link["with"] - link["without"]) < .3:  # разницы почти нет — не говорим о связи
        link = None
    word = RATE_WORD[min(5, max(1, round(avg30)))] if avg30 else None
    if len(rated) < 3:
        summary = "Оценивай кожу каждый день — через неделю покажу, как она меняется и что ей помогает."
    else:
        summary = f"За 30 дней кожа в среднем {fnum(avg30)} из 5 — «{word}»."
        if trend:
            summary += {"лучше": " Последняя неделя лучше прошлой.", "хуже": " Последняя неделя хуже прошлой.",
                        "так же": " Последняя неделя — как прошлая."}[trend]
    if link:
        summary += (f" Когда вечерний уход сделан, кожа в среднем {fnum(link['with'])}, без него — {fnum(link['without'])}.")
    return {"avg7": avg7, "avg30": avg30, "prev7": prev7, "trend": trend, "rated": len(rated), "word": word,
            "link": link, "summary": summary}


def _part_done(st, doc, part):
    m = set(doc["m"])
    return bool(st[part]) and all(s["id"] in m for s in st[part])


def payload(db, uid, today):
    st = load(db, uid)
    hist = history(db, uid, today - datetime.timedelta(days=HISTORY), today)
    c = Calc(st, hist, today)
    doc = c.doc(today)
    m = set(doc["m"])
    steps = {p: [{"id": s["id"], "title": s["title"], "product": s["product"], "done": s["id"] in m} for s in st[p]]
             for p in ("am", "pm")}
    extras = [c.extra_item(x, today) for x in st["extra"]]
    diary = []
    for i in range(DIARY_DAYS - 1, -1, -1):
        d = today - datetime.timedelta(days=i)
        dd = c.doc(d)
        diary.append({"day": d.isoformat(), "wd": d.weekday(), "r": dd["r"], "ok": c.ok(d)})
    routine = {"am": [{k: s[k] for k in ("id", "title", "product")} for s in st["am"]],
               "pm": [{k: s[k] for k in ("id", "title", "product")} for s in st["pm"]],
               "extra": [{**{k: x[k] for k in ("id", "title", "product", "days", "weekly")}, "sched": sched(x)} for x in st["extra"]]}
    return {
        "has": has_routine(st), "routine": routine, "notes": st["notes"],
        "today": {"day": today.isoformat(), "am": steps["am"], "pm": steps["pm"],
                  "extra": [x for x in extras if x["due"]], "later": [x for x in extras if not x["due"]],
                  "am_done": sum(x["done"] for x in steps["am"]), "am_total": len(steps["am"]),
                  "pm_done": sum(x["done"] for x in steps["pm"]), "pm_total": len(steps["pm"]),
                  "ok": day_ok(st, doc), "rating": doc["r"], "word": RATE_WORD.get(doc["r"]), "note": doc["t"],
                  "photo": photo_info(hist, today, has_routine(st))},
        "streak": c.streak(), "best": c.best(), "diary": diary, "stats": stats(c),
        "limits": {"steps": MAX_STEPS, "extra": MAX_EXTRA, "text": MAX_TEXT, "notes": MAX_NOTES, "note": MAX_DAY_NOTE},
    }


# ---------- изменения ----------

def _new_id(st, part):
    st["n"] += 1
    return f"{part[0]}{st['n']}"


def _touch_today(db, uid, st, today):
    """После правки ухода — пересчитать «сделано» сегодня (новый шаг — ещё не сделан)."""
    doc = day_doc(db, uid, today.isoformat())
    if doc["m"] or doc["ok"]:
        doc["ok"] = day_ok(st, doc)
        _flags(st, doc)
        put_doc(db, uid, today.isoformat(), doc)


def save(db, uid, b, today):
    st = load(db, uid)
    if "routine" not in b and "notes" not in b:
        raise ValueError("Уход: нужен routine или notes")
    if "notes" in b:
        st["notes"] = clean_notes(b["notes"], st["notes"])
    if "routine" in b:
        r = b["routine"]
        if not isinstance(r, dict) or not any(p in r for p in PARTS):
            raise ValueError("Уход: нужны части am, pm или extra")
        for part in PARTS:
            if part not in r:
                continue
            src = r[part]
            if not isinstance(src, list):
                raise ValueError(f"Уход ({PART_WORD[part]}): нужен список")
            limit = MAX_EXTRA if part == "extra" else MAX_STEPS
            if len(src) > limit:
                raise ValueError(f"В части «{PART_WORD[part]}» не больше {limit} шагов")
            have = {s["id"] for s in st[part]}
            seen, out = set(), []
            for x in src:
                s = clean_step(x, part)
                sid = str(x.get("id") or "")
                if sid not in have or sid in seen:
                    sid = _new_id(st, part)
                seen.add(sid)
                s["id"] = sid
                out.append(s)
            st[part] = out
        if has_routine(st) and not st["created"]:
            st["created"] = today.isoformat()
    store(db, uid, st)
    _touch_today(db, uid, st, today)
    return st


def mark(db, uid, b, today):
    d = H._day(b.get("day"), today)
    sid = str(b.get("id") or "")
    if not sid:
        raise ValueError("Отметка: нужен id шага")
    if "done" not in b:
        raise ValueError("Отметка: нужен done")
    st = load(db, uid)
    if not any(s["id"] == sid for p in PARTS for s in st[p]):
        raise ValueError("нет такого шага")
    doc = day_doc(db, uid, d.isoformat())
    doc["m"] = [x for x in doc["m"] if x != sid] + ([sid] if b["done"] else [])
    doc["ok"] = day_ok(st, doc)
    _flags(st, doc)
    if st["created"] and d.isoformat() < st["created"]:  # отметил день до «создания» — уход, значит, старше
        st["created"] = d.isoformat()
        store(db, uid, st)
    put_doc(db, uid, d.isoformat(), doc)


def rate(db, uid, b, today):
    d = H._day(b.get("day"), today)
    if "rating" not in b and "note" not in b:
        raise ValueError("Оценка: нужен rating или note")
    doc = day_doc(db, uid, d.isoformat())
    if "rating" in b:
        r = b["rating"]
        if r in (None, "", 0):
            doc["r"] = None
        else:
            doc["r"] = H._int(r, 1, 5, "Оценка")
    if "note" in b:
        doc["t"] = _plain(b["note"], MAX_DAY_NOTE, "Заметка")
    put_doc(db, uid, d.isoformat(), doc)


# ---------- «Заполнить списком» ----------

BOTH_RX = (r"(?<![а-я])(?:утр(?:ом|о)\s*(?:и|,|/|\+)\s*вечер(?:ом)?|утр(?:ом|о)\s*(?:и|,|/)\s*на\s+ночь"
           r"|(?:2|два)\s*раза\s*в\s*день|дважды\s+в\s+день)(?![а-я])")
AM_RX = r"(?<![а-я])(?:утр(?:ом|о|а)|с\s+утра|после\s+пробуждения)(?![а-я])"
PM_RX = r"(?<![а-я])(?:вечер(?:ом)?|на\s+ночь|перед\s+сном)(?![а-я])"
WEEKLY_RX = (r"(?<!\d)([1-6])\s*(?:раз[а]?|р\.?)\s*(?:в|за|/)\s*нед(?:елю|\.)?|(?<!\d)([1-6])\s*/\s*нед\w*"
             r"|(?:(один|дважды|трижды)\s+|(?<![а-я\d]))(?:раз\s+)?в\s+неделю|(?<![а-я])через\s+день(?![а-я])"
             r"|(?<![а-я])раз\s+в\s+(\d)\s+дн(?:я|ей)(?![а-я])")
HEAD_RX = re.compile(r"^(?P<h>утр(?:о|ом|ений\s+уход)|вечер(?:ом|ний\s+уход)?|на\s+ночь|перед\s+сном|"
                     r"утр(?:о|ом)\s+и\s+вечер(?:ом)?|каждый\s+день|ежедневно|"
                     r"дополнительно|доп\.?|иногда|процедуры|раз\s+в\s+неделю|по\s+расписанию|"
                     r"заметки|о\s+коже|про\s+кожу|особенности)\s*(?::\s*(?P<rest>.*)|-\s+(?P<rest2>.*))?$", re.I)
NOTE_RX = ((r"^(?:тип\s+кожи|кожа|у\s+меня\s+кожа)\b", "type"),
           (r"^(?:реагирует|реакция|аллерги\w*|раздражени\w*|краснеет)\b", "react"),
           (r"^(?:не\s+подходит|не\s+подходят|нельзя|не\s+использ\w*|избегать)\b", "avoid"))


def _head_part(h):
    h = h.lower()
    if re.match(r"утр\w*\s+и\s+вечер|каждый|ежедневно", h):
        return "both"
    if h.startswith("утр"):
        return "am"
    if h.startswith(("вечер", "на ночь", "перед")):
        return "pm"
    if re.match(r"заметки|о\s+коже|про\s+кожу|особенности", h):
        return "notes"
    return "extra"


def _split_product(title):
    """«Умыться — гель X» / «Крем (бренд)» / «Сыворотка: ниацинамид» → (шаг, средство)."""
    m = re.match(r"^(.+?)\s*\((.+)\)\s*$", title)
    if m:
        return m.group(1).strip(" ,.;:-–—"), m.group(2).strip()
    m = re.match(r"^(.+?)\s+(?:—|–|-)\s+(.+)$", title) or re.match(r"^(.+?)\s*:\s*(.+)$", title)
    if m:
        return m.group(1).strip(" ,.;:-–—"), m.group(2).strip(" ,.;:-–—")
    return title, ""


def _cap(s):
    s = " ".join(str(s).split()).strip(" ,.;:-–—")
    return (s[:1].upper() + s[1:])[:MAX_TEXT].rstrip()


def parse_item(line, section=None):
    """Строка ухода → (часть: am|pm|both|extra, {title, product, days?, weekly?}) или None."""
    s = re.sub(r"^\s*(?:[-–—*•·✓✔☐□▪►>]+|\d{1,2}[.)](?!\d))\s*", "", str(line)).strip()
    s = re.sub(r"^[^\w«\"(]+", "", s).strip()
    if not s:
        return None
    low = s.lower().replace("ё", "е")
    part = None
    m = re.search(BOTH_RX, low)
    if m:
        part = "both"
        s, low = (s[:m.start()] + " " + s[m.end():]).strip(), (low[:m.start()] + " " + low[m.end():]).strip()
    weekly = None
    m = re.search(WEEKLY_RX, low)
    if m:
        g = m.groups()
        if g[0] or g[1]:
            weekly = int(g[0] or g[1])
        elif "через" in m.group(0):
            weekly = 3
        elif g[3]:
            weekly = max(1, min(6, round(7 / int(g[3])))) if int(g[3]) else 1
        else:
            weekly = {"дважды": 2, "трижды": 3}.get(g[2], 1)
        s, low = (s[:m.start()] + " " + s[m.end():]).strip(), (low[:m.start()] + " " + low[m.end():]).strip()
    h = H.parse_line(s)  # дни недели, «утром/вечером», время — и название без них
    if not h:
        return None
    days = h["days"] if h["days"] != H.ALL_DAYS else []
    title, product = _split_product(h["title"])
    title = _cap(title)
    if len(re.sub(r"[^а-яa-z]", "", title.lower())) < 2:
        return None
    item = {"title": title, "product": _cap(product) if product else ""}
    if days or weekly:
        item.update({"days": days, "weekly": None if days else weekly})
        return "extra", item
    if part is None:
        if h["time"] == "утро" or re.search(AM_RX, low):
            part = "am"
        elif h["time"] == "вечер" or re.search(PM_RX, low):
            part = "pm"
        elif h["time"] and re.fullmatch(r"\d\d:\d\d", h["time"]):
            part = "am" if int(h["time"][:2]) < 15 else "pm"
    part = part or section or "both"
    if part == "extra":
        item.update({"days": [], "weekly": 1})
    return part, item


def parse_text(text):
    """Текст строками → {"am": [...], "pm": [...], "extra": [...], "notes": {...}, "skipped": [...]}.
    Заголовки «Утро:», «Вечер:», «Дополнительно:», «Заметки:» задают часть следующих строк; после «Утро:» можно
    перечислить шаги через запятую. Без части — утро и вечер."""
    out = {"am": [], "pm": [], "extra": [], "notes": {}, "skipped": []}
    section = None

    def note(key, val):
        val = _plain(val, MAX_NOTES * 2, "Заметка").strip(" ,.;:-–—")
        if val:
            out["notes"][key] = (out["notes"][key] + "; " + val if out["notes"].get(key) else val)[:MAX_NOTES]

    def add(line, sec):
        r = parse_item(line, sec)
        if not r:
            if line.strip():
                out["skipped"].append(line.strip()[:MAX_TEXT])
            return
        part, item = r
        for p in (("am", "pm") if part == "both" else (part,)):
            out[p].append(dict(item))

    for raw in str(text or "").replace("\r", "").split("\n"):
        line = re.sub(r"^\s*(?:[-–—*•·✓✔☐□▪►>]+|\d{1,2}[.)](?!\d))\s*", "", raw).strip()
        line = re.sub(r"^[^\w«\"(]+", "", line).strip()
        if not line:
            continue
        low = line.lower().replace("ё", "е")
        nk = next((k for rx, k in NOTE_RX if re.match(rx, low)), None)
        if nk and (":" in line or nk != "type" or section == "notes" or re.match(r"^тип\s+кожи", low)):
            note(nk, re.sub(r"^[^:—–-]*[:—–-]\s*", "", line) if re.search(r"[:—–-]", line) else line)
            continue
        hm = HEAD_RX.match(low)
        if hm:
            section = _head_part(hm.group("h"))
            rest = line[len(line) - len(hm.group("rest") or hm.group("rest2") or ""):] if (hm.group("rest") or hm.group("rest2")) else ""
            if rest.strip():
                if section == "notes":
                    note("type", rest)
                else:
                    for piece in re.split(r"\s*(?:,|;|→|->|\+|\s+затем\s+|\s+потом\s+)\s*", rest):
                        if piece.strip():
                            add(piece, section)
            continue
        if section == "notes":
            note(nk or "type", line)
            continue
        add(line, section)
    return out


def import_text(db, uid, text, today, preview=False, drop=None):
    text = str(text or "")
    if len(text) > 4000:
        raise ValueError("Слишком длинный список — до 4000 символов")
    if not text.strip():
        raise ValueError("Список пуст — напиши уход строками: «Утро: умыться, крем»")
    parsed = parse_text(text)
    drop = {str(x) for x in (drop or []) if isinstance(x, (str, int))}
    st = load(db, uid)
    res = {"am": [], "pm": [], "extra": [], "notes": parsed["notes"], "skipped": parsed["skipped"], "dup": [], "over": []}
    for part in PARTS:
        have = {s["title"].lower() for s in st[part]}
        room = (MAX_EXTRA if part == "extra" else MAX_STEPS) - len(st[part])
        for i, item in enumerate(parsed[part]):
            key = f"{part}:{i}"
            if key in drop:
                continue
            if item["title"].lower() in have:
                res["dup"].append(item["title"])
                continue
            have.add(item["title"].lower())
            if len(res[part]) >= room:
                res["over"].append(item["title"])
                continue
            x = {**item, "key": key}
            if part == "extra":
                x["sched"] = sched(item)
            res[part].append(x)
    if preview:
        return res
    if not any(res[p] for p in PARTS) and not res["notes"]:
        raise ValueError("Нечего добавить — шаги уже есть или строки не разобрались")
    for part in PARTS:
        for item in res[part]:
            s = clean_step(item, part)
            s["id"] = _new_id(st, part)
            st[part].append(s)
    for k, v in res["notes"].items():
        cur = st["notes"].get(k) or ""
        if v.lower() not in cur.lower():
            st["notes"][k] = ((cur + "; " + v) if cur else v)[:MAX_NOTES]
    if has_routine(st) and not st["created"]:
        st["created"] = today.isoformat()
    store(db, uid, st)
    _touch_today(db, uid, st, today)
    return {"added": {p: len(res[p]) for p in PARTS}, "dup": res["dup"], "over": res["over"], "skipped": res["skipped"]}


# ---------- фото лба ----------

def photo_info(hist, today, active=True):
    """«Фото лба» по дням с отметкой «f»: раз в PHOTO_EVERY дней от последнего фото. active — раздел ведётся (уход есть
    или фото уже были): без этого фото не просим. due — по плану сегодня (сделано сегодня — тоже due, как процедуры)."""
    days = sorted(d for d, doc in hist.items() if doc.get("f") and d <= today.isoformat())
    t = today.isoformat()
    done = bool(days) and days[-1] == t
    before = [d for d in days if d < t]
    last = days[-1] if days else None
    prev = datetime.date.fromisoformat(before[-1]) if before else None
    nxt = (prev + datetime.timedelta(days=PHOTO_EVERY)) if prev else today
    if done:
        nxt = today + datetime.timedelta(days=PHOTO_EVERY)
    active = bool(active or days)
    return {"due": active and (done or nxt <= today), "done": done, "count": len(days), "last": last,
            "next": nxt.isoformat(), "days": (today - datetime.date.fromisoformat(last)).days if last else None,
            "every": PHOTO_EVERY}


def photo_dir(root, uid):
    return os.path.join(root, "photos", "skin", str(int(uid)))


def _pday(v):
    """Дата фото «ГГГГ-ММ-ДД» строго (никаких «..», слэшей и чужих путей)."""
    s = str(v if v is not None else "")
    try:
        if not DAY_RX.fullmatch(s):
            raise ValueError
        datetime.date.fromisoformat(s)
    except ValueError:
        raise ValueError("Фото: неверная дата") from None
    return s


def jpeg_size(raw):
    """(ширина, высота) из заголовка JPEG; не JPEG или битый — ValueError."""
    if len(raw) < 100 or raw[:3] != b"\xff\xd8\xff":
        raise ValueError("Это не фото — нужен снимок JPEG")
    i, n = 2, len(raw)
    while i + 9 < n:
        if raw[i] != 0xFF:
            break
        m = raw[i + 1]
        if m == 0xFF:
            i += 1
            continue
        if 0xD0 <= m <= 0xD8 or m == 0x01:
            i += 2
            continue
        size = int.from_bytes(raw[i + 2:i + 4], "big")
        if size < 2 or m in (0xDA, 0xD9):  # данные кадра или конец раньше размеров
            break
        if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):
            h, w = int.from_bytes(raw[i + 5:i + 7], "big"), int.from_bytes(raw[i + 7:i + 9], "big")
            if w and h:
                return w, h
            break
        i += 2 + size
    raise ValueError("Фото не читается — сделай снимок ещё раз")


def decode_jpeg(v, limit=MAX_PHOTO):
    """base64 или data:image/jpeg;base64,… → байты JPEG с проверкой размера и заголовка."""
    s = str(v or "")
    m = re.match(r"data:image/(?:jpeg|jpg);base64,", s)
    if not m and s.startswith("data:"):
        raise ValueError("Фото: нужен JPEG")
    body = s[m.end():] if m else s
    if not body:
        raise ValueError("Нет фото")
    if len(body) > limit * 4 // 3 + 8:
        raise ValueError(f"Фото больше {limit // 1024} КБ — сделай снимок ещё раз")
    try:
        raw = base64.b64decode(body, validate=True)
    except ValueError:  # binascii.Error — тоже ValueError
        raise ValueError("Не получилось прочитать фото") from None
    if len(raw) > limit:
        raise ValueError(f"Фото больше {limit // 1024} КБ — сделай снимок ещё раз")
    w, h = jpeg_size(raw)
    if max(w, h) > MAX_SIDE:
        raise ValueError("Фото слишком большое — до 2048 точек по стороне")
    return raw


def _write(path, raw):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")  # 0600, своё имя у каждого запроса
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
        os.replace(tmp, path)  # повтор за день — заменяет целиком, без полузаписанного файла
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def photo_days(root, uid):
    """Дни, за которые есть фото (по файлам), по возрастанию."""
    try:
        names = os.listdir(photo_dir(root, uid))
    except OSError:
        return []
    return sorted(m.group(1) for m in map(FILE_RX.fullmatch, names) if m)


def _set_flag(db, uid, day, on):
    doc = day_doc(db, uid, day)
    if doc["f"] != on:
        doc["f"] = on
        put_doc(db, uid, day, doc)


def save_photo(db, root, uid, b, today):
    raw = decode_jpeg(b.get("image"))
    thumb = decode_jpeg(b["thumb"], MAX_THUMB) if b.get("thumb") else None
    day = today.isoformat()
    folder = photo_dir(root, uid)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    _write(os.path.join(folder, f"{day}.jpg"), raw)
    tp = os.path.join(folder, f"{day}.t.jpg")
    if thumb:
        _write(tp, thumb)
    elif os.path.exists(tp):  # старая миниатюра от прошлого снимка за этот день — не к этому фото
        os.remove(tp)
    _set_flag(db, uid, day, True)
    return day


def get_photo(root, uid, b):
    day = _pday(b.get("day"))
    folder = photo_dir(root, uid)
    path = os.path.join(folder, f"{day}.jpg")
    if b.get("thumb") and os.path.isfile(os.path.join(folder, f"{day}.t.jpg")):
        path = os.path.join(folder, f"{day}.t.jpg")
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        raise ValueError("Фото за этот день нет") from None
    return {"day": day, "image": "data:image/jpeg;base64," + base64.b64encode(raw).decode()}


def list_photos(db, root, uid, today):
    """Список по файлам; отметки «f» в днях кожи подтягиваются к файлам (удалили руками — отметка уходит)."""
    days = photo_days(root, uid)
    have = set(days)
    start = today - datetime.timedelta(days=HISTORY)
    for d, doc in history(db, uid, start, today).items():
        if doc["f"] and d not in have:
            _set_flag(db, uid, d, False)
    for d in days:
        if start.isoformat() <= d <= today.isoformat() and not day_doc(db, uid, d)["f"]:
            _set_flag(db, uid, d, True)
    return {"photos": [{"day": d} for d in reversed(days)], "count": len(days)}


def delete_photo(db, root, uid, b):
    day = _pday(b.get("day"))
    folder, gone = photo_dir(root, uid), False
    for name in (f"{day}.jpg", f"{day}.t.jpg"):
        try:
            os.remove(os.path.join(folder, name))
            gone = True
        except FileNotFoundError:
            pass
    if not gone:
        raise ValueError("Фото за этот день нет")
    _set_flag(db, uid, day, False)


# ---------- подключение ----------

def route(app, uid, path, b, today):
    if not path.startswith("/api/skin"):
        return None
    db = app.db
    b = b if isinstance(b, dict) else {}
    if path == "/api/skin":
        return payload(db, uid, today)
    if path == "/api/skin/save":
        save(db, uid, b, today)
        return payload(db, uid, today)
    if path == "/api/skin/mark":
        mark(db, uid, b, today)
        return payload(db, uid, today)
    if path == "/api/skin/rate":
        rate(db, uid, b, today)
        return payload(db, uid, today)
    if path == "/api/skin/import":
        drop = b.get("drop") if isinstance(b.get("drop"), list) else None
        r = import_text(db, uid, b.get("text"), today, bool(b.get("preview")), drop)
        return r if b.get("preview") else {**payload(db, uid, today), **r}
    root = app.DATA
    if path in ("/api/skin/photo", "/api/skin/photo/get"):
        if path == "/api/skin/photo" and "image" in b:
            save_photo(db, root, uid, b, today)
            return {**payload(db, uid, today), **list_photos(db, root, uid, today)}
        return get_photo(root, uid, b)
    if path == "/api/skin/photos":
        return list_photos(db, root, uid, today)
    if path == "/api/skin/photo/delete":
        delete_photo(db, root, uid, b)
        return {**payload(db, uid, today), **list_photos(db, root, uid, today)}
    return None


def extra(app, uid, today):
    """В /api/all (ext.skin): только сегодня, серия и есть ли уход — главной и вечернему чек-листу режима."""
    p = payload(app.db, uid, today)
    return {"has": p["has"], "today": p["today"], "streak": p["streak"],
            "rated": p["stats"]["rated"], "notes": any(p["notes"].values())}


def _steps(xs):
    return ", ".join(x["title"] + (f" ({x['product']})" if x["product"] else "") for x in xs)


def context_lines(app, uid, today):
    p = payload(app.db, uid, today)
    notes = {k: v for k, v in p["notes"].items() if v}
    s, t = p["stats"], p["today"]
    ph = t["photo"]
    if not p["has"] and not notes and not s["rated"] and not ph["count"]:
        return []
    out = []
    r = p["routine"]
    if p["has"]:
        parts = []
        if r["am"]:
            parts.append("утро — " + _steps(r["am"]))
        if r["pm"]:
            parts.append("вечер — " + _steps(r["pm"]))
        if r["extra"]:
            parts.append("процедуры — " + ", ".join(f"{x['title']} ({x['sched']})" for x in r["extra"]))
        out.append("УХОД ЗА КОЖЕЙ (настроил сам): " + "; ".join(parts))
    if notes:
        out.append("Про кожу (написал сам): " + "; ".join(f"{NOTE_WORD[k]} — {v}" for k, v in notes.items()))
    if p["has"]:
        bits = []
        if t["am_total"]:
            bits.append(f"утренний уход {t['am_done']}/{t['am_total']}")
        if t["pm_total"]:
            bits.append(f"вечерний {t['pm_done']}/{t['pm_total']}" + (f" (осталось: {_steps([x for x in t['pm'] if not x['done']])})"
                                                                      if t["pm_done"] < t["pm_total"] else ""))
        for x in t["extra"]:
            bits.append(f"{x['title'].lower()} по плану сегодня — {'сделано' if x['done'] else 'не сделано'}")
        if ph["due"]:
            bits.append(f"фото лба по плану сегодня — {'сделано' if ph['done'] else 'не сделано'}")
        bits.append(f"серия ухода {p['streak']} дн. (лучшая {p['best']})")
        out.append("Кожа сегодня (отмечает сам): " + "; ".join(bits))
    rated = [(x["day"], x["r"]) for x in p["diary"] if x["r"]]
    if rated:
        line = "Дневник кожи (оценивает сам, 1 — плохо, 5 — отлично): "
        line += (f"сегодня {t['rating']} — {t['word']}" + (f" («{t['note']}»)" if t["note"] else "") + "; ") if t["rating"] else "сегодня не оценил; "
        if s["avg7"] is not None:
            line += f"за 7 дней в среднем {fnum(s['avg7'])}"
        if s["avg30"] is not None:
            line += f", за 30 — {fnum(s['avg30'])}"
        if s["trend"]:
            line += f", неделя {s['trend']} прошлой" if s["trend"] != "так же" else ", неделя как прошлая"
        line += "; последние оценки: " + ", ".join(f"{d[8:10]}.{d[5:7]} — {v}" for d, v in rated[-7:])
        if s["link"]:
            line += (f". С вечерним уходом кожа в среднем {fnum(s['link']['with'])}, без него — {fnum(s['link']['without'])}")
        out.append(line)
    if ph["count"]:
        d = ph["last"]
        ago = {0: "сегодня", 1: "вчера"}.get(ph["days"], f"{ph['days']} дн. назад")
        out.append(f"Фото лба (делает сам раз в {PHOTO_EVERY} дня вечером после умывания, чтобы видеть, как меняются прыщи; "
                   f"сами фото тебе не показываются): всего {ph['count']}, последнее {d[8:10]}.{d[5:7]} ({ago}); "
                   + ("сегодня по плану — " + ("сделано" if ph["done"] else "ещё не сделано") if ph["due"]
                      else f"следующее {ph['next'][8:10]}.{ph['next'][5:7]}"))
    out.append("Советуй по коже бережно: простой уход по его списку, без рецептурных средств и диагнозов; "
               "сыпь, сильные, болезненные или гнойные прыщи, зуд, отёк, пятна — к дерматологу и сказать родителям.")
    return out


def remind_line(db, uid, day):
    """Строка в вечернее напоминание об отбое (coach.regime_remind ставит перед ней «🧴 »): невыполненный вечерний уход
    и процедуры на сегодня, в день фото — второй строкой PHOTO_LINE; нечего — ""."""
    p = payload(db, uid, day)
    t = p["today"]
    left = [x["title"] for x in t["pm"] if not x["done"]] + [x["title"] for x in t["extra"] if not x["done"]]
    line = ("Уход на ночь: " + ", ".join(left[:4]) + (f" и ещё {len(left) - 4}" if len(left) > 4 else "")) if left else ""
    if t["photo"]["due"] and not t["photo"]["done"]:
        line = f"{line}\n{PHOTO_LINE}" if line else "Уход на ночь: 📸 фото лба после умывания"
    return line


def evening_titles(db, uid):
    """Названия вечерних шагов — режим не повторяет их в своём чек-листе."""
    return [s["title"] for s in load(db, uid)["pm"]]


def same(a, b):
    """Пункт режима и шаг ухода — об одном и том же («Крем на лицо» и «Крем»)?"""
    na, nb = (re.sub(r"[^а-яa-z ]", "", x.lower().replace("ё", "е")).split() for x in (a, b))
    if not na or not nb:
        return False
    return na == nb or (na[0] == nb[0] and len(na[0]) >= 4)  # «Крем на лицо» и «Крем на ночь» — один крем
