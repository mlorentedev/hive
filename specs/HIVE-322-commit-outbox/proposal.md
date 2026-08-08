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

`vault_write` and `vault_patch` persist the file and return **without** waiting for a git commit — **by default**. Committing moves to a reconciler thread that drains a queue of pending paths on a tick, producing one commit per tick instead of one commit per write. The reconciler runs in every hive server process, daemon or not (ADR-018 §Decision).

Observable changes:

1. Write-tool latency under concurrency drops to file-I/O cost; the commit no longer appears in the caller's critical path.
2. Commit *rate* becomes bounded by the tick interval rather than by write volume — at a 5 s tick, a burst of 50 writes produces 1 commit instead of 50.
3. `vault_health` surfaces queue depth and last-flush age, so a stalled reconciler is observable rather than silent — plus the count and age of uncommitted vault paths, which is the only recovery signal in the non-daemon regime.
4. **Breaking:** `commit` defaults to deferral instead of `True`, so a successful write no longer implies a commit exists. `commit=True` is the synchronous escape hatch; `vault_delete` stays synchronous. Ships as `feat!` (ADR-018 §4).

## Out of scope

- Pushing. Hive remains commit-only per ADR-014; nothing here introduces network I/O.
- Automatic startup recovery outside the daemon. Deliberately refused, not deferred — without the singleton `daemon.lock` a startup sweep is indistinguishable from the obsidian-git timer ADR-014 removed. Bounded by `vault_health` observability instead (ADR-018 §3).
- A durable/persisted queue. Rejected in ADR-018 §3; revisit only if idempotency-keyed at-most-once is extended across the deferred path, where ADR-013's SQLite-backed constraint would bind.
- The [#176](https://github.com/mlorentedev/hive/issues/176) daemon rollout. It improves this spec's best case but no longer gates it.

## Risks / open questions

**Resolved 2026-08-07 — recorded in [ADR-018](../../docs/adr/adr-018-asynchronous-commit-queue.md), which gates this spec.**

- ~~Relationship to ADR-014.~~ **Resolved.** ADR-018 amends ADR-014: a *path-scoped* timed committer is exempt from the objection that killed the *sweeping* one, because a path enters the queue only after its write completes, so an agent's half-written file cannot be swept. The invariant is load-bearing and must be enforced in code: **the reconciler never runs `git add -A`.** This withdraws the "`add -A` sweep as self-heal fallback" proposed in #322's first comment — under ADR-014 that sweep is the failure mode, not the safety net.
- ~~The `_outbox.py` crash-loss contract forbids this use.~~ **Resolved.** A sibling primitive `CommitQueue` is introduced with its own contract; `Outbox[T]`'s "do NOT use for durable state" stays untouched and absolute. `CommitQueue` additionally deduplicates paths within a tick, which `Outbox[T]` does not.
- ~~The reconciler thread escapes all existing supervision.~~ **Resolved.** A synchronous watchdog reusing `_deadline.py`'s sync primitives (`popen_creation_kwargs()`, `_cleanup_index_lock()`). `bounded_call` is rejected because it is `async def` and would require an event loop inside a daemon thread.
- ~~Recovery of a dropped queue entry.~~ **Resolved, then revised 2026-08-07.** Under the daemon, startup reconciliation extends `_startup_self_heal`: holding the singleton `daemon.lock`, enumerate uncommitted vault paths and issue one recovery commit — safe against ADR-014 because it is a startup event rather than a recurring timer, and enumerates paths explicitly. **Outside the daemon there is no singleton and no automatic recovery**, by deliberate refusal: a startup sweep from a process that does not own the tree would stage a sibling hive's in-flight write or a human's half-edited file, which is ADR-014's original objection made sharper. Bounded by `vault_health` reporting the count and age of uncommitted vault paths (ADR-018 §3).
- ~~Interaction with ADR-013 / ADR-017.~~ **Checked.** ADR-013 pre-planned this as its deferred "2b", gated on "telemetry showing git-commit serialization as a real bottleneck OR genuine concurrent write-heavy load arriving" — **both gates have fired**. This spec implements a subset of 2b (deferred commit, no durable journal), so ADR-013's "journal MUST be SQLite-backed" constraint does not bind here but remains binding if idempotency-keyed at-most-once is later extended across the deferred path. ADR-017 needs no amendment: `--no-verify` applies uniformly through `_commit_args`.

**Also resolved 2026-08-07** — the two items below were the last open drafts in this spec; both are now decided in ADR-018 §4.

- ~~Commit granularity changes the `vault_delete` "git-recoverable" guarantee.~~ **Resolved.** `vault_delete` opts out of the queue and keeps a synchronous commit. A delete and a recreate inside one tick would collapse to a single state, and recoverability is precisely the guarantee that tool sells.
- ~~Response-contract wording.~~ **Resolved.** Deferral is now the default, so the suffix describes the normal path and is reworded to name the tick rather than to imply an anomaly. `commit=True` continues to mean "committed before this call returned". The change is breaking, ships as `feat!`, and carries bilingual site docs (EN + ES) per ADR-013's ACK-semantics flag.

**Consequences of the default flip that the drafts did not name** — decided in ADR-018 §4, listed here because they widen the break:

- `commit=False` is **subsumed**, not preserved. It becomes an alias for the deferred default — the path is queued and becomes *eligible* for the next drain, which is weaker than "committed within one tick": a drain may produce no commit under the external-committer short-circuit, and a hard kill before the tick leaves the path uncommitted. HIVE-104's "stays uncommitted until you flush" mode is removed; the benefit it existed for is what the queue now does automatically, and `vault_commit` still flushes early.
- `HIVE_AUTO_DEFER_TO_EXTERNAL_COMMITTER` composes by short-circuiting the **flush**, not the queue: paths queue as normal and the reconciler evaluates `_should_defer_to_external_committer()` at drain time, draining without committing while obsidian-git is healthy. Without this the queue would silently defeat the ADR-010 hand-off.

Still open:

- **Daemon-recovery provenance (AC9) — reopened 2026-08-07 by review.** ADR-018 §3 justified daemon-side startup recovery on the singleton `daemon.lock`, but that lock excludes sibling *hives*, not a *human*. A maintainer with a half-edited note open in Obsidian during a daemon restart produces dirty state that recovery cannot tell from its own orphaned write — ADR-014's original objection surviving inside the regime claimed safe. Fixing it needs **provenance** (commit only what hive wrote), and after a crash the in-memory queue that knew those paths is gone. Candidates: (i) recovery only *reports*, collapsing AC9 into AC11; (ii) a minimal persisted provenance record, reopening the trade-off §3 rejected; (iii) narrow to hive's write conventions, which is fragile. **Must be settled before ADR-018 is accepted.**

## Acceptance criteria

- [ ] AC1 — With the queue enabled, `vault_write` returns without a `git commit` in its call path (asserted by test, not by timing).
- [ ] AC2 — N writes queued **in one process** across one tick produce exactly one commit from that process, containing exactly those N paths, and no unrelated working-tree file is staged. "One commit per tick" is per-process: with P processes the bound is P commits per tick (see AC3), which is the point the daemon's single queue improves on.
- [ ] AC3 — 10 concurrent writers show write-tool latency bounded by file I/O, with commit count bounded by elapsed-time / tick rather than by write count. Asserted in **both** regimes: threads in one process, and separate processes sharing the vault filelock.
- [ ] AC4 — A reconciler flush that exceeds its deadline is terminated and logged, leaving no orphaned `git` process and no stale `index.lock`.
- [ ] AC5 — `vault_health` reports queue depth and last-flush age; a stalled reconciler is visible there.
- [ ] AC6 — On clean shutdown the queue is drained; nothing queued is silently discarded.
- [ ] AC7 — The reconciler never stages a working-tree file it did not queue. This is the load-bearing ADR-014 invariant and is guarded by an explicit test, not by convention.
- [ ] AC8 — The same path written twice within one tick produces one queue entry and appears once in the resulting commit.
- [ ] AC9 — After an unclean exit, the next **daemon** start commits the vault paths left uncommitted, enumerating them explicitly rather than via `git add -A`, **and leaves untouched any dirty path hive did not write** (the provenance gap under "Still open" — this criterion cannot be finalised until that is settled).
- [ ] AC10 — A non-daemon server start performs **no** recovery commit: a dirty working-tree file it did not write is left untouched. This guards the deliberate refusal in ADR-018 §3 against a future "helpful" sweep.
- [ ] AC11 — `vault_health` reports the count and age of uncommitted vault paths, so the non-daemon recovery gap is observable.
- [ ] AC12 — `commit` defaults to deferral on **both** `vault_write` and `vault_patch`: a plain call produces no commit in its call path, `commit=True` produces one before returning, `commit=False` is indistinguishable from the default (queued, not held indefinitely), and `vault_delete` commits synchronously regardless of the tick.
- [ ] AC13 — The reconciler acquires `_git_filelock(vault)` around its commit. Without this the deferred commit runs outside the lock the write path used to hold, which would invalidate the cross-process argument the whole rescope rests on (ADR-018 §Decision).

## References

- Bitácora board: [hive#322](https://github.com/mlorentedev/hive/issues/322)
- Gating ADR: `docs/adr/adr-018-asynchronous-commit-queue.md` (awaiting acceptance)
- Related ADR: `docs/adr/adr-014-vault-commit-coordination.md` (single deliberate committer — amended by ADR-018), `docs/adr/adr-011-phase-c-daemon-model.md` (single-owner daemon — needed for AC9 recovery only, **not** for the queue), `docs/adr/adr-013-write-idempotency-at-most-once.md` (the deferred "2b" this implements a subset of), `docs/adr/adr-010-external-committer-coexistence.md` (external-committer coexistence), `docs/adr/adr-017-auto-commit-bypasses-vault-pre-commit-hook.md`
- Prior art in-repo: `src/hive/_outbox.py` (`Outbox[T]` + reconciler-thread pattern, HIVE-115 PR-4), HIVE-104 commit coalescing (`commit=False` + `vault_commit`)
- Related issues: [#176](https://github.com/mlorentedev/hive/issues/176) (daemon rollout — improves the best case, no longer gates this spec), [#288](https://github.com/mlorentedev/hive/issues/288) / [#289](https://github.com/mlorentedev/hive/issues/289) (tracker locks — measured NOT to be a latency cause; hang risk only)
