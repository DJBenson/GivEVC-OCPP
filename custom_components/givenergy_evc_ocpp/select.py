"""Select entities for GivEnergy EVC OCPP."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import GivEnergyEvcCoordinator
from .entity import GivEnergyEvcEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up select entities."""

    runtime = hass.data[DOMAIN][entry.entry_id]
    coordinator: GivEnergyEvcCoordinator = runtime.coordinator
    async_add_entities([GivEnergyChargeModeSelect(coordinator)])


class GivEnergyChargeModeSelect(GivEnergyEvcEntity, SelectEntity):
    """Select entity for GivEnergy charger mode."""

    _attr_translation_key = "charge_mode"
    _attr_icon = "mdi:leaf-circle-outline"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: GivEnergyEvcCoordinator) -> None:
        """Initialise the select."""

        super().__init__(coordinator, "charge_mode")

    @property
    def available(self) -> bool:
        """The select requires an identified, connected charger."""

        return super().available and self.coordinator.data.connected

    @property
    def current_option(self) -> str | None:
        """Return the current charger mode."""

        return self.coordinator.data.charge_mode

    @property
    def options(self) -> list[str]:
        """Return available charger modes."""

        return self.coordinator.available_charge_modes

    async def async_select_option(self, option: str) -> None:
        """Select a new charger mode."""

        await self.coordinator.async_set_charge_mode(option)
