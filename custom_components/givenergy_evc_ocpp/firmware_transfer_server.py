"""Local chunked firmware transfer server for charger firmware updates."""

from __future__ import annotations

import json
import math
from pathlib import Path
import socket
import threading
from typing import Any, Awaitable, Callable

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import DEFAULT_LISTEN_HOST

SOCKET_TIMEOUT = 30
RECV_BUFFER = 4096
DEFAULT_CHUNK_SIZE = 4096


class GivEnergyFirmwareTransferServer:
    """Manage the charger's chunked firmware transfer protocol on a local port."""

    def __init__(self, hass: HomeAssistant, root: Path) -> None:
        """Initialise the firmware server wrapper."""

        self.hass = hass
        self.root = root
        self._event_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None
        self._thread: threading.Thread | None = None
        self._server_socket: socket.socket | None = None
        self._startup_complete = threading.Event()
        self._stop_event = threading.Event()
        self._startup_error: Exception | None = None
        self._port: int | None = None

    def set_event_callback(
        self, callback: Callable[[dict[str, Any]], Awaitable[None]]
    ) -> None:
        """Attach a callback used to surface transfer events into HA diagnostics."""

        self._event_callback = callback

    @property
    def is_running(self) -> bool:
        """Return whether the transfer server thread is running."""

        return self._thread is not None and self._thread.is_alive()

    async def async_start(self, port: int) -> None:
        """Start serving firmware files over the proprietary chunked protocol."""

        if self.is_running:
            return

        self.root.mkdir(parents=True, exist_ok=True)
        self._startup_complete.clear()
        self._stop_event.clear()
        self._startup_error = None
        self._port = port
        self._thread = threading.Thread(
            target=self._run_server,
            args=(port,),
            name="givenergy-evc-firmware-transfer",
            daemon=True,
        )
        self._thread.start()
        await self.hass.async_add_executor_job(self._startup_complete.wait, 5.0)

        if self._startup_error is not None:
            self._thread = None
            raise HomeAssistantError(
                f"Unable to start firmware transfer server: {self._startup_error}"
            ) from self._startup_error

    async def async_stop(self) -> None:
        """Stop the transfer server if it is running."""

        self._stop_event.set()

        server_socket = self._server_socket
        if server_socket is not None:
            await self.hass.async_add_executor_job(server_socket.close)

        thread = self._thread
        if thread is not None:
            await self.hass.async_add_executor_job(thread.join, 5.0)

        self._server_socket = None
        self._thread = None
        self._startup_complete.clear()
        self._startup_error = None
        self._port = None

    def _emit_event(self, event: dict[str, Any]) -> None:
        """Send a server event back into Home Assistant."""

        if self._event_callback is None:
            return
        self.hass.loop.call_soon_threadsafe(
            self.hass.async_create_task, self._event_callback(event)
        )

    @staticmethod
    def _recv_json(sock: socket.socket) -> dict[str, Any] | None:
        """Read from a socket until a complete JSON object is available."""

        buffer = b""
        while True:
            try:
                chunk = sock.recv(RECV_BUFFER)
            except socket.timeout:
                return None
            if not chunk:
                return None

            buffer += chunk

            try:
                text = buffer.decode("utf-8", errors="replace").strip()
            except UnicodeDecodeError:
                continue

            depth = 0
            end = -1
            for index, char in enumerate(text):
                if char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        end = index + 1
                        break

            if end <= 0:
                continue

            try:
                parsed = json.loads(text[:end])
            except json.JSONDecodeError:
                continue

            if isinstance(parsed, dict):
                return parsed

    @staticmethod
    def _send_json(sock: socket.socket, obj: dict[str, Any]) -> None:
        """Send a compact JSON object to the charger."""

        payload = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        sock.sendall(payload)

    def _resolve_firmware_path(self, filename: str) -> Path | None:
        """Find a firmware file while blocking path traversal."""

        safe_name = Path(filename.lstrip("/\\")).as_posix()
        if ".." in safe_name.split("/"):
            return None

        candidates = [
            self.root / safe_name,
            self.root / Path(safe_name).name,
        ]

        for candidate in candidates:
            if candidate.is_file():
                return candidate

        return None

    def _handle_download(
        self, sock: socket.socket, remote: str, request: dict[str, Any]
    ) -> None:
        """Send a firmware file to the charger in chunks."""

        requested_filename = str(request.get("filename", ""))
        pack_len = max(1, int(request.get("packlen", DEFAULT_CHUNK_SIZE) or DEFAULT_CHUNK_SIZE))
        file_path = self._resolve_firmware_path(requested_filename)

        if file_path is None:
            self._emit_event(
                {
                    "event": "file_not_found",
                    "remote": remote,
                    "requested_filename": requested_filename,
                }
            )
            self._send_json(sock, {"res": "File does not exist"})
            return

        file_size = file_path.stat().st_size
        pack_num = math.ceil(file_size / pack_len)
        self._emit_event(
            {
                "event": "download_started",
                "remote": remote,
                "requested_filename": requested_filename,
                "filename": file_path.name,
                "filesize": file_size,
                "chunk_size": pack_len,
                "chunk_count": pack_num,
            }
        )
        self._send_json(sock, {"res": "ok", "filesize": str(file_size)})

        checksum = 0
        bytes_sent = 0
        with file_path.open("rb") as firmware:
            while True:
                chunk = firmware.read(pack_len)
                if not chunk:
                    break
                sock.sendall(chunk)
                bytes_sent += len(chunk)
                for byte in chunk:
                    checksum = (checksum + byte) & 0xFFFFFFFF

        self._emit_event(
            {
                "event": "file_sent",
                "remote": remote,
                "filename": file_path.name,
                "bytes": bytes_sent,
                "checksum": checksum,
                "chunk_count": pack_num,
            }
        )

        result = self._recv_json(sock)
        if result is None:
            self._emit_event(
                {
                    "event": "checksum_missing",
                    "remote": remote,
                    "filename": file_path.name,
                    "checksum": checksum,
                }
            )
            return

        charger_checksum = str(result.get("checksum"))
        self._emit_event(
            {
                "event": "checksum_reported",
                "remote": remote,
                "filename": file_path.name,
                "charger_checksum": charger_checksum,
                "server_checksum": str(checksum),
            }
        )

        if charger_checksum == str(checksum):
            self._send_json(sock, {"checksum": "ok"})
            self._emit_event(
                {
                    "event": "checksum_ok",
                    "remote": remote,
                    "filename": file_path.name,
                }
            )
        else:
            self._send_json(sock, {"checksum": "false"})
            self._emit_event(
                {
                    "event": "checksum_mismatch",
                    "remote": remote,
                    "filename": file_path.name,
                    "charger_checksum": charger_checksum,
                    "server_checksum": str(checksum),
                }
            )

    def _handle_upload(
        self, sock: socket.socket, remote: str, request: dict[str, Any]
    ) -> None:
        """Receive a chunked file upload from the charger."""

        filename = Path(str(request.get("filename", "upload.bin"))).name
        pack_len = max(1, int(request.get("packlen", DEFAULT_CHUNK_SIZE) or DEFAULT_CHUNK_SIZE))
        pack_num = max(0, int(request.get("packnum", 0) or 0))
        expected_checksum = int(request.get("checksum", 0) or 0)
        save_dir = self.root / "uploads"
        save_dir.mkdir(parents=True, exist_ok=True)
        save_path = save_dir / filename

        self._emit_event(
            {
                "event": "upload_started",
                "remote": remote,
                "filename": filename,
                "chunk_size": pack_len,
                "chunk_count": pack_num,
            }
        )

        checksum = 0
        bytes_received = 0
        with save_path.open("wb") as uploaded:
            for pack_sn in range(pack_num):
                data = b""
                while len(data) < pack_len:
                    try:
                        chunk = sock.recv(pack_len - len(data))
                    except socket.timeout:
                        break
                    if not chunk:
                        break
                    data += chunk

                if not data:
                    break

                uploaded.write(data)
                bytes_received += len(data)
                for byte in data:
                    checksum = (checksum + byte) & 0xFFFFFFFF
                self._send_json(sock, {"packsn": str(pack_sn)})

        self._emit_event(
            {
                "event": "upload_complete",
                "remote": remote,
                "filename": filename,
                "bytes": bytes_received,
                "checksum": checksum,
                "expected_checksum": expected_checksum,
            }
        )

        if checksum == expected_checksum:
            self._send_json(sock, {"checksum": "ok"})
            self._emit_event(
                {
                    "event": "upload_checksum_ok",
                    "remote": remote,
                    "filename": filename,
                }
            )
        else:
            self._send_json(sock, {"checksum": "false"})
            self._emit_event(
                {
                    "event": "upload_checksum_mismatch",
                    "remote": remote,
                    "filename": filename,
                    "checksum": checksum,
                    "expected_checksum": expected_checksum,
                }
            )

    def _handle_client(self, conn: socket.socket, addr: tuple[str, int]) -> None:
        """Handle a single charger connection."""

        remote = f"{addr[0]}:{addr[1]}"
        self._emit_event({"event": "connect", "remote": remote})
        conn.settimeout(SOCKET_TIMEOUT)

        try:
            request = self._recv_json(conn)
            if request is None:
                self._emit_event({"event": "request_missing", "remote": remote})
                return

            requested_filename = str(request.get("filename", ""))
            upload = str(request.get("upload", "0"))
            self._emit_event(
                {
                    "event": "request_received",
                    "remote": remote,
                    "filename": requested_filename,
                    "upload": upload,
                    "packlen": request.get("packlen"),
                    "packnum": request.get("packnum"),
                }
            )

            if not requested_filename:
                self._send_json(conn, {"res": "Data format error"})
                self._emit_event({"event": "request_invalid", "remote": remote})
                return

            if upload == "1":
                self._handle_upload(conn, remote, request)
            else:
                self._handle_download(conn, remote, request)
        except Exception as err:
            self._emit_event({"event": "client_error", "remote": remote, "error": str(err)})
        finally:
            try:
                conn.close()
            except Exception:
                pass
            self._emit_event({"event": "disconnect", "remote": remote})

    def _run_server(self, port: int) -> None:
        """Run the blocking TCP server loop in a background thread."""

        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((DEFAULT_LISTEN_HOST, port))
            server.listen()
            server.settimeout(0.5)
            self._server_socket = server
            self._emit_event(
                {
                    "event": "server_started",
                    "port": port,
                    "root": str(self.root),
                    "path_hint": "/ChargerFirmware/<filename>.bin",
                }
            )
            self._startup_complete.set()

            while not self._stop_event.is_set():
                try:
                    conn, addr = server.accept()
                except socket.timeout:
                    continue
                except OSError:
                    if self._stop_event.is_set():
                        break
                    raise

                threading.Thread(
                    target=self._handle_client,
                    args=(conn, addr),
                    name="givenergy-evc-firmware-client",
                    daemon=True,
                ).start()
        except Exception as err:  # pragma: no cover - network bootstrap
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
            if self._server_socket is not None:
                try:
                    self._server_socket.close()
                except OSError:
                    pass
            if self._port is not None:
                self._emit_event({"event": "server_stopped", "port": self._port})
            self._server_socket = None
