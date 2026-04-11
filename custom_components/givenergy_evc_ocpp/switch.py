"""Switches for GivEnergy EVC OCPP."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import GivEnergyEvcCoordinator
from .entity import GivEnergyEvcEntity


@dataclass(frozen=True, kw_only=True)
class GivEnergySwitchDescription(SwitchEntityDescription):
    """Description of a GivEnergy switch."""

    is_on_fn: Callable[[GivEnergyEvcCoordinator], bool | None]
    turn_on_fn: Callable[[GivEnergyEvcCoordinator], Awaitable[dict[str, Any]]]
    turn_off_fn: Callable[[GivEnergyEvcCoordinator], Awaitable[dict[str, Any]]]


SWITCHES: tuple[GivEnergySwitchDescription, ...] = (
    GivEnergySwitchDescription(
        key="charge_now",
        translation_key="charge_now",
        icon="mdi:ev-station",
        entity_category=EntityCategory.CONFIG,
        is_on_fn=lambda coordinator: coordinator.data.transaction_active
        or coordinator.data.status in {"Charging", "SuspendedEV", "SuspendedEVSE"},
        turn_on_fn=lambda coordinator: coordinator.async_start_charging(),
        turn_off_fn=lambda coordinator: coordinator.async_stop_charging(),
    ),
    GivEnergySwitchDescription(
        key="charger_enabled",
        translation_key="charger_enabled",
        icon="mdi:ev-plug-type2",
        entity_category=EntityCategory.CONFIG,
        is_on_fn=lambda coordinator: coordinator.data.charger_enabled,
        turn_on_fn=lambda coordinator: coordinator.async_change_availability(True),
        turn_off_fn=lambda coordinator: coordinator.async_change_availability(False),
    ),
    GivEnergySwitchDescription(
        key="local_modbus",
        translation_key="local_modbus",
        icon="mdi:lan",
        entity_category=EntityCategory.CONFIG,
        is_on_fn=lambda coordinator: coordinator.data.local_modbus_enabled,
        turn_on_fn=lambda coordinator: coordinator.async_set_local_modbus_enabled(True),
        turn_off_fn=lambda coordinator: coordinator.async_set_local_modbus_enabled(False),
    ),
    GivEnergySwitchDescription(
        key="front_panel_leds",
        translation_key="front_panel_leds",
        icon="mdi:led-on",
        entity_category=EntityCategory.CONFIG,
        is_on_fn=lambda coordinator: coordinator.data.front_panel_leds_enabled,
        turn_on_fn=lambda coordinator: coordinator.async_set_front_panel_leds_enabled(True),
        turn_off_fn=lambda coordinator: coordinator.async_set_front_panel_leds_enabled(False),
    ),
    GivEnergySwitchDescription(
        key="plug_and_go",
        translation_key="plug_and_go",
        icon="mdi:car-electric",
        entity_category=EntityCategory.CONFIG,
        is_on_fn=lambda coordinator: coordinator.data.plug_and_go_enabled,
        turn_on_fn=lambda coordinator: coordinator.async_set_plug_and_go_enabled(True),
        turn_off_fn=lambda coordinator: coordinator.async_set_plug_and_go_enabled(False),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up switches."""

    runtime = hass.data[DOMAIN][entry.entry_id]
    coordinator: GivEnergyEvcCoordinator = runtime.coordinator
    async_add_entities(
        [
            *(GivEnergyEvcSwitch(coordinator, description) for description in SWITCHES),
            GivEnergyFirmwareFtpSwitch(coordinator),
        ]
    )


class GivEnergyEvcSwitch(GivEnergyEvcEntity, SwitchEntity):
    """Switch backed by OCPP configuration or commands."""

    entity_description: GivEnergySwitchDescription

    def __init__(
        self,
        coordinator: GivEnergyEvcCoordinator,
        description: GivEnergySwitchDescription,
    ) -> None:
        """Initialise the switch."""

        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Switches are only useful while the charger is connected."""

        return super().available and self.coordinator.data.connected

    @property
    def is_on(self) -> bool | None:
        """Return the current switch state."""

        return self.entity_description.is_on_fn(self.coordinator)

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the switch on."""

        del kwargs
        await self.entity_description.turn_on_fn(self.coordinator)

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the switch off."""

        del kwargs
        await self.entity_description.turn_off_fn(self.coordinator)


class GivEnergyFirmwareFtpSwitch(GivEnergyEvcEntity, SwitchEntity):
    """Switch controlling the local firmware FTP server."""

    _attr_translation_key = "firmware_ftp_server"
    _attr_icon = "mdi:folder-network-outline"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: GivEnergyEvcCoordinator) -> None:
        """Initialise the switch."""

        super().__init__(coordinator, "firmware_ftp_server")

    @property
    def available(self) -> bool:
        """The FTP server can be managed whenever the integration is loaded."""

        return True

    @property
    def is_on(self) -> bool | None:
        """Return whether the FTP server is running."""

        return self.coordinator.data.firmware_ftp_running

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return firmware FTP diagnostics."""

        return {
            "ftp_host": self.coordinator.data.firmware_ftp_host,
            "ftp_port": self.coordinator.firmware_ftp_port,
            "ftp_passive_ports": (
                f"{self.coordinator.firmware_ftp_passive_ports.start}-"
                f"{self.coordinator.firmware_ftp_passive_ports.stop - 1}"
            ),
            "firmware_directory": str(self.coordinator.firmware_directory),
            "selected_file": self.coordinator.data.selected_firmware_file,
            "available_files": self.coordinator.data.available_firmware_files,
            "last_error": self.coordinator.data.firmware_ftp_error,
            "last_transfer": self.coordinator.data.firmware_ftp_last_transfer,
            "recent_events": self.coordinator.data.firmware_ftp_events[-10:],
        }

    async def async_turn_on(self, **kwargs) -> None:
        """Start the firmware FTP server."""

        del kwargs
        await self.coordinator.async_set_firmware_ftp_enabled(True)

    async def async_turn_off(self, **kwargs) -> None:
        """Stop the firmware FTP server."""

        del kwargs
        await self.coordinator.async_set_firmware_ftp_enabled(False)
