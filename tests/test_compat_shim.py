"""Empirical classifier for the cancellation-race in ``_compat._patched_respond``.

Resolves the [BLOCKING] risk in
``specs/HIVE-104-write-throughput/proposal.md`` (Risk #1).

The question: when the client sends ``notifications/cancelled`` while a
``tools/call`` handler is still running, what actually reaches the wire?

- **(a) ErrorData wins** — ``RequestResponder.cancel()`` calls
  ``_send_response(ErrorData)`` at session.py:148-150 BEFORE our
  ``_compat._patched_respond`` fires. If the cancellation ack reaches the
  writer, our handler's late success would create a DUPLICATE response
  sharing the same ``request_id``. Some MCP clients treat that as a
  protocol error.
- **(b) Our success wins** — anyio scope cancellation kills the
  ``cancel()`` send mid-flight before it flushes. Only our late success
  reaches the wire. This is what ADR-007's original "best-effort raw send"
  plan assumed.
- **(c) Both lost** — both sends are cancelled. Wire silent. Status quo
  with our current silent-suppress.

We classify by spawning a real hive subprocess (mirroring the pattern in
``test_transport_recovery.py``), driving the race N times, and inspecting
the JSON-RPC frames per iteration.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from asyncio import create_subprocess_exec as _spawn_subprocess
from asyncio.subprocess import PIPE
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


_INIT_MSG: dict[str, object] = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "hive-104-classifier", "version": "0.0.0"},
    },
}

_INITIALIZED: dict[str, object] = {
    "jsonrpc": "2.0",
    "method": "notifications/initialized",
    "params": {},
}


async def _send(proc: asyncio.subprocess.Process, payload: dict[str, object]) -> None:
    assert proc.stdin is not None
    proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
    await proc.stdin.drain()


async def _drain_with_id(
    proc: asyncio.subprocess.Process,
    target_id: int,
    timeout: float = 2.0,
) -> list[dict[str, object]]:
    """Drain stdout up to ``timeout`` seconds, collecting all messages whose ``id`` matches."""
    assert proc.stdout is not None
    msgs: list[dict[str, object]] = []
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            return msgs
        try:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
        except TimeoutError:
            return msgs
        if not line:
            return msgs
        try:
            msg = json.loads(line.decode("utf-8"))
        except json.JSONDecodeError:
            continue
        if msg.get("id") == target_id:
            msgs.append(msg)


async def _spawn(vault: Path, db_dir: Path) -> asyncio.subprocess.Process:
    env = os.environ.copy()
    env["VAULT_PATH"] = str(vault)
    env["HIVE_LOG_PATH"] = str(db_dir / "hive.log")
    env["HIVE_DB_PATH"] = str(db_dir / "worker.db")
    env["HIVE_RELEVANCE_DB_PATH"] = str(db_dir / "relevance.db")
    env["HIVE_LESSON_DB_PATH"] = str(db_dir / "lesson_reinforcement.db")
    env["HIVE_LOG_LEVEL"] = "DEBUG"
    proc = await _spawn_subprocess(
        sys.executable, "-m", "hive.server",
        stdin=PIPE, stdout=PIPE, stderr=PIPE,
        env=env,
    )
    await _send(proc, _INIT_MSG)
    assert proc.stdout is not None
    while True:
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=15.0)
        if not line:
            raise RuntimeError("server closed stdout during init")
        msg = json.loads(line.decode("utf-8"))
        if msg.get("id") == 1:
            break
    await _send(proc, _INITIALIZED)
    return proc


async def _shutdown(proc: asyncio.subprocess.Process) -> None:
    if proc.stdin is not None and not proc.stdin.is_closing():
        proc.stdin.close()
    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
    except TimeoutError:
        proc.kill()
        await proc.wait()


def _classify(messages: list[dict[str, object]]) -> tuple[str, str]:
    """Map collected messages to scenario (a/b/c/?)."""
    if len(messages) == 0:
        return ("c", "no response observed — both sends lost to anyio cancellation")
    if len(messages) >= 2:
        kinds = [("error" if "error" in m else "result") for m in messages]
        return ("a+", f"DUPLICATE responses ({len(messages)} frames, kinds={kinds})")
    [msg] = messages
    if "error" in msg:
        err = msg["error"]
        code = err.get("code") if isinstance(err, dict) else None
        return ("a", f"single ErrorData (code={code}) — cancel ack reached wire")
    if "result" in msg:
        return ("b", "single success result — our handler's response delivered")
    return ("?", f"unrecognized frame shape: {msg!r}")


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Linux/macOS classifier for HIVE-104 Risk #1 (Windows timing differs)",
)
@pytest.mark.asyncio
async def test_classify_cancellation_race(tmp_path: Path) -> None:
    """Drive cancellation race N times against a real hive subprocess; report scenario distribution.

    This is NOT a pass/fail correctness test — it is an empirical classifier
    whose output dictates the Fase C design in HIVE-104. The assertion at the
    end only verifies all iterations were accounted for.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "10_projects").mkdir()
    (vault / "00_meta").mkdir()
    (vault / "50_work").mkdir()
    db_dir = tmp_path / "db"
    db_dir.mkdir()

    iterations = 20
    counts: dict[str, int] = {"a": 0, "a+": 0, "b": 0, "c": 0, "?": 0}
    samples: dict[str, tuple[int, str, list[dict[str, object]]]] = {}

    proc = await _spawn(vault, db_dir)
    try:
        for i in range(iterations):
            call_id = 100 + i
            await _send(proc, {
                "jsonrpc": "2.0",
                "id": call_id,
                "method": "tools/call",
                "params": {"name": "vault_list", "arguments": {}},
            })
            await _send(proc, {
                "jsonrpc": "2.0",
                "method": "notifications/cancelled",
                "params": {"requestId": call_id, "reason": "race-classifier"},
            })
            msgs = await _drain_with_id(proc, call_id, timeout=2.0)
            scenario, detail = _classify(msgs)
            counts[scenario] = counts.get(scenario, 0) + 1
            if scenario not in samples:
                samples[scenario] = (i, detail, msgs)
    finally:
        await _shutdown(proc)

    print("\n" + "=" * 70)
    print(f"HIVE-104 cancellation-race classifier (N={iterations})")
    print("=" * 70)
    print(f"  (a)  ErrorData wins (cancel ack delivered, success suppressed): {counts['a']}/{iterations}")
    print(f"  (a+) DUPLICATE responses (both delivered):                      {counts['a+']}/{iterations}")
    print(f"  (b)  Our success wins (cancel ack lost mid-flight):             {counts['b']}/{iterations}")
    print(f"  (c)  Both lost (silent):                                        {counts['c']}/{iterations}")
    print(f"  (?)  Unknown frame shape:                                       {counts['?']}/{iterations}")
    print("\nFirst observed sample per scenario:")
    for sc, (idx, detail, msgs) in samples.items():
        print(f"\n  [{sc}] iter={idx} — {detail}")
        for j, m in enumerate(msgs[:2]):
            print(f"      frame[{j}]: {json.dumps(m)[:250]}")
    print("=" * 70)

    total = sum(counts.values())
    assert total == iterations, f"lost iterations: total={total}, expected={iterations}"
