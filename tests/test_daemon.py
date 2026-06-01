"""Integration tests for the `hive serve` daemon (HIVE-118 / ADR-011).

Black-box: spawns the real daemon over the token-gated loopback Streamable-HTTP
transport (the path validated by `specs/.../spike/transport_spike.py`) and
drives it with a thin `fastmcp.Client` — the production analogue of the spike.
"""

from __future__ import annotations

import asyncio
import os
import socket
import stat
import subprocess
import sys
import time
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

HOST = "127.0.0.1"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return int(s.getsockname()[1])


def _wait_ready(port: int, deadline_s: float = 20.0) -> bool:
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        try:
            with socket.create_connection((HOST, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


async def _list_tools(url: str, token: str) -> list[str]:
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    transport = StreamableHttpTransport(
        url, headers={"Authorization": f"Bearer {token}"},
    )
    async with Client(transport) as client:
        return [t.name for t in await client.list_tools()]


# ── client shim (stdio) helpers ──────────────────────────────────────────

_DEMO_MARKER = "DAEMON-FORWARD-MARKER"


def _seed_demo_project(vault: Path) -> dict[str, str]:
    """Create a `demo` project with known content; return vault_query args."""
    ctx = vault / "10_projects" / "demo" / "00-context.md"
    ctx.parent.mkdir(parents=True, exist_ok=True)
    ctx.write_text(f"---\ntitle: demo\n---\n{_DEMO_MARKER}\n", encoding="utf-8")
    return {"project": "demo", "section": "context"}


async def _drive_shim(
    env: dict[str, str], tool: str, args: dict[str, str],
) -> tuple[list[str], str]:
    """Spawn the `hive client` stdio shim and drive it with a fastmcp client.

    Returns (tool names the shim exposes, text of the forwarded tool call).
    """
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport

    transport = StdioTransport(
        command=sys.executable, args=["-m", "hive.server", "client"], env=env,
    )
    async with Client(transport) as client:
        tools = [t.name for t in await client.list_tools()]
        result = await client.call_tool(tool, args)
    return tools, str(getattr(result, "data", result))


def _client_mode(state_dir: Path) -> str:
    """Return the `hive.client.mode=...` value the shim logged, or ''."""
    marker = "hive.client.mode="
    for log_file in sorted(state_dir.glob("hive-*.log")):
        for line in log_file.read_text(errors="replace").splitlines():
            if marker in line:
                return line.split(marker, 1)[1].strip()
    return ""


@pytest.fixture
def daemon_env(tmp_path: Path) -> tuple[dict[str, str], Path]:
    """Env pointing every DB + the vault + daemon state dir at a temp dir."""
    vault = tmp_path / "vault"
    (vault / "10_projects").mkdir(parents=True)
    env = {
        **os.environ,
        "HIVE_DB_PATH": str(tmp_path / "worker.db"),
        "HIVE_RELEVANCE_DB_PATH": str(tmp_path / "relevance.db"),
        "HIVE_LESSON_DB_PATH": str(tmp_path / "lesson.db"),
        "HIVE_LOG_PATH": str(tmp_path / "hive.log"),
        "VAULT_PATH": str(vault),
    }
    return env, tmp_path


def _spawn_daemon(env: dict[str, str], port: int) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, "-m", "hive.server", "serve", "--port", str(port)],
        env=env,
    )


def test_hive_serve_answers_tools_list(daemon_env: tuple[dict[str, str], Path]) -> None:
    """The daemon binds loopback, writes an owner-only token, and a
    token-authenticated client gets the full hive tool list."""
    env, state_dir = daemon_env
    port = _free_port()
    proc = _spawn_daemon(env, port)
    try:
        assert _wait_ready(port), "daemon did not bind its loopback port"
        token = (state_dir / "daemon.token").read_text(encoding="utf-8").strip()
        assert token, "daemon did not write a token"

        tools = asyncio.run(_list_tools(f"http://{HOST}:{port}/mcp", token))
        assert "vault_query" in tools
        assert "session_briefing" in tools

        if os.name != "nt":
            mode = stat.S_IMODE((state_dir / "daemon.token").stat().st_mode)
            assert mode == 0o600, f"token file not owner-only: {oct(mode)}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_hive_serve_rejects_bad_token(daemon_env: tuple[dict[str, str], Path]) -> None:
    """A request without the matching token is refused — the bare loopback
    port is not open."""
    env, _ = daemon_env
    port = _free_port()
    proc = _spawn_daemon(env, port)
    try:
        assert _wait_ready(port)
        with pytest.raises(Exception):  # noqa: B017, PT011 — any auth refusal
            asyncio.run(_list_tools(f"http://{HOST}:{port}/mcp", "not-the-token"))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_client_forwards_to_daemon(daemon_env: tuple[dict[str, str], Path]) -> None:
    """With a daemon running, the thin stdio shim connects over the token-gated
    transport and forwards a `vault_query` to it — reporting `daemon` mode."""
    env, state_dir = daemon_env
    args = _seed_demo_project(state_dir / "vault")
    port = _free_port()
    daemon = _spawn_daemon(env, port)
    try:
        assert _wait_ready(port), "daemon did not bind its loopback port"

        tools, text = asyncio.run(_drive_shim(env, "vault_query", args))
        assert "vault_query" in tools
        assert "session_briefing" in tools
        assert _DEMO_MARKER in text, f"forwarded query did not round-trip: {text!r}"

        mode = _client_mode(state_dir)
        assert mode.startswith("daemon"), f"shim did not report daemon mode: {mode!r}"
    finally:
        daemon.terminate()
        try:
            daemon.wait(timeout=5)
        except subprocess.TimeoutExpired:
            daemon.kill()


def test_client_falls_back_without_daemon(
    daemon_env: tuple[dict[str, str], Path],
) -> None:
    """With no daemon (no state files), the shim serves the query in-process and
    flags degraded (`fallback`) mode — today's behavior, transparently."""
    env, state_dir = daemon_env
    args = _seed_demo_project(state_dir / "vault")

    tools, text = asyncio.run(_drive_shim(env, "vault_query", args))
    assert "vault_query" in tools
    assert _DEMO_MARKER in text, f"in-process query failed: {text!r}"

    mode = _client_mode(state_dir)
    assert mode.startswith("fallback"), f"expected fallback mode, got {mode!r}"
    assert "no_daemon_state" in mode, f"unexpected fallback reason: {mode!r}"


def test_client_falls_back_on_stale_state(
    daemon_env: tuple[dict[str, str], Path],
) -> None:
    """A crashed daemon leaves stale port/token files but a dead port. The shim
    probes liveness, finds nothing listening, and falls back rather than hang."""
    env, state_dir = daemon_env
    args = _seed_demo_project(state_dir / "vault")
    dead_port = _free_port()  # bound + closed → nothing is listening
    (state_dir / "daemon.port").write_text(str(dead_port), encoding="utf-8")
    (state_dir / "daemon.token").write_text("stale-token", encoding="utf-8")

    tools, text = asyncio.run(_drive_shim(env, "vault_query", args))
    assert "vault_query" in tools
    assert _DEMO_MARKER in text, f"in-process query failed: {text!r}"

    mode = _client_mode(state_dir)
    assert mode.startswith("fallback"), f"expected fallback mode, got {mode!r}"
    assert "daemon_unreachable" in mode, f"unexpected fallback reason: {mode!r}"
