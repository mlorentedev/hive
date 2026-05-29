---
id: adr-002-system-architecture-phase5
type: adr
status: active
created: "2026-03-03"
owner: manu
---

# ADR-002: System Architecture — Post-Phase 5 Snapshot

## Status

Accepted

## Date

2026-03-03

## Context

Phases 1–5 are shipped. The system has grown from a single vault query tool to a full context platform with 10 tools, 5 resources, and 4 prompts across two MCP servers. This ADR documents the architecture as-built, including component inventory, data flow, and known failure modes.

ADR-001 established Claude Code as orchestrator with MCP extensions. This ADR does not change that decision — it captures the concrete implementation that resulted.

## Decision

Document the system architecture at the Phase 5 milestone for future reference, onboarding, and audit purposes.

## Component Inventory

### Vault MCP Server (`hive-vault`)

**Purpose:** On-demand Obsidian vault access, replacing static CLAUDE.md context loading.

| Primitive | Name | Purpose |
|---|---|---|
| Tool | `vault_query` | Read vault content by project/section/path |
| Tool | `vault_search` | Full-text search with metadata filters |
| Tool | `vault_smart_search` | Ranked search (status weight + recency) |
| Tool | `vault_summarize` | Small files inline, large files → delegation prompt |
| Tool | `vault_update` | Write to vault with frontmatter validation + git commit |
| Tool | `vault_create` | New file with auto-generated frontmatter + git commit |
| Tool | `vault_health` | Project health metrics + stale file detection |
| Tool | `vault_list_projects` | Enumerate all vault projects |
| Tool | `session_briefing` | One-call cold-start: tasks + lessons + git log + health |
| Tool | `vault_recent` | Files changed in last N days (git + frontmatter) |
| Resource | `hive://projects` | Static: project listing |
| Resource | `hive://health` | Static: vault health report |
| Resource | `hive://projects/{project}/context` | Template: project context doc |
| Resource | `hive://projects/{project}/tasks` | Template: project task backlog |
| Resource | `hive://projects/{project}/lessons` | Template: project lessons |
| Prompt | `retrospective` | End-of-session lesson extraction protocol |
| Prompt | `delegate` | Structured worker delegation protocol |
| Prompt | `vault_sync` | Post-sprint vault synchronization |
| Prompt | `benchmark` | Session token savings estimation |

### Worker MCP Server (`hive-worker`)

**Purpose:** Delegate low-complexity tasks to cheaper/local models.

| Primitive | Name | Purpose |
|---|---|---|
| Tool | `delegate_task` | Send task to worker model with routing |
| Tool | `list_models` | Enumerate available models + pricing |
| Tool | `worker_status` | Budget usage, model availability |

### Shared Infrastructure

| Component | Technology | Location |
|---|---|---|
| Configuration | pydantic-settings (`HiveSettings`) | `src/hive/config.py` |
| Budget tracker | SQLite (WAL mode) | `src/hive/budget.py` |
| HTTP clients | httpx (async) | `src/hive/clients.py` |
| Frontmatter | PyYAML + custom parser | `src/hive/frontmatter.py` |
| Vault storage | Obsidian (markdown + git) | `~/Projects/knowledge/` |
| Local LLM | Ollama (qwen2.5-coder:7b) | `ollama.kubelab.live:11434` |
| Cloud LLM | OpenRouter (Qwen3 Coder free) | API with $5/mo cap |

## Data Flow

### Read Path (vault query)
```
Claude Code → MCP call → vault_server.py
  → _resolve_file(vault_path, project, section, path)
  → Path.read_text() → _truncate() → return text
```

### Write Path (vault update)
```
Claude Code → MCP call → vault_server.py
  → validate_frontmatter(content) [replace only]
  → Path.write_text()
  → _git_commit(vault_path, rel_path, message)
  → return confirmation
```

### Worker Delegation Path
```
Claude Code → MCP call → worker_server.py
  → route_task(): Ollama → OpenRouter free → OpenRouter paid → reject
  → budget.can_spend() check [paid only]
  → httpx POST to model endpoint
  → budget.record() [if cost > 0]
  → return result
```

### Discovery Path (resources)
```
MCP Client (Cursor/Windsurf/Claude) → list_resources()
  → 2 static URIs + 3 templates
MCP Client → read_resource("hive://projects/hive/context")
  → _resolve_file() → read + truncate → return ResourceResult
```

## Failure Modes

| Failure | Impact | Mitigation |
|---|---|---|
| Vault path missing | All vault tools return error strings | Config validation at startup; clear error messages |
| Git not initialized in vault | Write tools fail at `_git_commit` | `git_vault` fixture ensures git in tests; error propagates clearly |
| Ollama unreachable | Worker falls back to OpenRouter free | `is_available()` health check; 3-tier routing |
| OpenRouter API key missing | Worker limited to Ollama only | `OPENROUTER_API_KEY` alias for compat; clear error on paid route |
| Budget exhausted ($5/mo) | Paid worker requests rejected | `can_spend()` guard before every paid call |
| Malformed frontmatter on write | `vault_update` rejects with validation error | `validate_frontmatter()` checks required fields before write |
| File not found | Tool returns descriptive error string | `_resolve_file()` returns error string, not exception |
| Large output | Truncated with `[... truncated, N more lines]` notice | `_truncate()` applied consistently; `max_lines` param |
| Concurrent vault writes | Last write wins (no locking) | Acceptable for single-user; git history preserves all versions |

## Metrics (measured)

- **Token savings:** 67–82% reduction vs static CLAUDE.md loading
- **Test coverage:** 190 tests, 90% line coverage, mypy --strict clean
- **Code size:** ~760 statements across 7 modules
- **Smart search scoring:** `score = match_count × status_weight + recency_bonus`

## Consequences

### Positive

- Complete component inventory enables onboarding and audit
- Failure modes documented — no hidden assumptions
- MCP Resources enable non-Claude clients to discover vault content
- Session briefing eliminates cold-start friction

### Negative

- Architecture doc requires maintenance as features evolve
- No locking for concurrent writes (acceptable for single-user)

## References

- [adr-001-orchestration-model.md](adr-001-orchestration-model.md): Foundation decision (Claude Code as orchestrator)
- [MCP Specification](https://modelcontextprotocol.io/)
- [FastMCP 3.x docs](https://github.com/jlowin/fastmcp)
