---
id: "HIVE-322-commit-outbox"
type: spec
status: draft # draft | implementing | verifying | archived
created: "2026-08-07"
issue: "hive#322"   # repo#NNN — GitHub issue / Project item that tracks this spec
tags: [spec, proposal]
template_version: "1.0"
---

# HIVE-322: Commit outbox

<!-- from issue #322: Concurrent writes serialize on the git commit path — tail latency grows linearly with writer count -->

## Why

Every `vault_write` / `vault_patch` synchronously runs a `git commit`, and git commits against one repository are inherently serial. Measured on a 1300-file vault, write throughput is pinned at **~30-33 writes/s regardless of concurrency** while tail latency grows linearly with the number of writers: 10 concurrent writers see p50 317 ms / max 418 ms inside the daemon, or max 1390 ms across separate processes. The reported field workload is up to 10 concurrent writers (agents dispatching subagents), so the vault is now a global serialization point for the whole agent fleet — adding agents buys queueing, not throughput. Full measurements and methodology in [#322](https://github.com/mlorentedev/hive/issues/322).

## What

`vault_write` and `vault_patch` persist the file and return **without** waiting for a git commit. Committing moves to a reconciler thread that drains a queue of pending paths on a tick, producing one commit per tick instead of one commit per write.

Observable changes:

1. Write-tool latency under concurrency drops to file-I/O cost; the commit no longer appears in the caller's critical path.
2. Commit *rate* becomes bounded by the tick interval rather than by write volume — at a 5 s tick, a burst of 50 writes produces 1 commit instead of 50.
3. `vault_health` surfaces queue depth and last-flush age, so a stalled reconciler is observable rather than silent.

## Out of scope

- The multi-process path. This spec targets the daemon (single-owner) regime; machines still running per-session stdio servers keep today's synchronous commit until the #176 rollout completes.
- Any change to `vault_commit`'s explicit-flush semantics, or to the existing `commit=False` keyword — both stay as they are.
- Pushing. Hive remains commit-only per ADR-014; nothing here introduces network I/O.

## Risks / open questions

**Resolved 2026-08-07 — recorded in [ADR-018](../../docs/adr/adr-018-asynchronous-commit-queue.md), which gates this spec.**

- ~~Relationship to ADR-014.~~ **Resolved.** ADR-018 amends ADR-014: a *path-scoped* timed committer is exempt from the objection that killed the *sweeping* one, because a path enters the queue only after its write completes, so an agent's half-written file cannot be swept. The invariant is load-bearing and must be enforced in code: **the reconciler never runs `git add -A`.** This withdraws the "`add -A` sweep as self-heal fallback" proposed in #322's first comment — under ADR-014 that sweep is the failure mode, not the safety net.
- ~~The `_outbox.py` crash-loss contract forbids this use.~~ **Resolved.** A sibling primitive `CommitQueue` is introduced with its own contract; `Outbox[T]`'s "do NOT use for durable state" stays untouched and absolute. `CommitQueue` additionally deduplicates paths within a tick, which `Outbox[T]` does not.
- ~~The reconciler thread escapes all existing supervision.~~ **Resolved.** A synchronous watchdog reusing `_deadline.py`'s sync primitives (`popen_creation_kwargs()`, `_cleanup_index_lock()`). `bounded_call` is rejected because it is `async def` and would require an event loop inside a daemon thread.
- ~~Recovery of a dropped queue entry.~~ **Resolved.** Startup reconciliation extends `_startup_self_heal`: under the singleton `daemon.lock`, enumerate uncommitted vault paths and issue one recovery commit. Safe against ADR-014 because it is a startup event rather than a recurring timer, and enumerates paths explicitly.
- ~~Interaction with ADR-013 / ADR-017.~~ **Checked.** ADR-013 pre-planned this as its deferred "2b", gated on "telemetry showing git-commit serialization as a real bottleneck OR genuine concurrent write-heavy load arriving" — **both gates have fired**. This spec implements a subset of 2b (deferred commit, no durable journal), so ADR-013's "journal MUST be SQLite-backed" constraint does not bind here but remains binding if idempotency-keyed at-most-once is later extended across the deferred path. ADR-017 needs no amendment: `--no-verify` applies uniformly through `_commit_args`.

Still open:

- Commit granularity changes the `vault_delete` "git-recoverable" guarantee: a delete and recreate inside one tick collapse into a single state. [AGENT-DRAFT — review before archive] Proposed resolution: `vault_delete` keeps a synchronous commit, opting out of the queue.
- Response-contract wording. HIVE-104 promises the suffix "(uncommitted — call vault_commit to flush)"; with deferral as the default that string now describes the normal path. ADR-013 flagged this as an observable contract change requiring bilingual docs. [AGENT-DRAFT — review before archive] Proposed resolution: new wording that names the tick, and `commit=True` continues to mean synchronous for callers that need the commit confirmed.

## Acceptance criteria

- [ ] AC1 — With the queue enabled, `vault_write` returns without a `git commit` in its call path (asserted by test, not by timing).
- [ ] AC2 — N queued writes across one tick produce exactly one commit containing exactly those N paths, and no unrelated working-tree file is staged.
- [ ] AC3 — 10 concurrent writers show write-tool latency bounded by file I/O, with commit count bounded by elapsed-time / tick rather than by write count.
- [ ] AC4 — A reconciler flush that exceeds its deadline is terminated and logged, leaving no orphaned `git` process and no stale `index.lock`.
- [ ] AC5 — `vault_health` reports queue depth and last-flush age; a stalled reconciler is visible there.
- [ ] AC6 — On clean shutdown the queue is drained; nothing queued is silently discarded.
- [ ] AC7 — The reconciler never stages a working-tree file it did not queue. This is the load-bearing ADR-014 invariant and is guarded by an explicit test, not by convention.
- [ ] AC8 — The same path written twice within one tick produces one queue entry and appears once in the resulting commit.
- [ ] AC9 — After an unclean exit, the next daemon start commits the vault paths left uncommitted, enumerating them explicitly rather than via `git add -A`.

## References

- Bitácora board: [hive#322](https://github.com/mlorentedev/hive/issues/322)
- Related ADR: `docs/adr/adr-014-vault-commit-coordination.md` (single deliberate committer — must be amended), `docs/adr/adr-011-phase-c-daemon-model.md` (single-owner daemon this spec assumes), `docs/adr/adr-010` (external-committer coexistence), `docs/adr/adr-017-auto-commit-bypasses-vault-pre-commit-hook.md`
- Prior art in-repo: `src/hive/_outbox.py` (`Outbox[T]` + reconciler-thread pattern, HIVE-115 PR-4), HIVE-104 commit coalescing (`commit=False` + `vault_commit`)
- Related issues: [#176](https://github.com/mlorentedev/hive/issues/176) (daemon rollout — gates the multi-process path), [#288](https://github.com/mlorentedev/hive/issues/288) / [#289](https://github.com/mlorentedev/hive/issues/289) (tracker locks — measured NOT to be a latency cause; hang risk only)
