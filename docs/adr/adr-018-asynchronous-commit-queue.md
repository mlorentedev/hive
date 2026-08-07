---
id: "ADR-018-asynchronous-commit-queue"
type: adr
status: proposed
owner: manu
date: "2026-08-07"
issue: "hive#322"   # repo#NNN — GitHub issue / Project item that triggered this decision
tags: [architecture, decision, concurrency, git, performance]
created: "2026-08-07"
---

# ADR-018: Asynchronous Commit Queue

## Status

Proposed (2026-08-07). Gates `specs/HIVE-322-commit-outbox/`. **Amends [ADR-014](adr-014-vault-commit-coordination.md)** — see "The ADR-014 tension" below, which is the load-bearing part of this decision. **Triggers the deferred "2b" evolution of [ADR-013](adr-013-write-idempotency-at-most-once.md)** — see "Relationship to ADR-013" below. The three design questions that blocked this ADR were resolved on 2026-08-07 and are recorded under "Decision".

## Date

2026-08-07

## Context

`vault_write` / `vault_patch` run a synchronous `git commit` on every call. Git commits against one repository serialize, so hive's write throughput has a hard ceiling independent of how much concurrency is applied to it.

Measured on a 1300-file synthetic vault (methodology and full tables in [#322](https://github.com/mlorentedev/hive/issues/322); commit counts asserted after every run, since `_git_commit` swallows failures by design and a skipped commit would otherwise register as a *fast* sample):

| 10 concurrent writers | throughput | p50 | max |
|---|---:|---:|---:|
| separate processes (pre-daemon) | 33.1 writes/s | 23.5 ms | 1389.6 ms |
| threads in one process (daemon) | 30.0 writes/s | 317.3 ms | **417.5 ms** |

Two conclusions follow, and the second is the one that motivates this ADR:

1. The Phase C daemon ([ADR-011](adr-011-phase-c-daemon-model.md)) **does fix tail fairness** — it converts a lottery (median 23 ms, worst case 1.4 s, some writers starving) into a fair queue where everyone lands in 317-418 ms. Worst-case latency improves 3.3x.
2. The daemon **does not raise the ceiling**. Throughput is ~30-33 writes/s either way. Removing the cross-process filelock does not help, because `_GIT_LOCK` still serializes inside the process and the underlying git commit is serial regardless. **Only committing less often raises the ceiling.**

The reported field workload is up to 10 concurrent writers (agents dispatching subagents), which puts the vault on the flat part of that curve: adding agents buys queueing, not throughput.

Prior art already in the repo:

- **HIVE-104** shipped commit coalescing (`commit=False` on writes plus an explicit `vault_commit` flush). Measured at ~3x throughput and ~3x better tail — but it is opt-in per call, so every agent must be configured to use it and something must call the flush.
- **HIVE-115 PR-4** shipped `src/hive/_outbox.py`: a generic `Outbox[T]` (thread-safe append, atomic swap-and-drain) with a reconciler thread draining on a tick. Exactly the shape this problem wants.
- **[ADR-010](adr-010-external-committer-coexistence.md) / [ADR-014](adr-014-vault-commit-coordination.md)** govern who is allowed to commit to the vault, and are the constraint this decision must satisfy.

## The ADR-014 tension

This is the part that must be settled explicitly rather than assumed.

ADR-014 chose **"Hive is the single deliberate committer"** and made turning obsidian-git's auto-commit timer **off** a precondition. Its stated objection to the timer was not that a second committer existed in the abstract, but a concrete failure mode: with `autoCommitOnlyStaged: false`, the timer "sweeps in-progress, unstaged work — including an agent's half-written change". ADR-014's framing is *event-driven commits, not timed ones*.

An asynchronous commit queue reintroduces a **timer-driven committer**. Taken naively, that reverses ADR-014.

The distinction that makes it safe is narrow and must be enforced in code, not merely intended:

> The reconciler commits **only the paths it has queued**. It never runs `git add -A` or otherwise stages the working tree.

A path-scoped timed committer cannot sweep an agent's half-written file, because a path enters the queue only *after* its write has completed. ADR-014's objection is therefore specific to *sweeping* timers, not to timed commits as such, and this ADR amends it to say so.

**Consequence for #322 as originally written:** the first comment on that issue proposed "per-path drain, with an `add -A` sweep as the self-heal fallback". Under ADR-014 that fallback **is** the failure mode. It is withdrawn. Recovery of a dropped queue entry needs a different answer (see the open questions).

## Relationship to ADR-013

ADR-013 already anticipated this work. It adopted "2a" (synchronous idempotency-key claim) and **deferred "2b"** — the durable journal plus reconciler and replay-on-startup — explicitly gated on:

> "EITHER (i) telemetry showing git-commit serialization as a real bottleneck, OR (ii) genuine concurrent write-heavy load arriving."

**Both gates have now fired.** The measurements above are (i); the 10-concurrent-writer field workload is (ii). ADR-013 also predicted the contract consequence this ADR must own: 2b "changes **ACK semantics** (accepted ≠ committed) — an observable contract change needing bilingual docs".

Two points of reconciliation:

- **This ADR implements a subset of 2b.** It defers the *commit*; it does not build 2b's durable journal (see Decision 3 — startup reconciliation was chosen over a persisted queue). ADR-013's constraint that "the journal MUST be **SQLite-backed**" therefore does not bind here, but **remains binding** if idempotency-keyed at-most-once is later extended across the deferred path.
- **ADR-013's at-most-once guarantee survives.** A claimed key means "the write was applied", and deferral does not duplicate writes. What changes is only that a claimed key no longer implies its commit has landed — which is precisely the ACK-semantics shift ADR-013 flagged.

[ADR-017](adr-017-auto-commit-bypasses-vault-pre-commit-hook.md) needs no amendment: deferred and recovery commits both route through `_git_commit` → `_commit_args`, so `--no-verify` continues to apply uniformly, and push-side scanning is unaffected.

## Decision

Move committing off the write path into a reconciler thread that drains a queue of pending paths on a tick (`HIVE_COMMIT_TICK_S`), producing one commit per tick rather than one per write. Scope the change to the single-owner daemon regime; the multi-process path keeps today's synchronous commit until the [#176](https://github.com/mlorentedev/hive/issues/176) rollout completes.

Under the daemon this is strictly simpler than the multi-process framing would suggest: one process means **one shared queue**, so 10 concurrent writers produce one commit per tick in total — not one per writer — and no cross-process coordination is required. At a 5 s tick that is a ~5% duty cycle on a 25 ms commit, where contention is negligible.

The three questions that blocked this ADR are resolved as follows.

### 1. A sibling primitive, not an amended `Outbox[T]`

Introduce `CommitQueue` with its own crash-loss contract. `Outbox[T]`'s docstring — "do NOT use for durable state (audit logs, transactional commits, monetary ledgers)" — stays **untouched and absolute**; carving an exception into a contract that says "never" would make it advisory, and a future reader could reasonably conclude the outbox is safe for durable state generally.

The two also need different semantics: `CommitQueue` must **deduplicate paths within a tick** (the same file written twice produces one entry, not two) and defines recovery, neither of which `Outbox[T]` has. Its contract is narrower than the one it declines to inherit: an unflushed path is a **delayed commit, not lost data**, because the file write lands on disk *before* the path is queued.

### 2. A synchronous watchdog reusing the existing termination primitives

The reconciler spawns git with its own deadline and reuses the **synchronous** primitives already in `_deadline.py` — `popen_creation_kwargs()` (the per-OS process-group/kill setup) and `_cleanup_index_lock()` — rather than duplicating platform logic.

`bounded_call` is rejected here despite being the repo-wide model: it is `async def`, so reusing it would require standing up an event loop inside a daemon thread purely to call it. Coupling async machinery to a synchronous thread costs more than the consistency buys. The *termination behaviour* stays shared even though the *supervision entry point* differs, which is where the actual cross-OS risk lives.

### 3. Startup reconciliation, not a persisted queue

Recovery extends `_startup_self_heal` (which already exists and today only clears a stale `index.lock`): enumerate uncommitted vault paths and issue one recovery commit.

This is safe against the ADR-014 objection for two independent reasons: it runs **under the singleton `daemon.lock`**, so no sibling hive owns the tree, and it is a **startup event, not a recurring timer** — the property that made obsidian-git's sweep unsafe is absent. It still enumerates paths explicitly rather than running `add -A`.

A persisted queue was rejected as adding a second synchronous disk write to the very path this ADR exists to empty, in exchange for a guarantee stronger than the problem needs — the failure being mitigated is a *delayed* commit, not a lost one.

## Consequences

### Positive

- Removes the throughput ceiling this ADR exists to address: commit rate becomes a function of the tick, not of write volume.
- Write-tool latency collapses to file I/O; the commit leaves the caller's critical path entirely.
- Reuses a primitive and a threading model already proven in-repo rather than introducing a new concurrency mechanism.
- Composes with ADR-011: one daemon, one queue, one commit per tick.

### Negative

- Reintroduces a timed committer, which ADR-014 removed — safe only under the path-scoped constraint above, which is now a load-bearing invariant that future changes can silently break.
- Commit granularity coarsens: a delete-and-recreate inside one tick collapses to a single state, weakening `vault_delete`'s "git-recoverable" guarantee unless it opts out.
- A write returns before its commit exists, so the response contract changes for every caller, and HIVE-104's "(uncommitted — call vault_commit to flush)" suffix now describes the normal path rather than an opt-in one. This is the ACK-semantics shift ADR-013 predicted, and it requires **bilingual site docs** (EN + ES) per the repo's docs rule.
- A crash between write and flush leaves a file on disk uncommitted until the next daemon start. Startup reconciliation bounds the exposure to one daemon lifetime rather than indefinitely, but the window is real and is not closed by a durable queue.
- Two supervision entry points now exist — `bounded_call` for the tool path, a synchronous watchdog for the reconciler. They share termination behaviour but not structure, so a future change to one must be mirrored deliberately.

### Neutral

- `vault_commit` and the explicit `commit=False` keyword keep their current semantics; this adds a default, it does not remove an option.
- Hive stays commit-only — no push, per ADR-014.

## References

- [#322](https://github.com/mlorentedev/hive/issues/322) — measurements, methodology, and design discussion
- `specs/HIVE-322-commit-outbox/` — the spec this ADR gates
- [ADR-014](adr-014-vault-commit-coordination.md) — amended by this ADR
- [ADR-011](adr-011-phase-c-daemon-model.md) — the single-owner daemon this decision assumes
- [ADR-010](adr-010-external-committer-coexistence.md), [ADR-013](adr-013-write-idempotency-at-most-once.md), [ADR-017](adr-017-auto-commit-bypasses-vault-pre-commit-hook.md) — interactions to check before acceptance
- [ADR-008](adr-008-hard-deadline-enforcement.md) — the supervision model the reconciler thread currently escapes
- `src/hive/_outbox.py` — the primitive under consideration
