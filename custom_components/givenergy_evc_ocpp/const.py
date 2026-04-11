"""Constants for the GivEnergy EVC OCPP integration."""

from homeassistant.const import Platform

DOMAIN = "givenergy_evc_ocpp"
TITLE = "GivEnergy EVC OCPP"

DEFAULT_LISTEN_HOST = "0.0.0.0"
DEFAULT_LISTEN_PORT = 7655
DEFAULT_ADOPT_FIRST_CHARGER = True
DEFAULT_DEBUG_LOGGING = False
DEFAULT_ENHANCED_LOGGING = False
DEFAULT_COMMAND_TIMEOUT = 20
DEFAULT_METER_VALUE_SAMPLE_INTERVAL = 15
DEFAULT_FIRMWARE_FTP_PORT = 2121
DEFAULT_FIRMWARE_FTP_PASSIVE_PORT_START = 30000
DEFAULT_FIRMWARE_FTP_PASSIVE_PORT_END = 30009
MAX_STORED_OCPP_FRAMES = 500
DEFAULT_EVSE_MIN_CURRENT = 6.0
DEFAULT_EVSE_MAX_CURRENT = 32.0

CONF_LISTEN_PORT = "listen_port"
CONF_FIRMWARE_FTP_PORT = "firmware_ftp_port"
CONF_FIRMWARE_FTP_PASSIVE_PORT_START = "firmware_ftp_passive_port_start"
CONF_FIRMWARE_FTP_PASSIVE_PORT_END = "firmware_ftp_passive_port_end"
CONF_EXPECTED_CHARGE_POINT_ID = "expected_charge_point_id"
CONF_ADOPT_FIRST_CHARGER = "adopt_first_charger"
CONF_DEBUG_LOGGING = "debug_logging"
CONF_ENHANCED_LOGGING = "enhanced_logging"
CONF_COMMAND_TIMEOUT = "command_timeout_seconds"
CONF_METER_VALUE_SAMPLE_INTERVAL = "meter_value_sample_interval_seconds"

DEFAULT_REMOTE_ID_TAG = "HA-REMOTE"

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SWITCH,
]

SERVICE_RESET = "reset"
SERVICE_UNLOCK_CONNECTOR = "unlock_connector"
SERVICE_TRIGGER_MESSAGE = "trigger_message"
SERVICE_GET_CONFIGURATION = "get_configuration"
SERVICE_CHANGE_CONFIGURATION = "change_configuration"
SERVICE_REMOTE_START_TRANSACTION = "remote_start_transaction"
SERVICE_REMOTE_STOP_TRANSACTION = "remote_stop_transaction"
SERVICE_SET_CHARGING_PROFILE = "set_charging_profile"
SERVICE_CLEAR_CHARGING_PROFILE = "clear_charging_profile"
SERVICE_CHANGE_AVAILABILITY = "change_availability"
SERVICE_UPDATE_FIRMWARE = "update_firmware"

ATTR_ENTRY_ID = "entry_id"

WEBSOCKET_SUBPROTOCOL = "ocpp1.6"

GIVENERGY_CHARGE_MODES: tuple[str, ...] = (
    "SuperEco",
    "Eco",
    "Boost",
    "ModbusSlave",
)
