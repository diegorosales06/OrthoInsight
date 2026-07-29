# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

OrthoInsight reads force/moment (Fx, Fy, Fz, Mx, My, Mz) data from MMS101 6-axis load cells over SPI on a Raspberry Pi, and displays it in a real-time PyQt6/PyQtGraph dashboard. There is no build system, package manifest, or test suite — this is a small hardware-lab tool, not a packaged application.

## Running the code

There is no `requirements.txt`. Dependencies are installed system-wide on the Pi:

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

`--debug` mode works on any machine (spidev/gpiozero import failures are caught), so it's the way to iterate on UI/plotting/logging logic without a Pi. The dashboard also has a "Debug Mode" toggle button that flips `Sampler.simulate` at runtime.

The optional C rewrite of the streaming logic:

```bash
make            # builds ./stream_c from stream.c (needs libgpiod, libyaml)
make clean
```

There is no lint or test command configured — verify changes by running `graphDash.py --debug` and exercising the UI (see the project instructions on testing UI changes in a browser/simulator — for this repo that means actually launching the PyQt app and checking the plots/controls).

## Architecture

### Hardware protocol (repeated in every entry point)

The MMS101 SPI command protocol is duplicated verbatim across `graphDash.py`, `pi_sender.py`, `cell_dashboard.py`, `stream.py`, `stream_threaded.py`, and re-implemented in C in `stream.c`. Each defines its own `Sensor` class with the same command bytes (`CMD_START`/`CMD_DATA2`/`CMD_BOOT`/etc.), the same `s24()` 3-byte-signed-int decoder, and the same init handshake: RESET → wait STANDBY → BOOT → wait READY → read 6x6 calibration coefficient matrix (`CMD_COEFF`) → set sample interval → START.

**When fixing a protocol bug, check whether it needs to be fixed in all of these files** — there is no shared module. `graphDash.py` is the canonical/actively-maintained version (reads all 6 axes, force + moment); the others are earlier/parallel variants that read only Fx/Fy/Fz:
- `cell_dashboard.py` — Pi-only, hardcoded 4-cell config, no networking.
- `pi_sender.py` + `laptop_dashboard.py` — split architecture: Pi reads sensors and streams newline-delimited JSON over TCP (port 5555); a separate laptop process renders the UI. Used when running PyQt directly on the Pi is impractical.
- `stream.py` / `stream_threaded.py` — headless CLI streamers (print-to-console, with cycle-timing stats) instead of a GUI; `stream_threaded.py` parallelizes reads with one worker thread per SPI bus via `ThreadPoolExecutor`.
- `stream.c` — C port of the same protocol using libgpiod v2 for GPIO and Linux `spidev` ioctls directly, with its own YAML parser calls via libyaml.

### `graphDash.py` internals (main entry point)

- `Sensor` — real hardware wrapper (raises if `spidev`/`gpiozero` aren't importable).
- `DummySensor` — generates sine-wave + noise data per axis; used in `--debug` mode and by the "Debug Mode" UI toggle.
- `DataStore` — thread-safe ring buffer (`collections.deque`, `MAX_BUFFER_SAMPLES=5000`) holding timestamps and per-cell/per-axis readings. `get_cell()` supports windowing to the last N seconds and always returns time relative to the window's first sample.
- `CSVLogger` — background `threading.Thread` that buffers samples from a `Queue` and periodically flushes rows to `~/OrthoInsightLogs/<date>/log_<timestamp>.csv`. Only runs when *not* in `--debug` mode. Log directory is currently hardcoded to `/home/sparkrnd/OrthoInsightLogs`.
- `Sampler` — background thread that polls all cells (real or simulated) at `rate_hz`, pushes readings into `DataStore`, and forwards them to `CSVLogger`. `running` gates whether it's actively sampling; `simulate` gates real vs. dummy sensors — both are toggled live from the UI.
- `CellTab` — one PyQt6 tab per load cell: force plot, moment plot, live readouts, tare/clear-tare (snapshots current reading as an offset, subtracted on future reads), and a causal moving-average smoother (`_moving_avg`, no lookahead — safe for real-time display).
- `Dashboard` — main window: control bar (start/stop, sample rate, rolling window size, moving-average window, debug toggle, clear data) plus the per-cell tabs. A `QTimer` fires every `REFRESH_MS` (50 ms) to redraw all tabs from `DataStore`.
- `main()` wires it together: loads `config/sensors.yaml`, builds either real `Sensor`s or `DummySensor`s (cell names/count come from the YAML unless `--debug --cells N` overrides it), starts `CSVLogger` + `Sampler` threads, then runs the Qt event loop.

### Sensor configuration

`config/sensors.yaml` lists cells as `{name, bus, dev, csb_gpio}` (SPI bus/device and the GPIO used for chip-select). Bus/GPIO numbers here are physical wiring facts about the current rig — don't "fix" values that look inconsistent (e.g. two cells sharing a bus) without confirming with the user, since they reflect actual hardware, not bugs. `pi_sender.py` and `cell_dashboard.py` hardcode a 4-cell config in source instead of reading this YAML.

### Deployment

`deploy.bat` is a Windows batch script the user runs manually to rsync-replace the whole directory on the Pi over SSH/SCP (`sparkrnd@raspberrypi.local`), then leaves the Pi to be run interactively. It is not invoked by Claude Code — treat it as user-owned tooling, not part of an automated pipeline.
