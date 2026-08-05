import math

from PyQt6.QtWidgets import QWidget
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

# (width_factor, height_factor) relative to the arch's base unit
TYPE_SIZE = {
    'molar':    (1.15, 1.10),
    'premolar': (0.75, 0.90),
    'canine':   (0.65, 1.05),
    'incisor':  (0.55, 0.90),
}

GRAY_THRESHOLD_N = 0.5
RED_CAP_N = 2.0
MID_N = (GRAY_THRESHOLD_N + RED_CAP_N) / 2  # 1.25

COLOR_GRAY  = QColor(180, 180, 180)
COLOR_GREEN = QColor(30, 200, 30)
COLOR_YELLOW = QColor(255, 255, 30)
COLOR_RED   = QColor(220, 30, 30)


def force_color(fz: float) -> QColor:
    """Fz (N) → heatmap color per the spec."""
    if fz <= GRAY_THRESHOLD_N:
        return COLOR_GRAY
    if fz >= RED_CAP_N:
        return COLOR_RED
    if fz <= MID_N:
        t = (fz - GRAY_THRESHOLD_N) / (MID_N - GRAY_THRESHOLD_N)
        return QColor(
            int(COLOR_GREEN.red()   + t * (COLOR_YELLOW.red()   - COLOR_GREEN.red())),
            int(COLOR_GREEN.green() + t * (COLOR_YELLOW.green() - COLOR_GREEN.green())),
            int(COLOR_GREEN.blue()  + t * (COLOR_YELLOW.blue()  - COLOR_GREEN.blue())),
        )
    t = (fz - MID_N) / (RED_CAP_N - MID_N)
    return QColor(
        int(COLOR_YELLOW.red()   + t * (COLOR_RED.red()   - COLOR_YELLOW.red())),
        int(COLOR_YELLOW.green() + t * (COLOR_RED.green() - COLOR_YELLOW.green())),
        int(COLOR_YELLOW.blue()  + t * (COLOR_RED.blue()  - COLOR_YELLOW.blue())),
    )


class ArchTab(QWidget):
    """Occlusal view of the lower dental arch as a live Fz heatmap."""

    def __init__(self, store, tooth_per_cell=None):
        super().__init__()
        self.store = store
        self.tooth_per_cell = tooth_per_cell or []

        # tooth_number -> cell_index
        self.tooth_to_cell = {}
        for cell_idx, tooth in enumerate(self.tooth_per_cell):
            if tooth is not None:
                self.tooth_to_cell[int(tooth)] = cell_idx

        self.setMinimumSize(600, 420)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update)
        self.timer.start(REFRESH_MS)

    # ---- data ----

    def _current_fz(self, cell_idx):
        t, arrs = self.store.get_cell(cell_idx)
        if len(t) == 0:
            return 0.0
        return float(arrs[2][-1])  # Fz is axis index 2

    # ---- painting ----

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # Themed background
        p.fillRect(0, 0, w, h, _qcolor(theme.SURFACE))

        # Reserve right side for the color bar legend
        bar_area_w = 100
        arch_w = w - bar_area_w
        arch_h = h

        # Parabola: vertex at anterior (bottom-center), opens upward toward posterior
        cx = arch_w / 2
        vertex_y = arch_h * 0.82
        end_y = arch_h * 0.18
        half_width = arch_w * 0.42
        a = (vertex_y - end_y) / (half_width ** 2)

        unit = min(arch_w / 20.0, arch_h / 12.0)

        n = len(LOWER_ARCH_ORDER)
        for i, tooth_num in enumerate(LOWER_ARCH_ORDER):
            t = -1 + 2 * i / (n - 1)
            x_off = t * half_width
            x = cx + x_off
            y = vertex_y - a * (x_off ** 2)
            dy_dx = -2 * a * x_off
            angle_deg = math.degrees(math.atan2(dy_dx, 1))

            cell_idx = self.tooth_to_cell.get(tooth_num)
            has_sensor = cell_idx is not None and cell_idx < self.store.n_cells
            fill = force_color(self._current_fz(cell_idx)) if has_sensor else None

            self._draw_tooth(p, tooth_num, x, y, angle_deg, unit, fill, has_sensor)

        self._draw_legend(p, arch_w, 0, bar_area_w, h)

        # Title
        title_font = QFont(); title_font.setPointSize(theme.FONT_SECTION); title_font.setBold(True)
        p.setFont(title_font)
        p.setPen(_qcolor(theme.ON_SURFACE))
        p.drawText(18, 26, "Lower Arch — Fz Heatmap")

        # Subtitle
        sub_font = QFont(); sub_font.setPointSize(theme.FONT_BODY)
        p.setFont(sub_font)
        p.setPen(_qcolor(theme.ON_SURFACE_MUTED))
        p.drawText(18, 44, "Occlusal view · outline only = no sensor mapped")

        p.end()

    def _draw_tooth(self, p, tooth_num, cx, cy, angle_deg, unit, fill, has_sensor):
        ttype = TOOTH_TYPE[tooth_num]
        w_factor, h_factor = TYPE_SIZE[ttype]
        tw = unit * w_factor
        th = unit * h_factor

        p.save()
        p.translate(cx, cy)
        p.rotate(angle_deg)

        path = self._tooth_path(ttype, tw, th)
        if has_sensor:
            p.setBrush(QBrush(fill))
            p.setPen(QPen(_qcolor(theme.ON_SURFACE), 1.5))
        else:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(_qcolor(theme.OUTLINE_STRONG), 1.2, Qt.PenStyle.DashLine))
        p.drawPath(path)

        # Simple cusp hints for molars / premolars
        cusp_pen = QPen(_qcolor(theme.ON_SURFACE_MUTED), 1.0)
        p.setPen(cusp_pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        if ttype == 'molar':
            r = th * 0.09
            for fx, fy in [(-0.22, -0.22), (0.22, -0.22), (-0.22, 0.22), (0.22, 0.22)]:
                p.drawEllipse(QPointF(fx * tw, fy * th), r, r)
        elif ttype == 'premolar':
            r = th * 0.10
            for fx, fy in [(-0.18, 0.0), (0.18, 0.0)]:
                p.drawEllipse(QPointF(fx * tw, fy * th), r, r)

        p.restore()

        # Label upright, centered on the tooth
        label_font = QFont()
        label_font.setPointSize(max(7, int(unit * 0.32)))
        label_font.setBold(True)
        p.setFont(label_font)
        p.setPen(_qcolor(theme.ON_SURFACE))
        text = str(tooth_num)
        fm = p.fontMetrics()
        p.drawText(
            QPointF(cx - fm.horizontalAdvance(text) / 2, cy + fm.height() / 3),
            text,
        )

    def _tooth_path(self, ttype, w, h):
        path = QPainterPath()
        if ttype == 'canine':
            # Rounded pentagon with a subtle point on the outer edge (+y in local frame)
            path.moveTo(0, -h / 2)
            path.lineTo(w / 2, -h / 4)
            path.lineTo(w / 2 * 0.85, h / 2)
            path.lineTo(-w / 2 * 0.85, h / 2)
            path.lineTo(-w / 2, -h / 4)
            path.closeSubpath()
        else:
            r = min(w, h) * {'molar': 0.30, 'premolar': 0.25, 'incisor': 0.20}[ttype]
            path.addRoundedRect(QRectF(-w / 2, -h / 2, w, h), r, r)
        return path

    # ---- color bar legend ----

    def _draw_legend(self, p, x, y, w, h):
        margin = 20
        bar_x = x + 15
        bar_w = 22
        bar_y = y + 40
        bar_h = h - 2 * margin - 20

        grad = QLinearGradient(0, bar_y, 0, bar_y + bar_h)
        grad.setColorAt(0.0,    COLOR_RED)     # top   = 2.0 N
        grad.setColorAt(0.375,  COLOR_YELLOW)  #        1.25 N
        grad.setColorAt(0.75,   COLOR_GREEN)   #        0.5 N
        grad.setColorAt(0.7501, COLOR_GRAY)    # ≤ 0.5 N gray band
        grad.setColorAt(1.0,    COLOR_GRAY)    # bottom = 0.0 N

        p.setPen(QPen(_qcolor(theme.OUTLINE_STRONG), 1))
        p.setBrush(QBrush(grad))
        p.drawRect(int(bar_x), int(bar_y), bar_w, int(bar_h))

        font = QFont(); font.setPointSize(theme.FONT_CAPTION)
        p.setFont(font)
        p.setPen(_qcolor(theme.ON_SURFACE))

        title_font = QFont(); title_font.setPointSize(theme.FONT_BODY); title_font.setBold(True)
        p.setFont(title_font)
        p.drawText(int(x + 10), int(y + 26), "Fz (N)")
        p.setFont(font)

        for value in [0.0, 0.5, 1.25, 2.0]:
            ry = self._bar_y_for_value(value, bar_y, bar_h)
            p.setPen(_qcolor(theme.OUTLINE_STRONG))
            p.drawLine(int(bar_x + bar_w), int(ry), int(bar_x + bar_w + 6), int(ry))
            p.setPen(_qcolor(theme.ON_SURFACE))
            p.drawText(int(bar_x + bar_w + 10), int(ry + 4), f"{value:.2f}")

        thresh_y = self._bar_y_for_value(0.5, bar_y, bar_h)
        note_font = QFont(); note_font.setPointSize(theme.FONT_CAPTION); note_font.setItalic(True)
        p.setFont(note_font)
        p.setPen(_qcolor(theme.ON_SURFACE_MUTED))
        p.drawText(int(bar_x - 2), int(thresh_y + bar_h * 0.15), "≤ 0.5N: gray")

    @staticmethod
    def _bar_y_for_value(value, bar_y, bar_h):
        # Bottom 25% of the bar is the gray band (0..0.5 N);
        # top 75% is the green→yellow→red gradient (0.5..2.0 N).
        if value <= 0:
            frac = 0
        elif value >= RED_CAP_N:
            frac = 1
        elif value <= GRAY_THRESHOLD_N:
            frac = value / GRAY_THRESHOLD_N * 0.25
        else:
            frac = 0.25 + (value - GRAY_THRESHOLD_N) / (RED_CAP_N - GRAY_THRESHOLD_N) * 0.75
        return bar_y + bar_h - frac * bar_h
