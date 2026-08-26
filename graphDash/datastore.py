import threading
import collections

import numpy as np

from graphDash.constants import MAX_BUFFER_SAMPLES, N_AXES


class DataStore:
    """Thread-safe ring buffer for timestamped force data."""

    def __init__(self, n_cells):
        self.n_cells = n_cells
        self.lock = threading.Lock()
        self.clear()

    def clear(self):
        with self.lock:
            self.t = collections.deque(maxlen=MAX_BUFFER_SAMPLES)
            self.data = [
                [collections.deque(maxlen=MAX_BUFFER_SAMPLES) for _ in range(N_AXES)]
                for _ in range(self.n_cells)
            ]

    def append(self, timestamp, all_readings):
        with self.lock:
            self.t.append(timestamp)
            for ci, vals in enumerate(all_readings):
                for ai, v in enumerate(vals):
                    self.data[ci][ai].append(v)

    def latest(self, cell_idx):
        """Most recent 6-axis reading for one cell, or None if no samples yet.

        Cheap alternative to get_cell() for callers that only need the newest
        sample -- the arch view polls this once per tooth per frame, where
        get_cell()'s full-buffer numpy copy would be pure waste."""
        with self.lock:
            if len(self.t) == 0:
                return None
            return [self.data[cell_idx][ai][-1] for ai in range(N_AXES)]

    def get_cell(self, cell_idx, window_s=None):
        with self.lock:
            if len(self.t) == 0:
                empty = np.array([])
                return empty, [empty] * N_AXES
            t = np.array(self.t)
            arrs = [np.array(self.data[cell_idx][ai]) for ai in range(N_AXES)]

        if window_s is not None and len(t) > 0:
            cutoff = t[-1] - window_s
            mask = t >= cutoff
            t = t[mask]
            arrs = [a[mask] for a in arrs]

        if len(t) > 0:
            t = t - t[0]
        return t, arrs
