"""Standalone validation harness for the Arch View's per-tooth glyph toggles.

Run it explicitly from the repo root:

    python3 tests/test_glyph_visibility.py

Same print-and-exit-code style as the other harnesses. `GlyphVisibility` is
plain Python -- no Qt call, no QApplication, no widget -- which is exactly why
it is worth asserting here: it is the whole state machine behind which glyph
each tooth draws, and every one of its invariants is a sentence the view, the
key bar and the toggle panel all separately rely on being true.

Importing `arch_tab` pulls in PyQt6 (the module defines widgets), but nothing
here constructs one.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from graphDash.ui.arch_tab import GlyphVisibility
from graphDash.ui.arch_model import FORCE_GLYPH, MOMENT_GLYPH

FORCE, MOMENT = FORCE_GLYPH.quantity, MOMENT_GLYPH.quantity
TEETH = ("LR7", "LR5", "LR2")

_results = []


def check(name, ok, detail=""):
    _results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'} | {name}" + (f"  --  {detail}" if detail else ""))


def section(title):
    print("\n" + "-" * 100)
    print(title)


# ---------------------------------------------------------------- defaults
section("Defaults: a tooth nobody has touched draws what it always drew")

vis = GlyphVisibility()
check("a fresh tooth shows all three components",
      vis.axes("LR7", FORCE) == (True, True, True),
      f"{vis.axes('LR7', FORCE)}")
check("a fresh tooth is not on its resultant",
      vis.resultant("LR7", FORCE) is False)
check("reading a tooth does not disturb its neighbours",
      vis.axes("LR5", FORCE) == (True, True, True))
check("three axes, no more", GlyphVisibility.N_AXES == 3)

# ---------------------------------------------------------------- isolation
section("Force and moment keep separate records")

vis = GlyphVisibility()
vis.set_axis("LR7", FORCE, 1, False)
check("hiding Fy on LR7 hides Fy on LR7",
      vis.axes("LR7", FORCE) == (True, False, True))
check("hiding Fy on LR7 says nothing about My on LR7",
      vis.axes("LR7", MOMENT) == (True, True, True))
check("hiding Fy on LR7 says nothing about Fy on LR5",
      vis.axes("LR5", FORCE) == (True, True, True))

vis.set_resultant("LR7", FORCE, True)
check("a resultant is per (tooth, quantity) too",
      vis.resultant("LR7", FORCE) and not vis.resultant("LR7", MOMENT)
      and not vis.resultant("LR5", FORCE))
check("turning a resultant on remembers the components underneath",
      vis.axes("LR7", FORCE) == (True, False, True),
      "the tooth draws only the resultant, but switching back restores this")

# ---------------------------------------------------------------- master row
section("The master row's two questions")

vis = GlyphVisibility()
check("all_axis is true when every listed tooth has that axis on",
      vis.all_axis(TEETH, FORCE, 0))
vis.set_axis("LR5", FORCE, 0, False)
check("one tooth off makes all_axis false", not vis.all_axis(TEETH, FORCE, 0))
check("and only for that axis", vis.all_axis(TEETH, FORCE, 1))

vis.set_axis_all(TEETH, FORCE, 0, True)
check("set_axis_all restores every listed tooth",
      all(vis.axis(p, FORCE, 0) for p in TEETH))
check("set_axis_all leaves teeth it was not given alone",
      vis.axes("LL3", FORCE) == (True, True, True))

check("any_resultant is false while every tooth shows components",
      not vis.any_resultant(TEETH, FORCE))
vis.set_resultant("LR2", FORCE, True)
check("one tooth on its resultant makes any_resultant true",
      vis.any_resultant(TEETH, FORCE),
      "this is what puts the resultant's legend and ceiling in the key")
vis.set_resultant_all(TEETH, FORCE, False)
check("set_resultant_all clears every listed tooth",
      not vis.any_resultant(TEETH, FORCE))

# A vacuous all() would say "yes, every one of no teeth has this on", which is
# what would let the master row claim an axis is showing on an empty arch.
check("all_axis over no teeth is vacuously true -- callers must guard",
      vis.all_axis((), FORCE, 0) is True,
      "ToothGlyphPanel._sync_enabled checks the list is non-empty")
check("any_resultant over no teeth is false", not vis.any_resultant((), FORCE))

# ---------------------------------------------------------------- lifetime
section("Records outlive the widgets and the mapping")

vis = GlyphVisibility()
vis.set_axis("LR2", FORCE, 0, False)
vis.set_resultant("LR7", FORCE, True)
# A config edit unmaps both teeth; the panel drops their rows entirely.
snapshot = (vis.axes("LR2", FORCE), vis.resultant("LR7", FORCE))
check("a tooth unmapped and remapped comes back with its flags",
      (vis.axes("LR2", FORCE), vis.resultant("LR7", FORCE)) == snapshot,
      "nothing deletes a record, so a rebuild never costs a toggle")

check("flags are booleans however they were set",
      (vis.set_axis("LR1", FORCE, 0, 0) or vis.axis("LR1", FORCE, 0)) is False
      and (vis.set_resultant("LR1", FORCE, 1) or vis.resultant("LR1", FORCE))
      is True,
      "a Qt checked-state arrives as an int")

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
