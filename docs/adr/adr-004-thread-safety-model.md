---
id: adr-004-thread-safety-model
type: adr
status: active
created: "2026-03-12"
owner: manu
---

# ADR-004: Thread-Safety Model for MCP Tool Handlers

## Status
Accepted (2026-03-12)

## Context

FastMCP dispatches **synchronous** tool handlers to a thread pool via `anyio.to_thread.run_sync`. When an MCP client sends N tool calls in parallel, they execute in N separate threads — all sharing the same `ServerContext` instance.

This means every shared resource in `ServerContext` (SQLite connections, HTTP clients, file I/O) is subject to concurrent access from multiple threads.

### Problem discovered (Issue #59)

`RelevanceTracker` and `UsageTracker` created SQLite connections with `check_same_thread=False`, which disables Python's thread-origin check but does **not** make the connection thread-safe. Concurrent `execute()` + `commit()` calls caused `SQLITE_MISUSE (error 21)`.

`BudgetTracker` lacked even the `check_same_thread=False` flag, causing `ProgrammingError` on cross-thread access.

`vault_write` and `vault_patch` had TOCTOU (time-of-check-time-of-use) race conditions: two threads could both pass the `exists()` check, then both write, with the second silently overwriting the first. The append mode had a classic lost-write race: Thread A reads "X", Thread B reads "X", Thread A writes "X+A", Thread B writes "X+B" (losing A).

`_git_commit` had no serialization, allowing interleaved `git add` / `git commit` from concurrent threads to corrupt the git index.

## Decision

### 1. SQLite Trackers: `threading.Lock` per instance

Each SQLite-backed class (`RelevanceTracker`, `UsageTracker`, `BudgetTracker`) gets a `threading.Lock` initialized in `__init__`. All public methods that touch `self._conn` acquire the lock.

**Key design choice: `Lock` over `RLock`.**

`BudgetTracker` has reentrant call chains: `can_spend()` → `month_remaining()` → `month_spent()`. Using `RLock` would silently allow reentrancy but mask design issues. Instead, we extract internal `_method()` versions (no lock) and have public methods acquire the lock exactly once:

```python
def _month_spent(self) -> float:
    """Internal — caller MUST hold self._lock."""
    ...

def month_spent(self) -> float:
    with self._lock:
        return self._month_spent()

def month_stats(self, budget: float) -> dict[str, Any]:
    with self._lock:
        spent = self._month_spent()  # no deadlock
        ...
```

This makes the locking boundary explicit: public methods acquire, internal methods assume. A future developer who accidentally calls a public method from inside the lock gets an immediate deadlock (fail-fast), not silent reentrancy.

### 2. Vault File I/O: Module-level `_WRITE_LOCK`

A single `threading.Lock` in `_vault_write.py` serializes all vault write operations (file I/O + git commit). This eliminates TOCTOU on `exists()` checks, lost writes on append, and stale reads on patch.

Coarse-grained by design: vault writes are infrequent (seconds between calls), so serialization has zero practical impact on throughput.

### 3. Git Operations: Module-level `_GIT_LOCK`

A `threading.Lock` in `_helpers.py` wraps the entire `git add` + `git commit` sequence. This prevents index corruption from interleaved git operations.

Separate from `_WRITE_LOCK` because `capture_lesson` (in `_workers.py`) also calls `_git_commit` outside of vault write paths. Lock ordering is always `_WRITE_LOCK` → `_GIT_LOCK` (never reversed), so no deadlock is possible.

### 4. Async Tool Handlers (not affected)

`capture_lesson` and `delegate_task` are `async def` — they run in the event loop, not the thread pool. Synchronous file I/O inside them (`_write_lesson`) blocks the event loop but is effectively serialized. If these are ever refactored to use `aiofiles`, the file I/O will need its own lock.

## Consequences

- **Thread-safe by default**: All shared mutable state is now protected.
- **Fail-fast on mistakes**: `Lock` (not `RLock`) means accidental reentrancy deadlocks immediately in dev, not silently in production.
- **Minimal performance impact**: All locks are uncontended in typical usage (sequential tool calls). Under parallel load, serialization adds microseconds — SQLite is the bottleneck regardless.
- **Future work**: If Hive ever needs true concurrent SQLite writes, migrate to `aiosqlite` with connection pooling. Current `Lock` pattern is a bridge, not a ceiling.

## References

- Issue: https://github.com/mlorentedev/hive/issues/59
- FastMCP thread dispatch: `fastmcp/server/dependencies.py:590`
- SQLite threading docs: https://www.sqlite.org/threadsafe.html
