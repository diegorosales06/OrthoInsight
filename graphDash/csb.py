"""Chip-select (CSB) pin bank for the MMS101 SPI1 AddOn Board.

All five connectors on the AddOn board share one SPI bus (SPI0: SCLK=GPIO11,
SDI=GPIO10, SDO=GPIO9). The only per-connector signal is CSB, and each
Conv.BD gates its MISO driver on it: U6 is a 74LVC2T45 whose DIR pin is tied
to that connector's CSB, so CSB high leaves the board's host-side SDO pin an
input (off the bus) and CSB low makes the board drive SDO push-pull.

There is no output enable. *Any* board whose CSB reads low owns the shared
MISO net, and on a Pi GPIO 13/19/26 (CN3/CN2/CN1) power up as inputs with
pull-downs -- i.e. CSB already asserted. So an unclaimed connector jams the
bus for every other cell on the board. The vendor's C sample avoids this by
exporting and driving all five CSB lines before it touches SPI; this module
is the equivalent.

Pins are claimed once and held for the life of the process. Releasing one
reverts it to a pulled-down input, which puts it straight back to jamming
the bus -- so nothing here ever closes a pin.
"""

try:
    from gpiozero import DigitalOutputDevice
except ImportError:
    DigitalOutputDevice = None

# CN1..CN5 CSB assignments, per the SDK user's guide (section 4-2).
ALL_CSB_GPIOS = (26, 19, 13, 6, 5)

_bank = {}


def _claim(gpio):
    if DigitalOutputDevice is None:
        raise RuntimeError(
            "CSB pins require gpiozero. "
            "Run with --debug to use the UI without hardware.")
    # active_high=False + initial_value=False => pin driven HIGH (deasserted)
    # at claim time, with no low glitch in between.
    return DigitalOutputDevice(gpio, active_high=False, initial_value=False)


def park_all(extra_gpios=()):
    """Drive every CSB line high (deasserted) before any SPI traffic.

    Covers all five AddOn-board connectors plus any extras from the config,
    so a connector that is populated but not configured can't hold the shared
    MISO net. Idempotent: pins already parked are left alone.
    """
    for gpio in tuple(ALL_CSB_GPIOS) + tuple(extra_gpios):
        if gpio not in _bank:
            _bank[gpio] = _claim(gpio)


def acquire(gpio):
    """The parked output for `gpio`, claiming it (deasserted) if needed."""
    if gpio not in _bank:
        _bank[gpio] = _claim(gpio)
    return _bank[gpio]
