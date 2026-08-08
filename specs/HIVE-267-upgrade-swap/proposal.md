---
id: "HIVE-267-upgrade-swap"
type: spec
status: verifying # draft | implementing | verifying | archived
created: "2026-06-24"
issue: "hive#267"   # repo#NNN — GitHub issue / Project item that tracks this spec
tags: [spec, proposal]
template_version: "1.0"
---

# HIVE-267: Upgrade swap

> **Naming**: file lives at `<repo>/specs/HIVE-267-upgrade-swap/proposal.md`.

> **Reviewed 2026-08-07 (#333); all draft markers resolved.** The A3 mechanism
> shipped in #290, #294 and #302 (`src/hive/_runtime.py`, 25 tests in
> `tests/test_runtime.py`) and is depended on in production by
> `_service._current_layout_exec()` and [ADR-019](../../docs/adr/adr-019-launcher-ownership.md).
>
> **Status is `verifying`, not `archived`, for two concrete reasons** — see
> Acceptance criteria:
>
> 1. **AC1/AC2 cannot be verified end-to-end yet.** They require `hive --version`
>    to report the new version from a shell, and no launcher is on `PATH` — this
>    spec's `tasks.md` promised "+ own launcher" and it was never built. That gap
>    is [#328](https://github.com/mlorentedev/hive/issues/328); its PR1 shipped
>    (#330) and ADR-019 settled the ownership question, but PR2 (the launcher
>    itself) has not landed.
> 2. **AC4 (real non-admin Windows re-validation) has not been performed.** The
>    A3 *feasibility spike* passed on real hardware 2026-06-24, but the shipped
>    implementation was never re-validated there, and that box is currently in the
>    orphaned-trampoline failure state (dotfiles#791).

## Why

<!-- from issue #267: Windows: uv tool upgrade corrupts in-use hive-vault install (ADR-015 mechanism A [MUST RESOLVE]) - recurred in the field -->

ADR-015 flags mechanism (A) "upgrade-swap" as the load-bearing `[MUST RESOLVE]`,
and it just bit on a real non-admin Windows box: a routine
`uv tool install --upgrade hive-vault` (the dotfiles auto-upgrade timer / setup
`prerequisite_command`) **silently corrupted the install** and took the hive MCP
down for an entire Claude Code session. The Startup supervisor keeps the venv
`python.exe` running, so it holds `…\uv\tools\hive-vault\Scripts` locked; uv
cannot replace the in-use directory (`Access is denied (os error 5)`), leaving a
malformed tool with no `hive` entrypoint until a manual kill-uninstall-reinstall.
This is field proof that the theoretical risk is now a recurring outage of the
primary vault-access surface.

## What

A swap mechanism in hive that lets the install be replaced **without ever
touching in-use files**, so an upgrade applied while the daemon is running leaves
a valid install instead of a locked, malformed one. After this change, an
upgrade-while-running ends with the `hive` entrypoint present and `hive --version`
reporting the new version; a failed swap leaves the previous working install
intact rather than a half-removed directory.

**Shipped** as `src/hive/_runtime.py` — `runtime_root()`, `versions_dir()`,
`version_path()`, `current_link()`, `_make_junction()`, `repoint()`,
`build_version()`, `current_version()`, `remove_version()`, `latest_version()`,
`_gc_other_versions()`, `self_upgrade()` — plus the `hive self-upgrade [version]`
subcommand (`server._run_self_upgrade`). PRs #290, #294, #302.

**One clause of the "After this change" sentence is still unmet:** `hive
--version` cannot be invoked from a shell, because nothing installs a launcher on
`PATH`. The layout is correct and `current` resolves to a working venv; it is
simply unreachable by name. Tracked as [#328](https://github.com/mlorentedev/hive/issues/328).

## Out of scope

**Boundary confirmed 2026-08-07.** The cross-repo split held up in practice: the
hive side shipped independently, and the dotfiles side is tracked separately as
AI-028 (dotfiles#791). One item has since been carved out into its own hive spec
rather than staying here:

- **The PATH launcher** — promised by this spec's `tasks.md` ("hive owns the layout
  … **+ own launcher**") but never built. Now owned by
  `specs/HIVE-328-runtime-launcher/` and decided by
  [ADR-019](../../docs/adr/adr-019-launcher-ownership.md). Keeping it here would
  have left two specs claiming the same deliverable.

- The **dotfiles rollout** that triggers the upgrade (`setup-windows.ps1`
  supervision/upgrade block, `tests/hive-upgrade-timer.bats`, `mcp-servers.json`
  `prerequisite_command`) — companion work in the dotfiles repo (ADR-015
  Consequences: ownership is hive **and** dotfiles). This spec owns only the
  hive-side swap mechanism (`src/hive/_service.py` / `_daemon.py`).
- **A1 (stop-before-upgrade) as the sole mechanism** — rejected by ADR-015: it
  breaks when a `hive client` session holds the install. May survive only as a
  secondary coordination step.
- The **interim mitigation** (gate the dotfiles auto-upgrade against a running
  daemon, or detect+self-repair the malformed state) — a separate, faster
  stop-gap, not the root fix this spec delivers.

## Risks / open questions

> Mechanism decision **RESOLVED** (2026-06-24). Remaining items are spike-time
> questions, not blockers on freezing `tasks.md`.

- **RESOLVED — mechanism = A3-first, spike-gated (per ADR-015 §(A), maintainer's
  call 2026-06-24).** Spike A3 on a real non-admin Windows box; if it proves too
  invasive, fall back to A4 then A2 (documented below). A3 is the bullet-proof,
  C7-safe target; its accepted cost is that **hive owns a Windows-specific
  install layout fronted by a junction, decoupled from `uv tool upgrade`** — so
  the dotfiles upgrade path must stop calling bare `uv tool install --upgrade`
  (companion dotfiles work).
  - **A3 (chosen target):** versioned install dir + a `current`
    junction; write the new version beside the old, atomically repoint the
    junction (`mklink /J`, no admin), GC the old dir. Bullet-proof against
    native-dep upgrades, C7-safe. **Cost:** hive owns a Windows-specific
    install/upgrade layout, decoupled from `uv tool upgrade` — a real
    maintenance surface.
  - **A4:** rename-then-replace each locked target via `MoveFileEx`. C7-safe,
    less invasive than A3, but must enumerate exactly which files uv touches —
    **fragile coupling to uv internals.**
  - **A2 (cheapest):** tolerate-in-place — uv already updates pure-python
    site-packages while running; treat the entrypoint-copy failure as non-fatal
    (the launcher is a version-agnostic trampoline) and refresh it in the brief
    unlocked window after exit 75. **Covers the observed #267 failure** (locked
    `Scripts`/entrypoint) but **NOT** a release that bumps a loaded native module
    (`.pyd`: pydantic-core, cryptography) → mixed-version venv. hive ships those
    deps, so this risk is real, not theoretical.
- **RESOLVED — interposition = hive owns the versioned layout (2026-06-24).**
  `uv tool` has no per-version dir (it always rewrites the same dir), so hive
  manages its own root: `%LOCALAPPDATA%\hive\runtime\versions\<v>\` (each a full
  venv built fresh by `uv venv` + `uv pip install hive-vault==<v>` — uv/pip still
  does the heavy lifting), a `current` junction → the active version, and a
  launcher that resolves through `current`. The dotfiles upgrade trigger calls a
  new `hive self-upgrade` instead of bare `uv tool install --upgrade`.
  **Consequence (accepted):** on Windows hive is no longer a plain `uv tool` —
  `uv tool list` shows a stale/unmanaged entry; documented as expected. (An
  upstream `uv` issue — replace-while-running on Windows — is filed regardless,
  per ADR-015.)
- **RESOLVED — the supervisor holding the lock is a non-issue for A3 (spike,
  2026-06-24; design confirmed at implementation).** This was the risk A3 was
  chosen to dissolve rather than manage: junction operations touch only the reparse
  point, never the locked target files, so the repoint succeeds *while* the
  supervisor keeps `python.exe` running. The spike proved it directly — `current`
  was repointed from `versions/1.41.5` to `1.41.6` while an exclusive
  `FileShare.None` lock was held on `1.41.5/core.pyd`, with no "Access is denied".
  No bounded stop/restart is needed, so there is no window where the MCP is dead.
  `self_upgrade` deliberately does **not** restart the daemon; the supervisor's
  pre-existing exit-75 restart-on-upgrade contract relaunches `hive serve` through
  the freshly repointed `current`. GC of a still-locked old version returns `False`
  and defers to the next run rather than failing the upgrade.
- **RESOLVED as scoping, NOT as validation — Windows-only is confirmed; the
  hardware re-validation is still owed.** Windows-only is settled and no longer
  open: A3 exists solely to work around Windows' in-use-file lock, and POSIX has no
  such constraint, so **Linux keeps `uv tool`** (dotfiles AI-028 AC6). ADR-019
  reaches the same conclusion for the launcher.
  **But the hardware gate itself is unmet.** What passed on real hardware was the
  A3 *feasibility spike*, before any code existed; the shipped `_runtime.py` has
  only ever run on CI (Linux/Windows runners) and this Linux dev box. See AC4 — it
  is one of the two reasons this spec is `verifying` rather than `archived`, and it
  is blocked in practice because the target machine is in the orphaned-trampoline
  state (dotfiles#791).

## Acceptance criteria

Observable outcomes. Each must be testable. Status reviewed 2026-08-07 — marked
against evidence, not against intent.

- [~] **AC1 — an upgrade applied while the daemon is running leaves a valid
      install:** the `hive` entrypoint is present and `hive --version` reports the
      new version, with no malformed/locked state. **Partially met.** The layout
      half holds: `build_version()` writes beside the in-use dir and `repoint()`
      flips the junction, so the entrypoint exists at
      `current/Scripts/hive.exe` — asserted by `tests/test_runtime.py`. The
      `hive --version` half **cannot be verified**, because no launcher is on
      `PATH` ([#328](https://github.com/mlorentedev/hive/issues/328)).
- [~] **AC2 — the documented #267 reproduction no longer fails on in-use files.**
      **Met in mechanism, unverified in the field.** The spike reproduced the exact
      failure mode and showed the repoint succeeding under an exclusive lock
      (`verification.md`). Not re-run against the shipped code on real hardware —
      same gap as AC4.
- [x] **AC3 — a failed swap leaves the previous working install intact and
      surfaces an actionable error.** **Met.** Stage-then-flip: the fallible
      `_make_junction` runs before `current` is disturbed, and failure raises a
      WHY/FIX `RuntimeError`. `build_version()` cleans a half-built dir on failure;
      `remove_version()` refuses the active version. Test:
      `test_failed_repoint_leaves_the_previous_current_intact`.
- [ ] **AC4 — validated on a real non-admin Windows box** (ADR-015
      hardware-validation discipline), not only the CI matrix. **NOT met.** The
      2026-06-24 real-hardware pass was the *feasibility spike*, before any code
      existed. The shipped implementation has run only on CI and a Linux dev box.
      Blocked in practice: the target machine is in the orphaned-trampoline state
      (dotfiles#791).

## References

- Bitácora board: hive#267 (the gating issue — see `issue:` frontmatter)
- Related ADR: `docs/adr/adr-015-windows-daemon-supervision-upgrade.md` (mechanism A)
- Related spec: `specs/HIVE-176-windows-daemon-supervision/` (the rollout epic)
- Related patterns: `00_meta/patterns/pattern-agent-oriented-errors.md` (WHY/FIX on failure)
