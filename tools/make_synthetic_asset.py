"""Bake the *procedural* arch into the mesh asset format.

    python3 tools/make_synthetic_asset.py --out graphDash/assets/arch_mesh

This is not the STL baker (`bake_arch_mesh.py` is). It triangulates the extruded
prisms the arch view drew before the mesh rewrite, which buys two things:

* the mesh renderer can be built and proved against a known-good target -- if
  the triangle path reproduces the prism path pixel for pixel, the rewrite is
  correct independently of any question about scan orientation;
* the tests get a fixture that needs no scan data, so `tests/` stays hermetic
  and runnable on a fresh clone.

Requires the procedural geometry in `arch_model` to still exist, so the fixture
it writes is committed rather than regenerated on demand.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from graphDash.ui import arch_asset


def _prism_mesh(tooth):
    """Triangulate one extruded crown: verts, index triples, per-face normals.

    Vertex layout is `base` then `top`, so a base index `i` has its top partner
    at `i + n` -- which keeps the side-wall triangulation readable.
    """
    n = len(tooth.base)
    verts = list(tooth.base) + list(tooth.top)
    tris, normals = [], []

    # Side walls: each quad (base i, base j, top j, top i) split along one
    # diagonal. Both halves share the wall's baked outward normal.
    for i, j, nrm in tooth.edges():
        tris.append((i, j, j + n))
        normals.append(nrm)
        tris.append((i, j + n, i + n))
        normals.append(nrm)

    # Occlusal face: a fan from the first top vertex. The outline is convex, so
    # a fan is safe here -- a real scan is not, and the STL baker keeps whatever
    # triangulation the mesh already has.
    e_z = tooth.e_z
    for k in range(1, n - 1):
        tris.append((n, n + k, n + k + 1))
        normals.append(e_z)

    # Basal face, wound the other way. It is culled at every legal camera pitch,
    # but including it makes the crown a closed surface, which lets the geometry
    # harness assert watertightness instead of special-casing an open shell.
    down = (-e_z[0], -e_z[1], -e_z[2])
    for k in range(1, n - 1):
        tris.append((0, k + 1, k))
        normals.append(down)

    return verts, tris, normals


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="graphDash/assets/arch_mesh",
                    help="asset base path, without extension")
    args = ap.parse_args()

    # Imported here so the failure is a clear message rather than an import-time
    # traceback once the procedural geometry is finally deleted.
    try:
        from graphDash.ui.arch_model import build_procedural_arch as build
    except ImportError:
        from graphDash.ui.arch_model import build_arch as build

    arch = build()
    teeth = []
    for tooth in arch.teeth:
        verts, tris, normals = _prism_mesh(tooth)
        teeth.append({
            "palmer": tooth.palmer, "ttype": tooth.ttype,
            "center": tooth.center, "frame": tooth.frame, "apex": tooth.apex,
            "label_anchor": tooth.label_anchor, "height": tooth.height,
            # The footprint the unmapped teeth draw is the crown's z = 0 ring,
            # which is exactly what the prism was extruded from.
            "silhouette": tuple(tooth.base),
            "verts": verts, "tris": tris, "normals": normals,
        })

    # Every crown vertex, not a reduced hull: this asset exists to reproduce the
    # old fit exactly. The STL baker substitutes a convex hull, which is where
    # the per-frame saving actually comes from.
    fit_hull = [v for t in teeth for v in t["verts"]]

    json_path, bin_path = arch_asset.write_asset(
        args.out, arch.unit, arch.guide, fit_hull, teeth)
    tris = sum(len(t["tris"]) for t in teeth)
    verts = sum(len(t["verts"]) for t in teeth)
    print(f"  {len(teeth)} teeth, {verts} verts, {tris} triangles")
    print(f"  wrote {json_path} ({os.path.getsize(json_path)} B)")
    print(f"  wrote {bin_path} ({os.path.getsize(bin_path)} B)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
