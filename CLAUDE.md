# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

OrthoInsight reads force/moment (Fx, Fy, Fz, Mx, My, Mz) data from MMS101 6-axis load cells over SPI on a Raspberry Pi, and displays it in a real-time PyQt6/PyQtGraph dashboard. There is no build system — this is a small hardware-lab tool, not a packaged application. The only automated test is a standalone compensation-validation harness (`tests/test_compensation.py`).

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

There is no lint command configured — verify UI changes by running `graphDash.py --debug` and exercising the UI (actually launch the PyQt app and check the plots/controls). The force/moment compensation has a standalone test harness, run from the repo root:

```bash
python3 tests/test_compensation.py   # validates compute_adjusted() against reference data
```

It imports the live `compute_adjusted()` and exits non-zero on failure. Importing `graphDash.force_moment` itself has no side effects.

## Architecture

### Package structure (`graphDash/`)

The `graphDash/` package is the main codebase, with `graphDash.py` at the repo root as a thin entry-point shim:

- `__main__.py` — entry point: arg parsing, config loading, DB init, thread wiring, pyqtgraph global config, `theme.apply(app)`, Qt event loop.
- `constants.py` — MMS101 command bytes, axis names/colors (colorblind-safe `tab10` palette), UI defaults (`REFRESH_MS`, `MAX_BUFFER_SAMPLES`, etc.).
- `protocol.py` — `s24()` 3-byte signed-int decoder, `Sensor` (real SPI hardware), `DummySensor` (simulation source for `--debug`). **`DummySensor.read_all()` contains two hand-toggled bodies** — a sine-wave generator and a fixed `vals` dict — one of which is commented out at any time. Which one is live changes as the user debugs; don't treat either as the intended version or "restore" the other one, and don't write tests that assume constant readings (use a local stub sensor instead). `Sensor.read_all()` returns `[Fx, Fy, Fz, Mx, My, Mz]` in `[N, N, N, N·m, N·m, N·m]`. The matrix-multiply result is right-shifted by 11 bits (÷2048), then forces are divided by 1000 (`0.001 N` LSB → N) and moments by 100000 (`0.00001 N·m` LSB → N·m) per the MMS101 datasheet matrix-operation section. **This per-axis scaling only lives in `graphDash/protocol.py`** — the standalone `stream.py` and `stream_threaded.py` streamers at the repo root still divide all six axes by 1000 (they only read forces, so it doesn't matter there; if you ever add moment support to them, apply the 100000 divisor).
- `datastore.py` — `DataStore`: thread-safe ring buffer (`collections.deque` + `threading.Lock`, `MAX_BUFFER_SAMPLES=5000`). `get_cell()` supports windowing to the last N seconds and returns time relative to the window's first sample.
- `csv_logger.py` — `CSVLogger`: long-lived background `threading.Thread` with per-recording lifecycle (see "Recording lifecycle" below).
- `sampler.py` — `Sampler`: background thread that polls all cells (real or simulated) at `rate_hz` and runs the sample pipeline (see "Sample pipeline" below), pushing one `readings` list into both `DataStore` and `CSVLogger` so the graph and the CSV can never diverge. `running` gates whether it's actively sampling; `simulate` gates real vs. dummy sensors — both are toggled live from the UI. `get_last_raw(ci)` / `get_all_last_raw()` expose the pre-tare readings the tare controls snapshot.
- `force_moment.py` — Force/moment override computation (see "Force/moment overrides" below). Contains `PositionVectors` (thread-safe per-tooth-type position vector store, `r = [rx, ry, rz]`), `TareOffsets` (per-cell `get`/`set`/`clear` plus the all-cell `set_all()`/`clear_all()` the global tare uses), and `compute_adjusted()` (applies threshold-based force/moment corrections). Importing this module has **no side effects** — the validation harness lives in `tests/test_compensation.py`, not here.
- `session_manager.py` — SQLite operations for session tracking (see "SQLite session database" below).
- `config.py` — `load_sensor_config()` / `save_sensor_config()` (YAML), `build_simulation_cells()` (creates `DummySensor` instances), and `try_init_sensors()` (constructs + inits real `Sensor`s, surfacing per-cell wiring failures instead of raising).
- `paths.py` — Cross-platform path resolution: detects Pi (`/home/sparkrnd` exists) vs. laptop, and returns the appropriate log root, logs directory, and database path.
- `ui/` — PyQt6 UI widgets:
  - `theme.py` — centralized design tokens (colors, typography scale, spacing) and a global QSS stylesheet applied via `theme.apply(app)`. All UI colors are defined here — components reference `theme.PRIMARY`, `theme.ON_SURFACE`, etc. instead of hardcoding hex values. The palette is white + dark blue (`#055CA3` primary) with semantic accents (coral for danger/stop, amber for debug mode, muted green for success). Plot curve colors remain in `constants.py` since they're data-level, not chrome-level.
  - `dashboard.py` — `Dashboard` main window: header with title + live status indicator (Idle / Recording), control bar in a rounded card (start/stop, sample rate, rolling window, smoothing, log point, clear data, tare all / clear tare, debug toggle) plus tabbed content area. Buttons use semantic variants via QSS dynamic properties (`variant="primary"`, `"danger"`, `"accent"`).
  - `cell_tab.py` — `CellTab`: one page per load cell with force plot, moment plot, live readouts, a read-only tare-offset line, and a causal moving-average smoother. Taring is global and lives in the `Dashboard` control bar (see "Tare" below), not here.
  - `cells_tab.py` — `CellsTab`: holds every `CellTab` in a `QStackedWidget` behind a single "Cell Graphs" tab. It has no in-page selector — the tab header itself is the dropdown (`_CellTabBar` in `dashboard.py` pops a `QMenu` of cell names when that tab is clicked, and the tab label shows the visible cell). Plots are wrapped in rounded card frames with themed axis/grid/legend styling. Readouts are monospace pill cards with a left color-accent bar.
  - `sessions_tab.py` — `SessionsTab`: paginated table of recording sessions with inline CSV viewer (see "Sessions tab" below). Tables use alternating row colors from theme.
  - `arch_tab.py` — `ArchTab`: **3D** dental arch showing a real-time force arrow per axis on each instrumented tooth (see "Arch View tab" below). This file is the *view* only — fit-to-pane, primitive assembly, painting, camera controls, key column. Text, outlines, and the tooth fill use theme tokens; the per-axis arrow colors come from `constants.FORCE_COLORS` / `MOMENT_COLORS`, the same palette the time-series plots use.
  - `arch_model.py` — the arch as **data**: the dental tables (`LOWER_ARCH_ORDER`, `TOOTH_TYPE`, `PALMER_LABEL`, per-type sizes/heights), `AxisSpec` / `GlyphScale` (which reading index, color, and anatomical name each arrow carries, plus the thresholds and `frac()`), and `build_arch() -> Arch` — every crown as a `Tooth` (world outline rings, extruded top, outward face normals, its own `frame` of three unit vectors, label anchor) plus the occlusal guide curve. Static, so it is built once per view, never per frame. **No widget and no camera**, which is the point: the arch's geometry is verifiable in a plain Python process with no `QApplication` (frames orthonormal and right-handed, normals outward, thresholds).
  - `proj3d.py` — the arch view's software 3D pipeline: vector helpers, an orbiting perspective `Camera`, and a per-frame `Projector` that maps world points to depth-divided image coordinates. Pure math, no Qt and no OpenGL — deliberately: `PyOpenGL` isn't in the Pi's package list, and `pyqtgraph.opengl` on the Pi's Mesa/V3D driver is a risk this view doesn't need. ~16 crowns and a handful of arrows per frame is cheap in pure Python.
  - `position_vector_tab.py` — `PositionVectorTab`: per-tooth-type position vector editor for force/moment override parameters (see "Position Vector tab" below).
  - `sensor_config_editor.py` — `SensorConfigEditor`: the shared editable sensor-config table widget (add/remove rows, field validation, optional per-row status column). Consumed by both `startup_config.py` and `sensor_config_tab.py`.
  - `sensor_config_tab.py` — `SensorConfigTab`: thin in-app tab wrapping `SensorConfigEditor`; saves to `sensors.yaml` on change and shows a restart-needed hint when the cell count changes.
  - `startup_config.py` — `StartupConfigDialog`: modal shown at launch that wraps `SensorConfigEditor`, lets the user pick real-hardware vs. debug mode, and (for real mode) test-connects each sensor before the dashboard opens.

### Hardware protocol (repeated across the remaining entry points)

The MMS101 SPI command protocol is duplicated verbatim across `graphDash/protocol.py` and the two standalone streamers at the repo root, `stream.py` and `stream_threaded.py`. Each defines its own `Sensor` class with the same command bytes (`CMD_START`/`CMD_DATA2`/`CMD_BOOT`/etc.), the same `s24()` 3-byte-signed-int decoder, and the same init handshake: RESET → wait STANDBY → BOOT → wait READY → read 6x6 calibration coefficient matrix (`CMD_COEFF`) → set sample interval → START.

**When fixing a protocol bug, check whether it needs to be fixed in all of these files** — there is no shared module. `graphDash/protocol.py` is the canonical/actively-maintained version (reads all 6 axes, force + moment); the streamers are parallel variants that read only Fx/Fy/Fz:
- `stream.py` / `stream_threaded.py` — headless CLI streamers (print-to-console, with cycle-timing stats) instead of a GUI; `stream_threaded.py` parallelizes reads with one worker thread per SPI bus via `ThreadPoolExecutor`.

(Earlier parallel entry points — `cell_dashboard.py`, `pi_sender.py` + `laptop_dashboard.py`, and the C port `stream.c` with its `Makefile` — have been removed from the tree.)

### Recording lifecycle (CSV + SQLite)

Each Start→Stop cycle in the UI creates one CSV file and one SQLite session row:

1. **Start** (`CSVLogger.start_recording()`): opens two CSVs at `logs_dir()` — the auto log `log_<timestamp>.csv` and a manual log `manual_log_<timestamp>.csv` (for "Log Point" captures) — writes both header rows, and registers one session in the SQLite database via `session_manager.start_session(log_file, manual_log_file)`.
2. **Sampling**: `Sampler` calls `CSVLogger.log_sample()` on each poll cycle. Samples are buffered (500 rows) and flushed periodically. `log_sample()` is a no-op when no recording is active. `CSVLogger.log_manual_point()` writes a single row to the manual log immediately.
3. **Stop** (`CSVLogger.stop_recording()`): flushes remaining buffer, closes both CSV files, marks the session's `end_time` in the database via `session_manager.end_session()`, then calls `prune_old_sessions()`.

The `CSVLogger` thread stays alive for the app's lifetime — only the recording state toggles. Recording state is guarded by a `threading.Lock`. On app shutdown the thread's `run()` loop routes its teardown through `stop_recording()`, so an in-progress recording is always ended in the DB (no orphaned `NULL` `end_time` rows).

### SQLite session database

`session_manager.py` stores session metadata in `recordings.db` (path resolved by `paths.py`). Uses a **connection-per-call** pattern for thread safety — no shared connection, no mutex needed.

**Schema** — `sessions` table:
- `session_id` (INTEGER PRIMARY KEY AUTOINCREMENT)
- `start_time` (TEXT, ISO-ish datetime)
- `end_time` (TEXT, NULL while recording is in progress)
- `file_path` (TEXT, absolute path to the auto-log CSV)
- `manual_file_path` (TEXT, absolute path to the manual-log CSV; added via an `ALTER TABLE` migration in `init_db()` for older DBs)

**Key functions:**
- `init_db()` — creates the table if it doesn't exist (and migrates in `manual_file_path`). Called once at app startup.
- `start_session(csv_file_path, manual_file_path=None)` → `session_id`
- `end_session(session_id)` — sets `end_time`
- `get_sessions(limit, offset)` — paginated, newest first
- `get_session_by_id(session_id)`
- `count_sessions()`
- `prune_old_sessions(keep_count=50)` — deletes old rows and both their CSV files on disk

### Data storage paths

Resolved by `graphDash/paths.py`:

| Location | Pi (`/home/sparkrnd` exists) | Laptop |
|---|---|---|
| Log root | `/home/sparkrnd/OrthoInsightLogs/` | `~/Downloads/OrthoInsightLogs/` |
| CSV files | `.../logs/log_<timestamp>.csv` | `.../logs/log_<timestamp>.csv` |
| SQLite DB | `.../recordings.db` | `.../recordings.db` |

### Sensor configuration

`config/sensors.yaml` lists cells as `{name, bus, dev, csb_gpio, tooth, tooth_type}` (SPI bus/device, the GPIO used for chip-select, an optional Universal tooth number for the arch view, and a tooth type for force/moment overrides). Bus/GPIO numbers here are physical wiring facts about the current rig — don't "fix" values that look inconsistent (e.g. two cells sharing a bus) without confirming with the user, since they reflect actual hardware, not bugs.

**Chip-select requirements** — cells may share an SPI `bus` (SCLK/MOSI/MISO are physically shared and normal), but each cell on a bus must have a **unique `csb_gpio`**. `Sensor._xfer()` asserts that GPIO as the real chip-select around each transaction, so two cells with the same `csb_gpio` are selected at once and drive MISO simultaneously — that's true electrical bus contention and garbage data, not a subtle timing bug. Correctness also assumes each deselected MMS101 tri-states MISO. Two further constraints when picking values:
- **Avoid the bus's hardware CE pins (and the reserved EEPROM pins) for `csb_gpio`.** `spi.open(bus, dev)` still pulses the hardware CE line selected by `dev` on every transfer regardless of the GPIO chip-select (SPI0 → CE0=GPIO8, CE1=GPIO7), so a `csb_gpio` of 7/8 — or 0/1, the ID_SD/ID_SC HAT-EEPROM pins — will fight another driver.
- **Same-bus reads must stay serialized.** There is no per-bus lock; the only thing preventing contention is the read model — `sampler.py` reads all cells sequentially in one thread, and `stream_threaded.py` uses one worker thread per bus. Reading two same-bus cells from different threads would interleave the assert/xfer/deassert in `_xfer` and reselect both. Add a `threading.Lock` per bus before introducing any per-cell threading on a shared bus.

Note: the current `sensors.yaml` has all cells on the same `bus`/`dev`/`csb_gpio`, so they cannot be addressed individually — surface that to the user rather than silently rewiring, per the "confirm with the user" note above.

The `tooth` field (optional) maps a sensor to a specific tooth in the lower dental arch using Universal numbering (17–32). This drives the Arch View tab. A cell with no `tooth`, or a `tooth` outside 17–32, simply never appears on the arch — that tooth stays a dashed outline.

The `tooth_type` field assigns a tooth category — `central_incisor`, `premolar`, or `molar` — which determines the default position vector `r = [rx, ry, rz]` (chiefly the default `rz`) used for force/moment override computations. It is schema-optional, but **a cell without one gets no compensation at all** (see "Sample pipeline"), so in practice every cell on the rig should have it set.

### Sample pipeline

Every sample follows exactly one chain, in `Sampler.run()`:

```
1. raw    = cell.read_all()                          # protocol.py, N and N*m
2. tared  = raw - TareOffsets.get(ci)                # per-cell 6-axis offset
3. adj    = compute_adjusted(tared, tooth_type, pv)  # force_moment.py, moments -> N*mm
                                                     # skipped if no tooth_type -> adj = tared
                    |
                    +--> DataStore.append()      -> Cell Graphs / Arch View
                    +--> CSVLogger.log_sample()  -> log_<ts>.csv
```

`store.append()` and `csv_logger.log_sample()` are handed the **same** `readings` objects, so
the values written to CSV are byte-for-byte the values the graph plots. The manual log goes
through the same data: `Dashboard._log_manual_point()` reads the last sample back out of
`DataStore`, so `manual_log_<ts>.csv` is consistent with both.

Two things that are deliberately *not* part of the stored/logged value:

- **Smoothing.** The `SMOOTHING` spinbox applies `CellTab._moving_avg()` at draw time only.
  With smoothing > 1 the drawn curve is a filtered view of the stored samples; the CSV always
  holds the unsmoothed values. Leave it at 1 if you need the plot to match the file exactly.
- **`_last_raw`.** `Sampler` keeps the pre-tare, pre-compensation reading purely so tare can
  snapshot it. It is never stored or logged.

`compute_adjusted()` runs **only for cells that declare a `tooth_type`** in `sensors.yaml`.
A cell without one falls through to tared raw readings — for both the graph and the CSV. That
skip used to be silent; `__main__.py` now prints a startup warning naming the cells missing a
`tooth_type`, and `Dashboard._on_config_changed()` flashes the same warning when a live config
edit leaves one unset.

### Tare

Taring is **global**: one press zeroes every cell at once. `Dashboard._tare_all()` calls
`Sampler.get_all_last_raw()`, which returns every cell's most recent raw reading under a
single `_last_raw_lock` acquisition — so all cells are zeroed against the *same* sampler
cycle — and hands that snapshot to `TareOffsets.set_all()`. Offsets stay **per-cell** (each
cell subtracts its own bias); it's the tare *action* that is global. `Clear Tare` calls
`TareOffsets.clear_all()`.

`Tare All` / `Clear Tare` live in the `Dashboard` control bar. `CellTab` has no tare buttons —
it only renders `offset_label`, a read-only display of the visible cell's offset, which
`Dashboard._refresh_offset_labels()` updates on every `CellTab` after a tare or clear.

`Sampler.run()` only fills `_last_raw` while `sampler.running` is True (i.e. while recording),
so taring before the first Start Recording is a no-op that flashes a hint via
`Dashboard._flash()` — the shared transient-message helper the manual-log confirmation also
uses.

### Force/moment overrides

`force_moment.py` applies threshold-based overrides to raw sensor readings. Each cell's `tooth_type` (from `sensors.yaml`) determines which position vector `r = [rx, ry, rz]` to use (`rx` is currently unused by the math). The `PositionVectors` class stores per-tooth-type vectors with thread-safe access; the UI tab writes to it, the sampler reads from it. Also defined here: `TareOffsets`, a per-cell 6-axis offset subtracted from raw readings before compensation runs.

**This section documents exactly what `compute_adjusted()` does. The code is authoritative — if a description here ever disagrees with the code, fix the description, not the code.**

**Position vector defaults** (all values in mm; `compute_adjusted` divides each by 1000 to work in meters):

| Tooth type | rx | ry | rz |
|---|---|---|---|
| Central incisor | 0.0 | 8.2 | 17.89 |
| Premolar | 0.0 | 9.5 | 14.9 |
| Molar | 0.0 | 9.5 | 15.4 |

**Units contract** — raw forces (`fxo, fyo, fzo`) arrive in **N** and raw moments (`mxo, myo, mzo`) in **N·m** from `protocol.py`. All arithmetic stays in SI (N, m, N·m); the final `mx, my, mz *= 1000` at the end of `compute_adjusted` converts moments to **N·mm** for display. So the values that get stored in `DataStore`, logged to CSV, and plotted are **forces in N, moments in N·mm.**

The `FORCE_THRESHOLD` is `0.3 N`.

**Force** — Fx and Fy always pass through raw. Fz is replaced (not summed) when the threshold trips:
- If `|fyo| >= 0.3 N` **or** `|fzo| >= 0.3 N`: `fz = (mxo + fyo*rz) / ry`, and **in the same branch the local `mxo` is rewritten in place** to `mxo = -fyo*rz + fz*ry`. This rewrite is intentional — the moment block below reads the updated `mxo`.
- Otherwise: `fz = fzo`.

**Moment** — My always passes through raw. Mx and Mz are corrected independently:
- `mz = mzo`; if `|fxo| >= 0.3 N`: `mz = mzo + fxo*ry`.
- `mx = mxo` (the possibly-rewritten `mxo`); if `|fzo| >= 0.3 N`: `mx = mxo - fz*ry`, using the corrected `fz`. Because the `|fzo|` case also triggers the force branch above, whenever this fires it reduces algebraically to `mx = -fyo*rz`.

### Dashboard tabs

- **Cell Graphs** (one tab, one load cell shown at a time — click the tab header to pop a dropdown of cells): force/moment time-series plots (PyQtGraph), live readout labels, a read-only tare-offset line, causal moving-average smoother (`_moving_avg`, no lookahead). Plots show the adjusted (overridden) force/moment values when a cell has a `tooth_type` configured. Force axis is in **N**, moment axis is in **N·mm** (see "Force/moment overrides" for the unit chain).
- **Arch View** (`ArchTab`): one lower dental arch (teeth 17–32) drawn in **3D** — a parabola in the z = 0 occlusal plane, each crown an extruded prism of its occlusal outline (rounded rects for molars/premolars, rounded pentagons for canines/incisors). Only teeth with a load cell mapped to them stand up as prisms; unmapped teeth stay flat dashed footprints in the plane, so the instrumented teeth are the only things with height. Palmer labels sit **buccal** (outside the arch curve) so they stay clear of the arrows, and are painted last so nothing occludes them. Refreshes every `REFRESH_MS` (50 ms), skipped while the tab is hidden.

  **Force arrows.** Each mapped tooth grows three arrows from the middle of its occlusal surface, one per force component, drawn in **that tooth's own frame** (*not* screen-fixed — the arrows rotate with the arch, matching what a bracket-mounted cell actually measures):

  | Axis | Direction |
  |---|---|
  | `Fx` | mesio-distal — tangent to the arch (`e_x = e_y × e_z`, right-handed) |
  | `Fy` | bucco-lingual — the arch's outward normal; **+y = buccal/labial** |
  | `Fz` | occlusal — **+z = up, out of the tooth**; negative is intrusive |

  Arrow length (and pen width) grows linearly with |value| between the low and high thresholds, held in `GlyphScale`: **force 0.25 N → 3 N**. Below the low threshold **that axis draws nothing** (per-axis, not per-tooth); at or above the high threshold the arrow clamps and stops growing. So a longer arrow always means more force, and a missing arrow always means under-threshold. `MOMENT_GLYPH` (**0.05 → 75 N·mm**) is defined next to it but nothing draws it yet — the moment layer is deliberately deferred.

  **Camera.** `Oblique` / `Occlusal` / `Anterior` preset buttons plus free orbit (left-drag), wheel zoom (0.45×–4×), and double-click to reset. Orbiting off a preset switches the header to "Custom" and unhighlights the buttons (`ArchView3D.preset_left`). Pitch is clamped to **2°–89.5°** — always above the occlusal plane, because the renderer paints each crown's top face last on the assumption that it is the prism's near face. The camera sits `CAM_DISTANCE = 7.0` world units out (the arch spans ~2 × 1.3) so a near tooth can't approach the eye and explode under the perspective divide; the view fits the pane by **scaling the projected coordinates**, never by dollying, so a longer lens costs nothing.

  **Fit.** `_Frame.fit()` projects all crown vertices (`Arch.vertices()`), takes the bounding box, and derives one px-per-image-unit scale (`FIT_MARGIN` leaves room for the arrows, which are deliberately **excluded** from the fit — otherwise the arch would breathe as forces grew). The resulting `_Frame` is the *only* thing carrying per-frame state: it owns the projector, the scale, the screen offsets, px-per-world-unit, and the arch `unit`, so no frame state is parked on the widget and every `_draw_*` helper takes it as an argument.

  **Paint order.** `_primitives()` yields `(depth, paint)` for every crown and arrow in one flat sequence, and `paintEvent` paints them sorted by descending depth. Rings are given `OVERLAY_DEPTH = -inf` so they sort last instead of needing a second list. All readings for a frame come from one `_readings()` call, so the arrows and the key's numeric readout can never show different samples.

  **Visibility** is back-face culling (drop side quads whose outward normal faces away) plus a painter's-algorithm depth sort over crowns and arrows together. Two wrinkles worth knowing:
  - An arrow's depth key is **clamped to its own tooth's apex depth**, so an arrow can never be swallowed by the crown it grows out of — an intrusive `-Fz` points straight into the tooth body and would otherwise be invisible. Teeth nearer the camera still cover it.
  - An arrow aimed close to the **view axis** has almost no projected length, so its shaft would lie about magnitude. Below `AXIAL_MIN_FRAC` of its face-on length it is replaced by a **ring glyph** sized by magnitude — filled dot = pointing toward the viewer, ✗ = away (the convention the old 2D view used for z). Rings are drawn in a final overlay pass because they mark their own tooth's occlusal surface.

  A fixed-width key column on the right documents the axis colors, the length scale, the ring glyphs, and a live `Fx Fy Fz` readout per instrumented tooth (arrows say which way; the numbers say how much).

- **Position Vector** (`PositionVectorTab`): per-tooth-type position vector editor. Dropdown selects tooth type (central incisor, premolar, molar); editable fields for `rx`, `ry`, and `rz` (all mm). Changing the tooth type loads that type's current values. Edits take effect immediately on the next sample cycle. A "Reset to Defaults" button restores the selected type's factory values.
- **Sessions** (`SessionsTab`): paginated table (20 rows/page) of all recording sessions from the SQLite database. In-progress sessions show "— recording —" in red. Clicking a file path opens an inline CSV viewer (first 500 rows). Auto-refreshes every 2 seconds. Missing files show an error message instead of crashing.

### Deployment

`deploy.bat` is a Windows batch script the user runs manually to rsync-replace the whole directory on the Pi over SSH/SCP (`sparkrnd@raspberrypi.local`), then leaves the Pi to be run interactively. It is not invoked by Claude Code — treat it as user-owned tooling, not part of an automated pipeline.
