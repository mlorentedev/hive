---
id: "ADR-018-asynchronous-commit-queue"
type: adr
status: accepted
owner: manu
date: "2026-08-07"
issue: "hive#322"   # repo#NNN — GitHub issue / Project item that triggered this decision
tags: [architecture, decision, concurrency, git, performance]
created: "2026-08-07"
---

# ADR-018: Asynchronous Commit Queue

## Status

Accepted (2026-08-07). Gates `specs/HIVE-322-commit-outbox/`, which is now unfrozen for implementation. **Amends [ADR-014](adr-014-vault-commit-coordination.md)** — see "The ADR-014 tension" below, which is the load-bearing part of this decision. **Triggers the deferred "2b" evolution of [ADR-013](adr-013-write-idempotency-at-most-once.md)** — see "Relationship to ADR-013" below. The three design questions that blocked this ADR were resolved on 2026-08-07 and are recorded under "Decision".

Revised the same day, before acceptance, on three points:

1. The reconciler is **no longer scoped to the daemon** (§Decision), which removes the dependency on the [#176](https://github.com/mlorentedev/hive/issues/176) rollout.
2. Deferral becomes the **default** rather than an opt-in, making this a breaking contract change (§4).
3. Startup recovery **reports and never commits** (§3). A first attempt at (1) kept daemon-side recovery committing under `daemon.lock`; review showed that lock excludes sibling hives but not a human editing in Obsidian, so the safety argument did not hold. Report-only is the only resolution that needs no provenance, and it removes the regime asymmetry (1) had introduced.

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

**Consequence for #322 as originally written:** the first comment on that issue proposed "per-path drain, with an `add -A` sweep as the self-heal fallback". Under ADR-014 that fallback **is** the failure mode. It is withdrawn. §3 answers recovery differently: startup reports rather than sweeps, and the only legal sweep is the one a human asks for via `vault_commit`.

## Relationship to ADR-013

ADR-013 already anticipated this work. It adopted "2a" (synchronous idempotency-key claim) and **deferred "2b"** — the durable journal plus reconciler and replay-on-startup — explicitly gated on:

> "EITHER (i) telemetry showing git-commit serialization as a real bottleneck, OR (ii) genuine concurrent write-heavy load arriving."

**Both gates have now fired.** The measurements above are (i); the 10-concurrent-writer field workload is (ii). ADR-013 also predicted the contract consequence this ADR must own: 2b "changes **ACK semantics** (accepted ≠ committed) — an observable contract change needing bilingual docs".

Two points of reconciliation:

- **This ADR implements a subset of 2b.** It defers the *commit*; it does not build 2b's durable journal, and after the §3 revision it does not build 2b's replay-on-startup either — startup reports rather than replays. ADR-013's constraint that "the journal MUST be **SQLite-backed**" therefore does not bind here, but **remains binding** if idempotency-keyed at-most-once is later extended across the deferred path.
- **ADR-013's at-most-once guarantee survives.** A claimed key means "the write was applied", and deferral does not duplicate writes. What changes is only that a claimed key no longer implies its commit has landed — which is precisely the ACK-semantics shift ADR-013 flagged.

[ADR-017](adr-017-auto-commit-bypasses-vault-pre-commit-hook.md) needs no amendment: the reconciler's deferred commit routes through `_git_commit` → `_commit_args` like every other write-path commit, so `--no-verify` continues to apply uniformly and push-side scanning is unaffected. There are no recovery commits to consider after §3.

## Decision

Move committing off the write path into a reconciler thread that drains a queue of pending paths on a tick (`HIVE_COMMIT_TICK_S`), producing one commit per tick rather than one per write. The reconciler runs **in every hive server process, daemon or not**. This decision does not wait on the [#176](https://github.com/mlorentedev/hive/issues/176) rollout.

Scoping the queue to the daemon was considered and rejected. The argument for it was that one process means one shared queue and therefore no cross-process coordination is required — but that coordination **already exists and is already load-bearing**. `_git_filelock(vault_path)` in `_helpers.py` is a per-vault `filelock.FileLock` that every write path already acquires, and HIVE-115's AC-9b already documents concurrent processes interleaving staged-but-uncommitted state underneath it, rescued by the next call. The queue changes commit *frequency*; it does not change the concurrency model.

This makes one requirement explicit that the argument would otherwise leave implied: **the reconciler must acquire `_git_filelock(vault)` around its commit.** The write path releases that lock when the tool returns, so a deferred commit runs entirely outside it — the pre-existing serialization only carries the rescope if the reconciler participates in it. Without this, moving the commit off the write path would also move it out from under the lock, which is the opposite of what this section claims.

The duty-cycle arithmetic is regime-independent, because the total commit work is identical either way. One daemon queue at a 5 s tick is a ~5% duty cycle on a 25 ms commit; ten process-local queues at the same tick is the same ~5%, redistributed and serialized by the filelock that already guards it. The measurements above agree: separate processes are not the *slower* regime (33.1 writes/s against the daemon's 30.0) — they are the *unfair* one, p50 23.5 ms with a 1389.6 ms tail. Deferring the commit removes that tail from the caller in both regimes, which is the outcome this ADR exists to buy.

Restricting the fix to the daemon would also aim it away from the machines that hurt. The 1.4 s tail is a multi-process measurement; #176 has been open since 2026-06-05, and on at least one affected machine the daemon was never activated at all. A fix gated on that rollout does not reach the workload that motivated it.

What the daemon still uniquely provides is a **singleton**, and exactly one part of this design needs one — recovery (§3).

The three questions that blocked this ADR, plus the default-flip decision added in the same-day revision, are resolved as follows. (§4 never blocked the ADR — it arrives from the spec's remaining draft items.)

### 1. A sibling primitive, not an amended `Outbox[T]`

Introduce `CommitQueue` with its own crash-loss contract. `Outbox[T]`'s docstring — "do NOT use for durable state (audit logs, transactional commits, monetary ledgers)" — stays **untouched and absolute**; carving an exception into a contract that says "never" would make it advisory, and a future reader could reasonably conclude the outbox is safe for durable state generally.

The two also need different semantics: `CommitQueue` must **deduplicate paths within a tick** (the same file written twice produces one entry, not two) and defines recovery, neither of which `Outbox[T]` has. Its contract is narrower than the one it declines to inherit: an unflushed path is a **delayed commit, not lost data**, because the file write lands on disk *before* the path is queued.

### 2. A synchronous watchdog reusing the existing termination primitives

The reconciler spawns git with its own deadline and reuses the **synchronous** primitives already in `_deadline.py` — `popen_creation_kwargs()` (the per-OS process-group/kill setup) and `_cleanup_index_lock()` — rather than duplicating platform logic.

`bounded_call` is rejected here despite being the repo-wide model: it is `async def`, so reusing it would require standing up an event loop inside a daemon thread purely to call it. Coupling async machinery to a synchronous thread costs more than the consistency buys. The *termination behaviour* stays shared even though the *supervision entry point* differs, which is where the actual cross-OS risk lives.

### 3. Startup recovery reports; it never commits. Remediation stays user-initiated

**No automatic startup recovery commits anything, in either regime.** Startup enumerates the vault paths left uncommitted and *reports* them — count and oldest age, in the `vault_health` runtime block. It does not stage and does not commit.

The first draft of this section split by regime: daemon-side recovery would commit under the singleton `daemon.lock`, and the non-daemon path would refuse. Review killed that, correctly. **`daemon.lock` excludes sibling *hives*; it does not exclude a *human*.** A maintainer with a half-edited note open in Obsidian while the daemon restarts produces dirty working-tree state that recovery cannot distinguish from its own orphaned write — so ADR-014's objection survived inside the regime the draft called safe. The singleton was necessary but not sufficient, and no amount of locking supplies what was actually missing.

What was missing is **provenance**: recovery would need to know which paths *hive* wrote, and after a crash the in-memory queue that knew them is gone. Reconstructing it means persisting a marker on the write path — the very thing this ADR exists to empty, and rejected below on its own merits. Two other options were weighed: narrowing recovery to paths matching hive's write conventions (fragile — it infers provenance from a naming convention), and a minimal persisted provenance record (buys a guarantee stronger than the problem needs, at the cost this ADR is trying to avoid).

Report-only wins because it is the only resolution that **does not need provenance at all.** Reporting a path is safe whoever wrote it.

The distinction this lands on is sharper than the one the draft was reaching for, and it restates ADR-014 rather than bending it:

> **Automatic committing is path-scoped, always.** Only the reconciler commits, and only paths it queued itself. **Sweeping the working tree is legal only as an explicit user action** — which is exactly what `vault_commit` already is.

`vault_commit` already sweeps via `_git_commit_all`'s `git add -A` (HIVE-104), and that stays correct: a human asking for a flush has consented to flushing their own in-progress edits. A timer has no such consent. ADR-014's objection was never to *sweeping* as such — it was to a **timer** doing it unasked, which is why obsidian-git's auto-commit had to be turned off while `vault_commit` did not.

So an orphaned path waits for the next hive write to that vault, an explicit `vault_commit`, or an external committer — and is visible in `vault_health` the whole time rather than silently rotting. This follows the warn-don't-reject precedent already used for suspected input corruption ([#114](https://github.com/mlorentedev/hive/issues/114), HIVE-115).

Two things get simpler as a result, which is the tell that this is the right cut. `_startup_self_heal` keeps its current job (clear a stale `index.lock`) and gains a report rather than a commit path, so it needs no lock reasoning at all. And the regime asymmetry disappears: daemon and multi-process now behave identically at startup, so there is no longer a difference for a future reader to mistake for a bug.

A persisted queue was rejected as adding a second synchronous disk write to the very path this ADR exists to empty, in exchange for a guarantee stronger than the problem needs — the failure being mitigated is a *delayed* commit, not a lost one. The file reaches disk **before** its path is queued, and that ordering is what makes every weaker guarantee in this section honest.

### 4. Deferral becomes the default; `commit=True` becomes the synchronous escape hatch

The `commit` keyword on `vault_write` / `vault_patch` flips its default from `True` (synchronous commit) to deferral.

Leaving the default synchronous would reproduce HIVE-104's outcome: a correct mechanism that nobody reaches for, because every agent must be configured to pass the flag and something must call the flush. HIVE-104 measured 4.8x on batched writes and 10.4x on multi-patch, and the field response was that agents avoid the MCP for writes anyway. The measured problem is caused by the *default*, so the default is what has to change.

`commit=True` keeps a precise meaning — commit synchronously before returning — for callers that need the commit confirmed. `vault_delete` opts out of the queue entirely and stays synchronous: its "git-recoverable" guarantee is exactly what coarsened commit granularity would weaken, since a delete and a recreate inside one tick collapse to a single state.

This is a **breaking contract change**: a successful write no longer implies that a commit exists. It is the ACK-semantics shift ADR-013 predicted, it requires bilingual site docs (EN + ES) per the repo's i18n rule, and it ships as `feat!` — the next release is a major.

Two adjacent mechanisms change meaning with it, and both are part of the same break:

- **`commit=False` is subsumed.** Under HIVE-104 it meant "written to disk, and it stays uncommitted until *you* call `vault_commit`". Under the queue it becomes an alias for the deferred default: the path is **queued and becomes eligible for the next drain**. Note the weaker verb — eligibility, not completion. A drain may legitimately produce no commit (the external-committer short-circuit below), and a process killed before its tick leaves the path on disk uncommitted (§3). The only guarantee is that the write reached disk before the path was queued; everything after that is best-effort. The indefinite-deferral mode is therefore **removed**, not preserved — but what it was for (batch many writes, pay for one commit) is what the queue now does automatically and without configuration. `vault_commit` remains available to flush early. Callers that genuinely need "on disk, never auto-committed" no longer have a keyword for it; that is a deliberate reduction, and the one place this ADR removes a capability rather than adding a default.
- **`HIVE_AUTO_DEFER_TO_EXTERNAL_COMMITTER` composes by short-circuiting the flush, not the queue.** The predicate `_should_defer_to_external_committer()` (env true ∧ obsidian-git present ∧ healthy) exists so hive can yield committing to obsidian-git under [ADR-010](adr-010-external-committer-coexistence.md). A reconciler that committed at every tick regardless would silently defeat it. Resolution: paths are queued as normal, and the reconciler evaluates the predicate at drain time — when the external committer is healthy it **drains without committing**, leaving the paths for obsidian-git; when it is not, it commits. This keeps the ADR-010 hand-off intact and keeps the decision in one place instead of two competing deferral mechanisms.

## Consequences

### Positive

- Removes the throughput ceiling this ADR exists to address: commit rate becomes a function of the tick, not of write volume.
- Write-tool latency collapses to file I/O; the commit leaves the caller's critical path entirely.
- Reuses a primitive and a threading model already proven in-repo rather than introducing a new concurrency mechanism.
- Ships independently of [#176](https://github.com/mlorentedev/hive/issues/176), so it reaches the multi-process machines that carry the 1.4 s tail today rather than waiting on a rollout that has been open since 2026-06-05.
- Composes with ADR-011 without depending on it: under the daemon there is one queue and one commit per tick, which is the best case, not the precondition.
- Because deferral is the default (§4), the improvement arrives without per-agent configuration — the failure mode that limited HIVE-104's opt-in coalescing.

### Negative

- Reintroduces a timed committer, which ADR-014 removed — safe only under the path-scoped constraint above, which is now a load-bearing invariant that future changes can silently break.
- Commit granularity coarsens: a delete-and-recreate inside one tick collapses to a single state, weakening `vault_delete`'s "git-recoverable" guarantee unless it opts out.
- A write returns before its commit exists, so the response contract changes for every caller, and HIVE-104's "(uncommitted — call vault_commit to flush)" suffix now describes the normal path rather than an opt-in one. This is the ACK-semantics shift ADR-013 predicted, and it requires **bilingual site docs** (EN + ES) per the repo's docs rule.
- A crash between write and flush leaves a file on disk uncommitted, and **nothing recovers it automatically** in either regime (§3). It waits for the next hive write to that vault, an explicit `vault_commit`, or an external committer — visible in `vault_health` throughout, but a real window that a durable queue would have closed and this design deliberately does not.
- Hive therefore no longer self-heals its own git state at startup. That is a genuine reduction against the first draft of this ADR, accepted because the alternative required provenance hive cannot reconstruct after a crash without persisting on the write path.
- Two supervision entry points now exist — `bounded_call` for the tool path, a synchronous watchdog for the reconciler. They share termination behaviour but not structure, so a future change to one must be mirrored deliberately.
- The default flip (§4) breaks the response contract for every existing caller, and `feat!` forces a major release. Callers that relied on "write returned ⇒ commit exists" must now pass `commit=True` explicitly.

### Neutral

- `vault_commit` keeps its current semantics as the explicit flush, and `commit=True` remains available as the synchronous escape hatch. Both ends of the range — commit now, or flush on demand — stay reachable; what changes is which behaviour you get without asking (§4). The one capability genuinely lost is *indefinite* deferral, which §4 records as a deliberate reduction rather than a neutral change.
- Hive stays commit-only — no push, per ADR-014.

## References

- [#322](https://github.com/mlorentedev/hive/issues/322) — measurements, methodology, and design discussion
- `specs/HIVE-322-commit-outbox/` — the spec this ADR gates
- [ADR-014](adr-014-vault-commit-coordination.md) — amended by this ADR
- [ADR-011](adr-011-phase-c-daemon-model.md) — the single-owner daemon; the queue's best case, but after the §3 revision it is **not required by any part of this decision**
- `src/hive/_helpers.py` — `_git_filelock()`, the pre-existing cross-process serialization the rescope rests on
- [ADR-010](adr-010-external-committer-coexistence.md), [ADR-013](adr-013-write-idempotency-at-most-once.md), [ADR-017](adr-017-auto-commit-bypasses-vault-pre-commit-hook.md) — interactions to check before acceptance
- [ADR-008](adr-008-hard-deadline-enforcement.md) — the supervision model the reconciler thread currently escapes
- `src/hive/_outbox.py` — the primitive under consideration
