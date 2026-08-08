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
2. `self_upgrade` (and a first install) installs a launcher into **`%LOCALAPPDATA%\hive\bin`** that resolves **through `current`**, so an upgrade needs no launcher rewrite, and prepends that directory to the User `PATH`. Hive never writes to `~/.local/bin`; it only probes it and warns.

Part 1 shipped in PR1 ([#330](https://github.com/mlorentedev/hive/pull/330)). Part 2's blocking design decision is **resolved** by [ADR-019](../../docs/adr/adr-019-launcher-ownership.md), accepted 2026-08-07 — see Risks below; PR2 has no gate left.

## Out of scope

- The dotfiles-side migration of the three install sites — dotfiles#791 (AI-028). This spec is its upstream dependency, not its implementation.
- Repairing an already-orphaned trampoline. Detection/repair is dotfiles#574 (`dotf doctor --fix`); this spec stops hive from *trusting* one.
- macOS / launchd.

## Risks / open questions

**RESOLVED 2026-08-07 — [ADR-019](../../docs/adr/adr-019-launcher-ownership.md), accepted the same day.** Hive owns `%LOCALAPPDATA%\hive\bin` (option B), prepends it to the User PATH, and never writes to or deletes from `~/.local/bin`.

The question was which directory owns the launcher. `~/.local/bin` is already on PATH on the affected machine, but uv put the trampoline there; and on Windows `PATHEXT` resolves `.COM;.EXE;.BAT;.CMD`, so a `hive.cmd` hive writes **loses to a leftover `hive.exe` sitting beside it**. Three candidates were weighed — (A) joint ownership of `~/.local/bin`, (B) a hive-owned directory, (C) writing to `~/.local/bin` but only replacing a `hive*` that fails the `_executes` probe.

**The criterion this section originally proposed was aimed at the wrong consumer, and correcting it is what settled the question.** It read: *"decide by asking which one `hive service install` can make work without admin and without a shell restart"*. But `hive service install` never consults PATH — `_resolve_exec()` prefers `_current_layout_exec()` and hands the supervisor an absolute path, and the MCP registration in `~/.claude.json` is absolute too (that is how it got stuck on the dead trampoline). No programmatic consumer needs PATH; only a human typing `hive` does. That reduces B's cost to a one-time "open a new terminal" and reduces the comparison to ownership, which B wins.

(A) is eliminated outright: under PATHEXT it installs a launcher and changes nothing observable on the very machine that motivated the work. (C) is ruled out by this spec's own Out-of-scope boundary, which assigns orphan repair to dotfiles#574 — and it would repair once while any later `uv tool install` recreates the trampoline. Its one merit, surfacing the broken state, is preserved as **detect-and-warn**: hive probes other `hive*` entries with `_executes()` and points at `dotf doctor --fix` without deleting anything.

Carried forward as a known gap: this spec alone does not make `hive` resolve while the orphan exists. That outcome needs the pair — ADR-019 plus dotfiles#574 — with each repo doing its declared job.

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
- [ ] **AC7** *(PR2)* — After `hive self-upgrade` on a machine with no prior install, `hive --version` works from a **fresh** shell (an already-open shell is out of contract — see ADR-019 Consequences).
- [ ] **AC8** *(PR2)* — An upgrade repoints `current` and requires no launcher rewrite; launcher installation is idempotent, and repeated upgrades add no duplicate `PATH` entries.
- [ ] **AC9** *(PR2)* — Hive never writes to or deletes from `~/.local/bin`. Asserted explicitly, because the rejected option C did exactly that and the boundary is the decision.
- [ ] **AC10** *(PR2)* — With a `hive*` on `PATH` that fails `_executes()`, install reports it and names `dotf doctor --fix`, without modifying it.
- [ ] **AC11** *(PR2)* — `%LOCALAPPDATA%\hive\bin` is **prepended** to the User `PATH`, not appended, so a stale `~/.local/bin` entry does not win on ordering.

## References

- Bitácora board: [hive#328](https://github.com/mlorentedev/hive/issues/328)
- Predecessor: `specs/HIVE-267-upgrade-swap/` — `tasks.md:28` is where the launcher was promised
- ADR: `docs/adr/adr-015-windows-daemon-supervision-upgrade.md` (`proposed`; its AC-3 is what PR2 closes)
- Downstream consumer: [dotfiles#791](https://github.com/mlorentedev/dotfiles/issues/791) (AI-028) — PR2 there is gated on PR2 here
- Adjacent: [dotfiles#574](https://github.com/mlorentedev/dotfiles/issues/574) (`dotf doctor --fix` repairs an orphaned trampoline), [hive#176](https://github.com/mlorentedev/hive/issues/176)
