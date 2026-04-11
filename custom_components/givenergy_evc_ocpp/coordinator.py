"""State coordinator for GivEnergy EVC OCPP."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
import logging
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfElectricCurrent, UnitOfElectricPotential
from homeassistant.const import UnitOfEnergy, UnitOfPower, UnitOfTime
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_ADOPT_FIRST_CHARGER,
    CONF_COMMAND_TIMEOUT,
    CONF_DEBUG_LOGGING,
    CONF_ENHANCED_LOGGING,
    CONF_EXPECTED_CHARGE_POINT_ID,
    CONF_LISTEN_PORT,
    CONF_METER_VALUE_SAMPLE_INTERVAL,
    DEFAULT_COMMAND_TIMEOUT,
    DEFAULT_EVSE_MAX_CURRENT,
    DEFAULT_EVSE_MIN_CURRENT,
    DEFAULT_ENHANCED_LOGGING,
    GIVENERGY_CHARGE_MODES,
    DEFAULT_METER_VALUE_SAMPLE_INTERVAL,
    DEFAULT_REMOTE_ID_TAG,
    DOMAIN,
    MAX_STORED_OCPP_FRAMES,
)

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1


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
    status: str | None = None
    operational_status: str | None = None
    firmware_status: str | None = None
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
    current_limit_a: float | None = None
    meter_value_sample_interval_seconds: int | None = None
    local_ip_address: str | None = None
    charger_enabled: bool | None = None
    charge_mode: str | None = None
    local_modbus_enabled: bool | None = None
    front_panel_leds_enabled: bool | None = None
    randomised_delay_duration_seconds: int | None = None
    supported_feature_profiles: list[str] = field(default_factory=list)
    configuration: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_boot_notification: dict[str, Any] | None = None
    last_status_notification: dict[str, Any] | None = None
    last_meter_values: dict[str, Any] | None = None
    last_get_configuration: dict[str, Any] | None = None
    last_command_results: dict[str, Any] = field(default_factory=dict)
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


class GivEnergyEvcCoordinator(DataUpdateCoordinator[GivEnergyEvcState]):
    """Coordinator for GivEnergy EVC state and commands."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialise the coordinator."""

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=None,
        )
        self.entry = entry
        self.data = GivEnergyEvcState()
        self.server: Any = None
        self._unsub_timer: CALLBACK_TYPE | None = None
        self._next_transaction_id = 1
        self._store = Store[dict[str, Any]](
            hass, STORAGE_VERSION, f"{DOMAIN}_{entry.entry_id}_state"
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
            "status": self.data.status,
            "charge_point_id": self.data.charge_point_id,
            "charge_point_serial_number": self.data.charge_point_serial_number,
            "charge_box_serial_number": self.data.charge_box_serial_number,
            "last_boot_notification": self.data.last_boot_notification,
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
        self.data.status = state.get("status")
        self.data.charge_point_id = state.get("charge_point_id")
        self.data.charge_point_serial_number = state.get("charge_point_serial_number")
        self.data.charge_box_serial_number = state.get("charge_box_serial_number")
        self.data.last_boot_notification = state.get("last_boot_notification")

        if self.data.transaction_id is not None:
            self._next_transaction_id = max(self._next_transaction_id, self.data.transaction_id + 1)

        self._publish_state()

    async def async_restore_persisted_state(self) -> None:
        """Restore persisted transaction/session state from storage."""

        stored = await self._store.async_load()
        self.restore_reload_state(stored)

    async def async_start(self) -> None:
        """Start coordinator tasks."""

        if self._unsub_timer is None:
            self._unsub_timer = async_track_time_interval(
                self.hass, self._async_handle_timer, timedelta(seconds=30)
            )

    async def async_stop(self) -> None:
        """Stop coordinator tasks."""

        if self._unsub_timer is not None:
            self._unsub_timer()
            self._unsub_timer = None

    @property
    def has_device(self) -> bool:
        """Return whether the charger has been identified."""

        return bool(self.data.charge_point_id or self.data.last_boot_notification)

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
    def expected_charge_point_id(self) -> str | None:
        """Return the configured charge point path filter."""

        value = self.entry.options.get(
            CONF_EXPECTED_CHARGE_POINT_ID,
            self.entry.data.get(CONF_EXPECTED_CHARGE_POINT_ID),
        )
        if value:
            return str(value).strip()
        return None

    @property
    def adopt_first_charger(self) -> bool:
        """Return whether the first charger should be adopted automatically."""

        return bool(
            self.entry.options.get(
                CONF_ADOPT_FIRST_CHARGER,
                self.entry.data.get(CONF_ADOPT_FIRST_CHARGER, True),
            )
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

        identifiers: set[tuple[str, str]] = {(DOMAIN, f"entry:{self.entry.entry_id}")}

        if self.data.charge_point_id:
            identifiers.add((DOMAIN, f"charge_point_id:{self.data.charge_point_id}"))
        if self.data.charge_point_serial_number:
            identifiers.add(
                (
                    DOMAIN,
                    f"charge_point_serial:{self.data.charge_point_serial_number}",
                )
            )
        if self.data.charge_box_serial_number:
            identifiers.add(
                (DOMAIN, f"charge_box_serial:{self.data.charge_box_serial_number}")
            )

        name_parts = [
            part
            for part in (
                self.data.manufacturer or "GivEnergy",
                self.data.model or "EVC",
            )
            if part
        ]
        if self.data.charge_point_id:
            name_parts.append(self.data.charge_point_id)

        return DeviceInfo(
            identifiers=identifiers,
            manufacturer=self.data.manufacturer or "GivEnergy",
            model=self.data.model or "EVC",
            name=" ".join(name_parts),
            sw_version=self.data.firmware_version,
            serial_number=(
                self.data.charge_point_serial_number
                or self.data.charge_box_serial_number
                or self.data.charge_point_id
            ),
        )

    def set_server(self, server: Any) -> None:
        """Attach the running websocket server."""

        self.server = server

    def can_accept_charge_point(self, candidate_id: str | None) -> bool:
        """Return whether the candidate charge point should be accepted."""

        expected = self.expected_charge_point_id
        adopted = self.data.charge_point_id

        if expected and candidate_id and candidate_id != expected:
            return False
        if adopted and candidate_id and candidate_id != adopted:
            return False
        return True

    async def async_note_rejected_charge_point(self, candidate_id: str | None) -> None:
        """Record a rejected charge point."""

        if candidate_id and candidate_id not in self.data.rejected_charge_points:
            self.data.rejected_charge_points.append(candidate_id)
            self._publish_state()

    async def async_connection_opened(self, candidate_id: str | None) -> None:
        """Handle a websocket connection opening."""

        if candidate_id:
            self.data.path_charge_point_id = candidate_id

        if (
            candidate_id
            and not self.data.charge_point_id
            and self.adopt_first_charger
            and not self.expected_charge_point_id
        ):
            self.data.charge_point_id = candidate_id
            self.data.adopted = True

        self.data.connected = True
        self.data.connection_state = "connected"
        self._touch_last_seen()
        await self._async_upsert_device()
        self._publish_state()

    async def async_connection_closed(self) -> None:
        """Handle a websocket connection closing."""

        self.data.connected = False
        self.data.connection_state = "disconnected"
        self._update_heartbeat_age()
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
            self.expected_charge_point_id
            or self.data.charge_point_id
            or candidate_id
            or self.data.charge_point_serial_number
            or self.data.charge_box_serial_number
        )
        if charge_point_id:
            self.data.charge_point_id = charge_point_id
            self.data.adopted = True

        self._touch_last_seen()
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

        self.data.last_status_notification = payload
        self.data.status = payload.get("status")
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
        self._touch_last_seen()
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
        self.data.meter_value_sample_interval_seconds = self._coerce_int(
            self._configuration_value("MeterValueSampleInterval")
        )
        local_ip_address = self._configuration_value("LocalIPAddress")
        self.data.local_ip_address = (
            str(local_ip_address).strip() if local_ip_address not in (None, "") else None
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

            if key in {"ChargeRate", "Imax", "MaxCurrent"}:
                self.data.current_limit_a = self._sanitize_current_limit_value(
                    value,
                    fallback=self.data.current_limit_a,
                    config_key=key,
                )
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

    async def async_set_current_limit(self, amperage: float) -> dict[str, Any]:
        """Change the charger current limit."""

        amperage = max(
            DEFAULT_EVSE_MIN_CURRENT,
            min(DEFAULT_EVSE_MAX_CURRENT, float(amperage)),
        )
        key = (
            "ChargeRate"
            if "ChargeRate" in self.data.configuration
            else "Imax"
            if "Imax" in self.data.configuration
            else "MaxCurrent"
        )
        value: float = round(amperage, 1)
        if key == "ChargeRate":
            value = round(amperage / 10, 1)
        return await self.async_change_configuration(key, value)

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

    async def async_start_charging(self) -> dict[str, Any]:
        """Request an immediate charging session."""

        return await self.async_remote_start_transaction(connector_id=1)

    async def async_stop_charging(self) -> dict[str, Any]:
        """Request that the current charging session stops."""

        return await self.async_remote_stop_transaction()

    async def _async_send_command(
        self, action: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Send an outbound OCPP call through the active server session."""

        if self.server is None:
            raise HomeAssistantError("OCPP server is not running")

        result = await self.server.async_send_call(
            action, payload, timeout=self.command_timeout
        )
        return result

    @callback
    def _publish_state(self) -> None:
        """Push the current state to entities."""

        self.async_set_updated_data(self.data)
        self._store.async_delay_save(self._serialize_storage_state, 1.0)

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
        if previous_age != self.data.heartbeat_age_seconds:
            self._publish_state()
            return
        if previous_duration != self.data.session_duration_seconds:
            self._publish_state()

    def as_diagnostics_dict(self) -> dict[str, Any]:
        """Return a serialisable diagnostics snapshot."""

        state = asdict(self.data)
        for key in (
            "last_seen",
            "last_heartbeat",
            "transaction_started_at",
            "transaction_ended_at",
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

    def _configuration_value(self, key: str) -> Any:
        """Return a configuration value if available."""

        return self.data.configuration.get(key, {}).get("value")

    def _extract_configured_current_limit(
        self, *, previous_value: float | None = None
    ) -> float | None:
        """Extract the configured charge-current limit from charger settings."""

        saw_candidate = False
        for key in ("ChargeRate", "Imax", "MaxCurrent"):
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

        if config_key == "ChargeRate":
            scaled_amperage = amperage * 10
            if DEFAULT_EVSE_MIN_CURRENT <= scaled_amperage <= DEFAULT_EVSE_MAX_CURRENT:
                return round(scaled_amperage, 1)

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
        for key in ("transaction_started_at", "transaction_ended_at"):
            value = state.get(key)
            if isinstance(value, datetime):
                state[key] = value.isoformat()
        return state

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
