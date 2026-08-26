"""The lower dental arch as data: crown geometry and the reading-to-arrow scale.

This module knows what a lower arch *is*. It knows nothing about cameras,
widgets, painting, or which load cell is attached to what -- `arch_tab.py` owns
all of that. Everything here is static, so `build_arch()` runs once per view
rather than once per frame.

World frame is `proj3d`'s: +x viewer's right in the occlusal view, +y posterior,
+z occlusal. The arch is a parabola `y = a*x^2` in the z = 0 plane with its
vertex at the anterior, and crowns extrude toward +z.

Each tooth carries its own orthonormal frame, which is what the force arrows are
drawn along:

    e_x  mesio-distal   (tangent to the arch)
    e_y  bucco-lingual  (outward normal; +y = buccal/labial)
    e_z  occlusal       (+z = up, out of the tooth)
"""

import math
from dataclasses import dataclass
from typing import Optional

from PyQt6.QtCore import QRectF
from PyQt6.QtGui import QPainterPath

from graphDash.constants import FORCE_COLORS, MOMENT_COLORS
from graphDash.ui.proj3d import vcross, vdot, vunit, vmad

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

ARCH_HALF_WIDTH = 1.0    # x of the terminal molars
ARCH_DEPTH      = 1.25   # y of the terminal molars; the parabola is y = a*x^2
OUTLINE_POINTS  = 18     # verts per crown outline after decimation
GUIDE_SAMPLES   = 61     # polyline resolution of the occlusal-plane guide curve
# QPainterPath flattens curves with an absolute tolerance, so a crown ~0.2 world
# units wide would collapse to a handful of verts. Build the outline at a much
# larger nominal size and scale the flattened points back down.
FLATTEN_SCALE   = 300.0


# ---- how a reading becomes an arrow ----

@dataclass(frozen=True)
class AxisSpec:
    """One force/moment component: where to read it and how to present it."""
    name: str          # "Fx"
    index: int         # into a 6-axis reading
    color: str         # hex, from constants.FORCE_COLORS / MOMENT_COLORS
    description: str   # anatomical direction, for the key


@dataclass(frozen=True)
class GlyphScale:
    """Maps one triple of readings onto three arrows.

    `axes` is ordered to match `Tooth.frame`, so axes[i] is drawn along the
    tooth's i-th basis vector. Magnitudes below `lo` draw nothing for that axis;
    magnitudes at or above `hi` clamp to the longest arrow.
    """
    lo: float          # N or N*mm
    hi: float          # N or N*mm
    unit_label: str    # e.g. "Force (N)"
    axes: tuple        # three AxisSpec, in Tooth.frame order

    def frac(self, value) -> Optional[float]:
        """Position of |value| within [lo, hi] as 0.0-1.0, or None if below lo."""
        m = abs(value)
        if m < self.lo:
            return None
        if m >= self.hi:
            return 1.0
        return (m - self.lo) / (self.hi - self.lo)


# Reading order is constants.ALL_AXES = (Fx, Fy, Fz, Mx, My, Mz).
FORCE_GLYPH = GlyphScale(
    lo=0.25, hi=3.0, unit_label="Force (N)",
    axes=(
        AxisSpec("Fx", 0, FORCE_COLORS[0], "mesio-distal"),
        AxisSpec("Fy", 1, FORCE_COLORS[1], "bucco-lingual"),
        AxisSpec("Fz", 2, FORCE_COLORS[2], "occlusal"),
    ),
)

# Kept for the moment layer this view will grow later; nothing draws it yet.
MOMENT_GLYPH = GlyphScale(
    lo=0.05, hi=75.0, unit_label="Moment (N·mm)",
    axes=(
        AxisSpec("Mx", 3, MOMENT_COLORS[0], "mesio-distal"),
        AxisSpec("My", 4, MOMENT_COLORS[1], "bucco-lingual"),
        AxisSpec("Mz", 5, MOMENT_COLORS[2], "occlusal"),
    ),
)


# ---- crown outlines ----

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


# ---- arc-length placement along the parabola ----

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


# ---- the arch ----

@dataclass(frozen=True)
class Tooth:
    """One crown as an extruded prism, plus its local sensor frame."""
    number: int
    ttype: str
    center: tuple          # crown base center, world (z = 0)
    frame: tuple           # (e_x, e_y, e_z) unit vectors -- arrow directions
    height: float
    apex: tuple            # arrow origin: middle of the occlusal surface
    base: tuple            # world ring at z = 0
    top: tuple             # world ring at z = height
    normals: tuple         # outward world normal per base edge
    label_anchor: tuple    # buccal of the crown, clear of the arrows

    @property
    def e_z(self):
        return self.frame[2]

    def edges(self):
        """(i, j, outward_normal) for each side face of the prism."""
        n = len(self.base)
        return ((i, (i + 1) % n, self.normals[i]) for i in range(n))


@dataclass(frozen=True)
class Arch:
    """Every crown of the lower arch, plus the scale everything is built on."""
    teeth: tuple
    unit: float            # base size unit; all glyph geometry is a multiple
    guide: tuple           # occlusal-plane polyline through the crown centers

    def vertices(self):
        """Every crown vertex -- what the view fits the pane to. Arrows are
        deliberately excluded, or the view would breathe as forces grew."""
        for tooth in self.teeth:
            yield from tooth.base
            yield from tooth.top


def _crown(number, cx, cy, a, unit):
    """Build one Tooth at (cx, cy) on a parabola of coefficient `a`."""
    ttype = TOOTH_TYPE[number]
    w_factor, d_factor = TYPE_SIZE[ttype]
    w, d = unit * w_factor, unit * d_factor
    height = unit * TYPE_HEIGHT[ttype]
    center = (cx, cy, 0.0)

    # Outward (buccal) normal of y = a*x^2: the interior of the U is the
    # y > a*x^2 side, so the outward direction is (2ax, -1).
    e_y = vunit((2 * a * cx, -1.0, 0.0))
    e_z = (0.0, 0.0, 1.0)
    e_x = vcross(e_y, e_z)              # right-handed: e_x x e_y = e_z

    base, top = [], []
    for lx, ly in _outline_points(ttype, w, d):
        pt = vmad(vmad(center, e_x, lx), e_y, ly)
        base.append(pt)
        top.append((pt[0], pt[1], height))

    normals = []
    for i in range(len(base)):
        p0, p1 = base[i], base[(i + 1) % len(base)]
        nrm = vunit(vcross((p1[0] - p0[0], p1[1] - p0[1], 0.0), e_z))
        out = ((p0[0] + p1[0]) / 2 - cx, (p0[1] + p1[1]) / 2 - cy, 0.0)
        if vdot(nrm, out) < 0:          # orient outward regardless of winding
            nrm = (-nrm[0], -nrm[1], -nrm[2])
        normals.append(nrm)

    return Tooth(
        number=number, ttype=ttype, center=center,
        frame=(e_x, e_y, e_z), height=height, apex=(cx, cy, height),
        base=tuple(base), top=tuple(top), normals=tuple(normals),
        label_anchor=vmad(center, e_y, d * 0.5 + unit * 0.45),
    )


def build_arch() -> Arch:
    """Lay the whole lower arch out in world coordinates.

    Teeth are placed by cumulative arc length, which keeps them touching along
    the curve regardless of the type mix, and sets the base `unit` that every
    crown, arrow, and ring is sized from.
    """
    a = ARCH_DEPTH / (ARCH_HALF_WIDTH ** 2)
    xs, ys, s_cum = _parabola_arc(a, ARCH_HALF_WIDTH)

    total_w = sum(TYPE_SIZE[TOOTH_TYPE[t]][0] for t in LOWER_ARCH_ORDER)
    unit = s_cum[-1] / (total_w + TOOTH_GAP_FRAC * (len(LOWER_ARCH_ORDER) - 1))

    teeth, cursor = [], 0.0
    for number in LOWER_ARCH_ORDER:
        w = unit * TYPE_SIZE[TOOTH_TYPE[number]][0]
        cx, cy = _point_at_arc(cursor + w / 2, xs, ys, s_cum)
        teeth.append(_crown(number, cx, cy, a, unit))
        cursor += w + TOOTH_GAP_FRAC * unit

    step = 2 * ARCH_HALF_WIDTH / (GUIDE_SAMPLES - 1)
    guide = tuple(
        (-ARCH_HALF_WIDTH + i * step, a * (-ARCH_HALF_WIDTH + i * step) ** 2, 0.0)
        for i in range(GUIDE_SAMPLES)
    )
    return Arch(teeth=tuple(teeth), unit=unit, guide=guide)
