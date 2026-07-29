import os

import yaml

from graphDash.protocol import DummySensor


def load_sensor_config(path):
    if not os.path.exists(path):
        return None
    with open(path, 'r') as file:
        return yaml.safe_load(file)


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
