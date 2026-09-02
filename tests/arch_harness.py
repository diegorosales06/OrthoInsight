"""Shared offscreen scaffolding for the Arch View checks.

Not a test itself -- imported by `bench_arch.py` and `render_arch.py`. Both run
under `QT_QPA_PLATFORM=offscreen`, so neither needs a display:

    QT_QPA_PLATFORM=offscreen python3 tests/bench_arch.py
    QT_QPA_PLATFORM=offscreen python3 tests/render_arch.py

The store here is a *static* stub on purpose. `protocol.DummySensor` drives a
sine wave, so two runs of identical code produce different pixels and a
pixel-diff has a noise floor; static readings diff to exactly zero, which is
what makes "this refactor changed nothing" a provable statement.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# Readings chosen so every axis is above its glyph threshold in both Force and
# Moment mode, with mixed signs -- so the arrows actually get drawn and the
# sign-dependent direction flip is exercised. Force lo/hi = 0.25/3.0 N,
# moment lo/hi = 0.05/75.0 N*mm.
STUB_READINGS = {
    0: [1.40, -0.80, 2.10, 22.0, -48.0, 9.0],
    1: [-0.55, 1.90, -1.25, -61.0, 12.0, -33.0],
    2: [2.60, 0.35, -0.40, 5.0, 70.0, 40.0],
}

# One reading is deliberately below every threshold, and one axis sits exactly
# at a clamp, so the "nothing drawn" and "clamped" paths appear in the sheets.
STUB_READINGS_EDGE = {
    0: [0.10, 0.05, 0.02, 0.01, 0.02, 0.0],       # all under lo: no arrows
    1: [3.0, -3.0, 3.0, 75.0, -75.0, 75.0],       # all at hi: fully clamped
    2: [0.0, 0.0, -2.0, 0.0, 0.0, -60.0],         # axial only: ring glyphs
}


class StubStore:
    """The slice of `DataStore` the arch view actually touches: `n_cells` and
    `latest()`. Values never change, so repeated renders are byte-identical."""

    def __init__(self, readings=None, n_cells=None):
        self.readings = STUB_READINGS if readings is None else readings
        self.n_cells = len(self.readings) if n_cells is None else n_cells

    def latest(self, cell_idx):
        vals = self.readings.get(cell_idx)
        return list(vals) if vals is not None else None


_APP = None


def app():
    """One QApplication for the process, created on demand.

    The reference is parked at module level deliberately: a QApplication that
    only a local holds is garbage collected the moment the function returns,
    and the next QWidget aborts with "Must construct a QApplication first".
    """
    global _APP
    from PyQt6.QtWidgets import QApplication
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


def build_view(store=None, tooth_to_cell=None, scale=None, show_resultant=False,
               size=(900, 620)):
    """An `ArchView3D` sized and ready to `grab()`.

    `tooth_to_cell` defaults to the three teeth `config/sensors.yaml` maps
    (LR7, LR2, LR5), so the harness renders the same mix of mapped crowns and
    unmapped footprints the real rig shows.
    """
    app()
    from graphDash.ui.arch_tab import ArchView3D, FORCE_GLYPH

    view = ArchView3D(
        store if store is not None else StubStore(),
        {'LR7': 0, 'LR2': 1, 'LR5': 2} if tooth_to_cell is None else tooth_to_cell,
        scale=FORCE_GLYPH if scale is None else scale,
        show_resultant=show_resultant,
    )
    view.resize(*size)
    return view


def scene_polygons(view):
    """Polygons the scene would paint before back-face culling.

    A budget figure, not a draw-call count: culling roughly halves it. Works for
    both the prism arch (18 side quads + 1 occlusal face per mapped crown) and a
    triangle-mesh arch, so the number stays comparable across the rewrite.
    """
    total = 0
    for tooth in view.arch.teeth:
        if tooth.palmer not in view.tooth_to_cell:
            total += 1                        # flat dashed footprint
        elif hasattr(tooth, "tris"):
            total += len(tooth.tris)          # mesh arch
        else:
            total += len(tooth.base) + 1      # prism arch: side quads + top
    return total
