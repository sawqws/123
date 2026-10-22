#!/usr/bin/env python3
"""«Контролёр темпа» — хук PostToolUse: если над просьбой Глеба Claude сидит слишком долго, долго думает перед
шагом или повторяет одно и то же — вмешивается подсказкой в контекст: что ускорить и чем помочь себе.
Глеб, 22.10: «если ты начинаешь слишком долго думать — чтобы он вмешивался, ускорял, помогал». Выкл: CTL_OFF=1.
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from turn import Turn, entries, transcript_for  # noqa: E402

FIRST = 12 * 60      # первая подсказка через 12 мин работы над просьбой
EVERY = 10 * 60      # потом не чаще, чем раз в 10 мин
THINK = 3 * 60       # думал перед шагом дольше 3 мин
REPEAT = 3           # столько ошибок подряд у одного инструмента
STATE = os.path.join(tempfile.gettempdir(), "ctl")

SPEED = ("Ускорься без потери качества: 1) что осталось — 1–3 пункта, лишнее выкинь; 2) независимое — параллельно "
         "(несколько вызовов в одном сообщении, агенты на отдельные части), долгое — run_in_background, а пока "
         "делай следующее; 3) не перечитывай прочитанное и не перезапускай уже зелёное; 4) застрял на одном больше "
         "2 попыток — смени подход или дай подзадачу агенту-помощнику с чётким брифом; 5) Глеб давно ничего не "
         "видел — одна строка статуса.")


def advice(t, st, now):
    out = []
    el = t.elapsed(now)
    if el >= FIRST and now - st.get("long", 0) >= EVERY:
        st["long"] = now
        out.append(f"⏱ Над просьбой Глеба уже {int(el // 60)} мин ({len(t.tools)} шагов). " + SPEED)
    if t.gaps and t.gaps[-1] >= THINK and now - st.get("think", 0) >= EVERY:
        st["think"] = now
        out.append(f"🧠 Перед последним шагом думал {int(t.gaps[-1] // 60)} мин. Дальше короче: выбери первый "
                   "разумный вариант и проверь его делом (команда, тест), а не в уме; большую задачу разбей на шаги.")
    last = t.tools[-REPEAT:]
    if len(last) == REPEAT and all(x["error"] for x in last) and len({x["name"] for x in last}) == 1 \
            and now - st.get("rep", 0) >= EVERY:
        st["rep"] = now
        out.append(f"🔁 {REPEAT} ошибки подряд у {last[0]['name']}. Не повторяй то же — прочитай ошибку целиком, "
                   "найди причину (sp-systematic-debugging) или зайди с другой стороны.")
    return out


def main():
    if os.environ.get("CTL_CHILD") or os.environ.get("CTL_OFF"):
        return
    try:
        inp = json.load(sys.stdin)
    except ValueError:
        return
    t = Turn(entries(transcript_for(inp)))
    if not t.found:
        return
    os.makedirs(STATE, exist_ok=True)
    sp = os.path.join(STATE, f"pace-{inp.get('session_id', 'x')}-{inp.get('agent_id', 'main')}.json")
    try:
        with open(sp) as f:
            st = json.load(f)
    except (OSError, ValueError):
        st = {}
    if st.get("prompt") != t.prompt_id:
        st = {"prompt": t.prompt_id}
    tips = advice(t, st, time.time())
    with open(sp, "w") as f:
        json.dump(st, f)
    if tips:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                                 "additionalContext": "Контролёр темпа:\n" + "\n".join(tips)}},
                         ensure_ascii=False))


if __name__ == "__main__":
    main()
