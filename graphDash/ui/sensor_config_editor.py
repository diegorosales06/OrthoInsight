"""Shared sensor-config table editor.

Used both by the in-app Sensor Config tab and by the startup config dialog.
Emits `sensors_changed(list)` on any edit; callers decide what to do with it
(save to YAML, restart sampler, mark row status, etc.).
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QComboBox, QHeaderView, QMessageBox,
)
from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtGui import QColor, QBrush

from graphDash.ui import theme


TOOTH_TYPE_OPTIONS = ["", "central_incisor", "premolar", "molar"]
TOOTH_TYPE_DISPLAY = {
    "": "(none)",
    "central_incisor": "Central Incisor",
    "premolar": "Premolar",
    "molar": "Molar",
}


class SensorConfigEditor(QWidget):
    sensors_changed = pyqtSignal(list)

    def __init__(self, sensors, show_status_column=False, parent=None):
        super().__init__(parent)
        self.sensors = [dict(s) for s in sensors]
        self.show_status_column = show_status_column
        self._building = False
        # per-row (ok: bool | None, message: str)
        self._row_status = [(None, "") for _ in self.sensors]

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.table = QTableWidget()
        cols = ["Name", "Bus", "Dev", "CSB GPIO", "Tooth", "Tooth Type"]
        if show_status_column:
            cols.append("Status")
        self.table.setColumnCount(len(cols))
        self.table.setHorizontalHeaderLabels(cols)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, len(cols)):
            if show_status_column and col == len(cols) - 1:
                header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
            else:
                header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.cellChanged.connect(self._on_cell_changed)
        layout.addWidget(self.table)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        add_btn = QPushButton("+ Add Sensor")
        add_btn.setMinimumHeight(38)
        add_btn.setProperty("variant", "primary")
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.clicked.connect(self._add_sensor)
        btn_row.addWidget(add_btn)

        remove_btn = QPushButton("Remove Selected")
        remove_btn.setMinimumHeight(38)
        remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        remove_btn.clicked.connect(self._remove_sensor)
        btn_row.addWidget(remove_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.setLayout(layout)
        self._rebuild_table()

    # -- public API -----------------------------------------------------

    def get_sensors(self):
        self._sync_from_table()
        return [dict(s) for s in self.sensors]

    def set_sensors(self, sensors):
        self.sensors = [dict(s) for s in sensors]
        self._row_status = [(None, "") for _ in self.sensors]
        self._rebuild_table()

    def set_row_status(self, row, ok, message=""):
        """ok: True (pass) / False (fail) / None (untested)."""
        if not self.show_status_column:
            return
        if row < 0 or row >= len(self.sensors):
            return
        self._row_status[row] = (ok, message)
        self._render_status_cell(row)
        self._paint_row(row)

    def clear_row_statuses(self):
        if not self.show_status_column:
            return
        self._row_status = [(None, "") for _ in self.sensors]
        for row in range(self.table.rowCount()):
            self._render_status_cell(row)
            self._paint_row(row)

    # -- table plumbing -------------------------------------------------

    def _rebuild_table(self):
        self._building = True
        self.table.setRowCount(len(self.sensors))
        for row, sensor in enumerate(self.sensors):
            self.table.setItem(row, 0, QTableWidgetItem(
                sensor.get("name", "")))
            self.table.setItem(row, 1, QTableWidgetItem(
                str(sensor.get("bus", 0))))
            self.table.setItem(row, 2, QTableWidgetItem(
                str(sensor.get("dev", 0))))
            self.table.setItem(row, 3, QTableWidgetItem(
                str(sensor.get("csb_gpio", 0))))

            tooth = sensor.get("tooth")
            self.table.setItem(row, 4, QTableWidgetItem(
                str(tooth) if tooth is not None else ""))

            combo = QComboBox()
            for key in TOOTH_TYPE_OPTIONS:
                combo.addItem(TOOTH_TYPE_DISPLAY[key], key)
            current_type = sensor.get("tooth_type", "")
            if current_type in TOOTH_TYPE_OPTIONS:
                combo.setCurrentIndex(TOOTH_TYPE_OPTIONS.index(current_type))
            combo.currentIndexChanged.connect(
                lambda _, r=row: self._on_tooth_type_changed(r))
            self.table.setCellWidget(row, 5, combo)

            if self.show_status_column:
                self._render_status_cell(row)
                self._paint_row(row)
        self._building = False

    def _render_status_cell(self, row):
        if not self.show_status_column:
            return
        ok, msg = self._row_status[row]
        col = self.table.columnCount() - 1
        if ok is None:
            text = "— not tested —"
        elif ok:
            text = "✓ Ready"
        else:
            text = f"✗ {msg}" if msg else "✗ Error"
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        if msg:
            item.setToolTip(msg)
        self.table.setItem(row, col, item)

    def _paint_row(self, row):
        if not self.show_status_column:
            return
        ok, _ = self._row_status[row]
        if ok is None:
            bg = None
        elif ok:
            bg = QColor(theme.ACCENT_SUCCESS)
            bg.setAlpha(38)
        else:
            bg = QColor(theme.ACCENT_DANGER)
            bg.setAlpha(48)
        editable_cols = range(self.table.columnCount() - 1) if self.show_status_column else range(self.table.columnCount())
        for col in editable_cols:
            item = self.table.item(row, col)
            if item is None:
                continue
            if bg is None:
                item.setData(Qt.ItemDataRole.BackgroundRole, None)
            else:
                item.setBackground(QBrush(bg))

    def _on_cell_changed(self, row, col):
        if self._building:
            return
        self._sync_from_table()
        # An edit invalidates the last test result.
        if self.show_status_column and 0 <= row < len(self._row_status):
            self._row_status[row] = (None, "")
            self._render_status_cell(row)
            self._paint_row(row)
        self.sensors_changed.emit(self.get_sensors())

    def _on_tooth_type_changed(self, row):
        if self._building:
            return
        self._sync_from_table()
        self.sensors_changed.emit(self.get_sensors())

    def _sync_from_table(self):
        for row in range(min(self.table.rowCount(), len(self.sensors))):
            sensor = self.sensors[row]

            item = self.table.item(row, 0)
            sensor["name"] = item.text() if item else ""

            for col, key in [(1, "bus"), (2, "dev"), (3, "csb_gpio")]:
                item = self.table.item(row, col)
                try:
                    sensor[key] = int(item.text()) if item else 0
                except ValueError:
                    sensor[key] = 0

            item = self.table.item(row, 4)
            if item and item.text().strip():
                try:
                    sensor["tooth"] = int(item.text())
                except ValueError:
                    sensor.pop("tooth", None)
            else:
                sensor.pop("tooth", None)

            combo = self.table.cellWidget(row, 5)
            if combo:
                tt = combo.currentData()
                if tt:
                    sensor["tooth_type"] = tt
                else:
                    sensor.pop("tooth_type", None)

    def _add_sensor(self):
        n = len(self.sensors) + 1
        self.sensors.append({
            "name": f"Cell {n}",
            "bus": 0,
            "dev": 0,
            "csb_gpio": 0,
        })
        self._row_status.append((None, ""))
        self._rebuild_table()
        self.sensors_changed.emit(self.get_sensors())

    def _remove_sensor(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self.sensors):
            return
        name = self.sensors[row].get("name", f"Cell {row + 1}")
        reply = QMessageBox.question(
            self, "Remove Sensor",
            f"Remove sensor '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.sensors.pop(row)
            self._row_status.pop(row)
            self._rebuild_table()
            self.sensors_changed.emit(self.get_sensors())
