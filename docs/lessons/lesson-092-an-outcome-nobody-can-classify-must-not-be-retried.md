---
id: lesson-092-an-outcome-nobody-can-classify-must-not-be-retried
type: lesson
status: active
created: "2026-08-23"
owner: manu
tags: [hive, lesson, retry, idempotency, fallback, cost, daemon, adr-011, HIVE-384]
---

# An outcome nobody can classify must not be retried

**Context:** `_dispatch_async` prefers the daemon and falls back to the in-process path when the daemon is not usable, which is how ADR-011 §3 says the client degrades. The fallback was guarded by a broad `except Exception` around `call_tool`, justified by a comment listing three failure modes: daemon mid-restart, stale token, handshake stall.
**Problem:** All three of those are **pre-submission** — the request never left. But the same `except` also caught a connection that died *after* `call_tool` had been invoked, and the daemon records usage as soon as the worker answers, **before** it serialises the response. So a broken connection at that point leaves an outcome nobody can classify: the inference may already have run and been billed. The fallback then ran a second one — double cost, and a second slot out of a pool whose concurrency is the binding constraint. Nothing was wrong with any individual line; the ambiguous case had simply been folded in with the three unambiguous ones and nobody noticed it did not belong.
**Solution:** Separate the two regions with an explicit flag, so the fallback is pre-submission only. Failing to *open* the session is unambiguous and degrades as documented. Failing after `call_tool` was invoked returns a task failure that names the ambiguity — deliberately **not** exit `3`, because exit `3` would advance the dispatcher's chain and spend a second pool on a task that may already have been served. Fail closed is the direction that does not retry. The test asserts `local.await_count == 0` — that no second inference was dispatched — rather than asserting the returned status, which would pass while the money was already spent.
**Why:** Retry safety is not a property of an operation, it is a property of **what the caller knows about the operation's outcome**. The reflex when a call fails is to try the other path, and it is right precisely as long as the failure proves nothing happened. The moment a failure mode exists where the work may have completed, "try again" silently becomes "do it twice", and a side effect that costs money or consumes a scarce slot makes that a defect rather than an inefficiency. The practical discipline: when you write a broad `except` around a remote call, enumerate the failure modes it catches and mark each one *before* or *after* the point of no return. If any is *after*, the handler is wrong for that one — and a comment listing only the *before* cases is evidence the author never asked. Sibling of [[docs/lessons/lesson-058-sequence-the-at-most-once-write-primitive-bef|lesson-058]], which is the same reasoning for vault writes; the alternative here would be an idempotency key on `delegate_task`, a genuinely larger change.
**Tags:** `#retry` `#idempotency` `#fallback` `#cost` `#daemon` `#adr-011` `#HIVE-384`
