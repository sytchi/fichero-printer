"""Constants for the Fichero Label Printer integration."""

DOMAIN = "fichero_printer"
PLATFORMS = ["sensor"]

CONF_ADDRESS = "address"
CONF_SWITCHBOT_ENTITY = "switchbot_entity"
CONF_STARTUP_DELAY = "startup_delay"
CONF_LABEL_LENGTH = "label_length"
CONF_DENSITY = "density"
CONF_POWER_OFF_ON_DISCONNECT = "power_off_on_disconnect"
CONF_AI_TASK_ENTITY = "ai_task_entity"

DEFAULT_STARTUP_DELAY = 3.0
DEFAULT_LABEL_LENGTH = 30
DEFAULT_DENSITY = 2
DEFAULT_MARGIN_MM = 1.0

SERVICE_CONNECT = "connect"
SERVICE_DISCONNECT = "disconnect"
SERVICE_PRINT = "print_label"
SERVICE_SAVE_FAVORITE = "save_favorite"
SERVICE_DELETE_FAVORITE = "delete_favorite"

CARD_URL = "/fichero-printer/fichero-printer-card.js"
