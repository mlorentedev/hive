---
tags: [spec, verification, templates]
created: "2026-08-23"
---

# Verification - HIVE-384-nan-worker-and-delegate-verb

## Evidence

Map every acceptance criterion from `proposal.md` to concrete proof (commit hash, test name, or observed behavior).

> **Status as of 2026-08-23: every criterion below is PENDING, and that is the spec's state, not an
> omission.** This folder was scaffolded by a docs-only PR; no implementation exists yet, so no
> criterion can carry evidence. Each row's follow-up is the correspondingly tagged `[AC<n>]` task in
> `tasks.md`. The only result recorded today is the pre-change baseline under Test status, which
> proves the tree was green *before* the work, not that any criterion is met. `spec archive` must
> refuse this folder until these rows carry real hashes and test names.

- [ ] AC1 (hive delegate honours the wire contract) -> commit `<hash>` / test `<name>`
- [ ] AC2 (exit codes separate pool-unavailable from task-failed) -> commit `<hash>` / test `<name>`
- [ ] AC3 (timeout kills the worker and returns without waiting) -> commit `<hash>` / test `<name>`
- [ ] AC4 (routes through the daemon, degrades honestly without one) -> commit `<hash>` / test `<name>`
- [ ] AC5 (the worker reaches NaN) -> commit `<hash>` / test `<name>`
- [ ] AC6 (Ollama and OpenRouter gone from every surface) -> commit `<hash>` / test `<name>`
- [ ] AC7 (the credential never appears in output) -> commit `<hash>` / test `<name>`

## Test status

- **Pre-change baseline, 2026-08-23** — `make check` on this branch before any source change:
  `897 passed, 2 skipped, 63 deselected in 241.15s, 85% coverage, exit 0`.
  Recorded so a later failure is attributable to the change rather than to the environment. The 63
  deselected are the `@pytest.mark.smoke` tests, excluded by `-m 'not smoke'`.
  *Caveat worth keeping:* the first attempt failed with 7 mypy errors about missing `types-psutil`
  stubs. That was a fresh worktree whose `.venv` was unsynced, **not** a repo defect —
  `types-psutil>=5.9.0` is declared in `pyproject.toml` and CI runs `uv run mypy src/`.
- Post-change test suite: `<command> -> <output / coverage %>` — PENDING
- Manual smoke test against a live NaN endpoint: PENDING. Required for AC5 and AC6; it cannot run in
  `make check` because it needs a credential, so its output is pasted here by hand.
- No regressions in existing test suite: PENDING (compare against the baseline above)

## Decisions made during implementation

Brief log of non-obvious trade-offs or course corrections taken during the work. Routine choices belong in commit messages, not here.

-
-

## Promotion candidates

Before archiving, flag what (if anything) should be promoted to the vault. If all three are "no", archive in repo is the only persistence.

- [ ] Lesson for the repo's `docs/lessons/`? <yes / no - one line of what>
- [ ] ADR-worthy decision for the repo's `docs/adr/adr-XXX.md`? <yes / no - one line of what>
- [ ] New pattern candidate for `00_meta/patterns/`? Only if this recurs in >1 project. <yes / no - one line>

## Archive checklist

- [ ] `proposal.md` frontmatter set to `status: archived`
- [ ] Folder moved: `specs/HIVE-384-nan-worker-and-delegate-verb/` -> `specs/archive/HIVE-384-nan-worker-and-delegate-verb/`
- [ ] Bitácora board ticket for this spec moved to Done / closed with PR link (ADR-018)
- [ ] Promotions above executed (if any)
