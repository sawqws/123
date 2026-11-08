"""Модели и нарезка для бота «Принтер»: STL, раскладка на столе, картинка, сборка, OrcaSlicer.

Папки внутри HOME (/var/lib/printer-bot):
  ai/                рабочая папка Claude (printer/ai.py)
  ai/models/src/     исходники: органайзер и файлы .scad. Только их Claude может править
  ai/models/stl/     готовые детали
  ai/models/png/     картинки деталей
  build/             папки заданий сборки и нарезки
  gcode/             нарезанное, что ещё не легло на принтер

Правленый код (JS, OpenSCAD) и слайсер идут в песочнице bubblewrap (Ubuntu 24.04 в ROOTFS): без сети, без файлов сервера,
виден только свой каталог задания. Память ограничена, приоритет самый низкий, поэтому боты не страдают.
Установка: printer/setup_slicer.sh. Тесты: printer/test_printer.py.
"""
import array
import hashlib
import json
import math
import os
import re
import shutil
import signal
import struct
import subprocess
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.environ.get("PRINTER_HOME") or "/var/lib/printer-bot"
ROOTFS = "/opt/orca/root"
ORCA = ["xvfb-run", "-a", "/opt/orca/AppRun"]  # пути внутри песочницы
OPENSCAD = "openscad"
THREE = "/opt/three.min.js"
PROFILES_DIR = os.path.join(HERE, "profiles")
TOOLS_DIR = os.path.join(HERE, "tools")
BED, HEIGHT, MARGIN, GAP = 220.0, 240.0, 5.0, 4.0
MAX_TRIANGLES = 1_500_000
SLICE_MEMORY, BUILD_MEMORY = 1500, 800  # МБ; вышел за предел — падает только эта задача, а не боты
SLICE_TIMEOUT, BUILD_TIMEOUT = 40 * 60, 5 * 60
NAME = re.compile(r"^[\w-]{1,60}$")
PROFILES = {  # label, файл процесса, файл пластика
    "bal": ("⚖️ Баланс", "process_bal.json", "filament_mid.json"),
    "strong": ("🛡 Прочнее", "process.json", "filament.json"),
    "fast": ("⚡ Быстрее", "process_max.json", "filament_max.json"),
}
PROJECT = "organizers"


class ModelError(Exception):
    """Понятный текст для Глеба и для Claude."""


def models_dir(kind=""):
    return os.path.join(HOME, "ai", "models", kind)


def stl_path(name):
    return os.path.join(models_dir("stl"), name + ".stl")


def png_path(name):
    return os.path.join(models_dir("png"), name + ".png")


def check_name(name):
    if not NAME.match(name or ""):
        raise ModelError(f"Плохое имя модели: {str(name)[:60]}")
    return name


def safe_stem(filename):
    """Имя модели из имени файла: буквы, цифры, «_» и «-»."""
    stem = os.path.splitext(os.path.basename((filename or "").replace("\\", "/")))[0]
    return re.sub(r"[^\w-]+", "_", stem).strip("_")[:60] or "model"


# ---------- сетка ----------

class Mesh:
    """Треугольники плоским массивом: по 9 чисел на треугольник."""

    def __init__(self, coords=()):
        self.v = array.array("f", coords)

    def __len__(self):
        return len(self.v) // 9

    @classmethod
    def load(cls, path):
        with open(path, "rb") as f:
            data = f.read()
        mesh = cls()
        count = struct.unpack("<I", data[80:84])[0] if len(data) >= 84 else -1
        if count >= 0 and len(data) == 84 + count * 50:
            if count > MAX_TRIANGLES:
                raise ModelError("В модели слишком много треугольников")
            for t in struct.iter_unpack("<12fH", data[84:]):
                mesh.v.extend(t[3:12])
        elif data[:5].lower() == b"solid":
            nums = re.findall(rb"vertex\s+(\S+)\s+(\S+)\s+(\S+)", data)
            if len(nums) > MAX_TRIANGLES * 3:
                raise ModelError("В модели слишком много треугольников")
            try:
                mesh.v.extend(float(x) for vertex in nums for x in vertex)
            except ValueError:
                raise ModelError("Файл STL повреждён") from None
            del mesh.v[len(mesh.v) // 9 * 9:]
        else:
            raise ModelError("Это не STL")
        if not len(mesh):
            raise ModelError("В файле нет ни одного треугольника")
        if not all(math.isfinite(x) for x in mesh.v):
            raise ModelError("Файл STL повреждён")
        return mesh

    def bbox(self):
        xs, ys, zs = self.v[0::3], self.v[1::3], self.v[2::3]
        return min(xs), min(ys), min(zs), max(xs), max(ys), max(zs)

    def size(self):
        x0, y0, z0, x1, y1, z1 = self.bbox()
        return x1 - x0, y1 - y0, z1 - z0

    def placed(self, x, y, rotate=False):
        """Копия: при rotate повёрнута на 90° вокруг Z, стоит на столе, ближний левый угол в точке (x, y)."""
        x0, y0, z0, x1, y1, _ = self.bbox()
        out = Mesh()
        v = self.v
        for i in range(0, len(v), 3):
            if rotate:
                out.v.extend((x + y1 - v[i + 1], y + v[i] - x0, v[i + 2] - z0))
            else:
                out.v.extend((x + v[i] - x0, y + v[i + 1] - y0, v[i + 2] - z0))
        return out

    def save(self, path):
        """Двоичный STL. Вырожденные треугольники выбрасываются: слайсеру нужны ненулевые нормали."""
        v, rows = self.v, []
        for i in range(0, len(v), 9):
            ax, ay, az, bx, by, bz, cx, cy, cz = v[i:i + 9]
            ux, uy, uz, wx, wy, wz = bx - ax, by - ay, bz - az, cx - ax, cy - ay, cz - az
            nx, ny, nz = uy * wz - uz * wy, uz * wx - ux * wz, ux * wy - uy * wx
            length = math.sqrt(nx * nx + ny * ny + nz * nz)
            if length > 0:
                rows.append(struct.pack("<12fH", nx / length, ny / length, nz / length, ax, ay, az, bx, by, bz, cx, cy, cz, 0))
        with open(path, "wb") as f:
            f.write(b"printer bot".ljust(80) + struct.pack("<I", len(rows)) + b"".join(rows))


def arrange(sizes, bed=BED, margin=MARGIN, gap=GAP):
    """Раскладка прямоугольников (ширина, глубина) рядами. Возвращает [(x, y, повернуть)] в порядке sizes.
    Весь набор стоит по центру стола. Не влезает — ModelError."""
    room = bed - 2 * margin
    order = sorted(range(len(sizes)), key=lambda i: -max(sizes[i]))
    spots, x, y, row, used_x = {}, 0.0, 0.0, 0.0, 0.0
    for i in order:
        w, d = sizes[i]
        if max(w, d) > room or min(w, d) > room:
            raise ModelError(f"Деталь {w:.0f}×{d:.0f} мм больше стола: влезает до {room:.0f} мм")
        options = [(max(w, d), min(w, d)), (min(w, d), max(w, d))]
        placed = False
        for pw, pd in options:
            if pw <= room and x + pw <= room and y + pd <= room:
                placed = True
                break
        if not placed:
            y, x, row = y + row + gap, 0.0, 0.0
            for pw, pd in options:
                if pw <= room and y + pd <= room:
                    placed = True
                    break
        if not placed:
            raise ModelError("Детали не влезают на один стол. Убери часть в следующий.")
        spots[i] = (x, y, (pw, pd) != (w, d), pw, pd)
        x += pw + gap
        row = max(row, pd)
        used_x = max(used_x, x - gap)
    used_y = y + row
    dx, dy = (bed - used_x) / 2, (bed - used_y) / 2
    return [(spots[i][0] + dx, spots[i][1] + dy, spots[i][2]) for i in range(len(sizes))]


# ---------- картинка ----------

def _view(mesh, yaw, elevation):
    """Треугольники в экранных координатах: [(глубина, яркость, (x, y)×3)], только лицевые."""
    cy, sy = math.cos(yaw), math.sin(yaw)
    ce, se = math.cos(elevation), math.sin(elevation)
    light = (-0.45, 0.35, 0.82)
    out, v = [], mesh.v
    step = 9 * max(1, len(mesh) // 300_000)  # очень тяжёлую сетку рисуем не целиком: картинка важнее скорости
    for i in range(0, len(v), step):
        pts = []
        for j in (0, 3, 6):
            x, y, z = v[i + j], v[i + j + 1], v[i + j + 2]
            rx, ry = x * cy - y * sy, x * sy + y * cy
            pts.append((rx, ry * se + z * ce, ry * ce - z * se))
        (ax, ay, az), (bx, by, bz), (cx, cy2, cz) = pts
        nx = (by - ay) * (cz - az) - (bz - az) * (cy2 - ay)
        ny = (bz - az) * (cx - ax) - (bx - ax) * (cz - az)
        nz = (bx - ax) * (cy2 - ay) - (by - ay) * (cx - ax)
        length = math.sqrt(nx * nx + ny * ny + nz * nz)
        if not length or nz / length >= 0:  # к камере обращены грани с отрицательной глубиной нормали
            continue
        nx, ny, nz = nx / length, ny / length, nz / length
        shade = 0.28 + 0.72 * max(0.0, nx * light[0] + ny * light[1] - nz * light[2])
        out.append((az + bz + cz, shade, (ax, ay), (bx, by), (cx, cy2)))
    return out


def _draw(draw, tris, box):
    """Рисует дальние грани раньше ближних, вписав модель в прямоугольник box."""
    if not tris:
        return
    xs = [p[0] for t in tris for p in t[2:]]
    ys = [p[1] for t in tris for p in t[2:]]
    x0, y0, w, h = box
    scale = min(w / max(max(xs) - min(xs), 1e-3), h / max(max(ys) - min(ys), 1e-3))
    ox = x0 + (w - (max(xs) - min(xs)) * scale) / 2 - min(xs) * scale
    oy = y0 + (h + (max(ys) - min(ys)) * scale) / 2 + min(ys) * scale
    for depth, shade, a, b, c in sorted(tris, key=lambda t: -t[0]):
        color = (int(218 * shade), int(208 * shade), int(192 * shade))
        draw.polygon([(ox + p[0] * scale, oy - p[1] * scale) for p in (a, b, c)], fill=color)


def render(mesh, out, width=1000, height=620):
    """Картинка детали: слева вид сбоку-сверху, справа вид сверху."""
    from PIL import Image, ImageDraw
    ss = 2
    img = Image.new("RGB", (width * ss, height * ss), (20, 18, 16))
    draw = ImageDraw.Draw(img)
    pad = 28 * ss
    main_w = int(width * ss * 0.64)
    _draw(draw, _view(mesh, math.radians(-32), math.radians(32)), (pad, pad, main_w - 2 * pad, height * ss - 2 * pad))
    _draw(draw, _view(mesh, 0.0, math.radians(90)), (main_w, pad, width * ss - main_w - pad, height * ss - 2 * pad))
    img.resize((width, height), Image.LANCZOS).save(out, "PNG")
    return out


# ---------- запуск в ограниченном окружении ----------

def sandbox(cmd, job, memory):
    """Команда в песочнице: образ только для чтения, виден один каталог job (/job), нет сети и окружения бота."""
    return ["systemd-run", "--scope", "--quiet", "--collect", "-p", f"MemoryMax={memory}M", "-p", "MemorySwapMax=1024M",
            "env", "-i", "PATH=/usr/local/bin:/usr/bin:/bin", "HOME=/tmp", "LC_ALL=C.UTF-8",
            "bwrap", "--ro-bind", ROOTFS, "/", "--bind", job, "/job", "--proc", "/proc", "--dev", "/dev",
            "--perms", "1777", "--tmpfs", "/tmp", "--tmpfs", "/run", "--unshare-all", "--new-session", "--die-with-parent", "--chdir", "/job",
            "nice", "-n", "19", *cmd]


def run(cmd, job, memory, timeout):
    """(код выхода, вывод). Время вышло — убивает задачу и бросает ModelError."""
    p = subprocess.Popen(sandbox(cmd, job, memory), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, errors="replace", start_new_session=True)
    try:
        out, _ = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
        p.communicate()
        raise ModelError(f"Не успело за {timeout // 60} мин") from None
    return p.returncode, out or ""


def new_job():
    """Папка задания. Удаляется вызывающим."""
    job = os.path.join(HOME, "build", "job-" + uuid.uuid4().hex[:12])
    os.makedirs(job)
    return job


def _tail(text, lines=12):
    return "\n".join(text.strip().splitlines()[-lines:])[-1500:]


# ---------- каталог моделей ----------

def _project_parts():
    try:
        with open(models_dir("organizers_parts.json"), encoding="utf-8") as f:
            return set(json.load(f))
    except (OSError, ValueError):
        return set()


def catalog():
    """Модели, новые сверху: [{name, project, modified}]."""
    folder, parts, out = models_dir("stl"), _project_parts(), []
    if not os.path.isdir(folder):
        return out
    for entry in os.scandir(folder):
        if entry.name.endswith(".stl"):
            name = entry.name[:-4]
            out.append({"name": name, "project": name in parts, "modified": entry.stat().st_mtime})
    return sorted(out, key=lambda m: (-m["modified"], m["name"]))


def fingerprints():
    """{деталь: отпечаток файла}: что изменила сборка."""
    out, folder = {}, models_dir("stl")
    if os.path.isdir(folder):
        for entry in os.scandir(folder):
            if entry.name.endswith(".stl"):
                with open(entry.path, "rb") as f:
                    out[entry.name[:-4]] = hashlib.md5(f.read()).hexdigest()
    return out


def describe(name):
    """Размеры и картинка детали. Картинку рисует, если её нет или STL новее."""
    path, png = stl_path(check_name(name)), png_path(name)
    if not os.path.exists(path):
        raise ModelError(f"Модели «{name}» нет")
    mesh = Mesh.load(path)
    if not os.path.exists(png) or os.path.getmtime(png) < os.path.getmtime(path):
        os.makedirs(os.path.dirname(png), exist_ok=True)
        render(mesh, png)
    sx, sy, sz = mesh.size()
    return {"name": name, "x": sx, "y": sy, "z": sz, "triangles": len(mesh), "png": png,
            "fits": fits(sx, sy, sz)}


def fits(sx, sy, sz):
    room = BED - 2 * MARGIN
    return sz <= HEIGHT and max(sx, sy) <= room


def add_stl(data_path, filename):
    """Присланный файл в каталог. Возвращает имя модели."""
    name = safe_stem(filename)
    mesh = Mesh.load(data_path)
    base, n = name, 2
    while os.path.exists(stl_path(name)):
        name, n = f"{base}_{n}", n + 1
    os.makedirs(models_dir("stl"), exist_ok=True)
    mesh.save(stl_path(name))
    return name


def delete(name):
    check_name(name)
    if name in _project_parts():
        raise ModelError("Это деталь органайзера, её меняют правкой проекта")
    for path in (stl_path(name), png_path(name), os.path.join(models_dir("src"), name + ".scad")):
        if os.path.exists(path):
            os.remove(path)


# ---------- сборка из исходников ----------

def build(name):
    """Собирает исходник: «organizers» или файл src/<name>.scad. Возвращает список готовых деталей.
    Ошибка — ModelError с выводом сборщика: его читает Claude, чтобы исправить."""
    check_name(name)
    job = new_job()
    try:
        if name == PROJECT:
            return _build_project(job)
        return [_build_scad(name, job)]
    finally:
        shutil.rmtree(job, ignore_errors=True)


def _build_scad(name, job):
    src = models_dir("src")
    if not os.path.exists(os.path.join(src, name + ".scad")):
        raise ModelError(f"Нет файла src/{name}.scad")
    for entry in os.scandir(src):
        if entry.name.endswith(".scad"):  # вместе с соседями: файл может подключать их через use и include
            shutil.copy(entry.path, job)
    code, out = run([OPENSCAD, "-o", name + ".stl", name + ".scad"], job, BUILD_MEMORY, BUILD_TIMEOUT)
    made = os.path.join(job, name + ".stl")
    if code or not os.path.exists(made):
        raise ModelError("OpenSCAD не собрал модель:\n" + _tail(out))
    return _install_part(name, made)


def _build_project(job):
    html = os.path.join(models_dir("src"), PROJECT + ".html")
    if not os.path.exists(html):
        raise ModelError(f"Нет файла src/{PROJECT}.html")
    shutil.copy(html, job)
    shutil.copy(os.path.join(TOOLS_DIR, "export_stl.js"), job)
    out_dir = os.path.join(job, "stl")
    os.makedirs(out_dir)
    code, out = run(["node", "--max-old-space-size=500", "export_stl.js", THREE, PROJECT + ".html", "stl"],
                    job, BUILD_MEMORY, BUILD_TIMEOUT)
    made = sorted(f[:-4] for f in os.listdir(out_dir) if f.endswith(".stl"))
    if code or not made:
        raise ModelError("Сборка органайзера упала:\n" + _tail(out))
    done = [_install_part(n, os.path.join(out_dir, n + ".stl")) for n in made]
    with open(models_dir("organizers_parts.json"), "w", encoding="utf-8") as f:
        json.dump(done, f)
    return done


def _install_part(name, made):
    """Проверяет STL и кладёт его в каталог: чужой файл проходит через разбор, а не копируется как есть."""
    check_name(name)
    mesh = Mesh.load(made)
    os.makedirs(models_dir("stl"), exist_ok=True)
    tmp = stl_path(name) + ".tmp"
    mesh.save(tmp)
    os.replace(tmp, stl_path(name))
    return name


# ---------- нарезка ----------

def layout(names):
    """Раскладка деталей на столе: [(имя, Mesh, x, y, повернуть)]. Не влезает — ModelError."""
    meshes = [Mesh.load(stl_path(check_name(n))) for n in names]
    if not meshes:
        raise ModelError("На столе пусто")
    for name, mesh in zip(names, meshes):
        if mesh.size()[2] > HEIGHT:
            raise ModelError(f"«{name}» выше {HEIGHT:.0f} мм: не влезет по высоте")
    spots = arrange([m.size()[:2] for m in meshes])
    return [(n, m, *spot) for n, m, spot in zip(names, meshes, spots)]


def slice_plate(names, profile, out_gcode):
    """Нарезает детали на один стол и кладёт G-code в out_gcode. Возвращает секунды работы слайсера."""
    if profile not in PROFILES:
        raise ModelError("Нет такого профиля")
    _, process, filament = PROFILES[profile]
    placed = layout(names)
    job = new_job()
    started = time.time()
    try:
        files = []
        for i, (name, mesh, x, y, rot) in enumerate(placed):
            path = os.path.join(job, f"part{i}.stl")
            mesh.placed(x, y, rot).save(path)
            files.append(os.path.basename(path))
        for f in ("machine.json", process, filament):
            shutil.copy(os.path.join(PROFILES_DIR, f), job)
        out_dir = os.path.join(job, "out")
        os.makedirs(out_dir)
        code, out = run([*ORCA, "--slice", "0", "--arrange", "0", "--load-settings", f"machine.json;{process}",
                         "--load-filaments", filament, "--outputdir", "out", *files], job, SLICE_MEMORY, SLICE_TIMEOUT)
        made = os.path.join(out_dir, "plate_1.gcode")
        if code in (-9, 137):
            raise ModelError("Модель слишком сложная для сервера: не хватило памяти")
        if code or not os.path.exists(made):
            print("orca ->", code, _tail(out), flush=True)
            raise ModelError(f"Слайсер не смог нарезать (код {code})")
        os.makedirs(os.path.dirname(out_gcode), exist_ok=True)
        shutil.move(made, out_gcode)
        return time.time() - started
    finally:
        shutil.rmtree(job, ignore_errors=True)
