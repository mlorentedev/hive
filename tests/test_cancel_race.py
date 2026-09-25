"""Cancelled tool calls must not kill the server (python-sdk#2416, #434).

On mcp 1.x the response a handler produced after its request was cancelled
tripped ``assert not self._completed`` in ``RequestResponder.respond``, the
assertion reached the stdio receive loop's task group, and the process exited;
every later call got ``Connection closed``. hive carried a monkey-patch for it
until #434 moved to mcp 2.x, whose dispatcher never answers a cancelled
request (``PeerCancelMode``: "the handler's eventual result or error is
dropped, not written").

The server is hive's own, plus one sync tool that sleeps in a worker thread:
work that cannot observe cancellation, so its response is always produced after
the cancel (the shape of the python-sdk#2416 repro). A fast tool such as
``vault_list`` notices the cancel in time and never reaches the race.

This replaces a diagnostic classifier (lesson 097), which measured the outcome
of the race on 1.x. On 2.x the outcome is specified, so the test gates: a
cancelled call gets at most one frame, and the server answers the next call.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from asyncio import create_subprocess_exec as _spawn_subprocess
from asyncio.subprocess import PIPE
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

_INIT_MSG: dict[str, object] = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "hive-cancel-race", "version": "0.0.0"},
    },
}

_INITIALIZED: dict[str, object] = {
    "jsonrpc": "2.0",
    "method": "notifications/initialized",
    "params": {},
}

_CANCELLED_CALLS = 3
_SLOW_S = 1.0

# hive's server with one uncancellable tool: FastMCP runs a sync ``def`` tool on
# a worker thread, which keeps running after the request is cancelled.
_SERVER_SCRIPT = f"""
import time
from hive.server import create_server

server = create_server()

@server.tool
def slow() -> str:
    time.sleep({_SLOW_S})
    return "done"

server.run()
"""


def _tool_call(call_id: int, name: str) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": call_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": {}},
    }


async def _send(proc: asyncio.subprocess.Process, payload: dict[str, object]) -> None:
    assert proc.stdin is not None
    proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
    await proc.stdin.drain()


async def _drain_with_id(
    proc: asyncio.subprocess.Process,
    target_id: int,
    timeout: float,
    *,
    first_only: bool = False,
) -> list[dict[str, object]]:
    """Collect frames whose ``id`` is ``target_id`` until ``timeout`` or EOF.

    ``first_only`` returns on the first match; otherwise the whole window is
    read, so a duplicate answer is caught.
    """
    assert proc.stdout is not None
    msgs: list[dict[str, object]] = []
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while (remaining := deadline - loop.time()) > 0:
        try:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
        except TimeoutError:
            break
        if not line:
            break
        try:
            msg = json.loads(line.decode("utf-8"))
        except json.JSONDecodeError:
            continue
        if msg.get("id") == target_id:
            msgs.append(msg)
            if first_only:
                break
    return msgs


async def _spawn(vault: Path, db_dir: Path) -> asyncio.subprocess.Process:
    env = os.environ.copy()
    env["VAULT_PATH"] = str(vault)
    env["HIVE_LOG_PATH"] = str(db_dir / "hive.log")
    env["HIVE_DB_PATH"] = str(db_dir / "worker.db")
    env["HIVE_RELEVANCE_DB_PATH"] = str(db_dir / "relevance.db")
    env["HIVE_LESSON_DB_PATH"] = str(db_dir / "lesson_reinforcement.db")
    proc = await _spawn_subprocess(
        sys.executable,
        "-c",
        _SERVER_SCRIPT,
        stdin=PIPE,
        stdout=PIPE,
        stderr=PIPE,
        env=env,
    )
    await _send(proc, _INIT_MSG)
    init = await _drain_with_id(proc, 1, timeout=15.0, first_only=True)
    if not init:
        raise RuntimeError("server did not answer initialize")
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


async def test_cancelled_calls_do_not_kill_the_server(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    for sub in ("10_projects", "00_meta", "50_work"):
        (vault / sub).mkdir(parents=True)
    db_dir = tmp_path / "db"
    db_dir.mkdir()

    proc = await _spawn(vault, db_dir)
    try:
        for call_id in range(100, 100 + _CANCELLED_CALLS):
            await _send(proc, _tool_call(call_id, "slow"))
            await _send(
                proc,
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/cancelled",
                    "params": {"requestId": call_id, "reason": "cancel-race"},
                },
            )
            # Outlast the sleep so the late response is produced inside the window.
            frames = await _drain_with_id(proc, call_id, timeout=_SLOW_S + 0.5)
            assert len(frames) <= 1, f"call {call_id} answered twice: {frames!r}"

        await _send(proc, _tool_call(999, "vault_list"))
        final = await _drain_with_id(proc, 999, timeout=15.0, first_only=True)
        assert proc.returncode is None, "server exited after the cancelled calls"
        assert len(final) == 1, f"no single answer after the cancelled calls: {final!r}"
        assert "result" in final[0], f"the call after the race failed: {final[0]!r}"
    finally:
        await _shutdown(proc)
