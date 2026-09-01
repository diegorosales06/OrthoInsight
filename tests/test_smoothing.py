"""Standalone validation harness for the upstream moving-average filter.

Run it explicitly from the repo root:

    python3 tests/test_smoothing.py

Same print-and-exit-code style as test_compensation.py -- this repo has no
pytest. MovingAverage is pure stdlib, so this needs no QApplication, no numpy
and no hardware.
"""

import os
import sys

# Allow running as a plain script from the repo root by putting the repo root --
# not tests/ -- on the import path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from graphDash.smoothing import MovingAverage, window_samples, RESYNC_EVERY

TOL = 1e-9

_results = []


def check(name, ok, detail=""):
    _results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'} | {name}" + (f"  --  {detail}" if detail else ""))


def close(a, b, tol=TOL):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def section(title):
    print("\n" + "-" * 100)
    print(title)


def six(v):
    """A 6-axis sample whose axes are distinguishable: [v, 2v, 3v, 4v, 5v, 6v]."""
    return [v * (k + 1) for k in range(6)]


# ---------------------------------------------------------------- seconds -> samples
section("Window: seconds -> sample count")

check("10 s @ 20 Hz  = 200 samples", window_samples(10, 20) == 200, f"got {window_samples(10, 20)}")
check("10 s @ 60 Hz  = 600 samples", window_samples(10, 60) == 600, f"got {window_samples(10, 60)}")
check("10 s @  1 Hz  =  10 samples", window_samples(10, 1) == 10, f"got {window_samples(10, 1)}")
check("0 s  -> 1 sample (off)", window_samples(0, 20) == 1, f"got {window_samples(0, 20)}")
check("1 s @ 100 Hz  = 100 samples", window_samples(1, 100) == 100, f"got {window_samples(1, 100)}")

ma = MovingAverage(1, window_s=10)
ma.window_s = -5
check("negative window clamps to 0", ma.window_s == 0, f"got {ma.window_s}")

# ---------------------------------------------------------------- constant input
section("Constant input is returned unchanged (the property the compensation harness relies on)")

ma = MovingAverage(1, window_s=10)
const = [-4.277, -0.038, -0.121, 0.00200, -0.05865, 0.03005]
out = None
for _ in range(500):                      # well past the 200-sample window
    out = ma.update(0, const, 20)
check("constant in -> identical out", close(out, const), f"got {[round(v, 6) for v in out]}")

check("first sample of a constant stream is already exact",
      close(MovingAverage(1, window_s=10).update(0, const, 20), const))

# ---------------------------------------------------------------- warm-up
section("Warm-up is an expanding mean (defined output from sample #1)")

ma = MovingAverage(1, window_s=10)        # 200 samples @ 20 Hz
outs = [ma.update(0, six(v), 20)[0] for v in (1.0, 2.0, 3.0, 4.0)]
expected = [1.0, 1.5, 2.0, 2.5]           # running means of 1,2,3,4
check("expanding mean over first 4 samples", close(outs, expected), f"got {outs}")

ma = MovingAverage(1, window_s=1)         # 4 samples @ 4 Hz
for v in (1.0, 2.0, 3.0, 4.0):
    ma.update(0, six(v), 4)
out = ma.update(0, six(5.0), 4)           # window full: mean(2,3,4,5)
check("full window drops the oldest sample", abs(out[0] - 3.5) <= TOL, f"got {out[0]}")

# ---------------------------------------------------------------- all axes
section("Every axis is filtered independently")

ma = MovingAverage(1, window_s=1)         # 4 samples @ 4 Hz
for v in (1.0, 2.0, 3.0, 4.0):
    out = ma.update(0, six(v), 4)
# mean of 1..4 is 2.5, so axis k should read 2.5 * (k+1)
check("all 6 axes averaged", close(out, [2.5 * (k + 1) for k in range(6)]),
      f"got {[round(v, 4) for v in out]}")

# ---------------------------------------------------------------- window shrink
section("Shrinking the window evicts immediately")

ma = MovingAverage(1, window_s=10)        # 200 @ 20 Hz
for v in range(1, 101):
    ma.update(0, six(float(v)), 20)       # holds 1..100, mean 50.5
ma.window_s = 1                           # now 20 samples @ 20 Hz
out = ma.update(0, six(101.0), 20)        # should hold 82..101, mean 91.5
check("window 10 s -> 1 s evicts on next sample", abs(out[0] - 91.5) <= TOL, f"got {out[0]}")

# ---------------------------------------------------------------- rate change
section("A live sample-rate change re-derives the sample count")

ma = MovingAverage(1, window_s=1)
for v in range(1, 41):
    ma.update(0, six(float(v)), 40)       # 40 samples @ 40 Hz, holds 1..40
check("40 Hz window holds 40 samples", window_samples(1, 40) == 40)
out = ma.update(0, six(41.0), 4)          # rate drops to 4 Hz -> window is 4
check("rate 40 -> 4 Hz shrinks window to 4", abs(out[0] - 39.5) <= TOL,
      f"expected mean(38,39,40,41)=39.5, got {out[0]}")

# ---------------------------------------------------------------- off
section("window_s = 0 is a one-sample window, i.e. the filter is off")

ma = MovingAverage(1, window_s=0)
ma.update(0, six(1.0), 20)
out = ma.update(0, six(100.0), 20)
check("filter off returns the sample it was given", abs(out[0] - 100.0) <= TOL, f"got {out[0]}")

# Turning the filter off is not a special mode -- the window is simply 1, so the
# buffer trims to the single most recent sample and re-enabling resumes from it.
ma = MovingAverage(1, window_s=1)         # 4 @ 4 Hz
for v in (1.0, 2.0, 3.0, 4.0):
    ma.update(0, six(v), 4)
ma.window_s = 0
ma.update(0, six(99.0), 4)                # window shrinks to 1: only 99 survives
ma.window_s = 1
out = ma.update(0, six(10.0), 4)          # mean(99, 10)
check("disabling trims history to the latest sample", abs(out[0] - 54.5) <= TOL, f"got {out[0]}")

# ---------------------------------------------------------------- reset
section("reset_all() flushes history")

ma = MovingAverage(2, window_s=10)
for v in range(1, 51):
    ma.update(0, six(float(v)), 20)
ma.reset_all()
out = ma.update(0, six(7.0), 20)
check("first sample after reset equals that sample", abs(out[0] - 7.0) <= TOL, f"got {out[0]}")

ma = MovingAverage(2, window_s=10)
for v in range(1, 51):
    ma.update(0, six(float(v)), 20)
    ma.update(1, six(float(v)), 20)
ma.reset_all()
check("reset_all() clears every cell",
      abs(ma.update(0, six(7.0), 20)[0] - 7.0) <= TOL
      and abs(ma.update(1, six(7.0), 20)[0] - 7.0) <= TOL)

# ---------------------------------------------------------------- per-cell isolation
section("Cells are filtered independently")

ma = MovingAverage(3, window_s=1)         # 4 @ 4 Hz
for v in (1.0, 2.0, 3.0, 4.0):
    ma.update(0, six(v), 4)
for v in (10.0, 20.0, 30.0, 40.0):
    ma.update(1, six(v), 4)
a = ma.update(0, six(5.0), 4)[0]           # mean(2,3,4,5)   = 3.5
b = ma.update(1, six(50.0), 4)[0]          # mean(20,30,40,50) = 35.0
c = ma.update(2, six(7.0), 4)[0]           # untouched cell, first sample
check("cell 0 unaffected by cell 1", abs(a - 3.5) <= TOL, f"got {a}")
check("cell 1 unaffected by cell 0", abs(b - 35.0) <= TOL, f"got {b}")
check("cell 2 has its own empty history", abs(c - 7.0) <= TOL, f"got {c}")

# ---------------------------------------------------------------- drift resync
section("Running sums stay accurate across a resync")

ma = MovingAverage(1, window_s=1)         # 20 @ 20 Hz
n = RESYNC_EVERY + 100                    # forces at least one resync
for i in range(n):
    out = ma.update(0, six(0.1), 20)
check(f"{n} samples of 0.1 still average to 0.1", abs(out[0] - 0.1) <= 1e-12,
      f"got {out[0]!r}")

# ---------------------------------------------------------------- caller's list is safe
section("update() does not retain or mutate the caller's list")

ma = MovingAverage(1, window_s=1)
buf = six(1.0)
ma.update(0, buf, 4)
buf[0] = 999.0                            # caller reuses its list
out = ma.update(0, six(1.0), 4)
check("mutating the caller's list does not corrupt history", abs(out[0] - 1.0) <= TOL,
      f"got {out[0]}")

# ---------------------------------------------------------------- pipeline order
section("Sampler pipeline: the filter runs BEFORE compute_adjusted")

# This is the whole point of the feature, and it is only observable because
# compute_adjusted() is non-linear -- it is threshold-gated at 0.3 N. Averaging
# then compensating is therefore NOT the same as compensating then averaging,
# and this pins down which one the sampler does.
from graphDash.sampler import Sampler
from graphDash.force_moment import compute_adjusted, PositionVectors

pv = PositionVectors()
sm = MovingAverage(1, window_s=1)
sampler = Sampler([], None, cell_names=["C1"], pos_vectors=pv,
                  cell_tooth_types=["central_incisor"], smoother=sm)
sampler.rate_hz = 2                        # 1 s @ 2 Hz -> a 2-sample window

trips = [0.0, 0.0, 0.5, 0.0, 0.0, 0.0]     # |Fz| = 0.5 -> over the 0.3 N threshold
quiet = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

sampler._process(0, trips)
got = sampler._process(0, quiet)

# Filter first: mean Fz = 0.25, which is UNDER the threshold, so compute_adjusted
# leaves Fz alone.
smoothed_first = compute_adjusted([0.0, 0.0, 0.25, 0.0, 0.0, 0.0], "central_incisor", pv)
check("_process == compute_adjusted(filtered input)", close(got, smoothed_first),
      f"got {[round(v, 4) for v in got]}")

# Compensate first: the 0.5 sample WOULD have tripped the branch, and averaging
# the two compensated outputs gives a different Fz entirely.
a = compute_adjusted(trips, "central_incisor", pv)
b = compute_adjusted(quiet, "central_incisor", pv)
compensated_first = [(a[k] + b[k]) / 2 for k in range(6)]
check("the two orders really do differ (so the test above has teeth)",
      not close(got, compensated_first, tol=1e-6),
      f"filter-first Fz={got[2]:.4f} vs compensate-first Fz={compensated_first[2]:.4f}")

# And a cell with no tooth_type gets filtered but not compensated.
sm2 = MovingAverage(1, window_s=1)
plain = Sampler([], None, cell_names=["C1"], cell_tooth_types=[None], smoother=sm2)
plain.rate_hz = 2
plain._process(0, six(2.0))
out = plain._process(0, six(4.0))
check("no tooth_type -> filtered, uncompensated", close(out, six(3.0)),
      f"got {[round(v, 4) for v in out]}")

# ---------------------------------------------------------------- summary
passed = sum(1 for _, ok, _ in _results if ok)
total = len(_results)
print("\n" + "=" * 100)
print(f"SUMMARY: {passed}/{total} CHECKS PASSED")
print("=" * 100)
if passed != total:
    print("\nFailures:")
    for name, ok, detail in _results:
        if not ok:
            print(f"  - {name}  {detail}")
sys.exit(0 if passed == total else 1)
