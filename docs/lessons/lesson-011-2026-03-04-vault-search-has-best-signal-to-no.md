---
id: lesson-011-2026-03-04-vault-search-has-best-signal-to-no
type: lesson
status: active
created: "2026-05-01"
owner: manu
tags: [hive, lesson]
---

# 2026-03-04: vault_search has best signal-to-noise ratio

- **Context:** Benchmarking measured signal-to-noise ratio across tools. `vault_search` scored 98.8% (almost all returned content is relevant). `session_briefing` scored 78.5% (includes boilerplate headers, health checks, git log noise).
- **Implication:** For targeted queries, prefer `vault_search` over `session_briefing`. Reserve `session_briefing` for cold-start orientation where breadth matters more than precision.
- **Action:** This data feeds into P1 (Context Curator) design — relevance scoring should weight search results higher than briefing sections.
