"""Картинки модели: сборка и стол печати (python3 render.py <папка>)."""
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

import model as M


def tris(m):
    mesh = m.to_mesh()
    v = np.asarray(mesh.vert_properties)[:, :3]
    return v[np.asarray(mesh.tri_verts)]


def draw(ax, m, color, light=np.array([0.4, -0.5, 0.8])):
    t = tris(m)
    n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-12
    k = 0.35 + 0.65 * np.clip(n @ (light / np.linalg.norm(light)), 0, 1)
    ax.add_collection3d(Poly3DCollection(t, facecolors=np.clip(np.outer(k, color), 0, 1), linewidths=0))
    return t.reshape(-1, 3)


def scene(parts, path, elev, azim, title):
    fig = plt.figure(figsize=(7, 7), dpi=130)
    ax = fig.add_subplot(projection="3d")
    pts = np.vstack([draw(ax, m, np.array(c)) for m, c in parts])
    lo, hi = pts.min(0), pts.max(0)
    mid, r = (lo + hi) / 2, (hi - lo).max() / 2
    ax.set_xlim(mid[0] - r, mid[0] + r); ax.set_ylim(mid[1] - r, mid[1] + r); ax.set_zlim(mid[2] - r, mid[2] + r)
    ax.set_box_aspect([1, 1, 1]); ax.view_init(elev, azim); ax.set_axis_off()
    ax.set_title(title)
    fig.tight_layout(); fig.savefig(path); plt.close(fig)


BLACK, ORANGE = (0.32, 0.32, 0.36), (1.0, 0.45, 0.1)

if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent
    f = M.frame()
    sp = M.spindle()
    # сборка: ось валика вертикально, ручка сверху
    asm_sp = sp.translate([0, 0, M.FLAT - M.SHAFT_D / 2 + M.BACK + M.AXIS])
    up = lambda m: m.rotate([0, 90, 0]).rotate([0, 0, 180])
    scene([(up(f), BLACK), (up(asm_sp), ORANGE)], out / "view_assembly.png", 20, -60, "Сборка")
    scene([(up(f), BLACK), (up(asm_sp), ORANGE)], out / "view_front.png", 15, 30, "Спереди: сюда выходит рулон")
    scene([(f, BLACK), (sp.translate([90, -20, 0]), ORANGE)], out / "view_bed.png", 35, -50, "Как лежат на столе")
