import math
from dataclasses import dataclass
from typing import Optional

from PyQt6.QtWidgets import QWidget, QHBoxLayout
from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF
from PyQt6.QtGui import (
    QPainter, QPen, QBrush, QColor, QPainterPath, QFont, QPolygonF,
)

from graphDash.constants import REFRESH_MS, FORCE_COLORS, MOMENT_COLORS
from graphDash.ui import theme


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

# (width_factor, height_factor) relative to the arch's base unit.
# Widths drive both the drawn tooth size AND its footprint along the arc
# (teeth are placed by cumulative arc-length so they sit side-by-side, touching).
TYPE_SIZE = {
    'molar':    (1.35, 1.20),
    'premolar': (0.90, 0.95),
    'canine':   (0.75, 1.10),
    'incisor':  (0.60, 0.95),
}

# Small gap between adjacent teeth along the arc, as a fraction of unit.
TOOTH_GAP_FRAC = 0.04


@dataclass(frozen=True)
class GlyphScale:
    """Maps one triple of readings (x, y, z) onto a vector glyph.

    Magnitudes below `lo` draw nothing for that axis; magnitudes at or above
    `hi` are clamped to the largest arrow / heaviest z marker.
    """
    lo: float          # N or N*mm
    hi: float          # N or N*mm
    unit_label: str    # e.g. "Force (N)"
    idx: tuple         # (x, y, z) indices into a 6-axis reading
    colors: tuple      # per-axis hex colors, same order as idx


# Reading order is constants.ALL_AXES = (Fx, Fy, Fz, Mx, My, Mz).
FORCE_GLYPH = GlyphScale(
    lo=0.25, hi=3.0, unit_label="Force (N)",
    idx=(0, 1, 2), colors=FORCE_COLORS,
)

MOMENT_GLYPH = GlyphScale(
    lo=0.05, hi=75.0, unit_label="Moment (N·mm)",
    idx=(3, 4, 5), colors=MOMENT_COLORS,
)


# ---- glyph geometry (multiples of the arch's base `unit`, so glyphs scale
# with the pane; pen widths are in px) ----
ARROW_MIN_LEN = 0.55
ARROW_MAX_LEN = 1.40
ARROW_HEAD    = 0.20
ARROW_MIN_W   = 1.2
ARROW_MAX_W   = 2.8
Z_RING_R      = 0.22
Z_DOT_MIN_R   = 0.04
Z_DOT_MAX_R   = 0.15

# Screen-space directions (+y is down in Qt's coordinate system).
_K = 0.7071067811865476
DIR_X_POS = (-_K,  _K)   # +x -> bottom-left
DIR_X_NEG = ( _K, -_K)   # -x -> top-right
DIR_Y_POS = ( 1.0, 0.0)  # +y -> right
DIR_Y_NEG = (-1.0, 0.0)  # -y -> left

# Fixed pixel sizes for the key column (independent of the arch's `unit`).
KEY_W = 132


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


def _draw_arrow(p, ox, oy, dx, dy, length, head, color, width, inset=0.0):
    """Arrow from (ox, oy) along the unit vector (dx, dy), with a filled head.

    `inset` pushes the tail forward (used to start at the z ring's edge) without
    changing the tip, so length stays a faithful read of magnitude."""
    sx, sy = ox + dx * inset, oy + dy * inset
    shaft = max(length - head * 0.85, inset)
    bx, by = ox + dx * shaft, oy + dy * shaft
    tip = QPointF(ox + dx * length, oy + dy * length)

    pen = QPen(color, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawLine(QPointF(sx, sy), QPointF(bx, by))

    px, py = -dy, dx  # perpendicular to the shaft
    half = head * 0.42
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(color))
    p.drawPolygon(QPolygonF([
        tip,
        QPointF(bx + px * half, by + py * half),
        QPointF(bx - px * half, by - py * half),
    ]))


def _draw_z_marker(p, ox, oy, ring_r, dot_r, width, positive, color):
    """Ring with a dot (+z, out of the tooth) or a cross (-z, into the tooth)."""
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(color, width))
    p.drawEllipse(QPointF(ox, oy), ring_r, ring_r)
    if positive:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(color))
        p.drawEllipse(QPointF(ox, oy), dot_r, dot_r)
    else:
        d = ring_r * 0.66
        p.drawLine(QPointF(ox - d, oy - d), QPointF(ox + d, oy + d))
        p.drawLine(QPointF(ox - d, oy + d), QPointF(ox + d, oy - d))


def _sample_parabola(half_width: float, a: float, n_samples: int = 220):
    """Sample y = -a * x^2 across [-half_width, half_width] and return
    (xs_rel, ys_rel, s_cum) where s_cum[i] is the arc length from index 0 to i.
    Positions are relative to the vertex (add cx / vertex_y to place)."""
    step = 2 * half_width / (n_samples - 1)
    xs = [-half_width + i * step for i in range(n_samples)]
    ys = [-a * x * x for x in xs]
    s = [0.0]
    for i in range(1, n_samples):
        dx = xs[i] - xs[i - 1]
        dy = ys[i] - ys[i - 1]
        s.append(s[-1] + math.sqrt(dx * dx + dy * dy))
    return xs, ys, s


def _pos_at_arc_length(s_target, xs_rel, ys_rel, s_cum, cx, vertex_y, a):
    """Return (x, y, angle_deg) at arc-length s_target along the sampled parabola.
    angle_deg is the tangent angle so a tooth drawn with the local +y axis
    pointing outward will sit flush against the arch curve."""
    if s_target <= 0:
        x_rel, y_rel = xs_rel[0], ys_rel[0]
    elif s_target >= s_cum[-1]:
        x_rel, y_rel = xs_rel[-1], ys_rel[-1]
    else:
        lo, hi = 0, len(s_cum) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if s_cum[mid] < s_target:
                lo = mid + 1
            else:
                hi = mid
        span = s_cum[lo] - s_cum[lo - 1]
        frac = (s_target - s_cum[lo - 1]) / span if span > 0 else 0.0
        x_rel = xs_rel[lo - 1] + frac * (xs_rel[lo] - xs_rel[lo - 1])
        y_rel = ys_rel[lo - 1] + frac * (ys_rel[lo] - ys_rel[lo - 1])
    x = cx + x_rel
    y = vertex_y + y_rel
    dy_dx = -2 * a * x_rel
    angle_deg = math.degrees(math.atan2(dy_dx, 1))
    return x, y, angle_deg


class ArchView(QWidget):
    """One occlusal arch annotating each mapped tooth with a vector glyph.

    Two in-plane arrows carry the x and y components (length = magnitude,
    heading = sign) and a ring marker carries z: a dot for out of the tooth,
    a cross for into it. Directions are screen-fixed, not tooth-relative.
    """

    def __init__(
        self,
        store,
        tooth_to_cell: dict,
        scale: GlyphScale,
        title: str,
        subtitle: str = "Occlusal view · dashed outline = no sensor mapped",
    ):
        super().__init__()
        self.store = store
        self.tooth_to_cell = tooth_to_cell
        self.scale = scale
        self.title = title
        self.subtitle = subtitle
        self.setMinimumSize(340, 420)

    def _reading_for(self, cell_idx: Optional[int]):
        if cell_idx is None or cell_idx >= self.store.n_cells:
            return None
        return self.store.latest(cell_idx)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        p.fillRect(0, 0, w, h, _qcolor(theme.SURFACE))

        arch_area_w = w - KEY_W
        arch_area_h = h

        top_pad = 60
        bottom_pad = 24
        # Wide enough for the terminal molars' buccal labels, which sit a full
        # tooth-height outboard of the arch curve — but proportional, so a
        # narrow pane doesn't spend most of its width on padding.
        side_pad = max(18, min(56, arch_area_w * 0.10))
        # Overall size scale for the arch — shrink to leave breathing room.
        ARCH_SCALE = 0.95
        avail_w = max(80, arch_area_w - 2 * side_pad)
        avail_h = max(80, arch_area_h - top_pad - bottom_pad)

        # Parabola geometry: pick half-width and vertical span so the arch fills
        # the pane at a pleasant occlusal-view aspect ratio.
        arch_aspect = 0.60  # vertical span / (2 * half_width)
        if avail_h < arch_aspect * avail_w:
            parab_h = avail_h
            half_width = parab_h / arch_aspect / 2
            if 2 * half_width > avail_w:
                half_width = avail_w / 2
                parab_h = 2 * half_width * arch_aspect
        else:
            half_width = avail_w / 2
            parab_h = 2 * half_width * arch_aspect

        half_width *= ARCH_SCALE
        parab_h *= ARCH_SCALE

        # Center the arch vertically within the drawable band (below the title).
        cx = side_pad + (avail_w / 2)
        center_y = top_pad + avail_h / 2
        end_y = center_y - parab_h / 2
        vertex_y = center_y + parab_h / 2
        a = (vertex_y - end_y) / (half_width ** 2)

        # Sample the parabola once for arc-length parameterization.
        xs_rel, ys_rel, s_cum = _sample_parabola(half_width, a)
        arc_length = s_cum[-1]

        # Choose the base unit so all teeth touch (with a tiny gap) along the arc.
        total_w_factor = sum(TYPE_SIZE[TOOTH_TYPE[t]][0] for t in LOWER_ARCH_ORDER)
        n_gaps = len(LOWER_ARCH_ORDER) - 1
        total_needed = total_w_factor + TOOTH_GAP_FRAC * n_gaps
        unit = arc_length / total_needed if total_needed > 0 else 20.0
        # Clamp so tall teeth don't spill outside the drawing area.
        max_h_factor = max(v[1] for v in TYPE_SIZE.values())
        unit = min(unit, avail_h / (max_h_factor * 1.6))

        # Place each tooth at its cumulative arc-length center, pulling its
        # reading once per frame.
        placements = []
        cursor = 0.0
        for tooth_num in LOWER_ARCH_ORDER:
            ttype = TOOTH_TYPE[tooth_num]
            w_factor = TYPE_SIZE[ttype][0]
            tw = unit * w_factor
            s_target = cursor + tw / 2
            x, y, angle_deg = _pos_at_arc_length(
                s_target, xs_rel, ys_rel, s_cum, cx, vertex_y, a
            )
            vals = self._reading_for(self.tooth_to_cell.get(tooth_num))
            placements.append((tooth_num, x, y, angle_deg, vals))
            cursor += tw + TOOTH_GAP_FRAC * unit

        # Two passes: every tooth body first, then every glyph, so a later
        # tooth can never paint over an earlier tooth's arrows.
        for tooth_num, x, y, angle_deg, vals in placements:
            self._draw_tooth(p, tooth_num, x, y, angle_deg, unit, vals is not None)
        p.save()
        p.setClipRect(QRectF(0, top_pad - 8, arch_area_w, h - top_pad + 8))
        for tooth_num, x, y, angle_deg, vals in placements:
            if vals is not None:
                self._draw_glyph(p, x, y, unit, vals)
        p.restore()

        self._draw_key(p, arch_area_w, 0, KEY_W, h)

        text_w = max(40, arch_area_w - 30)
        title_font = QFont(); title_font.setPointSize(theme.FONT_SECTION); title_font.setBold(True)
        p.setFont(title_font)
        p.setPen(_qcolor(theme.ON_SURFACE))
        p.drawText(18, 26, p.fontMetrics().elidedText(
            self.title, Qt.TextElideMode.ElideRight, int(text_w)))

        sub_font = QFont(); sub_font.setPointSize(theme.FONT_BODY)
        p.setFont(sub_font)
        p.setPen(_qcolor(theme.ON_SURFACE_MUTED))
        p.drawText(18, 44, p.fontMetrics().elidedText(
            self.subtitle, Qt.TextElideMode.ElideRight, int(text_w)))

        p.end()

    def _draw_tooth(self, p, tooth_num, cx, cy, angle_deg, unit, mapped):
        ttype = TOOTH_TYPE[tooth_num]
        w_factor, h_factor = TYPE_SIZE[ttype]
        tw = unit * w_factor
        th = unit * h_factor

        p.save()
        p.translate(cx, cy)
        p.rotate(angle_deg)

        path = self._tooth_path(ttype, tw, th)
        if mapped:
            p.setBrush(QBrush(_qcolor(theme.TOOTH_FILL)))
            p.setPen(QPen(_qcolor(theme.OUTLINE_STRONG), 1.5))
        else:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(_qcolor(theme.OUTLINE_STRONG), 1.2, Qt.PenStyle.DashLine))
        p.drawPath(path)

        # Label sits buccal (local +y, outside the arch) so it stays clear of the
        # glyph at the tooth center; the anterior teeth converge lingually, so
        # outward labels fan apart instead of piling up. Counter-rotate to keep
        # the text upright.
        p.translate(0, th * 0.75 + unit * 0.36)
        p.rotate(-angle_deg)
        label_font = QFont()
        label_font.setPointSize(max(7, int(unit * 0.28)))
        label_font.setBold(True)
        p.setFont(label_font)
        p.setPen(_qcolor(theme.ON_SURFACE_MUTED))
        text = PALMER_LABEL.get(tooth_num, str(tooth_num))
        fm = p.fontMetrics()
        p.drawText(QPointF(-fm.horizontalAdvance(text) / 2, fm.height() / 3), text)

        p.restore()

    def _draw_glyph(self, p, cx, cy, unit, vals):
        """Draw the x/y arrows and the z marker for one tooth, screen-aligned."""
        scale = self.scale
        vx = vals[scale.idx[0]]
        vy = vals[scale.idx[1]]
        vz = vals[scale.idx[2]]

        head = unit * ARROW_HEAD
        ring_r = unit * Z_RING_R
        for value, pos_dir, neg_dir, color in (
            (vx, DIR_X_POS, DIR_X_NEG, scale.colors[0]),
            (vy, DIR_Y_POS, DIR_Y_NEG, scale.colors[1]),
        ):
            f = _frac(value, scale)
            if f is None:
                continue
            dx, dy = pos_dir if value >= 0 else neg_dir
            _draw_arrow(
                p, cx, cy, dx, dy,
                unit * _lerp(ARROW_MIN_LEN, ARROW_MAX_LEN, f),
                head, _qcolor(color), _lerp(ARROW_MIN_W, ARROW_MAX_W, f),
                inset=ring_r,
            )

        fz = _frac(vz, scale)
        if fz is not None:
            _draw_z_marker(
                p, cx, cy,
                ring_r,
                unit * _lerp(Z_DOT_MIN_R, Z_DOT_MAX_R, fz),
                _lerp(ARROW_MIN_W, ARROW_MAX_W, fz),
                vz >= 0, _qcolor(scale.colors[2]),
            )

    def _tooth_path(self, ttype, w, h):
        """Shape a single tooth in its local frame.

        Local axes: +y = outer (labial/buccal) edge, -y = inner (lingual) edge.
        Molars/premolars are soft squircles; canines and incisors are
        rounded pentagons with a subtle cusp on the outer edge — matches
        the reference chart's simplified occlusal outlines.
        """
        path = QPainterPath()
        hw, hh = w / 2, h / 2
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
            r = min(w, h) * (0.38 if ttype == 'molar' else 0.32)
            path.addRoundedRect(QRectF(-hw, -hh, w, h), r, r)
        return path

    # ---- glyph key ----

    def _draw_key(self, p, x, y, w, h):
        scale = self.scale
        left = x + 12
        cur = y + 26

        heading = QFont(); heading.setPointSize(theme.FONT_BODY); heading.setBold(True)
        caption = QFont(); caption.setPointSize(theme.FONT_CAPTION)
        note = QFont(); note.setPointSize(theme.FONT_CAPTION); note.setItalic(True)
        fg = _qcolor(theme.ON_SURFACE)
        muted = _qcolor(theme.ON_SURFACE_MUTED)

        p.setFont(heading)
        p.setPen(fg)
        p.drawText(int(left), int(cur), scale.unit_label)
        cur += 26

        # Direction compass — the reference sketch, at fixed pixel size.
        p.setFont(caption)
        p.setPen(muted)
        p.drawText(int(left), int(cur), "Direction")
        cur += 18

        ox, oy = left + 52, cur + 18
        _draw_arrow(p, ox, oy, *DIR_X_POS, 30, 8, _qcolor(scale.colors[0]), 1.6, inset=7)
        _draw_arrow(p, ox, oy, *DIR_Y_POS, 34, 8, _qcolor(scale.colors[1]), 1.6, inset=7)
        _draw_z_marker(p, ox, oy, 7, 2.5, 1.6, True, _qcolor(scale.colors[2]))
        p.setPen(fg)
        p.drawText(int(ox - 44), int(oy + 32), "+x")
        p.drawText(int(ox + 38), int(oy + 4), "+y")
        p.drawText(int(ox - 4), int(oy - 12), "z")
        cur = oy + 48

        p.setPen(muted)
        p.drawText(int(left), int(cur), "− = opposite")
        cur += 26

        # Magnitude scale: shortest and longest arrow with their values.
        p.setPen(muted)
        p.drawText(int(left), int(cur), "Length")
        cur += 18
        for value, plen, width in (
            (scale.lo, 24, ARROW_MIN_W),
            (scale.hi, 60, ARROW_MAX_W),
        ):
            _draw_arrow(p, left, cur, *DIR_Y_POS, plen, 8, fg, width)
            p.setPen(fg)
            p.drawText(int(left), int(cur + 15), f"{value:g}")
            cur += 34

        cur += 6
        p.setPen(muted)
        p.drawText(int(left), int(cur), "z marker")
        cur += 18
        for positive, text in ((True, "+z  out"), (False, "−z  in")):
            _draw_z_marker(p, left + 8, cur, 7, 3.0, 1.8,
                           positive, _qcolor(scale.colors[2]))
            p.setPen(fg)
            p.drawText(int(left + 22), int(cur + 4), text)
            cur += 24

        cur += 8
        p.setFont(note)
        p.setPen(muted)
        p.drawText(int(left), int(cur), f"< {scale.lo:g} hidden")


class ArchTab(QWidget):
    """Side-by-side force and moment arches, each tooth carrying a vector glyph."""

    @staticmethod
    def _build_tooth_to_cell(tooth_per_cell):
        mapping = {}
        for cell_idx, tooth in enumerate(tooth_per_cell or []):
            if tooth is not None:
                mapping[int(tooth)] = cell_idx
        return mapping

    def __init__(self, store, tooth_per_cell=None):
        super().__init__()
        self.store = store
        self.tooth_per_cell = tooth_per_cell or []

        tooth_to_cell = self._build_tooth_to_cell(self.tooth_per_cell)

        self.force_view = ArchView(
            store,
            tooth_to_cell,
            scale=FORCE_GLYPH,
            title="Lower Arch — Force Components",
        )
        self.moment_view = ArchView(
            store,
            tooth_to_cell,
            scale=MOMENT_GLYPH,
            title="Lower Arch — Moment Components",
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.force_view, 1)
        layout.addWidget(self.moment_view, 1)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(REFRESH_MS)

    def set_tooth_per_cell(self, tooth_per_cell):
        """Re-map which load cell drives each tooth and push the new mapping
        to both arch views, so a live sensor-config change is reflected in the
        glyphs. The views read force/moment values from the DataStore, which
        already holds post-tare, post-compensation readings."""
        self.tooth_per_cell = list(tooth_per_cell or [])
        mapping = self._build_tooth_to_cell(self.tooth_per_cell)
        self.force_view.tooth_to_cell = mapping
        self.moment_view.tooth_to_cell = mapping

    def _refresh(self):
        if not self.isVisible():
            return
        self.force_view.update()
        self.moment_view.update()
