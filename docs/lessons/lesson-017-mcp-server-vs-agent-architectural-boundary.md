---
id: lesson-017-mcp-server-vs-agent-architectural-boundary
type: lesson
status: active
created: "2026-03-07"
owner: manu
tags: [hive, lesson, architecture, mcp, design-principle]
---

# MCP server vs agent — architectural boundary

**Context:** Evaluating whether hive should evolve from MCP server to agent framework
**Problem:** Confusion between MCP servers (tool providers, stateless, client-agnostic) and agents (autonomous actors with their own decision loops). Some competitor projects blur this line.
**Solution:** MCP servers extend the host — they provide tools the host decides when to call. Agents replace the host's decision loop. Hive is and should remain an MCP server. The host (Claude Code, Gemini CLI, etc.) is the orchestrator. Adding agent behavior would couple hive to a specific host.
**Why:** Protocol-level separation of concerns. MCP is a tool interface, not an execution framework. Staying protocol-pure keeps hive client-agnostic.
**Tags:** `#architecture` `#mcp` `#design-principle`
