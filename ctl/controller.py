#!/usr/bin/env python3
"""«Контролёр» — хук Stop/SubagentStop: перед тем как ответ уйдёт Глебу (или агент сдаст работу),
отдельный Claude проверяет ход: ошибки, непроверенное «готово», просьбы к Глебу, которые можно сделать самому.
Нашёл — ответ не уходит (decision=block), Claude исправляет. Не больше MAX_BLOCKS раз на одну просьбу Глеба.
Выкл: CTL_OFF=1. Глеб, 22.10: «каждую работу каждый чат проверять на ошибки, прежде чем мне отправлять,
и проверять, может ли он сделать сам, прежде чем говорить, что мне надо что-то сделать».
"""
import json
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from turn import Turn, entries, short, transcript_for  # noqa: E402

MODEL = os.environ.get("CTL_MODEL", "haiku")
TIMEOUT = 75
MAX_BLOCKS = 2
STATE = os.path.join(tempfile.gettempdir(), "ctl")
# похоже на просьбу к Глебу — тогда проверяем даже короткий ход
ASK = re.compile(r"(нужно|надо) от тебя|от тебя нужно|сделай|нажми|зайди|открой|добавь|включи|пришли|скопируй|"
                 r"вставь|установи|подтверди|в настройк|termius|для тебя:", re.I)

PROMPT = """Ты — «Контролёр»: проверяешь работу другого Claude перед тем, как она уйдёт Глебу (ученик 8 класса;
его правило: Claude делает всё сам, просит его только о том, что физически без него невозможно).

Просьба Глеба:
<<<
{prompt}
>>>

Что делал Claude (инструменты и результаты, сжато):
<<<
{log}
>>>

Ответ, который сейчас уйдёт:
<<<
{final}
>>>

Найди только СЕРЬЁЗНОЕ:
1. Просьба к Глебу сделать то, что Claude может сам: сервер (hub/remote.py), Telegram (tg.py), браузер (Playwright),
   git/PR/слияние, переменные среды, скрипты, агенты. Уместные просьбы — НЕ проблема: код подтверждения, оплата,
   вход в его аккаунт, нажатие в чужом интерфейсе, его выбор/вкус, его «да» перед отправкой работы учителю.
2. «Готово / работает / исправлено / отправлено», а в журнале нет проверки (тест, скрин, вывод команды) или журнал
   показывает обратное (ошибка, упавший тест, пустой результат).
3. Не сделана часть просьбы Глеба, и в ответе об этом не сказано.
4. Явные фактические или расчётные ошибки, видные из журнала.
Стиль, длину, форматирование, мелочи и ритуалы процесса (план задач TaskCreate, Пульс, скиллы, NOW.md) — не трогай. Сомневаешься — считай, что всё в порядке.

Ответь ТОЛЬКО JSON одной строкой: {{"ok": true}} или {{"ok": false, "problems": ["что не так и что сделать — коротко", ...]}}"""


def load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def review(t):
    prompt = PROMPT.format(prompt=short(t.prompt, 3000), log=t.log() or "(инструменты не вызывались)",
                           final=short(t.final, 4000))
    env = dict(os.environ, CTL_CHILD="1")
    try:
        r = subprocess.run(["claude", "-p", prompt, "--model", MODEL, "--tools", "", "--no-session-persistence"],
                           capture_output=True, text=True, timeout=TIMEOUT, env=env, cwd=tempfile.gettempdir())
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"\{.*\}", r.stdout or "", re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except ValueError:
        return None


def main():
    if os.environ.get("CTL_CHILD") or os.environ.get("CTL_OFF"):
        return
    try:
        inp = json.load(sys.stdin)
    except ValueError:
        return
    if inp.get("stop_hook_active"):
        return
    t = Turn(entries(transcript_for(inp)))
    if not t.found or not t.final:
        return
    if len(t.tools) < 2 and not ASK.search(t.final):
        return  # короткий ответ без работы — проверять нечего
    os.makedirs(STATE, exist_ok=True)
    key = f"{inp.get('session_id', 'x')}-{inp.get('agent_id', 'main')}"
    sp = os.path.join(STATE, f"review-{key}.json")
    st = load(sp)
    if st.get("prompt") != t.prompt_id:
        st = {"prompt": t.prompt_id, "blocks": 0}
    if st["blocks"] >= MAX_BLOCKS:
        return
    v = review(t)
    if not v or v.get("ok", True) or not v.get("problems"):
        return
    st["blocks"] += 1
    with open(sp, "w") as f:
        json.dump(st, f)
    probs = "\n".join(f"- {p}" for p in v["problems"][:5])
    print(json.dumps({
        "decision": "block",
        "reason": "🛂 Контролёр проверил ответ перед отправкой и нашёл:\n" + probs +
                  "\nИсправь сам (сделай, проверь делом) и только потом отвечай. Замечание неверно — проверь за 1 шаг "
                  "и отвечай как было, коротко сказав почему.",
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
