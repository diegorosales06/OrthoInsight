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

Two things are timed, and the gate is the worse of them:

* the **arch alone**, which is what `--scaling` fits a line through and what the
  bake budget trades against;
* the **whole tab** -- one `_refresh()` plus a repaint of the arch, the key bar
  and the toggle column -- which is what the timer actually drives. The key and
  the numbers used to be painted inside the view and were covered by the first
  figure for free; they are sibling widgets now, so timing only the view would
  let the gate pass while the tab misses frames.

Note for anyone comparing against numbers taken before the chrome moved out of
the view: the arch used to be clipped to `width - 204` and now gets the whole
widget, so at the same `--size` it rasterises a wider arch. Pi measurements
taken before that change are not comparable and must be redone.
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import arch_harness as harness           # sets QT_QPA_PLATFORM, fixes sys.path

from graphDash.constants import REFRESH_MS
from graphDash.ui.arch_model import LOWER_ARCH_ORDER
from graphDash.ui.arch_tab import DATA_SCALES, PANEL_SCROLL_W, PRESETS

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


def time_tab_ticks(tab, frames):
    """Milliseconds per whole-tab tick: one `_refresh()` plus one repaint.

    `_refresh` is what the timer calls -- it reads the store once, prints the
    numbers into the toggle panel and schedules the arch's paint -- so this is
    the figure `REFRESH_MS` is actually the budget for.
    """
    tab._refresh()
    tab.grab()
    out = []
    for _ in range(frames):
        t0 = time.perf_counter()
        tab._refresh()
        tab.grab()
        out.append((time.perf_counter() - t0) * 1000.0)
    return out


def tab_rows(frames, size, cases):
    """Time the whole tab for each (label, tooth list) case, both quantities."""
    rows = []
    for label, palmers in cases:
        store = harness.StubStore(
            {i: list(harness.STUB_READINGS[i % len(harness.STUB_READINGS)])
             for i in range(len(palmers))})
        tab = harness.build_tab(store=store, tooth_per_cell=palmers, size=size)
        for i, scale in enumerate(DATA_SCALES):
            tab._pick_data(i)
            ms = sorted(time_tab_ticks(tab, frames))
            rows.append((f"tab {label}/{scale.quantity}", "Oblique",
                         sum(ms) / len(ms), percentile(ms, 0.95), ms[-1]))
        tab.deleteLater()
    return rows


def scaling(frames, size):
    """Cost per triangle, and the triangle budget that fits one frame.

    Renders the arch with a growing number of teeth mapped and fits a line
    through the results. The intercept is everything that costs the same however
    much geometry there is -- the guide, the labels, the unmapped footprints, the
    grab itself -- and the slope is what the bake budget actually trades against.

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
            harness.set_all_resultant(view, resultant)
            for pi, (name, _, _) in enumerate(PRESETS):
                view.set_preset(pi)
                ms = sorted(time_frames(view, args.frames))
                mean = sum(ms) / len(ms)
                worst_mean = max(worst_mean, mean)
                rows.append((
                    f"{scale.quantity}/{'Resultant' if resultant else 'Components'}",
                    name, mean, percentile(ms, 0.95), ms[-1],
                ))

    # The whole tab, at the rig's three cells and at a fully instrumented arch.
    # The tab is wider than the view alone: it carries the toggle column beside
    # the arch and the key bar above it.
    rows += tab_rows(args.frames, (w + PANEL_SCROLL_W, h + 90),
                     (("3 teeth", ["LR7", "LR2", "LR5"]),
                      ("16 teeth", list(LOWER_ARCH_ORDER))))
    worst_mean = max([worst_mean] + [r[2] for r in rows])

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
