import argparse
import sys

from PyQt6.QtWidgets import QApplication

from graphDash import session_manager
from graphDash.protocol import Sensor
from graphDash.config import load_sensor_config, build_simulation_cells
from graphDash.datastore import DataStore
from graphDash.csv_logger import CSVLogger
from graphDash.sampler import Sampler
from graphDash.ui.dashboard import Dashboard


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

    config = load_sensor_config(args.config)
    names = []
    teeth = []
    if config and isinstance(config.get('sensors'), list):
        names = [sensor.get('name', f"Cell {i+1}")
                 for i, sensor in enumerate(config['sensors'])]
        teeth = [sensor.get('tooth') for sensor in config['sensors']]

    if args.debug:
        if args.cells is not None:
            n_cells = max(1, args.cells)
            if len(names) < n_cells:
                names += [f"Cell {len(names) + j + 1}"
                          for j in range(n_cells - len(names))]
                teeth += [None] * (n_cells - len(teeth))
            else:
                names = names[:n_cells]
                teeth = teeth[:n_cells]
        elif not names:
            names = ["Cell 1"]
            teeth = [None]
        cells = []
        simulation_cells = build_simulation_cells(names)
        print(f"Starting in debug mode with {len(simulation_cells)} simulated cells.")
    else:
        if not config or 'sensors' not in config:
            raise FileNotFoundError(
                f"Sensor configuration not found at {args.config}. "
                "Use --debug to run without hardware.")
        cells = []
        for sensor in config['sensors']:
            cells.append(Sensor(sensor['name'], sensor['bus'], sensor['dev'], sensor['csb_gpio']))
        for c in cells:
            print(f"Initializing {c.name}...")
            c.init()
        print("All cells ready.\n")
        simulation_cells = build_simulation_cells(names)

    n_cells = len(names) if names else len(cells)
    store = DataStore(n_cells)

    session_manager.init_db()
    session_manager.prune_old_sessions()

    csv_logger = CSVLogger()
    csv_logger.start()

    sampler = Sampler(cells, store, simulation_cells=simulation_cells, simulate=args.debug,
                      csv_logger=csv_logger, cell_names=names)
    sampler.start()

    # Ensure teeth list matches n_cells (defensive; real-hardware path already parallel)
    if len(teeth) < n_cells:
        teeth += [None] * (n_cells - len(teeth))
    else:
        teeth = teeth[:n_cells]

    app = QApplication([sys.argv[0]])
    win = Dashboard(sampler, store, n_cells, csv_logger=csv_logger,
                    tooth_per_cell=teeth)
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
