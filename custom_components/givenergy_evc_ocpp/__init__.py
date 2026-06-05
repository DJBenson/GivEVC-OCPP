"""GivEnergy EVC OCPP integration."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import socket
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.device_registry import DeviceEntry

from .const import (
    ATTR_CHARGE_POINT_ID,
    ATTR_ENTRY_ID,
    CONF_FIRMWARE_SERVER_ENABLED,
    DOMAIN,
    PLATFORMS,
    SERVICE_ADD_RFID_TAG,
    SERVICE_CHANGE_AVAILABILITY,
    SERVICE_CHANGE_CONFIGURATION,
    SERVICE_CLEAR_CHARGING_PROFILE,
    SERVICE_CLEAR_CHARGING_SCHEDULE,
    SERVICE_GET_CONFIGURATION,
    SERVICE_REMOTE_START_TRANSACTION,
    SERVICE_REMOTE_STOP_TRANSACTION,
    SERVICE_REMOVE_RFID_TAG,
    SERVICE_RESET,
    SERVICE_SET_CHARGING_PROFILE,
    SERVICE_SET_CHARGING_SCHEDULE,
    SERVICE_TRIGGER_MESSAGE,
    SERVICE_UNLOCK_CONNECTOR,
    SERVICE_UPDATE_FIRMWARE,
)
from . import api as _api
from .coordinator import GivEnergyEvcCoordinator
from .firmware_transfer_server import GivEnergyFirmwareTransferServer
from .hub import GivEnergyChargePointHub
from .server import GivEnergyOcppServer

_LOGGER = logging.getLogger(__name__)

RELOAD_STATE_KEY = "reload_state"


@dataclass(slots=True)
class GivEnergyRuntimeData:
    """Runtime data stored against the config entry."""

    hub: GivEnergyChargePointHub
    coordinator: GivEnergyEvcCoordinator
    server: GivEnergyOcppServer
    firmware_server: GivEnergyFirmwareTransferServer
    zeroconf_info: Any = field(default=None)


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the integration domain."""

    del config
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the config entry."""

    coordinator = GivEnergyEvcCoordinator(hass, entry, legacy_entity_ids=True)
    await coordinator.async_restore_persisted_state()
    reload_state = hass.data[DOMAIN].get(RELOAD_STATE_KEY, {}).pop(entry.entry_id, None)
    coordinator.restore_reload_state(reload_state)
    coordinator.data.firmware_server_enabled = bool(
        entry.options.get(
            CONF_FIRMWARE_SERVER_ENABLED,
            entry.data.get(CONF_FIRMWARE_SERVER_ENABLED, False),
        )
    )
    hub = GivEnergyChargePointHub(hass, entry, coordinator)
    await hub.async_restore_persisted_state()
    server = GivEnergyOcppServer(hass, hub)
    firmware_server = GivEnergyFirmwareTransferServer(
        hass, coordinator.firmware_directory
    )
    hub.attach_server(server)
    hub.attach_firmware_server(firmware_server)

    await coordinator.async_start()
    await hub.async_start()

    runtime_data = GivEnergyRuntimeData(
        hub=hub,
        coordinator=coordinator,
        server=server,
        firmware_server=firmware_server,
    )
    entry.runtime_data = runtime_data
    hass.data[DOMAIN][entry.entry_id] = runtime_data

    await _async_register_services(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Start the server only after all platform listeners are registered so that
    # any charger connecting immediately after startup fires the
    # SIGNAL_ACCEPTED_CHARGE_POINT into listeners that are already in place.
    try:
        await server.async_start()
    except OSError as err:
        raise ConfigEntryNotReady(
            f"Unable to listen on configured port {coordinator.listen_port}: {err}"
        ) from err

    # Register REST API views once for the lifetime of the HA process.
    # Views look up runtime data dynamically so they survive entry reloads.
    if not hass.data[DOMAIN].get("views_registered"):
        hass.http.register_view(_api.GivEnergyEvcApiStatusView())
        hass.http.register_view(_api.GivEnergyEvcApiCommandView())
        hass.data[DOMAIN]["views_registered"] = True

    # Advertise via mDNS so the GivLocal app can discover us on the LAN.
    runtime_data.zeroconf_info = await _async_register_zeroconf(hass, entry)

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    runtime: GivEnergyRuntimeData = entry.runtime_data

    hass.data[DOMAIN].setdefault(RELOAD_STATE_KEY, {})[entry.entry_id] = (
        runtime.coordinator.export_reload_state()
    )

    if runtime.zeroconf_info is not None:
        await _async_unregister_zeroconf(hass, runtime.zeroconf_info)

    await runtime.server.async_stop()
    await runtime.firmware_server.async_stop()
    await runtime.hub.async_stop()
    await runtime.coordinator.async_stop()

    hass.data[DOMAIN].pop(entry.entry_id, None)
    if not hass.data[DOMAIN]:
        _async_unregister_services(hass)

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry."""

    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Allow removing secondary chargers from the shared hub entry."""

    runtime: GivEnergyRuntimeData = entry.runtime_data
    charge_point_id = runtime.hub.charge_point_id_from_device(device_entry)
    if charge_point_id is None:
        return False
    return await runtime.hub.async_remove_charge_point(charge_point_id)


async def _async_register_services(hass: HomeAssistant) -> None:
    """Register domain services."""

    if hass.data[DOMAIN].get("services_registered"):
        return

    async def async_handle_reset(call: ServiceCall) -> None:
        runtime = _resolve_runtime(hass, call.data.get(ATTR_ENTRY_ID))
        coordinator = runtime.hub.resolve_service_target(call.data.get("charge_point_id"))
        await coordinator.async_reset(call.data["type"])

    async def async_handle_trigger(call: ServiceCall) -> None:
        runtime = _resolve_runtime(hass, call.data.get(ATTR_ENTRY_ID))
        coordinator = runtime.hub.resolve_service_target(call.data.get("charge_point_id"))
        await coordinator.async_trigger_message(
            call.data["requested_message"],
            connector_id=call.data.get("connector_id"),
        )

    async def async_handle_unlock_connector(call: ServiceCall) -> None:
        runtime = _resolve_runtime(hass, call.data.get(ATTR_ENTRY_ID))
        coordinator = runtime.hub.resolve_service_target(call.data.get("charge_point_id"))
        await coordinator.async_unlock_connector(
            connector_id=call.data.get("connector_id", 1)
        )

    async def async_handle_get_configuration(call: ServiceCall) -> dict[str, Any]:
        runtime = _resolve_runtime(hass, call.data.get(ATTR_ENTRY_ID))
        coordinator = runtime.hub.resolve_service_target(call.data.get("charge_point_id"))
        return await coordinator.async_refresh_configuration(
            call.data.get("keys")
        )

    async def async_handle_change_configuration(call: ServiceCall) -> None:
        runtime = _resolve_runtime(hass, call.data.get(ATTR_ENTRY_ID))
        coordinator = runtime.hub.resolve_service_target(call.data.get("charge_point_id"))
        await coordinator.async_change_configuration(
            call.data["key"],
            call.data["value"],
        )

    async def async_handle_remote_start(call: ServiceCall) -> None:
        runtime = _resolve_runtime(hass, call.data.get(ATTR_ENTRY_ID))
        coordinator = runtime.hub.resolve_service_target(call.data.get("charge_point_id"))
        await coordinator.async_remote_start_transaction(
            id_tag=call.data.get("id_tag"),
            connector_id=call.data.get("connector_id"),
            charging_profile=call.data.get("charging_profile"),
        )

    async def async_handle_remote_stop(call: ServiceCall) -> None:
        runtime = _resolve_runtime(hass, call.data.get(ATTR_ENTRY_ID))
        coordinator = runtime.hub.resolve_service_target(call.data.get("charge_point_id"))
        await coordinator.async_remote_stop_transaction(
            transaction_id=call.data.get("transaction_id"),
        )

    async def async_handle_set_profile(call: ServiceCall) -> None:
        runtime = _resolve_runtime(hass, call.data.get(ATTR_ENTRY_ID))
        coordinator = runtime.hub.resolve_service_target(call.data.get("charge_point_id"))
        await coordinator.async_set_charging_profile(
            connector_id=call.data["connector_id"],
            charging_profile=call.data["charging_profile"],
        )

    async def async_handle_clear_profile(call: ServiceCall) -> None:
        runtime = _resolve_runtime(hass, call.data.get(ATTR_ENTRY_ID))
        coordinator = runtime.hub.resolve_service_target(call.data.get("charge_point_id"))
        await coordinator.async_clear_charging_profile(
            connector_id=call.data.get("connector_id"),
            charging_profile_id=call.data.get("charging_profile_id"),
            stack_level=call.data.get("stack_level"),
            charging_profile_purpose=call.data.get("charging_profile_purpose"),
        )

    async def async_handle_change_availability(call: ServiceCall) -> None:
        runtime = _resolve_runtime(hass, call.data.get(ATTR_ENTRY_ID))
        coordinator = runtime.hub.resolve_service_target(call.data.get("charge_point_id"))
        await coordinator.async_change_availability(call.data["operative"])

    async def async_handle_update_firmware(call: ServiceCall) -> None:
        runtime = _resolve_runtime(hass, call.data.get(ATTR_ENTRY_ID))
        coordinator = runtime.hub.resolve_service_target(call.data.get("charge_point_id"))
        await coordinator.async_update_firmware(
            location=call.data["location"],
            retrieve_date=call.data["retrieve_date"],
            retries=call.data.get("retries"),
            retry_interval=call.data.get("retry_interval"),
        )

    async def async_handle_set_charging_schedule(
        call: ServiceCall,
    ) -> dict[str, Any]:
        runtime = _resolve_runtime(hass, call.data.get(ATTR_ENTRY_ID))
        coordinator = runtime.hub.resolve_service_target(call.data.get("charge_point_id"))
        show_ocpp_output = call.data.get("show_ocpp_output", False)
        result = await coordinator.async_set_charging_schedule(
            days=call.data.get("days", []),
            start=call.data["start"],
            end=call.data["end"],
            limit_a=call.data["limit_a"],
            show_ocpp_output=show_ocpp_output,
        )
        if show_ocpp_output:
            return result
        return {}

    async def async_handle_clear_charging_schedule(call: ServiceCall) -> None:
        runtime = _resolve_runtime(hass, call.data.get(ATTR_ENTRY_ID))
        coordinator = runtime.hub.resolve_service_target(call.data.get("charge_point_id"))
        await coordinator.async_clear_charging_schedule()

    async def async_handle_add_rfid_tag(call: ServiceCall) -> None:
        runtime = _resolve_runtime(hass, call.data.get(ATTR_ENTRY_ID))
        coordinator = runtime.hub.resolve_service_target(call.data.get("charge_point_id"))
        await coordinator.async_add_rfid_tag(
            id_tag=call.data["id_tag"],
            expiry_date=call.data.get("expiry_date"),
        )

    async def async_handle_remove_rfid_tag(call: ServiceCall) -> None:
        runtime = _resolve_runtime(hass, call.data.get(ATTR_ENTRY_ID))
        coordinator = runtime.hub.resolve_service_target(call.data.get("charge_point_id"))
        await coordinator.async_remove_rfid_tag(
            id_tag=call.data["id_tag"],
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_RESET,
        async_handle_reset,
        schema=vol.Schema(
            {
                vol.Optional(ATTR_ENTRY_ID): cv.string,
                vol.Optional(ATTR_CHARGE_POINT_ID): cv.string,
                vol.Required("type"): vol.In({"Soft", "Hard"}),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_TRIGGER_MESSAGE,
        async_handle_trigger,
        schema=vol.Schema(
            {
                vol.Optional(ATTR_ENTRY_ID): cv.string,
                vol.Optional(ATTR_CHARGE_POINT_ID): cv.string,
                vol.Required("requested_message"): cv.string,
                vol.Optional("connector_id"): vol.Coerce(int),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UNLOCK_CONNECTOR,
        async_handle_unlock_connector,
        schema=vol.Schema(
            {
                vol.Optional(ATTR_ENTRY_ID): cv.string,
                vol.Optional(ATTR_CHARGE_POINT_ID): cv.string,
                vol.Optional("connector_id", default=1): vol.Coerce(int),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_CONFIGURATION,
        async_handle_get_configuration,
        schema=vol.Schema(
            {
                vol.Optional(ATTR_ENTRY_ID): cv.string,
                vol.Optional(ATTR_CHARGE_POINT_ID): cv.string,
                vol.Optional("keys"): [cv.string],
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CHANGE_CONFIGURATION,
        async_handle_change_configuration,
        schema=vol.Schema(
            {
                vol.Optional(ATTR_ENTRY_ID): cv.string,
                vol.Optional(ATTR_CHARGE_POINT_ID): cv.string,
                vol.Required("key"): cv.string,
                vol.Required("value"): cv.string,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REMOTE_START_TRANSACTION,
        async_handle_remote_start,
        schema=vol.Schema(
            {
                vol.Optional(ATTR_ENTRY_ID): cv.string,
                vol.Optional(ATTR_CHARGE_POINT_ID): cv.string,
                vol.Optional("id_tag"): cv.string,
                vol.Optional("connector_id"): vol.Coerce(int),
                vol.Optional("charging_profile"): dict,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REMOTE_STOP_TRANSACTION,
        async_handle_remote_stop,
        schema=vol.Schema(
            {
                vol.Optional(ATTR_ENTRY_ID): cv.string,
                vol.Optional(ATTR_CHARGE_POINT_ID): cv.string,
                vol.Optional("transaction_id"): vol.Coerce(int),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_CHARGING_PROFILE,
        async_handle_set_profile,
        schema=vol.Schema(
            {
                vol.Optional(ATTR_ENTRY_ID): cv.string,
                vol.Optional(ATTR_CHARGE_POINT_ID): cv.string,
                vol.Required("connector_id"): vol.Coerce(int),
                vol.Required("charging_profile"): dict,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CLEAR_CHARGING_PROFILE,
        async_handle_clear_profile,
        schema=vol.Schema(
            {
                vol.Optional(ATTR_ENTRY_ID): cv.string,
                vol.Optional(ATTR_CHARGE_POINT_ID): cv.string,
                vol.Optional("connector_id"): vol.Coerce(int),
                vol.Optional("charging_profile_id"): vol.Coerce(int),
                vol.Optional("stack_level"): vol.Coerce(int),
                vol.Optional("charging_profile_purpose"): cv.string,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CHANGE_AVAILABILITY,
        async_handle_change_availability,
        schema=vol.Schema(
            {
                vol.Optional(ATTR_ENTRY_ID): cv.string,
                vol.Optional(ATTR_CHARGE_POINT_ID): cv.string,
                vol.Required("operative"): bool,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_FIRMWARE,
        async_handle_update_firmware,
        schema=vol.Schema(
            {
                vol.Optional(ATTR_ENTRY_ID): cv.string,
                vol.Optional(ATTR_CHARGE_POINT_ID): cv.string,
                vol.Required("location"): cv.string,
                vol.Required("retrieve_date"): cv.string,
                vol.Optional("retries"): vol.Coerce(int),
                vol.Optional("retry_interval"): vol.Coerce(int),
            }
        ),
    )

    _VALID_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_CHARGING_SCHEDULE,
        async_handle_set_charging_schedule,
        schema=vol.Schema(
            {
                vol.Optional(ATTR_ENTRY_ID): cv.string,
                vol.Optional(ATTR_CHARGE_POINT_ID): cv.string,
                vol.Optional("days", default=[]): vol.All(
                    [vol.In(_VALID_DAYS)], vol.Unique()
                ),
                vol.Required("start"): cv.string,
                vol.Required("end"): cv.string,
                vol.Required("limit_a"): vol.All(
                    vol.Coerce(int), vol.Range(min=6, max=32)
                ),
                vol.Optional("show_ocpp_output", default=False): bool,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CLEAR_CHARGING_SCHEDULE,
        async_handle_clear_charging_schedule,
        schema=vol.Schema(
            {
                vol.Optional(ATTR_ENTRY_ID): cv.string,
                vol.Optional(ATTR_CHARGE_POINT_ID): cv.string,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ADD_RFID_TAG,
        async_handle_add_rfid_tag,
        schema=vol.Schema(
            {
                vol.Optional(ATTR_ENTRY_ID): cv.string,
                vol.Optional(ATTR_CHARGE_POINT_ID): cv.string,
                vol.Required("id_tag"): cv.string,
                vol.Optional("expiry_date"): cv.string,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REMOVE_RFID_TAG,
        async_handle_remove_rfid_tag,
        schema=vol.Schema(
            {
                vol.Optional(ATTR_ENTRY_ID): cv.string,
                vol.Optional(ATTR_CHARGE_POINT_ID): cv.string,
                vol.Required("id_tag"): cv.string,
            }
        ),
    )

    hass.data[DOMAIN]["services_registered"] = True


def _async_unregister_services(hass: HomeAssistant) -> None:
    """Unregister domain services."""

    for service in (
        SERVICE_RESET,
        SERVICE_TRIGGER_MESSAGE,
        SERVICE_UNLOCK_CONNECTOR,
        SERVICE_GET_CONFIGURATION,
        SERVICE_CHANGE_CONFIGURATION,
        SERVICE_REMOTE_START_TRANSACTION,
        SERVICE_REMOTE_STOP_TRANSACTION,
        SERVICE_SET_CHARGING_PROFILE,
        SERVICE_CLEAR_CHARGING_PROFILE,
        SERVICE_CHANGE_AVAILABILITY,
        SERVICE_UPDATE_FIRMWARE,
        SERVICE_SET_CHARGING_SCHEDULE,
        SERVICE_CLEAR_CHARGING_SCHEDULE,
        SERVICE_ADD_RFID_TAG,
        SERVICE_REMOVE_RFID_TAG,
    ):
        hass.services.async_remove(DOMAIN, service)
    hass.data[DOMAIN]["services_registered"] = False


_NON_RUNTIME_KEYS = frozenset({"services_registered", "views_registered", RELOAD_STATE_KEY})


def _resolve_runtime(
    hass: HomeAssistant, entry_id: str | None
) -> GivEnergyRuntimeData:
    """Resolve the targeted config entry runtime."""

    runtimes: dict[str, GivEnergyRuntimeData] = {
        key: value
        for key, value in hass.data[DOMAIN].items()
        if key not in _NON_RUNTIME_KEYS
    }

    if entry_id:
        if entry_id not in runtimes:
            raise HomeAssistantError(f"No config entry {entry_id} is loaded")
        return runtimes[entry_id]

    if len(runtimes) != 1:
        raise HomeAssistantError(
            "Multiple GivEnergy EVC OCPP entries are loaded; specify entry_id"
        )

    return next(iter(runtimes.values()))


# ---------------------------------------------------------------------------
# mDNS advertisement helpers
# ---------------------------------------------------------------------------


def _local_ip() -> str | None:
    """Return the local IP that would reach the outside network."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(0.1)
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except Exception:
        return None


async def _async_register_zeroconf(
    hass: HomeAssistant, entry: ConfigEntry
) -> Any:
    """Register a _givenergy-evc._tcp.local. mDNS service.

    Returns the ServiceInfo instance so it can be passed to
    _async_unregister_zeroconf on unload, or None on failure.
    """
    try:
        from homeassistant.components import zeroconf as ha_zeroconf
        from zeroconf import ServiceInfo
    except ImportError:
        _LOGGER.debug("zeroconf not available; skipping mDNS advertisement")
        return None

    local_ip = await hass.async_add_executor_job(_local_ip)
    if not local_ip:
        _LOGGER.warning(
            "Could not determine local IP address; skipping mDNS advertisement"
        )
        return None

    http_port = 8123
    try:
        if hass.http is not None:
            http_port = hass.http.server_port
    except AttributeError:
        pass

    try:
        aiozc = await ha_zeroconf.async_get_instance(hass)
        info = ServiceInfo(
            type_="_givenergy-evc._tcp.local.",
            name="GivEnergy EVC OCPP._givenergy-evc._tcp.local.",
            addresses=[socket.inet_aton(local_ip)],
            port=http_port,
            properties={
                b"path": b"/api/givenergy_evc_ocpp",
                b"ver": b"1",
            },
            server=f"{socket.gethostname()}.local.",
        )
        await aiozc.async_register_service(info)
        _LOGGER.debug(
            "Registered mDNS _givenergy-evc._tcp.local. → %s:%s", local_ip, http_port
        )
        return info
    except Exception as err:
        _LOGGER.warning("Failed to register mDNS service: %s", err)
        return None


async def _async_unregister_zeroconf(hass: HomeAssistant, info: Any) -> None:
    """Unregister the mDNS service registered by _async_register_zeroconf."""
    try:
        from homeassistant.components import zeroconf as ha_zeroconf

        aiozc = await ha_zeroconf.async_get_instance(hass)
        await aiozc.async_unregister_service(info)
        _LOGGER.debug("Unregistered mDNS _givenergy-evc._tcp.local.")
    except Exception as err:
        _LOGGER.warning("Failed to unregister mDNS service: %s", err)
