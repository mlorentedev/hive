---
id: lesson-031-release-please-extra-files-does-not-retroacti
type: lesson
status: active
created: "2026-05-15"
owner: manu
tags: [hive, lesson, release-please, ci, error-handling, mcp-registry]
---

# release-please `extra-files` does not retroactively patch drifted files

**Context:** `server.json` was added to `release-please-config.json` as an `extra-files` entry pointing at `$.version` and `$.packages[0].version`. The file was already at v1.4.5 when the config landed. PyPI advanced through 1.5.x → 1.12.2 over eight releases, but `server.json` stayed at 1.4.5 the entire time, and the MCP Registry kept rejecting `mcp-publisher publish` with `400 duplicate version`. The failure was hidden by a generic `|| echo "skipped"` on the publish step.
**Problem:** release-please's `extra-files` updater only fires when it bumps a version inside a release PR. It does not synchronise pre-existing drift — if the file is wrong when you add the config, it stays wrong. Combined with a catch-all error silencer, the registry quietly froze for two months.
**Solution:** Bump the drifted file manually to the current version once. release-please picks up from there. While there, replace the catch-all silencer (`|| echo skipped`) with a grep on the *specific* failure string (`cannot publish duplicate version`) so genuine failures surface. Add a `workflow_dispatch` input to the release workflow so you can re-publish to the registry without inventing a new release.
**Why:** Generic error swallowers are tech-debt batteries: they capture symptoms forever until someone notices the divergence. Pair every "best-effort" step with a precise filter that distinguishes "expected idempotent miss" from "real failure", and provide a manual re-run path so corrective action doesn't require a fake feature commit.
**Tags:** `#release-please` `#ci` `#error-handling` `#mcp-registry`
