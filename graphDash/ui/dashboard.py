from PyQt6.QtWidgets import (
    QMainWindow, QTabWidget, QTabBar, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QSpinBox, QComboBox, QFrame, QMenu, QLineEdit,
)
from PyQt6.QtCore import QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup

from graphDash.constants import (
    DEFAULT_RATE_HZ, REFRESH_MS, DEFAULT_SMOOTH_S, MAX_SMOOTH_S,
)
from graphDash.ui import theme
from graphDash.ui.cells_tab import CellsTab
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


class _CellTabBar(QTabBar):
    """Tab bar where one designated tab doubles as a dropdown: clicking it
    switches to that tab *and* pops up a menu of the available load cells."""

    cell_picked = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self.menu_index = -1
        self._items = []
        self._current_item = 0

    def set_menu_tab(self, tab_index, items, current=0):
        self.menu_index = tab_index
        self._items = list(items)
        self._current_item = current

    def mousePressEvent(self, event):
        idx = self.tabAt(event.position().toPoint())
        if idx == self.menu_index and event.button() == Qt.MouseButton.LeftButton:
            self.setCurrentIndex(idx)
            self._popup(idx)
            return
        super().mousePressEvent(event)

    def _popup(self, tab_index):
        if not self._items:
            return
        menu = QMenu(self)
        group = QActionGroup(menu)
        group.setExclusive(True)
        for i, name in enumerate(self._items):
            act = QAction(name, menu)
            act.setCheckable(True)
            act.setChecked(i == self._current_item)
            act.triggered.connect(lambda _checked, n=i: self.cell_picked.emit(n))
            group.addAction(act)
            menu.addAction(act)
        rect = self.tabRect(tab_index)
        menu.setMinimumWidth(rect.width())
        menu.exec(self.mapToGlobal(rect.bottomLeft()))


class Dashboard(QMainWindow):
    def __init__(self, sampler, store, n_cells, csv_logger=None, tooth_per_cell=None, pos_vectors=None,
                 config_path=None, sensor_configs=None, tare_offsets=None,
                 smoother=None):
        super().__init__()
        self.sampler = sampler
        self.store = store
        self.csv_logger = csv_logger
        self.tooth_per_cell = tooth_per_cell or []
        self.pos_vectors = pos_vectors
        self.tare_offsets = tare_offsets
        self.smoother = smoother
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

        # Moving average — window in seconds, applied upstream in the sampler
        # (after tare, before compensation), not at draw time.
        self.ma_spin = QSpinBox()
        self.ma_spin.setRange(0, MAX_SMOOTH_S)
        self.ma_spin.setValue(DEFAULT_SMOOTH_S)
        self.ma_spin.setSuffix(" s")
        self.ma_spin.setMinimumWidth(80)
        self.ma_spin.setToolTip(
            "Moving average window in seconds (0 = off).\n"
            "Converted to a sample count using the live sample rate, so the\n"
            "window stays the same length in time when the rate changes.")
        self.ma_spin.valueChanged.connect(self._ma_changed)
        ctrl.addWidget(_labeled_control("SMOOTHING", self.ma_spin))
        self._ma_changed(self.ma_spin.value())

        ctrl.addStretch()

        # Point name — user label stamped onto every cell's manual-log row
        self.point_name_edit = QLineEdit()
        self.point_name_edit.setPlaceholderText("Point name (optional)")
        self.point_name_edit.setMinimumHeight(38)
        self.point_name_edit.setMaximumWidth(180)
        self.point_name_edit.setEnabled(False)
        self.point_name_edit.returnPressed.connect(self._log_manual_point)
        ctrl.addWidget(_labeled_control("POINT NAME", self.point_name_edit))

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

        # Tare All / Clear Tare — global, applies to every cell at once
        self.tare_btn = QPushButton("Tare All")
        self.tare_btn.setMinimumHeight(38)
        self.tare_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.tare_btn.setToolTip("Zero every cell from the same sample cycle")
        self.tare_btn.clicked.connect(self._tare_all)
        ctrl.addWidget(self.tare_btn)

        self.clear_tare_btn = QPushButton("Clear Tare")
        self.clear_tare_btn.setMinimumHeight(38)
        self.clear_tare_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_tare_btn.clicked.connect(self._clear_tare)
        ctrl.addWidget(self.clear_tare_btn)

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

        # Manual-log confirmation flag — rendered plainly on the window
        # background (no card/border/tint), right-aligned under the control bar.
        self.log_confirm_label = QLabel("")
        self.log_confirm_label.setStyleSheet(
            f"font-size: {theme.FONT_CAPTION}pt; color: {theme.ACCENT_SUCCESS}; "
            f"font-weight: 600; background: transparent; border: none;")
        root.addWidget(self.log_confirm_label, alignment=Qt.AlignmentFlag.AlignRight)

        # ---- Tabs ----
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.cell_tab_bar = _CellTabBar()
        self.tabs.setTabBar(self.cell_tab_bar)
        names = [
            self.sensor_configs[i].get("name", f"Cell {i+1}") if i < len(self.sensor_configs) else f"Cell {i+1}"
            for i in range(n_cells)
        ]
        self.cells_tab = CellsTab(n_cells, store, names, tare_offsets=tare_offsets)
        self.cell_tabs = self.cells_tab.cell_tabs
        self.cells_index = self.tabs.addTab(self.cells_tab, "Cell Graphs")
        self.cell_tab_bar.cell_picked.connect(self.cells_tab.select)
        self.cells_tab.selection_changed.connect(self._cell_selection_changed)
        self._cell_selection_changed(0)
        self.arch_tab = ArchTab(store, tooth_per_cell=self.tooth_per_cell)
        self.tabs.addTab(self.arch_tab, "Arch View")
        self.arch_tab.cell_picked.connect(self._show_cell)
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

    def _show_cell(self, idx):
        """Bring one cell's graphs to the front -- where the Arch View's crowns go.

        `CellsTab.select` is a no-op when that cell is already showing, and being
        a no-op it emits nothing, so raising the tab cannot be left to
        `selection_changed`. It happens here, unconditionally, and *after* the
        stack has been pointed at the right cell, so the tab is never briefly
        showing the cell the user was last looking at. When the select is a
        no-op the labels are already right: `_cell_selection_changed` is their
        only writer and it ran the last time the index changed.
        """
        if not 0 <= idx < len(self.cells_tab.names):
            return
        self.cells_tab.select(idx)
        self.tabs.setCurrentIndex(self.cells_index)

    def _cell_selection_changed(self, idx):
        """Keep the tab label and its dropdown in sync with the visible cell."""
        self.cell_tab_bar.set_menu_tab(self.cells_index, self.cells_tab.names, idx)
        self.tabs.setTabText(self.cells_index, f"Cell Graphs · {self.cells_tab.current_name()}  ▾")

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
            self._flush_smoother()
            self.sampler.running = True
            self.start_btn.setText("Stop Recording")
            self.start_btn.setProperty("variant", "danger")
            self._restyle(self.start_btn)
            self.log_point_btn.setEnabled(True)
            self.point_name_edit.setEnabled(True)
            self._set_status("recording")
        else:
            self.sampler.running = False
            if self.csv_logger:
                self.csv_logger.stop_recording()
            self.start_btn.setText("Start Recording")
            self.start_btn.setProperty("variant", "primary")
            self._restyle(self.start_btn)
            self.log_point_btn.setEnabled(False)
            self.point_name_edit.setEnabled(False)
            self.log_confirm_label.setText("")
            self.sessions_tab.refresh()
            self._set_status("idle")

    def _toggle_debug(self):
        self._flush_smoother()
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
        """Window size in seconds for the upstream filter."""
        if self.smoother is not None:
            self.smoother.window_s = val

    def _clear_data(self):
        self.store.clear()

    def _flush_smoother(self):
        """Drop the moving average's history.

        Called at the discontinuities where averaging across the boundary would
        smear a step into the data: starting a recording, switching between
        real and simulated sensors, and taring.
        """
        if self.smoother is not None:
            self.smoother.reset_all()

    def _tare_all(self):
        """Zero every cell at once, all from the same sampler cycle."""
        if self.sampler is None or self.tare_offsets is None:
            return
        snap = self.sampler.get_all_last_raw()
        if not snap:
            self._flash("Tare needs live data — start recording first.", warning=True)
            return
        self.tare_offsets.set_all(snap)
        self._flush_smoother()
        self._refresh_offset_labels()
        self._flash("✓ Tared all cells")

    def _clear_tare(self):
        if self.tare_offsets is None:
            return
        self.tare_offsets.clear_all()
        self._flush_smoother()
        self._refresh_offset_labels()
        self._flash("✓ Tare cleared")

    def _refresh_offset_labels(self):
        for t in self.cell_tabs:
            t._update_offset_label()

    def _log_manual_point(self):
        """Capture and log current data point for all cells."""
        if not self.csv_logger:
            return

        import time
        timestamp = time.time()
        label = self.point_name_edit.text().strip()

        logged = False
        for cell_idx in range(len(self.cell_tabs)):
            t, arrs = self.store.get_cell(cell_idx)
            if len(t) > 0:
                force_moment = [arr[-1] for arr in arrs]
                cell_id = self._cell_name(cell_idx)
                self.csv_logger.log_manual_point(timestamp, cell_id, force_moment, label)
                logged = True

        if logged:
            self._show_log_confirmation(label)
            self.point_name_edit.clear()

    def _cell_name(self, cell_idx):
        """Sensor name from sensors.yaml for this cell (matches the auto log's
        cell_id, which the sampler writes from the same names list)."""
        names = self.sampler.cell_names
        if cell_idx < len(names):
            return names[cell_idx]
        return f"Cell {cell_idx + 1}"

    def _flash(self, text, warning=False):
        """Flash a transient message on the window background (~2.5 s)."""
        color = theme.ACCENT_WARNING if warning else theme.ACCENT_SUCCESS
        self.log_confirm_label.setStyleSheet(
            f"font-size: {theme.FONT_CAPTION}pt; color: {color}; "
            f"font-weight: 600; background: transparent; border: none;")
        self.log_confirm_label.setText(text)
        QTimer.singleShot(2500, lambda: self.log_confirm_label.setText(""))

    def _show_log_confirmation(self, label):
        self._flash(f'✓ Logged "{label}"' if label else "✓ Point logged")

    def _on_config_changed(self, sensors):
        if self.start_btn.isChecked():
            self.start_btn.setChecked(False)
            self._toggle_run()

        names = [s.get("name", f"Cell {i+1}") for i, s in enumerate(sensors)]
        tooth_types = [s.get("tooth_type") for s in sensors]
        teeth = [s.get("tooth") for s in sensors]

        self.sampler.cell_names = names
        self.sampler.cell_tooth_types = tooth_types
        self.tooth_per_cell = teeth

        self.arch_tab.set_tooth_per_cell(teeth[:self.n_cells])

        self.cells_tab.set_names(names)

        self.store.clear()

        missing_types = [names[i] for i, tt in enumerate(tooth_types) if not tt]
        if missing_types:
            self._flash(
                f"No tooth type for {', '.join(missing_types)} — compensation skipped",
                warning=True)

        if len(sensors) != self.n_cells:
            self.sensor_config_tab.status_label.setText(
                "Config saved. Restart app for cell count changes to take effect.")
            self.sensor_config_tab.status_label.setStyleSheet(
                f"color: {theme.ACCENT_WARNING}; font-weight: 600;")

    def _refresh(self):
        self.cells_tab.refresh()
