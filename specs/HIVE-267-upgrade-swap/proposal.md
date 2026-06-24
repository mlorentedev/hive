---
id: "HIVE-267-upgrade-swap"
type: spec
status: draft # draft | implementing | verifying | archived
created: "2026-06-24"
issue: "hive#267"   # repo#NNN — GitHub issue / Project item that tracks this spec
tags: [spec, proposal]
template_version: "1.0"
---

# HIVE-267: Upgrade swap

> **Naming**: file lives at `<repo>/specs/HIVE-267-upgrade-swap/proposal.md`.

> `[AGENT-DRAFT — review before archive]` — this proposal was drafted from the
> rich content of issue #267. The blocking mechanism decision is **RESOLVED**
> (A3-first, see Risks); the remaining sections are drafted from the issue —
> review them before archive.

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

`[AGENT-DRAFT]` A swap mechanism in hive that lets the install be replaced
**without ever touching in-use files**, so an upgrade applied while the daemon is
running leaves a valid install instead of a locked, malformed one. After this
change, an upgrade-while-running ends with the `hive` entrypoint present and
`hive --version` reporting the new version; a failed swap leaves the previous
working install intact rather than a half-removed directory.

## Out of scope

`[AGENT-DRAFT]` — confirm the boundary, especially the cross-repo split.

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
- `[AGENT-DRAFT]` **The supervisor holds the lock.** The swap must succeed while
  the Startup supervisor keeps `python.exe` running, or coordinate a bounded
  stop/restart (exit-75 contract) without a window where the MCP is dead.
- `[AGENT-DRAFT]` **Windows-only, hardware-gated.** Like the other ADR-015
  mechanisms, this needs validation on a real non-admin Windows box, not just CI.

## Acceptance criteria

`[AGENT-DRAFT]` Observable outcomes. Each must be testable.

- [ ] An upgrade applied **while the daemon is running** leaves a valid install:
      the `hive` entrypoint is present and `hive --version` reports the new
      version — no malformed/locked state.
- [ ] The documented #267 reproduction (`uv tool install --upgrade` against a
      running daemon) no longer fails with "Access is denied / failed to remove
      directory" on in-use files.
- [ ] A swap that cannot complete leaves the **previous** working install intact
      and surfaces an actionable error — never a silently corrupted, dead MCP.
- [ ] Validated on a real non-admin Windows box (ADR-015 hardware-validation
      discipline), not only the CI matrix.

## References

- Bitácora board: hive#267 (the gating issue — see `issue:` frontmatter)
- Related ADR: `docs/adr/adr-015-windows-daemon-supervision-upgrade.md` (mechanism A)
- Related spec: `specs/HIVE-176-windows-daemon-supervision/` (the rollout epic)
- Related patterns: `00_meta/patterns/pattern-agent-oriented-errors.md` (WHY/FIX on failure)
