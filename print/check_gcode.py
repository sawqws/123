"""Проверка G-code перед печатью на Ender-3 V3 KE: python3 check_gcode.py файл.gcode

Ищет то, что ломает печать: выход за стол, поток выше лимита пластика,
температуры не по пластику, ожидание нагрева посреди печати, неизвестные для Klipper команды.
"""
import math
import re
import sys

BED, HEIGHT = 220.0, 245.0
AREA = math.pi * 1.75 ** 2 / 4
KNOWN = {"G0", "G1", "G2", "G3", "G4", "G21", "G28", "G90", "G91", "G92", "M73", "M82", "M83", "M84", "M104", "M106",
         "M107", "M109", "M140", "M190", "M204", "M220", "M221", "M400", "M486", "BED_MESH_CALIBRATE",
         "SET_GCODE_VARIABLE", "SET_VELOCITY_LIMIT", "EXCLUDE_OBJECT_DEFINE", "EXCLUDE_OBJECT_START",
         "EXCLUDE_OBJECT_END", "SET_PRINT_STATS_INFO"}


def check(path, max_flow, temps):
    x = y = z = 0.0
    f = 0.0
    layer = 0
    errs, flows, ext = [], [], []
    heat_waits, nozzle_sets = [], []
    cmds = set()
    for n, raw in enumerate(open(path, encoding="utf-8", errors="replace"), 1):
        line = raw.split(";")[0].strip()
        if raw.startswith(";LAYER_CHANGE"):
            layer += 1
        if not line:
            continue
        cmd = line.split()[0]
        cmds.add(cmd)
        p = dict((k, float(v)) for k, v in re.findall(r"([XYZEFS])(-?\d*\.?\d+)", line))
        if cmd in ("M109", "M190") and layer > 1:
            heat_waits.append(n)
        if cmd in ("M104", "M109"):
            nozzle_sets.append(p.get("S"))
        if cmd in ("G0", "G1"):
            nx, ny, nz = p.get("X", x), p.get("Y", y), p.get("Z", z)
            f = p.get("F", f)
            e = p.get("E", 0.0)
            d = math.dist((x, y), (nx, ny))
            if e > 0 and d > 0.05:
                ext.append((nx, ny))
                flows.append(e * AREA / d * f / 60)
            x, y, z = nx, ny, nz
            if z > HEIGHT:
                errs.append(f"строка {n}: Z={z} выше {HEIGHT}")
    xs, ys = [p[0] for p in ext], [p[1] for p in ext]
    flows.sort()
    p999 = flows[int(len(flows) * 0.999)]
    print(f"экструзия X {min(xs):.1f}..{max(xs):.1f}, Y {min(ys):.1f}..{max(ys):.1f}, слоёв {layer}")
    print(f"поток мм³/с: медиана {flows[len(flows)//2]:.1f}, 99.9% {p999:.1f}, макс {flows[-1]:.1f} (лимит {max_flow})")
    print(f"сопло: {sorted(set(s for s in nozzle_sets if s is not None))}")
    unknown = sorted(cmds - KNOWN)
    if min(xs) < -2.5 or max(xs) > BED or min(ys) < 0 or max(ys) > BED:
        errs.append("экструзия за пределами стола")
    if p999 > max_flow * 1.05:
        errs.append(f"поток {p999:.1f} выше лимита {max_flow}")
    bad_t = [s for s in nozzle_sets if s and not temps[0] <= s <= temps[1]]
    if bad_t:
        errs.append(f"температура сопла вне {temps}: {bad_t}")
    if heat_waits:
        errs.append(f"ожидание нагрева посреди печати: строки {heat_waits[:5]}")
    if unknown:
        errs.append(f"незнакомые команды: {unknown}")
    print("ОК" if not errs else "ОШИБКИ:\n- " + "\n- ".join(errs))
    return not errs


if __name__ == "__main__":
    flow = float(sys.argv[2]) if len(sys.argv) > 2 else 9
    lo, hi = (float(v) for v in (sys.argv[3] if len(sys.argv) > 3 else "200-220").split("-"))
    sys.exit(0 if check(sys.argv[1], flow, (lo, hi)) else 1)
