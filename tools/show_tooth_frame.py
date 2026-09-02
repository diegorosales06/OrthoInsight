"""Look at what `tooth_frames.py` measured: the boxes and the axes, on the mesh.

    # one tooth, interactive
    python3 tools/show_tooth_frame.py --stl-dir assets_src --tooth LR6

    # a PNG instead of a window (works headless)
    python3 tools/show_tooth_frame.py --stl-dir assets_src --tooth LR1 \\
        --out /tmp/LR1.png

Runs on a laptop, never on the Pi: it needs `trimesh` and `numpy` through
`tooth_frames`, plus PyQt6 to draw. It writes no asset -- this is a pair of eyes
on the numbers `tooth_frames.py` prints, nothing more, and it computes no
geometry of its own: the frame comes from `tooth_frames()` and the crown box
from that module's own `crown_slab()` / `box_in_frame()`, so the picture cannot
drift from the table.

What you are looking at, one tooth at a time:

  * the scanned tooth, shaded, in the same world space as `arch_mesh.json`
  * **amber dashed wireframe** -- the whole-tooth oriented bounding box, the
    minimum-volume box whose face normals *are* the tooth's axes
  * **green wireframe** -- the crown slab's box, the top `--crown-frac` of the
    tooth measured along its own axis; its centre is `ToothFrame.center`, where
    the arrows will start
  * **x red, y green, z blue** -- the frame itself, drawn from that crown
    centre: mesio-distal, buccal, occlusal
  * a hollow amber dot at the whole-tooth box centre, which sits mid-root and is
    exactly why the origin is taken from the crown instead

The two failure modes the table warns about are both visible here at a glance: a
box that latched onto the wrong face sits visibly askew to the tooth, and a
swapped e_x/e_y shows up as red and green pointing along the wrong extents.

Drag to orbit, wheel to zoom, double-click to reset, left/right arrows to step
through the arch, Q to quit. Frames and boxes always come from the **full**
mesh; `--faces` only thins what is drawn, so dragging stays responsive.
"""

import argparse
import math
import os
import sys
from dataclasses import dataclass

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

from PyQt6.QtWidgets import QApplication, QWidget
from PyQt6.QtCore import Qt, QPointF, QRectF
from PyQt6.QtGui import QPainter, QPen, QBrush, QColor, QFont

from bake_arch_mesh import DEFAULT_MANIFEST, discover, load_and_orient
from tooth_frames import (CROWN_FRAC, box_in_frame, crown_slab, read_manifest,
                          tooth_frames)
from graphDash.ui.arch_model import LOWER_ARCH_ORDER, normalize_tooth
from graphDash.ui.arch_tab import _paint_arrow
from graphDash.ui.proj3d import Camera, vadd, vscale, vdot, vunit

# Debug-tool chrome, deliberately not `theme.py`: nothing here ships.
BG          = QColor("#12161c")
TEXT        = QColor("#d7dee8")
DIM         = QColor("#7d8896")
TOOTH       = QColor("#cfc8b8")
BOX_FULL    = QColor("#e8a838")   # whole-tooth oriented box
BOX_CROWN   = QColor("#57c785")   # the crown slab
AXIS_COLORS = (QColor("#e2564a"), QColor("#4fb96a"), QColor("#4d94e8"))
AXIS_LABELS = ("x", "y", "z")

# Axis arrows are a fraction of the whole-tooth box, so a molar and an incisor
# each get an arrow that reads against their own size.
AXIS_LEN_FRAC = 0.75
FIT_MARGIN = 0.80        # room for the arrow labels, which are not fitted
CAM_DIST_FRAC = 9.0      # eye distance as a multiple of the tooth's long axis
ZOOM_MIN, ZOOM_MAX = 0.3, 6.0
ORBIT_SENS = 0.008
SHADE_LEVELS = 24
LIGHT_DIR = vunit((0.35, -0.55, 0.78))

# Corner i packs the three signs as bits, so two corners share an edge exactly
# when their indices differ in one bit.
BOX_EDGES = tuple((i, i ^ bit) for i in range(8) for bit in (1, 2, 4)
                  if not i & bit)


@dataclass(frozen=True)
class _Tooth:
    """One tooth ready to draw: the measured frame plus a mesh to hang it on."""
    frame: object            # tooth_frames.ToothFrame
    crown_extents: tuple     # (md, bl, occlusal) of the crown slab
    verts: list              # world-space vertex tuples (possibly decimated)
    tris: list               # (i, j, k, outward normal)


def box_corners(center, frame, extents):
    """The 8 corners of a box centred at `center` and aligned to `frame`."""
    half = [0.5 * e for e in extents]
    out = []
    for i in range(8):
        p = center
        for axis in range(3):
            s = 1.0 if i & (1 << axis) else -1.0
            p = vadd(p, vscale(frame[axis], s * half[axis]))
        out.append(p)
    return out


def load_scene(stl_dir, manifest_path, crown_frac, faces):
    """(arch-ordered palmer list, {palmer: _Tooth}).

    The meshes are loaded a second time rather than threaded out of
    `tooth_frames()`, which returns frames alone. Sixteen STLs cost about half a
    second, and the alternative -- rebuilding the frames here from the meshes --
    is the duplication that actually matters.
    """
    import numpy as np

    manifest = read_manifest(manifest_path)
    frames = tooth_frames(stl_dir, manifest, crown_frac)
    meshes = load_and_orient(discover(stl_dir, manifest.get("teeth", {})),
                             manifest)

    scene = {}
    for number, f in frames.items():
        m = meshes[number]
        points = [(float(p[0]), float(p[1]), float(p[2])) for p in m.vertices]
        slab, _cut = crown_slab(points, f.frame[2], crown_frac)
        _center, crown_extents = box_in_frame(slab, f.frame)

        m = m.copy()
        if faces and len(m.faces) > faces:
            try:
                m = m.simplify_quadric_decimation(face_count=faces)
            except TypeError:              # older trimesh took it positionally
                m = m.simplify_quadric_decimation(faces)
        # The back-face cull below trusts the winding. These scans are
        # watertight, so this is cheap insurance against an inside-out shell.
        m.fix_normals()

        scene[number] = _Tooth(
            frame=f, crown_extents=crown_extents,
            verts=[tuple(float(c) for c in v) for v in np.asarray(m.vertices)],
            tris=[(int(a), int(b), int(c), tuple(float(x) for x in n))
                  for (a, b, c), n in zip(np.asarray(m.faces),
                                          np.asarray(m.face_normals))])
    return [n for n in LOWER_ARCH_ORDER if n in scene], scene


class ToothFrameView(QWidget):
    """One tooth, its two boxes and its three axes, orbited by hand."""

    def __init__(self, order, scene, palmer, crown_frac):
        super().__init__()
        self.order = order
        self.scene = scene
        self.crown_frac = crown_frac
        self.zoom = 1.0
        self._drag = None
        self._shades = [
            QColor(int(TOOTH.red() * t), int(TOOTH.green() * t),
                   int(TOOTH.blue() * t))
            for t in (0.42 + 0.58 * i / (SHADE_LEVELS - 1)
                      for i in range(SHADE_LEVELS))]
        self.setMinimumSize(760, 640)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.select(palmer)

    # ---- the tooth on show ----

    def select(self, palmer):
        self.palmer = palmer
        self.tooth = t = self.scene[palmer]
        f = t.frame
        self.axis_len = AXIS_LEN_FRAC * max(f.extents)
        self.full_box = box_corners(f.box_center, f.frame, f.extents)
        self.crown_box = box_corners(f.center, f.frame, t.crown_extents)
        self.camera = Camera(target=f.center,
                             distance=CAM_DIST_FRAC * max(f.extents),
                             yaw=math.radians(-25.0), pitch=math.radians(18.0))
        self.setWindowTitle(f"{palmer} -- tooth frame")
        self.update()

    def step(self, delta):
        i = self.order.index(self.palmer)
        self.select(self.order[(i + delta) % len(self.order)])

    # ---- camera controls ----

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.position()

    def mouseMoveEvent(self, e):
        if self._drag is None:
            return
        d = e.position() - self._drag
        self._drag = e.position()
        self.camera.orbit(-d.x() * ORBIT_SENS, d.y() * ORBIT_SENS)
        self.update()

    def mouseReleaseEvent(self, e):
        self._drag = None

    def mouseDoubleClickEvent(self, e):
        self.zoom = 1.0
        self.select(self.palmer)

    def wheelEvent(self, e):
        step = 1.0 + 0.0016 * e.angleDelta().y()
        self.zoom = max(ZOOM_MIN, min(ZOOM_MAX, self.zoom * step))
        self.update()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Right, Qt.Key.Key_Down):
            self.step(1)
        elif e.key() in (Qt.Key.Key_Left, Qt.Key.Key_Up):
            self.step(-1)
        elif e.key() in (Qt.Key.Key_Q, Qt.Key.Key_Escape):
            self.close()

    # ---- painting ----

    def _fit(self, rect):
        """Projector plus the world->screen scale and offset for this frame.

        Everything drawn is fitted, arrows included -- unlike the arch view,
        whose arrows change length with the readings. These are pinned to the
        tooth, so letting them set the frame just keeps them on screen.
        """
        pr = self.camera.projector()
        f = self.tooth.frame
        pts = list(self.tooth.verts) + self.full_box + self.crown_box
        pts += [vadd(f.center, vscale(a, self.axis_len)) for a in f.frame]
        us, vs = [], []
        for pt in pts:
            u, v, _ = pr.project(pt)
            us.append(u)
            vs.append(v)
        s = (min(rect.width() / max(1e-6, max(us) - min(us)),
                 rect.height() / max(1e-6, max(vs) - min(vs)))
             * FIT_MARGIN * self.zoom)
        return (pr, s,
                rect.center().x() - s * (min(us) + max(us)) / 2,
                rect.center().y() + s * (min(vs) + max(vs)) / 2)

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), BG)
        pr, s, cx, cy = self._fit(QRectF(self.rect()).adjusted(18, 92, -18, -60))

        def place(pt):
            u, v, depth = pr.project(pt)
            return QPointF(cx + s * u, cy - s * v), depth

        # The tooth: project each vertex once, cull back faces, paint far to
        # near. Same shape of pipeline as the arch view, minus its shade table.
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        screen = [place(v) for v in self.tooth.verts]
        faces = []
        for i, j, k, n in self.tooth.tris:
            if vdot(n, pr.fwd) >= 0:
                continue
            lit = max(0.0, vdot(n, LIGHT_DIR))
            faces.append(((screen[i][1] + screen[j][1] + screen[k][1]) / 3.0,
                          i, j, k, self._shades[int(lit * (SHADE_LEVELS - 1))]))
        faces.sort(key=lambda tri: -tri[0])
        for _depth, i, j, k, color in faces:
            p.setPen(QPen(color, 1.0))
            p.setBrush(QBrush(color))
            p.drawPolygon(screen[i][0], screen[j][0], screen[k][0])

        # Boxes and axes ride on top: they are annotations, not geometry, and a
        # wireframe buried inside a solid tooth tells you nothing.
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._draw_box(p, place, self.full_box, BOX_FULL, 1.4, dashed=True)
        self._draw_box(p, place, self.crown_box, BOX_CROWN, 1.8)

        f = self.tooth.frame
        origin = place(f.center)[0]
        p.setPen(QPen(BOX_FULL, 1.4))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(place(f.box_center)[0], 4.5, 4.5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(BOX_CROWN))
        p.drawEllipse(origin, 3.5, 3.5)

        p.setFont(QFont("Menlo", 11, QFont.Weight.Bold))
        for axis, color, label in zip(f.frame, AXIS_COLORS, AXIS_LABELS):
            tip = place(vadd(f.center, vscale(axis, self.axis_len)))[0]
            neck = place(vadd(f.center, vscale(axis, self.axis_len * 0.80)))[0]
            _paint_arrow(p, origin, neck, tip, color, 2.4)
            p.setPen(QPen(color))
            p.drawText(tip + QPointF(8, -6), label)

        self._draw_key(p)
        p.end()

    def _draw_box(self, p, place, corners, color, width, dashed=False):
        pen = QPen(color, width)
        if dashed:
            pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        pts = [place(c)[0] for c in corners]
        for a, b in BOX_EDGES:
            p.drawLine(pts[a], pts[b])

    def _draw_key(self, p):
        f = self.tooth.frame
        vec = lambda v: f"({v[0]:+.3f}, {v[1]:+.3f}, {v[2]:+.3f})"

        p.setFont(QFont("Menlo", 13, QFont.Weight.Bold))
        p.setPen(QPen(TEXT))
        p.drawText(20, 30, f"{f.palmer}   {f.ttype}")

        p.setFont(QFont("Menlo", 9))
        p.setPen(QPen(DIM))
        p.drawText(20, 50, f"tilt {f.tilt_deg:5.1f}d   yaw {f.yaw_deg:5.1f}d   "
                           f"md/bl/occ {f.extents[0]:.2f}/{f.extents[1]:.2f}/"
                           f"{f.extents[2]:.2f}   crown "
                           f"{self.crown_extents_str()}   cut {f.cut:+.3f}")
        p.drawText(20, 64, f"crown centre {vec(f.center)}      "
                           f"box centre {vec(f.box_center)}")
        p.drawText(20, 78, f"e_x {vec(f.frame[0])}  e_y {vec(f.frame[1])}  "
                           f"e_z {vec(f.frame[2])}")

        y = self.height() - 44
        for label, color, dash in (
                ("whole-tooth oriented box (axes come from here)", BOX_FULL, True),
                (f"crown box, top {self.crown_frac:g} (centre = arrow origin)",
                 BOX_CROWN, False)):
            pen = QPen(color, 2.0)
            if dash:
                pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawLine(QPointF(20, y - 4), QPointF(46, y - 4))
            p.setPen(QPen(DIM))
            p.drawText(54, y, label)
            y += 15
        p.setPen(QPen(DIM))
        p.drawText(20, self.height() - 8,
                   f"camera yaw {math.degrees(self.camera.yaw) % 360:.0f}d  "
                   f"pitch {math.degrees(self.camera.pitch):.0f}d  "
                   f"zoom {self.zoom:.2f}x    "
                   "drag orbit / wheel zoom / double-click reset / "
                   "left-right tooth / Q quit")

    def crown_extents_str(self):
        return "/".join(f"{e:.2f}" for e in self.tooth.crown_extents)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stl-dir", default="assets_src",
                    help="directory of per-tooth STLs (default assets_src)")
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST,
                    help="filename->tooth overrides and orientation fixes")
    ap.add_argument("--tooth", default="LR6",
                    help="Palmer designation to show first (default LR6)")
    ap.add_argument("--crown-frac", type=float, default=CROWN_FRAC,
                    help="passed straight through to tooth_frames "
                         f"(default {CROWN_FRAC})")
    ap.add_argument("--faces", type=int, default=4000,
                    help="thin each tooth to this many triangles *for drawing* "
                         "only; the frames and boxes always come from the full "
                         "mesh (0 = draw every triangle). Measured on the dev "
                         "laptop: 4000 tris paints in 11 ms, a full 13.5k-tri "
                         "scan in 32 ms")
    ap.add_argument("--out", metavar="PNG",
                    help="save one frame here instead of opening a window")
    ap.add_argument("--yaw", type=float, default=-25.0,
                    help="camera yaw in degrees, for --out")
    ap.add_argument("--pitch", type=float, default=18.0,
                    help="camera pitch in degrees, for --out")
    ap.add_argument("--size", default="1200x900", help="--out image size, WxH")
    args = ap.parse_args()

    palmer = normalize_tooth(args.tooth)
    if palmer is None:
        raise SystemExit(f"{args.tooth!r} is not a lower tooth "
                         "(expected a Palmer designation, e.g. LR6)")

    app = QApplication(sys.argv)
    order, scene = load_scene(args.stl_dir, args.manifest, args.crown_frac,
                              args.faces)
    if palmer not in scene:
        raise SystemExit(f"no mesh for {palmer} in {args.stl_dir}; "
                         f"have {', '.join(order)}")

    view = ToothFrameView(order, scene, palmer, args.crown_frac)
    if args.out:
        w, h = (int(v) for v in args.size.lower().split("x"))
        view.resize(w, h)
        view.camera.set_orientation(math.radians(args.yaw),
                                    math.radians(args.pitch))
        view.grab().save(args.out)
        print(f"  wrote {args.out}")
        return 0

    view.show()
    view.raise_()
    view.activateWindow()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
