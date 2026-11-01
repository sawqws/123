"""Примеры ответов API для скриншотов мини-приложения без сервера (common/webapp/shots.js).
Данные — поддельный WHOOP из тестов (40 дней, бокс 4 раза в неделю), «сегодня» = 11.10.2026, 13:40.

  python3 coach/shots_fixture.py out.json [shots.json]
  node common/webapp/shots.js coach/webapp out.json /tmp/x/coach

shots.json — список снимков для shots.js ([{"name": ..., "click": [...], "wait": ...}]); без него — главная.
Ответы на POST с телом здесь не различаются: shots.js отдаёт один ответ на путь. Свои /api/… разделов (f_*.py)
добавляются сами: для каждого пути из EXTRA_PATHS вызывается route с пустым телом.
"""
import datetime
import json
import os
import sys
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.argv, ARGS = sys.argv[:1], sys.argv[1:]
sys.path.insert(0, HERE)
import test_coach as T  # noqa: E402  (ставит окружение и поддельный WHOOP)
import coach  # noqa: E402

# GET-пути разделов, которые приложение спрашивает без параметров — добавляй сюда свои
EXTRA_PATHS = ["/api/assess", "/api/journal", "/api/plan", "/api/calendar", "/api/plan/save",
               "/api/regime", "/api/regime/save", "/api/regime/check"]

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


def build(shots=None):
    db, _ = T.seeded(days=40)
    coach.db = db
    today = T.TODAY
    now = datetime.datetime.combine(today, datetime.time(13, 40))
    with mock.patch.object(coach, "user_now", lambda u: now), mock.patch.object(coach, "today_of", lambda uid: today):
        db.save_profile(T.U, {"age": "15", "sex": "m", "sport": "бокс", "goal": "form"})
        db.add_goal(T.U, "ложиться до 23:00")
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
        api["/api/kcal/link"] = {"token": "demo", "url": "https://sawqws.github.io/123/kcal/#pair=demo"}
    return {"base": "/", "api": api, "shots": shots or [{"name": "home"}]}


if __name__ == "__main__":
    out = ARGS[0]
    shots = json.load(open(ARGS[1])) if len(ARGS) > 1 else None
    json.dump(build(shots), open(out, "w"), ensure_ascii=False, default=str)
    print("ok", out)
