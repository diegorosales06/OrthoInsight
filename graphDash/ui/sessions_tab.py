import csv
import os

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
)
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QFont

from graphDash import session_manager

PAGE_SIZE = 20
REFRESH_MS = 2000
CSV_PREVIEW_ROWS = 500


class SessionsTab(QWidget):
    def __init__(self):
        super().__init__()
        self.page = 0
        self.selected_file = None
        self.manual_file = None
        self.viewer_visible = False
        self.viewing_manual = False
        self._last_signature = None

        layout = QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)

        # ---- Sessions table ----
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["ID", "Start", "End", "File"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.cellClicked.connect(self._on_cell_clicked)
        layout.addWidget(self.table, 2)

        # ---- Pagination controls ----
        ctrl_font = QFont(); ctrl_font.setPointSize(10)
        pag_row = QHBoxLayout()
        self.prev_btn = QPushButton("Prev")
        self.prev_btn.setFont(ctrl_font)
        self.prev_btn.clicked.connect(self._prev_page)
        self.next_btn = QPushButton("Next")
        self.next_btn.setFont(ctrl_font)
        self.next_btn.clicked.connect(self._next_page)
        self.page_label = QLabel("Page 1 of 1")
        self.page_label.setFont(ctrl_font)
        self.count_label = QLabel("0 sessions")
        self.count_label.setFont(ctrl_font)
        self.count_label.setStyleSheet("color: gray;")
        pag_row.addWidget(self.prev_btn)
        pag_row.addWidget(self.page_label)
        pag_row.addWidget(self.next_btn)
        pag_row.addStretch()
        pag_row.addWidget(self.count_label)
        layout.addLayout(pag_row)

        # ---- CSV viewer header ----
        viewer_hdr = QHBoxLayout()
        self.viewer_title = QLabel("No file selected")
        self.viewer_title.setFont(ctrl_font)
        self.viewer_title.setStyleSheet("color: gray;")
        self.toggle_log_btn = QPushButton("Show Manual Log")
        self.toggle_log_btn.setFont(ctrl_font)
        self.toggle_log_btn.clicked.connect(self._toggle_log_view)
        self.toggle_log_btn.setVisible(False)
        self.hide_btn = QPushButton("Hide")
        self.hide_btn.setFont(ctrl_font)
        self.hide_btn.clicked.connect(self._hide_viewer)
        self.hide_btn.setVisible(False)
        viewer_hdr.addWidget(self.viewer_title, 1)
        viewer_hdr.addWidget(self.toggle_log_btn)
        viewer_hdr.addWidget(self.hide_btn)
        layout.addLayout(viewer_hdr)

        # ---- Viewer status (missing file / in-progress note) ----
        self.viewer_status = QLabel("")
        self.viewer_status.setStyleSheet("color: #b58900;")
        self.viewer_status.setVisible(False)
        layout.addWidget(self.viewer_status)

        # ---- CSV viewer table ----
        self.viewer = QTableWidget(0, 0)
        self.viewer.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.viewer.verticalHeader().setVisible(False)
        self.viewer.setVisible(False)
        layout.addWidget(self.viewer, 3)

        self.setLayout(layout)

        # ---- Auto-refresh ----
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(REFRESH_MS)
        self.refresh()

    # ---- Data refresh ----

    def refresh(self):
        total = session_manager.count_sessions()
        n_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        if self.page >= n_pages:
            self.page = n_pages - 1
        offset = self.page * PAGE_SIZE
        rows = session_manager.get_sessions(limit=PAGE_SIZE, offset=offset)

        signature = (total, self.page, tuple(
            (r["session_id"], r["end_time"]) for r in rows))
        if signature != self._last_signature:
            self._populate_table(rows)
            self._last_signature = signature

        self.page_label.setText(f"Page {self.page + 1} of {n_pages}")
        self.count_label.setText(f"{total} session{'s' if total != 1 else ''}")
        self.prev_btn.setEnabled(self.page > 0)
        self.next_btn.setEnabled(self.page < n_pages - 1)

        if self.viewer_visible:
            current_file = self.manual_file if self.viewing_manual else self.selected_file
            if current_file and not os.path.exists(current_file):
                self._show_missing_file()

    def _populate_table(self, rows):
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            id_item = QTableWidgetItem(str(r["session_id"]))
            id_item.setData(Qt.ItemDataRole.UserRole, r["file_path"])
            self.table.setItem(i, 0, id_item)

            self.table.setItem(i, 1, QTableWidgetItem(r["start_time"] or ""))

            end = r["end_time"] if r["end_time"] else "— recording —"
            end_item = QTableWidgetItem(end)
            if not r["end_time"]:
                end_item.setForeground(Qt.GlobalColor.red)
            self.table.setItem(i, 2, end_item)

            # Display both auto-log and manual-log in one cell
            auto_name = os.path.basename(r["file_path"])
            manual_name = os.path.basename(r["manual_file_path"]) if r["manual_file_path"] else None

            if manual_name:
                display_text = f"{auto_name}\n{manual_name}"
            else:
                display_text = auto_name

            path_item = QTableWidgetItem(display_text)
            path_item.setForeground(Qt.GlobalColor.blue)
            path_item.setToolTip("Click to view CSV")
            path_item.setData(Qt.ItemDataRole.UserRole, r["file_path"])
            path_item.setData(Qt.ItemDataRole.UserRole + 1, r["end_time"])
            path_item.setData(Qt.ItemDataRole.UserRole + 2, r["manual_file_path"])
            self.table.setItem(i, 3, path_item)

    # ---- Interaction ----

    def _prev_page(self):
        if self.page > 0:
            self.page -= 1
            self._last_signature = None
            self.refresh()

    def _next_page(self):
        self.page += 1
        self._last_signature = None
        self.refresh()

    def _on_cell_clicked(self, row, col):
        if col != 3:
            return
        item = self.table.item(row, col)
        if not item:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        end_time = item.data(Qt.ItemDataRole.UserRole + 1)
        manual_path = item.data(Qt.ItemDataRole.UserRole + 2)
        self.selected_file = path
        self.manual_file = manual_path
        self.viewing_manual = False
        self._show_csv(path, in_progress=(end_time is None))

    def _show_csv(self, path, in_progress=False):
        self.viewer_visible = True
        self.viewer_title.setText(f"Selected: {os.path.basename(path)}")
        self.viewer_title.setStyleSheet("color: black;")
        self.hide_btn.setVisible(True)

        # Show toggle button only if manual log exists
        if self.manual_file:
            self.toggle_log_btn.setVisible(True)
            if self.viewing_manual:
                self.toggle_log_btn.setText("Show Auto Log")
            else:
                self.toggle_log_btn.setText("Show Manual Log")
        else:
            self.toggle_log_btn.setVisible(False)

        if not os.path.exists(path):
            self._show_missing_file()
            return

        try:
            with open(path, 'r', newline='') as f:
                reader = csv.reader(f)
                rows = []
                header = next(reader, None) or []
                for i, row in enumerate(reader):
                    if i >= CSV_PREVIEW_ROWS:
                        break
                    rows.append(row)
        except Exception as e:
            self.viewer.setVisible(False)
            self.viewer_status.setText(f"Error reading file: {e}")
            self.viewer_status.setVisible(True)
            return

        self.viewer.clear()
        self.viewer.setColumnCount(len(header))
        self.viewer.setHorizontalHeaderLabels(header)
        self.viewer.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                self.viewer.setItem(r, c, QTableWidgetItem(val))
        self.viewer.resizeColumnsToContents()
        self.viewer.setVisible(True)

        if in_progress:
            self.viewer_status.setText(
                f"Recording in progress — showing first {len(rows)} rows written so far.")
            self.viewer_status.setVisible(True)
        elif len(rows) >= CSV_PREVIEW_ROWS:
            self.viewer_status.setText(
                f"Showing first {CSV_PREVIEW_ROWS} rows of file.")
            self.viewer_status.setVisible(True)
        else:
            self.viewer_status.setVisible(False)

    def _show_missing_file(self):
        self.viewer.setVisible(False)
        self.viewer_status.setText(
            "File not found on disk (stale DB record or manually deleted).")
        self.viewer_status.setVisible(True)

    def _toggle_log_view(self):
        if self.viewing_manual and self.selected_file:
            self.viewing_manual = False
            self._show_csv(self.selected_file)
        elif not self.viewing_manual and self.manual_file:
            self.viewing_manual = True
            self._show_csv(self.manual_file)

    def _hide_viewer(self):
        self.viewer_visible = False
        self.selected_file = None
        self.manual_file = None
        self.viewing_manual = False
        self.viewer.setVisible(False)
        self.viewer_status.setVisible(False)
        self.hide_btn.setVisible(False)
        self.toggle_log_btn.setVisible(False)
        self.viewer_title.setText("No file selected")
        self.viewer_title.setStyleSheet("color: gray;")
