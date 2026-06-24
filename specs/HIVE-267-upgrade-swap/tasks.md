---
tags: [spec, tasks, templates]
created: "2026-06-24"
---

# Tasks - HIVE-267-upgrade-swap

> TDD order. One task = one focused commit. Tick as you go. Reorder freely while spec is in `draft` state; freeze once you start `implementing`.

## Setup

- [ ] Branch created from main: `feat/HIVE-267-upgrade-swap`
- [ ] `proposal.md` is complete and acceptance criteria are testable
- [ ] No open questions left in `proposal.md` "Risks / open questions"

## Implementation

> **FROZEN pending the A3-vs-A4 decision** (proposal.md → Risks, the blocking open
> question). The implementation steps depend on which swap mechanism is chosen and
> on whether the fix interposes inside uv's tool layout or installs a hive-owned
> location behind a junction. Do not expand this section until that is resolved.
>
> Sketch once decided (TDD order, one commit each):
>
> - [ ] Write failing test for the swap primitive (atomic repoint / rename) on a
>       locked target — pure, OS-mocked like the existing `_service` renderer tests.
> - [ ] Implement the swap primitive (A3 junction repoint **or** A4 MoveFileEx).
> - [ ] Write failing test: a failed swap leaves the previous install intact.
> - [ ] Implement the rollback / no-corruption guarantee + actionable error.
> - [ ] Wire the swap into the upgrade path (`_service.py` / `_daemon.py`).
> - [ ] Real-hardware validation on a non-admin Windows box (manual, recorded in
>       `verification.md`).

## Closing

- [ ] Every acceptance criterion from `proposal.md` is covered by at least one test
- [ ] Every acceptance criterion has a matching entry in `features.json` with a non-vacuous verification command
- [ ] Type checks pass
- [ ] Lint passes
- [ ] No unrelated changes in the diff (no scope creep)
- [ ] `verification.md` filled in
- [ ] PR opened referencing this spec folder

## Machine-readable features

This spec emits a sibling `features.json` (alongside this file) following [[pattern-feature-list-as-primitive]]. The JSON is the harness-facing contract: each acceptance criterion maps to ≥1 feature with `id`, `behavior`, `verification` (executable command), `state` (lifecycle), and `evidence` (harness-captured output).

**Pass-state gating:** the agent CANNOT write `"state": "passing"` — only the harness, after running `verification` and capturing exit code 0, may set that terminal state. Reviewers must reject PRs where features.json contains `passing` entries with empty `evidence`.

Minimal `features.json` skeleton (drop into `specs/HIVE-267-upgrade-swap/features.json` once the mechanism is decided):

```json
[
  {
    "id": "HIVE-267-upgrade-swap-f1",
    "behavior": "<one-line copy of an acceptance criterion>",
    "verification": "<single shell command; exit 0 means pass>",
    "state": "pending",
    "evidence": ""
  }
]
```
