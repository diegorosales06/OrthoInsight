import collections
import threading

from graphDash.constants import N_AXES, DEFAULT_SMOOTH_S

# The sums are maintained incrementally -- one add and one subtract per sample --
# so the cost per sample is independent of window size. That matters: a 60 s
# window at 100 Hz is 6000 samples, and re-summing that every cycle for every
# cell is not affordable on the Pi. Incremental float arithmetic drifts, so the
# sums are rebuilt from the buffer this often to bound the error.
RESYNC_EVERY = 2048


def window_samples(window_s, rate_hz):
    """How many samples a window of `window_s` seconds spans at `rate_hz`.

    Never less than 1. window_s = 0 means "smoothing off", which is the same
    thing as a window one sample long -- so there is no separate disabled mode
    to special-case, here or in MovingAverage.update().
    """
    return max(1, int(round(window_s * rate_hz)))


class MovingAverage:
    """Causal moving average over a window measured in SECONDS.

    Sits in the sample pipeline between the tare subtraction and
    compute_adjusted(), so the filtered stream is what gets compensated,
    stored, plotted and logged. There is no second smoothing stage anywhere
    downstream -- the graph shows exactly what the CSV holds.

    The window is held in seconds and converted to a sample count per call
    using the sampler's current rate, so the rate has exactly one owner (the
    sampler) and changing it does not silently change the window's length in
    time.

    Before the buffer holds a full window the output is an expanding mean --
    the average of however many samples have arrived -- so there is a defined
    output from the very first sample and no warm-up placeholder to handle.

    window_s and reset_all() are called from the GUI thread while update() runs
    on the sampler thread, hence the lock.
    """

    def __init__(self, n_cells, n_axes=N_AXES, window_s=DEFAULT_SMOOTH_S):
        self.n_cells = n_cells
        self.n_axes = n_axes
        self._lock = threading.Lock()
        self._window_s = max(0, int(window_s))
        self._buffers = [collections.deque() for _ in range(n_cells)]
        self._sums = [[0.0] * n_axes for _ in range(n_cells)]
        self._since_resync = [0] * n_cells

    @property
    def window_s(self):
        with self._lock:
            return self._window_s

    @window_s.setter
    def window_s(self, seconds):
        with self._lock:
            self._window_s = max(0, int(seconds))

    def update(self, cell_idx, values, rate_hz):
        """Push one 6-axis sample for a cell and return the filtered result.

        `values` is copied, so the caller may reuse its list.
        """
        with self._lock:
            n = window_samples(self._window_s, rate_hz)
            buf = self._buffers[cell_idx]
            sums = self._sums[cell_idx]

            sample = list(values)
            buf.append(sample)
            for k in range(self.n_axes):
                sums[k] += sample[k]

            # Normally evicts exactly one sample. Evicts several when the window
            # just shrank -- the user turned SMOOTHING down, or raised the
            # sample rate -- which is why this is a loop and not an `if`.
            while len(buf) > n:
                old = buf.popleft()
                for k in range(self.n_axes):
                    sums[k] -= old[k]

            self._since_resync[cell_idx] += 1
            if self._since_resync[cell_idx] >= RESYNC_EVERY:
                self._resum(cell_idx)

            # len(buf), not n: during warm-up the buffer is short and dividing
            # by n would bias the output toward zero.
            count = len(buf)
            return [sums[k] / count for k in range(self.n_axes)]

    def reset_all(self):
        """Flush every cell's history.

        Called at the discontinuities where averaging across the boundary would
        smear a step into the data: starting a recording, toggling debug mode,
        and taring.
        """
        with self._lock:
            for ci in range(self.n_cells):
                self._buffers[ci].clear()
                self._sums[ci] = [0.0] * self.n_axes
                self._since_resync[ci] = 0

    def _resum(self, cell_idx):
        # Caller holds the lock.
        buf = self._buffers[cell_idx]
        self._sums[cell_idx] = [sum(s[k] for s in buf) for k in range(self.n_axes)]
        self._since_resync[cell_idx] = 0
