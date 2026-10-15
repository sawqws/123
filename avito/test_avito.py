#!/usr/bin/env python3
"""Тесты бота «Авито» без сети: Telegram, Claude, rembg и Авито подменены. python3 avito/test_avito.py"""
import json
import os
import sys
import tempfile
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


def write_file(path, data):
    with open(path, "wb") as f:
        f.write(data)


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

    def test_reply_buyer(self):
        seen = {}

        def fake(prompt, images=(), system="", timeout=0):
            seen.update(prompt=prompt, images=images, system=system)
            return '```json\n{"reply": "Да, актуально! Когда удобно забрать?", "note": ""}\n```'
        with mock.patch.object(ai, "claude", fake):
            r = ai.reply_buyer({"title": "Велосипед", "price": 9000, "description": "Stels 26"}, "Актуально?",
                               "/tmp/x.jpg", [{"who": "buyer", "text": "Здравствуйте"}, {"who": "me", "text": "Добрый день"}],
                               floor=8000, notes="самовывоз м. Сокол")
        self.assertEqual(r["reply"], "Да, актуально! Когда удобно забрать?")
        self.assertIn("8000 ₽", seen["system"])
        self.assertIn("Покупатель: Здравствуйте", seen["prompt"])
        self.assertIn("самовывоз м. Сокол", seen["prompt"])
        self.assertEqual(seen["images"], ["/tmp/x.jpg"])
        with mock.patch.object(ai, "claude", lambda *a, **k: '{"reply": ""}'):
            with self.assertRaises(ai.AIError):
                ai.reply_buyer({}, "привет")

    def test_pick_similar(self):
        items = market.parse(PAGE)
        with mock.patch.object(ai, "claude", lambda *a, **k: '{"ids": [0, 1, 7]}'):
            self.assertEqual([i["price"] for i in ai.pick_similar("iPhone 13", items)], [35000, 37000])


class Flow(unittest.TestCase):
    """Сценарий целиком: фото → фон → описание → цена текстом → средняя цена → кнопка цены."""

    def setUp(self):
        avito.st.clear()
        avito.st.update(owner=7, mode="chat")
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
                write_file(t, b"jpg")
            return [{"src": s, "dst": t, "ok": True} for s, t in pairs]

        def run(chat, fn, *args, later=None):  # без потоков — тест последовательный
            fn(*args)

        self.patches = [
            mock.patch.object(avito, "tg", tg),
            mock.patch.object(avito, "send_album", lambda chat, paths, cap="": self.albums.append(paths)),
            mock.patch.object(avito, "download", lambda fid, dst: write_file(dst, b"orig")),
            mock.patch.object(avito, "remove_bg", remove_bg),
            mock.patch.object(avito, "run", run),
            mock.patch.object(avito, "schedule_photos", lambda chat, delay: avito.process_photos(chat)),
            mock.patch.object(avito, "spawn", lambda fn: fn()),
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
        self.assertTrue(any("Что это за вещь?" in t for t in self.texts()))
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

        edits = [p for m, p in self.sent if m == "editMessageText" and p.get("message_id") == 5]
        self.assertIn("Медиана: " + market.fmt_rub(36000), edits[-1]["text"])  # результат — на месте карточки

        avito.on_callback({"id": "2", "from": {"id": 7}, "data": "pr|33000",
                           "message": {"chat": {"id": 7}, "message_id": 5}})
        self.assertEqual(d["price"], 33000)
        last = [p for m, p in self.sent if m == "editMessageText"][-1]
        self.assertEqual(last["message_id"], 5)  # карточка с новой ценой — в том же сообщении
        self.assertIn(market.fmt_rub(33000), last["text"])

        avito.on_message(self.msg(text="/new"))
        self.assertIsNone(avito.draft())
        self.assertEqual(avito.st["ads"][0]["title"], "iPhone 13 128 ГБ")  # прошлое — в приложении

    def test_card_layout(self):
        d = {"id": "x", "title": "Кроссовки <Nike>", "description": "Почти новые.\n" * 12, "price": 3500,
             "category": "Обувь", "photos": [{"clean": "a"}, {"clean": None}], "questions": ["Размер?"]}
        text = avito.card(d)
        self.assertTrue(text.startswith("<b>📝 Кроссовки &lt;Nike&gt;</b>"))
        self.assertIn("<blockquote expandable>Почти новые.", text)  # длинное описание сворачивается
        self.assertIn(f"<b>💰 {market.fmt_rub(3500)}</b>", text)
        self.assertIn("На белом фоне: 1 из 2", text)
        self.assertIn("<blockquote>• Размер?</blockquote>", text)
        for line in text.splitlines():  # не больше одного эмодзи на строку
            self.assertLessEqual(sum(1 for ch in line if ord(ch) > 0x1F000), 1, line)
        rows = avito.card_buttons(d)
        self.assertLessEqual(max(len(r) for r in rows), 3)
        self.assertEqual({x["callback_data"] for r in rows for x in r}, {"pub", "mk", "cp", "re", "new"})
        self.assertTrue(all(len(x["text"]) <= 24 for r in rows for x in r))
        live = dict(d, status="live", url="https://www.avito.ru/moskva/obuv/krossovki_123", floor=3000)
        self.assertIn("🟢 На Авито", avito.card(live))
        self.assertIn("Торг до", avito.card(live))
        rows = avito.card_buttons(live)
        self.assertEqual({x["callback_data"] for r in rows for x in r}, {"buy", "sold", "floor", "mk", "cp", "re", "new"})
        self.assertTrue(all(len(x["text"]) <= 24 for r in rows for x in r))
        self.assertLessEqual(max(len(r) for r in rows), 3)
        self.assertIn("Продано за", avito.card(dict(d, status="sold", sold_price=3200)))

    def cb(self, data):
        avito.on_callback({"id": "1", "from": {"id": 7}, "data": data, "message": {"chat": {"id": 7}, "message_id": 9}})

    def make_ad(self):
        avito.on_message(self.msg(photo=[{"file_id": "a"}]))
        avito.on_message(self.msg(text="айфон 13"))
        avito.on_message(self.msg(text="35000"))
        return avito.draft()

    def test_publish_live_sold(self):
        d = self.make_ad()
        self.cb("pub")
        self.assertEqual(len(self.albums), 2)  # после фона и в наборе для Авито
        kit = [p for m, p in self.sent if m == "sendMessage" and "Выложить на Авито" in p.get("text", "")][-1]
        self.assertIn("<code>iPhone 13 128 ГБ</code>", kit["text"])
        self.assertIn("<code>35000</code>", kit["text"])
        self.assertEqual(kit["reply_markup"]["inline_keyboard"][0][0]["url"], avito.ADD_URL)
        self.assertTrue(self.texts()[-1].startswith("Отличный телефон."))  # описание — отдельным сообщением
        self.cb("live")
        self.assertEqual(d["status"], "live")
        avito.on_message(self.msg(text="вот https://www.avito.ru/moskva/telefony/iphone_13_4012345678."))
        self.assertEqual(d["url"], "https://www.avito.ru/moskva/telefony/iphone_13_4012345678")
        self.assertIn("iPhone 13 128 ГБ", d["title"])  # ссылка — не правка описания
        avito.on_message(self.msg(text="продал за 33к"))
        self.assertEqual((d["status"], d["sold_price"]), ("sold", 33000))
        text, rows = avito.ads_view()
        self.assertIn("✅ iPhone 13 128 ГБ", text)
        self.assertIn("продано на", text)

    def test_ads_switch(self):
        first = self.make_ad()
        avito.on_message(self.msg(text="/new"))
        avito.on_message(self.msg(text="велосипед stels"))
        text, rows = avito.ads_view()
        self.assertIn("сейчас", text)
        self.cb(rows[0][0]["callback_data"])
        self.assertEqual(avito.draft()["id"], first["id"])
        self.assertEqual(len(avito.st["ads"]), 1)

    def test_buyer_and_floor(self):
        d = self.make_ad()
        self.cb("floor")
        avito.on_message(self.msg(text="30000"))
        self.assertEqual(d["floor"], 30000)
        self.assertEqual(d["price"], 35000)  # минимальная цена — не цена объявления
        calls = []

        def fake(ad, buyer, image=None, history=(), floor=None, notes=""):
            calls.append((buyer, image, list(history), floor))
            return {"reply": f"Здравствуйте! Могу отдать за 33 000. ({len(calls)})", "note": "торг"}
        with mock.patch.object(ai, "reply_buyer", fake):
            self.cb("buy")
            avito.on_message(self.msg(text="Отдадите за 25?"))
            self.assertEqual(calls[-1][0], "Отдадите за 25?")
            self.assertEqual(calls[-1][3], 30000)
            self.assertEqual(d["price"], 35000)  # «25» от покупателя — не новая цена
            out = [p for m, p in self.sent if m == "sendMessage"][-1]
            self.assertIn("Могу отдать за 33 000", out["text"])
            self.assertEqual(out["reply_markup"]["inline_keyboard"][0][0]["copy_text"]["text"],
                             "Здравствуйте! Могу отдать за 33 000. (1)")
            self.cb("buyr")
            self.assertIn("по-другому", calls[-1][0])
            self.assertEqual(len(calls[-1][2]), 0)  # прошлый вариант ответа в историю не попал
            self.assertEqual(len(d["chat"]), 2)
            # скриншот чата
            self.cb("buy")
            with mock.patch.object(avito.media, "photo_path", lambda tok, msg, folder: "/tmp/shot.jpg"):
                avito.on_message(self.msg(photo=[{"file_id": "s"}]))
            self.assertEqual(calls[-1][1], "/tmp/shot.jpg")
            self.assertEqual(len(d["photos"]), 1)  # скрин не попал в фото объявления
            self.assertEqual(len(calls[-1][2]), 2)  # и знает прошлую переписку
            # голосовое
            self.cb("buy")
            with mock.patch.object(avito.media, "voice_text", lambda tok, msg: "а торг уместен"):
                avito.on_message(self.msg(voice={"file_id": "v"}))
            self.assertEqual(calls[-1][0], "а торг уместен")
            # без режима «покупатель» текст — снова правка объявления
            avito.on_message(self.msg(text="добавь про чехол"))
            self.assertEqual(len(calls), 4)

    def test_market_web_fallback(self):
        d = self.make_ad()
        blocked = mock.Mock(side_effect=market.Blocked("429"))
        web = {"prices": [30000, 32000, 34000, 35000, 36000, 38000, 990], "sources": ["https://x.ru"], "note": "Юла, октябрь"}
        with mock.patch.object(market, "collect", blocked), mock.patch.object(ai, "web_prices", return_value=web):
            self.cb("mk")
        m = d["market"]
        self.assertEqual(m["source"], "web")
        self.assertEqual(m["stats"]["n"], 6)  # 990 — выброс
        text = [p for k, p in self.sent if k == "editMessageText"][-1]["text"]
        self.assertIn("Примерно — по поиску в интернете", text)
        self.assertIn("Цен найдено: <b>6</b>", text)
        new = {"prices": [], "new": [10000, 12000, 11000], "sources": [], "note": "магазины"}
        with mock.patch.object(market, "collect", blocked), mock.patch.object(ai, "web_prices", return_value=new):
            self.cb("mk|f")
        self.assertEqual((d["market"]["source"], d["market"]["stats"]["median"]), ("new", 6000))  # медиана 6050 → красиво 6000
        self.assertIn("Предположение", [p for k, p in self.sent if k == "editMessageText"][-1]["text"])
        with mock.patch.object(market, "collect", blocked), \
                mock.patch.object(ai, "web_prices", return_value={"prices": [], "new": [], "sources": [], "note": ""}):
            self.cb("mk|f")
        self.assertIn("Цены не нашёл", [p for k, p in self.sent if k == "editMessageText"][-1]["text"])

    def test_wait_expires(self):
        self.make_ad()
        self.cb("buy")
        avito.st["wait"]["ts"] -= avito.WAIT_TTL + 1
        with mock.patch.object(ai, "reply_buyer", mock.Mock(side_effect=AssertionError)):
            avito.on_message(self.msg(text="32000"))
        self.assertEqual(avito.draft()["price"], 32000)

    def test_app_api(self):
        avito.on_message(self.msg(photo=[{"file_id": "a"}], caption="айфон 13"))
        aid = avito.draft()["id"]
        s = avito.app_state()
        self.assertEqual(s["ads"][0]["title"], "iPhone 13 128 ГБ")
        self.assertTrue(s["ads"][0]["current"])
        self.assertEqual(s["ads"][0]["photos"], [{"url": f"/api/photo/{aid}/0?c", "state": "clean"}])
        code, res = avito.api_post("/api/price", {"id": aid, "price": "4500"})
        self.assertEqual((code, res["ad"]["price_text"]), (200, market.fmt_rub(4500)))
        self.assertEqual(avito.api_post("/api/price", {"id": aid, "price": "abc"})[0], 400)
        code, res = avito.api_post("/api/market", {"id": aid})
        self.assertEqual((code, res["market"]["median"], res["market"]["n"]), (200, 36000, 2))
        self.assertEqual(avito.draft()["market"]["query"], "iphone 13 128")
        code, res = avito.api_post("/api/market", {"query": "велосипед"})  # просто запрос, без объявления
        self.assertEqual(code, 200)
        self.assertIsNone(res["ad"])
        with mock.patch.object(market, "collect", mock.Mock(side_effect=market.Blocked("429"))):
            code, res = avito.api_post("/api/market", {"query": "x", "force": True})
        self.assertEqual(code, 503)
        self.assertIn("avito.ru", res["url"])
        self.assertEqual(avito.api_post("/api/market", {"id": "нет"})[0], 404)

    def test_http(self):
        import threading
        import urllib.error
        import urllib.request
        from http.server import ThreadingHTTPServer
        avito.on_message(self.msg(photo=[{"file_id": "a"}], caption="айфон 13"))
        aid = avito.draft()["id"]
        srv = ThreadingHTTPServer(("127.0.0.1", 0), avito.Web)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            with urllib.request.urlopen(base + "/") as r:
                self.assertIn("kit.css?v=", r.read().decode())
            with self.assertRaises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(base + "/api/state")
            self.assertEqual(e.exception.code, 401)
            h = {"X-Init-Data": init_data("1:test", uid=7)}
            with urllib.request.urlopen(urllib.request.Request(base + "/api/state", headers=h)) as r:
                self.assertEqual(json.load(r)["ads"][0]["id"], aid)
            with urllib.request.urlopen(urllib.request.Request(base + f"/api/photo/{aid}/0", headers=h)) as r:
                self.assertEqual(r.read(), b"jpg")  # фото на белом фоне
            with self.assertRaises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(urllib.request.Request(base + f"/api/photo/{aid}/9", headers=h))
            self.assertEqual(e.exception.code, 404)
            req = urllib.request.Request(base + "/api/upload", data=b"\xff\xd8" + b"y" * 300, method="POST",
                                         headers=dict(h, **{"Content-Type": "image/jpeg"}))
            with urllib.request.urlopen(req) as r:
                self.assertEqual(len(json.load(r)["ad"]["photos"]), 2)
            # 🛠 поддержка из приложения: скриншот больше 64 КБ проходит, Support.start — заглушка
            import base64
            import support
            sup = support.Support.__new__(support.Support)
            sup.allowed, sup.media_dir, calls = avito.allowed, tempfile.mkdtemp(), []
            sup.start = lambda chat, text, shots=None: calls.append((chat, text, shots))

            def post(body):
                req = urllib.request.Request(base + "/api/support", data=json.dumps(body).encode(),
                                             headers=dict(h, **{"Content-Type": "application/json"}))
                try:
                    with urllib.request.urlopen(req) as r:
                        return r.status, json.load(r)
                except urllib.error.HTTPError as e:
                    return e.code, json.load(e)
            self.assertEqual(post({"text": "x"})[0], 503)  # ещё не запущена
            with mock.patch.object(avito, "SUP", sup):
                shot = base64.b64encode(b"\xff\xd8" + bytes(80 * 1024)).decode()
                self.assertEqual(post({"text": "цена не считается", "photo": shot}), (200, {"ok": True}))
                self.assertEqual(calls[-1][0], 7)
                self.assertTrue(calls[-1][2][0].endswith(".jpg"))
                self.assertEqual(post({"text": " "})[0], 400)
            self.assertEqual(len(calls), 1)
            self.assertIn("sup|ask", json.dumps(avito.help_buttons()))
        finally:
            srv.shutdown()
            srv.server_close()


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
        self.assertIn("⚠️ Голосовое не разобрал: тихо", self.texts()[-1])

    def test_text_only(self):
        avito.on_message(self.msg(text="Продаю велосипед Stels, 26 дюймов"))
        self.assertEqual(avito.draft()["title"], "iPhone 13 128 ГБ")

    def test_video(self):
        """Видео: 3 самых чётких кадра — фото на белом, речь — рассказ о вещи, сразу описание."""
        def video(tok, msg, folder, frames=4):
            paths = []
            for n, size in enumerate([50, 400, 300, 10, 500, 200]):
                path = os.path.join(folder, f"frame-{n}.jpg")
                write_file(path, b"x" * size)
                paths.append(path)
            return {"frames": paths, "text": "кроссовки найк сорок второй", "seconds": 9}
        with mock.patch.object(avito.media, "video", video):
            avito.on_message(self.msg(video_note={"file_id": "v", "duration": 9}))
        d = avito.draft()
        self.assertEqual(len(d["photos"]), 3)
        self.assertTrue(all(p["clean"] for p in d["photos"]))
        self.assertEqual(len(self.albums[-1]), 3)
        self.assertIn("кроссовки найк", d["info"])
        self.assertEqual(d["title"], "iPhone 13 128 ГБ")
        self.assertTrue(any("🎙" in t and "кроссовки найк" in t for t in self.texts()))
        # у готового объявления речь из видео — правка описания
        calls = []
        with mock.patch.object(avito.media, "video", video), \
                mock.patch.object(avito, "write", lambda chat, req="", mid=None: calls.append(req)):
            avito.on_message(self.msg(video={"file_id": "v2"}, caption="добавь про коробку"))
        self.assertEqual(len(d["photos"]), 6)
        self.assertEqual(calls, ["добавь про коробку\nкроссовки найк сорок второй"])
        with mock.patch.object(avito.media, "video", mock.Mock(side_effect=avito.media.MediaError("битое"))):
            avito.on_message(self.msg(video={"file_id": "v3"}))
        self.assertIn("⚠️ Видео не открылось: битое", self.texts()[-1])

    def test_best_frames(self):
        tmp = tempfile.mkdtemp()
        paths = []
        for n, size in enumerate([5, 50, 20, 40]):
            paths.append(os.path.join(tmp, f"{n}.jpg"))
            write_file(paths[-1], b"x" * size)
        self.assertEqual(avito.best_frames(paths, 2), [paths[1], paths[3]])  # в порядке видео
        self.assertEqual(avito.best_frames(paths, 0), [])

    def test_voice_asks_price(self):
        avito.on_message(self.msg(text="кроссовки nike 42"))
        with mock.patch.object(avito.media, "voice_text", lambda tok, msg: "а сколько такое стоит?"):
            avito.on_message(self.msg(voice={"file_id": "v"}))
        self.assertEqual(avito.draft()["market"]["stats"]["median"], 36000)
        self.assertNotIn("сколько", avito.draft()["info"])

    def test_web_upload_and_write(self):
        code, res = avito.save_upload(b"\xff\xd8" + b"x" * 500, "image/jpeg")
        self.assertEqual((code, res["ad"]["photos"][0]["state"], res["ad"]["pending"]), (200, "wait", 1))
        self.assertEqual(avito.save_upload(b"<html>", "text/html")[0], 400)
        aid = res["ad"]["id"]
        code, res = avito.api_post("/api/process", {"id": aid})
        self.assertEqual(code, 200)
        d = avito.draft()
        self.assertTrue(d["photos"][0]["clean"])  # фон убран
        self.assertEqual(d["title"], "iPhone 13 128 ГБ")  # и сразу описание
        self.assertEqual(avito.ad_view(d)["job"], "")
        self.assertEqual(self.albums, [])  # из приложения чат не засоряем
        seen = {}
        with mock.patch.object(ai, "write_ad", lambda info, imgs=(), prev=None, req="": seen.update(req=req) or {
                "title": "T", "description": "D", "query": "q", "category": "", "questions": []}):
            code, res = avito.api_post("/api/write", {"id": aid, "request": "короче"})
        self.assertEqual((code, res["ad"]["title"], seen["req"]), (200, "T", "короче"))
        with mock.patch.object(ai, "write_ad", mock.Mock(side_effect=ai.AIError("лимит"))):
            avito.api_post("/api/write", {"id": aid, "info": "есть чек"})
        self.assertIn("лимит", avito.ad_view(d)["error"])
        self.assertIn("есть чек", d["info"])
        code, res = avito.api_post("/api/send", {"id": aid}, {"id": 7})
        self.assertEqual((code, len(self.albums[-1])), (200, 1))
        code, res = avito.api_post("/api/region", {"region": "https://www.avito.ru/moskva/odezhda"})
        self.assertEqual((code, res["region"]), (200, "moskva"))
        self.assertEqual(avito.api_post("/api/region", {"region": "Москва!"})[0], 400)
        code, res = avito.api_post("/api/new", {})
        self.assertIsNone(avito.draft())
        self.assertFalse(res["ads"][0]["current"])
        self.assertEqual(avito.api_post("/api/write", {})[0], 400)  # ни фото, ни слов

    def test_market_has_histogram_data(self):
        avito.on_message(self.msg(text="айфон 13"))
        code, res = avito.api_post("/api/market", {"id": avito.draft()["id"]})
        self.assertEqual(res["market"]["prices"], [35000, 37000])
        self.assertEqual(res["market"]["sample"][0]["p"], 35000)


class Mode(Flow):
    """💬 Чат / 📱 Приложение: выбор на первом /start, /mode, кнопки, переключение из приложения."""

    def setUp(self):
        super().setUp()
        self.patches.append(mock.patch.object(avito, "public_url", "https://avito.test"))
        self.patches[-1].start()

    def buttons(self, i=-1):
        return [x for row in self.sent[i][1].get("reply_markup", {}).get("inline_keyboard", []) for x in row]

    def web_apps(self, i=-1):
        return [x for x in self.buttons(i) if "web_app" in x]

    def test_first_start_asks(self):
        avito.st.pop("mode")
        avito.on_message(self.msg(text="/start"))
        self.assertIn("Как удобнее?", self.texts()[0])
        self.assertEqual([x["callback_data"] for x in self.buttons()], ["mode:chat", "mode:app"])
        self.assertEqual(self.web_apps(), [])
        self.cb("mode:app")  # выбрал приложение: карточка → «✅ Режим», следом главный экран с кнопкой приложения
        self.assertEqual(avito.st["mode"], "app")
        m, p = self.sent[-3]
        self.assertEqual((m, p["message_id"]), ("editMessageText", 9))
        self.assertIn("✅ Режим: 📱 приложение", p["text"])
        self.assertEqual(self.sent[-2][0], "sendMessage")
        self.assertIn("Режим: 📱 приложение", self.sent[-2][1]["text"])
        self.assertEqual(self.web_apps(-2)[0]["web_app"]["url"], "https://avito.test/")
        avito.on_message(self.msg(text="/start"))  # второй раз не спрашивает
        self.assertNotIn("Как удобнее?", self.texts()[-1])

    def test_chat_mode_no_web_app(self):
        self.make_ad()
        for cmd in ("/start", "/help"):
            avito.on_message(self.msg(text=cmd))
            self.assertEqual(self.web_apps(), [], cmd)
            self.assertIn("Режим: 💬 чат", self.texts()[-1])
            data = [x.get("callback_data") for x in self.buttons()]
            for want in ("mode", "card", "ads", "new", "avl", "sup|ask"):
                self.assertIn(want, data)
            rows = self.sent[-1][1]["reply_markup"]["inline_keyboard"]
            self.assertTrue(all(len(r) <= 3 for r in rows))
            self.assertTrue(all(len(x["text"]) <= 24 for x in self.buttons()))
        avito.on_message(self.msg(text="/app"))  # явная просьба — кнопка приложения есть
        self.assertEqual(self.web_apps()[0]["web_app"]["url"], "https://avito.test/")

    def test_app_mode_short(self):
        avito.st["mode"] = "app"
        self.make_ad()
        avito.on_message(self.msg(text="/start"))
        self.assertEqual(self.web_apps()[0]["text"], "📱 Приложение")
        self.assertIn("Режим: 📱 приложение", self.texts()[-1])
        self.assertNotIn("/region", self.texts()[-1])  # коротко
        rows = self.sent[-1][1]["reply_markup"]["inline_keyboard"]
        self.assertLessEqual(len(rows), 3)
        self.assertIn("mode", [x.get("callback_data") for x in self.buttons()])
        avito.on_message(self.msg(text="4000"))  # чат отвечает и в режиме приложения
        self.assertEqual(avito.draft()["price"], 4000)

    def test_mode_command_and_button(self):
        avito.on_message(self.msg(text="/mode"))
        self.assertIn("🧭 Как удобнее?", self.texts()[-1])
        self.cb("mode")  # «⚙️ Режим» из меню — карточка на месте
        self.assertEqual(self.sent[-2][0], "editMessageText")
        self.assertIn("Как удобнее?", self.sent[-2][1]["text"])
        avito.st["mode"] = "app"
        self.cb("mode:chat")
        self.assertEqual(avito.st["mode"], "chat")
        self.assertIn("✅ Режим: 💬 чат", self.sent[-3][1]["text"])
        self.assertIn("Режим: 💬 чат · сменить — /mode", self.sent[-2][1]["text"])
        self.assertEqual(self.web_apps(-2), [])
        with self.assertRaises(ValueError):
            avito.set_mode("x")

    def test_switch_from_app(self):
        avito.st["mode"] = "app"
        code, res = avito.api_post("/api/mode", {"mode": "chat"}, {"id": 7})
        self.assertEqual((code, res["mode"], avito.st["mode"]), (200, "chat", "chat"))
        self.assertIn("✅ Режим: 💬 чат", self.texts()[-1])  # в чат пришло главное меню
        self.assertIn("ads", [x.get("callback_data") for x in self.buttons()])
        self.assertEqual(self.web_apps(), [])
        self.assertEqual(avito.api_post("/api/mode", {"mode": "x"}, {"id": 7})[0], 400)
        self.assertEqual(avito.app_state()["mode"], "chat")


def init_data(token, uid=7, age=0):
    """initData, подписанный как в Telegram (ключ HMAC("WebAppData", токен))."""
    import hashlib
    import hmac
    import time
    import urllib.parse
    pairs = {"auth_date": str(int(time.time()) - age), "user": json.dumps({"id": uid})}
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    pairs["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(pairs)



class Chats(Flow):
    """Чаты Авито через официальный API (подменён): ключи, новое сообщение → черновик → «Отправить», автоответ."""

    def setUp(self):
        super().setUp()
        import chats
        self.chats = chats
        self.calls, self.sent_av = [], []
        self.chat = {"id": "u2i-abc", "users": [{"id": 1, "name": "Глеб"}, {"id": 2, "name": "Ольга"}],
                     "context": {"type": "item", "value": {"id": 9, "title": "iPhone 13 128 ГБ",
                                                          "price_string": "35 000 ₽", "url": "https://avito.ru/9"}},
                     "last_message": {"id": "m1", "direction": "in", "created": 100, "type": "text",
                                      "content": {"text": "Торг уместен?"}}}
        self.msgs = [{"id": "m0", "direction": "out", "author_id": 1, "created": 50, "type": "text",
                      "content": {"text": "Здравствуйте"}},
                     {"id": "m1", "direction": "in", "author_id": 2, "created": 100, "type": "text",
                      "content": {"text": "Торг уместен?"}}]
        self.forbidden = False

        def http(method, path, params=None, body=None, form=None, auth=True):
            self.calls.append((method, path))
            if self.forbidden:
                raise chats.AvitoError(chats.NO_TARIFF)
            if path == "/core/v1/accounts/self":
                return {"id": 1, "name": "Глеб"}
            if path.endswith("/chats"):
                return {"chats": [self.chat]}
            if path.endswith("/messages/"):
                return {"messages": self.msgs}
            if path.endswith("/messages"):
                self.sent_av.append(body["message"]["text"])
                return {"id": "m2"}
            if "/chats/" in path and method == "GET":
                return self.chat
            return {"ok": True}
        self.patches += [mock.patch.object(chats, "http", http),
                         mock.patch.object(ai, "reply_buyer", lambda ad, buyer, image=None, history=(), floor=None,
                                           notes="": {"reply": f"Здравствуйте! Немного уступлю ({ad['price']}).",
                                                      "note": ""})]
        for p in self.patches[-2:]:
            p.start()

    def connect(self):
        avito.on_message(self.msg(text="/chats", message_id=1))
        self.assertIn("Подключить чаты Авито", self.texts()[-1])
        avito.on_message(self.msg(text="client123456 secret987654321", message_id=5))
        self.assertIn(("deleteMessage", {"chat_id": 7, "message_id": 5}), self.sent)
        self.assertIn("Авито подключён", self.texts()[-1])
        self.assertEqual(avito.st["av_user"]["id"], 1)

    def test_connect_new_message_send(self):
        self.connect()
        self.assertEqual(self.chats.poll_once(), 0)  # старые непрочитанные при подключении не шлём
        self.chat["last_message"] = dict(self.chat["last_message"], id="m3", created=200)
        self.msgs.append({"id": "m3", "direction": "in", "author_id": 2, "created": 200, "type": "text",
                          "content": {"text": "А зарядка есть?"}})
        self.assertEqual(self.chats.poll_once(), 1)
        card = self.texts()[-1]
        self.assertIn("Ольга", card)
        self.assertIn("А зарядка есть?", card)
        self.assertNotIn("Торг уместен?", card)  # уже виденное не повторяем
        self.assertIn("35000", card)
        k = self.chats.key("u2i-abc")
        avito.on_callback({"id": "1", "from": {"id": 7}, "data": f"avs|{k}",
                           "message": {"chat": {"id": 7}, "message_id": 40}})
        self.assertEqual(self.sent_av, ["Здравствуйте! Немного уступлю (35000)."])
        self.assertEqual(self.chats.poll_once(), 0)  # повторно не присылает

    def test_own_reply_and_auto_mode(self):
        self.connect()
        k = self.chats.key("u2i-abc")
        avito.on_callback({"id": "1", "from": {"id": 7}, "data": f"avw|{k}", "message": {"chat": {"id": 7}, "message_id": 3}})
        avito.on_message(self.msg(text="Да, зарядка есть", message_id=6))
        self.assertEqual(self.sent_av, ["Да, зарядка есть"])
        avito.st["av_mode"] = "auto"
        self.chat["last_message"] = dict(self.chat["last_message"], id="m4", created=300)
        self.msgs.append({"id": "m4", "direction": "in", "author_id": 2, "created": 300, "type": "text",
                          "content": {"text": "Заберу сегодня"}})
        self.chats.poll_once()
        self.assertEqual(len(self.sent_av), 2)
        self.assertIn("Ответил сам", self.texts()[-1])

    def test_no_tariff(self):
        avito.on_message(self.msg(text="/chats", message_id=1))
        self.forbidden = True
        avito.on_message(self.msg(text="client123456 secret987654321", message_id=5))
        self.assertIn("Максимальный", self.texts()[-1])
        self.assertNotIn("av_keys", avito.st)


class Buttons(Chats):
    """Каждая кнопка отвечает (часик не висит), старые и кривые кнопки не роняют бота, тупиков нет."""

    def answers(self):
        return [p for m, p in self.sent if m == "answerCallbackQuery"]

    def press(self, data):
        n = len(self.answers())
        self.cb(data)
        self.assertEqual(len(self.answers()), n + 1, f"нет ответа на кнопку {data}")
        return self.answers()[-1].get("text", "")

    def test_stale_and_broken_buttons_answered(self):
        for data in ("pr|100", "live", "sold"):  # объявления ещё нет
            self.assertIn("Объявления уже нет", self.press(data))
        self.make_ad()
        for data in ("pr|", "pr|abc", "open", "floor|x", "avo", "avs", "avmode", "avmode|zzz", "zzz", "avzz", ""):
            self.assertTrue(self.press(data), data)
        self.assertIn("устарела", self.press("zzz"))
        self.assertIn("Черновика уже нет", self.press("avs|nokey"))
        self.press("open|нет-такого")
        self.assertIn("Этого объявления уже нет", self.texts()[-1])
        self.press("avo|nokey")
        self.assertIn("Чат устарел", self.texts()[-1])

    def test_crash_in_handler_still_answered(self):
        self.make_ad()
        with mock.patch.object(avito, "show_card", side_effect=RuntimeError("сеть упала")):
            note = self.press("card")
        self.assertIn("⚠️", note)
        self.assertIn("Кнопка не сработала: сеть упала", self.texts()[-1])

    def test_new_cancels_buyer_wait(self):
        self.make_ad()
        self.cb("buy")
        self.assertEqual(avito.waiting()["kind"], "buyer")
        avito.on_message(self.msg(text="/new"))
        self.assertIsNone(avito.waiting())
        with mock.patch.object(ai, "reply_buyer", side_effect=AssertionError("не покупатель")):
            avito.on_message(self.msg(text="велосипед stels"))
        self.assertIn("велосипед", avito.draft()["info"])  # рассказ о новой вещи не пропал

    def test_command_cancels_floor_wait(self):
        self.make_ad()
        self.cb("floor")
        avito.on_message(self.msg(text="/ad"))
        self.assertIsNone(avito.waiting())
        avito.on_message(self.msg(text="40000"))
        self.assertEqual(avito.draft()["price"], 40000)  # число — снова цена, а не минимум

    def test_buyer_screenshot_without_ad(self):
        avito.st["wait"] = {"kind": "buyer", "ts": __import__("time").time()}
        with mock.patch.object(avito.media, "photo_path", side_effect=AssertionError("без объявления не качаем")):
            avito.on_message(self.msg(photo=[{"file_id": "s"}]))
        self.assertIn("Объявления уже нет", self.texts()[-1])

    def test_copy_and_publish_without_description(self):
        avito.draft(create=True).update(title="Велосипед", description="")
        self.cb("cp")
        self.cb("pub")
        empty = [p for m, p in self.sent if m == "sendMessage" and not p.get("text")]
        self.assertEqual(empty, [])  # пустое сообщение Telegram не принимает (400)

    def test_message_crash_reported(self):
        update = {"update_id": 1, "message": self.msg(text="/ad")}
        with mock.patch.object(avito, "on_message", side_effect=RuntimeError("диск")), \
                mock.patch.object(avito, "tg", side_effect=[{"result": [update]}, KeyboardInterrupt()]), \
                mock.patch.object(avito, "send") as send, mock.patch.object(avito, "setup_bot"), \
                mock.patch.object(avito.chats, "start"), mock.patch.object(avito, "serve"), \
                mock.patch.object(avito, "load", return_value={"owner": 7}), \
                mock.patch.object(avito.support, "Support") as sup, mock.patch.object(avito, "public_url", "x"), \
                mock.patch.object(avito, "set_menu"), mock.patch.object(avito.threading, "Thread"), \
                mock.patch.object(avito, "st", avito.st), mock.patch.object(avito, "SUP", None):  # main() их меняет
            sup.return_value.intercept.return_value = False
            with self.assertRaises(KeyboardInterrupt):
                avito.main()
        self.assertIn("Что-то сломалось: диск", send.call_args[0][1])

    def test_app_stuck_photos(self):
        """Фото загружено, но фон никто не убирает (загрузка оборвалась) — приложение видит stuck и само зовёт process."""
        d = avito.draft(create=True)
        d["photos"] = [{"orig": "/x/orig_01.jpg", "clean": None}, {"orig": "/x/orig_02.jpg", "clean": None, "work": True}]
        v = avito.ad_view(d)
        self.assertEqual((v["pending"], v["stuck"]), (2, 1))  # второе уже в работе (из чата)

    def test_all_keyboards_fit(self):
        """Кнопки ≤ 24 символов и ≤ 3 в ряд на всех экранах."""
        d = self.make_ad()
        boards = [avito.card_buttons(dict(d, **kw)) for kw in ({}, {"title": ""}, {"status": "live"},
                                                               {"status": "sold"}, {"price": None})]
        boards += [avito.mode_kb(), avito.help_buttons()]
        avito.st["mode"] = "app"
        boards.append(avito.help_buttons())
        self.cb("mk")
        boards.append(avito.market_buttons(d["market"]))
        self.connect()
        k = self.chats.key("u2i-abc")
        boards += [self.chats.draft_buttons(k), self.chats.open_buttons(k)]
        n = len(self.sent)
        for fn in (lambda: self.chats.show_chats(7), lambda: self.chats.show_modes(7), lambda: self.chats.ask_keys(7),
                   lambda: self.chats.open_chat(7, k), lambda: self.cb("pub"), lambda: self.cb("buy"),
                   lambda: self.cb("floor"), lambda: avito.on_message(self.msg(text="/ads"))):
            fn()
        boards += [p["reply_markup"]["inline_keyboard"] for m, p in self.sent[n:]
                   if (p.get("reply_markup") or {}).get("inline_keyboard")]
        for rows in boards:
            for row in rows:
                self.assertLessEqual(len(row), 3, row)
                for x in row:
                    self.assertLessEqual(len(x["text"]), 24, x["text"])


class Audit(Flow):
    """Находки полной проверки: порча state.json, вытеснение папок, повторные нажатия, лишние права Claude."""

    def test_corrupt_state_kept_aside(self):
        with open(avito.STATE, "w") as f:
            f.write('{"owner": 7, "ads": [')  # обрыв записи
        self.assertEqual(avito.load(), {})
        self.assertTrue(os.path.exists(avito.STATE + ".bad"))  # копия, а не молчаливая потеря
        self.assertFalse(os.path.exists(avito.STATE))

    def test_state_file_private_and_atomic(self):
        avito.st["av_keys"] = {"id": "x", "secret": "y"}
        avito.save()
        self.assertEqual(os.stat(avito.STATE).st_mode & 0o777, 0o600)  # в файле ключи Авито
        self.assertFalse(os.path.exists(avito.STATE + ".tmp"))
        self.assertEqual(avito.load()["av_keys"]["id"], "x")

    def test_cleanup_keeps_live_folders(self):
        d1 = avito.draft(create=True)
        keep = dict(d1, title="Живое", photos=[{"orig": "x", "clean": None}])
        avito.st["ads"] = [keep]
        avito.st.pop("draft")
        d = None
        for _ in range(20):  # куча пустых черновиков не должна вытеснять папку настоящего объявления
            avito.archive()
            d = avito.draft(create=True)
        self.assertTrue(os.path.isdir(os.path.join(avito.DRAFTS, d1["id"])))
        self.assertTrue(os.path.isdir(os.path.join(avito.DRAFTS, d["id"])))
        stale = os.path.join(avito.DRAFTS, "20200101-000000-dead")
        os.makedirs(stale)
        avito.cleanup_old()
        self.assertFalse(os.path.exists(stale))  # а лишнее чистится

    def test_write_busy_does_not_duplicate_info(self):
        d = self.make_ad()
        d["job"] = "write"
        before = d["info"]
        code, res = avito.api_post("/api/write", {"id": d["id"], "info": "ещё чехол"})
        self.assertEqual(code, 409)
        self.assertEqual(d["info"], before)

    def test_info_capped(self):
        d = avito.draft(create=True)
        for _ in range(10):
            avito.add_info(d, "слово " * 300)
        self.assertLessEqual(len(d["info"]), 4000)

    def test_bad_content_length(self):
        import socket
        import threading
        from http.server import ThreadingHTTPServer
        srv = ThreadingHTTPServer(("127.0.0.1", 0), avito.Web)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with mock.patch.object(avito, "web_user", lambda h: {"id": 7}):
                for bad in ("-5", "abc"):  # раньше read(-5) ждал конца потока, а «abc» ронял обработчик
                    c = socket.create_connection(srv.server_address, timeout=5)
                    c.sendall(f"POST /api/new HTTP/1.1\r\nHost: x\r\nContent-Length: {bad}\r\n\r\n".encode())
                    self.assertIn(b" 400 ", c.recv(200).split(b"\r\n")[0] + b" ")
                    c.close()
        finally:
            srv.shutdown()
            srv.server_close()

    def test_claude_tools_locked_down(self):
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            return mock.Mock(returncode=0, stdout=json.dumps({"result": "ok"}), stderr="")
        with mock.patch.object(ai.subprocess, "run", run):
            ai.claude("привет")
            img = os.path.join(avito.DATA, "p.jpg")
            write_file(img, b"x" * 200)
            ai.claude("с фото", [img])
        no_img, with_img = calls
        for banned in ("Bash", "Edit", "Write", "Glob", "Grep"):
            self.assertIn(banned, no_img)
            self.assertIn(banned, with_img)
        self.assertIn("Read", no_img[no_img.index("--disallowedTools"):])  # без фото читать диск нечем
        self.assertNotIn("Read", with_img[with_img.index("--disallowedTools"):])  # с фото — только Read

    def test_market_links_only_avito_https(self):
        self.assertEqual(market.safe_url("/moskva/x_1"), "https://www.avito.ru/moskva/x_1")
        self.assertEqual(market.safe_url("javascript:alert(1)"), "")
        self.assertEqual(market.safe_url("https://evil.test/x"), "")
        self.assertEqual(market.safe_url("http://www.avito.ru/x"), "")


class InitData(unittest.TestCase):
    def setUp(self):
        avito.st.clear()
        avito.st["owner"] = 7

    def test_check(self):
        self.assertEqual(avito.check_init_data(init_data("1:test"))["id"], 7)
        self.assertIsNone(avito.check_init_data(init_data("2:x")))
        self.assertIsNone(avito.check_init_data(init_data("1:test", age=90000)))
        self.assertIsNone(avito.check_init_data(init_data("1:test").replace("hash=", "hash=0")))
        self.assertIsNone(avito.web_user({"X-Init-Data": init_data("1:test", uid=8)}))  # не владелец
        self.assertEqual(avito.web_user({"X-Init-Data": init_data("1:test")})["id"], 7)

    def test_style_fallback(self):
        """Цветные кнопки: если Telegram их не знает (400 про кнопки) — те же кнопки без цвета."""
        import io
        import urllib.error
        bodies = []

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake(req, timeout=0):
            body = json.loads(req.data)
            bodies.append(body)
            if "style" in json.dumps(body):
                raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {},
                                             io.BytesIO(b'{"description":"Bad Request: can\'t parse inline keyboard button"}'))
            return Resp(b'{"ok": true, "result": {"message_id": 1}}')
        with mock.patch("urllib.request.urlopen", fake):
            self.assertTrue(avito.send(1, "x", [[avito.b("📊 Цена", "mk", "primary")]])["ok"])
        self.assertEqual(bodies[1]["reply_markup"]["inline_keyboard"][0][0], {"text": "📊 Цена", "callback_data": "mk"})

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
