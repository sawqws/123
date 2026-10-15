"""Решение от руки «как у Глеба» (выбрал 14.10, вариант 3):
- скрин задания (horo/shot.js — только карточка условия) не меняется, справа от каждого решённого номера —
  только ответ его почерком;
- всё решение — на тёмном листе в клетку (как тёмный лист в заметках iPad, Глеб выбрал 14.10), крупно, по номерам;
  не влезло на страницу — следующая страница.
Почерк живой: строка чуть плывёт и наклоняется, размер и промежутки гуляют, начало строк не по линейке.

Математика остаётся «живой» (Глеб, 15.10: «математику не трогай — там норм»): Hand(exact=False) — своя
дрожь, наклон и разброс. Текст и таблицы (horo/table.py) — Apple Pencil, Hand по умолчанию.

Пример (draw.py в папке задания):
    import sys; sys.path.insert(0, 'horo')
    from sheet import Work
    w = Work('horo/tmp/<task>')                 # там shot.png и shot.json от `node horo/shot.js <ссылка> .../shot.png`
    w.solve(1, [[('x − 12a', '(x − 4a)(x + 4a)'), ' + ', ('4a', 'x(x − 4a)'), ' = ', ('x − 4a', 'x(x + 4a)')]])
    w.solve(3, [['AH ⊥ BC, AK ⊥ CD'], ['∠HAK = 180° − ∠A = ∠B']], answer=['ч.т.д.'])
    w.save()                                    # draft_files/1_task.jpg, 2_sheet.jpg, 3_sheet.jpg…
Строка решения — элементы Hand.expr: строки и дроби (числитель, знаменатель). Длинную строку модуль
сам переносит по «=», а слишком длинный кусок — по «+»/«−». Ответ на скрине по умолчанию — последний
кусок решения после «=»; свой — answer=[...]; answer=False — на скрине ничего.
"""
import json
import os
import random

from PIL import Image, ImageDraw

from hand import Hand

SCREEN_INK = SHEET_INK = (90, 160, 240)   # голубой, как в его заметках iPad (скрин и лист тёмные)
PAPER, GRID = (36, 37, 37), (58, 60, 62)   # фон как у скрина задания, линии клетки чуть светлее
PEN = 5
SCREEN_SIZE, SHEET_SIZE = 58, 60
PAGE_W, PAGE_H, CELL = 1768, 2500, 52


def chunks(line):
    """Строку решения — на куски по «=»: каждый следующий кусок начинается с «= »."""
    out, cur = [], []
    for it in line:
        if isinstance(it, str) and '=' in it and cur:
            out.append(cur)
            tail = it.split('=', 1)[1]
            cur = ['= ' + tail.strip()] if tail.strip() else ['= ']
            continue
        cur.append(it)
    if cur:
        out.append(cur)
    return out


_probe = Image.new('RGB', (8000, 600))


def width(items, size):
    """Ширина строки в px (с запасом на живой почерк)."""
    return Hand(_probe, seed=1, width=PEN, exact=False).expr(items, 0, 300, size) * 1.1


def split_ops(ch, size, maxw):
    """Кусок шире строки — режем ещё по «+» и «−» (продолжение начинается со знака), как в тетради."""
    if width(ch, size) <= maxw:
        return [ch]
    out, cur = [], []
    for it in ch:
        if isinstance(it, str) and it.strip() in ('+', '−', '-') and cur and width(cur + [it], size) > maxw * 0.6:
            out.append(cur)
            cur = [it.strip() + ' ']
            continue
        cur.append(it)
    if cur:
        out.append(cur)
    return out


def pack(line, size, maxw):
    """Одна строка решения → строки не шире maxw."""
    rows, cur = [], []
    for ch in [p for c in chunks(line) for p in split_ops(c, size, maxw)]:
        test = cur + ch if cur else ch
        if cur and width(test, size) > maxw:
            rows.append(cur)
            cur = ch
        else:
            cur = test
    if cur:
        rows.append(cur)
    return rows


def hexpr(h, items, x, y, size, seed):
    """Строка как рукой: строка чуть уходит вверх/вниз, размер и шаг между частями гуляют."""
    rnd = random.Random(seed)
    slope = rnd.uniform(-0.009, 0.006)
    s = size * rnd.uniform(0.93, 1.07)
    x0 = x = x + rnd.uniform(-12, 18)
    y = y + rnd.uniform(-6, 6)
    for it in items:
        yy = y + slope * (x - x0) + rnd.uniform(-3, 3)
        ss = s * rnd.uniform(0.96, 1.04)
        x = h.text(it, x, yy, ss) if isinstance(it, str) else h.frac(it[0], it[1], x, yy, ss)
        x += rnd.uniform(-2, 7)
    return x


def paper():
    """Тёмный лист в клетку, как тёмный лист в заметках iPad."""
    im = Image.new('RGB', (PAGE_W, PAGE_H), PAPER)
    d = ImageDraw.Draw(im)
    for x in range(0, PAGE_W, CELL):
        d.line([x, 0, x, PAGE_H], fill=GRID, width=1)
    for y in range(0, PAGE_H, CELL):
        d.line([0, y, PAGE_W, y], fill=GRID, width=1)
    return im


def items(lay):
    """Номера примеров на скрине: {номер: {x — правее примера, y — середина примера}} по shot.json."""
    rects = lay.get('rects') or []
    w = lay.get('width') or 1
    labels = []
    for r in rects:
        t = r['t'].strip()
        head = t.split()[0] if t else ''
        if r['x0'] < w * 0.15 and head[:-1].isdigit() and head[-1:] in ').':
            labels.append((int(head[:-1]), r))
    labels.sort(key=lambda p: p[1]['y0'])
    out, prev = {}, 0
    for n, lb in labels:
        lh = lb['y1'] - lb['y0']   # пример — над номером не выше ~4 строк (дробь), заголовок задания не берём
        mine = [r for r in rects if max(prev, lb['y0'] - 4 * lh) < (r['y0'] + r['y1']) / 2 <= lb['y1'] + 10
                and r['y1'] >= lb['y0'] - lh] or [lb]   # и доходит до строки номера: отдельный заголовок выше — не пример
        top = min(r['y0'] for r in mine)
        out.setdefault(n, {'x': max(r['x1'] for r in mine) + 60, 'y': (top + lb['y1']) / 2})
        prev = lb['y1'] + 10
    return out


class Work:
    def __init__(self, folder, shot='shot.png'):
        self.dir = folder
        self.shot = os.path.join(folder, shot)
        try:
            with open(os.path.join(folder, shot.rsplit('.', 1)[0] + '.json')) as f:
                self.lay = json.load(f)
        except (OSError, ValueError):
            self.lay = {}
        self.items = items(self.lay)
        self.done = []   # (номер, строки, ответ)

    def solve(self, n, lines, answer=None):
        """n — номер примера (как на скрине) или None; lines — строки решения; answer — что написать на скрине."""
        lines = [[ln] if isinstance(ln, str) else list(ln) for ln in lines]
        if answer is None and lines:
            answer = chunks(lines[-1])[-1]
        self.done.append((n, lines, answer))

    def save(self):
        """Рисует скрин с ответами и листы; возвращает пути картинок (их бот покажет Глебу и прикрепит)."""
        out_dir = os.path.join(self.dir, 'draft_files')
        os.makedirs(out_dir, exist_ok=True)
        files = []
        if os.path.exists(self.shot):
            h = Hand(Image.open(self.shot), ink=SCREEN_INK, width=PEN, seed=21, mess=1.3, slant=0.06, exact=False)
            for n, _, ans in self.done:
                it = self.items.get(n)
                if it and ans:
                    hexpr(h, list(ans), it['x'], it['y'] + SCREEN_SIZE * 0.35, SCREEN_SIZE, seed=(n or 0) * 7)
            files.append(os.path.join(out_dir, '1_task.jpg'))
            h.save(files[-1])
        files += self._sheets(out_dir, start=len(files) + 1)
        return files

    def _sheets(self, out_dir, start):
        size, left, rowh = SHEET_SIZE, 70, SHEET_SIZE * 2.45
        maxw = PAGE_W - 60 - left - 130
        rnd = random.Random(9)
        pages, h, y = [], None, 0

        def new_page():
            nonlocal h, y
            if h is not None:
                pages.append(os.path.join(out_dir, f'{start + len(pages)}_sheet.jpg'))
                h.save(pages[-1])
            h = Hand(paper(), ink=SHEET_INK, width=PEN, seed=31 + len(pages), mess=1.3, slant=0.06, exact=False)
            y = CELL * 2 + size

        new_page()
        for n, lines, _ in self.done:
            rows = [r for ln in lines for r in pack(ln, size, maxw)]
            if y > CELL * 2 + size and y + rowh * min(len(rows), 3) > PAGE_H - CELL:
                new_page()   # пример не начинаем в самом низу страницы
            if n is not None:
                h.text(f'{n})', left + rnd.uniform(-6, 8), y + rnd.uniform(-5, 5), size)
            for r in rows:
                if y > PAGE_H - CELL - size:
                    new_page()
                k = min(1.0, maxw / width(r, size))   # одна дробь шире листа — пишем её чуть мельче, а не за край
                hexpr(h, r, left + 120, y, size * max(k, 0.6), seed=rnd.randrange(1 << 16))
                y += rowh
            y += CELL * rnd.uniform(0.5, 0.9)
        pages.append(os.path.join(out_dir, f'{start + len(pages)}_sheet.jpg'))
        h.save(pages[-1])
        return pages
