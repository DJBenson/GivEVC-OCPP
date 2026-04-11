"""Config flow for GivEnergy EVC OCPP."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_ADOPT_FIRST_CHARGER,
    CONF_COMMAND_TIMEOUT,
    CONF_DEBUG_LOGGING,
    CONF_ENHANCED_LOGGING,
    CONF_EXPECTED_CHARGE_POINT_ID,
    CONF_LISTEN_PORT,
    CONF_METER_VALUE_SAMPLE_INTERVAL,
    DEFAULT_ADOPT_FIRST_CHARGER,
    DEFAULT_COMMAND_TIMEOUT,
    DEFAULT_DEBUG_LOGGING,
    DEFAULT_ENHANCED_LOGGING,
    DEFAULT_LISTEN_HOST,
    DEFAULT_LISTEN_PORT,
    DEFAULT_METER_VALUE_SAMPLE_INTERVAL,
    DOMAIN,
    TITLE,
)


async def _async_can_listen_on_port(port: int) -> bool:
    """Return True if the configured port can be bound."""

    try:
        server = await asyncio.start_server(
            lambda _reader, _writer: None,
            host=DEFAULT_LISTEN_HOST,
            port=port,
            start_serving=False,
        )
    except OSError:
        return False

    server.close()
    await server.wait_closed()
    return True


def _build_user_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    """Build the initial config-flow schema."""

    return vol.Schema(
        {
            vol.Required(
                CONF_LISTEN_PORT,
                default=defaults.get(CONF_LISTEN_PORT, DEFAULT_LISTEN_PORT),
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            vol.Optional(
                CONF_EXPECTED_CHARGE_POINT_ID,
                default=defaults.get(CONF_EXPECTED_CHARGE_POINT_ID, ""),
            ): str,
            vol.Required(
                CONF_ADOPT_FIRST_CHARGER,
                default=defaults.get(
                    CONF_ADOPT_FIRST_CHARGER, DEFAULT_ADOPT_FIRST_CHARGER
                ),
            ): bool,
            vol.Required(
                CONF_METER_VALUE_SAMPLE_INTERVAL,
                default=defaults.get(
                    CONF_METER_VALUE_SAMPLE_INTERVAL,
                    DEFAULT_METER_VALUE_SAMPLE_INTERVAL,
                ),
            ): vol.All(vol.Coerce(int), vol.Range(min=5, max=3600)),
            vol.Required(
                CONF_ENHANCED_LOGGING,
                default=defaults.get(CONF_ENHANCED_LOGGING, DEFAULT_ENHANCED_LOGGING),
            ): bool,
        }
    )


def _build_options_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    """Build the options schema."""

    return vol.Schema(
        {
            vol.Required(
                CONF_LISTEN_PORT,
                default=defaults.get(CONF_LISTEN_PORT, DEFAULT_LISTEN_PORT),
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            vol.Optional(
                CONF_EXPECTED_CHARGE_POINT_ID,
                default=defaults.get(CONF_EXPECTED_CHARGE_POINT_ID, ""),
            ): str,
            vol.Required(
                CONF_ADOPT_FIRST_CHARGER,
                default=defaults.get(
                    CONF_ADOPT_FIRST_CHARGER, DEFAULT_ADOPT_FIRST_CHARGER
                ),
            ): bool,
            vol.Required(
                CONF_DEBUG_LOGGING,
                default=defaults.get(CONF_DEBUG_LOGGING, DEFAULT_DEBUG_LOGGING),
            ): bool,
            vol.Required(
                CONF_COMMAND_TIMEOUT,
                default=defaults.get(CONF_COMMAND_TIMEOUT, DEFAULT_COMMAND_TIMEOUT),
            ): vol.All(vol.Coerce(int), vol.Range(min=5, max=120)),
            vol.Required(
                CONF_METER_VALUE_SAMPLE_INTERVAL,
                default=defaults.get(
                    CONF_METER_VALUE_SAMPLE_INTERVAL,
                    DEFAULT_METER_VALUE_SAMPLE_INTERVAL,
                ),
            ): vol.All(vol.Coerce(int), vol.Range(min=5, max=3600)),
            vol.Required(
                CONF_ENHANCED_LOGGING,
                default=defaults.get(CONF_ENHANCED_LOGGING, DEFAULT_ENHANCED_LOGGING),
            ): bool,
        }
    )


class GivEnergyEvcOcppConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for GivEnergy EVC OCPP."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""

        errors: dict[str, str] = {}

        if user_input is not None:
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()

            if not await _async_can_listen_on_port(user_input[CONF_LISTEN_PORT]):
                errors["base"] = "port_in_use"
            else:
                data = {
                    CONF_LISTEN_PORT: user_input[CONF_LISTEN_PORT],
                    CONF_EXPECTED_CHARGE_POINT_ID: user_input[
                        CONF_EXPECTED_CHARGE_POINT_ID
                    ].strip(),
                    CONF_ADOPT_FIRST_CHARGER: user_input[CONF_ADOPT_FIRST_CHARGER],
                    CONF_METER_VALUE_SAMPLE_INTERVAL: user_input[
                        CONF_METER_VALUE_SAMPLE_INTERVAL
                    ],
                }
                options = {
                    CONF_DEBUG_LOGGING: DEFAULT_DEBUG_LOGGING,
                    CONF_COMMAND_TIMEOUT: DEFAULT_COMMAND_TIMEOUT,
                    CONF_ENHANCED_LOGGING: user_input[CONF_ENHANCED_LOGGING],
                }
                return self.async_create_entry(title=TITLE, data=data, options=options)

        return self.async_show_form(
            step_id="user",
            data_schema=_build_user_schema(user_input or {}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "GivEnergyEvcOcppOptionsFlow":
        """Return the options flow."""

        return GivEnergyEvcOcppOptionsFlow(config_entry)


class GivEnergyEvcOcppOptionsFlow(config_entries.OptionsFlow):
    """Options flow for GivEnergy EVC OCPP."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Store the config entry."""

        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage integration options."""

        errors: dict[str, str] = {}
        defaults = {**self._config_entry.data, **self._config_entry.options}

        if user_input is not None:
            current_port = defaults.get(CONF_LISTEN_PORT, DEFAULT_LISTEN_PORT)
            requested_port = user_input[CONF_LISTEN_PORT]

            if requested_port != current_port and not await _async_can_listen_on_port(
                requested_port
            ):
                errors["base"] = "port_in_use"
            else:
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_LISTEN_PORT: requested_port,
                        CONF_EXPECTED_CHARGE_POINT_ID: user_input[
                            CONF_EXPECTED_CHARGE_POINT_ID
                        ].strip(),
                        CONF_ADOPT_FIRST_CHARGER: user_input[
                            CONF_ADOPT_FIRST_CHARGER
                        ],
                        CONF_DEBUG_LOGGING: user_input[CONF_DEBUG_LOGGING],
                        CONF_COMMAND_TIMEOUT: user_input[CONF_COMMAND_TIMEOUT],
                        CONF_METER_VALUE_SAMPLE_INTERVAL: user_input[
                            CONF_METER_VALUE_SAMPLE_INTERVAL
                        ],
                        CONF_ENHANCED_LOGGING: user_input[CONF_ENHANCED_LOGGING],
                    },
                )

        return self.async_show_form(
            step_id="init",
            data_schema=_build_options_schema(defaults),
            errors=errors,
        )
