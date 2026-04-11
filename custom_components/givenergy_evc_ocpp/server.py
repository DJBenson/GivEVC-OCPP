"""Embedded websocket listener for inbound OCPP traffic."""

from __future__ import annotations

import logging
from pathlib import Path

from aiohttp import web
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .charge_point import GivEnergyChargePointSession
from .coordinator import GivEnergyEvcCoordinator
from .const import DEFAULT_LISTEN_HOST, WEBSOCKET_SUBPROTOCOL

_LOGGER = logging.getLogger(__name__)

_FIRMWARE_PREFIX = "/firmware/"


class GivEnergyOcppServer:
    """Manage the dedicated inbound OCPP websocket listener."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: GivEnergyEvcCoordinator,
    ) -> None:
        """Initialise the listener."""

        self.hass = hass
        self.coordinator = coordinator
        self._app = web.Application()
        self._app.router.add_get(
            _FIRMWARE_PREFIX + "{filename}", self._async_handle_firmware_download
        )
        self._app.router.add_get("/", self._async_handle_websocket)
        self._app.router.add_get("/{charge_point_id:.*}", self._async_handle_websocket)
        self._runner: web.AppRunner | None = None
        self._site: web.BaseSite | None = None
        self._session: GivEnergyChargePointSession | None = None

    async def async_start(self) -> None:
        """Start listening for websocket connections."""

        self._runner = web.AppRunner(self._app, access_log=None)
        await self._runner.setup()
        self._site = web.TCPSite(
            self._runner,
            host=DEFAULT_LISTEN_HOST,
            port=self.coordinator.listen_port,
        )
        await self._site.start()
        _LOGGER.info(
            "Listening for GivEnergy EVC OCPP connections on %s:%s",
            DEFAULT_LISTEN_HOST,
            self.coordinator.listen_port,
        )

    async def async_stop(self) -> None:
        """Stop the websocket listener."""

        if self._session is not None:
            await self._session.async_close()
            self._session = None

        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            self._site = None

    def firmware_http_base_url(self, host: str) -> str:
        """Return the base HTTP URL for firmware downloads via this server."""

        return f"http://{host}:{self.coordinator.listen_port}{_FIRMWARE_PREFIX}"

    async def async_send_call(
        self, action: str, payload: dict, timeout: int
    ) -> dict[str, object]:
        """Send an outbound OCPP call through the active session."""

        if self._session is None:
            raise HomeAssistantError("No GivEnergy charger is currently connected")
        return await self._session.async_call(action, payload, timeout=timeout)

    async def _async_handle_firmware_download(
        self, request: web.Request
    ) -> web.StreamResponse:
        """Serve a firmware file by name from the integration firmware directory."""

        filename = request.match_info["filename"]
        # Reject any path traversal attempts
        if "/" in filename or "\\" in filename or filename.startswith("."):
            return web.Response(status=400, text="Invalid filename")

        firmware_dir: Path = self.coordinator.firmware_directory
        file_path = firmware_dir / filename

        if not file_path.exists() or not file_path.is_file():
            _LOGGER.warning("Firmware HTTP request for unknown file: %s", filename)
            return web.Response(status=404, text="Firmware file not found")

        _LOGGER.info(
            "Serving firmware file %s (%d bytes) to %s",
            filename,
            file_path.stat().st_size,
            request.remote,
        )
        return web.FileResponse(file_path)

    async def _async_handle_websocket(self, request: web.Request) -> web.StreamResponse:
        """Accept a websocket request from the charger."""

        candidate_id = request.match_info.get("charge_point_id", "").strip("/") or None
        local_host = None
        if request.transport is not None:
            sockname = request.transport.get_extra_info("sockname")
            if isinstance(sockname, tuple) and sockname:
                local_host = str(sockname[0])

        if not self.coordinator.can_accept_charge_point(candidate_id):
            await self.coordinator.async_note_rejected_charge_point(candidate_id)
            _LOGGER.warning("Rejected unexpected charger connection for %s", candidate_id)
            return web.Response(status=403, text="Unexpected charge point ID")

        if self._session is not None and not self._session.websocket.closed:
            if candidate_id and candidate_id != self.coordinator.data.charge_point_id:
                return web.Response(status=409, text="A different charger is active")
            await self._session.async_close("Replacing existing OCPP session")

        websocket = web.WebSocketResponse(protocols=(WEBSOCKET_SUBPROTOCOL,))
        await websocket.prepare(request)

        if websocket.ws_protocol != WEBSOCKET_SUBPROTOCOL:
            _LOGGER.warning(
                "Charger connected without negotiating %s; continuing anyway",
                WEBSOCKET_SUBPROTOCOL,
            )

        session = GivEnergyChargePointSession(
            self.hass, websocket, self.coordinator, candidate_id
        )
        self._session = session
        await self.coordinator.async_connection_opened(candidate_id, local_host)

        try:
            await session.run()
        finally:
            if self._session is session:
                self._session = None
                await self.coordinator.async_connection_closed()

        return websocket
