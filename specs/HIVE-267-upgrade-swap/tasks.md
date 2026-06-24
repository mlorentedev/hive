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

> Mechanism = **A3 (versioned-dir + atomic junction swap)**, spike-gated per
> ADR-015. TDD order, one commit each. The spike GATES the rest: if A3 proves too
> invasive on real hardware, stop and fall back to A4→A2 (proposal.md → Risks).

- [ ] **Spike (gating):** on a real non-admin Windows box, validate A3 with uv —
      write a fresh `versions/<v>/` dir, create the `current` junction
      (`mklink /J`, no admin), atomically repoint it **while the daemon runs**,
      relaunch from `current`, confirm no locked-file / "Access is denied" error.
      Record evidence in `verification.md`. **If this fails, do not proceed —
      revisit the ladder.**
- [ ] Write failing test for the junction-repoint primitive (pure, OS-mocked like
      the existing `_service` renderer tests).
- [ ] Implement the versioned-dir layout + `current` junction repoint helper.
- [ ] Write failing test: a failed repoint leaves `current` pointing at the
      previous version (no corruption, previous install intact).
- [ ] Implement the rollback / no-corruption guarantee + actionable WHY/FIX error.
- [ ] Wire the swap into the upgrade path (`_service.py` / `_daemon.py`); the
      supervisor relaunches from `current`; GC the old dir once unreferenced.
- [ ] Decouple from bare `uv tool install --upgrade` (hive-managed upgrade) —
      companion dotfiles work, cross-repo, tracked separately.
- [ ] Real-hardware re-validation: the #267 reproduction no longer reproduces
      (manual, recorded in `verification.md`).

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
