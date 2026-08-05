from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QComboBox, QDoubleSpinBox, QPushButton, QGroupBox,
)
from PyQt6.QtGui import QFont
from PyQt6.QtCore import Qt

from graphDash.force_moment import TOOTH_TYPES, DEFAULTS
from graphDash.ui import theme


_DISPLAY_NAMES = {
    "central_incisor": "Central Incisor",
    "premolar": "Premolar",
    "molar": "Molar",
}


class PositionVectorTab(QWidget):
    def __init__(self, pos_vectors):
        super().__init__()
        self.pos_vectors = pos_vectors

        layout = QVBoxLayout()
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(10)

        title = QLabel("Position Vector Configuration")
        title.setStyleSheet(
            f"font-size: {theme.FONT_SECTION}pt; font-weight: 600; color: {theme.ON_SURFACE};")
        layout.addWidget(title)

        sub = QLabel("Edit the position vector [x, d+w, h] and sensor radius (d) per tooth type.")
        sub.setStyleSheet(f"color: {theme.ON_SURFACE_MUTED}; font-size: {theme.FONT_BODY}pt;")
        layout.addWidget(sub)
        layout.addSpacing(6)

        # Tooth type selector
        selector_row = QHBoxLayout()
        selector_label = QLabel("Tooth Type:")
        selector_label.setStyleSheet(f"font-size: {theme.FONT_CONTROL}pt; color: {theme.ON_SURFACE};")
        selector_row.addWidget(selector_label)

        self.type_combo = QComboBox()
        self.type_combo.setMinimumWidth(180)
        for tt in TOOTH_TYPES:
            self.type_combo.addItem(_DISPLAY_NAMES[tt], tt)
        self.type_combo.currentIndexChanged.connect(self._type_changed)
        selector_row.addWidget(self.type_combo)
        selector_row.addStretch()
        layout.addLayout(selector_row)
        layout.addSpacing(10)

        # Parameter inputs
        params_group = QGroupBox("Parameters")
        form = QFormLayout()
        form.setSpacing(12)
        form.setContentsMargins(6, 6, 6, 6)

        self.spin_x = self._make_spin(-100.0, 100.0, 0.0)
        form.addRow("x  (mm):", self.spin_x)

        self.spin_dw = self._make_spin(0.01, 200.0, 9.5)
        form.addRow("d + w  (mm):", self.spin_dw)

        self.spin_h = self._make_spin(0.01, 200.0, 17.15)
        form.addRow("h  (mm):", self.spin_h)

        self.spin_d = self._make_spin(0.01, 200.0, 4.8)
        form.addRow("d  (mm):", self.spin_d)

        params_group.setLayout(form)
        layout.addWidget(params_group)

        # Descriptions
        desc_font = QFont()
        desc_font.setPointSize(9)
        desc_font.setItalic(True)

        for line in [
            "x = lateral offset (default 0)",
            "d + w = sensor radius + sensor-end to shaft center",
            "h = height of tooth from top of sensor",
            "d = radius of force sensor",
        ]:
            lbl = QLabel(line)
            lbl.setFont(desc_font)
            lbl.setStyleSheet(f"color: {theme.ON_SURFACE_MUTED};")
            layout.addWidget(lbl)

        layout.addSpacing(15)

        # Reset button
        self.reset_btn = QPushButton("Reset to Defaults")
        self.reset_btn.setMinimumHeight(38)
        self.reset_btn.setMaximumWidth(200)
        self.reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reset_btn.clicked.connect(self._reset)
        layout.addWidget(self.reset_btn)

        layout.addStretch()
        self.setLayout(layout)

        self._load_values()

    def _make_spin(self, lo, hi, default):
        spin = QDoubleSpinBox()
        spin.setRange(lo, hi)
        spin.setDecimals(2)
        spin.setSingleStep(0.1)
        spin.setValue(default)
        spin.setMinimumWidth(140)
        spin.valueChanged.connect(self._value_changed)
        return spin

    def _current_type(self):
        return self.type_combo.currentData()

    def _type_changed(self):
        self._load_values()

    def _load_values(self):
        pv = self.pos_vectors.get(self._current_type())
        self.spin_x.blockSignals(True)
        self.spin_dw.blockSignals(True)
        self.spin_h.blockSignals(True)
        self.spin_d.blockSignals(True)

        self.spin_x.setValue(pv["x"])
        self.spin_dw.setValue(pv["d_plus_w"])
        self.spin_h.setValue(pv["h"])
        self.spin_d.setValue(pv["d"])

        self.spin_x.blockSignals(False)
        self.spin_dw.blockSignals(False)
        self.spin_h.blockSignals(False)
        self.spin_d.blockSignals(False)

    def _value_changed(self):
        self.pos_vectors.set(
            self._current_type(),
            x=self.spin_x.value(),
            d_plus_w=self.spin_dw.value(),
            h=self.spin_h.value(),
            d=self.spin_d.value(),
        )

    def _reset(self):
        self.pos_vectors.reset_defaults(self._current_type())
        self._load_values()
