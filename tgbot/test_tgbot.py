"""Тесты агента без настоящего Telegram: tgbot/.venv/bin/python tgbot/test_tgbot.py"""
import asyncio
import datetime
import os
import sys
import tempfile
import types as pytypes
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from telethon.tl import types  # noqa: E402

import actions  # noqa: E402
import agent  # noqa: E402
import jobs  # noqa: E402
import tg  # noqa: E402

UTC = datetime.timezone.utc


def user(uid, first, last=None, username=None):
    return types.User(id=uid, first_name=first, last_name=last, username=username, access_hash=1)


def group(cid, title):
    return types.Channel(id=cid, title=title, photo=types.ChatPhotoEmpty(), date=None, megagroup=True, access_hash=1)


def dialog(entity, unread=0):
    from telethon.utils import get_peer_id
    return pytypes.SimpleNamespace(entity=entity, name=actions.name(entity), id=get_peer_id(entity), unread_count=unread,
                                   unread_mentions_count=0, archived=False, pinned=False, is_user=isinstance(entity, types.User),
                                   date=datetime.datetime(2026, 10, 9, 12, tzinfo=UTC))


def msg(mid, text, sender_id=7, out=False, reply=None):
    return types.Message(id=mid, peer_id=types.PeerUser(7), date=datetime.datetime(2026, 10, 9, 11, 30, tzinfo=UTC),
                         message=text, out=out, from_id=types.PeerUser(sender_id),
                         reply_to=types.MessageReplyHeader(reply_to_msg_id=reply) if reply else None)


PETYA, SASHA, SASHA2 = user(7, "Петя", username="petya"), user(8, "Саша", "Иванов"), user(9, "Саша", "Петров")
CLASS = group(100, "8Б класс")
BOX = group(200, "Бокс")


class FakeClient:
    def __init__(self):
        self.sent = []
        self.calls = []
        self.dialogs = [dialog(PETYA, 2), dialog(SASHA), dialog(SASHA2), dialog(CLASS, 5), dialog(BOX)]
        self.history = [msg(3, "привет", reply=1), msg(2, "как дела"), msg(1, "ок", out=True)]

    async def get_dialogs(self, limit=None):
        return self.dialogs

    async def get_entity(self, x):
        for d in self.dialogs:
            if x in (d.id, getattr(d.entity, "id", None)) or (isinstance(x, str) and x.lstrip("@") == getattr(d.entity, "username", None)):
                return d.entity
        raise ValueError("nope")

    async def get_input_entity(self, x):
        return types.InputPeerChannel(x.id, 1) if isinstance(x, types.Channel) else types.InputPeerUser(x.id, 1)

    async def iter_messages(self, entity, limit=None, **kw):
        for m in self.history[:limit]:
            yield m

    async def iter_participants(self, entity, limit=None, search="", **kw):
        for u in (PETYA, SASHA, SASHA2):
            if search.casefold() in actions.name(u).casefold():
                yield u

    async def send_message(self, entity, text, **kw):
        self.sent.append((entity, text, kw))
        return pytypes.SimpleNamespace(id=55)

    async def kick_participant(self, c, u):
        self.calls.append(("kick", c.id, u.id))

    async def __call__(self, req):
        self.calls.append(req)
        if type(req).__name__ == "GetDialogFiltersRequest":
            return pytypes.SimpleNamespace(filters=[types.DialogFilterDefault()])


class Ctx:
    def __init__(self, answer=True):
        self.client = FakeClient()
        self.asked = []
        self.answer = answer
        self.jobs = jobs.Store(os.path.join(tempfile.mkdtemp(), "jobs.json"))

    async def confirm(self, text):
        self.asked.append(text)
        if not self.answer:
            raise actions.Cancelled


def run(coro):
    return asyncio.run(coro)


class Parser(unittest.TestCase):
    def test_send(self):
        a = vars(tg.parser().parse_args(["send", "Петя", "буду в 6", "--reply", "5"]))
        self.assertEqual((a["cmd"], a["chat"], a["text"], a["reply"]), ("send", "Петя", "буду в 6", 5))

    def test_every_cli_command_exists_in_actions(self):
        sub = next(x for x in tg.parser()._actions if x.dest == "cmd")
        self.assertEqual(set(sub.choices), set(actions.COMMANDS))

    def test_write_commands_marked(self):
        sub = next(x for x in tg.parser()._actions if x.dest == "cmd")
        for name, (fn, write) in actions.COMMANDS.items():
            self.assertEqual(write, sub.choices[name].description.startswith("⚠"), name)


class Resolve(unittest.TestCase):
    def setUp(self):
        actions._dialogs.clear()

    def test_by_name_username_id(self):
        ctx = Ctx()
        self.assertIs(run(actions.chat(ctx, "8б")), CLASS)
        self.assertIs(run(actions.chat(ctx, "@petya")), PETYA)
        self.assertIs(run(actions.chat(ctx, "-1000000000200")), BOX)

    def test_ambiguous(self):
        with self.assertRaises(actions.Fail) as e:
            run(actions.chat(Ctx(), "саша"))
        self.assertIn("Саша Иванов", str(e.exception))
        self.assertIs(run(actions.chat(Ctx(), "саша петров")), SASHA2)

    def test_missing(self):
        with self.assertRaises(actions.Fail):
            run(actions.chat(Ctx(), "нет такого"))


class Actions(unittest.TestCase):
    def setUp(self):
        actions._dialogs.clear()

    def test_send_asks_first(self):
        ctx = Ctx()
        out = run(actions.run(ctx, "send", {"chat": "Петя", "text": "буду в 6"}))
        self.assertIn("Петя", ctx.asked[0])
        self.assertIn("буду в 6", ctx.asked[0])
        self.assertEqual(ctx.client.sent[0][1], "буду в 6")
        self.assertIn("✅", out)

    def test_refused_nothing_sent(self):
        ctx = Ctx(answer=False)
        out = run(actions.run(ctx, "send", {"chat": "Петя", "text": "x"}))
        self.assertEqual(ctx.client.sent, [])
        self.assertIn("не подтвердил", out)

    def test_kick_finds_member_by_name(self):
        ctx = Ctx()
        run(actions.run(ctx, "kick", {"chat": "8Б", "users": ["Петя"]}))
        self.assertEqual(ctx.client.calls, [("kick", 200 - 100, 7)])
        self.assertEqual(len(ctx.asked), 1)

    def test_history(self):
        out = run(actions.run(Ctx(), "history", {"chat": "Петя", "limit": 10}))
        lines = out.splitlines()
        self.assertTrue(lines[1].startswith("[1] 09.10.26 14:30 Я: ок"))  # от старых к новым, время московское
        self.assertIn("(ответ на 1)", lines[3])

    def test_read_only_commands_never_ask(self):
        ctx = Ctx()
        run(actions.run(ctx, "chats", {"unread": True}))
        run(actions.run(ctx, "history", {"chat": "Петя"}))
        self.assertEqual(ctx.asked, [])

    def test_new_folder(self):
        ctx = Ctx()
        out = run(actions.run(ctx, "folder", {"name": "Школа", "chats": ["8Б"], "emoji": "📚"}))
        req = ctx.client.calls[-1]
        self.assertEqual(type(req).__name__, "UpdateDialogFilterRequest")
        self.assertEqual(req.filter.title.text, "Школа")
        self.assertEqual(req.filter.id, 2)
        self.assertEqual(len(req.filter.include_peers), 1)
        self.assertIn("1 чатов", out)

    def test_dates(self):
        d = actions.date("2026-10-10 09:00")
        self.assertEqual(d.astimezone(UTC).hour, 6)
        with self.assertRaises(actions.Fail):
            actions.date("завтра")


class Socket(unittest.TestCase):
    """tg.py -> сокет -> агент -> действие -> ответ; и кнопки «Да/Нет»."""

    def test_roundtrip_with_confirmation(self):
        tmp = tempfile.mkdtemp()
        sock = os.path.join(tmp, "a.sock")

        class Bot:
            def __init__(self):
                self.msgs = []

            async def send_message(self, chat, text, **kw):
                self.msgs.append((text, kw))
                return pytypes.SimpleNamespace(edit=mock.AsyncMock())

        a = agent.Agent.__new__(agent.Agent)
        a.allowed, a.trust, a.waiting, a.bot = 1, False, {}, Bot()
        fake = FakeClient()
        a.client = pytypes.SimpleNamespace(is_connected=lambda: True, is_user_authorized=mock.AsyncMock(return_value=True))
        a.client.__dict__.update({k: getattr(fake, k) for k in ("get_dialogs", "get_entity", "send_message")})
        actions._dialogs.clear()

        async def scenario():
            with mock.patch.object(agent, "SOCK", sock):
                server = await a.serve()
            with mock.patch.object(tg, "SOCK", sock):
                call = asyncio.to_thread(tg.call, "send", {"chat": "Петя", "text": "привет"})
                task = asyncio.ensure_future(call)
                while not a.waiting:
                    await asyncio.sleep(0.01)
                self.assertIn("Петя", a.bot.msgs[0][0])
                cid = next(iter(a.waiting))
                a.waiting[cid].set_result("all")
                ok, out = await task
                self.assertTrue(ok, out)
                self.assertTrue(a.trust)  # «Да на всё» — дальше в этом запросе без вопросов
                ok, out = await asyncio.to_thread(tg.call, "send", {"chat": "Петя", "text": "ещё"})
                self.assertTrue(ok, out)
                self.assertEqual(len(a.bot.msgs), 1)
                ok, out = await asyncio.to_thread(tg.call, "nope", {})
                self.assertFalse(ok)
            server.close()
            return fake.sent

        sent = run(scenario())
        self.assertEqual([t for _, t, _ in sent], ["привет", "ещё"])

    def test_no_agent(self):
        with mock.patch.object(tg, "SOCK", "/nonexistent.sock"):
            self.assertFalse(tg.call("me", {})[0])

    def test_big_output_goes_to_file(self):
        out = tg.show("x\n" * 20000)
        path = out.split(" в ", 1)[1].split(" —")[0]
        self.assertTrue(os.path.exists(path))
        os.remove(path)


MSK = jobs.MSK


class Schedule(unittest.TestCase):
    def test_days(self):
        self.assertEqual(jobs.parse_days("будни"), [0, 1, 2, 3, 4])
        self.assertEqual(jobs.parse_days("пн, ср,пт"), [0, 2, 4])
        self.assertEqual(jobs.parse_days("пт-пн"), [0, 4, 5, 6])
        self.assertEqual(jobs.parse_days("Пятница"), [4])
        with self.assertRaises(jobs.Bad):
            jobs.parse_days("иногда")

    def test_next_run(self):
        fri = datetime.datetime(2026, 10, 9, 19, 0, tzinfo=MSK)  # пятница, 19:00
        j = {"time": "07:00", "days": [0, 1, 2, 3, 4]}
        self.assertEqual(jobs.next_run(j, fri), datetime.datetime(2026, 10, 12, 7, 0, tzinfo=MSK))  # понедельник
        j = {"time": "21:00", "days": [4]}
        self.assertEqual(jobs.next_run(j, fri), datetime.datetime(2026, 10, 9, 21, 0, tzinfo=MSK))  # сегодня же
        self.assertIsNone(jobs.next_run({"time": "07:00", "once": "2026-10-09"}, fri))
        self.assertIsNone(jobs.next_run({"time": "07:00", "days": [0], "until": "2026-10-11"}, fri))

    def test_due_and_missed(self):
        st = jobs.Store(os.path.join(tempfile.mkdtemp(), "j.json"))
        start = datetime.datetime(2026, 10, 9, 6, 0, tzinfo=MSK)
        daily = st.new([{"id": 7, "name": "Мама"}], "07:00", text="доброе утро", now=start)
        once = st.new([{"id": 7, "name": "Мама"}], "08:00", once="2026-10-09", ai="поздравить", now=start)
        st.add(daily)
        st.add(once)
        run, missed = st.due(datetime.datetime(2026, 10, 9, 7, 0, 20, tzinfo=MSK))
        self.assertEqual([j["id"] for j in run], [daily["id"]])
        self.assertEqual(st.due(datetime.datetime(2026, 10, 9, 7, 1, tzinfo=MSK)), ([], []))  # второй раз не шлёт
        run, missed = st.due(datetime.datetime(2026, 10, 9, 12, 0, tzinfo=MSK))  # сервер лежал с 8 до 12
        self.assertEqual(([j["id"] for j in missed], run), ([once["id"]], []))
        left = st.load()
        self.assertEqual([j["id"] for j in left], [daily["id"]])  # разовая ушла, ежедневная — на завтра
        self.assertTrue(left[0]["next"].startswith("2026-10-10T07:00"))

    def test_job_needs_text_or_ai(self):
        st = jobs.Store(os.path.join(tempfile.mkdtemp(), "j.json"))
        with self.assertRaises(jobs.Bad):
            st.new([], "07:00")
        with self.assertRaises(jobs.Bad):
            st.new([], "25:00", text="x")

    def test_job_command(self):
        actions._dialogs.clear()
        ctx = Ctx()
        out = run(actions.run(ctx, "job", {"chats": ["Петя"], "time": "7:00", "days": "будни", "text": "го в школу"}))
        self.assertIn("✅", out)
        self.assertIn("по будням в 07:00 → Петя: «го в школу»", ctx.asked[0])
        self.assertEqual(len(ctx.jobs.load()), 1)
        self.assertIn("го в школу", run(actions.run(ctx, "jobs", {})))
        jid = ctx.jobs.load()[0]["id"]
        run(actions.run(ctx, "unjob", {"id": jid}))
        self.assertEqual(ctx.jobs.load(), [])

    def test_fire_sends_and_reports(self):
        a = agent.Agent.__new__(agent.Agent)
        a.jobs = jobs.Store(os.path.join(tempfile.mkdtemp(), "j.json"))
        fake = FakeClient()
        a.client = fake
        said = []
        a.say = mock.AsyncMock(side_effect=lambda t, **k: said.append(t))
        a.compose = mock.AsyncMock(return_value="сочинил")
        run(a.fire({"id": "x", "chats": [{"id": 7, "name": "Петя"}, {"id": 999, "name": "Нет"}], "ai": "что-то"}))
        self.assertEqual([t for _, t, _ in fake.sent], ["сочинил"])
        self.assertIn("📨 Петя: сочинил", said[0])
        self.assertIn("❌ Нет", said[0])


class Misc(unittest.TestCase):
    def test_split(self):
        parts = agent.split("а\n" * 5000)
        self.assertTrue(all(len(p) <= 3800 for p in parts))
        self.assertEqual("".join(p + "\n" for p in parts).count("а"), 5000)

    def test_prompt_placeholders(self):
        with open(os.path.join(agent.HERE, "prompt.md")) as f:
            p = f.read()
        self.assertIn("{TG}", p)
        self.assertIn("{NOW}", p)


if __name__ == "__main__":
    unittest.main()
