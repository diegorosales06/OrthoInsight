import threading

FORCE_THRESHOLD = 0.3  # N

TOOTH_TYPES = ("central_incisor", "premolar", "molar")

# Position vector r = [rx, ry, rz] and user-defined constant w.
DEFAULTS = {
    "central_incisor": {"rx": 0.0, "ry": 8.2, "rz": 17.89,"w": 2.298},
    "premolar":        {"rx": 0.0, "ry": 9.5, "rz": 14.9,  "w": 4.7},
    "molar":           {"rx": 0.0, "ry": 9.5, "rz": 15.4,  "w": 4.7},
}


class PositionVectors:
    """Thread-safe store for per-tooth-type position vectors."""

    def __init__(self):
        self._lock = threading.Lock()
        self._vectors = {tt: dict(DEFAULTS[tt]) for tt in TOOTH_TYPES}

    def get(self, tooth_type):
        with self._lock:
            return dict(self._vectors[tooth_type])

    def set(self, tooth_type, **kwargs):
        with self._lock:
            for key, val in kwargs.items():
                if key in self._vectors[tooth_type]:
                    self._vectors[tooth_type][key] = val

    def reset_defaults(self, tooth_type):
        with self._lock:
            self._vectors[tooth_type] = dict(DEFAULTS[tooth_type])


class TareOffsets:
    """Thread-safe per-cell 6-axis offset subtracted from raw readings
    before compensation runs."""

    def __init__(self, n_cells):
        self._lock = threading.Lock()
        self._offsets = [[0.0] * 6 for _ in range(n_cells)]

    def get(self, cell_idx):
        with self._lock:
            if 0 <= cell_idx < len(self._offsets):
                return list(self._offsets[cell_idx])
            return [0.0] * 6

    def set(self, cell_idx, offset):
        with self._lock:
            if 0 <= cell_idx < len(self._offsets):
                self._offsets[cell_idx] = [float(v) for v in offset][:6]

    def clear(self, cell_idx):
        with self._lock:
            if 0 <= cell_idx < len(self._offsets):
                self._offsets[cell_idx] = [0.0] * 6


def compute_adjusted(raw, tooth_type, pos_vectors):
    """Apply force/moment compensation based on threshold conditions.

    raw: [Fx, Fy, Fz, Mx, My, Mz] from sensor  (Fo and Mo)
    tooth_type: one of TOOTH_TYPES
    pos_vectors: PositionVectors instance

    Returns: [Fxf, Fyf, Fzf, Mxf, Myf, Mzf]

    Position vector r = [rx, ry, rz]; w is a user-defined constant.

    Force cases (Fxf = Fxo, Fyf = Fyo always; Fzf sums deltas from Fzo):
        case 1  (|Fxo| >= 0.3): no change to Fz
        case 2  (|Fyo| >= 0.3): delta_Fz = -(Mxo + Fyo*rz) / w
        case 3.1 (Fzo <= -0.3): delta_Fz = (Mxo - Fzo*w) / (w - ry) - Fzo
        case 3.2 (Fzo >=  0.3): delta_Fz = (Mxo - Fzo*w) / (ry - w) - Fzo
      When cases 2 and 3.x both fire, their deltas are summed.

    Moment cases (Myf = Myo always; Mx and Mz adjusted independently):
        case 1  (|Fxo| >= 0.3): Mzf = Mzo + Fxo*ry     (i.e. Mzo - (-Fxo*ry))
        case 2  (|Fyo| >= 0.3): no change
        case 3  (|Fzo| >= 0.3): Mxf = Mxo - Fzo*ry
    """
    pv = pos_vectors.get(tooth_type)
    rx = pv["rx"]/1000
    ry = pv["ry"]/1000
    rz = pv["rz"]/1000
    w = pv["w"]/1000 # in m
    # rx = pv["rx"]
    # ry = pv["ry"]
    # rz = pv["rz"]
    # w = pv["w"] # in mmk

    fxo, fyo, fzo = raw[0], raw[1], raw[2]
    mxo, myo, mzo = raw[3], raw[4], raw[5]

    # --- Force: Fx, Fy pass through; Fz sums applicable deltas from raw Fzo ---
    fx = fxo
    fy = fyo
    fz = fzo

    # print(rx, ry, rz, w)
   #ry=0.0082
   #rz = 0.014795
   #rz = 0.01789
    # w = 0.002298
    print(f"{ry=}, {rz=}")
    # print(f"{fxo=}, {mxo=}, {rz=}, {ry=}")

    if abs(fyo) >= FORCE_THRESHOLD or abs(fzo) >= FORCE_THRESHOLD: # case 2 and 3
        print("force case 2 and 3")
        fz = (mxo + fyo*rz) / ry
        mxo = -fyo*rz + fz*ry
        # print(f"{fzo=}")
        # print(f"{mxo=}")

    # --- Moment: raw sensor moments with per-component corrections ---
    mx = mxo
    my = myo
    mz = mzo

    if abs(fxo) >= FORCE_THRESHOLD:                       # case 1
        mz = mzo + fxo * ry
        print("moment cast 1")
    if abs(fzo) >= FORCE_THRESHOLD:                       # case 3
        print("moment case 3")
        mx = mxo - fz * ry

    # print(f"{fxo=}, {fyo=}, {fzo=}, {mxo=}, {myo=}, {mzo=}")
    #print(f"{fx=}, {fy=}, {fz=}, {mx=}, {my=}, {mz=}")
    mx, my, mz = mx*1000, my*1000, mz*1000
    return [fx, fy, fz, mx, my, mz]



FORCE_TOL = 0.1
MOMENT_TOL = 0.1 *1000

def run_compensation_tests(pos_vectors):
    """
    Runs compensation validation tests for the central incisor.

    Measured data was taken from the spreadsheet screenshots.
    Expected values are the spreadsheet's corrected values.

    compute_adjusted() returns:
        [Fx, Fy, Fz, Mx, My, Mz]

    Forces are in N.
    Moments are returned in N*mm.
    """

    TOOTH_TYPE = "central_incisor"
    TOLERANCE = 0.1

    tests = [

        # ==========================================================
        # Force Along X Axis
        # Rows corresponding to Applied Force = -4, -3
        # ==========================================================

        {
            "category": "Force Along X Axis",
            "row": 2,
            "measured": [-4.277, -0.038, -0.121,
                         0.00200, -0.05865, 0.03005],
            "expected": [-4.277, -0.038, -0.121,
                         2.000, -58.650, -5.020]
        },
        {
            "category": "Force Along X Axis",
            "row": 3,
            "measured": [-3.216, -0.066, -0.057,
                         0.00176, -0.04409, 0.02252],
            "expected": [-3.216, -0.066, -0.057,
                         1.760, -44.090, -3.850]
        },

        # ==========================================================
        # Force Along Y Axis
        # Rows corresponding to Applied Force = -4, -3
        # ==========================================================

        {
            "category": "Force Along Y Axis",
            "row": 2,
            "measured": [-0.021, -3.838, -2.160,
                         0.06448, 0.00159, -0.00167],
            "expected": [-0.021, -3.838, -0.509,
                         68.660, 1.590, -1.670]
        },
        {
            "category": "Force Along Y Axis",
            "row": 3,
            "measured": [-0.012, -2.855, -1.624,
                         0.04863, 0.00131, -0.00161],
            "expected": [-0.012, -2.855, -0.298,
                         51.080, 1.310, -1.610]
        },

        # ==========================================================
        # Force Along Z Axis
        # Rows corresponding to Applied Force = -4, -3
        # ==========================================================

        {
            "category": "Force Along Z Axis",
            "row": 2,
            "measured": [0.090, -0.029, -3.081,
                         -0.03571, -0.00270, -0.00004],
            "expected": [0.090, -0.029, -4.419,
                         0.525, -2.700, -0.040]
        },
        {
            "category": "Force Along Z Axis",
            "row": 3,
            "measured": [0.030, -0.025, -2.343,
                         -0.02676, -0.00230, -0.00016],
            "expected": [0.030, -0.025, -3.318,
                         0.447, -2.300, -0.160]
        },

        # ==========================================================
        # Neg Y Pos Z @ 45 Degrees
        # Rows corresponding to Applied Force = -4, -3
        # ==========================================================

        {
            "category": "Neg Y Pos Z @ 45 Degrees",
            "row": 2,
            "measured": [0.214, -2.556, 1.019,
                         0.06456, 0.00193, -0.00061],
            "expected": [0.214, -2.556, 3.295,
                         37.543, 1.930, -0.610]
        },
        {
            "category": "Neg Y Pos Z @ 45 Degrees",
            "row": 3,
            "measured": [0.165, -1.929, 0.752,
                         0.04830, 0.00046, -0.00039],
            "expected": [0.165, -1.929, 2.434,
                         28.337, 0.460, -0.390]
        }
    ]

    labels = ["Fx", "Fy", "Fz", "Mx", "My", "Mz"]

    passed = 0

    print("\n" + "=" * 100)
    print("CENTRAL INCISOR COMPENSATION TEST RESULTS")
    print("=" * 100)

    for idx, test in enumerate(tests, start=1):

        actual = compute_adjusted(
            test["measured"],
            TOOTH_TYPE,
            pos_vectors
        )

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

        for label, actual_val, expected_val, passed_component in zip(
                labels,
                actual,
                test["expected"],
                component_results):

            diff = actual_val - expected_val

            print(
                f"  {label:<2} | "
                f"actual={actual_val:>10.4f} | "
                f"expected={expected_val:>10.4f} | "
                f"diff={diff:>10.4f} | "
                f"{'PASS' if passed_component else 'FAIL'}"
            )

    print("\n" + "=" * 100)
    print(
        f"SUMMARY: {passed}/{len(tests)} TESTS PASSED "
        f"(Tolerance = ±{TOLERANCE})"
    )
    print("=" * 100)

run_compensation_tests(DEFAULTS)