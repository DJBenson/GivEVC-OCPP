"""OCPP 1.6J charge point session handling."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
import json
import logging
from typing import Any
from uuid import uuid4

from aiohttp import WSMessage, WSMsgType, web
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .coordinator import GivEnergyEvcCoordinator

_LOGGER = logging.getLogger(__name__)

CALL = 2
CALL_RESULT = 3
CALL_ERROR = 4

Handler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class GivEnergyChargePointSession:
    """Representation of a single connected OCPP charge point."""

    def __init__(
        self,
        hass: HomeAssistant,
        websocket: web.WebSocketResponse,
        coordinator: GivEnergyEvcCoordinator,
        charge_point_id: str | None,
    ) -> None:
        """Initialise the charge point session."""

        self.hass = hass
        self.websocket = websocket
        self.coordinator = coordinator
        self.charge_point_id = charge_point_id
        self._send_lock = asyncio.Lock()
        self._pending_calls: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._handlers: dict[str, Handler] = {
            "Authorize": self._handle_authorize,
            "BootNotification": self._handle_boot_notification,
            "DiagnosticsStatusNotification": self._handle_diagnostics_status,
            "FirmwareStatusNotification": self._handle_firmware_status,
            "Heartbeat": self._handle_heartbeat,
            "MeterValues": self._handle_meter_values,
            "StartTransaction": self._handle_start_transaction,
            "StatusNotification": self._handle_status_notification,
            "StopTransaction": self._handle_stop_transaction,
        }

    async def run(self) -> None:
        """Consume websocket messages until the session ends."""

        async for message in self.websocket:
            if message.type == WSMsgType.TEXT:
                await self._handle_text_message(message)
                continue

            if message.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR}:
                break

        self._cancel_pending_calls()

    async def async_call(
        self, action: str, payload: dict[str, Any], timeout: int
    ) -> dict[str, Any]:
        """Send a central-system initiated OCPP call."""

        if self.websocket.closed:
            raise HomeAssistantError("The GivEnergy charger is not connected")

        unique_id = uuid4().hex
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending_calls[unique_id] = future

        await self._send_frame([CALL, unique_id, action, payload])
        try:
            result = await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError as err:
            raise HomeAssistantError(
                f"OCPP action {action} timed out after {timeout} seconds"
            ) from err
        finally:
            self._pending_calls.pop(unique_id, None)

        return result

    async def async_close(self, message: str = "Closing OCPP session") -> None:
        """Close the underlying websocket session."""

        self._cancel_pending_calls()
        if not self.websocket.closed:
            await self.websocket.close(message=message.encode())

    async def _handle_text_message(self, message: WSMessage) -> None:
        """Handle a single text frame."""

        try:
            frame = json.loads(message.data)
        except json.JSONDecodeError:
            self.coordinator.record_ocpp_frame(
                direction="inbound",
                frame_type="invalid_json",
                raw_frame=message.data,
                note="Failed to decode inbound websocket message as JSON",
            )
            await self._send_call_error(
                None,
                "FormationViolation",
                "Invalid JSON received from charger",
            )
            return

        self.coordinator.record_ocpp_frame(
            direction="inbound",
            frame_type=str(frame[0]) if isinstance(frame, list) and frame else "unknown",
            raw_frame=frame,
        )

        if not isinstance(frame, list) or not frame:
            await self._send_call_error(
                None,
                "FormationViolation",
                "OCPP payload must be a JSON array",
            )
            return

        message_type = frame[0]
        if message_type == CALL and len(frame) == 4:
            unique_id = frame[1]
            action = frame[2]
            payload = frame[3]
            await self._handle_inbound_call(unique_id, action, payload)
            return

        if message_type == CALL_RESULT and len(frame) == 3:
            unique_id = frame[1]
            payload = frame[2]
            future = self._pending_calls.get(unique_id)
            if future is not None and not future.done():
                future.set_result(payload)
            return

        if message_type == CALL_ERROR and len(frame) == 5:
            unique_id = frame[1]
            error_code = frame[2]
            error_description = frame[3]
            future = self._pending_calls.get(unique_id)
            if future is not None and not future.done():
                future.set_exception(
                    HomeAssistantError(
                        f"OCPP error {error_code}: {error_description}"
                    )
                )
            return

        await self._send_call_error(
            frame[1] if len(frame) > 1 else None,
            "FormationViolation",
            "Unsupported OCPP frame shape",
        )

    async def _handle_inbound_call(
        self, unique_id: str, action: str, payload: dict[str, Any]
    ) -> None:
        """Dispatch an inbound CALL message."""

        if self.coordinator.debug_logging:
            _LOGGER.debug("Inbound OCPP CALL %s: %s", action, payload)

        handler = self._handlers.get(action)
        if handler is None:
            self.coordinator.record_unsupported_ocpp_action(action, payload)
            await self._send_call_error(
                unique_id,
                "NotImplemented",
                f"Action {action} is not implemented by this integration",
            )
            return

        try:
            result = await handler(payload)
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception("Failed to process inbound OCPP action %s", action)
            await self._send_call_error(
                unique_id,
                "InternalError",
                str(err),
            )
            return

        await self._send_frame([CALL_RESULT, unique_id, result])

    async def _handle_boot_notification(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Handle BootNotification."""

        await self.coordinator.async_record_boot(self.charge_point_id, payload)
        return {
            "currentTime": _ocpp_now(),
            "interval": 60,
            "status": "Accepted",
        }

    async def _handle_heartbeat(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Handle Heartbeat."""

        del payload
        await self.coordinator.async_record_heartbeat()
        return {"currentTime": _ocpp_now()}

    async def _handle_status_notification(
        self, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Handle StatusNotification."""

        await self.coordinator.async_record_status(payload)
        return {}

    async def _handle_authorize(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Accept Authorize requests for v1."""

        if self.coordinator.debug_logging:
            _LOGGER.debug("Authorize payload accepted: %s", payload)
        response = {"idTagInfo": {"status": "Accepted"}}
        self.coordinator.record_authorize_exchange(payload, response)
        return response

    async def _handle_start_transaction(
        self, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Handle StartTransaction."""

        transaction_id = await self.coordinator.async_start_transaction_from_charger(
            payload
        )
        response = {
            "transactionId": transaction_id,
            "idTagInfo": {"status": "Accepted"},
        }
        self.coordinator.record_start_transaction_exchange(payload, response)
        return response

    async def _handle_stop_transaction(
        self, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Handle StopTransaction."""

        await self.coordinator.async_stop_transaction_from_charger(payload)
        response = {"idTagInfo": {"status": "Accepted"}}
        self.coordinator.record_stop_transaction_exchange(payload, response)
        return response

    async def _handle_meter_values(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Handle MeterValues."""

        # TODO: if GivEnergy exposes richer transaction/session counters in a
        # vendor-specific shape, parse them here before the generic flattening
        # path so we can expose cleaner HA entities without losing raw payloads.
        await self.coordinator.async_record_meter_values(payload)
        return {}

    async def _handle_firmware_status(
        self, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Handle FirmwareStatusNotification."""

        await self.coordinator.async_record_firmware_status(payload)
        return {}

    async def _handle_diagnostics_status(
        self, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Handle DiagnosticsStatusNotification."""

        await self.coordinator.async_record_diagnostics_status(payload)
        return {}

    async def _send_frame(self, frame: list[Any]) -> None:
        """Send a JSON array frame to the charger."""

        if self.coordinator.debug_logging:
            _LOGGER.debug("Outbound OCPP frame: %s", frame)

        self.coordinator.record_ocpp_frame(
            direction="outbound",
            frame_type=str(frame[0]) if frame else "unknown",
            action=frame[2] if len(frame) > 2 and isinstance(frame[2], str) else None,
            payload=frame[-1] if frame else None,
            raw_frame=frame,
        )

        async with self._send_lock:
            await self.websocket.send_str(json.dumps(frame))

    async def _send_call_error(
        self, unique_id: str | None, error_code: str, error_description: str
    ) -> None:
        """Send an OCPP CALLERROR frame."""

        self.coordinator.record_call_error(
            unique_id=unique_id,
            error_code=error_code,
            error_description=error_description,
        )
        await self._send_frame(
            [CALL_ERROR, unique_id or uuid4().hex, error_code, error_description, {}]
        )

    def _cancel_pending_calls(self) -> None:
        """Cancel any in-flight CALL futures."""

        for future in self._pending_calls.values():
            if not future.done():
                future.cancel()
        self._pending_calls.clear()


def _ocpp_now() -> str:
    """Return an OCPP-friendly UTC timestamp."""

    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
