"""On-disk format for the baked arch mesh: reader, writer, and the spec itself.

The Pi never sees an STL. `tools/bake_arch_mesh.py` does the expensive work --
loading scans, decimating to a triangle budget, deriving each tooth's sensor
frame -- on a laptop with trimesh and numpy, and writes the result here. The
runtime then loads it with **stdlib only** (`json` + `array`), which is what
keeps the Arch View free of the GL and array dependencies the Pi image doesn't
carry.

Reader and writer live in the same module on purpose: they are one format, and
splitting them across the tool and the app is how the two silently drift apart.

Layout -- two files that travel together:

`<name>.json`   everything small and worth reading by eye: format version, the
                arch `unit`, the occlusal guide polyline, the fit hull, and per
                tooth its Palmer designation, type, centre, frame, apex,
                label anchor,
                height, flat silhouette, and the offsets/counts of its blocks in
                the binary.

`<name>.bin`    the bulk arrays, little-endian, three blocks per tooth:
                  verts    nv * 3  float32   world xyz
                  indices  nt * 3  uint32    triangle corners into verts
                  normals  nt * 3  float32   outward unit normal per triangle

Normals are baked rather than derived at load time because the renderer culls
against them every frame and a cross product per triangle per frame is exactly
the kind of cost this view cannot afford.
"""

import array
import json
import os
import sys

FORMAT_VERSION = 2      # v2: teeth are keyed by Palmer designation, not Universal number

# Type codes whose width we assert below -- `array`'s sizes are platform
# defined, and a silent 8-byte 'f' would desynchronise every offset.
_F32, _U32 = "f", "I"
assert array.array(_F32).itemsize == 4, "expected 4-byte float32"
assert array.array(_U32).itemsize == 4, "expected 4-byte uint32"

_NEEDS_SWAP = sys.byteorder != "little"


def _pack(typecode, values):
    a = array.array(typecode, values)
    if _NEEDS_SWAP:
        a.byteswap()
    return a.tobytes()


def _unpack(typecode, blob, offset, count):
    a = array.array(typecode)
    a.frombytes(blob[offset:offset + count * a.itemsize])
    if len(a) != count:
        raise ValueError(f"asset truncated: wanted {count} items at {offset}")
    if _NEEDS_SWAP:
        a.byteswap()
    return a


def _triples(flat):
    it = iter(flat)
    return tuple(zip(it, it, it))


def paths(base):
    """(json_path, bin_path) for an asset named without an extension."""
    return f"{base}.json", f"{base}.bin"


def write_asset(base, unit, guide, fit_hull, teeth):
    """Write an asset. Pure stdlib, so the bake tool and the app agree by
    construction.

    `teeth` is a sequence of dicts with keys: palmer, ttype, center, frame,
    apex, label_anchor, height, silhouette, verts, tris, normals -- `tris` being
    integer index triples and `normals` one unit vector per triangle.
    """
    json_path, bin_path = paths(base)
    blob = bytearray()
    entries = []
    for t in teeth:
        nv, nt = len(t["verts"]), len(t["tris"])
        if nt != len(t["normals"]):
            raise ValueError(f"tooth {t['palmer']}: {nt} tris but "
                             f"{len(t['normals'])} normals")
        if any(i >= nv for tri in t["tris"] for i in tri):
            raise ValueError(f"tooth {t['palmer']}: triangle index out of range")

        entry = {
            "palmer": t["palmer"], "ttype": t["ttype"],
            "center": list(t["center"]), "apex": list(t["apex"]),
            "frame": [list(v) for v in t["frame"]],
            "label_anchor": list(t["label_anchor"]),
            "height": float(t["height"]),
            "silhouette": [list(p) for p in t["silhouette"]],
            "vert_count": nv, "tri_count": nt,
            "verts_at": len(blob),
        }
        blob += _pack(_F32, [c for v in t["verts"] for c in v])
        entry["indices_at"] = len(blob)
        blob += _pack(_U32, [i for tri in t["tris"] for i in tri])
        entry["normals_at"] = len(blob)
        blob += _pack(_F32, [c for n in t["normals"] for c in n])
        entries.append(entry)

    manifest = {
        "format_version": FORMAT_VERSION,
        "unit": float(unit),
        "guide": [list(p) for p in guide],
        "fit_hull": [list(p) for p in fit_hull],
        "teeth": entries,
    }
    os.makedirs(os.path.dirname(os.path.abspath(bin_path)), exist_ok=True)
    with open(bin_path, "wb") as fh:
        fh.write(blob)
    with open(json_path, "w") as fh:
        json.dump(manifest, fh, indent=1, sort_keys=True)
        fh.write("\n")
    return json_path, bin_path


def read_asset(base):
    """Load an asset into plain tuples. Stdlib only -- this is the Pi path.

    Raises a message naming the bake command rather than falling back to
    anything: a silent fallback is how a stale or missing asset ships unnoticed.
    """
    json_path, bin_path = paths(base)
    try:
        with open(json_path) as fh:
            manifest = json.load(fh)
        with open(bin_path, "rb") as fh:
            blob = fh.read()
    except OSError as exc:
        raise RuntimeError(
            f"arch mesh asset missing or unreadable ({exc}).\n"
            f"Re-bake it with:  python3 tools/bake_arch_mesh.py --out {base}"
        ) from exc

    got = manifest.get("format_version")
    if got != FORMAT_VERSION:
        raise RuntimeError(
            f"arch mesh asset is format v{got}, this build reads "
            f"v{FORMAT_VERSION}. Re-bake it with:\n"
            f"  python3 tools/bake_arch_mesh.py --out {base}"
        )

    teeth = []
    for e in manifest["teeth"]:
        nv, nt = e["vert_count"], e["tri_count"]
        verts = _triples(_unpack(_F32, blob, e["verts_at"], nv * 3))
        idx = _triples(_unpack(_U32, blob, e["indices_at"], nt * 3))
        nrm = _triples(_unpack(_F32, blob, e["normals_at"], nt * 3))
        teeth.append({
            "palmer": e["palmer"], "ttype": e["ttype"],
            "center": tuple(e["center"]), "apex": tuple(e["apex"]),
            "frame": tuple(tuple(v) for v in e["frame"]),
            "label_anchor": tuple(e["label_anchor"]),
            "height": e["height"],
            "silhouette": tuple(tuple(p) for p in e["silhouette"]),
            "verts": verts,
            # (i, j, k, normal) -- the shape the renderer iterates, assembled
            # once at load so the paint loop never zips two lists.
            "tris": tuple((i, j, k, n) for (i, j, k), n in zip(idx, nrm)),
        })

    return {
        "unit": manifest["unit"],
        "guide": tuple(tuple(p) for p in manifest["guide"]),
        "fit_hull": tuple(tuple(p) for p in manifest["fit_hull"]),
        "teeth": teeth,
    }
