"""Standalone validation harness for the force/moment compensation.

This lives outside graphDash/force_moment.py on purpose: importing the
production module must have no side effects. Run it explicitly from the
repo root:

    python3 tests/test_compensation.py

It exercises the *live* compute_adjusted() (imported below) against the
central-incisor spreadsheet reference data. Measured values are the raw
sensor readings; expected values are the spreadsheet's corrected outputs.

compute_adjusted() returns [Fx, Fy, Fz, Mx, My, Mz] — forces in N,
moments in N*mm.
"""

import os
import sys

# Allow running as a plain script (`python3 tests/test_compensation.py`) from
# the repo root by putting the repo root — not tests/ — on the import path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from graphDash.force_moment import compute_adjusted, PositionVectors


TOOTH_TYPE = "central_incisor"
TOLERANCE = 0.1  # applied per component, in N and N*mm

TESTS = [
    # ==========================================================
    # Force Along X Axis  (Applied Force = -4, -3)
    # ==========================================================
    {
        "category": "Force Along X Axis",
        "row": 2,
        "measured": [-4.277, -0.038, -0.121, 0.00200, -0.05865, 0.03005],
        "expected": [-4.277, -0.038, -0.121, 2.000, -58.650, -5.020],
    },
    {
        "category": "Force Along X Axis",
        "row": 3,
        "measured": [-3.216, -0.066, -0.057, 0.00176, -0.04409, 0.02252],
        "expected": [-3.216, -0.066, -0.057, 1.760, -44.090, -3.850],
    },
    # ==========================================================
    # Force Along Y Axis  (Applied Force = -4, -3)
    # ==========================================================
    {
        "category": "Force Along Y Axis",
        "row": 2,
        "measured": [-0.021, -3.838, -2.160, 0.06448, 0.00159, -0.00167],
        "expected": [-0.021, -3.838, -0.509, 68.660, 1.590, -1.670],
    },
    {
        "category": "Force Along Y Axis",
        "row": 3,
        "measured": [-0.012, -2.855, -1.624, 0.04863, 0.00131, -0.00161],
        "expected": [-0.012, -2.855, -0.298, 51.080, 1.310, -1.610],
    },
    # ==========================================================
    # Force Along Z Axis  (Applied Force = -4, -3)
    # ==========================================================
    {
        "category": "Force Along Z Axis",
        "row": 2,
        "measured": [0.090, -0.029, -3.081, -0.03571, -0.00270, -0.00004],
        "expected": [0.090, -0.029, -4.419, 0.525, -2.700, -0.040],
    },
    {
        "category": "Force Along Z Axis",
        "row": 3,
        "measured": [0.030, -0.025, -2.343, -0.02676, -0.00230, -0.00016],
        "expected": [0.030, -0.025, -3.318, 0.447, -2.300, -0.160],
    },
    # ==========================================================
    # Neg Y Pos Z @ 45 Degrees  (Applied Force = -4, -3)
    # ==========================================================
    {
        "category": "Neg Y Pos Z @ 45 Degrees",
        "row": 2,
        "measured": [0.214, -2.556, 1.019, 0.06456, 0.00193, -0.00061],
        "expected": [0.214, -2.556, 3.295, 37.543, 1.930, -0.610],
    },
    {
        "category": "Neg Y Pos Z @ 45 Degrees",
        "row": 3,
        "measured": [0.165, -1.929, 0.752, 0.04830, 0.00046, -0.00039],
        "expected": [0.165, -1.929, 2.434, 28.337, 0.460, -0.390],
    },
]

LABELS = ["Fx", "Fy", "Fz", "Mx", "My", "Mz"]


def run_compensation_tests(pos_vectors=None):
    """Run every reference case and print a component-level breakdown.

    Returns the number of passing tests so the process can exit non-zero
    on failure.
    """
    if pos_vectors is None:
        pos_vectors = PositionVectors()

    passed = 0

    print("\n" + "=" * 100)
    print("CENTRAL INCISOR COMPENSATION TEST RESULTS")
    print("=" * 100)

    for idx, test in enumerate(TESTS, start=1):
        actual = compute_adjusted(test["measured"], TOOTH_TYPE, pos_vectors)

        component_results = [
            abs(a - e) <= TOLERANCE
            for a, e in zip(actual, test["expected"])
        ]
        test_pass = all(component_results)
        if test_pass:
            passed += 1

        print("\n" + "-" * 100)
        print(f"Test #{idx}")
        print(f"Category : {test['category']}")
        print(f"Row      : {test['row']}")
        print(f"Measured : {test['measured']}")
        print(f"Returned : {[round(x, 6) for x in actual]}")
        print(f"Expected : {test['expected']}")
        print(f"Result   : {'PASS' if test_pass else 'FAIL'}")

        print("\nComponent Breakdown:")
        for label, actual_val, expected_val, ok in zip(
                LABELS, actual, test["expected"], component_results):
            diff = actual_val - expected_val
            print(
                f"  {label:<2} | "
                f"actual={actual_val:>10.4f} | "
                f"expected={expected_val:>10.4f} | "
                f"diff={diff:>10.4f} | "
                f"{'PASS' if ok else 'FAIL'}"
            )

    print("\n" + "=" * 100)
    print(f"SUMMARY: {passed}/{len(TESTS)} TESTS PASSED (Tolerance = ±{TOLERANCE})")
    print("=" * 100)
    return passed


if __name__ == "__main__":
    n_passed = run_compensation_tests()
    sys.exit(0 if n_passed == len(TESTS) else 1)
