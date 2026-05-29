---
id: adr-008-hard-deadline-enforcement
type: adr
status: active
created: "2026-05-21"
---

# ADR-008: Hard Deadline Enforcement via `bounded_call` Supervisor

## Status

Proposed (2026-05-21) — Phase B piece of HIVE-115, shipping in the v1.16.0 release bundle alongside [adr-009-multi-process-wal-policy.md](adr-009-multi-process-wal-policy.md) v1 + [adr-010-external-committer-coexistence.md](adr-010-external-committer-coexistence.md) + the Outbox + Reconciler (ADR-009 v2 amendment). Closes issue #111.

## Context

[adr-005-transport-and-scale.md](adr-005-transport-and-scale.md) introduced `tool_span`: an `asyncio.timeout(ctx.tool_timeout)` context manager wrapping every async tool handler. It was the first defense-in-depth layer of a four-tier model (documented in the maintainer's cross-project async-threading pattern):

1. `asyncio.timeout()` on async tool handlers — this ADR's predecessor.
2. `Lock.acquire(timeout=30)` on thread-blocking points.
3. `subprocess.run(..., timeout=30)` on child processes.
4. `httpx` per-request timeouts.

Each layer enforces its own deadline correctly at its own execution model. The model was thought to provide an end-to-end deadline contract: `HIVE_TOOL_TIMEOUT=60` should bound any tool call to 60 seconds.

Two months of production revealed that **the composition does not enforce a global deadline**.

### Empirical evidence (issue #111)

```text
2026-05-21 19:10:48,690 INFO  Processing request of type CallToolRequest
2026-05-21 19:24:47,047 WARNING git commit timed out for ['10_projects\hive\90-lessons.md']
2026-05-21 19:24:47,047 INFO  mcp ok method=tools/call tool=capture_lesson id=11 elapsed_ms=838360
```

`capture_lesson` returned successfully after **838 seconds** while `ctx.tool_timeout = 60`. 14× over contract.

Additional repro cases from a 2026-05-22 follow-up session attached to the same issue:

| Tool | Configured `tool_timeout` | Observed elapsed | Client-visible result |
|---|---|---|---|
| `capture_lesson` (worker path) | 60s | 838s | hive.log shows "ok" 14m later; client got no reply or treated as rejection |
| `capture_lesson` (inline path) | 60s | >60s (write completed) | client interpreted silence as user-rejection |
| `vault_commit` | 60s | >60s (commit eventually succeeded via obsidian-git tick) | client got canned "Server busy" string |
| `vault_patch` | 60s | 60s | hive's outer envelope returned the "timeout" message correctly |

Only `vault_patch` exits cleanly at 60s. Every other path runs longer than the deadline and produces a different visible failure mode (silent, rejected-by-user, canned-busy).

### Root cause: `asyncio.timeout` cannot interrupt non-async work

`asyncio.timeout(60)` cancels at `await` points. Once execution enters:

- `asyncio.to_thread(...)` → asyncio cancels the future, but Python has no portable way to interrupt the running thread. The thread continues until its own loop reaches a yield point that observes the cancellation.
- `Lock.acquire(timeout=30)` → enforces only its own 30s deadline. Composed with retries, can chain to 60+ seconds.
- `subprocess.run(..., timeout=30)` → enforces only its own 30s. Once the subprocess is started, the asyncio cancel cannot stop it; only the subprocess's own timeout can. If the subprocess respawns or chains, the budget compounds.
- `httpx.AsyncClient` calls without per-request timeout → inherit client `http_timeout` (default 60s in hive). Cancellation propagates but only at next yield point.

So a single tool call can chain: Ollama probe (cached, ~ms) → OpenRouter HTTP probe (60s) → `vault_write` (sync) → `_git_commit` (30s filelock + 30s subprocess) → repeat. Total wall time bounded only by the union of individual layer timeouts, not the global 60s envelope.

### Why this is the riskiest fix of HIVE-115

Killing a subprocess mid-flight is platform-specific and can leave shared state in inconsistent positions:

- **`.git/index.lock` stale state**: if hive's git subprocess holds the lock and we `SIGTERM` it mid-commit, the lock file remains on disk. Future git invocations (hive AND obsidian-git AND user manual) see a phantom lock until cleanup.
- **SQLite mid-transaction**: WAL guarantees rollback on next open, but the writer must wait for the WAL replay. Negligible most cases.
- **Partial subprocess output**: `Popen.terminate()` does not flush stdout/stderr buffers. Captured output may be truncated.
- **Windows specifics**: `Popen.terminate()` calls `TerminateProcess`, which is NOT graceful — no opportunity for child process cleanup. Subprocess descendants (git → git-add → git-pack) may orphan.

The decision must be robust against all of these.

## Decision

### 1. Introduce `bounded_call(fn, deadline_s, ...)` supervisor

A new helper in `src/hive/_helpers.py` (or `src/hive/_deadline.py` as standalone module). Signature roughly:

```python
async def bounded_call(
    fn: Callable[..., T],
    *args,
    deadline_s: float,
    process_registry: list[subprocess.Popen] | None = None,
    **kwargs,
) -> T:
    """Run fn with hard deadline. Terminates registered subprocesses on expiry."""
```

Behavior on deadline expiry:

1. Cancel the running future (asyncio mechanism, best-effort signal to worker thread).
2. For each `Popen` in `process_registry`: `terminate()` (POSIX `SIGTERM`, Windows `TerminateProcess`). Sleep 2s (grace period).
3. Any Popen still running: `kill()` (POSIX `SIGKILL`, Windows `TerminateJobObject` if `CREATE_NEW_PROCESS_GROUP` was set on creation).
4. Drain stdout/stderr buffers (best-effort).
5. Cleanup `.git/index.lock` **only if** our Popen.pid wrote it (verified by reading the PID in the lock file). Never touch another process's lock.
6. Raise `mcp.protocol.TimeoutError` with structured payload `{tool, deadline_s, elapsed_s, subprocess_killed: N}`.

The registry is passed **explicitly** as a parameter (not via `contextvars`), because `asyncio.to_thread` boundary makes contextvar propagation fragile across the async/sync layer.

### 2. Migrate `subprocess.run → subprocess.Popen` in all git callsites

`subprocess.run` is a one-shot helper that does not expose the Popen handle. The supervisor needs the handle. Refactor:

```python
# Before
result = subprocess.run(
    ["git", "add", *paths], capture_output=True, text=True, timeout=30, cwd=vault,
)

# After
with subprocess.Popen(
    ["git", "add", *paths], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    text=True, cwd=vault,
    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if IS_WINDOWS else 0,
) as proc:
    process_registry.append(proc)
    try:
        stdout, stderr = proc.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
        raise
    finally:
        process_registry.remove(proc)
```

Affected sites (5 in `_helpers.py` per HIVE-104 audit):

- `_git_commit(paths, message)` — `git add` + `git commit`
- `_git_commit_all(message)` — `git add -A` + `git commit`
- `_git_log_recent(...)` — `git log`
- `_current_head_sha()` — `git rev-parse` (probably fast enough to skip, but consistent)
- `git_status` callers in `vault_health` and `session_briefing`

### 3. Cross-OS termination chain

| OS | Step | Mechanism |
|---|---|---|
| POSIX | terminate() | `SIGTERM` |
| POSIX | grace | 2s sleep |
| POSIX | kill() | `SIGKILL` |
| Windows | Popen creation | `creationflags=CREATE_NEW_PROCESS_GROUP` (lets terminate() reach child tree) |
| Windows | terminate() | `TerminateProcess` |
| Windows | grace | 2s sleep |
| Windows | kill() | re-terminate (TerminateProcess is non-graceful by definition; second call ensures descendants drop) |

On Windows, `subprocess.Popen.terminate()` calls `TerminateProcess` on the immediate child. Without `CREATE_NEW_PROCESS_GROUP`, git's spawned helpers (`git-add.exe`, `git-pack.exe`) may orphan. With it, the entire process group goes down together.

### 4. `.git/index.lock` ownership check

```python
def _cleanup_index_lock(vault: Path, our_pid: int) -> None:
    lock_path = vault / ".git" / "index.lock"
    if not lock_path.exists():
        return
    try:
        lock_contents = lock_path.read_text().strip()
        if str(our_pid) == lock_contents:
            lock_path.unlink()
            log.info(f"Cleaned up own index.lock from pid={our_pid}")
        else:
            log.info(f"Skipping index.lock cleanup; owner is {lock_contents}, not us ({our_pid})")
    except OSError as e:
        log.warning(f"Could not check index.lock ownership: {e}")
```

Never touches a lock written by another PID. obsidian-git's lock is safe.

### 5. Client-visible error surface

On deadline expiry, the tool returns an `mcp.protocol.TimeoutError` (or hive-specific structured error string) — NOT silence, NOT a canned "Server busy" message, NOT a fake-success. The client gets a recognizable, retriable error within `deadline_s + grace_s` of the call start.

### 6. Wrap all tool handlers (replace `tool_span`)

`tool_span` (the current `asyncio.timeout`-only wrapper) is replaced by `bounded_call`. `wrap_sync_tool` decorator and all 7 vault tools + `delegate_task` + `capture_lesson` route through the new supervisor.

Backward compatibility: the env var `HIVE_TOOL_TIMEOUT` (default 60) is honored; only the enforcement mechanism changes. Tools see no API change.

## Alternatives considered

### A) Keep `asyncio.timeout`-only `tool_span`

**Rejected.** Empirical evidence shows it does not enforce the contract. 14× over deadline is a contract violation, not a slow path.

### B) Re-raise `CancelledError` aggressively in workers

**Rejected.** Pattern would require every worker function to check a cancellation token on every iteration / before every blocking call. Massively invasive across the codebase. Does not solve subprocess hangs (cannot check inside subprocess.run).

### C) Per-step timeouts only (status quo, abandon global deadline contract)

**Rejected.** Means `HIVE_TOOL_TIMEOUT` is meaningless documentation. Users have explicitly relied on it. Removing the contract is worse than enforcing it.

### D) Move to an out-of-process timeout watchdog (separate Python process)

**Rejected.** Inverts the orchestration model. Same cross-OS termination problem at a different layer. Phase C's daemon model (ADR-011) is a cleaner way to introduce a long-running supervisor; for Phase B, in-process supervisor is correct.

### E) Use `signal.SIGALRM` on POSIX for thread interruption

**Rejected.** SIGALRM is async-signal-safe but interacts badly with asyncio (handler runs in main thread regardless of which thread blocked). Windows has no equivalent. Not portable.

## Consequences

- **Positive**: `HIVE_TOOL_TIMEOUT` becomes a hard contract. 14× violations of the 60s deadline disappear structurally. Users see deterministic behavior or recognizable errors, not silent hangs.
- **Positive**: client retry behavior becomes correct. With `mcp.protocol.TimeoutError` surfaced, retries are intentional, not "the host thinks the user rejected the call".
- **Positive**: Phase B Outbox + Reconciler ([adr-009-multi-process-wal-policy.md](adr-009-multi-process-wal-policy.md) v2) gains preemption authority over its reconciler thread. Without `bounded_call`, the reconciler itself becomes a new hang surface.
- **Positive**: structured timeout events feed Phase B/C decision-making (`tool_timeout_exceeded` count is a gate signal).
- **Negative**: `subprocess.run → Popen` refactor across 5 callsites is invasive. ~150 LOC of mechanical migration + careful testing.
- **Negative**: cross-OS termination behavior is not perfectly symmetric (POSIX SIGTERM is graceful, Windows TerminateProcess is not). Documented and tested per-OS.
- **Negative**: 2-second grace period on terminate is added latency for already-overflowing cases. Acceptable: a 62s response beats an 838s response by a margin large enough that the grace is invisible.
- **Marginal**: regression risk in git interactions. Heavy test coverage in PR (regression tests per #111 ACs, plus subprocess hang termination, plus partial-commit prevention).

## §5 — Cooperative-lock eviction (HIVE-116 amendment, 2026-05-28)

The hard-deadline mechanism specified in §§1–4 closes the "stuck subprocess" failure mode by terminating registered Popens on expiry. Empirical use surfaced a residual gap: when the worker thread holding the in-process `.git/hive.lock` filelock is stuck in `proc.communicate()` after the kill (Windows-specific stdio drain on terminated children), the lock object stays cached and sibling workers block on subsequent acquires.

[adr-012-cooperative-filelock-eviction-on-deadline.md](adr-012-cooperative-filelock-eviction-on-deadline.md) extends the supervisor's deadline branch with a post-kill drain (default `HIVE_POST_KILL_DRAIN_S=5.0`s) followed by eviction of the cached `FileLock` from `_GIT_FILELOCKS`. This is cooperation, not preemption — Python cannot cancel a thread; the eviction lets new acquires bypass the runaway holder.

The amendment does not modify §§1–4; existing termination semantics (SIGTERM→grace→SIGKILL on POSIX, TerminateProcess×2 on Windows) stand. The new step inserts between `_cleanup_index_lock` and `GHOST_RESPONSES.record`, with telemetry persisted via `LockEvictionTracker` for the 2026-06-05 Phase C decision input.

## References

- [adr-005-transport-and-scale.md](adr-005-transport-and-scale.md) — original `tool_span` introduction; superseded for enforcement mechanism by this ADR (defense-in-depth model preserved)
- [adr-007-mcp-cancellation-response.md](adr-007-mcp-cancellation-response.md) — `_compat.py` ghost-response handling; orthogonal but interacts (this ADR shrinks the ghost-response race window further)
- [adr-009-multi-process-wal-policy.md](adr-009-multi-process-wal-policy.md) — companion ADR (SQLite half); shares the `mcp.lock_contention` telemetry surface
- [adr-010-external-committer-coexistence.md](adr-010-external-committer-coexistence.md) — companion ADR (git half); same release bundle
- [lessons.md](../lessons.md) — "Three timeouts in a chain aren't a deadline" (load-bearing rationale)
- Spec: `specs/HIVE-115-latency-tail-redesign/` (forthcoming)
- Issue #111: https://github.com/mlorentedev/hive/issues/111
- Python subprocess docs: https://docs.python.org/3/library/subprocess.html#subprocess.Popen.terminate

<!-- Provenance (maintainer's cross-project knowledge store; not linked to preserve repo->store independence): pattern-async-threading §1 (four-layer defense-in-depth; this ADR refines it — ONE layer must own preemption). HIVE-115 backlog tracked in the forge (GitHub issues / milestones). -->
