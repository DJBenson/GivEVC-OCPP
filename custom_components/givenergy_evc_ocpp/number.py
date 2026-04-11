"""Number entities for GivEnergy EVC OCPP."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfElectricCurrent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DEFAULT_EVSE_MAX_CURRENT, DEFAULT_EVSE_MIN_CURRENT, DOMAIN
from .coordinator import GivEnergyEvcCoordinator
from .entity import GivEnergyEvcEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up number entities."""

    runtime = hass.data[DOMAIN][entry.entry_id]
    coordinator: GivEnergyEvcCoordinator = runtime.coordinator
    async_add_entities([GivEnergyCurrentLimitNumber(coordinator)])


class GivEnergyCurrentLimitNumber(GivEnergyEvcEntity, NumberEntity):
    """Writable current-limit entity."""

    _attr_translation_key = "current_limit"
    _attr_native_min_value = DEFAULT_EVSE_MIN_CURRENT
    _attr_native_max_value = DEFAULT_EVSE_MAX_CURRENT
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_mode = NumberMode.SLIDER
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: GivEnergyEvcCoordinator) -> None:
        """Initialise the number."""

        super().__init__(coordinator, "current_limit_number")

    @property
    def available(self) -> bool:
        """The number requires an identified charger."""

        return super().available and self.coordinator.data.connected

    @property
    def native_value(self) -> float | None:
        """Return the current configured limit."""

        return self.coordinator.data.current_limit_a

    async def async_set_native_value(self, value: float) -> None:
        """Set the charger current limit."""

        await self.coordinator.async_set_current_limit(value)
