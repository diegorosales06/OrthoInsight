#!/usr/bin/env python3
"""
Load-cell dashboard — runs entirely on the Pi.

Reads 4 MMS101 cells over SPI, displays a tabbed PyQt6 UI with
Fx/Fy/Fz per cell and a per-cell tare function.

Install once on the Pi:
    sudo apt install python3-pyqt6

Run (from laptop, with X11 forwarding):
    ssh -X pi@<PI_IP>
    python3 cell_dashboard.py

Or directly on the Pi with a monitor attached:
    python3 cell_dashboard.py

Press Ctrl-C or close the window to stop.
"""

import sys
import time
import threading
import spidev
from gpiozero import DigitalOutputDevice

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QWidget, QVBoxLayout,
    QHBoxLayout, QLabel, QPushButton,
)
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QFont

# ---------- MMS101 constants ----------
CMD_START, CMD_DATA2, CMD_BOOT   = 0xF0, 0xE2, 0xB0
CMD_STOP,  CMD_RESET, CMD_STATUS = 0xB2, 0xB4, 0x80
CMD_INTERVAL                     = 0x44
CMD_COEFF = (0x30, 0x32, 0x34, 0x36, 0x38, 0x3A)

STT_STANDBY, STT_READY = 1, 3

AXES = ("Fx", "Fy", "Fz")
SAMPLE_HZ = 20
REFRESH_MS = 50


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
            raise RuntimeError(f"{self.name}: cmd 0x{tx[0]:02X} -> status 0x{r[0]:02X}")
        return r

    def _wait(self, state):
        for _ in range(10):
            time.sleep(0.02)
            if self._cmd([CMD_STATUS], 4)[3] == state:
                return
        raise RuntimeError(f"{self.name}: timeout waiting for state {state}")

    def init(self):
        self._cmd([CMD_RESET], 1);   self._wait(STT_STANDBY)
        self._cmd([CMD_BOOT],  1);   self._wait(STT_READY)
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
            acc = sum(c*a for c, a in zip(self.coeff[axis], adc))
            out.append(int(acc / 2048) / 1000.0)
        return out

    def stop(self):
        try: self._cmd([CMD_STOP], 1)
        except Exception: pass
        self.spi.close()
        self.csb.close()


# ---------- Background sampler ----------
class Sampler(threading.Thread):
    """Reads all cells in a loop, stores the latest values."""
    daemon = True

    def __init__(self, cells):
        super().__init__()
        self.cells = cells
        self.latest = [[0.0, 0.0, 0.0] for _ in cells]
        self._stop = False

    def run(self):
        period = 1.0 / SAMPLE_HZ
        while not self._stop:
            t0 = time.time()
            for i, c in enumerate(self.cells):
                try:
                    self.latest[i] = c.read_forces()
                except Exception:
                    pass  # keep last good value
            elapsed = time.time() - t0
            if elapsed < period:
                time.sleep(period - elapsed)


# ---------- UI ----------
class CellTab(QWidget):
    def __init__(self, cell_idx, get_raw):
        super().__init__()
        self.cell_idx = cell_idx
        self.get_raw = get_raw
        self.offset = [0.0, 0.0, 0.0]

        big  = QFont(); big.setPointSize(40); big.setBold(True)
        med  = QFont(); med.setPointSize(20)
        btnf = QFont(); btnf.setPointSize(14)

        layout = QVBoxLayout()
        layout.setSpacing(15)
        layout.setContentsMargins(30, 30, 30, 30)

        self.value_labels = {}
        for axis in AXES:
            row = QHBoxLayout()
            name = QLabel(axis)
            name.setFont(med)
            val = QLabel("+0.000 N")
            val.setFont(big)
            val.setAlignment(Qt.AlignmentFlag.AlignRight
                             | Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(name)
            row.addWidget(val, 1)
            layout.addLayout(row)
            self.value_labels[axis] = val

        btn_row = QHBoxLayout()
        tare_btn = QPushButton("Tare")
        tare_btn.setFont(btnf)
        tare_btn.setMinimumHeight(50)
        tare_btn.clicked.connect(self.tare)

        clear_btn = QPushButton("Clear Tare")
        clear_btn.setFont(btnf)
        clear_btn.setMinimumHeight(50)
        clear_btn.clicked.connect(self.clear_tare)

        btn_row.addWidget(tare_btn)
        btn_row.addWidget(clear_btn)
        layout.addLayout(btn_row)

        self.offset_label = QLabel("offset: [0.000, 0.000, 0.000]")
        self.offset_label.setStyleSheet("color: gray;")
        layout.addWidget(self.offset_label)

        self.setLayout(layout)

    def tare(self):
        self.offset = list(self.get_raw())
        self._update_offset_label()

    def clear_tare(self):
        self.offset = [0.0, 0.0, 0.0]
        self._update_offset_label()

    def _update_offset_label(self):
        o = self.offset
        self.offset_label.setText(
            f"offset: [{o[0]:+.3f}, {o[1]:+.3f}, {o[2]:+.3f}]")

    def refresh(self):
        raw = self.get_raw()
        for i, axis in enumerate(AXES):
            v = raw[i] - self.offset[i]
            self.value_labels[axis].setText(f"{v:+7.3f} N")


class Dashboard(QMainWindow):
    def __init__(self, sampler):
        super().__init__()
        self.sampler = sampler
        self.setWindowTitle("Load Cell Dashboard")
        self.resize(560, 380)

        tabs = QTabWidget()
        self.cell_tabs = []
        for i in range(len(sampler.cells)):
            tab = CellTab(i, lambda i=i: sampler.latest[i])
            tabs.addTab(tab, f"Cell {i+1}")
            self.cell_tabs.append(tab)
        self.setCentralWidget(tabs)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(REFRESH_MS)

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

    sampler = Sampler(cells)
    sampler.start()

    app = QApplication(sys.argv)
    win = Dashboard(sampler)
    win.show()

    exit_code = app.exec()

    sampler._stop = True
    for c in cells:
        c.stop()
    sys.exit(exit_code)


main()
