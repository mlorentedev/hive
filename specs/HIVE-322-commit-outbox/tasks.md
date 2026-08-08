---
tags: [spec, tasks, templates]
created: "2026-08-07"
---

# Tasks - HIVE-322-commit-outbox

> TDD order. One task = one focused commit. Tick as you go. Reorder freely while spec is in `draft` state; freeze once you start `implementing`.
>
> **NOT FROZEN — one gate left.** All four design questions are resolved in ADR-018, and both remaining draft items in `proposal.md` are closed (2026-08-07). The only remaining gate is **ADR-018 acceptance**; it is still `proposed`. Freeze this file the moment the ADR is accepted.

## Setup

- [ ] Branch created from main: `feat/HIVE-322-commit-outbox` — not yet created; the docs-only rescope shipped on `docs/adr-018-in-process-reconciler`
- [ ] **ADR-018 accepted** — **gating**. Authored 2026-08-07, revised the same day to drop the daemon-only scoping (§Decision), split recovery by regime (§3), and make deferral the default (§4).
- [x] `proposal.md` is complete and acceptance criteria are testable — AC1-AC12
- [x] No open questions left in `proposal.md` "Risks / open questions" — both remaining draft items resolved (`vault_delete` opts out and stays synchronous; suffix reworded for deferral-by-default)

## Implementation

- [ ] [P] [AC4] Write failing test: a reconciler flush exceeding its deadline is terminated, leaving no orphaned `git` process and no stale `index.lock`
- [ ] [AC4] Give the reconciler a synchronous watchdog reusing `_deadline.py`'s sync primitives (`popen_creation_kwargs()`, `_cleanup_index_lock()`) — not `bounded_call`, which is `async def` (ADR-018 §2)
- [ ] [P] [AC2] Write failing test: N queued paths across one tick produce exactly one commit containing exactly those paths, with no unrelated working-tree file staged
- [ ] [P] [AC8] Write failing test: the same path written twice within one tick produces one queue entry, not two
- [ ] [AC2] [AC8] Add `CommitQueue` — a sibling primitive to `Outbox[T]` with its own crash-loss contract and within-tick path dedup (ADR-018 §1) — plus a reconciler thread draining on `HIVE_COMMIT_TICK_S`
- [ ] [P] [AC7] Write failing test: the reconciler never stages a working-tree file it did not queue (the load-bearing ADR-014 invariant — guard it explicitly)
- [ ] [AC1] Route `vault_write` / `vault_patch` through the queue; assert by test that no `git commit` occurs in the tool's call path
- [ ] [P] [AC6] Write failing test: clean shutdown drains the queue; nothing queued is discarded
- [ ] [AC6] Implement drain-on-shutdown on **two** triggers, since the reconciler is no longer daemon-only: the daemon's existing signal handling, and the stdio server's lifespan teardown / stdin EOF. A client `SIGKILL` gets no drain — that path is covered by AC11's observability, not by AC6
- [ ] [P] [AC5] Write failing test: `vault_health` reports queue depth and last-flush age, and a stalled reconciler is visible
- [ ] [AC5] Surface queue depth + last-flush age in the `vault_health` runtime block
- [ ] [P] [AC12] Write failing test: a plain `vault_write` produces no commit in its call path; `commit=True` produces one before returning; `vault_delete` commits synchronously regardless of tick
- [ ] [AC12] Flip the `commit` default to deferral on `vault_write` / `vault_patch`, keep `commit=True` synchronous, and opt `vault_delete` out of the queue entirely (ADR-018 §4). **Breaking — the commit must be `feat!`**
- [ ] [AC3] Concurrency benchmark beside `TestWriteThroughputBenchmark`: 10 writers, assert commit count is bounded by elapsed-time / tick rather than by write count
- [ ] [AC3] Run the same benchmark in the **separate-processes** regime, sharing `_git_filelock`, and assert the analogous bound — `P x elapsed/tick` for P processes, still independent of write count. This is the measurement that substantiates dropping the daemon-only scoping
- [ ] [P] [AC9] Write failing test: startup reconciliation commits uncommitted vault paths left by a prior crash, enumerating paths explicitly (never `add -A`)
- [ ] [AC9] Extend `_startup_self_heal` with the recovery commit, under the singleton `daemon.lock` (ADR-018 §3)
- [ ] [P] [AC10] Write failing test: a **non-daemon** server start performs no recovery commit and leaves a foreign dirty working-tree file untouched — guards the deliberate refusal against a future "helpful" sweep
- [ ] [P] [AC11] Write failing test: `vault_health` reports the count and age of uncommitted vault paths
- [ ] [AC11] Surface uncommitted-path count + oldest age in the `vault_health` runtime block — the only recovery signal outside the daemon (warn-don't-reject, per the #114 precedent)
- [ ] Refactor: fold the queue path and the existing `commit=False` deferral into one code path rather than two parallel deferral mechanisms — `commit=False` becomes an alias for the deferred default (ADR-018 §4), removing the indefinite-deferral mode
- [ ] [P] Write failing test: with `HIVE_AUTO_DEFER_TO_EXTERNAL_COMMITTER` satisfied, a tick drains the queue **without** producing a commit; with it unsatisfied, the same tick commits
- [ ] Evaluate `_should_defer_to_external_committer()` at drain time inside the reconciler, so the ADR-010 hand-off to obsidian-git is not silently defeated by a queue that commits on every tick (ADR-018 §4)
- [ ] (contract housekeeping) Response-suffix wording updated for deferral-by-default — name the tick rather than implying an anomaly; `commit=True` still means synchronous
- [ ] (docs housekeeping) Bilingual site docs (EN + ES) for the ACK-semantics change ADR-013 flagged as an observable contract change, including the major-version note

## Closing

- [ ] Every acceptance criterion from `proposal.md` is covered by at least one test
- [ ] Every acceptance criterion has a matching entry in `features.json` with a non-vacuous verification command
- [ ] Type checks pass
- [ ] Lint passes
- [ ] No unrelated changes in the diff (no scope creep)
- [ ] `verification.md` filled in
- [ ] PR opened referencing this spec folder

## Machine-readable features

This spec emits a sibling `features.json` following [[pattern-feature-list-as-primitive]]. Not yet written — it is authored once `tasks.md` freezes (after the gating ADR), so the verification commands describe real test names rather than guesses.

**Pass-state gating:** the agent CANNOT write `"state": "passing"` — only the harness, after running `verification` and capturing exit code 0, may set that terminal state.
