---
id: lesson-035-github-actions-floating-major-tags-are-publis
type: lesson
status: active
created: "2026-05-19"
owner: manu
tags: [hive, lesson, ci, github-actions, deps]
---

# GitHub Actions floating major tags are publisher-dependent — verify before bumping

**Context:** Bumping all CI actions to Node 24 ahead of 2026-06-02 deprecation. Started with @v6/@v8/@v5 across checkout/setup-uv/release-please-action.
**Problem:** CI failed with "Unable to resolve action astral-sh/setup-uv@v8, unable to find version v8" even though gh api showed v8.1.0 as latest release. Each action publisher uses a different tagging convention: actions/checkout publishes floating majors (v6 → v6.0.2), googleapis/release-please-action publishes floating majors (v5 → v5.0.0), but astral-sh/setup-uv publishes only floating MINORS (v7.4, v7.5, v7.6) plus exact SemVer (v8.0.0, v8.1.0). No floating major tag exists for setup-uv.</problem>
<parameter name="solution">Before bumping a GitHub Action's major version, verify the floating tag actually exists: gh api repos/OWNER/REPO/git/refs/tags/vN. If 404, fall back to exact SemVer (vN.M.P) or the highest floating minor (vN.M). For astral-sh/setup-uv, pin to v8.1.0 exactly until a v8 floating tag is published.
**Solution:** 
**Tags:** `#ci` `#github-actions` `#deps`
