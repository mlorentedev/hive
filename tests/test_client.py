"""Tests for the stdlib-only stable-endpoint adapter (HIVE-437)."""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import queue
import shutil
import socket
import statistics
import struct
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from tests.impostor import Impostor, server_context

if TYPE_CHECKING:
    from collections.abc import Iterator

    from hive._identity import Identity


class _McpHandler(BaseHTTPRequestHandler):
    requests: list[dict[str, Any]] = []
    reject_session_once = False
    # A session the restarted daemon no longer knows, and the one it hands out next.
    stale_session = ""
    next_session = "session-123"
    cert_pem = ""

    def do_POST(self) -> None:  # noqa: N802
        size = int(self.headers.get("Content-Length", "0"))
        message = json.loads(self.rfile.read(size))
        self.requests.append({"message": message, "headers": dict(self.headers)})
        method = message.get("method")
        if method == "notifications/initialized":
            self.send_response(202)
            self.end_headers()
            return
        if method == "initialize":
            self._send_json(
                {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "result": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "serverInfo": {"name": "test", "version": "1"},
                    },
                },
                session_id=self.next_session,
            )
            return
        if method == "server/discover":
            self._send_json(
                {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "result": {"capabilities": {}},
                },
                session_id="session-456",
            )
            return
        if self.reject_session_once and self.headers.get("Mcp-Session-Id"):
            type(self).reject_session_once = False
            self.send_response(404)
            self.end_headers()
            return
        if self.stale_session and self.headers.get("Mcp-Session-Id") == self.stale_session:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(b"event: message\n")
        self.wfile.write(b'data: {"jsonrpc":"2.0","method":"notifications/progress"}\n\n')
        self.wfile.write(b"event: message\n")
        self.wfile.write(
            b"data: "
            + json.dumps(
                {"jsonrpc": "2.0", "id": message["id"], "result": {"tools": []}},
                separators=(",", ":"),
            ).encode()
            + b"\n\n",
        )

    def do_DELETE(self) -> None:  # noqa: N802
        self.requests.append({"delete": True, "headers": dict(self.headers)})
        self.send_response(200)
        self.end_headers()

    def _send_json(self, payload: dict[str, Any], *, session_id: str = "") -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if session_id:
            self.send_header("Mcp-Session-Id", session_id)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format_string: str, *args: object) -> None:
        return


@pytest.fixture(scope="session")
def owner_identity(tmp_path_factory: pytest.TempPathFactory) -> Identity:
    """The fixture daemon's TLS identity, which every relay under test pins."""
    from hive._identity import create_identity

    state = tmp_path_factory.mktemp("owner-identity")
    return create_identity(state / "daemon.key", state / "daemon.crt")


@pytest.fixture
def mcp_http_server(owner_identity: Identity) -> Iterator[tuple[str, int, type[_McpHandler]]]:
    _McpHandler.requests = []
    _McpHandler.reject_session_once = False
    _McpHandler.stale_session = ""
    _McpHandler.next_session = "session-123"
    _McpHandler.cert_pem = owner_identity.cert_path.read_text(encoding="ascii")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _McpHandler)
    server.socket = server_context(owner_identity).wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield str(host), int(port), _McpHandler
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _install_owner_certificate(identity: Identity, state_dir: Path) -> None:
    from hive._daemon import _enforce_owner_only

    target = state_dir / "daemon.crt"
    shutil.copyfile(identity.cert_path, target)
    _enforce_owner_only(target)


def test_relay_preserves_json_session_protocol_and_sse(
    mcp_http_server: tuple[str, int, type[_McpHandler]],
) -> None:
    from hive._client import HttpRelay

    host, port, handler = mcp_http_server
    relay = HttpRelay(host, port, "secret-token", handler.cert_pem)
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2025-06-18"},
    }

    assert relay.forward(initialize)[0]["result"]["protocolVersion"] == "2025-06-18"
    assert relay.forward({"jsonrpc": "2.0", "method": "notifications/initialized"}) == []
    frames = relay.forward({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})

    assert frames == [
        {"jsonrpc": "2.0", "method": "notifications/progress"},
        {"jsonrpc": "2.0", "id": 2, "result": {"tools": []}},
    ]
    headers = handler.requests[-1]["headers"]
    assert headers["Mcp-Session-Id"] == "session-123"
    assert headers["MCP-Protocol-Version"] == "2025-06-18"
    assert headers["Accept"] == "application/json, text/event-stream"
    assert headers["Authorization"] == "Bearer secret-token"

    relay.close()
    assert handler.requests[-1]["delete"] is True


def test_relay_adds_current_protocol_metadata_header(
    mcp_http_server: tuple[str, int, type[_McpHandler]],
) -> None:
    from hive._client import HttpRelay

    host, port, handler = mcp_http_server
    relay = HttpRelay(host, port, "secret-token", handler.cert_pem)
    message = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "server/discover",
        "params": {
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            },
        },
    }

    relay.forward(message)

    assert handler.requests[-1]["headers"]["MCP-Protocol-Version"] == "2026-07-28"


def test_relay_reinitializes_after_daemon_restart_loses_session(
    mcp_http_server: tuple[str, int, type[_McpHandler]],
) -> None:
    from hive._client import HttpRelay

    host, port, handler = mcp_http_server
    relay = HttpRelay(host, port, "secret-token", handler.cert_pem)
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2025-06-18"},
    }
    relay.forward(initialize)
    handler.reject_session_once = True

    frames = relay.forward({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})

    assert frames[-1] == {"jsonrpc": "2.0", "id": 2, "result": {"tools": []}}
    methods = [request["message"]["method"] for request in handler.requests if "message" in request]
    assert methods == [
        "initialize",
        "tools/list",
        "initialize",
        "notifications/initialized",
        "tools/list",
    ]


def test_relay_rediscovers_after_daemon_restart_for_2026_protocol(
    mcp_http_server: tuple[str, int, type[_McpHandler]],
) -> None:
    from hive._client import HttpRelay

    host, port, handler = mcp_http_server
    relay = HttpRelay(host, port, "secret-token", handler.cert_pem)
    discover = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "server/discover",
        "params": {"_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}},
    }
    assert relay.forward(discover)[0]["result"] == {"capabilities": {}}
    handler.reject_session_once = True

    frames = relay.forward({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})

    assert frames[-1] == {"jsonrpc": "2.0", "id": 2, "result": {"tools": []}}
    methods = [request["message"]["method"] for request in handler.requests if "message" in request]
    assert methods == ["server/discover", "tools/list", "server/discover", "tools/list"]
    assert handler.requests[-1]["headers"]["MCP-Protocol-Version"] == "2026-07-28"


def test_relay_connection_failure_is_explicit_and_redacts_token(owner_identity: Identity) -> None:
    from hive._client import ClientError, HttpRelay

    pem = owner_identity.cert_path.read_text(encoding="ascii")
    relay = HttpRelay("127.0.0.1", 1, "must-never-leak", pem)

    with pytest.raises(ClientError, match="daemon unavailable") as excinfo:
        relay.forward({"jsonrpc": "2.0", "id": 1, "method": "initialize"})

    assert "must-never-leak" not in str(excinfo.value)


def test_relay_times_out_unresponsive_daemon_after_requested_deadline(
    mcp_http_server: tuple[str, int, type[_McpHandler]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hive._client import ClientError, HttpRelay

    host, port, handler = mcp_http_server
    timeouts: list[float] = []
    original_getresponse = http.client.HTTPConnection.getresponse

    def stalled_response(connection: http.client.HTTPConnection) -> None:
        assert connection.sock is not None
        read_timeout = connection.sock.gettimeout()
        assert isinstance(read_timeout, float)
        timeouts.append(read_timeout)
        original_getresponse(connection).read()
        raise TimeoutError("daemon stopped responding")

    monkeypatch.setattr(http.client.HTTPConnection, "getresponse", stalled_response)
    with pytest.raises(ClientError, match="daemon unavailable"):
        HttpRelay(host, port, "secret-token", handler.cert_pem).forward(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "delegate_task", "arguments": {"timeout_s": 180}},
            },
        )
    assert 180 < timeouts[0] < 200


def test_relay_reports_timeout_while_reading_sse(
    mcp_http_server: tuple[str, int, type[_McpHandler]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hive._client as client_module

    host, port, _handler = mcp_http_server

    def stalled_stream(response: http.client.HTTPResponse) -> list[dict[str, Any]]:
        response.read()
        raise TimeoutError("daemon stopped sending events")

    monkeypatch.setattr(client_module, "_sse_frames", stalled_stream)
    with pytest.raises(client_module.ClientError, match="daemon unavailable"):
        client_module.HttpRelay(host, port, "secret-token", _McpHandler.cert_pem).forward(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )


@pytest.mark.parametrize("impostor_kind", ["plaintext", "wrong-certificate"])
def test_relay_refuses_impostors_before_sending_bytes(
    owner_identity: Identity,
    tmp_path: Path,
    impostor_kind: str,
) -> None:
    from hive._client import ClientError, HttpRelay
    from hive._identity import create_identity

    impostor_identity = None
    if impostor_kind == "wrong-certificate":
        impostor_identity = create_identity(tmp_path / "impostor.key", tmp_path / "impostor.crt")
    impostor = Impostor(impostor_identity)
    token = "synthetic-bearer-" + "x" * 32
    relay = HttpRelay(
        "127.0.0.1",
        impostor.port,
        token,
        owner_identity.cert_path.read_text(encoding="ascii"),
    )

    with pytest.raises(ClientError, match="possible impersonation") as excinfo:
        relay.forward({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    impostor.wait()

    impostor.assert_refused_before_any_request(token)
    assert token not in str(excinfo.value)


def test_client_entrypoint_does_not_import_the_server_stack(tmp_path) -> None:
    script = tmp_path / "probe.py"
    script.write_text(
        "\n".join(
            [
                "import sys",
                "sys.argv = ['hive', 'client']",
                "import hive._client",
                "hive._client.run_client = lambda host='127.0.0.1': 0",
                "from hive.cli import main",
                "try:",
                "    main()",
                "except SystemExit as exc:",
                "    assert exc.code == 0",
                "forbidden = [n for n in sys.modules",
                "             if n == 'hive.server'",
                "             or n == 'hive._daemon'",
                "             or n == 'hive.config'",
                "             or n == 'subprocess'",
                "             or n.startswith('cryptography')",
                "             or n.startswith('fastmcp')",
                "             or n.startswith('mcp')]",
                "assert forbidden == [], forbidden",
            ],
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(script)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_console_scripts_use_the_lightweight_dispatcher() -> None:
    import tomllib
    from pathlib import Path

    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert project["project"]["scripts"] == {
        "hive": "hive.cli:main",
        "hive-vault": "hive.cli:main",
    }


def _measure_first_initialize(
    launcher: Path, host: str, env: dict[str, str], message: str
) -> float:
    started = time.monotonic()
    process = subprocess.Popen(
        [str(launcher), "client", "--host", host],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    responses: queue.Queue[str] = queue.Queue()
    reader = threading.Thread(target=lambda: responses.put(process.stdout.readline()), daemon=True)
    reader.start()
    try:
        process.stdin.write(message + "\n")
        process.stdin.flush()
        first_response = responses.get(timeout=4.1)
        elapsed = time.monotonic() - started
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    assert process.returncode == 0, process.stderr.read()
    assert json.loads(first_response)["id"] == 1
    return elapsed


def test_client_initialize_response_arrives_within_one_second(
    mcp_http_server: tuple[str, int, type[_McpHandler]],
    owner_identity: Identity,
    tmp_path: Path,
) -> None:
    from hive._daemon import _enforce_owner_only

    host, port, _handler = mcp_http_server
    _install_owner_certificate(owner_identity, tmp_path)
    token_path = tmp_path / "daemon.token"
    token_path.write_text("a" * 43, encoding="utf-8")
    _enforce_owner_only(token_path)
    env = {
        **os.environ,
        "HIVE_DB_PATH": str(tmp_path / "worker.db"),
        "HIVE_DAEMON_PORT": str(port),
    }
    message = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        },
    )
    launcher = Path(sys.executable).parent / ("hive.exe" if os.name == "nt" else "hive")
    assert launcher.is_file()

    cold_starts = [_measure_first_initialize(launcher, host, env, message) for _ in range(5)]
    assert statistics.median(cold_starts) < 1.0, cold_starts
    assert max(cold_starts) < 4.1, cold_starts


def test_client_without_credential_fails_without_starting_fallback(tmp_path) -> None:
    env = {
        **os.environ,
        "HIVE_DB_PATH": str(tmp_path / "worker.db"),
        "HIVE_DAEMON_PORT": "60412",
    }

    result = subprocess.run(
        [sys.executable, "-m", "hive.cli", "client"],
        input='{"jsonrpc":"2.0","id":1,"method":"initialize"}\n',
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=2.0,
    )

    assert result.returncode != 0
    assert result.stdout == ""
    assert "credential" in result.stderr.lower()


@pytest.mark.parametrize("token_text,expected", [("invalid", "invalid"), ("a" * 43, "owner-only")])
def test_client_rejects_corrupt_or_permission_invalid_credential(
    tmp_path: Path, token_text: str, expected: str
) -> None:
    token_path = tmp_path / "daemon.token"
    token_path.write_text(token_text, encoding="utf-8")
    if os.name == "nt":
        subprocess.run(
            ["icacls", str(token_path), "/grant", "*S-1-1-0:(R)"],
            check=True,
            capture_output=True,
        )
    else:
        token_path.chmod(0o644)
    env = {
        **os.environ,
        "HIVE_DB_PATH": str(tmp_path / "worker.db"),
        "HIVE_DAEMON_PORT": "1",
    }

    result = subprocess.run(
        [sys.executable, "-m", "hive.cli", "client"],
        input='{"jsonrpc":"2.0","id":1,"method":"initialize"}\n',
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=3.0,
    )

    assert result.returncode != 0
    assert result.stdout == ""
    assert expected in result.stderr
    if len(token_text) >= 32:
        assert token_text not in result.stderr


@pytest.fixture(scope="session")
def retired_identity(tmp_path_factory: pytest.TempPathFactory) -> Identity:
    """An identity the owner rotated away from (HIVE-456 AC6)."""
    from hive._identity import create_identity

    state = tmp_path_factory.mktemp("retired-identity")
    return create_identity(state / "daemon.key", state / "daemon.crt")


_TOOLS_LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}


def test_relay_repins_after_rotation_and_refuses_old_certificate(
    mcp_http_server: tuple[str, int, type[_McpHandler]],
    retired_identity: Identity,
) -> None:
    from hive._client import ClientError, HttpRelay

    host, port, handler = mcp_http_server
    retired_pem = retired_identity.cert_path.read_text(encoding="ascii")
    reloads: list[int] = []

    def rotated() -> tuple[str, str]:
        reloads.append(1)
        return "rotated-token", handler.cert_pem

    # A relay started before `rotate-identity` still pins the retired
    # certificate; the restarted daemon presents the new one.
    relay = HttpRelay(host, port, "retired-token", retired_pem, reload=rotated)
    frames = relay.forward(_TOOLS_LIST)

    assert frames[-1]["result"] == {"tools": []}
    assert reloads, "the relay re-read the pin before connecting"
    assert [r["headers"]["Authorization"] for r in handler.requests] == ["Bearer rotated-token"]

    # Now pinned to the new certificate, the relay refuses a listener that
    # still presents the retired one, and sends it nothing.
    impostor = Impostor(retired_identity)
    stale = HttpRelay(host, impostor.port, "rotated-token", handler.cert_pem, reload=rotated)
    with pytest.raises(ClientError, match="possible impersonation"):
        stale.forward(_TOOLS_LIST)
    impostor.wait()
    assert impostor.accepted.is_set()
    assert impostor.raw[:1] == b"\x16"
    assert impostor.decrypted == bytearray()


def test_relay_names_an_expired_owner_certificate(tmp_path: Path) -> None:
    import datetime as dt

    from hive._client import ClientError, HttpRelay
    from hive._identity import create_identity

    long_ago = dt.datetime.now(dt.UTC) - dt.timedelta(days=500)
    expired = create_identity(tmp_path / "daemon.key", tmp_path / "daemon.crt", now=long_ago)
    pem = expired.cert_path.read_text(encoding="ascii")
    listener = Impostor(expired)
    relay = HttpRelay("127.0.0.1", listener.port, "secret-token", pem)
    with pytest.raises(ClientError, match="expired certificate") as excinfo:
        relay.forward(_TOOLS_LIST)
    listener.wait()
    assert "secret-token" not in str(excinfo.value)
    assert listener.raw[:1] == b"\x16"
    assert listener.decrypted == bytearray()


def test_relay_drops_a_rotated_away_pin_before_its_next_connection(
    mcp_http_server: tuple[str, int, type[_McpHandler]],
    retired_identity: Identity,
) -> None:
    """A rotation for an exposed key must stop a running relay trusting it.

    The relay still pins the retired certificate, and an impostor holds the
    retired key, so the handshake would succeed. The relay must notice the
    rotation on disk before it connects, not only after a failed proof.
    """
    from hive._client import ClientError, HttpRelay

    _, _, handler = mcp_http_server
    retired_pem = retired_identity.cert_path.read_text(encoding="ascii")
    impostor = Impostor(retired_identity)
    relay = HttpRelay(
        "127.0.0.1",
        impostor.port,
        "retired-token",
        retired_pem,
        reload=lambda: ("rotated-token", handler.cert_pem),
    )
    with pytest.raises(ClientError, match="possible impersonation"):
        relay.forward(_TOOLS_LIST)
    impostor.wait()
    assert impostor.accepted.is_set()
    assert impostor.raw[:1] == b"\x16"
    assert impostor.decrypted == bytearray(), "the stale pin let the request through"


def test_relay_fails_closed_when_the_pin_cannot_be_reread(
    mcp_http_server: tuple[str, int, type[_McpHandler]],
) -> None:
    from hive._client import ClientError, HttpRelay

    host, port, handler = mcp_http_server

    def unreadable() -> tuple[str, str]:
        raise RuntimeError("daemon certificate is missing")

    relay = HttpRelay(host, port, "secret-token", handler.cert_pem, reload=unreadable)
    with pytest.raises(ClientError, match="certificate"):
        relay.forward(_TOOLS_LIST)
    assert handler.requests == []


# Certificates that pass the header check in `load_pinned_certificate` but
# whose body is not a certificate, reachable by a truncated write or a hand
# edit. They fail at different layers: a valid-base64 body decodes and only the
# TLS context rejects it; an invalid one fails while computing the fingerprint.
_CORRUPT_PEMS = [
    pytest.param(
        "-----BEGIN CERTIFICATE-----\nbm90IGEgY2VydGlmaWNhdGU=\n-----END CERTIFICATE-----\n",
        id="not-der",
    ),
    pytest.param(
        "-----BEGIN CERTIFICATE-----\nnot base64 at all\n-----END CERTIFICATE-----\n",
        id="not-base64",
    ),
]


@pytest.mark.parametrize("corrupt_pem", _CORRUPT_PEMS)
def test_client_reports_a_corrupt_certificate_without_a_traceback(
    tmp_path: Path, corrupt_pem: str
) -> None:
    from hive._daemon import _enforce_owner_only

    token = "a" * 43
    for name, text in (("daemon.token", token), ("daemon.crt", corrupt_pem)):
        path = tmp_path / name
        path.write_text(text, encoding="ascii")
        _enforce_owner_only(path)
    env = {
        **os.environ,
        "HIVE_DB_PATH": str(tmp_path / "worker.db"),
        "HIVE_DAEMON_PORT": "1",
    }

    result = subprocess.run(
        [sys.executable, "-m", "hive.cli", "client"],
        input='{"jsonrpc":"2.0","id":1,"method":"initialize"}\n',
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=3.0,
    )

    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert "rotate-identity" in result.stderr
    assert token not in result.stderr + result.stdout


@pytest.mark.parametrize("corrupt_pem", _CORRUPT_PEMS)
def test_relay_fails_closed_when_the_rotated_certificate_is_corrupt(
    mcp_http_server: tuple[str, int, type[_McpHandler]],
    corrupt_pem: str,
) -> None:
    from hive._client import ClientError, HttpRelay

    host, port, handler = mcp_http_server
    relay = HttpRelay(
        host,
        port,
        "secret-token",
        handler.cert_pem,
        reload=lambda: ("rotated-token", corrupt_pem),
    )
    with pytest.raises(ClientError, match="rotate-identity") as excinfo:
        relay.forward(_TOOLS_LIST)
    assert "secret-token" not in str(excinfo.value)
    assert handler.requests == []


# ── #487: a request that lands while the daemon restarts ─────────────────


@pytest.fixture
def restarting_daemon(
    owner_identity: Identity,
) -> Iterator[tuple[str, int, ThreadingHTTPServer]]:
    """A daemon whose port is bound but not yet listening, as during a restart.

    Holding the port bound means a connect fails until `server_activate()`
    (refused on Linux, a connect timeout on macOS: both before anything is
    sent), with no window in which another socket can take the port (#417).
    """
    _McpHandler.requests = []
    _McpHandler.reject_session_once = False
    _McpHandler.stale_session = ""
    _McpHandler.next_session = "session-123"
    _McpHandler.cert_pem = owner_identity.cert_path.read_text(encoding="ascii")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _McpHandler, bind_and_activate=False)
    server.server_bind()
    server.socket = server_context(owner_identity).wrap_socket(server.socket, server_side=True)
    host, port = server.server_address
    try:
        yield str(host), int(port), server
    finally:
        # shutdown() waits for serve_forever() to exit, so it blocks for good
        # on a server that never came back.
        if getattr(server, "came_back", False):
            server.shutdown()
        server.server_close()


def _come_back_after(server: ThreadingHTTPServer, delay_s: float) -> threading.Thread:
    def back() -> None:
        time.sleep(delay_s)
        server.server_activate()
        server.came_back = True  # type: ignore[attr-defined]
        server.serve_forever()

    thread = threading.Thread(target=back, daemon=True)
    thread.start()
    return thread


def test_relay_waits_for_a_restarting_daemon(
    restarting_daemon: tuple[str, int, ThreadingHTTPServer],
) -> None:
    from hive._client import HttpRelay

    host, port, server = restarting_daemon
    relay = HttpRelay(host, port, "secret-token", _McpHandler.cert_pem, reconnect_window_s=10.0)
    _come_back_after(server, 1.5)
    started = time.monotonic()

    frames = relay.forward(_TOOLS_LIST)

    assert frames[-1] == {"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}
    assert time.monotonic() - started >= 1.0, "the first connect must have failed"
    assert [r["message"]["method"] for r in _McpHandler.requests] == ["tools/list"]


def test_relay_gives_up_when_the_daemon_does_not_return(
    restarting_daemon: tuple[str, int, ThreadingHTTPServer],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hive._client as client_module

    host, port, _server = restarting_daemon
    monkeypatch.setattr(client_module, "_RECONNECT_INTERVAL_S", 0.1)
    relay = client_module.HttpRelay(
        host, port, "secret-token", _McpHandler.cert_pem, reconnect_window_s=1.5
    )
    started = time.monotonic()

    with pytest.raises(client_module.DaemonUnavailableError, match="daemon unavailable"):
        relay.forward(_TOOLS_LIST)

    assert 1.4 <= time.monotonic() - started < 5.0, "it must wait out the window, then stop"


def test_relay_restores_the_session_after_waiting_out_a_restart(
    mcp_http_server: tuple[str, int, type[_McpHandler]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hive._client as client_module

    host, port, handler = mcp_http_server
    relay = client_module.HttpRelay(
        host, port, "secret-token", handler.cert_pem, reconnect_window_s=10.0
    )
    relay.forward({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    # The restarted daemon refuses twice, then answers without the old session.
    real_handshake = client_module.HttpRelay._handshake
    refusals = [1, 1]

    def flaky(self: client_module.HttpRelay) -> http.client.HTTPSConnection:
        if refusals:
            refusals.pop()
            raise client_module._RefusedBeforeSendError("Hive daemon unavailable (test)")
        return real_handshake(self)

    monkeypatch.setattr(client_module.HttpRelay, "_handshake", flaky)
    monkeypatch.setattr(client_module, "_RECONNECT_INTERVAL_S", 0.05)
    handler.reject_session_once = True

    frames = relay.forward({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})

    assert frames[-1] == {"jsonrpc": "2.0", "id": 2, "result": {"tools": []}}
    assert refusals == []
    methods = [r["message"]["method"] for r in handler.requests if "message" in r]
    assert methods == [
        "initialize",
        "tools/list",
        "initialize",
        "notifications/initialized",
        "tools/list",
    ]


def test_relay_never_retries_an_impostor(owner_identity: Identity) -> None:
    from hive._client import ClientError, HttpRelay

    impostor = Impostor(None)
    relay = HttpRelay(
        "127.0.0.1",
        impostor.port,
        "synthetic-bearer-" + "x" * 32,
        owner_identity.cert_path.read_text(encoding="ascii"),
        reconnect_window_s=30.0,
    )
    started = time.monotonic()

    with pytest.raises(ClientError, match="possible impersonation"):
        relay.forward(_TOOLS_LIST)
    impostor.wait()

    assert time.monotonic() - started < 5.0, "an impostor must fail at once, not after the window"


# ── #513: a restart that closes the connection before the handshake ends ──


def _close_before_handshake(conn: socket.socket, *, reset: bool) -> None:
    # Read the ClientHello first: closing with it unread sends an RST, so the
    # FIN case would race into the reset case.
    conn.settimeout(5)
    with contextlib.suppress(OSError):
        conn.recv(65536)
    if reset:
        # SO_LINGER 0 makes close() send an RST.
        conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    conn.close()


@pytest.fixture
def closing_then_serving_daemon(
    owner_identity: Identity,
) -> Iterator[tuple[str, int, ThreadingHTTPServer]]:
    """A daemon that listens, closes its first connection unanswered, then serves.

    A daemon stopping or starting can accept a connection and close it before
    TLS completes. The same listening socket then serves, so no other socket can
    take the port in between.
    """
    _McpHandler.requests = []
    _McpHandler.reject_session_once = False
    _McpHandler.stale_session = ""
    _McpHandler.next_session = "session-123"
    _McpHandler.cert_pem = owner_identity.cert_path.read_text(encoding="ascii")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _McpHandler, bind_and_activate=False)
    server.server_bind()
    server.server_activate()
    server.closed_unanswered = 0  # type: ignore[attr-defined]

    def close_once_then_serve() -> None:
        conn, _ = server.socket.accept()
        _close_before_handshake(conn, reset=False)
        server.closed_unanswered += 1  # type: ignore[attr-defined]
        server.socket = server_context(owner_identity).wrap_socket(server.socket, server_side=True)
        server.serve_forever()

    threading.Thread(target=close_once_then_serve, daemon=True).start()
    host, port = server.server_address
    try:
        yield str(host), int(port), server
    finally:
        server.shutdown()
        server.server_close()


def test_relay_waits_out_a_restart_that_closes_before_the_handshake(
    closing_then_serving_daemon: tuple[str, int, ThreadingHTTPServer],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hive._client as client_module

    host, port, server = closing_then_serving_daemon
    monkeypatch.setattr(client_module, "_RECONNECT_INTERVAL_S", 0.1)
    relay = client_module.HttpRelay(
        host, port, "secret-token", _McpHandler.cert_pem, reconnect_window_s=10.0
    )

    frames = relay.forward(_TOOLS_LIST)

    assert server.closed_unanswered == 1  # type: ignore[attr-defined]
    assert frames[-1] == {"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}
    assert [r["message"]["method"] for r in _McpHandler.requests] == ["tools/list"]


class _ClosingListener:
    """Closes every connection before the TLS handshake, as a daemon that never comes back."""

    def __init__(self, *, reset: bool) -> None:
        self.listener = socket.create_server(("127.0.0.1", 0))
        # Closing the socket does not wake a blocked accept() on Linux.
        self.listener.settimeout(0.1)
        self.port = int(self.listener.getsockname()[1])
        self.closed = 0
        self._reset = reset
        self._stopping = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stopping.is_set():
            try:
                conn, _ = self.listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            _close_before_handshake(conn, reset=self._reset)
            self.closed += 1

    def stop(self) -> None:
        self._stopping.set()
        self._thread.join(timeout=5)
        self.listener.close()


@pytest.mark.parametrize("reset", [False, True], ids=["fin", "rst"])
def test_relay_reports_a_listener_that_always_closes_as_unavailable(
    owner_identity: Identity,
    monkeypatch: pytest.MonkeyPatch,
    reset: bool,
) -> None:
    """It presented no certificate, so it is not reported as an impersonation."""
    import hive._client as client_module

    listener = _ClosingListener(reset=reset)
    monkeypatch.setattr(client_module, "_RECONNECT_INTERVAL_S", 0.1)
    relay = client_module.HttpRelay(
        "127.0.0.1",
        listener.port,
        "secret-token",
        owner_identity.cert_path.read_text(encoding="ascii"),
        reconnect_window_s=1.0,
    )
    started = time.monotonic()

    try:
        with pytest.raises(client_module.DaemonUnavailableError, match="daemon unavailable"):
            relay.forward(_TOOLS_LIST)
        elapsed = time.monotonic() - started
    finally:
        listener.stop()

    assert listener.closed >= 2, "it must have retried within the window"
    assert elapsed < 5.0


def test_relay_never_retries_a_request_the_daemon_may_have_received(
    mcp_http_server: tuple[str, int, type[_McpHandler]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure after the request was written could duplicate a write if retried."""
    from hive._client import DaemonUnavailableError, HttpRelay

    host, port, handler = mcp_http_server
    original_getresponse = http.client.HTTPConnection.getresponse

    def dropped(connection: http.client.HTTPConnection) -> None:
        original_getresponse(connection).read()
        raise ConnectionResetError("daemon went away mid-response")

    monkeypatch.setattr(http.client.HTTPConnection, "getresponse", dropped)
    relay = HttpRelay(host, port, "secret-token", handler.cert_pem, reconnect_window_s=30.0)
    started = time.monotonic()

    with pytest.raises(DaemonUnavailableError):
        relay.forward(_TOOLS_LIST)

    assert time.monotonic() - started < 5.0
    assert len(handler.requests) == 1, "the request must have been sent exactly once"


def test_client_survives_an_unreachable_daemon(
    owner_identity: Identity,
    tmp_path: Path,
) -> None:
    """Each request gets a JSON-RPC error, and the client keeps reading stdin."""
    from hive._daemon import _enforce_owner_only

    _install_owner_certificate(owner_identity, tmp_path)
    token_path = tmp_path / "daemon.token"
    token = "a" * 43
    token_path.write_text(token, encoding="utf-8")
    _enforce_owner_only(token_path)
    env = {
        **os.environ,
        "HIVE_DB_PATH": str(tmp_path / "worker.db"),
        "HIVE_DAEMON_PORT": "1",
        "HIVE_CLIENT_RECONNECT_WINDOW_S": "0",
    }

    result = subprocess.run(
        [sys.executable, "-m", "hive.cli", "client"],
        input=(
            '{"jsonrpc":"2.0","id":1,"method":"initialize"}\n'
            '{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n'
        ),
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=10.0,
    )

    frames = [json.loads(line) for line in result.stdout.splitlines()]
    assert [f["id"] for f in frames] == [1, 2], "both requests must be answered"
    assert all(f["error"]["code"] == -32000 for f in frames)
    assert result.stderr.count("daemon unavailable") == 2
    assert token not in result.stderr + result.stdout


@pytest.mark.parametrize("raw", ["-1", "nan", "abc", "601"])
def test_reconnect_window_rejects_an_invalid_value(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    from hive._client import reconnect_window_s

    monkeypatch.setenv("HIVE_CLIENT_RECONNECT_WINDOW_S", raw)
    with pytest.raises(ValueError, match="HIVE_CLIENT_RECONNECT_WINDOW_S"):
        reconnect_window_s()


def test_reconnect_window_defaults_past_the_supervisors_restart_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hive._client import reconnect_window_s

    monkeypatch.delenv("HIVE_CLIENT_RECONNECT_WINDOW_S", raising=False)
    assert reconnect_window_s() == 45.0


def _calls(handler: type[_McpHandler]) -> list[tuple[str, str | None]]:
    return [
        (r["message"]["method"], r["headers"].get("Mcp-Session-Id"))
        for r in handler.requests
        if "message" in r
    ]


def _restart_daemon(handler: type[_McpHandler]) -> None:
    """The old session is gone, and a new initialize hands out another."""
    handler.stale_session = "session-123"
    handler.next_session = "session-789"


def test_relay_rebootstraps_when_the_restart_interrupts_reinitialize(
    mcp_http_server: tuple[str, int, type[_McpHandler]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refusal between the 404 and the new initialize must not lose the bootstrap."""
    import hive._client as client_module

    host, port, handler = mcp_http_server
    relay = client_module.HttpRelay(
        host, port, "secret-token", handler.cert_pem, reconnect_window_s=10.0
    )
    relay.forward({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    _restart_daemon(handler)
    real_handshake = client_module.HttpRelay._handshake
    connects: list[int] = []

    def refuse_the_rebootstrap(self: client_module.HttpRelay) -> http.client.HTTPSConnection:
        connects.append(1)
        if len(connects) == 2:  # 1: tools/list -> 404; 2: the re-initialize
            raise client_module._RefusedBeforeSendError("Hive daemon unavailable (test)")
        return real_handshake(self)

    monkeypatch.setattr(client_module.HttpRelay, "_handshake", refuse_the_rebootstrap)
    monkeypatch.setattr(client_module, "_RECONNECT_INTERVAL_S", 0.05)

    frames = relay.forward({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})

    assert frames[-1] == {"jsonrpc": "2.0", "id": 2, "result": {"tools": []}}
    assert _calls(handler)[-3:] == [
        ("initialize", None),
        ("notifications/initialized", "session-789"),
        ("tools/list", "session-789"),
    ], "the request must go out on a re-initialized session, never sessionless"


def test_relay_recovers_after_a_failed_reinitialize(
    mcp_http_server: tuple[str, int, type[_McpHandler]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A re-initialize lost after sending is answered; the next request retries it."""
    from hive._client import DaemonUnavailableError, HttpRelay

    host, port, handler = mcp_http_server
    relay = HttpRelay(host, port, "secret-token", handler.cert_pem, reconnect_window_s=10.0)
    relay.forward({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    _restart_daemon(handler)
    original_getresponse = http.client.HTTPConnection.getresponse
    responses: list[int] = []

    def drop_the_reinitialize(connection: http.client.HTTPConnection) -> http.client.HTTPResponse:
        responses.append(1)
        response = original_getresponse(connection)
        if len(responses) == 2:  # 1: tools/list -> 404; 2: the re-initialize
            response.read()
            raise ConnectionResetError("daemon went away mid-response")
        return response

    monkeypatch.setattr(http.client.HTTPConnection, "getresponse", drop_the_reinitialize)
    with pytest.raises(DaemonUnavailableError):
        relay.forward({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})

    frames = relay.forward({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})

    assert frames[-1] == {"jsonrpc": "2.0", "id": 3, "result": {"tools": []}}
    assert _calls(handler)[-3:] == [
        ("initialize", None),
        ("notifications/initialized", "session-789"),
        ("tools/list", "session-789"),
    ], "a failed re-initialize must not leave the relay sessionless for good"


def test_relay_never_retries_an_expired_pin(tmp_path: Path) -> None:
    import datetime as dt

    from hive._client import ClientError, DaemonUnavailableError, HttpRelay
    from hive._identity import create_identity

    long_ago = dt.datetime.now(dt.UTC) - dt.timedelta(days=500)
    expired = create_identity(tmp_path / "daemon.key", tmp_path / "daemon.crt", now=long_ago)
    listener = Impostor(expired)
    relay = HttpRelay(
        "127.0.0.1",
        listener.port,
        "secret-token",
        expired.cert_path.read_text(encoding="ascii"),
        reconnect_window_s=30.0,
    )
    started = time.monotonic()

    with pytest.raises(ClientError, match="expired certificate") as excinfo:
        relay.forward(_TOOLS_LIST)
    listener.wait()

    assert not isinstance(excinfo.value, DaemonUnavailableError), "an expired pin must exit"
    assert time.monotonic() - started < 5.0


def test_relay_never_retries_a_failed_event_stream(
    mcp_http_server: tuple[str, int, type[_McpHandler]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The daemon received the request before its stream broke; a retry could run it twice."""
    import hive._client as client_module

    host, port, handler = mcp_http_server

    def broken_stream(response: http.client.HTTPResponse) -> list[dict[str, Any]]:
        response.read()
        raise ConnectionResetError("daemon went away mid-stream")

    monkeypatch.setattr(client_module, "_sse_frames", broken_stream)
    relay = client_module.HttpRelay(
        host, port, "secret-token", handler.cert_pem, reconnect_window_s=30.0
    )
    started = time.monotonic()

    with pytest.raises(client_module.DaemonUnavailableError):
        relay.forward(_TOOLS_LIST)

    assert time.monotonic() - started < 5.0
    assert len(handler.requests) == 1, "the request must have been sent exactly once"
