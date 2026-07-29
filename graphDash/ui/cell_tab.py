import numpy as np

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
from PyQt6.QtGui import QFont
import pyqtgraph as pg

from graphDash.constants import (
    FORCE_AXES, FORCE_COLORS, MOMENT_AXES, MOMENT_COLORS,
    DEFAULT_WINDOW_S, N_AXES,
)


class CellTab(QWidget):
    def __init__(self, cell_idx, store):
        super().__init__()
        self.cell_idx = cell_idx
        self.store = store
        self.offset = [0.0] * N_AXES
        self.window_s = DEFAULT_WINDOW_S
        self.ma_n = 1  # 1 = no smoothing

        layout = QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)

        # --- Force plot ---
        self.force_plot = pg.PlotWidget()
        self.force_plot.setBackground("w")
        self.force_plot.setLabel("left", "Force (N)")
        self.force_plot.setLabel("bottom", "Time (s)")
        self.force_plot.addLegend()
        self.force_plot.showGrid(x=True, y=True, alpha=0.3)

        self.force_curves = []
        for i, axis in enumerate(FORCE_AXES):
            pen = pg.mkPen(color=FORCE_COLORS[i], width=2)
            curve = self.force_plot.plot([], [], pen=pen, name=axis)
            self.force_curves.append(curve)
        layout.addWidget(self.force_plot, 1)

        # Force readouts
        val_font = QFont(); val_font.setPointSize(12); val_font.setBold(True)
        force_val_row = QHBoxLayout()
        self.force_labels = []
        for i, axis in enumerate(FORCE_AXES):
            lbl = QLabel(f"{axis}: +0.000 N")
            lbl.setFont(val_font)
            lbl.setStyleSheet(f"color: {FORCE_COLORS[i]};")
            force_val_row.addWidget(lbl)
            self.force_labels.append(lbl)
        layout.addLayout(force_val_row)

        # --- Moment plot ---
        self.moment_plot = pg.PlotWidget()
        self.moment_plot.setBackground("w")
        self.moment_plot.setLabel("left", "Moment (N·mm)")
        self.moment_plot.setLabel("bottom", "Time (s)")
        self.moment_plot.addLegend()
        self.moment_plot.showGrid(x=True, y=True, alpha=0.3)

        self.moment_curves = []
        for i, axis in enumerate(MOMENT_AXES):
            pen = pg.mkPen(color=MOMENT_COLORS[i], width=2)
            curve = self.moment_plot.plot([], [], pen=pen, name=axis)
            self.moment_curves.append(curve)
        layout.addWidget(self.moment_plot, 1)

        # Moment readouts
        moment_val_row = QHBoxLayout()
        self.moment_labels = []
        for i, axis in enumerate(MOMENT_AXES):
            lbl = QLabel(f"{axis}: +0.000 N·mm")
            lbl.setFont(val_font)
            lbl.setStyleSheet(f"color: {MOMENT_COLORS[i]};")
            moment_val_row.addWidget(lbl)
            self.moment_labels.append(lbl)
        layout.addLayout(moment_val_row)

        # --- Tare controls ---
        btnf = QFont(); btnf.setPointSize(11)
        tare_row = QHBoxLayout()

        tare_btn = QPushButton("Tare")
        tare_btn.setFont(btnf)
        tare_btn.setMinimumHeight(36)
        tare_btn.clicked.connect(self.tare)

        clear_btn = QPushButton("Clear Tare")
        clear_btn.setFont(btnf)
        clear_btn.setMinimumHeight(36)
        clear_btn.clicked.connect(self.clear_tare)

        self.offset_label = QLabel("offset: F[0.000, 0.000, 0.000]  M[0.000, 0.000, 0.000]")
        self.offset_label.setStyleSheet("color: gray;")

        tare_row.addWidget(tare_btn)
        tare_row.addWidget(clear_btn)
        tare_row.addWidget(self.offset_label, 1)
        layout.addLayout(tare_row)

        self.setLayout(layout)

    def tare(self):
        t, arrs = self.store.get_cell(self.cell_idx)
        if len(t) > 0:
            self.offset = [float(a[-1]) for a in arrs]
            self._update_offset_label()

    def clear_tare(self):
        self.offset = [0.0] * N_AXES
        self._update_offset_label()

    def _update_offset_label(self):
        o = self.offset
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
        # Force axes (0, 1, 2)
        for i in range(3):
            adjusted = arrs[i] - self.offset[i] if len(arrs[i]) > 0 else arrs[i]
            smoothed = self._moving_avg(adjusted, self.ma_n)
            self.force_curves[i].setData(t, smoothed)
            if len(smoothed) > 0:
                self.force_labels[i].setText(
                    f"{FORCE_AXES[i]}: {smoothed[-1]:+7.3f} N")
        # Moment axes (3, 4, 5)
        for i in range(3):
            adjusted = arrs[i+3] - self.offset[i+3] if len(arrs[i+3]) > 0 else arrs[i+3]
            smoothed = self._moving_avg(adjusted, self.ma_n)
            self.moment_curves[i].setData(t, smoothed)
            if len(smoothed) > 0:
                self.moment_labels[i].setText(
                    f"{MOMENT_AXES[i]}: {smoothed[-1]:+7.3f} N·mm")
