"""Разборы за день — лента «Разборы за сегодня» на главной: утро · после тренировки · вечер (до 3–4 за день).

Когда бот присылает их сам (короткое сообщение в Telegram с «📱 Открыть», полный текст — здесь, в приложении):
- утро — как только WHOOP посчитал сон и восстановление за сегодня (coach.react после синхронизации): основной сон
  (не дневной `nap`) закончился с 05:00 до 16:00 по его времени (`morning_sleep`). Ночное пробуждение (сон кончился
  раньше 05:00) — ждём до времени /morning, как раньше (coach.morning_due). Раз в день;
- после тренировки — оценённая тренировка сегодня от 15 мин и нагрузкой от 5 (`workout_ok`), не больше 2 за день
  (`take_workout`), каждая — один раз;
- вечер — один раз: за час до отбоя по режиму (если напоминание об отбое раньше — в его время и одним сообщением
  с ним), без режима — в 21:00 (`evening_due`).
Данные поменялись в WHOOP (Глеб 03.11: «я изменяю, а разбор уже сделан и не меняется») — coach.refresh_reviews после
каждой синхронизации: у тренировки сменились вид спорта, время или нагрузка (≥ 0,3) — разбор пересоздаётся на том же
месте (новый текст, снова «новый», прежнее сообщение в Telegram — тихо правится, нового нет); тренировку удалили или
она стала «не тренировкой» (массаж, короче 15 мин) — разбор убирается и место за день освобождается. Утро — так же,
если восстановление сдвинулось на ≥ 3 или сон на ≥ 0,25 ч. Вечерний итог не пересоздаётся.
Хранение — таблица reviews (store.SCHEMA): текст — Markdown для приложения; «новый» (seen=0), пока не открыт;
msg — id сообщения в Telegram, src — по каким данным сделан (workout_src / morning_src).
API: POST /api/reviews {day?} → {day, items: [{id, kind, time, title, text, seen}]}, POST /api/reviews/seen {id}.
В /api/all — ext.reviews {items, new} (сегодня). Тренер видит первые строки сегодняшних разборов.
"""
import datetime
import html
import json
import re

import f_regime

KINDS = ("morning", "workout", "evening")
ORDER = {"morning": 0, "workout": 1, "evening": 2}
WAKE_FROM, WAKE_TILL = "05:00", "16:00"  # основной сон закончился в эти часы — это утро, а не ночное пробуждение
WORKOUT_MAX = 2  # разборов тренировок в день
WORKOUT_MINUTES, WORKOUT_STRAIN = 15, 5  # короче или легче — без разбора (прогулка, разминка)
EVENING_LEAD = 60  # вечерний итог — за столько минут до отбоя по режиму
EVENING_DEFAULT = datetime.time(21, 0)  # режим не настроен
EVENING_LATE = datetime.timedelta(hours=2)  # сервер лежал дольше — вечерний итог за этот день уже не шлём
BACK_DAYS = 7  # ленту можно открыть за сегодня и 7 дней назад
TEXT_MAX, TITLE_MAX = 6000, 80
STRAIN_STEP = 0.3  # нагрузка тренировки пересчиталась на столько — разбор пересоздать
RECOVERY_STEP, SLEEP_STEP = 3, 0.25  # утро: восстановление (%), сон (ч)


# ---------- правила: когда разбор ----------

def morning_sleep(s):
    """Сон (insights.sleep_of) — утренний: не дневной и закончился с 05:00 до 16:00."""
    return bool(s) and not s.get("nap") and WAKE_FROM <= (s.get("end") or "") < WAKE_TILL


def workout_ok(w):
    """Тренировка (insights.workout_of) достойна разбора: от 15 мин и нагрузка от 5."""
    return not w.get("rest") and (w.get("minutes") or 0) >= WORKOUT_MINUTES and (w.get("strain") or 0) >= WORKOUT_STRAIN


def take_workout(db, uid, day, wid):
    """Занять место под разбор тренировки: каждая — один раз, за день — не больше WORKOUT_MAX."""
    if not db.once(f"workout:{wid}"):
        return False
    return any(db.once(f"review-workout:{uid}:{day}:{i}") for i in range(1, WORKOUT_MAX + 1))


def free_workout(db, uid, day):
    """Разбор тренировки убран (её удалили в WHOOP) — место за день снова свободно."""
    for i in range(WORKOUT_MAX, 0, -1):
        if db.was_sent(f"review-workout:{uid}:{day}:{i}"):
            db.unsend(f"review-workout:{uid}:{day}:{i}")
            return


# ---------- данные поменялись: пересоздать разбор ----------

def workout_src(w):
    """По каким данным разбор тренировки (insights.workout_of): вид спорта, начало, минуты, нагрузка."""
    return {"sport": w.get("sport_key") or w.get("sport") or "", "start": w.get("start") or "",
            "minutes": w.get("minutes"), "strain": w.get("strain")}


def workout_changed(old, new):
    """Тренировка в WHOOP заметно поменялась: вид спорта, время (начало или длина) или нагрузка на ≥ 0,3."""
    if not old:
        return False
    if (old.get("sport"), old.get("start"), old.get("minutes")) != (new.get("sport"), new.get("start"), new.get("minutes")):
        return True
    a, b = old.get("strain"), new.get("strain")
    return a is None and b is not None or a is not None and b is not None and abs(b - a) >= STRAIN_STEP - 1e-9


def morning_src(d):
    """По каким данным утренний разбор: восстановление (%) и часы сна за день (insights.by_day / today)."""
    d = d or {}
    return {"recovery": (d.get("recovery") or {}).get("score"), "sleep": (d.get("sleep") or {}).get("hours")}


def morning_changed(old, new):
    """WHOOP пересчитал утро заметно: восстановление ±3, сон ±0,25 ч (или появилось то, чего не было).
    Пропало (снова считается) — ждём, не пересоздаём."""
    if not old:
        return False
    for k, step in (("recovery", RECOVERY_STEP), ("sleep", SLEEP_STEP)):
        a, b = old.get(k), new.get(k)
        if b is not None and (a is None or abs(b - a) >= step - 1e-9):
            return True
    return False


def _at(d, hhmm, tz):
    h, m = (int(x) for x in hhmm.split(":"))
    at = datetime.datetime.combine(d, datetime.time(h, m), tzinfo=tz)
    return at + datetime.timedelta(days=1) if h < 12 else at  # отбой после полуночи — уже следующие сутки


def evening_window(cfg, d, tz=None):
    """(с, до) вечернего итога дня d. cfg — режим (если настроен) или None."""
    if not cfg:
        at = datetime.datetime.combine(d, EVENING_DEFAULT, tzinfo=tz)
        return at, at + EVENING_LATE
    bed = _at(d, f_regime.bed_wake(cfg, d)[0], tz)
    lead = max(EVENING_LEAD, cfg.get("remind") or 0)
    return bed - datetime.timedelta(minutes=lead), bed


def evening_due(cfg, now):
    """День, вечерний итог которого пора прислать (now — время пользователя), или None."""
    for d in (now.date(), now.date() - datetime.timedelta(days=1)):
        a, b = evening_window(cfg, d, now.tzinfo)
        if a <= now < min(b, a + EVENING_LATE):
            return d
    return None


# ---------- хранение ----------

def html_md(h):
    """HTML Telegram (разбор по правилам: <b>, «• ») -> простой Markdown приложения."""
    s = re.sub(r"</?b>", "**", h or "")
    s = re.sub(r"</?i>", "", s)
    s = re.sub(r"(?m)^• ", "- ", s)
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def add(db, uid, day, kind, title, text, at, ref="", src=None):
    """Сохранить разбор (тот же день, вид и ref — заменить и снова сделать «новым»). src — по каким данным
    (workout_src / morning_src). -> id."""
    if kind not in KINDS:
        raise ValueError("нет такого разбора")
    title = " ".join(str(title or "").split())[:TITLE_MAX]
    text = str(text or "").strip()[:TEXT_MAX]
    src = json.dumps(src, ensure_ascii=False) if src else None
    db.x("INSERT INTO reviews (user, day, kind, ref, at, title, text, seen, created, src) VALUES (?,?,?,?,?,?,?,0,?,?) "
         "ON CONFLICT (user, day, kind, ref) DO UPDATE SET at=excluded.at, title=excluded.title, text=excluded.text, "
         "seen=0, created=excluded.created, src=excluded.src",
         (uid, str(day), kind, str(ref or ""), at, title, text, datetime.datetime.now().isoformat(timespec="seconds"), src))
    r = db.q("SELECT id FROM reviews WHERE user=? AND day=? AND kind=? AND ref=?", (uid, str(day), kind, str(ref or "")))
    return r[0]["id"] if r else None


def rows(db, uid, day, kind):
    """Разборы вида kind за день — для пересоздания: {id, ref, at, msg, src (dict или None)}."""
    out = db.q("SELECT id, ref, at, msg, src FROM reviews WHERE user=? AND day=? AND kind=?", (uid, str(day), kind))
    for r in out:
        try:
            r["src"] = json.loads(r["src"]) if r["src"] else None
        except ValueError:
            r["src"] = None
    return out


def set_msg(db, rid, msg):
    """Запомнить сообщение в Telegram с этим разбором — пересоздание правит его тихо, нового не шлёт."""
    if rid and msg:
        db.x("UPDATE reviews SET msg=? WHERE id=?", (int(msg), rid))


def set_src(db, rid, src):
    """Разбор сделан до того, как стали запоминать данные, — запомнить нынешние как исходные (без пересоздания)."""
    db.x("UPDATE reviews SET src=? WHERE id=?", (json.dumps(src, ensure_ascii=False), rid))


def retarget(db, rid, ref):
    """Тренировку в WHOOP заменили новой (удалили и создали заново) — разбор переходит к ней."""
    db.x("UPDATE reviews SET ref=? WHERE id=?", (str(ref), rid))


def remove(db, uid, rid):
    """Убрать разбор (тренировку удалили в WHOOP или она не тренировка). Место за день освобождается."""
    r = db.q("SELECT day, kind FROM reviews WHERE id=? AND user=?", (rid, uid))
    db.x("DELETE FROM reviews WHERE id=? AND user=?", (rid, uid))
    if r and r[0]["kind"] == "workout":
        free_workout(db, uid, r[0]["day"])


def items(db, uid, day):
    rows = db.q("SELECT id, kind, at, title, text, seen FROM reviews WHERE user=? AND day=?", (uid, str(day)))
    rows.sort(key=lambda r: (ORDER.get(r["kind"], 9), r["at"] or ""))
    return [{"id": r["id"], "kind": r["kind"], "time": r["at"] or "", "title": r["title"] or "", "text": r["text"] or "",
             "seen": bool(r["seen"])} for r in rows]


def seen(db, uid, rid):
    try:
        rid = int(rid)
    except (TypeError, ValueError):
        raise ValueError("нет такого разбора") from None
    if db.x("UPDATE reviews SET seen=1 WHERE id=? AND user=?", (rid, uid)).rowcount != 1:
        raise ValueError("нет такого разбора")


def _day(v, today):
    if not v:
        return today
    try:
        d = datetime.date.fromisoformat(str(v))
    except ValueError:
        raise ValueError("день в виде ГГГГ-ММ-ДД") from None
    if not today - datetime.timedelta(days=BACK_DAYS) <= d <= today:
        raise ValueError("разборы — за сегодня и неделю назад")
    return d


# ---------- подключение ----------

def payload(db, uid, day):
    its = items(db, uid, day)
    return {"day": day.isoformat(), "items": its, "new": sum(not x["seen"] for x in its)}


def route(app, uid, path, b, today):
    if not path.startswith("/api/reviews"):
        return None
    b = b or {}
    if path == "/api/reviews":
        return payload(app.db, uid, _day(b.get("day"), today))
    if path == "/api/reviews/seen":
        seen(app.db, uid, b.get("id"))
        return {"ok": True, **payload(app.db, uid, _day(b.get("day"), today))}
    return None


def evening_at(app, uid, day):
    """Во сколько придёт вечерний итог дня (ЧЧ:ММ) — для подсказки в ленте."""
    cfg = f_regime.load(app.db, uid, app.profile(uid)) if f_regime.configured(app.db, uid) else None
    return evening_window(cfg, day)[0].strftime("%H:%M")


def extra(app, uid, today):
    """В /api/all (ext.reviews): разборы за сегодня, сколько новых, когда ждать утро и вечер."""
    p = payload(app.db, uid, today)
    try:
        p["evening_at"] = evening_at(app, uid, today)
    except Exception:  # режим сломался — лента всё равно есть
        p["evening_at"] = EVENING_DEFAULT.strftime("%H:%M")
    p["morning"] = (app.profile(uid).get("morning") or "07:00") != "off"
    return p


def context_lines(app, uid, today):
    its = items(app.db, uid, today)
    if not its:
        return []
    name = {"morning": "утро", "workout": "после тренировки", "evening": "вечер"}
    out = []
    for x in its:
        first = next((ln for ln in x["text"].splitlines() if ln.strip()), "")
        out.append(f"{name[x['kind']]} {x['time']}: {first.strip('-* ').replace('**', '')[:140]}")
    return ["РАЗБОРЫ СЕГОДНЯ (уже присланы ему): " + "; ".join(out)]
