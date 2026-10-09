"""Оформление сообщений агента: Markdown Claude -> HTML Telegram, живой статус, карточки."""
import html
import re
import shlex

DOT = " · "


def esc(x):
    return html.escape(str(x), quote=False)


def card(title, body="", foot=""):
    """Заголовок жирным, тело — цитатой (длинное — раскрывается), подвал — курсивом."""
    out = f"<b>{title}</b>"
    if body:
        out += "\n" + quote(body)
    if foot:
        out += f"\n<i>{foot}</i>"
    return out


def quote(body_html, fold=6):
    lines = body_html.count("\n") + 1
    tag = "<blockquote expandable>" if lines > fold or len(body_html) > 500 else "<blockquote>"
    return f"{tag}{body_html}</blockquote>"


# ---------- Markdown -> HTML ----------

INLINE = [
    (re.compile(r"\*\*(.+?)\*\*"), r"<b>\1</b>"),
    (re.compile(r"__(.+?)__"), r"<u>\1</u>"),
    (re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])"), r"<i>\1</i>"),
    (re.compile(r"(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])"), r"<i>\1</i>"),
    (re.compile(r"~~(.+?)~~"), r"<s>\1</s>"),
    (re.compile(r"\|\|(.+?)\|\|"), r"<tg-spoiler>\1</tg-spoiler>"),
    (re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)"), r'<a href="\2">\1</a>'),
]


def inline(text):
    """Строка без кода: экранировать и разметить."""
    parts = re.split(r"(`[^`\n]+`)", text)
    out = []
    for p in parts:
        if len(p) > 1 and p.startswith("`") and p.endswith("`"):
            out.append(f"<code>{esc(p[1:-1])}</code>")
            continue
        p = esc(p)
        for rx, rep in INLINE:
            p = rx.sub(rep, p)
        out.append(p)
    return "".join(out)


def md(text):
    """Ответ Claude (Markdown) -> HTML для Telegram: жирный, курсив, код, ссылки, списки, цитаты, заголовки."""
    out, quote_buf, code_buf, in_code = [], [], [], False

    def flush_quote():
        if quote_buf:
            out.append(quote("\n".join(quote_buf)))
            quote_buf.clear()

    for raw in text.strip().split("\n"):
        if raw.strip().startswith("```"):
            if in_code:
                out.append("<pre>" + esc("\n".join(code_buf)) + "</pre>")
                code_buf.clear()
            else:
                flush_quote()
            in_code = not in_code
            continue
        if in_code:
            code_buf.append(raw)
            continue
        s = raw.rstrip()
        m = re.match(r"\s*>\s?(.*)", s)
        if m:
            quote_buf.append(inline(m.group(1)))
            continue
        flush_quote()
        if re.fullmatch(r"\s*([-*_])\s*(\1\s*){2,}", s):  # --- разделитель
            out.append("")
            continue
        if re.fullmatch(r"\s*\|?[\s:|-]+\|[\s:|-]*", s):  # строка-разделитель таблицы
            continue
        h = re.match(r"\s*#{1,6}\s+(.*)", s)
        if h:
            title = re.sub(r"^\*\*(.*)\*\*$", r"\1", h.group(1).strip())
            if out and out[-1] != "":
                out.append("")
            out.append(f"<b>{inline(title)}</b>")
            continue
        b = re.match(r"(\s*)[-*+•]\s+(.*)", s)
        if b:
            pad = "    " if len(b.group(1)) >= 2 else ""
            out.append(f"{pad}{'◦' if pad else '•'} {inline(b.group(2))}")
            continue
        n = re.match(r"\s*(\d+)[.)]\s+(.*)", s)
        if n:
            out.append(f"<b>{n.group(1)}.</b> {inline(n.group(2))}")
            continue
        if s.strip().startswith("|") and s.strip().endswith("|"):  # таблица -> строка через «·»
            cells = [c.strip() for c in s.strip().strip("|").split("|")]
            out.append(DOT.join(inline(c) for c in cells if c))
            continue
        out.append(inline(s))
    if in_code:
        out.append("<pre>" + esc("\n".join(code_buf)) + "</pre>")
    flush_quote()
    text = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def split_html(text, size=3900):
    """Резать длинный HTML по пустым строкам, не разрывая теги-блоки."""
    if len(text) <= size:
        return [text]
    parts, cur = [], ""
    for block in re.split(r"(?<=\n)\n", text):
        if len(cur) + len(block) > size and cur:
            parts.append(cur.strip())
            cur = ""
        while len(block) > size:  # один огромный блок — режем по строкам и закрываем теги
            cut = block.rfind("\n", 0, size)
            cut = cut if cut > 0 else size
            parts.append(close_tags(block[:cut]))
            block = reopen_tags(block[:cut]) + block[cut:].lstrip("\n")
        cur += block
    if cur.strip():
        parts.append(cur.strip())
    return parts


TAG = re.compile(r"<(/?)(b|i|u|s|code|pre|blockquote|tg-spoiler|a)(\s[^>]*)?>")


def open_stack(text):
    stack = []
    for m in TAG.finditer(text):
        if m.group(1):
            if stack and stack[-1][0] == m.group(2):
                stack.pop()
        else:
            stack.append((m.group(2), m.group(0)))
    return stack


def close_tags(text):
    return text + "".join(f"</{t}>" for t, _ in reversed(open_stack(text)))


def reopen_tags(text):
    return "".join(o for _, o in open_stack(text))


# ---------- Живой статус: что делает Claude ----------

STEPS = {
    "me": "👤 Смотрю твой аккаунт",
    "chats": "🗂 Листаю список чатов",
    "history": "📖 Читаю {chat}",
    "search": "🔎 Ищу «{q}»",
    "members": "👥 Смотрю участников {chat}",
    "info": "ℹ️ Узнаю про {chat}",
    "folders": "📁 Смотрю папки",
    "jobs": "⏰ Смотрю расписание",
    "read": "👁 Отмечаю прочитанным {chat}",
    "send": "✉️ Готовлю сообщение в {chat}",
    "edit": "✏️ Правлю сообщение в {chat}",
    "forward": "↪️ Пересылаю из {chat}",
    "delete": "🗑 Удаляю в {chat}",
    "pin": "📌 Закрепляю в {chat}",
    "unpin": "📌 Открепляю в {chat}",
    "kick": "👢 Удаляю из {chat}",
    "ban": "⛔ Баню в {chat}",
    "unban": "🔓 Разбаниваю в {chat}",
    "mute": "🔇 Ограничиваю в {chat}",
    "unmute": "🔊 Снимаю ограничения в {chat}",
    "admin": "⭐ Назначаю админа в {chat}",
    "unadmin": "➖ Снимаю админа в {chat}",
    "add": "➕ Добавляю в {chat}",
    "title": "🏷 Переименовываю {chat}",
    "about": "📝 Меняю описание {chat}",
    "link": "🔗 Делаю ссылку в {chat}",
    "create": "🆕 Создаю группу",
    "leave": "🚪 Выхожу из {chat}",
    "folder": "📁 Раскладываю в папку «{chat}»",
    "archive": "🗄 Убираю в архив",
    "unarchive": "🗄 Достаю из архива",
    "notify": "🔕 Настраиваю уведомления",
    "job": "⏰ Ставлю расписание",
    "unjob": "⏰ Убираю расписание",
}


def step(tool, inp, tg_path, names=None):
    """Шаг Claude (имя инструмента и его вход) -> строка для статуса или None. names: id чата -> название."""
    if tool == "Read":
        return "📄 Читаю длинную переписку"
    if tool != "Bash":
        return None
    cmd = (inp or {}).get("command", "")
    try:
        argv = shlex.split(cmd)
    except ValueError:
        return None
    if tg_path not in argv:
        return None
    rest = argv[argv.index(tg_path) + 1:]
    if not rest or rest[0].startswith("-"):
        return "📚 Вспоминаю команды"
    name = rest[0]
    tpl = STEPS.get(name)
    if not tpl:
        return None
    if "--help" in rest:
        return "📚 Вспоминаю команды"
    first = rest[1] if len(rest) > 1 and not rest[1].startswith("--") else ""  # чат — сразу после команды
    if first.lstrip("-").isdigit():
        first = (names or {}).get(first, "")
    chat = f"«{first}»" if first else "чат"
    return tpl.format(chat=chat, q=first)


SPIN = "◐◓◑◒"


def status(steps, seconds, thinking=True):
    """Карточка «работаю»: пройденные шаги и текущий."""
    t = f"{seconds // 60}:{seconds % 60:02d}"
    lines = [f"✓ {esc(s)}" for s in steps[-8:-1]] if steps else []
    if len(steps) > 8:
        lines.insert(0, f"<i>…и ещё {len(steps) - 8}</i>")
    if steps:
        lines.append(f"{SPIN[seconds // 2 % 4]} {esc(steps[-1])}")
    elif thinking:
        lines.append(f"{SPIN[seconds // 2 % 4]} Думаю")
    return f"<b>✦ Работаю</b>  <code>{t}</code>\n<blockquote>" + "\n".join(lines) + "</blockquote>"


def plural(n, one, few, many):
    n10, n100 = n % 10, n % 100
    w = one if n10 == 1 and n100 != 11 else few if 2 <= n10 <= 4 and not 12 <= n100 <= 14 else many
    return f"{n} {w}"
