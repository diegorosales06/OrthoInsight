"""3D arch view: one lower arch, three force arrows per instrumented tooth.

The arch is a parabola in the z = 0 (occlusal) plane; each tooth's crown is an
extruded prism of its occlusal outline. Every tooth that has a load cell mapped
to it grows three arrows from the middle of its occlusal surface -- one per force
component, drawn in that tooth's *own* frame:

    e_x  mesio-distal   (tangent to the arch)
    e_y  bucco-lingual  (outward normal; +y = buccal/labial)
    e_z  occlusal       (+z = up, out of the tooth)

Arrow length is linear in |value| across [lo, hi]: below `lo` the axis draws
nothing, at or above `hi` it clamps to the longest arrow. So a longer arrow is
always more force, and an absent arrow always means "under threshold".

Rendering is a software 3D pipeline (`proj3d`) painted with QPainter -- no
OpenGL, so it behaves the same on the Pi as it does under `--debug` on a
laptop. Visibility comes from back-face culling plus a painter's-algorithm
depth sort over teeth and arrows.
"""

import math
from dataclasses import dataclass, field
from typing import Optional

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel
from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF
from PyQt6.QtGui import (
    QPainter, QPen, QBrush, QColor, QPainterPath, QFont, QPolygonF,
)

from graphDash.constants import REFRESH_MS, FORCE_COLORS, MOMENT_COLORS
from graphDash.ui import theme
from graphDash.ui.proj3d import Camera, vcross, vdot, vunit, vmad


def _qcolor(hex_str):
    return QColor(hex_str)

# Mandibular arch in Universal numbering, ordered left-to-right on screen
# (patient's right on viewer's left, standard occlusal-view convention).
LOWER_ARCH_ORDER = [32, 31, 30, 29, 28, 27, 26, 25, 24, 23, 22, 21, 20, 19, 18, 17]

TOOTH_TYPE = {
    17: 'molar',    18: 'molar',    19: 'molar',
    20: 'premolar', 21: 'premolar',
    22: 'canine',
    23: 'incisor',  24: 'incisor',  25: 'incisor',  26: 'incisor',
    27: 'canine',
    28: 'premolar', 29: 'premolar',
    30: 'molar',    31: 'molar',    32: 'molar',
}

# Display-only Palmer-notation labels (LL# = lower-left quadrant,
# LR# = lower-right quadrant). Internal load-cell mapping still uses
# the Universal numbers in TOOTH_TYPE / tooth_to_cell.
PALMER_LABEL = {
    17: 'LL8', 18: 'LL7', 19: 'LL6', 20: 'LL5',
    21: 'LL4', 22: 'LL3', 23: 'LL2', 24: 'LL1',
    25: 'LR1', 26: 'LR2', 27: 'LR3', 28: 'LR4',
    29: 'LR5', 30: 'LR6', 31: 'LR7', 32: 'LR8',
}

# (width_factor, depth_factor) relative to the arch's base unit. Width drives
# both the drawn crown size AND its footprint along the arc (teeth are placed by
# cumulative arc length so they sit side-by-side, touching); depth is the
# bucco-lingual dimension.
TYPE_SIZE = {
    'molar':    (1.35, 1.20),
    'premolar': (0.90, 0.95),
    'canine':   (0.75, 1.10),
    'incisor':  (0.60, 0.95),
}

# Crown height as a fraction of `unit`, per type -- posterior crowns are drawn
# shorter than anterior ones, as they are clinically.
TYPE_HEIGHT = {
    'molar': 0.75, 'premolar': 0.85, 'canine': 1.15, 'incisor': 1.05,
}

# Small gap between adjacent teeth along the arc, as a fraction of unit.
TOOTH_GAP_FRAC = 0.04


@dataclass(frozen=True)
class GlyphScale:
    """Maps one triple of readings (x, y, z) onto three arrows.

    Magnitudes below `lo` draw nothing for that axis; magnitudes at or above
    `hi` are clamped to the longest arrow.
    """
    lo: float          # N or N*mm
    hi: float          # N or N*mm
    unit_label: str    # e.g. "Force (N)"
    idx: tuple         # (x, y, z) indices into a 6-axis reading
    colors: tuple      # per-axis hex colors, same order as idx
    names: tuple       # per-axis short names, same order as idx


# Reading order is constants.ALL_AXES = (Fx, Fy, Fz, Mx, My, Mz).
FORCE_GLYPH = GlyphScale(
    lo=0.25, hi=3.0, unit_label="Force (N)",
    idx=(0, 1, 2), colors=FORCE_COLORS, names=("Fx", "Fy", "Fz"),
)

# Kept for the moment layer this view will grow later; nothing draws it yet.
MOMENT_GLYPH = GlyphScale(
    lo=0.05, hi=75.0, unit_label="Moment (N·mm)",
    idx=(3, 4, 5), colors=MOMENT_COLORS, names=("Mx", "My", "Mz"),
)


# ---- arch geometry, in world units (the pane fit is applied at projection
# time, so nothing here depends on widget size) ----
ARCH_HALF_WIDTH = 1.0    # x of the terminal molars
ARCH_DEPTH      = 1.25   # y of the terminal molars; the parabola is y = a*x^2
OUTLINE_POINTS  = 18     # verts per crown outline after decimation
# QPainterPath flattens curves with an absolute tolerance, so a crown ~0.2 world
# units wide would collapse to a handful of verts. Build the outline at a much
# larger nominal size and scale the flattened points back down.
FLATTEN_SCALE   = 300.0

# ---- arrow geometry (lengths are multiples of `unit`; widths are px) ----
ARROW_MIN_LEN  = 0.60
ARROW_MAX_LEN  = 1.95
ARROW_HEAD_LEN = 0.30
ARROW_MIN_W    = 1.6
ARROW_MAX_W    = 3.4
ARROW_INSET    = 0.05    # lift the tail off the occlusal surface
ORIGIN_DOT_R   = 0.07
# An arrow aimed at (or away from) the camera has almost no screen length, so
# its projection lies about magnitude. Below this fraction of the length it
# would occupy face-on, draw a ring glyph instead: filled = toward the viewer,
# crossed = away. Same convention the old 2D view used for z.
AXIAL_MIN_FRAC = 0.34
RING_MIN_R     = 0.16    # multiples of `unit`
RING_MAX_R     = 0.30

# Camera presets: (yaw deg, pitch deg).
PRESETS = (
    ("Oblique",  -18.0, 40.0),
    ("Occlusal",   0.0, 89.5),
    ("Anterior",   0.0,  6.0),
)
DEFAULT_PRESET = 0
CAM_DISTANCE = 4.0
FIT_MARGIN = 0.86        # leaves room for arrows, which are not fitted
ZOOM_MIN, ZOOM_MAX = 0.45, 4.0
ORBIT_SENS = 0.008       # rad per pixel of drag

# Fixed pixel width of the painted key column.
KEY_W = 176

# Directional light for crown shading, in world coordinates.
LIGHT_DIR = vunit((0.35, -0.55, 0.78))


def _lerp(a, b, t):
    return a + (b - a) * t


def _frac(value, scale: GlyphScale) -> Optional[float]:
    """Position of |value| within [lo, hi] as 0.0-1.0, or None if below lo."""
    m = abs(value)
    if m < scale.lo:
        return None
    if m >= scale.hi:
        return 1.0
    return (m - scale.lo) / (scale.hi - scale.lo)


def _shade(color: QColor, f: float) -> QColor:
    """Lambert-shade `color`: f = 0 is a deep tint, f = 1 the color itself."""
    t = 0.60 + 0.40 * max(0.0, min(1.0, f))
    return QColor(
        int(color.red() * t), int(color.green() * t), int(color.blue() * t)
    )


# ---- arch construction ----

def _tooth_path(ttype, w, d) -> QPainterPath:
    """Occlusal outline of one crown in its local frame.

    Local axes: +x along the arch (mesio-distal), +y outward (bucco-labial).
    Molars/premolars are soft squircles; canines and incisors are rounded
    pentagons with a subtle cusp on the outer edge.
    """
    path = QPainterPath()
    hw, hh = w / 2, d / 2
    if ttype == 'canine':
        path.moveTo(-hw * 0.88, -hh * 0.95)
        path.quadTo(-hw, -hh * 0.85, -hw, -hh * 0.25)
        path.quadTo(-hw * 0.95, hh * 0.35, -hw * 0.55, hh * 0.75)
        path.quadTo(0, hh * 1.05, hw * 0.55, hh * 0.75)
        path.quadTo(hw * 0.95, hh * 0.35, hw, -hh * 0.25)
        path.quadTo(hw, -hh * 0.85, hw * 0.88, -hh * 0.95)
        path.quadTo(0, -hh * 1.02, -hw * 0.88, -hh * 0.95)
        path.closeSubpath()
    elif ttype == 'incisor':
        path.moveTo(-hw * 0.85, -hh * 0.9)
        path.quadTo(-hw, -hh * 0.75, -hw * 0.95, -hh * 0.1)
        path.quadTo(-hw * 0.85, hh * 0.55, -hw * 0.4, hh * 0.85)
        path.quadTo(0, hh * 1.02, hw * 0.4, hh * 0.85)
        path.quadTo(hw * 0.85, hh * 0.55, hw * 0.95, -hh * 0.1)
        path.quadTo(hw, -hh * 0.75, hw * 0.85, -hh * 0.9)
        path.quadTo(0, -hh * 1.0, -hw * 0.85, -hh * 0.9)
        path.closeSubpath()
    else:
        # Molar (wider, rounder) or premolar (slightly less rounded).
        r = min(w, d) * (0.38 if ttype == 'molar' else 0.32)
        path.addRoundedRect(QRectF(-hw, -hh, w, d), r, r)
    return path


def _outline_points(ttype, w, d):
    """Flatten `_tooth_path` to a decimated list of local (x, y) verts."""
    k = FLATTEN_SCALE
    poly = _tooth_path(ttype, w * k, d * k).toFillPolygon()
    pts = [(pt.x() / k, pt.y() / k) for pt in poly]
    # toFillPolygon repeats the start point to close the ring.
    if len(pts) > 1 and abs(pts[0][0] - pts[-1][0]) < 1e-6 \
            and abs(pts[0][1] - pts[-1][1]) < 1e-6:
        pts.pop()
    if len(pts) <= OUTLINE_POINTS:
        return pts
    step = len(pts) / OUTLINE_POINTS
    return [pts[min(len(pts) - 1, int(i * step))] for i in range(OUTLINE_POINTS)]


def _parabola_arc(a, half_width, n_samples=241):
    """Sample y = a*x^2 over [-half_width, half_width], returning
    (xs, ys, s_cum) with s_cum[i] the arc length from the first sample."""
    step = 2 * half_width / (n_samples - 1)
    xs = [-half_width + i * step for i in range(n_samples)]
    ys = [a * x * x for x in xs]
    s = [0.0]
    for i in range(1, n_samples):
        dx, dy = xs[i] - xs[i - 1], ys[i] - ys[i - 1]
        s.append(s[-1] + math.hypot(dx, dy))
    return xs, ys, s


def _point_at_arc(s_target, xs, ys, s_cum):
    """(x, y) at arc length `s_target` along the sampled parabola."""
    if s_target <= 0:
        return xs[0], ys[0]
    if s_target >= s_cum[-1]:
        return xs[-1], ys[-1]
    lo, hi = 0, len(s_cum) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if s_cum[mid] < s_target:
            lo = mid + 1
        else:
            hi = mid
    span = s_cum[lo] - s_cum[lo - 1]
    f = (s_target - s_cum[lo - 1]) / span if span > 0 else 0.0
    return (xs[lo - 1] + f * (xs[lo] - xs[lo - 1]),
            ys[lo - 1] + f * (ys[lo] - ys[lo - 1]))


@dataclass
class Tooth:
    """One crown as an extruded prism, plus its local sensor frame."""
    number: int
    ttype: str
    center: tuple            # crown base center, world (z = 0)
    e_x: tuple               # mesio-distal
    e_y: tuple               # bucco-lingual, outward
    e_z: tuple               # occlusal, (0, 0, 1)
    height: float
    apex: tuple              # arrow origin: middle of the occlusal surface
    base: list = field(default_factory=list)     # world ring at z = 0
    top: list = field(default_factory=list)      # world ring at z = height
    normals: list = field(default_factory=list)  # outward normal per base edge
    label_anchor: tuple = (0.0, 0.0, 0.0)


def build_arch():
    """Build every tooth of the lower arch in world coordinates.

    Depends only on the module constants, so it runs once per view rather than
    once per frame. Teeth are laid out by cumulative arc length, which keeps
    them touching along the curve regardless of the type mix.
    """
    a = ARCH_DEPTH / (ARCH_HALF_WIDTH ** 2)
    xs, ys, s_cum = _parabola_arc(a, ARCH_HALF_WIDTH)
    arc_length = s_cum[-1]

    total_w = sum(TYPE_SIZE[TOOTH_TYPE[t]][0] for t in LOWER_ARCH_ORDER)
    total = total_w + TOOTH_GAP_FRAC * (len(LOWER_ARCH_ORDER) - 1)
    unit = arc_length / total

    teeth = []
    cursor = 0.0
    for number in LOWER_ARCH_ORDER:
        ttype = TOOTH_TYPE[number]
        w_factor, d_factor = TYPE_SIZE[ttype]
        w, d = unit * w_factor, unit * d_factor
        height = unit * TYPE_HEIGHT[ttype]

        cx, cy = _point_at_arc(cursor + w / 2, xs, ys, s_cum)
        cursor += w + TOOTH_GAP_FRAC * unit

        # Outward (buccal) normal of y = a*x^2: the interior of the U is the
        # y > a*x^2 side, so the outward direction is (2ax, -1).
        e_y = vunit((2 * a * cx, -1.0, 0.0))
        e_z = (0.0, 0.0, 1.0)
        e_x = vcross(e_y, e_z)          # right-handed: e_x x e_y = e_z
        center = (cx, cy, 0.0)

        tooth = Tooth(
            number=number, ttype=ttype, center=center,
            e_x=e_x, e_y=e_y, e_z=e_z, height=height,
            apex=(cx, cy, height),
        )
        for lx, ly in _outline_points(ttype, w, d):
            base_pt = vmad(vmad(center, e_x, lx), e_y, ly)
            tooth.base.append(base_pt)
            tooth.top.append((base_pt[0], base_pt[1], height))

        n = len(tooth.base)
        for i in range(n):
            p0, p1 = tooth.base[i], tooth.base[(i + 1) % n]
            edge = (p1[0] - p0[0], p1[1] - p0[1], 0.0)
            nrm = vunit(vcross(edge, e_z))
            mid = ((p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2, 0.0)
            out = (mid[0] - center[0], mid[1] - center[1], 0.0)
            if vdot(nrm, out) < 0:      # orient outward regardless of winding
                nrm = (-nrm[0], -nrm[1], -nrm[2])
            tooth.normals.append(nrm)

        tooth.label_anchor = vmad(center, e_y, d * 0.5 + unit * 0.45)
        teeth.append(tooth)

    return teeth, unit


class ArchView3D(QWidget):
    """The 3D arch itself: orbiting camera, extruded crowns, force arrows."""

    def __init__(self, store, tooth_to_cell: dict, scale: GlyphScale,
                 title: str = "Lower Arch — Force Components (3D)"):
        super().__init__()
        self.store = store
        self.tooth_to_cell = tooth_to_cell
        self.scale = scale
        self.title = title
        self.teeth, self.unit = build_arch()

        ys = [t.center[1] for t in self.teeth]
        self.camera = Camera(
            target=(0.0, (min(ys) + max(ys)) / 2, self.unit * 0.5),
            distance=CAM_DISTANCE,
        )
        self.zoom = 1.0
        self._drag_pos = None
        self._ppu = 1.0          # px per world unit, refreshed every frame
        self._fwd = (0.0, 0.0, -1.0)
        self.set_preset(DEFAULT_PRESET)

        self.setMinimumSize(460, 420)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    # ---- camera control ----

    def set_preset(self, index):
        _, yaw, pitch = PRESETS[index % len(PRESETS)]
        self.camera.set_orientation(math.radians(yaw), math.radians(pitch))
        self.preset_index = index % len(PRESETS)
        self.update()

    def reset_view(self):
        self.zoom = 1.0
        self.set_preset(DEFAULT_PRESET)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self._drag_pos is None:
            return
        pos = event.position()
        dx = pos.x() - self._drag_pos.x()
        dy = pos.y() - self._drag_pos.y()
        self._drag_pos = pos
        # Drag right spins the arch right; drag down tilts toward occlusal.
        self.camera.orbit(-dx * ORBIT_SENS, dy * ORBIT_SENS)
        self.update()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mouseDoubleClickEvent(self, event):
        self.reset_view()

    def wheelEvent(self, event):
        steps = event.angleDelta().y() / 120.0
        self.zoom = max(ZOOM_MIN, min(ZOOM_MAX, self.zoom * (1.12 ** steps)))
        self.update()

    # ---- data ----

    def _reading_for(self, cell_idx: Optional[int]):
        if cell_idx is None or cell_idx >= self.store.n_cells:
            return None
        return self.store.latest(cell_idx)

    # ---- painting ----

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, _qcolor(theme.SURFACE))

        pane_w = max(120, w - KEY_W)
        top_pad, bottom_pad, side_pad = 58, 22, 18
        avail_w = max(60, pane_w - 2 * side_pad)
        avail_h = max(60, h - top_pad - bottom_pad)

        pr = self.camera.projector()

        # Fit on the static crowns only: the view must not breathe as arrows grow.
        us, vs = [], []
        for t in self.teeth:
            for pt in t.base:
                u, v, _ = pr.project(pt)
                us.append(u); vs.append(v)
            for pt in t.top:
                u, v, _ = pr.project(pt)
                us.append(u); vs.append(v)
        span_u = max(1e-6, max(us) - min(us))
        span_v = max(1e-6, max(vs) - min(vs))
        s = min(avail_w / span_u, avail_h / span_v) * FIT_MARGIN * self.zoom
        cx = side_pad + avail_w / 2 - s * (min(us) + max(us)) / 2
        cy = top_pad + avail_h / 2 + s * (min(vs) + max(vs)) / 2

        def to_screen(u, v):
            return QPointF(cx + s * u, cy - s * v)

        def project(pt):
            u, v, depth = pr.project(pt)
            return QPointF(cx + s * u, cy - s * v), depth

        # Screen px per world unit near the camera target, and the view axis --
        # for the handful of decorations sized or oriented in 2D. `s` is px per
        # *image* unit, and image coordinates are already divided by depth, so
        # the world-unit scale carries the distance back in.
        self._ppu = s / max(1e-6, self.camera.distance)
        self._fwd = pr.fwd

        p.save()
        p.setClipRect(QRectF(0, 0, pane_w, h))
        self._draw_arch_curve(p, project)

        # One drawable per tooth and per arrow, painted far-to-near so arrows
        # behind a crown are hidden by it and arrows in front are not. Rings
        # (arrows aimed along the view axis) are overlays: they mark a tooth's
        # own occlusal surface, so nothing of that tooth may cover them.
        drawables, overlays = [], []
        for tooth in self.teeth:
            cell_idx = self.tooth_to_cell.get(tooth.number)
            vals = self._reading_for(cell_idx)
            mapped = cell_idx is not None
            _, depth = project(tooth.apex)
            drawables.append((depth, lambda p, t=tooth, m=mapped, v=vals is not None:
                              self._draw_tooth(p, t, project, pr, m, v)))
            if vals is not None:
                arrows, rings = self._arrow_drawables(tooth, vals, project, depth)
                drawables.extend(arrows)
                overlays.extend(rings)

        drawables.sort(key=lambda d: -d[0])
        for _, draw in drawables:
            draw(p)
        for draw in overlays:
            draw(p)

        for tooth in self.teeth:
            self._draw_label(p, tooth, project)
        p.restore()

        self._draw_key(p, pane_w, 0, KEY_W, h)
        self._draw_header(p)
        p.end()

    def _draw_header(self, p):
        f = QFont(); f.setPointSize(theme.FONT_SECTION); f.setBold(True)
        p.setFont(f)
        p.setPen(_qcolor(theme.ON_SURFACE))
        p.drawText(18, 26, self.title)

        name = PRESETS[self.preset_index][0]
        f2 = QFont(); f2.setPointSize(theme.FONT_BODY)
        p.setFont(f2)
        p.setPen(_qcolor(theme.ON_SURFACE_MUTED))
        p.drawText(18, 44, f"{name} view · drag to orbit · scroll to zoom · "
                           "dashed = no sensor mapped")

    def _draw_arch_curve(self, p, project):
        """Faint occlusal-plane guide through the crown centers -- the ground
        plane cue that keeps the perspective readable."""
        pts = []
        a = ARCH_DEPTH / (ARCH_HALF_WIDTH ** 2)
        n = 60
        for i in range(n + 1):
            x = -ARCH_HALF_WIDTH + 2 * ARCH_HALF_WIDTH * i / n
            sp, _ = project((x, a * x * x, 0.0))
            pts.append(sp)
        pen = QPen(_qcolor(theme.OUTLINE), 1.0)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolyline(QPolygonF(pts))

    def _draw_tooth(self, p, tooth, project, pr, mapped, live):
        if not mapped:
            # Unmapped teeth stay flat footprints in the occlusal plane, so the
            # instrumented crowns are the only things standing up.
            poly = QPolygonF([project(pt)[0] for pt in tooth.base])
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(_qcolor(theme.OUTLINE_STRONG), 1.1, Qt.PenStyle.DashLine))
            p.drawPolygon(poly)
            return

        base = [project(pt) for pt in tooth.base]
        top = [project(pt) for pt in tooth.top]
        fill = _qcolor(theme.TOOTH_FILL)
        outline = QPen(_qcolor(theme.OUTLINE_STRONG), 1.0)

        # Sides: cull the faces pointing away, then paint the rest back-to-front.
        n = len(base)
        quads = []
        for i in range(n):
            nrm = tooth.normals[i]
            if vdot(nrm, pr.fwd) >= 0:
                continue
            j = (i + 1) % n
            depth = (base[i][1] + base[j][1] + top[i][1] + top[j][1]) / 4
            quads.append((depth, i, j, max(0.0, vdot(nrm, LIGHT_DIR))))
        quads.sort(key=lambda q: -q[0])
        p.setPen(Qt.PenStyle.NoPen)
        for _, i, j, lam in quads:
            p.setBrush(QBrush(_shade(fill, lam)))
            p.drawPolygon(QPolygonF([base[i][0], base[j][0], top[j][0], top[i][0]]))

        # Occlusal face last: with the camera always above the plane it is the
        # nearest face of the prism.
        p.setBrush(QBrush(_shade(fill, vdot(tooth.e_z, LIGHT_DIR))))
        p.setPen(outline)
        p.drawPolygon(QPolygonF([sp for sp, _ in top]))

        # Common origin of the three arrows.
        apex, _ = project(tooth.apex)
        r = max(1.5, self.unit * ORIGIN_DOT_R * self._ppu)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(_qcolor(theme.ON_SURFACE_SUBTLE if not live
                                  else theme.ON_SURFACE_MUTED)))
        p.drawEllipse(apex, r, r)

    def _arrow_drawables(self, tooth, vals, project, apex_depth):
        """Drawables for this tooth's above-threshold force components.

        Returns `(arrows, rings)`. An arrow aimed close to the view axis has
        almost no projected length, so its shaft would lie about magnitude --
        those become ring glyphs, returned separately because they belong on
        top of their own crown.

        Depth keys are clamped to the tooth's own apex so an arrow can never be
        swallowed by the crown it grows out of (an intrusive -Fz points straight
        into the tooth body); other teeth nearer the camera still cover it.
        """
        scale = self.scale
        arrows, rings = [], []
        axes = (
            (vals[scale.idx[0]], tooth.e_x, scale.colors[0]),
            (vals[scale.idx[1]], tooth.e_y, scale.colors[1]),
            (vals[scale.idx[2]], tooth.e_z, scale.colors[2]),
        )
        for value, axis, color in axes:
            f = _frac(value, scale)
            if f is None:
                continue
            sign = 1.0 if value >= 0 else -1.0
            direction = (axis[0] * sign, axis[1] * sign, axis[2] * sign)
            length = self.unit * _lerp(ARROW_MIN_LEN, ARROW_MAX_LEN, f)
            width = _lerp(ARROW_MIN_W, ARROW_MAX_W, f)

            tail_pt, _ = project(vmad(tooth.apex, direction,
                                      self.unit * ARROW_INSET))
            tip_pt, _ = project(vmad(tooth.apex, direction, length))
            span = math.hypot(tip_pt.x() - tail_pt.x(), tip_pt.y() - tail_pt.y())
            if span < AXIAL_MIN_FRAC * length * self._ppu:
                toward = vdot(direction, self._fwd) < 0
                rings.append(lambda p, pt=tail_pt, c=color, wd=width, fr=f,
                             tw=toward:
                             self._draw_axial_ring(p, pt, _qcolor(c), wd, fr, tw))
                continue

            _, mid_depth = project(vmad(tooth.apex, direction, length * 0.5))
            depth = min(mid_depth, apex_depth) - 1e-4
            arrows.append((depth, lambda p, d=direction, L=length, c=color,
                           wd=width:
                           self._draw_arrow(p, project, tooth.apex, d, L,
                                            _qcolor(c), wd)))
        return arrows, rings

    def _draw_arrow(self, p, project, origin, direction, length, color, width):
        """3D arrow: projected shaft plus a screen-space billboarded head."""
        head = min(self.unit * ARROW_HEAD_LEN, length * 0.55)
        inset = self.unit * ARROW_INSET
        tail_pt, _ = project(vmad(origin, direction, inset))
        neck_pt, _ = project(vmad(origin, direction, max(inset, length - head)))
        tip_pt, _ = project(vmad(origin, direction, length))

        pen = QPen(color, width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(tail_pt, neck_pt)

        dx, dy = tip_pt.x() - neck_pt.x(), tip_pt.y() - neck_pt.y()
        seg = math.hypot(dx, dy)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(color))
        if seg < 0.75:
            p.drawEllipse(tip_pt, width * 1.3, width * 1.3)
            return
        px, py = -dy / seg, dx / seg
        half = seg * 0.42
        p.drawPolygon(QPolygonF([
            tip_pt,
            QPointF(neck_pt.x() + px * half, neck_pt.y() + py * half),
            QPointF(neck_pt.x() - px * half, neck_pt.y() - py * half),
        ]))

    def _draw_axial_ring(self, p, center, color, width, frac, toward):
        """Ring sized by magnitude: filled dot = toward the viewer (the arrow
        points out of the screen), cross = away from it."""
        r = self._ppu * self.unit * _lerp(RING_MIN_R, RING_MAX_R, frac)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(color, width))
        p.drawEllipse(center, r, r)
        if toward:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(color))
            p.drawEllipse(center, r * 0.45, r * 0.45)
        else:
            d = r * 0.62
            p.drawLine(QPointF(center.x() - d, center.y() - d),
                       QPointF(center.x() + d, center.y() + d))
            p.drawLine(QPointF(center.x() - d, center.y() + d),
                       QPointF(center.x() + d, center.y() - d))

    def _draw_label(self, p, tooth, project):
        sp, _ = project(tooth.label_anchor)
        f = QFont(); f.setPointSize(theme.FONT_CAPTION); f.setBold(True)
        p.setFont(f)
        mapped = tooth.number in self.tooth_to_cell
        p.setPen(_qcolor(theme.ON_SURFACE if mapped else theme.ON_SURFACE_SUBTLE))
        text = PALMER_LABEL.get(tooth.number, str(tooth.number))
        fm = p.fontMetrics()
        p.drawText(QPointF(sp.x() - fm.horizontalAdvance(text) / 2,
                           sp.y() + fm.height() / 3), text)

    # ---- key column ----

    def _draw_key(self, p, x, y, w, h):
        scale = self.scale
        left = x + 12
        cur = y + 26

        heading = QFont(); heading.setPointSize(theme.FONT_BODY); heading.setBold(True)
        caption = QFont(); caption.setPointSize(theme.FONT_CAPTION)
        note = QFont(); note.setPointSize(theme.FONT_CAPTION); note.setItalic(True)
        mono = QFont("monospace"); mono.setPointSize(theme.FONT_CAPTION)

        p.setFont(heading)
        p.setPen(_qcolor(theme.ON_SURFACE))
        p.drawText(int(left), int(cur), scale.unit_label)
        cur += 20

        # Which arrow is which axis.
        axis_desc = ("mesio-distal", "bucco-lingual", "occlusal")
        for name, color, desc in zip(scale.names, scale.colors, axis_desc):
            self._draw_flat_arrow(p, left, cur, 26, _qcolor(color), 2.2)
            p.setFont(caption)
            p.setPen(_qcolor(theme.ON_SURFACE))
            p.drawText(int(left + 34), int(cur + 4), name)
            p.setPen(_qcolor(theme.ON_SURFACE_MUTED))
            p.drawText(int(left + 34), int(cur + 16), desc)
            cur += 30

        cur += 4
        p.setFont(caption)
        p.setPen(_qcolor(theme.ON_SURFACE_MUTED))
        p.drawText(int(left), int(cur), "Length")
        cur += 14
        fg = _qcolor(theme.ON_SURFACE)
        for value, plen, width in ((scale.lo, 24, ARROW_MIN_W),
                                   (scale.hi, 58, ARROW_MAX_W)):
            self._draw_flat_arrow(p, left, cur, plen, fg, width)
            p.setFont(caption)
            p.setPen(_qcolor(theme.ON_SURFACE))
            p.drawText(int(left + 64), int(cur + 4), f"{value:g}")
            cur += 22

        # Ring glyph: what an arrow becomes when it aims along the view axis.
        cur += 6
        p.setFont(caption)
        p.setPen(_qcolor(theme.ON_SURFACE_MUTED))
        p.drawText(int(left), int(cur), "Along view axis")
        cur += 16
        for toward, text in ((True, "toward you"), (False, "away")):
            self._draw_key_ring(p, left + 8, cur, 7.0, fg, 1.8, toward)
            p.setPen(_qcolor(theme.ON_SURFACE))
            p.drawText(int(left + 24), int(cur + 4), text)
            cur += 20

        cur += 6
        p.setFont(note)
        p.setPen(_qcolor(theme.ON_SURFACE_MUTED))
        p.drawText(int(left), int(cur), f"< {scale.lo:g}: not shown")
        cur += 14
        p.drawText(int(left), int(cur), f"clamped at {scale.hi:g}")
        cur += 14
        p.drawText(int(left), int(cur), "each tooth's own frame")
        cur += 22

        # Live values for the instrumented teeth -- an arrow says "which way",
        # this says "how much".
        if not self.tooth_to_cell:
            p.setFont(note)
            p.setPen(_qcolor(theme.ON_SURFACE_MUTED))
            p.drawText(int(left), int(cur), "no cell mapped to")
            p.drawText(int(left), int(cur + 13), "a lower-arch tooth")
            return

        p.setFont(caption)
        p.setPen(_qcolor(theme.ON_SURFACE_MUTED))
        p.drawText(int(left), int(cur), "Live")
        cur += 14
        for number in sorted(self.tooth_to_cell, key=LOWER_ARCH_ORDER.index):
            if cur > h - 26:
                break
            vals = self._reading_for(self.tooth_to_cell[number])
            p.setFont(mono)
            p.setPen(_qcolor(theme.ON_SURFACE))
            label = PALMER_LABEL.get(number, str(number))
            p.drawText(int(left), int(cur), label)
            if vals is None:
                p.setPen(_qcolor(theme.ON_SURFACE_SUBTLE))
                p.drawText(int(left + 34), int(cur), "--")
            else:
                xoff = 34
                for ai, color in zip(self.scale.idx, self.scale.colors):
                    p.setPen(_qcolor(color))
                    p.drawText(int(left + xoff), int(cur), f"{vals[ai]:+.2f}")
                    xoff += 44
            cur += 14

    @staticmethod
    def _draw_key_ring(p, x, y, r, color, width, toward):
        """Fixed-size copy of the axial ring glyph, for the key."""
        c = QPointF(x, y)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(color, width))
        p.drawEllipse(c, r, r)
        if toward:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(color))
            p.drawEllipse(c, r * 0.45, r * 0.45)
        else:
            d = r * 0.62
            p.drawLine(QPointF(x - d, y - d), QPointF(x + d, y + d))
            p.drawLine(QPointF(x - d, y + d), QPointF(x + d, y - d))

    @staticmethod
    def _draw_flat_arrow(p, x, y, length, color, width):
        """Straight right-pointing arrow in screen space, for the key."""
        head = 8.0
        pen = QPen(color, width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(x, y), QPointF(x + length - head * 0.85, y))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(color))
        bx = x + length - head * 0.85
        p.drawPolygon(QPolygonF([
            QPointF(x + length, y),
            QPointF(bx, y - head * 0.42),
            QPointF(bx, y + head * 0.42),
        ]))


class ArchTab(QWidget):
    """One 3D lower arch with camera presets above it."""

    @staticmethod
    def _build_tooth_to_cell(tooth_per_cell):
        """Universal tooth number -> cell index, for teeth on the lower arch.

        A cell with no `tooth`, or one outside 17-32, simply never appears."""
        mapping = {}
        for cell_idx, tooth in enumerate(tooth_per_cell or []):
            if tooth is None:
                continue
            try:
                number = int(tooth)
            except (TypeError, ValueError):
                continue
            if number in TOOTH_TYPE:
                mapping[number] = cell_idx
        return mapping

    def __init__(self, store, tooth_per_cell=None):
        super().__init__()
        self.store = store
        self.tooth_per_cell = tooth_per_cell or []

        self.view = ArchView3D(
            store,
            self._build_tooth_to_cell(self.tooth_per_cell),
            scale=FORCE_GLYPH,
        )

        bar = QHBoxLayout()
        bar.setContentsMargins(14, 8, 14, 4)
        bar.setSpacing(8)
        label = QLabel("VIEW")
        label.setStyleSheet(
            f"font-size: {theme.FONT_CAPTION}pt; color: {theme.ON_SURFACE_MUTED}; "
            f"font-weight: 700; letter-spacing: 1px; background: transparent;")
        bar.addWidget(label)
        self.preset_buttons = []
        for i, (name, _, _) in enumerate(PRESETS):
            btn = QPushButton(name)
            btn.setMinimumHeight(32)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _checked, idx=i: self._pick_preset(idx))
            bar.addWidget(btn)
            self.preset_buttons.append(btn)
        reset = QPushButton("Reset")
        reset.setMinimumHeight(32)
        reset.setCursor(Qt.CursorShape.PointingHandCursor)
        reset.clicked.connect(self._reset)
        bar.addWidget(reset)
        bar.addStretch()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addLayout(bar)
        root.addWidget(self.view, 1)

        self._highlight_preset(DEFAULT_PRESET)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(REFRESH_MS)

    def _pick_preset(self, index):
        self.view.set_preset(index)
        self._highlight_preset(index)

    def _reset(self):
        self.view.reset_view()
        self._highlight_preset(DEFAULT_PRESET)

    def _highlight_preset(self, index):
        for i, btn in enumerate(self.preset_buttons):
            btn.setProperty("variant", "primary" if i == index else "")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def set_tooth_per_cell(self, tooth_per_cell):
        """Re-map which load cell drives each tooth, so a live sensor-config
        change is reflected in the arrows. The view reads force values from the
        DataStore, which already holds post-tare, post-compensation readings."""
        self.tooth_per_cell = list(tooth_per_cell or [])
        self.view.tooth_to_cell = self._build_tooth_to_cell(self.tooth_per_cell)
        self.view.update()

    def _refresh(self):
        if not self.isVisible():
            return
        self.view.update()
