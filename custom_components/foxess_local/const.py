"""Constants for the FoxESS Local integration."""

DOMAIN = "foxess_local"
DEFAULT_PORT = 14431
SIGNAL_UPDATE = f"{DOMAIN}_update"

# Raw diagnostic register sensors to expose for labelling in HA history.
RAW_REG_START = 0x7918
RAW_REG_END = 0x7957
