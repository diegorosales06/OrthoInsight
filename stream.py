#!/usr/bin/env python3
"""
MMS101 multi-sensor force streamer (YAML-configured).
Reads sensor definitions from config/sensors.yaml so the number
of cells, SPI buses, and CSB GPIOs are never hardcoded.
Prereqs on the Pi:
    sudo apt install python3-spidev python3-gpiozero python3-lgpio python3-yaml
    Enable SPI0: sudo raspi-config -> Interface Options -> SPI -> Yes
    Enable SPI6: add 'dtoverlay=spi6-1cs' to /boot/firmware/config.txt, reboot
Press Ctrl-C to stop.
"""
import time
import signal
import pathlib
import yaml
import spidev
from gpiozero import DigitalOutputDevice
# ── MMS101 commands (SDK manual section 10-4) ────────────────────────
CMD_START, CMD_DATA2, CMD_BOOT   = 0xF0, 0xE2, 0xB0
CMD_STOP,  CMD_RESET, CMD_STATUS = 0xB2, 0xB4, 0x80
CMD_INTERVAL                      = 0x44
CMD_COEFF = (0x30, 0x32, 0x34, 0x36, 0x38, 0x3A)  # Fx,Fy,Fz,Mx,My,Mz
STT_STANDBY, STT_READY = 1, 3
def s24(b):
    """3 big-endian bytes -> signed int."""
    v = (b[0] << 16) | (b[1] << 8) | b[2]
    return v - 0x1000000 if v & 0x800000 else v
class Sensor:
    def __init__(self, name, bus, dev, csb_pin):
        self.name = name
        self.spi = spidev.SpiDev()
        self.spi.open(bus, dev)
        self.spi.mode = 0b11              # SPI mode 3
        self.spi.max_speed_hz = 2_000_000
        self.csb = csb_pin                # already output-HIGH
        self.coeff = [[0]*6 for _ in range(6)]
        self.spi.xfer2([0x00])            # dummy clock (SDK note)
    def _xfer(self, tx, rx_len):
        """One CSB-framed transaction: send tx, then clock rx_len bytes."""
        self.csb.on()                     # CSB LOW (active)
        try:
            if tx:
                self.spi.xfer2(list(tx))
            return bytes(self.spi.xfer2([0]*rx_len)) if rx_len else b""
        finally:
            self.csb.off()                # CSB HIGH (idle)
    def _cmd(self, tx, rx_len):
        r = self._xfer(tx, rx_len)
        if r[0] != 0x00:
            raise RuntimeError(f"{self.name}: cmd 0x{tx[0]:02X} -> status 0x{r[0]:02X}")
        return r
    def _wait(self, state):
        for _ in range(10):
            time.sleep(0.02)
            if self._cmd([CMD_STATUS], 4)[3] == state:
                return
        raise RuntimeError(f"{self.name}: timeout waiting for state {state}")
    def init(self):
        self._cmd([CMD_RESET], 1);   self._wait(STT_STANDBY)
        self._cmd([CMD_BOOT],  1);   self._wait(STT_READY)
        for axis in range(6):
            r = self._cmd([CMD_COEFF[axis]], 19)
            for k in range(6):
                self.coeff[axis][k] = s24(r[1+k*3 : 4+k*3])
        # NOTE: 0 disables the MMS101's automatic offset-temperature-correction
        # refresh (INTERVAL is that cadence, not a sample rate), which freezes the
        # correction at START and drifts without settling. Fixed in
        # graphDash/protocol.py; left alone here -- see graphDash/constants.py
        # TEMP_UPDATE_INTERVAL for the reasoning.
        self._cmd([CMD_INTERVAL, 0, 0, 0], 1)
        self._cmd([CMD_START], 1)
        time.sleep(0.01)
    def read_forces(self):
        """Returns (Fx, Fy, Fz) in Newtons."""
        r = self._cmd([CMD_DATA2], 21)
        adc = [s24(r[3+k*3 : 6+k*3]) for k in range(6)]
        out = []
        for axis in range(3):          # only Fx, Fy, Fz
            acc = sum(c*a for c, a in zip(self.coeff[axis], adc))
            out.append(int(acc / 2048) / 1000.0)   # >>11, then /1000 -> N
        return out
    def stop(self):
        try: self._cmd([CMD_STOP], 1)
        except Exception: pass
        self.spi.close()
        self.csb.close()
# ── Config loading ───────────────────────────────────────────────────
def load_sensors(yaml_path):
    """Build a list of Sensor objects from a YAML config file.
    All CSB pins are claimed as output-HIGH *before* any SPI bus is
    opened, so no converter board can drive MISO during construction.
    """
    cfg = yaml.safe_load(pathlib.Path(yaml_path).read_text())
    entries = cfg["sensors"]
    # Phase 1 — park every CSB HIGH so nothing floats during SPI setup
    csb_pins = {}
    for entry in entries:
        gpio = entry["csb_gpio"]
        if gpio not in csb_pins:
            csb_pins[gpio] = DigitalOutputDevice(
                gpio, active_high=False, initial_value=False)  # HIGH
    # Phase 2 — build Sensor objects, passing the pre-claimed pin
    sensors = []
    for entry in entries:
        sensors.append(Sensor(
            name     = entry["name"],
            bus      = entry["bus"],
            dev      = entry["dev"],
            csb_pin  = csb_pins[entry["csb_gpio"]],
        ))
    return sensors
# ── Main ─────────────────────────────────────────────────────────────
def main():
    config_path = pathlib.Path(__file__).parent / "config" / "sensors.yaml"
    sensors = load_sensors(config_path)
    for s in sensors:
        print(f"Initializing {s.name}...")
        s.init()
    print(f"\nStreaming {len(sensors)} sensor(s)  (Ctrl-C to stop)\n")
    running = [True]
    signal.signal(signal.SIGINT, lambda *_: running.__setitem__(0, False))
    cycle_times = []
    try:
        while running[0]:
            t_start = time.perf_counter()
            forces = [s.read_forces() for s in sensors]
            t_cycle = (time.perf_counter() - t_start) * 1000  # ms
            cycle_times.append(t_cycle)
            parts = [
                f"{s.name}: Fx={f[0]:+7.3f} Fy={f[1]:+7.3f} Fz={f[2]:+7.3f} N"
                for s, f in zip(sensors, forces)
            ]
            print("   |   ".join(parts) + f"   [{t_cycle:.2f} ms]")
            time.sleep(0.1)
    finally:
        print("\nStopping...")
        for s in sensors:
            s.stop()
        if cycle_times:
            avg = sum(cycle_times) / len(cycle_times)
            print(f"\n── Timing Summary ──")
            print(f"Cycles:  {len(cycle_times)}")
            print(f"Average: {avg:.3f} ms")
            print(f"Min:     {min(cycle_times):.3f} ms")
            print(f"Max:     {max(cycle_times):.3f} ms")
main()
