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
import contextlib
import json
import os
import sys
from asyncio import create_subprocess_exec as _spawn_subprocess
from asyncio.subprocess import PIPE
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
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
_STDERR_TAIL_BYTES = 8192

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


async def _spawn(tmp_path: Path, script: str) -> asyncio.subprocess.Process:
    vault = tmp_path / "vault"
    for sub in ("10_projects", "00_meta", "50_work"):
        (vault / sub).mkdir(parents=True, exist_ok=True)
    db_dir = tmp_path / "db"
    db_dir.mkdir(exist_ok=True)
    # A developer's own HIVE_VAULT_PATH outranks VAULT_PATH, so inheriting it
    # pointed the server at the real vault.
    env = {k: v for k, v in os.environ.items() if not k.startswith("HIVE_") and k != "VAULT_PATH"}
    env["HIVE_VAULT_PATH"] = str(vault)
    env["HIVE_LOG_PATH"] = str(db_dir / "hive.log")
    env["HIVE_DB_PATH"] = str(db_dir / "worker.db")
    env["HIVE_RELEVANCE_DB_PATH"] = str(db_dir / "relevance.db")
    env["HIVE_LESSON_DB_PATH"] = str(db_dir / "lesson_reinforcement.db")
    return await _spawn_subprocess(
        sys.executable,
        "-c",
        script,
        stdin=PIPE,
        stdout=PIPE,
        stderr=PIPE,
        env=env,
    )


async def _read_stderr(proc: asyncio.subprocess.Process, sink: bytearray) -> None:
    # Read as it arrives: an unread pipe that fills up blocks the server.
    assert proc.stderr is not None
    while chunk := await proc.stderr.read(65536):
        sink += chunk


@contextlib.asynccontextmanager
async def _running_server(tmp_path: Path, script: str) -> AsyncIterator[asyncio.subprocess.Process]:
    """An initialized server whose stderr is attached to any failure (#344).

    A server that dies shows up on this side as ``Connection lost`` or a missing
    answer. Its own traceback, on its stderr, is what tells a regression from a
    flake.
    """
    proc = await _spawn(tmp_path, script)
    stderr = bytearray()
    reader = asyncio.create_task(_read_stderr(proc, stderr))
    try:
        await _send(proc, _INIT_MSG)
        if not await _drain_with_id(proc, 1, timeout=15.0, first_only=True):
            raise RuntimeError("server did not answer initialize")
        await _send(proc, _INITIALIZED)
        yield proc
    except Exception as exc:
        await _shutdown(proc)
        await reader
        tail = bytes(stderr[-_STDERR_TAIL_BYTES:])
        exc.add_note(
            f"server stderr (last {len(tail)} of {len(stderr)} bytes):\n"
            + tail.decode("utf-8", "replace")
        )
        raise
    finally:
        await _shutdown(proc)
        await reader


async def _shutdown(proc: asyncio.subprocess.Process) -> None:
    if proc.stdin is not None and not proc.stdin.is_closing():
        proc.stdin.close()
    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
    except TimeoutError:
        proc.kill()
        await proc.wait()


async def test_cancelled_calls_do_not_kill_the_server(tmp_path: Path) -> None:
    async with _running_server(tmp_path, _SERVER_SCRIPT) as proc:
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


async def test_a_dead_server_reports_its_stderr(tmp_path: Path) -> None:
    """A failure carries the server's own traceback, not just 'Connection lost' (#344)."""
    crash = "import sys\nsys.stderr.write('Traceback: server-side-marker\\n')\nsys.exit(3)\n"
    with pytest.raises(RuntimeError, match="did not answer initialize") as excinfo:
        async with _running_server(tmp_path, crash):
            pass

    assert "server-side-marker" in "\n".join(excinfo.value.__notes__)


async def test_the_server_runs_against_the_test_vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HIVE_VAULT_PATH", str(tmp_path / "someone-elses-vault"))
    report = "from hive.config import settings\nprint(settings.vault_path, flush=True)\n"

    proc = await _spawn(tmp_path, report)
    out, _ = await proc.communicate()

    assert out.decode().strip() == str(tmp_path / "vault")
