#!/usr/bin/env python3
"""Вернуть день «Калорий» (Глеб 06.11: «пропали дни питания — вернуть и чтобы всё сохранялось»).

На сервере (из /root/horo):
  python3 coach/kcal_restore.py days                     дни с историей правок (сколько версий, последняя)
  python3 coach/kcal_restore.py show ДЕНЬ                сейчас + все прошлые версии дня (id, когда, что было)
  python3 coach/kcal_restore.py put ДЕНЬ ID              вернуть версию ID (текущая уходит в историю)
  python3 coach/kcal_restore.py union ДЕНЬ ID [ID…]      сейчас + недостающее из версий (ничего не удаляет)
  python3 coach/kcal_restore.py backup ФАЙЛ.db ДЕНЬ [--put]   день из копии базы (coach/data/backups); --put — вернуть
Пользователь — владелец (meta owner) или COACH_UID. База — COACH_DB или coach/data/coach.db.
После put/union «Калории» на всех устройствах заберут день сами (u — новее всех).
"""
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import store  # noqa: E402


def line(d):
    d = d if isinstance(d, dict) else {}
    its = d.get("items") or []
    kc = round(sum(float(i.get("kcal") or 0) for i in its))
    return f"{len(its)} поз., {kc} ккал, вода {sum(d.get('water') or [])} мл, u {d.get('u')}"


def items(d):
    return "\n".join(f"      {i.get('meal')} · {i.get('name')} · {i.get('g')} г · {round(float(i.get('kcal') or 0))} ккал"
                     for i in (d or {}).get("items") or [])


def main(argv):
    if not argv:
        print(__doc__)
        return 1
    db = store.Store(os.environ.get("COACH_DB") or os.path.join(HERE, "data", "coach.db"))
    uid = int(os.environ.get("COACH_UID") or db.meta("owner") or 0)
    cmd = argv[0]
    if cmd == "days":
        for r in db.q("SELECT day, COUNT(*) AS n, MAX(at) AS at FROM kcal_hist WHERE user=? GROUP BY day ORDER BY day DESC", (uid,)):
            print(r["day"], f"версий {r['n']}, последняя правка {r['at']}")
        return 0
    if cmd == "show" and len(argv) == 2:
        now = db.kcal_last(uid, 400).get(argv[1])
        print("сейчас:", line(now))
        print(items(now))
        for v in db.kcal_versions(uid, argv[1]):
            print(f"id {v['id']} · {v['at']} · {v['why']}: {line(v['data'])}")
            print(items(v["data"]))
        return 0
    if cmd in ("put", "union") and len(argv) >= 3:
        vers = {v["id"]: v["data"] for v in db.kcal_versions(uid, argv[1])}
        picked = [vers[int(i)] for i in argv[2:] if int(i) in vers]
        if not picked:
            print("нет таких версий")
            return 1
        if cmd == "put":
            d = picked[0]
        else:
            d = db.kcal_last(uid, 400).get(argv[1]) or {"items": [], "water": []}
            for v in picked:
                d = store.kcal_merge(None, v, d)  # недостающее из версии — к текущему, ничего не удаляя
        out = db.kcal_put(uid, argv[1], {"items": d.get("items") or [], "water": d.get("water") or [], "type": d.get("type")})
        print("вернул:", line(out))
        return 0
    if cmd == "backup" and len(argv) >= 3:
        src = sqlite3.connect(f"file:{argv[1]}?mode=ro", uri=True)
        row = src.execute("SELECT data FROM kcal_days WHERE user=? AND day=?", (uid, argv[2])).fetchone()
        d = json.loads(row[0]) if row else None
        print("в копии:", line(d))
        print(items(d))
        if d and "--put" in argv:
            cur = db.kcal_last(uid, 400).get(argv[2]) or {"items": [], "water": []}
            m = store.kcal_merge(None, d, cur)
            print("вернул:", line(db.kcal_put(uid, argv[2], {"items": m["items"], "water": m["water"], "type": m.get("type")})))
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
