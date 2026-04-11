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
    CONF_FIRMWARE_FTP_PORT,
    CONF_FIRMWARE_FTP_PASSIVE_PORT_END,
    CONF_FIRMWARE_FTP_PASSIVE_PORT_START,
    CONF_LISTEN_PORT,
    CONF_METER_VALUE_SAMPLE_INTERVAL,
    DEFAULT_ADOPT_FIRST_CHARGER,
    DEFAULT_COMMAND_TIMEOUT,
    DEFAULT_DEBUG_LOGGING,
    DEFAULT_ENHANCED_LOGGING,
    DEFAULT_FIRMWARE_FTP_PORT,
    DEFAULT_FIRMWARE_FTP_PASSIVE_PORT_END,
    DEFAULT_FIRMWARE_FTP_PASSIVE_PORT_START,
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


def _validate_ftp_port_ranges(
    listen_port: int,
    ftp_port: int,
    passive_start: int,
    passive_end: int,
) -> str | None:
    """Return a config-flow error key when the FTP port setup is invalid."""

    if listen_port == ftp_port:
        return "ports_must_differ"
    if passive_start > passive_end:
        return "ftp_passive_range_invalid"
    reserved = {listen_port, ftp_port}
    passive_ports = set(range(passive_start, passive_end + 1))
    if reserved & passive_ports:
        return "ftp_passive_range_conflict"
    return None


def _build_user_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    """Build the initial config-flow schema."""

    return vol.Schema(
        {
            vol.Required(
                CONF_LISTEN_PORT,
                default=defaults.get(CONF_LISTEN_PORT, DEFAULT_LISTEN_PORT),
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            vol.Required(
                CONF_FIRMWARE_FTP_PORT,
                default=defaults.get(
                    CONF_FIRMWARE_FTP_PORT, DEFAULT_FIRMWARE_FTP_PORT
                ),
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            vol.Required(
                CONF_FIRMWARE_FTP_PASSIVE_PORT_START,
                default=defaults.get(
                    CONF_FIRMWARE_FTP_PASSIVE_PORT_START,
                    DEFAULT_FIRMWARE_FTP_PASSIVE_PORT_START,
                ),
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            vol.Required(
                CONF_FIRMWARE_FTP_PASSIVE_PORT_END,
                default=defaults.get(
                    CONF_FIRMWARE_FTP_PASSIVE_PORT_END,
                    DEFAULT_FIRMWARE_FTP_PASSIVE_PORT_END,
                ),
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
            vol.Required(
                CONF_FIRMWARE_FTP_PORT,
                default=defaults.get(
                    CONF_FIRMWARE_FTP_PORT, DEFAULT_FIRMWARE_FTP_PORT
                ),
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            vol.Required(
                CONF_FIRMWARE_FTP_PASSIVE_PORT_START,
                default=defaults.get(
                    CONF_FIRMWARE_FTP_PASSIVE_PORT_START,
                    DEFAULT_FIRMWARE_FTP_PASSIVE_PORT_START,
                ),
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            vol.Required(
                CONF_FIRMWARE_FTP_PASSIVE_PORT_END,
                default=defaults.get(
                    CONF_FIRMWARE_FTP_PASSIVE_PORT_END,
                    DEFAULT_FIRMWARE_FTP_PASSIVE_PORT_END,
                ),
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

            validation_error = _validate_ftp_port_ranges(
                user_input[CONF_LISTEN_PORT],
                user_input[CONF_FIRMWARE_FTP_PORT],
                user_input[CONF_FIRMWARE_FTP_PASSIVE_PORT_START],
                user_input[CONF_FIRMWARE_FTP_PASSIVE_PORT_END],
            )
            if validation_error:
                errors["base"] = validation_error
            elif not await _async_can_listen_on_port(user_input[CONF_LISTEN_PORT]):
                errors["base"] = "port_in_use"
            elif not await _async_can_listen_on_port(user_input[CONF_FIRMWARE_FTP_PORT]):
                errors["base"] = "ftp_port_in_use"
            else:
                for passive_port in range(
                    user_input[CONF_FIRMWARE_FTP_PASSIVE_PORT_START],
                    user_input[CONF_FIRMWARE_FTP_PASSIVE_PORT_END] + 1,
                ):
                    if not await _async_can_listen_on_port(passive_port):
                        errors["base"] = "ftp_passive_port_in_use"
                        break

            if not errors:
                data = {
                    CONF_LISTEN_PORT: user_input[CONF_LISTEN_PORT],
                    CONF_FIRMWARE_FTP_PORT: user_input[CONF_FIRMWARE_FTP_PORT],
                    CONF_FIRMWARE_FTP_PASSIVE_PORT_START: user_input[
                        CONF_FIRMWARE_FTP_PASSIVE_PORT_START
                    ],
                    CONF_FIRMWARE_FTP_PASSIVE_PORT_END: user_input[
                        CONF_FIRMWARE_FTP_PASSIVE_PORT_END
                    ],
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
            current_ftp_port = defaults.get(
                CONF_FIRMWARE_FTP_PORT, DEFAULT_FIRMWARE_FTP_PORT
            )
            current_passive_start = defaults.get(
                CONF_FIRMWARE_FTP_PASSIVE_PORT_START,
                DEFAULT_FIRMWARE_FTP_PASSIVE_PORT_START,
            )
            current_passive_end = defaults.get(
                CONF_FIRMWARE_FTP_PASSIVE_PORT_END,
                DEFAULT_FIRMWARE_FTP_PASSIVE_PORT_END,
            )
            requested_port = user_input[CONF_LISTEN_PORT]
            requested_ftp_port = user_input[CONF_FIRMWARE_FTP_PORT]
            requested_passive_start = user_input[CONF_FIRMWARE_FTP_PASSIVE_PORT_START]
            requested_passive_end = user_input[CONF_FIRMWARE_FTP_PASSIVE_PORT_END]

            validation_error = _validate_ftp_port_ranges(
                requested_port,
                requested_ftp_port,
                requested_passive_start,
                requested_passive_end,
            )
            if validation_error:
                errors["base"] = validation_error
            elif requested_port != current_port and not await _async_can_listen_on_port(
                requested_port
            ):
                errors["base"] = "port_in_use"
            elif (
                requested_ftp_port != current_ftp_port
                and not await _async_can_listen_on_port(requested_ftp_port)
            ):
                errors["base"] = "ftp_port_in_use"
            else:
                passive_ports_changed = (
                    requested_passive_start != current_passive_start
                    or requested_passive_end != current_passive_end
                )
                if passive_ports_changed:
                    for passive_port in range(
                        requested_passive_start, requested_passive_end + 1
                    ):
                        if not await _async_can_listen_on_port(passive_port):
                            errors["base"] = "ftp_passive_port_in_use"
                            break

            if not errors:
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_LISTEN_PORT: requested_port,
                        CONF_FIRMWARE_FTP_PORT: requested_ftp_port,
                        CONF_FIRMWARE_FTP_PASSIVE_PORT_START: requested_passive_start,
                        CONF_FIRMWARE_FTP_PASSIVE_PORT_END: requested_passive_end,
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
