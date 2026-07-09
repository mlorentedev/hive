---
tags: [spec, verification, templates]
created: "2026-06-24"
---

# Verification - HIVE-267-upgrade-swap

## Evidence

Map every acceptance criterion from `proposal.md` to concrete proof (commit hash, test name, or observed behavior).

- [ ] Criterion 1 (upgrade-while-running leaves a valid install) -> commit `<hash>` / test `<name>`
- [ ] Criterion 2 (#267 reproduction no longer fails on in-use files) -> commit `<hash>` / test `<name>`
- [ ] Criterion 3 (failed swap leaves previous install intact + actionable error) -> commit `<hash>` / test `<name>`
- [ ] Criterion 4 (validated on real non-admin Windows hardware) -> observed behavior

## Test status

- Test suite: not yet — implementation not started (spike only).
- **A3 feasibility spike — PASSED** (real non-admin Windows `TDY\MLORENTE`, uv 0.10.5, 2026-06-24; sandbox `scratchpad/a3-spike.ps1`, never touched the real install):
  1. Created a `current` junction → `versions/1.41.5` **as non-admin** — read through it OK.
  2. Held an **exclusive in-process lock** on `versions/1.41.5/core.pyd` (simulating a loaded `.pyd` / running venv python). Confirmed locked: `Remove-Item` failed with "being used by another process".
  3. **Repointed `current` → `versions/1.41.6` WHILE 1.41.5 was locked — SUCCEEDED with NO "Access is denied"**; reads through `current` then returned 1.41.6. This is the exact #267 failure mode, now avoided.
  4. GC of the locked old version was correctly **deferred** (fails while locked) and **succeeded after the lock released** — A3's "GC old dir once unreferenced".
- No regressions: n/a (no code changed yet).

## Decisions made during implementation

Brief log of non-obvious trade-offs or course corrections taken during the work. Routine choices belong in commit messages, not here.

- **A3 validated on real non-admin Windows hardware (2026-06-24) — proceed with A3; no fallback to A4/A2 needed.** Junction ops touch only the reparse point, never the locked target files, so the repoint is immune to the in-use lock that breaks `uv tool upgrade` (#267).
- **`self_upgrade` orchestration decisions (2026-07-09, successor issue #292):**
  - **Version-resolution contract = explicit `<version>` REQUIRED.** Deterministic and network-free (tests need no PyPI); auto-`latest` is deferred to the dotfiles-trigger follow-up so it lands with the network seam it actually needs.
  - **`self_upgrade` does NOT restart the daemon.** It only builds + repoints + GCs; the supervisor's existing exit-75 restart-on-upgrade contract relaunches `hive serve` through the freshly repointed `current`. Keeping restart out of the swap keeps this PR scoped to the mechanism (no scope creep).
  - **GC retention = remove every non-current version; locked ones defer.** A still-locked old version (the supervisor holding its `python.exe`) returns `False` from `remove_version` and survives to the next run — no rollback-retention policy yet (out of scope; the acceptance criteria don't require keeping N previous versions).
  - **Idempotent + retry-safe.** No-op when already on the target (the unattended trigger can fire repeatedly); an already-built dir from a crashed prior run is reused rather than rebuilt (a rebuild would hit `build_version`'s 'already built' guard).
- Spike harness correction: an initial `Start-Process` lock-holder was racy (PowerShell startup latency left the file briefly unlocked, producing a false result). Switched to an in-process `[IO.File]::Open(..., FileShare.None)` for a deterministic lock — recorded so the next person does not repeat the race.
- Implementation note: the practical repoint (`rmdir` junction + recreate) has a sub-millisecond non-atomic window where `current` is absent; tolerated by the supervisor's relaunch loop. A fully atomic swap (create `current.new`, rename over `current`) is an optional refinement, not a feasibility blocker.

## Promotion candidates

Before archiving, flag what (if anything) should be promoted to the vault. If all three are "no", archive in repo is the only persistence.

- [ ] Lesson for the repo's `docs/lessons.md`? <yes / no - one line of what>
- [ ] ADR-worthy decision for the repo's `docs/adr/adr-XXX.md`? Likely yes — the A3/A4 choice updates/closes ADR-015 mechanism (A).
- [ ] New pattern candidate for `00_meta/patterns/`? Only if this recurs in >1 project. <yes / no - one line>

## Archive checklist

- [ ] `proposal.md` frontmatter set to `status: archived`
- [ ] Folder moved: `specs/HIVE-267-upgrade-swap/` -> `specs/archive/HIVE-267-upgrade-swap/`
- [ ] Bitácora board ticket for this spec moved to Done / closed with PR link (ADR-018)
- [ ] Promotions above executed (if any)
