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
