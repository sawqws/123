"""Вода: разбор «выпил 500 мл», «2 стакана воды», «+300», норма на день и оценка «идёшь ли по графику».

Норма — из питания (35 мл на кг веса + 10 мл на минуту тренировки, insights.nutrition); без веса — 2 л + тренировки.
График: пить равномерно с подъёма до часа до отбоя (по режиму дня; без режима — с 08:00 до 21:00) — к каждому часу
должна быть выпита своя доля нормы (Глеб 05.11: «пишет до 21:00 — не то время», у него отбой 22:45).
Только стандартная библиотека, без сети: это считается мгновенно, Claude не нужен.
"""
import re

GLASS, MUG, BOTTLE = 250, 300, 500
DAY_START, DAY_END = 8, 21  # пьём с 8 до 21
DEFAULT_NORM = 2000

NUM_WORDS = {"один": 1, "одну": 1, "одна": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6,
             "полтора": 1.5, "полторы": 1.5, "пол": 0.5, "половину": 0.5}
WATER = re.compile(r"\bвод(а|ы|у|ой|ичк[аиу]|ичкой)\b|💧|\bпопил\b")
OTHER = re.compile(r"\b(сок\w*|чай|чая|кофе|молок\w*|кол\w|газировк\w*|энергетик\w*|компот\w*|кефир\w*|"
                   r"какао|морс\w*|лимонад\w*|протеин\w*|коктейл\w*|пив\w*)\b")
NUM = r"(\d+(?:[.,]\d+)?|" + "|".join(NUM_WORDS) + r")"
AMOUNT = re.compile(NUM + r"\s*(мл|ml|миллилитр\w*|л|l|литр\w*)\b")
CONTAINER = re.compile(r"(?:" + NUM + r"\s*)?(стакан\w*|кружк\w*|кружеч\w*|чашк\w*|бутылк\w*|бутылочк\w*)")
LITER_WORD = re.compile(r"\b(пол|полтора|полторы)?\s*литр\w*")


def _num(s):
    s = (s or "").lower()
    if s in NUM_WORDS:
        return NUM_WORDS[s]
    try:
        return float(s.replace(",", "."))
    except ValueError:
        return None


def parse(text):
    """Миллилитры воды из фразы или None, если это не про воду."""
    t = " " + (text or "").lower().replace("ё", "е").strip() + " "
    if "?" in t or re.match(r"\s*(почему|зачем|как|сколько|что|когда|нужно|надо|можно|стоит|норм)\b", t):
        return None  # вопрос про воду — это тренеру, а не запись
    if OTHER.search(t):
        return None  # чай, сок, протеин — это еда/напиток с калориями, а не вода
    bare = re.fullmatch(r"\s*\+\s*(\d+(?:[.,]\d+)?)\s*(мл|л|литр\w*)?\s*", t)
    unit_only = re.fullmatch(r"\s*(?:выпил\w*|попил\w*)?\s*(\d+(?:[.,]\d+)?)\s*(мл|ml|л|литр\w*)\s*", t)
    if not (WATER.search(t) or bare or unit_only):
        return None
    ml = None
    m = AMOUNT.search(t)
    if m:
        v = _num(m.group(1))
        ml = v * 1000 if m.group(2)[0] in "лl" and not m.group(2).startswith("ml") else v
    else:
        total, found = 0, False
        for m in CONTAINER.finditer(t):
            n = _num(m.group(1)) if m.group(1) else 1
            size = GLASS if m.group(2).startswith("стакан") else BOTTLE if m.group(2).startswith("бутыл") else MUG
            total += (n or 1) * size
            found = True
        if found:
            ml = total
        elif LITER_WORD.search(t):
            w = LITER_WORD.search(t).group(1)
            ml = {"пол": 500, "полтора": 1500, "полторы": 1500}.get(w, 1000)
        else:
            m = re.search(r"(\d+(?:[.,]\d+)?)", t)
            if m:
                v = _num(m.group(1))
                ml = v * 1000 if v <= 5 else v  # «вода 2» — литры, «вода 300» — миллилитры
            elif re.search(r"\b(вы|по)пил\w*", t):
                ml = GLASS  # «попил воды» — считаем стакан
    if ml is None or not 30 <= ml <= 5000:
        return None
    return int(round(ml / 10) * 10)


def norm(nutrition, train_minutes=0):
    """(мл на день, пояснение)."""
    if nutrition and nutrition.get("water"):
        base = "норма по возрасту" if nutrition.get("teen") else "35 мл на кг веса"
        return int(nutrition["water"]), base + (" + тренировка" if train_minutes else "")
    return int(round((DEFAULT_NORM + train_minutes * 10) / 50) * 50), "ориентир без веса — добавь вес в профиле"


def liters(ml):
    return f"{ml / 1000:.1f}".rstrip("0").rstrip(".").replace(".", ",") + " л" if ml >= 1000 else f"{int(ml)} мл"


def _h(hhmm):
    """«22:45» → 22.75; плохое — None."""
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(hhmm or "").strip())
    if not m or int(m[1]) > 23 or int(m[2]) > 59:
        return None
    return int(m[1]) + int(m[2]) / 60


def window(wake=None, bed=None):
    """Окно «пьём»: (начало, конец) в часах — с подъёма до часа до отбоя (режим дня); чего нет — 08:00 / 21:00.
    Отбой после полуночи — конец не позже 23:00; окно короче 6 ч — по умолчанию."""
    start, end = _h(wake), _h(bed)
    start = DAY_START if start is None else start
    if end is None:
        end = DAY_END
    else:
        end = (end + 24 if end < 12 else end) - 1  # «00:30» — это уже ночь
        end = min(end, 23.0)
    if end - start < 6:
        return float(DAY_START), float(DAY_END)
    return float(start), float(end)


def clock(h):
    """21.75 → «21:45»."""
    m = round(h * 60)
    return f"{m // 60 % 24:02d}:{m % 60:02d}"


def expected_share(hour, win=None):
    """Какая доля нормы должна быть выпита к этому часу (дробному)."""
    a, b = win or (DAY_START, DAY_END)
    return max(0.0, min(1.0, (hour - a) / (b - a)))


def bar(share, n=10):
    k = max(0, min(n, round(share * n)))
    return "▰" * k + "▱" * (n - k)


def assess(drank, target, hour, trained=False, win=None):
    """Оценка: (эмодзи, текст). hour — текущий час с минутами (13.5 = 13:30); win — окно (начало, конец), см. window()."""
    share = drank / target if target else 0
    left = max(0, target - drank)
    start, end = win or (DAY_START, DAY_END)
    exp = expected_share(hour, (start, end))
    until = f"до {clock(end)}" + (" (за час до отбоя)" if win else "")
    if share >= 1.5:
        return "🟡", "Уже больше полутора норм — дальше пей по жажде: перебор воды тоже вредит (вымывает соли)."
    if share >= 1:
        return "✅", "Норма на сегодня выполнена" + (" — с учётом тренировки." if trained else ".")
    if hour >= end:
        return "🌙", (f"До нормы не хватило {liters(left)}. Перед сном много не пей — сон будет прерываться; "
                     "завтра начни со стакана сразу после подъёма.")
    hours_left = max(1, end - hour)
    pace = left / hours_left
    if share + 0.1 >= exp:
        return "👍", (f"Идёшь по графику. Осталось {liters(left)} — примерно {liters(max(50, round(pace / 50) * 50))} "
                     f"в час {until}.")
    need_now = max(0, round((exp * target - drank) / 50) * 50)
    return "⚠️", (f"Отстаёшь: к этому времени стоило выпить ещё {liters(need_now)}. Выпей стакан сейчас, "
                 f"дальше по {liters(max(50, round(pace / 50) * 50))} в час {until}."
                 + (" После тренировки вода особенно важна." if trained else ""))


def short(drank, target, hour, trained=False, win=None):
    """Оценка одной строкой для чата: (эмодзи, «по графику, осталось 1,3 л»)."""
    mark, _ = assess(drank, target, hour, trained, win)
    left = max(0, target - drank)
    if mark == "🟡":
        return mark, "больше 1,5 нормы — дальше по жажде"
    if mark == "✅":
        return mark, "норма выполнена"
    if mark == "🌙":
        return mark, f"не хватило {liters(left)} — завтра начни со стакана"
    if mark == "👍":
        return mark, f"по графику, осталось {liters(left)}"
    need_now = max(50, round((expected_share(hour, win) * target - drank) / 50) * 50)
    return mark, f"отстаёшь на {liters(need_now)} — выпей стакан"


def card(added, drank, target, why, hour, trained=False, title=None, extra="", note=None, win=None):
    """Сообщение о воде в общем стиле бота: заголовок, цитата (выпито, полоска, оценка), примечание курсивом.
    Коротко: подробная оценка — в приложении."""
    mark, text = short(drank, target, hour, trained, win)
    pct = round(drank / target * 100) if target else 0
    head = title or (f"💧 <b>+{liters(added)} воды</b>" if added else "💧 <b>Вода сегодня</b>")
    body = (f"За день <b>{liters(drank)}</b> из {liters(target)} · {pct}%\n{bar(drank / target if target else 0)}\n"
            f"{mark} {text[0].upper() + text[1:]}" + (f"\n{extra}" if extra else ""))
    return f"{head}\n<blockquote>{body}</blockquote>" + (f"\n<i>{note}</i>" if note else "")


def day_line(drank, target):
    """Строка для контекста Claude и утреннего разбора."""
    return f"{liters(drank)} из {liters(target)} ({round(drank / target * 100) if target else 0}%)"
