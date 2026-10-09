#!/usr/bin/env python3
"""Команды для аккаунта Telegram Глеба. Работают через запущенного агента (tgbot/agent.py):
он держит вход в аккаунт и спрашивает Глеба перед любым действием, которое что-то меняет.

  python3 tgbot/tg.py --help              список команд
  python3 tgbot/tg.py history "Класс" --limit 100

Чат можно указать по id, @username, ссылке t.me или части названия; человека — по id, @username или имени.
Только стандартная библиотека Python.
"""
import argparse
import json
import os
import socket
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SOCK = os.environ.get("TG_AGENT_SOCK") or os.path.join(HERE, "tmp", "agent.sock")
BIG = 20000  # больше — сохраняем в файл, чтобы не забить контекст Claude


def parser():
    p = argparse.ArgumentParser(prog="tg.py", description="Аккаунт Telegram Глеба. ⚠ = спросит подтверждение у Глеба.")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="команда")

    def cmd(name, help_):
        return sub.add_parser(name, help=help_, description=help_)

    cmd("me", "кто я (аккаунт)")
    c = cmd("chats", "список чатов")
    c.add_argument("--unread", action="store_true", help="только с непрочитанными")
    c.add_argument("--query", help="часть названия")
    c.add_argument("--type", choices=["личка", "группа", "канал", "бот"])
    c.add_argument("--limit", type=int, default=100)
    c = cmd("history", "сообщения чата (от старых к новым)")
    c.add_argument("chat")
    c.add_argument("--limit", type=int, default=50, help="сколько последних (до 3000)")
    c.add_argument("--since", help="не раньше даты: 2026-10-09 или «2026-10-09 14:00»")
    c.add_argument("--search", help="только содержащие текст")
    c.add_argument("--sender", help="только от этого человека")
    c.add_argument("--unread", action="store_true", help="только непрочитанные")
    c = cmd("search", "поиск текста по всем чатам")
    c.add_argument("query")
    c.add_argument("--limit", type=int, default=30)
    c = cmd("members", "участники группы")
    c.add_argument("chat")
    c.add_argument("--query", help="поиск по имени")
    c.add_argument("--admins", action="store_true")
    c.add_argument("--bots", action="store_true")
    c.add_argument("--limit", type=int, default=200)
    c = cmd("info", "о чате или человеке")
    c.add_argument("target")
    c = cmd("read", "отметить чат прочитанным")
    c.add_argument("chat")

    c = cmd("send", "⚠ написать сообщение (или запланировать --at)")
    c.add_argument("chat")
    c.add_argument("text", nargs="?", default="")
    c.add_argument("--reply", type=int, help="id сообщения, на которое ответить")
    c.add_argument("--at", help="отправить позже: «2026-10-10 09:00» (Москва)")
    c.add_argument("--file", help="путь к файлу")
    c = cmd("edit", "⚠ изменить своё сообщение")
    c.add_argument("chat")
    c.add_argument("id", type=int)
    c.add_argument("text")
    c = cmd("forward", "⚠ переслать сообщения")
    c.add_argument("chat", help="откуда")
    c.add_argument("ids", type=int, nargs="+")
    c.add_argument("--to", required=True, help="куда")
    c = cmd("delete", "⚠ удалить сообщения у всех")
    c.add_argument("chat")
    c.add_argument("ids", type=int, nargs="+")
    c = cmd("pin", "⚠ закрепить сообщение")
    c.add_argument("chat")
    c.add_argument("id", type=int)
    c.add_argument("--silent", action="store_true", help="без уведомления")
    c = cmd("unpin", "⚠ открепить (без id — все)")
    c.add_argument("chat")
    c.add_argument("id", type=int, nargs="?")
    for name, help_ in (("kick", "удалить из группы"), ("ban", "забанить"), ("unban", "разбанить"),
                        ("unmute", "снова разрешить писать"), ("unadmin", "снять админа"), ("add", "добавить в группу")):
        c = cmd(name, "⚠ " + help_)
        c.add_argument("chat")
        c.add_argument("users", nargs="+")
    c = cmd("mute", "⚠ запретить писать")
    c.add_argument("chat")
    c.add_argument("users", nargs="+")
    c.add_argument("--minutes", type=int, help="на сколько минут (без — навсегда)")
    c = cmd("admin", "⚠ сделать админом")
    c.add_argument("chat")
    c.add_argument("users", nargs="+")
    c.add_argument("--title", help="подпись админа")
    c = cmd("title", "⚠ переименовать группу")
    c.add_argument("chat")
    c.add_argument("text")
    c = cmd("about", "⚠ изменить описание группы")
    c.add_argument("chat")
    c.add_argument("text")
    c = cmd("link", "⚠ создать ссылку-приглашение")
    c.add_argument("chat")
    c = cmd("create", "⚠ создать группу (или --channel)")
    c.add_argument("title")
    c.add_argument("users", nargs="*")
    c.add_argument("--about")
    c.add_argument("--channel", action="store_true")
    c = cmd("leave", "⚠ выйти из группы")
    c.add_argument("chat")

    cmd("folders", "папки Telegram и чаты в них")
    c = cmd("folder", "⚠ создать папку или добавить/убрать в ней чаты")
    c.add_argument("name", help="название папки")
    c.add_argument("chats", nargs="*", help="какие чаты добавить")
    c.add_argument("--remove", nargs="+", help="какие чаты убрать")
    c.add_argument("--emoji", help="значок папки, например 📚")
    c.add_argument("--delete", action="store_true", help="удалить папку (чаты останутся)")
    c = cmd("archive", "⚠ убрать чаты в архив")
    c.add_argument("chats", nargs="+")
    c = cmd("unarchive", "⚠ вернуть чаты из архива")
    c.add_argument("chats", nargs="+")
    c = cmd("job", "⚠ сообщение по расписанию (повторять или один раз)")
    c.add_argument("chats", nargs="+", help="кому")
    c.add_argument("--time", required=True, help="во сколько, ЧЧ:ММ (Москва)")
    c.add_argument("--days", help="«каждый день» (по умолчанию), «будни», «выходные», «пн,ср,пт», «пн-пт»")
    c.add_argument("--once", help="один раз в эту дату ГГГГ-ММ-ДД")
    c.add_argument("--until", help="повторять до даты ГГГГ-ММ-ДД")
    c.add_argument("--text", help="готовый текст")
    c.add_argument("--ai", help="что написать — Claude каждый раз сочиняет новый текст")
    cmd("jobs", "список расписаний")
    c = cmd("unjob", "⚠ удалить расписание")
    c.add_argument("id")
    c = cmd("notify", "⚠ выключить (off) или включить (on) уведомления чатов")
    c.add_argument("mode", choices=["off", "on"])
    c.add_argument("chats", nargs="+")
    c.add_argument("--hours", type=int, help="на сколько часов выключить (без — навсегда)")
    return p


def call(cmd, args, timeout=20 * 60):
    """Запрос агенту: (ok, текст)."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(SOCK)
    except OSError:
        s.close()
        return False, "агент не запущен (pm2 status tg-agent)"
    with s:
        s.sendall(json.dumps({"cmd": cmd, "args": args}, ensure_ascii=False).encode() + b"\n")
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    try:
        r = json.loads(buf)
    except ValueError:
        return False, "агент оборвал ответ"
    return r["ok"], r["out"]


def show(out):
    if len(out) <= BIG:
        return out
    os.makedirs(os.path.join(HERE, "tmp", "out"), exist_ok=True)
    path = os.path.join(HERE, "tmp", "out", time.strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}.txt")
    with open(path, "w") as f:
        f.write(out)
    n = out.count("\n") + 1
    return f"Вывод большой ({n} строк), целиком в {path} — читай его через Read частями.\n\nНачало:\n{out[:3000]}"


def main(argv=None):
    a = vars(parser().parse_args(argv))
    cmd = a.pop("cmd")
    ok, out = call(cmd, a)
    print(show(out) if ok else "Ошибка: " + out)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
