"""Buttons for GivEnergy EVC OCPP."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import GivEnergyEvcCoordinator
from .entity import GivEnergyEvcEntity, GivEnergyPendingChargePointEntity
from .hub import SIGNAL_ACCEPTED_CHARGE_POINT, SIGNAL_PENDING_CHARGE_POINT


@dataclass(frozen=True, kw_only=True)
class GivEnergyButtonDescription(ButtonEntityDescription):
    """Description of a GivEnergy button."""

    press_fn: Callable[[GivEnergyEvcCoordinator], Awaitable[dict]]


BUTTONS: tuple[GivEnergyButtonDescription, ...] = (
    # --- Config buttons ---
    GivEnergyButtonDescription(
        key="unlock_connector",
        translation_key="unlock_connector",
        icon="mdi:lock-open-variant-outline",
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda coordinator: coordinator.async_unlock_connector(),
    ),
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
        key="factory_reset",
        translation_key="factory_reset",
        icon="mdi:alert-octagram-outline",
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda coordinator: coordinator.async_factory_reset(),
    ),
    GivEnergyButtonDescription(
        key="read_cp_voltage_and_duty_cycle",
        translation_key="read_cp_voltage_and_duty_cycle",
        icon="mdi:sine-wave",
        entity_category=EntityCategory.DIAGNOSTIC,
        press_fn=lambda coordinator: coordinator.async_read_cp_voltage_and_duty_cycle(),
    ),
    # --- Diagnostic buttons ---
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
    hub = runtime.hub

    def _accepted_entities(target: GivEnergyEvcCoordinator) -> list[ButtonEntity]:
        return [
            *(GivEnergyEvcButton(target, description) for description in BUTTONS),
        ]

    async_add_entities(
        [
            *(_accepted_entities(coordinator)),
            GivEnergyInstallSelectedFirmwareButton(coordinator),
            GivEnergyAcceptDiscoveredChargerButton(coordinator, is_primary=True),
        ]
    )

    for accepted in hub.accepted_secondary_coordinators():
        async_add_entities(_accepted_entities(accepted))

    for pending in hub.pending_secondary_coordinators():
        async_add_entities([GivEnergyAcceptDiscoveredChargerButton(pending)])

    entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            SIGNAL_ACCEPTED_CHARGE_POINT,
            lambda entry_id, target: (
                entry_id == entry.entry_id
                and async_add_entities(_accepted_entities(target))
            ),
        )
    )
    entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            SIGNAL_PENDING_CHARGE_POINT,
            lambda entry_id, target: (
                entry_id == entry.entry_id
                and async_add_entities([GivEnergyAcceptDiscoveredChargerButton(target)])
            ),
        )
    )


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


class GivEnergyInstallSelectedFirmwareButton(GivEnergyEvcEntity, ButtonEntity):
    """Button that sends UpdateFirmware for the selected local firmware file."""

    _attr_translation_key = "install_selected_firmware"
    _attr_icon = "mdi:download-circle-outline"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: GivEnergyEvcCoordinator) -> None:
        """Initialise the button."""

        super().__init__(coordinator, "install_selected_firmware")

    @property
    def available(self) -> bool:
        """Only expose installation when the charger and firmware server are ready."""

        return (
            super().available
            and self.coordinator.data.connected
            and self.coordinator.data.firmware_server_running
            and self.coordinator.data.selected_firmware_file is not None
            and self.coordinator.data.firmware_server_host is not None
            and not self.coordinator.firmware_update_in_progress
        )

    @property
    def extra_state_attributes(self) -> dict[str, str | int | None]:
        """Return the generated firmware URL ingredients."""

        return {
            "selected_file": self.coordinator.data.selected_firmware_file,
            "server_host": self.coordinator.data.firmware_server_host,
            "server_port": self.coordinator.firmware_server_port,
        }

    async def async_press(self) -> None:
        """Trigger a firmware update from the selected bundled file."""

        await self.coordinator.async_install_selected_firmware()


class GivEnergyAcceptDiscoveredChargerButton(
    GivEnergyPendingChargePointEntity, ButtonEntity
):
    """Button that accepts a newly discovered additional charger."""

    _attr_translation_key = "accept_discovered_charger"
    _attr_icon = "mdi:plus-circle-outline"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: GivEnergyEvcCoordinator,
        *,
        is_primary: bool = False,
    ) -> None:
        """Initialise the accept button."""

        key = "accept_discovered_charger" if is_primary else "accept_discovered_charger_pending"
        super().__init__(coordinator, key)
        self._is_primary = is_primary

    @property
    def available(self) -> bool:
        """Only expose acceptance while the charger is still pending."""

        charge_point_id = (
            self.coordinator.data.charge_point_id or self.coordinator.data.path_charge_point_id
        )
        return bool(charge_point_id and not self.coordinator.data.adopted)

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        """Return discovery metadata."""

        return {
            "charge_point_id": self.coordinator.data.charge_point_id
            or self.coordinator.data.path_charge_point_id,
            "serial_number": self.coordinator.data.charge_point_serial_number
            or self.coordinator.data.charge_box_serial_number,
        }

    async def async_press(self) -> None:
        """Accept the discovered charger into the full entity set."""

        runtime = self.hass.data[DOMAIN][self.coordinator.entry.entry_id]
        charge_point_id = (
            self.coordinator.data.charge_point_id or self.coordinator.data.path_charge_point_id
        )
        if charge_point_id is None:
            return
        await runtime.hub.async_accept_charge_point(charge_point_id)
