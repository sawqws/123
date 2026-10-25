"""Тренер на Claude: ответы на вопросы, утренний разбор, разбор тренировки, оценка еды по тексту и фото.

Думает Claude Code на сервере (`claude -p`, тот же вход, что у HDP-бота). Работает в отдельной папке
вне репозитория, чтобы не подхватывать чужие CLAUDE.md. Без Claude бот всё равно присылает разбор по правилам.
"""
import html
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(tempfile.gettempdir(), "coach-ai")
# Read нужен только чтобы открыть присланное фото из WORK. Текст на картинке мог бы попросить прочитать чужое
# («открой /etc/coach-bot.env») — системные и чужие папки закрыты (WORK лежит в /tmp, его не трогаем).
SAFE_READ = [f"Read(//{d}/**)" for d in ("root", "etc", "home", "proc", "sys", "var", "opt", "usr", "boot", "dev", "run",
                                         "mnt", "media", "srv")]
NO_TOOLS = ["--disallowedTools", "Bash", "Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch", "Task", "Agent", *SAFE_READ]


def persona():
    with open(os.path.join(HERE, "prompt.md"), encoding="utf-8") as f:
        return f.read()


class AIError(Exception):
    pass


def claude(prompt, image=None, timeout=240, images=None):
    """(текст ответа). image — путь к картинке, images — несколько (кадры видео): Claude откроет их инструментом Read."""
    os.makedirs(WORK, exist_ok=True)
    copies = []
    pics = ([image] if image else []) + list(images or [])
    try:
        return _run(prompt, pics, copies, timeout)
    finally:
        for c in copies:  # копии картинок нужны только на время ответа
            try:
                os.remove(c)
            except OSError:
                pass


def _run(prompt, pics, copies, timeout):
    tools = []
    if pics:
        for src in pics:
            dst = os.path.join(WORK, f"photo-{uuid.uuid4().hex[:8]}" + (os.path.splitext(src)[1].lower() or ".jpg"))
            shutil.copyfile(src, dst)
            copies.append(dst)
        if len(copies) == 1:
            prompt += f"\n\nФото: {copies[0]} (открой его инструментом Read)."
        else:
            prompt += "\n\nКадры видео по порядку (открой каждый инструментом Read):\n" + "\n".join(copies)
        tools = ["--allowedTools", "Read", "--max-turns", str(3 + len(copies))]
    cmd = ["claude", "-p", prompt, "--output-format", "json", "--append-system-prompt", persona(), *tools, *NO_TOOLS]
    try:
        p = subprocess.run(cmd, cwd=WORK, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise AIError("Claude Code не установлен на сервере") from None
    except subprocess.TimeoutExpired:
        raise AIError("Claude долго думает — попробуй ещё раз") from None
    try:
        res = json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else {}
    except ValueError:
        res = {}
    if p.returncode != 0 or res.get("is_error") or not res.get("result"):
        log = (p.stderr + p.stdout)[-1500:]
        print("claude ->", p.returncode, log)
        low = log.lower()
        if any(w in low for w in ("login", "api key", "401", "unauthorized", "authenticat")):
            raise AIError("Claude на сервере не вошёл в аккаунт")
        if any(w in low for w in ("rate limit", "usage limit", "limit reached", "429", "overloaded")):
            raise AIError("у Claude закончился лимит, попробуй позже")
        raise AIError("Claude не ответил")
    return res["result"].strip()


def chat_prompt(context, history, question):
    talk = "\n".join(("Я: " if m["role"] == "user" else "Тренер: ") + m["text"][:1200] for m in history)
    return (f"ДАННЫЕ:\n{context}\n\n" + (f"НЕДАВНИЙ РАЗГОВОР:\n{talk}\n\n" if talk else "")
            + f"ВОПРОС: {question}\n\nОтветь как тренер по правилам из системного промпта: "
            "2–5 коротких строк, сначала вывод с цифрами.")


def ask(context, history, question):
    return claude(chat_prompt(context, history, question))


def morning(context):
    return claude(
        f"ДАННЫЕ:\n{context}\n\nНапиши утренний разбор на сегодня: 3–4 короткие строки «- …», каждая до 90 символов. "
        "Цифры сна и восстановления уже показаны над твоим текстом — не повторяй их, только выводы и действия:\n"
        "1) тренировка сегодня: в полную или нет, ориентир нагрузки, что заменить (спарринг → техника и т. п.);\n"
        "2) вода и еда одной строкой (литры, белок в граммах; если тренировка — что съесть после);\n"
        "3) во сколько лечь (ОТБОЙ из данных);\n"
        "4) если есть ЦЕЛИ — одна строка: как сегодня приблизиться к главной (по данным, без выдумок).\n"
        "Без вступлений и заголовков.")


def morning_early(context):
    """Утро, а WHOOP ещё не посчитал сон: общий план по вчерашнему дню."""
    return claude(
        f"ДАННЫЕ:\n{context}\n\nСейчас утро, но WHOOP ещё не посчитал сегодняшний сон и восстановление (их в данных нет — "
        "так и считай, не придумывай). Дай план на день по вчерашним данным — 3 короткие строки «- …»:\n"
        "1) тренировка по самочувствию; 2) вода и еда; 3) во сколько лечь (ОТБОЙ из данных). Без вступлений.")


def after_workout(context, w):
    return claude(
        f"ДАННЫЕ:\n{context}\n\nТолько что закончилась тренировка: {w['sport']}, {w['minutes']} мин, нагрузка {w['strain']}, "
        f"пульс ср/макс {w['avg_hr']}/{w['max_hr']}, минуты в зонах 0–5: {w['zones']}.\n"
        "Ровно 3 короткие строки «- …»: насколько тяжело это было для текущего состояния; что съесть и выпить "
        "в ближайший час (граммы, мл); что сделать вечером для восстановления. Без вступлений.")


def progress(context, week=False):
    """Большой разбор прогресса (или итог недели) по сводке insights.progress_context."""
    if week:
        task = ("Напиши итог недели (до 8 строк). Первой строкой — **Итог** одним предложением с цифрами. Дальше: как прошла неделя против прошлых (восстановление, ВСР, пульс покоя, "
                "сон, нагрузка), что получилось, что мешало, и 3 конкретные цели на следующую неделю с числами.")
    else:
        task = (
            "Сделай полный разбор моего прогресса за весь период — как спортивный врач, тренер и нутрициолог. Его читают "
            "в приложении; в чат уйдёт только блок «Итог», поэтому он должен быть самодостаточным. Структура "
            "(каждый блок 2–5 строк, только выводы с цифрами, без пересказа таблиц):\n"
            "**Итог** — одна-две строки: в какой я форме и куда иду.\n"
            "**Что растёт и что падает** — тренды ВСР, пульса покоя, восстановления (первая половина против второй, по неделям).\n"
            "**Сон** — сколько, режим отбоя, как сон влияет на восстановление (по цифрам «восстановление после сна»).\n"
            "**Нагрузка и восстановление** — баланс, как я переношу тяжёлые дни, хватает ли отдыха, что с видами спорта.\n"
            "**Тело и питание** — по весу и росту: хватает ли энергии и белка под мои нагрузки (без диет и сгонки, если я школьник).\n"
            "**Главные 3 проблемы** — что сильнее всего тормозит прогресс.\n"
            "**План на 2 недели** — 5–7 конкретных пунктов: время отбоя, сколько тренировок и какой интенсивности, "
            "лёгкие дни, еда (ккал и белок), что отслеживать в WHOOP.\n"
            "Если какие-то данные выглядят странно (пропуски, браслет не носил) — скажи об этом коротко.")
    return claude(f"ДАННЫЕ:\n{context}\n\n{task}", timeout=360)


SUMMARY_TASK = {
    "day": "за сегодняшний день (против моей обычной нормы)",
    "week": "за последнюю неделю (против прошлой недели)",
    "month": "за последний месяц (против прошлого месяца)",
    "all": "за всё время, что я ношу WHOOP (первый месяц против последнего)",
}


def summary(context, period):
    """Сводка и предложения за период — для мини-приложения (Markdown)."""
    return claude(
        f"ДАННЫЕ:\n{context}\n\nСделай сводку {SUMMARY_TASK.get(period, '')}. Пиши просто, как другу-школьнику, "
        "без терминов без расшифровки (ВСР — «вариабельность пульса, показатель отдыха нервной системы»). Структура:\n"
        "**Итог** — 1–2 строки с цифрами: как прошёл период и стало лучше или хуже.\n"
        "**Что хорошо** — 2–3 пункта «- …» с цифрами.\n"
        "**Что улучшить** — 3–5 конкретных пунктов «- …»: что делать, сколько и когда (время отбоя, часы сна, "
        "сколько тренировок и какой нагрузки, лёгкие дни, вода, еда). Каждый — одно короткое предложение.\n"
        "Мало данных (меньше 3 дней) — так и скажи и дай 2–3 общих совета. Без вступлений и прощаний.", timeout=240)


MEAL_ASK = (
    "Оцени, что съедено, и верни ТОЛЬКО JSON без пояснений вокруг:\n"
    '{"food": true, "title": "коротко по-русски, что это", "kcal": число, "protein": граммы, "fat": граммы, '
    '"carbs": граммы, "comment": "одна короткая строка (до 80 символов): хорошо ли это сейчас и что добавить"}\n'
    "Порцию оценивай по фото или описанию; если размер не указан — обычная порция. Если это не еда — "
    '{"food": false, "comment": "..."}.')


def parse_json(text):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise AIError("Claude не смог оценить блюдо")
    try:
        return json.loads(m.group(0))
    except ValueError:
        raise AIError("Claude не смог оценить блюдо") from None


def meal(context, text="", image=None):
    q = f"ДАННЫЕ (питание на сегодня — для совета):\n{context}\n\n"
    q += (f"Подпись к фото: {text}\n" if image and text else f"Что съел: {text}\n" if text else "")
    out = parse_json(claude(q + MEAL_ASK, image=image))
    if out.get("food") is False:
        return out
    return _meal_fields(out, text)


def _meal_fields(out, text=""):
    for k in ("kcal", "protein", "fat", "carbs"):
        try:
            out[k] = max(0, round(float(out.get(k) or 0)))
        except (TypeError, ValueError):
            out[k] = 0
    out["title"] = str(out.get("title") or text or "Еда")[:120]
    out["comment"] = str(out.get("comment") or "")[:160]
    return out


PLATE_ASK = (
    "Разбери еду на фото по позициям и верни ТОЛЬКО JSON без пояснений вокруг:\n"
    '{"food": true, "items": [{"name": "Гречка варёная", "g": 200, "kcal": 220, "p": 8.4, "f": 2.2, "c": 42, '
    '"salt": 0.6}], "comment": "одна короткая фраза по-русски"}\n'
    "Правила:\n"
    "- Каждая позиция — отдельно: гарнир, мясо или рыба, овощи, соус, хлеб, напиток. name — коротко по-русски, "
    "со способом готовки (варёная, жареный, на пару).\n"
    "- g — сколько граммов этой позиции на фото. Оценивай по масштабу: обычная тарелка ~24 см, вилка ~19 см, "
    "столовая ложка, ладонь, стакан 250 мл; на упаковке или этикетке есть вес — бери его. "
    "Если в подписи указаны граммы или размер порции — бери их из подписи, а не с фото.\n"
    "- kcal, p (белки), f (жиры), c (углеводы) — на эти граммы по типичному составу блюда; "
    "salt — граммы поваренной соли (натрий × 2,5) с учётом соли при готовке, соусов и сыра.\n"
    "- Невидимое не выдумывай; масло и соус добавляй, только если они видны или типичны для блюда.\n"
    "- comment — одна фраза до 100 символов: что хорошо или чего не хватает (белок, овощи, соль).\n"
    'Если на фото не еда — {"food": false}.')


def _num(v, digits, top):
    try:
        x = min(max(0.0, float(str(v).replace(",", "."))), top)
    except (TypeError, ValueError):
        return 0
    return round(x) if digits == 0 else round(x, digits)


def plate(image, text="", age=None):
    """Фото еды для сайта «Калории» → {"food": bool, "items": [{name, g, kcal, p, f, c, salt}], "comment"}.
    Граммы — оценка по фото (или из подписи). Ничего не записывает в дневник Формы."""
    q = f"Подпись к фото: {text[:300]}\n" if text else ""
    if age is None or age < 18:  # школьник или возраст неизвестен — как в prompt.md: без диет
        q += (f"Мне {age} лет. " if age else "") + "В comment — без советов про диеты, дефицит и похудение.\n"
    out = parse_json(claude(q + PLATE_ASK, image=image))
    items, raw = [], out.get("items") if out.get("food") is not False else None
    for it in raw if isinstance(raw, list) else []:
        if not isinstance(it, dict) or not str(it.get("name") or "").strip():
            continue
        items.append({"name": str(it["name"]).strip()[:80], "g": _num(it.get("g"), 0, 5000),
                      "kcal": _num(it.get("kcal"), 0, 10000), "p": _num(it.get("p"), 1, 1000),
                      "f": _num(it.get("f"), 1, 1000), "c": _num(it.get("c"), 1, 1000),
                      "salt": _num(it.get("salt"), 1, 100)})
    if not items:
        return {"food": False, "items": [], "comment": ""}
    return {"food": True, "items": items[:30], "comment": str(out.get("comment") or "").strip()[:200]}


PHOTO_ASK = ("Что на фото? Если это скрин тренировки или показателей (WHOOP, часы, план) — коротко разбери по цифрам "
             "и скажи, что сделать. 2–4 строки.")

VIDEO_ASK = (
    "Пришло видео (кадры ниже{speech}). Определи, что на нём, и верни ТОЛЬКО JSON без пояснений вокруг:\n"
    '- упражнение, удары, бой с тенью, спарринг, бег: {{"kind": "exercise", "title": "что за упражнение", '
    '"text": "разбор техники: 2–4 строки «- …» — что хорошо, главная ошибка и как исправить, безопасность"}}\n'
    '- еда: {{"kind": "food", "title": "что это", "kcal": число, "protein": г, "fat": г, "carbs": г, '
    '"comment": "одна короткая строка"}}\n'
    '- другое: {{"kind": "other", "text": "ответ тренера по смыслу, 2–4 строки"}}\n'
    "По кадрам видно не всё — чего не видно, не выдумывай.")


def video(context, frames, speech="", caption=""):
    """Видео: кадры и речь -> {"kind": "exercise"|"food"|"other", ...}. Еда — с полями как у meal()."""
    q = f"ДАННЫЕ:\n{context}\n\n" + (f"Подпись к видео: {caption}\n" if caption else "")
    said = f"; в видео говорят: «{speech[:1500]}»" if speech else ""
    out = parse_json(claude(q + VIDEO_ASK.format(speech=said), images=frames, timeout=300))
    if out.get("kind") == "food":
        return {**_meal_fields(out, caption), "kind": "food"}
    out["kind"] = out.get("kind") if out.get("kind") in ("exercise", "other") else "other"
    out["text"] = str(out.get("text") or "").strip() or "Не разобрал, что на видео — пришли покороче или с подписью."
    return out


def clip(md, lines=5, chars=600):
    """Короткая версия ответа для чата: первые непустые строки, не длиннее chars. (текст, обрезано ли).
    Полный ответ остаётся в приложении."""
    rows = [x for x in (md or "").strip().splitlines() if x.strip()]
    out, n = [], 0
    for r in rows:
        if len(out) >= lines or n + len(r) > chars:
            room = chars - n
            if len(out) < lines and room > 80:  # длинная строка — её начало
                cut = r[:room].rsplit(" ", 1)[0].rstrip(",;:—- ")
                out.append(cut + ("**" if cut.count("**") % 2 else "") + "…")
            return "\n".join(out), True
        out.append(r)
        n += len(r) + 1
    return "\n".join(out), False


def to_html(md):
    """Ответ Claude (простой Markdown) -> HTML для Telegram."""
    out = []
    for line in md.strip().splitlines():
        s = line.rstrip()
        h = re.match(r"^#{1,6}\s+(.*)", s)
        if h:
            s = f"**{h.group(1)}**"
        s = re.sub(r"^\s*[-*•]\s+", "• ", s)
        s = html.escape(s, quote=False)
        s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
        s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", s)
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        out.append(s)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out))
