from PyQt6.QtWidgets import (
    QMainWindow, QTabWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QSpinBox, QComboBox, QFrame,
)
from PyQt6.QtCore import QTimer, Qt

from graphDash.constants import DEFAULT_RATE_HZ, REFRESH_MS
from graphDash.ui import theme
from graphDash.ui.cell_tab import CellTab
from graphDash.ui.sessions_tab import SessionsTab
from graphDash.ui.arch_tab import ArchTab
from graphDash.ui.position_vector_tab import PositionVectorTab
from graphDash.ui.sensor_config_tab import SensorConfigTab


def _labeled_control(label_text, widget):
    """Vertical stack: small label above a control. Keeps the control bar
    scannable without inline colons."""
    box = QVBoxLayout()
    box.setSpacing(2)
    box.setContentsMargins(0, 0, 0, 0)
    lbl = QLabel(label_text)
    lbl.setProperty("muted", True)
    lbl.setStyleSheet(f"font-size: {theme.FONT_CAPTION}pt; color: {theme.ON_SURFACE_MUTED};")
    box.addWidget(lbl)
    box.addWidget(widget)
    wrap = QWidget()
    wrap.setLayout(box)
    return wrap


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
        self.setWindowTitle("OrthoInsight — Load Cell Dashboard")
        self.resize(1100, 800)

        central = QWidget()
        root = QVBoxLayout()
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(14)
        central.setLayout(root)
        self.setCentralWidget(central)

        # ---- Header ----
        header_row = QHBoxLayout()
        header_row.setSpacing(12)
        title = QLabel("OrthoInsight")
        title.setProperty("role", "title")
        header_row.addWidget(title)
        header_row.addStretch()

        self.status_dot = QLabel("●")
        self.status_dot.setStyleSheet(f"color: {theme.ON_SURFACE_SUBTLE}; font-size: 14pt;")
        self.status_text = QLabel("Idle")
        self.status_text.setProperty("muted", True)
        self.status_text.setStyleSheet(f"color: {theme.ON_SURFACE_MUTED}; font-size: {theme.FONT_BODY}pt;")
        header_row.addWidget(self.status_dot)
        header_row.addWidget(self.status_text)
        root.addLayout(header_row)

        # subtle divider under the header
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet(f"color: {theme.OUTLINE}; background-color: {theme.OUTLINE}; max-height: 1px;")
        root.addWidget(divider)

        # ---- Control bar ----
        ctrl_container = QFrame()
        ctrl_container.setStyleSheet(
            f"QFrame {{ background-color: {theme.SURFACE_ALT}; "
            f"border: 1px solid {theme.OUTLINE}; border-radius: 10px; }}"
        )
        ctrl = QHBoxLayout()
        ctrl.setContentsMargins(14, 12, 14, 12)
        ctrl.setSpacing(14)
        ctrl_container.setLayout(ctrl)

        # Start / Stop — primary action
        self.start_btn = QPushButton("Start Recording")
        self.start_btn.setMinimumHeight(42)
        self.start_btn.setMinimumWidth(160)
        self.start_btn.setCheckable(True)
        self.start_btn.setProperty("variant", "primary")
        self.start_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_btn.clicked.connect(self._toggle_run)
        ctrl.addWidget(self.start_btn)

        # Sampling rate
        self.rate_spin = QSpinBox()
        self.rate_spin.setRange(1, 100)
        self.rate_spin.setValue(DEFAULT_RATE_HZ)
        self.rate_spin.setMinimumWidth(80)
        self.rate_spin.valueChanged.connect(self._rate_changed)
        ctrl.addWidget(_labeled_control("SAMPLE RATE (Hz)", self.rate_spin))

        # Rolling window
        self.win_combo = QComboBox()
        for s in [5, 10, 20, 30, 60]:
            self.win_combo.addItem(f"{s}s", s)
        self.win_combo.setCurrentIndex(1)  # 10 s default
        self.win_combo.setMinimumWidth(80)
        self.win_combo.currentIndexChanged.connect(self._window_changed)
        ctrl.addWidget(_labeled_control("WINDOW", self.win_combo))

        # Moving average
        self.ma_spin = QSpinBox()
        self.ma_spin.setRange(1, 200)
        self.ma_spin.setValue(1)
        self.ma_spin.setMinimumWidth(80)
        self.ma_spin.setToolTip("Moving average window in samples (1 = off)")
        self.ma_spin.valueChanged.connect(self._ma_changed)
        ctrl.addWidget(_labeled_control("SMOOTHING", self.ma_spin))

        ctrl.addStretch()

        # Log Point (manual logging) — ghost
        self.log_point_btn = QPushButton("Log Point")
        self.log_point_btn.setMinimumHeight(38)
        self.log_point_btn.setEnabled(False)
        self.log_point_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.log_point_btn.clicked.connect(self._log_manual_point)
        ctrl.addWidget(self.log_point_btn)

        # Clear data — ghost
        clear_btn = QPushButton("Clear Data")
        clear_btn.setMinimumHeight(38)
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.clicked.connect(self._clear_data)
        ctrl.addWidget(clear_btn)

        # Debug / simulation mode — accent when on
        self.debug_btn = QPushButton("Debug Mode")
        self.debug_btn.setMinimumHeight(38)
        self.debug_btn.setCheckable(True)
        self.debug_btn.setChecked(self.sampler.simulate)
        self.debug_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.debug_btn.clicked.connect(self._toggle_debug)
        ctrl.addWidget(self.debug_btn)
        self._toggle_debug()

        root.addWidget(ctrl_container)

        # ---- Tabs ----
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
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

    def _set_status(self, state):
        if state == "recording":
            self.status_dot.setStyleSheet(f"color: {theme.ACCENT_DANGER}; font-size: 14pt;")
            self.status_text.setText("Recording")
            self.status_text.setStyleSheet(
                f"color: {theme.ACCENT_DANGER}; font-size: {theme.FONT_BODY}pt; font-weight: 600;")
        else:
            self.status_dot.setStyleSheet(f"color: {theme.ON_SURFACE_SUBTLE}; font-size: 14pt;")
            self.status_text.setText("Idle")
            self.status_text.setStyleSheet(
                f"color: {theme.ON_SURFACE_MUTED}; font-size: {theme.FONT_BODY}pt;")

    def _restyle(self, btn):
        """Force a QSS re-evaluation after changing a dynamic property."""
        btn.style().unpolish(btn)
        btn.style().polish(btn)

    def _toggle_run(self):
        if self.start_btn.isChecked():
            if self.csv_logger:
                self.csv_logger.start_recording()
            self.sampler.running = True
            self.start_btn.setText("Stop Recording")
            self.start_btn.setProperty("variant", "danger")
            self._restyle(self.start_btn)
            self.log_point_btn.setEnabled(True)
            self._set_status("recording")
        else:
            self.sampler.running = False
            if self.csv_logger:
                self.csv_logger.stop_recording()
            self.start_btn.setText("Start Recording")
            self.start_btn.setProperty("variant", "primary")
            self._restyle(self.start_btn)
            self.log_point_btn.setEnabled(False)
            self.sessions_tab.refresh()
            self._set_status("idle")

    def _toggle_debug(self):
        self.sampler.simulate = self.debug_btn.isChecked()
        if self.sampler.simulate:
            self.debug_btn.setText("Debug Mode: ON")
            self.debug_btn.setProperty("variant", "accent")
        else:
            self.debug_btn.setText("Debug Mode: OFF")
            self.debug_btn.setProperty("variant", "")
        self._restyle(self.debug_btn)

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
                f"color: {theme.ACCENT_WARNING}; font-weight: 600;")

    def _refresh(self):
        for t in self.cell_tabs:
            t.refresh()
