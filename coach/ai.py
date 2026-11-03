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
import threading
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


def claude(prompt, image=None, timeout=240, images=None, model=None, effort=None, label=None):
    """(текст ответа). image — путь к картинке, images — несколько (кадры видео): Claude откроет их инструментом Read.
    label — своя подпись к нескольким фото (иначе — «кадры видео» или «одна еда с разных сторон»)."""
    os.makedirs(WORK, exist_ok=True)
    copies = []
    pics = ([image] if image else []) + list(images or [])
    try:
        return _run(prompt, pics, copies, timeout, model, effort, label)
    finally:
        for c in copies:  # копии картинок нужны только на время ответа
            try:
                os.remove(c)
            except OSError:
                pass


def _run(prompt, pics, copies, timeout, model=None, effort=None, label=None):
    tools = []
    if pics:
        for src in pics:
            dst = os.path.join(WORK, f"photo-{uuid.uuid4().hex[:8]}" + (os.path.splitext(src)[1].lower() or ".jpg"))
            shutil.copyfile(src, dst)
            copies.append(dst)
        if len(copies) == 1:
            prompt += f"\n\nФото: {copies[0]} (открой его инструментом Read)."
        elif label:
            prompt += f"\n\n{label}\n" + "\n".join(copies)
        elif model:  # несколько фото одной тарелки (сверху, сбоку)
            prompt += "\n\nФото одной и той же еды с разных сторон (открой каждое инструментом Read):\n" + "\n".join(copies)
        else:
            prompt += "\n\nКадры видео по порядку (открой каждый инструментом Read):\n" + "\n".join(copies)
        tools = ["--allowedTools", "Read", "--max-turns", str(3 + len(copies))]
    cmd = ["claude", "-p", prompt, "--output-format", "json", "--append-system-prompt", persona(), *tools, *NO_TOOLS,
           *(["--model", model] if model else []), *(["--effort", effort] if effort else [])]
    try:
        p = subprocess.run(cmd, cwd=WORK, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)  # иначе claude -p ждёт ввод от pm2 — лишние секунды
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


def chat_prompt(context, history, question, full=False):
    """Вопрос тренеру — о чём угодно (спорт, сон, еда, кожа, учёба, настроение, быт). full — ответ для приложения
    (полный, но по делу); без него — короткий для чата бота (целиком он всё равно остаётся в приложении)."""
    talk = "\n".join(("Я: " if m["role"] == "user" else "Тренер: ") + m["text"][:1200] for m in history)
    how = ("Ответ для приложения: полный, но по делу — до ~15 строк, пункты «- …», **жирным** главное. "
           if full else "Ответ для чата: 2–5 коротких строк. ")
    return (f"ДАННЫЕ:\n{context}\n\n" + (f"НЕДАВНИЙ РАЗГОВОР:\n{talk}\n\n" if talk else "")
            + f"ВОПРОС: {question}\n\nОтветь как личный наставник по правилам из системного промпта. {how}"
            "Сначала главный вывод, потом что сделать; опирайся на ДАННЫЕ (цифры, режим, привычки, еду, уход), "
            "когда они к месту, и не притягивай их, если вопрос о другом.")


def ask(context, history, question, full=False):
    return claude(chat_prompt(context, history, question, full))


def fast(prompt, timeout=120):
    """Быстрая модель без долгого размышления (CHAT_MODEL/CHAT_EFFORT: ответ за секунды, а не 20–40 с);
    недоступна — обычная модель."""
    if CHAT_MODEL:
        try:
            return claude(prompt, timeout=timeout, model=CHAT_MODEL, effort=CHAT_EFFORT or None)
        except AIError:
            pass
    return claude(prompt, timeout=240)


PLAIN = ("Пиши максимально простыми словами (как в системном промпте: «запас сил», «пульс в покое», «нагрузка»), "
         "без английских сокращений, вступлений и прощаний.")


def morning(context):
    """Утренний разбор — сразу, как WHOOP посчитал сон. Целиком — в приложении, в чат уходят первые 2 строки."""
    return fast(
        f"ДАННЫЕ:\n{context}\n\nНапиши утренний разбор на сегодня. Ответ для приложения: целиком его читают в приложении, "
        "а в чат уйдут только первые 2 строки — они должны быть самодостаточными.\n"
        "Первая строка — **вывод** одним предложением: какой сегодня день (сон и восстановление против ОБЫЧНО).\n"
        "Дальше 3–5 коротких строк «- …», каждая до 100 символов, только выводы и действия:\n"
        "1) тренировка сегодня: в полную или нет, ориентир нагрузки, что заменить (спарринг → техника и т. п.);\n"
        "2) вода и еда одной строкой (литры, белок в граммах; если тренировка — что съесть после);\n"
        "3) во сколько лечь (ОТБОЙ из данных);\n"
        "4) если есть ЦЕЛИ — одна строка: как сегодня приблизиться к главной (по данным, без выдумок).\n"
        + PLAIN)


def morning_early(context):
    """Утро, а WHOOP ещё не посчитал сон: общий план по вчерашнему дню."""
    return fast(
        f"ДАННЫЕ:\n{context}\n\nСейчас утро, но WHOOP ещё не посчитал сегодняшний сон и восстановление (их в данных нет — "
        "так и считай, не придумывай). Дай план на день по вчерашним данным — 3 короткие строки «- …»:\n"
        "1) тренировка по самочувствию; 2) вода и еда; 3) во сколько лечь (ОТБОЙ из данных). " + PLAIN)


def after_workout(context, w):
    """Разбор тренировки — как только WHOOP её посчитал. Целиком — в приложении, в чат — первые 2 строки."""
    return fast(
        f"ДАННЫЕ:\n{context}\n\nТолько что закончилась тренировка: {w['sport']}, {w['minutes']} мин, нагрузка {w['strain']}, "
        f"пульс средний/максимум {w['avg_hr']}/{w['max_hr']}, минуты в пульсовых зонах 0–5: {w['zones']}.\n"
        "Разбор тренировки для приложения (в чат уйдут первые 2 строки — пусть будут самодостаточными):\n"
        "Первая строка — **вывод** одним предложением: насколько тяжело это было для сегодняшнего восстановления.\n"
        "Дальше 4–5 коротких строк «- …», каждая до 100 символов:\n"
        "1) нагрузка, пульс и зоны — что это значит простыми словами (сколько было тяжёлой работы);\n"
        "2) что это значит для восстановления завтра;\n"
        "3) что съесть и выпить в ближайший час (граммы, мл);\n"
        "4) во сколько лечь сегодня (ОТБОЙ и режим из данных) и что сделать вечером для восстановления.\n" + PLAIN)


def evening(context, facts):
    """Вечерний итог дня — за ~1 ч до отбоя. facts — строки с цифрами дня (нагрузка, еда, вода, привычки)."""
    return fast(
        f"ДАННЫЕ:\n{context}\n\nИТОГ ДНЯ ПО ЦИФРАМ:\n{facts}\n\n"
        "Сейчас вечер, до отбоя около часа. Напиши вечерний итог дня для приложения (в чат уйдёт первая строка):\n"
        "Первая строка — **итог** одним предложением: как прошёл день (нагрузка, еда, вода, привычки).\n"
        "Дальше 3–5 коротких строк «- …», каждая до 100 символов:\n"
        "1) еда и вода: хватило ли, что добрать до сна (лёгкое, если поздно; без диет);\n"
        "2) привычки и уход за кожей — что сделано и что ещё успеть;\n"
        "3) что сделать до сна: 2–3 конкретных шага и во сколько лечь (ОТБОЙ из данных), сколько сна нужно.\n"
        "Чего нет в данных — не выдумывай. " + PLAIN)


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


def summary_prompt(context, period):
    return (f"ДАННЫЕ:\n{context}\n\nСделай сводку {SUMMARY_TASK.get(period, '')}. Пиши просто, как другу-школьнику, "
        "совсем без терминов: вместо ВСР/HRV пиши «запас сил», вместо SpO2 — «кислород в крови», вместо strain — «нагрузка», вместо RHR — «пульс в покое»; никаких английских сокращений. Структура:\n"
        "**Итог** — 1–2 строки с цифрами: как прошёл период и стало лучше или хуже.\n"
        "**Что хорошо** — 2–3 пункта «- …» с цифрами.\n"
        "**Что улучшить** — 3–5 конкретных пунктов «- …»: что делать, сколько и когда (время отбоя, часы сна, "
        "сколько тренировок и какой нагрузки, лёгкие дни, вода, еда). Каждый — одно короткое предложение.\n"
        "Мало данных (меньше 3 дней) — так и скажи и дай 2–3 общих совета. Без вступлений и прощаний.")


def summary(context, period):
    """Сводка и предложения за период — для мини-приложения (Markdown). Быстрая модель без долгого размышления
    (Глеб 02.11: «очень долго думает» — было 20–40 с); недоступна — обычная."""
    return fast(summary_prompt(context, period))


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
    "Ты нутрициолог, который по фото оценивает вес еды точнее всех. Разбери еду на фото по позициям.\n"
    "Сначала молча, про себя (в ответ это не пиши):\n"
    "1) Найди масштаб: тарелка (обычная плоская 24–26 см, глубокая 20–22 см, десертная 19–20 см), вилка ~19–20 см, "
    "столовая ложка ~18 см, чайная ~13 см, нож ~22 см, ладонь подростка ~17–18 см, стакан 250 мл, кружка 300–350 мл, "
    "контейнер, упаковка с весом на этикетке, монета, телефон ~15 см. Если фото несколько — сверь размеры по ним.\n"
    "2) Для каждой позиции: что можно сосчитать — посчитай штуки и умножь на вес одной (пельмень 12–13 г, "
    "вареник 25–30 г, куриный наггетс 18 г, котлета 70–100 г, яйцо 55 г, ломтик хлеба 25–35 г, сырник 50–60 г, "
    "блин 40–60 г, ролл 25–30 г); остальное — площадь на тарелке × высота слоя → объём × плотность "
    "(каши и рис ~0,8–0,9 г/мл, пюре ~1, супы ~1, салаты 0,5–0,7, листья 0,1–0,2, мясо ~1–1,1).\n"
    "3) Сверь с типичными порциями (гарнир 150–250 г, тарелка супа 250–350 г, котлета 80–100 г, стейк 150–250 г) "
    "и с подписью: если в подписи вес, число штук или размер посуды — это важнее фото.\n"
    "Потом верни ТОЛЬКО JSON без пояснений вокруг:\n"
    '{"food": true, "items": [{"name": "Гречка варёная", "g": 200, "lo": 170, "hi": 230, "sure": "mid", '
    '"kcal": 220, "p": 8.4, "f": 2.2, "c": 42, "salt": 0.6}], "comment": "одна короткая фраза по-русски"}\n'
    "Правила:\n"
    "- Каждая позиция отдельно: гарнир, мясо или рыба, овощи, соус, хлеб, напиток. name — коротко по-русски, "
    "со способом готовки (варёная, жареный, на пару).\n"
    "- g — лучшая оценка граммов позиции на фото; lo и hi — честный диапазон; sure — уверенность: "
    "high (есть масштаб, видно целиком или вес на упаковке), mid, low (нет масштаба, перекрыто, глубокая посуда).\n"
    "- kcal, p (белки), f (жиры), c (углеводы) — на эти граммы по типичному составу; "
    "salt — граммы соли (натрий × 2,5) с учётом соли при готовке, соусов и сыра.\n"
    "- Невидимое не выдумывай; масло и соус добавляй, только если они видны или типичны для блюда.\n"
    "- comment — одна фраза до 100 символов: что хорошо или чего не хватает (белок, овощи, соль).\n"
    'Если на фото не еда — {"food": false}.')
PLATE_MODEL = os.environ.get("KCAL_PHOTO_MODEL", "opus")  # сильная модель видит масштаб точнее; не вышло — модель по умолчанию


def _num(v, digits, top):
    try:
        x = min(max(0.0, float(str(v).replace(",", "."))), top)
    except (TypeError, ValueError):
        return 0
    return round(x) if digits == 0 else round(x, digits)


def plate(image, text="", age=None, images=None):
    """Фото еды для сайта «Калории» → {"food": bool, "items": [{name, g, lo, hi, sure, kcal, p, f, c, salt}], "comment"}.
    images — ещё фото той же еды (сбоку, с другого ракурса). Граммы — оценка по фото и масштабу (или из подписи).
    Ничего не записывает в дневник Формы."""
    q = f"Подпись к фото: {text[:300]}\n" if text else ""
    if age is None or age < 18:  # школьник или возраст неизвестен — как в prompt.md: без диет
        q += (f"Мне {age} лет. " if age else "") + "В comment — без советов про диеты, дефицит и похудение.\n"
    pics = [image] + list(images or [])
    try:
        out = parse_json(claude(q + PLATE_ASK, images=pics, model=PLATE_MODEL or None))
    except AIError:
        if not PLATE_MODEL:
            raise
        out = parse_json(claude(q + PLATE_ASK, images=pics))
    items, raw = [], out.get("items") if out.get("food") is not False else None
    for it in raw if isinstance(raw, list) else []:
        if not isinstance(it, dict) or not str(it.get("name") or "").strip():
            continue
        g = _num(it.get("g"), 0, 5000)
        lo, hi = _num(it.get("lo"), 0, 5000), _num(it.get("hi"), 0, 5000)
        if not 0 < lo <= g:
            lo = round(g * 0.85)
        if not hi >= g:
            hi = round(g * 1.15)
        sure = it.get("sure") if it.get("sure") in ("high", "mid", "low") else "mid"
        items.append({"name": str(it["name"]).strip()[:80], "g": g, "lo": lo, "hi": hi, "sure": sure,
                      "kcal": _num(it.get("kcal"), 0, 10000), "p": _num(it.get("p"), 1, 1000),
                      "f": _num(it.get("f"), 1, 1000), "c": _num(it.get("c"), 1, 1000),
                      "salt": _num(it.get("salt"), 1, 100)})
    if not items:
        return {"food": False, "items": [], "comment": ""}
    return {"food": True, "items": items[:30], "comment": str(out.get("comment") or "").strip()[:200]}


SKIN_ASK = (
    "Посмотри фото лба (сделано вечером после умывания) и скажи коротко: всё ли нормально с кожей и что поправить в уходе. "
    "Выше — мой уход, заметки о коже и отметки ухода за неделю.\n"
    "Верни ТОЛЬКО JSON без пояснений вокруг:\n"
    '{"status": "ok", "why": "…", "points": [], "compare": null}\n'
    "Правила:\n"
    "- status \"ok\" — кожа в порядке или как обычно, менять ничего не надо: why — одна короткая строка почему "
    "(до 120 символов), points — [].\n"
    "- status \"attention\" — есть что поправить: points — 2–4 коротких пункта (до 160 символов каждый): что видно "
    "простыми словами и что сделать — из того, что у меня уже есть в уходе (чаще, реже, не пропускать вечер, убрать то, "
    "что раздражает), или базовое безрецептурное (мягкое умывание, лёгкий увлажняющий крем, крем от солнца днём, "
    "не трогать и не выдавливать, чистая наволочка). why — пусто.\n"
    "- Без диагнозов и названий болезней, без рецептурных средств и лекарств, без новых брендов. Пиши на «ты», "
    "просто, как старший друг, без латиницы и терминов.\n"
    "- Много воспалённых прыщей, гной, боль, сильное покраснение или отёк, или резко хуже прошлого фото — status "
    "\"attention\" и отдельным пунктом: «Покажи дерматологу и скажи родителям».\n"
    "- status \"retake\" — лба не видно, темно, размыто или это не лицо: why — что не так с фото.\n"
    "- compare — только если есть прошлое фото: \"лучше\", \"так же\" или \"хуже\" (сравни кожу, не свет); "
    "нет прошлого фото — null.")
SKIN_MODEL = os.environ.get("SKIN_PHOTO_MODEL", "sonnet")  # модель с картинками; не ответила — модель по умолчанию
SKIN_OK = "Всё нормально, идём дальше."
COMPARE = ("лучше", "так же", "хуже")


def _line(v, top):
    return " ".join(str(v or "").replace("**", "").split()).strip(" -•")[:top].strip()


def skin(facts, photo, prev=None):
    """Разбор фото лба → {"status": ok|attention|none, "text", "compare", "retake"?}. prev — прошлое фото для сравнения.
    Ответ без понятного статуса — AIError (разбор будет «none»)."""
    pics = [photo] + ([prev] if prev else [])
    label = ("Фото по порядку (открой оба инструментом Read): первое — сегодня, второе — прошлое, для сравнения:"
             if prev else None)
    q = f"{facts}\n\n" + ("Есть прошлое фото — сравни.\n" if prev else "Прошлого фото нет — compare: null.\n") + SKIN_ASK
    try:
        out = parse_json(claude(q, images=pics, model=SKIN_MODEL or None, effort=CHAT_EFFORT or None, label=label,
                                timeout=150))
    except AIError:
        if not SKIN_MODEL:
            raise
        out = parse_json(claude(q, images=pics, label=label))
    st, why = out.get("status"), _line(out.get("why"), 160)
    cmp_ = out.get("compare") if prev and out.get("compare") in COMPARE else None
    if st == "retake":
        return {"status": "none", "retake": True, "compare": None,
                "text": f"Фото не получилось разобрать{': ' + why[:1].lower() + why[1:] if why else ''}. Сними ещё раз при свете."}
    if st == "ok":
        return {"status": "ok", "text": SKIN_OK + (f"\n{why}" if why else ""), "compare": cmp_}
    pts = [p for p in (_line(x, 200) for x in (out.get("points") or []) if isinstance(x, str)) if p][:4]
    if st == "attention" and pts:
        return {"status": "attention", "text": "\n".join(f"- {p}" for p in pts), "compare": cmp_}
    raise AIError("Claude не разобрал фото")


KCAL_CHAT = (
    "Ты — личный спортивный нутрициолог в приложении «Калории». Цель — расти и восстанавливаться после бокса: "
    "добирать норму калорий, белка и воды, соль держать в лимите; никаких диет, дефицита и похудения.\n"
    "Как отвечать:\n"
    "- По-русски, 2–6 коротких строк, сначала вывод. Без воды и общих фраз.\n"
    "- Считай от того, сколько ОСТАЛОСЬ до нормы сегодня (ккал, белки, жиры, углеводы, вода; соль — не больше лимита) "
    "и от времени дня: до тренировки за 1,5–2 ч — углеводы и немного белка, мало жира; после — белок + углеводы; "
    "перед сном — творог или молочное.\n"
    "- Предлагай обычную еду, которая есть дома или в магазине, с граммами — с учётом того, что его порции "
    "больше обычных (коэффициент в данных).\n"
    "- Если он перечисляет, что может съесть, — выбери лучшее сочетание из этого, скажи сколько граммов и что "
    "добавить, чтобы закрыть норму; что из списка лучше отложить — коротко почему.\n"
    "- Цифры честные, по типичному составу; не выдумывай того, чего нет в данных.\n"    "- Воду — добрать сегодня, равномерно до сна (не переносить на завтра).\n"
    "- Обычный текст: строки и пункты «- …», без markdown (без **, #, таблиц).\n"
    "Формат: сначала сам ответ обычным текстом. В самом конце — отдельной последней строкой список того, что "
    "конкретно советуешь съесть сейчас (0–5 позиций, граммы на его порцию), чтобы добавить в дневник одной кнопкой:\n"
    'ITEMS: [{"name": "Творог 5%", "g": 200, "kcal": 242, "p": 34, "f": 10, "c": 4, "salt": 0.2}]\n'
    "Если совет не про конкретную еду — ITEMS: []")
CHAT_MODEL = os.environ.get("KCAL_CHAT_MODEL", "sonnet")  # быстрая модель
CHAT_EFFORT = os.environ.get("KCAL_CHAT_EFFORT", "low")  # без долгого размышления: первые слова ~3 с, весь ответ ~7 с (было 20–30)


def kcal_chat_prompt(app_ctx, whoop_ctx, history, question):
    talk = "\n".join(("Я: " if m.get("role") == "user" else "Нутрициолог: ") + str(m.get("text") or "")[:800]
                     for m in history[-12:])
    return (f"ДНЕВНИК ПИТАНИЯ СЕГОДНЯ (приложение «Калории»):\n{app_ctx}\n\n"
            + (f"WHOOP И ПРОФИЛЬ:\n{whoop_ctx[:2500]}\n\n" if whoop_ctx else "")
            + (f"РАЗГОВОР:\n{talk}\n\n" if talk else "") + f"ВОПРОС: {question}\n\n" + KCAL_CHAT)


def chat_split(full):
    """Ответ чата → (текст, блюда). Блюда — в последней строке «ITEMS: [...]» (или старый формат JSON {text, items})."""
    full = (full or "").strip()
    raw, i = None, full.rfind("ITEMS:")
    if i >= 0:
        text, tail = full[:i].strip(), full[i + 6:].strip()
        try:
            raw = json.loads(tail[:tail.rfind("]") + 1] if "]" in tail else tail)
        except ValueError:
            raw = None
    elif full.startswith("{"):
        try:
            j = json.loads(full)
            text, raw = str(j.get("text") or ""), j.get("items")
        except ValueError:
            text = full
    else:
        text = full
    items = []
    for it in raw if isinstance(raw, list) else []:
        if isinstance(it, dict) and str(it.get("name") or "").strip() and _num(it.get("g"), 0, 3000) > 0:
            items.append({"name": str(it["name"]).strip()[:80], "g": _num(it.get("g"), 0, 3000),
                          "kcal": _num(it.get("kcal"), 0, 10000), "p": _num(it.get("p"), 1, 1000),
                          "f": _num(it.get("f"), 1, 1000), "c": _num(it.get("c"), 1, 1000),
                          "salt": _num(it.get("salt"), 1, 100)})
    return text.strip()[:2000], items[:5]


def kcal_chat(app_ctx, whoop_ctx, history, question):
    """Чат «Калорий» целиком: совет, что съесть и что добрать. → {"text", "items": [{name, g, kcal, p, f, c, salt}]}."""
    q = kcal_chat_prompt(app_ctx, whoop_ctx, history, question)
    try:
        out = claude(q, model=CHAT_MODEL or None, effort=CHAT_EFFORT or None)
    except AIError:
        if not CHAT_MODEL:
            raise
        out = claude(q)
    text, items = chat_split(out)
    if not text and not items:
        raise AIError("Claude не ответил")
    return {"text": text, "items": items}


def claude_stream(prompt, model=None, timeout=120, effort=None):
    """Ответ Claude по кусочкам (stream-json с частичными сообщениями) — генератор строк текста."""
    os.makedirs(WORK, exist_ok=True)
    cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose", "--include-partial-messages",
           "--append-system-prompt", persona(), *NO_TOOLS, *(["--model", model] if model else []),
           *(["--effort", effort] if effort else [])]
    try:
        p = subprocess.Popen(cmd, cwd=WORK, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except FileNotFoundError:
        raise AIError("Claude Code не установлен на сервере") from None
    killer = threading.Timer(timeout, p.kill)
    killer.start()
    got = False
    try:
        for line in p.stdout:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            ev = d.get("event") or {}
            if d.get("type") == "stream_event" and ev.get("type") == "content_block_delta":
                t = (ev.get("delta") or {}).get("text") or ""
                if t:
                    got = True
                    yield t
            elif d.get("type") == "result" and d.get("is_error"):
                raise AIError("Claude не ответил")
        p.wait()
    finally:
        killer.cancel()
        if p.poll() is None:
            p.kill()
    if not got:
        log = (p.stderr.read() if p.stderr else "")[-800:].lower()
        if any(w in log for w in ("rate limit", "usage limit", "limit reached", "429", "overloaded")):
            raise AIError("у Claude закончился лимит, попробуй позже")
        raise AIError("Claude не ответил")


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
