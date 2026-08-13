import numpy as np

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
)
from PyQt6.QtCore import Qt
import pyqtgraph as pg

from graphDash.constants import (
    FORCE_AXES, FORCE_COLORS, MOMENT_AXES, MOMENT_COLORS,
    DEFAULT_WINDOW_S, N_AXES,
)
from graphDash.ui import theme


def _style_plot(pw, y_label):
    """Apply theme styling to a pyqtgraph PlotWidget."""
    pw.setBackground(theme.PLOT_BG)
    pw.showGrid(x=True, y=True, alpha=theme.PLOT_GRID_ALPHA)
    for axis_name in ("left", "bottom"):
        axis = pw.getAxis(axis_name)
        axis.setPen(pg.mkPen(color=theme.PLOT_AXIS_COLOR, width=1))
        axis.setTextPen(pg.mkPen(color=theme.ON_SURFACE_MUTED))
    pw.setLabel("left", y_label, color=theme.ON_SURFACE, size=f"{theme.FONT_BODY}pt")
    pw.setLabel("bottom", "Time (s)", color=theme.ON_SURFACE, size=f"{theme.FONT_BODY}pt")
    legend = pw.addLegend(offset=(-10, 10), labelTextColor=theme.ON_SURFACE)
    legend.setBrush(pg.mkBrush(255, 255, 255, 220))
    legend.setPen(pg.mkPen(theme.OUTLINE))
    return legend


def _readout_card(axis_name, color, unit):
    """A rounded pill showing 'Axis: value unit', color-coded by axis."""
    lbl = QLabel(f"{axis_name}: +0.000 {unit}")
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setStyleSheet(
        f"QLabel {{"
        f"  background-color: {theme.SURFACE_ALT};"
        f"  color: {color};"
        f"  border: 1px solid {theme.OUTLINE};"
        f"  border-left: 3px solid {color};"
        f"  border-radius: 6px;"
        f"  padding: 6px 12px;"
        f"  font-size: {theme.FONT_READOUT}pt;"
        f"  font-weight: 600;"
        f"  font-family: 'SF Mono', 'Menlo', 'Consolas', monospace;"
        f"}}"
    )
    lbl.setMinimumWidth(150)
    return lbl


class CellTab(QWidget):
    def __init__(self, cell_idx, store, sampler=None, tare_offsets=None):
        super().__init__()
        self.cell_idx = cell_idx
        self.store = store
        self.sampler = sampler
        self.tare_offsets = tare_offsets
        self.window_s = DEFAULT_WINDOW_S
        self.ma_n = 1

        layout = QVBoxLayout()
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        # --- Force plot ---
        self.force_plot = pg.PlotWidget()
        _style_plot(self.force_plot, "Force (N)")
        self.force_curves = []
        for i, axis in enumerate(FORCE_AXES):
            pen = pg.mkPen(color=FORCE_COLORS[i], width=theme.PLOT_LINE_WIDTH)
            curve = self.force_plot.plot([], [], pen=pen, name=axis)
            self.force_curves.append(curve)
        layout.addWidget(self._plot_frame(self.force_plot), 1)

        # Force readouts
        force_val_row = QHBoxLayout()
        force_val_row.setSpacing(8)
        self.force_labels = []
        for i, axis in enumerate(FORCE_AXES):
            lbl = _readout_card(axis, FORCE_COLORS[i], "N")
            force_val_row.addWidget(lbl)
            self.force_labels.append(lbl)
        force_val_row.addStretch()
        layout.addLayout(force_val_row)

        # --- Moment plot ---
        self.moment_plot = pg.PlotWidget()
        _style_plot(self.moment_plot, "Moment (N·mm)")
        self.moment_curves = []
        for i, axis in enumerate(MOMENT_AXES):
            pen = pg.mkPen(color=MOMENT_COLORS[i], width=theme.PLOT_LINE_WIDTH)
            curve = self.moment_plot.plot([], [], pen=pen, name=axis)
            self.moment_curves.append(curve)
        layout.addWidget(self._plot_frame(self.moment_plot), 1)

        # Moment readouts
        moment_val_row = QHBoxLayout()
        moment_val_row.setSpacing(8)
        self.moment_labels = []
        for i, axis in enumerate(MOMENT_AXES):
            lbl = _readout_card(axis, MOMENT_COLORS[i], "N·mm")
            moment_val_row.addWidget(lbl)
            self.moment_labels.append(lbl)
        moment_val_row.addStretch()
        layout.addLayout(moment_val_row)

        # --- Tare controls ---
        tare_row = QHBoxLayout()
        tare_row.setSpacing(10)

        tare_btn = QPushButton("Tare")
        tare_btn.setMinimumHeight(36)
        tare_btn.setMinimumWidth(100)
        tare_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        tare_btn.clicked.connect(self.tare)

        clear_btn = QPushButton("Clear Tare")
        clear_btn.setMinimumHeight(36)
        clear_btn.setMinimumWidth(120)
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.clicked.connect(self.clear_tare)

        self.offset_label = QLabel("offset: F[0.000, 0.000, 0.000]  M[0.000, 0.000, 0.000]")
        self.offset_label.setStyleSheet(
            f"color: {theme.ON_SURFACE_MUTED}; "
            f"font-family: 'SF Mono', 'Menlo', 'Consolas', monospace; "
            f"font-size: {theme.FONT_BODY}pt;"
        )

        tare_row.addWidget(tare_btn)
        tare_row.addWidget(clear_btn)
        tare_row.addWidget(self.offset_label, 1)
        layout.addLayout(tare_row)

        self.setLayout(layout)

    def _plot_frame(self, plot_widget):
        """Wrap a plot in a rounded card border matching theme."""
        frame = QFrame()
        frame.setStyleSheet(
            f"QFrame {{"
            f"  background-color: {theme.SURFACE};"
            f"  border: 1px solid {theme.OUTLINE};"
            f"  border-radius: 8px;"
            f"}}"
        )
        inner = QVBoxLayout()
        inner.setContentsMargins(6, 6, 6, 6)
        inner.addWidget(plot_widget)
        frame.setLayout(inner)
        return frame

    def tare(self):
        if self.sampler is None or self.tare_offsets is None:
            return
        raw = self.sampler.get_last_raw(self.cell_idx)
        self.tare_offsets.set(self.cell_idx, raw)
        self._update_offset_label()

    def clear_tare(self):
        if self.tare_offsets is None:
            return
        self.tare_offsets.clear(self.cell_idx)
        self._update_offset_label()

    def _current_offset(self):
        if self.tare_offsets is None:
            return [0.0] * N_AXES
        return self.tare_offsets.get(self.cell_idx)

    def _update_offset_label(self):
        o = self._current_offset()
        self.offset_label.setText(
            f"offset: F[{o[0]:+.3f}, {o[1]:+.3f}, {o[2]:+.3f}]  "
            f"M[{o[3]:+.3f}, {o[4]:+.3f}, {o[5]:+.3f}]")

    @staticmethod
    def _moving_avg(arr, n):
        """Causal moving average — output same length as input, no lookahead."""
        if n <= 1 or len(arr) == 0:
            return arr
        kernel = np.ones(n) / n
        smoothed = np.convolve(arr, kernel, mode="full")[:len(arr)]
        cs = np.cumsum(arr)
        for j in range(min(n - 1, len(arr))):
            smoothed[j] = cs[j] / (j + 1)
        return smoothed

    def refresh(self):
        t, arrs = self.store.get_cell(self.cell_idx, self.window_s)
        for i in range(3):
            smoothed = self._moving_avg(arrs[i], self.ma_n)
            self.force_curves[i].setData(t, smoothed)
            if len(smoothed) > 0:
                self.force_labels[i].setText(
                    f"{FORCE_AXES[i]}: {smoothed[-1]:+7.3f} N")
        for i in range(3):
            smoothed = self._moving_avg(arrs[i+3], self.ma_n)
            self.moment_curves[i].setData(t, smoothed)
            if len(smoothed) > 0:
                self.moment_labels[i].setText(
                    f"{MOMENT_AXES[i]}: {smoothed[-1]:+7.3f} N·mm")
