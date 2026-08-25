from PyQt6.QtWidgets import QWidget, QVBoxLayout, QStackedWidget
from PyQt6.QtCore import pyqtSignal

from graphDash.ui.cell_tab import CellTab


class CellsTab(QWidget):
    """Holds every load cell's graphs in a stack. There is no in-page selector —
    the tab header itself acts as the dropdown (see `_TabBarMenu` in
    dashboard.py), and `select()` is what that menu calls."""

    selection_changed = pyqtSignal(int)

    def __init__(self, n_cells, store, names=None, tare_offsets=None):
        super().__init__()
        self.store = store
        self.cell_tabs = []
        self.names = [
            names[i] if names and i < len(names) else f"Cell {i+1}"
            for i in range(n_cells)
        ]

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.stack = QStackedWidget()
        for i in range(n_cells):
            tab = CellTab(i, store, tare_offsets=tare_offsets)
            self.stack.addWidget(tab)
            self.cell_tabs.append(tab)
        layout.addWidget(self.stack, 1)

        self.setLayout(layout)

    @property
    def current_index(self):
        return self.stack.currentIndex()

    def current_name(self):
        idx = self.stack.currentIndex()
        return self.names[idx] if 0 <= idx < len(self.names) else ""

    def select(self, idx):
        if 0 <= idx < self.stack.count() and idx != self.stack.currentIndex():
            self.stack.setCurrentIndex(idx)
            self.selection_changed.emit(idx)

    def set_names(self, names):
        """Rename the cells (e.g. after a sensor config edit)."""
        for i in range(len(self.names)):
            if i < len(names):
                self.names[i] = names[i]
        self.selection_changed.emit(self.stack.currentIndex())

    def refresh(self):
        """Only the visible cell needs redrawing — the others repaint on the
        next tick after being selected."""
        current = self.stack.currentWidget()
        if current is not None:
            current.refresh()
