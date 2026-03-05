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
│              (src/hive/server.py)                │
│                                                  │
│  ┌─────────────┐  ┌────────────┐  ┌──────────┐  │
│  │ Vault Tools  │  │Worker Tools│  │Resources │  │
│  │ (12 tools)   │  │ (3 tools)  │  │(5 URIs)  │  │
│  └──────┬──────┘  └─────┬──────┘  └──────────┘  │
│         │               │                        │
│  ┌──────▼──────┐  ┌─────▼──────┐                 │
│  │ frontmatter │  │  clients   │                 │
│  │  budget     │  │  budget    │                 │
│  │  usage      │  │  config    │                 │
│  └─────────────┘  └────────────┘                 │
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
| `server.py` | Unified FastMCP server — all tools, resources, prompts |
| `config.py` | pydantic-settings configuration with `HIVE_` env prefix |
| `frontmatter.py` | YAML frontmatter parsing, validation, and generation |
| `clients.py` | Async HTTP clients for Ollama and OpenRouter |
| `budget.py` | SQLite budget tracker with WAL mode ($1/mo default cap) |
| `usage.py` | Tool call analytics and token estimation |
| `__init__.py` | Package marker |

## Key Design Decisions

### Single Server

Vault and worker functionality are served from a single FastMCP instance. This simplifies MCP registration (one server instead of two) and allows tools to share state (e.g., usage tracking).

### Dependency Injection

`create_server()` accepts optional overrides for vault path, clients, and trackers. This enables testing without real infrastructure.

### Git Auto-Commit

All vault writes auto-commit to git. This provides full history and enables `vault_recent` to detect changes via `git log`.

### Budget Controls

Worker delegation uses a SQLite database with WAL mode for concurrent-safe budget tracking. Monthly caps and per-request limits prevent cost overruns.
