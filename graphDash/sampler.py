import time
import threading

from graphDash.constants import DEFAULT_RATE_HZ, N_AXES


class Sampler(threading.Thread):
    daemon = True

    def __init__(self, cells, store, simulation_cells=None, simulate=False, csv_logger=None, cell_names=None):
        super().__init__()
        self.cells = cells
        self.simulation_cells = simulation_cells or []
        self.simulate = simulate
        self.store = store
        self.csv_logger = csv_logger
        self.cell_names = cell_names or [f"Cell {i+1}" for i in range(len(cells) or len(simulation_cells))]
        self.rate_hz = DEFAULT_RATE_HZ
        self.running = False
        self._stop = False

    def run(self):
        while not self._stop:
            if not self.running:
                time.sleep(0.05)
                continue
            t0 = time.time()
            readings = []
            source_cells = self.simulation_cells if self.simulate else self.cells
            if not source_cells and self.simulation_cells:
                source_cells = [None] * len(self.simulation_cells)
            for c in source_cells:
                if c is None:
                    readings.append([0.0] * N_AXES)
                    continue
                try:
                    readings.append(c.read_all())
                except Exception:
                    readings.append([0.0] * N_AXES)
            self.store.append(t0, readings)

            if self.csv_logger:
                for cell_idx, reading in enumerate(readings):
                    self.csv_logger.log_sample(t0, self.cell_names[cell_idx], reading)

            elapsed = time.time() - t0
            period = 1.0 / self.rate_hz
            if elapsed < period:
                time.sleep(period - elapsed)
