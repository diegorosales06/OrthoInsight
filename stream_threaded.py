#!/usr/bin/env python3
"""
MMS101 multi-sensor force streamer (YAML-configured).

Threaded version:
- One worker thread per SPI bus.
- Measures per-cell read times.
- Measures total cycle time.
"""

import time
import signal
import pathlib
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import yaml
import spidev
from gpiozero import DigitalOutputDevice

CMD_START, CMD_DATA2, CMD_BOOT = 0xF0, 0xE2, 0xB0
CMD_STOP, CMD_RESET, CMD_STATUS = 0xB2, 0xB4, 0x80
CMD_INTERVAL = 0x44
CMD_COEFF = (0x30, 0x32, 0x34, 0x36, 0x38, 0x3A)

STT_STANDBY, STT_READY = 1, 3


def s24(b):
    v = (b[0] << 16) | (b[1] << 8) | b[2]
    return v - 0x1000000 if v & 0x800000 else v


class Sensor:
    def __init__(self, name, bus, dev, csb_pin):
        self.name = name
        self.bus = bus
        self.spi = spidev.SpiDev()
        self.spi.open(bus, dev)
        self.spi.mode = 0b11
        self.spi.max_speed_hz = 2_000_000
        self.csb = csb_pin
        self.coeff = [[0] * 6 for _ in range(6)]
        self.spi.xfer2([0x00])

    def _xfer(self, tx, rx_len):
        self.csb.on()
        try:
            if tx:
                self.spi.xfer2(list(tx))
            return bytes(self.spi.xfer2([0] * rx_len)) if rx_len else b''
        finally:
            self.csb.off()

    def _cmd(self, tx, rx_len):
        r = self._xfer(tx, rx_len)
        if r[0] != 0x00:
            raise RuntimeError(f'{self.name}: cmd 0x{tx[0]:02X} -> status 0x{r[0]:02X}')
        return r

    def _wait(self, state):
        for _ in range(10):
            time.sleep(0.02)
            if self._cmd([CMD_STATUS], 4)[3] == state:
                return
        raise RuntimeError(f'{self.name}: timeout waiting for state {state}')

    def init(self):
        self._cmd([CMD_RESET], 1)
        self._wait(STT_STANDBY)
        self._cmd([CMD_BOOT], 1)
        self._wait(STT_READY)

        for axis in range(6):
            r = self._cmd([CMD_COEFF[axis]], 19)
            for k in range(6):
                self.coeff[axis][k] = s24(r[1 + k * 3:4 + k * 3])

        self._cmd([CMD_INTERVAL, 0, 0, 0], 1)
        self._cmd([CMD_START], 1)
        time.sleep(0.01)

    def read_forces(self):
        r = self._cmd([CMD_DATA2], 21)
        adc = [s24(r[3 + k * 3:6 + k * 3]) for k in range(6)]

        out = []
        for axis in range(3):
            acc = sum(c * a for c, a in zip(self.coeff[axis], adc))
            out.append(int(acc / 2048) / 1000.0)
        return out

    def stop(self):
        try:
            self._cmd([CMD_STOP], 1)
        except Exception:
            pass
        self.spi.close()
        self.csb.close()


def load_sensors(yaml_path):
    cfg = yaml.safe_load(pathlib.Path(yaml_path).read_text())
    entries = cfg['sensors']

    csb_pins = {}
    for entry in entries:
        gpio = entry['csb_gpio']
        if gpio not in csb_pins:
            csb_pins[gpio] = DigitalOutputDevice(
                gpio,
                active_high=False,
                initial_value=False
            )

    return [
        Sensor(
            entry['name'],
            entry['bus'],
            entry['dev'],
            csb_pins[entry['csb_gpio']]
        )
        for entry in entries
    ]


def read_bus(sensor_list):
    results = []
    for sensor in sensor_list:
        t0 = time.perf_counter()
        force = sensor.read_forces()
        ms = (time.perf_counter() - t0) * 1000
        results.append((sensor, force, ms))
    return results


def main():
    config_path = pathlib.Path(__file__).parent / "config" / "sensors.yaml"
    sensors = load_sensors(config_path)

    for s in sensors:
        print(f'Initializing {s.name}...')
        s.init()

    bus_groups = defaultdict(list)
    for s in sensors:
        bus_groups[s.bus].append(s)

    print(f'\nStreaming {len(sensors)} sensor(s) using {len(bus_groups)} SPI bus worker(s)\n')

    executor = ThreadPoolExecutor(max_workers=len(bus_groups))
    running = [True]
    signal.signal(signal.SIGINT, lambda *_: running.__setitem__(0, False))

    cycle_times = []

    try:
        while running[0]:
            cycle_start = time.perf_counter()

            futures = [
                executor.submit(read_bus, sensor_group)
                for sensor_group in bus_groups.values()
            ]

            results = []
            for future in futures:
                results.extend(future.result())

            cycle_ms = (time.perf_counter() - cycle_start) * 1000
            cycle_times.append(cycle_ms)

            parts = []
            for sensor, force, read_ms in results:
                parts.append(
                    f'{sensor.name}: '
                    f'Fx={force[0]:+7.3f} '
                    f'Fy={force[1]:+7.3f} '
                    f'Fz={force[2]:+7.3f}N '
                    f'[read={read_ms:.3f} ms]'
                )

            print(' | '.join(parts) + f' | TOTAL_CYCLE={cycle_ms:.3f} ms')
            time.sleep(0.1)

    finally:
        executor.shutdown(wait=True)

        print('\nStopping...')
        for s in sensors:
            s.stop()

        if cycle_times:
            print('\n── Timing Summary ──')
            print(f'Cycles:  {len(cycle_times)}')
            print(f'Average: {sum(cycle_times)/len(cycle_times):.3f} ms')
            print(f'Min:     {min(cycle_times):.3f} ms')
            print(f'Max:     {max(cycle_times):.3f} ms')


if __name__ == '__main__':
    main()
