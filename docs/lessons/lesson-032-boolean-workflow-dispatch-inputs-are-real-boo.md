---
id: lesson-032-boolean-workflow-dispatch-inputs-are-real-boo
type: lesson
status: active
created: "2026-05-15"
owner: manu
tags: [hive, lesson, github-actions, workflow-dispatch, ci, boolean-coercion]
---

# Boolean `workflow_dispatch` inputs are real booleans in `if:` expressions

**Context:** Added `workflow_dispatch` with a boolean input `republish_mcp` and gated the publish job with `if: inputs.republish_mcp == 'true'`. The job skipped on every manual trigger.
**Problem:** GitHub Actions converts `type: boolean` inputs to actual booleans when reading via `inputs.*` in expressions. Comparing against the string `'true'` always evaluates false, silently skipping the gated job.
**Solution:** Use the input truthily (`inputs.republish_mcp`) or compare against the unquoted boolean (`inputs.republish_mcp == true`). Confirmed by re-triggering and seeing `publish-mcp` actually run.
**Why:** Documented but easy to miss — most other Actions contexts are strings. When a `workflow_dispatch` input is declared as boolean, the expression engine respects the type. String comparison is a silent footgun: the workflow appears to "work" because the trigger succeeds, but the gated job is never reached.
**Tags:** `#github-actions` `#workflow-dispatch` `#ci` `#boolean-coercion`
