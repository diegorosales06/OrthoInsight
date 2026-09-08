"""Frame-time gate for the Arch View, runnable with no display.

    QT_QPA_PLATFORM=offscreen python3 tests/bench_arch.py
    QT_QPA_PLATFORM=offscreen python3 tests/bench_arch.py --frames 60 --size 900x620

Same print-and-exit-code style as the other harnesses. **Run it on the Pi 4** --
that is the machine the budget has to fit, and a dev laptop is roughly an order
of magnitude faster, so laptop numbers prove nothing about the deployment.

Exits non-zero when the mean frame time exceeds `REFRESH_MS`, the interval
`ArchTab`'s timer repaints at: past that the view cannot keep its own schedule.
The p95 matters as much as the mean -- a sort that occasionally goes quadratic
shows up there first.
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import arch_harness as harness           # sets QT_QPA_PLATFORM, fixes sys.path

from graphDash.constants import REFRESH_MS
from graphDash.ui.arch_model import LOWER_ARCH_ORDER
from graphDash.ui.arch_tab import DATA_SCALES, PRESETS

# How many teeth to map when measuring the slope. Real rigs instrument a few
# teeth, but the cost per triangle is what sets the bake budget, so the sweep
# runs up to a fully instrumented arch to get a long enough lever arm.
SCALING_STEPS = (0, 1, 3, 8, 16)


def percentile(sorted_ms, q):
    """Nearest-rank percentile -- no numpy in this repo's runtime."""
    if not sorted_ms:
        return 0.0
    k = max(0, min(len(sorted_ms) - 1, int(round(q * (len(sorted_ms) - 1)))))
    return sorted_ms[k]


def time_frames(view, frames):
    """Milliseconds per `grab()`, discarding a warm-up frame.

    The first grab pays for font loading and the backing store allocation, which
    would otherwise dominate a short run and make the mean meaningless.
    """
    view.grab()
    out = []
    for _ in range(frames):
        t0 = time.perf_counter()
        view.grab()
        out.append((time.perf_counter() - t0) * 1000.0)
    return out


def scaling(frames, size):
    """Cost per triangle, and the triangle budget that fits one frame.

    Renders the arch with a growing number of teeth mapped and fits a line
    through the results. The intercept is everything that costs the same however
    much geometry there is -- key column, labels, footprints, the grab itself --
    and the slope is what the bake budget actually trades against.

    Run this on the Pi 4 and hand the reported budget to
    `tools/bake_arch_mesh.py --budget`.
    """
    rows = []
    print("=" * 100)
    print(f"Arch View scaling  --  {size[0]}x{size[1]}, {frames} frames/step")
    print("=" * 100)
    print(f"\n{'mapped teeth':>13} {'polygons':>10} {'mean':>10}")
    print("-" * 100)
    for k in SCALING_STEPS:
        vals = [1.4, -0.8, 2.1, 22.0, -48.0, 9.0]
        store = harness.StubStore({i: vals for i in range(k)}, n_cells=max(k, 1))
        view = harness.build_view(
            store=store, size=size,
            tooth_to_cell={t: i for i, t in enumerate(LOWER_ARCH_ORDER[:k])})
        polys = harness.scene_polygons(view)
        ms = time_frames(view, frames)
        mean = sum(ms) / len(ms)
        rows.append((polys, mean))
        print(f"{k:13d} {polys:10d} {mean:8.2f}ms")

    (p0, m0), (p1, m1) = rows[0], rows[-1]
    slope = (m1 - m0) / max(1, p1 - p0)
    print("\n" + "-" * 100)
    print(f"fixed overhead:  {m0:.2f} ms   (chrome, labels, footprints, grab)")
    print(f"marginal cost:   {slope * 1000:.2f} us per triangle")

    room = int((REFRESH_MS - m0) / slope) if slope > 0 else 0
    print(f"\nfits in one {REFRESH_MS} ms frame on THIS machine: ~{room} triangles")
    if room > 0:
        for mapped in (3, 8, 16):
            print(f"    {mapped:2d} teeth mapped -> ~{room // mapped} triangles/tooth")
    print("\nNumbers from a dev laptop do not transfer -- re-run this on the Pi 4")
    print("and take the budget from there.")
    print("=" * 100)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--frames", type=int, default=40,
                    help="timed frames per case (default 40)")
    ap.add_argument("--size", default="900x620",
                    help="widget size, WxH (default 900x620)")
    ap.add_argument("--scaling", action="store_true",
                    help="measure cost per triangle and report the bake budget")
    args = ap.parse_args()
    w, h = (int(v) for v in args.size.lower().split("x"))

    if args.scaling:
        return scaling(args.frames, (w, h))

    view = harness.build_view(size=(w, h))
    polys = harness.scene_polygons(view)

    print("=" * 100)
    print(f"Arch View frame-time gate  --  {w}x{h}, {args.frames} frames/case")
    print(f"scene polygons (pre-cull): {polys}   "
          f"budget: {REFRESH_MS} ms/frame ({1000 // REFRESH_MS} Hz)")
    print("=" * 100)

    worst_mean = 0.0
    rows = []
    for scale in DATA_SCALES:
        # Only force has a resultant; moment is timed as components only.
        for resultant in ((False, True) if scale.resultant is not None
                          else (False,)):
            view.set_scale(scale)
            view.set_resultant(resultant)
            for pi, (name, _, _) in enumerate(PRESETS):
                view.set_preset(pi)
                ms = sorted(time_frames(view, args.frames))
                mean = sum(ms) / len(ms)
                worst_mean = max(worst_mean, mean)
                rows.append((
                    f"{scale.quantity}/{'Resultant' if resultant else 'Components'}",
                    name, mean, percentile(ms, 0.95), ms[-1],
                ))

    print(f"\n{'case':28} {'view':10} {'mean':>9} {'p95':>9} {'max':>9}")
    print("-" * 100)
    for case, name, mean, p95, mx in rows:
        flag = "" if mean <= REFRESH_MS else "   <-- OVER BUDGET"
        print(f"{case:28} {name:10} {mean:7.2f}ms {p95:7.2f}ms {mx:7.2f}ms{flag}")

    ok = worst_mean <= REFRESH_MS
    print("\n" + "=" * 100)
    print(f"SUMMARY: worst-case mean {worst_mean:.2f} ms vs {REFRESH_MS} ms budget"
          f"  --  {'PASS' if ok else 'FAIL'}")
    print("=" * 100)
    if not ok:
        print("\nFallbacks, in order: lower the per-tooth triangle budget and re-bake;")
        print("drop antialiasing on the crown fills; cache crowns to a QPixmap keyed on")
        print("camera pose; drop the Arch View refresh to 10 Hz.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
