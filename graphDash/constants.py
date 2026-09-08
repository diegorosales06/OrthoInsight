# ---------- MMS101 constants ----------
CMD_START, CMD_DATA2, CMD_BOOT   = 0xF0, 0xE2, 0xB0
CMD_STOP,  CMD_RESET, CMD_STATUS = 0xB2, 0xB4, 0x80
CMD_INTERVAL                     = 0x44
CMD_COEFF = (0x30, 0x32, 0x34, 0x36, 0x38, 0x3A)
STT_STANDBY, STT_READY = 1, 3

FORCE_AXES   = ("Fx", "Fy", "Fz")
# Colorblind-safe palette (matplotlib tab10), harmonizes with baby blue theme
FORCE_COLORS = ("#1F77B4", "#2CA02C", "#D62728")  # blue, green, red
MOMENT_AXES   = ("Mx", "My", "Mz")
MOMENT_COLORS = ("#FF7F0E", "#9467BD", "#17BECF")  # orange, purple, cyan
# Arch view only: the single vector-sum arrow one tooth can show in place of its
# three component arrows.
#
# It used to be the same red as Fz, which was harmless while the whole arch
# showed either components or resultants. It no longer is: the toggles are
# per-tooth, so one crown's resultant and its neighbour's Fz can be the only two
# arrows on screen, and if both are red neither says which it is. Purple is the
# remaining tab10 hue that is not a force colour -- moment reuses it for My, but
# moment has no resultant, so the two never appear together.
RESULTANT_COLOR = "#9467BD"
ALL_AXES = FORCE_AXES + MOMENT_AXES
N_AXES = 6

DEFAULT_RATE_HZ    = 20
DEFAULT_WINDOW_S   = 10
REFRESH_MS         = 50
MAX_BUFFER_SAMPLES = 5000

# Moving-average window, in SECONDS (converted to a sample count using the live
# sample rate). 0 = filter off.
DEFAULT_SMOOTH_S   = 1
MAX_SMOOTH_S       = 60
