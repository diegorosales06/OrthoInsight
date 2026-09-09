"""Validate the Arch View's tooth picking -- click a crown, open its cell.

    QT_QPA_PLATFORM=offscreen python3 tests/test_arch_pick.py

Print-and-exit-code, like the other harnesses here; not a pytest file. It needs
a QApplication (the view is a QWidget) but no display, and no hardware.

What it protects, in order of how badly it breaks if it goes:

* the pick agrees with the pixels -- same projection, same cull, same triangles;
* the cheap reject stage is *sound*, so a click it drops cannot be on a crown;
* unmapped teeth are not targets;
* a drag is not a click.
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import arch_harness as harness           # sets QT_QPA_PLATFORM, fixes sys.path

from PyQt6.QtCore import QPointF, Qt

from graphDash.ui.arch_tab import PRESETS, ZOOM_MIN, ZOOM_MAX, CLICK_SLOP

# `build_view` maps these three, the mix `config/sensors.yaml` carries.
MAPPED = ("LR7", "LR2", "LR5")
# Unmapped, and deliberately NOT LR8. In the shipped bake LR7/LR8 and LL7/LL8
# are not merely identical meshes (the source STLs are byte-identical
# placeholders) but *fully coincident in world space* -- same centre, same apex,
# same verts. So a click on what looks like LR8 resolves to the mapped LR7, and
# that is correct. Do not "fix" it from a test failure.
UNMAPPED = ("LR6", "LR4", "LL3", "LL6")

PASS, FAIL = [], []


def check(name, ok):
    (PASS if ok else FAIL).append(name)
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")


class _Event:
    """The slice of QMouseEvent the view's handlers actually read.

    Constructing a real QMouseEvent needs a global position and a buttons mask
    that add nothing here -- the handlers call `position()` and `button()` and
    nothing else, so a shim exercises the same code with less to get wrong.
    """

    def __init__(self, pos, button=Qt.MouseButton.LeftButton):
        self._pos, self._button = pos, button

    def position(self):
        return self._pos

    def button(self):
        return self._button


def screen_of(view, frame):
    """World -> (x, y, depth) floats, the way `tooth_at` projects."""
    pr, sc, cx, cy = frame.projector, frame.scale_px, frame.cx, frame.cy

    def screen(pt):
        u, v, depth = pr.project(pt)
        return cx + sc * u, cy - sc * v, depth
    return screen


# ---- 1. a mapped tooth's apex picks that tooth, or one in front of it ----

def test_apex_picks_its_tooth(view):
    for preset in range(len(PRESETS)):
        view.set_preset(preset)
        for zoom in (ZOOM_MIN, 1.0, ZOOM_MAX):
            view.zoom = zoom
            frame = view._frame()
            screen = screen_of(view, frame)
            for palmer in MAPPED:
                tooth = view._tooth_by_palmer[palmer]
                pos = frame.point(tooth.apex)
                got = view.tooth_at(pos)
                where = f"{palmer} apex, {PRESETS[preset][0]} zoom {zoom:g}"
                if got == palmer:
                    check(f"{where} -> {palmer}", True)
                    continue
                # Not a failure by itself: the apex of one crown can sit behind
                # another crown, and the nearer one is the right answer. Assert
                # the invariant that actually matters instead of assuming no
                # tooth ever occludes another.
                if got is None:
                    check(f"{where} -> None (expected a crown)", False)
                    continue
                mine = view._crown_depth_at(
                    screen, frame.projector.fwd, tooth, pos.x(), pos.y())
                theirs = view._crown_depth_at(
                    screen, frame.projector.fwd, view._tooth_by_palmer[got],
                    pos.x(), pos.y())
                check(f"{where} -> {got}, which is nearer",
                      theirs is not None and mine is not None
                      and theirs <= mine)
    view.set_preset(0)
    view.zoom = 1.0


# ---- 2. unmapped teeth are not targets ----

def test_unmapped_never_picked(view):
    for preset in range(len(PRESETS)):
        view.set_preset(preset)
        frame = view._frame()
        for palmer in UNMAPPED:
            tooth = view._tooth_by_palmer[palmer]
            got = view.tooth_at(frame.point(tooth.apex))
            check(f"{palmer} (unmapped) apex -> {got}, never an unmapped tooth",
                  got is None or got in view.tooth_to_cell)
        # And nothing anywhere resolves to a tooth with no cell behind it.
        for palmer in UNMAPPED:
            got = view.tooth_at(frame.point(view._tooth_by_palmer[palmer].center))
            check(f"{palmer} (unmapped) centre -> {got}, has a cell or is None",
                  got is None or got in view.tooth_to_cell)
    view.set_preset(0)


# ---- 3. empty canvas picks nothing ----

def test_empty_space(view):
    view.set_preset(0)
    w, h = view.width(), view.height()
    for pos in (QPointF(4, 4), QPointF(w - 4, 4),
                QPointF(4, h - 4), QPointF(w - 4, h - 4)):
        check(f"empty ({pos.x():.0f}, {pos.y():.0f}) -> None",
              view.tooth_at(pos) is None)


# ---- 4. the cheap reject stage is sound ----

def test_bbox_soundness(view):
    """Every projected crown vertex must lie inside the projected 8-corner box.

    This is the property that lets `tooth_at` skip a tooth without looking at a
    single triangle. It is checked over the camera range because that is where a
    perspective map could in principle betray it. A tighter bound here would be
    an unsound one, and this is the check that would catch it.
    """
    escapes = tested = 0
    for yaw in (-90.0, -45.0, 0.0, 45.0, 90.0):
        for pitch in (88.0, 60.0, 30.0, 5.0):
            view.camera.set_orientation(math.radians(yaw), math.radians(pitch))
            frame = view._frame()
            screen = screen_of(view, frame)
            for tooth in view.arch.teeth:
                box = [screen(c) for c in view._tooth_box[tooth.palmer]]
                x0 = min(c[0] for c in box)
                x1 = max(c[0] for c in box)
                y0 = min(c[1] for c in box)
                y1 = max(c[1] for c in box)
                for v in tooth.verts:
                    x, y, _ = screen(v)
                    tested += 1
                    if not (x0 - 1e-6 <= x <= x1 + 1e-6
                            and y0 - 1e-6 <= y <= y1 + 1e-6):
                        escapes += 1
    check(f"bbox contains all {tested} projected verts over 20 poses "
          f"({escapes} escapes)", escapes == 0)
    view.set_preset(0)


# ---- 5. a click is not a drag ----

def _press(view, pos):
    view.mousePressEvent(_Event(pos))


def _move(view, pos):
    view.mouseMoveEvent(_Event(pos))


def _release(view, pos):
    view.mouseReleaseEvent(_Event(pos))


def test_click_vs_drag(view):
    view.set_preset(0)
    view.zoom = 1.0
    frame = view._frame()
    apex = frame.point(view._tooth_by_palmer["LR5"].apex)
    picked = []
    view.tooth_picked.connect(picked.append)

    picked.clear()
    _press(view, apex)
    _release(view, apex)
    check("press+release on a crown emits once, with its cell index",
          picked == [view.tooth_to_cell["LR5"]])

    picked.clear()
    preset_before = view.preset_index
    _press(view, apex)
    _move(view, QPointF(apex.x() + 40, apex.y() + 40))
    _move(view, apex)
    _release(view, apex)
    check("drag out and back emits nothing", picked == [])
    check("drag left the preset", view.preset_index != preset_before)

    view.set_preset(0)
    picked.clear()
    nudge = QPointF(apex.x() + CLICK_SLOP - 1, apex.y())
    _press(view, apex)
    _move(view, nudge)
    _release(view, nudge)
    check("a wobble inside CLICK_SLOP still emits", picked == [
        view.tooth_to_cell["LR5"]])
    check("a wobble inside CLICK_SLOP does not orbit off the preset",
          view.preset_index == 0)

    picked.clear()
    empty = QPointF(4, 4)
    _press(view, empty)
    _release(view, empty)
    check("press+release on empty canvas emits nothing", picked == [])

    # Qt order: Press, Release, DblClick, Release.
    view.set_preset(0)
    picked.clear()
    _press(view, apex)
    _release(view, apex)
    view.mouseDoubleClickEvent(_Event(apex))
    _release(view, apex)
    check("double-click on a crown emits exactly once",
          picked == [view.tooth_to_cell["LR5"]])
    check("double-click on a crown does not reset the camera",
          view.preset_index == 0)

    view.camera.set_orientation(math.radians(30.0), math.radians(40.0))
    view.preset_index = None
    view.mouseDoubleClickEvent(_Event(QPointF(4, 4)))
    check("double-click on empty canvas resets the camera",
          view.preset_index == 0 and view.zoom == 1.0)

    view.tooth_picked.disconnect(picked.append)


# ---- 6/7. the relay, and a live config edit ----

def test_tab_relay_and_config_edit():
    tab = harness.build_tab()
    got = []
    tab.cell_picked.connect(got.append)
    tab.view.tooth_picked.emit(2)
    check("ArchTab relays tooth_picked as cell_picked", got == [2])

    tab.view._hover = "LR7"
    tab.set_tooth_per_cell(["LR6", None, None])
    frame = tab.view._frame()
    check("a config edit clears the hover", tab.view._hover is None)
    check("the unmapped LR7 is no longer pickable",
          tab.view.tooth_at(frame.point(
              tab.view._tooth_by_palmer["LR7"].apex)) is None)
    check("the newly mapped LR6 is pickable",
          tab.view.tooth_at(frame.point(
              tab.view._tooth_by_palmer["LR6"].apex)) == "LR6")


def main():
    harness.app()
    view = harness.build_view()
    view.grab()            # one paint, so the view is in a realistic state

    print("apex picks its own tooth")
    test_apex_picks_its_tooth(view)
    print("unmapped teeth are not targets")
    test_unmapped_never_picked(view)
    print("empty canvas")
    test_empty_space(view)
    print("cheap reject stage is sound")
    test_bbox_soundness(view)
    print("click vs drag")
    test_click_vs_drag(view)
    print("tab relay and live config edit")
    test_tab_relay_and_config_edit()

    total = len(PASS) + len(FAIL)
    print(f"\n{len(PASS)}/{total} TESTS PASSED")
    for name in FAIL:
        print(f"  failed: {name}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
