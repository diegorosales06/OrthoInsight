import time
import threading

from graphDash.constants import DEFAULT_RATE_HZ, N_AXES
from graphDash.force_moment import compute_adjusted


class Sampler(threading.Thread):
    daemon = True

    def __init__(self, cells, store, simulation_cells=None, simulate=False,
                 csv_logger=None, cell_names=None,
                 pos_vectors=None, cell_tooth_types=None,
                 tare_offsets=None, smoother=None):
        super().__init__()
        self.cells = cells
        self.simulation_cells = simulation_cells or []
        self.simulate = simulate
        self.store = store
        self.csv_logger = csv_logger
        self.cell_names = cell_names or [f"Cell {i+1}" for i in range(len(cells) or len(simulation_cells))]
        self.pos_vectors = pos_vectors
        self.cell_tooth_types = cell_tooth_types or []
        self.tare_offsets = tare_offsets
        self.smoother = smoother
        self.rate_hz = DEFAULT_RATE_HZ
        self.running = False
        self._stop = False
        self._last_raw_lock = threading.Lock()
        self._last_raw = {}

    def get_last_raw(self, cell_idx):
        """Most recent raw sensor reading for a cell, pre-tare, pre-compensation."""
        with self._last_raw_lock:
            r = self._last_raw.get(cell_idx)
            return list(r) if r is not None else [0.0] * N_AXES

    def get_all_last_raw(self):
        """Snapshot of every cell's most recent raw reading, pre-tare,
        pre-compensation. Taken under one lock acquisition, so all entries
        come from the same sampler cycle."""
        with self._last_raw_lock:
            return {ci: list(r) for ci, r in self._last_raw.items()}

    def _process(self, cell_idx, raw):
        """Run one cell's raw reading through the sample pipeline.

            raw -> tare -> smooth -> compensate

        Each stage is skipped when its collaborator was not supplied, so the
        result is always a 6-axis reading. What this returns is exactly what
        gets stored, plotted and logged -- nothing downstream processes it
        further.
        """
        reading = raw
        if self.tare_offsets is not None:
            offset = self.tare_offsets.get(cell_idx)
            reading = [reading[k] - offset[k] for k in range(N_AXES)]
        if self.smoother is not None:
            reading = self.smoother.update(cell_idx, reading, self.rate_hz)

        tooth_type = (self.cell_tooth_types[cell_idx]
                      if cell_idx < len(self.cell_tooth_types) else None)
        if tooth_type and self.pos_vectors:
            try:
                return compute_adjusted(reading, tooth_type, self.pos_vectors)
            except Exception:
                return reading
        return reading

    def run(self):
        while not self._stop:
            if not self.running:
                time.sleep(0.05)
                continue
            t0 = time.time()
            source_cells = self.simulation_cells if self.simulate else self.cells
            if not source_cells and self.simulation_cells:
                source_cells = [None] * len(self.simulation_cells)
            raw_readings = []
            for c in source_cells:
                if c is None:
                    raw_readings.append([0.0] * N_AXES)
                    continue
                try:
                    raw_readings.append(c.read_all())
                except Exception:
                    raw_readings.append([0.0] * N_AXES)

            with self._last_raw_lock:
                for ci, r in enumerate(raw_readings):
                    self._last_raw[ci] = list(r)

            readings = [self._process(ci, raw)
                        for ci, raw in enumerate(raw_readings)]
            self.store.append(t0, readings)

            if self.csv_logger:
                for cell_idx, reading in enumerate(readings):
                    self.csv_logger.log_sample(t0, self.cell_names[cell_idx], reading)

            elapsed = time.time() - t0
            period = 1.0 / self.rate_hz
            if elapsed < period:
                time.sleep(period - elapsed)
