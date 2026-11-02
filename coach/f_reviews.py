"""Разборы за день — лента «Разборы за сегодня» на главной: утро · после тренировки · вечер (до 3–4 за день).

Когда бот присылает их сам (короткое сообщение в Telegram с «📱 Открыть», полный текст — здесь, в приложении):
- утро — как только WHOOP посчитал сон и восстановление за сегодня (coach.react после синхронизации): основной сон
  (не дневной `nap`) закончился с 05:00 до 16:00 по его времени (`morning_sleep`). Ночное пробуждение (сон кончился
  раньше 05:00) — ждём до времени /morning, как раньше (coach.morning_due). Раз в день;
- после тренировки — оценённая тренировка сегодня от 15 мин и нагрузкой от 5 (`workout_ok`), не больше 2 за день
  (`take_workout`), каждая — один раз;
- вечер — один раз: за час до отбоя по режиму (если напоминание об отбое раньше — в его время и одним сообщением
  с ним), без режима — в 21:00 (`evening_due`).
Хранение — таблица reviews (store.SCHEMA): текст — Markdown для приложения; «новый» (seen=0), пока не открыт.
API: POST /api/reviews {day?} → {day, items: [{id, kind, time, title, text, seen}]}, POST /api/reviews/seen {id}.
В /api/all — ext.reviews {items, new} (сегодня). Тренер видит первые строки сегодняшних разборов.
"""
import datetime
import html
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


# ---------- правила: когда разбор ----------

def morning_sleep(s):
    """Сон (insights.sleep_of) — утренний: не дневной и закончился с 05:00 до 16:00."""
    return bool(s) and not s.get("nap") and WAKE_FROM <= (s.get("end") or "") < WAKE_TILL


def workout_ok(w):
    """Тренировка (insights.workout_of) достойна разбора: от 15 мин и нагрузка от 5."""
    return (w.get("minutes") or 0) >= WORKOUT_MINUTES and (w.get("strain") or 0) >= WORKOUT_STRAIN


def take_workout(db, uid, day, wid):
    """Занять место под разбор тренировки: каждая — один раз, за день — не больше WORKOUT_MAX."""
    if not db.once(f"workout:{wid}"):
        return False
    return any(db.once(f"review-workout:{uid}:{day}:{i}") for i in range(1, WORKOUT_MAX + 1))


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


def add(db, uid, day, kind, title, text, at, ref=""):
    """Сохранить разбор (тот же день, вид и ref — заменить и снова сделать «новым»). -> id."""
    if kind not in KINDS:
        raise ValueError("нет такого разбора")
    title = " ".join(str(title or "").split())[:TITLE_MAX]
    text = str(text or "").strip()[:TEXT_MAX]
    db.x("INSERT INTO reviews (user, day, kind, ref, at, title, text, seen, created) VALUES (?,?,?,?,?,?,?,0,?) "
         "ON CONFLICT (user, day, kind, ref) DO UPDATE SET at=excluded.at, title=excluded.title, text=excluded.text, "
         "seen=0, created=excluded.created",
         (uid, str(day), kind, str(ref or ""), at, title, text, datetime.datetime.now().isoformat(timespec="seconds")))
    r = db.q("SELECT id FROM reviews WHERE user=? AND day=? AND kind=? AND ref=?", (uid, str(day), kind, str(ref or "")))
    return r[0]["id"] if r else None


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
