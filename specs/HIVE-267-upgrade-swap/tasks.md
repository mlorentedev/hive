---
tags: [spec, tasks, templates]
created: "2026-06-24"
---

# Tasks - HIVE-267-upgrade-swap

> TDD order. One task = one focused commit. Tick as you go. Reorder freely while spec is in `draft` state; freeze once you start `implementing`.
>
> **FROZEN — spec is `verifying` (reviewed 2026-08-07, #333).** The A3 mechanism
> shipped in #290, #294 and #302. Two items remain, both outside this repo's
> unit-test reach: the dotfiles companion trigger, and real-hardware
> re-validation. See `proposal.md` -> Acceptance criteria for what is and is not
> met.

## Setup

- [x] Branch created from main: `feat/HIVE-267-upgrade-swap`
- [x] `proposal.md` is complete and acceptance criteria are testable — reviewed 2026-08-07, marked against evidence
- [x] No open questions left in `proposal.md` "Risks / open questions" — all draft markers resolved 2026-08-07 (#333)

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
> + `current` junction + own launcher **[moved to HIVE-328 / ADR-019 — never
> built here, see the correction below]**; versions built via `uv venv` + `uv pip
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
- [x] Implement "build a version into a fresh dir" (`uv venv` + `uv pip install
      hive-vault==<v>`), never touching the in-use one; GC old versions once
      unreferenced. `_runtime.build_version()` (cleans a half-built dir on
      failure), `current_version()`, `remove_version()` (refuses the active
      version; defers a locked dir instead of crashing).
- [x] Add the `hive self-upgrade <version>` subcommand wiring the above
      (`_runtime.self_upgrade`: build → repoint → GC; `server._run_self_upgrade`
      CLI wrapper + `_dispatch` branch). Version-resolution contract
      **RESOLVED (2026-07-09): explicit `<version>` REQUIRED** — deterministic,
      network-free; auto-latest-from-PyPI is a follow-up scoped to the dotfiles
      trigger. The launcher (`~/.local/bin`) + supervisor already resolve through
      `current` (repoint primitive). Tracked as successor issue #292. Tests in
      `tests/test_runtime.py` (`self_upgrade` orchestration + CLI wrapper).
      **CORRECTION (2026-08-07, #333):** this task claimed "The launcher
      (`~/.local/bin`) + supervisor already resolve through `current`". The
      supervisor half is true; **the launcher half was false** — nothing ever
      installed one, so after a successful upgrade `current` pointed at a
      working venv no shell could find. That false claim is why the "+ own
      launcher" promise below went unnoticed, and it became
      [#328](https://github.com/mlorentedev/hive/issues/328). `~/.local/bin` is
      also the wrong directory: [ADR-019](../../docs/adr/adr-019-launcher-ownership.md)
      chose `%LOCALAPPDATA%\hive\bin` and forbids hive touching `~/.local/bin`.
- [x] **Auto-latest resolution (#292):** make `hive self-upgrade`'s version
      argument optional — omitted, it resolves the newest `hive-vault` release
      from PyPI's JSON API (`_runtime.latest_version`, the module's single
      network seam, bounded timeout, actionable WHY/FIX on failure) so the
      unattended trigger can fire a bare `hive self-upgrade`. An explicit version
      stays deterministic and network-free. Tests in `tests/test_runtime.py`
      (`latest_version` happy/timeout/bad-payload + CLI omitted-vs-explicit).
- [ ] **Companion (dotfiles, cross-repo):** replace the bare `uv tool install
      --upgrade` trigger (`setup-windows.ps1` timer / `mcp-servers.json`
      `prerequisite_command`) with `hive self-upgrade` (now bare — auto-latest
      landed above). Document the "no-longer-a-uv-tool on Windows" consequence.
- [ ] **Real-hardware re-validation (AC4, gating archive):** the #267
      reproduction no longer reproduces against the *shipped* code (manual,
      recorded in `verification.md`). The 2026-06-24 hardware pass covered the
      feasibility spike only. Blocked in practice — the target Windows box is in
      the orphaned-trampoline state (dotfiles#791).

## Closing

- [~] Every acceptance criterion from `proposal.md` is covered by at least one test — AC3 fully; AC1/AC2 at unit level only (the end-to-end half needs #328's launcher); AC4 is inherently manual
- [x] Every acceptance criterion has a matching entry in `features.json` with a non-vacuous verification command — 5 features, all `pending`; the two manual ones say so explicitly
- [x] Type checks pass — `mypy --strict src/` clean
- [x] Lint passes — `ruff check` + `ruff format --check` clean
- [x] No unrelated changes in the diff (no scope creep)
- [x] `verification.md` filled in — evidence recorded 2026-08-07
- [x] PR opened referencing this spec folder — #290, #294, #302

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
