# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

OrthoInsight reads force/moment (Fx, Fy, Fz, Mx, My, Mz) data from MMS101 6-axis load cells over SPI on a Raspberry Pi, and displays it in a real-time PyQt6/PyQtGraph dashboard. There is no build system — this is a small hardware-lab tool, not a packaged application. The automated tests are two standalone harnesses: compensation validation (`tests/test_compensation.py`) and the moving-average filter (`tests/test_smoothing.py`).

## Running the code

Python dependencies are listed in `requirements.txt` (numpy, PyQt6, pyqtgraph, PyYAML). On the Pi, they are installed system-wide:

```bash
sudo apt install python3-pyqt6 python3-pyqtgraph python3-numpy python3-yaml python3-spidev python3-gpiozero python3-lgpio
```

**The 3D Arch View adds no runtime dependencies** — it is Python stdlib (`math`, `dataclasses`, `functools`, `json`, `array`) plus `QPainter`, and deliberately uses **no** `PyOpenGL`, `pyqtgraph.opengl`, Qt3D, or QtQuick3D (and no numpy either). `PyOpenGL` is not in the apt line above, and pyqtgraph's GL widget on the Pi's Mesa/V3D driver is a risk the view doesn't need. Keep it that way: if a change to `proj3d.py` / `arch_model.py` / `arch_tab.py` / `arch_asset.py` seems to want a GL or array dependency, that is a signal the change is going the wrong way.

The arch's crowns are real scanned meshes, which is exactly why that rule still holds: **all the expensive work happens offline**. `tools/bake_arch_mesh.py` runs on a laptop with `trimesh` + `numpy` — loading STLs, decimating to a triangle budget, deriving each tooth's sensor frame — and writes `graphDash/assets/arch_mesh.{json,bin}`. The Pi only ever reads that file. Nothing under `graphDash/` imports the bake tool or its dependencies, and nothing should.

Main dashboard (`graphDash.py`), the actively developed entry point:

```bash
python3 graphDash.py                    # real hardware, config/sensors.yaml
python3 graphDash.py --debug            # simulated data, no hardware/SPI needed
python3 graphDash.py --debug --cells 4  # simulate a specific cell count
python3 graphDash.py --config path/to/sensors.yaml
```

`graphDash.py` is a thin shim that delegates to the `graphDash` package (`graphDash/__main__.py`).

`--debug` mode works on any machine (spidev/gpiozero import failures are caught), so it's the way to iterate on UI/plotting/logging logic without a Pi. On the current dev laptop the **system `python3` cannot run it** (no numpy, no pyqtgraph) — use the local `.venv/`, e.g. `.venv/bin/python graphDash.py --debug`. That venv is not committed (it self-ignores via its own `.venv/.gitignore` containing `*`), so it may simply not exist on a fresh clone; recreate it from `requirements.txt` rather than assuming a missing venv means something is broken. The dashboard also has a "Debug Mode" toggle button that flips `Sampler.simulate` at runtime. CSV logging and SQLite session tracking work in debug mode too — simulated recordings are stored just like real ones.

There is no lint command configured — verify UI changes by running `graphDash.py --debug` and exercising the UI (actually launch the PyQt app and check the plots/controls).

For a view that is drawn rather than laid out (the Arch View), that check can be automated without a display: run under `QT_QPA_PLATFORM=offscreen`, build the widget against a stub store, and `widget.grab().save(path)` to get a PNG you can actually look at. Two techniques that paid off and are worth reusing:
- **Pixel-diff against a baseline** to prove a refactor changed nothing. Beware the noise floor: with the sine-wave `DummySensor` the readings move between runs, so diff two runs of *identical* code first to learn what "unchanged" looks like — a static-value stub store diffs to zero, live simulated data does not.
- **Sweep the camera** (a matrix of yaw/pitch values, each grabbed into one sheet) rather than eyeballing one angle. The fit-to-pane and face-culling bugs only showed up at the extremes.

Both are now packaged, along with the frame-time gate. All three run headless:

```bash
QT_QPA_PLATFORM=offscreen python3 tests/render_arch.py --out /tmp/arch_a   # presets, edge cases, yaw x pitch sweep sheets
python3 tests/render_arch.py --diff /tmp/arch_a /tmp/arch_b                # zero noise floor: the stub store is static
QT_QPA_PLATFORM=offscreen python3 tests/bench_arch.py                      # ms/frame (arch alone AND whole tab), exits 1 over REFRESH_MS
QT_QPA_PLATFORM=offscreen python3 tests/bench_arch.py --scaling            # us/triangle -> the bake budget
QT_QPA_PLATFORM=offscreen python3 tests/bench_arch.py --pick               # ms per hit test, on a crown and off
```

`tests/arch_harness.py` holds the shared `StubStore` and the two widget builders — `build_view` for the arch alone and `build_tab` for the whole tab, since the key bar and the toggle column are sibling widgets and appear in neither a `build_view` grab nor a `build_view` timing. `bench_arch.py` times both, and the gate is the worse: the tab's tick is what `REFRESH_MS` is actually the budget for. The harness is imported by the other two, not run directly.

Note when comparing frame times against anything measured before the chrome moved out of the view: the arch used to be clipped to `width - KEY_W` and now gets the whole widget, so at the same `--size` it rasterises a wider arch. **The Pi's `--scaling` number has to be retaken.** **Run `--scaling` on the Pi 4, not the laptop** — the laptop is roughly an order of magnitude faster and its numbers say nothing about the deployment.

`arch_model.build_arch()` needs **no `QApplication`** — it only reads a file. Arch geometry (frames orthonormal and right-handed, face normals outward, crowns closed, apex inside its own footprint, `GlyphScale.frac()` thresholds) is assertable in a plain Python process, and `tests/test_arch_model.py` does exactly that against whatever asset is currently shipped — so a bad bake fails there rather than in a screenshot. The force/moment compensation and the moving-average filter each have a standalone test harness, run from the repo root:

```bash
python3 tests/test_compensation.py   # validates compute_adjusted() against reference data
python3 tests/test_smoothing.py      # validates the MovingAverage filter
python3 tests/test_tooth_frames.py   # validates the pure half of tools/tooth_frames.py
python3 tests/test_glyph_visibility.py  # validates the Arch View's per-tooth toggle state
QT_QPA_PLATFORM=offscreen python3 tests/test_arch_pick.py   # validates click-a-tooth picking
```

Both import the live production code and exit non-zero on failure. Importing `graphDash.force_moment` or `graphDash.smoothing` has no side effects. Neither is a pytest file — they are print-and-exit-code scripts, run explicitly.

`test_compensation.py` pushes each reference row through the live `MovingAverage` before calling `compute_adjusted()`, so it exercises the real pipeline order rather than bypassing the filter. Those rows are static and a moving average of a constant is that same constant, so the filter is an identity there and the expected values are unaffected by it — the filter's own behaviour (seconds→samples, warm-up, window shrink, live rate change, reset, per-cell isolation) is checked in `test_smoothing.py`, which needs no `QApplication`, no numpy and no hardware.

**As of 2026-08-26 this harness reports `6/8 TESTS PASSED` and exits 1** on `refactor/modular-graphdash` and its descendants — a pre-existing condition, not something a UI change caused. Confirm it against your base commit before assuming your work broke it.

## Architecture

### Package structure (`graphDash/`)

The `graphDash/` package is the main codebase, with `graphDash.py` at the repo root as a thin entry-point shim:

- `__main__.py` — entry point: arg parsing, config loading, DB init, thread wiring, pyqtgraph global config, `theme.apply(app)`, Qt event loop.
- `constants.py` — MMS101 command bytes, axis names/colors (colorblind-safe `tab10` palette), UI defaults (`REFRESH_MS`, `MAX_BUFFER_SAMPLES`, etc.).
- `protocol.py` — `s24()` 3-byte signed-int decoder, `Sensor` (real SPI hardware), `DummySensor` (simulation source for `--debug`). **`DummySensor.read_all()` contains two hand-toggled bodies** — a sine-wave generator and a fixed `vals` dict — one of which is commented out at any time. Which one is live changes as the user debugs; don't treat either as the intended version or "restore" the other one, and don't write tests that assume constant readings (use a local stub sensor instead). `Sensor.read_all()` returns `[Fx, Fy, Fz, Mx, My, Mz]` in `[N, N, N, N·m, N·m, N·m]`. The matrix-multiply result is right-shifted by 11 bits (÷2048), then forces are divided by 1000 (`0.001 N` LSB → N) and moments by 100000 (`0.00001 N·m` LSB → N·m) per the MMS101 datasheet matrix-operation section. **This per-axis scaling only lives in `graphDash/protocol.py`** — the standalone `stream.py` and `stream_threaded.py` streamers at the repo root still divide all six axes by 1000 (they only read forces, so it doesn't matter there; if you ever add moment support to them, apply the 100000 divisor).
- `datastore.py` — `DataStore`: thread-safe ring buffer (`collections.deque` + `threading.Lock`, `MAX_BUFFER_SAMPLES=5000`). `get_cell()` supports windowing to the last N seconds and returns time relative to the window's first sample.
- `csv_logger.py` — `CSVLogger`: long-lived background `threading.Thread` with per-recording lifecycle (see "Recording lifecycle" below).
- `sampler.py` — `Sampler`: background thread that polls all cells (real or simulated) at `rate_hz`. `run()` is the orchestration loop; `_process(cell_idx, raw)` is the per-sample pipeline (see "Sample pipeline" below) and is the natural place to test that pipeline's order without a thread or a store, pushing one `readings` list into both `DataStore` and `CSVLogger` so the graph and the CSV can never diverge. `running` gates whether it's actively sampling; `simulate` gates real vs. dummy sensors — both are toggled live from the UI. `get_last_raw(ci)` / `get_all_last_raw()` expose the pre-tare readings the tare controls snapshot.
- `smoothing.py` — `MovingAverage`: the causal moving-average filter applied to all six axes in the sample pipeline, between tare and compensation (see "Sample pipeline" below). Its window is specified in **seconds** and converted to a sample count internally as the module-level `window_samples(window_s, rate_hz)` — `max(1, round(...))` — using the sampler's live rate, so changing the sample rate does not change the window's length in *time*. Before the buffer holds a full window the output is an expanding mean, so there is a defined output from the very first sample. `window_s = 0` is not a special disabled mode: it simply yields a one-sample window, so there is no branch for it. Pure stdlib (`collections.deque` + `threading.Lock`) — no numpy, so it is testable in a plain Python process. Thread-safe because `window_s` / `reset_all()` are written from the GUI thread while `update()` runs on the sampler thread.
- `force_moment.py` — Force/moment override computation (see "Force/moment overrides" below). Contains `PositionVectors` (thread-safe per-tooth-type position vector store, `r = [rx, ry, rz]`), `TareOffsets` (per-cell `get`/`set`/`clear` plus the all-cell `set_all()`/`clear_all()` the global tare uses), and `compute_adjusted()` (applies threshold-based force/moment corrections). Importing this module has **no side effects** — the validation harness lives in `tests/test_compensation.py`, not here.
- `session_manager.py` — SQLite operations for session tracking (see "SQLite session database" below).
- `config.py` — `load_sensor_config()` / `save_sensor_config()` (YAML), `build_simulation_cells()` (creates `DummySensor` instances), and `try_init_sensors()` (constructs + inits real `Sensor`s, surfacing per-cell wiring failures instead of raising).
- `paths.py` — Cross-platform path resolution: detects Pi (`/home/sparkrnd` exists) vs. laptop, and returns the appropriate log root, logs directory, and database path.
- `ui/` — PyQt6 UI widgets:
  - `theme.py` — centralized design tokens (colors, typography scale, spacing) and a global QSS stylesheet applied via `theme.apply(app)`. All UI colors are defined here — components reference `theme.PRIMARY`, `theme.ON_SURFACE`, etc. instead of hardcoding hex values. The palette is white + dark blue (`#055CA3` primary) with semantic accents (coral for danger/stop, amber for debug mode, muted green for success). Plot curve colors remain in `constants.py` since they're data-level, not chrome-level.
  - `dashboard.py` — `Dashboard` main window: header with title + live status indicator (Idle / Recording), control bar in a rounded card (start/stop, sample rate, rolling window, smoothing window in seconds, log point, clear data, tare all / clear tare, debug toggle) plus tabbed content area. Buttons use semantic variants via QSS dynamic properties (`variant="primary"`, `"danger"`, `"accent"`).
  - `cell_tab.py` — `CellTab`: one page per load cell with force plot, moment plot, live readouts, and a read-only tare-offset line. It applies **no** smoothing of its own — it draws exactly what `DataStore` holds, which is what makes the plot and the CSV identical (the moving average runs upstream in `smoothing.py`). Taring is global and lives in the `Dashboard` control bar (see "Tare" below), not here.
  - `cells_tab.py` — `CellsTab`: holds every `CellTab` in a `QStackedWidget` behind a single "Cell Graphs" tab. It has no in-page selector — the tab header itself is the dropdown (`_CellTabBar` in `dashboard.py` pops a `QMenu` of cell names when that tab is clicked, and the tab label shows the visible cell). Plots are wrapped in rounded card frames with themed axis/grid/legend styling. Readouts are monospace pill cards with a left color-accent bar.
  - `sessions_tab.py` — `SessionsTab`: paginated table of recording sessions with inline CSV viewer (see "Sessions tab" below). Tables use alternating row colors from theme.
  - `arch_tab.py` — `ArchTab`: **3D** dental arch showing a real-time force *or* moment glyph per axis on each instrumented tooth — a straight arrow for a force, a circular arrow curling about the axis for a moment — or one glyph on their resultant (see "Arch View tab" below). This file holds the whole tab: `ArchView3D` (the *view* only — fit-to-pane, primitive assembly, painting, camera controls), `ArchKeyBar` (the landscape key across the top), `ToothGlyphPanel` (the per-tooth toggles down the right) and `GlyphVisibility` (which glyphs each tooth shows). Text, outlines, and the tooth fill use theme tokens; the per-axis arrow colors come from `constants.FORCE_COLORS` / `MOMENT_COLORS`, the same palette the time-series plots use, and the resultant arrow is `constants.RESULTANT_COLOR` (purple — it is drawn alongside the force components now that teeth toggle independently, so it can't reuse `Fz`'s red).
  - `arch_model.py` — the arch as **data**: the dental tables (`LOWER_ARCH_ORDER`, `TOOTH_TYPE`) and `normalize_tooth()`, `AxisSpec` / `ResultantSpec` / `GlyphScale` (which reading index, color, and anatomical name each glyph carries, plus the thresholds, `frac()` / `resultant_frac()`, and the `curl` flag that makes moments circular arrows), and `build_arch() -> Arch`, which **loads the baked mesh** rather than computing geometry. Every crown is a `Tooth`: `verts` (unique world vertices), `tris` (`(i, j, k, outward normal)` per triangle, normals baked so the per-frame cull is one dot product), `silhouette` (the flat z = 0 outline drawn when no cell is mapped), its own `frame` of three unit vectors, `apex`, and a label anchor. Static, so it is built once per view, never per frame. **No widget and no camera**, which is the point: the arch's geometry is verifiable in a plain Python process with no `QApplication`. A missing or version-mismatched asset raises with the bake command in the message — it never falls back, because a silent fallback is how a stale asset reaches the Pi unnoticed.
  - `arch_asset.py` — the on-disk mesh format, reader **and** writer in one module so the bake tool and the app cannot drift apart. `arch_mesh.json` carries everything small and inspectable (version, `unit`, guide, fit hull, and per tooth its frame/apex/silhouette plus block offsets); `arch_mesh.bin` carries the bulk float32/uint32 arrays. Stdlib only (`json`, `array`) — this is the Pi path.
  - `proj3d.py` — the arch view's software 3D pipeline: vector helpers, an orbiting perspective `Camera`, and a per-frame `Projector` that maps world points to depth-divided image coordinates. Pure math, no Qt and no OpenGL — deliberately: `PyOpenGL` isn't in the Pi's package list, and `pyqtgraph.opengl` on the Pi's Mesa/V3D driver is a risk this view doesn't need. Geometry-agnostic, so the move from prisms to scanned meshes needed nothing from it.
  - `position_vector_tab.py` — `PositionVectorTab`: per-tooth-type position vector editor for force/moment override parameters (see "Position Vector tab" below).
  - `sensor_config_editor.py` — `SensorConfigEditor`: the shared editable sensor-config table widget (add/remove rows, field validation, optional per-row status column). Consumed by both `startup_config.py` and `sensor_config_tab.py`.
  - `sensor_config_tab.py` — `SensorConfigTab`: thin in-app tab wrapping `SensorConfigEditor`; saves to `sensors.yaml` on change and shows a restart-needed hint when the cell count changes.
  - `startup_config.py` — `StartupConfigDialog`: modal shown at launch that wraps `SensorConfigEditor`, lets the user pick real-hardware vs. debug mode, and (for real mode) test-connects each sensor before the dashboard opens.

### Hardware protocol (repeated across the remaining entry points)

The MMS101 SPI command protocol is duplicated verbatim across `graphDash/protocol.py` and the two standalone streamers at the repo root, `stream.py` and `stream_threaded.py`. Each defines its own `Sensor` class with the same command bytes (`CMD_START`/`CMD_DATA2`/`CMD_BOOT`/etc.), the same `s24()` 3-byte-signed-int decoder, and the same init handshake: RESET → wait STANDBY → BOOT → wait READY → read 6x6 calibration coefficient matrix (`CMD_COEFF`) → set the temperature-correction interval (`CMD_INTERVAL`) → START.

**`CMD_INTERVAL` (0x44) is not a sample rate.** It is how many device-side acquisitions pass between automatic refreshes of the MMS101's offset temperature correction reference; the Conv.BD always acquires at 1 ms regardless, and nothing in this protocol sets a sample rate. Sending `0` disables refreshes, which pins the correction to the die temperature measured in the single TempADC conversion at `START` — the AFEs then self-heat and the reading drifts without ever settling. `graphDash/constants.py` carries the chosen cadence as `TEMP_UPDATE_INTERVAL` with the reasoning; **`stream.py` and `stream_threaded.py` still send `0`** and are commented to say so.

`Sensor.read_all()` decodes DATA2 bytes 1-2 as the **Measure Status** word into `last_status` / `stale_count` / `nack_count` — bit b9 is “new data is not ready” (the frame repeats the previous sample) and b0-b5 are per-axis NACKs. These are counters only: a stale or NACKed frame is still returned, since dropping samples there would be a new failure mode. The streamers discard those bytes.

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

`config/sensors.yaml` lists cells as `{name, bus, dev, csb_gpio, tooth, tooth_type}` (SPI bus/device, the GPIO used for chip-select, an optional Palmer tooth designation for the arch view, and a tooth type for force/moment overrides). Bus/GPIO numbers here are physical wiring facts about the current rig — don't "fix" values that look inconsistent (e.g. two cells sharing a bus) without confirming with the user, since they reflect actual hardware, not bugs.

**Chip-select requirements** — cells may share an SPI `bus` (SCLK/MOSI/MISO are physically shared and normal), but each cell on a bus must have a **unique `csb_gpio`**. `Sensor._xfer()` asserts that GPIO as the real chip-select around each transaction, so two cells with the same `csb_gpio` are selected at once and drive MISO simultaneously — that's true electrical bus contention and garbage data, not a subtle timing bug. Correctness also assumes each deselected MMS101 tri-states MISO. Two further constraints when picking values:
- **Avoid the bus's hardware CE pins (and the reserved EEPROM pins) for `csb_gpio`.** `spi.open(bus, dev)` still pulses the hardware CE line selected by `dev` on every transfer regardless of the GPIO chip-select (SPI0 → CE0=GPIO8, CE1=GPIO7), so a `csb_gpio` of 7/8 — or 0/1, the ID_SD/ID_SC HAT-EEPROM pins — will fight another driver.
- **Same-bus reads must stay serialized.** There is no per-bus lock; the only thing preventing contention is the read model — `sampler.py` reads all cells sequentially in one thread, and `stream_threaded.py` uses one worker thread per bus. Reading two same-bus cells from different threads would interleave the assert/xfer/deassert in `_xfer` and reselect both. Add a `threading.Lock` per bus before introducing any per-cell threading on a shared bus.

Note: the current `sensors.yaml` has all cells on the same `bus`/`dev`/`csb_gpio`, so they cannot be addressed individually — surface that to the user rather than silently rewiring, per the "confirm with the user" note above.

The `tooth` field (optional) maps a sensor to a specific tooth using its **Palmer designation** — `LL1`–`LL8` for the lower-left quadrant and `LR1`–`LR8` for the lower-right, numbered from the midline outward, so `LR5` is the lower-right second premolar. This drives the Arch View tab.

Palmer is the notation used **everywhere**: `sensors.yaml`, the labels in the arch view, the baked mesh asset, and the STL filenames the baker reads (`LL5.stl`, `LR2.stl`). One notation end to end means the label on screen, the line in the config and the name of the scan file are the same string.

Every tooth identifier passes through `arch_model.normalize_tooth()`, which is the single definition of what counts as a tooth. It accepts any case and stray whitespace, and still resolves a bare Universal number from a config written before the migration (`31` → `LR7`). Anything else — an upper-arch tooth, a nonexistent `LR9`, an unset field — returns `None`, and that cell simply never appears on the arch; the tooth stays a dashed outline.

Note: as currently committed, `sensors.yaml` maps its three cells to **LR7, LR2 and LR5** — carried over faithfully from the Universal numbers 31, 26 and 29 that preceded the Palmer migration. **Those assignments disagree with the cells' own names and `tooth_type`s**: the cell called `incisor` (`tooth_type: central_incisor`) sits on LR7, a molar; `premolar` sits on LR2, an incisor; `molar` sits on LR5, a premolar. That mismatch predates the migration and may well be placeholder numbering. It matters — `tooth_type` picks the position vector the compensation math uses, while `tooth` only picks where the arrow is drawn, so the two disagreeing means the arch shows a correct reading on the wrong tooth. Ask the user which teeth the cells actually sit on rather than guessing.

If *no* cell maps to a lower tooth the Arch View renders an empty arch and says so in the toggle panel — correct behavior for the config, not a broken view.

The `tooth_type` field assigns a tooth category — `central_incisor`, `premolar`, or `molar` — which determines the default position vector `r = [rx, ry, rz]` (chiefly the default `rz`) used for force/moment override computations. It is schema-optional, but **a cell without one gets no compensation at all** (see "Sample pipeline"), so in practice every cell on the rig should have it set.

### Sample pipeline

Every sample follows exactly one chain, in `Sampler._process()` (`run()` handles only the orchestration around it — polling, timestamping, storing, logging, pacing):

```
1. raw    = cell.read_all()                          # protocol.py, N and N*m
2. tared  = raw - TareOffsets.get(ci)                # per-cell 6-axis offset
3. sm     = MovingAverage.update(ci, tared, rate_hz) # smoothing.py, all 6 axes
                                                     # window in SECONDS; skipped if no smoother
4. adj    = compute_adjusted(sm, tooth_type, pv)     # force_moment.py, moments -> N*mm
                                                     # skipped if no tooth_type -> adj = sm
                    |
                    +--> DataStore.append()      -> Cell Graphs / Arch View
                    +--> CSVLogger.log_sample()  -> log_<ts>.csv
```

`store.append()` and `csv_logger.log_sample()` are handed the **same** `readings` objects, so
the values written to CSV are byte-for-byte the values the graph plots. The manual log goes
through the same data: `Dashboard._log_manual_point()` reads the last sample back out of
`DataStore`, so `manual_log_<ts>.csv` is consistent with both.

**Smoothing IS part of the stored/logged value.** The `SMOOTHING` spinbox sets
`MovingAverage.window_s` in **seconds**, and the filter runs in the sampler at step 3 — so the
compensation, the plots, the Arch View and the CSV all see the *same* filtered stream. There is no
draw-time smoothing anywhere any more; the plot always matches the file exactly. Because
`compute_adjusted()` is threshold-gated at `0.3 N`, filtering upstream also stops noise straddling
that threshold from flipping `Fz`/`Mx` between the corrected and pass-through branches sample to
sample.

The filter's history is flushed (`MovingAverage.reset_all()`, via `Dashboard._flush_smoother()`) at
the discontinuities where averaging across the boundary would smear a step into the data: **Start
Recording**, the **Debug Mode** toggle, and **Tare All** / **Clear Tare**. `Clear Data` does *not*
flush — it only empties the plot buffer, and the physical signal is continuous. A live sample-rate
change does not flush either; the sample count is simply re-derived from the new rate on the next
cycle.

One thing that is deliberately *not* part of the stored/logged value:

- **`_last_raw`.** `Sampler` keeps the pre-tare, pre-filter, pre-compensation reading purely so
  tare can snapshot it. It is never stored or logged.

`compute_adjusted()` runs **only for cells that declare a `tooth_type`** in `sensors.yaml`.
A cell without one falls through to tared, smoothed readings — for both the graph and the CSV. That
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

- **Cell Graphs** (one tab, one load cell shown at a time — click the tab header to pop a dropdown of cells): force/moment time-series plots (PyQtGraph), live readout labels, a read-only tare-offset line. Curves and readouts are drawn straight from `DataStore` with no further processing, so they match the CSV exactly; the `SMOOTHING` control acts upstream (see "Sample pipeline"). Plots show the adjusted (overridden) force/moment values when a cell has a `tooth_type` configured. Force axis is in **N**, moment axis is in **N·mm** (see "Force/moment overrides" for the unit chain).
- **Arch View** (`ArchTab`): one lower dental arch (`LR8`–`LR1`, `LL1`–`LL8`) drawn in **3D** from **real scanned crown meshes**, baked offline and loaded from `graphDash/assets/`. Only teeth with a load cell mapped to them stand up as meshes; unmapped teeth stay flat dashed footprints in the occlusal plane, so the instrumented teeth are the only things with height — and the triangle budget is spent only on teeth that carry data. Palmer labels sit **buccal** (outside the arch curve) so they stay clear of the arrows, and are painted last so nothing occludes them. Refreshes every `REFRESH_MS` (50 ms), skipped while the tab is hidden.

  **Glyphs.** Each mapped tooth grows a glyph per axis from the middle of its occlusal surface, drawn in **that tooth's own frame** (*not* screen-fixed — they rotate with the arch, matching what a bracket-mounted cell actually measures):

  | Axis | Direction |
  |---|---|
  | `Fx` / `Mx` | mesio-distal — tangent to the arch (`e_x = e_y × e_z`, right-handed) |
  | `Fy` / `My` | bucco-lingual — the arch's outward normal; **+y = buccal/labial** |
  | `Fz` / `Mz` | occlusal — **+z = up, out of the tooth**; negative is intrusive |

  The `Fz` direction is the user's stated convention. The **`Fx` sign has not been confirmed against the physical brackets** — it falls out of making the tooth frame right-handed, which points `+Fx` toward the patient's right at the anterior. If a known mesial load ever comes out pointing distally, flip the sign in `arch_model._crown()`; don't "fix" it speculatively.

  **A force is a straight arrow, a moment is a curl.** A force pushes *along* its axis, so it is drawn as a straight arrow along it. A moment turns *about* its axis, so it is drawn as a circular arrow encircling it — right-hand rule, thumb along the signed axis and fingers following the arrow, the standard `M_O` notation. Which one a quantity gets is `GlyphScale.curl` (`MOMENT_GLYPH` sets it, `FORCE_GLYPH` doesn't); the view never tests for a particular scale object. **Curls are moment-only** — this is not a rendering style you can turn on for forces, it is what the quantity means.

  Magnitude ramps linearly with |value| between the low and high thresholds, held in `GlyphScale`: **force 0.25 N → 3 N**, **moment 0.05 → 75 N·mm**. Below the low threshold **that axis draws nothing** (per-axis, not per-tooth); at or above the high threshold it clamps and stops growing. What grows differs by glyph, but the reading doesn't: more glyph is always more load, and a missing glyph always means under-threshold.
  - An **arrow** gets longer (`ARROW_MIN_LEN` → `ARROW_MAX_LEN`).
  - A **curl** sweeps further around a **fixed-radius** circle (`CURL_MIN_SWEEP` 70° → `CURL_MAX_SWEEP` 320°, stopping short of a full turn so the head stays clear of the tail). The radius is deliberately *not* the ramp: holding every circle on the arch to the same size is what makes two teeth comparable at a glance, and growing circles on adjacent teeth would collide. Pen width ramps for both.

  The three component curls all encircle the same apex, so they **nest** rather than share a circle: `CURL_RADII` gives Mx the innermost and Mz the outermost, in `Tooth.frame` order. `CURL_RESULTANT_RANK` reserves the middle radius for a resultant curl; nothing draws one today, since the only quantity with a resultant is force and forces are arrows.

  **What the glyphs mean is picked in two places.** One toggle applies to the whole view; everything else is per tooth.

  - **DATA — `Force` / `Moment`,** an exclusive button group under the camera presets (`ArchTab._button_group` / `_set_active`), independent of the camera. Swaps `ArchView3D.scale` between `FORCE_GLYPH` and `MOMENT_GLYPH` (`DATA_SCALES`, in button order). Nothing else changes: same arch, same camera, same tooth frames — only which three reading indices, colors, thresholds and unit label the view uses. Moments keep their own `constants.MOMENT_COLORS` (orange/purple/cyan), matching the moment time-series plot.
  - **The toggle panel** (`ToothGlyphPanel`), a fixed-width scrolling column down the far right. One row per **instrumented** tooth — an unmapped tooth has no reading, so a row for it would control nothing — with a button per component (`X`/`Y`/`Z`, headed with the axis names of the quantity showing) and, in force mode, an `R` button for that tooth's resultant, with that tooth's live reading printed underneath. An `All` row above them flips one axis across every tooth at once: off if they are all on, otherwise on.

  A tooth's `R` **supersedes** its components — turn it on and that tooth draws *only* the resultant, and its three axis buttons grey out rather than sit there checked and lie about what is on the arch. Because the choice is per tooth, one crown can show a resultant while its neighbour shows components, which is why `constants.RESULTANT_COLOR` is **purple, not the red it used to be**: a resultant and an `Fz` on adjacent teeth would otherwise be the same arrow. Purple is free here — moment reuses it for `My`, and moment has no resultant.

  **Moment offers components only.** That is not the panel hiding a column; `MOMENT_GLYPH.resultant` is `None`, and `scale.resultant is None` is the single gate the view, the panel and the key all test. `GlyphScale.resultant_frac()` returns `None` for such a scale rather than raising.

  State lives in **`GlyphVisibility`**, keyed by `(Palmer designation, GlyphScale.quantity)` and owned by `ArchTab` — plain Python, no Qt, and asserted without a `QApplication` in `tests/test_glyph_visibility.py`. Force and moment keep **separate** records, so hiding `Fy` on LR6 says nothing about `My` on LR6 and switching modes and back restores exactly what was showing. Records are created on demand as "all three components, no resultant" (the view as it behaved before there were any toggles) and are never deleted, so a tooth that a live config edit unmaps and later remaps comes back with its flags. The dashboard builds `ArchTab` once and never rebuilds it, so the toggles survive switching tabs — **for the life of the process, not across restarts.** `ToothGlyphPanel` re-points its existing rows at the other quantity on a DATA switch (`retarget()` — `setText`, a colour restyle, and hiding the resultant column, which is built in both modes for exactly this reason) and only throws its widgets away and rebuilds them when the set of rows changes, i.e. on a config edit. The distinction is a measured one: a full rebuild of sixteen rows is ~60 ms of widget churn and stylesheet parsing on the laptop, so most of a second on the Pi, on a widget otherwise held to a 50 ms frame.

  Both modes go through `ArchView3D._glyph_vectors()`, which yields `(unit axis, 0–1 magnitude fraction, color, rank)` — everything downstream (the ramp, the arrow/curl choice, ring-glyph substitution, depth clamping) is shared, so components and resultant can't drift apart. It is the one place the flags decide what is **drawn**; `_glyph_primitives` never re-reads them. Three other places read the same flags to say what the drawing *means* — `ArchView3D.readouts()` colours the numbers, `ArchKeyBar._any_resultant()` decides whether the key carries the resultant's legend and ceiling, and `ToothGlyphPanel._sync_enabled()` pushes them back onto the buttons — all reads, never a second source of truth.

  The resultant has its **own, larger `hi`** (`ResultantSpec`: **5 N**) but shares the scale's `lo`. The key's ramp draws a second clamp sample, in the resultant's own colour, whenever some tooth is on its resultant — a maximum-length arrow means 3 N for a component and 5 N for a resultant, and the reader has to be able to tell which. A resultant reaches up to √3× a single component, so reusing the component `hi` would peg it at maximum length most of the time; sharing `lo` keeps "nothing drawn" meaning the same thing either way. `GlyphScale.frac()` and `resultant_frac()` are the same ramp with different ceilings.

  The button bar is **two rows** (camera presets above, DATA below) on purpose: all of them side by side put the tab's minimum width near 1000 px, wider than the small screens the Pi runs. The per-tooth toggles went into a right-hand column for the same reason — sixteen teeth would never fit in a bar, and the column scrolls.

  Screen budget is the constraint the whole layout is drawn against, so two sizes are deliberate rather than aesthetic. `ArchView3D`'s own minimum is small (360×260): the key bar and the toggle column are siblings now, so their heights *add* to the tab's minimum instead of being carved out of the arch's pane, and a floor sized for a comfortable arch would put the tab above the 800×480 screens the Pi runs. And `PANEL_W` is **derived** from the widgets in a row (margins + the Palmer label + four fixed-size buttons + their spacing), not chosen: pick a number below that and `setFixedWidth` beats the layout's minimum, and `QBoxLayout` makes up the difference by shrinking the widgets that asked for a fixed size — the Palmer label first, and `QLabel` clips rather than elides.

  **Camera.** `Oblique` / `Occlusal` / `Anterior` preset buttons plus free orbit (left-drag), wheel zoom (0.45×–4×), and double-click to reset. Orbiting off a preset switches the header to "Custom" and unhighlights the buttons (`ArchView3D.preset_left`). Pitch is clamped to **2°–89.5°** — always above the occlusal plane. The per-triangle depth sort no longer needs that (the old prism renderer painted the top face last, assuming it was the near face; a mesh has no single top face), but the clamp stays: teeth are only ever viewed from the occlusal side, and the flat dashed footprints of unmapped teeth degenerate to lines edge-on. The camera sits `CAM_DISTANCE = 7.0` world units out (the arch spans ~2 × 1.3) so a near tooth can't approach the eye and explode under the perspective divide; the view fits the pane by **scaling the projected coordinates**, never by dollying, so a longer lens costs nothing.

  **Fit.** `_Frame.fit()` projects `Arch.vertices()` — a baked convex hull of the whole arch, not every mesh vertex — takes the bounding box, and derives one px-per-image-unit scale (`FIT_MARGIN` leaves room for the arrows, which are deliberately **excluded** from the fit — otherwise the arch would breathe as forces grew). The resulting `_Frame` is the *only* thing carrying per-frame state: it owns the projector, the scale, the screen offsets, px-per-world-unit, and the arch `unit`, so no frame state is parked on the widget and every `_draw_*` helper takes it as an argument. It is **rebuilt, never cached from the last paint** — `ArchView3D._frame()` calls `fit` with `_view_rect()`, the same rect `paintEvent` uses, and the pick calls the same helper, so the two can never drift and a click arriving before a freshly shown tab's first paint still resolves. What *is* parked on the widget is a **memo of that pure function**, keyed on exactly what `fit` reads (`camera.yaw/pitch/target/distance`, `zoom`, `width`, `height`) — the fit hull is 2410 points, ~0.8 ms here and ~8 ms on a Pi, and the hover test calls `_frame()` per mouse-move. The invariant to hold: **anything `_Frame.fit` reads must appear in the key.** The memo also takes that cost off every idle repaint.

  **Paint order.** `_primitives()` yields `(depth, paint)` for every crown and arrow in one flat sequence, and `paintEvent` paints them sorted by descending depth. Rings are given `OVERLAY_DEPTH = -inf` so they sort last instead of needing a second list. All readings for a frame come from one store read, so the arrows and the panel's numeric readout can never show different samples — but the two are separate widgets now, so it takes a handshake rather than a shared paint pass: `ArchTab._refresh()` calls `ArchView3D.take_readings()`, prints the result in the panel, and the `update()` it then schedules paints that same parked sample. A paint that arrives without one (an orbit, a resize) simply reads fresh.

  **Picking.** Clicking a mapped crown opens that cell's graphs — `ArchView3D.tooth_at(pos)` resolves a screen point to a Palmer designation, `tooth_picked` carries the **cell index** out (the view owns `tooth_to_cell` and the hit test already consults it, so resolving there is what stops the tab that opens from disagreeing with the click), `ArchTab` relays it as `cell_picked`, and `Dashboard._show_cell` selects the cell *then* raises the tab — in that order and unconditionally, because `CellsTab.select` is a no-op when the cell is already current and a no-op emits nothing. The hit test is two-stage and runs **only on a mouse event**, never on the frame tick: a sound screen-bbox reject from `Tooth.box`, the crown's eight world-space AABB corners baked in `arch_model` beside the rest of its geometry (a perspective map sends a convex body to a convex image, so the box contains every projected vertex; measured at 0 escapes in 192,640), then an exact point-in-triangle over the survivors using `_draw_crown`'s own cull and triangles, nearest depth winning so an occluded crown loses to the one in front. Same projection, same cull, same triangles as the paint — **what you can click is by construction what you can see**, and structurally so: `_Frame.place_xy()` is the single world→screen mapping, with `place()` wrapping it for the painter, so there is no second copy of it to drift. Measured at 0.9 ms per pick with all 16 teeth mapped (~9 ms on a Pi), and 0.05 ms when the pointer is over empty canvas, which is the case hover pays for. Two consequences worth knowing: unmapped teeth are **not** targets (a footprint has no cell, and the same `palmer in tooth_to_cell` test the painter uses gates the pick), and a press/release inside `CLICK_SLOP` is a click while anything beyond it latches into an orbit — the dead zone suppresses the *orbit itself*, not just the classification, so a click with a few pixels of hand wobble can't knock the camera off its preset. `mouseDoubleClickEvent` skips its camera reset when the double-click lands on a crown, since the first release has already opened that cell. Hover is a **cursor change only** (`PICK_HOVER`, a documented lever like `CROWN_ANTIALIAS`); it runs the same `tooth_at` predicate as the click, because a cheaper predicate would promise clicks that do nothing.

  **Visibility** is back-face culling (drop triangles whose baked outward normal faces away from the eye) plus a painter's-algorithm depth sort. The sort is two-level and deliberately so: triangles order *within* a crown, while crowns and arrows order against each other in `_primitives`, which is what preserves the arrow depth clamp below. Flattening every triangle into the global list would be slower and would break that clamp. Two wrinkles worth knowing:
  - An arrow's depth key is **clamped to its own tooth's apex depth**, so an arrow can never be swallowed by the crown it grows out of — an intrusive `-Fz` points straight into the tooth body and would otherwise be invisible. Teeth nearer the camera still cover it.
  - An arrow aimed close to the **view axis** has almost no projected length, so its shaft would lie about magnitude. Below `AXIAL_MIN_FRAC` of its face-on length it is replaced by a **ring glyph** sized by magnitude — filled dot = pointing toward the viewer, ✗ = away (the convention the old 2D view used for z). A ring marks its own tooth's occlusal surface, so nothing of that tooth may cover it — hence `OVERLAY_DEPTH` (see "Paint order"). **Rings are arrow-only**, and the key hides that legend in moment mode: a curl's worst pose is the mirror of an arrow's. Aimed at the eye — where an arrow degenerates — a curl is a full face-on circle and reads best; it degenerates instead when its axis lies *in* the screen plane, where the circle projects to a short stroke. Nothing substitutes for it there; the whole circle is projected honestly rather than drawn as a fitted ellipse, so it foreshortens correctly through an orbit.
  - The three curls of one tooth all sort at its **apex depth**, so they never interleave with each other. `sorted` is stable and `_glyph_primitives` yields in `Tooth.frame` order, which puts the outermost circle on top of the ones it rings.

  **Where the geometry comes from.** `tools/bake_arch_mesh.py` turns per-tooth STLs into the asset, on a laptop, with `trimesh` + `numpy`. Two things about it are worth knowing before touching it:

  - **Orientation is derived from the tooth numbering, not from the mesh.** A scan arrives in an arbitrary pose and unit, and nothing in the file says which way is up. The baker takes `+x` from the lower-right molars toward the lower-left molars, `+y` from the incisors toward the molars, and `+z` as their cross product. That is robust in a way a principal-axis fit is not — PCA gives axes but not signs, and a sign error here silently points every force arrow the wrong way. Filenames are expected in Palmer form (`LL5.stl`), but Universal and FDI are accepted and translated; the scheme is decided across the whole filename set, never per file, because per file is impossible — `31` is Universal `LR7`, a molar, and FDI `LL1`, a central incisor, at opposite ends of the arch. What numbering cannot settle is chirality, and a determinant cannot either — `+z` is `e_x × e_y`, so the basis is right-handed however the scan was written. The geometry is what tells you, and `--report` ends with that test: crowns are broader than roots, so the wide end must land at `+z` (`crown check = 16 up / 0 down`). When it doesn't, `tools/arch_bake.json` carries two fixes that look identical from the geometry — **`mirror_z`** reflects world z and reverses triangle winding (a genuinely mirrored export; no rotation undoes a mirror), **`flip_z`** turns the arch 180° about `+y` (LL/LR filenames swapped). Both are folded into the single transform `load_and_orient()` applies before anything measures the mesh, so the crown cut, the oriented boxes and the baked face normals all see the corrected tooth. The current `assets_src` scans need `mirror_z`, and it is set.
  - **`unit` is pinned to the arch this view was tuned against.** Every arrow length, ring radius, origin-dot size and `CAM_DISTANCE` is a multiple of it, so the baker recovers `unit` from real centre-to-centre tooth spacing and aborts if it lands more than 25% off `0.20688`. Out-of-range almost always means the orientation or the numbering is wrong, not that the patient's arch is unusual.

  **Per-tooth frames are not baked yet.** Every tooth in the shipped asset shares `e_z = (0,0,1)` — `build_teeth` hard-codes it, and `e_y` comes from the global arch parabola, so `Tooth.frame` is a yaw-only rotation. `tools/tooth_frames.py` computes the real thing: each tooth's own x̂/ŷ/ẑ as the face normals of its **oriented** (minimum-volume, not axis-aligned) bounding box, plus a centre for the vectors to start from. It writes no asset and nothing under `graphDash/` reads it — it is the input to a later arch view where each tooth's arrows sit at the tooth centre and use that tooth's axes. Run it as `python3 tools/tooth_frames.py --stl-dir assets_src`; `--compare graphDash/assets/arch_mesh` prints the angle from the baked frame, `--json` writes the machine-readable form, and `tooth_frames()` is the function a later change calls. The per-tooth table and the sanity checks are not a mode — the checks run on every path, the JSON one included, and a hard geometric failure exits 1.

  The file has one seam and it is load-bearing: everything above the `--- scans ---` marker is arithmetic on plain 3-tuples, using `proj3d`'s vector helpers, with no trimesh, no numpy and no file access. That is what `tests/test_tooth_frames.py` exercises — against a synthetic box with a known tilt and yaw, so it keeps working when the scans in `assets_src/` are replaced. Below the marker is the adapter: `trimesh.bounds.oriented_bounds` is called in exactly one function. If a change to the pure half seems to want numpy, that is the signal it belongs below the seam instead.

  Two things about it were measured, not chosen, and re-deciding them without re-measuring will go wrong:
  - **The box encloses the whole tooth, root included.** A crown-only box fails on the anterior teeth — an incisor crown is a wedge, so its minimum-volume box aligns to the labial face instead of the tooth axis, and LR1/LL1 come out 43–46° off with the occlusal and bucco-lingual extents swapped. The root pins the axis: whole-tooth tilts run 10–15° on molars and 28–32° on incisors, which is what a real arch looks like.
  - **The centre comes from the crown, the axes from the whole tooth.** That whole-tooth box centres mid-root, below the gum line. So the top `--crown-frac` (default 0.45) of the tooth *along its own axis* supplies the origin. Changing the fraction moves only the centre, never the axes — `--crown-frac 0.30` and `0.60` are the check.

  The weak spot is yaw, and `--report` names it: a tooth whose box is nearly square in cross-section has two nearly interchangeable side faces, so the box can settle up to 45° off the arch's buccal direction and still be minimal. On the current scans LL3 and LL4 do exactly that (46° and 44°), which means `e_x` and `e_y` are plausibly swapped on those two. It also flags teeth that come out with identical frames — `assets_src/LL7.stl` and `LL8.stl` are byte-identical, as are `LR7.stl` and `LR8.stl`, so those pairs are placeholders.

  **Always look at a bake before trusting it** — `tests/test_arch_model.py` for the invariants, then the `render_arch.py` sweep sheet for inside-out crowns, then confirm `+y` buccal and `+z` out of the tooth against the physical brackets.

  **Performance.** The triangle budget is the one number that matters, and it is measured, not chosen: `tests/bench_arch.py --scaling` reports µs per triangle and the budget that fits a frame. On the dev laptop that is ~3.5 µs/triangle with a ~1.6 ms fixed overhead; a Pi 4 is roughly an order of magnitude slower, so **re-run it there** and pass the answer to `bake_arch_mesh.py --budget`. Three optimisations are already in and worth not undoing: crown vertices are projected once each rather than once per triangle corner; shading is a precomputed 32-level pen/brush table (`_shade_table`) rather than a `QColor` per triangle; and `CROWN_ANTIALIAS` is a documented switch — turning it off halves the cost per triangle at the price of a slightly jagged crown silhouette, and is the first lever to pull if the Pi measurement comes up short. After that: lower the budget and re-bake, then cache the crowns to a `QPixmap` keyed on camera pose (which costs the ability of a nearer tooth to occlude a farther tooth's arrow), then drop the refresh to 10 Hz.

  **The key is a landscape bar** (`ArchKeyBar`) across the top of the tab, not a column beside the arch — so the whole width of the view is arch and the whole right-hand column is toggles. It documents the axis colors and their anatomical directions, the magnitude ramp (headed "Length" for arrows, "Sweep" for curls, with a sample glyph at `lo` and at the clamp), the ring glyphs where they apply, and the notation notes. It reads the view rather than being told, since the scale, the tooth mapping and the visibility flags all change what it should say, and it is **painted** so it can reuse the very sample-glyph painters the arch draws with (`_key_arrow` / `_key_curl` / `_paint_ring`) — a key arrow that had drifted from a real arrow would be worse than no key.

  Its groups are measured and placed left to right, and one that would not fit is **dropped rather than clipped**, most-load-bearing first: a narrow bar loses the notation notes before the axis colours. Where it sits is measured too, not a breakpoint — `ArchTab._place_key()` compares `ArchKeyBar.preferred_width()` (~740 px in force mode, ~845 once a tooth is on its resultant — that adds a legend group) against the width left beside the button column **and the gap between them** and moves the bar to a row of its own when it doesn't fit. The gap is part of the sum: leaving it out claims a fit at widths where the bar is then handed less than it asked for and truncates silently, which is the outcome the measurement exists to prevent. The measurement is cached (`_key_needs`) and invalidated by the three things that can move it — a DATA switch, a resultant toggle, a config edit — so a window drag doesn't re-run it per pixel. On a wide laptop it sits beside the VIEW/DATA buttons; on the sub-1000 px screens the Pi runs it takes the second row, which is the only way all three axes survive.

  **The live per-tooth numbers are not in the key** — they print under each tooth's buttons in the toggle panel, next to the toggles that decide which of them are on the arch. Three signed components for a tooth in component mode, one unsigned magnitude for a tooth on its resultant, so the panel can be showing both at once. A component whose toggle is off keeps its number but is drawn in `theme.ON_SURFACE_SUBTLE`: still readable, visibly not on the arch. `ArchView3D.readouts()` resolves the text and colour, because the numbers have to say what the glyphs say; the panel only sets labels. The `|F|` legend row and its larger clamp value appear in the key only while some tooth is actually using them.

- **Position Vector** (`PositionVectorTab`): per-tooth-type position vector editor. Dropdown selects tooth type (central incisor, premolar, molar); editable fields for `rx`, `ry`, and `rz` (all mm). Changing the tooth type loads that type's current values. Edits take effect immediately on the next sample cycle. A "Reset to Defaults" button restores the selected type's factory values.
- **Sessions** (`SessionsTab`): paginated table (20 rows/page) of all recording sessions from the SQLite database. In-progress sessions show "— recording —" in red. Clicking a file path opens an inline CSV viewer (first 500 rows). Auto-refreshes every 2 seconds. Missing files show an error message instead of crashing.

### Deployment

`deploy.bat` is a Windows batch script the user runs manually to rsync-replace the whole directory on the Pi over SSH/SCP (`sparkrnd@raspberrypi.local`), then leaves the Pi to be run interactively. It is not invoked by Claude Code — treat it as user-owned tooling, not part of an automated pipeline.
