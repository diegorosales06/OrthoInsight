from PyQt6.QtWidgets import (
    QMainWindow, QTabWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QSpinBox, QComboBox,
)
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QFont

from graphDash.constants import DEFAULT_RATE_HZ, REFRESH_MS
from graphDash.ui.cell_tab import CellTab
from graphDash.ui.sessions_tab import SessionsTab
from graphDash.ui.arch_tab import ArchTab
from graphDash.ui.position_vector_tab import PositionVectorTab
from graphDash.ui.sensor_config_tab import SensorConfigTab


class Dashboard(QMainWindow):
    def __init__(self, sampler, store, n_cells, csv_logger=None, tooth_per_cell=None, pos_vectors=None,
                 config_path=None, sensor_configs=None):
        super().__init__()
        self.sampler = sampler
        self.store = store
        self.csv_logger = csv_logger
        self.tooth_per_cell = tooth_per_cell or []
        self.pos_vectors = pos_vectors
        self.config_path = config_path
        self.sensor_configs = sensor_configs or []
        self.n_cells = n_cells
        self.setWindowTitle("Load Cell Dashboard")
        self.resize(900, 750)

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

        # Moving average
        ma_label = QLabel("  MA (samples):")
        ma_label.setFont(ctrl_font)
        ctrl.addWidget(ma_label)
        self.ma_spin = QSpinBox()
        self.ma_spin.setRange(1, 200)
        self.ma_spin.setValue(1)
        self.ma_spin.setFont(ctrl_font)
        self.ma_spin.setToolTip("Moving average window in samples (1 = off)")
        self.ma_spin.valueChanged.connect(self._ma_changed)
        ctrl.addWidget(self.ma_spin)

        # Debug / simulation mode
        self.debug_btn = QPushButton("Debug Mode")
        self.debug_btn.setFont(ctrl_font)
        self.debug_btn.setMinimumHeight(40)
        self.debug_btn.setCheckable(True)
        self.debug_btn.setChecked(self.sampler.simulate)
        self.debug_btn.clicked.connect(self._toggle_debug)
        ctrl.addWidget(self.debug_btn)
        self._toggle_debug()

        # Clear data
        clear_btn = QPushButton("Clear Data")
        clear_btn.setFont(ctrl_font)
        clear_btn.setMinimumHeight(40)
        clear_btn.clicked.connect(self._clear_data)
        ctrl.addWidget(clear_btn)

        # Log Point (manual logging)
        self.log_point_btn = QPushButton("Log Point")
        self.log_point_btn.setFont(ctrl_font)
        self.log_point_btn.setMinimumHeight(40)
        self.log_point_btn.setEnabled(False)
        self.log_point_btn.clicked.connect(self._log_manual_point)
        ctrl.addWidget(self.log_point_btn)

        ctrl.addStretch()
        root.addLayout(ctrl)

        # ---- Tabs ----
        self.tabs = QTabWidget()
        self.cell_tabs = []
        for i in range(n_cells):
            tab = CellTab(i, store)
            self.tabs.addTab(tab, f"Cell {i+1}")
            self.cell_tabs.append(tab)
        self.arch_tab = ArchTab(store, tooth_per_cell=self.tooth_per_cell)
        self.tabs.addTab(self.arch_tab, "Arch View")
        if self.pos_vectors:
            self.pos_vector_tab = PositionVectorTab(self.pos_vectors)
            self.tabs.addTab(self.pos_vector_tab, "Position Vector")
        self.sessions_tab = SessionsTab()
        self.tabs.addTab(self.sessions_tab, "Sessions")
        if self.config_path:
            self.sensor_config_tab = SensorConfigTab(self.sensor_configs, self.config_path)
            self.sensor_config_tab.config_changed.connect(self._on_config_changed)
            self.tabs.addTab(self.sensor_config_tab, "Sensor Config")
        root.addWidget(self.tabs, 1)

        # ---- Refresh timer ----
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(REFRESH_MS)

    def _toggle_run(self):
        if self.start_btn.isChecked():
            if self.csv_logger:
                self.csv_logger.start_recording()
            self.sampler.running = True
            self.start_btn.setText("Stop")
            self.start_btn.setStyleSheet(
                "background-color: #e74c3c; color: white;")
            self.log_point_btn.setEnabled(True)
        else:
            self.sampler.running = False
            if self.csv_logger:
                self.csv_logger.stop_recording()
            self.start_btn.setText("Start")
            self.start_btn.setStyleSheet("")
            self.log_point_btn.setEnabled(False)
            self.sessions_tab.refresh()

    def _toggle_debug(self):
        self.sampler.simulate = self.debug_btn.isChecked()
        if self.sampler.simulate:
            self.debug_btn.setText("Debug Mode: ON")
            self.debug_btn.setStyleSheet("background-color: #3498db; color: white;")
        else:
            self.debug_btn.setText("Debug Mode: OFF")
            self.debug_btn.setStyleSheet("")

    def _rate_changed(self, val):
        self.sampler.rate_hz = val

    def _window_changed(self):
        ws = self.win_combo.currentData()
        for t in self.cell_tabs:
            t.window_s = ws

    def _ma_changed(self, val):
        for t in self.cell_tabs:
            t.ma_n = val

    def _clear_data(self):
        self.store.clear()

    def _log_manual_point(self):
        """Capture and log current data point for all cells."""
        if not self.csv_logger:
            return

        import time
        timestamp = time.time()

        for cell_idx in range(len(self.cell_tabs)):
            t, arrs = self.store.get_cell(cell_idx)
            if len(t) > 0:
                force_moment = [arr[-1] for arr in arrs]
                self.csv_logger.log_manual_point(timestamp, f"Cell {cell_idx + 1}", force_moment)

    def _on_config_changed(self, sensors):
        if self.start_btn.isChecked():
            self.start_btn.setChecked(False)
            self._toggle_run()

        n = min(len(sensors), self.n_cells)
        names = [s.get("name", f"Cell {i+1}") for i, s in enumerate(sensors)]
        tooth_types = [s.get("tooth_type") for s in sensors]
        teeth = [s.get("tooth") for s in sensors]

        self.sampler.cell_names = names
        self.sampler.cell_tooth_types = tooth_types
        self.tooth_per_cell = teeth

        if hasattr(self.arch_tab, 'tooth_per_cell'):
            self.arch_tab.tooth_per_cell = teeth[:self.n_cells]

        for i in range(n):
            self.tabs.setTabText(i, names[i])

        self.store.clear()

        if len(sensors) != self.n_cells:
            self.sensor_config_tab.status_label.setText(
                "Config saved. Restart app for cell count changes to take effect.")
            self.sensor_config_tab.status_label.setStyleSheet(
                "color: #e67e22; font-weight: bold;")

    def _refresh(self):
        for t in self.cell_tabs:
            t.refresh()
