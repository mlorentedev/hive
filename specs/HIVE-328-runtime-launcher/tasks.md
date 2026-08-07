---
tags: [spec, tasks, templates]
created: "2026-08-07"
---

# Tasks - HIVE-328-runtime-launcher

> TDD order. One task = one focused commit. Tick as you go.
>
> **Split into two PRs.** PR1 (exec resolution) has no design ambiguity and ships first — it is the half that stops `hive service install` registering a daemon against a dead binary. PR2 (the launcher) is **blocked on the directory-ownership decision** in `proposal.md` and must not start until that is settled.

## Setup

- [x] Branch created from master: `fix/resolve-exec-verifies-the-binary`
- [x] Gating issue open: hive#328
- [x] `proposal.md` is complete and acceptance criteria are testable
- [ ] No open questions left in `proposal.md` "Risks / open questions" — **one BLOCKING question remains** (launcher directory), scoped to PR2

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

### PR2 — the PATH launcher (AC7, AC8) — BLOCKED

- [ ] **GATE: decide the launcher directory** (`proposal.md` -> Risks). `~/.local/bin` (already on PATH, no admin, no restart, but shared with uv and losing to a leftover `hive.exe` under PATHEXT) vs `%LOCALAPPDATA%\hive\bin` (hive-owned, but needs a PATH mutation that does not reach running shells). Middle path to evaluate: write to `~/.local/bin` but only replace a `hive*` entry that FAILS the `_executes` probe — repair, never seizure.
- [ ] [AC7] Install a launcher resolving through `current` as part of `self_upgrade` and first install.
- [ ] [AC8] Idempotent re-install; an upgrade repoints the junction and rewrites nothing.
- [ ] [AC8] Never clobber a healthy non-hive binary of the same name.
- [ ] Then flip ADR-015 -> `accepted` (its AC-3) and unblock dotfiles#791 PR2.

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
