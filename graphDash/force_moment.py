import threading

FORCE_THRESHOLD = 0.3  # N

TOOTH_TYPES = ("central_incisor", "premolar", "molar")

# Position vector r = [rx, ry, rz] per tooth type (all in mm).
DEFAULTS = {
    "central_incisor": {"rx": 0.0, "ry": 8.2, "rz": 17.89},
    "premolar":        {"rx": 0.0, "ry": 9.5, "rz": 14.9},
    "molar":           {"rx": 0.0, "ry": 9.5, "rz": 15.4},
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

    This docstring describes exactly what the code below does — the live,
    production compensation. Do not change the algorithm to match a prior
    description; if the two ever disagree, the code is authoritative.

    raw: [Fxo, Fyo, Fzo, Mxo, Myo, Mzo] from sensor
         forces in N, moments in N*m.
    tooth_type: one of TOOTH_TYPES
    pos_vectors: PositionVectors instance

    Returns: [Fx, Fy, Fz, Mx, My, Mz]
             forces in N, moments in N*mm (Mx/My/Mz are scaled by 1000 at
             the end).

    Position vector r = [rx, ry, rz] is stored in mm and divided by 1000
    here so all arithmetic runs in metres / N / N*m. rx is unused.

    Force:
        Fx = Fxo, Fy = Fyo always.
        Fz = Fzo, unless |Fyo| >= 0.3 or |Fzo| >= 0.3, in which case:
            Fz  = (Mxo + Fyo*rz) / ry
        The same branch then rewrites the local Mxo in place:
            Mxo = -Fyo*rz + Fz*ry
        (intentional — the moment block below reads this updated Mxo).

    Moment (My = Myo always; Mx and Mz corrected independently):
        Mz = Mzo, then if |Fxo| >= 0.3:  Mz = Mzo + Fxo*ry
        Mx = Mxo, then if |Fzo| >= 0.3:  Mx = Mxo - Fz*ry
        Note Mx uses the possibly-rewritten Mxo and the corrected Fz, so
        whenever the |Fzo| branch fires it reduces to Mx = -Fyo*rz.

    Finally Mx, My, Mz are multiplied by 1000 (N*m -> N*mm).
    """
    pv = pos_vectors.get(tooth_type)
    rx = pv["rx"]/1000
    ry = pv["ry"]/1000
    rz = pv["rz"]/1000

    fxo, fyo, fzo = raw[0], raw[1], raw[2]
    mxo, myo, mzo = raw[3], raw[4], raw[5]

    # --- Force: Fx, Fy pass through; Fz sums applicable deltas from raw Fzo ---
    fx = fxo
    fy = fyo
    fz = fzo

   #ry=0.0082
   #rz = 0.014795
   #rz = 0.01789
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
