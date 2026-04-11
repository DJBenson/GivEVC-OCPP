"""Binary sensors for GivEnergy EVC OCPP."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import GivEnergyEvcCoordinator
from .entity import GivEnergyEvcEntity

_PLUGGED_IN_STATUSES = {"Preparing", "Charging", "SuspendedEVSE", "SuspendedEV", "Finishing"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensors for the config entry."""

    runtime = hass.data[DOMAIN][entry.entry_id]
    coordinator: GivEnergyEvcCoordinator = runtime.coordinator
    async_add_entities([GivEnergyPluggedInBinarySensor(coordinator)])


class GivEnergyPluggedInBinarySensor(GivEnergyEvcEntity, BinarySensorEntity):
    """Binary sensor that is on when a car is plugged in."""

    _attr_translation_key = "car_plugged_in"
    _attr_device_class = BinarySensorDeviceClass.PLUG

    def __init__(self, coordinator: GivEnergyEvcCoordinator) -> None:
        """Initialise the binary sensor."""

        super().__init__(coordinator, "car_plugged_in")

    @property
    def is_on(self) -> bool | None:
        """Return True when a car is plugged in."""

        if self.coordinator.data.status is None:
            return None
        return self.coordinator.data.status in _PLUGGED_IN_STATUSES
