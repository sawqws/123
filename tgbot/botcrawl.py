"""Протыкать бота целиком: /start и все кнопки на всех экранах (до глубины --depth) через Секретаря (tg.py botpress).
Опасные кнопки (отправить, заказать, создать, обновить…) botpress не жмёт. Пишет отчёт: что нажато, что ответил бот,
где тишина, крутилка без ответа, «⚠️»/ошибка. Запуск на сервере (Секретарь должен быть запущен):
    python3 tgbot/botcrawl.py <username> [--depth 3] [--max 80] [--wait 7] [--out отчёт.json]"""
import argparse
import json
import re
import sys
import time

import tg

BAD = re.compile(r"⚠️|ошибк|error|traceback|exception|не удалось|не получилось|что-то пошло", re.I)
SKIP_KINDS = {"url", "app", "inline", "phone", "geo", "copy"}


def press(bot, wait, **kw):
    """Одно действие через Секретаря. Секретарь перезапускается («Обновить всех») — ждём до 4 мин и повторяем."""
    args = {"bot": bot, "wait": wait, "text": None, "click": None, "msg": None, "force": False}
    args.update(kw)
    for _ in range(16):
        try:
            ok, out = tg.call("botpress", args, timeout=wait * 6 + 60)
        except OSError as e:
            ok, out = False, f"агент оборвал ответ ({e})"
        if ok or not re.search(r"агент не запущен|агент оборвал", str(out)):
            break
        time.sleep(15)
    if not ok:
        return {"fail": out}
    return out if isinstance(out, dict) else {"fail": str(out)[:300]}


def screens(res):
    """Экраны из ответа: сообщения с кнопками (новые и правленое)."""
    msgs = list(res.get("new") or []) + ([res["edited"]] if res.get("edited") else [])
    out = []
    for m in msgs:
        btns = [b for row in m.get("buttons") or [] for b in row]
        if btns:
            out.append({"msg": m["id"], "text": m.get("text") or "", "buttons": btns})
    return out


def sig(s):
    return (s["text"][:60], tuple(b["text"] for b in s["buttons"]))


def problems(res):
    p = []
    if res.get("fail") and "делает что-то по-настоящему" not in res["fail"]:
        p.append("сбой: " + res["fail"][:200])
    if res.get("silent"):
        p.append(res["silent"])
    a = res.get("answer")
    if isinstance(a, str) and a.startswith(("⚠️", "ERROR")):
        p.append(a)
    for m in list(res.get("new") or []) + ([res["edited"]] if res.get("edited") else []):
        if BAD.search(m.get("text") or ""):
            p.append("в ответе: " + (m.get("text") or "")[:240])
    for m in res.get("new") or []:
        for row in m.get("buttons") or []:
            if len(row) > 3:
                p.append(f"в ряду {len(row)} кнопок: " + ", ".join(b["text"] for b in row))
            for b in row:
                if len(b["text"]) > 24:
                    p.append(f"длинная кнопка ({len(b['text'])}): {b['text']}")
    return p


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("bot")
    ap.add_argument("--depth", type=int, default=3)
    ap.add_argument("--max", type=int, default=80)
    ap.add_argument("--wait", type=float, default=7)
    ap.add_argument("--start", default="/start")
    ap.add_argument("--mode", default="💬 В чате", help="что выбрать в карточке «Как удобнее?»")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    log, seen, clicks = [], set(), [0]

    def record(path, res):
        entry = {"path": " › ".join(path), "secs": res.get("secs"), "problems": problems(res),
                 "reply": [(m.get("text") or "")[:160] for m in res.get("new") or []]
                 + ([("✎ " + (res["edited"].get("text") or ""))[:160]] if res.get("edited") else []),
                 "answer": res.get("answer")}
        log.append(entry)
        mark = "❌" if entry["problems"] else "✅"
        print(f"{mark} {entry['path']} ({entry['secs']} с)" + "".join("\n    · " + x for x in entry["problems"]), flush=True)

    def replay(path):
        res = press(a.bot, a.wait, text=a.start)
        cur = screens(res)
        for label in path[1:]:
            target = next((s for s in reversed(cur) if any(b["text"] == label for b in s["buttons"])), None)
            if not target:
                return []
            res = press(a.bot, a.wait, click=label, msg=str(target["msg"]))
            cur = screens(res) or cur
        return cur

    def explore(path, screen, depth):
        for b in screen["buttons"]:
            if clicks[0] >= a.max:
                return
            label = b["text"]
            if b.get("kind") in SKIP_KINDS:
                log.append({"path": " › ".join(path + [label]), "skip": b.get("kind"), "url": b.get("url")})
                continue
            clicks[0] += 1
            res = press(a.bot, a.wait, click=label, msg=str(screen["msg"]))
            if res.get("fail") and "нет в последних" in res["fail"]:
                cur = replay(path)  # меню поправилось на месте — пройти путь заново
                s2 = next((s for s in reversed(cur) if any(x["text"] == label for x in s["buttons"])), None)
                if s2:
                    screen = s2
                    res = press(a.bot, a.wait, click=label, msg=str(s2["msg"]))
            if res.get("fail") and "по-настоящему" in res["fail"]:
                clicks[0] -= 1
                log.append({"path": " › ".join(path + [label]), "skip": "опасная"})
                continue
            record(path + [label], res)
            if depth < a.depth:
                for s in screens(res):
                    if sig(s) not in seen:
                        seen.add(sig(s))
                        explore(path + [label], s, depth + 1)
                # после захода вглубь меню могло поправиться — дальше по этому экрану через повтор пути
                if res.get("edited") and res["edited"]["id"] == screen["msg"]:
                    cur = replay(path)
                    s2 = next((s for s in reversed(cur) if sig(s) == sig(screen)), None)
                    if not s2:
                        return
                    screen = s2

    t0 = time.time()
    res = press(a.bot, a.wait, text=a.start)
    record([a.start], res)
    mode = next((s for s in screens(res) if any(b["text"] == a.mode for b in s["buttons"])), None)
    if mode:  # карточка «Как удобнее?» — проверяем в режиме чата: там все кнопки в чате
        record([a.start, a.mode], press(a.bot, a.wait, click=a.mode, msg=str(mode["msg"]), force=True))
        res = press(a.bot, a.wait, text=a.start)
        record([a.start], res)
    for s in screens(res):
        seen.add(sig(s))
        explore([a.start], s, 1)
    summary = {"bot": a.bot, "clicks": clicks[0], "secs": round(time.time() - t0),
               "problems": sum(1 for e in log if e.get("problems")), "log": log}
    print(f"Итог @{a.bot}: нажатий {clicks[0]}, с проблемами {summary['problems']}, {summary['secs']} с", flush=True)
    if a.out:
        with open(a.out, "w") as f:
            json.dump(summary, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
