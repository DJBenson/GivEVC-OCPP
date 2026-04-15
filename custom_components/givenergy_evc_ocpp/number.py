"""Number entities for GivEnergy EVC OCPP."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfElectricCurrent, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DEFAULT_EVSE_MAX_CURRENT, DEFAULT_EVSE_MIN_CURRENT, DOMAIN
from .coordinator import GivEnergyEvcCoordinator
from .entity import GivEnergyEvcEntity
from .hub import SIGNAL_ACCEPTED_CHARGE_POINT


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up number entities."""

    runtime = hass.data[DOMAIN][entry.entry_id]
    coordinator: GivEnergyEvcCoordinator = runtime.coordinator
    def _entities(target: GivEnergyEvcCoordinator) -> list[NumberEntity]:
        return [
            GivEnergyCurrentLimitNumber(target),
            GivEnergyRandomisedDelayNumber(target),
            GivEnergyMaxImportCapacityNumber(target),
            GivEnergySuspendedStateTimeoutNumber(target),
        ]

    async_add_entities(_entities(coordinator))
    for accepted in runtime.hub.accepted_secondary_coordinators():
        async_add_entities(_entities(accepted))

    def _on_accepted(signal_entry_id: str, target: GivEnergyEvcCoordinator) -> None:
        if signal_entry_id == entry.entry_id:
            hass.async_add_job(async_add_entities, _entities(target))

    entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            SIGNAL_ACCEPTED_CHARGE_POINT,
            _on_accepted,
        )
    )


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


class GivEnergyRandomisedDelayNumber(GivEnergyEvcEntity, NumberEntity):
    """Slider for the charger's randomised delay duration."""

    _attr_translation_key = "randomised_delay_duration"
    _attr_native_min_value = 600
    _attr_native_max_value = 1800
    _attr_native_step = 60
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_mode = NumberMode.SLIDER
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: GivEnergyEvcCoordinator) -> None:
        """Initialise the number."""

        super().__init__(coordinator, "randomised_delay_duration")

    @property
    def available(self) -> bool:
        """The number requires a connected charger with configuration loaded."""

        return (
            super().available
            and self.coordinator.data.connected
            and self.coordinator.data.randomised_delay_duration_seconds is not None
        )

    @property
    def native_value(self) -> float | None:
        """Return the current randomised delay duration."""

        return self.coordinator.data.randomised_delay_duration_seconds

    async def async_set_native_value(self, value: float) -> None:
        """Set the randomised delay duration."""

        await self.coordinator.async_set_randomised_delay_duration(int(value))


class GivEnergyMaxImportCapacityNumber(GivEnergyEvcEntity, NumberEntity):
    """Slider for the maximum grid import capacity of the installation."""

    _attr_translation_key = "max_import_capacity"
    _attr_native_min_value = 40
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_mode = NumberMode.SLIDER
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: GivEnergyEvcCoordinator) -> None:
        """Initialise the number."""

        super().__init__(coordinator, "max_import_capacity")

    @property
    def available(self) -> bool:
        """Only available when connected and the charger has reported Imax."""

        return (
            super().available
            and self.coordinator.data.connected
            and self.coordinator.data.max_import_capacity_a is not None
        )

    @property
    def native_value(self) -> float | None:
        """Return the current max import capacity setting, clamped to slider bounds."""

        value = self.coordinator.data.max_import_capacity_a
        if value is None:
            return None
        return float(max(self._attr_native_min_value, min(self._attr_native_max_value, value)))

    async def async_set_native_value(self, value: float) -> None:
        """Set the max import capacity."""

        await self.coordinator.async_set_max_import_capacity(int(value))


class GivEnergySuspendedStateTimeoutNumber(GivEnergyEvcEntity, NumberEntity):
    """Slider for the suspended-state wait timeout (SuspevTime), in minutes."""

    _attr_translation_key = "suspended_state_timeout"
    _attr_native_min_value = 0
    _attr_native_max_value = 720
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_mode = NumberMode.SLIDER
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: GivEnergyEvcCoordinator) -> None:
        """Initialise the number."""

        super().__init__(coordinator, "suspended_state_timeout")

    @property
    def available(self) -> bool:
        """Only available when connected on firmware >= 1.14."""

        return (
            super().available
            and self.coordinator.data.connected
            and GivEnergyEvcCoordinator._firmware_version_at_least(
                self.coordinator.data.firmware_version, 1, 14
            )
        )

    @property
    def native_value(self) -> float:
        """Return the timeout in minutes (0 when unset)."""

        seconds = self.coordinator.data.suspended_state_timeout_seconds
        return (seconds // 60) if seconds is not None else 0

    async def async_set_native_value(self, value: float) -> None:
        """Set the suspended-state timeout, converting minutes to seconds."""

        await self.coordinator.async_set_suspended_state_timeout(int(value) * 60)
