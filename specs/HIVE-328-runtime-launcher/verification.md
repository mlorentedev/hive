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
- [ ] AC7, AC8 -> PR2, blocked on the launcher-directory decision

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
