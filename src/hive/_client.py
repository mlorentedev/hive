"""``hive client`` — the thin stdio shim Claude Code spawns (HIVE-118 / ADR-011).

Claude Code keeps its v1 stdio MCP contract (``~/.claude.json`` unchanged): it
spawns this shim over stdio exactly as it spawns ``hive`` today. The shim runs
in one of two modes:

* **daemon mode** — a single-owner ``hive serve`` daemon is reachable on its
  published loopback port. The shim becomes a transparent FastMCP *proxy* that
  forwards every request to the daemon over the token-gated Streamable-HTTP
  transport (ADR-011 §2). One daemon owns SQLite + git; N shims proxy into it.
  The shim re-implements no tools — it forwards the protocol, so the daemon is
  the single source of truth for the tool surface. Crucially the proxy resolves
  its backend through a reconnecting factory on *every* forwarded call, so a
  daemon that dies and is restarted on a new port + token is followed without
  restarting the shim (closes M1 — the one-shot startup decision could never
  survive the daemon's death).
* **fallback mode** — no daemon is reachable (none started, or a crashed daemon
  left stale state files behind a dead port). The shim serves
  ``create_server()`` in-process over stdio — exactly today's per-session
  behavior — so a daemon outage *degrades* rather than *breaks* hive (proposal
  item 4 / the "Transparent fallback" acceptance criterion).

The active mode is logged as ``hive.client.mode=<mode> ...`` on the first call
and on every transition, so an operator can confirm which path a session took
and *see* a recovery (a new ``endpoint=`` port is a reconnect) without a line
per forwarded call. This is deliberately a log signal, not an MCP-visible flag:
the tool surface stays unchanged (the richer cross-session ``hive status``
observability surface is a later slice).
"""

from __future__ import annotations

import contextlib
import logging
import socket
from typing import TYPE_CHECKING, Any

from hive._daemon import DEFAULT_HOST, MCP_PATH, port_file_path, token_file_path

if TYPE_CHECKING:
    from fastmcp import Client, FastMCP

_log = logging.getLogger("hive")

# How long to wait for the daemon's loopback port to accept a TCP connection
# before declaring it unreachable and falling back. This probe runs on *every*
# session spawn, so a dead daemon must not stall Claude Code's startup; yet it
# must tolerate a momentarily busy machine. 500 ms is the spike's proven budget.
_PROBE_TIMEOUT_S = 0.5

# Bound for the MCP ``initialize`` handshake to the daemon. The TCP probe only
# proves the port *accepts* connections; this caps the gap where it accepts but
# the MCP layer never replies (a wedged daemon), turning what would otherwise be
# an unbounded hang into a fast, bounded failure. fastmcp's global default is
# ``None`` (no timeout), so this must be set explicitly.
_DAEMON_INIT_TIMEOUT_S = 5.0


def _read_state() -> tuple[int, str] | None:
    """Read the daemon's published port + token, or ``None`` if either is absent.

    A missing/garbage port or empty token means no daemon ever published state
    here — the caller treats that as the ``no_daemon_state`` fallback reason.
    """
    try:
        port = int(port_file_path().read_text(encoding="utf-8").strip())
        token = token_file_path().read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return None
    if port <= 0 or not token:
        return None
    return port, token


def _daemon_reachable(host: str, port: int) -> bool:
    """Return True if a process is accepting connections at ``host:port``.

    A cheap TCP connect — enough to distinguish a live daemon from stale state
    files left by a crash (the connect is refused when nothing is listening).
    The bearer token still guards against *using* a wrong server, so a deeper
    MCP handshake is left to the auto-reconnect slice.
    """
    with (
        contextlib.suppress(OSError),
        socket.create_connection(
            (host, port),
            timeout=_PROBE_TIMEOUT_S,
        ),
    ):
        return True
    return False


def _serve_in_process() -> None:
    """Fallback: run the v1 in-process stdio server (today's behavior)."""
    from hive.server import create_server

    create_server().run()


def _remote_client(host: str, port: int, token: str) -> Client[Any]:
    """A fresh proxy backend bound to the daemon's CURRENT port + token.

    The transport carries a bounded ``init_timeout`` so a daemon that accepts
    TCP but stalls the MCP handshake (the gap the TCP probe cannot see) fails
    fast instead of hanging the session. The per-request ``timeout`` is
    deliberately left unset: tool calls such as ``delegate_task`` legitimately
    run for tens of seconds and the daemon owns their deadline budget — capping
    it here would sever long calls.
    """
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    transport = StreamableHttpTransport(
        f"http://{host}:{port}{MCP_PATH}",
        headers={"Authorization": f"Bearer {token}"},
    )
    return Client(transport, init_timeout=_DAEMON_INIT_TIMEOUT_S)


class _ReconnectingBackend:
    """Per-call backend selector for the stdio proxy (HIVE-118 slice 3, M1).

    FastMCP's proxy calls this on EVERY forwarded operation
    (``FastMCPProxy._get_client``), so re-reading the daemon's published
    port + token here is the seam that follows a *restarted* daemon to its new
    port and new token without restarting the shim — closing M1.

    Routing prefers the daemon: while it is reachable every call is forwarded
    to it, so the in-process fallback — itself a full git + SQLite owner — does
    no writes and two owners never write concurrently (the single-owner
    invariant holds at the routing layer). That fallback server is built lazily,
    only the first time the daemon is found unreachable, and cached; a session
    whose daemon stays healthy never creates a second owner at all. The narrow
    up-transition race (a fallback write in flight as the daemon returns) is
    covered by the idempotency key (slice 2) and ``.git/index.lock`` self-heal
    (slice 1.2) — exactly the multi-owner defenses already shipped.
    """

    def __init__(self, host: str) -> None:
        self._host = host
        self._in_process: FastMCP | None = None
        self._logged_mode = ""

    def __call__(self) -> Client[Any]:
        from fastmcp import Client

        state = _read_state()
        if state is not None and _daemon_reachable(self._host, state[0]):
            port, token = state
            self._log_mode(f"daemon endpoint={self._host}:{port}")
            return _remote_client(self._host, port, token)
        reason = "no_daemon_state" if state is None else "daemon_unreachable"
        self._log_mode(f"fallback reason={reason}")
        return Client(self._in_process_server())

    def _in_process_server(self) -> FastMCP:
        """Lazily build + cache the in-process server (today's fallback owner)."""
        if self._in_process is None:
            from hive.server import create_server

            self._in_process = create_server()
        return self._in_process

    def _log_mode(self, mode: str) -> None:
        """Log the active mode once and on every transition (incl. reconnect).

        A steady session logs a single line; a flapping or restarted daemon
        leaves one line per transition (a new ``endpoint=`` port marks a
        reconnect), so recovery is visible without a line per forwarded call.
        """
        if mode != self._logged_mode:
            _log.info("hive.client.mode=%s", mode)
            self._logged_mode = mode


def _serve_proxy(host: str) -> None:
    """Daemon mode: forward stdio MCP to the daemon, reconnecting per call.

    The proxy resolves its backend through ``_ReconnectingBackend`` on every
    forwarded request, so a daemon that dies and restarts on a new port + token
    is followed transparently (M1), and a daemon that dies without returning
    degrades to the in-process server rather than erroring every call.
    """
    from fastmcp.server.providers.proxy import FastMCPProxy

    FastMCPProxy(client_factory=_ReconnectingBackend(host)).run()


def run_client(host: str = DEFAULT_HOST) -> None:
    """Run the thin stdio shim: proxy to a live daemon, else serve in-process.

    Decision order: no published state -> ``no_daemon_state`` fallback; state
    present but the port is dead -> ``daemon_unreachable`` fallback; otherwise
    engage the reconnecting proxy. A cold start with no daemon serves in-process
    directly (today's behavior, unchanged). Once the proxy is engaged its
    per-call factory owns the mode decision — following a restarted daemon and
    degrading to in-process if it dies, recovering when it returns (M1). The
    initial ``daemon`` mode is logged by the factory on its first forwarded call.
    """
    state = _read_state()
    if state is None:
        _log.info("hive.client.mode=fallback reason=no_daemon_state")
        _serve_in_process()
        return

    if not _daemon_reachable(host, state[0]):
        _log.info("hive.client.mode=fallback reason=daemon_unreachable")
        _serve_in_process()
        return

    _serve_proxy(host)
