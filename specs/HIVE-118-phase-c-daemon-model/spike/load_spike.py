"""HIVE-118 Phase C — concurrency / load spike.

Emulates **N parallel client sessions** against ONE daemon to validate the
single-owner concurrency model (ADR-011 §1) *before* building the real daemon —
the operating-model claim that a single owner serving N sessions removes the
inter-process contention class that HIVE-115/116 fought (git-lock waits, WAL
growth, latency tail under simultaneous sessions).

The spike daemon owns one SQLite DB (WAL + ``busy_timeout``, mirroring ADR-009)
and exposes three tools:

- ``record(session)`` — INSERT a row + return the running count (a shared-state
  write, the exact thing that used to contend **across processes**).
- ``ping`` — trivial fast call.
- ``slow`` — a blocking ``time.sleep`` (a stand-in for a slow git op), used to
  probe head-of-line blocking.

Then ``N_SESSIONS`` clients (each its own connection = one Claude Code session)
fire ``CALLS_PER_SESSION`` concurrent ``record`` calls. Checks:

1. **Zero failures** — every call across every session succeeds (no
   ``database is locked`` / contention error).
2. **No lost writes** — the DB holds exactly ``N_SESSIONS × CALLS_PER_SESSION``
   rows; the single owner serialized all concurrent writers correctly.
3. **No head-of-line blocking** — while one session runs ``slow`` (~0.4 s),
   other sessions' ``ping`` calls still return promptly (the async daemon
   multiplexes; a slow call does not starve other sessions).
4. **Latency tail reported** — p50/p95/p99 + throughput, so a regression to a
   serialized tail is visible.

Run (crank the knobs via argv on the Windows pass; defaults 8 / 25 / 0.4)::

    uv run python .../spike/load_spike.py [sessions] [calls] [slow_s]

Exit ``0`` = every check passed. Scope note: this validates the transport +
single-owner concurrency layer, NOT the real vault/git path (that is the
post-build multi-client integration test in tasks.md). It de-risks the model.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import secrets
import socket
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

HOST = "127.0.0.1"
PATH = "/mcp"
_TOKEN_ENV = "HIVE_SPIKE_TOKEN"
_DB_ENV = "HIVE_SPIKE_DB"

N_SESSIONS = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 8
CALLS_PER_SESSION = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 25
SLOW_S = float(sys.argv[3]) if len(sys.argv) > 3 else 0.4
HOL_PINGS = 6


# ── daemon (child process) ───────────────────────────────────────────────


def _serve(token: str, port: int, db_path: str) -> None:
    """Single-owner daemon: owns one SQLite DB, serves record/ping/slow."""
    from fastmcp import FastMCP
    from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

    init = sqlite3.connect(db_path)
    init.execute("PRAGMA journal_mode=WAL")
    init.execute("CREATE TABLE IF NOT EXISTS calls (id INTEGER PRIMARY KEY, session TEXT)")
    init.commit()
    init.close()

    verifier = StaticTokenVerifier({token: {"client_id": "hive-load", "scopes": []}})
    mcp: FastMCP = FastMCP("hive-load-daemon", auth=verifier)

    @mcp.tool
    def ping() -> str:
        """Trivial fast call."""
        return "pong"

    @mcp.tool
    def record(session: str) -> int:
        """Shared-state write: INSERT a row, return the running count.

        A fresh connection per call (WAL + busy_timeout) mirrors ADR-009 —
        concurrent writers serialize on SQLite's lock, not an inter-process
        filelock, because there is exactly one owning process.
        """
        conn = sqlite3.connect(db_path, timeout=5.0, isolation_level=None)
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("INSERT INTO calls (session) VALUES (?)", (session,))
            (count,) = conn.execute("SELECT COUNT(*) FROM calls").fetchone()
            return int(count)
        finally:
            conn.close()

    @mcp.tool
    def slow() -> str:
        """A blocking call (stand-in for a slow git op) — HOL-blocking probe."""
        time.sleep(SLOW_S)
        return "done"

    mcp.run(transport="http", host=HOST, port=port, path=PATH, show_banner=False)


# ── client sessions (this process) ───────────────────────────────────────


def _client(url: str, token: str):  # noqa: ANN202 — fastmcp Client, spike
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    return Client(StreamableHttpTransport(url, headers={"Authorization": f"Bearer {token}"}))


async def _session(url: str, token: str, session_id: str, n_calls: int) -> list[float]:
    """One session: open a connection, fire n_calls record() calls in series."""
    latencies: list[float] = []
    async with _client(url, token) as client:
        for _ in range(n_calls):
            t0 = time.monotonic()
            await client.call_tool("record", {"session": session_id})
            latencies.append((time.monotonic() - t0) * 1000)
    return latencies


async def _one_call(url: str, token: str, tool: str) -> float:
    t0 = time.monotonic()
    async with _client(url, token) as client:
        await client.call_tool(tool, {})
    return (time.monotonic() - t0) * 1000


async def _run_load(url: str, token: str) -> list[list[float] | BaseException]:
    tasks = [
        _session(url, token, f"s{i}", CALLS_PER_SESSION) for i in range(N_SESSIONS)
    ]
    return await asyncio.gather(*tasks, return_exceptions=True)


async def _hol_probe(url: str, token: str) -> list[float]:
    """Fire one slow() and, mid-flight, HOL_PINGS ping()s from other sessions."""
    slow_task = asyncio.create_task(_one_call(url, token, "slow"))
    await asyncio.sleep(0.05)  # let slow() land on the daemon first
    pings = await asyncio.gather(*[_one_call(url, token, "ping") for _ in range(HOL_PINGS)])
    await slow_task
    return pings


# ── driver ───────────────────────────────────────────────────────────────


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return int(s.getsockname()[1])


def _wait_ready(port: int, deadline_s: float = 20.0) -> bool:
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        with contextlib.suppress(OSError), socket.create_connection(
            (HOST, port), timeout=0.5,
        ):
            return True
        time.sleep(0.1)
    return False


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round(p / 100 * (len(ordered) - 1))))
    return ordered[idx]


def main() -> int:
    token = secrets.token_urlsafe(32)
    port = _free_port()
    url = f"http://{HOST}:{port}{PATH}"
    db_path = str(Path(tempfile.gettempdir()) / f"hive-load-{port}.db")
    checks: list[tuple[str, bool, str]] = []

    env = {**os.environ, _TOKEN_ENV: token, _DB_ENV: db_path}
    child_log = Path(tempfile.gettempdir()) / f"hive-load-daemon-{port}.log"
    with child_log.open("wb") as log:
        proc = subprocess_popen(port, env, log)
        try:
            if not _wait_ready(port):
                tail = child_log.read_text(errors="replace")[-800:]
                checks.append(("daemon ready", False, f"timeout:\n{tail}"))
                return _report(checks, proc, db_path, child_log)

            total = N_SESSIONS * CALLS_PER_SESSION
            wall0 = time.monotonic()
            results = asyncio.run(_run_load(url, token))
            wall_s = time.monotonic() - wall0

            errors = [r for r in results if isinstance(r, BaseException)]
            lat = [x for r in results if isinstance(r, list) for x in r]
            checks.append((
                f"{N_SESSIONS} sessions × {CALLS_PER_SESSION} calls: zero failures",
                not errors and len(lat) == total,
                f"ok={len(lat)}/{total}, errors={len(errors)}"
                + (f" e.g. {errors[0]!r}"[:80] if errors else ""),
            ))

            rows = _db_count(db_path)
            checks.append((
                "no lost writes (single owner serialized all)",
                rows == total,
                f"rows={rows}/{total}",
            ))

            ping_lat = asyncio.run(_hol_probe(url, token))
            ping_p95 = _pct(ping_lat, 95)
            checks.append((
                "no head-of-line blocking under slow() call",
                ping_p95 < SLOW_S * 1000 * 0.6,
                f"ping p95={ping_p95:.0f} ms vs slow={SLOW_S * 1000:.0f} ms",
            ))

            thr = len(lat) / wall_s if wall_s else 0
            checks.append((
                "latency tail bounded (no serialized blowup)",
                _pct(lat, 99) < 5000,
                f"p50={_pct(lat, 50):.0f} p95={_pct(lat, 95):.0f} "
                f"p99={_pct(lat, 99):.0f} ms | {thr:.0f} calls/s over {wall_s:.2f}s",
            ))
        finally:
            proc.terminate()
            with contextlib.suppress(Exception):
                proc.wait(timeout=5)

    return _report(checks, proc, db_path, child_log)


def subprocess_popen(port: int, env: dict[str, str], log):  # noqa: ANN001,ANN201
    import subprocess

    return subprocess.Popen(  # noqa: S603 — fixed argv, spike only
        [sys.executable, __file__, "--serve", str(port)],
        env=env, stdout=log, stderr=log,
    )


def _db_count(db_path: str) -> int:
    with contextlib.suppress(Exception):
        conn = sqlite3.connect(db_path)
        try:
            (n,) = conn.execute("SELECT COUNT(*) FROM calls").fetchone()
            return int(n)
        finally:
            conn.close()
    return -1


def _report(checks, proc, db_path: str, child_log: Path) -> int:  # noqa: ANN001
    with contextlib.suppress(Exception):
        if proc.poll() is None:
            proc.kill()
    for stale in (db_path, f"{db_path}-wal", f"{db_path}-shm"):
        Path(stale).unlink(missing_ok=True)
    child_log.unlink(missing_ok=True)

    print(f"\nHIVE-118 load spike — {N_SESSIONS} parallel sessions, one daemon\n")
    width = max(len(name) for name, _, _ in checks)
    all_ok = True
    for name, ok, detail in checks:
        all_ok &= ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {name.ljust(width)}  {detail}")
    print(f"\n{'ALL PASS' if all_ok else 'FAILED'} — "
          f"{sum(ok for _, ok, _ in checks)}/{len(checks)} checks\n")
    return 0 if all_ok else 1


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--serve":
        _serve(os.environ[_TOKEN_ENV], int(sys.argv[2]), os.environ[_DB_ENV])
    else:
        raise SystemExit(main())
