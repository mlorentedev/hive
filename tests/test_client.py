"""Tests for the stdlib-only stable-endpoint adapter (HIVE-437)."""

from __future__ import annotations

import http.client
import json
import os
import queue
import shutil
import socket
import ssl
import statistics
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

    from hive._identity import Identity


class _McpHandler(BaseHTTPRequestHandler):
    requests: list[dict[str, Any]] = []
    reject_session_once = False
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
                session_id="session-123",
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


def _server_context(identity: Identity) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(identity.cert_path, identity.key_path)
    return context


@pytest.fixture
def mcp_http_server(owner_identity: Identity) -> Iterator[tuple[str, int, type[_McpHandler]]]:
    _McpHandler.requests = []
    _McpHandler.reject_session_once = False
    _McpHandler.cert_pem = owner_identity.cert_path.read_text(encoding="ascii")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _McpHandler)
    server.socket = _server_context(owner_identity).wrap_socket(server.socket, server_side=True)
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


class _Impostor:
    """A listener on the stable port that is not the daemon, recording every byte.

    ``identity=None`` is a plaintext HTTP listener. Otherwise it terminates TLS
    with a certificate the client never pinned and records what it decrypts.
    """

    def __init__(self, identity: Identity | None) -> None:
        self._context = None if identity is None else _server_context(identity)
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.port = int(self.listener.getsockname()[1])
        self.accepted = threading.Event()
        self.raw = bytearray()
        self.decrypted = bytearray()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        self.listener.settimeout(10)
        try:
            conn, _ = self.listener.accept()
        except OSError:
            return
        self.accepted.set()
        conn.settimeout(2)
        with conn:
            if self._context is None:
                self._capture_plaintext(conn)
            else:
                self._capture_tls(conn)

    def _capture_plaintext(self, conn: socket.socket) -> None:
        try:
            self.raw += conn.recv(65536)
            conn.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
            while chunk := conn.recv(65536):
                self.raw += chunk
        except OSError:
            return

    def _capture_tls(self, conn: socket.socket) -> None:
        assert self._context is not None
        try:
            self.raw += conn.recv(5, socket.MSG_PEEK)
            with self._context.wrap_socket(conn, server_side=True) as tls:
                while chunk := tls.recv(65536):
                    self.decrypted += chunk
        except OSError:
            return

    def wait(self) -> None:
        self._thread.join(timeout=10)
        self.listener.close()


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
    impostor = _Impostor(impostor_identity)
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

    # Positive control: the relay really dialled the impostor and spoke TLS.
    assert impostor.accepted.is_set()
    assert impostor.raw[:1] == b"\x16"
    captured = bytes(impostor.raw + impostor.decrypted)
    assert b"Authorization" not in captured
    assert b"POST" not in captured
    assert token.encode() not in captured
    assert impostor.decrypted == b""
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


def test_client_unreachable_daemon_exits_with_a_json_rpc_error(
    owner_identity: Identity,
    tmp_path: Path,
) -> None:
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
    assert json.loads(result.stdout)["error"]["code"] == -32000
    assert "daemon unavailable" in result.stderr
    assert token not in result.stderr + result.stdout
