"""``hive serve`` — the Phase C single-owner daemon (ADR-011).

One long-lived process owns the vault git working tree and all SQLite trackers
and serves thin clients over loopback Streamable-HTTP gated by a per-daemon
bearer token. The transport + owner-only token model is the one validated
cross-OS by ``specs/HIVE-118-phase-c-daemon-model/spike/transport_spike.py``.

This first slice provides the entrypoint: write an owner-only token + port
state file, then run ``create_server()`` over HTTP with a token verifier. The
resilience/observability pillar (ADR-011 §4) lands in later slices.
"""

from __future__ import annotations

import os
import secrets
import socket
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from hive.config import settings

if TYPE_CHECKING:
    from fastmcp.server.auth import AuthProvider

DEFAULT_HOST = "127.0.0.1"
MCP_PATH = "/mcp"
TOKEN_FILENAME = "daemon.token"
PORT_FILENAME = "daemon.port"


def daemon_state_dir() -> Path:
    """Directory holding the token + port state files (beside the SQLite DBs)."""
    return Path(settings.db_path).parent


def token_file_path() -> Path:
    return daemon_state_dir() / TOKEN_FILENAME


def port_file_path() -> Path:
    return daemon_state_dir() / PORT_FILENAME


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((DEFAULT_HOST, 0))
        return int(s.getsockname()[1])


def write_owner_only(path: Path, content: str) -> None:
    """Write *content* to *path* so only the current user can read it.

    POSIX: mode ``0600``. Windows: strip inherited ACEs and grant the current
    user only via ``icacls`` (a bare ``chmod`` cannot express an owner-only ACL
    on NTFS). Both forms are validated by the transport spike on each OS.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if os.name == "nt":
        user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
        subprocess.run(  # noqa: S603,S607 — fixed args, owner derived from env
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:(F)"],
            check=False, capture_output=True, text=True,
        )
    else:
        path.chmod(0o600)


def _token_verifier(token: str) -> AuthProvider:
    """A static bearer-token verifier admitting exactly *token* (ADR-011 §2)."""
    from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

    return StaticTokenVerifier({token: {"client_id": "hive-daemon", "scopes": []}})


def run_serve(host: str = DEFAULT_HOST, port: int = 0) -> None:
    """Run hive as a single-owner daemon over loopback Streamable-HTTP + token.

    Generates a per-daemon token, publishes it (owner-only) and the chosen port
    to the state dir, then serves the real ``create_server()`` instance. A
    ``port`` of 0 picks a free loopback port.
    """
    from hive.server import create_server

    resolved_port = port or _free_port()
    token = secrets.token_urlsafe(32)
    # Each startup republishes a fresh token + port, overwriting any state a
    # prior daemon left behind. We deliberately do NOT clean these up on stop:
    # uvicorn's SIGTERM handling exits the process via the signal (rc -15),
    # bypassing `finally`/`atexit`, and SIGTERM is exactly how systemd / kill
    # stop the daemon. Stale state is benign — the client's TCP liveness probe
    # falls back (`daemon_unreachable`) and a restart overwrites it. See ADR-011
    # startup self-heal for the crash path.
    write_owner_only(token_file_path(), token)
    write_owner_only(port_file_path(), str(resolved_port))

    server = create_server(auth=_token_verifier(token))
    server.run(
        transport="http", host=host, port=resolved_port,
        path=MCP_PATH, show_banner=False,
    )
