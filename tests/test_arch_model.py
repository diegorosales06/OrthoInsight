"""Standalone validation harness for the baked arch mesh.

Run it explicitly from the repo root:

    python3 tests/test_arch_model.py

Same print-and-exit-code style as the other harnesses. `build_arch()` only reads
a file, so this needs no QApplication, no numpy and no hardware -- which is the
point: the arch's geometry is checkable without ever opening a window.

These are the invariants the renderer silently assumes. A frame that is not
right-handed points Fx the wrong way; a normal that faces inward makes the
back-face cull erase the visible half of a crown; an apex outside the crown puts
the arrows in mid-air. None of those announce themselves in a screenshot.
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from graphDash.ui import arch_asset
from graphDash.ui.arch_model import (
    FORCE_GLYPH, LOWER_ARCH_ORDER, MOMENT_GLYPH, TOOTH_TYPE, build_arch,
    normalize_tooth,
)
from graphDash.ui.proj3d import vcross, vdot, vnorm, vsub

TOL = 1e-5

_results = []


def check(name, ok, detail=""):
    _results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'} | {name}" + (f"  --  {detail}" if detail else ""))


def section(title):
    print("\n" + "-" * 100)
    print(title)


def close(a, b, tol=TOL):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


arch = build_arch()

# ---------------------------------------------------------------- the arch
section("Arch: every lower tooth, once")

numbers = [t.palmer for t in arch.teeth]
check("all 16 teeth present exactly once",
      sorted(numbers) == sorted(LOWER_ARCH_ORDER), f"got {sorted(numbers)}")
check("tooth types match the dental table",
      all(t.ttype == TOOTH_TYPE[t.palmer] for t in arch.teeth))
check("unit is positive", arch.unit > 0, f"unit={arch.unit:.5f}")
check("guide is a polyline in the occlusal plane",
      len(arch.guide) >= 2 and all(abs(p[2]) <= TOL for p in arch.guide),
      f"{len(arch.guide)} points")

# The fit projects only the hull, so anything outside it would be cropped.
lo = [min(c[i] for t in arch.teeth for c in t.verts) for i in range(3)]
hi = [max(c[i] for t in arch.teeth for c in t.verts) for i in range(3)]
hlo = [min(p[i] for p in arch.fit_hull) for i in range(3)]
hhi = [max(p[i] for p in arch.fit_hull) for i in range(3)]
check("fit hull spans every crown vertex",
      all(hlo[i] <= lo[i] + TOL and hhi[i] >= hi[i] - TOL for i in range(3)),
      f"hull {[round(v, 3) for v in hlo]}..{[round(v, 3) for v in hhi]}")

# ---------------------------------------------------------------- notation
section("Palmer notation: the one gate every tooth identifier passes through")

check("every arch tooth is a valid Palmer designation",
      all(normalize_tooth(t.palmer) == t.palmer for t in arch.teeth))
check("arch runs lower-right to lower-left across the screen",
      numbers[0] == "LR8" and numbers[-1] == "LL8",
      f"{numbers[0]} .. {numbers[-1]}")
check("both quadrants number 1-8 outward from the midline",
      sorted(numbers) == sorted(f"L{q}{i}" for q in "LR" for i in range(1, 9)))

check("case and stray whitespace are accepted",
      normalize_tooth("lr5") == "LR5" and normalize_tooth("  LL8 ") == "LL8")
check("a pre-migration Universal number still resolves",
      normalize_tooth(31) == "LR7" and normalize_tooth("26") == "LR2",
      "31 -> LR7 (molar), 26 -> LR2 (incisor)")
check("no ninth tooth in a quadrant", normalize_tooth("LR9") is None)
check("upper-arch and nonsense values map to nothing",
      all(normalize_tooth(v) is None
          for v in (None, "", "UL6", "x", 3, 99, True, [])),
      "unset or upper-arch is normal -- it just never draws")

# ---------------------------------------------------------------- frames
section("Tooth frames: orthonormal and right-handed (the arrow directions)")

bad_unit, bad_orth, bad_hand, bad_z = [], [], [], []
for t in arch.teeth:
    e_x, e_y, e_z = t.frame
    if not all(abs(vnorm(v) - 1.0) <= TOL for v in t.frame):
        bad_unit.append(t.palmer)
    if max(abs(vdot(e_x, e_y)), abs(vdot(e_y, e_z)), abs(vdot(e_x, e_z))) > TOL:
        bad_orth.append(t.palmer)
    if not close(vcross(e_x, e_y), e_z):
        bad_hand.append(t.palmer)
    if not close(e_z, (0.0, 0.0, 1.0)):
        bad_z.append(t.palmer)

check("all basis vectors are unit length", not bad_unit, f"bad: {bad_unit}")
check("all frames are orthogonal", not bad_orth, f"bad: {bad_orth}")
check("all frames are right-handed (e_x x e_y = e_z)", not bad_hand, f"bad: {bad_hand}")
check("e_z is occlusal (+z), so -Fz is intrusive", not bad_z, f"bad: {bad_z}")

# e_y points buccal: away from the arch's interior, which lies toward +y of the
# anterior teeth. Testing against the arch centroid catches a flipped normal.
cy = sum(t.center[1] for t in arch.teeth) / len(arch.teeth)
inward = [t.palmer for t in arch.teeth
          if vdot(t.frame[1], (t.center[0], t.center[1] - cy, 0.0)) <= 0.0]
check("e_y points buccal (outward) on every tooth", not inward, f"inward: {inward}")

# ---------------------------------------------------------------- meshes
section("Crown meshes: indices, normals, closure")

bad_idx, bad_norm, outward_fail, open_edges, empty = [], [], [], [], []
for t in arch.teeth:
    nv = len(t.verts)
    if not t.tris or not t.verts:
        empty.append(t.palmer)
        continue
    if any(i >= nv or i < 0 for i, j, k, _ in t.tris for i in (i, j, k)):
        bad_idx.append(t.palmer)
    if any(abs(vnorm(n) - 1.0) > 1e-4 for *_, n in t.tris):
        bad_norm.append(t.palmer)

    centroid = tuple(sum(v[i] for v in t.verts) / nv for i in range(3))
    for i, j, k, n in t.tris:
        face = tuple((t.verts[i][a] + t.verts[j][a] + t.verts[k][a]) / 3.0
                     for a in range(3))
        if vdot(n, vsub(face, centroid)) <= 0.0:
            outward_fail.append(t.palmer)
            break

    # A closed surface shares every edge between exactly two triangles. An open
    # shell would let the cull expose the inside of a crown at some angles.
    seen = {}
    for i, j, k, _ in t.tris:
        for a, b in ((i, j), (j, k), (k, i)):
            key = (min(a, b), max(a, b))
            seen[key] = seen.get(key, 0) + 1
    if any(c != 2 for c in seen.values()):
        open_edges.append((t.palmer, sum(1 for c in seen.values() if c != 2)))

check("every tooth has a mesh", not empty, f"empty: {empty}")
check("all triangle indices are in range", not bad_idx, f"bad: {bad_idx}")
check("all face normals are unit length", not bad_norm, f"bad: {bad_norm}")
check("all face normals point outward", not outward_fail, f"bad: {outward_fail}")
check("every crown is a closed surface", not open_edges, f"open: {open_edges}")

# ---------------------------------------------------------------- anchors
section("Arrow origins and footprints")

bad_apex, bad_sil = [], []
for t in arch.teeth:
    xs = [v[0] for v in t.verts]
    ys = [v[1] for v in t.verts]
    if not (t.apex[2] > t.center[2] and
            min(xs) - TOL <= t.apex[0] <= max(xs) + TOL and
            min(ys) - TOL <= t.apex[1] <= max(ys) + TOL):
        bad_apex.append(t.palmer)
    if len(t.silhouette) < 3 or any(abs(p[2]) > TOL for p in t.silhouette):
        bad_sil.append(t.palmer)

check("apex is above the crown centre and inside its footprint",
      not bad_apex, f"bad: {bad_apex}")
check("silhouette is a flat polygon in the occlusal plane",
      not bad_sil, f"bad: {bad_sil}")

# ---------------------------------------------------------------- glyph scale
section("Glyph scale: the thresholds the arrows are ramped against")

for scale in (FORCE_GLYPH, MOMENT_GLYPH):
    q = scale.quantity
    check(f"{q}: below lo draws nothing", scale.frac(scale.lo * 0.99) is None)
    check(f"{q}: at lo the ramp starts at 0", scale.frac(scale.lo) == 0.0)
    check(f"{q}: at hi the ramp clamps to 1", scale.frac(scale.hi) == 1.0)
    check(f"{q}: past hi stays clamped", scale.frac(scale.hi * 10) == 1.0)
    check(f"{q}: sign does not change length",
          scale.frac(scale.hi / 2) == scale.frac(-scale.hi / 2))
    check(f"{q}: resultant has a larger ceiling than one component",
          scale.resultant.hi > scale.hi,
          f"{scale.resultant.hi} > {scale.hi}")
    check(f"{q}: resultant shares lo, so 'not shown' means the same thing",
          scale.resultant_frac(scale.lo * 0.99) is None
          and scale.resultant_frac(scale.lo) == 0.0)
    check(f"{q}: three clamped components stay on the resultant ramp",
          scale.resultant_frac(math.sqrt(3) * scale.hi) <= 1.0)
    check(f"{q}: axes carry three distinct reading indices",
          len({a.index for a in scale.axes}) == 3,
          f"{[a.index for a in scale.axes]}")

check("force and moment read disjoint halves of the sample",
      not ({a.index for a in FORCE_GLYPH.axes} & {a.index for a in MOMENT_GLYPH.axes}))

# ---------------------------------------------------------------- round trip
section("Asset round-trip: what the baker writes is what the app reads")

import tempfile

with tempfile.TemporaryDirectory() as tmp:
    base = os.path.join(tmp, "roundtrip")
    arch_asset.write_asset(
        base, arch.unit, arch.guide, arch.fit_hull,
        [{"palmer": t.palmer, "ttype": t.ttype, "center": t.center,
          "frame": t.frame, "apex": t.apex, "label_anchor": t.label_anchor,
          "height": t.height, "silhouette": t.silhouette, "verts": t.verts,
          "tris": [(i, j, k) for i, j, k, _ in t.tris],
          "normals": [n for *_, n in t.tris]} for t in arch.teeth])
    again = build_arch(base)

check("round-trip preserves tooth count and order",
      [t.palmer for t in again.teeth] == numbers)
check("round-trip preserves vertices exactly",
      all(a.verts == b.verts for a, b in zip(arch.teeth, again.teeth)))
check("round-trip preserves triangles and normals exactly",
      all(a.tris == b.tris for a, b in zip(arch.teeth, again.teeth)))
check("round-trip preserves frames and apexes exactly",
      all(a.frame == b.frame and a.apex == b.apex
          for a, b in zip(arch.teeth, again.teeth)))

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
