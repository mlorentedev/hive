---
id: lesson-087-a-green-review-check-can-mean-did-not-review
type: lesson
status: active
created: "2026-08-09"
owner: manu
tags: [hive, lesson, code-review, ci, verification, coderabbit, definition-of-done]
---

# A green review check can mean "did not review"

**Context:** Five PRs were opened in one session. Each showed a `CodeRabbit` check with conclusion `SUCCESS`, alongside the usual CI contexts.
**Problem:** Only one of the five had actually been reviewed. The other four carried a comment beginning "Review limit reached" — the bot had hit its quota, posted a notice, and reported the check as successful. The conclusion is honest about the *check* (it ran, it did not error) and silent about the *review* (it never happened). Reading the rollup, all five look equally covered, and the Definition of Done's "reviewer comments triaged" gate passes vacuously on four of them.
**Solution:** Treat a bot's check conclusion as necessary, not sufficient. Read the comment body — `gh pr view <n> --json comments` — and confirm a review was actually performed before counting it as one. The single PR that was reviewed produced three valid findings, including a `%.1f` format that rendered `300.04` as `300.0` in the very warning the PR added, so the reviews were worth having.
**Why:** Same shape as the other verification failures in this codebase: a signal that reports on its own execution rather than on its subject. `addopts` deselecting a marked test, a `features.json` command naming a test that no longer exists, a benchmark whose load fell below its discrimination threshold — and now a review bot that passes when it declines to review. When a check is a gate, ask what it asserts when the thing it gates did not happen.
**Tags:** `#code-review` `#ci` `#verification` `#coderabbit` `#definition-of-done`
