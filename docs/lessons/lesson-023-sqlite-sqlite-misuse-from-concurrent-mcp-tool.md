---
id: lesson-023-sqlite-sqlite-misuse-from-concurrent-mcp-tool
type: lesson
status: active
created: "2026-03-12"
owner: manu
tags: [hive, lesson, concurrency, sqlite, threading, mcp]
---

# SQLite SQLITE_MISUSE from concurrent MCP tool calls

**Context:** FastMCP dispatches synchronous tool handlers to a thread pool via anyio.to_thread.run_sync. Concurrent tool calls share the same ServerContext, including SQLite-backed trackers.
**Problem:** RelevanceTracker and UsageTracker used check_same_thread=False without locking, causing SQLITE_MISUSE (error 21). BudgetTracker lacked the flag entirely, causing ProgrammingError on cross-thread access. vault_write/vault_patch had TOCTOU race conditions on file read-modify-write. _git_commit had no serialization, allowing interleaved git add/commit.
**Solution:** Added threading.Lock to all three SQLite trackers. Used Lock (not RLock) with internal _method() pattern to avoid deadlock on reentrant calls (e.g. month_stats calling _month_spent). Added module-level _WRITE_LOCK in _vault_write.py for atomic file I/O + git commit. Added _GIT_LOCK in _helpers.py to serialize all git operations.
**Tags:** `#concurrency` `#sqlite` `#threading` `#mcp`
