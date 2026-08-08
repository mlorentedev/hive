---
id: "ADR-019-launcher-ownership"
type: adr
status: proposed
owner: manu
date: "2026-08-07"
issue: "hive#328"   # repo#NNN — GitHub issue / Project item that triggered this decision
tags: [architecture, decision, packaging, windows, path]
created: "2026-08-07"
---

# ADR-019: Launcher Ownership on Windows

## Status

Proposed (2026-08-07). Unblocks PR2 of `specs/HIVE-328-runtime-launcher/`, which is in turn
the upstream dependency of dotfiles AI-028 PR2. Scoped to **Windows only** — see "Why this is
Windows-only" below.

## Date

2026-08-07

## Context

[ADR-015](adr-015-windows-daemon-supervision-upgrade.md) and HIVE-267 gave hive its own
install layout ("A3"): `self_upgrade` builds `versions/<v>` and flips a `current` junction,
so an upgrade repoints one link instead of rewriting every consumer. `HIVE-267/tasks.md:28`
promised "hive owns the layout … **+ own launcher**". The layout shipped; the launcher did not.

The consequence is narrow and specific. `versions/<v>` contains a real venv, and that venv
**already ships a working `hive` console script** — `_service._current_layout_exec()` resolves
exactly `current/Scripts/hive.exe` on Windows and `current/bin/hive` on POSIX. Nothing needs to
be *built*. The only missing piece is putting something on `PATH` that resolves through
`current`, so that a human typing `hive` reaches the version hive itself manages.

Meanwhile the maintainer's Windows box demonstrated the failure this creates. The `hive-vault`
uv tool was removed, leaving `~/.local/bin/hive.exe` as an orphaned uv trampoline: present on
PATH, but dead (`uv trampoline failed to canonicalize script path`). PR1 of HIVE-328
([#330](https://github.com/mlorentedev/hive/pull/330)) stopped hive from *trusting* such a
binary by adding `_service._executes()`, a bounded `--version` probe. It did not decide who
owns the launcher.

Three candidate directories were on the table:

- **(A)** `~/.local/bin`, shared with uv, writing hive's shim beside whatever is there.
- **(B)** `%LOCALAPPDATA%\hive\bin`, owned solely by hive, added to the User `PATH`.
- **(C)** `~/.local/bin`, but only ever *replacing* a `hive*` entry that fails `_executes` —
  "repair, never seizure".

### The decision criterion was aimed at the wrong consumer

`specs/HIVE-328-runtime-launcher/proposal.md` framed the choice as:

> Decide by asking which one `hive service install` can make work **without admin and without
> a shell restart**, since that is the constraint that selected A3 in the first place.

That criterion does not discriminate, because **`hive service install` never consults `PATH`**.
`_service._resolve_exec()` prefers `_current_layout_exec()` and returns an absolute path; the
supervisor is handed a concrete command, not a name to resolve. The MCP registration in
`~/.claude.json` is likewise an absolute path — that is precisely how it got stuck pointing at
the dead trampoline.

So no consumer that must keep working needs `PATH` at all. `PATH` serves exactly one purpose
here: a human typing `hive` in a terminal. Once that is recognised, "a PATH mutation does not
reach already-running shells" stops being a disqualifying cost and becomes a one-time
convenience delay — open a new terminal, once, on first install.

With the criterion dissolved, the comparison reduces to ownership. That is a question with a
clear answer.

## Decision

**Hive owns `%LOCALAPPDATA%\hive\bin` and installs its launcher there, prepending that
directory to the User `PATH`.** Hive never writes to, and never deletes from, `~/.local/bin`.

Specifics:

1. `self_upgrade` (and a first install) writes a `hive.cmd` shim into `%LOCALAPPDATA%\hive\bin`
   that dispatches through the `current` junction. Because it resolves through `current`, an
   upgrade repoints the junction and the shim needs no rewrite — the property A3 exists for.
2. The directory is **prepended** to the User `PATH`, not appended. Appending would leave a
   stale `~/.local/bin\hive.exe` winning by position; prepending means hive's entry is found
   first as soon as a new shell starts.
3. Installation is idempotent: an existing correct shim and an existing `PATH` entry are both
   no-ops, so repeated upgrades do not accumulate `PATH` fragments.
4. **Detect and warn; never delete.** At install time hive probes any other `hive*` it finds on
   `PATH` with `_executes()` and, on failure, reports it: *"orphaned launcher detected at
   `<path>` — run `dotf doctor --fix`"*. Detection is already within this spec's remit (it
   probes today); removal is not.

### Why this is Windows-only

On POSIX, `~/.local/bin` is already on `PATH` and there is no in-use-file lock, so `uv tool`
remains the install model (AI-028 AC6). A3 exists to work around a Windows constraint, and so
does its launcher. This ADR does not change anything on Linux or macOS.

## Rejected alternatives

### (A) Joint ownership of `~/.local/bin`, unconditional write

Rejected because **it does not work on Windows.** `PATHEXT` resolves `.COM;.EXE;.BAT;.CMD` in
order, so a `hive.cmd` that hive writes loses to a leftover `hive.exe` sitting beside it. On
the exact machine that motivated this work, option A would install a launcher and change
nothing observable. It also leaves hive racing `uv tool install hive-vault` indefinitely.

### (C) Conditional repair of `~/.local/bin`

Rejected on three grounds, the first of which is decisive.

1. **The spec already assigns this elsewhere.** `HIVE-328/proposal.md` Out of scope, verbatim:
   *"Repairing an already-orphaned trampoline. Detection/repair is dotfiles#574
   (`dotf doctor --fix`); this spec stops hive from* trusting *one."* Option C is hive deleting
   the orphan — the precise act the spec placed in another repo. Adopting it would mean
   amending that boundary, and no evidence has emerged that the boundary was wrong.
2. **It repairs once; it does not prevent recurrence.** Any later `uv tool install` or
   `uv tool upgrade hive-vault` recreates the trampoline beside hive's shim and re-shadows it.
   The orphaned-trampoline problem has already recurred once on this machine after being
   documented as a manual recipe — automating the recipe is still not a fix. Removing the
   shared directory is.
3. **It has hive deleting another tool's artifact.** Even a dead one. That is a different class
   of act from installing hive's own, and it is avoidable.

Option C's one genuine merit — the user discovers the broken state — is preserved by the
detect-and-warn behaviour in Decision 4, which stays inside the spec's boundary.

### (B') A `PATH` mutation that reaches running shells

Not available without a shell-integration hook in every shell hive might be launched from,
which is a larger surface than the problem justifies.

## Consequences

### Positive

- Clean ownership: hive and uv never contend for a directory, so the PATHEXT shadowing hazard
  cannot arise at all rather than being repaired after the fact.
- Immune to recurrence. A future `uv tool install` cannot shadow a launcher in a directory uv
  does not write to.
- Uninstall is a single directory removal plus one `PATH` entry.
- Composes with A3 unchanged: the shim resolves through `current`, so upgrades stay
  launcher-free.
- Hive never deletes a file another tool created, which keeps the install path defensible.

### Negative

- **A fresh install is not usable from an already-open shell.** The User `PATH` change reaches
  new processes only. This is the accepted cost, and it is bounded: no supervised or
  programmatic consumer depends on `PATH` (see Context).
- **This ADR alone does not make `hive` resolve while the orphan exists.** A dead
  `~/.local/bin\hive.exe` remains on `PATH`; prepending hive's directory wins on ordering, but
  a user who has not re-run setup still has the stale entry present. "Human types `hive` and it
  works" is delivered by the **pair**: this ADR plus dotfiles#574 removing the orphan. Each
  repo does its declared job; neither is sufficient alone.
- A `PATH` mutation is a persistent change to the user environment, which is a heavier act than
  writing a file. It is idempotent, but it is not invisible.
- Two install models now coexist — `uv tool` on POSIX, A3 + launcher on Windows. Divergence has
  a maintenance cost, and is justified only by the Windows file-lock constraint that created A3.

### Neutral

- `_resolve_exec()` is unchanged by this decision. Its ordering already prefers the layout, and
  the launcher does not enter supervisor resolution.
- The `current` junction remains the single indirection point for upgrades.

## References

- [#328](https://github.com/mlorentedev/hive/issues/328) — the issue this unblocks
- `specs/HIVE-328-runtime-launcher/` — the spec; its "Risks / open questions" posed this
  decision, and its Out-of-scope boundary is what rules out option C
- [#330](https://github.com/mlorentedev/hive/pull/330) — PR1, which added `_executes()`
- [ADR-015](adr-015-windows-daemon-supervision-upgrade.md) — Windows daemon supervision, where
  A3 originates
- `specs/HIVE-267-upgrade-swap/` — the `tasks.md:28` promise of an owned launcher
- `src/hive/_runtime.py` — `current_link()`, `self_upgrade()`, the A3 layout
- `src/hive/_service.py` — `_resolve_exec()`, `_current_layout_exec()`, `_executes()`
- dotfiles AI-028 (#791) — the install-model migration blocked on this; dotfiles #574 —
  `dotf doctor --fix`, which owns orphan removal
