---
id: lesson-021-2026-03-08-test-flakiness-from-real-external-
type: lesson
status: active
created: "2026-05-01"
owner: manu
tags: [hive, lesson]
---

# 2026-03-08: Test flakiness from real external services in unit tests

- **Context:** After merging `vault_summarize` into `delegate_task`, the large-file test became flaky — passing in isolation, failing in full suite.
- **Problem:** The `vault_mcp` test fixture creates a server with default settings. When `OPENROUTER_API_KEY` is set in the environment (from dotfiles), the server actually connects to OpenRouter and returns a real summary instead of the expected fallback content.
- **Solution:** Made test assertions handle both cases (worker available → summary, worker unavailable → raw content). The proper fix would be injecting a null OpenRouter client in the vault_mcp fixture.
- **Lesson:** Test fixtures that create servers without explicit client injection will use real external services if env vars are set. Always inject mock clients in unit test fixtures.
