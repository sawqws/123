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
NO_TOOLS = ["--disallowedTools", "Bash", "Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch", "Task", "Agent"]


def persona():
    with open(os.path.join(HERE, "prompt.md"), encoding="utf-8") as f:
        return f.read()


class AIError(Exception):
    pass


def claude(prompt, image=None, timeout=240):
    """(текст ответа). image — путь к картинке: Claude откроет её инструментом Read."""
    os.makedirs(WORK, exist_ok=True)
    tools = []
    if image:
        dst = os.path.join(WORK, f"photo-{uuid.uuid4().hex[:8]}" + os.path.splitext(image)[1].lower())
        shutil.copyfile(image, dst)
        prompt += f"\n\nФото: {dst} (открой его инструментом Read)."
        tools = ["--allowedTools", "Read", "--max-turns", "4"]
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
            + f"ВОПРОС: {question}\n\nОтветь как тренер по правилам из системного промпта.")


def ask(context, history, question):
    return claude(chat_prompt(context, history, question))


def morning(context):
    return claude(
        f"ДАННЫЕ:\n{context}\n\nНапиши утренний разбор на сегодня (до 8 строк):\n"
        "1) одна строка — вывод о готовности (восстановление, сон, ВСР против нормы);\n"
        "2) тренировка сегодня: можно ли в полную силу, какая интенсивность и ориентир нагрузки, что лучше заменить;\n"
        "3) питание и вода на сегодня одной-двумя строками (ккал и белок из расчёта, когда есть тренировка — что съесть до и после);\n"
        "4) во сколько лечь спать сегодня, чтобы выспаться.\n"
        "Не повторяй все цифры подряд — только те, что важны для решений.")


def after_workout(context, w):
    return claude(
        f"ДАННЫЕ:\n{context}\n\nТолько что закончилась тренировка: {w['sport']}, {w['minutes']} мин, нагрузка {w['strain']}, "
        f"пульс ср/макс {w['avg_hr']}/{w['max_hr']}, минуты в зонах 0–5: {w['zones']}.\n"
        "Дай короткий разбор (до 5 строк): насколько тяжёлой была тренировка для текущего состояния, "
        "что съесть и выпить в ближайший час (с граммами), и что сделать для восстановления сегодня вечером.")


def progress(context, week=False):
    """Большой разбор прогресса (или итог недели) по сводке insights.progress_context."""
    if week:
        task = ("Напиши итог недели (до 12 строк): как прошла неделя против прошлых (восстановление, ВСР, пульс покоя, "
                "сон, нагрузка), что получилось, что мешало, и 3 конкретные цели на следующую неделю с числами.")
    else:
        task = (
            "Сделай полный разбор моего прогресса за весь период — как спортивный врач, тренер и нутрициолог. Структура "
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


MEAL_ASK = (
    "Оцени, что съедено, и верни ТОЛЬКО JSON без пояснений вокруг:\n"
    '{"food": true, "title": "коротко по-русски, что это", "kcal": число, "protein": граммы, "fat": граммы, '
    '"carbs": граммы, "comment": "одна строка совета тренера: хорошо ли это сейчас и что добавить"}\n'
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
    for k in ("kcal", "protein", "fat", "carbs"):
        try:
            out[k] = max(0, round(float(out.get(k) or 0)))
        except (TypeError, ValueError):
            out[k] = 0
    out["title"] = str(out.get("title") or text or "Еда")[:120]
    return out


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
