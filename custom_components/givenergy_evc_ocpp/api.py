"""REST API views for the GivEnergy EVC OCPP integration.

Exposes two authenticated endpoints on Home Assistant's existing HTTP server
(default port 8123) so that the GivLocal mobile app can read charger state and
send commands without any additional ports or services.

Endpoints
---------
GET  /api/givenergy_evc_ocpp/status
    Returns state for every accepted charger, keyed by charge_point_id.

GET  /api/givenergy_evc_ocpp/status/{charge_point_id}
    Returns state for a single charger.

    Query parameters:
        diagnostics=true  — include large internal fields (OCPP frame history,
                            raw configuration dump, firmware event log, etc.)
                            Omitted by default to keep responses manageable.

POST /api/givenergy_evc_ocpp/command
    Execute a command on a charger.

    Body (JSON):
        {
          "charge_point_id": "CE1234",   // optional; defaults to primary
          "action": "start_charging",    // see COMMANDS below
          "params": {}                   // action-specific parameters
        }

All endpoints require a long-lived HA access token:
    Authorization: Bearer <token>
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import logging
from typing import Any

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Fields excluded from the default status response because they are either
# internal coordinator bookkeeping or large diagnostic payloads. Pass
# ?diagnostics=true to include them.
_DIAGNOSTIC_FIELDS: frozenset[str] = frozenset(
    {
        "ocpp_frame_history",
        "configuration",
        "firmware_manifest_entries",
        "firmware_server_events",
        "rejected_charge_points",
        "unsupported_ocpp_actions",
        "meter_samples",
        "parsed_meter_values",
        "last_meter_values",
        "last_boot_notification",
        "last_status_notification",
        "last_get_configuration",
        "last_update_firmware_request",
        "last_command_results",
        "last_message_response_payload",
        "last_authorize_request",
        "last_authorize_response",
        "last_start_transaction_request",
        "last_start_transaction_response",
        "last_stop_transaction_request",
        "last_stop_transaction_response",
        "last_call_error",
        "path_charge_point_id",
    }
)


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _serialize(obj: Any) -> Any:
    """Recursively convert non-JSON-safe types."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize(v) for v in obj]
    if isinstance(obj, set):
        return sorted(_serialize(v) for v in obj)
    return obj


def _state_to_dict(coordinator, *, include_diagnostics: bool = False) -> dict[str, Any]:
    """Serialise coordinator state to a JSON-safe dict."""
    raw = asdict(coordinator.data)
    if not include_diagnostics:
        raw = {k: v for k, v in raw.items() if k not in _DIAGNOSTIC_FIELDS}
    return _serialize(raw)


def _get_hub(hass: HomeAssistant):
    """Return the GivEnergyChargePointHub from hass.data, or None."""
    for value in hass.data.get(DOMAIN, {}).values():
        if hasattr(value, "hub"):
            return value.hub
    return None


# ---------------------------------------------------------------------------
# Status view
# ---------------------------------------------------------------------------


class GivEnergyEvcApiStatusView(HomeAssistantView):
    """Serve full charger state for one or all chargers.

    GET /api/givenergy_evc_ocpp/status
        → { "<charge_point_id>": { ...state... }, ... }

    GET /api/givenergy_evc_ocpp/status/<charge_point_id>
        → { ...state... }

    Append ?diagnostics=true to include internal/large diagnostic fields.
    """

    url = "/api/givenergy_evc_ocpp/status"
    name = "api:givenergy_evc_ocpp:status"
    extra_urls = ["/api/givenergy_evc_ocpp/status/{charge_point_id}"]

    async def get(
        self,
        request: web.Request,
        charge_point_id: str | None = None,
    ) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        hub = _get_hub(hass)
        if hub is None:
            return self.json_message("Integration not loaded", status_code=503)

        include_diagnostics = request.rel_url.query.get("diagnostics", "").lower() == "true"

        if charge_point_id:
            coordinator = hub.get_charge_point_coordinator(charge_point_id)
            if coordinator is None:
                return self.json_message(
                    f"Charge point {charge_point_id!r} not found",
                    status_code=404,
                )
            return self.json(
                _state_to_dict(coordinator, include_diagnostics=include_diagnostics)
            )

        result: dict[str, Any] = {}
        all_coordinators = [
            hub.primary_coordinator,
            *hub.accepted_secondary_coordinators(),
        ]
        for coordinator in all_coordinators:
            cp_id = coordinator.data.charge_point_id or "primary"
            result[cp_id] = _state_to_dict(
                coordinator, include_diagnostics=include_diagnostics
            )
        return self.json(result)


# ---------------------------------------------------------------------------
# Command view
# ---------------------------------------------------------------------------


class GivEnergyEvcApiCommandView(HomeAssistantView):
    """Execute a command on a charger.

    POST /api/givenergy_evc_ocpp/command
    Body: { "charge_point_id": "...", "action": "...", "params": {} }

    Returns: { "ok": true, "result": <command response or null> }

    Supported actions and their params
    -----------------------------------
    start_charging          — no params
    stop_charging           — no params
    set_current_limit       — { "current_a": float }
    set_max_import_capacity — { "amperage": int }
    set_charge_mode         — { "mode": "SuperEco|Eco|Boost|ModbusSlave" }
    set_plug_and_go         — { "enabled": bool }
    set_charger_enabled     — { "enabled": bool }
    set_local_modbus        — { "enabled": bool }
    set_front_panel_leds    — { "enabled": bool }
    set_randomised_delay    — { "seconds": int }
    set_suspended_timeout   — { "seconds": int }
    unlock_connector        — { "connector_id": int }  (default: 1)
    reset                   — { "type": "Soft|Hard" }  (default: "Soft")
    factory_reset           — no params
    read_cp_voltage         — no params
    remote_start            — { "id_tag": str?, "connector_id": int?,
                                "charging_profile": dict? }
    remote_stop             — { "transaction_id": int? }
    set_charging_profile    — { "connector_id": int, "charging_profile": dict }
    clear_charging_profile  — { "connector_id": int?, "charging_profile_id": int?,
                                "stack_level": int?,
                                "charging_profile_purpose": str? }
    set_charging_schedule   — { "days": ["mon",...], "start": "HH:MM",
                                "end": "HH:MM", "limit_a": int }
    clear_charging_schedule — no params
    add_rfid_tag            — { "id_tag": str, "expiry_date": str? }
    remove_rfid_tag         — { "id_tag": str }
    change_configuration    — { "key": str, "value": str }
    get_configuration       — { "keys": [str]? }
    change_availability     — { "operative": bool }
    trigger_message         — { "requested_message": str, "connector_id": int? }
    update_firmware         — { "location": str, "retrieve_date": str,
                                "retries": int?, "retry_interval": int? }
    install_firmware        — no params
    """

    url = "/api/givenergy_evc_ocpp/command"
    name = "api:givenergy_evc_ocpp:command"

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        hub = _get_hub(hass)
        if hub is None:
            return self.json_message("Integration not loaded", status_code=503)

        try:
            body = await request.json()
        except Exception:
            return self.json_message("Invalid JSON body", status_code=400)

        action: str | None = body.get("action")
        params: dict[str, Any] = body.get("params") or {}
        cp_id: str | None = body.get("charge_point_id")

        if not action:
            return self.json_message("Missing 'action'", status_code=400)

        try:
            coordinator = hub.resolve_service_target(cp_id)
        except HomeAssistantError as err:
            return self.json_message(str(err), status_code=404)

        try:
            result = await _dispatch(coordinator, action, params)
        except _UnknownAction:
            return self.json_message(f"Unknown action: {action!r}", status_code=400)
        except HomeAssistantError as err:
            _LOGGER.warning("Command %r failed: %s", action, err)
            return self.json_message(str(err), status_code=502)
        except (KeyError, TypeError, ValueError) as err:
            return self.json_message(f"Invalid params: {err}", status_code=400)

        return self.json(
            {"ok": True, "result": _serialize(result) if result is not None else None}
        )


# ---------------------------------------------------------------------------
# Command dispatcher
# ---------------------------------------------------------------------------


class _UnknownAction(Exception):
    pass


async def _dispatch(coordinator, action: str, params: dict[str, Any]) -> Any:
    """Route an action string to the appropriate coordinator method."""

    match action:
        # — Charging control ------------------------------------------------
        case "start_charging":
            return await coordinator.async_start_charging()

        case "stop_charging":
            return await coordinator.async_stop_charging()

        case "remote_start":
            return await coordinator.async_remote_start_transaction(
                id_tag=params.get("id_tag"),
                connector_id=params.get("connector_id"),
                charging_profile=params.get("charging_profile"),
            )

        case "remote_stop":
            return await coordinator.async_remote_stop_transaction(
                transaction_id=params.get("transaction_id"),
            )

        # — Current & capacity ----------------------------------------------
        case "set_current_limit":
            return await coordinator.async_set_current_limit(float(params["current_a"]))

        case "set_max_import_capacity":
            return await coordinator.async_set_max_import_capacity(int(params["amperage"]))

        # — Mode & behaviour ------------------------------------------------
        case "set_charge_mode":
            return await coordinator.async_set_charge_mode(str(params["mode"]))

        case "set_plug_and_go":
            return await coordinator.async_set_plug_and_go_enabled(bool(params["enabled"]))

        case "set_charger_enabled":
            return await coordinator.async_change_availability(bool(params["enabled"]))

        case "set_local_modbus":
            return await coordinator.async_set_local_modbus_enabled(bool(params["enabled"]))

        case "set_front_panel_leds":
            return await coordinator.async_set_front_panel_leds_enabled(bool(params["enabled"]))

        case "set_randomised_delay":
            return await coordinator.async_set_randomised_delay_duration(int(params["seconds"]))

        case "set_suspended_timeout":
            return await coordinator.async_set_suspended_state_timeout(int(params["seconds"]))

        # — Connector & availability ----------------------------------------
        case "unlock_connector":
            return await coordinator.async_unlock_connector(
                int(params.get("connector_id", 1))
            )

        case "change_availability":
            return await coordinator.async_change_availability(bool(params["operative"]))

        # — OCPP profiles & schedules ---------------------------------------
        case "set_charging_profile":
            return await coordinator.async_set_charging_profile(
                connector_id=int(params["connector_id"]),
                charging_profile=params["charging_profile"],
            )

        case "clear_charging_profile":
            return await coordinator.async_clear_charging_profile(
                connector_id=params.get("connector_id"),
                charging_profile_id=params.get("charging_profile_id"),
                stack_level=params.get("stack_level"),
                charging_profile_purpose=params.get("charging_profile_purpose"),
            )

        case "set_charging_schedule":
            return await coordinator.async_set_charging_schedule(
                days=params.get("days", []),
                start=str(params["start"]),
                end=str(params["end"]),
                limit_a=int(params["limit_a"]),
            )

        case "clear_charging_schedule":
            return await coordinator.async_clear_charging_schedule()

        # — RFID tags -------------------------------------------------------
        case "add_rfid_tag":
            return await coordinator.async_add_rfid_tag(
                id_tag=str(params["id_tag"]),
                expiry_date=params.get("expiry_date"),
            )

        case "remove_rfid_tag":
            return await coordinator.async_remove_rfid_tag(id_tag=str(params["id_tag"]))

        # — Raw OCPP configuration ------------------------------------------
        case "change_configuration":
            return await coordinator.async_change_configuration(
                str(params["key"]), str(params["value"])
            )

        case "get_configuration":
            return await coordinator.async_refresh_configuration(params.get("keys"))

        # — Device management -----------------------------------------------
        case "reset":
            return await coordinator.async_reset(str(params.get("type", "Soft")))

        case "factory_reset":
            return await coordinator.async_factory_reset()

        case "read_cp_voltage":
            return await coordinator.async_read_cp_voltage_and_duty_cycle()

        case "trigger_message":
            return await coordinator.async_trigger_message(
                str(params["requested_message"]),
                connector_id=params.get("connector_id"),
            )

        # — Firmware --------------------------------------------------------
        case "update_firmware":
            return await coordinator.async_update_firmware(
                location=str(params["location"]),
                retrieve_date=str(params["retrieve_date"]),
                retries=params.get("retries"),
                retry_interval=params.get("retry_interval"),
            )

        case "install_firmware":
            return await coordinator.async_install_selected_firmware()

        case _:
            raise _UnknownAction(action)
