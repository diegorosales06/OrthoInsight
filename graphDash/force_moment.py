import threading

FORCE_THRESHOLD = 0.3  # N

TOOTH_TYPES = ("central_incisor", "premolar", "molar")

# Position vector r = [rx, ry, rz] and user-defined constant w.
DEFAULTS = {
    "central_incisor": {"rx": 0.0, "ry": 8.2, "rz": 17.93, "w": 2.298},
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
    w = pv["w"]/1000

    fxo, fyo, fzo = raw[0], raw[1], raw[2]
    mxo, myo, mzo = raw[3], raw[4], raw[5]

    # --- Force: Fx, Fy pass through; Fz sums applicable deltas from raw Fzo ---
    fx = fxo
    fy = fyo
    fz = fzo

    # print(rx, ry, rz, w)

    # case 2
    if abs(fyo) >= FORCE_THRESHOLD and abs(fzo) >= FORCE_THRESHOLD:
        fz = (mxo + fy*rz) / -ry
    elif abs(fyo) >= FORCE_THRESHOLD or abs(fzo) >= FORCE_THRESHOLD:
        fz = (mxo + fy*rz) / -ry

    # --- Moment: raw sensor moments with per-component corrections ---
    mx = mxo
    # mx = -fy*rz - fz*ry
    my = myo
    mz = mzo

    if abs(fxo) >= FORCE_THRESHOLD:                       # case 1
        mz = mzo + fxo * ry

    if abs(fzo) >= FORCE_THRESHOLD:                       # case 3
        mx = mxo - fzo * ry

    print(f"{fx=}, {fy=}, {fz=}, {mx=}, {my=}, {mz=}")
    mx, my, mz = mx*1000, my*1000, mz*1000
    return [fx, fy, fz, mx, my, mz]
