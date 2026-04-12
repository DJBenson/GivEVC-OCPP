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
    async_add_entities(
        [
            GivEnergyChargeModeSelect(coordinator),
            GivEnergyFirmwareFileSelect(coordinator),
        ]
    )


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


class GivEnergyFirmwareFileSelect(GivEnergyEvcEntity, SelectEntity):
    """Select entity for bundled firmware files served by the local transfer server."""

    _attr_translation_key = "firmware_file"
    _attr_icon = "mdi:file-download-outline"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: GivEnergyEvcCoordinator) -> None:
        """Initialise the select."""

        super().__init__(coordinator, "firmware_file")

    @property
    def available(self) -> bool:
        """Only expose the firmware list while the local transfer server is running."""

        return self.coordinator.data.firmware_server_running

    @property
    def current_option(self) -> str | None:
        """Return the selected firmware file."""

        return self.coordinator.data.selected_firmware_file

    @property
    def options(self) -> list[str]:
        """Return available bundled firmware files."""

        self.coordinator._refresh_available_firmware_files()
        return list(self.coordinator.data.available_firmware_files)

    async def async_select_option(self, option: str) -> None:
        """Select a bundled firmware file."""

        await self.coordinator.async_set_selected_firmware_file(option)
