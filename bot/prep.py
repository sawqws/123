"""📖 Подготовка к проверочной: что прошли по предмету (темы с сайта) -> конспект и вопросы за 1, 5 или 10 минут.

Здесь только логика без Telegram: предметы и «что прошли» (по темам из horo/prep.js), разбор просьбы
(«подготовь к проверочной по алгебре за 5 минут»), промпт для Claude (bot/prep_prompt.md) и вид результата
(HTML Telegram, ответы под спойлером). Чат, кнопки и приложение — в bot.py (раздел «Подготовка»).
"""
import datetime
import html
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
PROMPT = os.path.join(HERE, "prep_prompt.md")

MINUTES = (1, 5, 10)
ICON = {1: "⚡", 5: "📝", 10: "📚"}
BUTTON = {1: "⚡ 1 мин", 5: "📝 5 мин", 10: "📚 10 мин"}
SPAN = {1: "1 минуту", 5: "5 минут", 10: "10 минут"}
WINDOW = 28          # дней: что прошли «недавно» (тема закончилась не раньше, чем WINDOW дней назад)
LONG = 120           # тема дольше стольких дней — это курс на год (проект), а не тема к проверочной
MAX_TOPICS = 3
MAX_MATERIAL = 15000  # символов материалов в промпте

SKIP = re.compile(r"^(физическ|физкульт)", re.I)  # к физкультуре не готовят
# Математика на сайте — темы «А1. …» (алгебра) и «Г1. …» (геометрия): для Глеба это два предмета
SPLIT = {"Математика": [("Алгебра", "➗", re.compile(r"^\s*[АA]\s*\d")), ("Геометрия", "📐", re.compile(r"^\s*Г\s*\d"))]}
ALIASES = [  # как говорят -> начало названия предмета
    ("матем", ("Алгебра", "Геометрия", "Математика")), ("матеш", ("Алгебра", "Геометрия", "Математика")),
    ("обж", ("ОБиЗР", "ОБЖ")), ("инглиш", ("Английский",)), ("english", ("Английский",)),
    ("испанск", ("Испанский",)), ("общагу", ("Обществознание",)), ("русичу", ("Русский",)),
]
TRIGGER = re.compile(r"подготов|натаск|\bк\s+(провероч|контрольн|самостоят|зач[её]т|тест)|\bповтор(и|ить|им)\b", re.I)
STOP = set((
    "подготовь подготовить подготовка подготовку подготовиться подготовься подготовишь подготовим натаскай повтори "
    "повторить повторим меня мне нас к ко по за на в во и а с со мной минут минуту минуты минутку мин пожалуйста плиз "
    "проверочной проверочную проверочная проверочным контрольной контрольную контрольная самостоятельной "
    "самостоятельную зачету зачет тесту тест давай можешь можно хочу надо нужно теме темам тему темы предмету "
    "предмет одну одна пять пяти десять десяти быстро быстренько подробно подробнее кратко коротко срочно завтра "
    "сегодня будет у я ты бот привет ну плс язык языку языка языке уроку урок"
).split())
GENERIC = re.compile(r"^(подтверждение|test|тест)\s*[\d.,]*$|^зач[её]т по теории$|\b[1-4][.,]0\b", re.I)  # служебные


def esc(x):
    return html.escape(str(x), quote=False)


def today_iso(today=None):
    return (today or datetime.date.today()).isoformat()


def _date(s):
    return datetime.date.fromisoformat(s)


def ddmm(s):
    d = _date(s)
    return f"{d.day:02d}.{d.month:02d}"


# ---------- что прошли ----------

def subject_of(t):
    """Предмет темы для Глеба: «Математика» делится на «Алгебра» и «Геометрия»."""
    for name, _, rx in SPLIT.get(t.get("subj"), []):
        if rx.match(t.get("title") or ""):
            return name
    return t.get("subj") or "Предмет"


def icon_of(t):
    for name, icon, rx in SPLIT.get(t.get("subj"), []):
        if rx.match(t.get("title") or ""):
            return icon
    return t.get("icon") or "📘"


def started(topics, today=None):
    day = today_iso(today)
    return [t for t in topics or [] if t.get("start") and t["start"] <= day and not SKIP.search(t.get("subj") or "")]


def subjects(topics, today=None):
    """Предметы, по которым уже что-то прошли: [{name, icon, n}], сначала те, где тема началась позже всех."""
    by = {}
    for t in started(topics, today):
        s = by.setdefault(subject_of(t), {"name": subject_of(t), "icon": icon_of(t), "n": 0, "last": ""})
        s["n"] += 1
        s["last"] = max(s["last"], t["start"])
    return [{k: v for k, v in s.items() if k != "last"}
            for s in sorted(by.values(), key=lambda s: (s["last"], s["name"]), reverse=True)]


def short(name):
    """Название предмета для кнопки (≤ 24 символа с эмодзи): «Английский язык» -> «Английский»."""
    name = re.sub(r"\s+язык$", "", name.strip())
    return name if len(name) <= 14 else name.split()[0]


def recent(topics, subj, today=None):
    """Что прошли недавно: темы предмета, начатые и закончившиеся не раньше WINDOW дней назад (текущая — первой),
    не больше MAX_TOPICS. Ничего недавнего — последняя начатая тема."""
    day = today or datetime.date.today()
    mine = [t for t in started(topics, day) if subject_of(t) == subj]
    mine.sort(key=lambda t: t["start"], reverse=True)
    edge = (day - datetime.timedelta(days=WINDOW)).isoformat()
    long = lambda t: (_date(t["deadline"]) - _date(t["start"])).days > LONG
    fresh = [t for t in mine if t["deadline"] >= edge and not long(t)]
    return (fresh or mine[:1])[:MAX_TOPICS]


def clean_title(title):
    """«2. Становление империи. Пётр I (1682-1721) (2026)» -> без учебного года и фамилии учителя в скобках."""
    t = re.sub(r"\s*\((?:20\d\d(?:\s*-\s*20\d\d)?|[А-ЯЁ][а-яё]+\s+[А-ЯЁ]\.\s*[А-ЯЁ]\.)\)", "", title or "")
    return re.sub(r"\s+", " ", t).strip()


def subtopics(t, n=6):
    """Подтемы по названиям заданий: без номеров, служебных («Подтверждение 2.0») и повторов."""
    out, seen = [], set()
    for task in t.get("tasks") or []:
        x = re.sub(r"^\s*[\d.]+\s*[.)]?\s*", "", task.get("title") or "")
        x = re.sub(r"[^\w)»\".!?]+$", "", x).strip()
        if not x or GENERIC.match(x) or x.lower() in seen:
            continue
        seen.add(x.lower())
        out.append(x)
    return out[:n]


def topics_html(subj, icon, topics):
    """Карточка «что прошли» для чата."""
    lines = []
    for t in topics:
        lines.append(f"• <b>{esc(clean_title(t['title']))}</b> · до {ddmm(t['deadline'])}")
        subs = subtopics(t, 4)
        if subs:
            lines.append(f"<i>{esc(', '.join(subs))}</i>")
    body = "\n".join(lines) or "Тем пока нет — подготовлю по программе 8 класса"
    return (f"{icon} <b>{esc(subj)}</b>\n<blockquote>Прошли за последние недели:\n{body}</blockquote>\n"
            "<i>Сколько у тебя времени?</i>")


def topic_brief(t):
    """Тема для приложения."""
    return {"uuid": t["uuid"], "title": clean_title(t["title"]), "start": t["start"], "deadline": t["deadline"],
            "subs": subtopics(t)}


# ---------- просьба словами ----------

def _words(text):
    return re.findall(r"[a-zа-я]+", (text or "").lower().replace("ё", "е"))


def is_request(text):
    return bool(TRIGGER.search(text or ""))


def _stem(name):
    return _words(name)[0][:4] if _words(name) else ""


def find_subject(text, names):
    """Предмет в просьбе («по алгебре», «к контрольной по физике») -> название из names или None."""
    words = _words(text)
    for alias, targets in ALIASES:
        if any(w.startswith(alias) for w in words):
            for target in targets:
                hit = next((n for n in names if n.lower().startswith(target.lower())), None)
                if hit:
                    return hit
    for name in names:
        stem = _stem(name)
        if len(stem) >= 3 and any(w.startswith(stem) and w not in STOP for w in words):
            return name
    return None


def find_minutes(text):
    """«за 5 минут», «пять минут», «минутку», «быстро», «подробно» -> 1 | 5 | 10 или None."""
    t = (text or "").lower().replace("ё", "е")
    m = re.search(r"(\d+)\s*-?\s*(?:мин|m\b)", t)
    if m:
        n = int(m.group(1))
        return min(MINUTES, key=lambda x: (abs(x - n), -x))  # посередине — больше времени
    if re.search(r"\bдесят|\b10\b", t):
        return 10
    if re.search(r"\bпят(ь|и)\b|\b5\b", t):
        return 5
    if re.search(r"\b(одну|за|в)\s+минуту\b|\bминутк|\bбыстр|\bэкспресс", t):
        return 1
    if re.search(r"\bподробн", t):
        return 10
    return None


def find_tz(text, subj=None):
    """Своё ТЗ в просьбе: остались значимые слова, кроме «подготовь к проверочной по …» -> весь текст, иначе ""."""
    stem = _stem(subj) if subj else ""
    rest = [w for w in _words(text) if w not in STOP and not (stem and w.startswith(stem))
            and not any(w.startswith(a) for a, _ in ALIASES)]
    return (text or "").strip() if sum(len(w) for w in rest) >= 5 else ""


def detect(text, names):
    """Просьба словами -> (предмет или None, минуты или None, ТЗ или "")."""
    subj = find_subject(text, names)
    return subj, find_minutes(text), find_tz(text, subj)


# ---------- промпт ----------

def _sections():
    with open(PROMPT, encoding="utf-8") as f:
        raw = f.read()
    parts = re.split(r"^## формат (\S+)\s*$", raw, flags=re.M)
    return parts[0].strip(), {parts[i]: parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}


def material(topics, details):
    """Темы, задания и теория с сайта -> текст для промпта (не длиннее MAX_MATERIAL)."""
    det = {d.get("uuid"): d for d in details or []}
    out = []
    for t in topics:
        d = det.get(t["uuid"]) or {}
        out.append(f"### Тема: {clean_title(t['title'])} (с {ddmm(t['start'])} до {ddmm(t['deadline'])})")
        subs = subtopics(t, 20)
        if subs:
            out.append("Задания темы: " + "; ".join(subs))
        for m in d.get("materials") or []:
            out.append(f"#### Теория: {m.get('title', '')}\n{m.get('text', '')}")
        for c in d.get("checks") or []:
            out.append(f"#### Итоговое задание «{c.get('title', '')}»: {c.get('text', '')}")
        if d.get("questions"):
            out.append("#### Вопросы из тестов темы:\n" + "\n".join(f"- {q}" for q in d["questions"]))
    text = "\n\n".join(out)
    return text if len(text) <= MAX_MATERIAL else text[:MAX_MATERIAL].rsplit("\n", 1)[0] + "\n…"


def build_prompt(minutes, subj="", topics=(), details=(), tz="", avoid=()):
    """Промпт для claude -p. avoid — уже заданные вопросы: тогда нужны только новые вопросы («🔁 Ещё вопросы»)."""
    common, fmt = _sections()
    task = []
    if subj:
        task.append(f"Предмет: {subj}.")
    if tz:
        task.append(f"ТЗ от Глеба (готовь именно по нему; темы ниже — только то, что к нему относится): «{tz}»")
    if topics:
        task.append("Что недавно прошли на уроках (материалы с сайта школы):\n\n" + material(topics, details))
    elif not tz:
        task.append("Материалов с сайта нет — готовь по программе 8 класса по этому предмету, по последним темам.")
    key = "more" if avoid else str(minutes)
    body = "\n".join(task) + "\n\n" + fmt.get(key, fmt["5"])
    text = common.replace("{{MINUTES}}", SPAN.get(minutes, "5 минут")).replace("{{TASK}}", body)
    return text.replace("{{AVOID}}", "\n".join(f"- {q}" for q in avoid) or "-")


# ---------- ответ Claude ----------

def _strs(x, n):
    return [str(s).strip() for s in (x if isinstance(x, list) else []) if str(s).strip()][:n]


def parse(out):
    """Ответ Claude -> {title, points, sections, mistakes, questions} или None (не JSON)."""
    m = re.search(r"\{.*\}", out or "", re.S)
    try:
        data = json.loads(m.group(0)) if m else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return None
    sections = [{"title": str(s.get("title") or "").strip(), "text": str(s.get("text") or "").strip()}
                for s in data.get("sections") or [] if isinstance(s, dict) and (s.get("text") or s.get("title"))][:8]
    questions = [{"q": str(q.get("q") or "").strip(), "a": str(q.get("a") or "").strip()}
                 for q in data.get("questions") or [] if isinstance(q, dict) and q.get("q")][:12]
    res = {"title": str(data.get("title") or "").strip()[:80], "points": _strs(data.get("points"), 10),
           "sections": sections, "mistakes": _strs(data.get("mistakes"), 6), "questions": questions}
    return res if res["points"] or res["sections"] or res["questions"] else None


def fmt(s):
    """Строка от Claude -> HTML Telegram: экранирование, **жирный**, `код`."""
    t = esc(s)
    t = re.sub(r"\*\*([^*\n]+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", t)
    return t.replace("*", "")


def quote(text):
    tag = "blockquote expandable" if len(text) > 700 or text.count("\n") >= 8 else "blockquote"
    return f"<{tag}>{text}</blockquote>"


def render(res, subj="", minutes=5, more=False, topics=(), tz="", raw=""):
    """Результат -> HTML для Telegram. Абзацы разделены пустой строкой: длинное бот режет на сообщения между ними."""
    name = subj or (res or {}).get("title") or "Подготовка"
    if more:
        head = f"🔁 <b>Ещё вопросы · {esc(name)}</b>"
    else:
        title = (res or {}).get("title")
        head = f"{ICON.get(minutes, '📖')} <b>{esc(name)}{': ' + esc(title) if title and subj else ''}</b>"
    blocks = [head]
    if not res:  # Claude ответил не JSON — показываем как есть
        blocks.append(quote(fmt(raw.strip())[:3300] or "Пусто"))
    else:
        if res["points"]:
            blocks.append(quote("\n".join(f"• {fmt(p)}" for p in res["points"])))
        for s in res["sections"]:
            blocks.append((f"<b>📌 {fmt(s['title'])}</b>\n" if s["title"] else "") + quote(fmt(s["text"])[:1500]))
        if res["mistakes"]:
            blocks.append("<b>⚠️ Типовые ошибки</b>\n" + quote("\n".join(f"• {fmt(m)}" for m in res["mistakes"])))
        if res["questions"]:
            blocks.append("<b>❓ Проверь себя</b> <i>— ответ под спойлером, нажми</i>")
            for i, q in enumerate(res["questions"], 1):
                blocks.append(f"<b>{i}.</b> {fmt(q['q'])}\n<tg-spoiler>{fmt(q['a']) or '—'}</tg-spoiler>")
    note = [] if more else [f"за {SPAN.get(minutes, '')}"]
    if tz:
        note.append(f"по ТЗ «{esc(tz[:80])}{'…' if len(tz) > 80 else ''}»")
    if topics and not more:
        note.append("темы: " + esc(", ".join(clean_title(t["title"]) for t in topics))[:300])
    if note:
        blocks.append(f"<i>{' · '.join(note)}</i>")
    return "\n\n".join(blocks)
