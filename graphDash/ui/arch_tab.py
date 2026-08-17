import math
from dataclasses import dataclass
from typing import Callable, Optional

from PyQt6.QtWidgets import QWidget, QHBoxLayout
from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF
from PyQt6.QtGui import (
    QPainter, QPen, QBrush, QColor, QPainterPath, QFont, QLinearGradient,
)

from graphDash.constants import REFRESH_MS
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

COLOR_GRAY  = QColor(180, 180, 180)
COLOR_GREEN = QColor(30, 200, 30)
COLOR_YELLOW = QColor(255, 255, 30)
COLOR_RED   = QColor(220, 30, 30)


@dataclass(frozen=True)
class HeatmapScale:
    """Configures the gray→green→yellow→red heatmap for one arch view."""
    gray_threshold: float
    red_cap: float
    unit_label: str            # e.g. "Fz (N)" or "|M| (N·mm)"
    tick_values: tuple         # values to label on the color bar

    @property
    def mid(self):
        return (self.gray_threshold + self.red_cap) / 2


FORCE_SCALE = HeatmapScale(
    gray_threshold=0.5,
    red_cap=2.0,
    unit_label="Fz (N)",
    tick_values=(0.0, 0.5, 1.25, 2.0),
)

MOMENT_SCALE = HeatmapScale(
    gray_threshold=5.0,
    red_cap=30.0,
    unit_label="|M| (N·mm)",
    tick_values=(0.0, 5.0, 17.5, 30.0),
)


def heatmap_color(value: float, scale: HeatmapScale) -> QColor:
    if value <= scale.gray_threshold:
        return COLOR_GRAY
    if value >= scale.red_cap:
        return COLOR_RED
    mid = scale.mid
    if value <= mid:
        t = (value - scale.gray_threshold) / (mid - scale.gray_threshold)
        a, b = COLOR_GREEN, COLOR_YELLOW
    else:
        t = (value - mid) / (scale.red_cap - mid)
        a, b = COLOR_YELLOW, COLOR_RED
    return QColor(
        int(a.red()   + t * (b.red()   - a.red())),
        int(a.green() + t * (b.green() - a.green())),
        int(a.blue()  + t * (b.blue()  - a.blue())),
    )


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


def fz_metric(store, cell_idx):
    t, arrs = store.get_cell(cell_idx)
    if len(t) == 0:
        return 0.0
    return float(arrs[2][-1])


def moment_magnitude_metric(store, cell_idx):
    t, arrs = store.get_cell(cell_idx)
    if len(t) == 0:
        return 0.0
    mx = float(arrs[3][-1])
    my = float(arrs[4][-1])
    mz = float(arrs[5][-1])
    return math.sqrt(mx * mx + my * my + mz * mz)


class ArchView(QWidget):
    """One occlusal arch rendered as a live heatmap of a per-cell metric."""

    def __init__(
        self,
        store,
        tooth_to_cell: dict,
        metric_fn: Callable[[object, int], float],
        scale: HeatmapScale,
        title: str,
        subtitle: str = "Occlusal view · outline only = no sensor mapped",
    ):
        super().__init__()
        self.store = store
        self.tooth_to_cell = tooth_to_cell
        self.metric_fn = metric_fn
        self.scale = scale
        self.title = title
        self.subtitle = subtitle
        self.setMinimumSize(340, 420)

    def _value_for(self, cell_idx: Optional[int]) -> Optional[float]:
        if cell_idx is None or cell_idx >= self.store.n_cells:
            return None
        return self.metric_fn(self.store, cell_idx)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        p.fillRect(0, 0, w, h, _qcolor(theme.SURFACE))

        bar_area_w = 90
        arch_area_w = w - bar_area_w
        arch_area_h = h

        top_pad = 60
        bottom_pad = 24
        side_pad = 24
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

        # Place each tooth at its cumulative arc-length center.
        cursor = 0.0
        for tooth_num in LOWER_ARCH_ORDER:
            ttype = TOOTH_TYPE[tooth_num]
            w_factor = TYPE_SIZE[ttype][0]
            tw = unit * w_factor
            s_target = cursor + tw / 2
            x, y, angle_deg = _pos_at_arc_length(
                s_target, xs_rel, ys_rel, s_cum, cx, vertex_y, a
            )

            cell_idx = self.tooth_to_cell.get(tooth_num)
            value = self._value_for(cell_idx)
            fill = heatmap_color(value, self.scale) if value is not None else None
            self._draw_tooth(p, tooth_num, x, y, angle_deg, unit, fill)

            cursor += tw + TOOTH_GAP_FRAC * unit

        self._draw_legend(p, arch_area_w, 0, bar_area_w, h)

        title_font = QFont(); title_font.setPointSize(theme.FONT_SECTION); title_font.setBold(True)
        p.setFont(title_font)
        p.setPen(_qcolor(theme.ON_SURFACE))
        p.drawText(18, 26, self.title)

        sub_font = QFont(); sub_font.setPointSize(theme.FONT_BODY)
        p.setFont(sub_font)
        p.setPen(_qcolor(theme.ON_SURFACE_MUTED))
        p.drawText(18, 44, self.subtitle)

        p.end()

    def _draw_tooth(self, p, tooth_num, cx, cy, angle_deg, unit, fill):
        ttype = TOOTH_TYPE[tooth_num]
        w_factor, h_factor = TYPE_SIZE[ttype]
        tw = unit * w_factor
        th = unit * h_factor

        p.save()
        p.translate(cx, cy)
        p.rotate(angle_deg)

        path = self._tooth_path(ttype, tw, th)
        if fill is not None:
            p.setBrush(QBrush(fill))
            p.setPen(QPen(_qcolor(theme.ON_SURFACE), 1.5))
        else:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(_qcolor(theme.OUTLINE_STRONG), 1.2, Qt.PenStyle.DashLine))
        p.drawPath(path)

        p.restore()

        label_font = QFont()
        label_font.setPointSize(max(7, int(unit * 0.28)))
        label_font.setBold(True)
        p.setFont(label_font)
        p.setPen(_qcolor(theme.ON_SURFACE))
        text = PALMER_LABEL.get(tooth_num, str(tooth_num))
        fm = p.fontMetrics()
        p.drawText(
            QPointF(cx - fm.horizontalAdvance(text) / 2, cy + fm.height() / 3),
            text,
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

    # ---- color bar legend ----

    def _draw_legend(self, p, x, y, w, h):
        margin = 20
        bar_x = x + 12
        bar_w = 20
        bar_y = y + 40
        bar_h = h - 2 * margin - 20

        grad = QLinearGradient(0, bar_y, 0, bar_y + bar_h)
        # 75% top region = green→yellow→red (gray_threshold..red_cap)
        # 25% bottom band = gray (0..gray_threshold)
        grad.setColorAt(0.0,    COLOR_RED)
        grad.setColorAt(0.375,  COLOR_YELLOW)
        grad.setColorAt(0.75,   COLOR_GREEN)
        grad.setColorAt(0.7501, COLOR_GRAY)
        grad.setColorAt(1.0,    COLOR_GRAY)

        p.setPen(QPen(_qcolor(theme.OUTLINE_STRONG), 1))
        p.setBrush(QBrush(grad))
        p.drawRect(int(bar_x), int(bar_y), bar_w, int(bar_h))

        font = QFont(); font.setPointSize(theme.FONT_CAPTION)
        title_font = QFont(); title_font.setPointSize(theme.FONT_BODY); title_font.setBold(True)
        p.setFont(title_font)
        p.setPen(_qcolor(theme.ON_SURFACE))
        p.drawText(int(x + 8), int(y + 26), self.scale.unit_label)
        p.setFont(font)

        for value in self.scale.tick_values:
            ry = self._bar_y_for_value(value, bar_y, bar_h)
            p.setPen(_qcolor(theme.OUTLINE_STRONG))
            p.drawLine(int(bar_x + bar_w), int(ry), int(bar_x + bar_w + 6), int(ry))
            p.setPen(_qcolor(theme.ON_SURFACE))
            p.drawText(int(bar_x + bar_w + 10), int(ry + 4), f"{value:g}")

        thresh_y = self._bar_y_for_value(self.scale.gray_threshold, bar_y, bar_h)
        note_font = QFont(); note_font.setPointSize(theme.FONT_CAPTION); note_font.setItalic(True)
        p.setFont(note_font)
        p.setPen(_qcolor(theme.ON_SURFACE_MUTED))
        p.drawText(int(bar_x - 2), int(thresh_y + bar_h * 0.15),
                   f"≤ {self.scale.gray_threshold:g}: gray")

    def _bar_y_for_value(self, value, bar_y, bar_h):
        gt = self.scale.gray_threshold
        cap = self.scale.red_cap
        if value <= 0:
            frac = 0
        elif value >= cap:
            frac = 1
        elif value <= gt:
            frac = value / gt * 0.25
        else:
            frac = 0.25 + (value - gt) / (cap - gt) * 0.75
        return bar_y + bar_h - frac * bar_h


class ArchTab(QWidget):
    """Side-by-side force (Fz) and moment (|M|) heatmap arches."""

    def __init__(self, store, tooth_per_cell=None):
        super().__init__()
        self.store = store
        self.tooth_per_cell = tooth_per_cell or []

        tooth_to_cell = {}
        for cell_idx, tooth in enumerate(self.tooth_per_cell):
            if tooth is not None:
                tooth_to_cell[int(tooth)] = cell_idx

        self.force_view = ArchView(
            store,
            tooth_to_cell,
            metric_fn=fz_metric,
            scale=FORCE_SCALE,
            title="Lower Arch — Fz Heatmap",
        )
        self.moment_view = ArchView(
            store,
            tooth_to_cell,
            metric_fn=moment_magnitude_metric,
            scale=MOMENT_SCALE,
            title="Lower Arch — |M| Heatmap",
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.force_view, 1)
        layout.addWidget(self.moment_view, 1)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(REFRESH_MS)

    def _refresh(self):
        self.force_view.update()
        self.moment_view.update()
