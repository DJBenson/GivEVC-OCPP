"""Select entities for GivEnergy EVC OCPP."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import GivEnergyEvcCoordinator
from .entity import GivEnergyEvcEntity
from .hub import SIGNAL_ACCEPTED_CHARGE_POINT


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
    for accepted in runtime.hub.accepted_secondary_coordinators():
        async_add_entities([GivEnergyChargeModeSelect(accepted)])

    def _on_accepted(signal_entry_id: str, target: GivEnergyEvcCoordinator) -> None:
        if signal_entry_id == entry.entry_id:
            hass.async_add_job(async_add_entities, [GivEnergyChargeModeSelect(target)])

    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_ACCEPTED_CHARGE_POINT, _on_accepted)
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
    """Select entity for manifest-backed firmware files served by the local transfer server."""

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
        """Return the selected firmware file label."""

        filename = self.coordinator.data.selected_firmware_file
        return self._option_label(filename) if filename else None

    @property
    def options(self) -> list[str]:
        """Return available manifest-backed firmware files with cache-state labels."""

        self.coordinator._refresh_available_firmware_files()
        return [
            self._option_label(filename)
            for filename in self.coordinator.data.available_firmware_files
        ]

    async def async_select_option(self, option: str) -> None:
        """Select a manifest-backed firmware file."""

        await self.coordinator.async_set_selected_firmware_file(
            self._filename_from_option(option)
        )

    def _option_label(self, filename: str) -> str:
        """Return the UI label for a firmware file."""

        prefix = "[cached]" if self.coordinator.is_firmware_cached(filename) else "[download]"
        return f"{prefix} {filename}"

    def _filename_from_option(self, option: str) -> str:
        """Map a UI label back to the underlying firmware filename."""

        option = option.strip()
        for filename in self.coordinator.data.available_firmware_files:
            if option == filename or option == self._option_label(filename):
                return filename
        return option
