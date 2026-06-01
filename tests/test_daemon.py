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
) -> dict[str, object]:
    """Spawn the `hive client` stdio shim and drive it with a fastmcp client.

    Returns the tool / resource / prompt names the shim exposes plus the text
    of the forwarded tool call — enough to assert that the proxy forwards the
    whole MCP surface, not just `tools/list`.
    """
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport

    transport = StdioTransport(
        command=sys.executable, args=["-m", "hive.server", "client"], env=env,
    )
    async with Client(transport) as client:
        tools = [t.name for t in await client.list_tools()]
        resources = [str(r.uri) for r in await client.list_resources()]
        prompts = [p.name for p in await client.list_prompts()]
        result = await client.call_tool(tool, args)
    return {
        "tools": tools,
        "resources": resources,
        "prompts": prompts,
        "text": str(getattr(result, "data", result)),
    }


def _client_mode(state_dir: Path) -> str:
    """Return the `hive.client.mode=...` value the shim logged, or ''."""
    marker = "hive.client.mode="
    for log_file in sorted(state_dir.glob("hive-*.log")):
        for line in log_file.read_text(errors="replace").splitlines():
            if marker in line:
                return line.split(marker, 1)[1].strip()
    return ""


# ── multi-client (slice 3) helpers ────────────────────────────────────────


def _git_init_vault(vault: Path) -> None:
    """Make *vault* a git repo with one commit, so vault_write can auto-commit."""
    for cmd in (
        ["git", "init"],
        ["git", "config", "user.email", "test@test.com"],
        ["git", "config", "user.name", "Test"],
        ["git", "add", "."],
        ["git", "commit", "-m", "init"],
    ):
        subprocess.run(cmd, cwd=vault, capture_output=True, check=True)


async def _client_appends(env: dict[str, str], markers: list[str]) -> int:
    """One shim session: append each marker to demo/context, count completions."""
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport

    transport = StdioTransport(
        command=sys.executable, args=["-m", "hive.server", "client"], env=env,
    )
    done = 0
    async with Client(transport) as client:
        for marker in markers:
            await client.call_tool(
                "vault_write",
                {
                    "project": "demo",
                    "section": "context",
                    "operation": "append",
                    "content": f"\n{marker}\n",
                },
            )
            done += 1
    return done


async def _two_clients_append(
    env: dict[str, str], markers_a: list[str], markers_b: list[str],
) -> list[int]:
    """Two shim sessions append concurrently against the same daemon."""
    return list(
        await asyncio.gather(
            _client_appends(env, markers_a),
            _client_appends(env, markers_b),
        ),
    )


# ── observability (slice 4) helpers ───────────────────────────────────────


async def _session_calls(url: str, token: str, tool: str, args: dict[str, str],
                         times: int) -> None:
    """Open one MCP session, call *tool* *times*, then disconnect."""
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    transport = StreamableHttpTransport(
        url, headers={"Authorization": f"Bearer {token}"},
    )
    async with Client(transport) as client:
        for _ in range(times):
            await client.call_tool(tool, args)


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
    transport and forwards the full MCP surface to it — reporting `daemon` mode,
    without leaking the bearer token into the logs."""
    env, state_dir = daemon_env
    args = _seed_demo_project(state_dir / "vault")
    port = _free_port()
    daemon = _spawn_daemon(env, port)
    try:
        assert _wait_ready(port), "daemon did not bind its loopback port"
        token = (state_dir / "daemon.token").read_text(encoding="utf-8").strip()

        surface = asyncio.run(_drive_shim(env, "vault_query", args))
        assert "vault_query" in surface["tools"]
        assert "session_briefing" in surface["tools"]
        assert _DEMO_MARKER in surface["text"], (
            f"forwarded query did not round-trip: {surface['text']!r}"
        )
        # The proxy forwards resources + prompts too, not just tools (L2).
        assert any(str(uri).startswith("hive://") for uri in surface["resources"]), (
            f"proxy did not forward resources: {surface['resources']!r}"
        )
        assert "retrospective" in surface["prompts"], (
            f"proxy did not forward prompts: {surface['prompts']!r}"
        )

        mode = _client_mode(state_dir)
        assert mode.startswith("daemon"), f"shim did not report daemon mode: {mode!r}"

        # The bearer token must never reach disk in any hive log (L4).
        logs = "".join(
            f.read_text(errors="replace") for f in state_dir.glob("hive-*.log")
        )
        assert token and token not in logs, "bearer token leaked into a hive log"
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

    surface = asyncio.run(_drive_shim(env, "vault_query", args))
    assert "vault_query" in surface["tools"]
    assert _DEMO_MARKER in surface["text"], f"in-process query failed: {surface['text']!r}"

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
    # Bound but NOT listening, held open: connects are refused deterministically
    # (no accept queue => RST) and no other process can grab the port mid-test.
    dead = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    dead.bind((HOST, 0))
    try:
        (state_dir / "daemon.port").write_text(
            str(dead.getsockname()[1]), encoding="utf-8",
        )
        (state_dir / "daemon.token").write_text("stale-token", encoding="utf-8")

        surface = asyncio.run(_drive_shim(env, "vault_query", args))
        assert "vault_query" in surface["tools"]
        assert _DEMO_MARKER in surface["text"], (
            f"in-process query failed: {surface['text']!r}"
        )

        mode = _client_mode(state_dir)
        assert mode.startswith("fallback"), f"expected fallback mode, got {mode!r}"
        assert "daemon_unreachable" in mode, f"unexpected fallback reason: {mode!r}"
    finally:
        dead.close()


def test_two_clients_share_one_daemon(
    daemon_env: tuple[dict[str, str], Path],
) -> None:
    """Two concurrent client shims write to the vault through ONE daemon: every
    write lands (the single owner serialized them — no lost updates to the same
    file) and the daemon owns the resulting git commits (single-owner AC)."""
    env, state_dir = daemon_env
    vault = state_dir / "vault"
    _seed_demo_project(vault)  # 10_projects/demo/00-context.md
    _git_init_vault(vault)

    n_each = 4
    markers_a = [f"MARKER-A-{i}" for i in range(n_each)]
    markers_b = [f"MARKER-B-{i}" for i in range(n_each)]

    port = _free_port()
    daemon = _spawn_daemon(env, port)
    try:
        assert _wait_ready(port), "daemon did not bind its loopback port"

        done = asyncio.run(_two_clients_append(env, markers_a, markers_b))
        assert done == [n_each, n_each], f"a client session aborted early: {done}"

        # No lost writes: every marker from both concurrent sessions survived,
        # proving the single owner serialized the read-append-write cycles.
        context = vault / "10_projects" / "demo" / "00-context.md"
        content = context.read_text(encoding="utf-8")
        missing = [m for m in markers_a + markers_b if m not in content]
        assert not missing, f"lost writes — single-owner serialization failed: {missing}"

        # The daemon owns git: each append produced exactly one commit on top of
        # `init` (init + 2*n_each). A different count would mean a dropped commit
        # or coalesced/lost writes — i.e. broken single-owner serialization.
        log = subprocess.run(
            ["git", "log", "--oneline"],
            cwd=vault, capture_output=True, text=True, check=True,
        ).stdout
        commit_count = len(log.splitlines())
        assert commit_count == 1 + 2 * n_each, (
            f"expected {1 + 2 * n_each} commits (init + per-append), got "
            f"{commit_count}: {log!r}"
        )
    finally:
        daemon.terminate()
        try:
            daemon.wait(timeout=10)
        except subprocess.TimeoutExpired:
            daemon.kill()


def test_status_aggregates_across_sessions(
    daemon_env: tuple[dict[str, str], Path],
) -> None:
    """The daemon's /status endpoint reports per-tool metrics aggregated across
    sessions and surviving their disconnect, and is gated by the bearer token."""
    import httpx

    env, state_dir = daemon_env
    args = _seed_demo_project(state_dir / "vault")
    port = _free_port()
    daemon = _spawn_daemon(env, port)
    k = 3
    try:
        assert _wait_ready(port), "daemon did not bind its loopback port"
        token = (state_dir / "daemon.token").read_text(encoding="utf-8").strip()
        mcp_url = f"http://{HOST}:{port}/mcp"

        # Two sequential sessions: each opens, calls vault_query k times, then
        # fully disconnects before the next starts. If /status still counts both,
        # the metrics survived the disconnects and aggregate across sessions.
        asyncio.run(_session_calls(mcp_url, token, "vault_query", args, k))
        asyncio.run(_session_calls(mcp_url, token, "vault_query", args, k))

        status_url = f"http://{HOST}:{port}/status"
        resp = httpx.get(status_url, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, f"/status not served: {resp.status_code}"
        payload = resp.json()

        assert payload["tools"]["vault_query"]["calls"] == 2 * k, (
            f"metrics did not aggregate across sessions: {payload['tools']!r}"
        )
        assert payload["tools"]["vault_query"]["errors"] == 0
        assert payload["sessions_started"] >= 2, (
            f"expected >=2 sessions, got {payload['sessions_started']}"
        )
        assert payload["version"]
        assert payload["uptime_s"] >= 0

        # The bare loopback port must stay token-gated (ADR-011 §2).
        bad = httpx.get(status_url)
        assert bad.status_code == 401, f"/status not token-gated: {bad.status_code}"
    finally:
        daemon.terminate()
        try:
            daemon.wait(timeout=10)
        except subprocess.TimeoutExpired:
            daemon.kill()
