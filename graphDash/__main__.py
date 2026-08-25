import argparse
import sys

import pyqtgraph as pg
from PyQt6.QtWidgets import QApplication

from graphDash import session_manager
from graphDash.ui import theme
from graphDash.config import (
    load_sensor_config, save_sensor_config, build_simulation_cells, default_sensor,
)
from graphDash.datastore import DataStore
from graphDash.csv_logger import CSVLogger
from graphDash.force_moment import PositionVectors, TareOffsets
from graphDash.sampler import Sampler
from graphDash.ui.dashboard import Dashboard
from graphDash.ui.startup_config import StartupConfigDialog


def _initial_sensor_configs(config, args):
    """Get the sensor list to seed the startup dialog with.

    Uses whatever's in sensors.yaml if present. In debug mode, pads or trims
    to `--cells` (default 1) so users get a usable starting point on a
    freshly cloned repo.
    """
    sensors = []
    if config and isinstance(config.get('sensors'), list):
        sensors = [dict(s) for s in config['sensors']]

    if args.debug:
        n_cells = max(1, args.cells) if args.cells is not None else max(1, len(sensors))
        if len(sensors) < n_cells:
            for i in range(len(sensors), n_cells):
                sensors.append(default_sensor(i))
        else:
            sensors = sensors[:n_cells]
    return sensors


def main(argv=None):
    parser = argparse.ArgumentParser(description="Load Cell Dashboard")
    parser.add_argument(
        "--debug", action="store_true",
        help="Run the UI with simulated data instead of real sensors")
    parser.add_argument(
        "--cells", type=int, default=None,
        help="Number of simulated cells when running in debug mode")
    parser.add_argument(
        "--config", default="config/sensors.yaml",
        help="Sensor configuration YAML file")
    args = parser.parse_args(argv)

    app = QApplication([sys.argv[0]])
    pg.setConfigOptions(antialias=True, background=theme.PLOT_BG, foreground=theme.PLOT_FG)
    theme.apply(app)

    config = load_sensor_config(args.config)
    initial_sensors = _initial_sensor_configs(config, args)

    # Persist any padding/trimming done for debug mode so the dialog and disk
    # start in sync.
    if args.debug and initial_sensors:
        save_sensor_config(args.config, initial_sensors)

    dialog = StartupConfigDialog(
        initial_sensors, args.config, initial_debug=args.debug)
    if dialog.exec() != dialog.DialogCode.Accepted or dialog.result_mode is None:
        sys.exit(0)

    simulate = (dialog.result_mode == "debug")
    sensor_configs = dialog.sensor_configs
    cells = dialog.ready_cells if not simulate else []

    if not sensor_configs:
        print("No sensors configured. Exiting.")
        sys.exit(1)

    names = [s.get("name", f"Cell {i+1}") for i, s in enumerate(sensor_configs)]
    teeth = [s.get("tooth") for s in sensor_configs]
    tooth_types = [s.get("tooth_type") for s in sensor_configs]
    n_cells = len(sensor_configs)

    # compute_adjusted() only runs for cells that declare a tooth_type; without
    # one the sampler stores/logs tared raw readings instead. Say so loudly —
    # the skip is otherwise silent and looks like broken compensation.
    missing_types = [names[i] for i, tt in enumerate(tooth_types) if not tt]
    if missing_types:
        print(f"WARNING: no tooth_type set for {', '.join(missing_types)} — "
              f"force/moment compensation is SKIPPED for these cells; their "
              f"graphed and logged values are tared raw readings.")

    if simulate:
        simulation_cells = build_simulation_cells(names)
        print(f"Starting in debug mode with {len(simulation_cells)} simulated cells.")
    else:
        simulation_cells = build_simulation_cells(names)
        print(f"Starting with {len(cells)} hardware cells.")

    store = DataStore(n_cells)

    session_manager.init_db()
    session_manager.prune_old_sessions()

    csv_logger = CSVLogger()
    csv_logger.start()

    pos_vectors = PositionVectors()
    tare_offsets = TareOffsets(n_cells)

    sampler = Sampler(cells, store, simulation_cells=simulation_cells, simulate=simulate,
                      csv_logger=csv_logger, cell_names=names,
                      pos_vectors=pos_vectors, cell_tooth_types=tooth_types,
                      tare_offsets=tare_offsets)
    sampler.start()

    win = Dashboard(sampler, store, n_cells, csv_logger=csv_logger,
                    tooth_per_cell=teeth, pos_vectors=pos_vectors,
                    config_path=args.config, sensor_configs=sensor_configs,
                    tare_offsets=tare_offsets)
    win.show()

    exit_code = app.exec()

    sampler._stop = True
    if csv_logger:
        csv_logger.stop()
    for c in cells:
        c.stop()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
