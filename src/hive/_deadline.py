"""Hard deadline supervisor — ``bounded_call`` + cross-OS Popen termination.

Implements the canonical "hard deadline with subprocess termination
authority" pattern documented as primitive 7 of
[[pattern-multi-process-mcp-server]] in the vault, and decided in
[[adr-008-hard-deadline-enforcement]].

Closes hive issue #111: a tool call that registers a subprocess (git
add/commit) and exceeds its deadline now terminates the subprocess on
deadline expiry, removes any ``.git/index.lock`` whose PID matches one
we spawned, and surfaces a ``TimeoutError`` to the caller — replacing
the previous behaviour where ``asyncio.timeout`` would cancel only at
``await`` points and silently let a subprocess run unbounded.

Cross-OS termination chain (per ADR-008 §3):

- POSIX: ``start_new_session=True`` at Popen creation +
  ``os.killpg(os.getpgid(pid), SIGTERM)`` then SIGKILL after grace.
  Reaches descendant process groups (git → git-add → git-pack).
- Windows: ``creationflags=subprocess.CREATE_NEW_PROCESS_GROUP`` at
  Popen creation + ``Popen.terminate()`` (TerminateProcess) twice
  (no graceful kill on Windows; second call ensures the group drops).

``.git/index.lock`` cleanup is PID-owned only: we read the PID from
the lock file and only unlink when it matches a PID we spawned. This
preserves obsidian-git's lock if the race lands the wrong way.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import sys
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

_log = logging.getLogger(__name__)

_DEFAULT_GRACE_S = 2.0
_DRAIN_TIMEOUT_S = 0.5

IS_WINDOWS = sys.platform == "win32"


# ── Cross-OS Popen creation helpers ─────────────────────────────────────


def popen_creation_kwargs() -> dict[str, Any]:
    """Platform-specific kwargs to make a Popen terminatable as a group.

    On POSIX, ``start_new_session=True`` puts the child in a new process
    group whose leader is the child itself, so ``os.killpg(pid, SIG)``
    reaches descendants (relevant for git invocations that fork helpers
    like ``git-pack-objects``).

    On Windows, ``creationflags=CREATE_NEW_PROCESS_GROUP`` lets
    ``TerminateProcess`` reach the child process tree.

    Return type is ``dict[str, Any]`` because mypy cannot unify the two
    branches against ``subprocess.Popen``'s overloaded signatures; this
    is the canonical "platform-specific kwargs bundle" trade-off.
    """
    if IS_WINDOWS:
        # CREATE_NEW_PROCESS_GROUP only exists on Windows; mypy on POSIX
        # cannot resolve the symbol, so dereference via getattr.
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


# ── Termination primitives ──────────────────────────────────────────────


def _send_terminate(proc: subprocess.Popen[bytes]) -> bool:
    """First-stage termination. Returns True if a signal was actually sent."""
    if proc.poll() is not None:
        return False
    try:
        if IS_WINDOWS:
            proc.terminate()
            return True
        # POSIX: reach the process group if start_new_session was set
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            # Process already gone between poll() and getpgid()
            return False
    except OSError as exc:
        _log.debug("terminate failed for pid=%s: %s", proc.pid, exc)
        return False
    return True


def _send_kill(proc: subprocess.Popen[bytes]) -> None:
    """Second-stage termination. POSIX: SIGKILL. Windows: TerminateProcess again."""
    if proc.poll() is not None:
        return
    try:
        if IS_WINDOWS:
            # No graceful kill exists on Windows; re-fire TerminateProcess
            # so descendant processes in the new group drop on retry.
            proc.terminate()
            return
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    except OSError as exc:
        _log.debug("kill failed for pid=%s: %s", proc.pid, exc)


async def _terminate_registry(
    registry: list[subprocess.Popen[bytes]],
    grace_s: float,
) -> list[int]:
    """Terminate every Popen in ``registry``; return PIDs we signalled.

    Iteration sequence: SIGTERM → ``await asyncio.sleep(grace_s)`` → SIGKILL
    survivors → drain stdio best-effort. Returns PIDs that received a
    termination signal (matches ADR-008 §1 step 6 structured payload).
    """
    signalled: list[int] = []
    for proc in list(registry):
        if _send_terminate(proc):
            signalled.append(proc.pid)
    if not signalled:
        return []
    await asyncio.sleep(grace_s)
    for proc in list(registry):
        if proc.poll() is None:
            _send_kill(proc)
    # Drain stdio in a thread so a blocked pipe cannot stall the supervisor.
    for proc in list(registry):
        try:
            await asyncio.wait_for(
                asyncio.to_thread(proc.communicate),
                timeout=_DRAIN_TIMEOUT_S,
            )
        except (TimeoutError, OSError, ValueError):
            # ValueError fires when communicate is called twice on the
            # same Popen; we accept best-effort drain failure.
            continue
    return signalled


# ── .git/index.lock cleanup ─────────────────────────────────────────────


def _cleanup_index_lock(vault: Path, our_pids: list[int]) -> None:
    """Remove ``vault/.git/index.lock`` ONLY if the PID inside is ours.

    Reads the first line of the lock file (git writes the owning PID
    there) and unlinks only on match against any PID in ``our_pids``.
    Foreign locks (obsidian-git, manual ``git commit`` from a shell)
    are left untouched — the safety guarantee documented in ADR-008 §4.
    """
    lock_path = vault / ".git" / "index.lock"
    if not lock_path.exists():
        return
    try:
        contents = lock_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        _log.debug("cannot read index.lock at %s: %s", lock_path, exc)
        return
    if not contents:
        return
    try:
        owner = int(contents.splitlines()[0].strip())
    except (ValueError, IndexError):
        # Lock file present but unparseable PID — never touch.
        _log.debug(
            "index.lock at %s has unparseable owner %r; leaving in place",
            lock_path, contents,
        )
        return
    if owner not in our_pids:
        _log.info(
            "mcp.index_lock.skip_cleanup owner_pid=%s our_pids=%s",
            owner, our_pids,
        )
        return
    try:
        lock_path.unlink()
    except OSError as exc:
        _log.warning("could not unlink own index.lock pid=%s: %s", owner, exc)
        return
    _log.info("mcp.index_lock.cleaned_own pid=%s", owner)


# ── The supervisor ──────────────────────────────────────────────────────


async def bounded_call[T](
    fn: Callable[..., Awaitable[T]],
    *args: object,
    deadline_s: float,
    process_registry: list[subprocess.Popen[bytes]] | None = None,
    grace_s: float = _DEFAULT_GRACE_S,
    tool_name: str | None = None,
    vault_for_index_cleanup: Path | None = None,
    **kwargs: object,
) -> T:
    """Run ``fn(*args, **kwargs)`` with a hard wall-clock deadline.

    On deadline expiry:

    1. ``asyncio.timeout`` cancels the inner future at the next await
       point.
    2. Every still-running ``Popen`` in ``process_registry`` receives
       SIGTERM (POSIX) or TerminateProcess (Windows).
    3. After ``grace_s`` of cooperative shutdown, survivors receive
       SIGKILL (POSIX) or a second TerminateProcess (Windows).
    4. Stdio is drained best-effort.
    5. If ``vault_for_index_cleanup`` is set, ``_cleanup_index_lock``
       removes our own ``.git/index.lock`` (PID-matched).
    6. ``GHOST_RESPONSES.record(source="deadline")`` for operator
       triage in ``vault_health`` (per pre-PR-3 audit B4).
    7. Raises ``TimeoutError`` with a structured one-line message.

    The registry is **mutated by the caller** — callers append every
    Popen they spawn and remove on normal exit (see ``_helpers._run_git``).
    On expiry the supervisor iterates whatever is still in the list.
    """
    start = time.monotonic()
    try:
        async with asyncio.timeout(deadline_s):
            return await fn(*args, **kwargs)
    except TimeoutError:
        elapsed = time.monotonic() - start
        registry = process_registry or []
        killed = await _terminate_registry(registry, grace_s)
        if vault_for_index_cleanup is not None and killed:
            _cleanup_index_lock(vault_for_index_cleanup, killed)
        try:
            from hive._compat import GHOST_RESPONSES

            GHOST_RESPONSES.record(tool=tool_name, source="deadline")
        except Exception as exc:  # noqa: BLE001
            # Observability must never crash the timeout path.
            _log.debug("ghost_responses.record failed: %s", exc)
        _log.warning(
            "mcp.bounded_call.deadline_exceeded tool=%s elapsed_s=%.1f "
            "deadline_s=%.1f killed_pids=%s",
            tool_name or "<unknown>", elapsed, deadline_s, killed,
        )
        # HIVE-116 AC-1: cooperative filelock eviction. Mirrors the
        # ``tool_span`` branch; both supervisors share the eviction
        # primitive so direct ``bounded_call`` callers (tests, future
        # tooling) get the same unblocking behaviour as the MCP tool
        # wrapper path.
        if vault_for_index_cleanup is not None and killed:
            from hive._helpers import _drain_and_evict

            await _drain_and_evict(
                vault_for_index_cleanup, killed,
                tool_name or "<unknown>",
            )
        raise TimeoutError(
            f"bounded_call exceeded deadline "
            f"(elapsed {elapsed:.1f}s > {deadline_s:.1f}s); "
            f"terminated {len(killed)} subprocess(es)",
        ) from None
