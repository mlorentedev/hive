---
id: lesson-066-ci-lint-runs-ruff-format-check-too-a-clean-ru
type: lesson
status: active
created: "2026-06-24"
owner: manu
tags: [hive, lesson, ci, ruff, dev-workflow, fail-fast]
---

# CI lint runs `ruff format --check` too — a clean `ruff check` is not enough

**Context:** Fixing #246/#252 across several atomic PRs. `ruff check src/ tests/` was clean locally, so a PR was pushed.
**Problem:** CI's `check (3.12)` failed in ~10s and `check (3.13)` showed as *cancelled* — looking like a flaky/cancelled run. The real failure was the `uv run ruff format --check src/ tests/` step (CI runs it after `ruff check`): a passing linter is not a passing formatter. The drift was a single `assert any(...)` wrapped across lines that the formatter collapses to one line. The `3.13` leg was *cancelled* only because the matrix `fail-fast` killed it when `3.12` failed — a red herring.
**Solution:** Before any push, run BOTH `ruff check` AND `ruff format --check` (the full `make check` covers both). On a dev box where `make`/`uv run` is broken (e.g. a Python 3.14 trampoline), invoke the venv python directly: `.venv/Scripts/python.exe -m ruff format --check src/ tests/`. Fix drift with `ruff format <file>`. When a CI matrix shows one leg `failed` and a sibling `cancelled`, read the leg that actually `failed` — `fail-fast` cancels the rest.
**Tags:** `#ci` `#ruff` `#dev-workflow` `#fail-fast` `#hive`
