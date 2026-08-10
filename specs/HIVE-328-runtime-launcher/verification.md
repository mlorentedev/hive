---
tags: [spec, verification, templates]
created: "2026-08-07"
---

# Verification - HIVE-328-runtime-launcher

## Evidence

- [x] AC1 (layout beats PATH, resolves through `current`) -> `test_resolve_exec_prefers_the_a3_layout_over_an_arbitrary_path_hit`
- [x] AC2 (dead PATH hit falls through) -> `test_resolve_exec_rejects_a_path_hit_that_cannot_start`
- [x] AC3 (healthy PATH hit still wins with no layout) -> `test_resolve_exec_accepts_a_path_hit_that_starts`
- [x] AC4 (module-invocation fallback) -> `test_resolve_exec_falls_back_to_module_invocation`
- [x] AC5 (probe never propagates) -> `test_executes_treats_any_probe_failure_as_not_runnable`
- [x] AC6 (half-built layout does not win) -> `test_resolve_exec_ignores_a_layout_whose_launcher_is_missing`
- [ ] AC7 (fresh shell resolves `hive` after an upgrade) -> **open**, hardware-only; see "What PR2 cannot verify here"
- [x] AC8 (upgrade needs no launcher rewrite; install is idempotent) -> `test_launcher_survives_a_repoint_without_being_rewritten`, `test_install_launcher_rewrites_nothing_on_a_second_run`, `test_prepend_user_path_is_idempotent`
- [x] AC9 (never writes to or deletes from `~/.local/bin`) -> `test_install_launcher_never_touches_local_bin`, `test_launcher_lives_in_a_hive_owned_dir_beside_the_runtime`
- [x] AC10 (dead `hive*` reported, named with `dotf doctor --fix`, left unmodified) -> `test_orphaned_launchers_reports_a_dead_hive_without_modifying_it`, `test_orphaned_launchers_ignores_a_healthy_hive`, `test_orphaned_launchers_skips_hives_own_directory`, `test_orphan_warning_names_the_path_and_the_repair_command`
- [x] AC11 (prepended, not appended) -> `test_prepend_user_path_puts_hives_dir_first`
- [x] POSIX boundary (ADR-019 is Windows-only) -> `test_install_launcher_is_an_explicit_noop_off_windows`

## What PR2 cannot verify here, and why that is stated rather than papered over

AC7 says *"`hive --version` works from a fresh shell"*. That is a claim about the Windows shell's
environment, and this session ran on Linux — so it stays unticked, its `features.json` entry stays
`manual:`, ADR-015 stays `proposed`, and #328 stays open. The alternative, ticking it from a mocked
Windows, would assert exactly the thing no mock can know.

What *is* host-independent is every mechanism AC7 rests on, and each is pinned by a test: where the
shim goes (AC9), what it contains and that it dispatches through `current` (AC8), that a repeat
install rewrites nothing (AC8), and that the `PATH` entry is prepended rather than appended (AC11).
The forced-Windows fixture stubs exactly three seams — `sys.platform` and the two `winreg`
accessors — so the *policy* (prepend, deduplicate, never append) runs on every push and only the
registry call itself waits for hardware.

## Test status (PR2, this session)

```
$ uv run pytest tests/ -q
884 passed, 2 skipped, 63 deselected in 223.02s

$ uv run ruff check src/ tests/
All checks passed!

$ uv run ruff format --check src/ tests/
71 files already formatted

$ uv run mypy --strict src/
Success: no issues found in 30 source files
```

The 12 new tests were confirmed **red before the implementation existed** (`AttributeError` on
each new symbol), not merely green afterwards.

## A defect the tests caught before it could ship

The first `install_launcher` wrote the shim with `write_text` and compared with `read_text`, and
`test_install_launcher_rewrites_nothing_on_a_second_run` failed on an mtime that kept moving. The
cause is text-mode newline translation in both directions: the renderer emits `CRLF` because a
`.cmd` needs it, reading translates it back to `\n` so the idempotency check could never match, and
on Windows *writing* would have expanded `\n` to `os.linesep` and produced `\r\r\n` — a malformed
batch file. Fixed by making the shim a byte-exact artifact (`read_bytes`/`write_bytes`). Worth
recording because the visible symptom (a moving mtime) was the harmless half; the corrupt-on-Windows
half would have shipped silently on the one platform that runs this code.

## The failure this closes, observed on real hardware (2026-08-07)

Not hypothetical. On the maintainer's Windows box, at the time of writing:

```
$ python -c "import shutil; print(shutil.which('hive'))"
C:\Users\mlorente\.local\bin\hive.exe

$ hive --version
error: uv trampoline failed to canonicalize script path
```

The old `_resolve_exec()` returned that first path unconditionally, so `hive service install` would have registered a supervised daemon whose task action is a binary that cannot start — and Task Scheduler would have reported the registration as successful. The new resolution runs the same `--version` probe and falls through.

## Test status

```
$ .venv/Scripts/python.exe -m pytest tests/test_service.py -q
22 passed

$ .venv/Scripts/python.exe -m ruff check src/ tests/
All checks passed!

$ .venv/Scripts/python.exe -m ruff format --check src/ tests/
68 files already formatted

$ .venv/Scripts/python.exe -m mypy --strict src/
Success: no issues found in 29 source files
```

Full-suite run and the cross-OS matrix are carried by CI on the PR (local Python here is 3.14, outside the supported 3.12/3.13 floor, so CI is the authority for the whole-suite result).

- No regressions: the five existing `test_service.py` cases that monkeypatch `_resolve_exec` are untouched and green; `_resolve_exec` had **zero direct coverage** before this change, which is how the defect survived.

## Decisions made during implementation

- **Split PR1 from PR2.** The exec-resolution half has no design ambiguity and closes the live hole; the launcher half turns on an unresolved directory-ownership question. Landing a half-implemented launcher would have been a worse artifact than a written-up decision.
- **Verify by execution, not by existence.** `shutil.which` answers "is there a file on PATH", which is not the question the supervisor needs answered. The probe costs one bounded subprocess on the install path only.
- **Fixture reuses `_runtime._make_junction`.** The first draft used `Path.symlink_to` and failed with `WinError 1314` — a Windows symlink needs a privilege a junction does not. That is the same fact that made A3 pick `mklink /J`, so the test now exercises the production seam instead of a parallel one that only works on POSIX or elevated Windows.
- **Accepted the `sys.executable` fallback's version-pinning wrinkle.** Under A3 it resolves the pinned venv rather than following `current`; it is a last resort reached only when no layout and nothing runnable exist, where the running interpreter is the only guaranteed-working answer. Recorded in `proposal.md` rather than silently.

## Promotion candidates

- [ ] Lesson for the repo's `docs/lessons.md`? **yes, on PR2 completion** — "a `which` hit proves a file exists on PATH, not that it runs; when a supervisor is being pointed at something, existence is the wrong predicate". Held until the spec closes so it is written once, whole.
- [ ] ADR-worthy decision? not for PR1. The **launcher-directory** decision in PR2 likely is — it settles who owns `~/.local/bin` between hive and uv, which outlives this spec.
- [ ] New pattern candidate for `00_meta/patterns/`? no — single-project mechanics.

## Archive checklist

- [ ] `proposal.md` frontmatter set to `status: archived`
- [ ] Folder moved: `specs/HIVE-328-runtime-launcher/` -> `specs/archive/HIVE-328-runtime-launcher/`
- [ ] Bitácora board ticket moved to Done / closed with PR link (ADR-018) — **only after PR2**; PR1 alone does not close #328
- [ ] Promotions above executed (if any)
