#!/usr/bin/env python3
"""{{NAME}} — пример-заготовка Студии: заметки в чате и в мини-приложении. Claude переписывает его под ТЗ.
Как устроено — в botkit.py (шапка). Стиль: заголовок жирным с одним эмодзи, содержимое — цитатой, кнопки ≤ 24 символов."""
import botkit as kit

bot = kit.Bot("{{NAME}}", emoji="📝", about="Быстрые заметки: пиши сюда — я сохраню.", short="Быстрые заметки",
              commands=[("start", "Главное"), ("help", "Что умею")], app_text="Заметки")


def notes():
    return bot.db.get("notes", [])


@bot.home
def home(c):
    items = notes()
    lines = [f"• {kit.esc(n['text'][:60])}" for n in items[-5:][::-1]] or ["<i>Пока пусто — просто напиши что-нибудь.</i>"]
    # bot.menu сам добавляет кнопку приложения (кроме режима «чат»), «⚙️ Режим» и «🛠 Поддержка»
    bot.menu(c, f"📝 <b>Заметки</b> · {kit.plural(len(items), 'штука', 'штуки', 'штук')}", lines,
             buttons=[[kit.b("➕ Новая", "new", "primary"), kit.b("🗑 Очистить", "clear?") if items else None]],
             quick=[[kit.b("➕ Новая", "new", "primary")]])


@bot.command("help")
def help_(c):
    c.say(kit.card("💡 <b>Что я умею</b>", ["Пиши текстом или голосом — сохраню заметку.",
                                          "Все заметки — в приложении (/app) или кнопками здесь (/mode).",
                                          "🛠 Что-то не так или хочется улучшить — /fix"]), [[kit.b("‹ Главное", "home")]])


def add(text):
    bot.db.update("notes", lambda v: v + [{"text": text.strip()[:1000], "at": kit.now().strftime("%d.%m %H:%M")}], [])


@bot.button("new")
def new(c):
    c.ask("note", "✍️ Что записать?")


@bot.reply("note")
@bot.text
def on_text(c):
    add(c.text)
    c.say("✅ Записал")
    home(kit.Ctx(bot, c.chat, c.user))


@bot.button("clear?")
def clear_ask(c):
    c.show(kit.card("🗑 <b>Удалить все заметки?</b>", ["Это не отменить."]),
           [[kit.b("Да, удалить", "clear", "danger"), kit.b("Нет", "home")]])


@bot.button("clear")
def clear(c):
    bot.db.set("notes", [])
    c.toast("Удалил")
    home(c)


# --- мини-приложение ---

@bot.api("state")
def state(r):
    return {"notes": notes()[::-1]}


@bot.api("add")
def api_add(r):
    text = str(r.body.get("text") or "").strip()
    if not text:
        raise kit.Fail("Пустая заметка")
    add(text)
    return state(r)


@bot.api("delete")
def api_delete(r):
    i = int(r.body.get("index", -1))
    items = notes()[::-1]
    if not 0 <= i < len(items):
        raise kit.Fail("Такой заметки нет")
    items.pop(i)
    bot.db.set("notes", items[::-1])
    return state(r)


if __name__ == "__main__":
    bot.run()
