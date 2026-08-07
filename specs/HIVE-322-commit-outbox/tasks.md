---
tags: [spec, tasks, templates]
created: "2026-08-07"
---

# Tasks - HIVE-322-commit-outbox

> TDD order. One task = one focused commit. Tick as you go. Reorder freely while spec is in `draft` state; freeze once you start `implementing`.
>
> **NOT FROZEN.** The three `[MUST RESOLVE]` design questions are resolved (ADR-018, 2026-08-07), but two `[AGENT-DRAFT]` items remain in `proposal.md` and ADR-018 is still `proposed`. Implementation begins once the ADR is accepted.

## Setup

- [x] Branch created from main: `feat/HIVE-322-commit-outbox`
- [ ] **ADR-018 accepted** — authored 2026-08-07 with all three design questions resolved; awaiting acceptance — **gating**
- [ ] `proposal.md` is complete and acceptance criteria are testable
- [ ] No open questions left in `proposal.md` "Risks / open questions" — 2 `[AGENT-DRAFT]` items remain (`vault_delete` opt-out, response-suffix wording)

## Implementation

- [ ] [P] [AC4] Write failing test: a reconciler flush exceeding its deadline is terminated, leaving no orphaned `git` process and no stale `index.lock`
- [ ] [AC4] Give the reconciler a synchronous watchdog reusing `_deadline.py`'s sync primitives (`popen_creation_kwargs()`, `_cleanup_index_lock()`) — not `bounded_call`, which is `async def` (ADR-018 §2)
- [ ] [P] [AC2] Write failing test: N queued paths across one tick produce exactly one commit containing exactly those paths, with no unrelated working-tree file staged
- [ ] [P] [AC8] Write failing test: the same path written twice within one tick produces one queue entry, not two
- [ ] [AC2] [AC8] Add `CommitQueue` — a sibling primitive to `Outbox[T]` with its own crash-loss contract and within-tick path dedup (ADR-018 §1) — plus a reconciler thread draining on `HIVE_COMMIT_TICK_S`
- [ ] [P] [AC7] Write failing test: the reconciler never stages a working-tree file it did not queue (the load-bearing ADR-014 invariant — guard it explicitly)
- [ ] [AC1] Route `vault_write` / `vault_patch` through the queue; assert by test that no `git commit` occurs in the tool's call path
- [ ] Refactor: fold the queue path and the existing `commit=False` deferral into one code path rather than two parallel deferral mechanisms
- [ ] [P] [AC6] Write failing test: clean shutdown drains the queue; nothing queued is discarded
- [ ] [AC6] Implement drain-on-shutdown, wired to the daemon's existing signal handling
- [ ] [P] [AC5] Write failing test: `vault_health` reports queue depth and last-flush age, and a stalled reconciler is visible
- [ ] [AC5] Surface queue depth + last-flush age in the `vault_health` runtime block
- [ ] [AC3] Concurrency benchmark beside `TestWriteThroughputBenchmark`: 10 writers, assert commit count is bounded by elapsed-time / tick rather than by write count
- [ ] [AC3] Decide and implement the non-daemon (multi-process) behaviour — keep synchronous commit until #176 lands
- [ ] [P] [AC9] Write failing test: startup reconciliation commits uncommitted vault paths left by a prior crash, enumerating paths explicitly (never `add -A`)
- [ ] [AC9] Extend `_startup_self_heal` with the recovery commit, under the singleton `daemon.lock` (ADR-018 §3)
- [ ] (risk-mitigation, no AC) `vault_delete` opts out of the queue (keeps synchronous commit) per the proposal's resolution
- [ ] (contract housekeeping, no AC) Response-suffix wording updated for deferral-by-default; `commit=True` still means synchronous
- [ ] (docs housekeeping, no AC) Bilingual site docs (EN + ES) for the ACK-semantics change ADR-013 flagged as an observable contract change

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
