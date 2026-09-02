"""Render the Arch View to PNGs with no display, for eyeballing and diffing.

    QT_QPA_PLATFORM=offscreen python3 tests/render_arch.py --out /tmp/arch_before
    QT_QPA_PLATFORM=offscreen python3 tests/render_arch.py --out /tmp/arch_after
    python3 tests/render_arch.py --diff /tmp/arch_before /tmp/arch_after

Three things it produces:

* `presets_*.png`  -- one image per (data, show, camera preset) combination.
* `sweep_*.png`    -- a yaw x pitch contact sheet. This is the one that earns its
  keep: fit-to-pane and face-culling bugs hide at the extremes of the camera
  range and are invisible at the default oblique view.
* `edge_*.png`     -- the below-threshold / clamped / axial-ring readings.

`--diff` compares two directories pixel for pixel. The stub store is static, so
two runs of unchanged code diff to exactly zero -- any non-zero count is a real
change, with no noise floor to argue about.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import arch_harness as harness           # sets QT_QPA_PLATFORM, fixes sys.path

# Corners and middles of the legal camera range: pitch is clamped to 2..89.5
# degrees (Camera.PITCH_MIN/PITCH_MAX), so 5 and 88 stand in for the extremes.
SWEEP_YAW = (-90.0, -45.0, 0.0, 45.0, 90.0)
SWEEP_PITCH = (88.0, 60.0, 30.0, 5.0)


def _save(image, path):
    if not image.save(path):
        raise SystemExit(f"could not write {path}")
    return path


def render_presets(out_dir, size):
    from graphDash.ui.arch_tab import DATA_SCALES, PRESETS
    view = harness.build_view(size=size)
    written = []
    for scale in DATA_SCALES:
        for resultant in (False, True):
            view.set_scale(scale)
            view.set_resultant(resultant)
            for pi, (name, _, _) in enumerate(PRESETS):
                view.set_preset(pi)
                tag = f"{scale.quantity}_{'resultant' if resultant else 'components'}_{name}"
                written.append(_save(view.grab().toImage(),
                                     os.path.join(out_dir, f"presets_{tag.lower()}.png")))
    return written


def render_sweep(out_dir, size, tag="force_components", **kw):
    """One contact sheet: rows of pitch, columns of yaw, all in a single PNG."""
    import math
    from PyQt6.QtGui import QImage, QPainter
    from PyQt6.QtCore import QPointF

    view = harness.build_view(size=size, **kw)
    cw, ch = size
    sheet = QImage(cw * len(SWEEP_YAW), ch * len(SWEEP_PITCH),
                   QImage.Format.Format_ARGB32)
    sheet.fill(0xFFFFFFFF)
    p = QPainter(sheet)
    for r, pitch in enumerate(SWEEP_PITCH):
        for c, yaw in enumerate(SWEEP_YAW):
            view.camera.set_orientation(math.radians(yaw), math.radians(pitch))
            view.preset_index = None
            p.drawImage(QPointF(c * cw, r * ch), view.grab().toImage())
    p.end()
    return _save(sheet, os.path.join(out_dir, f"sweep_{tag}.png"))


def render_edges(out_dir, size):
    from graphDash.ui.arch_tab import DATA_SCALES
    written = []
    for scale in DATA_SCALES:
        view = harness.build_view(
            store=harness.StubStore(harness.STUB_READINGS_EDGE),
            scale=scale, size=size)
        written.append(_save(view.grab().toImage(),
                             os.path.join(out_dir, f"edge_{scale.quantity.lower()}.png")))
    return written


def diff(dir_a, dir_b):
    """Per-file differing-pixel counts between two render directories."""
    from PyQt6.QtGui import QImage
    harness.app()
    names = sorted(set(os.listdir(dir_a)) & set(os.listdir(dir_b)))
    only_a = sorted(set(os.listdir(dir_a)) - set(os.listdir(dir_b)))
    only_b = sorted(set(os.listdir(dir_b)) - set(os.listdir(dir_a)))
    if only_a or only_b:
        print(f"  (only in {dir_a}: {only_a or 'none'})")
        print(f"  (only in {dir_b}: {only_b or 'none'})")

    total = 0
    for name in names:
        if not name.endswith(".png"):
            continue
        a = QImage(os.path.join(dir_a, name))
        b = QImage(os.path.join(dir_b, name))
        if a.size() != b.size():
            print(f"  SIZE  | {name}: {a.size()} vs {b.size()}")
            total += 1
            continue
        # Bulk-compare the buffers first: identical is the expected answer, and
        # a per-pixel Python loop over half a megapixel is far too slow to run
        # across a whole directory just to confirm nothing moved.
        a = a.convertToFormat(QImage.Format.Format_ARGB32)
        b = b.convertToFormat(QImage.Format.Format_ARGB32)
        ba, bb = a.constBits().asstring(a.sizeInBytes()), b.constBits().asstring(b.sizeInBytes())
        n = 0 if ba == bb else sum(
            1 for i in range(0, len(ba), 4) if ba[i:i + 4] != bb[i:i + 4])
        total += n
        pct = 100.0 * n / max(1, a.width() * a.height())
        print(f"  {'SAME' if n == 0 else 'DIFF'}  | {name}: {n} px ({pct:.3f}%)")
    print(f"\n{'IDENTICAL' if total == 0 else f'{total} differing pixels total'}")
    return 0 if total == 0 else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", help="directory to write PNGs into")
    ap.add_argument("--diff", nargs=2, metavar=("DIR_A", "DIR_B"),
                    help="compare two previously rendered directories")
    ap.add_argument("--size", default="900x620", help="cell size, WxH")
    ap.add_argument("--sweep-size", default="460x420",
                    help="cell size inside the contact sheet, WxH")
    args = ap.parse_args()

    if args.diff:
        return diff(*args.diff)
    if not args.out:
        ap.error("one of --out or --diff is required")

    size = tuple(int(v) for v in args.size.lower().split("x"))
    sweep = tuple(int(v) for v in args.sweep_size.lower().split("x"))
    os.makedirs(args.out, exist_ok=True)

    from graphDash.ui.arch_tab import MOMENT_GLYPH
    written = render_presets(args.out, size)
    written += render_edges(args.out, size)
    written.append(render_sweep(args.out, sweep, "force_components"))
    written.append(render_sweep(args.out, sweep, "moment_resultant",
                                scale=MOMENT_GLYPH, show_resultant=True))
    for path in written:
        print(f"  wrote {path}")
    print(f"\n{len(written)} images in {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
