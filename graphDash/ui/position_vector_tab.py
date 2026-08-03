from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QDoubleSpinBox, QGridLayout, QMessageBox,
)
from PyQt6.QtGui import QFont
from PyQt6.QtCore import Qt


class PositionVectorTab(QWidget):
    def __init__(self, position_vector_config, on_apply_callback=None):
        super().__init__()
        self.position_vector_config = position_vector_config
        self.on_apply_callback = on_apply_callback

        layout = QVBoxLayout()
        layout.setContentsMargins(20, 20, 20, 20)

        # Title
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title = QLabel("Position Vector Configuration")
        title.setFont(title_font)
        layout.addWidget(title)

        # Description
        desc = QLabel("Define the position vector (r) for moment calculation: M = r × F")
        desc.setStyleSheet("color: gray; margin-bottom: 20px;")
        layout.addWidget(desc)

        # Input grid
        grid = QGridLayout()
        grid.setSpacing(15)

        self.input_fields = {}
        rx, ry, rz = position_vector_config.get_vector()

        for idx, (label_text, initial_value) in enumerate([
            ("X (m):", rx),
            ("Y (m):", ry),
            ("Z (m):", rz),
        ]):
            label = QLabel(label_text)
            label.setStyleSheet("font-weight: bold;")
            spinbox = QDoubleSpinBox()
            spinbox.setRange(-999.0, 999.0)
            spinbox.setSingleStep(0.001)
            spinbox.setDecimals(3)
            spinbox.setValue(initial_value)
            spinbox.setMinimumWidth(150)

            font = QFont()
            font.setPointSize(11)
            spinbox.setFont(font)

            grid.addWidget(label, idx, 0, alignment=Qt.AlignmentFlag.AlignRight)
            grid.addWidget(spinbox, idx, 1)
            self.input_fields[label_text[0]] = spinbox

        layout.addLayout(grid)

        # Current values display
        layout.addSpacing(20)
        current_label = QLabel("Current Position Vector:")
        current_label.setStyleSheet("font-weight: bold; color: #34495e;")
        layout.addWidget(current_label)

        self.current_values_label = QLabel(f"({rx:.3f}, {ry:.3f}, {rz:.3f})")
        self.current_values_label.setStyleSheet("color: #7f8c8d; font-size: 12px;")
        layout.addWidget(self.current_values_label)

        # Buttons
        layout.addSpacing(20)
        btn_row = QHBoxLayout()
        btn_font = QFont()
        btn_font.setPointSize(11)

        apply_btn = QPushButton("Apply Changes")
        apply_btn.setFont(btn_font)
        apply_btn.setMinimumHeight(40)
        apply_btn.setMinimumWidth(150)
        apply_btn.setStyleSheet(
            "background-color: #27ae60; color: white; "
            "border: none; border-radius: 4px; font-weight: bold;"
        )
        apply_btn.clicked.connect(self._on_apply)
        btn_row.addWidget(apply_btn)

        reset_btn = QPushButton("Reset to Default (1, 1, 1)")
        reset_btn.setFont(btn_font)
        reset_btn.setMinimumHeight(40)
        reset_btn.setMinimumWidth(180)
        reset_btn.setStyleSheet(
            "background-color: #95a5a6; color: white; "
            "border: none; border-radius: 4px; font-weight: bold;"
        )
        reset_btn.clicked.connect(self._on_reset)
        btn_row.addWidget(reset_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        layout.addStretch()
        self.setLayout(layout)

    def _on_apply(self):
        try:
            x = self.input_fields["X"].value()
            y = self.input_fields["Y"].value()
            z = self.input_fields["Z"].value()

            self.position_vector_config.set_vector(x, y, z)
            self.position_vector_config.save()

            self.current_values_label.setText(f"({x:.3f}, {y:.3f}, {z:.3f})")

            if self.on_apply_callback:
                self.on_apply_callback()

            msg = QMessageBox(self)
            msg.setWindowTitle("Success")
            msg.setText("Position vector updated successfully!")
            msg.setStyleSheet("QMessageBox { background-color: white; }")
            msg.exec()

        except Exception as e:
            msg = QMessageBox(self)
            msg.setWindowTitle("Error")
            msg.setText(f"Failed to update position vector: {str(e)}")
            msg.setStyleSheet("QMessageBox { background-color: white; }")
            msg.exec()

    def _on_reset(self):
        self.position_vector_config.reset_to_default()
        x, y, z = self.position_vector_config.get_vector()
        self.input_fields["X"].setValue(x)
        self.input_fields["Y"].setValue(y)
        self.input_fields["Z"].setValue(z)
        self.current_values_label.setText(f"({x:.3f}, {y:.3f}, {z:.3f})")

        if self.on_apply_callback:
            self.on_apply_callback()
