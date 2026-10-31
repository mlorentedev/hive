---
id: lesson-115-a-major-version-pin-takes-the-cached-patch
type: lesson
status: active
created: "2026-10-31"
owner: manu
tags: [hive, lesson, github-actions, node, astro, pages, dependabot]
---

# A major-version pin takes the cached patch

**Context:** `pages.yml` set up Node with `actions/setup-node` and `node-version: 22`.
Dependabot PR #532 bumped astro from 7.4.0 to 7.4.2 and the site build failed with
"Node.js v22.23.3 is not supported by Astro! ... >=22.23.4".

**Problem:** `node-version: 22` is satisfied by whatever 22.x the runner image already
has in its tool cache. setup-node only downloads a newer patch when the cached one
does not match. astro raises its Node floor within a major, in a patch release, so a
dependency bump can require a Node patch that is newer than the image's cache. The
build then fails on a PR that touched only `package-lock.json`.

**Solution:** Add `check-latest: true`, so setup-node resolves the newest 22.x on every
run instead of accepting the cached one. Do not pin an exact patch: it would have to be
bumped again every time astro raises its floor. `tests/test_pages_workflow.py` pins the
key.

**Rule:** A major-only runtime pin in CI means "whatever the image cached". When a
dependency can raise its runtime floor within that major, ask for the newest patch
(`check-latest`), or the next floor bump will break the build.
