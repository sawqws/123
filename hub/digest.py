"""Отбивка и напоминания «Главного». Чистые функции: без сети и Telegram, состояние — словарь st (state.json хаба).

Ключи в st:
  reminders   — [{id, time "ЧЧ:ММ", days [0..6] | None (каждый день), date "ГГГГ-ММ-ДД" | None (разово), text, sent "ГГГГ-ММ-ДД"}]
  rmd_seq     — счётчик id напоминаний
  digest_time — "ЧЧ:ММ" или "off" (нет ключа — DEFAULT_TIME)
  digest_day  — день, за который отбивка уже была

Опоздание (цикл раз в минуту мог пропустить минуты, хаб мог лежать): отбивка и повторяющиеся напоминания
шлются, если сейчас >= времени и сегодня ещё не слали, но не позже LATE (3 ч) — иначе день пропускается молча.
Разовые напоминания не теряются: если хаб лежал, придут при первой проверке с пометкой, когда должны были быть.
Время по Москве.

API для hub.py:
  due(st, now_ts, apps, server_info, paid_iso) -> [(text, buttons|None)]   (меняет st, вызывать в watch_once)
  command(st, text, now_ts) -> (reply, buttons|None) или None, если это не наша команда
  callback(st, data) -> (note, new_text, buttons) для "rmd|<id>", иначе None
  HELP_LINES
"""
import datetime
import html
import re

TZ = datetime.timezone(datetime.timedelta(hours=3))  # Москва
DEFAULT_TIME = "21:00"
LATE = 3 * 3600
DAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
USAGE = ("Как задать:\n"
         "• <code>/remind 09:00 текст</code> — каждый день\n"
         "• <code>/remind пн,ср 18:30 текст</code> — по дням (пн-пт, будни, выходные)\n"
         "• <code>/remind 25.10 10:00 текст</code> — один раз (или «завтра 10:00 …»)")
HELP_LINES = ("• /remind 09:00 текст — напоминание каждый день; «пн,ср 18:30 …», «будни 8:00 …» — по дням; "
              "«25.10 10:00 …» — один раз.\n"
              "• /reminders — список напоминаний и удаление.\n"
              "• /digest 21:00 — время вечерней отбивки (боты, сервер, оплата, напоминания); /digest off — выключить.\n")


def esc(x):
    return html.escape(str(x), quote=False)


def b(text, data):
    return {"text": text, "callback_data": data}


def _now(ts):
    return datetime.datetime.fromtimestamp(ts, TZ)


def _fmt_d(d):
    return f"{d.day:02d}.{d.month:02d}"


# ---------- разбор ----------

def parse_time(s):
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", s.strip())
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return None
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def parse_days(s):
    """'пн,ср' / 'пн-пт' / 'будни' / 'выходные' / '' -> список дней недели (0=пн) или None = каждый день.
    Ошибка — ValueError."""
    s = s.lower().replace("ё", "е").strip()
    if s in ("", "ежедневно", "каждый день", "всегда"):
        return None
    days = set()
    for tok in re.split(r"[,\s]+", s):
        if not tok:
            continue
        if tok == "будни":
            days |= {0, 1, 2, 3, 4}
        elif tok == "выходные":
            days |= {5, 6}
        elif re.fullmatch(r"[а-я]{2}-[а-я]{2}", tok) and tok[:2] in DAYS and tok[3:] in DAYS:
            i, j = DAYS.index(tok[:2]), DAYS.index(tok[3:])
            days |= {(i + k) % 7 for k in range((j - i) % 7 + 1)}
        elif tok in DAYS:
            days.add(DAYS.index(tok))
        else:
            raise ValueError(tok)
    return None if len(days) == 7 else sorted(days)


def parse_remind(args, now_ts):
    """'[когда] ЧЧ:ММ текст' -> {time, days, date, text}; ошибка — ValueError с текстом для человека."""
    m = re.fullmatch(r"(.*?)\s*(\d{1,2}:\d{2})\s+(.+)", args.strip(), re.S)
    if not m:
        raise ValueError("нужно время ЧЧ:ММ и текст")
    when, tm, text = m.group(1).strip(), parse_time(m.group(2)), m.group(3).strip()
    if not tm:
        raise ValueError(f"нет такого времени: {m.group(2)}")
    now = _now(now_ts)
    today = now.date()
    date, days = None, None
    dm = re.fullmatch(r"(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?", when)
    if when.lower() in ("сегодня", "завтра"):
        date = today + datetime.timedelta(days=when.lower() == "завтра")
    elif dm:
        y = dm.group(3)
        year = (int(y) + 2000 if len(y) == 2 else int(y)) if y else today.year
        try:
            date = datetime.date(year, int(dm.group(2)), int(dm.group(1)))
        except ValueError:
            raise ValueError(f"нет такой даты: {when}")
        if not y and date < today:
            date = date.replace(year=year + 1)
    else:
        try:
            days = parse_days(when)
        except ValueError as e:
            raise ValueError(f"не понял дни «{e}» — пиши пн вт ср чт пт сб вс, пн-пт, будни, выходные")
    if date and (date < today or date == today and tm <= now.strftime("%H:%M")):
        raise ValueError("это время уже прошло")
    return {"time": tm, "days": days, "date": date.isoformat() if date else None, "text": text}


def describe(r):
    if r.get("date"):
        when = _fmt_d(datetime.date.fromisoformat(r["date"]))
    elif r.get("days") is None:
        when = "каждый день"
    elif r["days"] == [0, 1, 2, 3, 4]:
        when = "по будням"
    elif r["days"] == [5, 6]:
        when = "по выходным"
    else:
        when = "по " + ", ".join(DAYS[d] for d in r["days"])
    return f"{when} в {r['time']}"


def _on_day(r, d):
    return r["date"] == d.isoformat() if r.get("date") else r.get("days") is None or d.weekday() in r["days"]


# ---------- команды ----------

def reminders_view(st):
    rs = sorted(st.get("reminders") or [], key=lambda r: (r.get("date") or "", r["time"]))
    if not rs:
        return f"⏰ <b>Напоминаний нет</b>\n<blockquote>{USAGE}</blockquote>", None
    lines = []
    rows = []
    for i, r in enumerate(rs, 1):
        lines.append(f"{i}. {describe(r)} — {esc(r['text'])}")
        short = r["text"] if len(r["text"]) <= 18 else r["text"][:17].rstrip() + "…"
        rows.append([b(f"🗑 {i}. {short}", f"rmd|{r['id']}")])
    return "⏰ <b>Напоминания</b>\n<blockquote>" + "\n".join(lines) + "</blockquote>\n<i>Нажми, чтобы удалить.</i>", rows


def command(st, text, now_ts):
    text = (text or "").strip()
    if not text.startswith("/"):
        return None
    cmd, _, args = text.partition(" ")
    cmd = cmd.split("@")[0].lower()
    args = args.strip()
    if cmd == "/reminders":
        return reminders_view(st)
    if cmd == "/remind":
        if not args:
            return USAGE, None
        try:
            r = parse_remind(args, now_ts)
        except ValueError as e:
            return f"Не понял: {esc(e)}.\n\n{USAGE}", None
        st["rmd_seq"] = int(st.get("rmd_seq") or 0) + 1
        r["id"] = str(st["rmd_seq"])
        now = _now(now_ts)
        # время сегодня уже прошло — сегодня не слать (иначе пришло бы сразу)
        r["sent"] = now.date().isoformat() if not r["date"] and r["time"] <= now.strftime("%H:%M") else None
        st.setdefault("reminders", []).append(r)
        return f"✅ Напомню {describe(r)}: {esc(r['text'])}", [[b("⏰ Все напоминания", "rmd|list")]]
    if cmd == "/digest":
        a = args.lower()
        if a in ("off", "выкл", "нет", "0"):
            st["digest_time"] = "off"
            return "🔕 Отбивка выключена. Включить: /digest 21:00", None
        if a:
            tm = parse_time(a)
            if not tm:
                return "Пришли время так: /digest 21:00 (или /digest off)", None
            st["digest_time"] = tm
            if tm > _now(now_ts).strftime("%H:%M"):
                st.pop("digest_day", None)  # перенесли на попозже — сегодня ещё придёт
            return f"✅ Отбивка каждый день в {tm}.", None
        cur = st.get("digest_time") or DEFAULT_TIME
        return ("🔕 Отбивка выключена." if cur == "off" else f"📋 Отбивка каждый день в {cur}.") + \
            "\n/digest 21:00 — другое время, /digest off — выключить.", None
    return None


def callback(st, data):
    if not (data or "").startswith("rmd|"):
        return None
    rid = data[4:]
    if rid == "list":
        return ("", *reminders_view(st))
    rs = st.get("reminders") or []
    left = [r for r in rs if r["id"] != rid]
    st["reminders"] = left
    return ("🗑 Удалено" if len(left) < len(rs) else "Уже удалено", *reminders_view(st))


# ---------- отбивка ----------

def digest_text(st, now, apps, srv, paid_iso):
    today = now.date()
    lines = []
    if apps is None:
        lines.append("⚠️ pm2 не ответил — состояние ботов неизвестно")
    else:
        down = [a for a in apps if a.get("status") != "online"]
        ok = len(apps) - len(down)
        if down:
            lines.append("🔴 Не работают: " + ", ".join(f"{esc(a['label'])} ({esc(a['status'])})" for a in down))
            lines.append(f"🟢 Работают: {ok}")
        else:
            lines.append(f"🟢 Все боты работают ({ok})")
    srv = srv or {}
    parts = []
    if srv.get("mem_total"):
        warn = "⚠️ " if srv["mem_free"] / srv["mem_total"] < 0.1 else ""
        parts.append(f"{warn}память {srv['mem_free']}/{srv['mem_total']} МБ")
    if srv.get("disk_total"):
        warn = "⚠️ " if srv["disk_free"] / srv["disk_total"] < 0.1 else ""
        parts.append(f"{warn}диск {srv['disk_free']}/{srv['disk_total']} ГБ")
    if parts:
        lines.append("• Свободно: " + " · ".join(parts))
    if paid_iso:
        paid = datetime.date.fromisoformat(paid_iso)
        left = (paid - today).days
        lines.append(f"• Оплата до {_fmt_d(paid)}: " + (
            f"осталось {left} дн." if left > 0 else "срок сегодня!" if left == 0 else "срок прошёл! Продли."))
    else:
        lines.append("• Срок оплаты не указан — /paid ДД.ММ")
    rs = st.get("reminders") or []
    for label, d in (("Сегодня", today), ("Завтра", today + datetime.timedelta(days=1))):
        day = sorted((r for r in rs if _on_day(r, d)), key=lambda r: r["time"])
        if day:
            lines.append(f"• {label}: " + "; ".join(f"{r['time']} {esc(r['text'])}" for r in day))
    text = (f"📋 <b>Отбивка · {DAYS[today.weekday()]} {_fmt_d(today)}</b>\n<blockquote>" + "\n".join(lines)
            + "</blockquote>\n<i>Что доработать? Нажми ✏️ Доработать</i>")
    return text, [[b("✏️ Доработать", "fix")]]


def due(st, now_ts, apps, server_info, paid_iso):
    now = _now(now_ts)
    today, hm = now.date(), now.strftime("%H:%M")
    day = today.isoformat()

    def late(tm, d=today):
        h, m = map(int, tm.split(":"))
        return now_ts - datetime.datetime(d.year, d.month, d.day, h, m, tzinfo=TZ).timestamp()

    out = []
    keep = []
    for r in st.get("reminders") or []:
        if r.get("date"):
            d = datetime.date.fromisoformat(r["date"])
            if d < today or d == today and hm >= r["time"]:
                note = f"\n<i>(было на {_fmt_d(d)} {r['time']} — я не работал)</i>" if late(r["time"], d) > LATE else ""
                out.append((f"⏰ {esc(r['text'])}{note}", None))
                continue  # разовое — удаляем
        elif _on_day(r, today) and hm >= r["time"] and r.get("sent") != day:
            r["sent"] = day
            if late(r["time"]) <= LATE:
                out.append((f"⏰ {esc(r['text'])}", None))
        keep.append(r)
    if "reminders" in st:
        st["reminders"] = keep
    tm = st.get("digest_time") or DEFAULT_TIME
    if tm != "off" and hm >= tm and st.get("digest_day") != day:
        st["digest_day"] = day
        if late(tm) <= LATE:
            out.append(digest_text(st, now, apps, server_info, paid_iso))
    return out
