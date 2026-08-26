"""3D arch view: one lower arch, three force arrows per instrumented tooth.

`arch_model.py` owns the arch geometry and the reading-to-arrow scale; `proj3d.py`
owns the camera. This module is the view: it fits the arch to the pane, turns one
frame's readings into depth-sorted primitives, paints them with QPainter, and
wires up the camera controls.

Arrow length is linear in |value| across the scale's [lo, hi]: below `lo` the
axis draws nothing, at or above `hi` it clamps to the longest arrow. So a longer
arrow is always more force, and an absent arrow always means "under threshold".

No OpenGL -- the software pipeline behaves the same on the Pi as it does under
`--debug` on a laptop. Visibility is back-face culling plus a painter's-algorithm
depth sort over crowns and arrows together.
"""

import math
from dataclasses import dataclass
from functools import partial

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel
from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF, pyqtSignal
from PyQt6.QtGui import QPainter, QPen, QBrush, QColor, QFont, QPolygonF

from graphDash.constants import REFRESH_MS
from graphDash.ui import theme
from graphDash.ui.proj3d import Camera, vdot, vunit, vmad
from graphDash.ui.arch_model import (
    build_arch, GlyphScale, FORCE_GLYPH, MOMENT_GLYPH,
    LOWER_ARCH_ORDER, PALMER_LABEL, TOOTH_TYPE,
)

__all__ = ["ArchTab", "ArchView3D", "FORCE_GLYPH", "MOMENT_GLYPH", "PRESETS"]


def _qcolor(hex_str):
    return QColor(hex_str)


def _lerp(a, b, t):
    return a + (b - a) * t


# ---- glyph geometry (lengths are multiples of the arch's `unit`; widths px) ----
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
# Rings mark their own tooth's occlusal surface, so nothing of that tooth may
# cover them. Primitives paint far-to-near, so the nearest possible depth puts
# them last.
OVERLAY_DEPTH  = float("-inf")

# ---- camera ----
# Presets: (name, yaw deg, pitch deg).
PRESETS = (
    ("Oblique",  -18.0, 40.0),
    ("Occlusal",   0.0, 89.5),
    ("Anterior",   0.0,  6.0),
)
DEFAULT_PRESET = 0
# The arch spans ~2 x 1.3 world units. Keep the eye well outside that so a near
# tooth can never approach the camera and blow up under the perspective divide
# -- the view fits the pane by scaling, so a longer lens costs nothing.
CAM_DISTANCE = 7.0
FIT_MARGIN = 0.86        # leaves room for arrows, which are not fitted
ZOOM_MIN, ZOOM_MAX = 0.45, 4.0
ORBIT_SENS = 0.008       # rad per pixel of drag

# ---- chrome ----
KEY_W = 204              # fixed pixel width of the painted key column
KEY_ARROW_HEAD = 8.0     # px, for the key's flat sample arrows
KEY_RING_R = 7.0
TOP_PAD, BOTTOM_PAD, SIDE_PAD = 58, 22, 18

# Directional light for crown shading, in world coordinates.
LIGHT_DIR = vunit((0.35, -0.55, 0.78))


def _shade(color: QColor, f: float) -> QColor:
    """Lambert-shade `color`: f = 0 is a deep tint, f = 1 the color itself."""
    t = 0.60 + 0.40 * max(0.0, min(1.0, f))
    return QColor(
        int(color.red() * t), int(color.green() * t), int(color.blue() * t)
    )


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

    def place(self, world):
        """(screen point, depth) for one world point."""
        u, v, depth = self.projector.project(world)
        return QPointF(self.cx + self.scale_px * u,
                       self.cy - self.scale_px * v), depth

    def point(self, world) -> QPointF:
        return self.place(world)[0]

    def depth(self, world) -> float:
        return self.place(world)[1]

    def hidden(self, normal) -> bool:
        """True when a face with this outward normal points away from the eye."""
        return vdot(normal, self.projector.fwd) >= 0

    def toward_viewer(self, direction) -> bool:
        return vdot(direction, self.projector.fwd) < 0


# ---- screen-space painting primitives (shared by the arch and its key) ----

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


class ArchView3D(QWidget):
    """The 3D arch itself: orbiting camera, extruded crowns, force arrows."""

    #: Emitted when a mouse orbit moves the camera off the selected preset.
    preset_left = pyqtSignal()

    def __init__(self, store, tooth_to_cell: dict, scale: GlyphScale,
                 title: str = "Lower Arch — Force Components (3D)"):
        super().__init__()
        self.store = store
        self.tooth_to_cell = tooth_to_cell
        self.scale = scale
        self.title = title
        self.arch = build_arch()

        ys = [t.center[1] for t in self.arch.teeth]
        self.camera = Camera(
            target=(0.0, (min(ys) + max(ys)) / 2, self.arch.unit * 0.5),
            distance=CAM_DISTANCE,
        )
        self.zoom = 1.0
        self._drag_pos = None
        self.set_preset(DEFAULT_PRESET)

        self.setMinimumSize(460, 420)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

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
        if self.preset_index is not None and (dx or dy):
            self.preset_index = None
            self.preset_left.emit()
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

    def _readings(self):
        """{tooth number: 6-axis reading} for every instrumented tooth.

        One store read per cell per frame, shared by the arrows and the key's
        numeric readout so the two can never show different samples.
        """
        latest = {}
        for number, cell_idx in self.tooth_to_cell.items():
            if cell_idx < self.store.n_cells:
                vals = self.store.latest(cell_idx)
                if vals is not None:
                    latest[number] = vals
        return latest

    # ---- painting ----

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, _qcolor(theme.SURFACE))

        pane_w = max(120, w - KEY_W)
        frame = _Frame.fit(self.arch, self.camera, self.zoom, QRectF(
            SIDE_PAD, TOP_PAD,
            max(60, pane_w - 2 * SIDE_PAD), max(60, h - TOP_PAD - BOTTOM_PAD)))
        readings = self._readings()

        p.save()
        p.setClipRect(QRectF(0, 0, pane_w, h))
        self._draw_guide(p, frame)
        for _, paint in sorted(self._primitives(frame, readings),
                               key=lambda prim: -prim[0]):
            paint(p)
        for tooth in self.arch.teeth:
            self._draw_label(p, frame, tooth)
        p.restore()

        self._draw_key(p, pane_w, h, readings)
        self._draw_header(p)
        p.end()

    def _primitives(self, frame, readings):
        """Yield (depth, paint) for everything in the scene, unordered.

        Depth is distance from the eye, so the caller paints in descending
        order: far things first, near things over them.
        """
        for tooth in self.arch.teeth:
            mapped = tooth.number in self.tooth_to_cell
            apex_depth = frame.depth(tooth.apex)
            draw = self._draw_crown if mapped else self._draw_footprint
            yield apex_depth, partial(draw, frame=frame, tooth=tooth)

            vals = readings.get(tooth.number)
            if vals is not None:
                yield from self._arrow_primitives(frame, tooth, vals, apex_depth)

    def _arrow_primitives(self, frame, tooth, vals, apex_depth):
        """One primitive per above-threshold component of this tooth.

        An arrow's depth is clamped to its own tooth's apex so the crown it grows
        out of can never swallow it -- an intrusive -Fz points straight into the
        tooth body. Teeth nearer the camera still cover it.
        """
        for axis, direction in zip(self.scale.axes, tooth.frame):
            value = vals[axis.index]
            f = self.scale.frac(value)
            if f is None:
                continue
            if value < 0:
                direction = (-direction[0], -direction[1], -direction[2])
            length = frame.unit * _lerp(ARROW_MIN_LEN, ARROW_MAX_LEN, f)
            width = _lerp(ARROW_MIN_W, ARROW_MAX_W, f)
            color = _qcolor(axis.color)

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

    def _draw_footprint(self, p, frame, tooth):
        """A tooth with no cell mapped to it: a flat outline in the occlusal
        plane, so the instrumented crowns are the only things standing up."""
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_qcolor(theme.OUTLINE_STRONG), 1.1, Qt.PenStyle.DashLine))
        p.drawPolygon(QPolygonF([frame.point(pt) for pt in tooth.base]))

    def _draw_crown(self, p, frame, tooth):
        """An instrumented tooth: the extruded prism plus its arrow origin."""
        base = [frame.place(pt) for pt in tooth.base]
        top = [frame.place(pt) for pt in tooth.top]
        fill = _qcolor(theme.TOOTH_FILL)

        # Sides: cull the faces pointing away, then paint the rest back-to-front.
        quads = [
            ((base[i][1] + base[j][1] + top[i][1] + top[j][1]) / 4,
             i, j, max(0.0, vdot(nrm, LIGHT_DIR)))
            for i, j, nrm in tooth.edges() if not frame.hidden(nrm)
        ]
        quads.sort(key=lambda q: -q[0])
        p.setPen(Qt.PenStyle.NoPen)
        for _, i, j, lam in quads:
            p.setBrush(QBrush(_shade(fill, lam)))
            p.drawPolygon(QPolygonF([base[i][0], base[j][0], top[j][0], top[i][0]]))

        # Occlusal face last: with the camera always above the plane (see
        # Camera.PITCH_MIN) it is the nearest face of the prism.
        p.setBrush(QBrush(_shade(fill, vdot(tooth.e_z, LIGHT_DIR))))
        p.setPen(QPen(_qcolor(theme.OUTLINE_STRONG), 1.0))
        p.drawPolygon(QPolygonF([sp for sp, _ in top]))

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
        mapped = tooth.number in self.tooth_to_cell
        p.setPen(_qcolor(theme.ON_SURFACE if mapped else theme.ON_SURFACE_SUBTLE))
        text = PALMER_LABEL.get(tooth.number, str(tooth.number))
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
                           "scroll to zoom · dashed = no sensor mapped")

    # ---- key column ----

    def _draw_key(self, p, x, h, readings):
        scale = self.scale
        left = x + 12
        cur = 26
        fg = _qcolor(theme.ON_SURFACE)
        muted = _qcolor(theme.ON_SURFACE_MUTED)

        heading = QFont(); heading.setPointSize(theme.FONT_BODY); heading.setBold(True)
        caption = QFont(); caption.setPointSize(theme.FONT_CAPTION)
        note = QFont(); note.setPointSize(theme.FONT_CAPTION); note.setItalic(True)
        mono = QFont("monospace"); mono.setPointSize(theme.FONT_CAPTION)

        p.setFont(heading)
        p.setPen(fg)
        p.drawText(int(left), int(cur), scale.unit_label)
        cur += 20

        # Which arrow is which axis.
        for axis in scale.axes:
            self._key_arrow(p, left, cur, 26, _qcolor(axis.color), 2.2)
            p.setFont(caption)
            p.setPen(fg)
            p.drawText(int(left + 34), int(cur + 4), axis.name)
            p.setPen(muted)
            p.drawText(int(left + 34), int(cur + 16), axis.description)
            cur += 30

        cur += 4
        p.setFont(caption)
        p.setPen(muted)
        p.drawText(int(left), int(cur), "Length")
        cur += 14
        for value, plen, width in ((scale.lo, 24, ARROW_MIN_W),
                                   (scale.hi, 58, ARROW_MAX_W)):
            self._key_arrow(p, left, cur, plen, fg, width)
            p.setFont(caption)
            p.setPen(fg)
            p.drawText(int(left + 64), int(cur + 4), f"{value:g}")
            cur += 22

        # Ring glyph: what an arrow becomes when it aims along the view axis.
        cur += 6
        p.setFont(caption)
        p.setPen(muted)
        p.drawText(int(left), int(cur), "Along view axis")
        cur += 16
        for toward, text in ((True, "toward you"), (False, "away")):
            _paint_ring(p, QPointF(left + 8, cur), KEY_RING_R, fg, 1.8, toward)
            p.setPen(fg)
            p.drawText(int(left + 24), int(cur + 4), text)
            cur += 20

        cur += 6
        p.setFont(note)
        p.setPen(muted)
        for line in (f"< {scale.lo:g}: not shown", f"clamped at {scale.hi:g}",
                     "each tooth's own frame"):
            p.drawText(int(left), int(cur), line)
            cur += 14
        cur += 8

        # Live values for the instrumented teeth -- an arrow says which way,
        # this says how much.
        if not self.tooth_to_cell:
            p.drawText(int(left), int(cur), "no cell mapped to")
            p.drawText(int(left), int(cur + 13), "a lower-arch tooth")
            return

        p.setFont(caption)
        p.setPen(muted)
        p.drawText(int(left), int(cur), "Live")
        cur += 14
        p.setFont(mono)
        for number in sorted(self.tooth_to_cell, key=LOWER_ARCH_ORDER.index):
            if cur > h - 26:
                break
            p.setPen(fg)
            p.drawText(int(left), int(cur), PALMER_LABEL.get(number, str(number)))
            vals = readings.get(number)
            if vals is None:
                p.setPen(_qcolor(theme.ON_SURFACE_SUBTLE))
                p.drawText(int(left + 34), int(cur), "--")
            else:
                for i, axis in enumerate(scale.axes):
                    v = vals[axis.index]
                    # Drop the decimals on big readings rather than run the
                    # column off the edge of the key.
                    p.setPen(_qcolor(axis.color))
                    p.drawText(int(left + 34 + i * 52), int(cur),
                               f"{v:+.2f}" if abs(v) < 100 else f"{v:+.0f}")
            cur += 14

    @staticmethod
    def _key_arrow(p, x, y, length, color, width):
        """Straight right-pointing sample arrow, in the key's flat 2D space."""
        neck_x = x + length - KEY_ARROW_HEAD * 0.85
        _paint_arrow(p, QPointF(x, y), QPointF(neck_x, y),
                     QPointF(x + length, y), color, width)


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
        self.view.preset_left.connect(lambda: self._highlight_preset(None))

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
        """Mark the active preset, or none of them once the user has orbited."""
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
