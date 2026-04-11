"""Shared entity helpers for GivEnergy EVC OCPP."""

from __future__ import annotations

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import GivEnergyEvcCoordinator


class GivEnergyEvcEntity(CoordinatorEntity[GivEnergyEvcCoordinator]):
    """Base entity for GivEnergy EVC OCPP."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: GivEnergyEvcCoordinator, key: str) -> None:
        """Initialise the entity."""

        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{key}"

    @property
    def device_info(self):
        """Return device information."""

        return self.coordinator.device_info

    @property
    def available(self) -> bool:
        """Return whether the entity has enough data to be useful."""

        return self.coordinator.has_device
