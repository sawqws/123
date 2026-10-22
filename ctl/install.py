#!/usr/bin/env python3
"""Ставит «Контролёра» в настройки Claude Code этого контейнера (~/.claude/settings.json) — для всех чатов.
Зовёт pulse/reporter.py start (первая команда каждого ответа), поэтому стоит в каждом облачном чате сам.
Хуки подхватываются на лету. Повторный запуск ничего не меняет. usage: python3 ctl/install.py [--remove]
"""
import json
import os
import shutil
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DST = os.path.expanduser("~/.claude/ctl")
SETTINGS = os.path.expanduser("~/.claude/settings.json")
FILES = ("turn.py", "controller.py", "pace.py")
RAW = "https://raw.githubusercontent.com/sawqws/123/main/ctl/"
MARK = "/.claude/ctl/"  # по этому кусочку команды узнаём свои хуки


def hooks():
    run = lambda f, t: {"type": "command", "command": f"python3 {DST}/{f}", "timeout": t}  # noqa: E731
    return {
        "Stop": [{"hooks": [run("controller.py", 90) | {"statusMessage": "🛂 Контролёр проверяет ответ"}]}],
        "SubagentStop": [{"hooks": [run("controller.py", 90) | {"statusMessage": "🛂 Контролёр проверяет агента"}]}],
        "PostToolUse": [{"matcher": "", "hooks": [run("pace.py", 10)]}],
    }


def copy_files():
    os.makedirs(DST, exist_ok=True)
    for f in FILES:
        src = os.path.join(HERE, f)
        if os.path.exists(src):
            shutil.copyfile(src, os.path.join(DST, f))
        else:  # нет репозитория 123 рядом (чат только с Brain) — берём из main
            with urllib.request.urlopen(RAW + f, timeout=20) as r, open(os.path.join(DST, f), "wb") as out:
                out.write(r.read())


def ours(group):
    return any(MARK in (h.get("command") or "") or "ctl-hook-check" in (h.get("command") or "")
               for h in group.get("hooks", []))


def merge(cfg, add=True):
    hk = cfg.setdefault("hooks", {})
    for ev in list(hk):
        hk[ev] = [g for g in hk[ev] if not ours(g)]
        if not hk[ev]:
            del hk[ev]
    if add:
        for ev, groups in hooks().items():
            hk.setdefault(ev, []).extend(groups)
    if not hk:
        del cfg["hooks"]
    return cfg


def main(argv):
    try:
        with open(SETTINGS) as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        cfg = {}
    add = "--remove" not in argv
    if add:
        copy_files()
    new = merge(json.loads(json.dumps(cfg)), add)
    if new != cfg:
        os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
        tmp = SETTINGS + ".tmp"
        with open(tmp, "w") as f:
            json.dump(new, f, ensure_ascii=False, indent=2)
        os.replace(tmp, SETTINGS)
    print("Контролёр " + ("стоит" if add else "снят"))


if __name__ == "__main__":
    main(sys.argv[1:])
