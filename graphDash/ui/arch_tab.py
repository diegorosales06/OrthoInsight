"""3D arch view: one lower arch, force or moment arrows per instrumented tooth.

`arch_model.py` owns the arch geometry and the reading-to-arrow scale; `proj3d.py`
owns the camera. This module is the view: it fits the arch to the pane, turns one
frame's readings into depth-sorted primitives, paints them with QPainter, and
wires up the camera controls.

What the glyphs mean is picked in two places. The DATA toggle swaps the whole
view between the force and the moment `GlyphScale`. Everything else is decided
one tooth at a time, in the toggle panel down the right-hand side: each
instrumented tooth turns its three components on and off independently, and (in
force mode only) can swap them for a single arrow along their vector sum instead
-- `arch_model.ResultantSpec`, which carries its own larger `hi` and its own
colour, since a resultant and a neighbour's Fz can now be the only two arrows on
screen. Moment has no resultant at all, so that column is absent in moment mode.

Those choices live in `GlyphVisibility`, which is keyed by (tooth, quantity) and
owned by the tab. Force and moment keep separate records, and because the
dashboard builds the tab once and never rebuilds it, the toggles survive
switching tabs -- for the life of the process, not across restarts.

A force is a push *along* an axis and is drawn as a straight arrow. A moment is
a rotation *about* one, so it is drawn as a circular arrow encircling the axis,
right-hand rule -- thumb along the signed axis, fingers following the arrow.
Which of the two a scale gets is `GlyphScale.curl`, not a test against a
particular scale object.

Magnitude is linear in |value| across the scale's [lo, hi]: below `lo` the axis
draws nothing, at or above `hi` it clamps. What grows differs with the glyph --
a straight arrow gets longer, a curl sweeps further around its fixed-radius
circle -- but in both cases more arrow is always more load, and an absent glyph
always means "under threshold".

No OpenGL -- the software pipeline behaves the same on the Pi as it does under
`--debug` on a laptop. Visibility is back-face culling plus a painter's-algorithm
depth sort over crowns and arrows together.
"""

import math
from dataclasses import dataclass
from functools import lru_cache, partial

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QScrollArea, QFrame,
)
from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF, pyqtSignal
from PyQt6.QtGui import (
    QPainter, QPen, QBrush, QColor, QFont, QFontMetrics, QPolygonF,
)

from graphDash.constants import REFRESH_MS
from graphDash.ui import theme
from graphDash.ui.proj3d import (
    Camera, vcross, vdot, vmad, vnorm, vscale, vsub, vunit,
)
from graphDash.ui.arch_model import (
    build_arch, GlyphScale, FORCE_GLYPH, MOMENT_GLYPH,
    LOWER_ARCH_ORDER, TOOTH_TYPE, normalize_tooth,
)

__all__ = ["ArchTab", "ArchView3D", "ArchKeyBar", "GlyphVisibility",
           "ToothGlyphPanel", "FORCE_GLYPH", "MOMENT_GLYPH", "DATA_SCALES",
           "PRESETS"]


def _qcolor(hex_str):
    return QColor(hex_str)


def _lerp(a, b, t):
    return a + (b - a) * t


# ---- glyph geometry (lengths are multiples of the arch's `unit`; widths px) ----
ARROW_MIN_LEN  = 0.60
ARROW_MAX_LEN  = 1.6
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
# Rings mark their own tooth's occlusal surface, so nothing of that tooth may
# cover them. Primitives paint far-to-near, so the nearest possible depth puts
# them last.
OVERLAY_DEPTH  = float("-inf")

# ---- curl geometry (moments: a rotation about an axis, not a push along it) ----
# The circle's radius is fixed and its *sweep* ramps with magnitude. Keeping the
# radius constant is what makes two teeth comparable at a glance -- every curl on
# the arch is the same size, and only how far round it goes says how much. The
# three components nest instead of sharing one circle, since all three encircle
# the same apex: Mx innermost, Mz outermost, matching `Tooth.frame` order.
CURL_RADII      = (0.34, 0.50, 0.66)    # multiples of `unit`, in Tooth.frame order
# The rank a resultant reports, so a curled resultant would take the middle
# radius. Nothing draws one today -- only force has a resultant and forces are
# arrows -- but the resultant branch still has to yield *some* rank, and the
# middle is the one that would read.
CURL_RESULTANT_RANK = 1
CURL_MIN_SWEEP  = math.radians(70.0)
CURL_MAX_SWEEP  = math.radians(320.0)   # short of a full turn, so the head stays
                                        # clear of the tail and the sweep is readable
CURL_HEAD_SWEEP = math.radians(30.0)
CURL_STEP       = math.radians(9.0)     # polyline resolution of the arc

# ---- camera ----
# Presets: (name, yaw deg, pitch deg).
PRESETS = (
    ("Oblique",  -18.0, 40.0),
    ("Occlusal",   0.0, 89.5),
    ("Anterior",   0.0,  6.0),
)
DEFAULT_PRESET = 0
# What the DATA toggle picks between, in button order.
DATA_SCALES = (FORCE_GLYPH, MOMENT_GLYPH)
# The arch spans ~2 x 1.3 world units. Keep the eye well outside that so a near
# tooth can never approach the camera and blow up under the perspective divide
# -- the view fits the pane by scaling, so a longer lens costs nothing.
CAM_DISTANCE = 7.0
FIT_MARGIN = 0.86        # leaves room for arrows, which are not fitted
ZOOM_MIN, ZOOM_MAX = 0.45, 4.0
ORBIT_SENS = 0.008       # rad per pixel of drag

# ---- picking ----
# Clicking a crown opens that cell's graphs, so a press/release has to be told
# apart from an orbit. Sized for a fingertip rather than a mouse: the Pi's
# screen is touch, and Qt synthesises mouse events from it.
CLICK_SLOP = 6           # px of travel below which a press/release is a click
HOVER_STEP = 3           # px of travel below which the hover test is skipped
# Documented lever, like CROWN_ANTIALIAS below. Off means the click still works
# and the cursor simply stays an open hand -- the first thing to turn off if a
# Pi measurement of `tooth_at` ever comes up short.
PICK_HOVER = True

# ---- chrome ----
# The key is a landscape bar across the top of the tab, beside the camera and
# DATA buttons -- not a column down the side of the view. That is what leaves
# the whole width of the widget for the arch, and the whole right-hand column
# for the per-tooth toggles.
KEY_BAR_H     = 88       # px, matches the two stacked button rows beside it
KEY_BAR_PAD   = 14       # px, left/right inset
KEY_LINE      = 15       # px, baseline pitch -- four lines fit in KEY_BAR_H
KEY_GROUP_GAP = 18       # px between groups
KEY_ARROW_HEAD = 8.0     # px, for the key's flat sample arrows
KEY_RING_R = 5.5
KEY_CURL_R = 6.5         # px, for the key's flat sample curls
TOP_PAD, BOTTOM_PAD, SIDE_PAD = 58, 22, 18

# Directional light for crown shading, in world coordinates.
SHADE_LEVELS = 32       # quantised Lambert steps; see _shade_table

# Antialias the crown fills? This is the biggest single performance lever in the
# view, and it is a deliberate switch rather than a guess: measured on the dev
# laptop with a fully instrumented arch (1088 triangles), turning it off takes
# the marginal cost from 3.3 to 1.7 us per triangle -- which doubles the
# triangle budget a Pi 4 can afford. What it costs is a slightly jagged crown
# silhouette; everything else in the view (arrows, rings, labels, guide, key)
# stays antialiased either way.
#
# Leave it on until `tests/bench_arch.py --scaling` on the actual Pi says
# otherwise, then flip it here and re-bake at the larger budget.
CROWN_ANTIALIAS = True
LIGHT_DIR = vunit((0.35, -0.55, 0.78))


def _shade(color: QColor, f: float) -> QColor:
    """Lambert-shade `color`: f = 0 is a deep tint, f = 1 the color itself."""
    t = 0.60 + 0.40 * max(0.0, min(1.0, f))
    return QColor(
        int(color.red() * t), int(color.green() * t), int(color.blue() * t)
    )


def _shade_table(color: QColor):
    """`SHADE_LEVELS` (pen, brush) pairs spanning the Lambert range.

    Built once per view. Constructing a QColor, a QPen and a QBrush per triangle
    per frame costs more than filling the triangle does; quantising the shade
    instead makes crowns ~30% cheaper to paint. At 32 levels the step is about
    three RGB units on the tooth fill, which is well below a visible band.

    The pen matters as much as the brush: adjacent antialiased fills leave a
    hairline of background along every shared edge, and a mesh has two orders of
    magnitude more shared edges than the old prism did. Stroking each triangle
    in its own fill colour closes those seams.
    """
    table = []
    for level in range(SHADE_LEVELS):
        c = _shade(color, level / (SHADE_LEVELS - 1))
        table.append((QPen(c, 1.0), QBrush(c)))
    return tuple(table)


# ---- one frame's worth of projection ----

@dataclass(frozen=True)
class _Frame:
    """World -> screen for a single paint pass.

    Built once per `paintEvent` by `fit()` and read-only thereafter, so no frame
    state has to live on the widget.
    """
    projector: object
    scale_px: float      # px per image unit
    cx: float
    cy: float
    ppu: float           # px per world unit near the camera target
    unit: float          # the arch's base unit, in world units

    @classmethod
    def fit(cls, arch, camera, zoom, rect: QRectF):
        """Fit the arch's crowns into `rect` at the camera's current pose."""
        pr = camera.projector()
        us, vs = [], []
        for pt in arch.vertices():
            u, v, _ = pr.project(pt)
            us.append(u)
            vs.append(v)
        span_u = max(1e-6, max(us) - min(us))
        span_v = max(1e-6, max(vs) - min(vs))
        s = min(rect.width() / span_u, rect.height() / span_v) * FIT_MARGIN * zoom
        # `s` is px per *image* unit and image coordinates are already divided by
        # depth, so the world-unit scale carries the camera distance back in.
        return cls(
            projector=pr, scale_px=s,
            cx=rect.center().x() - s * (min(us) + max(us)) / 2,
            cy=rect.center().y() + s * (min(vs) + max(vs)) / 2,
            ppu=s / max(1e-6, camera.distance),
            unit=arch.unit,
        )

    def place_xy(self, world):
        """(x, y, depth) as plain floats -- the one world -> screen mapping.

        `place` wraps this for the painter, which needs QPointF; the hit test
        calls it directly, since it projects a crown's 602 vertices and has no
        use for the object. Both go through here, so the pick and the paint
        cannot drift into two slightly different mappings -- which would land
        every click a few pixels off the crown it looks like it hit, silently.
        """
        u, v, depth = self.projector.project(world)
        return self.cx + self.scale_px * u, self.cy - self.scale_px * v, depth

    def place(self, world):
        """(screen point, depth) for one world point."""
        x, y, depth = self.place_xy(world)
        return QPointF(x, y), depth

    def point(self, world) -> QPointF:
        return self.place(world)[0]

    def depth(self, world) -> float:
        return self.place(world)[1]

    def hidden(self, normal) -> bool:
        """True when a face with this outward normal points away from the eye."""
        return vdot(normal, self.projector.fwd) >= 0

    def toward_viewer(self, direction) -> bool:
        return vdot(direction, self.projector.fwd) < 0



def _crown_depth_at(frame, tooth, px, py):
    """Depth of the nearest front-facing triangle of `tooth` under (px, py).

    The exact stage of the hit test. The cull is `_draw_crown`'s cull -- a
    triangle you cannot see must not be a triangle you can click -- and, as
    there, each vertex is projected once rather than once per triangle corner.

    Depth is interpolated screen-space linearly rather than perspective
    correctly. At CAM_DISTANCE = 7.0 for a crown ~0.27 world units tall that is
    off by a fraction of a crown's thickness, and it is only ever used to order
    *overlapping crowns* -- the same job the painter's own sort does.
    """
    pts = [frame.place_xy(v) for v in tooth.verts]
    fwd = frame.projector.fwd
    best = None
    for i, j, k, nrm in tooth.tris:
        if vdot(nrm, fwd) >= 0.0:
            continue
        ax, ay, ad = pts[i]
        bx, by, bd = pts[j]
        cx, cy, cd = pts[k]
        v0x, v0y = bx - ax, by - ay
        v1x, v1y = cx - ax, cy - ay
        den = v0x * v1y - v1x * v0y
        if den == 0.0:
            continue                          # edge-on sliver
        qx, qy = px - ax, py - ay
        a = (qx * v1y - v1x * qy) / den
        if a < 0.0 or a > 1.0:
            continue
        b = (v0x * qy - qx * v0y) / den
        if b < 0.0 or a + b > 1.0:
            continue
        d = ad + a * (bd - ad) + b * (cd - ad)
        if best is None or d < best:
            best = d
    return best


# ---- screen-space painting primitives (shared by the arch and its key) ----

def _paint_head(p, neck: QPointF, tip: QPointF, color, width):
    """Filled arrowhead spanning `neck` -> `tip`, or a dot if they nearly meet.

    Shared by the straight arrows and the curls -- the two differ only in what
    leads up to the head, so an edge-on glyph of either kind degrades the same
    way rather than in its own.
    """
    dx, dy = tip.x() - neck.x(), tip.y() - neck.y()
    seg = math.hypot(dx, dy)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(color))
    if seg < 0.75:
        p.drawEllipse(tip, width * 1.3, width * 1.3)
        return
    px, py = -dy / seg, dx / seg
    half = seg * 0.42
    p.drawPolygon(QPolygonF([
        tip,
        QPointF(neck.x() + px * half, neck.y() + py * half),
        QPointF(neck.x() - px * half, neck.y() - py * half),
    ]))


def _paint_arrow(p, tail: QPointF, neck: QPointF, tip: QPointF, color, width):
    """Shaft from `tail` to `neck`, plus a head filling `neck` -> `tip`.

    Screen space only: the arch's 3D arrows project their three points first,
    the key's flat sample arrows construct them directly.
    """
    pen = QPen(color, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawLine(tail, neck)
    _paint_head(p, neck, tip, color, width)


def _paint_curl(p, points, tip: QPointF, color, width):
    """Arc through `points`, with a head carrying it on to `tip`.

    Screen space only, like `_paint_arrow`: the arch's 3D curls project their
    arc first, the key's flat sample curls construct it directly.
    """
    pen = QPen(color, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPolyline(QPolygonF(points))
    _paint_head(p, points[-1], tip, color, width)


def _curl_basis(axis, frame):
    """Two unit vectors spanning the plane the curl's circle lies in.

    Seeded from whichever of the tooth's own basis vectors is least aligned with
    `axis`: for a component curl that is exactly one of the other two axes, and
    for the resultant it is still tied to the tooth, so the circle keeps a
    stable start point as the arch orbits rather than spinning with the camera.

    `v = axis x u` makes the pair right-handed, which is what puts a positive
    sweep on the right-hand-rule side of the axis -- curl the fingers the way the
    arrow runs and the thumb points along `axis`.
    """
    seed = min(frame, key=lambda b: abs(vdot(b, axis)))
    u = vunit(vsub(seed, vscale(axis, vdot(seed, axis))))
    return u, vcross(axis, u)


def _key_arrow(p, x, y, length, color, width):
    """Straight right-pointing sample arrow, in the key's flat 2D space."""
    neck_x = x + length - KEY_ARROW_HEAD * 0.85
    _paint_arrow(p, QPointF(x, y), QPointF(neck_x, y),
                 QPointF(x + length, y), color, width)


def _key_curl(p, x, y, r, sweep, color, width):
    """Sample curl for the key: the same arc the arch draws, but face-on and in
    the key's flat 2D space, centred at (x + r, y) and sweeping the same way --
    counter-clockwise on screen, as a curl about +view looks."""
    def at(angle):
        return QPointF(x + r + r * math.cos(angle), y - r * math.sin(angle))

    head = min(CURL_HEAD_SWEEP, sweep * 0.45)
    shaft = sweep - head
    steps = max(2, int(shaft / CURL_STEP) + 1)
    _paint_curl(p, [at(shaft * i / steps) for i in range(steps + 1)],
                at(sweep), color, width)


def _reading_text(v, signed=True):
    """Drop the decimals on big readings rather than run the column off the edge
    of the panel. A magnitude is never signed."""
    sign = "+" if signed else ""
    return format(v, f"{sign}.2f") if abs(v) < 100 else format(v, f"{sign}.0f")


def _paint_ring(p, center: QPointF, r, color, width, toward):
    """Ring marking an axis aimed along the view: filled dot = toward the viewer
    (out of the screen), cross = away from it."""
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


class GlyphVisibility:
    """Which glyphs each tooth shows, keyed by (Palmer designation, quantity).

    Force and moment keep **separate** records: hiding Fy on LR6 says nothing
    about My on LR6. The key is `GlyphScale.quantity` -- the same string the DATA
    buttons are labelled from -- so switching modes swaps the whole answer and
    switching back restores it.

    A tooth's resultant flag is exclusive: while it is on that tooth draws only
    the resultant, and its three component flags are remembered but ignored.
    Moment has no resultant at all (`GlyphScale.resultant is None`), so the flag
    is simply never set in that mode.

    Records are created on demand as "all three components, no resultant" --
    which is the view as it behaved before there were any toggles -- and are
    never deleted, so a tooth that a config edit unmaps and later remaps comes
    back with the flags it had.

    Plain Python, no Qt: it lives for the life of the process on the tab widget,
    not on disk, and is assertable without a QApplication.
    """

    N_AXES = 3

    def __init__(self):
        self._state = {}      # (palmer, quantity) -> [ax0, ax1, ax2, resultant]

    def _record(self, palmer, quantity):
        return self._state.setdefault((palmer, quantity),
                                      [True] * self.N_AXES + [False])

    def axis(self, palmer, quantity, rank) -> bool:
        return self._record(palmer, quantity)[rank]

    def axes(self, palmer, quantity) -> tuple:
        return tuple(self._record(palmer, quantity)[:self.N_AXES])

    def resultant(self, palmer, quantity) -> bool:
        return self._record(palmer, quantity)[self.N_AXES]

    def set_axis(self, palmer, quantity, rank, on):
        self._record(palmer, quantity)[rank] = bool(on)

    def set_resultant(self, palmer, quantity, on):
        self._record(palmer, quantity)[self.N_AXES] = bool(on)

    def set_axis_all(self, palmers, quantity, rank, on):
        for palmer in palmers:
            self.set_axis(palmer, quantity, rank, on)

    def set_resultant_all(self, palmers, quantity, on):
        for palmer in palmers:
            self.set_resultant(palmer, quantity, on)

    def all_axis(self, palmers, quantity, rank) -> bool:
        """True when every listed tooth has this axis on -- what the master row
        needs to decide whether its next click clears or restores."""
        return all(self.axis(p, quantity, rank) for p in palmers)

    def any_resultant(self, palmers, quantity) -> bool:
        """True when at least one listed tooth is drawing its resultant, which
        is what puts the resultant's legend row and ceiling in the key."""
        return any(self.resultant(p, quantity) for p in palmers)


class ArchView3D(QWidget):
    """The 3D arch itself: orbiting camera, extruded crowns, force arrows."""

    #: Emitted when a mouse orbit moves the camera off the selected preset.
    preset_left = pyqtSignal()

    #: Emitted when a click lands on a mapped crown. The payload is the *cell
    #: index* that crown's sensor writes to, not the Palmer designation: the
    #: view already owns `tooth_to_cell` and the hit test consults it to decide
    #: what is clickable at all, so resolving the index in the same breath from
    #: the same dict makes it impossible for the tab that opens to be for a
    #: different cell than the click tested against.
    tooth_picked = pyqtSignal(int)

    def __init__(self, store, tooth_to_cell: dict, scale: GlyphScale,
                 visibility=None):
        super().__init__()
        self.store = store
        self.tooth_to_cell = tooth_to_cell
        self.scale = scale
        # Which glyphs each tooth shows is `GlyphVisibility`'s to say, and it is
        # the *only* thing that says it: the view reads it, the toggle panel
        # writes it, and there is no second way in. A setter here would let the
        # arch and the panel's buttons disagree without either being wrong.
        self.visibility = visibility if visibility is not None \
            else GlyphVisibility()
        self.arch = build_arch()
        self._tooth_by_palmer = {t.palmer: t for t in self.arch.teeth}

        ys = [t.center[1] for t in self.arch.teeth]
        self.camera = Camera(
            target=(0.0, (min(ys) + max(ys)) / 2, self.arch.unit * 0.5),
            distance=CAM_DISTANCE,
        )
        self.zoom = 1.0
        self._press_pos = None    # set while a button is down; the gesture
        self._drag_pos = None     # set once it breaks CLICK_SLOP; the orbit
        self._hover = None        # Palmer under the pointer, or None
        self._hover_tested = None
        self._frame_memo = None   # see _frame()
        self._pending = None      # see take_readings()
        self.set_preset(DEFAULT_PRESET)

        self._shades = _shade_table(_qcolor(theme.TOOTH_FILL))
        # Deliberately small. The key and the toggles are sibling widgets now,
        # and their heights add to the tab's minimum rather than being carved
        # out of this pane -- so a floor sized for a comfortable arch would put
        # the tab's minimum above the 800x480 screens the Pi runs. The arch is
        # fit-scaled into whatever it gets.
        self.setMinimumSize(360, 260)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        # Without this `mouseMoveEvent` is only delivered with a button down, so
        # the hover cursor would never appear.
        self.setMouseTracking(True)

    # ---- what the arrows mean ----

    @property
    def title(self):
        # No components/resultant suffix: teeth choose that one at a time now.
        return f"Lower Arch — {self.scale.quantity} (3D)"

    def set_scale(self, scale: GlyphScale):
        """Swap force <-> moment. Only the scale changes; the arch, the camera
        and the tooth mapping are the same either way -- and each quantity keeps
        its own per-tooth toggles, so switching back restores what was showing.
        """
        self.scale = scale
        self.update()

    # ---- camera control ----

    def set_preset(self, index):
        _, yaw, pitch = PRESETS[index % len(PRESETS)]
        self.camera.set_orientation(math.radians(yaw), math.radians(pitch))
        self.preset_index = index % len(PRESETS)
        self.update()

    def view_name(self):
        return "Custom" if self.preset_index is None \
            else PRESETS[self.preset_index][0]

    def reset_view(self):
        self.zoom = 1.0
        self.set_preset(DEFAULT_PRESET)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            # Two positions, and they mean different things. `_press_pos` is
            # where the gesture began and never moves, because `_drag_pos` is
            # overwritten on every move -- the orbit is incremental -- so it
            # holds the last mouse *step*, not the travel since the press.
            # `_drag_pos` stays None until the slop breaks, which is exactly
            # what "this became an orbit" means; no separate flag is needed.
            self._press_pos = event.position()
            self._drag_pos = None
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        pos = event.position()
        if self._press_pos is None:
            if PICK_HOVER:
                self._update_hover(pos)
            return
        if self._drag_pos is None:
            if (abs(pos.x() - self._press_pos.x()) <= CLICK_SLOP
                    and abs(pos.y() - self._press_pos.y()) <= CLICK_SLOP):
                # Dead zone. This suppresses the *orbit*, not just the click
                # classification: the emit below fires on any non-zero delta, so
                # without it a click with two pixels of hand wobble would knock
                # the camera off its preset and unhighlight the VIEW button.
                return
            # Latched by construction: `_drag_pos` is never cleared until the
            # release, so someone who orbits 200 px away and comes back near the
            # press point has still moved the camera, and must not also select.
            self._drag_pos = pos
            return
        dx = pos.x() - self._drag_pos.x()
        dy = pos.y() - self._drag_pos.y()
        self._drag_pos = pos
        # Drag right spins the arch right; drag down tilts toward occlusal.
        self.camera.orbit(-dx * ORBIT_SENS, dy * ORBIT_SENS)
        if self.preset_index is not None and (dx or dy):
            self.preset_index = None
            self.preset_left.emit()
        self.update()

    def mouseReleaseEvent(self, event):
        press, dragged = self._press_pos, self._drag_pos is not None
        self._press_pos = self._drag_pos = None
        # `clear_hover`, not just the cursor: the pointer may still be over the
        # crown it was over before the press, and leaving `_hover` set would make
        # the next move a no-op -- so the hand would stay closed-then-open until
        # the pointer left the tooth and came back.
        self.clear_hover()
        if press is None or dragged \
                or event.button() != Qt.MouseButton.LeftButton:
            return
        palmer = self.tooth_at(press)
        if palmer is not None:
            # `tooth_at` only ever returns a tooth it found in `tooth_to_cell`,
            # so this needs no second membership check. Last statement, after
            # every local is cleared: the connected slot raises another tab,
            # which hides this widget while its own handler is still on the stack.
            self.tooth_picked.emit(self.tooth_to_cell[palmer])

    def mouseDoubleClickEvent(self, event):
        """Reset the camera -- unless the double-click landed on a crown.

        Qt delivers Press, Release, DblClick, Release, and the double-click does
        not pass through `mousePressEvent`. So the first release already fired at
        most one select and cleared `_press_pos`, and the second release finds it
        None and fires nothing: at most one select per double-click, by
        construction, with no doubleClickInterval timer putting dead air on the
        primary interaction.

        What is left is that a double-click on a crown would both open its graph
        and reset the camera of a tab the user has just left -- a surprise on
        their next visit. Fix that here, in the reset, rather than in the select.
        """
        if event.button() == Qt.MouseButton.LeftButton \
                and self.tooth_at(event.position()) is not None:
            return
        self.reset_view()

    def leaveEvent(self, event):
        self.clear_hover()

    def wheelEvent(self, event):
        steps = event.angleDelta().y() / 120.0
        self.zoom = max(ZOOM_MIN, min(ZOOM_MAX, self.zoom * (1.12 ** steps)))
        self.update()

    # ---- data ----

    def take_readings(self):
        """Read the store once and park the result for the paint about to run.

        The glyphs and the numbers are separate widgets now that the key has
        left the view -- the arch paints here, the readings print in the toggle
        panel -- so they can no longer share a paint pass. This is what keeps
        them on the same sample anyway: `ArchTab._refresh` reads once a tick,
        hands the result to the panel, and leaves it here for the `update()` it
        then schedules. A paint that arrives without one (an orbit, a resize)
        simply reads fresh.
        """
        self._pending = self._readings()
        return self._pending

    def drop_readings(self):
        """Discard the parked sample, so the next paint reads fresh.

        Needed when the tooth mapping changes under it: `_pending` is keyed by
        Palmer designation, and while the tab is hidden nothing consumes it, so
        a sample read under the old mapping can otherwise sit there indefinitely
        and paint arrows for a cell that no longer exists.
        """
        self._pending = None

    def readouts(self, readings):
        """{Palmer: [(text, hex colour)]} for the toggle panel's numbers.

        It lives on the view rather than the panel because the numbers have to
        say the same thing the glyphs do: one magnitude for a tooth on its
        resultant (the norm of the very vector the arrow is drawn along), three
        signed components otherwise -- with a component whose toggle is off
        greyed rather than dropped, so the reading stays legible while saying it
        is not on the arch.
        """
        scale, vis = self.scale, self.visibility
        quantity = scale.quantity
        out = {}
        for palmer in self.tooth_to_cell:
            vals = readings.get(palmer)
            if vals is None:
                out[palmer] = [("--", theme.ON_SURFACE_SUBTLE)]
            elif scale.resultant is not None and vis.resultant(palmer, quantity):
                out[palmer] = [(
                    _reading_text(self._magnitude(palmer, vals), signed=False),
                    scale.resultant.color)]
            else:
                out[palmer] = [
                    (_reading_text(vals[axis.index]),
                     axis.color if vis.axis(palmer, quantity, rank)
                     else theme.ON_SURFACE_SUBTLE)
                    for rank, axis in enumerate(scale.axes)]
        return out

    def _readings(self):
        """{Palmer designation: 6-axis reading} for every instrumented tooth.

        One store read per cell per frame, shared by the arrows and the panel's
        numeric readout so the two can never show different samples.
        """
        latest = {}
        for palmer, cell_idx in self.tooth_to_cell.items():
            if cell_idx < self.store.n_cells:
                vals = self.store.latest(cell_idx)
                if vals is not None:
                    latest[palmer] = vals
        return latest

    # ---- where the arch lands on screen ----

    def _view_rect(self) -> QRectF:
        """The pane the arch is fitted into.

        The whole widget is the arch: the key is a bar above this widget and the
        toggles a column beside it, so nothing is reserved here.

        Shared by the paint and the pick deliberately. If the two ever computed
        this separately and drifted, every click would land a few pixels off the
        crown it looks like it hit, and nothing on screen would say so.
        """
        w, h = self.width(), self.height()
        return QRectF(SIDE_PAD, TOP_PAD,
                      max(60, w - 2 * SIDE_PAD), max(60, h - TOP_PAD - BOTTOM_PAD))

    def _frame(self) -> "_Frame":
        """The world -> screen mapping for the current camera pose.

        Rebuilt rather than cached from the last paint. `_Frame.fit` is a pure
        function of (arch, camera, zoom, rect) -- all widget state that outlives
        any one frame -- so this keeps the rule that no frame state is parked on
        the widget. A cached painted frame would have six writers to invalidate
        (orbit, wheel, resize, set_preset, reset_view, first show), and a missed
        one is a silent few-pixel offset that shows up only as "sometimes it
        picks the neighbouring tooth". It would also be *absent* in the case that
        matters most -- a click arriving before a freshly shown tab's first paint.

        What is parked is a memo of that pure function, because `fit` projects
        the 2410-point fit hull (0.8 ms here, ~8 ms on a Pi) and the hover test
        calls this per mouse-move. The memo also takes that cost off every idle
        repaint, since a still camera keeps the key constant.

        The invariant: **anything `_Frame.fit` reads must appear in the key.**
        """
        cam, rect = self.camera, self._view_rect()
        # Keyed on exactly what `fit` consumes: the arch is constant, the camera
        # reduces to these four, and the pane enters as the rect itself -- not as
        # width/height, which `fit` never reads. Mirroring the signature rather
        # than a hand-copied field list is what keeps the key honest.
        key = (cam.yaw, cam.pitch, cam.target, cam.distance, self.zoom, rect)
        if self._frame_memo is None or self._frame_memo[0] != key:
            self._frame_memo = (key, _Frame.fit(self.arch, cam, self.zoom, rect))
        return self._frame_memo[1]

    # ---- picking ----

    def tooth_at(self, pos: QPointF):
        """Palmer designation of the mapped crown under `pos`, or None.

        Runs the same projection, the same back-face cull and the same triangles
        `_draw_crown` paints, so what you can click is by construction what you
        can see. Two stages: a sound screen-bbox reject (`Tooth.box`), then an
        exact point-in-triangle over the survivors, nearest depth winning -- so
        an occluded crown loses to the one in front of it.

        Unmapped teeth are skipped before either stage, using the very membership
        test the painter uses to choose `_draw_crown` over `_draw_footprint`. One
        dict, one test: the clickable set cannot disagree with what is drawn.

        Costs nothing on the frame tick -- this runs only on a mouse event.
        """
        frame = self._frame()
        px, py = pos.x(), pos.y()
        best, best_depth = None, None
        for tooth in self.arch.teeth:
            if tooth.palmer not in self.tooth_to_cell:
                continue          # a dashed footprint: no cell, so no target
            box = [frame.place_xy(c) for c in tooth.box]
            xs = [c[0] for c in box]
            ys = [c[1] for c in box]
            if not (min(xs) <= px <= max(xs) and min(ys) <= py <= max(ys)):
                continue
            depth = _crown_depth_at(frame, tooth, px, py)
            if depth is not None and (best_depth is None or depth < best_depth):
                best, best_depth = tooth.palmer, depth
        return best

    def clear_hover(self):
        """Forget what the pointer was over and drop the pointing hand.

        Needed when the clickable set changes under a stationary pointer -- a
        config edit that unmaps a crown must not leave the hand hovering over a
        footprint until the mouse next moves.
        """
        self._hover = self._hover_tested = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def _update_hover(self, pos):
        """Point the cursor at whatever is clickable under `pos`.

        Runs the *same* `tooth_at` predicate as the click, not a cheaper one: a
        crown's projected bbox is noticeably larger than the crown and overlaps
        its neighbour, so a pointing hand over the bbox alone would promise a
        click that does nothing.

        It is affordable because stage 1 rejects. Cursor over empty canvas or a
        footprint costs 0.02 ms here (~0.2 ms on a Pi) -- that is the ~99% case.
        Only a pointer actually inside a crown's bbox reaches the exact stage,
        and HOVER_STEP caps how often that can happen during a sweep. Nothing
        repaints: the cursor is the whole affordance.
        """
        last = self._hover_tested
        if last is not None and abs(pos.x() - last.x()) < HOVER_STEP \
                and abs(pos.y() - last.y()) < HOVER_STEP:
            return
        self._hover_tested = pos
        palmer = self.tooth_at(pos)
        if palmer == self._hover:
            return
        self._hover = palmer
        self.setCursor(Qt.CursorShape.PointingHandCursor if palmer
                       else Qt.CursorShape.OpenHandCursor)

    # ---- painting ----

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, _qcolor(theme.SURFACE))

        frame = self._frame()
        readings = self._pending if self._pending is not None else self._readings()
        self._pending = None

        self._draw_guide(p, frame)
        for _, paint in sorted(self._primitives(frame, readings),
                               key=lambda prim: -prim[0]):
            paint(p)
        for tooth in self.arch.teeth:
            self._draw_label(p, frame, tooth)

        self._draw_header(p)
        p.end()

    def _primitives(self, frame, readings):
        """Yield (depth, paint) for everything in the scene, unordered.

        Depth is distance from the eye, so the caller paints in descending
        order: far things first, near things over them.
        """
        for tooth in self.arch.teeth:
            mapped = tooth.palmer in self.tooth_to_cell
            apex_depth = frame.depth(tooth.apex)
            draw = self._draw_crown if mapped else self._draw_footprint
            yield apex_depth, partial(draw, frame=frame, tooth=tooth)

            # `mapped`, not just `vals`: a parked reading (see take_readings)
            # can outlive the mapping it was read under -- a live sensor-config
            # edit rewrites `tooth_to_cell` between the read and the paint --
            # and a glyph on a tooth this frame draws as an unmapped footprint
            # would be an arrow with no sensor behind it.
            vals = readings.get(tooth.palmer) if mapped else None
            if vals is not None:
                yield from self._glyph_primitives(frame, tooth, vals, apex_depth)

    def _glyph_vectors(self, tooth, vals):
        """Yield (unit axis, 0-1 magnitude fraction, color, rank) for one tooth.

        Component mode yields one entry per above-threshold axis the tooth's
        toggles leave on, each along one of that tooth's own basis vectors.
        Resultant mode yields at most one, along the vector sum of the three
        components in that same frame -- so the glyph follows the way the tooth
        is actually being pushed, and clamps at the resultant's own larger `hi`.

        Which of the two a tooth is in is its own choice (`GlyphVisibility`), so
        one tooth can show a resultant while its neighbour shows components. A
        tooth in resultant mode shows *only* the resultant -- its component
        flags are remembered but not consulted here.

        `rank` is the axis's position in `Tooth.frame`, which the curls use to
        nest their radii; the straight arrows share one origin and ignore it.
        """
        scale = self.scale
        quantity = scale.quantity
        vis = self.visibility
        if scale.resultant is not None and vis.resultant(tooth.palmer, quantity):
            total = self._resultant_vector(tooth, vals)
            f = scale.resultant_frac(vnorm(total))
            if f is not None:
                yield (vunit(total), f, _qcolor(scale.resultant.color),
                       CURL_RESULTANT_RANK)
            return
        for rank, (axis, basis) in enumerate(zip(scale.axes, tooth.frame)):
            if not vis.axis(tooth.palmer, quantity, rank):
                continue
            value = vals[axis.index]
            f = scale.frac(value)
            if f is None:
                continue
            direction = basis if value >= 0 \
                else (-basis[0], -basis[1], -basis[2])
            yield direction, f, _qcolor(axis.color), rank

    def _resultant_vector(self, tooth, vals):
        """The three components summed in this tooth's own frame, as a world
        vector. Its magnitude is what the resultant arrow and the toggle panel's
        numeric readout both report."""
        total = (0.0, 0.0, 0.0)
        for axis, basis in zip(self.scale.axes, tooth.frame):
            total = vmad(total, basis, vals[axis.index])
        return total

    def _glyph_primitives(self, frame, tooth, vals, apex_depth):
        """One primitive per glyph this tooth shows: its above-threshold
        components, or the single resultant.

        A glyph's depth is clamped to its own tooth's apex so the crown it grows
        out of can never swallow it -- an intrusive -Fz points straight into the
        tooth body, and a curl encircles the crown, half of it behind. Teeth
        nearer the camera still cover either.

        Curls all sort at the same depth. `sorted` is stable and this yields in
        `Tooth.frame` order, so the outermost circle lands on top of the ones it
        rings, which is the order that reads.
        """
        for direction, f, color, rank in self._glyph_vectors(tooth, vals):
            width = _lerp(ARROW_MIN_W, ARROW_MAX_W, f)

            if self.scale.curl:
                yield apex_depth - 1e-4, partial(
                    self._draw_curl, frame=frame, tooth=tooth,
                    direction=direction, radius=frame.unit * CURL_RADII[rank],
                    sweep=_lerp(CURL_MIN_SWEEP, CURL_MAX_SWEEP, f),
                    color=color, width=width)
                continue

            length = frame.unit * _lerp(ARROW_MIN_LEN, ARROW_MAX_LEN, f)
            tail = frame.point(vmad(tooth.apex, direction,
                                    frame.unit * ARROW_INSET))
            tip = frame.point(vmad(tooth.apex, direction, length))
            span = math.hypot(tip.x() - tail.x(), tip.y() - tail.y())
            if span < AXIAL_MIN_FRAC * length * frame.ppu:
                yield OVERLAY_DEPTH, partial(
                    _paint_ring, center=tail, color=color, width=width,
                    r=frame.ppu * frame.unit * _lerp(RING_MIN_R, RING_MAX_R, f),
                    toward=frame.toward_viewer(direction))
                continue

            mid_depth = frame.depth(vmad(tooth.apex, direction, length * 0.5))
            yield min(mid_depth, apex_depth) - 1e-4, partial(
                self._draw_arrow, frame=frame, origin=tooth.apex,
                direction=direction, length=length, color=color, width=width)

    def _draw_arrow(self, p, frame, origin, direction, length, color, width):
        head = min(frame.unit * ARROW_HEAD_LEN, length * 0.55)
        inset = frame.unit * ARROW_INSET
        _paint_arrow(
            p,
            frame.point(vmad(origin, direction, inset)),
            frame.point(vmad(origin, direction, max(inset, length - head))),
            frame.point(vmad(origin, direction, length)),
            color, width)

    def _draw_curl(self, p, frame, tooth, direction, radius, sweep, color, width):
        """A circular arrow encircling `direction`, centred on the tooth's apex.

        The whole circle is projected, not approximated by an ellipse, so it
        foreshortens correctly as the arch orbits -- and an axis lying in the
        screen plane collapses its circle to a short stroke rather than to
        nothing. That is the mirror of the straight arrows' failure case: an
        arrow is worst aimed *at* the eye, a curl is best there, which is why the
        ring substitution above has no counterpart here.
        """
        u, v = _curl_basis(direction, tooth.frame)

        def at(angle):
            return frame.point(vmad(vmad(tooth.apex, u, radius * math.cos(angle)),
                                    v, radius * math.sin(angle)))

        head = min(CURL_HEAD_SWEEP, sweep * 0.45)
        shaft = sweep - head
        steps = max(2, int(shaft / CURL_STEP) + 1)
        _paint_curl(p, [at(shaft * i / steps) for i in range(steps + 1)],
                    at(sweep), color, width)

    def _draw_footprint(self, p, frame, tooth):
        """A tooth with no cell mapped to it: a flat outline in the occlusal
        plane, so the instrumented crowns are the only things standing up."""
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_qcolor(theme.OUTLINE_STRONG), 1.1, Qt.PenStyle.DashLine))
        p.drawPolygon(QPolygonF([frame.point(pt) for pt in tooth.silhouette]))

    def _draw_crown(self, p, frame, tooth):
        """An instrumented tooth: its baked crown mesh plus its arrow origin.

        Cull, sort, fill. The whole crown remains a *single* primitive in
        `_primitives`, so triangles order among themselves here while crowns and
        arrows keep ordering against each other outside -- which is what
        preserves the arrow depth clamp.
        """
        # Project each vertex once. A welded mesh shares roughly three triangles
        # per vertex, so projecting per corner instead would triple the frame's
        # dominant cost.
        pts = [frame.place(v) for v in tooth.verts]
        fwd = frame.projector.fwd
        top = SHADE_LEVELS - 1

        # Sum of the three depths, not their mean: the sort only needs the
        # ordering, and a division per triangle per frame is not free here.
        faces = [
            (pts[i][1] + pts[j][1] + pts[k][1], i, j, k,
             int(max(0.0, vdot(nrm, LIGHT_DIR)) * top))
            for i, j, k, nrm in tooth.tris if vdot(nrm, fwd) < 0.0
        ]
        faces.sort(key=lambda f: -f[0])

        # Without antialiasing there are no partial-coverage pixels to leave a
        # seam, so the seam-closing pen is pure cost and goes away with it.
        if CROWN_ANTIALIAS:
            for _, i, j, k, level in faces:
                pen, brush = self._shades[level]
                p.setPen(pen)
                p.setBrush(brush)
                p.drawPolygon(QPolygonF([pts[i][0], pts[j][0], pts[k][0]]))
        else:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            p.setPen(Qt.PenStyle.NoPen)
            for _, i, j, k, level in faces:
                p.setBrush(self._shades[level][1])
                p.drawPolygon(QPolygonF([pts[i][0], pts[j][0], pts[k][0]]))
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Common origin of this tooth's three arrows.
        r = max(1.5, frame.unit * ORIGIN_DOT_R * frame.ppu)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(_qcolor(theme.ON_SURFACE_MUTED)))
        p.drawEllipse(frame.point(tooth.apex), r, r)

    def _draw_guide(self, p, frame):
        """Faint occlusal-plane curve through the crown centers -- the ground
        plane cue that keeps the perspective readable."""
        p.setPen(QPen(_qcolor(theme.OUTLINE), 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolyline(QPolygonF([frame.point(pt) for pt in self.arch.guide]))

    def _draw_label(self, p, frame, tooth):
        f = QFont()
        f.setPointSize(theme.FONT_CAPTION)
        f.setBold(True)
        p.setFont(f)
        mapped = tooth.palmer in self.tooth_to_cell
        p.setPen(_qcolor(theme.ON_SURFACE if mapped else theme.ON_SURFACE_SUBTLE))
        text = tooth.palmer
        sp = frame.point(tooth.label_anchor)
        fm = p.fontMetrics()
        p.drawText(QPointF(sp.x() - fm.horizontalAdvance(text) / 2,
                           sp.y() + fm.height() / 3), text)

    def _draw_header(self, p):
        f = QFont()
        f.setPointSize(theme.FONT_SECTION)
        f.setBold(True)
        p.setFont(f)
        p.setPen(_qcolor(theme.ON_SURFACE))
        p.drawText(18, 26, self.title)

        f2 = QFont()
        f2.setPointSize(theme.FONT_BODY)
        p.setFont(f2)
        p.setPen(_qcolor(theme.ON_SURFACE_MUTED))
        p.drawText(18, 44, f"{self.view_name()} view · drag to orbit · "
                           "scroll to zoom · click a tooth for its graphs · "
                           "dashed = no sensor mapped")

    def _magnitude(self, palmer, vals):
        """|resultant| for one tooth -- the same vector the arrow is drawn along."""
        tooth = self._tooth_by_palmer.get(palmer)
        return vnorm(self._resultant_vector(tooth, vals)) if tooth else 0.0


def _set_active(buttons, index):
    """Mark one button of an exclusive group active, or none when `index` is
    None (which is what an orbit off a camera preset leaves behind)."""
    for i, btn in enumerate(buttons):
        btn.setProperty("variant", "primary" if i == index else "")
        btn.style().unpolish(btn)
        btn.style().polish(btn)


# ---- the key, as a landscape bar ----

class ArchKeyBar(QWidget):
    """What the glyphs mean, laid out across the top of the tab.

    Everything the key column used to say down the right-hand side, turned on
    its side: which colour is which axis and what anatomical direction it runs
    in, how magnitude maps to length or sweep, what a ring means, and the
    notation notes. The live per-tooth numbers are *not* here -- they moved into
    the toggle panel, beside the buttons that control them.

    It reads the view rather than being told: the scale, the tooth mapping and
    the visibility flags all live there, and every one of them changes what the
    key should say. Painted, not laid out, so it can reuse the very sample-glyph
    painters the arch draws with -- a key arrow that had drifted from a real
    arrow would be worse than no key at all.

    Groups are measured and placed left to right, and one that would not fit is
    dropped rather than clipped mid-word. They are emitted most-load-bearing
    first, so a narrow bar loses the notation notes before it loses the axis
    colours. `preferred_width()` reports what it would take to lose none, which
    is what `ArchTab` uses to decide whether the bar fits beside the buttons or
    has to take a row of its own.
    """

    def __init__(self, view: "ArchView3D"):
        super().__init__()
        self.view = view
        self.setFixedHeight(KEY_BAR_H)

    # ---- geometry ----

    @staticmethod
    def _line(i):
        """Baseline of the i-th line. Four fit between the bar's padding."""
        return 22 + i * KEY_LINE

    @staticmethod
    def _adv(font, text):
        """Width of `text`, without needing a paint device -- which is what lets
        `preferred_width()` be asked before the bar has ever painted."""
        return QFontMetrics(font).horizontalAdvance(text)

    def _fonts(self):
        heading = QFont(); heading.setPointSize(theme.FONT_BODY); heading.setBold(True)
        caption = QFont(); caption.setPointSize(theme.FONT_CAPTION)
        note = QFont(); note.setPointSize(theme.FONT_CAPTION); note.setItalic(True)
        return heading, caption, note

    def _glyph_w(self):
        return 2 * KEY_CURL_R + 4 if self.view.scale.curl else 26

    def _any_resultant(self):
        scale = self.view.scale
        return scale.resultant is not None and self.view.visibility.any_resultant(
            self.view.tooth_to_cell, scale.quantity)

    def _groups(self):
        """The bar's content, in the order it is willing to lose it."""
        scale = self.view.scale
        specs = list(scale.axes)
        if self._any_resultant():
            specs.append(scale.resultant)
        return ([self._group_heading]
                + [partial(self._group_spec, spec=spec) for spec in specs]
                + [self._group_ramp, self._group_rings, self._group_notes])

    def preferred_width(self):
        """Width at which every group is drawn -- nothing dropped."""
        widths = [g(None, 0, measure=True) for g in self._groups()]
        widths = [w for w in widths if w > 0]
        return (2 * KEY_BAR_PAD + sum(widths)
                + KEY_GROUP_GAP * max(0, len(widths) - 1))

    # ---- painting ----

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(0, 0, self.width(), self.height(), _qcolor(theme.SURFACE))

        x = KEY_BAR_PAD
        limit = self.width() - KEY_BAR_PAD
        for group in self._groups():
            w = group(None, 0, measure=True)
            if w <= 0:                     # e.g. the ring legend in moment mode
                continue
            if x + w > limit:
                break
            group(p, x, measure=False)
            x += w + KEY_GROUP_GAP
        p.end()

    def _text_group(self, p, x, rows, measure):
        """Paint (or just measure) a stack of (font, colour, text) lines."""
        widest = 0
        for i, (font, color, text) in enumerate(rows):
            widest = max(widest, self._adv(font, text))
            if not measure:
                p.setFont(font)
                p.setPen(_qcolor(color))
                p.drawText(int(x), int(self._line(i)), text)
        return widest

    def _group_heading(self, p, x, measure):
        """The quantity and its unit, plus the thresholds that bound the ramp."""
        scale = self.view.scale
        heading, _, note = self._fonts()
        muted = theme.ON_SURFACE_MUTED
        rows = [(heading, theme.ON_SURFACE, scale.unit_label),
                (note, muted, f"< {scale.lo:g}: not shown"),
                (note, muted, f"clamped at {scale.hi:g}")]
        return self._text_group(p, x, rows, measure)

    def _group_spec(self, p, x, measure, spec):
        """One axis (or the resultant): its sample glyph, name and direction."""
        _, caption, _ = self._fonts()
        glyph_w = self._glyph_w()
        text_x = x + glyph_w + 6
        if not measure:
            if self.view.scale.curl:
                _key_curl(p, x, self._line(0) - 4, KEY_CURL_R,
                          CURL_MAX_SWEEP * 0.7, _qcolor(spec.color), 2.0)
            else:
                _key_arrow(p, x, self._line(0) - 4, 24, _qcolor(spec.color), 2.2)
            p.setFont(caption)
            p.setPen(_qcolor(spec.color))
            p.drawText(int(text_x), int(self._line(0)), spec.name)
            p.setPen(_qcolor(theme.ON_SURFACE_MUTED))
            p.drawText(int(x), int(self._line(1)), spec.description)
        return max(glyph_w + 6 + self._adv(caption, spec.name),
                   self._adv(caption, spec.description))

    def _group_ramp(self, p, x, measure):
        """What growing means: a sample glyph at `lo` and one at each clamp."""
        scale = self.view.scale
        _, caption, _ = self._fonts()
        title = "Sweep" if scale.curl else "Length"
        widest = self._adv(caption, title)
        if not measure:
            p.setFont(caption)
            p.setPen(_qcolor(theme.ON_SURFACE_MUTED))
            p.drawText(int(x), int(self._line(0)), title)
        fg = _qcolor(theme.ON_SURFACE)
        glyph_w = 2 * KEY_CURL_R + 4 if scale.curl else 42
        samples = [(scale.lo, CURL_MIN_SWEEP, 18, ARROW_MIN_W, fg),
                   (scale.hi, CURL_MAX_SWEEP, 38, ARROW_MAX_W, fg)]
        # A resultant clamps at its own, larger ceiling, so a maximum-length
        # arrow means one thing for a component and another for a resultant.
        # Its own sample says which, in its own colour, rather than leaving the
        # reader to size a purple arrow against the component ramp.
        if self._any_resultant():
            samples.append((scale.resultant.hi, CURL_MAX_SWEEP, 38, ARROW_MAX_W,
                            _qcolor(scale.resultant.color)))
        for i, (value, sweep, length, width, color) in enumerate(samples):
            y = self._line(1 + i)
            if not measure:
                if scale.curl:
                    _key_curl(p, x, y - 4, KEY_CURL_R, sweep, color, width)
                else:
                    _key_arrow(p, x, y - 4, length, color, width)
                p.setPen(color)
                p.drawText(int(x + glyph_w + 6), int(y), f"{value:g}")
            widest = max(widest,
                         glyph_w + 6 + self._adv(caption, f"{value:g}"))
        return widest

    def _group_rings(self, p, x, measure):
        """The ring substitution -- arrows only. A curl never degenerates that
        way, so in moment mode this group is empty and takes no width."""
        if self.view.scale.curl:
            return 0
        _, caption, _ = self._fonts()
        title = "Along view axis"
        widest = self._adv(caption, title)
        fg = _qcolor(theme.ON_SURFACE)
        if not measure:
            p.setFont(caption)
            p.setPen(_qcolor(theme.ON_SURFACE_MUTED))
            p.drawText(int(x), int(self._line(0)), title)
        for i, (toward, text) in enumerate(((True, "toward you"), (False, "away"))):
            y = self._line(1 + i)
            if not measure:
                _paint_ring(p, QPointF(x + KEY_RING_R, y - 4), KEY_RING_R,
                            fg, 1.6, toward)
                p.setPen(fg)
                p.drawText(int(x + 2 * KEY_RING_R + 8), int(y), text)
            widest = max(widest,
                         2 * KEY_RING_R + 8 + self._adv(caption, text))
        return widest

    def _group_notes(self, p, x, measure):
        """The notation the glyphs are drawn in."""
        _, _, note = self._fonts()
        muted = theme.ON_SURFACE_MUTED
        rows = [(note, muted, "each tooth's own frame")]
        if self.view.scale.curl:
            rows += [(note, muted, "right-hand rule:"),
                     (note, muted, "thumb along the axis")]
        return self._text_group(p, x, rows, measure)


# ---- the per-tooth toggle panel ----

TOGGLE_W = 34            # px, one axis/resultant button
TOGGLE_H = 24
PANEL_ML, PANEL_MR = 10, 12   # px, the column's left/right margins
ROW_LABEL_W = 38              # px, the Palmer designation at the start of a row
ROW_GAP = 3                   # px, between the label and the buttons
#: Wide enough for the label plus four buttons at their fixed size. Narrower and
#: `setFixedWidth` wins over the layout's minimum, and QBoxLayout makes up the
#: difference by shrinking the widgets that asked for a fixed size -- the Palmer
#: label goes first, and QLabel clips rather than elides.
PANEL_W = (PANEL_ML + ROW_LABEL_W + 4 * (TOGGLE_W + ROW_GAP) + PANEL_MR)
#: The scrollbar rides inside this, so it must clear a native one (17 px on
#: platforms that do not take the theme's 12).
PANEL_SCROLL_W = PANEL_W + 18
#: Three equal columns for the live numbers, so a value's x position depends on
#: which column it is in and not on how many characters the values to its left
#: happened to have. The old key column had a fixed 52 px pitch for the same
#: reason; joining variable-length strings loses it even in a monospace font.
READOUT_COL_W = (PANEL_W - PANEL_ML - PANEL_MR) // 3


@lru_cache(maxsize=16)
def _toggle_stylesheet(hex_color):
    """A compact checkable button that lights up in its own glyph colour.

    Cached: there are four distinct glyph colours and a rowful of buttons per
    tooth, so the same handful of strings would otherwise be rebuilt on every
    rebuild of the panel.

    The colour is the point: the arch, the key, the time-series plots and this
    panel all have to agree on what colour Fy is, or the toggles stop reading as
    the arrows they control. The base stylesheet's generic `:checked` rule
    (theme.py) would make every axis the same blue.
    """
    on = QColor(hex_color)
    tint = QColor(on).lighter(178)
    return f"""
    QPushButton {{
        padding: 0px;
        font-size: {theme.FONT_CAPTION}pt;
        font-weight: 600;
        color: {theme.ON_SURFACE_SUBTLE};
        background-color: {theme.SURFACE};
        border: 1px solid {theme.OUTLINE_STRONG};
        border-radius: 5px;
    }}
    QPushButton:hover {{ border-color: {on.name()}; }}
    QPushButton:checked {{
        color: {on.name()};
        background-color: {tint.name()};
        border: 1px solid {on.name()};
        font-weight: 700;
    }}
    QPushButton:disabled {{
        color: {theme.ON_SURFACE_SUBTLE};
        background-color: {theme.SURFACE_ALT};
        border-color: {theme.OUTLINE};
    }}
    """


class ToothGlyphPanel(QWidget):
    """Right-hand column of per-tooth glyph toggles.

    One row per *instrumented* tooth -- an unmapped tooth has no reading, so a
    row for it would control nothing -- carrying the three component toggles and,
    when the quantity has a resultant, a resultant toggle that supersedes them.
    A master row above toggles one axis across every tooth at once.

    The panel owns no state: every click writes through to the `GlyphVisibility`
    it shares with the view, which is what lets `rebuild()` throw the widgets
    away and remake them on a mode switch or a live config edit without losing a
    single toggle.
    """

    #: Emitted after any toggle, so the view can repaint immediately rather than
    #: waiting out the refresh timer.
    changed = pyqtSignal()

    #: Columns per row: the three components plus the resultant. The resultant
    #: column is built in both modes and hidden in moment mode, so a DATA switch
    #: is a re-point rather than a rebuild.
    N_COLS = GlyphVisibility.N_AXES + 1

    def __init__(self, visibility: GlyphVisibility):
        super().__init__()
        self.visibility = visibility
        self.palmers = []
        self.scale = None
        # A rule down the left edge, so the toggles read as their own column
        # rather than as part of the arch beside them. A plain QWidget ignores a
        # stylesheet background and border unless it is told to style itself --
        # without this the rule is simply never painted, and the background only
        # looks right because theme.SURFACE happens to be the window colour.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            f"ToothGlyphPanel {{ background: {theme.SURFACE}; "
            f"border-left: 1px solid {theme.OUTLINE}; }}")
        self._head_labels = []        # column heads, axes then resultant
        self._axis_buttons = {}       # palmer -> [QPushButton x3]
        self._resultant_buttons = {}  # palmer -> QPushButton
        self._master_buttons = []     # the "All" row, axes then resultant
        self._readouts = {}           # palmer -> [QLabel x3] of live values
        self.setFixedWidth(PANEL_W)

        self._grid = QVBoxLayout(self)
        self._grid.setContentsMargins(10, 8, 12, 10)
        self._grid.setSpacing(4)

    # ---- construction ----

    def retarget(self, scale: GlyphScale):
        """Point the existing rows at another quantity, without rebuilding them.

        A DATA switch changes the button labels, their colours, whether the
        resultant column applies and which set of flags is showing -- but not
        which teeth have rows or what those rows are made of. Re-pointing them is
        a handful of `setText` calls; rebuilding is ~68 widgets and ~68 freshly
        parsed stylesheets, which on the Pi is most of a second of frozen UI on a
        widget otherwise held to a 50 ms frame.

        Falls back to a full rebuild when there is no structure to re-point.
        """
        if self.scale is None or not self.palmers:
            return self.rebuild(self.palmers, scale)
        self.scale = scale
        self._apply_scale()

    def rebuild(self, palmers, scale: GlyphScale):
        """Re-lay the column from scratch, for this tooth list and quantity.

        Called on construction and when a live sensor-config edit remaps the
        cells -- i.e. when the set of rows itself changes. A DATA switch goes
        through `retarget` instead.
        """
        self.palmers = list(palmers)
        self.scale = scale
        self._head_labels = []
        self._axis_buttons = {}
        self._resultant_buttons = {}
        self._master_buttons = []
        self._readouts = {}
        self._clear()

        self._grid.addWidget(self._caption("TEETH"))
        if not self.palmers:
            note = QLabel("no cell mapped to\na lower-arch tooth")
            note.setWordWrap(True)
            note.setStyleSheet(
                f"font-size: {theme.FONT_CAPTION}pt; "
                f"color: {theme.ON_SURFACE_SUBTLE}; background: transparent;")
            self._grid.addWidget(note)
            self._grid.addStretch()
            return

        self._grid.addLayout(self._header_row())
        self._grid.addLayout(self._master_row())
        self._grid.addSpacing(2)

        for palmer in self.palmers:
            self._grid.addLayout(self._tooth_row(palmer))
        self._grid.addStretch()
        self._apply_scale()

    def _clear(self):
        self._clear_layout(self._grid)

    @classmethod
    def _clear_layout(cls, layout):
        """Empty a layout of its widgets and nested rows.

        `setParent(None)` first, not `deleteLater()` alone: taking an item out of
        a layout stops it being *positioned*, but the widget is still a visible
        child of the panel until the deferred delete actually runs, and the next
        rebuild draws the new rows straight over the old ones.
        """
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
            elif item.layout() is not None:
                cls._clear_layout(item.layout())

    def _caption(self, text):
        label = QLabel(text)
        label.setStyleSheet(
            f"font-size: {theme.FONT_CAPTION}pt; color: {theme.ON_SURFACE_MUTED}; "
            f"font-weight: 700; letter-spacing: 1px; background: transparent;")
        return label

    @property
    def _has_resultant(self):
        return self.scale is not None and self.scale.resultant is not None

    def _row(self):
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(3)
        return row

    def _row_label(self, text, color=None, bold=False):
        label = QLabel(text)
        label.setFixedWidth(38)
        label.setStyleSheet(
            f"font-size: {theme.FONT_CAPTION}pt; "
            f"color: {color or theme.ON_SURFACE}; "
            f"font-weight: {700 if bold else 600}; background: transparent;")
        return label

    def _column_head(self):
        """An empty column head; `_style_head` gives it its text and colour."""
        label = QLabel()
        label.setFixedWidth(TOGGLE_W)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return label

    @staticmethod
    def _style_head(label, text, color):
        label.setText(text)
        label.setStyleSheet(
            f"font-size: {theme.FONT_CAPTION}pt; color: {color}; "
            f"font-weight: 700; background: transparent;")

    @staticmethod
    def _set_toggle_color(btn, hex_color):
        """Dress a toggle in a glyph colour, skipping the restyle when it
        already wears it -- a stylesheet assignment costs a parse and a
        polish even when the string is identical."""
        if btn.property("glyphColor") == hex_color:
            return
        btn.setProperty("glyphColor", hex_color)
        btn.setStyleSheet(_toggle_stylesheet(hex_color))

    def _set_readout(self, label, text, color):
        """One live number. The colour is a stylesheet, so it is only touched
        when a toggle changes it -- the text changes every sample and must stay
        a plain `setText`, not a rich-text parse."""
        if label.property("readoutColor") != color:
            label.setProperty("readoutColor", color)
            label.setStyleSheet(
                f"font-family: monospace; font-size: {theme.FONT_CAPTION}pt; "
                f"color: {color}; background: transparent;")
        label.setText(text)

    def _header_row(self):
        """Column heads. Their text and colour are the quantity's, so they say
        Fx/Fy/Fz in force mode and Mx/My/Mz in moment mode -- `_apply_scale`
        fills them in, here and after a DATA switch alike."""
        row = self._row()
        row.addWidget(self._row_label("", theme.ON_SURFACE_MUTED))
        self._head_labels = [self._column_head() for _ in range(self.N_COLS)]
        for label in self._head_labels:
            row.addWidget(label)
        row.addStretch()
        return row

    def _button(self, on_click):
        btn = QPushButton()
        btn.setCheckable(True)
        btn.setFixedSize(TOGGLE_W, TOGGLE_H)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(on_click)
        return btn

    def _master_row(self):
        """One click clears an axis across the whole arch, the next restores it.

        Its buttons are momentary in effect but still checkable, so they read as
        "every tooth has this on" at a glance; `_sync_enabled` keeps them honest.
        """
        row = self._row()
        row.addWidget(self._row_label("All", theme.ON_SURFACE_MUTED, bold=True))
        self._master_buttons = [
            self._button(partial(self._toggle_all, rank))
            for rank in range(GlyphVisibility.N_AXES)]
        self._master_buttons.append(self._button(self._toggle_all_resultant))
        for btn in self._master_buttons:
            btn.setText("●")
            row.addWidget(btn)
        row.addStretch()
        return row

    def _tooth_row(self, palmer):
        """This tooth's toggles, with its live reading printed underneath.

        The numbers sit here rather than in the key because they are about this
        tooth, not about the notation -- and because they belong next to the
        buttons that decide which of them are on the arch.

        The resultant button is built even in moment mode and hidden there,
        which is what lets a DATA switch re-point these rows instead of
        rebuilding them.
        """
        row = self._row()
        row.addWidget(self._row_label(palmer))
        buttons = [self._button(partial(self._toggle_axis, palmer, rank))
                   for rank in range(GlyphVisibility.N_AXES)]
        self._axis_buttons[palmer] = buttons
        resultant = self._button(partial(self._toggle_resultant, palmer))
        resultant.setText("R")
        self._resultant_buttons[palmer] = resultant
        for btn in buttons + [resultant]:
            row.addWidget(btn)
        row.addStretch()

        numbers = self._row()
        numbers.setSpacing(0)
        labels = []
        for _ in range(GlyphVisibility.N_AXES):
            label = QLabel("--")
            label.setFixedWidth(READOUT_COL_W)
            labels.append(label)
            numbers.addWidget(label)
        numbers.addStretch()
        self._readouts[palmer] = labels

        cell = QVBoxLayout()
        cell.setContentsMargins(0, 0, 0, 0)
        cell.setSpacing(1)
        cell.addLayout(row)
        cell.addLayout(numbers)
        return cell

    # ---- the quantity showing ----

    def _apply_scale(self):
        """Point every built widget at `self.scale`.

        The one place the axis names, the glyph colours and the resultant
        column's applicability reach the widgets -- so `rebuild` and `retarget`
        end up in exactly the same state.
        """
        specs = list(self.scale.axes)
        if self._has_resultant:
            specs.append(self.scale.resultant)
        for i, label in enumerate(self._head_labels):
            spec = specs[i] if i < len(specs) else None
            label.setVisible(spec is not None)
            self._style_head(label, spec.name if spec else "",
                             spec.color if spec else theme.ON_SURFACE_MUTED)
        for rank, btn in enumerate(self._master_buttons):
            self._retarget_button(btn, specs, rank, "●")
        for palmer, buttons in self._axis_buttons.items():
            for rank, btn in enumerate(buttons):
                self._retarget_button(btn, specs, rank,
                                      self.scale.axes[rank].name[-1].upper())
            self._retarget_button(self._resultant_buttons[palmer], specs,
                                  GlyphVisibility.N_AXES, "R")
        for labels in self._readouts.values():
            for label in labels:
                self._set_readout(label, "--", theme.ON_SURFACE_SUBTLE)
        self._sync_enabled()

    def _retarget_button(self, btn, specs, rank, text):
        """A toggle in the rank-th column: shown only when that column applies,
        wearing that column's glyph colour."""
        spec = specs[rank] if rank < len(specs) else None
        btn.setVisible(spec is not None)
        if spec is None:
            return
        btn.setText(text)
        self._set_toggle_color(btn, spec.color)

    # ---- state <-> buttons ----

    def _sync_enabled(self):
        """Push `GlyphVisibility` back onto the buttons.

        A tooth showing its resultant draws *only* that, so its three component
        buttons grey out rather than sitting there checked and lying about what
        is on the arch. The master row answers for the teeth those buttons are
        live on and no others -- a row of teeth that are all on their resultants
        has no component axes on the arch, so its master buttons say so and go
        dead rather than writing through controls the user cannot see.
        """
        quantity = self.scale.quantity
        vis = self.visibility
        for palmer, buttons in self._axis_buttons.items():
            resultant = self._has_resultant and vis.resultant(palmer, quantity)
            for rank, btn in enumerate(buttons):
                btn.setChecked(vis.axis(palmer, quantity, rank))
                btn.setEnabled(not resultant)
            self._resultant_buttons[palmer].setChecked(resultant)
        component = self._component_palmers()
        for rank, btn in enumerate(self._master_buttons):
            if rank < GlyphVisibility.N_AXES:
                btn.setEnabled(bool(component))
                btn.setChecked(bool(component)
                               and vis.all_axis(component, quantity, rank))
            else:
                btn.setChecked(bool(self.palmers)
                               and all(vis.resultant(p, quantity)
                                       for p in self.palmers))

    def _component_palmers(self):
        """The teeth whose component toggles are live -- i.e. not the ones
        currently superseded by their own resultant."""
        quantity = self.scale.quantity
        return [p for p in self.palmers
                if not (self._has_resultant
                        and self.visibility.resultant(p, quantity))]

    def set_readouts(self, readouts):
        """Print one frame's numbers, from `ArchView3D.readouts()`.

        Each entry is already (text, colour) -- the view resolved which axes are
        showing and whether the tooth is on its resultant, so this only has to
        set the labels. A tooth on its resultant has one number, not three, so
        the columns it does not use are blanked rather than left stale.
        """
        for palmer, labels in self._readouts.items():
            parts = readouts.get(palmer) or [("--", theme.ON_SURFACE_SUBTLE)]
            for i, label in enumerate(labels):
                text, color = parts[i] if i < len(parts) \
                    else ("", theme.ON_SURFACE_SUBTLE)
                self._set_readout(label, text, color)

    def _emit(self):
        self._sync_enabled()
        self.changed.emit()

    def _toggle_axis(self, palmer, rank, checked):
        self.visibility.set_axis(palmer, self.scale.quantity, rank, checked)
        self._emit()

    def _toggle_resultant(self, palmer, checked):
        self.visibility.set_resultant(palmer, self.scale.quantity, checked)
        self._emit()

    def _toggle_all(self, rank, _checked):
        quantity = self.scale.quantity
        palmers = self._component_palmers()
        if not palmers:
            return
        on = not self.visibility.all_axis(palmers, quantity, rank)
        self.visibility.set_axis_all(palmers, quantity, rank, on)
        self._emit()

    def _toggle_all_resultant(self, _checked):
        quantity = self.scale.quantity
        on = not all(self.visibility.resultant(p, quantity)
                     for p in self.palmers)
        self.visibility.set_resultant_all(self.palmers, quantity, on)
        self._emit()


class ArchTab(QWidget):
    """One 3D lower arch, with camera presets and the arrow toggles above it."""

    #: Relayed from `ArchView3D.tooth_picked`: a crown was clicked, and this is
    #: the cell index behind it. A relay rather than letting the dashboard reach
    #: into `.view` -- it touches this widget through one method today, and that
    #: is worth keeping.
    cell_picked = pyqtSignal(int)

    @staticmethod
    def _build_tooth_to_cell(tooth_per_cell):
        """Palmer designation -> cell index, for teeth on the lower arch.

        A cell with no `tooth`, or one naming an upper tooth, simply never
        appears -- `normalize_tooth` is the single gate that decides which."""
        mapping = {}
        for cell_idx, tooth in enumerate(tooth_per_cell or []):
            palmer = normalize_tooth(tooth)
            if palmer is not None:
                mapping[palmer] = cell_idx
        return mapping

    def __init__(self, store, tooth_per_cell=None):
        super().__init__()
        self.store = store
        self.tooth_per_cell = tooth_per_cell or []

        self.visibility = GlyphVisibility()
        self.view = ArchView3D(
            store,
            self._build_tooth_to_cell(self.tooth_per_cell),
            scale=DATA_SCALES[0],
            visibility=self.visibility,
        )
        self.panel = ToothGlyphPanel(self.visibility)
        self.key = ArchKeyBar(self.view)
        self.panel.changed.connect(self.view.update)
        # A tooth switching to its resultant adds a legend group and a ceiling
        # to the key, so the key both needs repainting and may no longer fit
        # where it is -- and a key that has quietly dropped a group is exactly
        # what the measured placement exists to prevent.
        self.panel.changed.connect(self._key_content_changed)

        # Two rows, not one: all the buttons side by side would put the tab's
        # minimum width near 1000 px, wider than the small screens the Pi runs.
        camera_bar = QHBoxLayout()
        camera_bar.setContentsMargins(14, 8, 14, 2)
        camera_bar.setSpacing(8)
        self.preset_buttons = self._button_group(
            camera_bar, "VIEW", [name for name, _, _ in PRESETS],
            self._pick_preset)
        reset = QPushButton("Reset")
        reset.setMinimumHeight(32)
        reset.setCursor(Qt.CursorShape.PointingHandCursor)
        reset.clicked.connect(self._reset)
        camera_bar.addWidget(reset)
        camera_bar.addStretch()

        arrow_bar = QHBoxLayout()
        arrow_bar.setContentsMargins(14, 0, 14, 4)
        arrow_bar.setSpacing(8)
        self.data_buttons = self._button_group(
            arrow_bar, "DATA", [sc.quantity for sc in DATA_SCALES],
            self._pick_data)
        arrow_bar.addStretch()

        # The buttons stack at the left of the top strip and the key runs
        # landscape along the rest of it, so the whole top of the tab is "what
        # you are looking at and what it means" and neither eats the arch.
        controls = QVBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(0)
        controls.addLayout(camera_bar)
        controls.addLayout(arrow_bar)
        controls.addStretch()

        strip = QFrame()
        strip.setObjectName("archTopStrip")
        strip.setStyleSheet(
            f"#archTopStrip {{ background: {theme.SURFACE}; "
            f"border-bottom: 1px solid {theme.OUTLINE}; }}")
        self._strip_col = QVBoxLayout(strip)
        # One pixel at the bottom is the strip's rule; without it the key bar's
        # own background paints straight over the border.
        self._strip_col.setContentsMargins(0, 0, 0, 1)
        self._strip_col.setSpacing(0)
        self._controls = controls
        self._top_row = QHBoxLayout()
        self._top_row.setContentsMargins(0, 0, 0, 0)
        self._top_row.setSpacing(10)
        self._top_row.addLayout(controls)
        self._strip_col.addLayout(self._top_row)
        self._key_beside = None
        self._key_width = None
        self._place_key()

        # The panel scrolls: a fully instrumented arch is sixteen rows, more
        # than fits beside the view on a Pi-sized screen.
        scroller = QScrollArea()
        scroller.setWidget(self.panel)
        scroller.setWidgetResizable(True)
        scroller.setFrameShape(QFrame.Shape.NoFrame)
        scroller.setFixedWidth(PANEL_SCROLL_W)
        scroller.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self.view, 1)
        body.addWidget(scroller)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(strip)
        root.addLayout(body, 1)

        _set_active(self.preset_buttons, DEFAULT_PRESET)
        _set_active(self.data_buttons, 0)
        self._rebuild_panel()
        self.view.preset_left.connect(
            lambda: _set_active(self.preset_buttons, None))
        self.view.tooth_picked.connect(self.cell_picked)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(REFRESH_MS)

    @staticmethod
    def _button_group(bar, title, names, on_pick):
        """A captioned row of buttons of which exactly one is active."""
        label = QLabel(title)
        label.setStyleSheet(
            f"font-size: {theme.FONT_CAPTION}pt; color: {theme.ON_SURFACE_MUTED}; "
            f"font-weight: 700; letter-spacing: 1px; background: transparent;")
        bar.addWidget(label)
        buttons = []
        for i, name in enumerate(names):
            btn = QPushButton(name)
            btn.setMinimumHeight(32)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _checked, idx=i: on_pick(idx))
            bar.addWidget(btn)
            buttons.append(btn)
        return buttons

    def _pick_preset(self, index):
        self.view.set_preset(index)
        _set_active(self.preset_buttons, index)

    def _pick_data(self, index):
        """Force <-> moment. The camera is unaffected, and each quantity keeps
        its own per-tooth toggles -- so the panel is re-pointed at the new scale
        rather than carrying the old one's state across."""
        self.view.set_scale(DATA_SCALES[index])
        _set_active(self.data_buttons, index)
        self.panel.retarget(self.view.scale)
        self._push_readouts()
        self._key_content_changed()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_key()

    def _key_content_changed(self):
        """The key's content changed, so what it needs may have too."""
        self._key_width = None
        self._place_key()
        self.key.update()

    def _key_needs(self):
        """`ArchKeyBar.preferred_width()`, cached.

        The measure pass walks every group with its own QFontMetrics, which is
        ~1 ms on the Pi -- affordable when the answer changes, wasteful on every
        step of a window drag, where it is constant. Only a scale switch, a
        resultant toggle or a config edit can move it, and each invalidates.
        """
        if self._key_width is None:
            self._key_width = self.key.preferred_width()
        return self._key_width

    def _place_key(self):
        """Beside the buttons if the key fits there, on its own row if not.

        The key wants ~740 px in force mode, ~845 once a tooth is on its
        resultant. Beside the buttons it only gets the tab's width less the
        button column and the gap between them, which on the small screens the
        Pi runs is not enough -- and a key that has quietly dropped `Fz` is
        worse than one that took a second row. So the choice is measured, not a
        breakpoint: `preferred_width()` says what it needs, and it moves.

        The gap is part of the sum: leaving it out claims a fit at widths where
        the key is then handed `spacing` px less than it asked for and silently
        truncates -- the very outcome being measured against.
        """
        available = (self.width() - self._controls.sizeHint().width()
                     - self._top_row.spacing()
                     - self._strip_col.contentsMargins().left()
                     - self._strip_col.contentsMargins().right())
        beside = available >= self._key_needs()
        if beside == self._key_beside:
            return
        self._key_beside = beside
        self._top_row.removeWidget(self.key)
        self._strip_col.removeWidget(self.key)
        if beside:
            self._top_row.addWidget(self.key, 1)
        else:
            self._strip_col.addWidget(self.key)

    def _rebuild_panel(self):
        """Re-lay the toggle column for the mapped teeth and the current scale.

        `GlyphVisibility` outlives the widgets, so a rebuild never costs a
        toggle -- teeth that stay mapped come back exactly as they were.
        """
        palmers = sorted(self.view.tooth_to_cell, key=LOWER_ARCH_ORDER.index)
        self.panel.rebuild(palmers, self.view.scale)
        self._push_readouts()

    def _push_readouts(self):
        """Fill the panel's numbers now, rather than leaving a column of `--`
        until the next timer tick. A rebuild or a re-point resets them to their
        placeholder while the arch beside them is already painting the new
        quantity."""
        self.panel.set_readouts(self.view.readouts(self.view.take_readings()))

    def _reset(self):
        """Camera only -- which data the arrows show is a separate choice."""
        self.view.reset_view()
        _set_active(self.preset_buttons, DEFAULT_PRESET)

    def set_tooth_per_cell(self, tooth_per_cell):
        """Re-map which load cell drives each tooth, so a live sensor-config
        change is reflected in the arrows. The view reads force values from the
        DataStore, which already holds post-tare, post-compensation readings."""
        self.tooth_per_cell = list(tooth_per_cell or [])
        self.view.tooth_to_cell = self._build_tooth_to_cell(self.tooth_per_cell)
        # The parked sample was read under the old mapping; a tooth this edit
        # unmapped must not paint one last set of arrows from it.
        self.view.drop_readings()
        # Same reasoning for the pointer: a crown this edit unmapped must not
        # keep the pointing hand until the mouse next moves.
        self.view.clear_hover()
        self._rebuild_panel()
        # A remapped tooth can carry a resultant flag from the last time it was
        # mapped, which changes what the key needs.
        self._key_content_changed()
        self.view.update()

    def _refresh(self):
        """One tick: read the store once, print it, then repaint the arch.

        The order matters. `take_readings` parks the sample it read for the
        `update()` below to paint, so the numbers in the panel and the glyphs on
        the arch are the same sample even though they are now separate widgets.
        """
        if not self.isVisible():
            return
        self.panel.set_readouts(self.view.readouts(self.view.take_readings()))
        self.view.update()
