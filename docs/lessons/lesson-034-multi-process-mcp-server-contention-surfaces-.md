---
id: lesson-034-multi-process-mcp-server-contention-surfaces-
type: lesson
status: active
created: "2026-05-18"
owner: manu
tags: [hive, lesson, mcp, concurrency, multi-process, sqlite, filelock, scalability]
---

# Multi-process MCP server contention surfaces — checklist + patterns

**Context:** Hive runs as N independent `uvx hive-vault` subprocesses (one per Claude Code session) sharing the same vault git repo + three SQLite trackers. PR #90 fixed the symptoms (39-min hang ending in `AssertionError('Request already responded to')`; recurring `git commit timed out`; silent capture_lesson loss). PR #92 hardened the design with the patterns that apply across any stateful multi-process MCP server.

**Problem:** Intra-process primitives (`threading.Lock`, default `sqlite3` connection) silently fail to coordinate across separate MCP server subprocesses, even when each is correct on its own. The symptoms only appear under parallel usage and are hard to attribute (cuelgues, crash from monkey-patch-able assertions, silent data loss).

**Solution:** Four primitive patterns:

1. **Inter-process file lock** (`filelock`) on the git index. The thread-local `_GIT_LOCK` only serializes within one process — N processes still race on `.git/index.lock`. Wrap the whole write critical section (read-modify-write + commit), not just the git call, or you lose data on concurrent appends to the same file (the `capture_lesson` loss).
2. **SQLite as inter-process queue, not async cache.** Set `connect(timeout=10)` + `PRAGMA busy_timeout=10000` + `PRAGMA synchronous=NORMAL` + `PRAGMA wal_autocheckpoint=200`. Replace SELECT+UPDATE with `INSERT ... ON CONFLICT DO UPDATE`. Buffer writes in memory and flush in batches; reads flush first so they see fresh data.
3. **Rate-limit shared-state mutations** (`apply_decay` was the canonical bug). Use an atomic `INSERT ... ON CONFLICT DO UPDATE WHERE elapsed >= T` claim: row updates = decay runs; row unchanged = skip. Multiple briefings within T seconds = exactly one decay.
4. **Cache subprocess-spawn ops** by HEAD SHA. `git log` / `git recent` were spawning per call. `_current_head_sha(vault)` reads `.git/HEAD` directly (no subprocess), perfect cache key.

**Process-model patterns:**
- Per-PID log file (`hive-{pid}.log`) — `RotatingFileHandler` rotation races corrupt the log under N concurrent writers.
- Defer `create_server()` out of import-time → main(). Importing the module side-effect-free saves ~300-600ms × N spawns.
- For client cancellation races, monkey-patch BOTH `RequestResponder.__exit__` (re-raised CancelledError, issue #75) AND `RequestResponder.respond` (AssertionError on `_completed=True` when handler finishes after cancel). Self-gate both on the exact failure mode so they degrade silently if upstream fixes.

**UX:** `format_io_error(exc, path, action)` discriminator returning per-class hints beats `f"File I/O error: {exc!r}"`. The LLM relay can act on "permission denied — check writable by MCP process" but not on `[Errno 13]`.

**Verdict on scaling:** The original ADR-005 estimated wall at 50 sessions. Audit revised down to ~20–25 (every read also writes to SQLite via `track()`; triple-timeout stack up to 90s; `apply_decay` correctness break at 5+ concurrent briefings). Post-PR-92 (buffered writes + apply_decay gate + git_log cache), the wall should rise but exact number needs measurement.

**Tags:** `#mcp` `#concurrency` `#multi-process` `#sqlite` `#filelock` `#scalability`

**Cross-project pattern:** the multi-process-mcp-server pattern (maintainer's cross-project knowledge store) was distilled from this lesson (origin L-HIVE-90/92); not linked here to preserve repo->store independence.
