---
tags: [spec, verification, templates]
created: "2026-06-24"
---

# Verification - HIVE-267-upgrade-swap

## Evidence

Map every acceptance criterion from `proposal.md` to concrete proof (commit hash, test name, or observed behavior).

Recorded 2026-08-07 (#333). Marked against evidence, not intent — see
`proposal.md` -> Acceptance criteria for the same status with reasoning.

- [~] **Criterion 1** (upgrade-while-running leaves a valid install) -> **partially met.**
      Layout half: `8355b9f` (#290) `repoint()` + versioned paths, `6c41c8b` (#294)
      `build_version()` / `self_upgrade()`. Tests: `tests/test_runtime.py` (25 passing).
      `hive --version` half: **unverifiable** — no launcher on `PATH`
      ([#328](https://github.com/mlorentedev/hive/issues/328); PR1 `#330` shipped,
      PR2 pending, directory settled by ADR-019).
- [~] **Criterion 2** (#267 reproduction no longer fails on in-use files) -> **met in
      mechanism, unverified in the field.** The spike below reproduced the exact
      failure mode and showed the repoint succeeding under an exclusive lock. Never
      re-run against the shipped code on real hardware — see Criterion 4.
- [x] **Criterion 3** (failed swap leaves previous install intact + actionable error)
      -> **met.** `8355b9f` (#290), stage-then-flip: the fallible `_make_junction`
      runs before `current` is disturbed; failure raises a WHY/FIX `RuntimeError`.
      Test: `test_failed_repoint_leaves_the_previous_current_intact`. Supporting:
      `build_version()` cleans a half-built dir on failure; `remove_version()`
      refuses the active version and defers a locked one.
- [ ] **Criterion 4** (validated on real non-admin Windows hardware) -> **NOT met.**
      The 2026-06-24 hardware pass was the *feasibility spike*, before any code
      existed. The shipped implementation has run only on CI (Linux + Windows
      runners) and a Linux dev box. Blocked in practice: the target machine is in
      the orphaned-trampoline state (dotfiles#791). **This is the gate on archiving
      this spec.**

## Test status

- Test suite: **25 passing** in `tests/test_runtime.py` (`uv run pytest
  tests/test_runtime.py -q`, verified 2026-08-07). Covers the repoint primitive,
  the failed-repoint rollback, `build_version` / `current_version` /
  `remove_version`, `self_upgrade` orchestration, `latest_version` (happy /
  timeout / bad-payload) and the CLI wrapper omitted-vs-explicit paths.
  `mypy --strict src/` and `ruff` clean.
- Shipped in three PRs: **#290** (`8355b9f`, layout + junction repoint), **#294**
  (`6c41c8b`, `hive self-upgrade` end-to-end), **#302** (`e0001f9`, auto-latest
  from PyPI). All three commit hashes verified to resolve.
- **Production dependency:** `_service._current_layout_exec()` resolves
  `current/{Scripts,bin}/hive[.exe]`, and ADR-019 builds the launcher decision on
  this layout. The mechanism is load-bearing, not experimental.
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

Decided 2026-08-07 (#333).

- [x] Lesson for the repo's `docs/lessons.md`? **Yes — already captured.** The
      2026-08-07 entry *"A ticket's premise expires; the code moves on and the
      instruction does not"* (#337) draws on this spec: its `tasks.md` asserted the
      launcher already resolved through `current`, which was false and is why the
      "+ own launcher" promise went unnoticed until it became #328.
- [x] ADR-worthy decision? **Yes — two, both written.** The A3 choice is recorded
      in [ADR-015](../../docs/adr/adr-015-windows-daemon-supervision-upgrade.md)
      mechanism (A), whose AC-3 is closed by #328 PR2 rather than here. The
      launcher-ownership question this spec opened is
      [ADR-019](../../docs/adr/adr-019-launcher-ownership.md).
- [x] New pattern candidate for `00_meta/patterns/`? **No.** The junction-swap
      technique is specific to Windows in-use-file locking and has not recurred in
      another project; A3 is documented in ADR-015 where it belongs.

## Archive checklist

**Not yet archivable.** Blocked on Criterion 4 (real-hardware re-validation of
the shipped code) and, for AC1's `hive --version` clause, on #328 PR2. Frontmatter
is `verifying` as of 2026-08-07.

- [ ] `proposal.md` frontmatter set to `status: archived` — currently `verifying`
- [ ] Folder moved: `specs/HIVE-267-upgrade-swap/` -> `specs/archive/HIVE-267-upgrade-swap/`
- [ ] Bitácora board ticket for this spec moved to Done / closed with PR link (ADR-018)
- [x] Promotions above executed — lesson captured in #337; ADR-015 and ADR-019 both written
