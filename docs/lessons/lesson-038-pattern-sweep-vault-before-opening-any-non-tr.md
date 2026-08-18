---
id: lesson-038-pattern-sweep-vault-before-opening-any-non-tr
type: lesson
status: active
created: "2026-05-20"
owner: manu
tags: [hive, lesson, workflow, git, rules-discipline]
---

# Pattern-sweep vault before opening any non-trivial branch

**Context:** Starting the post-HIVE-104 docs cleanup PR. Named the branch docs/post-HIVE-104 and was about to push when a sweep of _meta/patterns/pattern-git-workflow.md surfaced two rule violations.
**Problem:** Violations were: (1) docs/ prefix not in the approved table (chore/, fix/, feat/, release/), and (2) post-HIVE-104 is a milestone/phase reference, forbidden by git-workflow §7 "phase tracking belongs in the vault backlog, not git history". Without the sweep, both would have landed on origin and been visible to humans/CI.
**Solution:** Before the first commit on any new branch, query the cross-cutting patterns that gate the work: vault_query(project="_meta", path="patterns/pattern-git-workflow.md") for branch/commit/PR rules, plus any topic-specific pattern (docs-site-starlight, language-standards, spec-driven-development). Renaming the branch later is cheap; renaming after push is not. The branch was renamed to chore/commit-policy-doc-followups before any push.
**Tags:** `#workflow` `#git` `#rules-discipline`
