"""HIVE-118 Phase C — client auto-reconnect spike (slice 3, closes M1).

Proves the mid-session recovery mechanism BEFORE wiring it into the shim: a
reconnecting ``client_factory`` that re-reads the daemon's published
port+token on EVERY forwarded call follows a RESTARTED daemon to its new port
and new bearer token, with no shim restart. FastMCP's proxy calls the factory
per request (``ProxyTool._get_client``), so re-reading state there is the seam.

This closes M1 (the one-shot startup mode decision) and composes with slice
1.2: killing the daemon frees its singleton ``daemon.lock`` so the replacement
reacquires it and self-heals the stale ``.git/index.lock`` before serving.

Checks (against the REAL ``hive serve`` daemon):

1. The factory talks to daemon A.
2. A is killed and B starts on a DIFFERENT port with a fresh token -> the
   factory re-reads state and talks to B (the reconnect).
3. No daemon -> the factory yields no remote client (the shim would fall back).

Run::

    uv run python specs/HIVE-118-phase-c-daemon-model/spike/reconnect_spike.py

Exit 0 = every check passed.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import _spikelib as lib

HOST = "127.0.0.1"
MARKER = "RECONNECT-MARKER"


def _spawn_real_daemon(env: dict[str, str], port: int) -> subprocess.Popen[bytes]:
    return subprocess.Popen(  # noqa: S603 — fixed argv, spike only
        [sys.executable, "-m", "hive.server", "serve", "--port", str(port)],
        env=env,
    )


def _kill(proc: subprocess.Popen[bytes] | None) -> None:
    if proc is None:
        return
    with contextlib.suppress(Exception):
        proc.kill()
        proc.wait(timeout=5)


def _read_state(state_dir: Path) -> tuple[int, str] | None:
    try:
        port = int((state_dir / "daemon.port").read_text(encoding="utf-8").strip())
        token = (state_dir / "daemon.token").read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return None
    return (port, token) if port > 0 and token else None


def _reachable(port: int, timeout: float = 0.5) -> bool:
    with contextlib.suppress(OSError), socket.create_connection(
        (HOST, port), timeout=timeout,
    ):
        return True
    return False


def _make_remote_client(state_dir: Path):  # noqa: ANN201 — fastmcp Client | None
    """The reconnecting factory's remote branch: re-read state, fresh client.

    Returns a per-call ``Client`` bound to the CURRENT published port+token, or
    ``None`` when no daemon is reachable (the shim's cue to serve in-process).
    """
    st = _read_state(state_dir)
    if st is None or not _reachable(st[0]):
        return None
    port, token = st
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    return Client(
        StreamableHttpTransport(
            f"http://{HOST}:{port}/mcp",
            headers={"Authorization": f"Bearer {token}"},
        ),
        init_timeout=5,
    )


async def _query(client) -> str:  # noqa: ANN001
    async with client:
        r = await client.call_tool(
            "vault_query", {"project": "demo", "section": "context"},
        )
    return str(getattr(r, "data", r))


def _wait_ready_published(state_dir: Path, deadline_s: float = 20.0) -> int | None:
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        st = _read_state(state_dir)
        if st and _reachable(st[0]):
            return st[0]
        time.sleep(0.1)
    return None


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="hive-reconnect-"))
    vault = tmp / "vault"
    (vault / "10_projects" / "demo").mkdir(parents=True)
    (vault / "10_projects" / "demo" / "00-context.md").write_text(
        f"---\ntitle: demo\n---\n{MARKER}\n", encoding="utf-8",
    )
    env = {
        **os.environ,
        "HIVE_DB_PATH": str(tmp / "worker.db"),
        "HIVE_RELEVANCE_DB_PATH": str(tmp / "relevance.db"),
        "HIVE_LESSON_DB_PATH": str(tmp / "lesson.db"),
        "HIVE_LOG_PATH": str(tmp / "hive.log"),
        "VAULT_PATH": str(vault),
    }
    state_dir = tmp  # daemon_state_dir() == db_path.parent == tmp
    checks: list[tuple[str, bool, str]] = []
    a: subprocess.Popen[bytes] | None = None
    b: subprocess.Popen[bytes] | None = None
    try:
        port_a = lib.free_port()
        port_b = lib.free_port()  # reserved while A is alive => distinct from A
        a = _spawn_real_daemon(env, port_a)
        text_a = ""
        if lib.wait_ready(port_a):
            client = _make_remote_client(state_dir)
            text_a = asyncio.run(_query(client)) if client else "<no client>"
        checks.append((
            "factory talks to daemon A", MARKER in text_a,
            f"port_a={port_a} text={text_a[:40]!r}",
        ))

        _kill(a)
        a = None
        b = _spawn_real_daemon(env, port_b)  # restart: new port + new token
        published_b = _wait_ready_published(state_dir)
        client_b = _make_remote_client(state_dir)
        text_b = asyncio.run(_query(client_b)) if client_b else "<no client>"
        checks.append((
            "factory follows restart to B (new port + new token)",
            published_b == port_b and port_b != port_a and MARKER in text_b,
            f"port_a={port_a} port_b={port_b} published={published_b} "
            f"text={text_b[:40]!r}",
        ))

        _kill(b)
        b = None
        time.sleep(0.5)
        no_client = _make_remote_client(state_dir)
        checks.append((
            "no daemon -> factory yields no remote client (shim falls back)",
            no_client is None, f"client={no_client!r}",
        ))
    finally:
        _kill(a)
        _kill(b)
        code = lib.report("HIVE-118 client auto-reconnect spike", checks)
    return code


if __name__ == "__main__":
    sys.exit(main())
