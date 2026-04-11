"""Buttons for GivEnergy EVC OCPP."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import GivEnergyEvcCoordinator
from .entity import GivEnergyEvcEntity


@dataclass(frozen=True, kw_only=True)
class GivEnergyButtonDescription(ButtonEntityDescription):
    """Description of a GivEnergy button."""

    press_fn: Callable[[GivEnergyEvcCoordinator], Awaitable[dict]]


BUTTONS: tuple[GivEnergyButtonDescription, ...] = (
    GivEnergyButtonDescription(
        key="reset_soft",
        translation_key="reset_soft",
        icon="mdi:restart",
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda coordinator: coordinator.async_reset("Soft"),
    ),
    GivEnergyButtonDescription(
        key="reset_hard",
        translation_key="reset_hard",
        icon="mdi:restart-alert",
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda coordinator: coordinator.async_reset("Hard"),
    ),
    GivEnergyButtonDescription(
        key="unlock_connector",
        translation_key="unlock_connector",
        icon="mdi:lock-open-variant-outline",
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda coordinator: coordinator.async_unlock_connector(),
    ),
    GivEnergyButtonDescription(
        key="trigger_meter_values",
        translation_key="trigger_meter_values",
        icon="mdi:gauge",
        entity_category=EntityCategory.DIAGNOSTIC,
        press_fn=lambda coordinator: coordinator.async_trigger_message("MeterValues"),
    ),
    GivEnergyButtonDescription(
        key="refresh_configuration",
        translation_key="refresh_configuration",
        icon="mdi:cog-refresh",
        entity_category=EntityCategory.DIAGNOSTIC,
        press_fn=lambda coordinator: coordinator.async_refresh_configuration(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up button entities."""

    runtime = hass.data[DOMAIN][entry.entry_id]
    coordinator: GivEnergyEvcCoordinator = runtime.coordinator
    async_add_entities(GivEnergyEvcButton(coordinator, description) for description in BUTTONS)


class GivEnergyEvcButton(GivEnergyEvcEntity, ButtonEntity):
    """GivEnergy OCPP button."""

    entity_description: GivEnergyButtonDescription

    def __init__(
        self,
        coordinator: GivEnergyEvcCoordinator,
        description: GivEnergyButtonDescription,
    ) -> None:
        """Initialise the button."""

        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Buttons should only be available while connected."""

        return super().available and self.coordinator.data.connected

    async def async_press(self) -> None:
        """Handle button press."""

        await self.entity_description.press_fn(self.coordinator)
