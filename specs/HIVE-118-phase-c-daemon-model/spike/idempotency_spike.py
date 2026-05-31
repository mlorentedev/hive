"""HIVE-118 Phase C — write-idempotency spike (ADR-011 §6.2).

De-risks the decided design: a `vault_write`/`vault_patch` carries a client
idempotency key; the daemon (and the stdio fallback) consult one applied-key
store so a retried write after a mid-call daemon death is a **no-op**
(at-most-once), and — crucially — this is safe for **append** mode, where a
naive content check cannot tell "already applied" from "two identical appends".

The spike daemon owns one SQLite DB with a `UNIQUE` idempotency column and an
`INSERT OR IGNORE` write, guarded by an intra-process lock (ADR-004). Checks:

1. **Duplicate key is a no-op (sequential)** — the same key twice writes once.
2. **Distinct keys insert** — different keys each write.
3. **Concurrent duplicates dedupe to one** — N racing calls with one key write
   exactly one row (the UNIQUE constraint + lock hold under concurrency).

Run::

    uv run python specs/HIVE-118-phase-c-daemon-model/spike/idempotency_spike.py

Exit 0 = every check passed.
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
import tempfile
from pathlib import Path

import _spikelib as lib

RACERS = 12


def _serve(token: str, port: int, db_path: str) -> None:
    import threading

    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS writes "
        "(id INTEGER PRIMARY KEY, session TEXT, idem TEXT UNIQUE)",
    )
    conn.commit()
    lock = threading.Lock()
    mcp = lib.build_server("hive-idem-daemon", token)

    @mcp.tool
    async def write(session: str, idem_key: str) -> dict:
        """Idempotent write: INSERT OR IGNORE on the idempotency key.

        One owning connection + lock makes the check-and-insert atomic; the
        UNIQUE column makes a retry a no-op even under concurrency.
        """
        with lock:
            cur = conn.execute(
                "INSERT OR IGNORE INTO writes (session, idem) VALUES (?, ?)",
                (session, idem_key),
            )
            conn.commit()
            (count,) = conn.execute("SELECT COUNT(*) FROM writes").fetchone()
        return {"count": int(count), "inserted": cur.rowcount == 1}

    lib.serve(mcp, port)


async def _drive(url: str, token: str) -> list[tuple[str, bool, str]]:
    checks: list[tuple[str, bool, str]] = []
    async with lib.make_client(url, token) as client:
        async def write(idem: str) -> dict:
            r = await client.call_tool("write", {"session": "s1", "idem_key": idem})
            return dict(getattr(r, "data", r))

        first = await write("k1")
        dup = await write("k1")
        checks.append((
            "duplicate key is a no-op (sequential)",
            first["inserted"] and not dup["inserted"] and dup["count"] == 1,
            f"first={first}, dup={dup}",
        ))

        other = await write("k2")
        checks.append((
            "distinct keys insert",
            other["inserted"] and other["count"] == 2,
            f"{other}",
        ))

        # Concurrent duplicates: RACERS calls, all idem='k3', one warm connection.
        results = await asyncio.gather(*[write("k3") for _ in range(RACERS)])
        inserted = sum(1 for r in results if r["inserted"])
        final = max(r["count"] for r in results)
        checks.append((
            f"{RACERS} concurrent duplicates dedupe to one",
            inserted == 1 and final == 3,
            f"inserted={inserted}/{RACERS}, final_rows={final}",
        ))
    return checks


def main() -> int:
    token, port = lib.new_token(), lib.free_port()
    db_path = str(Path(tempfile.gettempdir()) / f"hive-idem-{port}.db")
    proc, child_log = lib.spawn_daemon(
        __file__, port, {lib.TOKEN_ENV: token, lib.DB_ENV: db_path},
    )
    checks: list[tuple[str, bool, str]] = []
    try:
        if not lib.wait_ready(port):
            checks = [("daemon ready", False, lib.log_tail(child_log))]
        else:
            checks = asyncio.run(_drive(lib.url_for(port), token))
    finally:
        code = lib.report("HIVE-118 idempotency spike — at-most-once writes", checks)
        lib.cleanup(
            proc, db_path, f"{db_path}-wal", f"{db_path}-shm", child_log,
        )
    return code


if __name__ == "__main__":
    import os

    if len(sys.argv) >= 3 and sys.argv[1] == "--serve":
        _serve(os.environ[lib.TOKEN_ENV], int(sys.argv[2]), os.environ[lib.DB_ENV])
    else:
        raise SystemExit(main())
