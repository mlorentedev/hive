---
id: lesson-002-2026-03-01-claude-code-as-orchestrator-not-a-
type: lesson
status: active
created: "2026-05-01"
owner: manu
tags: [hive, lesson]
---

# 2026-03-01: Claude Code as orchestrator, not a custom framework

- **Context:** Evaluated CrewAI, LangGraph, AutoGen as orchestration options.
- **Decision:** Use Claude Code natively as orchestrator. Extend with MCP servers only.
- **Rationale:** Custom orchestrators replace Claude Code (lose ecosystem improvements). MCP servers extend it (ride the wave). Only ~650 lines of custom Python to maintain.
- **Trade-off:** Limited to what Claude Code's Agent/MCP system can do. Acceptable — it already does parallel subagents, hooks, and skills.
