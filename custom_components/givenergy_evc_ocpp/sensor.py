"""Sensors for GivEnergy EVC OCPP."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DEFAULT_EVSE_MAX_CURRENT, DEFAULT_EVSE_MIN_CURRENT, DOMAIN
from .coordinator import GivEnergyEvcCoordinator
from .entity import GivEnergyEvcEntity


def _format_local_timestamp(value: Any) -> str | None:
    """Format datetimes as readable local timestamps."""

    if value is None:
        return None

    local_value = dt_util.as_local(value)
    return local_value.strftime("%Y-%m-%d %H:%M:%S %Z")


@dataclass(frozen=True, kw_only=True)
class GivEnergySensorDescription(SensorEntityDescription):
    """Description of a GivEnergy EVC sensor."""

    value_fn: Callable[[GivEnergyEvcCoordinator], Any]
    attrs_fn: Callable[[GivEnergyEvcCoordinator], dict[str, Any]] | None = None


SENSORS: tuple[GivEnergySensorDescription, ...] = (
    # --- Connection / status ---
    GivEnergySensorDescription(
        key="connection_status",
        translation_key="connection_status",
        icon="mdi:ev-plug-type2",
        value_fn=lambda coordinator: "Connected"
        if coordinator.data.connected
        else "Disconnected",
    ),
    GivEnergySensorDescription(
        key="charger_status",
        translation_key="charger_status",
        icon="mdi:ev-station",
        value_fn=lambda coordinator: coordinator.data.status,
        attrs_fn=lambda coordinator: {
            "charge_point_id": coordinator.data.charge_point_id,
            "connected": coordinator.data.connected,
            "operational_status": coordinator.data.operational_status,
            "transaction_id": coordinator.data.transaction_id,
            "supported_feature_profiles": coordinator.data.supported_feature_profiles,
        },
    ),
    GivEnergySensorDescription(
        key="operational_status",
        translation_key="operational_status",
        icon="mdi:toggle-switch-outline",
        value_fn=lambda coordinator: coordinator.data.operational_status,
    ),
    # --- Live measurements ---
    GivEnergySensorDescription(
        key="live_power",
        translation_key="live_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        suggested_display_precision=2,
        value_fn=lambda coordinator: coordinator.data.live_power_kw,
    ),
    GivEnergySensorDescription(
        key="live_current",
        translation_key="live_current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=2,
        value_fn=lambda coordinator: coordinator.data.live_current_a,
    ),
    GivEnergySensorDescription(
        key="live_voltage",
        translation_key="live_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=1,
        value_fn=lambda coordinator: coordinator.data.live_voltage_v,
    ),
    GivEnergySensorDescription(
        key="current_limit",
        translation_key="current_limit",
        icon="mdi:current-ac",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=1,
        value_fn=lambda coordinator: coordinator.data.current_limit_a,
    ),
    GivEnergySensorDescription(
        key="evse_min_current",
        translation_key="evse_min_current",
        icon="mdi:current-ac",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=1,
        value_fn=lambda coordinator: DEFAULT_EVSE_MIN_CURRENT,
    ),
    GivEnergySensorDescription(
        key="evse_max_current",
        translation_key="evse_max_current",
        icon="mdi:current-ac",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=1,
        value_fn=lambda coordinator: DEFAULT_EVSE_MAX_CURRENT,
    ),
    # --- Session data ---
    GivEnergySensorDescription(
        key="charge_session_energy",
        translation_key="charge_session_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=3,
        value_fn=lambda coordinator: coordinator.data.session_energy_kwh,
    ),
    GivEnergySensorDescription(
        key="meter_energy",
        translation_key="meter_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=lambda coordinator: coordinator.data.total_energy_kwh,
    ),
    GivEnergySensorDescription(
        key="charge_start_time",
        translation_key="charge_start_time",
        icon="mdi:clock-start",
        value_fn=lambda coordinator: _format_local_timestamp(
            coordinator.data.transaction_started_at
        ),
    ),
    GivEnergySensorDescription(
        key="charge_end_time",
        translation_key="charge_end_time",
        icon="mdi:clock-end",
        value_fn=lambda coordinator: _format_local_timestamp(
            coordinator.data.transaction_ended_at
        ),
    ),
    GivEnergySensorDescription(
        key="charge_session_duration",
        translation_key="charge_session_duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_display_precision=1,
        value_fn=lambda coordinator: coordinator.data.session_duration_seconds,
    ),
    # --- Diagnostic sensors ---
    GivEnergySensorDescription(
        key="last_seen",
        translation_key="last_seen",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda coordinator: coordinator.data.last_seen,
    ),
    GivEnergySensorDescription(
        key="heartbeat_age",
        translation_key="heartbeat_age",
        icon="mdi:timer-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        value_fn=lambda coordinator: coordinator.data.heartbeat_age_seconds,
    ),
    GivEnergySensorDescription(
        key="error_code",
        translation_key="error_code",
        icon="mdi:alert-circle-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda coordinator: coordinator.data.error_code,
        attrs_fn=lambda coordinator: {
            "vendor_error_code": coordinator.data.vendor_error_code,
            "status": coordinator.data.status,
        },
    ),
    GivEnergySensorDescription(
        key="serial_number",
        translation_key="serial_number",
        icon="mdi:identifier",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda coordinator: coordinator.data.charge_point_serial_number
        or coordinator.data.charge_box_serial_number
        or coordinator.data.charge_point_id,
    ),
    GivEnergySensorDescription(
        key="local_ip_address",
        translation_key="local_ip_address",
        icon="mdi:ip-network-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda coordinator: coordinator.data.local_ip_address,
        attrs_fn=lambda coordinator: {
            "source": (
                "ocpp_config"
                if coordinator.data.local_ip_address
                and coordinator.data.local_ip_address
                != coordinator.data.websocket_remote_address
                else "websocket_peer"
            ),
            "websocket_peer": coordinator.data.websocket_remote_address,
        },
    ),
    GivEnergySensorDescription(
        key="meter_value_sample_interval_seconds",
        translation_key="meter_value_sample_interval_seconds",
        icon="mdi:timer-cog-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        value_fn=lambda coordinator: coordinator.data.meter_value_sample_interval_seconds,
    ),
    GivEnergySensorDescription(
        key="last_message_response",
        translation_key="last_message_response",
        icon="mdi:message-reply-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda coordinator: coordinator.data.last_message_response_status,
        attrs_fn=lambda coordinator: {
            "action": coordinator.data.last_message_response_action,
            "captured_at": _format_local_timestamp(
                coordinator.data.last_message_response_at
            ),
            "payload": coordinator.data.last_message_response_payload,
        },
    ),
    GivEnergySensorDescription(
        key="cp_voltage",
        translation_key="cp_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=1,
        value_fn=lambda coordinator: coordinator.data.cp_voltage_v,
        attrs_fn=lambda coordinator: {
            "last_cp_response": coordinator.data.last_command_results.get(
                "DataTransfer:CP"
            ),
        },
    ),
    GivEnergySensorDescription(
        key="cp_duty_cycle",
        translation_key="cp_duty_cycle",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement="%",
        suggested_display_precision=0,
        value_fn=lambda coordinator: coordinator.data.cp_duty_cycle_percent,
        attrs_fn=lambda coordinator: {
            "last_cp_response": coordinator.data.last_command_results.get(
                "DataTransfer:CP"
            ),
        },
    ),
    GivEnergySensorDescription(
        key="firmware_status",
        translation_key="firmware_status",
        icon="mdi:download-network-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda coordinator: coordinator.data.firmware_update_state
        or coordinator.data.firmware_status,
        attrs_fn=lambda coordinator: {
            "derived_state": coordinator.data.firmware_update_state,
            "raw_ocpp_status": coordinator.data.firmware_status,
            "last_update_firmware_request": coordinator.data.last_update_firmware_request,
            "last_firmware_status_notification": coordinator.data.last_command_results.get(
                "FirmwareStatusNotification"
            ),
            "last_update_firmware_result": coordinator.data.last_command_results.get(
                "UpdateFirmware"
            ),
            "target_file": coordinator.data.firmware_update_target_file,
            "target_version": coordinator.data.firmware_update_target_version,
            "previous_version": coordinator.data.firmware_update_previous_version,
            "current_version": coordinator.data.firmware_version,
            "started_at": _format_local_timestamp(
                coordinator.data.firmware_update_started_at
            ),
            "download_completed_at": _format_local_timestamp(
                coordinator.data.firmware_update_download_completed_at
            ),
            "install_started_at": _format_local_timestamp(
                coordinator.data.firmware_update_install_started_at
            ),
            "completed_at": _format_local_timestamp(
                coordinator.data.firmware_update_completed_at
            ),
            "failure_reason": coordinator.data.firmware_update_failure_reason,
            "last_transfer_event": coordinator.data.firmware_update_last_transfer_event,
            "last_ocpp_status": coordinator.data.firmware_update_last_ocpp_status,
            "expected_reconnect_by": _format_local_timestamp(
                coordinator.data.firmware_update_expected_reconnect_by
            ),
            "charger_online": coordinator.data.connected,
            "server_running": coordinator.data.firmware_server_running,
            "server_host": coordinator.data.firmware_server_host,
            "server_port": coordinator.firmware_server_port,
            "manifest_url": coordinator.firmware_manifest_url,
            "manifest_error": coordinator.data.firmware_manifest_error,
            "manifest_refreshed_at": _format_local_timestamp(
                coordinator.data.firmware_manifest_refreshed_at
            ),
            "selected_file": coordinator.data.selected_firmware_file,
            "selected_file_cached": (
                coordinator.is_firmware_cached(coordinator.data.selected_firmware_file)
                if coordinator.data.selected_firmware_file
                else None
            ),
            "cached_files": coordinator.cached_firmware_files(),
            "last_transfer": coordinator.data.firmware_server_last_transfer,
            "server_events": coordinator.data.firmware_server_events,
        },
    ),
    GivEnergySensorDescription(
        key="charging_schedule",
        translation_key="charging_schedule",
        icon="mdi:calendar-clock",
        value_fn=lambda coordinator: len(coordinator.data.charging_schedule),
        attrs_fn=lambda coordinator: {
            f"schedule_{i + 1}_{attr}": val
            for i, window in enumerate(coordinator.data.charging_schedule)
            for attr, val in {
                "days": ", ".join(d.capitalize() for d in window.get("days", [])),
                "start": window.get("start"),
                "end": window.get("end"),
                "duration_minutes": window.get("duration_minutes"),
                "limit_a": window.get("limit_a"),
            }.items()
        },
    ),
    GivEnergySensorDescription(
        key="rfid_tags",
        translation_key="rfid_tags",
        icon="mdi:card-account-details-outline",
        value_fn=lambda coordinator: len(coordinator.data.rfid_tags),
        attrs_fn=lambda coordinator: {
            f"tag_{i + 1}_{attr}": val
            for i, tag in enumerate(coordinator.data.rfid_tags)
            for attr, val in {
                "id_tag": tag.get("id_tag"),
                "name": tag.get("name"),
                "status": tag.get("status"),
            }.items()
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors for the config entry."""

    runtime = hass.data[DOMAIN][entry.entry_id]
    coordinator: GivEnergyEvcCoordinator = runtime.coordinator
    async_add_entities(GivEnergyEvcSensor(coordinator, description) for description in SENSORS)


class GivEnergyEvcSensor(GivEnergyEvcEntity, SensorEntity):
    """A sensor backed by coordinator state."""

    entity_description: GivEnergySensorDescription

    def __init__(
        self,
        coordinator: GivEnergyEvcCoordinator,
        description: GivEnergySensorDescription,
    ) -> None:
        """Initialise the sensor."""

        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """Return the current sensor value."""

        return self.entity_description.value_fn(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra state attributes."""

        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator)
