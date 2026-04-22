"""State coordinator for GivEnergy EVC OCPP."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
import hashlib
import json
import logging
from pathlib import Path
import re
from typing import Any
from urllib.parse import quote

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfElectricCurrent, UnitOfElectricPotential
from homeassistant.const import UnitOfEnergy, UnitOfPower, UnitOfTime
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_COMMAND_TIMEOUT,
    CONF_DEBUG_LOGGING,
    CONF_ENHANCED_LOGGING,
    CONF_FIRMWARE_MANIFEST_URL,
    CONF_FIRMWARE_SERVER_PORT,
    LEGACY_CONF_FIRMWARE_FTP_PORT,
    CONF_LISTEN_PORT,
    CONF_METER_VALUE_SAMPLE_INTERVAL,
    DEFAULT_COMMAND_TIMEOUT,
    DEFAULT_EVSE_MAX_CURRENT,
    DEFAULT_EVSE_MIN_CURRENT,
    DEFAULT_ENHANCED_LOGGING,
    DEFAULT_FIRMWARE_MANIFEST_URL,
    DEFAULT_FIRMWARE_SERVER_PORT,
    GIVENERGY_CHARGE_MODES,
    DEFAULT_METER_VALUE_SAMPLE_INTERVAL,
    DEFAULT_REMOTE_ID_TAG,
    DOMAIN,
    MAX_STORED_OCPP_FRAMES,
)

_LOGGER = logging.getLogger(__name__)

CP_READING_PATTERN = re.compile(
    r"CP_Voltage:(?P<voltage>\d+(?:\.\d+)?)V,CP_Duty:(?P<duty>\d+(?:\.\d+)?)%"
)

STORAGE_VERSION = 1
FIRMWARE_DOWNLOADING_TIMEOUT = timedelta(minutes=10)
FIRMWARE_INSTALLING_TIMEOUT = timedelta(minutes=15)
FIRMWARE_INSTALL_DISCONNECT_GRACE = timedelta(minutes=2)
FIRMWARE_INSTALL_QUIET_GRACE = timedelta(seconds=90)


@dataclass(slots=True)
class GivEnergyEvcState:
    """Mutable integration state."""

    connected: bool = False
    adopted: bool = False
    charge_point_id: str | None = None
    path_charge_point_id: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    firmware_version: str | None = None
    charge_point_serial_number: str | None = None
    charge_box_serial_number: str | None = None
    connection_state: str = "disconnected"
    car_plugged_in: bool | None = None
    status: str | None = None
    operational_status: str | None = None
    firmware_status: str | None = None
    firmware_update_state: str | None = None
    firmware_update_target_file: str | None = None
    firmware_update_target_version: str | None = None
    firmware_update_previous_version: str | None = None
    firmware_update_started_at: datetime | None = None
    firmware_update_download_completed_at: datetime | None = None
    firmware_update_install_started_at: datetime | None = None
    firmware_update_completed_at: datetime | None = None
    firmware_update_failure_reason: str | None = None
    firmware_update_last_ocpp_status: str | None = None
    firmware_update_last_transfer_event: str | None = None
    firmware_update_expected_reconnect_by: datetime | None = None
    firmware_server_enabled: bool = False
    firmware_server_running: bool = False
    firmware_server_host: str | None = None
    firmware_server_error: str | None = None
    firmware_manifest_error: str | None = None
    firmware_manifest_refreshed_at: datetime | None = None
    firmware_manifest_entries: dict[str, dict[str, Any]] = field(default_factory=dict)
    firmware_server_last_transfer: dict[str, Any] | None = None
    selected_firmware_file: str | None = None
    available_firmware_files: list[str] = field(default_factory=list)
    firmware_server_events: list[dict[str, Any]] = field(default_factory=list)
    diagnostics_status: str | None = None
    error_code: str | None = None
    vendor_error_code: str | None = None
    last_seen: datetime | None = None
    last_heartbeat: datetime | None = None
    heartbeat_age_seconds: int | None = None
    transaction_id: int | None = None
    transaction_active: bool = False
    transaction_id_tag: str | None = None
    transaction_meter_start_wh: float | None = None
    transaction_started_at: datetime | None = None
    transaction_ended_at: datetime | None = None
    session_duration_seconds: int | None = None
    session_energy_kwh: float | None = None
    total_energy_kwh: float | None = None
    live_power_kw: float | None = None
    live_current_a: float | None = None
    live_voltage_v: float | None = None
    cp_voltage_v: float | None = None
    cp_duty_cycle_percent: float | None = None
    current_limit_a: float | None = None
    max_import_capacity_a: int | None = None
    plug_and_go_enabled: bool = False
    meter_value_sample_interval_seconds: int | None = None
    local_ip_address: str | None = None
    websocket_remote_address: str | None = None
    charger_enabled: bool | None = None
    charge_mode: str | None = None
    local_modbus_enabled: bool | None = None
    front_panel_leds_enabled: bool | None = None
    randomised_delay_duration_seconds: int | None = None
    suspended_state_timeout_seconds: int | None = None
    supported_feature_profiles: list[str] = field(default_factory=list)
    configuration: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_boot_notification: dict[str, Any] | None = None
    last_status_notification: dict[str, Any] | None = None
    last_meter_values: dict[str, Any] | None = None
    last_get_configuration: dict[str, Any] | None = None
    last_update_firmware_request: dict[str, Any] | None = None
    last_command_results: dict[str, Any] = field(default_factory=dict)
    last_message_response_action: str | None = None
    last_message_response_status: str | None = None
    last_message_response_at: datetime | None = None
    last_message_response_payload: Any = None
    meter_samples: list[dict[str, Any]] = field(default_factory=list)
    parsed_meter_values: dict[str, Any] = field(default_factory=dict)
    rejected_charge_points: list[str] = field(default_factory=list)
    ocpp_frame_history: list[dict[str, Any]] = field(default_factory=list)
    unsupported_ocpp_actions: list[dict[str, Any]] = field(default_factory=list)
    last_authorize_request: dict[str, Any] | None = None
    last_authorize_response: dict[str, Any] | None = None
    last_start_transaction_request: dict[str, Any] | None = None
    last_start_transaction_response: dict[str, Any] | None = None
    last_stop_transaction_request: dict[str, Any] | None = None
    last_stop_transaction_response: dict[str, Any] | None = None
    last_call_error: dict[str, Any] | None = None
    charging_schedule: list[dict[str, Any]] = field(default_factory=list)
    rfid_tags: list[dict[str, Any]] = field(default_factory=list)


class GivEnergyEvcCoordinator(DataUpdateCoordinator[GivEnergyEvcState]):
    """Coordinator for GivEnergy EVC state and commands."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        *,
        charge_point_id: str | None = None,
        legacy_entity_ids: bool = True,
        use_storage: bool = True,
    ) -> None:
        """Initialise the coordinator."""

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=None,
        )
        self.entry = entry
        self.data = GivEnergyEvcState()
        if charge_point_id:
            self.data.charge_point_id = charge_point_id
        self.server: Any = None
        self.firmware_server: Any = None
        self._unsub_timer: CALLBACK_TYPE | None = None
        self._next_transaction_id = 1
        self._firmware_server_auto_stop_task = None
        self._legacy_entity_ids = legacy_entity_ids
        self._use_storage = use_storage
        storage_key = f"{DOMAIN}_{entry.entry_id}_state"
        if charge_point_id and not legacy_entity_ids:
            safe_charge_point_id = re.sub(r"[^A-Za-z0-9_.-]", "_", charge_point_id)
            storage_key = f"{DOMAIN}_{entry.entry_id}_{safe_charge_point_id}_state"
        self._store = (
            Store[dict[str, Any]](hass, STORAGE_VERSION, storage_key)
            if use_storage
            else None
        )

    def export_reload_state(self) -> dict[str, Any]:
        """Export volatile state that should survive an entry reload."""

        return {
            "transaction_id": self.data.transaction_id,
            "transaction_active": self.data.transaction_active,
            "transaction_id_tag": self.data.transaction_id_tag,
            "transaction_meter_start_wh": self.data.transaction_meter_start_wh,
            "transaction_started_at": self.data.transaction_started_at,
            "transaction_ended_at": self.data.transaction_ended_at,
            "session_duration_seconds": self.data.session_duration_seconds,
            "session_energy_kwh": self.data.session_energy_kwh,
            "total_energy_kwh": self.data.total_energy_kwh,
            "cp_voltage_v": self.data.cp_voltage_v,
            "cp_duty_cycle_percent": self.data.cp_duty_cycle_percent,
            "status": self.data.status,
            "charge_point_id": self.data.charge_point_id,
            "charge_point_serial_number": self.data.charge_point_serial_number,
            "charge_box_serial_number": self.data.charge_box_serial_number,
            "last_boot_notification": self.data.last_boot_notification,
            "car_plugged_in": self.data.car_plugged_in,
            "plug_and_go_enabled": self.data.plug_and_go_enabled,
            "firmware_update_state": self.data.firmware_update_state,
            "firmware_update_target_file": self.data.firmware_update_target_file,
            "firmware_update_target_version": self.data.firmware_update_target_version,
            "firmware_update_previous_version": self.data.firmware_update_previous_version,
            "firmware_update_started_at": self.data.firmware_update_started_at,
            "firmware_update_download_completed_at": self.data.firmware_update_download_completed_at,
            "firmware_update_install_started_at": self.data.firmware_update_install_started_at,
            "firmware_update_completed_at": self.data.firmware_update_completed_at,
            "firmware_update_failure_reason": self.data.firmware_update_failure_reason,
            "firmware_update_last_ocpp_status": self.data.firmware_update_last_ocpp_status,
            "firmware_update_last_transfer_event": self.data.firmware_update_last_transfer_event,
            "firmware_update_expected_reconnect_by": self.data.firmware_update_expected_reconnect_by,
            "firmware_server_enabled": self.data.firmware_server_enabled,
            "firmware_server_host": self.data.firmware_server_host,
            "firmware_server_last_transfer": self.data.firmware_server_last_transfer,
            "selected_firmware_file": self.data.selected_firmware_file,
            "charging_schedule": self.data.charging_schedule,
            "rfid_tags": self.data.rfid_tags,
            "last_message_response_action": self.data.last_message_response_action,
            "last_message_response_status": self.data.last_message_response_status,
            "last_message_response_at": self.data.last_message_response_at,
            "last_message_response_payload": self.data.last_message_response_payload,
        }

    def restore_reload_state(self, state: dict[str, Any] | None) -> None:
        """Restore volatile state after an entry reload."""

        if not state:
            return

        self.data.transaction_id = self._coerce_int(state.get("transaction_id"))
        self.data.transaction_active = bool(state.get("transaction_active", False))
        self.data.transaction_id_tag = state.get("transaction_id_tag")
        self.data.transaction_meter_start_wh = self._coerce_float(
            state.get("transaction_meter_start_wh")
        )
        self.data.transaction_started_at = self._coerce_datetime(
            state.get("transaction_started_at")
        )
        self.data.transaction_ended_at = self._coerce_datetime(
            state.get("transaction_ended_at")
        )
        self.data.session_duration_seconds = self._coerce_int(
            state.get("session_duration_seconds")
        )
        self.data.session_energy_kwh = self._coerce_float(state.get("session_energy_kwh"))
        self.data.total_energy_kwh = self._coerce_float(state.get("total_energy_kwh"))
        self.data.cp_voltage_v = self._coerce_float(state.get("cp_voltage_v"))
        self.data.cp_duty_cycle_percent = self._coerce_float(
            state.get("cp_duty_cycle_percent")
        )
        self.data.status = state.get("status")
        self.data.charge_point_id = state.get("charge_point_id")
        self.data.charge_point_serial_number = state.get("charge_point_serial_number")
        self.data.charge_box_serial_number = state.get("charge_box_serial_number")
        self.data.last_boot_notification = state.get("last_boot_notification")
        stored_car_plugged_in = state.get("car_plugged_in")
        self.data.car_plugged_in = (
            bool(stored_car_plugged_in)
            if stored_car_plugged_in is not None
            else self._is_car_plugged_in_status(self.data.status)
        )
        self.data.plug_and_go_enabled = bool(state.get("plug_and_go_enabled", False))
        self.data.firmware_update_state = state.get("firmware_update_state")
        firmware_update_target_file = state.get("firmware_update_target_file")
        self.data.firmware_update_target_file = (
            str(firmware_update_target_file).strip()
            if firmware_update_target_file
            else None
        )
        firmware_update_target_version = state.get("firmware_update_target_version")
        self.data.firmware_update_target_version = (
            str(firmware_update_target_version).strip()
            if firmware_update_target_version
            else None
        )
        firmware_update_previous_version = state.get("firmware_update_previous_version")
        self.data.firmware_update_previous_version = (
            str(firmware_update_previous_version).strip()
            if firmware_update_previous_version
            else None
        )
        self.data.firmware_update_started_at = self._coerce_datetime(
            state.get("firmware_update_started_at")
        )
        self.data.firmware_update_download_completed_at = self._coerce_datetime(
            state.get("firmware_update_download_completed_at")
        )
        self.data.firmware_update_install_started_at = self._coerce_datetime(
            state.get("firmware_update_install_started_at")
        )
        self.data.firmware_update_completed_at = self._coerce_datetime(
            state.get("firmware_update_completed_at")
        )
        firmware_update_failure_reason = state.get("firmware_update_failure_reason")
        self.data.firmware_update_failure_reason = (
            str(firmware_update_failure_reason).strip()
            if firmware_update_failure_reason
            else None
        )
        firmware_update_last_ocpp_status = state.get("firmware_update_last_ocpp_status")
        self.data.firmware_update_last_ocpp_status = (
            str(firmware_update_last_ocpp_status).strip()
            if firmware_update_last_ocpp_status
            else None
        )
        firmware_update_last_transfer_event = state.get(
            "firmware_update_last_transfer_event"
        )
        self.data.firmware_update_last_transfer_event = (
            str(firmware_update_last_transfer_event).strip()
            if firmware_update_last_transfer_event
            else None
        )
        self.data.firmware_update_expected_reconnect_by = self._coerce_datetime(
            state.get("firmware_update_expected_reconnect_by")
        )
        self.data.firmware_server_enabled = bool(
            state.get("firmware_server_enabled", state.get("firmware_ftp_enabled", False))
        )
        firmware_server_host = state.get(
            "firmware_server_host", state.get("firmware_ftp_host")
        )
        self.data.firmware_server_host = (
            str(firmware_server_host).strip() if firmware_server_host else None
        )
        self.data.firmware_server_last_transfer = state.get(
            "firmware_server_last_transfer", state.get("firmware_ftp_last_transfer")
        )
        selected_firmware_file = state.get("selected_firmware_file")
        self.data.selected_firmware_file = (
            str(selected_firmware_file).strip() if selected_firmware_file else None
        )
        self.data.charging_schedule = state.get("charging_schedule") or []
        self.data.rfid_tags = state.get("rfid_tags") or []
        last_message_response_action = state.get("last_message_response_action")
        self.data.last_message_response_action = (
            str(last_message_response_action).strip()
            if last_message_response_action
            else None
        )
        last_message_response_status = state.get("last_message_response_status")
        self.data.last_message_response_status = (
            str(last_message_response_status).strip()
            if last_message_response_status
            else None
        )
        self.data.last_message_response_at = self._coerce_datetime(
            state.get("last_message_response_at")
        )
        self.data.last_message_response_payload = state.get("last_message_response_payload")

        if self.data.transaction_id is not None:
            self._next_transaction_id = max(self._next_transaction_id, self.data.transaction_id + 1)

        self._refresh_available_firmware_files()
        self._publish_state()

    async def async_restore_persisted_state(self) -> None:
        """Restore persisted transaction/session state from storage."""

        if self._store is None:
            return
        stored = await self._store.async_load()
        self.restore_reload_state(stored)

    async def async_start(self, *, manage_firmware_server: bool = True) -> None:
        """Start coordinator tasks."""

        if manage_firmware_server:
            try:
                await self.async_refresh_firmware_manifest()
            except HomeAssistantError as err:
                self.data.firmware_manifest_error = str(err)
                self.data.firmware_server_enabled = False

            if self.data.firmware_server_enabled and self.firmware_server is not None:
                try:
                    if not self.data.available_firmware_files:
                        raise HomeAssistantError(
                            "No firmware entries were loaded from the configured manifest"
                        )
                    await self.firmware_server.async_start(self.firmware_server_port)
                except HomeAssistantError as err:
                    self.data.firmware_server_running = False
                    self.data.firmware_server_error = str(err)
                else:
                    self.data.firmware_server_running = True
                    self.data.firmware_server_error = None

        if self._unsub_timer is None:
            self._unsub_timer = async_track_time_interval(
                self.hass, self._async_handle_timer, timedelta(seconds=30)
            )

        self._publish_state()

    async def async_stop(self) -> None:
        """Stop coordinator tasks."""

        if self._unsub_timer is not None:
            self._unsub_timer()
            self._unsub_timer = None

        if self._firmware_server_auto_stop_task is not None:
            self._firmware_server_auto_stop_task.cancel()
            self._firmware_server_auto_stop_task = None

        self.data.firmware_server_running = False

    @property
    def has_device(self) -> bool:
        """Return whether the charger has been identified."""

        return bool(self.data.charge_point_id or self.data.last_boot_notification)

    @property
    def entity_unique_id_prefix(self) -> str:
        """Return the entity unique ID prefix for this coordinator."""

        if self._legacy_entity_ids:
            return self.entry.entry_id

        charge_point_id = self.data.charge_point_id or self.data.path_charge_point_id
        if charge_point_id:
            return f"{self.entry.entry_id}_{charge_point_id}"
        return f"{self.entry.entry_id}_pending"

    @property
    def debug_logging(self) -> bool:
        """Return whether verbose logging is enabled."""

        return bool(self.entry.options.get(CONF_DEBUG_LOGGING, False))

    @property
    def enhanced_logging(self) -> bool:
        """Return whether enhanced OCPP frame capture is enabled."""

        return bool(
            self.entry.options.get(CONF_ENHANCED_LOGGING, DEFAULT_ENHANCED_LOGGING)
        )

    @property
    def command_timeout(self) -> int:
        """Return the configured command timeout."""

        return int(
            self.entry.options.get(CONF_COMMAND_TIMEOUT, DEFAULT_COMMAND_TIMEOUT)
        )

    @property
    def listen_port(self) -> int:
        """Return the configured listen port."""

        return int(
            self.entry.options.get(
                CONF_LISTEN_PORT, self.entry.data.get(CONF_LISTEN_PORT)
            )
        )

    @property
    def firmware_server_port(self) -> int:
        """Return the configured firmware transfer server port."""

        return int(
            self.entry.options.get(
                CONF_FIRMWARE_SERVER_PORT,
                self.entry.options.get(
                    LEGACY_CONF_FIRMWARE_FTP_PORT,
                    self.entry.data.get(
                        CONF_FIRMWARE_SERVER_PORT,
                        self.entry.data.get(
                            LEGACY_CONF_FIRMWARE_FTP_PORT,
                            DEFAULT_FIRMWARE_SERVER_PORT,
                        ),
                    ),
                ),
            )
        )

    @property
    def firmware_manifest_url(self) -> str:
        """Return the configured firmware manifest URL."""

        value = self.entry.options.get(
            CONF_FIRMWARE_MANIFEST_URL,
            self.entry.data.get(
                CONF_FIRMWARE_MANIFEST_URL, DEFAULT_FIRMWARE_MANIFEST_URL
            ),
        )
        return str(value).strip()

    @property
    def firmware_directory(self) -> Path:
        """Return the directory served by the local firmware transfer server."""

        return Path(__file__).resolve().parent / "firmware"

    @property
    def firmware_update_in_progress(self) -> bool:
        """Return whether a firmware update is currently active."""

        return self._firmware_update_in_progress()

    @property
    def desired_meter_value_sample_interval(self) -> int:
        """Return the desired meter sampling interval."""

        return int(
            self.entry.options.get(
                CONF_METER_VALUE_SAMPLE_INTERVAL,
                self.entry.data.get(
                    CONF_METER_VALUE_SAMPLE_INTERVAL,
                    DEFAULT_METER_VALUE_SAMPLE_INTERVAL,
                ),
            )
        )

    @property
    def available_charge_modes(self) -> list[str]:
        """Return supported charger modes for the select entity."""

        options = list(GIVENERGY_CHARGE_MODES)
        if self.data.charge_mode and self.data.charge_mode not in options:
            options.append(self.data.charge_mode)
        return options

    @property
    def device_info(self) -> DeviceInfo:
        """Return Home Assistant device metadata."""

        identifiers: set[tuple[str, str]] = set()
        entry_id = self.entry.entry_id

        # All identifiers are scoped to the current config entry so they cannot
        # accidentally match a stale device from a previous installation.
        if self._legacy_entity_ids:
            identifiers.add((DOMAIN, f"entry:{entry_id}"))

        effective_charge_point_id = (
            self.data.charge_point_id or self.data.path_charge_point_id
        )
        if effective_charge_point_id:
            identifiers.add((DOMAIN, f"{entry_id}:charge_point_id:{effective_charge_point_id}"))
        if self.data.charge_point_serial_number:
            identifiers.add(
                (
                    DOMAIN,
                    f"{entry_id}:charge_point_serial:{self.data.charge_point_serial_number}",
                )
            )
        if self.data.charge_box_serial_number:
            identifiers.add(
                (DOMAIN, f"{entry_id}:charge_box_serial:{self.data.charge_box_serial_number}")
            )

        name_parts = [
            part
            for part in (
                self.data.manufacturer or "GivEnergy",
                self.data.model or "EVC",
            )
            if part
        ]
        if effective_charge_point_id:
            name_parts.append(effective_charge_point_id)

        return DeviceInfo(
            identifiers=identifiers,
            manufacturer=self.data.manufacturer or "GivEnergy",
            model=self.data.model or "EVC",
            name=" ".join(name_parts),
            sw_version=self.data.firmware_version,
            serial_number=(
                self.data.charge_point_serial_number
                or self.data.charge_box_serial_number
                or effective_charge_point_id
            ),
        )

    def set_server(self, server: Any) -> None:
        """Attach the running websocket server."""

        self.server = server

    def set_firmware_server(self, server: Any, *, register_events: bool = True) -> None:
        """Attach the local firmware transfer server wrapper."""

        self.firmware_server = server
        if server is not None and register_events:
            server.set_event_callback(self._async_handle_firmware_server_event)

    async def async_handle_firmware_server_event(self, event: dict[str, Any]) -> None:
        """Record a firmware transfer-server event routed by the shared hub."""

        await self._async_handle_firmware_server_event(event)

    def can_accept_charge_point(self, candidate_id: str | None) -> bool:
        """Return whether the candidate charge point should be accepted."""

        del candidate_id
        return True

    async def async_note_rejected_charge_point(self, candidate_id: str | None) -> None:
        """Record a rejected charge point."""

        if candidate_id and candidate_id not in self.data.rejected_charge_points:
            self.data.rejected_charge_points.append(candidate_id)
            self._publish_state()

    async def async_connection_opened(
        self,
        candidate_id: str | None,
        local_host: str | None = None,
        remote_host: str | None = None,
    ) -> None:
        """Handle a websocket connection opening."""

        if candidate_id:
            self.data.path_charge_point_id = candidate_id
        if local_host:
            self.data.firmware_server_host = local_host
        if remote_host:
            self.data.websocket_remote_address = remote_host

        if candidate_id and not self.data.charge_point_id:
            self.data.charge_point_id = candidate_id
            self.data.adopted = True

        self.data.connected = True
        self.data.connection_state = "connected"
        self._touch_last_seen()
        self._handle_firmware_reconnect()
        await self._async_upsert_device()
        self._publish_state()

    async def async_connection_closed(self) -> None:
        """Handle a websocket connection closing."""

        self.data.connected = False
        self.data.connection_state = "disconnected"
        self._update_heartbeat_age()
        self._handle_firmware_disconnect()
        self._publish_state()

    async def async_record_boot(
        self, candidate_id: str | None, payload: dict[str, Any]
    ) -> None:
        """Record BootNotification data."""

        self.data.last_boot_notification = payload
        self.data.manufacturer = payload.get("chargePointVendor") or "GivEnergy"
        self.data.model = payload.get("chargePointModel") or "EVC"
        self.data.firmware_version = payload.get("firmwareVersion")
        self.data.charge_point_serial_number = payload.get("chargePointSerialNumber")
        self.data.charge_box_serial_number = payload.get("chargeBoxSerialNumber")

        charge_point_id = (
            self.data.charge_point_id
            or candidate_id
            or self.data.charge_point_serial_number
            or self.data.charge_box_serial_number
        )
        if charge_point_id:
            self.data.charge_point_id = charge_point_id
            if self.data.adopted or self._legacy_entity_ids:
                self.data.adopted = True

        self._touch_last_seen()
        self._handle_firmware_version_observed()
        await self._async_upsert_device()
        self._publish_state()

        # Pull configuration as soon as the charger boots so vendor-specific
        # settings become available to entities without manual intervention.
        if self.server is not None:
            self.hass.async_create_task(self.async_initialize_remote_settings())

    async def async_record_heartbeat(self) -> None:
        """Record a Heartbeat message."""

        self.data.last_heartbeat = datetime.now(UTC)
        self._touch_last_seen()
        self._update_heartbeat_age()
        self._publish_state()

    async def async_record_status(self, payload: dict[str, Any]) -> None:
        """Record StatusNotification data."""

        previous_plugged_in = self.data.car_plugged_in
        self.data.last_status_notification = payload
        self.data.status = payload.get("status")
        self.data.car_plugged_in = self._is_car_plugged_in_status(self.data.status)
        self.data.error_code = payload.get("errorCode")
        self.data.vendor_error_code = payload.get("vendorErrorCode")
        self.data.charger_enabled = self.data.status != "Unavailable"
        self.data.operational_status = self._derive_operational_status(self.data.status)
        self._touch_last_seen()

        # Re-evaluate the last meter payload against the new charger status so
        # transitions like Charging -> SuspendedEVSE do not leave stale live
        # power/current values selected from a previous charging state.
        if self.data.last_meter_values:
            self._apply_meter_values_payload(self.data.last_meter_values)

        self._publish_state()

        if (
            self.data.plug_and_go_enabled
            and previous_plugged_in is False
            and self.data.car_plugged_in is True
            and not self.data.transaction_active
        ):
            self.hass.async_create_task(self._async_handle_plug_and_go_start())

    async def async_record_meter_values(self, payload: dict[str, Any]) -> None:
        """Record and parse MeterValues data."""

        self.data.last_meter_values = payload
        self._touch_last_seen()
        self._restore_transaction_from_meter_values(payload)
        self._apply_meter_values_payload(payload)
        self._publish_state()

    def _apply_meter_values_payload(self, payload: dict[str, Any]) -> None:
        """Parse meter values into stable live/session sensors."""

        flattened_samples = self._flatten_meter_values_payload(payload)
        self.data.meter_samples = flattened_samples

        parsed_map = {
            sample["sample_key"]: {
                "timestamp": sample["timestamp"],
                "raw_value": sample["raw_value"],
                "numeric_value": sample["numeric_value"],
                "normalized_value": sample["normalized_value"],
                "unit": sample["unit"],
                "measurand": sample["measurand"],
                "phase": sample["phase"],
                "context": sample["context"],
                "location": sample["location"],
            }
            for sample in flattened_samples
        }
        self.data.parsed_meter_values = parsed_map

        meter_groups = self._group_meter_samples(flattened_samples)
        ev_meter_group = self._pick_givenergy_ev_meter_group(meter_groups)
        preferred_group = ev_meter_group or self._pick_preferred_meter_group(
            meter_groups
        )
        live_samples = preferred_group or flattened_samples
        power_delivery_expected = self._status_expects_power_delivery()

        power_sample = self._pick_preferred_sample(
            live_samples,
            measurand="Power.Active.Import",
            preferred_phases=("L1", None, "L1-N", "N"),
            preferred_locations=("Outlet", None, "Body", "Cable"),
            preferred_contexts=("Sample.Periodic", None, "Transaction.Begin"),
            prefer_positive=power_delivery_expected,
            prefer_non_negative=not power_delivery_expected,
        )
        if power_sample and power_sample["normalized_value"] is not None:
            self.data.live_power_kw = round(power_sample["normalized_value"] / 1000, 2)

        current_sample = self._pick_preferred_sample(
            live_samples,
            measurand="Current.Import",
            preferred_phases=("L1", "N", None, "L1-N"),
            preferred_locations=("Outlet", None, "Body", "Cable"),
            preferred_contexts=("Sample.Periodic", None, "Transaction.Begin"),
            prefer_positive=power_delivery_expected,
            prefer_non_negative=not power_delivery_expected,
        )
        if current_sample and current_sample["normalized_value"] is not None:
            self.data.live_current_a = round(current_sample["normalized_value"], 3)

        voltage_sample = self._pick_preferred_sample(
            live_samples,
            measurand="Voltage",
            preferred_phases=("L1-N", None, "L1", "N"),
            preferred_locations=("Outlet", None, "Body", "Cable"),
            preferred_contexts=("Sample.Periodic", None, "Transaction.Begin"),
            prefer_non_negative=True,
        )
        if voltage_sample and voltage_sample["normalized_value"] is not None:
            self.data.live_voltage_v = round(voltage_sample["normalized_value"], 1)

        previous_total_wh = (
            self.data.total_energy_kwh * 1000 if self.data.total_energy_kwh is not None else None
        )
        total_energy_samples = ev_meter_group or preferred_group or flattened_samples
        total_energy_sample = self._pick_total_energy_sample(
            total_energy_samples, previous_total_wh
        )
        if total_energy_sample and total_energy_sample["normalized_value"] is not None:
            total_wh = total_energy_sample["normalized_value"]
            self.data.total_energy_kwh = round(total_wh / 1000, 2)
            if (
                self.data.transaction_active
                and self.data.transaction_meter_start_wh is None
                and total_wh > 0
            ):
                _LOGGER.warning(
                    "Mid-session reconnect detected: using current meter reading %.0f Wh "
                    "as session baseline (session energy will start from 0)",
                    total_wh,
                )
                self.data.transaction_meter_start_wh = total_wh
                self.data.session_energy_kwh = 0.0
            elif (
                self.data.transaction_meter_start_wh is not None
                and total_wh >= self.data.transaction_meter_start_wh
            ):
                self.data.session_energy_kwh = round(
                    (total_wh - self.data.transaction_meter_start_wh) / 1000, 3
                )

    async def async_record_firmware_status(self, payload: dict[str, Any]) -> None:
        """Record FirmwareStatusNotification data."""

        self.data.firmware_status = payload.get("status")
        self.data.firmware_update_last_ocpp_status = self.data.firmware_status
        self.data.last_command_results["FirmwareStatusNotification"] = payload
        self._touch_last_seen()
        self._apply_firmware_ocpp_status(self.data.firmware_status)
        self._publish_state()

    async def async_record_diagnostics_status(self, payload: dict[str, Any]) -> None:
        """Record DiagnosticsStatusNotification data."""

        self.data.diagnostics_status = payload.get("status")
        self._touch_last_seen()
        self._publish_state()

    async def async_start_transaction_from_charger(
        self, payload: dict[str, Any]
    ) -> int:
        """Record a new charger-initiated transaction and return an ID."""

        transaction_id = self._next_transaction_id
        self._next_transaction_id += 1

        meter_start = self._coerce_float(payload.get("meterStart"))

        self.data.transaction_id = transaction_id
        self.data.transaction_active = True
        self.data.transaction_id_tag = payload.get("idTag")
        self.data.transaction_meter_start_wh = meter_start
        self.data.transaction_started_at = datetime.now(UTC)
        self.data.transaction_ended_at = None
        self.data.session_duration_seconds = 0
        self.data.session_energy_kwh = 0 if meter_start is not None else None
        self._touch_last_seen()
        self._publish_state()

        return transaction_id

    async def async_stop_transaction_from_charger(
        self, payload: dict[str, Any]
    ) -> None:
        """Record the end of a transaction."""

        meter_stop = self._coerce_float(payload.get("meterStop"))
        stopped_at = datetime.now(UTC)
        if (
            meter_stop is not None
            and self.data.transaction_meter_start_wh is not None
            and meter_stop >= self.data.transaction_meter_start_wh
        ):
            self.data.session_energy_kwh = round(
                (meter_stop - self.data.transaction_meter_start_wh) / 1000, 3
            )
            self.data.total_energy_kwh = round(meter_stop / 1000, 2)

        self.data.transaction_active = False
        self.data.transaction_id = payload.get("transactionId", self.data.transaction_id)
        self.data.transaction_ended_at = stopped_at
        if self.data.transaction_started_at is not None:
            self.data.session_duration_seconds = max(
                int((stopped_at - self.data.transaction_started_at).total_seconds()),
                0,
            )
        self._touch_last_seen()
        self._publish_state()

    def _restore_transaction_from_meter_values(self, payload: dict[str, Any]) -> None:
        """Recover transaction state from MeterValues after reconnects or reloads."""

        transaction_id = self._coerce_int(payload.get("transactionId"))
        if transaction_id is None:
            return

        if self.data.transaction_id != transaction_id:
            self.data.transaction_id = transaction_id

        if not self.data.transaction_active:
            self.data.transaction_active = True

        if self.data.transaction_started_at is None:
            timestamp = None
            meter_values = payload.get("meterValue") or []
            if meter_values:
                timestamp = self._parse_ocpp_timestamp(meter_values[0].get("timestamp"))
            self.data.transaction_started_at = timestamp or datetime.now(UTC)

        self._next_transaction_id = max(self._next_transaction_id, transaction_id + 1)

    async def async_record_command_result(self, action: str, result: Any) -> None:
        """Store the last result for a central-system initiated command."""

        self.data.last_command_results[action] = result
        self.data.last_message_response_action = action
        self.data.last_message_response_status = self._derive_command_result_status(
            result
        )
        self.data.last_message_response_at = datetime.now(UTC)
        self.data.last_message_response_payload = result
        self._publish_state()

    async def _async_handle_firmware_server_event(self, event: dict[str, Any]) -> None:
        """Record a firmware transfer server event from the background thread."""

        event_record = {
            "captured_at": datetime.now(UTC).isoformat(),
            **event,
        }
        self.data.firmware_server_events.append(event_record)
        if len(self.data.firmware_server_events) > 50:
            self.data.firmware_server_events = self.data.firmware_server_events[-50:]

        event_type = event.get("event")
        self.data.firmware_update_last_transfer_event = (
            str(event_type).strip() if event_type else None
        )
        if event_type == "server_started":
            self.data.firmware_server_running = True
            self.data.firmware_server_error = None
        elif event_type == "server_stopped":
            self.data.firmware_server_running = False
        elif event_type == "server_error":
            self.data.firmware_server_running = False
            self.data.firmware_server_error = str(event.get("error"))

        if event_type in {
            "download_started",
            "file_sent",
            "checksum_reported",
            "checksum_ok",
            "checksum_mismatch",
            "upload_started",
            "upload_complete",
            "upload_checksum_ok",
            "upload_checksum_mismatch",
        }:
            self.data.firmware_server_last_transfer = event_record

        self._apply_firmware_transfer_event(event_record)
        self._publish_state()

    @callback
    def record_ocpp_frame(
        self,
        *,
        direction: str,
        frame_type: str,
        action: str | None = None,
        payload: Any = None,
        raw_frame: Any = None,
        note: str | None = None,
    ) -> None:
        """Record a raw OCPP frame in the rolling diagnostics buffer."""

        if not self.enhanced_logging:
            return

        entry = {
            "captured_at": datetime.now(UTC).isoformat(),
            "direction": direction,
            "frame_type": frame_type,
            "action": action,
            "payload": payload,
            "raw_frame": raw_frame,
            "note": note,
        }
        self.data.ocpp_frame_history.append(entry)
        if len(self.data.ocpp_frame_history) > MAX_STORED_OCPP_FRAMES:
            self.data.ocpp_frame_history = self.data.ocpp_frame_history[
                -MAX_STORED_OCPP_FRAMES:
            ]
        self._publish_state()

    @callback
    def record_unsupported_ocpp_action(
        self, action: str, payload: dict[str, Any]
    ) -> None:
        """Record an unsupported inbound OCPP action."""

        entry = {
            "captured_at": datetime.now(UTC).isoformat(),
            "action": action,
            "payload": payload,
        }
        self.data.unsupported_ocpp_actions.append(entry)
        if len(self.data.unsupported_ocpp_actions) > 20:
            self.data.unsupported_ocpp_actions = self.data.unsupported_ocpp_actions[-20:]
        self._publish_state()

    @callback
    def record_authorize_exchange(
        self, request: dict[str, Any], response: dict[str, Any]
    ) -> None:
        """Record the last Authorize request and response."""

        if not self.enhanced_logging:
            return
        self.data.last_authorize_request = request
        self.data.last_authorize_response = response
        self._publish_state()

    @callback
    def record_start_transaction_exchange(
        self, request: dict[str, Any], response: dict[str, Any]
    ) -> None:
        """Record the last StartTransaction request and response."""

        if not self.enhanced_logging:
            return
        self.data.last_start_transaction_request = request
        self.data.last_start_transaction_response = response
        self._publish_state()

    @callback
    def record_stop_transaction_exchange(
        self, request: dict[str, Any], response: dict[str, Any]
    ) -> None:
        """Record the last StopTransaction request and response."""

        if not self.enhanced_logging:
            return
        self.data.last_stop_transaction_request = request
        self.data.last_stop_transaction_response = response
        self._publish_state()

    @callback
    def record_call_error(
        self,
        *,
        unique_id: str | None,
        error_code: str,
        error_description: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Record the last OCPP CALLERROR emitted by the server."""

        if not self.enhanced_logging:
            return
        self.data.last_call_error = {
            "captured_at": datetime.now(UTC).isoformat(),
            "unique_id": unique_id,
            "error_code": error_code,
            "error_description": error_description,
            "details": details or {},
        }
        self._publish_state()

    async def async_refresh_configuration(
        self, keys: list[str] | None = None
    ) -> dict[str, Any]:
        """Fetch and store the full charger configuration."""

        payload: dict[str, Any] = {}
        if keys:
            payload["key"] = keys
        result = await self._async_send_command("GetConfiguration", payload)
        await self.async_record_command_result("GetConfiguration", result)

        config_items = result.get("configurationKey", [])
        configuration: dict[str, dict[str, Any]] = {}
        for item in config_items:
            key = item.get("key")
            if not key:
                continue
            configuration[key] = item

        self.data.configuration = configuration
        self.data.last_get_configuration = result
        self.data.supported_feature_profiles = self._split_csv_configuration_value(
            configuration.get("SupportedFeatureProfiles", {}).get("value")
        )
        self.data.current_limit_a = self._extract_configured_current_limit(
            previous_value=self.data.current_limit_a
        )
        self.data.max_import_capacity_a = self._extract_max_import_capacity()
        self.data.meter_value_sample_interval_seconds = self._coerce_int(
            self._configuration_value("MeterValueSampleInterval")
        )
        local_ip_address = self._configuration_value("LocalIPAddress")
        reported = str(local_ip_address).strip() if local_ip_address not in (None, "") else None
        self.data.local_ip_address = (
            reported
            if reported and reported != "0.0.0.0"
            else self.data.websocket_remote_address
        )
        self.data.charge_mode = self._normalize_charge_mode(
            self._configuration_value("EcoMode")
        )
        self.data.local_modbus_enabled = self._coerce_bool(
            self._configuration_value("EnableLocalModbus")
        )
        self.data.front_panel_leds_enabled = self._coerce_bool(
            self._configuration_value("FrontPanelLEDsEnabled")
        )
        self.data.randomised_delay_duration_seconds = self._coerce_int(
            self._configuration_value("RandomisedDelayDuration")
        )
        self.data.suspended_state_timeout_seconds = self._coerce_int(
            self._configuration_value("SuspevTime")
        )
        self._publish_state()
        return result

    async def async_initialize_remote_settings(self) -> None:
        """Refresh charger configuration and apply preferred settings."""

        await self.async_refresh_configuration()
        await self.async_apply_preferred_configuration()

    async def async_apply_preferred_configuration(self) -> None:
        """Apply opinionated charger settings managed by this integration."""

        configured_interval = self.data.meter_value_sample_interval_seconds
        desired_interval = self.desired_meter_value_sample_interval

        if configured_interval == desired_interval:
            return

        if "MeterValueSampleInterval" not in self.data.configuration:
            _LOGGER.debug(
                "Charger did not report MeterValueSampleInterval in GetConfiguration"
            )
            return

        result = await self.async_change_configuration(
            "MeterValueSampleInterval", desired_interval
        )
        status = str(result.get("status", "Unknown"))
        if status in {"Accepted", "RebootRequired"}:
            self.data.meter_value_sample_interval_seconds = desired_interval
            _LOGGER.info(
                "Applied MeterValueSampleInterval=%s seconds", desired_interval
            )
            self._publish_state()
            return

        _LOGGER.warning(
            "Unable to apply MeterValueSampleInterval=%s, charger returned status=%s",
            desired_interval,
            status,
        )

    async def async_change_configuration(
        self, key: str, value: str | int | float | bool
    ) -> dict[str, Any]:
        """Send ChangeConfiguration and update local state optimistically."""

        result = await self._async_send_command(
            "ChangeConfiguration",
            {"key": key, "value": str(value)},
        )
        await self.async_record_command_result("ChangeConfiguration", result)

        status = str(result.get("status", "Unknown"))
        if status in {"Accepted", "RebootRequired"}:
            if key not in self.data.configuration:
                self.data.configuration[key] = {"key": key, "readonly": False}
            self.data.configuration[key]["value"] = str(value)

            if key in {"ChargeRate", "MaxCurrent"}:
                if key == "ChargeRate":
                    encoded_amperage = self._coerce_float(value)
                    if encoded_amperage is not None:
                        self.data.current_limit_a = round(encoded_amperage / 10, 1)
                else:
                    self.data.current_limit_a = self._sanitize_current_limit_value(
                        value,
                        fallback=self.data.current_limit_a,
                        config_key=key,
                    )
            if key == "Imax":
                capacity = self._coerce_int(value)
                if capacity is not None and 40 <= capacity <= 100:
                    self.data.max_import_capacity_a = capacity
            if key == "EcoMode":
                self.data.charge_mode = self._normalize_charge_mode(value)
            if key == "MeterValueSampleInterval":
                self.data.meter_value_sample_interval_seconds = self._coerce_int(value)
            if key == "EnableLocalModbus":
                self.data.local_modbus_enabled = self._coerce_bool(value)
            if key == "FrontPanelLEDsEnabled":
                self.data.front_panel_leds_enabled = self._coerce_bool(value)
            if key == "RandomisedDelayDuration":
                self.data.randomised_delay_duration_seconds = self._coerce_int(value)
            if key == "SuspevTime":
                self.data.suspended_state_timeout_seconds = self._coerce_int(value)

        if status == "RebootRequired":
            await self.async_reset("Soft")

        self._publish_state()
        return result

    async def async_reset(self, reset_type: str) -> dict[str, Any]:
        """Issue a Reset command."""

        result = await self._async_send_command("Reset", {"type": reset_type})
        await self.async_record_command_result("Reset", result)
        return result

    async def async_unlock_connector(self, connector_id: int = 1) -> dict[str, Any]:
        """Issue an UnlockConnector command."""

        result = await self._async_send_command(
            "UnlockConnector", {"connectorId": connector_id}
        )
        await self.async_record_command_result("UnlockConnector", result)
        return result

    async def async_trigger_message(
        self, requested_message: str, connector_id: int | None = None
    ) -> dict[str, Any]:
        """Issue a TriggerMessage command."""

        payload: dict[str, Any] = {"requestedMessage": requested_message}
        if connector_id is not None:
            payload["connectorId"] = connector_id
        result = await self._async_send_command("TriggerMessage", payload)
        await self.async_record_command_result("TriggerMessage", result)
        return result

    async def async_factory_reset(self) -> dict[str, Any]:
        """Issue the vendor-specific factory reset DataTransfer command."""

        result = await self._async_send_command(
            "DataTransfer",
            {
                "vendorId": "GivEnergy",
                "messageId": "Setting",
                "data": "Refactory",
            },
        )
        await self.async_record_command_result("DataTransfer", result)
        return result

    async def async_read_cp_voltage_and_duty_cycle(self) -> dict[str, Any]:
        """Read CP voltage and duty cycle through the vendor DataTransfer path."""

        result = await self._async_send_command(
            "DataTransfer",
            {
                "vendorId": "GivEnergy",
                "messageId": "Parameter",
                "data": "CP",
            },
        )
        await self.async_record_command_result("DataTransfer", result)
        await self.async_record_command_result("DataTransfer:CP", result)

        status = str(result.get("status", "Unknown"))
        data = result.get("data")
        parsed = self._parse_cp_reading(data)
        if status == "Accepted" and parsed is not None:
            self.data.cp_voltage_v = parsed["voltage_v"]
            self.data.cp_duty_cycle_percent = parsed["duty_cycle_percent"]
            self._publish_state()
        elif status == "Accepted":
            _LOGGER.warning("Unable to parse CP reading payload: %r", data)

        return result

    async def async_remote_start_transaction(
        self,
        id_tag: str | None = None,
        connector_id: int | None = None,
        charging_profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Issue a RemoteStartTransaction command."""

        payload: dict[str, Any] = {"idTag": id_tag or DEFAULT_REMOTE_ID_TAG}
        if connector_id is not None:
            payload["connectorId"] = connector_id
        if charging_profile is not None:
            payload["chargingProfile"] = charging_profile
        result = await self._async_send_command("RemoteStartTransaction", payload)
        await self.async_record_command_result("RemoteStartTransaction", result)
        return result

    async def async_remote_stop_transaction(
        self, transaction_id: int | None = None
    ) -> dict[str, Any]:
        """Issue a RemoteStopTransaction command."""

        actual_transaction_id = transaction_id or self.data.transaction_id
        if actual_transaction_id is None:
            raise HomeAssistantError("No active transaction is known for the charger")

        result = await self._async_send_command(
            "RemoteStopTransaction", {"transactionId": actual_transaction_id}
        )
        await self.async_record_command_result("RemoteStopTransaction", result)
        return result

    async def async_set_charging_profile(
        self, connector_id: int, charging_profile: dict[str, Any]
    ) -> dict[str, Any]:
        """Issue a SetChargingProfile command."""

        result = await self._async_send_command(
            "SetChargingProfile",
            {
                "connectorId": connector_id,
                "csChargingProfiles": charging_profile,
            },
        )
        await self.async_record_command_result("SetChargingProfile", result)
        return result

    async def async_clear_charging_profile(
        self,
        connector_id: int | None = None,
        charging_profile_id: int | None = None,
        stack_level: int | None = None,
        charging_profile_purpose: str | None = None,
    ) -> dict[str, Any]:
        """Issue a ClearChargingProfile command."""

        payload: dict[str, Any] = {}
        if connector_id is not None:
            payload["connectorId"] = connector_id
        if charging_profile_id is not None:
            payload["id"] = charging_profile_id
        if stack_level is not None:
            payload["stackLevel"] = stack_level
        if charging_profile_purpose is not None:
            payload["chargingProfilePurpose"] = charging_profile_purpose

        result = await self._async_send_command("ClearChargingProfile", payload)
        await self.async_record_command_result("ClearChargingProfile", result)
        return result

    async def async_set_charging_schedule(
        self,
        days: list[str],
        start: str,
        end: str,
        limit_a: int,
        show_ocpp_output: bool = False,
    ) -> dict[str, Any]:
        """Set a recurring charging schedule via SetChargingProfile.

        days: list of day names e.g. ["mon","wed","fri"]; empty/all-7 → Daily.
        start/end: "HH:MM" local time strings.
        limit_a: charge current in amps.
        """

        ALL_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        day_index = {d: i for i, d in enumerate(ALL_DAYS)}

        # Normalise days — default to every day
        normalised = sorted(
            {d.lower() for d in days if d.lower() in day_index},
            key=lambda d: day_index[d],
        )
        all_days_selected = not normalised or set(normalised) == set(ALL_DAYS)
        if all_days_selected:
            normalised = ALL_DAYS

        def _parse_hhmm(t: str) -> tuple[int, int]:
            parts = t.strip().split(":")
            return int(parts[0]), int(parts[1])

        sh, sm = _parse_hhmm(start)
        eh, em = _parse_hhmm(end)

        start_local_secs = sh * 3600 + sm * 60
        end_local_secs = eh * 3600 + em * 60
        if end_local_secs <= start_local_secs:
            duration = 86400 - start_local_secs + end_local_secs
        else:
            duration = end_local_secs - start_local_secs

        def _merge_intervals(
            intervals: list[tuple[int, int]],
        ) -> list[tuple[int, int]]:
            """Merge active intervals on a linear OCPP recurrence timeline."""

            merged: list[tuple[int, int]] = []
            for start_offset, end_offset in sorted(intervals):
                if start_offset == end_offset:
                    continue
                if not merged or start_offset > merged[-1][1]:
                    merged.append((start_offset, end_offset))
                    continue
                previous_start, previous_end = merged[-1]
                merged[-1] = (previous_start, max(previous_end, end_offset))
            return merged

        def _add_circular_interval(
            intervals: list[tuple[int, int]],
            start_offset: int,
            duration_seconds: int,
            cycle_seconds: int,
        ) -> None:
            """Add an active interval to a circular OCPP recurrence timeline."""

            if duration_seconds >= cycle_seconds:
                intervals.append((0, cycle_seconds))
                return

            start_offset %= cycle_seconds
            end_offset = (start_offset + duration_seconds) % cycle_seconds
            if start_offset < end_offset:
                intervals.append((start_offset, end_offset))
                return

            intervals.append((0, end_offset))
            intervals.append((start_offset, cycle_seconds))

        def _periods_from_intervals(
            intervals: list[tuple[int, int]],
            cycle_seconds: int,
        ) -> list[tuple[int, int]]:
            """Convert active intervals into OCPP startPeriod state changes."""

            merged = _merge_intervals(intervals)
            if merged == [(0, cycle_seconds)]:
                return [(0, limit_a)]

            active_at_zero = any(start_offset == 0 for start_offset, _ in merged)
            periods: list[tuple[int, int]] = [
                (0, limit_a if active_at_zero else 0)
            ]

            def _append_period(start_period: int, limit: int) -> None:
                if periods[-1][0] == start_period:
                    periods[-1] = (start_period, limit)
                elif periods[-1][1] != limit:
                    periods.append((start_period, limit))

            for start_offset, end_offset in merged:
                if start_offset > 0:
                    _append_period(start_offset, limit_a)
                if end_offset < cycle_seconds:
                    _append_period(end_offset, 0)

            return periods

        local_tz = dt_util.get_time_zone(self.hass.config.time_zone) or UTC

        if all_days_selected:
            recurrency = "Daily"
            now_utc = datetime.now(UTC)
            anchor = (now_utc - timedelta(days=now_utc.weekday())).replace(
                hour=0, minute=0, second=0, microsecond=0
            )

            now_local = datetime.now(local_tz)
            local_day = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
            local_start = local_day.replace(hour=sh, minute=sm)
            local_end = local_day.replace(hour=eh, minute=em)
            if local_end <= local_start:
                local_end += timedelta(days=1)

            utc_start = local_start.astimezone(UTC)
            utc_end = local_end.astimezone(UTC)
            start_offset = utc_start.hour * 3600 + utc_start.minute * 60
            duration_seconds = int((utc_end - utc_start).total_seconds())
            intervals: list[tuple[int, int]] = []
            _add_circular_interval(intervals, start_offset, duration_seconds, 86400)
            periods = _periods_from_intervals(intervals, 86400)
        else:
            recurrency = "Weekly"
            now_utc = datetime.now(UTC)
            days_since_monday = now_utc.weekday()  # Monday=0
            anchor = (now_utc - timedelta(days=days_since_monday)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )

            now_local = datetime.now(local_tz)
            local_week_start = (
                now_local - timedelta(days=now_local.weekday())
            ).replace(hour=0, minute=0, second=0, microsecond=0)

            intervals = []
            for day_name in normalised:
                local_day = local_week_start + timedelta(days=day_index[day_name])
                local_start = local_day.replace(hour=sh, minute=sm)
                local_end = local_day.replace(hour=eh, minute=em)
                if local_end <= local_start:
                    local_end += timedelta(days=1)

                utc_start = local_start.astimezone(UTC)
                utc_end = local_end.astimezone(UTC)
                start_offset = int((utc_start - anchor).total_seconds())
                duration_seconds = int((utc_end - utc_start).total_seconds())
                _add_circular_interval(
                    intervals, start_offset, duration_seconds, 604800
                )

            periods = _periods_from_intervals(intervals, 604800)

        # Build OCPP chargingSchedulePeriod (startPeriod as string per portal convention)
        ocpp_periods = [
            {"startPeriod": str(p[0]), "limit": str(p[1])}
            for p in sorted(periods)
        ]

        profile = {
            "stackLevel": 0,
            "chargingProfilePurpose": "TxDefaultProfile",
            "chargingProfileKind": "Recurring",
            "recurrencyKind": recurrency,
            "chargingSchedule": {
                "chargingRateUnit": "A",
                "startSchedule": anchor.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "chargingSchedulePeriod": ocpp_periods,
            },
            "chargingProfileId": 1,
        }

        command_payload = {"connectorId": 0, "csChargingProfiles": profile}
        result = await self._async_send_command("SetChargingProfile", command_payload)
        await self.async_record_command_result("SetChargingProfile", result)

        # Store the schedule window for sensor visibility
        self.data.charging_schedule = [
            {
                "days": normalised,
                "start": start,
                "end": end,
                "limit_a": limit_a,
                "duration_minutes": duration // 60,
            }
        ]
        self._publish_state()
        if show_ocpp_output:
            return {
                "ocpp_output": {
                    "action": "SetChargingProfile",
                    "payload": command_payload,
                },
                "charger_response": result,
            }
        return result

    async def async_clear_charging_schedule(self) -> dict[str, Any]:
        """Clear the active charging schedule via ClearChargingProfile."""

        result = await self._async_send_command(
            "ClearChargingProfile",
            {
                "connectorId": 0,
                "chargingProfilePurpose": "TxDefaultProfile",
                "stackLevel": 0,
            },
        )
        await self.async_record_command_result("ClearChargingProfile", result)
        self.data.charging_schedule = []
        self._publish_state()
        return result

    async def async_add_rfid_tag(
        self, id_tag: str, expiry_date: str | None = None
    ) -> dict[str, Any]:
        """Add or update an RFID tag on the charger's local authorisation list."""

        version_result = await self._async_send_command("GetLocalListVersion", {})
        current_version = version_result.get("listVersion", 0) if isinstance(version_result, dict) else 0
        new_version = current_version + 1

        id_tag_info: dict[str, Any] = {"status": "Accepted"}
        if expiry_date:
            id_tag_info["expiryDate"] = expiry_date

        payload = {
            "listVersion": new_version,
            "updateType": "Differential",
            "localAuthorizationList": [
                {"idTag": id_tag, "idTagInfo": id_tag_info}
            ],
        }
        result = await self._async_send_command("SendLocalList", payload)
        await self.async_record_command_result("SendLocalList", result)

        # Mirror in coordinator state — update existing entry or append
        tags = [t for t in self.data.rfid_tags if t["id_tag"] != id_tag]
        tags.append({"id_tag": id_tag, "expiry_date": expiry_date})
        self.data.rfid_tags = tags
        self._publish_state()
        return result

    async def async_remove_rfid_tag(self, id_tag: str) -> dict[str, Any]:
        """Remove an RFID tag from the charger's local authorisation list."""

        version_result = await self._async_send_command("GetLocalListVersion", {})
        current_version = version_result.get("listVersion", 0) if isinstance(version_result, dict) else 0
        new_version = current_version + 1

        payload = {
            "listVersion": new_version,
            "updateType": "Differential",
            "localAuthorizationList": [{"idTag": id_tag}],
        }
        result = await self._async_send_command("SendLocalList", payload)
        await self.async_record_command_result("SendLocalList", result)

        self.data.rfid_tags = [t for t in self.data.rfid_tags if t["id_tag"] != id_tag]
        self._publish_state()
        return result

    async def async_change_availability(self, operative: bool) -> dict[str, Any]:
        """Issue a ChangeAvailability command."""

        result = await self._async_send_command(
            "ChangeAvailability",
            {"connectorId": 0, "type": "Operative" if operative else "Inoperative"},
        )
        await self.async_record_command_result("ChangeAvailability", result)
        self.data.charger_enabled = operative
        self.data.operational_status = "Operative" if operative else "Inoperative"
        self._publish_state()
        return result

    async def async_update_firmware(
        self,
        location: str,
        retrieve_date: str,
        retries: int | None = None,
        retry_interval: int | None = None,
    ) -> dict[str, Any]:
        """Issue an UpdateFirmware command."""

        if self._firmware_update_in_progress():
            raise HomeAssistantError(
                "A firmware update is already in progress; wait for it to complete or fail before starting another"
            )

        payload: dict[str, Any] = {
            "location": location,
            "retrieveDate": retrieve_date,
        }
        if retries is not None:
            payload["retries"] = retries
        if retry_interval is not None:
            payload["retryInterval"] = retry_interval

        self.data.last_update_firmware_request = dict(payload)
        self._start_firmware_update_session(location)
        result = await self._async_send_command("UpdateFirmware", payload)
        await self.async_record_command_result("UpdateFirmware", result)
        self._publish_state()
        return result

    async def async_set_current_limit(self, amperage: float) -> dict[str, Any]:
        """Change the charger current limit."""

        amperage = max(
            DEFAULT_EVSE_MIN_CURRENT,
            min(DEFAULT_EVSE_MAX_CURRENT, float(amperage)),
        )
        key = (
            "ChargeRate"
            if "ChargeRate" in self.data.configuration
            else "MaxCurrent"
        )
        value: float = round(amperage, 1)
        # GivEnergy expects ChargeRate writes in tenths of amps, but reports the
        # stored value back in real amps via GetConfiguration.
        if key == "ChargeRate":
            value = round(amperage * 10, 1)
        return await self.async_change_configuration(key, value)

    async def async_set_max_import_capacity(self, amperage: int) -> dict[str, Any]:
        """Set the maximum grid import capacity allowed by the installation."""

        amperage = max(40, min(100, int(amperage)))
        return await self.async_change_configuration("Imax", amperage)

    async def async_set_charge_mode(self, mode: str) -> dict[str, Any]:
        """Change the GivEnergy charge mode."""

        normalized = self._normalize_charge_mode(mode)
        if normalized is None:
            raise HomeAssistantError(f"Unsupported charge mode: {mode}")
        return await self.async_change_configuration("EcoMode", normalized)

    async def async_set_local_modbus_enabled(self, enabled: bool) -> dict[str, Any]:
        """Enable or disable the charger's local Modbus server."""

        return await self.async_change_configuration(
            "EnableLocalModbus", str(enabled).lower()
        )

    async def async_set_front_panel_leds_enabled(self, enabled: bool) -> dict[str, Any]:
        """Enable or disable the charger's front panel LEDs."""

        return await self.async_change_configuration(
            "FrontPanelLEDsEnabled", str(enabled).lower()
        )

    async def async_set_randomised_delay_duration(self, seconds: int) -> dict[str, Any]:
        """Set the charger's randomised delay duration."""

        return await self.async_change_configuration(
            "RandomisedDelayDuration", int(seconds)
        )

    async def async_set_suspended_state_timeout(self, seconds: int) -> dict[str, Any]:
        """Set the suspended-state wait timeout in seconds (0 = disabled)."""

        seconds = max(0, min(43200, int(seconds)))
        return await self.async_change_configuration("SuspevTime", seconds)

    async def async_set_plug_and_go_enabled(self, enabled: bool) -> dict[str, Any]:
        """Enable or disable Home Assistant-side plug-and-go behavior."""

        self.data.plug_and_go_enabled = enabled
        self._publish_state()
        return {"enabled": enabled}

    async def async_set_firmware_server_enabled(self, enabled: bool) -> dict[str, Any]:
        """Start or stop the local firmware transfer server."""

        if self.firmware_server is None:
            raise HomeAssistantError("Firmware transfer server is not available")

        if enabled:
            await self.async_refresh_firmware_manifest()
            if not self.data.available_firmware_files:
                raise HomeAssistantError(
                    "No firmware files were loaded from the configured firmware manifest"
                )
            self._clear_firmware_update_session()
            try:
                await self.firmware_server.async_start(self.firmware_server_port)
            except HomeAssistantError as err:
                self.data.firmware_server_running = False
                self.data.firmware_server_enabled = False
                self.data.firmware_server_error = str(err)
                self._publish_state()
                raise

            self.data.firmware_server_enabled = True
            self.data.firmware_server_running = True
            self.data.firmware_server_error = None
        else:
            await self.firmware_server.async_stop()
            self.data.firmware_server_enabled = False
            self.data.firmware_server_running = False
            self.data.firmware_server_error = None

        self._publish_state()
        return {
            "enabled": self.data.firmware_server_enabled,
            "running": self.data.firmware_server_running,
            "port": self.firmware_server_port,
        }

    async def async_set_selected_firmware_file(self, filename: str) -> None:
        """Select the firmware file to expose/install."""

        if not self.data.firmware_manifest_entries:
            await self.async_refresh_firmware_manifest()
        if filename not in self.data.available_firmware_files:
            raise HomeAssistantError(f"Unknown firmware file from manifest: {filename}")
        self.data.selected_firmware_file = filename
        self._publish_state()

    async def async_install_selected_firmware(self) -> dict[str, Any]:
        """Install the currently selected firmware file."""

        if not self.data.firmware_manifest_entries:
            await self.async_refresh_firmware_manifest()
        filename = self.data.selected_firmware_file
        if not filename:
            raise HomeAssistantError("No firmware file is currently selected")
        if filename not in self.data.available_firmware_files:
            raise HomeAssistantError(
                f"The selected firmware file is no longer available: {filename}"
            )
        if not self.data.firmware_server_running:
            raise HomeAssistantError("The firmware server is not running")
        if not self.data.connected:
            raise HomeAssistantError("No GivEnergy charger is currently connected")
        if not self.data.firmware_server_host:
            raise HomeAssistantError(
                "Unable to determine the Home Assistant host address for the charger"
            )

        await self._async_ensure_firmware_cached(filename)

        retrieve_at = (datetime.now(UTC) + timedelta(seconds=60)).replace(microsecond=0)
        location = (
            f"ftp://{self.data.firmware_server_host}:{self.firmware_server_port}/"
            f"ChargerFirmware/{quote(filename)}"
        )
        return await self.async_update_firmware(
            location=location,
            retrieve_date=retrieve_at.isoformat().replace("+00:00", "Z"),
            retries=1,
            retry_interval=60,
        )

    def _start_firmware_update_session(self, location: str) -> None:
        """Initialize a new local firmware update session."""

        target_file = self.data.selected_firmware_file or Path(location).name or None
        self.data.firmware_status = None
        self.data.last_command_results.pop("FirmwareStatusNotification", None)
        self.data.firmware_update_state = None
        self.data.firmware_update_target_file = target_file
        self.data.firmware_update_target_version = self._derive_firmware_version_from_filename(
            target_file
        )
        self.data.firmware_update_previous_version = self.data.firmware_version
        self.data.firmware_update_started_at = datetime.now(UTC)
        self.data.firmware_update_download_completed_at = None
        self.data.firmware_update_install_started_at = None
        self.data.firmware_update_completed_at = None
        self.data.firmware_update_failure_reason = None
        self.data.firmware_update_last_ocpp_status = None
        self.data.firmware_update_last_transfer_event = None
        self.data.firmware_update_expected_reconnect_by = None

    def _clear_firmware_update_session(self) -> None:
        """Clear the local firmware update session and raw firmware sensor state."""

        self.data.firmware_status = None
        self.data.firmware_update_state = None
        self.data.firmware_update_target_file = None
        self.data.firmware_update_target_version = None
        self.data.firmware_update_previous_version = None
        self.data.firmware_update_started_at = None
        self.data.firmware_update_download_completed_at = None
        self.data.firmware_update_install_started_at = None
        self.data.firmware_update_completed_at = None
        self.data.firmware_update_failure_reason = None
        self.data.firmware_update_last_ocpp_status = None
        self.data.firmware_update_last_transfer_event = None
        self.data.firmware_update_expected_reconnect_by = None
        self.data.last_update_firmware_request = None
        self.data.last_command_results.pop("FirmwareStatusNotification", None)
        self.data.last_command_results.pop("UpdateFirmware", None)
        self.data.firmware_server_last_transfer = None
        self.data.firmware_server_events = []

    async def async_refresh_firmware_manifest(self) -> None:
        """Fetch and parse the configured firmware manifest."""

        manifest_url = self.firmware_manifest_url
        if not manifest_url:
            self.data.firmware_manifest_error = "No firmware manifest URL is configured"
            self.data.firmware_manifest_entries = {}
            self._refresh_available_firmware_files()
            self._publish_state()
            return

        session = async_get_clientsession(self.hass)
        try:
            async with session.get(manifest_url, allow_redirects=True) as response:
                if response.status != 200:
                    raise HomeAssistantError(
                        f"Manifest request failed with HTTP {response.status}"
                    )
                manifest = json.loads(await response.text())
        except Exception as err:
            self.data.firmware_manifest_error = str(err)
            self.data.firmware_manifest_entries = {}
            self._refresh_available_firmware_files()
            self._publish_state()
            raise HomeAssistantError(
                f"Unable to load firmware manifest from {manifest_url}: {err}"
            ) from err

        self.data.firmware_manifest_entries = self._parse_firmware_manifest(manifest)
        self.data.firmware_manifest_error = None
        self.data.firmware_manifest_refreshed_at = datetime.now(UTC)
        self._refresh_available_firmware_files()
        self._publish_state()

    def _parse_firmware_manifest(self, manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """Parse the firmware manifest into filename-indexed entries."""

        models = manifest.get("models")
        if not isinstance(models, dict):
            raise HomeAssistantError("Firmware manifest does not contain a valid models map")

        preferred_model = self._derive_manifest_model_key()
        selected_models: list[tuple[str, dict[str, Any]]] = []

        if preferred_model and preferred_model in models and isinstance(models[preferred_model], dict):
            selected_models = [(preferred_model, models[preferred_model])]
        else:
            selected_models = [
                (model_key, model_value)
                for model_key, model_value in models.items()
                if isinstance(model_value, dict)
            ]

        entries: dict[str, dict[str, Any]] = {}
        for model_key, model_data in selected_models:
            versions = model_data.get("versions")
            if not isinstance(versions, dict):
                continue
            for version, entry in versions.items():
                if not isinstance(entry, dict):
                    continue
                filename = entry.get("filename")
                url = entry.get("url")
                checksum_md5 = entry.get("checksum_md5")
                size = entry.get("size")
                if not filename or not url or not checksum_md5:
                    continue
                normalized_filename = str(filename).strip()
                entries[normalized_filename] = {
                    "model": model_key,
                    "version": str(version).strip(),
                    "filename": normalized_filename,
                    "url": str(url).strip(),
                    "checksum_md5": str(checksum_md5).strip().lower(),
                    "size": self._coerce_int(size),
                }

        if not entries:
            raise HomeAssistantError("Firmware manifest did not yield any usable firmware entries")

        return entries

    def _derive_manifest_model_key(self) -> str | None:
        """Infer the firmware manifest model key from the charger's current version."""

        version = self.data.firmware_version
        if not version:
            return None
        parts = str(version).strip().split("_")
        if len(parts) < 3:
            return None
        return "_".join(parts[:-1])

    async def _async_ensure_firmware_cached(self, filename: str) -> Path:
        """Ensure the selected firmware file exists locally and matches the manifest checksum."""

        entry = self.data.firmware_manifest_entries.get(filename)
        if not entry:
            raise HomeAssistantError(f"No manifest entry was found for firmware file: {filename}")

        firmware_dir = self.firmware_directory
        firmware_dir.mkdir(parents=True, exist_ok=True)
        target_path = firmware_dir / filename

        if await self._async_cached_firmware_matches_manifest(target_path, entry):
            return target_path

        await self._async_download_firmware(target_path, entry)

        if not await self._async_cached_firmware_matches_manifest(target_path, entry):
            raise HomeAssistantError(
                f"Downloaded firmware failed checksum validation: {filename}"
            )

        return target_path

    async def _async_cached_firmware_matches_manifest(
        self, path: Path, entry: dict[str, Any]
    ) -> bool:
        """Return whether a cached firmware file matches manifest size and checksum."""

        if not path.is_file():
            return False

        expected_size = entry.get("size")
        actual_size = path.stat().st_size
        if expected_size is not None and actual_size != expected_size:
            return False

        expected_md5 = entry.get("checksum_md5")
        actual_md5 = await self.hass.async_add_executor_job(self._compute_md5, path)
        return actual_md5 == expected_md5

    async def _async_download_firmware(self, target_path: Path, entry: dict[str, Any]) -> None:
        """Download a firmware file into the local cache."""

        download_url = entry["url"]
        temp_path = target_path.with_suffix(f"{target_path.suffix}.download")
        if temp_path.exists():
            temp_path.unlink()

        session = async_get_clientsession(self.hass)
        try:
            async with session.get(download_url, allow_redirects=True) as response:
                if response.status != 200:
                    raise HomeAssistantError(
                        f"Firmware download failed with HTTP {response.status}"
                    )
                data = await response.read()
        except Exception as err:
            raise HomeAssistantError(
                f"Unable to download firmware from {download_url}: {err}"
            ) from err

        await self.hass.async_add_executor_job(temp_path.write_bytes, data)
        try:
            if not await self._async_cached_firmware_matches_manifest(temp_path, entry):
                raise HomeAssistantError(
                    f"Downloaded file checksum or size did not match manifest for {entry['filename']}"
                )
            await self.hass.async_add_executor_job(temp_path.replace, target_path)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def _firmware_update_in_progress(self) -> bool:
        """Return whether a firmware update session is currently active."""

        if self.data.firmware_update_state in {"Downloading", "Downloaded", "Installing"}:
            return True

        return (
            self.data.firmware_update_started_at is not None
            and self.data.firmware_update_completed_at is None
            and self.data.firmware_update_failure_reason is None
        )

    def _apply_firmware_ocpp_status(self, status: str | None) -> None:
        """Advance the local firmware update state from OCPP status events."""

        if status in (None, ""):
            return

        now = datetime.now(UTC)
        normalized = str(status).strip()

        if normalized == "Downloading":
            self.data.firmware_update_state = "Downloading"
            return

        if normalized == "Downloaded":
            self.data.firmware_update_state = "Downloaded"
            self.data.firmware_update_download_completed_at = (
                self.data.firmware_update_download_completed_at or now
            )
            return

        if normalized in {"Installing", "InstallScheduled"}:
            self.data.firmware_update_state = "Installing"
            self.data.firmware_update_install_started_at = (
                self.data.firmware_update_install_started_at or now
            )
            self.data.firmware_update_expected_reconnect_by = (
                now + FIRMWARE_INSTALLING_TIMEOUT
            )
            return

        if normalized == "Installed":
            self.data.firmware_update_state = "Installed"
            self.data.firmware_update_completed_at = now
            self.data.firmware_update_failure_reason = None
            self.data.firmware_update_expected_reconnect_by = None
            self._schedule_firmware_server_auto_stop()
            return

        if normalized in {
            "DownloadFailed",
            "InstallationFailed",
            "InvalidSignature",
            "SignatureVerifiedFailed",
        }:
            self.data.firmware_update_state = "Failed"
            self.data.firmware_update_completed_at = now
            self.data.firmware_update_failure_reason = normalized
            self.data.firmware_update_expected_reconnect_by = None

    def _apply_firmware_transfer_event(self, event: dict[str, Any]) -> None:
        """Advance the local firmware update state from transfer-server events."""

        event_type = event.get("event")
        if not event_type:
            return

        now = datetime.now(UTC)

        if event_type == "download_started" and self.data.firmware_update_state is None:
            self.data.firmware_update_state = "Downloading"
            return

        if event_type in {"checksum_ok", "file_sent"}:
            if self.data.firmware_update_state not in {"Installed", "Failed"}:
                self.data.firmware_update_state = "Downloaded"
                self.data.firmware_update_download_completed_at = (
                    self.data.firmware_update_download_completed_at or now
                )
            return

        if event_type in {"checksum_mismatch", "file_not_found"}:
            self.data.firmware_update_state = "Failed"
            self.data.firmware_update_completed_at = now
            self.data.firmware_update_failure_reason = str(event_type)
            self.data.firmware_update_expected_reconnect_by = None

    def _handle_firmware_disconnect(self) -> None:
        """Infer a transition into install phase when OCPP drops after download."""

        now = datetime.now(UTC)
        if self.data.firmware_update_state != "Downloaded":
            return
        completed_at = self.data.firmware_update_download_completed_at
        if completed_at is None:
            return
        if now - completed_at > FIRMWARE_INSTALL_DISCONNECT_GRACE:
            return

        self.data.firmware_update_state = "Installing"
        self.data.firmware_update_install_started_at = (
            self.data.firmware_update_install_started_at or now
        )
        self.data.firmware_update_expected_reconnect_by = now + FIRMWARE_INSTALLING_TIMEOUT

    def _handle_firmware_reconnect(self) -> None:
        """Clear transient reconnect expectations and prepare for version check."""

        if self.data.firmware_update_state == "Installing":
            self.data.firmware_update_expected_reconnect_by = (
                datetime.now(UTC) + FIRMWARE_INSTALLING_TIMEOUT
            )

    def _handle_firmware_version_observed(self) -> None:
        """Mark installs successful when the charger comes back on a new version."""

        current_version = self.data.firmware_version
        if not current_version:
            return

        if self.data.firmware_update_state not in {"Downloaded", "Installing"}:
            return

        previous_version = self.data.firmware_update_previous_version
        target_version = self.data.firmware_update_target_version
        version_changed = previous_version is not None and current_version != previous_version
        target_reached = target_version is not None and current_version == target_version

        if version_changed or target_reached:
            self.data.firmware_update_state = "Installed"
            self.data.firmware_update_completed_at = datetime.now(UTC)
            self.data.firmware_update_failure_reason = None
            self.data.firmware_update_expected_reconnect_by = None
            self._schedule_firmware_server_auto_stop()

    @staticmethod
    def _derive_firmware_version_from_filename(filename: str | None) -> str | None:
        """Convert a firmware filename into the version string reported by the charger."""

        if not filename:
            return None
        name = Path(filename).name
        if name.lower().endswith(".bin"):
            name = name[:-4]
        return name or None

    async def async_start_charging(self) -> dict[str, Any]:
        """Request an immediate charging session."""

        return await self.async_remote_start_transaction(connector_id=1)

    async def async_stop_charging(self) -> dict[str, Any]:
        """Request that the current charging session stops."""

        return await self.async_remote_stop_transaction()

    async def _async_handle_plug_and_go_start(self) -> None:
        """Start charging after a real plug-in edge when plug-and-go is enabled."""

        try:
            await self.async_start_charging()
        except HomeAssistantError as err:
            _LOGGER.warning("Plug and Go failed to start charging: %s", err)
        except Exception:  # pragma: no cover - defensive logging
            _LOGGER.exception("Unexpected error while handling Plug and Go start")

    async def _async_send_command(
        self, action: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Send an outbound OCPP call through the active server session."""

        if self.server is None:
            raise HomeAssistantError("OCPP server is not running")
        charge_point_id = self.data.charge_point_id or self.data.path_charge_point_id

        result = await self.server.async_send_call(
            charge_point_id, action, payload, timeout=self.command_timeout
        )
        return result

    def _parse_cp_reading(self, payload: Any) -> dict[str, float] | None:
        """Parse a CP voltage/duty cycle payload into structured values."""

        if not isinstance(payload, str):
            return None

        match = CP_READING_PATTERN.fullmatch(payload.strip())
        if match is None:
            return None

        return {
            "voltage_v": float(match.group("voltage")),
            "duty_cycle_percent": float(match.group("duty")),
        }

    def _derive_command_result_status(self, result: Any) -> str:
        """Extract a user-facing status from an outbound command response."""

        if isinstance(result, dict):
            status = result.get("status")
            if status is not None:
                return str(status)
            if result == {}:
                return "Success"
        return "Unknown"

    @callback
    def _publish_state(self) -> None:
        """Push the current state to entities."""

        self.async_set_updated_data(self.data)
        if self._store is not None:
            self._store.async_delay_save(self._serialize_storage_state, 1.0)

    @callback
    def publish_state(self) -> None:
        """Public wrapper used by the hub to push state changes."""

        self._publish_state()

    @callback
    def _touch_last_seen(self) -> None:
        """Update the last-seen timestamp."""

        self.data.last_seen = datetime.now(UTC)
        self._update_heartbeat_age()

    @callback
    def _update_heartbeat_age(self) -> None:
        """Recompute the heartbeat age."""

        if self.data.last_heartbeat is None:
            self.data.heartbeat_age_seconds = None
            return

        age = datetime.now(UTC) - self.data.last_heartbeat
        self.data.heartbeat_age_seconds = max(int(age.total_seconds()), 0)

    @callback
    def _derive_operational_status(self, status: str | None) -> str | None:
        """Convert the raw OCPP status to a simpler HA-facing state."""

        if status is None:
            return None
        if status == "Unavailable":
            return "Inoperative"
        if status == "Faulted":
            return "Faulted"
        return "Operative"

    @staticmethod
    def _is_car_plugged_in_status(status: str | None) -> bool | None:
        """Return whether the raw charger status indicates a plugged-in car."""

        if status is None:
            return None
        return status in {
            "Preparing",
            "Charging",
            "SuspendedEVSE",
            "SuspendedEV",
            "Finishing",
        }

    async def _async_upsert_device(self) -> None:
        """Create or update the charger device."""

        device_registry = dr.async_get(self.hass)
        device_registry.async_get_or_create(
            config_entry_id=self.entry.entry_id,
            **self.device_info,
        )

    async def _async_handle_timer(self, _now: datetime) -> None:
        """Update derived age metrics."""

        previous_age = self.data.heartbeat_age_seconds
        previous_duration = self.data.session_duration_seconds
        previous_firmware_state = self.data.firmware_update_state
        self._update_heartbeat_age()
        if self.data.transaction_active and self.data.transaction_started_at is not None:
            self.data.session_duration_seconds = max(
                int(
                    (
                        datetime.now(UTC) - self.data.transaction_started_at
                    ).total_seconds()
                ),
                0,
            )
        self._advance_firmware_state_from_timeouts()
        if previous_age != self.data.heartbeat_age_seconds:
            self._publish_state()
            return
        if previous_duration != self.data.session_duration_seconds:
            self._publish_state()
            return
        if previous_firmware_state != self.data.firmware_update_state:
            self._publish_state()

    def as_diagnostics_dict(self) -> dict[str, Any]:
        """Return a serialisable diagnostics snapshot."""

        state = asdict(self.data)
        for key in (
            "last_seen",
            "last_heartbeat",
            "transaction_started_at",
            "transaction_ended_at",
            "firmware_update_started_at",
            "firmware_update_download_completed_at",
            "firmware_update_install_started_at",
            "firmware_update_completed_at",
            "firmware_update_expected_reconnect_by",
        ):
            value = state.get(key)
            if value is not None:
                state[key] = value.isoformat()
        return {
            "entry_id": self.entry.entry_id,
            "data": dict(self.entry.data),
            "options": dict(self.entry.options),
            "state": state,
        }

    def _refresh_available_firmware_files(self) -> None:
        """Refresh the list of firmware files available from the manifest catalog."""

        files = sorted(self.data.firmware_manifest_entries)
        self.data.available_firmware_files = files

        if self.data.selected_firmware_file not in files:
            self.data.selected_firmware_file = files[0] if files else None

    def firmware_cache_path(self, filename: str) -> Path:
        """Return the local cache path for a firmware filename."""

        return self.firmware_directory / filename

    def is_firmware_cached(self, filename: str) -> bool:
        """Return whether a firmware file is already present in the local cache."""

        return self.firmware_cache_path(filename).is_file()

    def cached_firmware_files(self) -> list[str]:
        """Return cached firmware files from the current manifest-backed catalog."""

        return [
            filename
            for filename in self.data.available_firmware_files
            if self.is_firmware_cached(filename)
        ]

    @staticmethod
    def _compute_md5(path: Path) -> str:
        """Compute the MD5 checksum of a firmware file."""

        digest = hashlib.md5()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    def _configuration_value(self, key: str) -> Any:
        """Return a configuration value if available."""

        return self.data.configuration.get(key, {}).get("value")

    def _extract_max_import_capacity(self) -> int | None:
        """Extract the max import capacity from the Imax configuration key."""

        raw = self._configuration_value("Imax")
        return self._coerce_int(raw)

    def _extract_configured_current_limit(
        self, *, previous_value: float | None = None
    ) -> float | None:
        """Extract the configured charge-current limit from charger settings."""

        saw_candidate = False
        for key in ("ChargeRate", "MaxCurrent"):
            raw_value = self._configuration_value(key)
            if raw_value in (None, ""):
                continue

            saw_candidate = True
            limit = self._sanitize_current_limit_value(
                raw_value, fallback=None, config_key=key
            )
            if limit is not None:
                return limit

        if saw_candidate:
            return previous_value or DEFAULT_EVSE_MAX_CURRENT

        return previous_value

    def _sanitize_current_limit_value(
        self,
        value: Any,
        *,
        fallback: float | None,
        config_key: str | None = None,
    ) -> float | None:
        """Accept only values that look like a real GivEnergy charge-current limit."""

        amperage = self._coerce_float(value)
        if amperage is None:
            return fallback

        if DEFAULT_EVSE_MIN_CURRENT <= amperage <= DEFAULT_EVSE_MAX_CURRENT:
            return round(amperage, 1)

        if config_key:
            _LOGGER.debug(
                "Ignoring %s=%s as a charge-current limit because it is outside the "
                "supported %s-%sA range",
                config_key,
                value,
                DEFAULT_EVSE_MIN_CURRENT,
                DEFAULT_EVSE_MAX_CURRENT,
            )
        return fallback

    @staticmethod
    def _normalize_charge_mode(value: Any) -> str | None:
        """Normalize the charger's GivEnergy mode value."""

        if value in (None, ""):
            return None
        normalized = str(value).strip()
        for option in GIVENERGY_CHARGE_MODES:
            if normalized.casefold() == option.casefold():
                return option
        return normalized

    def _serialize_storage_state(self) -> dict[str, Any]:
        """Serialize persisted transaction/session state."""

        state = self.export_reload_state()
        for key in (
            "transaction_started_at",
            "transaction_ended_at",
            "firmware_update_started_at",
            "firmware_update_download_completed_at",
            "firmware_update_install_started_at",
            "firmware_update_completed_at",
            "firmware_update_expected_reconnect_by",
        ):
            value = state.get(key)
            if isinstance(value, datetime):
                state[key] = value.isoformat()
        return state

    def _advance_firmware_state_from_timeouts(self) -> None:
        """Fail stale firmware-update phases when the charger never reports back."""

        now = datetime.now(UTC)
        state = self.data.firmware_update_state

        if state == "Downloading" and self.data.firmware_update_started_at is not None:
            if now - self.data.firmware_update_started_at > FIRMWARE_DOWNLOADING_TIMEOUT:
                self.data.firmware_update_state = "Failed"
                self.data.firmware_update_completed_at = now
                self.data.firmware_update_failure_reason = "download_timeout"
                self.data.firmware_update_expected_reconnect_by = None
            return

        if state == "Downloaded":
            completed_at = self.data.firmware_update_download_completed_at
            if completed_at is None:
                return
            quiet_for = (
                now - self.data.last_seen
                if self.data.last_seen is not None
                else timedelta.max
            )
            if (
                now - completed_at > FIRMWARE_INSTALL_QUIET_GRACE
                and quiet_for > FIRMWARE_INSTALL_QUIET_GRACE
            ):
                self.data.firmware_update_state = "Installing"
                self.data.firmware_update_install_started_at = (
                    self.data.firmware_update_install_started_at or now
                )
                self.data.firmware_update_expected_reconnect_by = (
                    now + FIRMWARE_INSTALLING_TIMEOUT
                )
            return

        if state == "Installing":
            deadline = self.data.firmware_update_expected_reconnect_by
            if deadline is not None and now > deadline:
                self.data.firmware_update_state = "Failed"
                self.data.firmware_update_completed_at = now
                self.data.firmware_update_failure_reason = "install_timeout"
                self.data.firmware_update_expected_reconnect_by = None

    def _schedule_firmware_server_auto_stop(self) -> None:
        """Stop the firmware server automatically after a confirmed successful install."""

        if not self.data.firmware_server_running or self.firmware_server is None:
            return
        if self._firmware_server_auto_stop_task is not None:
            return
        self._firmware_server_auto_stop_task = self.hass.async_create_task(
            self._async_auto_stop_firmware_server()
        )

    async def _async_auto_stop_firmware_server(self) -> None:
        """Disable the firmware server after a successful update confirmation."""

        try:
            await self.async_set_firmware_server_enabled(False)
        except HomeAssistantError as err:
            _LOGGER.warning(
                "Unable to auto-stop firmware server after successful firmware update: %s",
                err,
            )
        finally:
            self._firmware_server_auto_stop_task = None

    @staticmethod
    def _parse_ocpp_timestamp(value: Any) -> datetime | None:
        """Parse an OCPP timestamp string into UTC datetime."""

        if value in (None, ""):
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:
            return None

    @classmethod
    def _coerce_datetime(cls, value: Any) -> datetime | None:
        """Convert datetime-like persisted values into UTC datetime."""

        if isinstance(value, datetime):
            return value.astimezone(UTC)
        return cls._parse_ocpp_timestamp(value)

    def _flatten_meter_values_payload(
        self, payload: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Flatten MeterValues into a list of parsed samples."""

        flattened: list[dict[str, Any]] = []
        meter_values = payload.get("meterValue") or []

        for group_index, meter_value in enumerate(meter_values):
            timestamp = meter_value.get("timestamp")
            sampled_values = meter_value.get("sampledValue") or []
            for sample_index, sampled_value in enumerate(sampled_values):
                raw_value = sampled_value.get("value")
                numeric_value = self._coerce_float(raw_value)
                measurand = sampled_value.get(
                    "measurand", "Energy.Active.Import.Register"
                )
                phase = sampled_value.get("phase")
                context = sampled_value.get("context")
                location = sampled_value.get("location")
                unit = sampled_value.get("unit")
                normalized_value = self._normalize_sample_value(
                    measurand, unit, numeric_value
                )
                sample_key = "|".join(
                    [
                        measurand,
                        phase or "no_phase",
                        context or "no_context",
                        location or "no_location",
                        unit or "no_unit",
                        str(group_index),
                        str(sample_index),
                    ]
                )
                flattened.append(
                    {
                        "timestamp": timestamp,
                        "group_index": group_index,
                        "sample_index": sample_index,
                        "raw_value": raw_value,
                        "numeric_value": numeric_value,
                        "normalized_value": normalized_value,
                        "measurand": measurand,
                        "phase": phase,
                        "context": context,
                        "location": location,
                        "unit": unit,
                        "sample_key": sample_key,
                    }
                )

        return flattened

    def _pick_preferred_sample(
        self,
        samples: list[dict[str, Any]],
        *,
        measurand: str,
        preferred_phases: tuple[str | None, ...],
        preferred_locations: tuple[str | None, ...] = (None,),
        preferred_contexts: tuple[str | None, ...] = (None,),
        prefer_positive: bool = False,
        prefer_non_negative: bool = False,
    ) -> dict[str, Any] | None:
        """Pick the most useful sample for a given measurand."""

        candidates = [
            sample
            for sample in samples
            if sample["measurand"] == measurand
            and sample["normalized_value"] is not None
        ]
        if not candidates:
            return None

        if prefer_positive:
            positive_candidates = [
                sample for sample in candidates if sample["normalized_value"] > 0
            ]
            if positive_candidates:
                candidates = positive_candidates
        elif prefer_non_negative:
            non_negative_candidates = [
                sample for sample in candidates if sample["normalized_value"] >= 0
            ]
            if non_negative_candidates:
                candidates = non_negative_candidates

        phase_scores = {
            phase: len(preferred_phases) - index
            for index, phase in enumerate(preferred_phases)
        }
        location_scores = {
            location: len(preferred_locations) - index
            for index, location in enumerate(preferred_locations)
        }
        context_scores = {
            context: len(preferred_contexts) - index
            for index, context in enumerate(preferred_contexts)
        }

        def score(item: dict[str, Any]) -> tuple[int, int, int, float]:
            return (
                phase_scores.get(item.get("phase"), 0),
                location_scores.get(item.get("location"), 0),
                context_scores.get(item.get("context"), 0),
                item["normalized_value"],
            )

        return max(candidates, key=score)

    def _group_meter_samples(
        self, samples: list[dict[str, Any]]
    ) -> dict[int, list[dict[str, Any]]]:
        """Group flattened samples by meterValue block."""

        groups: dict[int, list[dict[str, Any]]] = {}
        for sample in samples:
            groups.setdefault(sample["group_index"], []).append(sample)
        return groups

    def _pick_givenergy_ev_meter_group(
        self, groups: dict[int, list[dict[str, Any]]]
    ) -> list[dict[str, Any]] | None:
        """Return the GivEnergy EV charger meter block when present.

        GivEnergy documents meter ID 0 as the charger's internal EV meter.
        In observed OCPP payloads this maps to the first meterValue block.
        """

        ev_group = groups.get(0)
        if not ev_group:
            return None

        seen_measurands = {sample["measurand"] for sample in ev_group}
        if "Power.Active.Import" in seen_measurands and "Voltage" in seen_measurands:
            return ev_group

        return None

    def _pick_preferred_meter_group(
        self, groups: dict[int, list[dict[str, Any]]]
    ) -> list[dict[str, Any]] | None:
        """Pick the most coherent meterValue group for live readings."""

        if not groups:
            return None

        power_delivery_expected = self._status_expects_power_delivery()

        def group_summary(
            samples: list[dict[str, Any]],
        ) -> tuple[float | None, float | None, float | None, float | None]:
            power_sample = self._pick_preferred_sample(
                samples,
                measurand="Power.Active.Import",
                preferred_phases=("L1", None, "L1-N", "N"),
                prefer_positive=power_delivery_expected,
                prefer_non_negative=not power_delivery_expected,
            )
            current_sample = self._pick_preferred_sample(
                samples,
                measurand="Current.Import",
                preferred_phases=("L1", "N", None, "L1-N"),
                prefer_positive=power_delivery_expected,
                prefer_non_negative=not power_delivery_expected,
            )
            voltage_sample = self._pick_preferred_sample(
                samples,
                measurand="Voltage",
                preferred_phases=("L1-N", None, "L1", "N"),
                prefer_non_negative=True,
            )
            energy_sample = self._pick_total_energy_sample(samples, None)
            return (
                power_sample["normalized_value"] if power_sample else None,
                current_sample["normalized_value"] if current_sample else None,
                voltage_sample["normalized_value"] if voltage_sample else None,
                energy_sample["normalized_value"] if energy_sample else None,
            )

        def score(
            item: tuple[int, list[dict[str, Any]]],
        ) -> tuple[int, int, int, int, int, float, float]:
            _group_index, samples = item
            power, current, voltage, energy = group_summary(samples)
            within_current_limit = int(self._sample_within_current_limit(current))
            within_power_limit = int(
                self._sample_within_power_limit(power, current, voltage)
            )
            has_valid_voltage = int(voltage is not None and voltage > 100)
            non_negative_power = int(power is not None and power >= 0)
            charging_like = int(
                power is not None
                and current is not None
                and power > 100
                and current > 0.5
            )
            near_zero_idle = int(
                power is not None
                and current is not None
                and abs(power) <= 50
                and abs(current) <= 0.5
            )
            energy_score = energy or 0.0

            if power_delivery_expected:
                return (
                    within_current_limit,
                    within_power_limit,
                    charging_like,
                    has_valid_voltage,
                    non_negative_power,
                    power or float("-inf"),
                    energy_score,
                )

            return (
                near_zero_idle,
                has_valid_voltage,
                non_negative_power,
                -(abs(power) if power is not None else float("inf")),
                energy_score,
            )

        return max(groups.items(), key=score)[1]

    def _status_expects_power_delivery(self) -> bool:
        """Return whether the charger state implies it should be delivering power."""

        status = self.data.status
        if status is None:
            return self.data.transaction_active
        return status == "Charging"

    def _sample_within_current_limit(self, current: float | None) -> bool:
        """Return whether a current sample is plausible for this charger."""

        if current is None:
            return False
        limit = self.data.current_limit_a or DEFAULT_EVSE_MAX_CURRENT
        return current <= (limit * 1.1)

    def _sample_within_power_limit(
        self,
        power_w: float | None,
        current_a: float | None,
        voltage_v: float | None,
    ) -> bool:
        """Return whether a power sample is plausible for the configured limit."""

        if power_w is None:
            return False

        limit = self.data.current_limit_a or DEFAULT_EVSE_MAX_CURRENT
        reference_voltage = voltage_v if voltage_v and voltage_v > 100 else 240.0
        max_power_w = limit * reference_voltage * 1.1
        if power_w <= max_power_w:
            return True

        if current_a is not None and current_a <= (limit * 1.1):
            return True

        return False

    def _pick_total_energy_sample(
        self, samples: list[dict[str, Any]], previous_total_wh: float | None
    ) -> dict[str, Any] | None:
        """Choose the best lifetime energy candidate from ambiguous samples."""

        # TODO: refine this heuristic once we have captured more real GivEnergy
        # meter traces. Some firmware versions appear to report multiple
        # non-zero import registers and the lifetime counter needs to stay stable.
        candidates = [
            sample
            for sample in samples
            if sample["measurand"] == "Energy.Active.Import.Register"
            and sample["normalized_value"] is not None
        ]
        if not candidates:
            return None

        non_zero = [sample for sample in candidates if sample["normalized_value"] > 0]
        pool = non_zero or candidates

        if previous_total_wh is None:
            return max(pool, key=lambda item: item["normalized_value"])

        non_decreasing = [
            sample
            for sample in pool
            if sample["normalized_value"] >= previous_total_wh
        ]
        if non_decreasing:
            return min(
                non_decreasing,
                key=lambda item: item["normalized_value"] - previous_total_wh,
            )

        return min(
            pool, key=lambda item: abs(item["normalized_value"] - previous_total_wh)
        )

    def _normalize_sample_value(
        self, measurand: str, unit: str | None, value: float | None
    ) -> float | None:
        """Normalise sample values onto a stable unit for HA entities."""

        if value is None:
            return None
        if measurand == "Power.Active.Import" and unit == "kW":
            return value * 1000
        if measurand == "Energy.Active.Import.Register" and unit == "kWh":
            return value * 1000
        return value

    @staticmethod
    def _coerce_float(value: Any) -> float | None:
        """Convert an OCPP numeric field into float if possible."""

        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _coerce_bool(value: Any) -> bool | None:
        """Convert common OCPP boolean representations into bool."""

        if isinstance(value, bool):
            return value
        if value is None:
            return None
        normalized = str(value).strip().lower()
        if normalized in {"1", "true", "on", "yes"}:
            return True
        if normalized in {"0", "false", "off", "no"}:
            return False
        return None

    @staticmethod
    def _coerce_int(value: Any) -> int | None:
        """Convert an OCPP numeric field into int if possible."""

        if value in (None, ""):
            return None
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _split_csv_configuration_value(value: Any) -> list[str]:
        """Split comma-separated configuration strings."""

        if value in (None, ""):
            return []
        return [item.strip() for item in str(value).split(",") if item.strip()]

    @staticmethod
    def _firmware_version_at_least(version: str | None, major: int, minor: int) -> bool:
        """Return True if the reported firmware version is >= major.minor.

        Firmware version strings follow the pattern ``AC_GL1_1.14`` — the
        version number is the segment after the final underscore.
        """

        if not version:
            return False
        try:
            numeric = version.rsplit("_", 1)[-1]
            parts = numeric.split(".")
            fw_major = int(parts[0])
            fw_minor = int(parts[1]) if len(parts) > 1 else 0
            return (fw_major, fw_minor) >= (major, minor)
        except (ValueError, IndexError):
            return False
