"""Shared helpers for the HIVE-118 transport/daemon spikes (Linux + Windows).

Centralises the daemon-spawn / readiness / client / cleanup / report boilerplate
so every spike inherits the Windows-robust behaviour learned the hard way:
report BEFORE cleanup, and kill+wait the child before unlinking its log (Windows
holds the handle until the process fully exits). Each spike defines its own
``_serve`` and tools; this module owns the plumbing.
"""

from __future__ import annotations

import contextlib
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

HOST = "127.0.0.1"
PATH = "/mcp"
TOKEN_ENV = "HIVE_SPIKE_TOKEN"
DB_ENV = "HIVE_SPIKE_DB"


def new_token() -> str:
    return secrets.token_urlsafe(32)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return int(s.getsockname()[1])


def url_for(port: int) -> str:
    return f"http://{HOST}:{port}{PATH}"


def wait_ready(port: int, deadline_s: float = 20.0) -> bool:
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        with contextlib.suppress(OSError), socket.create_connection(
            (HOST, port), timeout=0.5,
        ):
            return True
        time.sleep(0.1)
    return False


# ── daemon side ──────────────────────────────────────────────────────────


def build_server(name: str, token: str):  # noqa: ANN201 — fastmcp FastMCP
    """A FastMCP server gated by a per-daemon bearer token (ADR-011 §2)."""
    from fastmcp import FastMCP
    from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

    verifier = StaticTokenVerifier({token: {"client_id": name, "scopes": []}})
    return FastMCP(name, auth=verifier)


def serve(mcp, port: int) -> None:  # noqa: ANN001
    mcp.run(transport="http", host=HOST, port=port, path=PATH, show_banner=False)


def spawn_daemon(
    script: str, port: int, env_extra: Mapping[str, str] | None = None,
) -> tuple[subprocess.Popen[bytes], Path]:
    """Launch ``python <script> --serve <port>`` with output captured to a log.

    The parent closes its log handle immediately after spawn; the child keeps
    its inherited handle, so the parent never blocks the file's later deletion.
    """
    child_log = Path(tempfile.gettempdir()) / f"hive-spike-{port}-{secrets.token_hex(3)}.log"
    env = {**os.environ, **(env_extra or {})}
    log = child_log.open("wb")
    try:
        proc = subprocess.Popen(  # noqa: S603 — fixed argv, spike only
            [sys.executable, str(script), "--serve", str(port)],
            env=env, stdout=log, stderr=log,
        )
    finally:
        log.close()
    return proc, child_log


def log_tail(child_log: Path, n: int = 800) -> str:
    with contextlib.suppress(OSError):
        return child_log.read_text(errors="replace")[-n:]
    return "(no daemon log)"


def cleanup(proc: subprocess.Popen[bytes] | None, *paths: str | Path) -> None:
    """Best-effort teardown — never fatal. Kill+wait before unlinking (Windows)."""
    if proc is not None:
        with contextlib.suppress(Exception):
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)
    for p in paths:
        with contextlib.suppress(OSError):
            Path(p).unlink(missing_ok=True)


# ── client side ──────────────────────────────────────────────────────────


def make_client(  # noqa: ANN201 — fastmcp Client
    url: str, token: str | None = None, *, raw_headers: dict[str, str] | None = None,
):
    """A FastMCP client over Streamable-HTTP. ``raw_headers`` overrides auth."""
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    if raw_headers is not None:
        headers = raw_headers
    elif token is not None:
        headers = {"Authorization": f"Bearer {token}"}
    else:
        headers = {}
    return Client(StreamableHttpTransport(url, headers=headers))


# ── report ───────────────────────────────────────────────────────────────


def report(title: str, checks: list[tuple[str, bool, str]]) -> int:
    """Print the result table (BEFORE any cleanup) and return an exit code."""
    label = "Windows" if os.name == "nt" else "Linux/POSIX"
    print(f"\n{title} ({label})\n")
    if not checks:
        print("  (no checks ran — the spike errored before producing results)\n")
        return 1
    width = max(len(name) for name, _, _ in checks)
    all_ok = True
    for name, ok, detail in checks:
        all_ok &= ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {name.ljust(width)}  {detail}")
    print(f"\n{'ALL PASS' if all_ok else 'FAILED'} — "
          f"{sum(ok for _, ok, _ in checks)}/{len(checks)} checks\n")
    return 0 if all_ok else 1
