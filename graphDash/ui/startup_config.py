"""Startup configuration dialog.

Shown before the main Dashboard is constructed. Lets the user edit sensor
config, run a Test Connection pass that attempts to init each cell, and then
continue into the app with the cells that succeeded.

Continue is disabled until the user has run Test Connection at least once.
If some cells fail, Continue proceeds with only the working ones (config
file is untouched).
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QScrollArea,
    QWidget,
)
from PyQt6.QtCore import Qt

from graphDash.ui import theme
from graphDash.ui.sensor_config_editor import SensorConfigEditor
from graphDash.config import save_sensor_config, try_init_sensors


class StartupConfigDialog(QDialog):
    """Return codes via `self.result_mode`:
       - "hardware": use `self.ready_cells` (live Sensor objects)
       - "debug":    caller should build DummySensors from `self.sensor_configs`
       - None:       user closed the dialog without continuing (exit app)
    """

    def __init__(self, sensor_configs, config_path, initial_debug=False, parent=None):
        super().__init__(parent)
        self.setWindowTitle("OrthoInsight — Sensor Setup")
        self.setModal(True)
        self.resize(900, 620)

        self.config_path = config_path
        self.initial_debug = initial_debug

        # Populated on Continue / Debug:
        self.result_mode = None
        self.ready_cells = []       # list[Sensor]
        self.sensor_configs = list(sensor_configs)  # final config snapshot

        self._has_tested = False

        root = QVBoxLayout()
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(12)

        title = QLabel("Sensor Setup")
        title.setStyleSheet(
            f"font-size: {theme.FONT_TITLE}pt; font-weight: 600; color: {theme.ON_SURFACE};")
        root.addWidget(title)

        sub = QLabel(
            "Configure each load cell's SPI bus, chip-select GPIO, and tooth assignment. "
            "Run <b>Test Connection</b> to verify each cell responds before entering the dashboard."
        )
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color: {theme.ON_SURFACE_MUTED}; font-size: {theme.FONT_BODY}pt;")
        root.addWidget(sub)

        self.editor = SensorConfigEditor(sensor_configs, show_status_column=True)
        self.editor.sensors_changed.connect(self._on_sensors_changed)
        root.addWidget(self.editor, stretch=1)

        # Failure summary panel (hidden until Test runs with failures).
        self.summary_frame = QFrame()
        self.summary_frame.setFrameShape(QFrame.Shape.StyledPanel)
        self.summary_frame.setStyleSheet(
            f"QFrame {{ background: {theme.SURFACE_ALT}; "
            f"border: 1px solid {theme.OUTLINE}; border-radius: 8px; }}"
        )
        summary_layout = QVBoxLayout(self.summary_frame)
        summary_layout.setContentsMargins(14, 10, 14, 10)
        summary_layout.setSpacing(4)
        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet(
            f"color: {theme.ON_SURFACE}; font-size: {theme.FONT_BODY}pt;")
        summary_layout.addWidget(self.summary_label)
        self.summary_frame.setVisible(False)
        root.addWidget(self.summary_frame)

        # Bottom button row.
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self.debug_btn = QPushButton("Skip — Debug Mode")
        self.debug_btn.setMinimumHeight(42)
        self.debug_btn.setProperty("variant", "accent")
        self.debug_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.debug_btn.setToolTip(
            "Enter the dashboard with simulated sensors instead of real hardware.")
        self.debug_btn.clicked.connect(self._on_debug)
        if initial_debug:
            # Already in debug mode — the shortcut is meaningless.
            self.debug_btn.setVisible(False)
        btn_row.addWidget(self.debug_btn)

        btn_row.addStretch()

        self.test_btn = QPushButton("Test Connection")
        self.test_btn.setMinimumHeight(42)
        self.test_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.test_btn.clicked.connect(self._on_test)
        btn_row.addWidget(self.test_btn)

        self.continue_btn = QPushButton("Continue →")
        self.continue_btn.setMinimumHeight(42)
        self.continue_btn.setProperty("variant", "primary")
        self.continue_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.continue_btn.setEnabled(False)
        self.continue_btn.setToolTip("Run Test Connection first.")
        self.continue_btn.clicked.connect(self._on_continue)
        btn_row.addWidget(self.continue_btn)

        root.addLayout(btn_row)
        self.setLayout(root)

    # -- signals --------------------------------------------------------

    def _on_sensors_changed(self, sensors):
        # Persist edits immediately so the user's config survives a close.
        save_sensor_config(self.config_path, sensors)
        # Any edit invalidates the last test pass.
        self._has_tested = False
        self.continue_btn.setEnabled(False)
        self.continue_btn.setToolTip("Run Test Connection first.")
        self.summary_frame.setVisible(False)

    def _on_test(self):
        sensors = self.editor.get_sensors()
        if not sensors:
            self.summary_label.setText(
                f"<span style='color:{theme.ACCENT_DANGER}; font-weight:600;'>"
                f"No sensors configured. Add at least one row.</span>")
            self.summary_frame.setVisible(True)
            return

        self.test_btn.setEnabled(False)
        self.test_btn.setText("Testing...")
        self.editor.clear_row_statuses()
        # Force a paint before the (potentially slow) init calls.
        self.repaint()

        try:
            if self.initial_debug:
                results, all_ok = _fake_init_dummies(sensors)
            else:
                results, all_ok = try_init_sensors(sensors)
        finally:
            self.test_btn.setEnabled(True)
            self.test_btn.setText("Test Connection")

        ready_cells = []
        failed = []
        for row, entry in enumerate(results):
            self.editor.set_row_status(row, entry["ok"], entry["error"])
            if entry["ok"]:
                ready_cells.append(entry["cell"])
            else:
                failed.append((entry["config"].get("name", f"Cell {row+1}"), entry["error"]))

        # Close any previously-held cells before replacing.
        self._release_ready_cells()
        self.ready_cells = ready_cells

        self._has_tested = True
        n_ok = len(ready_cells)
        n_total = len(results)

        if all_ok:
            self.summary_label.setText(
                f"<span style='color:{theme.ACCENT_SUCCESS}; font-weight:600;'>"
                f"All {n_total} cells ready.</span>")
            self.summary_frame.setVisible(True)
            self.continue_btn.setEnabled(True)
            self.continue_btn.setToolTip("")
        elif n_ok == 0:
            lines = ["<b style='color:{c}'>No cells could be initialized.</b>".format(c=theme.ACCENT_DANGER)]
            for name, err in failed:
                lines.append(f"• <b>{name}</b>: {err}")
            self.summary_label.setText("<br>".join(lines))
            self.summary_frame.setVisible(True)
            self.continue_btn.setEnabled(False)
            self.continue_btn.setToolTip("Fix the failing cells or use Debug Mode.")
        else:
            lines = [
                f"<b style='color:{theme.ACCENT_WARNING}'>"
                f"{n_ok} of {n_total} cells ready. Continue will drop the failing ones.</b>"
            ]
            for name, err in failed:
                lines.append(f"• <b>{name}</b>: {err}")
            self.summary_label.setText("<br>".join(lines))
            self.summary_frame.setVisible(True)
            self.continue_btn.setEnabled(True)
            self.continue_btn.setToolTip("Continue with only the working cells.")

    def _on_continue(self):
        all_configs = self.editor.get_sensors()
        if self.initial_debug:
            # Debug: sim cells for every configured row, no hardware.
            self._release_ready_cells()
            self.ready_cells = []
            self.sensor_configs = all_configs
            self.result_mode = "debug"
        else:
            # Hardware: keep only the rows whose Sensor is live.
            ready_names = {c.name for c in self.ready_cells}
            self.sensor_configs = [
                c for c in all_configs if c.get("name") in ready_names
            ]
            self.result_mode = "hardware"
        self.accept()

    def _on_debug(self):
        # Debug: release any live cells, snapshot the full config.
        self._release_ready_cells()
        self.ready_cells = []
        self.sensor_configs = self.editor.get_sensors()
        self.result_mode = "debug"
        self.accept()

    def _release_ready_cells(self):
        for cell in self.ready_cells:
            try:
                cell.stop()
            except Exception:
                pass
        self.ready_cells = []

    def reject(self):
        # User closed the dialog (X button / Esc): drop live cells.
        self._release_ready_cells()
        self.result_mode = None
        super().reject()


def _fake_init_dummies(sensor_configs):
    """Debug-mode counterpart to `try_init_sensors`: every row succeeds.
    Returns the same (results, all_ok) shape but with cell=None (the caller
    builds DummySensors for the whole set after the dialog closes)."""
    results = [
        {"config": cfg, "cell": None, "ok": True, "error": ""}
        for cfg in sensor_configs
    ]
    return results, True
