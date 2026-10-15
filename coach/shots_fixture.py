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
EXTRA_PATHS = ["/api/assess", "/api/journal", "/api/plan", "/api/calendar", "/api/plan/save"]


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
    return {"base": "/", "api": api, "shots": shots or [{"name": "home"}]}


if __name__ == "__main__":
    out = ARGS[0]
    shots = json.load(open(ARGS[1])) if len(ARGS) > 1 else None
    json.dump(build(shots), open(out, "w"), ensure_ascii=False, default=str)
    print("ok", out)
