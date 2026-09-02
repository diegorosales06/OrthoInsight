import os
from dataclasses import dataclass
from typing import Callable

import yaml

from graphDash.protocol import DummySensor


# ---------------------------------------------------------------------------
# Sensor-config schema — the single source of truth for the shape of one
# sensor row: its fields, their order, defaults, coercion, and which fields
# are optional (omitted when empty). Everything that reads, writes, edits, or
# seeds a sensor config drives off SENSOR_FIELDS instead of re-listing the
# field set: save_sensor_config() serializes with it, default_sensor() seeds
# with it, and the UI editor builds its columns / parses its table with it.
# Add a field here and it flows to all of them.
# ---------------------------------------------------------------------------

def _to_int(raw, default=0):
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def _to_palmer_or_none(raw):
    """A Palmer designation (`LL5`, `LR2`, ...) for the editor's Tooth cell.

    Delegates to `arch_model.normalize_tooth`, which is also what the arch view
    maps cells through -- so a value the editor accepts is exactly a value the
    arch can draw, and there is no second opinion about which teeth exist. A
    Universal number from a config written before the migration still parses.

    Imported inside the function: `arch_model` still carries the retired
    procedural geometry and its `QPainterPath` import with it, and loading a
    sensor config should not require Qt.
    """
    from graphDash.ui.arch_model import normalize_tooth
    return normalize_tooth(raw)


@dataclass(frozen=True)
class SensorField:
    key: str            # dict key / YAML key
    label: str          # column header in the editor table
    default: object     # value for a fresh row (required fields) / empty marker
    optional: bool      # optional fields are omitted from YAML when empty
    widget: str         # "text" or "choice" — how the editor renders/reads it
    parse: Callable     # raw cell value -> stored value (None => drop, if optional)


SENSOR_FIELDS = (
    SensorField("name",       "Name",       "",   False, "text",   lambda r: "" if r is None else str(r)),
    SensorField("bus",        "Bus",        0,    False, "text",   _to_int),
    SensorField("dev",        "Dev",        0,    False, "text",   _to_int),
    SensorField("csb_gpio",   "CSB GPIO",   0,    False, "text",   _to_int),
    SensorField("tooth",      "Tooth",      None, True,  "text",   _to_palmer_or_none),
    SensorField("tooth_type", "Tooth Type", None, True,  "choice", lambda r: r or None),
)


def default_sensor(index):
    """A fresh sensor-config row with schema defaults. `index` is 0-based;
    the row is named "Cell <index+1>". Optional fields are left unset."""
    row = {f.key: f.default for f in SENSOR_FIELDS if not f.optional}
    row["name"] = f"Cell {index + 1}"
    return row


def serialize_sensors(sensors):
    """Project each sensor dict onto the schema: required fields always
    present (in schema order), optional fields dropped when empty."""
    ordered = []
    for s in sensors:
        entry = {}
        for f in SENSOR_FIELDS:
            val = s.get(f.key, f.default)
            if f.optional:
                if val is None or val == "":
                    continue
                entry[f.key] = val
            else:
                entry[f.key] = f.default if val is None else val
        ordered.append(entry)
    return ordered


def load_sensor_config(path):
    if not os.path.exists(path):
        return None
    with open(path, 'r') as file:
        return yaml.safe_load(file)


def save_sensor_config(path, sensors):
    ordered = serialize_sensors(sensors)
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
    from graphDash import csb as csb_bank
    from graphDash.protocol import Sensor

    # Park every CSB line high BEFORE any cell is initialized. All connectors
    # share one MISO net and a Conv.BD only releases it while its own CSB is
    # high, so a not-yet-claimed pin (GPIO 13/19/26 power up pulled *down*)
    # would jam the bus for whichever cell we're initializing. See csb.py.
    csb_bank.park_all(
        cfg["csb_gpio"] for cfg in sensor_configs if cfg.get("csb_gpio") is not None
    )

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
