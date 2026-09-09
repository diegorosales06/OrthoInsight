# ---------- MMS101 constants ----------
CMD_START, CMD_DATA2, CMD_BOOT   = 0xF0, 0xE2, 0xB0
CMD_STOP,  CMD_RESET, CMD_STATUS = 0xB2, 0xB4, 0x80
CMD_INTERVAL                     = 0x44
CMD_COEFF = (0x30, 0x32, 0x34, 0x36, 0x38, 0x3A)
STT_STANDBY, STT_READY = 1, 3

# INTERVAL (0x44) payload: how many device-side data acquisitions between automatic
# refreshes of the MMS101's offset temperature correction reference. This is NOT a
# sample rate -- the Conv.BD always acquires at 1 ms (SDK guide, State ID list, MEASURE)
# and nothing in this protocol sets a sample rate at all.
#
# 0 disables refreshes entirely, and that is what this code used to send. The MMS101
# then corrects every sample against the die temperature measured in the single TempADC
# conversion at START (datasheet p.22), so as the AFEs self-heat the correction error
# grows and never comes back -- drift that never settles. Mitsumi's reference flow
# (SDK guide 10-10) sets INTERVAL before START for exactly this reason.
#
# Units: the guide says "after data is acquired N times" without naming the counter, but
# the documented range 0~10,000,000 is ~2.8 h at the device's 1 ms cadence and ~5.8 days
# at our 20 Hz host reads, so it counts device acquisitions. 10_000 ~= every 10 s.
# Each refresh costs ~7.5 ms of held-over data while the AFE re-runs TempADC and its
# settling filter, so don't set this so low that those plateaus dominate.
TEMP_UPDATE_INTERVAL = 10_000

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
