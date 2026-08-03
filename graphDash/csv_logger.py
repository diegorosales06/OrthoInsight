import csv
import os
import time
import threading
from datetime import datetime
from queue import Queue

from graphDash import session_manager
from graphDash.paths import logs_dir, ensure_dirs


class CSVLogger(threading.Thread):
    """Long-lived logger thread. A CSV file is opened on `start_recording()`
    and closed on `stop_recording()`; `log_sample()` is a no-op between
    recordings."""

    daemon = True

    def __init__(self):
        super().__init__()
        self.queue = Queue()
        self._stop = False
        self._lock = threading.Lock()

        # Per-recording state (guarded by _lock)
        self.recording = False
        self.file_handle = None
        self.csv_writer = None
        self.session_id = None
        self.current_file = None
        self.start_time = None

        # Manual logging state (guarded by _lock)
        self.manual_file_handle = None
        self.manual_csv_writer = None
        self.manual_session_id = None
        self.manual_file = None

        self.buffer = []
        self.buffer_size = 500

    # ---- public API called from Sampler / UI ----

    def start_recording(self) -> str:
        """Open a new CSV file, register a session, return the file path."""
        with self._lock:
            if self.recording:
                return self.current_file
            ensure_dirs()
            timestamp_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            log_file = str(logs_dir() / f"log_{timestamp_str}.csv")
            manual_log_file = str(logs_dir() / f"manual_log_{timestamp_str}.csv")
            try:
                # Auto log
                self.file_handle = open(log_file, 'w', newline='', buffering=1)
                self.csv_writer = csv.writer(self.file_handle)
                self.csv_writer.writerow(
                    ['timestamp', 'cell_id', 'Fx', 'Fy', 'Fz', 'Mx', 'My', 'Mz'])
                self.current_file = log_file

                # Manual log
                self.manual_file_handle = open(manual_log_file, 'w', newline='', buffering=1)
                self.manual_csv_writer = csv.writer(self.manual_file_handle)
                self.manual_csv_writer.writerow(
                    ['timestamp', 'cell_id', 'Fx', 'Fy', 'Fz', 'Mx', 'My', 'Mz'])
                self.manual_file = manual_log_file

                # Register single session with both file paths
                self.session_id = session_manager.start_session(log_file, manual_log_file)

                self.start_time = time.time()
                self.recording = True
                return log_file
            except Exception as e:
                print(f"Error starting CSV recording: {e}")
                self._reset_recording_state_locked()
                return ""

    def stop_recording(self) -> None:
        """Flush, close the current file, end the session, prune old ones."""
        with self._lock:
            if not self.recording:
                return
            session_id = self.session_id

        self._flush()

        with self._lock:
            self._close_file_locked()
            self._close_manual_file_locked()
            self._reset_recording_state_locked()

        if session_id is not None:
            try:
                session_manager.end_session(session_id)
                session_manager.prune_old_sessions()
            except Exception as e:
                print(f"Error finalizing session {session_id}: {e}")

    def log_sample(self, timestamp_absolute, cell_id, force_moment_values):
        with self._lock:
            if not self.recording or self.start_time is None:
                return
            relative_time = timestamp_absolute - self.start_time
        self.queue.put((relative_time, cell_id, force_moment_values))

    def log_manual_point(self, timestamp_absolute, cell_id, force_moment_values):
        """Log a single manual point immediately."""
        with self._lock:
            if not self.recording or self.manual_csv_writer is None:
                return
            relative_time = timestamp_absolute - self.start_time
            try:
                row = [f"{relative_time:.6f}", cell_id] + [f"{v:.6f}" for v in force_moment_values]
                self.manual_csv_writer.writerow(row)
                self.manual_file_handle.flush()
            except Exception as e:
                print(f"Error writing manual point to CSV: {e}")

    def stop(self):
        self._stop = True

    # ---- thread body ----

    def run(self):
        try:
            while not self._stop:
                try:
                    item = self.queue.get(timeout=0.1)
                    if item is None:
                        break
                    self.buffer.append(item)
                    if len(self.buffer) >= self.buffer_size:
                        self._flush()
                except Exception:
                    if len(self.buffer) > 0:
                        self._flush()
            self._flush()
        finally:
            with self._lock:
                self._close_file_locked()
                self._close_manual_file_locked()
                self._reset_recording_state_locked()

    # ---- internals ----

    def _flush(self):
        with self._lock:
            if not self.csv_writer or not self.buffer:
                self.buffer = []
                return
            try:
                for relative_time, cell_id, values in self.buffer:
                    row = [f"{relative_time:.6f}", cell_id] + [f"{v:.6f}" for v in values]
                    self.csv_writer.writerow(row)
                self.file_handle.flush()
            except Exception as e:
                print(f"Error writing to CSV: {e}")
            finally:
                self.buffer = []

    def _close_file_locked(self):
        if self.file_handle:
            try:
                self.file_handle.close()
            except Exception as e:
                print(f"Error closing CSV file: {e}")
        self.file_handle = None
        self.csv_writer = None

    def _close_manual_file_locked(self):
        if self.manual_file_handle:
            try:
                self.manual_file_handle.close()
            except Exception as e:
                print(f"Error closing manual CSV file: {e}")
        self.manual_file_handle = None
        self.manual_csv_writer = None

    def _reset_recording_state_locked(self):
        self.recording = False
        self.session_id = None
        self.current_file = None
        self.manual_session_id = None
        self.manual_file = None
        self.start_time = None
