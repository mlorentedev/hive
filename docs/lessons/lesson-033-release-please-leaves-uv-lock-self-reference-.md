---
id: lesson-033-release-please-leaves-uv-lock-self-reference-
type: lesson
status: active
created: "2026-05-17"
owner: manu
tags: [hive, lesson, release-please, uv, ci, cross-project, lock-files]
---

# release-please leaves uv.lock self-reference stale on every release

**Context:** hive-vault uses release-please for version bumps. `release-please-config.json` registers `pyproject.toml` and `server.json` (via `extra-files`), but `uv.lock` cannot be added there — its `[[package]]` array-of-tables format makes jsonpath targeting unreliable in release-please's TOML updater. Every release left the lock's editable self-reference anchored at the previous version. By 2026-05-17 master's `uv.lock` said `1.12.1` while `pyproject.toml` said `1.12.6` (five releases of drift).
**Problem:** The drift is invisible in CI because `uv` operations still resolve, but every developer who runs `uv lock` / `uv sync` / `uv run` on master gets an uncommitted `uv.lock` diff. The signal of meaningful lock changes is blurred and master is internally inconsistent (lock self-ref vs `pyproject.toml` version mismatch). Identical pattern hits any project pairing release-please with `uv`, `poetry`, or `Cargo`.
**Solution:** In `release.yml`, after `googleapis/release-please-action@v4`, gate four steps on `if: steps.release.outputs.pr`: (1) jq-extract the PR's `headBranchName` into `GITHUB_ENV`, (2) `actions/checkout@v4` of that branch with the release PAT, (3) `setup-uv` + Python, (4) `uv lock` and conditionally commit/push any diff back to the PR branch. Route the dynamic branch name through `GITHUB_ENV` (not direct `${{ steps.… }}` interpolation in `run:`) to satisfy the workflow-injection lint. Cross-project version: the generalized "Special case: lock files with self-references" pattern lives in the maintainer's cross-project knowledge store (this lesson is its L-HIVE-88 origin); not linked here to preserve repo->store independence.
**Why:** release-please's `extra-files` only mutates targets when it bumps versions during a release PR — it cannot reliably address array-of-table TOML entries by name. Regenerating the lock on the release-please branch shifts the work to where it actually has access to the new `pyproject.toml` version. The `if: steps.release.outputs.pr` gate keeps the steps no-op when release-please has nothing to release; the PAT keeps the commit attributable and re-triggers the standard CI workflow on the new commit.
**Tags:** `#release-please` `#uv` `#ci` `#cross-project` `#lock-files`
