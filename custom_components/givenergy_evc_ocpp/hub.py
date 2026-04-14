"""Hub runtime for multi-charge-point GivEnergy EVC OCPP support."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store

from .const import DOMAIN

if TYPE_CHECKING:
    from .coordinator import GivEnergyEvcCoordinator
    from .firmware_transfer_server import GivEnergyFirmwareTransferServer
    from .server import GivEnergyOcppServer

_LOGGER = logging.getLogger(__name__)

HUB_STORAGE_VERSION = 1
SIGNAL_PENDING_CHARGE_POINT = f"{DOMAIN}_pending_charge_point"
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
        self._pending_charge_points: set[str] = set()
        self._secondary_coordinators: dict[str, GivEnergyEvcCoordinator] = {}
        self._signalled_pending: set[str] = set()
        self._signalled_accepted: set[str] = set()

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

    @property
    def pending_charge_points(self) -> set[str]:
        """Return pending charge point IDs."""

        return set(self._pending_charge_points)

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

        if (
            normalized_id
            and primary_id is None
            and self.primary_coordinator.data.path_charge_point_id in (None, normalized_id)
            and not self._secondary_coordinators
        ):
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
        """Record a discovered but not yet accepted charger."""

        charge_point_id = self._coordinator_charge_point_id(coordinator)
        if not charge_point_id:
            return

        if charge_point_id in self._accepted_charge_points:
            return

        self._pending_charge_points.add(charge_point_id)
        if coordinator is not self.primary_coordinator and charge_point_id not in self._signalled_pending:
            self._signalled_pending.add(charge_point_id)
            async_dispatcher_send(
                self.hass, SIGNAL_PENDING_CHARGE_POINT, self.entry.entry_id, coordinator
            )
        self._schedule_save()

    async def async_accept_charge_point(self, charge_point_id: str) -> None:
        """Accept a discovered charger and onboard its full entity set."""

        normalized_id = self._normalize_id(charge_point_id)
        if normalized_id is None:
            return

        coordinator = self.get_charge_point_coordinator(normalized_id)
        if coordinator is None:
            raise ValueError(f"Unknown charge point ID: {charge_point_id}")

        coordinator.data.adopted = True
        if coordinator is self.primary_coordinator and coordinator.data.charge_point_id is None:
            coordinator.data.charge_point_id = normalized_id

        self._pending_charge_points.discard(normalized_id)
        self._accepted_charge_points.add(normalized_id)
        self._schedule_save()
        coordinator.publish_state()

        if coordinator is self.primary_coordinator:
            return

        if normalized_id not in self._signalled_accepted:
            self._signalled_accepted.add(normalized_id)
            async_dispatcher_send(
                self.hass, SIGNAL_ACCEPTED_CHARGE_POINT, self.entry.entry_id, coordinator
            )

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

    def pending_secondary_coordinators(self) -> list[GivEnergyEvcCoordinator]:
        """Return pending non-primary charger coordinators."""

        return [
            coordinator
            for charge_point_id, coordinator in sorted(self._secondary_coordinators.items())
            if charge_point_id in self._pending_charge_points
        ]

    def resolve_service_target(
        self, charge_point_id: str | None
    ) -> GivEnergyEvcCoordinator:
        """Resolve a service target, defaulting to the legacy primary charger."""

        if charge_point_id:
            coordinator = self.get_charge_point_coordinator(charge_point_id)
            if coordinator is None or not coordinator.data.adopted:
                raise HomeAssistantError(
                    f"Charge point {charge_point_id} is not accepted"
                )
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
    def _normalize_id(charge_point_id: str | None) -> str | None:
        """Normalize a charge point ID."""

        if not charge_point_id:
            return None
        normalized = str(charge_point_id).strip()
        return normalized or None

    def _coordinator_charge_point_id(self, coordinator: GivEnergyEvcCoordinator) -> str | None:
        """Return the best-known charge point ID for a coordinator."""

        return coordinator.data.charge_point_id or coordinator.data.path_charge_point_id
