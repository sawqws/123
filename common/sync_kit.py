"""Собрать общий вид мини-приложений: стеклянный слой (common/webapp/glass.css) дописывается в конец kit.css.
kit.css/kit.js в папках webapp/ — ссылки на common/webapp. Отдельные файлы только у Студии (botkit) и Ловца —
им дописывается studio/kit/glass.css. Повторный запуск заменяет слой, а не дублирует его. python3 common/sync_kit.py"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMMON = os.path.join(ROOT, "common", "webapp")
MARK = "/* ========== Стекло (как в iOS)"


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def write(p, s):
    with open(p, "w", encoding="utf-8") as f:
        f.write(s)


def base(path):
    s = read(path)
    return s[:s.index("\n" + MARK)].rstrip() + "\n" if MARK in s else s


def main():
    glass = read(os.path.join(COMMON, "glass.css"))
    kit = base(os.path.join(COMMON, "kit.css")) + glass
    write(os.path.join(COMMON, "kit.css"), kit)
    sk = os.path.join(ROOT, "studio", "kit")
    for p in (os.path.join(sk, "template", "webapp", "kit.css"), os.path.join(ROOT, "catcher", "webapp", "kit.css")):
        write(p, base(p) + read(os.path.join(sk, "glass.css")))
    print("ok")


if __name__ == "__main__":
    main()
