"""Быстрое добавление из чата: «завтра в 18:00 бокс», «по пн и ср английский», «12.10 контрольная!».

parse(text, today) -> {"title", "date", "time", "repeat", "important"}
"""
import datetime
import re

DAYS = {  # начало слова -> день недели (1 = пн)
    "пн": 1, "понед": 1, "вт": 2, "вторн": 2, "ср": 3, "сред": 3, "чт": 4, "четв": 4,
    "пт": 5, "пятн": 5, "сб": 6, "суб": 6, "вс": 7, "воскр": 7,
}
DAY_WORD = r"(?:пн|вт|ср|чт|пт|сб|вс|понедельник\w*|вторник\w*|сред[аыуе]|четверг\w*|пятниц[аыуе]|суббот[аыуе]|воскресень[еяю])"
MONTHS = ["январ", "феврал", "март", "апрел", "ма", "июн", "июл", "август", "сентябр", "октябр", "ноябр", "декабр"]
MONTH_WORD = r"(январ[ья]|феврал[ья]|марта?|апрел[ья]|ма[йя]|июн[ья]|июл[ья]|августа?|сентябр[ья]|октябр[ья]|ноябр[ья]|декабр[ья])"
B = r"(?<![\w])"   # граница слова слева (\b в Python не любит кириллицу рядом с цифрами)
E = r"(?![\w])"    # граница справа


def day_num(word):
    w = word.lower()
    for k, v in sorted(DAYS.items(), key=lambda kv: -len(kv[0])):
        if w.startswith(k):
            return v
    return None


def month_num(word):
    w = word.lower()
    if w.startswith("ма") and not w.startswith("мар"):
        return 5
    for i, m in enumerate(MONTHS):
        if w.startswith(m) and m != "ма":
            return i + 1
    return None


def parse(text, today=None):
    today = today or datetime.date.today()
    s = " " + text.strip() + " "
    out = {"title": "", "date": today, "time": None, "repeat": "", "important": False}

    def cut(rx, fn):
        nonlocal s
        m = re.search(rx, s, re.I)
        if not m:
            return False
        if fn(m) is False:
            return False
        s = s[:m.start()] + " " + s[m.end():]
        return True

    # повтор
    def rep(value):
        def f(m):
            out["repeat"] = value
        return f
    cut(B + r"(?:каждый день|ежедневно)" + E, rep("1234567"))
    cut(B + r"по будням" + E, rep("12345"))
    cut(B + r"по выходным" + E, rep("67"))

    def days_list(m):
        nums = sorted({day_num(w) for w in re.findall(DAY_WORD, m.group(1), re.I)} - {None})
        if not nums:
            return False
        out["repeat"] = "".join(map(str, nums))
    cut(B + r"(?:кажд(?:ый|ую|ое|ые)|по)\s+(" + DAY_WORD + r"(?:\s*(?:,|и)\s*" + DAY_WORD + r")*)" + E, days_list)

    # дата
    def rel(n):
        def f(m):
            out["date"] = today + datetime.timedelta(days=n)
        return f
    cut(B + r"послезавтра" + E, rel(2)) or cut(B + r"завтра" + E, rel(1)) or cut(B + r"сегодня" + E, rel(0))

    def after(m):
        n = int(m.group(1)) if m.group(1) else 1
        unit = m.group(2).lower()
        out["date"] = today + datetime.timedelta(days=n * (7 if unit.startswith("нед") else 1))
    cut(B + r"через\s+(\d+\s+)?(дн[яейь]|день|недел[юиь])" + E, after)

    def weekday(m):
        d = day_num(m.group(1))
        out["date"] = today + datetime.timedelta(days=(d - today.isoweekday()) % 7)
    if not out["repeat"]:
        cut(B + r"(?:во?\s+)?(" + DAY_WORD + r")" + E, weekday)

    def fix_year(d):  # «5.10», сказанное 9 октября, — это 5 октября; давно прошедшая дата — следующий год
        return d.replace(year=d.year + 1) if d < today - datetime.timedelta(days=30) else d

    def dotted(m):
        dd, mm, yy = int(m.group(1)), int(m.group(2)), m.group(3)
        try:
            d = datetime.date(int(yy) + (2000 if len(yy) == 2 else 0) if yy else today.year, mm, dd)
        except ValueError:
            return False
        out["date"] = d if yy else fix_year(d)
    cut(B + r"(\d{1,2})\.(\d{1,2})(?:\.(\d{4}|\d{2}))?" + E, dotted)

    def worded(m):
        try:
            d = datetime.date(today.year, month_num(m.group(2)), int(m.group(1)))
        except (ValueError, TypeError):
            return False
        out["date"] = fix_year(d)
    cut(B + r"(\d{1,2})\s+" + MONTH_WORD + E, worded)

    # время
    def hm(m):
        out["time"] = f"{int(m.group(1)):02d}:{m.group(2)}"
    found = cut(B + r"(?:в\s+)?([01]?\d|2[0-3]):([0-5]\d)" + E, hm)

    def hour(m):
        h = int(m.group(1))
        part = (m.group(2) or "").lower()
        if part.startswith(("вечер", "дня")) and h < 12:
            h += 12
        if part.startswith("ноч") and h == 12:
            h = 0
        out["time"] = f"{h:02d}:00"
    if not found:
        cut(B + r"в\s+([01]?\d|2[0-3])(?:\s*ч(?:аса|асов|ас)?\.?)?(?:\s+(утра|вечера|дня|ночи))?" + E, hour)

    # важность
    if "!" in s:
        out["important"] = True
        s = s.replace("!", " ")

    title = re.sub(r"\s+", " ", s).strip(" ,.;:-—")
    title = re.sub(r"^(?:в|во|на)\s+", "", title, flags=re.I) if len(title.split()) > 1 else title
    out["title"] = title[:1].upper() + title[1:]
    if out["repeat"] and out["date"] < today:
        out["date"] = today
    return out
