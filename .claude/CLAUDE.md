# Hive Project

> Vault-native AI orchestration: two MCP servers extending Claude Code.

## Architecture

- **Vault MCP Server** (`src/hive/vault_server.py`): On-demand Obsidian vault access
- **Worker MCP Server** (`src/hive/worker_server.py`): Task delegation to Ollama/Qwen

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
| `src/hive/vault_server.py` | Vault MCP server |
| `src/hive/worker_server.py` | Worker MCP server |
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
| `src/hive/budget.py` | SQLite budget tracker ($5/mo cap, WAL mode) |
| `src/hive/clients.py` | Async HTTP clients (Ollama + OpenRouter) |

## Worker Routing

1. Ollama `qwen2.5-coder:7b` (homelab mini PC) → primary, free
2. OpenRouter `qwen/qwen3-coder:free` → fallback, free tier
3. OpenRouter paid (DeepSeek) → if `max_cost_per_request > 0` and budget allows
4. Reject → return error, Claude handles it

## Verification Commands

```bash
# Lint
ruff check src/ tests/
mypy src/

# Tests
pytest tests/ -v --cov=hive

# Run vault server locally
python -m hive.vault_server

# Run worker server locally
python -m hive.worker_server
```
