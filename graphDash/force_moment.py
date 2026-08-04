import threading

FORCE_THRESHOLD = 0.3  # N

TOOTH_TYPES = ("central_incisor", "premolar", "molar")

DEFAULTS = {
    "central_incisor": {"x": 0.0, "d_plus_w": 9.5, "h": 17.15, "d": 4.8},
    "premolar":        {"x": 0.0, "d_plus_w": 9.5, "h": 14.9,  "d": 4.8},
    "molar":           {"x": 0.0, "d_plus_w": 9.5, "h": 15.4, "d": 4.8},
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


def compute_adjusted(raw, tooth_type, pos_vectors):
    """Apply force/moment overrides based on threshold conditions.

    raw: [Fx, Fy, Fz, Mx, My, Mz] from sensor
    tooth_type: one of TOOTH_TYPES
    pos_vectors: PositionVectors instance

    Returns: [Fx', Fy', Fz', Mx', My', Mz']

    Position vector r = [x, y, z] where:
        x  = pv["x"]
        y  = pv["d_plus_w"]   (d + w)
        z  = pv["h"]          (h)

    Moment is always computed as r x F (cross product of position vector
    and raw force vector), with specific terms removed when conditions
    are met:
        Condition 1  (|Fx| >= 0.3 N): drop  -y*Fx  from Mz
        Condition 2  (|Fz| >= 0.3 N): drop   y*Fz  from Mx

    Force adjustments (applied to Fz only, both use original raw Fz):
        Condition 3  (|Fy| >= 0.3 N): delta = -(h * Fy) / (d+w)
        Condition 4  (|Fz| >= 0.3 N): delta = +(Fz * (d+w)) / d
    """
    pv = pos_vectors.get(tooth_type)
    x = pv["x"]
    y = pv["d_plus_w"]
    z = pv["h"]
    d = pv["d"]

    raw_fx, raw_fy, raw_fz = raw[0], raw[1], raw[2]

    # --- Moment: r x F = [y*Fz - z*Fy,  z*Fx - x*Fz,  x*Fy - y*Fx] ---
    mx = y * raw_fz - z * raw_fy
    my = z * raw_fx - x * raw_fz
    mz = x * raw_fy - y * raw_fx

    if abs(raw_fx) >= FORCE_THRESHOLD:
        mz = x * raw_fy                   # condition 1: drop -y*Fx

    if abs(raw_fz) >= FORCE_THRESHOLD:
        mx = -z * raw_fy                   # condition 2: drop y*Fz

    # --- Force: adjust Fz (Fx, Fy unchanged) ---
    fx = raw_fx
    fy = raw_fy
    fz = raw_fz

    if abs(raw_fy) >= FORCE_THRESHOLD:     # condition 3
        fz += -(z * raw_fy) / y

    if abs(raw_fz) >= FORCE_THRESHOLD:     # condition 4
        fz += (raw_fz * y) / d

    return [fx, fy, fz, mx, my, mz]
