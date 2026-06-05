# HIVE-176 — Tasks (TDD order)

> Why: [ADR-015](../../docs/adr/adr-015-windows-daemon-supervision-upgrade.md). Proposal: [proposal.md](proposal.md).

## PR1 — S4U principal + supervisor-loop (AC-1, AC-2) — this branch

- [ ] **T1 (red)** Update `test_render_windows_task_xml_*`: assert the action is a PowerShell supervisor loop relaunching `hive serve` on non-zero exit (not a bare `<Arguments>serve</Arguments>`), and assert an `<LogonType>S4U</LogonType>` principal. Add a test that a clean exit 0 path is expressed (`if($LASTEXITCODE -eq 0){break}`).
- [ ] **T2 (red)** Update `test_install_windows_registers_scheduled_task` to assert the new XML (S4U + supervisor loop).
- [ ] **T3 (green)** `_service.render_windows_task_xml`: add S4U `<Principal>` (+ `<Actions Context="Author">`), change the action to the inline PowerShell supervisor loop. Add `user` param; `_install_windows` resolves the current Windows user.
- [ ] **T4 (green)** Keep `<RestartOnFailure>` as a secondary net for engine-launch failure; keep LogonTrigger / IgnoreNew / `PT0S`.
- [ ] **T5 (refactor)** Keep `render_windows_task_xml` < 40 lines; mypy --strict + ruff clean.
- [ ] **T6 (verify)** `uv run pytest tests/test_service.py` green; `make check` green.

## PR2 — A upgrade-swap (AC-3) — after spike

- [ ] **S1 (spike)** Validate A3 (versioned-dir + junction swap) feasibility with `uv` on Windows; characterise which files lock (entrypoint `.exe`, loaded `.pyd`). Fall back A4 (rename-replace) then A2 (tolerate-in-place).
- [ ] **S2** File upstream `uv` issue: replace-while-running on Windows.
- [ ] **T7+** Implement the chosen mechanism + tests. Then flip ADR-015 → accepted.

## PR3 — dotfiles wiring

- [ ] **T8** `setup-windows.ps1` daemon-supervision block → new mechanism (S4U task install + new upgrade flow).
- [ ] **T9** `tests/hive-upgrade-timer.bats` expectations updated.

## Verification

- [ ] `verification.md` filled with test output + spike evidence + the 2026-06-04 hardware-validation log lines.
