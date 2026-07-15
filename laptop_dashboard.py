#!/usr/bin/env python3
"""
Laptop-side dashboard for the 4-cell load cell rig.

Install once:
    pip install PyQt6

Then:
    1. Edit PI_HOST below to match your Pi's IP (USB gadget or direct Ethernet).
    2. Start pi_sender.py on the Pi.
    3. Run:  python3 laptop_dashboard.py

Features:
    - One tab per cell, showing Fx / Fy / Fz in Newtons.
    - "Tare" button per tab: snapshots current reading as an offset so
      the displayed values go to ~0. "Clear tare" undoes it.
    - Auto-reconnects if the Pi drops or restarts.
"""

import sys
import socket
import json
import threading
import time

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QWidget, QVBoxLayout,
    QHBoxLayout, QLabel, QPushButton,
)
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QFont

# ---------- CONFIG ----------
PI_HOST = "10.55.0.1"   # <-- SET THIS to your Pi's USB-gadget or Ethernet IP
PI_PORT = 5555
N_CELLS = 4
AXES    = ("Fx", "Fy", "Fz")
REFRESH_MS = 50         # UI refresh rate (20 Hz)
# ----------------------------


class Receiver(threading.Thread):
    """Background thread: connects to Pi, reads JSON lines, stores latest sample."""
    daemon = True

    def __init__(self, host, port):
        super().__init__()
        self.host = host
        self.port = port
        self.latest = [[0.0, 0.0, 0.0] for _ in range(N_CELLS)]
        self.connected = False
        self._stop = False

    def run(self):
        while not self._stop:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(3.0)
                s.connect((self.host, self.port))
                s.settimeout(None)
                self.connected = True
                f = s.makefile("r")
                for line in f:
                    if self._stop:
                        break
                    try:
                        msg = json.loads(line)
                        cells = msg.get("cells", [])
                        if len(cells) == N_CELLS:
                            self.latest = cells
                    except json.JSONDecodeError:
                        pass
            except (socket.timeout, ConnectionRefusedError, OSError):
                pass
            finally:
                self.connected = False
                try: s.close()
                except Exception: pass
                time.sleep(1.0)


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
            val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
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
        self.offset_label.setText(f"offset: [{o[0]:+.3f}, {o[1]:+.3f}, {o[2]:+.3f}]")

    def refresh(self):
        raw = self.get_raw()
        for i, axis in enumerate(AXES):
            v = raw[i] - self.offset[i]
            self.value_labels[axis].setText(f"{v:+7.3f} N")


class Dashboard(QMainWindow):
    def __init__(self, receiver):
        super().__init__()
        self.receiver = receiver
        self.setWindowTitle("Load Cell Dashboard")
        self.resize(560, 380)

        tabs = QTabWidget()
        self.cell_tabs = []
        for i in range(N_CELLS):
            tab = CellTab(i, lambda i=i: self.receiver.latest[i])
            tabs.addTab(tab, f"Cell {i+1}")
            self.cell_tabs.append(tab)
        self.setCentralWidget(tabs)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(REFRESH_MS)

    def _refresh(self):
        status = "connected" if self.receiver.connected else f"waiting for {PI_HOST}:{PI_PORT}..."
        self.setWindowTitle(f"Load Cell Dashboard — {status}")
        for t in self.cell_tabs:
            t.refresh()


def main():
    app = QApplication(sys.argv)
    rx = Receiver(PI_HOST, PI_PORT)
    rx.start()
    win = Dashboard(rx)
    win.show()
    sys.exit(app.exec())


main()
