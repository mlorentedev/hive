---
id: lesson-005-2026-03-01-pydantic-settings-over-manual-env-
type: lesson
status: active
created: "2026-05-01"
owner: manu
tags: [hive, lesson]
---

# 2026-03-01: pydantic-settings over manual env var resolution

- **Context:** config.py had 6 `_resolve_*()` functions + module-level constants (52 lines). No startup validation, harder to test.
- **Decision:** Refactor to `pydantic-settings` `BaseSettings` with `HIVE_` prefix.
- **Rationale:** Standard 12-factor pattern. Automatic type coercion (str→Path, str→float). Startup validation catches bad config immediately. `AliasChoices` for backward-compatible `OPENROUTER_API_KEY` (no prefix). 52 → 25 lines, zero test file changes needed (DI already in place).
- **Trade-off:** New dependency (`pydantic-settings`). Acceptable — pydantic is already a transitive dep of FastMCP, so it adds ~0 weight.
