"""Stdlib-only stdio adapter for Hive's stable local HTTP endpoint."""

from __future__ import annotations

import contextlib
import http.client
import json
import math
import sys
from typing import Any

from hive._credential import _read_token
from hive._endpoint import (
    DEFAULT_HOST,
    MCP_PATH,
    configured_daemon_port,
    token_file_path,
)

_CONNECT_TIMEOUT_S = 0.75
_READ_TIMEOUT_S = 70.0
_MAX_ERROR_BODY = 4096


class ClientError(RuntimeError):
    """A safe, actionable adapter failure that never contains credentials."""


def _metadata_protocol(message: dict[str, Any]) -> str:
    params = message.get("params")
    if not isinstance(params, dict):
        return ""
    meta = params.get("_meta")
    if not isinstance(meta, dict):
        return ""
    value = meta.get("io.modelcontextprotocol/protocolVersion")
    return value if isinstance(value, str) else ""


def _message_name(message: dict[str, Any]) -> str:
    params = message.get("params")
    if not isinstance(params, dict):
        return ""
    for key in ("name", "uri"):
        value = params.get(key)
        if isinstance(value, str):
            return value
    return ""


def _read_timeout(message: dict[str, Any]) -> float:
    params = message.get("params")
    if isinstance(params, dict):
        arguments = params.get("arguments")
        if isinstance(arguments, dict):
            deadline = arguments.get("timeout_s")
            if isinstance(deadline, (int, float)) and math.isfinite(deadline) and deadline > 0:
                return max(_READ_TIMEOUT_S, deadline + 10.0)
    return _READ_TIMEOUT_S


def _json_frame(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ClientError("daemon returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise ClientError("daemon returned a non-object JSON-RPC frame")
    return value


def _sse_frames(response: http.client.HTTPResponse) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    data: list[bytes] = []
    while line := response.readline():
        stripped = line.rstrip(b"\r\n")
        if not stripped:
            if data:
                frames.append(_json_frame(b"\n".join(data)))
                data.clear()
            continue
        if stripped.startswith(b"data:"):
            data.append(stripped[5:].lstrip())
    if data:
        frames.append(_json_frame(b"\n".join(data)))
    return frames


class HttpRelay:
    """Translate stdio JSON-RPC messages into Streamable HTTP requests."""

    def __init__(self, host: str, port: int, token: str) -> None:
        self._host = host
        self._port = port
        self._token = token
        self._session_id = ""
        self._protocol_version = ""
        self._initialize_message: dict[str, Any] | None = None
        self._initialized_message: dict[str, Any] | None = None

    def _headers(self, message: dict[str, Any]) -> dict[str, str]:
        protocol = _metadata_protocol(message) or self._protocol_version
        headers = {
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        if protocol:
            headers["MCP-Protocol-Version"] = protocol
        method = message.get("method")
        if isinstance(method, str):
            headers["Mcp-Method"] = method
        if name := _message_name(message):
            headers["Mcp-Name"] = name
        return headers

    def _open(
        self,
        method: str,
        message: dict[str, Any] | None = None,
    ) -> tuple[http.client.HTTPConnection, http.client.HTTPResponse]:
        connection = http.client.HTTPConnection(
            self._host,
            self._port,
            timeout=_CONNECT_TIMEOUT_S,
        )
        try:
            connection.connect()
            if connection.sock is not None:
                connection.sock.settimeout(_read_timeout(message or {}))
            body = None if message is None else json.dumps(message, separators=(",", ":"))
            headers = self._headers(message or {})
            connection.request(method, MCP_PATH, body=body, headers=headers)
            return connection, connection.getresponse()
        except (OSError, http.client.HTTPException) as exc:
            connection.close()
            raise ClientError(
                f"Hive daemon unavailable at http://{self._host}:{self._port}{MCP_PATH}",
            ) from exc

    def forward(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        """Forward one client request/notification and return response frames."""
        method = message.get("method")
        if method == "initialize":
            self._initialize_message = message.copy()
        elif method == "notifications/initialized":
            self._initialized_message = message.copy()
        return self._forward(message, allow_reinitialize=True)

    def _forward(
        self,
        message: dict[str, Any],
        *,
        allow_reinitialize: bool,
    ) -> list[dict[str, Any]]:
        connection, response = self._open("POST", message)
        try:
            if session_id := response.getheader("Mcp-Session-Id"):
                self._session_id = session_id
            if response.status in (202, 204):
                return []
            if response.status == 404 and allow_reinitialize and self._session_id:
                response.read(_MAX_ERROR_BODY)
                self._reinitialize()
                return self._forward(message, allow_reinitialize=False)
            if response.status != 200:
                response.read(_MAX_ERROR_BODY)
                raise ClientError(f"Hive daemon rejected the request with HTTP {response.status}")
            content_type = response.getheader("Content-Type", "").lower()
            if "text/event-stream" in content_type:
                frames = _sse_frames(response)
            elif "application/json" in content_type:
                frames = [_json_frame(response.read())]
            else:
                raise ClientError(f"Hive daemon returned unsupported content type {content_type!r}")
            self._capture_protocol(message, frames)
            return frames
        except (OSError, http.client.HTTPException) as exc:
            raise ClientError(
                f"Hive daemon unavailable at http://{self._host}:{self._port}{MCP_PATH}",
            ) from exc
        finally:
            connection.close()

    def _reinitialize(self) -> None:
        """Restore the backend session after a daemon restart."""
        if self._initialize_message is None:
            raise ClientError("Hive daemon lost the MCP session before initialize")
        self._session_id = ""
        self._protocol_version = ""
        self._forward(self._initialize_message, allow_reinitialize=False)
        initialized = self._initialized_message or {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        }
        self._forward(initialized, allow_reinitialize=False)

    def _capture_protocol(
        self,
        message: dict[str, Any],
        frames: list[dict[str, Any]],
    ) -> None:
        if message.get("method") != "initialize":
            return
        for frame in frames:
            result = frame.get("result")
            if isinstance(result, dict) and isinstance(result.get("protocolVersion"), str):
                self._protocol_version = result["protocolVersion"]
                return

    def close(self) -> None:
        """Terminate a legacy HTTP session when one was negotiated."""
        if not self._session_id:
            return
        with contextlib.suppress(ClientError):
            connection, response = self._open("DELETE")
            try:
                response.read(_MAX_ERROR_BODY)
            finally:
                connection.close()


def _write_frame(frame: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(frame, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _error_frame(message: dict[str, Any], error: ClientError) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": message.get("id"),
        "error": {"code": -32000, "message": str(error)},
    }


def run_client(host: str = DEFAULT_HOST) -> int:
    """Relay stdio to the stable daemon, failing explicitly when unavailable."""
    try:
        port = configured_daemon_port()
        token = _read_token(token_file_path())
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"hive client: {exc}", file=sys.stderr)
        return 1
    relay = HttpRelay(host, port, token)
    try:
        for raw in sys.stdin.buffer:
            try:
                message = json.loads(raw)
                if not isinstance(message, dict):
                    raise ValueError("JSON-RPC message must be an object")
                for frame in relay.forward(message):
                    _write_frame(frame)
            except (json.JSONDecodeError, ValueError) as exc:
                print(f"hive client: invalid JSON-RPC input ({exc})", file=sys.stderr)
                return 1
            except ClientError as exc:
                if "id" in message:
                    _write_frame(_error_frame(message, exc))
                print(f"hive client: {exc}", file=sys.stderr)
                return 1
    finally:
        relay.close()
    return 0
