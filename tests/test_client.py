"""Tests for the stdlib-only stable-endpoint adapter (HIVE-437)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator


class _McpHandler(BaseHTTPRequestHandler):
    requests: list[dict[str, Any]] = []
    reject_session_once = False

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


@pytest.fixture
def mcp_http_server() -> Iterator[tuple[str, int, type[_McpHandler]]]:
    _McpHandler.requests = []
    _McpHandler.reject_session_once = False
    server = ThreadingHTTPServer(("127.0.0.1", 0), _McpHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield str(host), int(port), _McpHandler
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_relay_preserves_json_session_protocol_and_sse(
    mcp_http_server: tuple[str, int, type[_McpHandler]],
) -> None:
    from hive._client import HttpRelay

    host, port, handler = mcp_http_server
    relay = HttpRelay(host, port, "secret-token")
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
    relay = HttpRelay(host, port, "secret-token")
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
    relay = HttpRelay(host, port, "secret-token")
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


def test_relay_connection_failure_is_explicit_and_redacts_token() -> None:
    from hive._client import ClientError, HttpRelay

    relay = HttpRelay("127.0.0.1", 1, "must-never-leak")

    with pytest.raises(ClientError, match="daemon unavailable") as excinfo:
        relay.forward({"jsonrpc": "2.0", "id": 1, "method": "initialize"})

    assert "must-never-leak" not in str(excinfo.value)


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


def test_client_initialize_response_arrives_within_one_second(
    mcp_http_server: tuple[str, int, type[_McpHandler]],
    tmp_path,
) -> None:
    from hive._daemon import _enforce_owner_only

    host, port, _handler = mcp_http_server
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

    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, "-m", "hive.cli", "client", "--host", host],
        input=message + "\n",
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=1.0,
    )
    elapsed = time.monotonic() - started

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["id"] == 1
    assert elapsed < 1.0


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
    assert not (tmp_path / "10_projects").exists()
