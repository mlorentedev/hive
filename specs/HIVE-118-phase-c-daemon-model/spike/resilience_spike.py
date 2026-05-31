"""HIVE-118 Phase C — resilience spike (ADR-011 §4 crash-only).

De-risks the load-bearing resilience claims before the daemon is built:

1. **State durable after SIGKILL** — write rows, hard-kill the daemon
   (``SIGKILL`` / ``TerminateProcess``, i.e. no graceful shutdown), then read
   the SQLite file directly: the committed rows survive (WAL durability).
2. **No corruption after SIGKILL** — ``PRAGMA integrity_check`` is clean.
3. **Client reconnects to a restarted daemon** — start a fresh daemon on the
   same DB, reconnect, write more, total reflects both runs (single owner,
   state shared across the restart).
4. **Daemon survives a client disconnect mid-call** — a client that abandons a
   slow call (drops mid-flight) does not take the daemon down; another session
   keeps working (no hang, no leaked state).

These map to the "Supervised recovery", "crash-safe state", and "metrics
survive disconnect" acceptance criteria — minus the supervisor itself (that is
the systemd/launchd unit, validated post-build).

Run::

    uv run python specs/HIVE-118-phase-c-daemon-model/spike/resilience_spike.py

Exit 0 = every check passed.
"""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

import _spikelib as lib

ROWS_BEFORE = 20
ROWS_AFTER = 10
SLOW_S = 0.5


def _serve(token: str, port: int, db_path: str) -> None:
    import threading

    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE IF NOT EXISTS rows (id INTEGER PRIMARY KEY)")
    conn.commit()
    lock = threading.Lock()
    mcp = lib.build_server("hive-resilience-daemon", token)

    @mcp.tool
    async def write() -> int:
        with lock:
            conn.execute("INSERT INTO rows DEFAULT VALUES")
            conn.commit()
            (count,) = conn.execute("SELECT COUNT(*) FROM rows").fetchone()
        return int(count)

    @mcp.tool
    async def ping() -> str:
        return "pong"

    @mcp.tool
    async def slow() -> str:
        await asyncio.to_thread(time.sleep, SLOW_S)
        return "done"

    lib.serve(mcp, port)


def _db_rows(db_path: str) -> int:
    with contextlib.suppress(Exception):
        conn = sqlite3.connect(db_path)
        try:
            (n,) = conn.execute("SELECT COUNT(*) FROM rows").fetchone()
            return int(n)
        finally:
            conn.close()
    return -1


def _db_ok(db_path: str) -> str:
    with contextlib.suppress(Exception):
        conn = sqlite3.connect(db_path)
        try:
            (res,) = conn.execute("PRAGMA integrity_check").fetchone()
            return str(res)
        finally:
            conn.close()
    return "unreadable"


async def _write_n(url: str, token: str, n: int) -> int:
    async with lib.make_client(url, token) as client:
        last = 0
        for _ in range(n):
            r = await client.call_tool("write", {})
            last = int(getattr(r, "data", r))
        return last


async def _disconnect_then_serve(url: str, token: str) -> bool:
    """Abandon a slow call mid-flight, then prove another session still works."""
    victim = lib.make_client(url, token)
    await victim.__aenter__()
    task = asyncio.create_task(victim.call_tool("slow", {}))
    await asyncio.sleep(0.1)  # let slow() land on the daemon
    task.cancel()
    # CancelledError subclasses BaseException, not Exception — suppress it
    # explicitly, else cancelling the abandoned call escapes the teardown.
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task
    with contextlib.suppress(Exception):
        await victim.__aexit__(None, None, None)  # drop the connection mid-call
    # A fresh session must still be served by the (surviving) daemon.
    async with lib.make_client(url, token) as healthy:
        r = await healthy.call_tool("ping", {})
    return "pong" in str(getattr(r, "data", r))


def main() -> int:
    token = lib.new_token()
    db_path = str(Path(tempfile.gettempdir()) / f"hive-resilience-{lib.free_port()}.db")
    checks: list[tuple[str, bool, str]] = []
    proc_a = proc_b = None
    logs: list[Path] = []
    try:
        # ── run 1: write, then HARD-kill (no graceful shutdown) ──
        port_a = lib.free_port()
        proc_a, log_a = lib.spawn_daemon(
            __file__, port_a, {lib.TOKEN_ENV: token, lib.DB_ENV: db_path},
        )
        logs.append(log_a)
        if not lib.wait_ready(port_a):
            checks.append(("daemon A ready", False, lib.log_tail(log_a)))
            return lib.report("HIVE-118 resilience spike", checks)

        written = asyncio.run(_write_n(lib.url_for(port_a), token, ROWS_BEFORE))
        disconnect_ok = asyncio.run(_disconnect_then_serve(lib.url_for(port_a), token))
        checks.append((
            "daemon survives client disconnect mid-call",
            disconnect_ok, "served a new session after a dropped slow() call",
        ))

        proc_a.kill()  # SIGKILL / TerminateProcess — simulate a crash
        proc_a.wait(timeout=5)

        rows = _db_rows(db_path)
        checks.append((
            "state durable after SIGKILL",
            rows == ROWS_BEFORE, f"rows={rows}/{ROWS_BEFORE} (wrote {written})",
        ))
        integrity = _db_ok(db_path)
        checks.append((
            "no corruption after SIGKILL", integrity == "ok", f"integrity_check={integrity}",
        ))

        # ── run 2: restart on the same DB, reconnect, write more ──
        port_b = lib.free_port()
        proc_b, log_b = lib.spawn_daemon(
            __file__, port_b, {lib.TOKEN_ENV: token, lib.DB_ENV: db_path},
        )
        logs.append(log_b)
        if not lib.wait_ready(port_b):
            checks.append(("daemon B ready (restart)", False, lib.log_tail(log_b)))
        else:
            total = asyncio.run(_write_n(lib.url_for(port_b), token, ROWS_AFTER))
            checks.append((
                "client reconnects to restarted daemon; state shared",
                total == ROWS_BEFORE + ROWS_AFTER,
                f"total={total}/{ROWS_BEFORE + ROWS_AFTER}",
            ))
    finally:
        code = lib.report("HIVE-118 resilience spike — crash-only durability", checks)
        lib.cleanup(proc_a, db_path, f"{db_path}-wal", f"{db_path}-shm")
        lib.cleanup(proc_b, *logs)
    return code


if __name__ == "__main__":
    import os

    if len(sys.argv) >= 3 and sys.argv[1] == "--serve":
        _serve(os.environ[lib.TOKEN_ENV], int(sys.argv[2]), os.environ[lib.DB_ENV])
    else:
        raise SystemExit(main())
