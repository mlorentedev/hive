---
id: "ADR-001-orchestration-model"
type: adr
status: accepted
date: "2026-03-01"
tags: [architecture, decision, orchestration, mcp]
owner: manu
created: "2026-03-28"
---

# ADR-001: Claude Code as Orchestrator with MCP Extension Layer

## Context
Need a multi-model AI orchestration system that:
- Fixes context window saturation in Claude Code sessions (5h→3h due to static vault loading)
- Routes low-complexity tasks to cheaper/local models (Ollama, Qwen)
- Uses Obsidian vault as single source of truth (read-write by agents)
- Stays under $100-110/mo budget
- Benefits from ongoing Claude Code ecosystem improvements (plugins, hooks, skills)
- Scales from solo dev to small team

## Options Considered

1. **Custom orchestrator (CrewAI / LangGraph / AutoGen)**
    * *Pros:* Full control over routing, mature frameworks, multi-model native
    * *Cons:* API-based (pay-per-token, no plan cap), replaces Claude Code (lose ecosystem), heavy dependency, no Obsidian vault integration

2. **Claude Code + MCP extension servers**
    * *Pros:* Rides Claude Code improvements for free, minimal custom code (~650 lines), flat-rate billing, MCP is a standard protocol, vault access on-demand instead of upfront
    * *Cons:* Limited to Claude Code's orchestration capabilities, MCP SDK maturity varies by language

3. **Multi-tool setup (Claude Code + separate Qwen CLI + shared vault)**
    * *Pros:* Each tool runs independently, no custom code
    * *Cons:* No coordination between tools, duplicate context loading, manual task routing, vault conflict risk

## Decision
We chose **Option 2: Claude Code + MCP extension servers** because:
- Claude Code already has parallel subagents, hooks, and skills — it IS an orchestrator
- MCP servers are the minimal extension point (typed tools, protocol-based, language-agnostic)
- Only 2 custom servers to maintain: vault (read/write vault) and worker (delegate to Ollama/Qwen)
- Upstream Claude Code improvements flow through automatically
- Total cost stays at $100 Claude plan + $10 Qwen API cap = $110/mo

## Architecture

```
Claude Code (brain + orchestrator)
    ├── Vault MCP Server (Python/FastMCP)
    │   └── On-demand vault access replaces static CLAUDE.md loading
    ├── Worker MCP Server (Python/FastMCP)
    │   ├── Ollama (homelab VPN) → primary, free
    │   └── Qwen API (OpenRouter) → fallback, $10/mo cap
    └── Existing MCP servers (drawio, context7, sequential-thinking)
```

## Consequences
- **Positive:** 60-70% reduction in static context. Parallel grunt work capacity. Self-improving over time. Existing dotfiles/vault work fully preserved.
- **Negative:** Dependent on Claude Code's MCP implementation quality. Worker output needs Claude review (no autonomous deployment). Python MCP servers add a runtime dependency.

## References
- [Model Context Protocol spec](https://modelcontextprotocol.io/)
- [FastMCP Python SDK](https://github.com/jlowin/fastmcp)

<!-- Provenance: the related dotfiles MCP-persistence decision lives in the maintainer's cross-project knowledge store. Not linked here to preserve repo->store independence (knowledge-placement directionality invariant). -->
