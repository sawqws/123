"""☰ Меню-квадратик у поля ввода — во всех ботах как у Главного (Глеб, 16.10: «в каждом боте добавь штуку, которую
я обвёл»). Это обычная клавиатура Telegram (ReplyKeyboardMarkup), свёрнутая в значок справа от поля ввода:
one_time_keyboard — после нажатия прячется, значок остаётся; is_persistent=False — не занимает экран.

Подключение (только стандартная библиотека):

    import menukb
    KB = menukb.MenuKB([["🔄 Обновить", "⚙️ Режим"], ["🛠 Поддержка"]], version=1, placeholder="Номер машины…")
    ...
    KB.ensure(send_fn, chat, done=st.get("kb_ver"), mark=lambda v: st.update(kb_ver=v))   # /start и при запуске
    if KB.has(text):        # нажали кнопку меню — это текст сообщения
        ...

send_fn(chat, text, reply_markup) — функция бота, которая шлёт сообщение (свой tg/sendMessage).
"""

NOTE = "☰ Меню — квадратик справа от поля ввода"
REV = 1  # общий сброс: +1 — все боты при запуске заново ставят меню владельцу (23.10: у Глеба пропал квадратик)


class MenuKB:
    def __init__(self, rows, version=1, placeholder="Напишите или ☰ справа"):
        self.rows = [list(r) for r in rows]
        self.version = version + 1000 * REV
        self.placeholder = placeholder[:64]
        self.texts = {t for r in self.rows for t in r}

    def markup(self):
        return {"keyboard": [[{"text": t} for t in r] for r in self.rows], "resize_keyboard": True,
                "one_time_keyboard": True, "is_persistent": False, "input_field_placeholder": self.placeholder}

    def has(self, text):
        return (text or "").strip() in self.texts

    def ensure(self, send_fn, chat, done=None, mark=None, force=False, text=NOTE):
        """Поставить меню один раз на версию (или force — по /start). True — отправили."""
        if not chat or (not force and done == self.version):
            return False
        try:
            send_fn(chat, text, self.markup())
        except Exception as e:
            print("меню-квадратик:", repr(e), flush=True)
            return False
        if mark:
            mark(self.version)
        return True
