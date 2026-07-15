#!/usr/bin/env python3
"""
Pi-side streamer. Reads 4 MMS101 load cells and pushes JSON lines
over TCP to the laptop dashboard.

Wire protocol: newline-delimited JSON, one message per sample:
    {"t": <unix_time>, "cells": [[Fx,Fy,Fz], [..], [..], [..]]}

Run on the Pi:
    python3 pi_sender.py

Then start the laptop dashboard. Ctrl-C to stop.
"""

import time
import signal
import socket
import json
import spidev
from gpiozero import DigitalOutputDevice

# ---------- MMS101 constants (same as cellStream.py) ----------
CMD_START, CMD_DATA2, CMD_BOOT   = 0xF0, 0xE2, 0xB0
CMD_STOP,  CMD_RESET, CMD_STATUS = 0xB2, 0xB4, 0x80
CMD_INTERVAL                     = 0x44
CMD_COEFF = (0x30, 0x32, 0x34, 0x36, 0x38, 0x3A)

STT_STANDBY, STT_READY = 1, 3

HOST = "0.0.0.0"   # listen on all interfaces (USB gadget + Ethernet)
PORT = 5555
RATE_HZ = 20       # send rate


def s24(b):
    v = (b[0] << 16) | (b[1] << 8) | b[2]
    return v - 0x1000000 if v & 0x800000 else v


class Sensor:
    def __init__(self, name, bus, dev, csb_gpio):
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
        r = self._cmd([CMD_DATA2], 21)
        adc = [s24(r[3+k*3 : 6+k*3]) for k in range(6)]
        out = []
        for axis in range(3):
            acc = sum(c*a for c, a in zip(self.coeff[axis], adc))
            out.append(int(acc / 2048) / 1000.0)
        return out

    def stop(self):
        try: self._cmd([CMD_STOP], 1)
        except Exception: pass
        self.spi.close()
        self.csb.close()


def main():
    cells = [
        Sensor("Cell 1", bus=0, dev=0, csb_gpio=13),
        Sensor("Cell 2", bus=6, dev=0, csb_gpio=12),
        Sensor("Cell 3", bus=0, dev=0, csb_gpio=26),
        Sensor("Cell 4", bus=6, dev=0, csb_gpio=16),
    ]
    for c in cells:
        print(f"Initializing {c.name}...")
        c.init()

    running = [True]
    signal.signal(signal.SIGINT, lambda *_: running.__setitem__(0, False))

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    srv.listen(1)
    srv.settimeout(1.0)  # so Ctrl-C is responsive between clients
    print(f"\nListening on {HOST}:{PORT}. Waiting for laptop...\n")

    period = 1.0 / RATE_HZ

    try:
        while running[0]:
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                continue
            print(f"Connected: {addr}")
            conn.settimeout(None)
            try:
                while running[0]:
                    t0 = time.time()
                    msg = {
                        "t": t0,
                        "cells": [c.read_forces() for c in cells],
                    }
                    conn.sendall((json.dumps(msg) + "\n").encode())
                    elapsed = time.time() - t0
                    if elapsed < period:
                        time.sleep(period - elapsed)
            except (BrokenPipeError, ConnectionResetError, OSError) as e:
                print(f"Laptop disconnected ({e}). Waiting for reconnect...")
            finally:
                conn.close()
    finally:
        print("\nStopping...")
        srv.close()
        for c in cells:
            c.stop()


main()
