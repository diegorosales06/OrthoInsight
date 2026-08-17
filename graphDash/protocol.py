import time

try:
    import spidev
    from gpiozero import DigitalOutputDevice
except ImportError:
    spidev = None
    DigitalOutputDevice = None
import numpy as np

from graphDash.constants import (
    CMD_START, CMD_DATA2, CMD_BOOT, CMD_STOP, CMD_RESET, CMD_STATUS,
    CMD_INTERVAL, CMD_COEFF, STT_STANDBY, STT_READY, N_AXES,
)


def s24(b):
    v = (b[0] << 16) | (b[1] << 8) | b[2]
    return v - 0x1000000 if v & 0x800000 else v


class Sensor:
    def __init__(self, name, bus, dev, csb_gpio):
        if spidev is None or DigitalOutputDevice is None:
            raise RuntimeError(
                "Real sensors require spidev and gpiozero. "
                "Run with --debug to use the UI without hardware.")
        self.name = name
        self.spi = spidev.SpiDev()
        self.spi.open(bus, dev)
        self.spi.mode = 0b11
        self.spi.max_speed_hz = 2_000_000
        self.csb = DigitalOutputDevice(csb_gpio, active_high=False,
                                       initial_value=False)
        self.coeff = [[0]*6 for _ in range(6)]
        self.spi.xfer2([0x00])

    def _xfer(self, tx, rx_len):
        self.csb.on()
        try:
            if tx:
                self.spi.xfer2(list(tx))
            return bytes(self.spi.xfer2([0]*rx_len)) if rx_len else b""
        finally:
            self.csb.off()

    def _cmd(self, tx, rx_len):
        r = self._xfer(tx, rx_len)
        if r[0] != 0x00:
            raise RuntimeError(f"{self.name}: cmd 0x{tx[0]:02X} -> 0x{r[0]:02X}")
        return r

    def _wait(self, state):
        for _ in range(10):
            time.sleep(0.02)
            if self._cmd([CMD_STATUS], 4)[3] == state:
                return
        raise RuntimeError(f"{self.name}: timeout waiting for state {state}")

    def init(self):
        self._cmd([CMD_RESET], 1);  self._wait(STT_STANDBY)
        self._cmd([CMD_BOOT],  1);  self._wait(STT_READY)
        for axis in range(6):
            r = self._cmd([CMD_COEFF[axis]], 19)
            for k in range(6):
                self.coeff[axis][k] = s24(r[1+k*3 : 4+k*3])
        self._cmd([CMD_INTERVAL, 0, 0, 0], 1)
        self._cmd([CMD_START], 1)
        time.sleep(0.01)

    def read_all(self):
        """Returns [Fx, Fy, Fz, Mx, My, Mz] in [N, N, N, N*m, N*m, N*m]."""
        r = self._cmd([CMD_DATA2], 21)
        adc = [s24(r[3+k*3 : 6+k*3]) for k in range(6)]
        out = []
        for axis in range(6):
            acc = sum(c * a for c, a in zip(self.coeff[axis], adc))
            shifted = int(acc / 2048)
            scale = 1000.0 if axis < 3 else 100000.0
            out.append(shifted / scale)
        return out

    def stop(self):
        try: self._cmd([CMD_STOP], 1)
        except Exception: pass
        self.spi.close()
        self.csb.close()


class DummySensor:
    def __init__(self, name, amplitude=1.0, frequency=0.25, phase=0.0):
        self.name = name
        self.start_time = time.time()
        self.amplitude = amplitude
        self.frequency = frequency
        self.phase = phase

    def read_all(self):
        t = time.time() - self.start_time
        values = []
        # for axis in range(N_AXES):
        #     base = np.sin(2 * np.pi * self.frequency * t + self.phase + axis * 0.5)
        #     noise = 0.02 * np.sin(2 * np.pi * (self.frequency * 3) * t + axis)
        #     values.append(float(self.amplitude * (base + noise)))


        vals = {
            "Fx": 0.114,
            "Fy": -0.044,
            "Fz": -3.846,
            "Mx": -0.04416,
            "My": -0.00292,
            "Mz": -0.00018
        }
        values.append(vals["Fx"]) # Fx
        values.append(vals["Fy"]) # Fy
        values.append(vals["Fz"]) # Fz
        values.append(vals["Mx"]) # Mx
        values.append(vals["My"]) # My
        values.append(vals["Mz"]) # Mz
        
        return values

    def stop(self):
        pass
        self.csb.close()


