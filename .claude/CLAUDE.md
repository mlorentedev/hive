# Hive Project

> Vault-native AI orchestration: unified MCP server extending Claude Code.

## Architecture

- **Hive MCP Server**: On-demand Obsidian vault access + worker delegation to Ollama/Qwen
- Modular: `server.py` (registration) + `_context.py` (state) + `_helpers.py` (pure functions) + domain modules (`_vault_read`, `_vault_write`, `_vault_health`, `_workers`)

See ADR: `~/Projects/knowledge/10_projects/hive/30-architecture/adr-001-orchestration-model.md`

## Technical Standards

| Requirement | Tool/Pattern |
|---|---|
| Python | 3.12+ |
| Type hints | mypy --strict |
| Dependencies | uv |
| Formatting | Ruff |
| Testing | pytest + pytest-cov |
| MCP framework | FastMCP |
| HTTP client | httpx (async) |

## Key Paths

| Path | Role |
|---|---|
| `src/hive/server.py` | Thin registration layer — resources, prompts, create_server() |
| `src/hive/_context.py` | ServerContext dataclass — shared state for tool handlers |
| `src/hive/_helpers.py` | Pure functions — path resolution, formatting, git ops, tracking |
| `src/hive/_vault_read.py` | vault_list, vault_query, vault_search, session_briefing |
| `src/hive/_vault_write.py` | vault_write, vault_patch |
| `src/hive/_vault_health.py` | vault_health, health report builder |
| `src/hive/_workers.py` | capture_lesson, delegate_task, worker_status |
| `src/hive/config.py` | Configuration (vault path, Ollama endpoint, OpenRouter key) |
| `tests/` | pytest suite |
| `~/Projects/knowledge/` | Obsidian vault (source of truth) |

## Vault Integration

- Vault path: `~/Projects/knowledge/`
- All vault writes MUST auto-commit to git (best-effort — never crash on git failure)
- All vault writes MUST validate YAML frontmatter
- Project vault entry: `~/Projects/knowledge/10_projects/hive/`

## MCP Tool Schema Rules

- **NEVER use `| None` in tool parameter types** — generates `anyOf` in JSON Schema, Claude Code drops these tools
- Use empty defaults: `str = ""`, `list[T] = []` (`# noqa: B006`), `int = 0`
- All subprocess calls MUST catch `Exception` (not just specific types)
- HTTP clients MUST catch `httpx.TimeoutException` (covers ReadTimeout, not just ConnectTimeout)

## Key Modules

| Module | Role |
|---|---|
| `src/hive/budget.py` | SQLite budget tracker ($1/mo default cap, WAL mode) |
| `src/hive/clients.py` | Async HTTP clients (Ollama + OpenRouter) |
| `src/hive/relevance.py` | EMA-based section relevance scoring |
| `src/hive/frontmatter.py` | YAML frontmatter parsing, validation, generation |

## Worker Routing

1. Ollama `qwen2.5-coder:7b` (homelab mini PC) → primary, free
2. OpenRouter `qwen/qwen3-coder:free` → fallback, free tier
3. OpenRouter paid (Qwen3 Coder) → if `max_cost_per_request > 0` and budget allows
4. Reject → return error, Claude handles it

## Verification Commands

All commands go through the Makefile:

```bash
make install    # Create venv + install deps
make lint       # Ruff linter
make typecheck  # mypy --strict
make test       # Unit + integration tests (excludes smoke)
make smoke      # E2E smoke tests (needs Ollama + OPENROUTER_API_KEY)
make check      # lint + typecheck + test
make build      # check + uv build
make run        # Run Hive MCP server locally
make clean      # Remove build artifacts
```
