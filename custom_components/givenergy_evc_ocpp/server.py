"""Embedded websocket listener for inbound OCPP traffic."""

from __future__ import annotations

import logging

from aiohttp import web
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .charge_point import GivEnergyChargePointSession
from .hub import GivEnergyChargePointHub
from .const import DEFAULT_LISTEN_HOST, WEBSOCKET_SUBPROTOCOL

_LOGGER = logging.getLogger(__name__)

class GivEnergyOcppServer:
    """Manage the dedicated inbound OCPP websocket listener."""

    def __init__(
        self,
        hass: HomeAssistant,
        hub: GivEnergyChargePointHub,
    ) -> None:
        """Initialise the listener."""

        self.hass = hass
        self.hub = hub
        self._app = web.Application()
        self._app.router.add_get("/", self._async_handle_websocket)
        self._app.router.add_get("/{charge_point_id:.*}", self._async_handle_websocket)
        self._runner: web.AppRunner | None = None
        self._site: web.BaseSite | None = None
        self._sessions: dict[str, GivEnergyChargePointSession] = {}

    async def async_start(self) -> None:
        """Start listening for websocket connections."""

        self._runner = web.AppRunner(self._app, access_log=None)
        await self._runner.setup()
        self._site = web.TCPSite(
            self._runner,
            host=DEFAULT_LISTEN_HOST,
            port=self.hub.primary_coordinator.listen_port,
        )
        await self._site.start()
        _LOGGER.info(
            "Listening for GivEnergy EVC OCPP connections on %s:%s",
            DEFAULT_LISTEN_HOST,
            self.hub.primary_coordinator.listen_port,
        )

    async def async_stop(self) -> None:
        """Stop the websocket listener."""

        for session in list(self._sessions.values()):
            await session.async_close()
        self._sessions.clear()

        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            self._site = None

    async def async_send_call(
        self, charge_point_id: str | None, action: str, payload: dict, timeout: int
    ) -> dict[str, object]:
        """Send an outbound OCPP call through the active session."""

        if charge_point_id:
            session = self._sessions.get(charge_point_id)
            if session is None:
                raise HomeAssistantError(
                    f"GivEnergy charger {charge_point_id} is not currently connected"
                )
            return await session.async_call(action, payload, timeout=timeout)

        if len(self._sessions) == 1:
            return await next(iter(self._sessions.values())).async_call(
                action, payload, timeout=timeout
            )

        raise HomeAssistantError("No target charger was specified for the OCPP command")

    async def _async_handle_websocket(self, request: web.Request) -> web.StreamResponse:
        """Accept a websocket request from the charger."""

        candidate_id = request.match_info.get("charge_point_id", "").strip("/") or None
        local_host = None
        if request.transport is not None:
            sockname = request.transport.get_extra_info("sockname")
            if isinstance(sockname, tuple) and sockname:
                local_host = str(sockname[0])

        remote_host = request.remote or None

        coordinator = self.hub.coordinator_for_connection(candidate_id)

        if candidate_id:
            existing_session = self._sessions.get(candidate_id)
            if existing_session is not None and not existing_session.websocket.closed:
                return web.Response(status=409, text="Charge point already connected")

        websocket = web.WebSocketResponse(protocols=(WEBSOCKET_SUBPROTOCOL,))
        await websocket.prepare(request)

        if websocket.ws_protocol != WEBSOCKET_SUBPROTOCOL:
            _LOGGER.warning(
                "Charger connected without negotiating %s; continuing anyway",
                WEBSOCKET_SUBPROTOCOL,
            )

        session = GivEnergyChargePointSession(
            self.hass, websocket, coordinator, candidate_id
        )
        session_key = candidate_id or f"pending:{id(session)}"
        self._sessions[session_key] = session
        await coordinator.async_connection_opened(candidate_id, local_host, remote_host)
        await self.hub.async_note_discovered_charge_point(coordinator)

        try:
            await session.run()
        finally:
            self._sessions.pop(session_key, None)
            await coordinator.async_connection_closed()

        return websocket
