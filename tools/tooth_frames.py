"""Recover each tooth's own x/y/z directions and centre from its bounding box.

    # look at the numbers -- this is the whole point of the tool
    python3 tools/tooth_frames.py --stl-dir assets_src

    # the machine-readable form, and the angle from the frames baked today
    python3 tools/tooth_frames.py --stl-dir assets_src --json /tmp/frames.json
    python3 tools/tooth_frames.py --stl-dir assets_src --compare graphDash/assets/arch_mesh

Runs on a laptop, never on the Pi: like `bake_arch_mesh.py` it needs `trimesh`
and `numpy`. It writes no asset -- `graphDash/assets/arch_mesh.*` and
`arch_asset.FORMAT_VERSION` are untouched. This is the input to a later arch
view in which each tooth's force vectors start at that tooth's centre and point
along that tooth's own axes, instead of the single shared vertical the view uses
today (`bake_arch_mesh.build_teeth` hard-codes `e_z = (0,0,1)` for all sixteen).

The three directions are the face normals of the tooth's **oriented** bounding
box -- the minimum-volume box, not the axis-aligned one, which would hand every
tooth the same three normals and defeat the point. Of the three normal pairs,
the one nearest world up is the top face and becomes `e_z`; of the two adjacent
sides, the one facing out of the arch becomes `e_y`; `e_x` closes the
right-handed set, matching `arch_model.Tooth.frame`.

Two choices in here are measurements, not taste:

  * **The box encloses the whole tooth, root included.** A crown-only box fails
    on the anterior teeth: an incisor crown is a wedge, so its minimum-volume
    box aligns to the labial face rather than the tooth axis, and LR1/LL1 come
    out 43-46 degrees off with their occlusal and bucco-lingual extents
    swapped. The long root pins the axis, so the whole-tooth box is stable
    everywhere -- 10-15 degrees of tilt on molars, 28-32 on incisors, which is
    what a real arch looks like.
  * **The centre comes from the crown.** That same whole-tooth box centres
    mid-root, below the gum line, which is no place to start an arrow from. So
    the axes come from the whole tooth and the origin from the top
    `--crown-frac` of it, measured along the tooth's own axis.

Everything is reported in the same world space as `arch_mesh.json` -- this tool
reuses the baker's `load_and_orient()`, so the centres and axes are directly
comparable to the baked `center` / `apex` / `frame` (see `--compare`).

The file has one seam, and it is worth keeping: everything above `--- scans ---`
is arithmetic on plain 3-tuples with no trimesh, no numpy and no file access, so
`tests/test_tooth_frames.py` exercises it against a synthetic box in a plain
Python process. Below the seam is the adapter that turns STLs into those tuples.
"""

import argparse
import json
import math
import os
import sys
from dataclasses import asdict, dataclass

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

from bake_arch_mesh import (DEFAULT_MANIFEST, arch_curve, discover,
                            load_and_orient, need_trimesh)
from graphDash.ui.arch_model import LOWER_ARCH_ORDER, TOOTH_TYPE
from graphDash.ui.proj3d import vcross, vdot, vmad, vscale, vsub, vunit

# The top 45% of the tooth's own long axis. The cervical line would be the
# anatomical cut, but on these scans an incisor's profile widens almost
# monotonically from root apex to incisal edge, so the narrowest cross-section
# is too weakly defined to find reliably. A fraction is one number to tune and
# the report prints where it landed.
CROWN_FRAC = 0.45

# A box that has latched onto the wrong face shows up as an implausible tilt
# long before it shows up as a wrong arrow.
TILT_WARN_DEG = 40.0

# A tooth whose box is nearly square in cross-section has a weakly determined
# yaw: the two side faces are almost interchangeable, so the box can settle up
# to 45 degrees away from the arch's own buccal direction and still be the
# minimum-volume box. That is a property of the tooth, not a bug, but it means
# e_x and e_y may have swapped roles on that tooth.
YAW_WARN_DEG = 35.0

ORTHO_TOL = 1e-6

UP = (0.0, 0.0, 1.0)


@dataclass(frozen=True)
class ToothFrame:
    """One tooth's own coordinate system, in arch-asset world space."""

    palmer: str                  # "LR6"
    ttype: str                   # arch_model.TOOTH_TYPE
    center: tuple                # crown box centre -- where the vectors start
    frame: tuple                 # (e_x, e_y, e_z): mesio-distal, buccal, occlusal
    extents: tuple               # whole-tooth box, ordered (md, bl, occlusal)
    box_center: tuple            # whole-tooth box centre
    cut: float                   # crown plane, as a coordinate along e_z
    tilt_deg: float              # angle between e_z and world up
    yaw_deg: float               # e_y vs the arch curve's buccal direction, in plan


def angle_deg(a, b):
    """Angle between two unit vectors, clamped against float drift."""
    return math.degrees(math.acos(max(-1.0, min(1.0, vdot(a, b)))))


def plan_angle_deg(a, b):
    """Angle between two vectors seen from above, ignoring their z components.

    Yaw and tilt are separate questions -- measuring yaw in plan keeps a tooth's
    inclination from leaking into it.
    """
    flat_a, flat_b = vunit((a[0], a[1], 0.0)), vunit((b[0], b[1], 0.0))
    if flat_a == (0.0, 0.0, 0.0) or flat_b == (0.0, 0.0, 0.0):
        return 90.0                       # straight up: no meaningful yaw
    return angle_deg(flat_a, flat_b)


def canonical_frame(axes, buccal):
    """Sort three box face normals into (e_x, e_y, e_z), right-handed.

    `axes` are the box's three unit normals in any order and either sign, which
    is all an oriented box gives you; `buccal` is the outward direction of the
    arch at this tooth, which is what breaks the sign ambiguity.
    """
    # Top face: the normal pair nearest world up, signed out of the tooth.
    i_occ = max(range(3), key=lambda i: abs(axes[i][2]))
    e_z = axes[i_occ] if axes[i_occ][2] > 0 else vscale(axes[i_occ], -1.0)

    # First adjacent side: of the two that are left, the one facing out of the
    # arch. The buccal reference is the global arch curve rather than this
    # tooth's own shape -- a single molar is too round to say which way is out.
    rest = [i for i in range(3) if i != i_occ]
    i_bl = max(rest, key=lambda i: abs(vdot(axes[i], buccal)))
    e_y = axes[i_bl] if vdot(axes[i_bl], buccal) > 0 else vscale(axes[i_bl], -1.0)

    # A no-op for a box, whose axes are already orthogonal, but it keeps the
    # frame exactly orthonormal rather than nearly so.
    e_y = vunit(vmad(e_y, e_z, -vdot(e_y, e_z)))

    # The remaining box axis is the second adjacent side. e_x is taken from the
    # cross product instead, so the set is right-handed by construction and
    # matches the repo's e_x x e_y = e_z convention -- and so the extent along
    # it can be measured rather than looked up.
    return (vcross(e_y, e_z), e_y, e_z)


def box_in_frame(points, frame):
    """Centre and extents of a point set measured along a frame's three axes.

    Called twice per tooth: once on every vertex, where the frame is the box's
    own basis and this reproduces the oriented box exactly, and once on the
    crown slab. One way to measure a box, not two.
    """
    center, extents = (0.0, 0.0, 0.0), []
    for axis in frame:
        lo = hi = vdot(points[0], axis)
        for p in points:
            d = vdot(p, axis)
            lo, hi = min(lo, d), max(hi, d)
        center = vmad(center, axis, 0.5 * (lo + hi))
        extents.append(hi - lo)
    return center, tuple(extents)


def crown_slab(points, e_z, crown_frac):
    """The top `crown_frac` of the points along `e_z`, and the cut plane.

    The cut follows the tooth's own inclination rather than being horizontal,
    which is the only reason it needs the frame at all.
    """
    along = [vdot(p, e_z) for p in points]
    lo, hi = min(along), max(along)
    # Written from lo so that a fraction of 1.0 lands exactly on the lowest
    # vertex rather than a rounding step above it, which would silently drop it.
    cut = lo + (1.0 - crown_frac) * (hi - lo)
    return [p for p, d in zip(points, along) if d >= cut], cut


def build_frame(palmer, points, buccal, axes, crown_frac):
    """Everything a tooth's frame needs, from tuples alone."""
    frame = canonical_frame(axes, buccal)
    box_center, extents = box_in_frame(points, frame)
    slab, cut = crown_slab(points, frame[2], crown_frac)
    center, _ = box_in_frame(slab, frame)
    return ToothFrame(
        palmer=palmer,
        ttype=TOOTH_TYPE[palmer],
        center=center,
        frame=frame,
        extents=extents,
        box_center=box_center,
        cut=cut,
        tilt_deg=angle_deg(frame[2], UP),
        yaw_deg=plan_angle_deg(frame[1], buccal),
    )


def check(frames):
    """Warnings worth reading, and the hard failures that make the tool exit 1.

    Runs on every path, not just when the table prints: a caller taking the
    JSON has more need of this than one reading the numbers by eye.
    """
    warnings, failures = [], []
    mean_y = sum(f.center[1] for f in frames.values()) / len(frames)

    for number in sorted(frames):
        f = frames[number]
        e_x, e_y, e_z = f.frame
        md, bl, occ = f.extents

        lengths = [abs(vdot(v, v) - 1.0) for v in f.frame]
        dots = [abs(vdot(e_x, e_y)), abs(vdot(e_y, e_z)), abs(vdot(e_x, e_z))]
        if max(lengths) > ORTHO_TOL or max(dots) > ORTHO_TOL:
            failures.append(f"{number}: frame is not orthonormal "
                            f"(|len-1| {max(lengths):.2e}, |dot| {max(dots):.2e})")
        if vdot(vcross(e_x, e_y), e_z) < 1.0 - ORTHO_TOL:
            failures.append(f"{number}: frame is left-handed")

        if f.tilt_deg > TILT_WARN_DEG:
            warnings.append(f"{number}: e_z is {f.tilt_deg:.1f} deg off vertical -- "
                            "the box may have latched onto the wrong face")
        if occ < max(md, bl):
            warnings.append(f"{number}: occlusal extent {occ:.2f} is not the "
                            f"largest (md {md:.2f}, bl {bl:.2f}) -- axis roles "
                            "are probably swapped")
        if f.yaw_deg > YAW_WARN_DEG:
            warnings.append(f"{number}: e_y is {f.yaw_deg:.1f} deg off the arch's "
                            f"buccal direction, and the box is nearly square "
                            f"({md:.2f} md vs {bl:.2f} bl) -- e_x and e_y may "
                            "have swapped on this tooth")
        # Same buccal test tests/test_arch_model.py makes of the baked frames:
        # the arch is symmetric about x = 0, so out is (x, y - mean_y).
        if vdot(e_y, (f.center[0], f.center[1] - mean_y, 0.0)) <= 0:
            warnings.append(f"{number}: e_y points into the arch, not buccal")
        # The crown centre is the whole point of the record; a crown cut that
        # put it outside the tooth would be silent otherwise.
        offset = vsub(f.center, f.box_center)
        if any(abs(vdot(offset, a)) > e / 2 + ORTHO_TOL
               for a, e in zip(f.frame, f.extents)):
            failures.append(f"{number}: crown centre lies outside the tooth's box")

    # Identical scans produce identical frames. That is placeholder input, not
    # a bug in here, but it is worth saying out loud -- and the frames say it
    # directly, so nothing has to hold on to the meshes to find out.
    seen = {}
    for number in sorted(frames):
        seen.setdefault((frames[number].frame, frames[number].center),
                        []).append(number)
    for group in seen.values():
        if len(group) > 1:
            warnings.append(f"{', '.join(group)}: identical frames -- the same "
                            "scan supplied twice?")
    return warnings, failures


# ------------------------------------------------------------------ scans ---
# Below here is the adapter: STLs, trimesh and numpy. Everything above works on
# plain tuples so it can be tested without any of them.


def read_manifest(path=DEFAULT_MANIFEST):
    """The baker's manifest, or an empty one. Missing is normal, not an error."""
    if not path or not os.path.exists(path):
        return {}
    with open(path) as fh:
        return json.load(fh)


def box_directions(vertices):
    """The oriented box's three face normals, as unit tuples.

    The only place trimesh's geometry is touched. `oriented_bounds` returns the
    world->box transform, so the inverse has the face normals as its columns.
    """
    trimesh, np = need_trimesh()
    to_world = np.linalg.inv(trimesh.bounds.oriented_bounds(vertices)[0])
    return tuple(vunit(tuple(float(v) for v in to_world[:3, i]))
                 for i in range(3))


def tooth_frames(stl_dir, manifest=None, crown_frac=CROWN_FRAC):
    """Palmer designation -> ToothFrame, in arch-asset world space.

    `manifest` is the parsed `arch_bake.json` dict, or None to read the default.
    This is the entry point a later arch view (or a later `build_teeth`) calls;
    everything below it is command line.
    """
    if not 0.0 < crown_frac <= 1.0:
        raise SystemExit(f"crown fraction must be in (0, 1], got {crown_frac}")
    manifest = read_manifest() if manifest is None else manifest

    meshes = load_and_orient(discover(stl_dir, manifest.get("teeth", {})),
                             manifest)
    a, b, _ = arch_curve([m.vertices.mean(axis=0)
                          for _, m in sorted(meshes.items())])

    frames = {}
    for number in LOWER_ARCH_ORDER:
        mesh = meshes.get(number)
        if mesh is None:
            continue
        points = [(float(p[0]), float(p[1]), float(p[2])) for p in mesh.vertices]

        # Outward normal of y = ax^2 + bx + c at this crown: the interior of
        # the U is the y > curve side, so out is (2ax + b, -1). Same convention
        # as bake_arch_mesh.build_teeth.
        cx = sum(p[0] for p in points) / len(points)
        buccal = vunit((2 * a * cx + b, -1.0, 0.0))

        frames[number] = build_frame(number, points, buccal,
                                     box_directions(mesh.vertices), crown_frac)
    return frames


# -------------------------------------------------------------- reporting ---


def table(frames, crown_frac):
    print(f"\n  {len(frames)} teeth, crown fraction {crown_frac:g}\n")
    head = (f"  {'tooth':<5} {'type':<15} {'centre':<24} "
            f"{'e_x (mesio-distal)':<22} {'e_y (buccal)':<22} "
            f"{'e_z (occlusal)':<22} {'tilt':>6} {'yaw':>6}  "
            f"{'md/bl/occ':<17} cut")
    print(head)
    print("  " + "-" * (len(head) - 2))
    for number in LOWER_ARCH_ORDER:
        f = frames.get(number)
        if f is None:
            continue
        vec = lambda v: f"({v[0]:+.3f},{v[1]:+.3f},{v[2]:+.3f})"
        print(f"  {f.palmer:<5} {f.ttype:<15} {vec(f.center):<24} "
              f"{vec(f.frame[0]):<22} {vec(f.frame[1]):<22} "
              f"{vec(f.frame[2]):<22} {f.tilt_deg:5.1f}d {f.yaw_deg:5.1f}d  "
              f"{f.extents[0]:.2f}/{f.extents[1]:.2f}/{f.extents[2]:.2f}     "
              f"{f.cut:+.3f}")

    missing = [n for n in LOWER_ARCH_ORDER if n not in frames]
    if missing:
        print(f"\n  no mesh for: {', '.join(missing)}")


def compare(frames, base):
    """Angle between each new axis and the one in the baked asset."""
    from graphDash.ui import arch_asset

    baked = {t["palmer"]: t for t in arch_asset.read_asset(base)["teeth"]}
    print(f"\n  against {base}.json\n")
    print(f"  {'tooth':<5} {'d(e_x)':>7} {'d(e_y)':>7} {'d(e_z)':>7}   "
          f"{'centre shift':<24} baked apex")
    print("  " + "-" * 76)
    for number in LOWER_ARCH_ORDER:
        f, old = frames.get(number), baked.get(number)
        if f is None or old is None:
            continue
        deltas = [angle_deg(f.frame[i], tuple(old["frame"][i])) for i in range(3)]
        shift = vsub(f.center, old["center"])
        print(f"  {f.palmer:<5} {deltas[0]:6.1f}d {deltas[1]:6.1f}d {deltas[2]:6.1f}d   "
              f"({shift[0]:+.3f},{shift[1]:+.3f},{shift[2]:+.3f})       "
              f"({old['apex'][0]:+.3f},{old['apex'][1]:+.3f},{old['apex'][2]:+.3f})")
    print("\n  d(e_z) should equal the reported tilt: the baked e_z is world up.")
    print("  The baked centre is a footprint centre at z = 0, so its shift is "
          "mostly the crown height.\n")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stl-dir", required=True, help="directory of per-tooth STLs")
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST,
                    help="filename->tooth overrides and orientation fixes")
    ap.add_argument("--crown-frac", type=float, default=CROWN_FRAC,
                    help="fraction of the tooth's long axis counted as crown, "
                         f"measured down from the tip (default {CROWN_FRAC})")
    ap.add_argument("--json", metavar="PATH", help="write the frames as JSON")
    ap.add_argument("--compare", metavar="BASE",
                    help="asset base path to compare against, e.g. "
                         "graphDash/assets/arch_mesh")
    args = ap.parse_args()

    frames = tooth_frames(args.stl_dir, read_manifest(args.manifest),
                          args.crown_frac)
    warnings, failures = check(frames)

    table(frames, args.crown_frac)
    if args.compare:
        compare(frames, args.compare)
    if args.json:
        payload = {
            "stl_dir": os.path.abspath(args.stl_dir),
            "crown_frac": args.crown_frac,
            "teeth": [asdict(frames[n])
                      for n in LOWER_ARCH_ORDER if n in frames],
        }
        with open(args.json, "w") as fh:
            json.dump(payload, fh, indent=1, sort_keys=True)
        print(f"  wrote {len(payload['teeth'])} frames to {args.json}")

    print()
    for line in warnings:
        print(f"  warning: {line}")
    for line in failures:
        print(f"  FAILED:  {line}")
    if not warnings and not failures:
        print("  all frames orthonormal, right-handed, buccal and plausibly upright.")
    print()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
