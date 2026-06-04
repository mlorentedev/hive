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

import httpx
import pytest

if TYPE_CHECKING:
    from collections.abc import Callable
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
        url,
        headers={"Authorization": f"Bearer {token}"},
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
    env: dict[str, str],
    tool: str,
    args: dict[str, str],
) -> dict[str, object]:
    """Spawn the `hive client` stdio shim and drive it with a fastmcp client.

    Returns the tool / resource / prompt names the shim exposes plus the text
    of the forwarded tool call — enough to assert that the proxy forwards the
    whole MCP surface, not just `tools/list`.
    """
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport

    transport = StdioTransport(
        command=sys.executable,
        args=["-m", "hive.server", "client"],
        env=env,
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


def _client_modes(state_dir: Path) -> list[str]:
    """Return every `hive.client.mode=...` value the shim logged, in order."""
    marker = "hive.client.mode="
    modes: list[str] = []
    for log_file in sorted(state_dir.glob("hive-*.log")):
        for line in log_file.read_text(errors="replace").splitlines():
            if marker in line:
                modes.append(line.split(marker, 1)[1].strip())
    return modes


def _client_mode(state_dir: Path) -> str:
    """Return the first `hive.client.mode=...` value the shim logged, or ''."""
    modes = _client_modes(state_dir)
    return modes[0] if modes else ""


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
        command=sys.executable,
        args=["-m", "hive.server", "client"],
        env=env,
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
    env: dict[str, str],
    markers_a: list[str],
    markers_b: list[str],
) -> list[int]:
    """Two shim sessions append concurrently against the same daemon."""
    return list(
        await asyncio.gather(
            _client_appends(env, markers_a),
            _client_appends(env, markers_b),
        ),
    )


# ── auto-reconnect (slice 3, closes M1) helpers ───────────────────────────


def _published_port(state_dir: Path) -> int:
    """The port the daemon last published, or 0 if unreadable."""
    try:
        return int((state_dir / "daemon.port").read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def _wait_published(state_dir: Path, port: int, deadline_s: float = 20.0) -> bool:
    """Wait until the daemon has published *port* AND is accepting connections."""
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        if _published_port(state_dir) == port and _wait_ready(port, deadline_s=0.5):
            return True
        time.sleep(0.1)
    return False


async def _reconnect_then_retry(
    env: dict[str, str],
    key: str,
    content: str,
    restart: Callable[[], None],
) -> None:
    """One shim session straddling a daemon restart.

    Append *content* under idempotency *key* against daemon A, run *restart*
    (kill A, bring up B on a new port+token), then retry the SAME keyed write.
    The second call must follow the shim to B and dedupe — exercising both the
    reconnect (per-call state re-read) and at-most-once (idempotency_key).
    """
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport

    args = {
        "project": "demo",
        "section": "context",
        "operation": "append",
        "content": content,
        "idempotency_key": key,
    }
    transport = StdioTransport(
        command=sys.executable,
        args=["-m", "hive.server", "client"],
        env=env,
    )
    async with Client(transport) as client:
        await client.call_tool("vault_write", args)  # lands on daemon A
        await asyncio.to_thread(restart)  # A dies, B takes over
        await client.call_tool("vault_write", args)  # must follow to daemon B


async def _query_across_kill(
    env: dict[str, str],
    args: dict[str, str],
    kill: Callable[[], None],
) -> tuple[str, str]:
    """One shim session: vault_query, kill the daemon, vault_query again.

    Returns both responses. The second must succeed in-process — proving the
    shim degrades mid-session instead of erroring every call once the daemon it
    was proxying to dies without returning.
    """
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport

    transport = StdioTransport(
        command=sys.executable,
        args=["-m", "hive.server", "client"],
        env=env,
    )
    async with Client(transport) as client:
        first = await client.call_tool("vault_query", args)
        await asyncio.to_thread(kill)
        second = await client.call_tool("vault_query", args)
    return (
        str(getattr(first, "data", first)),
        str(getattr(second, "data", second)),
    )


# ── observability (slice 4) helpers ───────────────────────────────────────


async def _session_calls(url: str, token: str, tool: str, args: dict[str, str], times: int) -> None:
    """Open one MCP session, call *tool* *times*, then disconnect."""
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    transport = StreamableHttpTransport(
        url,
        headers={"Authorization": f"Bearer {token}"},
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
        # A bad bearer token is refused at the transport with HTTP 401 — not a
        # generic failure. Asserting the status distinguishes auth refusal from
        # an unrelated server error (which would surface a different code).
        with pytest.raises(httpx.HTTPStatusError, match="401"):
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
        logs = "".join(f.read_text(errors="replace") for f in state_dir.glob("hive-*.log"))
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
            str(dead.getsockname()[1]),
            encoding="utf-8",
        )
        (state_dir / "daemon.token").write_text("stale-token", encoding="utf-8")

        surface = asyncio.run(_drive_shim(env, "vault_query", args))
        assert "vault_query" in surface["tools"]
        assert _DEMO_MARKER in surface["text"], f"in-process query failed: {surface['text']!r}"

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
            cwd=vault,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        commit_count = len(log.splitlines())
        assert commit_count == 1 + 2 * n_each, (
            f"expected {1 + 2 * n_each} commits (init + per-append), got {commit_count}: {log!r}"
        )
    finally:
        daemon.terminate()
        try:
            daemon.wait(timeout=10)
        except subprocess.TimeoutExpired:
            daemon.kill()


def test_client_reconnects_to_restarted_daemon_without_duplicate_write(
    daemon_env: tuple[dict[str, str], Path],
) -> None:
    """A daemon that dies mid-session and restarts on a NEW port + NEW token is
    followed by the SAME shim session — and a retried keyed write is deduped.

    This closes M1: the one-shot startup decision (proxy bound to daemon A's
    port/token for the session's life) cannot survive A's death. The
    reconnecting `client_factory` re-reads the published state on every
    forwarded call, so call #2 reaches the restarted daemon B. Driving it with
    a repeated `idempotency_key` proves the reconnect is *safe* for writes: the
    retry that auto-reconnect makes possible lands at-most-once (slice 2),
    leaving exactly one append rather than duplicating an in-flight write.

    Composes with slice 1.2: killing A frees its singleton `daemon.lock`, so B
    reacquires it and self-heals before serving. A and B share the state dir,
    hence the same `idempotency.db` — the seam that makes the key dedupe across
    the restart.
    """
    env, state_dir = daemon_env
    vault = state_dir / "vault"
    _seed_demo_project(vault)
    _git_init_vault(vault)
    context = vault / "10_projects" / "demo" / "00-context.md"
    marker = "RECONNECT-IDEM-MARKER"

    port_a = _free_port()
    port_b = _free_port()  # reserved while A is alive => guaranteed distinct
    assert port_b != port_a, "test setup: ports must differ"
    daemon_a: subprocess.Popen[bytes] | None = _spawn_daemon(env, port_a)
    daemon_b: subprocess.Popen[bytes] | None = None

    def restart() -> None:
        nonlocal daemon_a, daemon_b
        assert daemon_a is not None
        daemon_a.kill()
        daemon_a.wait(timeout=10)
        daemon_a = None
        daemon_b = _spawn_daemon(env, port_b)
        assert _wait_published(state_dir, port_b), "daemon B never published its port"

    try:
        assert _wait_ready(port_a), "daemon A did not bind its loopback port"
        asyncio.run(
            _reconnect_then_retry(env, "idem-key-1", f"\n{marker}\n", restart),
        )

        # The retry followed the shim to B and was deduped: exactly one append.
        content = context.read_text(encoding="utf-8")
        assert content.count(marker) == 1, (
            f"keyed write was not at-most-once across the reconnect: "
            f"{content.count(marker)} occurrences"
        )
        assert _published_port(state_dir) == port_b, "shim did not end up on daemon B"
    finally:
        for d in (daemon_a, daemon_b):
            if d is None:
                continue
            d.terminate()
            try:
                d.wait(timeout=10)
            except subprocess.TimeoutExpired:
                d.kill()


def test_client_degrades_in_process_when_daemon_dies_mid_session(
    daemon_env: tuple[dict[str, str], Path],
) -> None:
    """When the daemon the shim is proxying to dies and does NOT return, the
    next forwarded call degrades to the in-process server rather than erroring.

    This is the other half of M1's bidirectional recovery (the reconnect test
    covers daemon-returns; this covers daemon-gone). It exercises the factory's
    lazy in-process fallback branch — the second owner that prefer-daemon
    routing keeps dormant while a daemon is alive — and confirms the shim logs
    the `daemon -> fallback` transition so an operator can see the degrade.
    """
    env, state_dir = daemon_env
    vault = state_dir / "vault"
    args = _seed_demo_project(vault)

    port = _free_port()
    daemon: subprocess.Popen[bytes] | None = _spawn_daemon(env, port)

    def kill() -> None:
        nonlocal daemon
        assert daemon is not None
        daemon.kill()
        daemon.wait(timeout=10)
        # The shim's per-call factory TCP-probes the now-dead port; a refused
        # connect (loopback RST) flips it to in-process without a restart.
        assert not _wait_ready(port, deadline_s=2.0), "daemon port still open"
        daemon = None

    try:
        assert _wait_ready(port), "daemon did not bind its loopback port"
        first, second = asyncio.run(_query_across_kill(env, args, kill))

        assert _DEMO_MARKER in first, f"daemon-mode query failed: {first!r}"
        assert _DEMO_MARKER in second, f"in-process degrade query failed: {second!r}"

        modes = _client_modes(state_dir)
        assert any(m.startswith("daemon") for m in modes), (
            f"shim never logged daemon mode: {modes!r}"
        )
        assert any("daemon_unreachable" in m for m in modes), (
            f"shim did not log the daemon->fallback transition: {modes!r}"
        )
    finally:
        if daemon is not None:
            daemon.terminate()
            try:
                daemon.wait(timeout=10)
            except subprocess.TimeoutExpired:
                daemon.kill()


def test_health_probe_is_unauthenticated_and_reports_ready(
    daemon_env: tuple[dict[str, str], Path],
) -> None:
    """The daemon exposes an UNAUTHENTICATED `/health` liveness probe a
    supervisor (systemd readiness, restart-on-upgrade wait, the client's
    reconnect probe) can poll without the bearer token.

    Contract: 200 + `status=ok` + `ready=true` (DBs open + vault resolvable) +
    `version` + `uptime_s`. Liveness is the minimal NON-sensitive surface — it
    must never leak the token-gated metrics/budget that live on `/status`
    (ADR-011 §2: the bare loopback port stays closed for everything sensitive).
    """
    import httpx

    env, state_dir = daemon_env
    port = _free_port()
    daemon = _spawn_daemon(env, port)
    try:
        assert _wait_ready(port), "daemon did not bind its loopback port"

        # No Authorization header: a supervisor must probe liveness tokenless.
        resp = httpx.get(f"http://{HOST}:{port}/health")
        assert resp.status_code == 200, f"/health not served unauthenticated: {resp.status_code}"
        payload = resp.json()
        assert payload["status"] == "ok", f"unexpected health status: {payload!r}"
        assert payload["ready"] is True, f"daemon reported not ready: {payload!r}"
        assert payload["version"], "health probe missing version"
        assert payload["uptime_s"] >= 0

        # The sensitive surface (per-tool metrics + budget) must NOT leak from an
        # unauthenticated probe — those stay token-gated on /status.
        assert "budget" not in payload, "health probe leaked budget to no-token caller"
        assert "tools" not in payload, "health probe leaked metrics to no-token caller"
    finally:
        daemon.terminate()
        try:
            daemon.wait(timeout=10)
        except subprocess.TimeoutExpired:
            daemon.kill()


def test_daemon_self_heals_stale_index_lock(
    daemon_env: tuple[dict[str, str], Path],
) -> None:
    """A prior daemon crashed mid-commit, leaving a stale `.git/index.lock`
    owned by a now-dead PID. On startup — holding the singleton `daemon.lock`,
    so single ownership is proven — the daemon clears the stale lock, and a
    subsequent vault_write COMMITS normally instead of silently failing on a
    lock git will never release on its own (the write succeeds to disk either
    way; the discriminator is whether the commit lands)."""
    env, state_dir = daemon_env
    vault = state_dir / "vault"
    _seed_demo_project(vault)
    _git_init_vault(vault)

    # A crashed prior daemon's lock: an implausible, certainly-dead PID.
    stale_lock = vault / ".git" / "index.lock"
    stale_lock.write_text("999999\n", encoding="utf-8")

    port = _free_port()
    daemon = _spawn_daemon(env, port)
    try:
        assert _wait_ready(port), "daemon did not bind its loopback port"

        done = asyncio.run(_client_appends(env, ["HEAL-MARKER"]))
        assert done == 1, "vault_write did not complete"

        assert not stale_lock.exists(), "startup self-heal did not clear the stale .git/index.lock"
        # The write committed: git log grew past `init`. If the stale lock had
        # survived, the daemon's best-effort commit would have failed silently
        # and the log would still be a single `init` commit.
        log = subprocess.run(
            ["git", "log", "--oneline"],
            cwd=vault,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert len(log.splitlines()) >= 2, f"write did not commit: {log!r}"
        content = (vault / "10_projects" / "demo" / "00-context.md").read_text(
            encoding="utf-8",
        )
        assert "HEAL-MARKER" in content
    finally:
        daemon.terminate()
        try:
            daemon.wait(timeout=10)
        except subprocess.TimeoutExpired:
            daemon.kill()


def test_second_daemon_declines_when_state_owned(
    daemon_env: tuple[dict[str, str], Path],
) -> None:
    """Two `hive serve` invocations with auto-port bind DIFFERENT free ports, so
    the OS port-in-use guard never fires — both would end up owning the same
    SQLite DBs + git tree (a single-owner violation, ADR-011 §1). A singleton
    flock on `daemon.lock` closes the gap: the second daemon, even on a
    different port, finds the state dir already owned and declines cleanly
    (exit 0) without binding a second port.

    Explicit distinct ports stand in deterministically for the `_free_port()`
    race the auto-port path would produce.
    """
    env, state_dir = daemon_env
    (state_dir / "vault" / "10_projects").mkdir(parents=True, exist_ok=True)

    port_a = _free_port()
    daemon_a = _spawn_daemon(env, port_a)
    daemon_b: subprocess.Popen[bytes] | None = None
    try:
        assert _wait_ready(port_a), "first daemon did not bind its loopback port"

        port_b = _free_port()
        assert port_b != port_a, "test setup: ports must differ"
        daemon_b = _spawn_daemon(env, port_b)

        # The second daemon must decline promptly, not run forever.
        rc = daemon_b.wait(timeout=15)
        assert rc == 0, f"second daemon did not decline cleanly: rc={rc}"
        assert not _wait_ready(port_b, deadline_s=2.0), (
            "second daemon bound a port despite the singleton lock"
        )
    finally:
        for d in (daemon_a, daemon_b):
            if d is None:
                continue
            d.terminate()
            try:
                d.wait(timeout=10)
            except subprocess.TimeoutExpired:
                d.kill()


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


# ── slice 1.3: restart-on-upgrade (drift detection + cooperative stop) ─────


@pytest.mark.parametrize(
    ("boot", "current", "expected"),
    [
        ("1.30.0", "1.30.0", False),  # steady state — no restart
        ("1.30.0", "1.31.0", True),  # in-place upgrade — restart
        ("1.30.0", "1.29.0", True),  # rollback ships new code too — restart
        ("1.30.0", "<not-found>", False),  # transient swap window — must NOT bounce
        ("<not-found>", "1.30.0", False),  # never resolved at boot — don't churn
    ],
)
def test_upgrade_detected_predicate(boot: str, current: str, expected: bool) -> None:
    """The drift predicate decides when an in-place package swap warrants a
    supervised restart. A resolvable version that DIFFERS from the boot snapshot
    is drift (upgrade and rollback both ship code the running process is not
    executing). The ``<not-found>`` sentinel — returned by ``_current_version``
    during the brief window where ``uv tool upgrade`` has removed the old
    ``*.dist-info`` but not yet written the new one — must NEVER trigger a
    restart, or a transient unreadable read would bounce a healthy daemon."""
    from hive._daemon import _upgrade_detected

    assert _upgrade_detected(boot, current) is expected


def test_watch_for_upgrade_sets_should_exit_on_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The poll loop flips the owned server's ``should_exit`` and returns True the
    moment it observes a resolvable version different from boot — the cooperative
    stop the spike proved (vs a signal that cuts the in-flight handler)."""
    import hive._daemon as daemon

    # boot read, one steady poll, then the upgraded version appears.
    versions = iter(["1.30.0", "1.30.0", "1.31.0"])
    monkeypatch.setattr(daemon, "_current_version", lambda *a, **k: next(versions))

    class _FakeServer:
        should_exit = False

    server = _FakeServer()
    boot = daemon._current_version()  # consumes the first "1.30.0"
    drifted = asyncio.run(daemon._watch_for_upgrade(server, boot=boot, poll_s=0.01))

    assert drifted is True
    assert server.should_exit is True


def test_watch_for_upgrade_returns_false_on_external_stop() -> None:
    """A signal-driven stop (``systemctl stop``) flips ``should_exit`` out from
    under the watcher; it must report 'not drift' so ``run_serve`` exits 0 and the
    supervisor does not restart a daemon that was asked to stop."""
    import hive._daemon as daemon

    class _FakeServer:
        should_exit = True  # already stopping for another reason

    drifted = asyncio.run(
        daemon._watch_for_upgrade(_FakeServer(), boot="1.30.0", poll_s=0.01),
    )
    assert drifted is False


def test_run_serve_maps_drift_to_restart_exit_code(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """run_serve maps a drift-caused stop to the dedicated restart exit code so a
    ``Restart=on-failure`` supervisor relaunches into the new code, while a clean
    stop returns 0 (consistent with the singleton-decline path, which also
    exits 0 to avoid a no-op restart loop)."""
    import hive._daemon as daemon

    # Stub the owned-serve loop so no real port is bound / server built.
    monkeypatch.setattr(daemon, "daemon_state_dir", lambda: tmp_path)
    monkeypatch.setattr(daemon, "token_file_path", lambda: tmp_path / "daemon.token")
    monkeypatch.setattr(daemon, "port_file_path", lambda: tmp_path / "daemon.port")
    monkeypatch.setattr(daemon, "lock_file_path", lambda: tmp_path / "daemon.lock")
    monkeypatch.setattr(daemon, "_startup_self_heal", lambda vault: None)

    monkeypatch.setattr(daemon, "_serve_owned", lambda *a, **k: True)
    assert daemon.run_serve(port=54321) == daemon.EXIT_RESTART_ON_UPGRADE

    monkeypatch.setattr(daemon, "_serve_owned", lambda *a, **k: False)
    assert daemon.run_serve(port=54321) == 0
