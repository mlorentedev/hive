---
id: "HIVE-328-runtime-launcher"
type: spec
status: implementing # draft | implementing | verifying | archived
created: "2026-08-07"
issue: "hive#328"   # repo#NNN — GitHub issue / Project item that tracks this spec
tags: [spec, proposal]
template_version: "1.0"
---

# HIVE-328: Runtime launcher + honest exec resolution

<!-- from issue #328: self-upgrade builds the A3 layout but installs no PATH launcher; _resolve_exec() trusts a dead trampoline -->

## Why

A3 (HIVE-267 / ADR-015) made hive own its install layout: `self_upgrade` builds `versions/<v>` and flips a `current` junction. But `specs/HIVE-267-upgrade-swap/tasks.md:28` promised "hive owns the layout … **+ own launcher**", and the launcher was never implemented — so after a successful upgrade `current` points at a working venv that no shell can find. Independently, `_service._resolve_exec()` did a bare `shutil.which("hive")`, which cannot tell a healthy console script from an orphaned uv trampoline: on the maintainer's Windows box `which` returns `~/.local/bin/hive.exe`, and running it yields `error: uv trampoline failed to canonicalize script path`. `hive service install` would therefore have registered a supervised daemon against a binary that can never start. Together these are why dotfiles#791 (AI-028) PR2 cannot proceed.

## What

1. `_resolve_exec()` resolves in an order that reflects ownership: the A3 `current` layout first (it follows upgrades, so the registered command survives a version swap unrewritten), then a `hive` on PATH **but only once verified to actually start**, then `<python> -m hive.server`. A present-but-broken binary falls through instead of being selected.
2. `self_upgrade` (and a first install) installs a launcher on PATH that resolves **through `current`**, so an upgrade needs no launcher rewrite.

Part 1 ships in PR1. **Part 2 is blocked on an unresolved design decision** — see below.

## Out of scope

- The dotfiles-side migration of the three install sites — dotfiles#791 (AI-028). This spec is its upstream dependency, not its implementation.
- Repairing an already-orphaned trampoline. Detection/repair is dotfiles#574 (`dotf doctor --fix`); this spec stops hive from *trusting* one.
- macOS / launchd.

## Risks / open questions

**BLOCKING for PR2 — which directory owns the launcher?** `~/.local/bin` is already on PATH on the affected machine (no admin, no shell restart — the constraints A3 was chosen under), but uv put the trampoline there, so hive writing into it means hive and uv both claim the directory. Worse on Windows: `PATHEXT` resolves `.COM;.EXE;.BAT;.CMD`, so a `hive.cmd` hive writes **loses to a leftover `hive.exe` sitting beside it** — meaning the launcher work is inseparable from disposing of another tool's artifact.

Two candidate designs, neither obviously right:

- **(A) Joint ownership of `~/.local/bin`.** hive writes its shim there and must detect-and-remove a stale trampoline. Already on PATH, so it works with no admin and no restart. Cost: hive races `uv tool install hive-vault` forever, and deleting a file uv created is a different class of act from installing hive's own.
- **(B) hive owns `%LOCALAPPDATA%\hive\bin`.** No collision with uv. Cost: a PATH mutation step (User-scope env var on Windows), which does not affect already-running shells — so a fresh install is not usable until the user opens a new shell.

Decide by asking which one `hive service install` can make work **without admin and without a shell restart**, since that is the constraint that selected A3 in the first place. A defensible middle path: write to `~/.local/bin` but only *replace* a `hive*` entry that fails the `_executes` probe, never a healthy one — repair rather than seizure.

Non-blocking, decided:

- **`sys.executable` fallback under A3.** Once hive runs *from* `versions/<v>`, `sys.executable` is that pinned venv's python, so `-m hive.server` resolves the pinned version rather than following `current`. Accepted: the fallback is a last resort reached only when there is no layout and nothing runnable on PATH, and in that situation "the interpreter that is running" is the only thing guaranteed to work. Correctness of the *supervisor-follows-the-junction* property is carried by branch 1, which is preferred whenever a layout exists.
- **Probe cost.** `_executes` spawns `<command> --version` with a bounded 10 s timeout, on the install path only — not per request.

## Acceptance criteria

- [x] **AC1** — `_resolve_exec()` prefers the A3 layout over an arbitrary PATH hit, and returns the path **through `current`**, not the concrete `versions/<v>` dir.
- [x] **AC2** — A PATH hit that cannot start is not selected; resolution falls through to the module invocation.
- [x] **AC3** — A healthy PATH hit is still selected when no layout exists (no regression for uv-tool installs).
- [x] **AC4** — With neither a layout nor a PATH hit, `<python> -m hive.server` is returned.
- [x] **AC5** — The probe answers `False` on any subprocess failure and never propagates (AGENTS.md broad-`Exception` rule; Windows raises `OSError` variants here, not `CalledProcessError`).
- [x] **AC6** — A `current` junction pointing at a half-built version (no launcher inside) does not win.
- [ ] **AC7** *(PR2, blocked)* — After `hive self-upgrade` on a machine with no prior install, `hive --version` works from a fresh shell.
- [ ] **AC8** *(PR2, blocked)* — An upgrade repoints `current` and requires no launcher rewrite; launcher installation is idempotent.

## References

- Bitácora board: [hive#328](https://github.com/mlorentedev/hive/issues/328)
- Predecessor: `specs/HIVE-267-upgrade-swap/` — `tasks.md:28` is where the launcher was promised
- ADR: `docs/adr/adr-015-windows-daemon-supervision-upgrade.md` (`proposed`; its AC-3 is what PR2 closes)
- Downstream consumer: [dotfiles#791](https://github.com/mlorentedev/dotfiles/issues/791) (AI-028) — PR2 there is gated on PR2 here
- Adjacent: [dotfiles#574](https://github.com/mlorentedev/dotfiles/issues/574) (`dotf doctor --fix` repairs an orphaned trampoline), [hive#176](https://github.com/mlorentedev/hive/issues/176)
