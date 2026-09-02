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
import os
from dataclasses import dataclass
from typing import Optional

from PyQt6.QtCore import QRectF
from PyQt6.QtGui import QPainterPath

from graphDash.constants import FORCE_COLORS, MOMENT_COLORS, RESULTANT_COLOR
from graphDash.ui import arch_asset
from graphDash.ui.proj3d import vcross, vdot, vunit, vmad

# Teeth are identified by their **Palmer** designation everywhere: in
# `sensors.yaml`, in the arch view's labels, in the baked mesh asset, and in the
# STL filenames the baker reads. One notation end to end means the label on
# screen, the line in the config and the name of the scan file are the same
# string, with nothing to translate and nothing to get backwards.
#
# `LL#` is the lower-left quadrant and `LR#` the lower-right, each numbered 1
# (central incisor) outward to 8 (third molar). The mandibular arch has no other
# teeth, so these sixteen are the whole vocabulary.
#
# Ordered left-to-right on screen -- the patient's right sits on the viewer's
# left, the standard occlusal-view convention -- so the lower-right quadrant
# comes first, counting inward.
LOWER_ARCH_ORDER = ['LR8', 'LR7', 'LR6', 'LR5', 'LR4', 'LR3', 'LR2', 'LR1',
                    'LL1', 'LL2', 'LL3', 'LL4', 'LL5', 'LL6', 'LL7', 'LL8']

TOOTH_TYPE = {
    'LR8': 'molar',    'LR7': 'molar',    'LR6': 'molar',
    'LR5': 'premolar', 'LR4': 'premolar',
    'LR3': 'canine',
    'LR2': 'incisor',  'LR1': 'incisor',
    'LL1': 'incisor',  'LL2': 'incisor',
    'LL3': 'canine',
    'LL4': 'premolar', 'LL5': 'premolar',
    'LL6': 'molar',    'LL7': 'molar',    'LL8': 'molar',
}

# Universal numbering was the identifier before the move to Palmer. Kept only so
# a `sensors.yaml` or a mesh asset written back then still loads -- see
# `normalize_tooth()`. Nothing writes Universal any more.
_UNIVERSAL_TO_PALMER = {
    17: 'LL8', 18: 'LL7', 19: 'LL6', 20: 'LL5',
    21: 'LL4', 22: 'LL3', 23: 'LL2', 24: 'LL1',
    25: 'LR1', 26: 'LR2', 27: 'LR3', 28: 'LR4',
    29: 'LR5', 30: 'LR6', 31: 'LR7', 32: 'LR8',
}


def normalize_tooth(value):
    """A Palmer designation for `value`, or None if it names no lower tooth.

    The single gate every tooth identifier passes through, so config files, the
    sensor-config editor and the mesh asset all agree on what counts. Accepts
    any case and surrounding whitespace, and still understands a bare Universal
    number from a config written before the migration.

    Returns None rather than raising: an unset or upper-arch tooth is a normal
    thing for a cell to have, and it simply never appears on the lower arch.
    """
    if value is None:
        return None
    if isinstance(value, bool):           # bool is an int; nothing names a tooth
        return None
    if isinstance(value, int):
        return _UNIVERSAL_TO_PALMER.get(value)

    text = str(value).strip().upper().replace(" ", "")
    if text in TOOTH_TYPE:
        return text
    if text.isdigit():                    # "31" from a pre-migration YAML
        return _UNIVERSAL_TO_PALMER.get(int(text))
    return None

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
class ResultantSpec:
    """The vector sum of a scale's three axes, drawn as one arrow.

    It shares the scale's `lo` -- below that, nothing is drawn at all -- but
    clamps at its own, larger `hi`: a resultant reaches up to sqrt(3) times a
    single component, so reusing the component `hi` would peg it at full length
    most of the time.
    """
    name: str          # "|F|"
    color: str         # hex, from constants.RESULTANT_COLOR
    description: str   # for the key
    hi: float          # N or N*mm


@dataclass(frozen=True)
class GlyphScale:
    """Maps one triple of readings onto three arrows, or onto their resultant.

    `axes` is ordered to match `Tooth.frame`, so axes[i] is drawn along the
    tooth's i-th basis vector. Magnitudes below `lo` draw nothing for that axis;
    magnitudes at or above `hi` clamp to the longest arrow.
    """
    lo: float          # N or N*mm
    hi: float          # N or N*mm
    quantity: str      # "Force" / "Moment", for the view's header
    unit_label: str    # e.g. "Force (N)"
    axes: tuple        # three AxisSpec, in Tooth.frame order
    resultant: ResultantSpec

    def frac(self, value) -> Optional[float]:
        """Position of |value| within [lo, hi] as 0.0-1.0, or None if below lo."""
        return self._ramp(value, self.hi)

    def resultant_frac(self, magnitude) -> Optional[float]:
        """Same ramp, but clamped at the resultant's own, larger `hi`."""
        return self._ramp(magnitude, self.resultant.hi)

    def _ramp(self, value, hi) -> Optional[float]:
        m = abs(value)
        if m < self.lo:
            return None
        if m >= hi:
            return 1.0
        return (m - self.lo) / (hi - self.lo)


# Reading order is constants.ALL_AXES = (Fx, Fy, Fz, Mx, My, Mz).
FORCE_GLYPH = GlyphScale(
    lo=0.25, hi=3.0, quantity="Force", unit_label="Force (N)",
    axes=(
        AxisSpec("Fx", 0, FORCE_COLORS[0], "mesio-distal"),
        AxisSpec("Fy", 1, FORCE_COLORS[1], "bucco-lingual"),
        AxisSpec("Fz", 2, FORCE_COLORS[2], "occlusal"),
    ),
    resultant=ResultantSpec("|F|", RESULTANT_COLOR, "resultant force", hi=5.0),
)

MOMENT_GLYPH = GlyphScale(
    lo=0.05, hi=75.0, quantity="Moment", unit_label="Moment (N·mm)",
    axes=(
        AxisSpec("Mx", 3, MOMENT_COLORS[0], "mesio-distal"),
        AxisSpec("My", 4, MOMENT_COLORS[1], "bucco-lingual"),
        AxisSpec("Mz", 5, MOMENT_COLORS[2], "occlusal"),
    ),
    resultant=ResultantSpec("|M|", RESULTANT_COLOR, "resultant moment", hi=130.0),
)


# ---- the retired procedural arch ----
#
# Prism geometry, kept only to regenerate the committed test fixture (see
# `build_procedural_arch`). Nothing in the running view reaches this code.

@dataclass(frozen=True)
class _ProceduralTooth:
    """A crown as an extruded prism: two outline rings and their side normals."""
    palmer: str
    ttype: str
    center: tuple
    frame: tuple
    height: float
    apex: tuple
    base: tuple
    top: tuple
    normals: tuple
    label_anchor: tuple

    @property
    def e_z(self):
        return self.frame[2]

    def edges(self):
        """(i, j, outward_normal) for each side face of the prism."""
        n = len(self.base)
        return ((i, (i + 1) % n, self.normals[i]) for i in range(n))


@dataclass(frozen=True)
class _ProceduralArch:
    teeth: tuple
    unit: float
    guide: tuple


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
    """One crown as a triangle mesh, plus its local sensor frame.

    The mesh comes from a scan, baked to a fixed triangle budget by
    `tools/bake_arch_mesh.py`; nothing here computes geometry. `verts` are world
    points and `tris` are `(i, j, k, outward_normal)`, normals baked so the
    renderer's per-frame back-face cull is a dot product and nothing more.
    """
    palmer: str            # Palmer designation, e.g. 'LR5' -- the tooth's identity
    ttype: str
    center: tuple          # crown centre, world (z = 0)
    frame: tuple           # (e_x, e_y, e_z) unit vectors -- arrow directions
    height: float
    apex: tuple            # arrow origin: middle of the occlusal surface
    verts: tuple           # unique world vertices
    tris: tuple            # (i, j, k, outward unit normal) per triangle
    silhouette: tuple      # flat z = 0 outline, drawn when no cell is mapped
    label_anchor: tuple    # buccal of the crown, clear of the arrows

    @property
    def e_z(self):
        return self.frame[2]


@dataclass(frozen=True)
class Arch:
    """Every crown of the lower arch, plus the scale everything is built on."""
    teeth: tuple
    unit: float            # base size unit; all glyph geometry is a multiple
    guide: tuple           # occlusal-plane polyline through the crown centers
    fit_hull: tuple        # the point set the view fits the pane to

    def vertices(self):
        """What the view fits the pane to.

        A baked hull rather than every mesh vertex: the fit runs each frame and
        a projected bounding box is decided entirely by extreme points, so the
        interior of a crown can never move it. Arrows stay excluded, or the view
        would breathe as forces grew.
        """
        return iter(self.fit_hull)


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

    return _ProceduralTooth(
        palmer=number, ttype=ttype, center=center,
        frame=(e_x, e_y, e_z), height=height, apex=(cx, cy, height),
        base=tuple(base), top=tuple(top), normals=tuple(normals),
        label_anchor=vmad(center, e_y, d * 0.5 + unit * 0.45),
    )


def build_procedural_arch():
    """The pre-mesh arch: 16 extruded prisms laid along a parabola.

    Retired from the view, kept only so `tools/make_synthetic_asset.py` can
    regenerate the scan-free test fixture. Returns `_ProceduralTooth`s, not
    `Tooth`s -- they carry rings and edges, not a mesh.
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
    return _ProceduralArch(teeth=tuple(teeth), unit=unit, guide=guide)


# ---- loading the baked mesh ----

#: Asset base path, without extension. `.json` + `.bin` sit beside it.
ASSET_BASE = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "assets", "arch_mesh")


def build_arch(base=None) -> Arch:
    """Load the baked arch mesh.

    Static, so the view builds this once and never per frame. Stdlib only -- the
    scan processing happened offline in `tools/bake_arch_mesh.py`, which is what
    keeps this import free of numpy and of any GL dependency.

    A missing or stale asset raises rather than falling back to anything: a
    silent fallback is how a bad asset reaches the Pi unnoticed.
    """
    data = arch_asset.read_asset(ASSET_BASE if base is None else base)
    teeth = tuple(
        Tooth(
            palmer=t["palmer"], ttype=t["ttype"], center=t["center"],
            frame=t["frame"], height=t["height"], apex=t["apex"],
            verts=t["verts"], tris=t["tris"], silhouette=t["silhouette"],
            label_anchor=t["label_anchor"],
        )
        for t in data["teeth"]
    )
    return Arch(teeth=teeth, unit=data["unit"], guide=data["guide"],
                fit_hull=data["fit_hull"])
