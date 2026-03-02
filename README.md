# hive-vault

[![CI](https://github.com/mlorentedev/hive/actions/workflows/ci.yml/badge.svg)](https://github.com/mlorentedev/hive/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/hive-vault)](https://pypi.org/project/hive-vault/)
[![codecov](https://codecov.io/gh/mlorentedev/hive/graph/badge.svg)](https://codecov.io/gh/mlorentedev/hive)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Context infrastructure for AI-assisted development — on-demand Obsidian vault access via MCP.

## The Problem

AI coding assistants load context statically. A typical `CLAUDE.md` grows to 800+ lines of standards, patterns, and project knowledge. Most of it is irrelevant to the current task. Every session pays the full token cost.

## The Solution

Hive replaces static context with **on-demand vault queries** via the [Model Context Protocol](https://modelcontextprotocol.io/). Your knowledge stays in an Obsidian vault. Claude Code loads only what it needs, when it needs it.

**Measured result:** 67-82% token reduction on targeted queries vs static context loading.

## Install

```bash
claude mcp add --transport stdio hive-vault --scope user -- uvx hive-vault
```

That's it. No cloning, no venv, no setup. `uvx` handles everything.

## Usage

Once registered, Claude Code can use these tools:

```python
# Load project context on demand
vault_query(project="my-project", section="context")    # project overview
vault_query(project="my-project", section="tasks")       # active backlog
vault_query(project="my-project", section="roadmap")     # strategy

# Search across all knowledge
vault_search(query="authentication pattern")

# Access cross-project patterns
vault_query(project="_meta", path="patterns/pattern-language-standards.md")

# Write back lessons and decisions
vault_update(project="my-project", section="lessons", operation="append", content="...")
vault_create(project="my-project", path="30-architecture/adr-003.md", content="...", doc_type="adr")

# Health check
vault_health()
vault_list_projects()
```

## Vault Structure

Hive expects an Obsidian vault with this layout:

```
~/Projects/knowledge/          # vault root (configurable via VAULT_PATH)
├── 00_meta/
│   └── patterns/              # cross-project patterns
├── 10_projects/
│   ├── my-project/
│   │   ├── 00-context.md      # section shortcut: "context"
│   │   ├── 10-roadmap.md      # section shortcut: "roadmap"
│   │   ├── 11-tasks.md        # section shortcut: "tasks"
│   │   ├── 90-lessons.md      # section shortcut: "lessons"
│   │   └── 30-architecture/   # arbitrary paths
│   └── another-project/
└── ...
```

## Configuration

| Environment Variable | Default | Description |
|---|---|---|
| `VAULT_PATH` | `~/Projects/knowledge` | Path to your Obsidian vault |

## Architecture

```
Claude Code (orchestrator)
    └── hive-vault (MCP server, stdio)
            ├── vault_query    — read sections or files on demand
            ├── vault_search   — full-text search across vault
            ├── vault_update   — write with YAML frontmatter validation + auto git commit
            ├── vault_create   — new files with auto-generated frontmatter
            ├── vault_health   — project health metrics
            └── vault_list_projects — discover available projects
```

## Development

```bash
git clone https://github.com/mlorentedev/hive.git
cd hive
uv venv && uv pip install -e ".[dev]"

# Quality checks
uv run ruff check src/ tests/
uv run mypy src/
uv run pytest tests/ -v --cov=hive
```

## License

MIT
