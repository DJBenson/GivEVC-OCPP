"""Hub runtime for multi-charge-point GivEnergy EVC OCPP support."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store

from .const import DOMAIN

if TYPE_CHECKING:
    from .coordinator import GivEnergyEvcCoordinator
    from .firmware_transfer_server import GivEnergyFirmwareTransferServer
    from .server import GivEnergyOcppServer

HUB_STORAGE_VERSION = 1
SIGNAL_ACCEPTED_CHARGE_POINT = f"{DOMAIN}_accepted_charge_point"


class GivEnergyChargePointHub:
    """Manage discovered and accepted chargers behind one listener."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry,
        primary_coordinator: GivEnergyEvcCoordinator,
    ) -> None:
        """Initialise the hub registry."""

        self.hass = hass
        self.entry = entry
        self.primary_coordinator = primary_coordinator
        self.server: GivEnergyOcppServer | None = None
        self.firmware_server: GivEnergyFirmwareTransferServer | None = None
        self._store = Store[dict[str, Any]](
            hass, HUB_STORAGE_VERSION, f"{DOMAIN}_{entry.entry_id}_hub"
        )
        self._accepted_charge_points: set[str] = set()
        self._secondary_coordinators: dict[str, GivEnergyEvcCoordinator] = {}
        self._signalled_accepted: set[str] = set()
        self._primary_claimed: bool = False

    async def async_restore_persisted_state(self) -> None:
        """Restore accepted charger IDs from storage."""

        stored = await self._store.async_load() or {}
        accepted = stored.get("accepted_charge_points") or []
        self._accepted_charge_points = {
            str(charge_point_id).strip()
            for charge_point_id in accepted
            if str(charge_point_id).strip()
        }

        primary_id = self.primary_coordinator.data.charge_point_id
        if primary_id:
            self._accepted_charge_points.add(primary_id)
            self.primary_coordinator.data.adopted = True
            self._primary_claimed = True

    async def async_start(self) -> None:
        """Create runtime coordinators for any accepted secondary chargers."""

        from .coordinator import GivEnergyEvcCoordinator

        primary_id = self.primary_charge_point_id
        for charge_point_id in sorted(self._accepted_charge_points):
            if charge_point_id == primary_id:
                continue
            coordinator = GivEnergyEvcCoordinator(
                self.hass,
                self.entry,
                charge_point_id=charge_point_id,
                legacy_entity_ids=False,
                use_storage=False,
            )
            coordinator.data.charge_point_id = charge_point_id
            coordinator.data.adopted = True
            self._attach_shared_runtime(coordinator)
            await coordinator.async_start(manage_firmware_server=False)
            self._secondary_coordinators[charge_point_id] = coordinator

    async def async_stop(self) -> None:
        """Stop secondary coordinators."""

        for coordinator in self._secondary_coordinators.values():
            await coordinator.async_stop()

    @property
    def primary_charge_point_id(self) -> str | None:
        """Return the current primary charge point ID."""

        return self.primary_coordinator.data.charge_point_id

    @property
    def accepted_charge_points(self) -> set[str]:
        """Return accepted charge point IDs."""

        return set(self._accepted_charge_points)

    def attach_server(self, server: GivEnergyOcppServer) -> None:
        """Attach the shared websocket server."""

        self.server = server
        self.primary_coordinator.set_server(server)
        for coordinator in self._secondary_coordinators.values():
            coordinator.set_server(server)

    def attach_firmware_server(self, firmware_server: GivEnergyFirmwareTransferServer) -> None:
        """Attach the shared firmware transfer server."""

        self.firmware_server = firmware_server
        self.primary_coordinator.set_firmware_server(firmware_server)
        for coordinator in self._secondary_coordinators.values():
            coordinator.set_firmware_server(firmware_server, register_events=False)

    def coordinator_for_connection(
        self, candidate_id: str | None
    ) -> GivEnergyEvcCoordinator:
        """Resolve the coordinator to use for a new websocket connection."""

        normalized_id = self._normalize_id(candidate_id)
        primary_id = self.primary_charge_point_id

        if normalized_id and normalized_id == primary_id:
            return self.primary_coordinator

        if normalized_id and normalized_id in self._secondary_coordinators:
            return self._secondary_coordinators[normalized_id]

        if normalized_id and not self._primary_claimed:
            # First ever connection — claim the primary coordinator atomically
            # so a second simultaneous connection doesn't also take it.
            self._primary_claimed = True
            return self.primary_coordinator

        if normalized_id:
            coordinator = self._secondary_coordinators.get(normalized_id)
            if coordinator is None:
                from .coordinator import GivEnergyEvcCoordinator

                coordinator = GivEnergyEvcCoordinator(
                    self.hass,
                    self.entry,
                    charge_point_id=normalized_id,
                    legacy_entity_ids=False,
                    use_storage=False,
                )
                coordinator.data.charge_point_id = normalized_id
                self._attach_shared_runtime(coordinator)
                self._secondary_coordinators[normalized_id] = coordinator
                self.hass.async_create_task(
                    coordinator.async_start(manage_firmware_server=False)
                )
            return coordinator

        return self.primary_coordinator

    async def async_note_discovered_charge_point(
        self, coordinator: GivEnergyEvcCoordinator
    ) -> None:
        """Record and auto-accept a discovered charger."""

        charge_point_id = self._coordinator_charge_point_id(coordinator)
        if not charge_point_id:
            return

        normalized_id = self._normalize_id(charge_point_id)
        if normalized_id is None:
            return

        already_accepted = normalized_id in self._accepted_charge_points

        if not already_accepted:
            coordinator.data.adopted = True
            if coordinator is self.primary_coordinator and coordinator.data.charge_point_id is None:
                coordinator.data.charge_point_id = normalized_id

            self._accepted_charge_points.add(normalized_id)
            self._schedule_save()
            coordinator.publish_state()

        if coordinator is self.primary_coordinator:
            return

        if normalized_id not in self._signalled_accepted:
            self._signalled_accepted.add(normalized_id)
            # Schedule the signal as a task so it runs with a proper task context
            # on the HA event loop. Firing inline via async_dispatcher_send causes
            # async_add_entities (called from the signal listener) to fail with
            # "loop is not the running loop" under Python 3.14 due to eager_start.
            self.hass.async_create_task(
                self._async_dispatch_accepted(normalized_id, coordinator),
                f"givenergy_evc_ocpp_signal_{normalized_id}",
            )

    async def _async_dispatch_accepted(
        self, normalized_id: str, coordinator: GivEnergyEvcCoordinator
    ) -> None:
        """Dispatch the accepted charge point signal as a proper HA task."""

        async_dispatcher_send(
            self.hass, SIGNAL_ACCEPTED_CHARGE_POINT, self.entry.entry_id, coordinator
        )

    async def async_remove_charge_point(self, charge_point_id: str) -> bool:
        """Remove a secondary charger from the hub."""

        normalized_id = self._normalize_id(charge_point_id)
        if normalized_id is None:
            return False

        if normalized_id == self.primary_charge_point_id:
            return False

        coordinator = self._secondary_coordinators.pop(normalized_id, None)
        self._accepted_charge_points.discard(normalized_id)
        self._signalled_accepted.discard(normalized_id)
        self._schedule_save()

        if coordinator is not None:
            if self.server is not None:
                await self.server.async_disconnect_charge_point(normalized_id)
            await coordinator.async_stop()

        return True

    def get_charge_point_coordinator(
        self, charge_point_id: str | None
    ) -> GivEnergyEvcCoordinator | None:
        """Return the coordinator for a known charge point ID."""

        normalized_id = self._normalize_id(charge_point_id)
        if normalized_id is None:
            return self.primary_coordinator

        if normalized_id == self.primary_charge_point_id:
            return self.primary_coordinator

        return self._secondary_coordinators.get(normalized_id)

    def accepted_secondary_coordinators(self) -> list[GivEnergyEvcCoordinator]:
        """Return accepted non-primary charger coordinators."""

        return [
            coordinator
            for charge_point_id, coordinator in sorted(self._secondary_coordinators.items())
            if charge_point_id in self._accepted_charge_points
        ]

    def resolve_service_target(
        self, charge_point_id: str | None
    ) -> GivEnergyEvcCoordinator:
        """Resolve a service target, defaulting to the legacy primary charger."""

        if charge_point_id:
            coordinator = self.get_charge_point_coordinator(charge_point_id)
            if coordinator is None:
                raise HomeAssistantError(f"Charge point {charge_point_id} is not known")
            return coordinator
        return self.primary_coordinator

    def _attach_shared_runtime(self, coordinator: GivEnergyEvcCoordinator) -> None:
        """Attach the shared server and firmware runtime to a coordinator."""

        if self.server is not None:
            coordinator.set_server(self.server)
        if self.firmware_server is not None:
            coordinator.set_firmware_server(self.firmware_server, register_events=False)

    def _schedule_save(self) -> None:
        """Persist accepted charge points."""

        self._store.async_delay_save(
            lambda: {"accepted_charge_points": sorted(self._accepted_charge_points)},
            1.0,
        )

    @staticmethod
    def charge_point_id_from_device(device_entry: dr.DeviceEntry) -> str | None:
        """Extract the charge point ID from a device entry."""

        for domain, value in device_entry.identifiers:
            if domain != DOMAIN:
                continue
            # New format: "<entry_id>:charge_point_id:<id>"
            if ":charge_point_id:" in value:
                return value.split(":charge_point_id:", 1)[1]
            # Legacy format: "charge_point_id:<id>"
            if value.startswith("charge_point_id:"):
                return value.split(":", 1)[1]
        return None

    @staticmethod
    def _normalize_id(charge_point_id: str | None) -> str | None:
        """Normalize a charge point ID."""

        if not charge_point_id:
            return None
        normalized = str(charge_point_id).strip()
        return normalized or None

    def _coordinator_charge_point_id(self, coordinator: GivEnergyEvcCoordinator) -> str | None:
        """Return the best-known charge point ID for a coordinator."""

        return coordinator.data.charge_point_id or coordinator.data.path_charge_point_id
