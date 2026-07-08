---
tags: [spec, tasks, templates]
created: "2026-06-24"
---

# Tasks - HIVE-267-upgrade-swap

> TDD order. One task = one focused commit. Tick as you go. Reorder freely while spec is in `draft` state; freeze once you start `implementing`.

## Setup

- [x] Branch created from main: `feat/HIVE-267-upgrade-swap`
- [ ] `proposal.md` is complete and acceptance criteria are testable
- [ ] No open questions left in `proposal.md` "Risks / open questions"

## Implementation

> Mechanism = **A3 (versioned-dir + atomic junction swap)**, spike-gated per
> ADR-015. TDD order, one commit each. The spike GATES the rest: if A3 proves too
> invasive on real hardware, stop and fall back to A4→A2 (proposal.md → Risks).

- [x] **Spike (gating): PASSED** (2026-06-24, real non-admin Windows). Junction
      created non-admin; `current` repointed `versions/1.41.5` → `1.41.6` while
      1.41.5 was locked — no "Access is denied"; old-version GC deferred then
      succeeded on release. Evidence in `verification.md`. **A3 confirmed feasible
      — proceed; no fallback to A4/A2 needed.**
> Interposition = **hive owns the layout** (`%LOCALAPPDATA%\hive\runtime\versions\<v>`
> + `current` junction + own launcher; versions built via `uv venv` + `uv pip
> install`; dotfiles upgrade calls `hive self-upgrade`). Likely a new
> `src/hive/_runtime.py` (layout/junction/GC) + a `hive self-upgrade` subcommand.

- [x] Write failing test for the junction-repoint primitive. Renderer (`mklink /J`
      argv) asserted as a string like the `_service` renderers; the repoint
      orchestration runs a **real** symlink on POSIX CI / a real junction on
      Windows (single OS seam `_make_junction`), so criterion 3 is exercised, not
      only mocked. `tests/test_runtime.py`.
- [x] Implement the repoint primitive + versioned-layout paths
      (`runtime_root()`, `versions_dir()`, `version_path()`, `current_link()`,
      `repoint(version)`) in `src/hive/_runtime.py`.
- [x] Write failing test: a failed repoint leaves `current` at the previous
      version (`test_failed_repoint_leaves_the_previous_current_intact`).
- [x] Implement the rollback / no-corruption guarantee (stage-then-flip: the
      fallible `_make_junction` runs before `current` is disturbed) + actionable
      WHY/FIX `RuntimeError`.
- [ ] Implement "build a version into a fresh dir" (`uv venv` + `uv pip install
      hive-vault==<v>`), never touching the in-use one; GC old versions once
      unreferenced.
- [ ] Add the `hive self-upgrade [<version>]` subcommand wiring the above; the
      launcher (`~/.local/bin`) + supervisor resolve through `current`.
- [ ] **Companion (dotfiles, cross-repo):** replace the bare `uv tool install
      --upgrade` trigger (`setup-windows.ps1` timer / `mcp-servers.json`
      `prerequisite_command`) with `hive self-upgrade`. Document the
      "no-longer-a-uv-tool on Windows" consequence.
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
