---
id: lesson-047-github-closes-n-keyword-only-parsed-when-on-i
type: lesson
status: active
created: "2026-05-22"
owner: manu
tags: [hive, lesson, github, workflow, pull-request, automation]
---

# GitHub `Closes #N` keyword only parsed when on its own line at PR body footer

**Context:** HIVE-115 PR-3 (#119) body included `Closes #111` inside a Summary section bullet list ("Closes **#111** — the 838s..."). PR-4 (#121) body had the same `Closes #110` styling.
**Problem:** After PR-3 merged, issue #111 stayed OPEN — had to close manually with a `gh issue close` + reference comment. GraphQL `closingIssuesReferences` returned empty. The auto-close keyword detection failed silently.
**Solution:** For PR-4 (#121), placed `Closes #110` on its own line at the body footer. GraphQL verified `closingIssuesReferences: [{number: 110}]`. On merge, #110 auto-closed correctly. Rule: GitHub's close-keyword scanner is fragile within rich markdown (bold, inline list items, surrounding text); always put `Closes #N` / `Fixes #N` / `Resolves #N` on a bare line at the bottom of the PR body. Verify with `gh api graphql -f query='{... pullRequest(number:N) { closingIssuesReferences { nodes { number } } } }'` before relying on auto-close.
**Tags:** `#github` `#workflow` `#pull-request` `#automation`
