#!/usr/bin/env python3
"""Тесты бота «Авито» без сети: Telegram, Claude, rembg и Авито подменены. python3 avito/test_avito.py"""
import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["AVITO_TOKEN"] = "1:test"
os.environ["AVITO_DATA"] = tempfile.mkdtemp()
import ai  # noqa: E402
import avito  # noqa: E402
import market  # noqa: E402

PAGE = """
<html><div data-marker="catalog-serp">
<div class="iva-item" data-marker="item" data-item-id="111">
  <a href="/moskva/telefony/iphone_13_128gb_111" data-marker="item-title" title="iPhone 13, 128 ГБ">x</a>
  <meta itemprop="price" content="35000">
</div>
<div class="iva-item" data-marker="item" data-item-id="222">
  <a data-marker="item-title" href="/moskva/telefony/iphone_13_222" title="iPhone 13 синий &amp; чехол">x</a>
  <meta itemprop="price" content="37 000">
</div>
<div class="iva-item" data-marker="item" data-item-id="333">
  <a data-marker="item-title" href="/moskva/telefony/chehol_333" title="Чехол для iPhone 13">x</a>
  <meta itemprop="price" content="500">
</div>
<div class="iva-item" data-marker="item" data-item-id="444"><p>без цены</p></div>
</div></html>
"""


class Price(unittest.TestCase):
    def test_parse(self):
        p = avito.parse_price
        self.assertEqual(p("3500"), 3500)
        self.assertEqual(p("3 500 ₽"), 3500)
        self.assertEqual(p("цена 3.5к"), 3500)
        self.assertEqual(p("12 тыс"), 12000)
        self.assertEqual(p("1 299,99 руб"), 1299)
        self.assertIsNone(p("42"))  # размер, а не цена
        self.assertEqual(p("42 ₽"), 42)
        self.assertIsNone(p("добавь, что есть чек"))
        self.assertIsNone(p("iPhone 13 128"))


class Market(unittest.TestCase):
    def test_parse(self):
        items = market.parse(PAGE)
        self.assertEqual([i["price"] for i in items], [35000, 37000, 500])
        self.assertEqual(items[1]["title"], "iPhone 13 синий & чехол")
        self.assertEqual(items[0]["url"], "https://www.avito.ru/moskva/telefony/iphone_13_128gb_111")

    def test_stats_and_outliers(self):
        items = [{"price": p} for p in [30000, 32000, 33000, 34000, 35000, 36000, 38000, 500, 990000]]
        s = market.stats(items)
        self.assertEqual(s["n"], 7)
        self.assertEqual(s["median"], 34000)
        self.assertLess(s["fast"], s["median"])
        self.assertGreater(s["high"], s["median"])

    def test_nice(self):
        self.assertEqual(market.nice(34870), 34900)
        self.assertEqual(market.nice(1240), 1250)
        self.assertEqual(market.nice(473), 470)

    def test_relevant(self):
        items = market.parse(PAGE)
        self.assertEqual(len(market.relevant("iphone 13", items)), 3)
        self.assertEqual(len(market.relevant("iphone 13 128", items)), 3)

    def test_collect_pages_and_block(self):
        calls = []

        def fake(url, mode):
            calls.append(url)
            return PAGE if len(calls) == 1 else ""
        with mock.patch.object(market, "fetch", fake):
            self.assertEqual(len(market.collect("iphone 13")), 3)
        self.assertIn("q=iphone+13", calls[0])
        with mock.patch.object(market, "fetch", mock.Mock(side_effect=market.Blocked("429"))):
            with self.assertRaises(market.Blocked):
                market.collect("x")

    def test_url(self):
        self.assertEqual(market.search_url("кроссовки nike", "moskva", 2),
                         "https://www.avito.ru/moskva?q=%D0%BA%D1%80%D0%BE%D1%81%D1%81%D0%BE%D0%B2%D0%BA%D0%B8+nike&p=2")


class AI(unittest.TestCase):
    def test_json_and_title_limit(self):
        long = "Кроссовки Nike Air Max 90 оригинал белые мужские размер 42 почти новые"
        with mock.patch.object(ai, "claude", lambda *a, **k: '```json\n{"title": "%s", "description": "Д", '
                                                              '"query": "nike air max 90"}\n```' % long):
            ad = ai.write_ad("инфо")
        self.assertLessEqual(len(ad["title"]), 50)
        self.assertTrue(long.startswith(ad["title"]))
        self.assertEqual(ad["questions"], [])

    def test_pick_similar(self):
        items = market.parse(PAGE)
        with mock.patch.object(ai, "claude", lambda *a, **k: '{"ids": [0, 1, 7]}'):
            self.assertEqual([i["price"] for i in ai.pick_similar("iPhone 13", items)], [35000, 37000])


class Flow(unittest.TestCase):
    """Сценарий целиком: фото → фон → описание → цена текстом → средняя цена → кнопка цены."""

    def setUp(self):
        avito.st.clear()
        avito.st["owner"] = 7
        avito.busy.clear()
        self.sent = []
        self.albums = []

        def tg(method, **p):
            self.sent.append((method, p))
            if method == "getFile":
                return {"ok": True, "result": {"file_path": "photos/x.jpg"}}
            return {"ok": True, "result": {"message_id": len(self.sent)}}

        def remove_bg(pairs):
            for s, t in pairs:
                open(t, "wb").write(b"jpg")
            return [{"src": s, "dst": t, "ok": True} for s, t in pairs]

        def run(chat, fn, *args, later=None):  # без потоков — тест последовательный
            fn(*args)

        self.patches = [
            mock.patch.object(avito, "tg", tg),
            mock.patch.object(avito, "send_album", lambda chat, paths, cap="": self.albums.append(paths)),
            mock.patch.object(avito, "download", lambda fid, dst: open(dst, "wb").write(b"orig")),
            mock.patch.object(avito, "remove_bg", remove_bg),
            mock.patch.object(avito, "run", run),
            mock.patch.object(avito, "schedule_photos", lambda chat, delay: avito.process_photos(chat)),
            mock.patch.object(ai, "write_ad", lambda info, imgs=(), prev=None, req="": {
                "title": "iPhone 13 128 ГБ", "description": "Отличный телефон.\n" + info,
                "query": "iphone 13 128", "category": "Телефоны", "questions": ["Есть ли коробка?"]}),
            mock.patch.object(ai, "pick_similar", lambda item, items: items[:2]),
            mock.patch.object(market, "collect", lambda q, region="all": market.parse(PAGE)),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def texts(self):
        return [p.get("text", "") for m, p in self.sent if m in ("sendMessage", "editMessageText")]

    def msg(self, **kw):
        return {"chat": {"id": 7, "type": "private"}, "from": {"id": 7, "first_name": "Глеб"}, **kw}

    def test_flow(self):
        avito.on_message(self.msg(photo=[{"file_id": "a"}], media_group_id="g"))
        self.assertEqual(len(self.albums), 1)
        self.assertTrue(any("Расскажи о вещи" in t for t in self.texts()))
        d = avito.draft()
        self.assertTrue(d["photos"][0]["clean"].endswith(".jpg"))

        avito.on_message(self.msg(text="айфон 13, 128, батарея 89%"))
        self.assertEqual(d["title"], "iPhone 13 128 ГБ")
        self.assertIn("батарея 89%", d["description"])
        self.assertTrue(any("Есть ли коробка?" in t for t in self.texts()))

        avito.on_message(self.msg(text="35к"))
        self.assertEqual(d["price"], 35000)

        avito.on_callback({"id": "1", "from": {"id": 7}, "data": "mk",
                           "message": {"chat": {"id": 7}, "message_id": 5}})
        m = d["market"]
        self.assertEqual(m["seen"], 3)
        self.assertEqual(m["stats"]["median"], 36000)
        self.assertTrue(any("Похожих объявлений: <b>2</b> из 3" in t for t in self.texts()))

        avito.on_callback({"id": "2", "from": {"id": 7}, "data": "pr|33000",
                           "message": {"chat": {"id": 7}, "message_id": 5}})
        self.assertEqual(d["price"], 33000)

        avito.on_message(self.msg(text="/new"))
        self.assertIsNone(avito.draft())

    def test_caption_writes_immediately_and_stranger_blocked(self):
        avito.on_message(self.msg(photo=[{"file_id": "a"}], caption="кроссовки nike 42"))
        self.assertEqual(avito.draft()["title"], "iPhone 13 128 ГБ")
        self.sent.clear()
        avito.on_message({"chat": {"id": 9, "type": "private"}, "from": {"id": 9}, "text": "привет"})
        self.assertIn("личный бот", self.texts()[0])

    def test_voice(self):
        with mock.patch.object(avito.media, "voice_text", lambda tok, msg: "кроссовки найк сорок второй"):
            avito.on_message(self.msg(voice={"file_id": "v"}))
        self.assertTrue(any("🎙" in t and "кроссовки найк" in t for t in self.texts()))
        self.assertIn("кроссовки найк", avito.draft()["info"])
        self.assertEqual(avito.draft()["title"], "iPhone 13 128 ГБ")
        with mock.patch.object(avito.media, "voice_text", mock.Mock(side_effect=avito.media.MediaError("тихо"))):
            avito.on_message(self.msg(voice={"file_id": "v"}))
        self.assertIn("🎙 тихо", self.texts()[-1])

    def test_text_only(self):
        avito.on_message(self.msg(text="Продаю велосипед Stels, 26 дюймов"))
        self.assertEqual(avito.draft()["title"], "iPhone 13 128 ГБ")


class Photos(unittest.TestCase):
    def test_place_on_white(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("нет Pillow")
        import photos
        cut = Image.new("RGBA", (400, 200), (0, 0, 0, 0))
        cut.paste((200, 0, 0, 255), (100, 50, 300, 150))
        res = photos.place_on_white(cut, size=100, fill=0.5)
        self.assertEqual(res.size, (100, 100))
        self.assertEqual(res.getpixel((0, 0)), (255, 255, 255))
        self.assertEqual(res.getpixel((50, 50)), (200, 0, 0))
        self.assertIsNone(photos.place_on_white(Image.new("RGBA", (10, 10), (0, 0, 0, 0))))


if __name__ == "__main__":
    unittest.main(verbosity=1)
