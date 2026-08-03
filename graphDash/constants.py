# ---------- MMS101 constants ----------
CMD_START, CMD_DATA2, CMD_BOOT   = 0xF0, 0xE2, 0xB0
CMD_STOP,  CMD_RESET, CMD_STATUS = 0xB2, 0xB4, 0x80
CMD_INTERVAL                     = 0x44
CMD_COEFF = (0x30, 0x32, 0x34, 0x36, 0x38, 0x3A)
STT_STANDBY, STT_READY = 1, 3

FORCE_AXES   = ("Fx", "Fy", "Fz")
FORCE_COLORS = ("#e74c3c", "#2ecc71", "#3498db")  # red, green, blue
MOMENT_AXES   = ("Mx", "My", "Mz")
MOMENT_COLORS = ("#e67e22", "#9b59b6", "#1abc9c")  # orange, purple, teal
ALL_AXES = FORCE_AXES + MOMENT_AXES
N_AXES = 3  # Force axes only; moments are calculated

DEFAULT_RATE_HZ    = 20
DEFAULT_WINDOW_S   = 10
REFRESH_MS         = 50
MAX_BUFFER_SAMPLES = 5000
