---
id: lesson-018-ci-publish-failures-on-duplicate-versions-mak
type: lesson
status: active
created: "2026-03-07"
owner: manu
tags: [hive, lesson, ci, release, idempotency]
---

# CI publish failures on duplicate versions — make idempotent

**Context:** release-please created a release, CI published to PyPI and MCP Registry. Re-running the workflow failed because the version was already published.
**Problem:** MCP Registry publish step returned a non-zero exit code on duplicate version, failing the entire CI pipeline. PyPI had the same issue but was already handled with `--skip-existing`.
**Solution:** Added `continue-on-error: true` to the MCP Registry publish step. Duplicate publishes are expected (re-runs, manual triggers) and should not block CI.
**Why:** Idempotency in CI pipelines. Any publish step that can be re-run must tolerate "already exists" as a success condition.
**Tags:** `#ci` `#release` `#idempotency`
