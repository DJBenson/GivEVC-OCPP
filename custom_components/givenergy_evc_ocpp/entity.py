"""Shared entity helpers for GivEnergy EVC OCPP."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import GivEnergyEvcCoordinator


class GivEnergyEvcEntity(CoordinatorEntity[GivEnergyEvcCoordinator]):
    """Base entity for GivEnergy EVC OCPP."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: GivEnergyEvcCoordinator, key: str) -> None:
        """Initialise the entity."""

        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entity_unique_id_prefix}_{key}"

    @property
    def device_info(self):
        """Return device information."""

        return self.coordinator.device_info

    @property
    def available(self) -> bool:
        """Return whether the entity has enough data to be useful."""

        return self.coordinator.has_device


class GivEnergyChargePointEntity(GivEnergyEvcEntity):
    """Shared base for charger-scoped entities."""

    def __init__(self, coordinator: GivEnergyEvcCoordinator, key: str) -> None:
        """Initialise the charger-scoped entity."""

        super().__init__(coordinator, key)


class GivEnergyPendingChargePointEntity(GivEnergyEvcEntity):
    """Base entity for a pending discovered charger."""

    @property
    def available(self) -> bool:
        """Pending charger entities remain available until accepted."""

        charge_point_id = (
            self.coordinator.data.charge_point_id or self.coordinator.data.path_charge_point_id
        )
        return bool(charge_point_id and not self.coordinator.data.adopted)
