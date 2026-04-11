"""Local read-only FTP server for bundled firmware files."""

from __future__ import annotations

from pathlib import Path
import threading
from typing import Any, Awaitable, Callable

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import DEFAULT_LISTEN_HOST


class GivEnergyFirmwareFtpServer:
    """Manage a small read-only FTP server for local firmware delivery."""

    def __init__(self, hass: HomeAssistant, root: Path) -> None:
        """Initialise the FTP server wrapper."""

        self.hass = hass
        self.root = root
        self._event_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None
        self._server: Any = None
        self._thread: threading.Thread | None = None
        self._startup_complete = threading.Event()
        self._startup_error: Exception | None = None
        self._port: int | None = None

    def set_event_callback(
        self, callback: Callable[[dict[str, Any]], Awaitable[None]]
    ) -> None:
        """Attach a callback used to surface FTP events into HA diagnostics."""

        self._event_callback = callback

    def set_masquerade_address(self, address: str) -> None:
        """Update the PASV masquerade address on a running server's handler class."""

        if self._server is not None:
            self._server.handler.masquerade_address = address

    @property
    def is_running(self) -> bool:
        """Return whether the FTP server thread is running."""

        return self._thread is not None and self._thread.is_alive()

    async def async_start(
        self,
        port: int,
        *,
        passive_ports: range,
        masquerade_address: str | None = None,
    ) -> None:
        """Start serving firmware files over FTP."""

        if self.is_running:
            return

        self.root.mkdir(parents=True, exist_ok=True)
        self._startup_complete.clear()
        self._startup_error = None
        self._port = port
        self._thread = threading.Thread(
            target=self._run_server,
            args=(port, list(passive_ports), masquerade_address),
            name="givenergy-evc-firmware-ftp",
            daemon=True,
        )
        self._thread.start()
        await self.hass.async_add_executor_job(self._startup_complete.wait, 5.0)

        if self._startup_error is not None:
            self._thread = None
            raise HomeAssistantError(
                f"Unable to start firmware FTP server: {self._startup_error}"
            ) from self._startup_error

    async def async_stop(self) -> None:
        """Stop the FTP server if it is running."""

        server = self._server
        thread = self._thread

        if server is not None:
            await self.hass.async_add_executor_job(server.close_all)

        if thread is not None:
            await self.hass.async_add_executor_job(thread.join, 5.0)

        self._server = None
        self._thread = None
        self._startup_complete.clear()
        self._startup_error = None
        self._port = None

    def _emit_event(self, event: dict[str, Any]) -> None:
        """Send an FTP event back into Home Assistant."""

        if self._event_callback is None:
            return
        self.hass.loop.call_soon_threadsafe(
            self.hass.async_create_task, self._event_callback(event)
        )

    def _run_server(
        self,
        port: int,
        passive_ports: list[int],
        masquerade_address: str | None,
    ) -> None:
        """Run the blocking FTP server loop in a background thread."""

        try:
            from pyftpdlib.authorizers import DummyAuthorizer
            from pyftpdlib.handlers import FTPHandler
            from pyftpdlib.servers import FTPServer

            authorizer = DummyAuthorizer()
            authorizer.add_anonymous(str(self.root), perm="elr")

            parent = self

            class ReadOnlyFirmwareHandler(FTPHandler):
                banner = "GivEnergy EVC OCPP firmware FTP server ready."

                def on_connect(self) -> None:
                    parent._emit_event(
                        {
                            "event": "connect",
                            "remote": self.remote_ip,
                            "port": port,
                        }
                    )

                def on_disconnect(self) -> None:
                    parent._emit_event(
                        {
                            "event": "disconnect",
                            "remote": self.remote_ip,
                        }
                    )

                def on_login(self, username: str) -> None:
                    parent._emit_event(
                        {
                            "event": "login",
                            "remote": self.remote_ip,
                            "username": username,
                        }
                    )

                def on_file_sent(self, file: str) -> None:
                    path = Path(file)
                    parent._emit_event(
                        {
                            "event": "file_sent",
                            "remote": self.remote_ip,
                            "filename": path.name,
                            "bytes": path.stat().st_size if path.exists() else None,
                        }
                    )

                def on_incomplete_file_sent(self, file: str) -> None:
                    path = Path(file)
                    parent._emit_event(
                        {
                            "event": "file_send_incomplete",
                            "remote": self.remote_ip,
                            "filename": path.name,
                            "bytes": path.stat().st_size if path.exists() else None,
                        }
                    )

                def ftp_RETR(self, file: str) -> Any:
                    parent._emit_event(
                        {
                            "event": "retr",
                            "remote": self.remote_ip,
                            "requested_path": file,
                        }
                    )
                    return super().ftp_RETR(file)

            ReadOnlyFirmwareHandler.authorizer = authorizer
            ReadOnlyFirmwareHandler.passive_ports = passive_ports
            if masquerade_address:
                ReadOnlyFirmwareHandler.masquerade_address = masquerade_address
            self._server = FTPServer((DEFAULT_LISTEN_HOST, port), ReadOnlyFirmwareHandler)
            self._emit_event(
                {
                    "event": "server_started",
                    "port": port,
                    "passive_ports": passive_ports,
                    "masquerade_address": masquerade_address,
                    "root": str(self.root),
                }
            )
            self._startup_complete.set()
            self._server.serve_forever(timeout=0.5, blocking=True, handle_exit=False)
        except Exception as err:  # pragma: no cover - network server bootstrap
            self._startup_error = err
            self._emit_event(
                {
                    "event": "server_error",
                    "port": self._port,
                    "error": str(err),
                }
            )
            self._startup_complete.set()
        finally:
            if self._port is not None:
                self._emit_event(
                    {
                        "event": "server_stopped",
                        "port": self._port,
                    }
                )
            self._server = None
