# Sensor drift fix — MMS101 offset temperature correction

Branch `driftFix`, commit `a2dab20`.

## The problem

Force/moment readings from the MMS101 load cells drifted continuously and never
settled. On a separate Mitsumi evaluation kit the same sensors drift for 2–3 minutes
after start and then stabilize. The drift was **mostly Fz**.

## Root cause

`graphDash/protocol.py` was sending this in the init handshake:

```python
self._cmd([CMD_INTERVAL, 0, 0, 0], 1)
```

`CMD_INTERVAL` (0x44) is **not a sample-rate setting**, which is how it had been read
until now — CLAUDE.md described this step as "set sample interval". Per the Conv.BD SDK
guide §10-4-10 it is the *automatic temperature-sensor update cadence* for the MMS101's
offset temperature correction:

> `0`: Temperature updates are not performed automatically.
> `N(>0)`: The temperature sensor value is updated after data is acquired N times.

Passing `0` explicitly disabled it. `RESTART` (0xC0), the manual "refresh the temperature
reference now" command, appears nowhere in the repo either.

Nothing in this protocol sets a sample rate at all — the Conv.BD always acquires at 1 ms
(SDK guide, State ID list, MEASURE: *"Updated sensor data at 1msec intervals"*).

### Why that produces drift that never settles

MMS101 datasheet p.22:

> The second and subsequent ADC data are subject to offset temperature correction using
> temperature sensor values acquired **during the first AD conversion**. This makes
> correction error larger with changes in ambient temperature, **requiring regular update
> of the temperature sensor values.**

So every sample after the first was corrected against the die temperature measured in the
single TempADC conversion at the instant `START` ran. The six AFEs then self-heat (up to
10 mA VDD each) alongside the Conv.BD's FPGA and two LDOs. The correction error tracks
that thermal excursion, and because the reference was never refreshed it never returned.

Mitsumi's own reference flow (SDK guide §10-10) is
`RESET → BOOT → COEFF → INTERVAL → START → DATA2… + RESTART at any timing → STOP`,
footnoted: *"The temperature sensor value for offset temperature correction is updated
according to the INTERVAL Command and RESTART Command."* Their pipeline refreshes; ours
did not.

### On the Fz-dominant symptom

A 2–5 minute settling period is **expected and correct**, and should survive this fix.
Datasheet p.15 documents Fz (AFE3) specifically:

> Drift occurs in Fz (AFE3) immediately after the AD conversion start. In this case, it is
> recommended that data is acquired after waiting approximately 5 min for stabilization.

That matches the observed symptom and is not something to engineer away. What the fix
addresses is the part that *never stopped*.

## What changed

### 1. Send a non-zero temperature-update interval

**`graphDash/constants.py`** — new constant, with the reasoning and an explicit note that
this is not a sample rate:

```python
TEMP_UPDATE_INTERVAL = 10_000
```

**`graphDash/protocol.py:79-81`** — big-endian 3-byte payload per SDK guide §10-3, still
before `START` per §10-10:

```python
n = TEMP_UPDATE_INTERVAL
self._cmd([CMD_INTERVAL, (n >> 16) & 0xFF, (n >> 8) & 0xFF, n & 0xFF], 1)
```

On the wire that is `0x44 0x00 0x27 0x10`.

**On the units of N.** It is a count, not a time — and it counts the Conv.BD's own 1 ms
polls of the sensor, *not* our `DATA2` reads. The guide's own sequence diagram (§10-10)
distinguishes the two: the Conv.BD → MMS101 transaction is labelled *"Request of data
acquisition"* and annotated **1 ms interval**, while the host side is *"DATA2 Command (any
timing) → Output the latest saved data"*. We only read what the board last latched, so we
never advance the counter. The documented range `0~10,000,000` agrees — ~2.8 hours at 1 ms,
a sensible ceiling, versus a nonsensical ~5.8 days at our 20 Hz.

So `10_000` ≈ a refresh every **10 seconds**, and it stays 10 seconds regardless of what
the sample-rate spinbox is set to.

**This is still an inference from the documentation, not a measured fact.** If the counter
turns out to be host reads after all, `10_000` would mean ~8.3 minutes at 20 Hz — far too
slow to hold the correction, and the refresh period would silently change with the UI rate.
See the verification section for the check that settles it.

Each refresh costs ~7.5 ms during which the AFE re-runs TempADC and its settling filter
(datasheet p.15), so `DATA2` returns a held value for ~7–8 device samples and then steps to
the newly-corrected value. At 10 s spacing that is a negligible duty cycle.

### 2. Stop discarding Measure Status

`read_all()` was slicing `r[3:21]` and throwing away `r[1]` and `r[2]`. Per SDK guide
§10-4-2 those two bytes are the **Measure Status** word (bit list §10-6): bit `b9` is
"Not Update — new data is not ready", bits `b0`–`b5` are per-axis NACKs from the MMS101.

**`graphDash/protocol.py:88-92`** now decodes them into `last_status`, `stale_count` and
`nack_count` on the `Sensor`. Counters only — a stale or NACKed frame is still returned,
because dropping samples there would be a new failure mode. This is what makes the refresh
cadence and any axis dropout observable, and it is how the interval units get confirmed.

### 3. Match the datasheet's integer shift

**`graphDash/protocol.py:99`** — `acc >> 11` replaces `int(acc / 2048)`.

The datasheet specifies an arithmetic right shift by 11 bits. `int()` truncates toward
zero; `>>` floors. Checked against the worked examples in datasheet Tables 5 and 6:

| Matrix operation data | `>> 11` | `int(acc / 2048)` | Datasheet |
|---|---|---|---|
| `E79607FF` | −200000 | −199999 | **−200000** |
| `FB1E07FF` | −40000 | −39999 | **−40000** |
| `FFFFFFFF` | −1 | 0 | **−1** |

`>>11` matches all 12 tabulated cases; `int(acc / 2048)` missed 3 by 1 LSB — e.g.
−200.000 N was being read as −199.999 N. A fixed ±1 LSB asymmetry across zero, not a drift
cause, but a real deviation from the reference implementation.

### 4. Documentation and the two streamers

- **`CLAUDE.md`** — the "set sample interval" description in the *Hardware protocol*
  section is corrected, and the Measure Status decode is documented.
- **`stream.py:65`, `stream_threaded.py:79`** — these carry the identical
  `[CMD_INTERVAL, 0, 0, 0]` line. Per agreed scope their behavior is unchanged; each gained
  a comment pointing at this fix. **They still have the bug.**

## What was verified

All laptop-side. `--debug` uses `DummySensor` and bypasses `Sensor` entirely, so none of
this exercises the hardware path.

- `tests/test_compensation.py` — output **byte-identical** to the base commit, same exit
  code. The `6/8 TESTS PASSED` / exit 1 is the pre-existing condition CLAUDE.md documents,
  confirmed against `main` by diff rather than assumed.
- `tests/test_smoothing.py` — 26/26.
- `tests/test_tooth_frames.py` — 35/35.
- The shift change checked against datasheet Tables 5 and 6 (above).
- `graphDash.py --debug` launches clean under `QT_QPA_PLATFORM=offscreen`.

## What is NOT verified — do this on the Pi

**The fix itself is unverified on hardware.**

1. **Confirm the interval is accepted.** `Sensor.init()` raises on any non-`0x00` status
   byte, so a payload the Conv.BD rejects as `0x83 Illegal Parameter` fails loudly at
   startup. A clean launch confirms the value is in range.

2. **Pin down the interval units.** Record ~2 minutes at rest and look for the ~7.5 ms
   held-value plateaus. Their spacing tells you what the counter actually counts:
   - ~10 s spacing → the 1 ms reading is right, leave `TEMP_UPDATE_INTERVAL` alone.
   - ~500 s spacing → it counts host `DATA2` reads at 20 Hz; drop the constant by ~50×.

   The new `stale_count` makes these easy to spot.

3. **The acceptance test.** Rig mechanically at rest and undisturbed, record **30+ minutes
   from a cold start**, before and after, and plot Fz against time.
   - Before: Fz walks monotonically for the whole run.
   - After: Fz drifts for the first 2–5 minutes, then flattens to within Fz's effective
     resolution of 0.06 N RMS.

   **Do not tare during the run** — tare is a static DC offset and would mask exactly what
   is being measured. Judge the result on the raw trend.

4. **Sanity-check the shift.** Apply a known load in both directions; readings should be
   symmetric about zero to within 1 LSB (0.001 N / 0.00001 N·m).

## Outstanding: `config/sensors.yaml`

**Not addressed, and it needs a decision before the acceptance test means anything.**

As committed, all four cells sit on `bus: 0, dev: 0, csb_gpio: 0`, two are both named
`Cell 3`, and `csb_gpio: 0` is not a chip-select line at all — `graphDash/csb.py:27` lists
`ALL_CSB_GPIOS = (26, 19, 13, 6, 5)` for CN1–CN5. As written, every cell asserts a GPIO
wired to no connector while all five real CSB lines stay parked high, so no Conv.BD is ever
selected.

If that was the config live during the drifting runs, those measurements have a second
explanation and the before/after comparison needs a correct config first. This was left
alone deliberately: CLAUDE.md is explicit that bus/GPIO numbers are physical wiring facts,
not bugs to fix without confirming.

**Also worth knowing:** launching the app can rewrite this file. A smoke test run of
`graphDash.py --debug --cells 3` during this work silently rewrote `sensors.yaml` and
dropped the fourth cell. It was caught in the diff and reverted, and is not in the commit.

## Deliberately not in scope

- **Periodic `RESTART` (0xC0) from the sampler thread.** The chosen approach is automatic
  refresh via `INTERVAL`, which is what Mitsumi's reference flow does and needs no
  sampler-thread work. Keep `RESTART` in reserve if the interval units turn out wrong.
- **Auto-zero / drift tracking in the pipeline.** Tare remains a one-shot DC snapshot; the
  moving average has unity DC gain and passes drift through untouched. Neither can track a
  baseline, and neither was made to.
- **Fixing `CMD_INTERVAL` in the two streamers.**
- **Rewriting `sensors.yaml`.**
