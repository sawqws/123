"""Сматыватель боксёрских бинтов под Ender-3 V3 KE: корпус + валик с ручкой.

Обе детали стоят на столе в позе печати и печатаются без поддержек.
python3 model.py -> frame.stl, spindle.stl, plate.stl (обе детали на одном столе 220x220).
"""
import math
import sys
from pathlib import Path

import numpy as np
import trimesh
from manifold3d import CrossSection, Manifold

SEG = 96
WRAP_W = 60.0     # зазор между щеками (бинт до 55 мм)
CHEEK = 3.5       # толщина щёк
BACK = 3.5        # толщина спинки (лежит на столе)
BASE_W = 76.0     # ширина спинки
AXIS = 52.0       # от спинки до оси валика (рулон до ~100 мм)
FLANGE_R = 48.0   # радиус щёк вокруг оси
SHAFT_D = 14.0    # валик
CLEAR = 0.8       # зазор по диаметру отверстия
SLOT = 3.5        # прорезь вилки под конец бинта
COLLAR_D = 26.0
CRANK_R = 45.0    # плечо ручки
KNOB_D, KNOB_L = 14.0, 24.0
FILLET = 7.0      # косынки между спинкой и щеками
FLAT = 1.5        # срез низа валика под стол
FEED_W, FEED_L = 4.0, 57.0  # щель-натяжитель в спинке


def cyl_x(length, d, x0=0.0, y=0.0, z=0.0):
    """Цилиндр вдоль X от x0."""
    c = Manifold.cylinder(length, d / 2, d / 2, SEG)
    return c.rotate([0, 90, 0]).translate([x0, y, z])


def teardrop(d):
    """Отверстие-капля (острие вверх) — круглое горизонтальное отверстие без провиса."""
    r = d / 2
    circle = CrossSection.circle(r, SEG)
    tip = CrossSection([[(-r * math.cos(math.pi / 4), r * math.sin(math.pi / 4)),
                         (r * math.cos(math.pi / 4), r * math.sin(math.pi / 4)),
                         (0, r * math.sqrt(2))]])
    return circle + tip


def frame():
    width = BASE_W
    # щека в плоскости (Y, Z): оболочка круга вокруг оси и спинки
    zc = BACK + AXIS
    cheek2d = (CrossSection.square([width, 1]).translate([-width / 2, 0])
               + CrossSection.circle(FLANGE_R, SEG).translate([0, zc])).hull()
    hole2d = teardrop(SHAFT_D + CLEAR).translate([0, zc])
    cheek2d = cheek2d - hole2d

    def cheek(x0):
        # extrude идёт по Z; поворачиваем так, чтобы толщина шла по X, а (Y,Z) остались плоскостью щеки
        m = cheek2d.extrude(CHEEK)            # плоскость XY, толщина Z
        m = m.rotate([90, 0, 0])               # (x,y,z)->(x,-z,y): 2D y -> Z
        m = m.rotate([0, 0, 90])               # 2D x -> Y, толщина -> X
        return m.translate([x0, 0, 0])

    total = WRAP_W + 2 * CHEEK
    body = cheek(0) + cheek(CHEEK + WRAP_W)
    body += Manifold.cube([total, width, BACK]).translate([0, -width / 2, 0])
    # косынки 45° вдоль стыка спинки и щёк (жёсткость, печатаются без поддержек)
    tri = CrossSection([[(0, 0), (FILLET, 0), (0, FILLET)]])
    for x0, flip in ((CHEEK, 1), (CHEEK + WRAP_W, -1)):
        g = tri.extrude(width).rotate([90, 0, 0])   # треугольник в плоскости XZ, длина по -Y
        if flip < 0:
            g = g.mirror([1, 0, 0])
        body += g.translate([x0, width / 2, BACK])
    # щель-натяжитель: бинт идёт через неё ровно и с натягом; сверху фаска
    slot = Manifold.cube([FEED_L, FEED_W, BACK + FILLET + 2]).translate([(total - FEED_L) / 2, -FEED_W / 2, -1])
    chamfer = (CrossSection([[(-FEED_W / 2, 0), (FEED_W / 2, 0), (FEED_W / 2 + 2, 2.01), (-FEED_W / 2 - 2, 2.01)]])
               .extrude(FEED_L).rotate([90, 0, 90]).translate([(total - FEED_L) / 2, 0, BACK - 2]))
    body -= slot + chamfer
    return body


def spindle():
    """Валик лежит вдоль X, ось на высоте SHAFT_D/2, низ срезан на 1.5 мм, чтобы зубцы вилки крепко стояли на столе."""
    r = SHAFT_D / 2
    z = r
    shaft_len = CHEEK + WRAP_W + CHEEK - 0.5   # проходит обе щеки
    x0 = 0.0
    collar_len = 5.0
    hub_len = 6.0
    arm_t = 8.0
    parts = []
    # вал с вилкой
    shaft = cyl_x(shaft_len, SHAFT_D, x0, 0, z)
    fork_start = x0 + CHEEK + 1.0
    shaft -= Manifold.cube([shaft_len, SLOT, SHAFT_D + 2]).translate([fork_start, -SLOT / 2, -1])
    # фаска на концах зубцов, чтобы легко входили в нижнюю щеку
    tip = x0 + shaft_len
    cone = (Manifold.cylinder(2.0, r + 1, r - 1.2, SEG).rotate([0, 90, 0]).translate([tip - 2.0, 0, z]))
    ring = cyl_x(2.0, SHAFT_D + 4, tip - 2.0, 0, z) - cone
    shaft -= ring
    parts.append(shaft)
    # упорный буртик и ступица
    parts.append(cyl_x(collar_len, COLLAR_D, x0 - collar_len, 0, z))
    parts.append(cyl_x(hub_len, 20, x0 - collar_len - hub_len, 0, z))
    # плечо ручки (по Y) и рукоять (параллельно оси, наружу)
    ax = x0 - collar_len - hub_len
    arm2d = (CrossSection.circle(10, SEG) + CrossSection.circle(KNOB_D / 2 + 1, SEG).translate([0, CRANK_R]))
    arm2d = arm2d.hull()
    arm = arm2d.extrude(arm_t).rotate([0, -90, 0]).translate([ax, 0, z])  # плоскость YZ, толщина -X
    parts.append(arm)
    parts.append(cyl_x(KNOB_L, KNOB_D, ax - arm_t - KNOB_L, CRANK_R, z))
    m = sum(parts[1:], parts[0])
    # ровное дно: срез 1.5 мм
    m -= Manifold.cube([400, 400, 10]).translate([-200, -200, -10 + FLAT])
    return m.translate([0, 0, -FLAT])


def to_trimesh(m):
    mesh = m.to_mesh()
    return trimesh.Trimesh(vertices=np.asarray(mesh.vert_properties)[:, :3], faces=np.asarray(mesh.tri_verts))


def main(out=Path(__file__).parent):
    fr, sp = frame(), spindle()
    for name, m in (("frame", fr), ("spindle", sp)):
        assert m.status().name == "NoError" and len(m.decompose()) == 1, name
        t = to_trimesh(m)
        t.export(out / f"{name}.stl")
        bb = m.bounding_box()
        print(f"{name}: {bb[3]-bb[0]:.1f} x {bb[4]-bb[1]:.1f} x {bb[5]-bb[2]:.1f} мм, {m.volume()/1000:.1f} см3")
    # стол: обе детали рядом, 10 мм зазор
    fb, sb = fr.bounding_box(), sp.bounding_box()
    plate = fr + sp.translate([fb[3] + 10 - sb[0], fb[1] - sb[1], 0])
    to_trimesh(plate).export(out / "plate.stl")
    return fr, sp


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent)
