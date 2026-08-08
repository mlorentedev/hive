---
tags: [spec, tasks, templates]
created: "2026-08-07"
---

# Tasks - HIVE-328-runtime-launcher

> TDD order. One task = one focused commit. Tick as you go.
>
> **Split into two PRs.** PR1 (exec resolution) had no design ambiguity and shipped first ([#330](https://github.com/mlorentedev/hive/pull/330)) — it is the half that stops `hive service install` registering a daemon against a dead binary. PR2 (the launcher) was blocked on the directory-ownership decision; that is **resolved by [ADR-019](../../docs/adr/adr-019-launcher-ownership.md)**, accepted 2026-08-07, and PR2 has no gate left.

## Setup

- [x] Branch created from master: `fix/resolve-exec-verifies-the-binary`
- [x] Gating issue open: hive#328
- [x] `proposal.md` is complete and acceptance criteria are testable
- [x] No open questions left in `proposal.md` "Risks / open questions" — the launcher-directory question is resolved by ADR-019

## Implementation

### PR1 — honest exec resolution (AC1-AC6) — no design ambiguity, ships first

- [x] [P] [AC1] Write failing test: the A3 layout beats an arbitrary PATH hit, and the returned path goes **through `current`**, not `versions/<v>`.
- [x] [P] [AC2] Write failing test: a PATH hit that cannot start falls through to the module invocation.
- [x] [P] [AC3] Write failing test: a healthy PATH hit is still selected when no layout exists.
- [x] [P] [AC4] Write failing test: neither layout nor PATH hit yields `<python> -m hive.server`.
- [x] [P] [AC5] Write failing test: the probe answers `False` on a raised `OSError` and never propagates.
- [x] [P] [AC6] Write failing test: a `current` pointing at a version dir with no launcher inside does not win.
- [x] [AC1] Add `_current_layout_exec()` — reads `_runtime.current_link() / {Scripts,bin} / hive[.exe]`, returns `None` when absent.
- [x] [AC2] [AC5] Add `_executes(command)` — bounded `<command> --version` probe, broad `Exception` -> `False`, `_subprocess_run_kwargs()` so no console window flashes on Windows.
- [x] [AC1-AC4] Rewrite `_resolve_exec()` as layout -> verified PATH hit -> module invocation.
- [x] Test fixture uses `_runtime._make_junction`, not `Path.symlink_to`: a Windows symlink needs `SeCreateSymbolicLinkPrivilege` (WinError 1314) while a junction does not — which is precisely why A3 uses `mklink /J`. Reusing the production seam keeps the fixture honest cross-OS.
- [x] `make check` equivalent green: ruff, ruff format --check, mypy --strict, pytest.

### PR2 — the PATH launcher (AC7-AC11) — unblocked; ADR-019 accepted 2026-08-07

- [x] **GATE: decide the launcher directory** — resolved by ADR-019: hive owns `%LOCALAPPDATA%\hive\bin`, prepended to the User PATH, and never touches `~/.local/bin`. The spec's original criterion (*"which one `hive service install` can make work without a shell restart"*) was aimed at the wrong consumer — service install uses an absolute path and never reads PATH.
- [x] **ADR-019 accepted** (2026-08-07) — was **gating**; PR2 is now free to start
- [ ] [P] [AC9] Write failing test: no install path writes to or deletes from `~/.local/bin` (guards the rejected option C — the boundary *is* the decision)
- [ ] [P] [AC10] Write failing test: a `hive*` on PATH failing `_executes()` is reported and named alongside `dotf doctor --fix`, and is left unmodified
- [ ] [P] [AC11] Write failing test: the hive bin dir is **prepended** to the User PATH, not appended
- [ ] [P] [AC8] Write failing test: re-running install is idempotent — no duplicate `PATH` entries, no rewritten shim
- [ ] [AC7] Write a `hive.cmd` shim into `%LOCALAPPDATA%\hive\bin` dispatching through `current`, as part of `self_upgrade` and first install
- [ ] [AC11] Prepend the directory to the User `PATH` (User-scope env var; no admin)
- [ ] [AC10] Probe other `hive*` PATH entries with `_executes()` and warn on failure — detect only, never delete (ADR-019 Decision 4)
- [ ] [AC7] Verify on Windows from a **fresh** shell; an already-open shell is explicitly out of contract
- [ ] Author `features.json` now that the criteria set is complete (AC1-AC11)
- [ ] Then flip ADR-015 -> `accepted` (its AC-3) and unblock dotfiles#791 PR2

## Closing

- [x] Every PR1 acceptance criterion is covered by at least one test
- [ ] Every acceptance criterion has a matching entry in `features.json` — deferred to PR2, when the criteria set is complete
- [x] Type checks pass
- [x] Lint passes
- [x] No unrelated changes in the diff (no scope creep)
- [x] `verification.md` filled in
- [ ] PR opened referencing this spec folder

## Machine-readable features

`features.json` is authored when PR2 lands and the criteria set is complete. PR1's six criteria each map 1:1 onto a named test in `tests/test_service.py`, verifiable with a single command.

**Pass-state gating:** the agent CANNOT write `"state": "passing"` — only the harness, after running `verification` and capturing exit code 0, may set that terminal state.
