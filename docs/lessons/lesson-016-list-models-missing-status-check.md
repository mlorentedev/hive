---
id: lesson-016-list-models-missing-status-check
type: lesson
status: active
created: "2026-03-06"
owner: manu
tags: [hive, lesson]
---

# list_models() missing status check

- **Context:** `OpenRouterClient.list_models()` called `resp.json()` without checking `resp.status_code` first
- **Root cause:** The `generate()` method had proper status checks but `list_models()` was added later and missed the pattern
- **Fix:** Added `if resp.status_code >= 400` guard + `float()` pricing wrapped in try/except ValueError
- **Lesson:** When adding new methods to an HTTP client class, copy the full error-handling pattern from existing methods, not just the happy path
