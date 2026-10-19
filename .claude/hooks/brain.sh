#!/usr/bin/env bash
# В начале каждого чата: подтянуть второй мозг Глеба и показать, на чём остановились (Brain/NOW.md).
# Вывод попадает в контекст Claude. Не падает, если Brain недоступен.
B=/home/user/brain
if [ ! -d "$B/.git" ]; then
  timeout 120 git clone -q --depth 1 https://github.com/sawqws/Brain "$B" >/dev/null 2>&1 || rm -rf "$B"
else
  timeout 60 git -C "$B" pull -q --ff-only >/dev/null 2>&1 || true
fi
if [ -f "$B/NOW.md" ]; then
  echo "=== Brain/NOW.md — на чём остановились (продолжай отсюда, Глеба не переспрашивай) ==="
  cat "$B/NOW.md"
  echo "=== Дальше прочитай $B/wiki/INDEX.md и нужные страницы. NOW.md обновляй сам после каждого шага. ==="
else
  echo "Brain не подключён: вызови add_repo sawqws/Brain (клон в $B), прочитай NOW.md и wiki/INDEX.md."
fi
# «Пульс»: сводка этого чата (контекст, задачи, статус) → бот «Пульс» через Главного
python3 "$(dirname "$0")/../../pulse/reporter.py" start >/dev/null 2>&1 || true
# Библиотеки для почерка и браузера — в фоне, чтобы первая задача не ждала установки (старт сессии не тормозит)
( python3 -c "import scipy, skimage, numpy, PIL" 2>/dev/null || pip install -q scipy scikit-image numpy pillow
  [ -d "$(dirname "$0")/../../node_modules/playwright" ] || (cd "$(dirname "$0")/../.." && npm i --silent)
) >/dev/null 2>&1 &
exit 0
