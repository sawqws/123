"""Примеры ответов API для скриншотов мини-приложения без сервера (common/webapp/shots.js).
Данные — поддельный WHOOP из тестов (40 дней, бокс 4 раза в неделю), «сегодня» = 11.10.2026, 13:40.

  python3 coach/shots_fixture.py out.json [shots.json]
  node common/webapp/shots.js coach/webapp out.json /tmp/x/coach

  python3 coach/shots_fixture.py --serve 8790 [--kcal-stub]   # локальный сервер для devcheck/perfcheck/своих сценариев

shots.json — список снимков для shots.js ([{"name": ..., "click": [...], "wait": ...}]); без него — главная.
--serve: http://127.0.0.1:<порт>/ — приложение как сайт (без Telegram), /api/… — эти же примеры, /kcal/ — «Калории»
из docs/kcal (coach.kcal_static, как на сервере); пока в docs/kcal нет режима embed (или с --kcal-stub) — заглушка
KCAL_STUB по договору coach/SYNC.md, п. 5 (сегменты сверху, отступ под меню Формы, тема, kcal-changed).
Ответы на POST с телом здесь не различаются: shots.js отдаёт один ответ на путь. Свои /api/… разделов (f_*.py)
добавляются сами: для каждого пути из EXTRA_PATHS вызывается route с пустым телом.
"""
import datetime
import json
import os
import sys
import tempfile
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.argv, ARGS = sys.argv[:1], sys.argv[1:]
sys.path.insert(0, HERE)
import test_coach as T  # noqa: E402  (ставит окружение и поддельный WHOOP)
import coach  # noqa: E402

# GET-пути разделов, которые приложение спрашивает без параметров — добавляй сюда свои
EXTRA_PATHS = ["/api/assess", "/api/journal", "/api/plan", "/api/calendar", "/api/plan/save",
               "/api/regime", "/api/regime/save", "/api/regime/check",
               "/api/habits", "/api/habits/save", "/api/habits/mark", "/api/habits/goal",
               "/api/skin", "/api/skin/save", "/api/skin/mark", "/api/skin/rate", "/api/reviews"]

# Пример «Калорий» и «Режима» по договору coach/SYNC.md — пока сервер их не отдаёт (или для ровных скриншотов)
KCAL = {"items": [
    {"name": "Овсянка с бананом", "meal": "breakfast", "g": 300, "kcal": 420, "p": 12, "f": 8, "c": 74, "salt": 0.3},
    {"name": "Гречка с курицей", "meal": "lunch", "g": 350, "kcal": 560, "p": 42, "f": 12, "c": 68, "salt": 1.6},
    {"name": "Творог 5% с ягодами", "meal": "snack", "g": 220, "kcal": 262, "p": 34, "f": 10, "c": 14, "salt": 0.2}],
    "eaten": {"kcal": 1242, "protein": 88, "fat": 30, "carbs": 156, "salt": 2.1}, "water": 750,
    "goals": {"kcal": 3400, "protein": 130, "fat": 105, "carbs": 485, "salt": 5, "water": 3000},
    "type": "train", "updated": "2026-10-11T13:20:00"}
REGIME_CFG = {"train_days": [0, 2, 4, 5, 6], "train_time": "10:00", "train_minutes": 90,
              "school_days": [0, 1, 2, 3, 4], "school_start": "09:00", "school_end": "16:40",
              "wake": "06:45", "bed": "22:00", "weekend_wake": "08:00", "massage_days": [3],
              "vacation": {"from": "2026-10-05", "to": "2026-10-18", "train_time": "10:00", "massage_days": [3, 6]},
              "shift": {"from": "2026-10-09", "bed": "23:45", "wake": "08:30", "step": 30, "every": 2},
              "evening": ["Душ и умыть лицо", "Ужин: белок и углеводы", "Крем на лицо", "Телефон в сторону"],
              "remind": 45}


def regime_sample(today):
    cfg, week = REGIME_CFG, []
    for i in range(7):
        d = today + datetime.timedelta(days=i)
        wd, on = d.weekday(), d.weekday() in REGIME_CFG["train_days"]
        bed, wake = ("23:15", "08:00") if i < 2 else ("22:45", "07:30") if i < 4 else ("22:15", "07:00")
        week.append({"day": d.isoformat(), "mode": "vacation", "bed": bed, "wake": wake,
                     "train": {"on": on, "time": "10:00", "minutes": 90,
                               "intensity": ("light" if i == 0 else "full") if on else "rest",
                               "why": "3-й день бокса подряд — техника" if i == 0 else "" if on else "день отдыха"},
                     "massage": wd in (3, 6), "kcal": 3400 if on else 2800, "protein": 130 if on else 125,
                     "water": 3000 if on else 2500,
                     "checklist": [{"id": f"e{k}", "text": x, "done": k < 1 and i == 0} for k, x in enumerate(cfg["evening"])],
                     "tips": ["Восстановление 82%, но это 3-й день подряд — работай над техникой",
                              "Сдвигаем сон: сегодня лечь в 23:15, через 2 дня — в 22:45"] if i == 0 else []})
    return {"cfg": cfg, "today": week[0], "week": week}


# Привычки и цели — нейтральный пример (как у любого пользователя): список строками через /api/habits/import
HABITS = """Пить 8 стаканов воды
Зарядка утром
Медитация 10 минут утром
Прогулка 8000 шагов
Читать 20 мин перед сном
Английский 3 раза в неделю
Растяжка вечером
Убраться в комнате по выходным"""
GOALS = [("Купить продукты на неделю", True), ("Повторить конспект", False), ("Ответить на письма", False)]


def seed_habits(today):
    """История за 3 недели (не всё и не всегда — как в жизни) и сегодня: часть сделана, цели на день."""
    coach.route(T.U, "/api/habits/import", {"text": HABITS})
    ids = [h["id"] for h in coach.route(T.U, "/api/habits", {})["habits"]]
    st = coach.f_habits.load(coach.db, T.U)
    for h in st["items"]:
        h["created"] = (today - datetime.timedelta(days=21)).isoformat()
    coach.f_habits.store(coach.db, T.U, st)
    for back in range(1, 22):
        d = (today - datetime.timedelta(days=back)).isoformat()
        doc = {"v": {}, "s": [], "g": []}
        for i, hid in enumerate(ids):
            if (back * 7 + i * 3) % 10 < (8 if i < 2 else 6):  # первые две — почти всегда, остальные — чаще всего
                doc["v"][hid] = {0: 8, 3: 8000, 2: 10, 4: 20}.get(i, 1)
        coach.f_habits.put_doc(coach.db, T.U, d, doc)
    t = {"v": {ids[0]: 5, ids[1]: 1, ids[2]: 10, ids[3]: 4200}, "s": [], "g": []}
    t["g"] = [{"id": f"g{i}", "text": x, "done": dn} for i, (x, dn) in enumerate(GOALS)]
    coach.f_habits.put_doc(coach.db, T.U, today.isoformat(), t)


# Уход за кожей — нейтральный пример (обычный базовый уход), «Заполнить списком» через /api/skin/import
SKIN = """Утро: умыться мягким гелем, увлажняющий крем
Вечер:
- умыться
- крем на ночь
Маска 2 раза в неделю: вт, сб
Скраб раз в неделю
Тип кожи: комбинированная
Не подходит: спиртовые лосьоны"""
SKIN_RATES = [3, 3, 4, 3, 2, 3, 3, 4, 4, 3, 3, 4, 4, 5, 4, 3, 4, 4, 4, 5, 4, 4, 3, 4, 5, 4, 4, 5, 4]  # 29 дней до сегодня


def seed_skin(today):
    """Уход и дневник кожи за месяц (не каждый день всё по плану — как в жизни); сегодня утро сделано, вечер — нет."""
    coach.route(T.U, "/api/skin/import", {"text": SKIN})
    st = coach.f_skin.load(coach.db, T.U)
    st["created"] = (today - datetime.timedelta(days=29)).isoformat()
    coach.f_skin.store(coach.db, T.U, st)
    am, pm = [s["id"] for s in st["am"]], [s["id"] for s in st["pm"]]
    for back, r in enumerate(reversed(SKIN_RATES), 1):
        d = today - datetime.timedelta(days=back)
        full = back < 6 or back % 4 != 0
        m = am + (pm if full else []) + ([st["extra"][0]["id"]] if d.weekday() in (1, 5) else [])
        coach.f_skin.put_doc(coach.db, T.U, d.isoformat(), {"m": m, "r": r if back % 7 != 3 else None, "t": "",
                                                            "a": True, "p": full, "ok": full})
    coach.f_skin.put_doc(coach.db, T.U, today.isoformat(), {"m": am, "r": 4, "t": "", "a": True, "p": False, "ok": False})
    # фото лба раз в 3 дня — серые заглушки (никаких настоящих лиц); последнее 3 дня назад — сегодня день фото
    from test_skin import GRAY_URL
    for back in (15, 12, 9, 6, 3):
        coach.f_skin.save_photo(coach.db, coach.DATA, T.U, {"image": GRAY_URL, "thumb": GRAY_URL},
                                today - datetime.timedelta(days=back))


# Разборы за сегодня — нейтральный пример: утро (уже открыт) и после тренировки (новый); вечер ещё впереди
REVIEWS = [
    ("morning", "07:40", "Восстановление 82%", "",
     "Восстановление **82%** · сон **7:40** из 8:10 · запас сил 98 (обычно 90) · пульс в покое 53\n\n"
     "**Хороший день — можно тренироваться в полную силу.**\n- Тренировка: нагрузка 12–14, разминка 15 мин\n"
     "- Вода 2,5 л, белок 120 г; после тренировки — творог или курица с рисом\n- Лечь до 23:00, подъём в 07:30\n"
     "- Отбой 23:00 · подъём 07:30 · выходной", True),
    ("workout", "12:20", "Бокс · 75 мин", "w-demo",
     "Нагрузка **13,4** · 75 мин · пульс 148/186 · 640 ккал · тяжёлых минут (зоны 4–5) 18\n\n"
     "**Тяжёлая, но посильная тренировка для сегодняшнего восстановления.**\n"
     "- 18 минут почти на максимуме — это раунды; остальное — ровная работа\n"
     "- Завтра восстановление, скорее всего, будет ниже — запланируй лёгкий день\n"
     "- В ближайший час: 500 мл воды, 30 г белка и углеводы (рис, банан)\n"
     "- Лечь сегодня до 23:00 — сну нужно больше обычного, около 8:30", False),
]


def seed_reviews(today):
    for kind, at, title, ref, text, seen in REVIEWS:
        rid = coach.f_reviews.add(coach.db, T.U, today.isoformat(), kind, title, text, at, ref=ref)
        if seen:
            coach.f_reviews.seen(coach.db, T.U, rid)


def seed_water(db, today):
    """Вода прошлых дней (для строки «Питание и вода» в оценке недели): нейтральный пример, сегодня не трогаем."""
    for i, ml in enumerate([1600, 1400, 1900, 1500, 1750, 1300, 1650, 1200, 1500, 1350, 1600, 1250, 1400, 1300], 1):
        day = (today - datetime.timedelta(days=i)).isoformat()
        db.add_water(T.U, day, "12:00", ml // 2)
        db.add_water(T.U, day, "18:00", ml - ml // 2)


def build(shots=None):
    db, _ = T.seeded(days=40)
    coach.db = db
    today = T.TODAY
    now = datetime.datetime.combine(today, datetime.time(13, 40))
    with mock.patch.object(coach, "user_now", lambda u: now), mock.patch.object(coach, "today_of", lambda uid: today), \
            mock.patch.object(coach, "DATA", tempfile.mkdtemp()):  # фото лба — во временную папку, не в coach/data
        db.save_profile(T.U, {"age": "15", "sex": "m", "sport": "бокс", "goal": "form"})
        db.add_goal(T.U, "ложиться до 23:00")
        seed_habits(today)
        seed_skin(today)
        seed_reviews(today)
        seed_water(db, today)
        allp = coach.route(T.U, "/api/all", {})
        allp["today"]["brief"] = "<b>Зелёный день — 82%.</b>\n• ВСР выше нормы, можно спарринг.\n• Ложись до 23:00."
        t = allp["today"]
        if not t.get("kcal"):  # сервер ещё без «Калорий» — пример по договору
            t["kcal"] = KCAL
            for k in ("kcal", "protein", "fat", "carbs"):
                t["eaten"][k] = t["eaten"].get(k, 0) + KCAL["eaten"][k]
            t["eaten"]["salt"] = KCAL["eaten"]["salt"]
            t["water"]["drank"] += KCAL["water"]
            t["water"]["log"] = [{"id": -1, "time": "08:10", "ml": 250, "src": "kcal"},
                                 {"id": -2, "time": "12:30", "ml": 500, "src": "kcal"}] + t["water"]["log"]
        reg = regime_sample(today)
        allp.setdefault("ext", {}).setdefault("regime", reg)
        api = {"/api/all": allp,
               "/api/day": coach.route(T.U, "/api/day", {"d": (today - datetime.timedelta(days=2)).isoformat()}),
               "/api/series": {"series": allp["series"]},
               "/api/progress": {"progress": None},
               "/api/history": {"history": [
                   {"role": "user", "text": "Готов ли я к спаррингу?"},
                   {"role": "coach", "text": "Да. Восстановление **82%**, ВСР выше нормы.\n- Разминка 15 мин\n- После — белок и вода"}]},
               "/api/summary": {"summary": coach.insights.summary(db, T.U, today, "week"), "text": None}}
        for p in EXTRA_PATHS:
            try:
                api[p] = coach.route(T.U, p, {})
            except (KeyError, ValueError):
                pass
        if not isinstance(api.get("/api/regime"), dict) or "today" not in api["/api/regime"]:
            api["/api/regime"] = api["/api/regime/save"] = reg
            api["/api/regime/check"] = reg
        api["/api/habits/import"] = coach.f_habits.import_text(  # предпросмотр «Добавить списком»
            db, T.U, "Пить 8 стаканов воды\nАнглийский 3 раза в неделю\nБег пн ср пт в 07:00", today, preview=True)
        api["/api/skin/import"] = coach.f_skin.import_text(db, T.U, "Утром: тоник\nКрем от солнца утром\nПатчи по субботам",
                                                           today, preview=True)  # предпросмотр «Заполнить списком»
        # фото лба: список, снимок (серый), сохранение сегодня и удаление — последними (меняют данные)
        api["/api/skin/photos"] = coach.route(T.U, "/api/skin/photos", {})
        api["/api/skin/photo/get"] = coach.route(T.U, "/api/skin/photo/get", {"day": api["/api/skin/photos"]["photos"][0]["day"]})
        from test_skin import GRAY_URL
        api["/api/skin/photo"] = coach.route(T.U, "/api/skin/photo", {"image": GRAY_URL, "thumb": GRAY_URL})
        api["/api/skin/photo/delete"] = coach.route(T.U, "/api/skin/photo/delete", {"day": today.isoformat()})
        api["/api/assess?period=month"] = coach.route(T.U, "/api/assess", {"period": "month"})  # --serve: оценка месяца
        api["/api/kcal/link"] = {"token": "demo", "url": "https://sawqws.github.io/123/kcal/#pair=demo"}
    return {"base": "/", "api": api, "shots": shots or [{"name": "home"}]}


# Заглушка встроенных «Калорий» (embed) для скриншотов и замеров, пока настоящие в этой ветке без режима embed
KCAL_STUB = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"><title>Калории</title>
<style>
:root{--bg:#000;--card:#141415;--ink:#fff;--hint:rgba(235,235,245,.6);--fill:rgba(120,120,128,.24);color-scheme:dark}
:root.lt{--bg:#fff;--card:#f2f2f7;--ink:#000;--hint:rgba(60,60,67,.6);--fill:rgba(120,120,128,.14);color-scheme:light}
*{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:var(--ink);
font:16px/1.35 -apple-system,BlinkMacSystemFont,"SF Pro Text",Inter,system-ui,sans-serif;-webkit-font-smoothing:antialiased}
main{max-width:760px;margin:0 auto;padding:12px 16px calc(env(safe-area-inset-bottom) + 90px)}
@media (min-width:1024px){main{max-width:1100px;padding:24px 32px 32px;display:grid;grid-template-columns:1fr 1fr;gap:0 24px}
 .seg,h1{grid-column:1/-1}}
.seg{position:sticky;top:0;z-index:2;display:flex;gap:4px;padding:3px;border-radius:999px;background:var(--fill);
 -webkit-backdrop-filter:blur(20px);backdrop-filter:blur(20px);margin-bottom:14px}
.seg button{flex:1;min-height:36px;border:0;border-radius:999px;background:none;color:var(--ink);font:600 14px/1 inherit}
.seg button.on{background:var(--card);box-shadow:0 2px 8px rgba(0,0,0,.18)}
h1{font-size:30px;margin:6px 4px 14px;letter-spacing:-.02em}
.card{background:var(--card);border-radius:28px;padding:20px;margin-bottom:14px}
.top{display:flex;gap:20px;align-items:center}.ring{flex:none;width:118px;height:118px;border-radius:50%;display:grid;place-items:center;
 background:conic-gradient(var(--ink) 0 37%,var(--fill) 0);position:relative}
.ring::after{content:"";position:absolute;inset:10px;border-radius:50%;background:var(--card)}
.ring b{position:relative;z-index:1;font-size:24px;text-align:center}.ring b small{display:block;font-size:12px;color:var(--hint);font-weight:500}
.m{flex:1}.m div{display:flex;justify-content:space-between;font-size:14px;color:var(--hint);margin:6px 0}.m b{color:var(--ink)}
.row{display:flex;justify-content:space-between;align-items:center;min-height:52px;border-top:.5px solid var(--fill)}
.row:first-of-type{border:0}.row span{color:var(--hint);font-size:13px;display:block}
.add{width:100%;min-height:48px;border:0;border-radius:999px;background:var(--ink);color:var(--bg);font:600 16px/1 inherit;margin-top:8px}
</style></head><body><main>
<nav class="seg"><button class="on">Дневник</button><button>Неделя</button><button>Чат</button><button>Цели</button></nav>
<h1>Сегодня</h1>
<section class="card"><div class="top"><div class="ring"><b>2 158<small>ккал осталось</small></b></div>
<div class="m"><div>Белок <b>88/130 г</b></div><div>Жиры <b>30/105 г</b></div><div>Углеводы <b>156/485 г</b></div><div>Соль <b>2,1/5 г</b></div></div></div>
<button class="add" id="add">Добавить еду</button></section>
<section class="card"><b>Завтрак</b><div class="row"><div>Овсянка с бананом<span>300 г · Б 12 · Ж 8 · У 74</span></div><b>420</b></div></section>
<section class="card"><b>Обед</b><div class="row"><div>Гречка с курицей<span>350 г · Б 42 · Ж 12 · У 68</span></div><b>560</b></div></section>
<section class="card"><b>Перекус</b><div class="row"><div>Творог 5% с ягодами<span>220 г · Б 34 · Ж 10 · У 14</span></div><b>262</b></div></section>
<section class="card"><b>Ужин</b><div class="row"><div>Пока пусто<span>сфотографируй тарелку</span></div><b>—</b></div></section>
<section class="card"><b>Вода</b><div class="row"><div>750 мл из 3 л<span>08:10 · 250 мл, 12:30 · 500 мл</span></div><b>25%</b></div></section>
<section class="card"><b>Неделя</b><div class="row"><div>В среднем 2 940 ккал<span>белок 121 г в день</span></div><b>▲</b></div></section>
</main><script>
var q=new URLSearchParams(location.search);function th(t){document.documentElement.classList.toggle('lt',t==='light')}
th(q.get('theme'));addEventListener('message',function(e){if(e.origin===location.origin&&e.data&&e.data.type==='forma-theme')th(e.data.theme)});
document.getElementById('add').onclick=function(){parent.postMessage({type:'kcal-changed'},location.origin)};
document.querySelectorAll('.seg button').forEach(function(b){b.onclick=function(){document.querySelectorAll('.seg button').forEach(function(x){x.classList.toggle('on',x===b)})}});
</script></body></html>"""


def serve(port, stub=False):
    """Локальный сервер для проверки вида: приложение как сайт, /api/… — примеры, /kcal/ — «Калории» (или заглушка)."""
    import re
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import urllib.parse
    api = build()["api"]
    api["/api/kcal/state"] = {"days": {}, "goals": None, "salt": None, "water": None}
    index = os.path.join(coach.KCAL_DIR, "index.html")
    stub = stub or not (os.path.isfile(index) and "embed" in open(index, encoding="utf-8").read())

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code, body, ctype="application/json; charset=utf-8", headers=None):
            body = body if isinstance(body, bytes) else (body if isinstance(body, str) else json.dumps(body, ensure_ascii=False, default=str)).encode()
            self.send_response(code)
            for k, v in {"Content-Type": ctype, "Content-Length": str(len(body)), "Cache-Control": "no-store", **(headers or {})}.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.do_GET()

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            path = u.path
            if path.startswith("/api/"):
                return self.send(200, api.get(f"{path}?{u.query}", api.get(path, {})))
            if path in ("/kcal/", "/kcal/index.html") and stub:
                return self.send(200, KCAL_STUB, "text/html; charset=utf-8")
            if path == "/kcal" or path.startswith("/kcal/"):
                code, body, ctype, cache, extra = coach.kcal_static(path, u.query, self.headers)
                return self.send(code, body, ctype, {**extra, "Cache-Control": cache})
            name = "index.html" if path in ("/", "/site/") else os.path.basename(path)
            file = os.path.join(coach.WEB, name)
            if not name or not os.path.isfile(file):
                return self.send(404, {"error": "not found"})
            body = open(file, "rb").read()
            if name == "index.html":
                body = re.sub(rb'\s*<script src="https://telegram\.org/[^"]*"></script>', b"", body).replace(b"{{v}}", b"1")
            ctype = coach.mimetypes.guess_type(name)[0] or "application/octet-stream"
            return self.send(200, body, ctype + ("; charset=utf-8" if ctype.startswith("text/") or ctype.endswith("javascript") else ""))

    print(f"http://127.0.0.1:{port}/  · «Калории»: {'заглушка' if stub else coach.KCAL_DIR}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()


if __name__ == "__main__":
    if ARGS and ARGS[0] == "--serve":
        serve(int(ARGS[1]) if len(ARGS) > 1 and ARGS[1].isdigit() else 8790, "--kcal-stub" in ARGS)
        sys.exit()
    out = ARGS[0]
    shots = json.load(open(ARGS[1])) if len(ARGS) > 1 else None
    json.dump(build(shots), open(out, "w"), ensure_ascii=False, default=str)
    print("ok", out)
