---
title: Architecture
description: System architecture and module map.
---

## System Overview

```
┌─────────────────────────────────────────────────┐
│              MCP Host (any client)                │
│         Claude Code, Codex CLI, Cursor, ...      │
└──────────────────────┬──────────────────────────┘
                       │ MCP (stdio)
┌──────────────────────▼──────────────────────────┐
│                 Hive MCP Server                  │
│                                                  │
│  server.py (registration + resources + prompts)  │
│  _context.py (ServerContext shared state)        │
│                                                  │
│  ┌─────────────┐  ┌────────────┐  ┌──────────┐  │
│  │ Vault+Session│  │Worker Tools│  │Resources │  │
│  │ _vault_read  │  │ _workers   │  │(5 URIs)  │  │
│  │ _vault_write │  │ (2 tools)  │  └──────────┘  │
│  │ _vault_health│  └─────┬──────┘                │
│  │ (8 tools)    │        │                       │
│  └──────┬──────┘  ┌─────▼──────┐                 │
│         │         │  clients   │                 │
│  ┌──────▼──────┐  │  budget    │                 │
│  │ _helpers    │  │  config    │                 │
│  │ frontmatter │  └────────────┘                 │
│  │ usage       │                                 │
│  └─────────────┘                                 │
└──────────────────────────────────────────────────┘
         │                    │
    ┌────▼────┐    ┌─────────▼──────────┐
    │ Obsidian │    │   Ollama (local)    │
    │  Vault   │    │   OpenRouter (cloud)│
    └─────────┘    └────────────────────┘
```

## Module Map

| Module | Role |
|---|---|
| `server.py` | Thin registration layer — resources, prompts, `create_server()` |
| `_context.py` | `ServerContext` dataclass — shared state for all tool handlers |
| `_helpers.py` | Pure functions — path resolution, formatting, git ops, tracking |
| `_vault_read.py` | Read tools — `vault_list`, `vault_query`, `vault_search`, `session_briefing` |
| `_vault_write.py` | Write tools — `vault_write`, `vault_patch` |
| `_vault_health.py` | Health tools — `vault_health`, health report builder |
| `_workers.py` | Worker tools — `capture_lesson`, `delegate_task`, `worker_status` |
| `config.py` | pydantic-settings configuration with `HIVE_` env prefix |
| `frontmatter.py` | YAML frontmatter parsing, validation, and generation |
| `clients.py` | Async HTTP clients for Ollama and OpenRouter |
| `budget.py` | SQLite budget tracker with WAL mode ($1/mo default cap) |
| `relevance.py` | EMA-based section relevance scoring for adaptive context |
| `usage.py` | Tool call analytics and token estimation |

## Key Design Decisions

### Single Server, Modular Internals

Vault and worker functionality are served from a single FastMCP instance. Internally, tools are organized into domain modules (`_vault_read`, `_vault_write`, `_vault_health`, `_workers`) that register themselves via `register_*(mcp, ctx)` functions. Shared state lives in a `ServerContext` dataclass (`_context.py`), and pure utility functions live in `_helpers.py`.

### Dependency Injection

`create_server()` accepts optional overrides for vault path, clients, and trackers. This enables testing without real infrastructure.

### Git Auto-Commit

All vault writes auto-commit to git. This provides full history and enables `vault_search(since_days=N)` to detect changes via `git log`.

### Budget Controls

Worker delegation uses a SQLite database with WAL mode for concurrent-safe budget tracking. Monthly caps and per-request limits prevent cost overruns.
