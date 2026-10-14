"""Собрать общий вид мини-приложений: слой «Оникс-стекло» (common/webapp/onyx.css) дописывается в конец kit.css.
kit.css/kit.js в папках webapp/ — ссылки на common/webapp. Отдельные файлы только у Студии (botkit) и Ловца —
им дописывается studio/kit/onyx.css. Повторный запуск заменяет слой, а не дублирует его. python3 common/sync_kit.py"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMMON = os.path.join(ROOT, "common", "webapp")
MARKS = ("/* ========== Оникс-стекло", "/* ========== Пастель (как iPhone)", "/* ========== Стекло (как в iOS)")


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def write(p, s):
    with open(p, "w", encoding="utf-8") as f:
        f.write(s)


def base(path):
    s = read(path)
    for mark in MARKS:
        if mark in s:
            s = s[:s.index("\n" + mark)].rstrip() + "\n"
    return s


def main():
    layer = read(os.path.join(COMMON, "onyx.css"))
    kit = base(os.path.join(COMMON, "kit.css")) + layer
    write(os.path.join(COMMON, "kit.css"), kit)
    sk = os.path.join(ROOT, "studio", "kit")
    for p in (os.path.join(sk, "template", "webapp", "kit.css"), os.path.join(ROOT, "catcher", "webapp", "kit.css")):
        write(p, base(p) + read(os.path.join(sk, "onyx.css")))
    print("ok")


if __name__ == "__main__":
    main()
