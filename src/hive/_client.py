"""``hive client`` — the thin stdio shim Claude Code spawns (HIVE-118 / ADR-011).

Claude Code keeps its v1 stdio MCP contract (``~/.claude.json`` unchanged): it
spawns this shim over stdio exactly as it spawns ``hive`` today. The shim then
chooses a backend at startup:

* **daemon mode** — a single-owner ``hive serve`` daemon is reachable on its
  published loopback port. The shim becomes a transparent FastMCP *proxy* that
  forwards every request to the daemon over the token-gated Streamable-HTTP
  transport (ADR-011 §2). One daemon owns SQLite + git; N shims proxy into it.
  The shim re-implements no tools — it forwards the protocol, so the daemon is
  the single source of truth for the tool surface.
* **fallback mode** — no daemon is reachable (none started, or a crashed daemon
  left stale state files behind a dead port). The shim serves
  ``create_server()`` in-process over stdio — exactly today's per-session
  behavior — so a daemon outage *degrades* rather than *breaks* hive (proposal
  item 4 / the "Transparent fallback" acceptance criterion).

The chosen mode is logged once at startup as ``hive.client.mode=<mode> ...`` so
an operator can confirm which path a session took. This is deliberately a log
signal, not an MCP-visible flag: slice 2 must not change the tool surface (the
richer cross-session ``hive status`` observability surface is a later slice).
"""

from __future__ import annotations

import contextlib
import logging
import socket

from hive._daemon import DEFAULT_HOST, MCP_PATH, port_file_path, token_file_path

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
    with contextlib.suppress(OSError), socket.create_connection(
        (host, port), timeout=_PROBE_TIMEOUT_S,
    ):
        return True
    return False


def _serve_in_process() -> None:
    """Fallback: run the v1 in-process stdio server (today's behavior)."""
    from hive.server import create_server

    create_server().run()


def _serve_proxy(host: str, port: int, token: str) -> None:
    """Daemon mode: forward stdio MCP to the daemon over token-gated HTTP.

    The backend transport is wrapped in a ``Client`` carrying a bounded
    ``init_timeout`` so a daemon that accepts TCP but stalls the MCP handshake
    (the gap the TCP probe cannot see) fails fast instead of hanging the
    session. The per-request ``timeout`` is deliberately left unset: tool calls
    such as ``delegate_task`` legitimately run for tens of seconds and the
    daemon owns their deadline budget — capping it here would sever long calls.

    ``create_proxy`` treats a disconnected ``Client`` as a per-request session
    factory (``Client.new()``), which preserves ``init_timeout`` on every
    forwarded call and keeps concurrent sessions isolated.
    """
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport
    from fastmcp.server import create_proxy

    transport = StreamableHttpTransport(
        f"http://{host}:{port}{MCP_PATH}",
        headers={"Authorization": f"Bearer {token}"},
    )
    backend = Client(transport, init_timeout=_DAEMON_INIT_TIMEOUT_S)
    create_proxy(backend).run()


def run_client(host: str = DEFAULT_HOST) -> None:
    """Run the thin stdio shim: proxy to a live daemon, else serve in-process.

    Decision order: no published state -> ``no_daemon_state`` fallback; state
    present but the port is dead -> ``daemon_unreachable`` fallback; otherwise
    proxy to the daemon. The mode is logged once before the server loop starts.
    """
    state = _read_state()
    if state is None:
        _log.info("hive.client.mode=fallback reason=no_daemon_state")
        _serve_in_process()
        return

    port, token = state
    if not _daemon_reachable(host, port):
        _log.info("hive.client.mode=fallback reason=daemon_unreachable")
        _serve_in_process()
        return

    _log.info("hive.client.mode=daemon endpoint=%s:%d", host, port)
    _serve_proxy(host, port, token)
