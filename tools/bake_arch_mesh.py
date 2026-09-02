"""Turn scanned tooth STLs into the arch mesh asset the dashboard loads.

    # first contact with a new scan set -- diagnostics only, writes nothing
    python3 tools/bake_arch_mesh.py --stl-dir ~/scans --report

    # the real bake
    python3 tools/bake_arch_mesh.py --stl-dir ~/scans --budget 300 \\
        --out graphDash/assets/arch_mesh

Runs on a laptop, never on the Pi: it needs `trimesh` and `numpy`
(`pip install trimesh`), and its whole purpose is to move that cost offline so
the runtime stays stdlib plus QPainter.

What makes this tool delicate is that a scan arrives in an arbitrary pose and an
arbitrary unit, and nothing in the file says which way is up. Rather than guess
from the geometry, the orientation is derived from **the tooth numbering**,
which the filenames already carry:

    +x  from tooth 32 toward tooth 17   (viewer's right in the occlusal view)
    +y  from the incisors toward the molars   (posterior)
    +z  = x cross y   (occlusal -- up, out of the tooth)

That is robust in a way a principal-axis fit is not: PCA gives axes but not
signs, and a sign error here silently points every force arrow the wrong way.

What numbering cannot settle is chirality: a mirrored export gives the same
three axes with `+z` pointing into the roots. Nor can a determinant say so --
`e_z` is `e_x x e_y`, so the basis is right-handed however the scan was written.
The geometry is what tells you, and `--report` ends with the test: crowns are
broader than roots, so the wide end must land at `+z`. If it does not,
`arch_bake.json` carries two fixes that look identical from here --
**`mirror_z`** reflects the scan (for a genuinely mirrored export; a rotation
cannot undo a mirror) and **`flip_z`** turns it about `+y` (for LL/LR filenames
that are swapped). Both are folded into the one transform in `load_and_orient`,
before anything measures the mesh. **Always look at the verification sheet
before trusting a bake.**
"""

import argparse
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from graphDash.ui import arch_asset
from graphDash.ui.arch_model import LOWER_ARCH_ORDER, TOOTH_TYPE, normalize_tooth

# Palmer is what this app speaks, and `LL5.stl` / `LR2.stl` is the expected
# filename. Scans exported under Universal or FDI numbering are still accepted
# and translated, because a scanner rarely asks which notation you prefer.
# The scheme is detected across the whole filename set, never per file.
FDI_TO_PALMER = {
    38: 'LL8', 37: 'LL7', 36: 'LL6', 35: 'LL5',
    34: 'LL4', 33: 'LL3', 32: 'LL2', 31: 'LL1',
    41: 'LR1', 42: 'LR2', 43: 'LR3', 44: 'LR4',
    45: 'LR5', 46: 'LR6', 47: 'LR7', 48: 'LR8',
}
UNIVERSAL_RANGE = range(17, 33)

# Teeth that anchor the orientation. Molars sit posterior, incisors anterior;
# LR8 and LL8 are the two ends of the arch, so their difference is the x axis.
MOLARS = ('LL8', 'LL7', 'LR7', 'LR8')
INCISORS = ('LL2', 'LL1', 'LR1', 'LR2')
RIGHT_END = ('LL8', 'LL7', 'LL6')
LEFT_END = ('LR6', 'LR7', 'LR8')

# The procedural arch spanned x in [-1, 1] and y in [0, 1.25]. Normalising the
# scan to the same extent keeps every arrow length, ring radius and camera
# constant in arch_tab.py valid without retuning a single number.
TARGET_HALF_WIDTH = 1.0

# Mesio-distal width of each crown as a multiple of `unit` -- the table the
# retired procedural arch was laid out from. It lives here, not in the runtime,
# because it is now only ever used to reproduce that arch's *scale*: keeping
# `unit` close to its old value is what leaves every arrow length, ring radius
# and camera constant in arch_tab.py valid without retuning a single one.
MD_WIDTH = {"molar": 1.35, "premolar": 0.90, "canine": 0.75, "incisor": 0.60}
TOOTH_GAP_FRAC = 0.04
PROCEDURAL_UNIT = 0.20688       # what the prism arch produced; sanity anchor

DEFAULT_MANIFEST = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "arch_bake.json")


def need_trimesh():
    try:
        import numpy as np
        import trimesh
    except ImportError as exc:
        raise SystemExit(
            f"{exc}\n\nThis is the offline baker; it needs trimesh and numpy:\n"
            "    .venv/bin/pip install trimesh\n"
            "The runtime does not -- it only reads the asset this writes."
        )
    return trimesh, np


# ---- finding and naming the meshes ----

def _parse_number(stem, scheme):
    """The Palmer designation this filename names, or None.

    `scheme` is decided once for the whole set by `detect_scheme`.
    """
    if scheme == "palmer":
        hits = [t for t in TOOTH_TYPE
                if re.search(rf"(?<![A-Za-z]){t}(?![A-Za-z0-9])", stem, re.I)]
        return hits[0] if len(hits) == 1 else None

    table = UNIVERSAL_RANGE if scheme == "universal" else FDI_TO_PALMER
    hits = [int(h) for h in re.findall(r"\d+", stem) if int(h) in table]
    if len(hits) != 1:
        return None
    return (normalize_tooth(hits[0]) if scheme == "universal"
            else FDI_TO_PALMER[hits[0]])


def detect_scheme(names):
    """Which numbering the filenames use, decided across the whole set.

    Per-file detection cannot work: "31" is a valid tooth in both Universal
    (LR7, a molar) and FDI (LL1, a central incisor) -- opposite ends of the
    arch. Whichever scheme explains the most files wins, and a tie is an error
    rather than a coin flip.
    """
    scored = sorted(
        ((sum(1 for n in names if _parse_number(n, s) is not None), s)
         for s in ("universal", "fdi", "palmer")), reverse=True)
    best, runner = scored[0], scored[1]
    if best[0] == 0:
        raise SystemExit(
            "no filename named a lower tooth. Expected Palmer (LL5.stl, "
            "LR2.stl), or Universal (17-32) / FDI (31-48) which are "
            f"translated.\nMap them by hand in {DEFAULT_MANIFEST}.")
    if best[0] == runner[0]:
        raise SystemExit(
            f"filenames are equally consistent with {best[1]} and {runner[1]} "
            f"numbering ({best[0]} files each).\n"
            f'Set "numbering" explicitly in {DEFAULT_MANIFEST}.')
    return best[1]


def discover(stl_dir, overrides, scheme=None):
    """Map STL paths to Palmer designations.

    Guessing silently is how a premolar's mesh ends up labelled as a molar and
    nobody notices until the arrows sit on the wrong tooth, so an unmapped or
    duplicated file is a hard error, and the numbering scheme is decided across
    the whole set rather than per file.
    """
    candidates = [n for n in sorted(os.listdir(stl_dir))
                  if n.lower().endswith((".stl", ".ply", ".obj"))
                  and overrides.get(n, "") is not None]
    if not candidates:
        raise SystemExit(f"no tooth meshes found in {stl_dir}")
    scheme = scheme or detect_scheme(
        [os.path.splitext(n)[0] for n in candidates if n not in overrides]
        or [os.path.splitext(n)[0] for n in candidates])
    print(f"  numbering detected: {scheme}")

    found, unmapped = {}, []
    for name in sorted(os.listdir(stl_dir)):
        if not name.lower().endswith((".stl", ".ply", ".obj")):
            continue
        path = os.path.join(stl_dir, name)
        if name in overrides:
            number = normalize_tooth(overrides[name])
            if number is None:            # explicitly excluded, e.g. the whole arch
                continue
        else:
            number = _parse_number(os.path.splitext(name)[0], scheme)
            if number is None:
                unmapped.append(name)
                continue
        if number in found:
            raise SystemExit(
                f"two files claim tooth {number}: {os.path.basename(found[number])} "
                f"and {name}\nResolve it in {DEFAULT_MANIFEST}.")
        found[number] = path

    if unmapped:
        raise SystemExit(
            "could not map these files to a tooth:\n  " +
            "\n  ".join(unmapped) +
            f"\n\nAdd them to \"teeth\" in {DEFAULT_MANIFEST} as "
            '{"filename.stl": "LR6"}, or {"filename.stl": null} to skip '
            "(use null for the combined arch).")
    return found


# ---- orientation ----

def world_transform(centroids, flip_z=False, mirror_z=False):
    """The 3x3 taking the scan's frame to the app's, from tooth numbering.

    Returns the matrix whose rows are the world axes expressed in scan
    coordinates, so `world = R @ scan`. With `mirror_z` it is a reflection
    rather than a rotation, and `det` comes back negative to say so -- callers
    have to reverse their triangle winding to match.
    """
    _, np = need_trimesh()

    def mean_of(numbers):
        pts = [centroids[n] for n in numbers if n in centroids]
        if not pts:
            raise SystemExit(
                f"need at least one of teeth {numbers} to orient the scan; "
                "none of them were supplied.")
        return np.mean(np.array(pts), axis=0)

    # +x runs along the arch, from the patient's left end to the right end.
    right, left = mean_of(RIGHT_END), mean_of(LEFT_END)
    e_x = right - left
    # +y is posterior: incisors are anterior, molars posterior.
    e_y = mean_of(MOLARS) - mean_of(INCISORS)

    e_x = e_x / np.linalg.norm(e_x)
    e_y = e_y - e_x * float(e_x @ e_y)        # Gram-Schmidt: make y perpendicular
    e_y = e_y / np.linalg.norm(e_y)
    e_z = np.cross(e_x, e_y)
    if flip_z:
        # Not a mirror: a 180-degree turn about +y. Use it when the LL and LR
        # *filenames* are swapped, which makes e_x point the wrong way along
        # the arch and drags e_z apical with it. It keeps the geometry
        # untouched and right-handed, and moves every tooth to the other side.
        e_x, e_z = -e_x, -e_z
    if mirror_z:
        # A genuine mirror, for a scan exported left-handed. No rotation can
        # undo that -- rotating a mirrored arch back to crowns-up leaves the
        # patient's left on the right -- so reflect instead, which is why this
        # returns det < 0. Reflecting z alone keeps +x toward the patient's
        # left and +y posterior, and only lifts the crowns.
        e_z = -e_z
    return np.array([e_x, e_y, e_z])


def convex_hull_2d(points):
    """Monotone-chain hull of (x, y) pairs -- the crown's occlusal silhouette.

    Written out rather than pulled from scipy because the silhouette is the one
    piece of bake output the runtime draws directly, and a crown is convex
    enough in occlusal projection that a hull is the honest outline.
    """
    pts = sorted(set((round(x, 9), round(y, 9)) for x, y in points))
    if len(pts) <= 2:
        return pts

    def half(seq):
        out = []
        for p in seq:
            while len(out) >= 2:
                (ax, ay), (bx, by) = out[-2], out[-1]
                if (bx - ax) * (p[1] - ay) - (by - ay) * (p[0] - ax) > 0:
                    break
                out.pop()
            out.append(p)
        return out[:-1]

    return half(pts) + half(reversed(pts))


def decimate_ring(points, count):
    """Thin a closed outline to `count` points, evenly by index."""
    n = len(points)
    if n <= count:
        return list(points)
    return [points[round(i * n / count) % n] for i in range(count)]


# ---- the bake ----

def load_and_orient(paths, manifest):
    """Load every tooth, put the whole set in the app's world frame, and scale."""
    trimesh, np = need_trimesh()

    meshes = {}
    for number, path in sorted(paths.items()):
        m = trimesh.load(path, force="mesh")
        if m.is_empty:
            raise SystemExit(f"tooth {number}: {path} loaded empty")
        meshes[number] = m

    centroids = {n: m.centroid for n, m in meshes.items()}
    R = world_transform(centroids, manifest.get("flip_z", False),
                        manifest.get("mirror_z", False))
    mirrored = float(np.linalg.det(R)) < 0

    for m in meshes.values():
        m.vertices = m.vertices @ R.T
        if mirrored:
            # A reflection turns every triangle inside out. Reverse the winding
            # here, at the transform, so that everything downstream -- the
            # oriented boxes, the crown cut, `fix_normals`, the baked face
            # normals the runtime culls on -- sees an outward-wound surface.
            m.faces = np.ascontiguousarray(np.asarray(m.faces)[:, ::-1])

    # Centre on the arch, then scale so it spans the extent the view was tuned
    # for. Both are derived from the whole set, so the teeth keep their relative
    # positions exactly as scanned.
    allv = np.vstack([m.vertices for m in meshes.values()])
    half = 0.5 * (allv[:, 0].max() - allv[:, 0].min())
    scale = manifest.get("scale") or (TARGET_HALF_WIDTH / half)
    origin = np.array([0.5 * (allv[:, 0].max() + allv[:, 0].min()),
                       allv[:, 1].min(), allv[:, 2].min()])
    for m in meshes.values():
        m.vertices = (m.vertices - origin) * scale
    return meshes


def arch_curve(centroids):
    """Least-squares `y = a x^2 + b x + c` through the crown centres.

    The buccal normal comes from this curve rather than from each crown's own
    principal axes: a single molar is too round for PCA to give a stable axis,
    and per-tooth fits let neighbouring teeth disagree about which way is out.
    A curve through all sixteen varies smoothly by construction.
    """
    _, np = need_trimesh()
    xs = np.array([c[0] for c in centroids])
    ys = np.array([c[1] for c in centroids])
    return np.polyfit(xs, ys, 2)


def build_teeth(meshes, budget, curve, unit, label_gap):
    trimesh, np = need_trimesh()
    a, b, _ = curve
    teeth = []

    for number in LOWER_ARCH_ORDER:
        m = meshes.get(number)
        if m is None:
            continue
        if budget and len(m.faces) > budget:
            try:
                m = m.simplify_quadric_decimation(face_count=budget)
            except TypeError:             # older trimesh took it positionally
                m = m.simplify_quadric_decimation(budget)
        m.merge_vertices()
        # Segmented scans arrive with holes at the gingival margin and
        # inconsistent winding. The renderer's back-face cull assumes a closed,
        # outward-wound surface: an open shell shows the inside of the tooth at
        # some camera angles, and tests/test_arch_model.py asserts exactly this.
        m.fill_holes()
        m.fix_normals()
        if not m.is_watertight:
            print(f"    warning: tooth {number} is not closed after repair "
                  f"({len(m.faces)} faces) -- it may render see-through at some "
                  f"angles. Check the scan for gaps at the gingival margin.")

        v = np.asarray(m.vertices, dtype=float)
        f = np.asarray(m.faces, dtype=int)
        normals = np.asarray(m.face_normals, dtype=float)
        cx, cy = float(v[:, 0].mean()), float(v[:, 1].mean())

        # Same convention as the retired procedural arch: the interior of the U
        # is the y > curve side, so the outward normal is (2ax + b, -1).
        ey = np.array([2 * a * cx + b, -1.0, 0.0])
        ey /= np.linalg.norm(ey)
        ez = np.array([0.0, 0.0, 1.0])
        ex = np.cross(ey, ez)             # right-handed: ex x ey = ez

        # Arrow origin: the middle of the occlusal surface, taken from the
        # upward-facing faces only. An axis-aligned box top would land on
        # whichever cusp happens to be highest.
        up = normals[:, 2] > 0.5
        if up.any():
            patch = v[np.unique(f[up])]
            apex = (float(patch[:, 0].mean()), float(patch[:, 1].mean()),
                    float(patch[:, 2].max()))
        else:
            apex = (cx, cy, float(v[:, 2].max()))

        sil = decimate_ring(convex_hull_2d(v[:, :2]), 20)
        buccal = max(float((p[0] - cx) * ey[0] + (p[1] - cy) * ey[1]) for p in sil)

        teeth.append({
            "palmer": number, "ttype": TOOTH_TYPE[number],
            "center": (cx, cy, 0.0),
            "frame": (tuple(ex), tuple(ey), tuple(ez)),
            "apex": apex, "height": float(v[:, 2].max()),
            "label_anchor": (cx + ey[0] * (buccal + label_gap),
                             cy + ey[1] * (buccal + label_gap), 0.0),
            "silhouette": [(x, y, 0.0) for x, y in sil],
            "verts": [tuple(p) for p in v],
            "tris": [tuple(int(i) for i in tri) for tri in f],
            "normals": [tuple(n) for n in normals],
        })
    return teeth


def derive_unit(meshes):
    """The size unit every glyph is a multiple of, from real tooth spacing.

    Not the mean crown bounding box: posterior teeth sit rotated most of a right
    angle to the arch, so their x-extent is bucco-lingual width, not mesio-distal
    width, and averaging the two is meaningless.

    Instead invert the layout the procedural arch used. Adjacent crown centres
    there sat `unit * (w_i + w_j) / 2 + unit * gap` apart, so summing the real
    centre-to-centre distances and dividing by the same expression recovers the
    same `unit` from measured geometry.
    """
    _, np = need_trimesh()
    present = [n for n in LOWER_ARCH_ORDER if n in meshes]
    centres = [meshes[n].vertices[:, :2].mean(axis=0) for n in present]
    span = sum(float(np.linalg.norm(b - a))
               for a, b in zip(centres, centres[1:]))

    widths = [MD_WIDTH[TOOTH_TYPE[n]] for n in present]
    divisor = (sum(widths) - (widths[0] + widths[-1]) / 2.0
               + TOOTH_GAP_FRAC * (len(present) - 1))
    unit = span / divisor

    ratio = unit / PROCEDURAL_UNIT
    print(f"  unit = {unit:.5f}  ({ratio:.2f}x the arch this view was tuned against)")
    if not 0.8 <= ratio <= 1.25:
        raise SystemExit(
            f"unit is {ratio:.2f}x the value every glyph constant in "
            "arch_tab.py was tuned for -- arrows and rings would be sized "
            "wrong.\nThis almost always means the orientation or the tooth "
            "numbering is off; check the --report output before overriding.")
    return unit


def crowns_up(meshes):
    """How many teeth have their wide end at +z, and how many at -z.

    The determinant of `world_transform` cannot answer this: `e_z` is built as
    `e_x x e_y`, so the basis is right-handed by construction whatever the scan
    does. The geometry answers it instead -- a crown is broader than the root
    below it, so the occlusal end is the end with the larger footprint. That is
    a fact about teeth rather than about the export, which is what makes it a
    test and not a restatement of the code above.
    """
    _, np = need_trimesh()
    up = down = 0
    for m in meshes.values():
        v = np.asarray(m.vertices, dtype=float)
        z = v[:, 2]
        lo, hi = z.min(), z.max()
        band = 0.15 * (hi - lo)

        def footprint(sel):
            p = v[sel]
            return ((p[:, 0].max() - p[:, 0].min())
                    * (p[:, 1].max() - p[:, 1].min()))

        if footprint(z >= hi - band) >= footprint(z <= lo + band):
            up += 1
        else:
            down += 1
    return up, down


def report(paths, manifest):
    """Print what the scans look like without deciding anything about them."""
    trimesh, np = need_trimesh()
    print("=" * 100)
    print(f"{len(paths)} tooth meshes")
    print("=" * 100)
    print(f"\n{'tooth':>6} {'type':<16} {'faces':>8} {'extent (file units)':>28} {'watertight':>11}")
    print("-" * 100)
    centroids = {}
    for number in LOWER_ARCH_ORDER:
        if number not in paths:
            continue
        m = trimesh.load(paths[number], force="mesh")
        centroids[number] = m.centroid
        ext = m.bounds[1] - m.bounds[0]
        print(f"{number:>6} {TOOTH_TYPE[number]:<16} {len(m.faces):8d} "
              f"{ext[0]:8.2f} {ext[1]:8.2f} {ext[2]:8.2f} {str(m.is_watertight):>11}")

    missing = [n for n in LOWER_ARCH_ORDER if n not in paths]
    print(f"\nmissing teeth: {missing or 'none'}"
          "   (a missing tooth renders as a dashed footprint, which is fine "
          "for one that is genuinely absent)")

    R = world_transform(centroids, manifest.get("flip_z", False),
                        manifest.get("mirror_z", False))
    print("\nOrientation derived from the numbering (rows = world axes in scan coords):")
    for name, row in zip(("+x  right ", "+y  posterior", "+z  occlusal"), R):
        print(f"  {name}  [{row[0]:7.4f} {row[1]:7.4f} {row[2]:7.4f}]")
    oriented = {}
    for number in centroids:
        m = trimesh.load(paths[number], force="mesh")
        m.vertices = m.vertices @ R.T
        oriented[number] = m
    up, down = crowns_up(oriented)
    verdict = ("crowns occlusal, as expected" if up > down
               else "CROWNS APICAL: +z points into the roots")
    print(f"\n  crown check = {up} up / {down} down   ({verdict})")
    if up <= down:
        print('  -> a mirrored export: set "mirror_z": true in '
              "tools/arch_bake.json and re-run --report.")
        print("  -> the one thing that looks identical from here is LL and LR "
              'filenames swapped, which wants "flip_z": true instead. Only you '
              "know which the scan is.")

    pts = np.array([centroids[n] for n in centroids]) @ R.T
    print(f"\n  arch spans x {pts[:,0].min():.2f}..{pts[:,0].max():.2f}, "
          f"y {pts[:,1].min():.2f}..{pts[:,1].max():.2f} in scan units")
    print("\nNothing was written. Re-run without --report to bake.")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stl-dir", required=True, help="directory of per-tooth STLs")
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST,
                    help="filename->tooth overrides and orientation fixes")
    ap.add_argument("--out", default="graphDash/assets/arch_mesh",
                    help="asset base path, without extension")
    ap.add_argument("--budget", type=int, default=300,
                    help="max triangles per crown after decimation (default 300; "
                         "take the real number from tests/bench_arch.py --scaling "
                         "run on the Pi)")
    ap.add_argument("--allow-missing", action="store_true",
                    help="bake even if some teeth have no mesh; they vanish "
                         "from the arch entirely rather than showing as a "
                         "footprint, since a footprint needs an outline too")
    ap.add_argument("--report", action="store_true",
                    help="inspect the scans and print diagnostics; write nothing")
    args = ap.parse_args()

    manifest = {}
    if os.path.exists(args.manifest):
        with open(args.manifest) as fh:
            manifest = json.load(fh)

    paths = discover(args.stl_dir, manifest.get("teeth", {}))
    if args.report:
        return report(paths, manifest)

    missing = [n for n in LOWER_ARCH_ORDER if n not in paths]
    if missing and not args.allow_missing:
        raise SystemExit(
            f"no mesh supplied for teeth {missing}.\n"
            "The arch draws sixteen teeth; one with no mesh has no outline "
            "either, so it disappears from the view and its label with it.\n"
            "Supply the meshes, or pass --allow-missing to accept the gap.")

    trimesh, np = need_trimesh()
    meshes = load_and_orient(paths, manifest)

    centroids = [m.vertices.mean(axis=0) for _, m in sorted(meshes.items())]
    curve = arch_curve(centroids)

    unit = derive_unit(meshes)

    teeth = build_teeth(meshes, args.budget, curve, unit, unit * 0.45)

    # Occlusal guide: the fitted curve, sampled across the arch's own span.
    allv = np.vstack([m.vertices for m in meshes.values()])
    x0, x1 = float(allv[:, 0].min()), float(allv[:, 0].max())
    a, b, c = curve
    guide = [(x0 + (x1 - x0) * i / 60.0,
              float(a * (x0 + (x1 - x0) * i / 60.0) ** 2
                    + b * (x0 + (x1 - x0) * i / 60.0) + c), 0.0)
             for i in range(61)]

    # Fit hull, not every vertex: the view fits the pane each frame, and a
    # projected bounding box is decided entirely by extreme points.
    hull = trimesh.points.PointCloud(allv).convex_hull
    fit_hull = [tuple(float(v) for v in p) for p in hull.vertices]

    json_path, bin_path = arch_asset.write_asset(
        args.out, unit, guide, fit_hull, teeth)

    tris = sum(len(t["tris"]) for t in teeth)
    print(f"  {len(teeth)} teeth, {tris} triangles "
          f"({tris // max(1, len(teeth))} per crown), fit hull {len(fit_hull)} pts")
    print(f"  wrote {json_path} ({os.path.getsize(json_path)} B)")
    print(f"  wrote {bin_path} ({os.path.getsize(bin_path)} B)")
    print("\nNow LOOK at it before trusting it:")
    print("  python3 tests/test_arch_model.py")
    print("  QT_QPA_PLATFORM=offscreen python3 tests/render_arch.py --out /tmp/arch_stl")
    print("Check the sweep sheet for inside-out crowns, and confirm +y points")
    print("buccal and +z out of the tooth against the physical brackets.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
