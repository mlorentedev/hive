---
id: lesson-048-you-cannot-cancel-a-python-thread-you-started
type: lesson
status: active
created: "2026-05-28"
owner: manu
tags: [hive, lesson]
---

# You cannot cancel a Python thread you started

**Tag:** concurrency, deadlines, cooperation-pattern, hive-116

**Context.** HIVE-115 PR-3 introduced `bounded_call`/`tool_span` to enforce wall-clock deadlines on tool calls. The supervisor terminates registered `Popen` subprocesses on expiry — that part works. But two weeks of empirical use (issue #141) showed that the worker thread doing the sync `_git_commit` is NOT cancelled when the deadline fires. The client sees `TimeoutError`, but the thread keeps running. In one Windows case it ran 246 seconds after the supposedly-60s deadline. While the thread runs, it holds `_GIT_LOCK` (threading) + the singleton `_git_filelock` (filelock) and blocks every sibling.

**Problem.** `asyncio.timeout` is purely cooperative — it cancels the awaiting coroutine at the next `await`, but `asyncio.to_thread`'s worker thread has no awaitable inside the body. CPython has no `Thread.cancel()`. `PyThreadState_SetAsyncExc` exists but is documented as not reliable for cancelling code inside C-implemented blocking calls (which is exactly where stuck threads live). So a thread inside `subprocess.communicate()` stays there until the subprocess flushes its stdio — and on Windows, communicate() on a SIGKILLed child can block far longer than it does on POSIX. Result: deadline supervisor + runaway thread + cached singleton lock = "fast client response, blocked siblings, orphan lock file." Foundation lesson is "Three timeouts in a chain aren't a deadline" (above); this lesson is the corollary that motivates the cooperation primitive.

**Solution.** Stop trying to cancel the thread. Instead, **evict the cached lock object from the singleton cache so the next acquire constructs a fresh one.** The runaway thread eventually releases (FD closes when the thread exits or when GC reaps the FileLock); meanwhile the new acquires bypass it. On POSIX the kernel tracks `fcntl.flock` per-fd, so the new FileLock can acquire the moment the runaway holder's fd closes. On Windows the orphan lock file persists in `.git/` until the parent process exits (filelock library invariant), but no longer blocks new acquires.

The supervisor inserts the eviction between SIGKILL and the TimeoutError raise:

```python
# In bounded_call / tool_span TimeoutError branch:
killed = await _terminate_registry(registry, grace_s)
if vault is not None and killed:
    _cleanup_index_lock(vault, killed)
    await asyncio.sleep(HIVE_POST_KILL_DRAIN_S)  # 5s default
    evict_filelock(vault)                          # pop from cache
    _record_lock_eviction(vault, killed)           # telemetry
raise TimeoutError(...)
```

The 5s drain is intentional: gives the worker thread a chance to escape `with lock:` naturally on the happy path (Linux subprocess.communicate returns within ~100ms of SIGKILL). Eviction is the safety net for the worst case where the thread is stuck. Drain calibration {1, 5, 10} converged on 5s as the smallest value that never raced eviction in 20-run validation.

**Codified in [adr-012](../adr/adr-012-cooperative-filelock-eviction-on-deadline.md)** (decision) + the maintainer's cross-project multi-process-mcp-server pattern §primitive-8 (reusable form). [adr-008](../adr/adr-008-hard-deadline-enforcement.md) §5 amendment cross-references this.

**Anti-pattern caught.** Earlier draft of HIVE-116 considered "Option A: `Popen.wait(timeout=N)` inside `_run_git`" — an inner timeout to make the thread escape voluntarily. This violates the SSOT principle from HIVE-115 audit B1 ("bounded_call is the single source of truth for deadlines, inner timeouts race the supervisor's external termination"). Eviction is the right shape because it doesn't add a second clock; it cleans up state the runaway thread is no longer authoritative over.

**Cross-platform note.** On Windows, the `.git/hive.lock` file may persist as a 0-byte file even after eviction — `Device or resource busy` on `rm` until the parent process exits. This is the filelock library's invariant (the file is the handle's medium; closing the handle doesn't unlink the file). Documented in `docs/troubleshooting.md` as a cosmetic artifact, not a functional issue.

**When to escalate.** If `vault_health.runtime.lock_eviction.count_30d` rises above ~10/month in normal use, the cooperation pattern is reaching its limit and ADR-011 (daemon model) becomes mandatory. ADR-012 buys observation time for the 2026-06-05 Phase C decision checkpoint (issue #124); it is not a permanent fix for sustained N≥10 multi-session usage.
