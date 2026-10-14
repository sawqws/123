"""Аватар бота Студии: python3 avatar.py out.png <буква> <#цвет1> <#цвет2>
Тот же стиль, что у всех ботов («Оникс-стекло», common/avatar.py): почти чёрный фон, свечение из цветов плана,
стеклянный диск и золотая округлая буква; без подходящего шрифта — знак ✦, без Pillow — свой растеризатор. 640×640."""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
COMMON = os.environ.get("BOTKIT_COMMON") or os.path.join(os.path.dirname(HERE), "common")


def load():
    path = os.path.join(COMMON, "avatar.py")
    if not os.path.exists(path):
        path = os.path.join(os.path.dirname(HERE), "common", "avatar.py")
    spec = importlib.util.spec_from_file_location("common_avatar", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    out, letter = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "")
    c1 = sys.argv[3] if len(sys.argv) > 3 else "#7c5cff"
    c2 = sys.argv[4] if len(sys.argv) > 4 else "#22d3ee"
    load().custom(out, letter, c1, c2)


if __name__ == "__main__":
    main()
