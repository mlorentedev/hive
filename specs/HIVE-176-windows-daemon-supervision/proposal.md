---
id: "HIVE-176-windows-daemon-supervision"
type: spec
status: implementing # draft | implementing | verifying | archived
created: "2026-06-05"
issue: "hive#176"   # repo#NNN — GitHub issue / Project item that tracks this spec
tags: [spec, proposal]
template_version: "1.0"
---

# HIVE-176 — Windows daemon supervision & auto-upgrade

> **Why (upstream):** [ADR-015](../../docs/adr/adr-015-windows-daemon-supervision-upgrade.md) — amends ADR-011 §4. The first real Windows rollout validation (#176) proved the daemon's restart-on-upgrade is broken on Windows in three ways (A upgrade-swap, B restart trigger, C console window). This spec implements the per-OS divergent Windows mechanism.

## Problem (validated 2026-06-04 on real Windows hardware)

- **A** — `uv tool upgrade` cannot replace the in-use `hive.exe` / loaded `.pyd` on Windows (`os error 32`). Structural: hive is always running under the daemon model.
- **B** — Task Scheduler `<RestartOnFailure>` does not restart on the daemon's exit 75 (confirmed: 6 min later task `Ready`, no process). Not a 1:1 map of systemd `Restart=on-failure`.
- **C** — the daemon shows a console window at every start/logon (interactive-token task action of a console app).

## What

A per-OS Windows mechanism (no admin), keeping ADR-011's shared daemon contract:

- **B (restart)** — the task action becomes an inline supervisor loop (PowerShell) that relaunches `hive serve` while it exits non-zero, breaks on exit 0. Replaces the non-functional `<RestartOnFailure>` as the restart mechanism.
- **C (windowless)** — register under an `S4U` `<Principal>` → session 0, no console window, non-elevated, no stored password.
- **A (upgrade-swap)** — spike-gated (A3 versioned-dir + junction swap preferred; A4 rename-replace fallback). Decoupled from `uv tool upgrade`, which cannot replace in-use files.

## Acceptance criteria

- **AC-1 (C)** `render_windows_task_xml` emits an S4U `<Principal>` (`<LogonType>S4U</LogonType>`); daemon runs windowless. *(spike-validated)*
- **AC-2 (B)** The task action is a supervisor loop that relaunches `hive serve` on non-zero exit and stops on exit 0. *(spike-validated)*
- **AC-3 (A)** `uv tool upgrade` while the daemon runs no longer leaves a broken/half-upgraded install; the daemon restarts onto the new version. *(needs spike — blocks ADR-015 acceptance)*
- **AC-4** Linux/macOS rendering + behaviour unchanged (no regression in `render_systemd_unit` / install dispatch).
- **AC-5** `make check` green (ruff + mypy --strict + pytest) on the cross-OS CI matrix.

## Decomposition (atomic PRs)

- **PR1 (hive)** — AC-1 + AC-2: S4U principal + supervisor-loop in `render_windows_task_xml` + tests. *(this branch)*
- **PR2 (hive)** — AC-3: A upgrade-swap (after spike) + upstream `uv` issue.
- **PR3 (dotfiles)** — wire `setup-windows.ps1` + `tests/hive-upgrade-timer.bats` to the new mechanism.

## Non-goals

- Changing the Linux/macOS mechanism. Changing the MCP tool surface. Activating the daemon where ROI is unproven (the stdio fallback stays the safety net throughout).
