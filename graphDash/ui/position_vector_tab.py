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

        sub = QLabel("Edit the position vector r = [rx, ry, rz] and constant w per tooth type.")
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

        self.spin_rx = self._make_spin(-100.0, 100.0, 0.0)
        form.addRow("rx  (mm):", self.spin_rx)

        self.spin_ry = self._make_spin(-200.0, 200.0, 9.5)
        form.addRow("ry  (mm):", self.spin_ry)

        self.spin_rz = self._make_spin(-200.0, 200.0, 17.15)
        form.addRow("rz  (mm):", self.spin_rz)

        self.spin_w = self._make_spin(0.01, 200.0, 4.7)
        form.addRow("w  (mm):", self.spin_w)

        params_group.setLayout(form)
        layout.addWidget(params_group)

        # Descriptions
        desc_font = QFont()
        desc_font.setPointSize(9)
        desc_font.setItalic(True)

        for line in [
            "rx = lateral offset (default 0)",
            "ry = position vector y component",
            "rz = position vector z component (tooth height)",
            "w  = user-defined constant",
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
        for spin in (self.spin_rx, self.spin_ry, self.spin_rz, self.spin_w):
            spin.blockSignals(True)

        self.spin_rx.setValue(pv["rx"])
        self.spin_ry.setValue(pv["ry"])
        self.spin_rz.setValue(pv["rz"])
        self.spin_w.setValue(pv["w"])

        for spin in (self.spin_rx, self.spin_ry, self.spin_rz, self.spin_w):
            spin.blockSignals(False)

    def _value_changed(self):
        self.pos_vectors.set(
            self._current_type(),
            rx=self.spin_rx.value(),
            ry=self.spin_ry.value(),
            rz=self.spin_rz.value(),
            w=self.spin_w.value(),
        )

    def _reset(self):
        self.pos_vectors.reset_defaults(self._current_type())
        self._load_values()
