---
id: adr-012-cooperative-filelock-eviction-on-deadline
type: adr
status: active
created: "2026-05-27"
---

# ADR-012: Cooperative Filelock Eviction on Deadline

## Status

Accepted — 2026-05-28. Ships with HIVE-116 PR-2 / v1.21.0.

## Context

HIVE-115 PR-3 introduced `bounded_call` and `tool_span` as the deadline supervisors that terminate registered `subprocess.Popen` instances on wall-clock expiry. The supervisor cleans `.git/index.lock` (git's native cooperative lock) when the killed PID matches one we spawned, via `_cleanup_index_lock`.

Empirical evidence collected 2026-05-27 from two independent Windows sessions (issue #141, hive logs `hive-31272.log` + `hive-9092.log`) showed a residual failure mode: when the worker thread holding the `_filelock_with_telemetry` context is stuck in `proc.communicate()` after the subprocess is killed (Windows `subprocess` stdio drain on terminated children can block indefinitely), the in-process `.git/hive.lock` filelock object is never released. Sibling workers block on subsequent acquires; one user-visible session showed `capture_lesson` running for 246 seconds while the client had long since received a "timeout" response.

Three constraints converge:

1. **Python cannot cancel a thread.** `asyncio.timeout` cancels only the awaiting coroutine. The worker thread inside `asyncio.to_thread` continues until the body returns. This is a CPython invariant — see [lessons.md](../lessons.md), "Three timeouts in a chain aren't a deadline".
2. **`filelock` is a cooperative library.** It does not expose a "force-release-from-outside-the-holder" primitive because the underlying `fcntl.flock` (POSIX) and `msvcrt.locking` (Windows) attach the lock to the file descriptor of the holder; a non-holder cannot release.
3. **`_GIT_FILELOCKS` caches a singleton per vault.** This was the right call for HIVE-104 — re-entrant acquires from nested `_git_commit` calls reuse the same `FileLock` object cleanly. But the cache extends the lifetime of the orphan file handle past the runaway worker's natural completion.

## Decision

The deadline supervisor evicts the cached `FileLock` for the affected vault from `_GIT_FILELOCKS` after a post-kill drain window. The next `_git_filelock(vault)` call constructs a fresh `FileLock` against the same path.

Order of operations on deadline expiry:

1. `_terminate_registry` — SIGTERM → grace → SIGKILL → drain stdio.
2. `_cleanup_index_lock` — remove `.git/index.lock` if PID-owned.
3. **NEW: `await asyncio.sleep(post_kill_drain_s)`** — give the worker thread time to escape `_filelock_with_telemetry.__exit__` naturally on the happy path.
4. **NEW: `evict_filelock(vault)`** — pop the cached `FileLock` from `_GIT_FILELOCKS` under `_GIT_FILELOCKS_GUARD`.
5. **NEW: record event** to `LockEvictionTracker` (SQLite) for `vault_health.runtime.lock_eviction.count_30d` telemetry.
6. Record `GHOST_RESPONSES.record(source="deadline")`.
7. Raise `TimeoutError` to the caller.

Default `HIVE_POST_KILL_DRAIN_S = 5.0` seconds (validated [0.5, 30.0]). Matches `HIVE_OUTBOX_TICK_S` for symmetry — both windows represent "give cooperative state a chance to settle naturally before forcing it." A calibration sweep at {1, 5, 10} on the cross-worker integration harness chose 5s as the smallest value that never trips a race-warning in 20 consecutive runs on Linux.

## Consequences

### Positive

- **Sibling workers unblock within `deadline + grace + drain + slack`.** The cache eviction means new `_git_filelock(vault)` acquires get a fresh `FileLock` object; on POSIX the kernel resolves the underlying `fcntl` lock the moment the runaway thread eventually releases its FD; on Windows the orphan file persists but no longer blocks new acquires (filelock library invariant).
- **Telemetry feeds the Phase C decision gate.** `lock_eviction.count_30d` is a new input to the 2026-06-05 checkpoint (issue #124). Sustained eviction frequency is evidence that the cooperation pattern is reaching its limit; sustained low frequency justifies further deferral of the daemon model.
- **Cross-OS symmetric.** No platform-specific eviction logic. The cache is process-local Python state; popping it works identically on POSIX and Windows.
- **Idempotent.** `evict_filelock` returns `False` when nothing is cached, allowing the supervisor to call it unconditionally without per-call gating.

### Negative

- **R1 race window.** If the worker thread escapes `__exit__` between the eviction and a new acquire from a sibling, the sibling acquires a fresh `FileLock` while the old object is in mid-release. On POSIX `fcntl.flock` this is benign (kernel-tracked per fd). On Windows `msvcrt.locking` the semantics are subtler — empirically clean on 20-run validation, but a `mcp.lock_eviction.race` WARNING log is a planned PR-2.5 safety net.
- **Adds `post_kill_drain_s` to the supervisor's tail latency.** A deadline-killed `vault_write` now responds in `deadline + grace + drain + ε` instead of `deadline + grace + ε`. With defaults that is 60 + 2 + 5 = 67s instead of 62s. The trade-off is correctness (sibling unblock) for 5s of additional perceived latency on the affected call.
- **The orphan `.git/hive.lock` file persists on Windows.** Eviction releases the cached *object*; it does NOT delete the *file*. Users still see the 0-byte file in `.git/` until the parent `hive-vault` process exits. This is documented in `docs/troubleshooting.md` as a known cosmetic artifact.
- **Not a substitute for Phase C (ADR-011).** This decision closes the operational gap but does not change the architectural fact that Python cannot preempt threads. When telemetry shows sustained eviction frequency, the daemon model becomes mandatory; ADR-012 buys time, not absolution.

## Alternatives considered

### A. `Popen.wait(timeout=N)` inside `_run_git`

Adds an inner timeout to the worker thread so it escapes `__exit__` voluntarily within N seconds of SIGKILL. Rejected: violates HIVE-115 audit B1 — `bounded_call` is the SSOT for deadlines, inner timeouts race the supervisor's external termination. Different error semantics, harder to reason about.

### B. Drop the `_GIT_FILELOCKS` singleton cache

Construct a new `FileLock` on every `_git_filelock` call. Rejected: breaks nested acquire re-entry (the original HIVE-104 motivation for the cache). Would require restructuring `vault_write_lock` to track per-call lock instances.

### C. Custom inter-process primitive

Roll our own `.git/hive.lock` with an explicit "force-release on PID failure" API. Rejected: filelock is the right library; the bug is in our composition, not the abstraction.

### D. Phase C daemon model NOW

Eliminate the multi-process contention class entirely by funneling all SQLite + git through a single daemon. Rejected for this ADR scope: the daemon model is a v2.0 redesign that needs Phase A+B telemetry as input. ADR-012 is the cooperation-pattern fix that buys telemetry time.

## Implementation

`src/hive/_helpers.py`:
- `evict_filelock(vault_path) -> bool` — eviction primitive.
- `_post_kill_drain() -> float` — lazy settings read.
- `_drain_and_evict(vault, killed_pids, tool_name)` — sleep + evict + record + log.
- `_record_lock_eviction(vault, killed_pids)` — defer to module-level tracker singleton.
- `_LOCK_EVICTION_TRACKER` + `register_lock_eviction_tracker(tracker)` — process-global singleton wiring.
- `_VAULT_FOR_EVICTION_CV: ContextVar[Path | None]` — contextvar carrying the vault from `wrap_sync_tool` to `tool_span`.

`src/hive/_lock_eviction.py` (new):
- `LockEvictionTracker(_SqliteTracker)` with `record(vault, killed_pids)`, `count_last_30d()`, `last_iso()`. DB at `~/.local/share/hive/lock_evictions.db`.

`src/hive/_deadline.py`:
- `bounded_call` calls `_drain_and_evict` after `_cleanup_index_lock` when `vault_for_index_cleanup` is set and killed_pids is non-empty.

`src/hive/_helpers.py` (tool_span):
- After ghost-response record + WARNING log, reads `_VAULT_FOR_EVICTION_CV` and calls `_drain_and_evict` when set + killed.

`src/hive/_helpers.py` (wrap_sync_tool):
- For tools in `_PARTIAL_STATE_TOOLS = {"vault_write", "vault_patch"}`, sets `_VAULT_FOR_EVICTION_CV` to `ctx.vault` for the duration of the call.

`src/hive/config.py`:
- `post_kill_drain_s: float = Field(default=5.0, ge=0.5, le=30.0)`.

`src/hive/_context.py`:
- `ServerContext.lock_eviction: LockEvictionTracker`.

`src/hive/_vault_health.py`:
- `runtime_block_text` surfaces `lock_eviction.count_30d` + `lock_eviction.last_iso` under `## runtime`.

## References

- Issue: [hive#141](https://github.com/mlorentedev/hive/issues/141) — original repro
- Spec: `specs/HIVE-116-stale-lock-after-deadline/` (repo)
- Prior ADR: [adr-008-hard-deadline-enforcement.md](adr-008-hard-deadline-enforcement.md) — amended with §5 referencing this ADR
- Foundation lesson: [lessons.md](../lessons.md), "Three timeouts in a chain aren't a deadline"
- Corollary lesson: [lessons.md](../lessons.md), "You cannot cancel a Python thread you started" — captures the cooperation pattern this ADR formalizes
- Phase C dependency: ADR-011 daemon model (deferred; this ADR buys observation time)

<!-- Provenance (maintainer's cross-project knowledge store; not linked to preserve repo->store independence): pattern-multi-process-mcp-server — extended with primitive 8 (cooperative-lock eviction). -->
