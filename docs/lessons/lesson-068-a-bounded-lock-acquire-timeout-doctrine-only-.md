---
id: lesson-068-a-bounded-lock-acquire-timeout-doctrine-only-
type: lesson
status: active
created: "2026-07-08"
owner: manu
tags: [hive, lesson, concurrency, sqlite, threading, mcp, session_briefing, HIVE-282]
---

# A bounded `Lock.acquire(timeout=)` doctrine only holds if every caller follows it (#282)

**Context:** `session_briefing(project=<slug>)` reproducibly timed out at the outer 60s tool deadline on Windows (non-admin, per-session stdio `hive`, no daemon), while every other tool stayed instant. `~/.local/share/hive/hive-*.log` from the field session showed `killed 0 subprocess(es)` on each timeout — the supervisor's Popen-kill path never fired, so nothing to terminate was ever spawned.
**Problem:** `killed 0` plus a bounded `subprocess.run(timeout=30)` in `_git_log`/`_git_recent` ruled out git by pure reasoning (a git hang self-aborts at ~30s, well under the 60s ceiling) without needing to reproduce live. That left `RelevanceTracker` (tasks/lessons access recording + decay + score ranking) as the only per-project-only step — and it turned out to guard every SQLite call with a bare `with self._lock:` (a plain `threading.Lock`, no timeout), in direct violation of this repo's own [[#2026-03-13 asyncio.timeout cannot interrupt threads — use lock timeouts for sync code]] lesson from issue #63. `UsageTracker` and `BudgetTracker` share the exact same base class (`_SqliteTracker`) and the same un-timed `with self._lock:` pattern — they just hadn't been hit yet.
**Solution:** Added `_bounded_sync_call()` (thread + `join(timeout)`, mirroring the existing `_acquire_with_telemetry` for the async-side `tool_span`/`bounded_call` primitives) and wired it into every `session_briefing` touchpoint into `ctx.relevance` (`apply_decay`, both `record_access` calls, `get_scores`). On timeout the briefing degrades gracefully — default section order, a visible `_(relevance ranking unavailable — timed out...)_` note — instead of hanging for the full 60s. Deliberately scoped to `RelevanceTracker` only; `UsageTracker`/`BudgetTracker` were left as a follow-up because a silent skip means something different for each (losing a relevance score is cosmetic, under-counting a spend write against `budget.cap_usd` is not) — a shared fix needs a per-tracker decision on what "degraded" means, not a blanket copy-paste.
**Why:** A repo-wide doctrine ("use lock timeouts for sync code") is not self-enforcing — it has to be re-verified at every call site that shares a lock, especially ones added after the lesson was written. Grepping for `with self\._lock:` across `_SqliteTracker` subclasses would have caught all three trackers in one pass; checking only the one with an open bug report missed the sibling risk. When a lesson exists specifically because of a past incident, treat "does this new code follow it" as a standing grep, not a one-time audit.
**Tags:** `#concurrency` `#sqlite` `#threading` `#mcp` `#session_briefing` `#HIVE-282`
