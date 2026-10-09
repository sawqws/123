"""Действия с аккаунтом Telegram Глеба (Telethon). Их вызывает Claude через tgbot/tg.py.

Каждая команда — async-функция (ctx, args) -> str. ctx даёт:
  ctx.client         — TelegramClient аккаунта
  await ctx.confirm(text)  — спросить Глеба кнопкой; при отказе бросает Cancelled
Команды с write=True что-то меняют в Telegram и обязаны вызвать ctx.confirm перед делом.
"""
import datetime
import os
import re
import time
from zoneinfo import ZoneInfo

from telethon import errors, functions, types, utils

import jobs

MSK = ZoneInfo("Europe/Moscow")
COMMANDS = {}  # имя -> (функция, write)


class Fail(Exception):
    """Понятная ошибка для Claude (без трассировки)."""


class Cancelled(Exception):
    """Глеб нажал «Нет» или не ответил."""


def command(name, write=False):
    def deco(fn):
        COMMANDS[name] = (fn, write)
        return fn
    return deco


async def run(ctx, cmd, args):
    if cmd not in COMMANDS:
        raise Fail(f"нет команды {cmd}")
    fn, _ = COMMANDS[cmd]
    try:
        return await fn(ctx, args)
    except Cancelled:
        return "❌ Глеб не подтвердил, действие не выполнено. Не повторяй его без новой просьбы."
    except errors.FloodWaitError as e:
        raise Fail(f"Telegram просит подождать {e.seconds} с, повтори позже")
    except (errors.ChatAdminRequiredError, errors.UserAdminInvalidError, errors.RightForbiddenError):
        raise Fail("нужны права администратора в этом чате")
    except errors.RPCError as e:
        raise Fail(f"Telegram ответил ошибкой {e.__class__.__name__}: {e.message}")


# ---------- Поиск чатов и людей ----------

_dialogs = {}  # id(client) -> (время, список диалогов)


async def dialogs(ctx, fresh=False):
    key = id(ctx.client)
    t, ds = _dialogs.get(key, (0, None))
    if fresh or ds is None or time.time() - t > 120:
        ds = await ctx.client.get_dialogs(limit=None)
        _dialogs[key] = (time.time(), ds)
    return ds


def pick(items, query, name):
    """Из items (пары (имя, объект)) — один по точному совпадению или подстроке, иначе Fail."""
    q = query.casefold().strip()
    exact = [o for n, o in items if n.casefold() == q]
    if len(exact) == 1:
        return exact[0]
    part = exact or [o for n, o in items if q in n.casefold()]
    if len(part) == 1:
        return part[0]
    if not part:
        raise Fail(f"{name} «{query}» не найден")
    names = "; ".join(n for n, o in items if o in part[:15])
    raise Fail(f"под «{query}» подходят несколько ({len(part)}): {names}. Укажи точнее или по id")


def as_id(s):
    s = str(s).strip()
    return int(s) if re.fullmatch(r"-?\d+", s) else None


async def chat(ctx, query):
    """Чат по id, @username, ссылке t.me или части названия."""
    q = str(query).strip()
    if q.casefold() in ("me", "избранное", "saved"):
        return await ctx.client.get_entity("me")
    num = as_id(q)
    if num is not None or q.startswith("@") or "t.me/" in q:
        try:
            return await ctx.client.get_entity(num if num is not None else q)
        except (ValueError, errors.RPCError):
            raise Fail(f"чат {q} не найден")
    ds = await dialogs(ctx)
    return pick([(d.name or "", d.entity) for d in ds], q, "чат")


async def user(ctx, query, where=None):
    """Человек по id, @username или имени (сначала среди участников чата where, потом в личках)."""
    q = str(query).strip()
    num = as_id(q)
    if num is not None or q.startswith("@"):
        try:
            return await ctx.client.get_entity(num if num is not None else q)
        except (ValueError, errors.RPCError):
            raise Fail(f"человек {q} не найден")
    if where is not None and not isinstance(where, types.User):
        found = [p async for p in ctx.client.iter_participants(where, search=q, limit=50)]
        if found:
            return pick([(name(p), p) for p in found], q, "участник")
    ds = await dialogs(ctx)
    return pick([(d.name or "", d.entity) for d in ds if d.is_user], q, "человек")


async def users(ctx, queries, where=None):
    return [await user(ctx, q, where) for q in queries]


# ---------- Форматирование ----------

def name(e):
    if e is None:
        return "?"
    if isinstance(e, types.User):
        n = " ".join(x for x in (e.first_name, e.last_name) if x) or "Удалённый аккаунт"
    else:
        n = getattr(e, "title", None) or "?"
    return n


def who(e):
    """Имя (@username, id) — чтобы Claude мог сослаться точно."""
    u = getattr(e, "username", None)
    return f"{name(e)} ({'@' + u + ', ' if u else ''}id {utils.get_peer_id(e)})"


def kind(e):
    if isinstance(e, types.User):
        return "бот" if e.bot else "личка"
    if isinstance(e, types.Channel):
        return "группа" if e.megagroup else "канал"
    return "группа"


def media(m):
    if m.action is not None:
        return f"[служебное: {type(m.action).__name__.removeprefix('MessageAction')}] "
    if m.media is None:
        return ""
    if m.photo:
        return "[фото] "
    if m.voice:
        return "[голосовое] "
    if m.video_note:
        return "[кружок] "
    if m.gif:
        return "[гиф] "
    if m.video:
        return "[видео] "
    if m.sticker:
        return f"[стикер {getattr(m.file, 'emoji', '') or ''}] "
    if m.poll:
        q = m.poll.poll.question
        return f"[опрос: {getattr(q, 'text', q)}] "
    if m.geo:
        return "[геопозиция] "
    if m.contact:
        return "[контакт] "
    if m.document:
        return f"[файл {getattr(m.file, 'name', '') or ''}] "
    return "[вложение] "


def line(m):
    t = m.date.astimezone(MSK).strftime("%d.%m.%y %H:%M")
    author = "Я" if m.out else name(m.sender) if m.sender else str(m.sender_id)
    re_ = f" (ответ на {m.reply_to_msg_id})" if m.reply_to_msg_id else ""
    fwd = " [переслано]" if m.fwd_from else ""
    text = (m.message or "").replace("\n", " ⏎ ")
    if len(text) > 3000:
        text = text[:3000] + "…"
    return f"[{m.id}] {t} {author}{re_}{fwd}: {media(m)}{text}"


def date(s):
    """«2026-10-09», «2026-10-09 14:30» или «09.10 14:30» — московское время."""
    s = s.strip()
    year = datetime.datetime.now(MSK).year
    for f in ("%Y-%m-%d %H:%M", "%Y-%m-%d", "%d.%m.%Y %H:%M", "%d.%m.%Y", "%d.%m %H:%M", "%d.%m"):
        try:
            d = datetime.datetime.strptime(s, f) if "%Y" in f else \
                datetime.datetime.strptime(f"{s} {year}", f + " %Y")  # год сразу, иначе 29.02 не разберётся
        except ValueError:
            continue
        return d.replace(tzinfo=MSK)
    raise Fail(f"не понял дату «{s}», нужно ГГГГ-ММ-ДД ЧЧ:ММ")


def is_channel(e):
    return isinstance(e, types.Channel)


# ---------- Чтение (без подтверждения) ----------

@command("me")
async def me(ctx, a):
    m = await ctx.client.get_me()
    return f"Аккаунт: {who(m)}, телефон +{m.phone}"


@command("chats")
async def chats(ctx, a):
    ds = await dialogs(ctx, fresh=True)
    if a.get("unread"):
        ds = [d for d in ds if d.unread_count or d.unread_mentions_count]
    if a.get("query"):
        q = a["query"].casefold()
        ds = [d for d in ds if q in (d.name or "").casefold()]
    if a.get("type"):
        ds = [d for d in ds if kind(d.entity) == a["type"]]
    ds = ds[: a.get("limit") or 100]
    if not ds:
        return "Ничего не найдено"
    out = []
    for d in ds:
        u = getattr(d.entity, "username", None)
        extra = []
        if d.unread_count:
            extra.append(f"непрочитано {d.unread_count}")
        if d.unread_mentions_count:
            extra.append(f"упоминаний {d.unread_mentions_count}")
        if d.archived:
            extra.append("архив")
        if d.pinned:
            extra.append("закреплён")
        last = d.date.astimezone(MSK).strftime("%d.%m %H:%M") if d.date else ""
        out.append(f"{d.id} | {kind(d.entity)} | {d.name}{' | @' + u if u else ''} | {last}"
                   + (" | " + ", ".join(extra) if extra else ""))
    return "id | тип | название | последнее | отметки\n" + "\n".join(out)


@command("history")
async def history(ctx, a):
    c = await chat(ctx, a["chat"])
    since = date(a["since"]) if a.get("since") else None
    kw = {}
    if a.get("search"):
        kw["search"] = a["search"]
    if a.get("sender"):
        kw["from_user"] = await user(ctx, a["sender"], c)
    if a.get("unread"):
        d = next((d for d in await dialogs(ctx, fresh=True) if d.id == utils.get_peer_id(c)), None)
        n = d.unread_count if d else 0
        if not n:
            return f"В «{name(c)}» нет непрочитанных"
        kw["min_id"] = d.dialog.read_inbox_max_id
    limit = min(a.get("limit") or 50, 3000)
    msgs = []
    async for m in ctx.client.iter_messages(c, limit=limit, **kw):
        if since and m.date < since:
            break
        msgs.append(m)
    if not msgs:
        return f"В «{name(c)}» сообщений не найдено"
    msgs.reverse()
    head = f"Чат: {who(c)}, сообщений: {len(msgs)} (время московское)"
    return head + "\n" + "\n".join(line(m) for m in msgs)


@command("search")
async def search(ctx, a):
    msgs = [m async for m in ctx.client.iter_messages(None, search=a["query"], limit=min(a.get("limit") or 30, 200))]
    if not msgs:
        return "Ничего не найдено"
    out = []
    for m in msgs:
        c = await m.get_chat()
        out.append(f"{name(c)} (id {m.chat_id}) " + line(m))
    return "\n".join(out)


@command("members")
async def members(ctx, a):
    c = await chat(ctx, a["chat"])
    if isinstance(c, types.User):
        raise Fail("это личка, а не группа")
    kw = {}
    if a.get("admins"):
        kw["filter"] = types.ChannelParticipantsAdmins
    if a.get("bots"):
        kw["filter"] = types.ChannelParticipantsBots
    out = []
    async for p in ctx.client.iter_participants(c, limit=min(a.get("limit") or 200, 10000), search=a.get("query") or "", **kw):
        part = getattr(p, "participant", None)
        role = ("создатель" if isinstance(part, (types.ChannelParticipantCreator, types.ChatParticipantCreator))
                else "админ" if isinstance(part, (types.ChannelParticipantAdmin, types.ChatParticipantAdmin))
                else "ограничен" if isinstance(part, types.ChannelParticipantBanned) else "")
        flags = [x for x in (role, "бот" if p.bot else "", "удалён" if p.deleted else "") if x]
        out.append(f"{p.id} | {name(p)}{' | @' + p.username if p.username else ''}" + (" | " + ", ".join(flags) if flags else ""))
    return f"{name(c)}: {len(out)} участников\n" + "\n".join(out)


@command("info")
async def info(ctx, a):
    e = await chat(ctx, a["target"])
    lines = [f"{kind(e)}: {who(e)}"]
    if isinstance(e, types.User):
        full = await ctx.client(functions.users.GetFullUserRequest(e))
        if full.full_user.about:
            lines.append("О себе: " + full.full_user.about)
        if e.phone:
            lines.append("Телефон: +" + e.phone)
        if e.status:
            lines.append("Статус: " + type(e.status).__name__.removeprefix("UserStatus"))
        return "\n".join(lines)
    if is_channel(e):
        full = (await ctx.client(functions.channels.GetFullChannelRequest(e))).full_chat
        lines.append(f"Участников: {full.participants_count}")
    else:
        full = (await ctx.client(functions.messages.GetFullChatRequest(e.id))).full_chat
        lines.append(f"Участников: {len(getattr(full.participants, 'participants', []) or [])}")
    if full.about:
        lines.append("Описание: " + full.about)
    if getattr(e, "admin_rights", None) or getattr(e, "creator", False):
        lines.append("Я: " + ("создатель" if e.creator else "админ"))
    else:
        lines.append("Я: обычный участник (админские действия недоступны)")
    return "\n".join(lines)


# ---------- Действия (с подтверждением) ----------

def cut(t, n=700):
    return t if len(t) <= n else t[:n] + "…"


@command("send", write=True)
async def send(ctx, a):
    c = await chat(ctx, a["chat"])
    when = date(a["at"]) if a.get("at") else None
    f = a.get("file")
    if f and not os.path.isfile(f):
        raise Fail(f"файла {f} нет")
    what = f"📤 Написать в «{name(c)}»" + (f" ответом на {a['reply']}" if a.get("reply") else "") \
        + (f" в {when:%d.%m %H:%M}" if when else "") + (f", файл {os.path.basename(f)}" if f else "") \
        + ":\n\n" + cut(a.get("text") or "")
    await ctx.confirm(what)
    m = await ctx.client.send_message(c, a.get("text") or "", reply_to=a.get("reply"), file=f, schedule=when,
                                      parse_mode=None, link_preview=False)
    return f"✅ Отправлено в «{name(c)}» (сообщение {m.id})" + (" — запланировано" if when else "")


@command("edit", write=True)
async def edit(ctx, a):
    c = await chat(ctx, a["chat"])
    await ctx.confirm(f"✏️ Изменить своё сообщение {a['id']} в «{name(c)}» на:\n\n{cut(a['text'])}")
    await ctx.client.edit_message(c, a["id"], a["text"], parse_mode=None)
    return "✅ Изменено"


@command("forward", write=True)
async def forward(ctx, a):
    src, dst = await chat(ctx, a["chat"]), await chat(ctx, a["to"])
    await ctx.confirm(f"↪️ Переслать {len(a['ids'])} сообщ. из «{name(src)}» в «{name(dst)}»")
    await ctx.client.forward_messages(dst, a["ids"], src)
    return "✅ Переслано"


@command("delete", write=True)
async def delete(ctx, a):
    c = await chat(ctx, a["chat"])
    msgs = await ctx.client.get_messages(c, ids=a["ids"])
    preview = "\n".join(line(m) for m in msgs if m)[:1500] or "(сообщения не найдены)"
    await ctx.confirm(f"🗑 Удалить {len(a['ids'])} сообщ. в «{name(c)}» у всех:\n\n{preview}")
    res = await ctx.client.delete_messages(c, a["ids"], revoke=True)
    n = sum(r.pts_count for r in res) if res else 0
    return f"✅ Удалено {n} из {len(a['ids'])}"


@command("read")
async def read(ctx, a):
    c = await chat(ctx, a["chat"])
    await ctx.client.send_read_acknowledge(c)
    return f"✅ «{name(c)}» отмечен прочитанным"


@command("pin", write=True)
async def pin(ctx, a):
    c = await chat(ctx, a["chat"])
    m = await ctx.client.get_messages(c, ids=a["id"])
    await ctx.confirm(f"📌 Закрепить в «{name(c)}»:\n\n{line(m) if m else a['id']}")
    await ctx.client.pin_message(c, a["id"], notify=not a.get("silent"))
    return "✅ Закреплено"


@command("unpin", write=True)
async def unpin(ctx, a):
    c = await chat(ctx, a["chat"])
    await ctx.confirm(f"📌 Открепить в «{name(c)}» " + (f"сообщение {a['id']}" if a.get("id") else "все сообщения"))
    await ctx.client.unpin_message(c, a.get("id"))
    return "✅ Откреплено"


def group(c):
    if isinstance(c, types.User):
        raise Fail("это личка, а не группа")
    return c


async def each(ctx, a, verb, emoji, do):
    c = group(await chat(ctx, a["chat"]))
    us = await users(ctx, a["users"], c)
    await ctx.confirm(f"{emoji} {verb} в «{name(c)}»: " + ", ".join(who(u) for u in us))
    done, bad = [], []
    for u in us:
        try:
            await do(c, u)
            done.append(name(u))
        except errors.RPCError as e:
            bad.append(f"{name(u)}: {e.message}")
    return (f"✅ {verb}: {', '.join(done)}" if done else "Никого") + ("\n⚠️ " + "; ".join(bad) if bad else "")


@command("kick", write=True)
async def kick(ctx, a):
    return await each(ctx, a, "Удалить из чата", "👢", lambda c, u: ctx.client.kick_participant(c, u))


@command("ban", write=True)
async def ban(ctx, a):
    return await each(ctx, a, "Забанить", "⛔", lambda c, u: ctx.client.edit_permissions(c, u, view_messages=False))


@command("unban", write=True)
async def unban(ctx, a):
    return await each(ctx, a, "Разбанить", "✅", lambda c, u: ctx.client.edit_permissions(c, u))


@command("mute", write=True)
async def mute(ctx, a):
    until = datetime.timedelta(minutes=a["minutes"]) if a.get("minutes") else None
    verb = f"Запретить писать ({a['minutes']} мин)" if until else "Запретить писать (навсегда)"
    return await each(ctx, a, verb, "🔇", lambda c, u: ctx.client.edit_permissions(c, u, until, send_messages=False))


@command("unmute", write=True)
async def unmute(ctx, a):
    return await each(ctx, a, "Разрешить писать", "🔊", lambda c, u: ctx.client.edit_permissions(c, u))


@command("admin", write=True)
async def admin(ctx, a):
    rights = dict(change_info=True, delete_messages=True, ban_users=True, invite_users=True,
                  pin_messages=True, manage_call=True, add_admins=False, is_admin=True)
    return await each(ctx, a, "Сделать админом", "⭐",
                      lambda c, u: ctx.client.edit_admin(c, u, title=a.get("title"), **rights))


@command("unadmin", write=True)
async def unadmin(ctx, a):
    return await each(ctx, a, "Снять админа", "➖", lambda c, u: ctx.client.edit_admin(c, u, is_admin=False))


async def _invite(ctx, c, u):
    if is_channel(c):
        await ctx.client(functions.channels.InviteToChannelRequest(c, [u]))
    else:
        await ctx.client(functions.messages.AddChatUserRequest(c.id, u, fwd_limit=50))


@command("add", write=True)
async def add(ctx, a):
    c = group(await chat(ctx, a["chat"]))
    us = await users(ctx, a["users"])
    await ctx.confirm(f"➕ Добавить в «{name(c)}»: " + ", ".join(who(u) for u in us))
    done, bad = [], []
    for u in us:
        try:
            await _invite(ctx, c, u)
            done.append(name(u))
        except errors.RPCError as e:
            bad.append(f"{name(u)}: {e.message}")
    return (f"✅ Добавлены: {', '.join(done)}" if done else "Никого не добавил") + ("\n⚠️ " + "; ".join(bad) if bad else "")


@command("title", write=True)
async def title(ctx, a):
    c = group(await chat(ctx, a["chat"]))
    await ctx.confirm(f"🏷 Переименовать «{name(c)}» → «{a['text']}»")
    if is_channel(c):
        await ctx.client(functions.channels.EditTitleRequest(c, a["text"]))
    else:
        await ctx.client(functions.messages.EditChatTitleRequest(c.id, a["text"]))
    return "✅ Название изменено"


@command("about", write=True)
async def about(ctx, a):
    c = group(await chat(ctx, a["chat"]))
    await ctx.confirm(f"📝 Описание «{name(c)}»:\n\n{cut(a['text'])}")
    await ctx.client(functions.messages.EditChatAboutRequest(c, a["text"]))
    return "✅ Описание изменено"


@command("link", write=True)
async def link(ctx, a):
    c = group(await chat(ctx, a["chat"]))
    await ctx.confirm(f"🔗 Создать ссылку-приглашение в «{name(c)}»")
    r = await ctx.client(functions.messages.ExportChatInviteRequest(c))
    return f"✅ Ссылка: {r.link}"


@command("create", write=True)
async def create(ctx, a):
    us = await users(ctx, a.get("users") or [])
    what = "канал" if a.get("channel") else "группу"
    await ctx.confirm(f"🆕 Создать {what} «{a['title']}»" + (": " + ", ".join(who(u) for u in us) if us else ""))
    r = await ctx.client(functions.channels.CreateChannelRequest(
        title=a["title"], about=a.get("about") or "", megagroup=not a.get("channel"), broadcast=bool(a.get("channel"))))
    c = r.chats[0]
    bad = []
    for u in us:
        try:
            await _invite(ctx, c, u)
        except errors.RPCError as e:
            bad.append(f"{name(u)}: {e.message}")
    _dialogs.pop(id(ctx.client), None)
    return f"✅ Создан{'' if a.get('channel') else 'а'} «{a['title']}» (id {utils.get_peer_id(c)})" \
        + ("\n⚠️ Не добавлены: " + "; ".join(bad) if bad else "")


@command("leave", write=True)
async def leave(ctx, a):
    c = group(await chat(ctx, a["chat"]))
    await ctx.confirm(f"🚪 Выйти из «{name(c)}»")
    await ctx.client.delete_dialog(c)
    _dialogs.pop(id(ctx.client), None)
    return "✅ Вышел"


# ---------- Папки, архив, уведомления ----------

def ftitle(f):
    t = getattr(f, "title", "")
    return getattr(t, "text", t) or ""


async def folder_list(ctx):
    r = await ctx.client(functions.messages.GetDialogFiltersRequest())
    return [f for f in getattr(r, "filters", r) if isinstance(f, types.DialogFilter)]


@command("folders")
async def folders(ctx, a):
    fs = await folder_list(ctx)
    if not fs:
        return "Папок нет"
    names = {d.id: d.name for d in await dialogs(ctx)}
    out = []
    for f in fs:
        auto = [w for w, on in (("все контакты", f.contacts), ("не контакты", f.non_contacts), ("группы", f.groups),
                                ("каналы", f.broadcasts), ("боты", f.bots)) if on]
        chats_ = [names.get(utils.get_peer_id(p), str(utils.get_peer_id(p))) for p in f.include_peers]
        out.append(f"📁 {ftitle(f)}{' ' + f.emoticon if f.emoticon else ''} (id {f.id}, чатов {len(chats_)})"
                   + (f"\n  автоматически: {', '.join(auto)}" if auto else "")
                   + (f"\n  {'; '.join(chats_)}" if chats_ else ""))
    return "\n".join(out)


@command("folder", write=True)
async def folder(ctx, a):
    fs = await folder_list(ctx)
    f = next((f for f in fs if ftitle(f).casefold() == a["name"].casefold()), None)
    if a.get("delete"):
        if not f:
            raise Fail(f"папки «{a['name']}» нет")
        await ctx.confirm(f"📁 Удалить папку «{ftitle(f)}» (чаты останутся)")
        await ctx.client(functions.messages.UpdateDialogFilterRequest(f.id))
        return "✅ Папка удалена"
    add = [await chat(ctx, q) for q in a.get("chats") or []]
    rem = [await chat(ctx, q) for q in a.get("remove") or []]
    peers = list(f.include_peers) if f else []
    have = {utils.get_peer_id(p) for p in peers}
    for c in add:
        if utils.get_peer_id(c) not in have:
            peers.append(await ctx.client.get_input_entity(c))
            have.add(utils.get_peer_id(c))
    drop = {utils.get_peer_id(c) for c in rem}
    peers = [p for p in peers if utils.get_peer_id(p) not in drop]
    if not peers and not (f and (f.contacts or f.non_contacts or f.groups or f.broadcasts or f.bots)):
        raise Fail("в папке не останется чатов — удали её через --delete")
    what = (f"📁 Папка «{a['name']}»" + (" (новая)" if not f else "")
            + (":\n➕ " + ", ".join(name(c) for c in add) if add else "")
            + ("\n➖ " + ", ".join(name(c) for c in rem) if rem else ""))
    await ctx.confirm(what)
    if f:
        f.include_peers = peers
        f.pinned_peers = [p for p in f.pinned_peers if utils.get_peer_id(p) not in drop]
        f.exclude_peers = [p for p in f.exclude_peers if utils.get_peer_id(p) not in have]
        if a.get("emoji"):
            f.emoticon = a["emoji"]
    else:
        fid = max([f.id for f in fs] + [1]) + 1
        f = types.DialogFilter(id=fid, title=types.TextWithEntities(a["name"], []), pinned_peers=[],
                               include_peers=peers, exclude_peers=[], emoticon=a.get("emoji"))
    await ctx.client(functions.messages.UpdateDialogFilterRequest(f.id, f))
    return f"✅ Папка «{a['name']}»: {len(peers)} чатов"


async def edit_folder(ctx, a, folder_id, verb):
    cs = [await chat(ctx, q) for q in a["chats"]]
    await ctx.confirm(f"🗂 {verb}: " + ", ".join(name(c) for c in cs))
    await ctx.client.edit_folder(cs, [folder_id] * len(cs))
    _dialogs.pop(id(ctx.client), None)
    return f"✅ {verb}: {len(cs)}"


@command("archive", write=True)
async def archive(ctx, a):
    return await edit_folder(ctx, a, 1, "В архив")


@command("unarchive", write=True)
async def unarchive(ctx, a):
    return await edit_folder(ctx, a, 0, "Из архива")


@command("notify", write=True)
async def notify(ctx, a):
    cs = [await chat(ctx, q) for q in a["chats"]]
    off = a["mode"] == "off"
    until = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=a["hours"]) if a.get("hours")
             else datetime.datetime(2038, 1, 1, tzinfo=datetime.timezone.utc)) if off else None
    verb = (f"Без звука на {a['hours']} ч" if a.get("hours") else "Без звука навсегда") if off else "Включить уведомления"
    await ctx.confirm(f"🔕 {verb}: " + ", ".join(name(c) for c in cs))
    for c in cs:
        await ctx.client(functions.account.UpdateNotifySettingsRequest(
            peer=types.InputNotifyPeer(await ctx.client.get_input_entity(c)),
            settings=types.InputPeerNotifySettings(mute_until=until or datetime.datetime.fromtimestamp(0, datetime.timezone.utc))))
    return f"✅ {verb}: {len(cs)}"


# ---------- Расписание (tgbot/jobs.py) ----------

@command("job", write=True)
async def job(ctx, a):
    cs = [await chat(ctx, q) for q in a["chats"]]
    try:
        j = ctx.jobs.new([{"id": utils.get_peer_id(c), "name": name(c)} for c in cs], a["time"], days=a.get("days"),
                         once=a.get("once"), text=a.get("text"), ai=a.get("ai"), until=a.get("until"))
    except ValueError as e:
        raise Fail(str(e))
    first = datetime.datetime.fromisoformat(j["next"]).astimezone(MSK)
    await ctx.confirm(f"⏰ По расписанию: {jobs.describe(j)}\n\nПервый раз: {first:%d.%m %H:%M}")
    ctx.jobs.add(j)
    return f"✅ Задача {j['id']} создана, первый раз {first:%d.%m %H:%M}"


@command("jobs")
async def jobs_(ctx, a):
    js = ctx.jobs.load()
    if not js:
        return "Расписаний нет"
    return "\n".join(f"{j['id']} | {jobs.describe(j)} | следующий раз "
                     f"{datetime.datetime.fromisoformat(j['next']).astimezone(MSK):%d.%m %H:%M} | отправлено {j.get('sent', 0)}"
                     for j in js)


@command("unjob", write=True)
async def unjob(ctx, a):
    j = next((j for j in ctx.jobs.load() if j["id"] == a["id"]), None)
    if not j:
        raise Fail(f"задачи {a['id']} нет, смотри jobs")
    await ctx.confirm(f"⏰ Удалить расписание: {jobs.describe(j)}")
    ctx.jobs.remove(a["id"])
    return "✅ Удалено"
