"""Integration tests for the `hive serve` daemon (HIVE-118 / ADR-011).

Black-box: spawns the real daemon over the token-gated loopback Streamable-HTTP
transport (the path validated by `specs/.../spike/transport_spike.py`) and
drives it with a thin `fastmcp.Client` — the production analogue of the spike.
"""

from __future__ import annotations

import asyncio
import contextlib
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
    import ssl
    from collections.abc import Callable
    from pathlib import Path

HOST = "127.0.0.1"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return int(s.getsockname()[1])


def _wait_ready(port: int, deadline_s: float = 45.0) -> bool:
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        try:
            with socket.create_connection((HOST, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def _owner_cert_pem(state_dir: Path, deadline_s: float = 20.0) -> str:
    cert = state_dir / "daemon.crt"
    end = time.monotonic() + deadline_s
    while not cert.exists() and time.monotonic() < end:
        time.sleep(0.05)
    return cert.read_text(encoding="ascii")


def _pinned_get(port: int, cert_pem: str, path: str) -> int:
    import http.client

    from hive._tls import pinned_context

    conn = http.client.HTTPSConnection(HOST, port, timeout=10, context=pinned_context(cert_pem))
    try:
        conn.request("GET", path)
        response = conn.getresponse()
        response.read()
        return response.status
    finally:
        conn.close()


def _pinned(state_dir: Path) -> ssl.SSLContext:
    """A client context that trusts only this daemon's certificate."""
    from hive._tls import pinned_context

    return pinned_context(_owner_cert_pem(state_dir))


async def _list_tools(url: str, token: str, verify: ssl.SSLContext) -> list[str]:
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    transport = StreamableHttpTransport(
        url,
        headers={"Authorization": f"Bearer {token}"},
        verify=verify,
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
        args=["-m", "hive.cli", "client"],
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
        args=["-m", "hive.cli", "client"],
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
                    # commit=True explicitly: these tests use the commit as an
                    # *instrument* to observe single-owner serialization, not as
                    # the subject under test. Since ADR-018 made deferral the
                    # default, an implicit commit no longer lands inside the
                    # call, and the instrument would read empty for a reason
                    # that has nothing to do with serialization.
                    "commit": True,
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

    Append *content* under idempotency *key* against daemon A, restart it
    at the same endpoint, then retry the SAME keyed write. The second call
    must follow the shim to B and dedupe without reconfiguring the client.
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
        args=["-m", "hive.cli", "client"],
        env=env,
    )
    async with Client(transport) as client:
        assert "vault_health" in {tool.name for tool in await client.list_tools()}
        assert not (await client.call_tool("vault_health", {})).is_error
        await client.call_tool("vault_write", args)  # lands on daemon A
        await asyncio.to_thread(restart)  # A dies, B takes over
        assert "vault_health" in {tool.name for tool in await client.list_tools()}
        assert not (await client.call_tool("vault_health", {})).is_error
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
        args=["-m", "hive.cli", "client"],
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


async def _session_calls(
    url: str,
    token: str,
    tool: str,
    args: dict[str, str],
    times: int,
    verify: ssl.SSLContext,
) -> None:
    """Open one MCP session, call *tool* *times*, then disconnect."""
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    transport = StreamableHttpTransport(
        url,
        headers={"Authorization": f"Bearer {token}"},
        verify=verify,
    )
    async with Client(transport) as client:
        for _ in range(times):
            await client.call_tool(tool, args)


@pytest.fixture
def daemon_env(tmp_path: Path) -> tuple[dict[str, str], Path]:
    """Env pointing every DB + the vault + daemon state dir at a temp dir.

    The daemon/client subprocesses inherit a *scrubbed* copy of ``os.environ``:
    every ambient ``HIVE_*`` / ``VAULT_PATH`` is stripped first, then only the
    test's temp paths are set. Without the scrub a dev box that exports its real
    ``HIVE_VAULT_PATH`` (which pydantic-settings' ``AliasChoices`` ranks ABOVE
    the unprefixed ``VAULT_PATH``) would shadow the override below — the
    subprocess would open the developer's real vault, where the seeded ``demo``
    project does not exist, and every forwarded query would 404 ("Project 'demo'
    not found"). This is the #212 class of "passes on clean CI, fails on a dev
    box with real local state". Both vault aliases are set so neither precedence
    order can leak. The autouse ``_isolate_hive_data_dir`` only fixes the parent
    process; these subprocesses re-read the environment, so they need it here.
    """
    vault = tmp_path / "vault"
    (vault / "10_projects").mkdir(parents=True)
    base = {k: v for k, v in os.environ.items() if not k.startswith("HIVE_") and k != "VAULT_PATH"}
    env = {
        **base,
        "HIVE_DB_PATH": str(tmp_path / "worker.db"),
        "HIVE_RELEVANCE_DB_PATH": str(tmp_path / "relevance.db"),
        "HIVE_LESSON_DB_PATH": str(tmp_path / "lesson.db"),
        "HIVE_LOG_PATH": str(tmp_path / "hive.log"),
        "VAULT_PATH": str(vault),
        "HIVE_VAULT_PATH": str(vault),
    }
    return env, tmp_path


def _spawn_daemon(env: dict[str, str], port: int) -> subprocess.Popen[bytes]:
    env["HIVE_DAEMON_PORT"] = str(port)
    return subprocess.Popen(
        [sys.executable, "-m", "hive.server", "serve"],
        env=env,
    )


def _kill_tree(proc: subprocess.Popen[bytes], timeout_s: float = 10.0) -> None:
    """Kill *proc* AND its descendants, then reap them all.

    ``Popen.kill()`` terminates only the immediate process. On Windows the
    daemon's ``uvicorn`` server runs the listening socket in a *child*
    ``python.exe`` (confirmed via ``psutil.net_connections``: the LISTEN owner is
    the child PID, not ``proc.pid``). Killing only the parent leaves that child
    bound to the port, so a follow-up ``_wait_ready`` still connects and a
    "daemon is gone" assertion flaps. ``psutil`` (a hard hive dependency) lets us
    enumerate and kill the whole tree so the port is actually released. Falls
    back to a plain ``proc.kill()`` if the process is already gone.
    """
    import psutil

    try:
        parent = psutil.Process(proc.pid)
        victims = [*parent.children(recursive=True), parent]
    except psutil.NoSuchProcess:
        with contextlib.suppress(Exception):
            proc.kill()
        return
    for victim in victims:
        with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
            victim.kill()
    psutil.wait_procs(victims, timeout=timeout_s)
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=timeout_s)


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

        tools = asyncio.run(_list_tools(f"https://{HOST}:{port}/mcp", token, _pinned(state_dir)))
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


def test_delegate_remote_client_reaches_the_daemon_over_pinned_tls(
    daemon_env: tuple[dict[str, str], Path],
) -> None:
    """``hive delegate``'s transport talks to the real TLS daemon with the pin alone."""
    from hive import _delegate

    env, state_dir = daemon_env
    port = _free_port()
    proc = _spawn_daemon(env, port)
    try:
        assert _wait_ready(port), "daemon did not bind its loopback port"
        pem = _owner_cert_pem(state_dir)
        token = (state_dir / "daemon.token").read_text(encoding="utf-8").strip()
        assert _delegate._probe_daemon(HOST, port, pem) == "verified"

        async def tools() -> set[str]:
            async with _delegate._remote_client(HOST, port, token, pem) as client:
                return {tool.name for tool in await client.list_tools()}

        assert "delegate_task" in asyncio.run(tools())
    finally:
        _kill_tree(proc)


def _initialize(relay: object) -> list[dict[str, object]]:
    from hive._client import HttpRelay

    assert isinstance(relay, HttpRelay)
    return relay.forward(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "identity-test", "version": "1"},
            },
        },
    )


def test_relay_accepts_the_old_token_after_an_impostor_round(
    daemon_env: tuple[dict[str, str], Path],
) -> None:
    """Non-disclosure, not revocation: refusing an impostor leaves the token valid."""
    from hive._client import ClientError, HttpRelay
    from hive._identity import create_identity
    from tests.impostor import Impostor

    env, state_dir = daemon_env
    port = _free_port()
    proc = _spawn_daemon(env, port)
    try:
        assert _wait_ready(port), "daemon did not bind its loopback port"
        pem = _owner_cert_pem(state_dir)
        token = (state_dir / "daemon.token").read_text(encoding="utf-8").strip()

        impostor = Impostor(create_identity(state_dir / "imp.key", state_dir / "imp.crt"))
        with pytest.raises(ClientError, match="possible impersonation"):
            _initialize(HttpRelay(HOST, impostor.port, token, pem))
        impostor.wait()
        impostor.assert_refused_before_any_request(token)

        frames = _initialize(HttpRelay(HOST, port, token, pem))
        assert "result" in frames[-1], frames
        assert (state_dir / "daemon.token").read_text(encoding="utf-8").strip() == token
    finally:
        _kill_tree(proc)


def test_plaintext_era_token_capture_is_revoked_by_first_tls_start(
    daemon_env: tuple[dict[str, str], Path],
) -> None:
    """A bearer captured while #453 served plaintext is dead after the first TLS start."""
    import http.client

    import httpx

    from hive._daemon import _create_token
    from tests.impostor import Impostor

    env, state_dir = daemon_env
    # 1. A #453-shaped state directory: a token and no identity.
    captured_token = _create_token(state_dir / "daemon.token")
    assert not (state_dir / "identity.state").exists()

    # 2. The #453 relay sent the bearer in plaintext; an impostor recorded it.
    impostor = Impostor(None)
    legacy = http.client.HTTPConnection(HOST, impostor.port, timeout=5)
    with contextlib.suppress(OSError, http.client.HTTPException):
        legacy.request(
            "POST", "/mcp", body=b"{}", headers={"Authorization": f"Bearer {captured_token}"}
        )
        legacy.getresponse().read()
    legacy.close()
    impostor.wait()
    assert captured_token.encode() in bytes(impostor.raw)

    # 3. The first TLS start rotates it: the capture is refused, the new one works.
    port = _free_port()
    proc = _spawn_daemon(env, port)
    try:
        assert _wait_ready(port), "daemon did not bind its loopback port"
        verify = _pinned(state_dir)
        rotated = (state_dir / "daemon.token").read_text(encoding="utf-8").strip()
        assert rotated != captured_token

        status = f"https://{HOST}:{port}/status"
        old = httpx.get(
            status, headers={"Authorization": f"Bearer {captured_token}"}, verify=verify
        )
        new = httpx.get(status, headers={"Authorization": f"Bearer {rotated}"}, verify=verify)
        assert old.status_code == 401
        assert new.status_code == 200
    finally:
        _kill_tree(proc)


def test_hive_serve_rejects_bad_token(daemon_env: tuple[dict[str, str], Path]) -> None:
    """A request without the matching token is refused — the bare loopback
    port is not open."""
    env, state_dir = daemon_env
    port = _free_port()
    proc = _spawn_daemon(env, port)
    try:
        assert _wait_ready(port)
        # A bad bearer token is refused at the transport with HTTP 401 — not a
        # generic failure. Assert the status on the wire rather than through an
        # MCP client: mcp 2.x wraps it in MCPError, 1.x raised HTTPStatusError,
        # and neither wrapping is the property under test.
        resp = httpx.post(
            f"https://{HOST}:{port}/mcp",
            verify=_pinned(state_dir),
            headers={
                "Authorization": "Bearer not-the-token",
                "Accept": "application/json, text/event-stream",
            },
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )
        assert resp.status_code == 401, f"bad token not refused: {resp.status_code}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.mark.parametrize("transport_kind", ["stdio", "http"])
def test_copilot_daemon_mode_initialize_list_and_health(
    daemon_env: tuple[dict[str, str], Path],
    transport_kind: str,
) -> None:
    """Both Copilot-supported transports reach the same real daemon and tools."""
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport, StreamableHttpTransport

    env, state_dir = daemon_env
    port = _free_port()
    daemon = _spawn_daemon(env, port)
    try:
        assert _wait_ready(port), "daemon did not bind its loopback port"
        token = (state_dir / "daemon.token").read_text(encoding="utf-8").strip()
        launcher = os.path.join(
            os.path.dirname(sys.executable), "hive.exe" if os.name == "nt" else "hive"
        )
        transport = (
            StdioTransport(command=launcher, args=["client"], env=env)
            if transport_kind == "stdio"
            else StreamableHttpTransport(
                f"https://{HOST}:{port}/mcp",
                headers={"Authorization": "Bearer " + token},
                verify=_pinned(state_dir),
            )
        )

        async def smoke() -> None:
            started = time.monotonic()
            async with Client(transport) as client:
                initialize_s = time.monotonic() - started
                assert initialize_s < 4.1, f"{transport_kind} initialize took {initialize_s:.3f}s"
                assert "vault_health" in {tool.name for tool in await client.list_tools()}
                health = await client.call_tool("vault_health", {})
                assert not health.is_error
                assert "server" in str(getattr(health, "data", health)).lower()

        asyncio.run(smoke())
        status = httpx.get(
            f"https://{HOST}:{port}/status",
            verify=_pinned(state_dir),
            headers={"Authorization": "Bearer " + token},
            timeout=3.0,
        )
        assert status.status_code == 200
        assert status.json()["tools"]["vault_health"]["calls"] == 1
    finally:
        _kill_tree(daemon)


def test_client_forwards_to_daemon(daemon_env: tuple[dict[str, str], Path]) -> None:
    """With a daemon running, the thin stdio shim connects over the token-gated
    transport and forwards the full MCP surface without leaking the token."""
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

        # The bearer token must never reach disk in any hive log (L4).
        logs = "".join(f.read_text(errors="replace") for f in state_dir.glob("hive-*.log"))
        assert token and token not in logs, "bearer token leaked into a hive log"
    finally:
        daemon.terminate()
        try:
            daemon.wait(timeout=5)
        except subprocess.TimeoutExpired:
            daemon.kill()


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


def test_client_stable_endpoint_restart_avoids_duplicate_write(
    daemon_env: tuple[dict[str, str], Path],
) -> None:
    """One shim survives a daemon restart at the same URL and token."""
    env, state_dir = daemon_env
    vault = state_dir / "vault"
    _seed_demo_project(vault)
    _git_init_vault(vault)
    context = vault / "10_projects" / "demo" / "00-context.md"
    marker = "RECONNECT-IDEM-MARKER"

    port = _free_port()
    daemon_a: subprocess.Popen[bytes] | None = _spawn_daemon(env, port)
    daemon_b: subprocess.Popen[bytes] | None = None
    original_token = ""

    def restart() -> None:
        nonlocal daemon_a, daemon_b, original_token
        assert daemon_a is not None
        original_token = (state_dir / "daemon.token").read_text(encoding="utf-8")
        _kill_tree(daemon_a)
        daemon_a = None
        daemon_b = _spawn_daemon(env, port)
        assert _wait_published(state_dir, port), "daemon B never restored the stable endpoint"

    try:
        assert _wait_ready(port), "daemon A did not bind its loopback port"
        asyncio.run(
            _reconnect_then_retry(env, "idem-key-1", f"\n{marker}\n", restart),
        )

        content = context.read_text(encoding="utf-8")
        assert content.count(marker) == 1, (
            f"keyed write was not at-most-once across the reconnect: "
            f"{content.count(marker)} occurrences"
        )
        assert _published_port(state_dir) == port
        assert (state_dir / "daemon.token").read_text(encoding="utf-8") == original_token
    finally:
        for d in (daemon_a, daemon_b):
            if d is None:
                continue
            d.terminate()
            try:
                d.wait(timeout=10)
            except subprocess.TimeoutExpired:
                d.kill()


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
        resp = httpx.get(f"https://{HOST}:{port}/health", verify=_pinned(state_dir))
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


def test_stable_port_serves_tls13_only(daemon_env: tuple[dict[str, str], Path]) -> None:
    """ADR-022 A1 invariant 8: the stable port speaks TLS 1.3 and nothing else."""
    import ssl

    env, state_dir = daemon_env
    port = _free_port()
    daemon = _spawn_daemon(env, port)
    try:
        assert _wait_ready(port), "daemon did not bind its loopback port"
        cert_pem = _owner_cert_pem(state_dir)

        # Plaintext HTTP gets no HTTP response, only a closed or TLS-alerted socket.
        with socket.create_connection((HOST, port), timeout=5) as raw:
            raw.sendall(b"GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
            try:
                reply = raw.recv(64)
            except OSError:
                reply = b""
        assert not reply.startswith(b"HTTP/"), "the stable port answered plaintext HTTP"

        # Offer every TLS 1.2 cipher, so the only reason left to refuse the
        # handshake is the protocol version. uvicorn's default cipher string
        # shares no cipher with Python's default client, which made this pass
        # even without the TLS 1.3 minimum; with this offer, removing the
        # minimum lets the handshake complete. The refusal arrives either as
        # a protocol-version alert or as a bare close, so any SSLError counts.
        legacy = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        legacy.load_verify_locations(cadata=cert_pem)
        legacy.maximum_version = ssl.TLSVersion.TLSv1_2
        legacy.set_ciphers("ALL:@SECLEVEL=0")
        with (
            socket.create_connection((HOST, port), timeout=5) as raw,
            pytest.raises(ssl.SSLError),
        ):
            legacy.wrap_socket(raw, server_hostname=HOST)

        # Positive control: the same port serves pinned TLS 1.3, and the
        # tokenless /status is still refused there.
        assert _pinned_get(port, cert_pem, "/health") == 200
        assert _pinned_get(port, cert_pem, "/status") == 401
    finally:
        _kill_tree(daemon)


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
        mcp_url = f"https://{HOST}:{port}/mcp"
        verify = _pinned(state_dir)

        # Two sequential sessions: each opens, calls vault_query k times, then
        # fully disconnects before the next starts. If /status still counts both,
        # the metrics survived the disconnects and aggregate across sessions.
        asyncio.run(_session_calls(mcp_url, token, "vault_query", args, k, verify))
        asyncio.run(_session_calls(mcp_url, token, "vault_query", args, k, verify))

        status_url = f"https://{HOST}:{port}/status"
        resp = httpx.get(status_url, headers={"Authorization": f"Bearer {token}"}, verify=verify)
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
        bad = httpx.get(status_url, verify=verify)
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
