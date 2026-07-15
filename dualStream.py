#!/usr/bin/env python3
"""
Dual MMS101 force streamer.

Cell 1: SPI0 (/dev/spidev0.0), CSB = GPIO13
Cell 2: SPI6 (/dev/spidev6.0), CSB = GPIO12

Prereqs on the Pi:
    sudo apt install python3-spidev python3-gpiozero python3-lgpio
    Enable SPI0: sudo raspi-config -> Interface Options -> SPI -> Yes
    Enable SPI6: add 'dtoverlay=spi6-1cs' to /boot/firmware/config.txt, reboot

Press Ctrl-C to stop.
"""

import time
import signal
import spidev
from gpiozero import DigitalOutputDevice

# MMS101 commands (SDK manual section 10-4)
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
    def __init__(self, name, bus, dev, csb_gpio):
        self.name = name
        self.spi = spidev.SpiDev()
        self.spi.open(bus, dev)
        self.spi.mode = 0b11              # SPI mode 3
        self.spi.max_speed_hz = 2_000_000
        self.csb = DigitalOutputDevice(csb_gpio, active_high=False,
                                       initial_value=False)  # idle HIGH
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


def main():
    cell1 = Sensor("Cell 1", bus=0, dev=0, csb_gpio=13)
    cell2 = Sensor("Cell 2", bus=6, dev=0, csb_gpio=12)
    cell3 = Sensor("Cell 3", bus=0, dev=0, csb_gpio=26)
    cell4 = Sensor("Cell 4", bus=6, dev=0, csb_gpio=16)

    print("Initializing Cell 1..."); cell1.init()
    print("Initializing Cell 2..."); cell2.init()
    print("Initializing Cell 3..."); cell3.init()
    print("Initializing Cell 4..."); cell4.init()
    print("\nStreaming (Ctrl-C to stop)\n")

    running = [True]
    signal.signal(signal.SIGINT, lambda *_: running.__setitem__(0, False))

    try:
        while running[0]:
            f1 = cell1.read_forces()
            f2 = cell2.read_forces()
            f3 = cell3.read_forces()
            f4 = cell4.read_forces()
            print(f"Cell 1: Fx={f1[0]:+7.3f} Fy={f1[1]:+7.3f} Fz={f1[2]:+7.3f} N"
                  f"   |   "
                  f"Cell 2: Fx={f2[0]:+7.3f} Fy={f2[1]:+7.3f} Fz={f2[2]:+7.3f} N"
                  f"   |   "
                  f"Cell 3: Fx={f3[0]:+7.3f} Fy={f3[1]:+7.3f} Fz={f3[2]:+7.3f} N"
                  f"   |   "
                  f"Cell 4: Fx={f4[0]:+7.3f} Fy={f4[1]:+7.3f} Fz={f4[2]:+7.3f} N")
            time.sleep(0.1)
    finally:
        print("\nStopping...")
        cell1.stop()
        cell2.stop()
        cell3.stop()
        cell4.stop()

main()