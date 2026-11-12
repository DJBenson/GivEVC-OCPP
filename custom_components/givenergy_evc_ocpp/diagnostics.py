"""Diagnostics support for GivEnergy EVC OCPP."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

TO_REDACT = {
    "rfid_tags",
    "transaction_id_tag",
    "last_authorize_request",
    "last_start_transaction_request",
    "last_stop_transaction_request",
    "last_update_firmware_request",
    "ocpp_frame_history",
    "charge_point_serial_number",
    "charge_box_serial_number",
    "local_ip_address",
    "websocket_remote_address",
    "firmware_server_host",
    "idTag",
    "location",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return config-entry diagnostics with sensitive fields redacted."""

    runtime = hass.data[DOMAIN][entry.entry_id]
    return async_redact_data(runtime.coordinator.as_diagnostics_dict(), TO_REDACT)
