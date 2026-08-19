import os

import yaml

from graphDash.protocol import DummySensor


def load_sensor_config(path):
    if not os.path.exists(path):
        return None
    with open(path, 'r') as file:
        return yaml.safe_load(file)


def save_sensor_config(path, sensors):
    ordered = []
    for s in sensors:
        entry = {}
        entry["name"] = s.get("name", "")
        entry["bus"] = s.get("bus", 0)
        entry["dev"] = s.get("dev", 0)
        entry["csb_gpio"] = s.get("csb_gpio", 0)
        if s.get("tooth") is not None:
            entry["tooth"] = s["tooth"]
        if s.get("tooth_type"):
            entry["tooth_type"] = s["tooth_type"]
        ordered.append(entry)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        yaml.dump({"sensors": ordered}, f, default_flow_style=False, sort_keys=False)


def build_simulation_cells(names):
    cells = []
    for i, name in enumerate(names):
        cells.append(DummySensor(
            name,
            amplitude=1.0 + 0.1 * i,
            frequency=0.2 + 0.05 * i,
            phase=i * 0.7,
        ))
    return cells


def try_init_sensors(sensor_configs):
    """Attempt to construct + init a Sensor for each config entry.

    Returns (results, all_ok) where results is a list of dicts, one per input
    row, each with keys: {"config", "cell", "ok", "error"}. `cell` is the
    initialized Sensor on success, None on failure. Callers can filter for
    ok==True to get the working cells.

    Never raises for hardware/wiring issues — the whole point is to surface
    those in the config UI. It does still raise if the config itself is
    malformed (missing required keys, non-int fields, etc.), because that's
    a bug the user needs to see, not a per-cell failure.
    """
    from graphDash.protocol import Sensor

    results = []
    all_ok = True
    for cfg in sensor_configs:
        entry = {"config": cfg, "cell": None, "ok": False, "error": ""}
        cell = None
        try:
            cell = Sensor(cfg["name"], cfg["bus"], cfg["dev"], cfg["csb_gpio"])
            cell.init()
            entry["cell"] = cell
            entry["ok"] = True
        except Exception as exc:
            entry["error"] = str(exc) or exc.__class__.__name__
            all_ok = False
            if cell is not None:
                try:
                    cell.stop()
                except Exception:
                    pass
        results.append(entry)
    return results, all_ok
