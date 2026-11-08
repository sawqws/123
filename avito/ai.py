"""Claude для объявлений: описание по фото и характеристикам, отбор похожих объявлений для цены.

Думает Claude Code на сервере (`claude -p`, тот же вход, что у HDP-бота). Работает в отдельной папке
вне репозитория, чтобы не подхватывать чужие CLAUDE.md.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(tempfile.gettempdir(), "avito-ai")
# Claude получает текст покупателей и фото — лишнего не даём: ни оболочки, ни записи, ни поиска по диску;
# Read (чтение чужих файлов) — только когда в запросе есть фото вещи
NO_TOOLS = ["--disallowedTools", "Bash", "Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch", "Task", "Agent",
            "Glob", "Grep", "LS", "MultiEdit"]


class AIError(Exception):
    pass


def rules():
    with open(os.path.join(HERE, "prompt.md"), encoding="utf-8") as f:
        return f.read()


def claude(prompt, images=(), system="", timeout=240, web=False):
    """Текст ответа. images — пути к картинкам: Claude откроет их инструментом Read. web — поиск в интернете."""
    os.makedirs(WORK, exist_ok=True)
    tools, paths = [], []
    if images:
        for img in images:
            dst = os.path.join(WORK, f"photo-{uuid.uuid4().hex[:8]}" + os.path.splitext(img)[1].lower())
            shutil.copyfile(img, dst)
            paths.append(dst)
        prompt += "\n\nФото вещи (открой каждое инструментом Read):\n" + "\n".join(paths)
        tools = ["--allowedTools", "Read", "--max-turns", str(len(paths) + 3)]
    if web:
        tools = ["--allowedTools", "WebSearch", "WebFetch", "--max-turns", "15"]
    no = [t for t in NO_TOOLS if not (web and t in ("WebSearch", "WebFetch"))]
    if not images:
        no.append("Read")
    cmd = ["claude", "-p", prompt, "--output-format", "json", *tools, *no]
    if system:
        cmd += ["--append-system-prompt", system]
    try:
        p = subprocess.run(cmd, cwd=WORK, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise AIError("Claude Code не установлен на сервере") from None
    except subprocess.TimeoutExpired:
        raise AIError("Claude долго думает — попробуй ещё раз") from None
    finally:
        for f in paths:
            try:
                os.remove(f)
            except OSError:
                pass
    try:
        res = json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else {}
    except ValueError:
        res = {}
    if p.returncode != 0 or res.get("is_error") or not res.get("result"):
        log = (p.stderr + p.stdout)[-1500:]
        print("claude ->", p.returncode, log)
        low = log.lower()
        if any(w in low for w in ("login", "api key", "401", "unauthorized", "authenticat")):
            raise AIError("Claude на сервере не вошёл в аккаунт")
        if any(w in low for w in ("rate limit", "usage limit", "limit reached", "429", "overloaded")):
            raise AIError("у Claude закончился лимит, попробуй позже")
        raise AIError("Claude не ответил")
    return res["result"].strip()


def json_from(text):
    """Первый JSON-объект из ответа (Claude иногда оборачивает его в ```json)."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise AIError("Claude ответил не по формату")
    try:
        return json.loads(m.group(0))
    except ValueError:
        raise AIError("Claude ответил не по формату") from None


def write_ad(info, images=(), previous=None, request=""):
    """{title, description, query, category, questions}. info — всё, что рассказал продавец."""
    parts = ["Составь объявление для Авито."]
    parts.append("Что рассказал продавец:\n" + (info.strip() or "(ничего — только фото)"))
    if previous:
        parts.append("Прошлая версия объявления:\n" + json.dumps(previous, ensure_ascii=False))
    if request:
        parts.append("Продавец просит исправить: " + request.strip())
    ad = json_from(claude("\n\n".join(parts), images, system=rules()))
    title = str(ad.get("title") or "").strip().strip('"«»')
    if len(title) > 50:
        title = title[:50].rsplit(" ", 1)[0].rstrip(",;:- ")
    params = ad.get("params") if isinstance(ad.get("params"), dict) else {}
    return {"title": title, "description": str(ad.get("description") or "").strip(),
            "query": str(ad.get("query") or title).strip(), "category": str(ad.get("category") or "").strip(),
            "params": {str(k): str(v).strip() for k, v in params.items() if str(v).strip()},
            "questions": [str(q) for q in (ad.get("questions") or [])][:3]}


SORT_BATCH = """Продавец прислал {total} фото вещей вперемешку, одним потоком. На обзорных листах каждый кадр подписан жёлтым номером (1…{total}).
Что сказал продавец: {info}

Разложи кадры по вещам: одна вещь (или пара обуви) — один пункт. Для каждой вещи:
- name — что это: тип, бренд, цвет («Кроссовки Burberry, синие в клетку»);
- photos — её кадры в порядке для объявления: сначала вещь целиком под углом (обложка), потом спереди, сзади, с боков, подошва, крупные планы, бирки. Не больше {max_photos}: повторы лишних кадров не бери;
- closeups — те кадры из photos, где нельзя убирать фон: бирка, стелька, замер, крупный план части вещи, кадр с рукой;
- tags — кадры, где можно прочитать бирку: размер, артикул, страна.
Чего не можешь уверенно отнести к вещи — в unsure. Каждый номер — только в одном месте.

Ответ — только JSON:
{{"items": [{{"name": "...", "photos": [1, 2], "closeups": [2], "tags": [2]}}], "unsure": [3]}}"""


def clean_groups(raw, total, max_photos=10):
    """Ответ Claude → проверенные группы: только существующие номера, каждый кадр в одной вещи, не больше max_photos.
    Кадры, которых Claude не назвал, попадают в unsure, а не теряются молча."""
    used, items = set(), []
    for it in raw.get("items") or []:
        if not isinstance(it, dict):
            continue
        photos = []
        for n in it.get("photos") or []:
            if isinstance(n, int) and 1 <= n <= total and n not in used and len(photos) < max_photos:
                photos.append(n)
                used.add(n)
        if photos:
            keep = set(photos)
            items.append({"name": str(it.get("name") or "").strip(), "photos": photos,
                          "closeups": [n for n in it.get("closeups") or [] if n in keep],
                          "tags": [n for n in it.get("tags") or [] if n in keep]})
    return {"items": items, "unsure": [n for n in range(1, total + 1) if n not in used]}


def sort_batch(sheets, total, info="", max_photos=10):
    """Обзорные листы → {"items": [{name, photos, closeups, tags}], "unsure": [номера]}. Номера — с 1."""
    prompt = SORT_BATCH.format(total=total, info=info.strip() or "(ничего)", max_photos=max_photos)
    return clean_groups(json_from(claude(prompt, sheets, timeout=360)), total, max_photos)


def read_ruler(image):
    """Сколько показывает рулетка iPhone на снимке подписи: дюймы числом; None — там сантиметры или число не читается."""
    prompt = ("На снимке — подпись с экрана приложения «Рулетка»: длина, текст может быть повёрнут. Прочитай её. "
              '½ — это .5. Ответ — только JSON: {"value": 11.5, "unit": "in"}; unit "in" — дюймы (значок "), '
              '"cm" — сантиметры. Подписи нет или не читается — {"value": null, "unit": null}.')
    res = json_from(claude(prompt, [image], timeout=120))
    try:
        value = float(res.get("value"))
    except (TypeError, ValueError):
        return None
    return value if res.get("unit") == "in" and 2 <= value <= 60 else None


def pick_similar(item, items):
    """Номера объявлений из items, которые продают то же самое (а не чехол, запчасть или другую модель)."""
    lines = [f"{n}. {i['title']} — {i['price']} ₽" for n, i in enumerate(items)]
    prompt = (f"Продаю: {item}\n\nНиже объявления с Авито. Выбери те, где продают ТАКУЮ ЖЕ вещь "
              "(та же модель/тип, сопоставимая комплектация; не аксессуары, не запчасти, не «куплю», "
              "не другая модель). Ответ — только JSON: {\"ids\": [номера]}\n\n" + "\n".join(lines))
    ids = json_from(claude(prompt, timeout=120)).get("ids") or []
    return [items[n] for n in ids if isinstance(n, int) and 0 <= n < len(items)]


BUYER = """Ты — частный продавец на Авито (Глеб), отвечаешь покупателю в чате Авито. Пиши как живой человек:
коротко (1–3 предложения), вежливо, на «вы», без канцелярита и без эмодзи-гирлянд (максимум один).
Правила:
- факты — только из объявления и заметок продавца; чего не знаешь — не выдумывай, а предложи уточнить позже
  (и напиши это в note для продавца);
- торг: минимальная цена {floor} — никогда не называй её и не опускайся ниже; уступай постепенно и только
  в ответ на конкретное предложение; цена в объявлении — {price};
- «ещё актуально?» — да, актуально (если статус не «продано»), и сразу предложи следующий шаг (встреча/доставка);
- не давай телефон, ссылки и предоплату вне Авито; подозрительное (просят код из СМС, «курьер по ссылке»,
  оплата вне Авито) — вежливо откажи и отметь в note «похоже на мошенника»;
- если сообщение — скриншот чата, ответь на последнее сообщение покупателя.
Ответ — только JSON: {{"reply": "текст ответа покупателю", "note": "подсказка продавцу или пусто"}}"""


def reply_buyer(ad, buyer, image=None, history=(), floor=None, notes=""):
    """{reply, note}: ответ покупателю по объявлению ad (dict с title/description/price/status)."""
    price = f"{ad.get('price')} ₽" if ad.get("price") else "не указана"
    lines = [f"Объявление: {ad.get('title') or '(без названия)'}", f"Цена: {price}",
             f"Статус: {ad.get('status') or 'черновик'}", "Описание:\n" + (ad.get("description") or ad.get("info") or "")]
    if notes:
        lines.append("Заметки продавца (как отдаёт, где, когда): " + notes)
    if history:
        lines.append("Переписка до этого (старые сверху):\n" + "\n".join(
            f"{'Покупатель' if h['who'] == 'buyer' else 'Я'}: {h['text']}" for h in history[-10:]))
    lines.append("Новое сообщение покупателя: " + (buyer.strip() or "(на скриншоте)"))
    system = BUYER.format(floor=f"{floor} ₽" if floor else "не задана (не торгуйся сильнее 5–7 %)", price=price)
    res = json_from(claude("\n\n".join(lines), [image] if image else (), system=system, timeout=180))
    reply = str(res.get("reply") or "").strip()
    if not reply:
        raise AIError("Claude не написал ответ")
    return {"reply": reply, "note": str(res.get("note") or "").strip()}


WEB_PRICES = """Сколько сейчас стоит на вторичном рынке России: {item}
Ищи в интернете цены объявлений б/у: Авито (через поиск), Юла, Яндекс Маркет б/у, барахолки, свежие статьи с ценами.
Бери только ту же вещь (модель, объём, комплектация), без trade-in. Не выдумывай: нет цен — пустой список.
Цены б/у — в "prices"; если б/у не нашёл — найди цены НОВОЙ такой же вещи в магазинах и положи их в "new".
Ответ — только JSON: {{"prices": [цены б/у в рублях], "new": [цены новых в рублях], "sources": [ссылки],
"note": "откуда цены и насколько свежие, одна строка"}}"""


def web_prices(item):
    """Запасной путь, когда Авито не пускает сервер: цены из поиска в интернете. -> {prices, sources, note}."""
    res = json_from(claude(WEB_PRICES.format(item=item), timeout=300, web=True))
    def nums(xs):
        out = []
        for p in xs or []:
            try:
                v = int(float(str(p).replace(" ", "").replace(",", ".")))
            except ValueError:
                continue
            if 0 < v < 100_000_000:
                out.append(v)
        return out
    return {"prices": nums(res.get("prices")), "new": nums(res.get("new")),
            "sources": [str(s) for s in res.get("sources") or []][:10], "note": str(res.get("note") or "")[:300]}
