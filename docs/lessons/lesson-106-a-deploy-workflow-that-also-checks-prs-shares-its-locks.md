---
id: lesson-106-a-deploy-workflow-that-also-checks-prs-shares-its-locks
type: lesson
status: active
created: "2026-10-19"
owner: manu
tags: [hive, lesson, github-actions, ci, pages, BUG-009]
---

# A deploy workflow that also checks PRs shares its locks

**Context:** `pages.yml` built and deployed the docs site only on a push to
master, so no pull request ever built `site/` and every Dependabot npm bump
merged unverified (#477). The fix adds a `pull_request` trigger to the same
workflow, so the PR check is the exact job that later deploys.

**Problem:** Everything declared at workflow level was written for one event
and silently applies to the new one. `concurrency: {group: pages,
cancel-in-progress: true}` would let a PR push cancel an in-flight master
deploy; the workflow-level `pages: write` / `id-token: write` would reach every
PR build; and the deploy job had no event guard. None of it fails a check.

**Solution:** Key the PR runs on their own group and keep deploys on the
original `pages` (not `pages-${{ github.ref }}`, which would split
`workflow_dispatch` from a branch and master pushes into separate deploy
groups). Move the Pages token to the deploy job, and guard both the deploy job
and the artifact upload on `github.event_name != 'pull_request'`.
`tests/test_pages_workflow.py` pins all four, since there is no actionlint in
CI and each failure is silent.

**Rule:** When you add a trigger to a workflow, re-read every workflow-level
key (`concurrency`, `permissions`, `env`) as if it were written for the new
event, because it now is.
