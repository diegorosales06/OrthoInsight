from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QLabel, QComboBox, QHeaderView, QMessageBox,
)
from PyQt6.QtGui import QFont
from PyQt6.QtCore import pyqtSignal, Qt

from graphDash.ui import theme


_TOOTH_TYPE_OPTIONS = ["", "central_incisor", "premolar", "molar"]
_TOOTH_TYPE_DISPLAY = {
    "": "(none)",
    "central_incisor": "Central Incisor",
    "premolar": "Premolar",
    "molar": "Molar",
}


class SensorConfigTab(QWidget):
    config_changed = pyqtSignal(list)

    def __init__(self, sensors, config_path):
        super().__init__()
        self.config_path = config_path
        self.sensors = [dict(s) for s in sensors]
        self._building = False

        layout = QVBoxLayout()
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(10)

        title = QLabel("Sensor Configuration")
        title.setStyleSheet(
            f"font-size: {theme.FONT_SECTION}pt; font-weight: 600; color: {theme.ON_SURFACE};")
        layout.addWidget(title)

        sub = QLabel("Edit sensor settings. Changes save to sensors.yaml immediately and stop any active recording.")
        sub.setStyleSheet(f"color: {theme.ON_SURFACE_MUTED}; font-size: {theme.FONT_BODY}pt;")
        layout.addWidget(sub)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet(f"color: {theme.ACCENT_SUCCESS}; font-weight: 600;")
        layout.addWidget(self.status_label)

        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(
            ["Name", "Bus", "Dev", "CSB GPIO", "Tooth", "Tooth Type"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, 6):
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
            for key in _TOOTH_TYPE_OPTIONS:
                combo.addItem(_TOOTH_TYPE_DISPLAY[key], key)
            current_type = sensor.get("tooth_type", "")
            if current_type in _TOOTH_TYPE_OPTIONS:
                combo.setCurrentIndex(_TOOTH_TYPE_OPTIONS.index(current_type))
            combo.currentIndexChanged.connect(
                lambda _, r=row: self._on_tooth_type_changed(r))
            self.table.setCellWidget(row, 5, combo)
        self._building = False

    def _on_cell_changed(self, row, col):
        if self._building:
            return
        self._sync_from_table()
        self._save_and_notify()

    def _on_tooth_type_changed(self, row):
        if self._building:
            return
        self._sync_from_table()
        self._save_and_notify()

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

    def _save_and_notify(self):
        from graphDash.config import save_sensor_config
        save_sensor_config(self.config_path, self.sensors)
        self.status_label.setText("Config saved. Recording stopped.")
        self.config_changed.emit(self.sensors)

    def _add_sensor(self):
        n = len(self.sensors) + 1
        self.sensors.append({
            "name": f"Cell {n}",
            "bus": 0,
            "dev": 0,
            "csb_gpio": 0,
        })
        self._rebuild_table()
        self._save_and_notify()

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
            self._rebuild_table()
            self._save_and_notify()
