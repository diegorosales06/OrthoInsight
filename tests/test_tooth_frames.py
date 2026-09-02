"""Standalone validation harness for tools/tooth_frames.py.

Run it explicitly from the repo root:

    python3 tests/test_tooth_frames.py

Same print-and-exit-code style as test_smoothing.py -- this repo has no pytest.
Everything exercised here is the pure half of the tool: tuples in, tuples out.
So this needs no trimesh, no numpy, no STLs and no QApplication, and it keeps
working when the scans in assets_src/ are replaced -- which they will be, since
LL7/LL8 and LR7/LR8 are currently the same file twice.

The synthetic tooth is a box with a known tilt and yaw, sampled on a lattice.
Its frame is fed to canonical_frame in scrambled order with flipped signs,
because that is all an oriented bounding box actually gives you.
"""

import math
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "tools"))

from graphDash.ui.proj3d import vcross, vdot, vmad, vscale, vunit
from tooth_frames import (ORTHO_TOL, ToothFrame, angle_deg, box_in_frame,
                          build_frame, canonical_frame, check, crown_slab,
                          plan_angle_deg)

TOL = 1e-9

_results = []


def expect(name, ok, detail=""):
    _results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'} | {name}" + (f"  --  {detail}" if detail else ""))


def close(a, b, tol=1e-6):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def tilted_frame(tilt_deg, yaw_deg):
    """A right-handed frame with an exact tilt and an exact plan yaw.

    Built by yawing a level frame about world up and then tipping it about its
    own e_y, so e_y never leaves the horizontal plane: the plan yaw stays
    exactly `yaw_deg` and e_z tips exactly `tilt_deg`. Projecting a tilted
    vector back down would not give either angle back, which is a fixture
    subtlety, not a claim about the tool.
    """
    t, y = math.radians(tilt_deg), math.radians(yaw_deg)
    e_y = (math.sin(y), -math.cos(y), 0.0)          # buccal at yaw, level
    e_x0 = vcross(e_y, (0.0, 0.0, 1.0))
    e_z = vmad(vscale((0.0, 0.0, 1.0), math.cos(t)), e_x0, math.sin(t))
    return (vcross(e_y, e_z), e_y, e_z)


def box_points(center, frame, extents, n=9):
    """A lattice filling the box -- corners alone would not test the slab."""
    pts = []
    for i in range(n):
        for j in range(n):
            for k in range(n):
                p = center
                for axis, ext, step in zip(frame, extents, (i, j, k)):
                    p = vmad(p, axis, ext * (step / (n - 1) - 0.5))
                pts.append(p)
    return pts


def scrambled(frame):
    """The same three directions as a box hands them over: any order, any sign."""
    return (vscale(frame[1], -1.0), frame[2], vscale(frame[0], -1.0))


# ------------------------------------------------------------ canonical_frame
print("\ncanonical_frame -- roles, signs, handedness")

TILT, YAW = 22.0, 12.0
truth = tilted_frame(TILT, YAW)
buccal = vunit((0.0, -1.0, 0.0))
got = canonical_frame(scrambled(truth), buccal)

expect("e_z is the axis nearest world up, pointing up", close(got[2], truth[2]),
       f"got {[round(v, 4) for v in got[2]]}")
expect("e_y is the side facing buccal, pointing out", close(got[1], truth[1]),
       f"got {[round(v, 4) for v in got[1]]}")
expect("e_x closes the set", close(got[0], truth[0]))
expect("orthonormal", all(abs(vdot(v, v) - 1.0) < TOL for v in got) and
       max(abs(vdot(got[0], got[1])), abs(vdot(got[1], got[2])),
           abs(vdot(got[0], got[2]))) < TOL)
expect("right-handed: e_x x e_y = e_z",
       close(vcross(got[0], got[1]), got[2]))

flipped = canonical_frame(tuple(vscale(v, -1.0) for v in truth), buccal)
expect("all three box normals flipped -> same frame", close(flipped[1], truth[1]) and
       close(flipped[2], truth[2]),
       "an oriented box gives no signs; the arch and world up supply them")

expect("tilt is recovered", abs(angle_deg(got[2], (0.0, 0.0, 1.0)) - TILT) < 1e-6,
       f"{angle_deg(got[2], (0.0, 0.0, 1.0)):.3f} deg")
expect("yaw is recovered", abs(plan_angle_deg(got[1], buccal) - YAW) < 1e-6,
       f"{plan_angle_deg(got[1], buccal):.3f} deg")
expect("yaw is measured in plan: a steep tilt alone reads as no yaw",
       all(plan_angle_deg(tilted_frame(t, 0.0)[1], buccal) < 1e-9
           for t in (0.0, 20.0, 60.0, 89.0)),
       "tilt does not leak into yaw")
expect("plan_angle_deg ignores z outright",
       abs(plan_angle_deg((1.0, 0.0, 7.0), (0.0, 1.0, -3.0)) - 90.0) < 1e-9)

# A box whose cross-section is square has no defensible yaw; the tool must not
# pretend otherwise, and the >35 deg warning is what says so.
square = canonical_frame(scrambled(tilted_frame(10.0, 44.0)), buccal)
expect("near-square box yaws freely (this is the LL3/LL4 case)",
       plan_angle_deg(square[1], buccal) > 35.0,
       f"{plan_angle_deg(square[1], buccal):.1f} deg off buccal")

# ---------------------------------------------------------------- box_in_frame
print("\nbox_in_frame -- one way to measure a box, used twice")

CENTER, EXTENTS = (0.3, -0.2, 0.5), (0.24, 0.31, 0.78)
pts = box_points(CENTER, truth, EXTENTS)
c, e = box_in_frame(pts, truth)
expect("recovers the box centre", close(c, CENTER), f"got {[round(v, 4) for v in c]}")
expect("recovers the extents in frame order", close(e, EXTENTS),
       f"got {[round(v, 4) for v in e]}")

rotated_pts = box_points(CENTER, tilted_frame(40.0, 30.0), EXTENTS)
_, e2 = box_in_frame(rotated_pts, truth)
expect("a box measured in the wrong frame reads larger",
       all(x > y - TOL for x, y in zip(e2, EXTENTS)) and any(x > y + 1e-3 for x, y in zip(e2, EXTENTS)),
       "which is why the frame has to be the box's own axes")

# ------------------------------------------------------------------ crown_slab
print("\ncrown_slab -- the cut follows the tooth, not the world")

for frac in (0.3, 0.45, 0.6):
    slab, cut = crown_slab(pts, truth[2], frac)
    ctr, _ = box_in_frame(slab, truth)
    along = vdot(ctr, truth[2]) - vdot(CENTER, truth[2])
    expect(f"crown centre sits above the box centre at frac {frac}",
           along > 0 and along < EXTENTS[2] / 2,
           f"{along:+.4f} along e_z, box half-height {EXTENTS[2] / 2:.4f}")

heights = []
for frac in (0.2, 0.4, 0.6, 0.8, 1.0):
    slab, _ = crown_slab(pts, truth[2], frac)
    ctr, _ = box_in_frame(slab, truth)
    heights.append(vdot(ctr, truth[2]))
expect("a bigger crown fraction moves the centre down the tooth, monotonically",
       all(a > b for a, b in zip(heights, heights[1:])),
       f"{[round(h, 4) for h in heights]}")
expect("frac 1.0 centres on the box centre",
       abs(heights[-1] - vdot(CENTER, truth[2])) < 1e-9)

slab_all, _ = crown_slab(pts, truth[2], 1.0)
expect("frac 1.0 is the whole tooth", len(slab_all) == len(pts))

# ----------------------------------------------------------------- build_frame
print("\nbuild_frame -- the whole pure pipeline on a synthetic tooth")

tf = build_frame("LR6", pts, buccal, scrambled(truth), 0.45)
expect("frame matches canonical_frame", close(tf.frame[2], truth[2]))
expect("whole-tooth extents are the box's", close(tf.extents, EXTENTS))
expect("box centre is the box's", close(tf.box_center, CENTER))
expect("crown centre is offset up e_z from the box centre",
       vdot(tf.center, truth[2]) > vdot(tf.box_center, truth[2]))
expect("tilt and yaw are reported", abs(tf.tilt_deg - TILT) < 1e-6 and
       abs(tf.yaw_deg - YAW) < 1e-6, f"{tf.tilt_deg:.2f} / {tf.yaw_deg:.2f} deg")
expect("ttype comes from the dental table", tf.ttype == "molar")

# ----------------------------------------------------------------------- check
print("\ncheck -- the gate that now runs on every path, JSON included")


def one(palmer, center=(-1.0, 1.0, 0.5), yaw=-90.0, tilt=10.0, **over):
    """A healthy ToothFrame, with fields overridden to break it.

    `yaw` places e_y in world space: -90 points it at -x, +90 at +x, 0 at -y.
    The default puts a tooth on the right of the arch with e_y facing out of
    it, so the fixture satisfies the buccal check the way a real tooth does.
    `yaw_deg` is a different number -- the reported deviation from the arch's
    buccal direction -- so it is a plain field, overridden when a test wants
    the ambiguity warning.
    """
    frame = tilted_frame(tilt, yaw)
    base = dict(palmer=palmer, ttype="molar", center=center, frame=frame,
                extents=(0.3, 0.3, 0.8),
                box_center=vmad(center, frame[2], -0.1),
                cut=0.5, tilt_deg=tilt, yaw_deg=5.0)
    base.update(over)
    return ToothFrame(**base)


# Three teeth around a shallow arch, each e_y pointing away from its middle.
healthy = {"LR6": one("LR6"),
           "LL6": one("LL6", center=(1.0, 1.0, 0.5), yaw=90.0),
           "LR1": one("LR1", center=(0.0, -0.5, 0.5), yaw=0.0, tilt=8.0)}
w, f = check(healthy)
expect("a healthy set is silent", not w and not f, f"warnings {w} failures {f}")

_, f = check({**healthy, "LR6": one("LR6", frame=tuple(
    vscale(v, -1.0) if i == 0 else v
    for i, v in enumerate(tilted_frame(10.0, -90.0))))})
expect("a left-handed frame is a hard failure", any("left-handed" in x for x in f), str(f))

_, f = check({**healthy, "LR6": one("LR6", frame=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0),
                                                  (0.1, 0.0, 1.0)))})
expect("a non-orthonormal frame is a hard failure",
       any("orthonormal" in x for x in f), str(f))

_, f = check({**healthy, "LR6": one("LR6", box_center=(-1.0, 1.0, -2.0))})
expect("a crown centre outside the tooth's box is a hard failure",
       any("outside" in x for x in f), str(f))

w, _ = check({**healthy, "LR6": one("LR6", tilt_deg=47.0)})
expect("an implausible tilt warns", any("off vertical" in x for x in w), str(w))

w, _ = check({**healthy, "LR6": one("LR6", yaw_deg=44.0)})
expect("an ambiguous yaw warns", any("buccal direction" in x for x in w), str(w))

w, _ = check({**healthy, "LR6": one("LR6", extents=(0.9, 0.3, 0.4))})
expect("occlusal extent not being the largest warns",
       any("not the" in x for x in w), str(w))

w, _ = check({**healthy, "LR6": one("LR6", yaw=90.0)})
expect("an inward-facing e_y warns", any("into the arch" in x for x in w), str(w))

twin = dict(center=(0.9, 1.2, 0.5), yaw=90.0, tilt=12.0)
w, _ = check({**healthy, "LL7": one("LL7", **twin), "LL8": one("LL8", **twin)})
expect("two teeth with identical frames warn",
       any("identical frames" in x for x in w), str(w))

# -------------------------------------------------------------------- summary
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
