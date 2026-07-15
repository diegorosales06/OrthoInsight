#!/usr/bin/env python3
"""
Load-cell dashboard with real-time plots — runs entirely on the Pi.

Each tab shows one cell with Fx/Fy/Fz plotted vs time (rolling window).
Controls: Start/Stop, sampling rate, rolling window, tare, clear data.

Install once on the Pi:
    sudo apt install python3-pyqt6 python3-pyqtgraph python3-numpy

Run (from laptop with X11 forwarding):
    ssh -X pi@<PI_IP>
    python3 cell_dashboard.py

Or directly on the Pi with HDMI:
    python3 cell_dashboard.py
"""

import sys
import time
import threading
import collections
import spidev
from gpiozero import DigitalOutputDevice
import numpy as np

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QWidget, QVBoxLayout,
    QHBoxLayout, QLabel, QPushButton, QSpinBox, QComboBox,
)
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QFont
import pyqtgraph as pg

# ---------- MMS101 constants ----------
CMD_START, CMD_DATA2, CMD_BOOT   = 0xF0, 0xE2, 0xB0
CMD_STOP,  CMD_RESET, CMD_STATUS = 0xB2, 0xB4, 0x80
CMD_INTERVAL                     = 0x44
CMD_COEFF = (0x30, 0x32, 0x34, 0x36, 0x38, 0x3A)
STT_STANDBY, STT_READY = 1, 3

AXES   = ("Fx", "Fy", "Fz")
COLORS = ("#e74c3c", "#2ecc71", "#3498db")  # red, green, blue

DEFAULT_RATE_HZ    = 20
DEFAULT_WINDOW_S   = 10
REFRESH_MS         = 50
MAX_BUFFER_SAMPLES = 5000


def s24(b):
    v = (b[0] << 16) | (b[1] << 8) | b[2]
    return v - 0x1000000 if v & 0x800000 else v


class Sensor:
    def __init__(self, name, bus, dev, csb_gpio):
        self.name = name
        self.spi = spidev.SpiDev()
        self.spi.open(bus, dev)
        self.spi.mode = 0b11
        self.spi.max_speed_hz = 2_000_000
        self.csb = DigitalOutputDevice(csb_gpio, active_high=False,
                                       initial_value=False)
        self.coeff = [[0]*6 for _ in range(6)]
        self.spi.xfer2([0x00])

    def _xfer(self, tx, rx_len):
        self.csb.on()
        try:
            if tx:
                self.spi.xfer2(list(tx))
            return bytes(self.spi.xfer2([0]*rx_len)) if rx_len else b""
        finally:
            self.csb.off()

    def _cmd(self, tx, rx_len):
        r = self._xfer(tx, rx_len)
        if r[0] != 0x00:
            raise RuntimeError(f"{self.name}: cmd 0x{tx[0]:02X} -> 0x{r[0]:02X}")
        return r

    def _wait(self, state):
        for _ in range(10):
            time.sleep(0.02)
            if self._cmd([CMD_STATUS], 4)[3] == state:
                return
        raise RuntimeError(f"{self.name}: timeout waiting for state {state}")

    def init(self):
        self._cmd([CMD_RESET], 1);  self._wait(STT_STANDBY)
        self._cmd([CMD_BOOT],  1);  self._wait(STT_READY)
        for axis in range(6):
            r = self._cmd([CMD_COEFF[axis]], 19)
            for k in range(6):
                self.coeff[axis][k] = s24(r[1+k*3 : 4+k*3])
        self._cmd([CMD_INTERVAL, 0, 0, 0], 1)
        self._cmd([CMD_START], 1)
        time.sleep(0.01)

    def read_forces(self):
        r = self._cmd([CMD_DATA2], 21)
        adc = [s24(r[3+k*3 : 6+k*3]) for k in range(6)]
        out = []
        for axis in range(3):
            acc = sum(c * a for c, a in zip(self.coeff[axis], adc))
            out.append(int(acc / 2048) / 1000.0)
        return out

    def stop(self):
        try: self._cmd([CMD_STOP], 1)
        except Exception: pass
        self.spi.close()
        self.csb.close()


# ---------- Shared data store ----------
class DataStore:
    """Thread-safe ring buffer for timestamped force data."""

    def __init__(self, n_cells):
        self.n_cells = n_cells
        self.lock = threading.Lock()
        self.clear()

    def clear(self):
        with self.lock:
            self.t = collections.deque(maxlen=MAX_BUFFER_SAMPLES)
            self.data = [
                [collections.deque(maxlen=MAX_BUFFER_SAMPLES) for _ in AXES]
                for _ in range(self.n_cells)
            ]

    def append(self, timestamp, all_forces):
        with self.lock:
            self.t.append(timestamp)
            for ci, forces in enumerate(all_forces):
                for ai, v in enumerate(forces):
                    self.data[ci][ai].append(v)

    def get_cell(self, cell_idx, window_s=None):
        with self.lock:
            if len(self.t) == 0:
                empty = np.array([])
                return empty, [empty, empty, empty]
            t = np.array(self.t)
            arrs = [np.array(self.data[cell_idx][ai]) for ai in range(3)]

        if window_s is not None and len(t) > 0:
            cutoff = t[-1] - window_s
            mask = t >= cutoff
            t = t[mask]
            arrs = [a[mask] for a in arrs]

        if len(t) > 0:
            t = t - t[0]
        return t, arrs


# ---------- Sampler thread ----------
class Sampler(threading.Thread):
    daemon = True

    def __init__(self, cells, store):
        super().__init__()
        self.cells = cells
        self.store = store
        self.rate_hz = DEFAULT_RATE_HZ
        self.running = False
        self._stop = False

    def run(self):
        while not self._stop:
            if not self.running:
                time.sleep(0.05)
                continue
            t0 = time.time()
            forces = []
            for c in self.cells:
                try:
                    forces.append(c.read_forces())
                except Exception:
                    forces.append([0.0, 0.0, 0.0])
            self.store.append(t0, forces)
            elapsed = time.time() - t0
            period = 1.0 / self.rate_hz
            if elapsed < period:
                time.sleep(period - elapsed)


# ---------- Cell tab with plot ----------
class CellTab(QWidget):
    def __init__(self, cell_idx, store):
        super().__init__()
        self.cell_idx = cell_idx
        self.store = store
        self.offset = [0.0, 0.0, 0.0]
        self.window_s = DEFAULT_WINDOW_S

        layout = QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)

        # --- Plot ---
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setBackground("w")
        self.plot_widget.setLabel("left", "Force (N)")
        self.plot_widget.setLabel("bottom", "Time (s)")
        self.plot_widget.addLegend()
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)

        self.curves = []
        for i, axis in enumerate(AXES):
            pen = pg.mkPen(color=COLORS[i], width=2)
            curve = self.plot_widget.plot([], [], pen=pen, name=axis)
            self.curves.append(curve)

        layout.addWidget(self.plot_widget, 1)

        # --- Current values ---
        val_font = QFont(); val_font.setPointSize(14); val_font.setBold(True)
        val_row = QHBoxLayout()
        self.val_labels = []
        for i, axis in enumerate(AXES):
            lbl = QLabel(f"{axis}: +0.000 N")
            lbl.setFont(val_font)
            lbl.setStyleSheet(f"color: {COLORS[i]};")
            val_row.addWidget(lbl)
            self.val_labels.append(lbl)
        layout.addLayout(val_row)

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

        self.offset_label = QLabel("offset: [0.000, 0.000, 0.000]")
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
        self.offset = [0.0, 0.0, 0.0]
        self._update_offset_label()

    def _update_offset_label(self):
        o = self.offset
        self.offset_label.setText(
            f"offset: [{o[0]:+.3f}, {o[1]:+.3f}, {o[2]:+.3f}]")

    def refresh(self):
        t, arrs = self.store.get_cell(self.cell_idx, self.window_s)
        for i in range(3):
            adjusted = arrs[i] - self.offset[i] if len(arrs[i]) > 0 else arrs[i]
            self.curves[i].setData(t, adjusted)
            if len(adjusted) > 0:
                self.val_labels[i].setText(
                    f"{AXES[i]}: {adjusted[-1]:+7.3f} N")


# ---------- Main window ----------
class Dashboard(QMainWindow):
    def __init__(self, sampler, store, n_cells):
        super().__init__()
        self.sampler = sampler
        self.store = store
        self.setWindowTitle("Load Cell Dashboard")
        self.resize(900, 550)

        central = QWidget()
        root = QVBoxLayout()
        central.setLayout(root)
        self.setCentralWidget(central)

        # ---- Control bar ----
        ctrl_font = QFont(); ctrl_font.setPointSize(11)
        ctrl = QHBoxLayout()

        # Start / Stop
        self.start_btn = QPushButton("Start")
        self.start_btn.setFont(ctrl_font)
        self.start_btn.setMinimumHeight(40)
        self.start_btn.setMinimumWidth(100)
        self.start_btn.setCheckable(True)
        self.start_btn.clicked.connect(self._toggle_run)
        ctrl.addWidget(self.start_btn)

        # Sampling rate
        rate_label = QLabel("  Rate (Hz):")
        rate_label.setFont(ctrl_font)
        ctrl.addWidget(rate_label)
        self.rate_spin = QSpinBox()
        self.rate_spin.setRange(1, 100)
        self.rate_spin.setValue(DEFAULT_RATE_HZ)
        self.rate_spin.setFont(ctrl_font)
        self.rate_spin.valueChanged.connect(self._rate_changed)
        ctrl.addWidget(self.rate_spin)

        # Rolling window
        win_label = QLabel("  Window (s):")
        win_label.setFont(ctrl_font)
        ctrl.addWidget(win_label)
        self.win_combo = QComboBox()
        self.win_combo.setFont(ctrl_font)
        for s in [5, 10, 20, 30, 60]:
            self.win_combo.addItem(str(s), s)
        self.win_combo.setCurrentIndex(1)  # 10 s default
        self.win_combo.currentIndexChanged.connect(self._window_changed)
        ctrl.addWidget(self.win_combo)

        # Clear data
        clear_btn = QPushButton("Clear Data")
        clear_btn.setFont(ctrl_font)
        clear_btn.setMinimumHeight(40)
        clear_btn.clicked.connect(self._clear_data)
        ctrl.addWidget(clear_btn)

        ctrl.addStretch()
        root.addLayout(ctrl)

        # ---- Tabs ----
        tabs = QTabWidget()
        self.cell_tabs = []
        for i in range(n_cells):
            tab = CellTab(i, store)
            tabs.addTab(tab, f"Cell {i+1}")
            self.cell_tabs.append(tab)
        root.addWidget(tabs, 1)

        # ---- Refresh timer ----
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(REFRESH_MS)

    def _toggle_run(self):
        if self.start_btn.isChecked():
            self.sampler.running = True
            self.start_btn.setText("Stop")
            self.start_btn.setStyleSheet(
                "background-color: #e74c3c; color: white;")
        else:
            self.sampler.running = False
            self.start_btn.setText("Start")
            self.start_btn.setStyleSheet("")

    def _rate_changed(self, val):
        self.sampler.rate_hz = val

    def _window_changed(self):
        ws = self.win_combo.currentData()
        for t in self.cell_tabs:
            t.window_s = ws

    def _clear_data(self):
        self.store.clear()

    def _refresh(self):
        for t in self.cell_tabs:
            t.refresh()


# ---------- Main ----------
def main():
    cells = [
        Sensor("Cell 1", bus=0, dev=0, csb_gpio=13),
        Sensor("Cell 2", bus=6, dev=0, csb_gpio=12),
        Sensor("Cell 3", bus=0, dev=0, csb_gpio=26),
        Sensor("Cell 4", bus=6, dev=0, csb_gpio=16),
    ]
    for c in cells:
        print(f"Initializing {c.name}...")
        c.init()
    print("All cells ready.\n")

    store = DataStore(len(cells))
    sampler = Sampler(cells, store)
    sampler.start()

    app = QApplication(sys.argv)
    win = Dashboard(sampler, store, len(cells))
    win.show()

    exit_code = app.exec()

    sampler._stop = True
    for c in cells:
        c.stop()
    sys.exit(exit_code)


main()
