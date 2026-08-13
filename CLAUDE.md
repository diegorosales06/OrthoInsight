# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

OrthoInsight reads force/moment (Fx, Fy, Fz, Mx, My, Mz) data from MMS101 6-axis load cells over SPI on a Raspberry Pi, and displays it in a real-time PyQt6/PyQtGraph dashboard. There is no build system or test suite — this is a small hardware-lab tool, not a packaged application.

## Running the code

Python dependencies are listed in `requirements.txt` (numpy, PyQt6, pyqtgraph, PyYAML). On the Pi, they are installed system-wide:

```bash
sudo apt install python3-pyqt6 python3-pyqtgraph python3-numpy python3-yaml python3-spidev python3-gpiozero python3-lgpio
```

Main dashboard (`graphDash.py`), the actively developed entry point:

```bash
python3 graphDash.py                    # real hardware, config/sensors.yaml
python3 graphDash.py --debug            # simulated data, no hardware/SPI needed
python3 graphDash.py --debug --cells 4  # simulate a specific cell count
python3 graphDash.py --config path/to/sensors.yaml
```

`graphDash.py` is a thin shim that delegates to the `graphDash` package (`graphDash/__main__.py`).

`--debug` mode works on any machine (spidev/gpiozero import failures are caught), so it's the way to iterate on UI/plotting/logging logic without a Pi. The dashboard also has a "Debug Mode" toggle button that flips `Sampler.simulate` at runtime. CSV logging and SQLite session tracking work in debug mode too — simulated recordings are stored just like real ones.

The optional C rewrite of the streaming logic:

```bash
make            # builds ./stream_c from stream.c (needs libgpiod, libyaml)
make clean
```

There is no lint or test command configured — verify changes by running `graphDash.py --debug` and exercising the UI (see the project instructions on testing UI changes in a browser/simulator — for this repo that means actually launching the PyQt app and checking the plots/controls).

## Architecture

### Package structure (`graphDash/`)

The `graphDash/` package is the main codebase, with `graphDash.py` at the repo root as a thin entry-point shim:

- `__main__.py` — entry point: arg parsing, config loading, DB init, thread wiring, pyqtgraph global config, `theme.apply(app)`, Qt event loop.
- `constants.py` — MMS101 command bytes, axis names/colors (colorblind-safe `tab10` palette), UI defaults (`REFRESH_MS`, `MAX_BUFFER_SAMPLES`, etc.).
- `protocol.py` — `s24()` 3-byte signed-int decoder, `Sensor` (real SPI hardware), `DummySensor` (sine+noise simulation).
- `datastore.py` — `DataStore`: thread-safe ring buffer (`collections.deque` + `threading.Lock`, `MAX_BUFFER_SAMPLES=5000`). `get_cell()` supports windowing to the last N seconds and returns time relative to the window's first sample.
- `csv_logger.py` — `CSVLogger`: long-lived background `threading.Thread` with per-recording lifecycle (see "Recording lifecycle" below).
- `sampler.py` — `Sampler`: background thread that polls all cells (real or simulated) at `rate_hz`, applies force/moment overrides via `force_moment.compute_adjusted()`, then pushes adjusted readings into `DataStore` and forwards them to `CSVLogger`. `running` gates whether it's actively sampling; `simulate` gates real vs. dummy sensors — both are toggled live from the UI.
- `force_moment.py` — Force/moment override computation (see "Force/moment overrides" below). Contains `PositionVectors` (thread-safe per-tooth-type position vector store) and `compute_adjusted()` (applies threshold-based moment cross-product and force corrections).
- `session_manager.py` — SQLite operations for session tracking (see "SQLite session database" below).
- `config.py` — `load_sensor_config()` (YAML) and `build_simulation_cells()` (creates `DummySensor` instances).
- `paths.py` — Cross-platform path resolution: detects Pi (`/home/sparkrnd` exists) vs. laptop, and returns the appropriate log root, logs directory, and database path.
- `ui/` — PyQt6 UI widgets:
  - `theme.py` — centralized design tokens (colors, typography scale, spacing) and a global QSS stylesheet applied via `theme.apply(app)`. All UI colors are defined here — components reference `theme.PRIMARY`, `theme.ON_SURFACE`, etc. instead of hardcoding hex values. The palette is white + dark blue (`#055CA3` primary) with semantic accents (coral for danger/stop, amber for debug mode, muted green for success). Plot curve colors remain in `constants.py` since they're data-level, not chrome-level.
  - `dashboard.py` — `Dashboard` main window: header with title + live status indicator (Idle / Recording), control bar in a rounded card (start/stop, sample rate, rolling window, smoothing, log point, clear data, debug toggle) plus tabbed content area. Buttons use semantic variants via QSS dynamic properties (`variant="primary"`, `"danger"`, `"accent"`).
  - `cell_tab.py` — `CellTab`: one page per load cell with force plot, moment plot, live readouts, tare/clear-tare, and a causal moving-average smoother.
  - `cells_tab.py` — `CellsTab`: holds every `CellTab` in a `QStackedWidget` behind a single "Cell Graphs" tab. It has no in-page selector — the tab header itself is the dropdown (`_CellTabBar` in `dashboard.py` pops a `QMenu` of cell names when that tab is clicked, and the tab label shows the visible cell). Plots are wrapped in rounded card frames with themed axis/grid/legend styling. Readouts are monospace pill cards with a left color-accent bar.
  - `sessions_tab.py` — `SessionsTab`: paginated table of recording sessions with inline CSV viewer (see "Sessions tab" below). Tables use alternating row colors from theme.
  - `arch_tab.py` — `ArchTab`: dental arch heatmap showing real-time Fz force on a parabolic lower-arch layout (see "Arch View tab" below). Text and outlines use theme tokens; the functional heatmap palette (gray→green→yellow→red) is independent.
  - `position_vector_tab.py` — `PositionVectorTab`: per-tooth-type position vector editor for force/moment override parameters (see "Position Vector tab" below).
  - `sensor_config_tab.py` — `SensorConfigTab`: editable table of sensor configs with add/remove; saves to `sensors.yaml` on change.

### Hardware protocol (repeated in every entry point)

The MMS101 SPI command protocol is duplicated verbatim across the `graphDash` package (`protocol.py`), `pi_sender.py`, `cell_dashboard.py`, `stream.py`, `stream_threaded.py`, and re-implemented in C in `stream.c`. Each defines its own `Sensor` class with the same command bytes (`CMD_START`/`CMD_DATA2`/`CMD_BOOT`/etc.), the same `s24()` 3-byte-signed-int decoder, and the same init handshake: RESET → wait STANDBY → BOOT → wait READY → read 6x6 calibration coefficient matrix (`CMD_COEFF`) → set sample interval → START.

**When fixing a protocol bug, check whether it needs to be fixed in all of these files** — there is no shared module. `graphDash/protocol.py` is the canonical/actively-maintained version (reads all 6 axes, force + moment); the others are earlier/parallel variants that read only Fx/Fy/Fz:
- `cell_dashboard.py` — Pi-only, hardcoded 4-cell config, no networking.
- `pi_sender.py` + `laptop_dashboard.py` — split architecture: Pi reads sensors and streams newline-delimited JSON over TCP (port 5555); a separate laptop process renders the UI. Used when running PyQt directly on the Pi is impractical.
- `stream.py` / `stream_threaded.py` — headless CLI streamers (print-to-console, with cycle-timing stats) instead of a GUI; `stream_threaded.py` parallelizes reads with one worker thread per SPI bus via `ThreadPoolExecutor`.
- `stream.c` — C port of the same protocol using libgpiod v2 for GPIO and Linux `spidev` ioctls directly, with its own YAML parser calls via libyaml.

### Recording lifecycle (CSV + SQLite)

Each Start→Stop cycle in the UI creates one CSV file and one SQLite session row:

1. **Start** (`CSVLogger.start_recording()`): opens a new CSV at `logs_dir() / log_<timestamp>.csv`, writes the header row, registers a session in the SQLite database via `session_manager.start_session()`.
2. **Sampling**: `Sampler` calls `CSVLogger.log_sample()` on each poll cycle. Samples are buffered (500 rows) and flushed periodically. `log_sample()` is a no-op when no recording is active.
3. **Stop** (`CSVLogger.stop_recording()`): flushes remaining buffer, closes the CSV file, marks the session's `end_time` in the database via `session_manager.end_session()`, then calls `prune_old_sessions()`.

The `CSVLogger` thread stays alive for the app's lifetime — only the recording state toggles. Recording state is guarded by a `threading.Lock`.

### SQLite session database

`session_manager.py` stores session metadata in `recordings.db` (path resolved by `paths.py`). Uses a **connection-per-call** pattern for thread safety — no shared connection, no mutex needed.

**Schema** — `sessions` table:
- `session_id` (INTEGER PRIMARY KEY AUTOINCREMENT)
- `start_time` (TEXT, ISO-ish datetime)
- `end_time` (TEXT, NULL while recording is in progress)
- `file_path` (TEXT, absolute path to the CSV file)

**Key functions:**
- `init_db()` — creates the table if it doesn't exist. Called once at app startup.
- `start_session(csv_file_path)` → `session_id`
- `end_session(session_id)` — sets `end_time`
- `get_sessions(limit, offset)` — paginated, newest first
- `count_sessions()`
- `prune_old_sessions(keep_count=50)` — deletes old rows and their CSV files on disk

### Data storage paths

Resolved by `graphDash/paths.py`:

| Location | Pi (`/home/sparkrnd` exists) | Laptop |
|---|---|---|
| Log root | `/home/sparkrnd/OrthoInsightLogs/` | `~/Downloads/OrthoInsightLogs/` |
| CSV files | `.../logs/log_<timestamp>.csv` | `.../logs/log_<timestamp>.csv` |
| SQLite DB | `.../recordings.db` | `.../recordings.db` |

### Sensor configuration

`config/sensors.yaml` lists cells as `{name, bus, dev, csb_gpio, tooth, tooth_type}` (SPI bus/device, the GPIO used for chip-select, an optional Universal tooth number for the arch heatmap, and a tooth type for force/moment overrides). Bus/GPIO numbers here are physical wiring facts about the current rig — don't "fix" values that look inconsistent (e.g. two cells sharing a bus) without confirming with the user, since they reflect actual hardware, not bugs. `pi_sender.py` and `cell_dashboard.py` hardcode a 4-cell config in source instead of reading this YAML.

The `tooth` field (optional) maps a sensor to a specific tooth in the lower dental arch using Universal numbering (17–32). This drives the Arch View heatmap tab.

The `tooth_type` field (optional) assigns a tooth category — `central_incisor`, `premolar`, or `molar` — which determines the default position vector (specifically the default `h` value) used for force/moment override computations. When present, the sampler applies `compute_adjusted()` from `force_moment.py` to each sample before storing/logging.

### Force/moment overrides

`force_moment.py` applies threshold-based overrides to raw sensor readings. Each cell's `tooth_type` (from `sensors.yaml`) determines which position vector `r = [x, d+w, h]` to use. An additional parameter `d` (sensor radius) is needed for one of the force corrections. The `PositionVectors` class stores per-tooth-type vectors with thread-safe access; the UI tab writes to it, the sampler reads from it.

**Position vector defaults:**

| Tooth type | x | d+w | h | d |
|---|---|---|---|---|
| Central incisor | 0.0 | 9.5 | 17.15 | 4.8 |
| Premolar | 0.0 | 9.5 | 14.9 | 4.8 |
| Molar | 0.0 | 9.5 | 15.4 | 4.8 |

**Moment** — always computed as `r × F` (cross product) using raw forces, never raw sensor Mx/My/Mz. Full cross product: `M = [y*Fz − z*Fy, z*Fx − x*Fz, x*Fy − y*Fx]` where `x=x, y=d+w, z=h`. Then:
- Condition 1 (`|Fx| >= 0.3 N`): drop the `−y*Fx` term from Mz
- Condition 2 (`|Fz| >= 0.3 N`): drop the `y*Fz` term from Mx

When both conditions met: `M = [−z*Fy, z*Fx − x*Fz, x*Fy]`. When neither: full cross product (no terms dropped).

**Force** — adjustments to Fz only; Fx and Fy pass through unchanged. Both deltas use the original raw Fz (independent, additive):
- Condition 3 (`|Fy| >= 0.3 N`): `Fz += −(h * Fy) / (d+w)`
- Condition 4 (`|Fz| >= 0.3 N`): `Fz += (Fz * (d+w)) / d`

Moment computation always uses raw forces even when conditions 3/4 adjust the displayed force values. The adjusted values are what get stored in `DataStore` and logged to CSV.

### Dashboard tabs

- **Cell Graphs** (one tab, one load cell shown at a time — click the tab header to pop a dropdown of cells): force/moment time-series plots (PyQtGraph), live readout labels, tare/clear-tare controls, causal moving-average smoother (`_moving_avg`, no lookahead). Plots show the adjusted (overridden) force/moment values when a cell has a `tooth_type` configured.
- **Arch View** (`ArchTab`): lower dental arch (teeth 17–32) rendered via `QPainter` on a parabolic curve. Teeth are drawn with type-specific shapes (rounded rects for molars/premolars/incisors, pentagons for canines) and cusp hints (small circles). Mapped teeth are filled with a Fz-based heatmap color; unmapped teeth have dashed outlines and no fill. Color scale: gray ≤ 0.5 N, green→yellow at 0.5→1.25 N, yellow→red at 1.25→2.0 N, capped red above. Includes a gradient color bar legend. Refreshes every `REFRESH_MS` (50 ms).
- **Position Vector** (`PositionVectorTab`): per-tooth-type position vector editor. Dropdown selects tooth type (central incisor, premolar, molar); editable fields for x, d+w, h, and d. Changing the tooth type loads that type's current values. Edits take effect immediately on the next sample cycle. A "Reset to Defaults" button restores the selected type's factory values.
- **Sessions** (`SessionsTab`): paginated table (20 rows/page) of all recording sessions from the SQLite database. In-progress sessions show "— recording —" in red. Clicking a file path opens an inline CSV viewer (first 500 rows). Auto-refreshes every 2 seconds. Missing files show an error message instead of crashing.

### Deployment

`deploy.bat` is a Windows batch script the user runs manually to rsync-replace the whole directory on the Pi over SSH/SCP (`sparkrnd@raspberrypi.local`), then leaves the Pi to be run interactively. It is not invoked by Claude Code — treat it as user-owned tooling, not part of an automated pipeline.
