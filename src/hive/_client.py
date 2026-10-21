"""Stdlib-only stdio adapter for Hive's stable local HTTP endpoint."""

from __future__ import annotations

import contextlib
import http.client
import json
import math
import os
import ssl
import sys
import time
from typing import TYPE_CHECKING, Any

from hive._credential import _read_token
from hive._endpoint import (
    DEFAULT_HOST,
    MCP_PATH,
    configured_daemon_port,
    identity_cert_path,
    token_file_path,
)
from hive._tls import (
    fingerprint_matches,
    load_pinned_certificate,
    pem_fingerprint,
    pinned_context,
    presented_expired_pin,
)

if TYPE_CHECKING:
    from collections.abc import Callable

_CONNECT_TIMEOUT_S = 0.75
_READ_TIMEOUT_S = 70.0
_MAX_ERROR_BODY = 4096
RECONNECT_WINDOW_ENV = "HIVE_CLIENT_RECONNECT_WINDOW_S"
# Past systemd's RestartSec=30, so a request that lands mid-restart still
# reaches the daemon that comes back (#487).
_DEFAULT_RECONNECT_WINDOW_S = 45.0
_MAX_RECONNECT_WINDOW_S = 600.0
_RECONNECT_INTERVAL_S = 1.0


class ClientError(RuntimeError):
    """A safe, actionable adapter failure that never contains credentials."""


class DaemonUnavailableError(ClientError):
    """The daemon could not be reached; the relay answers and keeps running."""


class _RefusedBeforeSendError(DaemonUnavailableError):
    """The connection failed before any byte was written, so a retry cannot
    deliver the request twice."""


def reconnect_window_s() -> float:
    """How long a refused connect is retried, from ``HIVE_CLIENT_RECONNECT_WINDOW_S``.

    Read with the stdlib, like ``configured_daemon_port``: the client must not
    import ``hive.config``. ``0`` disables the wait.
    """
    raw = os.environ.get(RECONNECT_WINDOW_ENV, "").strip()
    if not raw:
        return _DEFAULT_RECONNECT_WINDOW_S
    try:
        window = float(raw)
    except ValueError as exc:
        raise ValueError(
            f"{RECONNECT_WINDOW_ENV} must be a number of seconds, got {raw!r}"
        ) from exc
    if not math.isfinite(window) or not 0 <= window <= _MAX_RECONNECT_WINDOW_S:
        raise ValueError(
            f"{RECONNECT_WINDOW_ENV} must be between 0 and {_MAX_RECONNECT_WINDOW_S:g}, got {raw!r}"
        )
    return window


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


def _discard(frame: dict[str, Any]) -> None:
    return


def _sse_frames(
    response: http.client.HTTPResponse,
    emit: Callable[[dict[str, Any]], None],
) -> list[dict[str, Any]]:
    """Parse an SSE body, handing each frame to ``emit`` as soon as it ends (#499)."""
    frames: list[dict[str, Any]] = []
    data: list[bytes] = []

    def end_event() -> None:
        frame = _json_frame(b"\n".join(data))
        data.clear()
        frames.append(frame)
        emit(frame)

    while line := response.readline():
        stripped = line.rstrip(b"\r\n")
        if not stripped:
            if data:
                end_event()
            continue
        if stripped.startswith(b"data:"):
            data.append(stripped[5:].lstrip())
    if data:
        end_event()
    return frames


class _UnverifiedListenerError(Exception):
    """The listener did not prove the pinned identity."""


def _parse_pin(cert_pem: str) -> tuple[ssl.SSLContext, str]:
    """The TLS context and fingerprint a relay pins, or an actionable error.

    The header check in ``load_pinned_certificate`` lets a truncated or
    hand-edited body through. It fails here, either decoding the fingerprint
    or loading the trust anchor, and must fail as a ``ClientError`` rather
    than a traceback (HIVE-437 AC4).
    """
    try:
        return pinned_context(cert_pem), pem_fingerprint(cert_pem)
    except (ssl.SSLError, ValueError):
        raise ClientError(
            "the daemon identity certificate is corrupt; run "
            "`hive service rotate-identity`; nothing was sent",
        ) from None


class HttpRelay:
    """Translate stdio JSON-RPC messages into Streamable HTTP requests.

    Every request opens a new TCP connection, so the daemon's identity is
    proven on each one, by the pinned TLS handshake and then the certificate
    fingerprint, before the bearer or any message is written (ADR-022 A1).
    """

    def __init__(
        self,
        host: str,
        port: int,
        token: str,
        cert_pem: str,
        *,
        reload: Callable[[], tuple[str, str]] | None = None,
        reconnect_window_s: float = 0.0,
    ) -> None:
        self._host = host
        self._port = port
        self._reload = reload
        self._reconnect_window_s = reconnect_window_s
        self._pin(token, cert_pem)
        self._session_id = ""
        self._protocol_version = ""
        self._bootstrap_message: dict[str, Any] | None = None
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

    def _endpoint(self) -> str:
        return f"https://{self._host}:{self._port}{MCP_PATH}"

    def _impersonation(self) -> ClientError:
        return ClientError(
            f"could not verify the Hive daemon at {self._endpoint()}: the listener did "
            "not prove the owner's identity (possible impersonation); nothing was sent",
        )

    def _pin(self, token: str, cert_pem: str) -> None:
        self._token = token
        self._context, self._fingerprint = _parse_pin(cert_pem)

    def _refresh_pin(self) -> None:
        """Re-read the owner's token and certificate before every connection.

        ``hive service rotate-identity`` replaces both while a relay may still
        be running. Checking only after a failed proof would keep trusting a
        rotated-away key, which is exactly the key an attacker may hold. A pin
        that can no longer be read fails closed rather than falling back to
        the one in memory.
        """
        if self._reload is None:
            return
        try:
            token, cert_pem = self._reload()
        except (OSError, RuntimeError, ValueError):
            raise ClientError(
                "could not re-read the Hive daemon certificate or token; nothing was sent",
            ) from None
        context, fingerprint = _parse_pin(cert_pem)
        if fingerprint != self._fingerprint or token != self._token:
            self._token, self._context, self._fingerprint = token, context, fingerprint

    def _connect(self) -> http.client.HTTPSConnection:
        self._refresh_pin()
        try:
            return self._handshake()
        except _UnverifiedListenerError:
            raise self._impersonation() from None

    def _handshake(self) -> http.client.HTTPSConnection:
        connection = http.client.HTTPSConnection(
            self._host,
            self._port,
            timeout=_CONNECT_TIMEOUT_S,
            context=self._context,
        )
        try:
            connection.connect()
        except ssl.SSLError as exc:
            connection.close()
            if presented_expired_pin(exc):
                raise ClientError(
                    f"the Hive daemon at {self._endpoint()} presented an expired "
                    "certificate; restart the daemon to regenerate it, then check "
                    "`hive service status`; nothing was sent",
                ) from exc
            raise _UnverifiedListenerError from exc
        except OSError as exc:
            # After the ssl.SSLError branch on purpose: SSLError is an OSError,
            # and an impostor or an expired pin must never be retried.
            connection.close()
            raise _RefusedBeforeSendError(f"Hive daemon unavailable at {self._endpoint()}") from exc
        sock = connection.sock
        if not isinstance(sock, ssl.SSLSocket) or not fingerprint_matches(sock, self._fingerprint):
            connection.close()
            raise _UnverifiedListenerError
        return connection

    def _open(
        self,
        method: str,
        message: dict[str, Any] | None = None,
    ) -> tuple[http.client.HTTPConnection, http.client.HTTPResponse]:
        connection = self._connect()
        try:
            if connection.sock is not None:
                connection.sock.settimeout(_read_timeout(message or {}))
            body = None if message is None else json.dumps(message, separators=(",", ":"))
            headers = self._headers(message or {})
            connection.request(method, MCP_PATH, body=body, headers=headers)
            return connection, connection.getresponse()
        except (OSError, http.client.HTTPException) as exc:
            connection.close()
            raise DaemonUnavailableError(f"Hive daemon unavailable at {self._endpoint()}") from exc

    def forward(
        self,
        message: dict[str, Any],
        emit: Callable[[dict[str, Any]], None] = _discard,
    ) -> list[dict[str, Any]]:
        """Forward one client request/notification and return response frames.

        ``emit`` receives each frame as it arrives, so a long call's progress
        reaches the host before its result does (#499).
        """
        method = message.get("method")
        if method in ("initialize", "server/discover"):
            self._bootstrap_message = message.copy()
        elif method == "notifications/initialized":
            self._initialized_message = message.copy()
        deadline = time.monotonic() + self._reconnect_window_s
        while True:
            try:
                return self._forward(message, allow_reinitialize=True, emit=emit)
            except _RefusedBeforeSendError:
                # Only a refusal before sending is retried: nothing reached the
                # daemon, so the request cannot run twice. A 404 on the way
                # here meant the daemon had not run it either.
                if time.monotonic() + _RECONNECT_INTERVAL_S > deadline:
                    raise
                time.sleep(_RECONNECT_INTERVAL_S)

    def _forward(
        self,
        message: dict[str, Any],
        *,
        allow_reinitialize: bool,
        emit: Callable[[dict[str, Any]], None] = _discard,
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
                return self._forward(message, allow_reinitialize=False, emit=emit)
            if response.status != 200:
                response.read(_MAX_ERROR_BODY)
                raise ClientError(f"Hive daemon rejected the request with HTTP {response.status}")
            content_type = response.getheader("Content-Type", "").lower()
            if "text/event-stream" in content_type:
                frames = _sse_frames(response, emit)
            elif "application/json" in content_type:
                frames = [_json_frame(response.read())]
                emit(frames[0])
            else:
                raise ClientError(f"Hive daemon returned unsupported content type {content_type!r}")
            self._capture_protocol(message, frames)
            return frames
        except (OSError, http.client.HTTPException) as exc:
            raise DaemonUnavailableError(f"Hive daemon unavailable at {self._endpoint()}") from exc
        finally:
            connection.close()

    def _reinitialize(self) -> None:
        """Restore the backend session after a daemon restart."""
        if self._bootstrap_message is None:
            raise ClientError("Hive daemon lost the MCP session before discovery or initialize")
        stale = (self._session_id, self._protocol_version)
        self._session_id = ""
        self._protocol_version = ""
        try:
            self._forward(self._bootstrap_message, allow_reinitialize=False)
            if self._bootstrap_message["method"] == "server/discover":
                return
            initialized = self._initialized_message or {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            }
            self._forward(initialized, allow_reinitialize=False)
        except ClientError:
            # Keep the stale session: the next attempt meets its 404 and
            # re-initializes. An empty one would go out sessionless for good,
            # because only a request that carries a session triggers this path.
            self._session_id, self._protocol_version = stale
            raise

    def _capture_protocol(
        self,
        message: dict[str, Any],
        frames: list[dict[str, Any]],
    ) -> None:
        if message.get("method") not in ("initialize", "server/discover"):
            return
        if message.get("method") == "server/discover":
            self._protocol_version = _metadata_protocol(message)
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


class _Answer:
    """Writes one message's frames to stdout and tracks whether it was answered.

    The host waits for an answer to every request it sends. A stream can end
    before that answer, or break just after it, so the relay must know which
    happened: owe an error in the first case, and never send a second answer
    in the other.
    """

    def __init__(self, message: dict[str, Any]) -> None:
        self._id = message.get("id")
        self.owed = "id" in message and "method" in message

    def __call__(self, frame: dict[str, Any]) -> None:
        _write_frame(frame)
        if frame.get("id") == self._id and ("result" in frame or "error" in frame):
            self.owed = False


def _error_frame(message: dict[str, Any], error: ClientError) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": message.get("id"),
        "error": {"code": -32000, "message": str(error)},
    }


def _owner_credentials() -> tuple[str, str]:
    """The owner's bearer and pinned certificate, read from the state dir."""
    return _read_token(token_file_path()), load_pinned_certificate(identity_cert_path())


def run_client(host: str = DEFAULT_HOST) -> int:
    """Relay stdio to the stable daemon, failing explicitly when unavailable."""
    try:
        port = configured_daemon_port()
        token, cert_pem = _owner_credentials()
        relay = HttpRelay(
            host,
            port,
            token,
            cert_pem,
            reload=_owner_credentials,
            reconnect_window_s=reconnect_window_s(),
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"hive client: {exc}", file=sys.stderr)
        return 1
    try:
        for raw in sys.stdin.buffer:
            answer: _Answer | None = None
            try:
                message = json.loads(raw)
                if not isinstance(message, dict):
                    raise ValueError("JSON-RPC message must be an object")
                answer = _Answer(message)
                # The frames go out through `answer` as they arrive; the
                # returned list is the same frames again.
                relay.forward(message, emit=answer)
                if answer.owed:
                    raise DaemonUnavailableError(
                        "the Hive daemon ended the response without answering"
                    )
            except (json.JSONDecodeError, ValueError) as exc:
                print(f"hive client: invalid JSON-RPC input ({exc})", file=sys.stderr)
                return 1
            except DaemonUnavailableError as exc:
                # The harness never relaunches an exited stdio server, so stay
                # up: the next request reconnects to the restarted daemon (#487).
                if answer is not None and answer.owed:
                    _write_frame(_error_frame(message, exc))
                print(f"hive client: {exc}", file=sys.stderr)
            except ClientError as exc:
                if answer is not None and answer.owed:
                    _write_frame(_error_frame(message, exc))
                print(f"hive client: {exc}", file=sys.stderr)
                return 1
    finally:
        relay.close()
    return 0
