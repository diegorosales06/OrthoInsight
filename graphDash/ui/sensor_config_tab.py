from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PyQt6.QtCore import pyqtSignal

from graphDash.ui import theme
from graphDash.ui.sensor_config_editor import SensorConfigEditor


class SensorConfigTab(QWidget):
    config_changed = pyqtSignal(list)

    def __init__(self, sensors, config_path):
        super().__init__()
        self.config_path = config_path

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

        self.editor = SensorConfigEditor(sensors, show_status_column=False)
        self.editor.sensors_changed.connect(self._on_sensors_changed)
        layout.addWidget(self.editor)

        self.setLayout(layout)

    @property
    def sensors(self):
        return self.editor.get_sensors()

    def _on_sensors_changed(self, sensors):
        from graphDash.config import save_sensor_config
        save_sensor_config(self.config_path, sensors)
        self.status_label.setText("Config saved. Recording stopped.")
        self.status_label.setStyleSheet(f"color: {theme.ACCENT_SUCCESS}; font-weight: 600;")
        self.config_changed.emit(sensors)
