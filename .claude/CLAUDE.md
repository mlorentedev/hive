# Hive Project

> Vault-native AI orchestration: unified MCP server extending Claude Code.

## Architecture

- **Hive MCP Server** (`src/hive/server.py`): On-demand Obsidian vault access + worker delegation to Ollama/Qwen

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
| `src/hive/server.py` | Unified Hive MCP server (vault + worker) |
| `src/hive/config.py` | Configuration (vault path, Ollama endpoint, OpenRouter key) |
| `tests/` | pytest suite |
| `~/Projects/knowledge/` | Obsidian vault (source of truth) |

## Vault Integration

- Vault path: `~/Projects/knowledge/`
- All vault writes MUST auto-commit to git
- All vault writes MUST validate YAML frontmatter
- Project vault entry: `~/Projects/knowledge/10_projects/hive/`

## Key Modules

| Module | Role |
|---|---|
| `src/hive/budget.py` | SQLite budget tracker ($1/mo default cap, WAL mode) |
| `src/hive/clients.py` | Async HTTP clients (Ollama + OpenRouter) |

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
