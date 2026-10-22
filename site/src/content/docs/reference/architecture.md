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
│  │ _vault_write │  │ (3 tools)  │  └──────────┘  │
│  │ _vault_health│  └─────┬──────┘                │
│  │ (10 tools)   │        │                       │
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
    │ Obsidian │    │  OpenAI-compatible  │
    │  Vault   │    │  endpoint (/v1)     │
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
| `_vault_ask.py` | Semantic Q&A tool — `vault_ask` (optional; disabled until an embeddings endpoint is configured) |
| `_semantic.py` | Chunking and persisted embedding index behind `vault_ask` (needs the `[semantic]` extra) |
| `_vault_health.py` | Health tools — `vault_health`, health report builder |
| `_workers.py` | Worker tools — `capture_lesson`, `delegate_task`, `worker_status` |
| `config.py` | pydantic-settings configuration with `HIVE_` env prefix |
| `frontmatter.py` | YAML frontmatter parsing, validation, and generation |
| `clients.py` | Async HTTP client for OpenAI-compatible endpoints (worker, embeddings, synthesis) |
| `budget.py` | SQLite usage telemetry for worker requests (requests, tokens, latency) with WAL mode. The name is historical: the spend cap it once enforced was removed in 4.0.0 |
| `relevance.py` | EMA-based section relevance scoring for adaptive context |
| `usage.py` | Tool call analytics and token estimation |

## Key Design Decisions

### Single Server, Modular Internals

Vault and worker functionality are served from a single FastMCP instance. Internally, tools are organized into domain modules (`_vault_read`, `_vault_write`, `_vault_health`, `_workers`) that register themselves via `register_*(mcp, ctx)` functions. Shared state lives in a `ServerContext` dataclass (`_context.py`), and pure utility functions live in `_helpers.py`.

### Dependency Injection

`create_server()` accepts optional overrides for vault path, clients, and trackers. This enables testing without real infrastructure.

### Git Auto-Commit

All vault writes reach git, which is what gives full history and lets `vault_search(since_days=N)` detect changes via `git log`. Since the asynchronous commit queue landed they reach it *asynchronously*: a write returns as soon as the file is on disk, and a reconciler thread commits the queued paths on the next tick (`HIVE_COMMIT_TICK_S`). Commit rate follows the tick rather than write volume.

Bulk flows need no special handling any more — batching is what the queue does by default. `commit=True` forces a synchronous commit for callers that need it confirmed, and `vault_commit(message=...)` still flushes the working tree in one shot. `vault_delete` stays synchronous and out of the queue, so a delete and a recreate cannot collapse into one tick. When the obsidian-git plugin is detected in the vault (via `.obsidian/plugins/obsidian-git/data.json`), `vault_health` surfaces an `external_committer` block and the reconciler hands off to it at drain time instead of committing.

### One Worker, Classified Failures

Worker delegation makes one attempt against a single OpenAI-compatible endpoint; there is no internal fallback chain, because a backend that falls back on its own is a second routing authority. Failures are classified as `pool_unavailable` (the endpoint did not serve the request) or `task_failed` (it answered and the answer is unusable), so the caller that owns a routing map can decide whether to retry elsewhere. Usage telemetry — requests, tokens and latency per model — is recorded in a SQLite database with WAL mode and an explicit `threading.Lock` for thread-safe concurrent access. There is no spend cap.
