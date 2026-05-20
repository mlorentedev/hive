# hive-vault

[![CI](https://github.com/mlorentedev/hive/actions/workflows/ci.yml/badge.svg)](https://github.com/mlorentedev/hive/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/mlorentedev/hive/graph/badge.svg)](https://codecov.io/gh/mlorentedev/hive)
[![PyPI](https://img.shields.io/pypi/v/hive-vault)](https://pypi.org/project/hive-vault/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)](https://python.org)
[![Docs](https://img.shields.io/badge/docs-hive-blue)](https://mlorentedev.github.io/hive/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

<a href="https://glama.ai/mcp/servers/mlorentedev/hive">
  <img width="380" height="200" src="https://glama.ai/mcp/servers/mlorentedev/hive/badge" />
</a>

<!-- mcp-name: io.github.mlorentedev/hive-vault -->

**Your AI coding assistant forgets everything between sessions. Hive fixes that.**

Hive is an [MCP](https://modelcontextprotocol.io/) server that connects your AI assistant to an [Obsidian](https://obsidian.md/) vault. Instead of loading everything upfront, it queries only what's needed — on demand.

| Metric | Without Hive | With Hive |
|---|---|---|
| Context loaded per session | ~800 lines (static) | ~50 lines (on demand) |
| Token cost for context | 100% every session | 6% average per query |
| Knowledge retained between sessions | 0% | 100% (in vault) |

> Measured on a real vault with 19 projects, 200+ files. See [benchmarks](https://mlorentedev.github.io/hive/guides/benchmarks/).

## Quick Start

Hive runs without a vault — vault tools return a friendly error until `VAULT_PATH` is set, so you can install first and configure later.

```bash
# Minimal — uses default vault path ~/Projects/knowledge
claude mcp add -s user hive -- uvx --upgrade hive-vault

# With a custom vault path
claude mcp add -s user hive -e VAULT_PATH=$HOME/path/to/vault -- uvx --upgrade hive-vault

# Gemini CLI
gemini mcp add -s user -e VAULT_PATH=$HOME/path/to/vault hive-vault uvx -- --upgrade hive-vault
```

> Default vault path: `~/Projects/knowledge`. Override with `VAULT_PATH` (or `HIVE_VAULT_PATH`) as shown above.

For Codex CLI, GitHub Copilot, Cursor, Windsurf, and other clients, see [Getting Started](https://mlorentedev.github.io/hive/getting-started/).

Then ask your assistant: *"Use vault_list to see my vault"*

## Tools

| Tool | What it does |
|---|---|
| `vault_query` | Load project context, tasks, roadmap, lessons — or any file by path |
| `vault_search` | Full-text search with metadata filters, regex, ranked results, recent changes, lesson-usage ranking (`rank_by`) |
| `vault_list` | Browse projects and files with glob filtering |
| `vault_health` | Health metrics, drift detection, usage stats |
| `vault_write` | Create, append, or replace vault files with auto git commit |
| `vault_patch` | Surgical find-and-replace with auto git commit |
| `capture_lesson` | Capture lessons inline / batch-extract from text / look up existing lessons by keyword (`find=`) |
| `session_briefing` | Tasks + lessons + git log + health in one call |
| `delegate_task` | Route tasks to cheaper models or summarize vault files |
| `worker_status` | Budget, connectivity, available models |

Plus 5 [resources](https://mlorentedev.github.io/hive/reference/resources/) and 4 [prompts](https://mlorentedev.github.io/hive/guides/prompts/) for guided workflows.

### Lesson reinforcement

Every read of a lesson via `vault_query`, `vault_search`, or `capture_lesson(find=…)` increments a counter and grows that lesson's confidence asymptotically toward 1.0. Validated lessons rank higher than one-shot captures over time.

```bash
# Surface the top-ranked lessons matching a keyword
capture_lesson(project="hive", find="multi-process")

# Search lessons ranked by usage signal (not BM25)
vault_search(query="timeout", rank_by="reinforcements")    # most-reinforced first
vault_search(query="timeout", rank_by="confidence")        # highest decayed confidence
vault_search(query="timeout", rank_by="hybrid")            # α=0.7 BM25 + 0.3 confidence
```

Storage: SQLite side-table at `HIVE_LESSON_DB_PATH` (default `~/.local/share/hive/lesson_reinforcement.db`). WAL mode + busy_timeout make it cross-process safe.

## Architecture

```
MCP Host (Claude Code, Gemini CLI, Codex CLI, Cursor, ...)
    └── hive-vault (MCP server, stdio)
            ├── Vault Tools (7) ── Obsidian vault (Markdown + YAML frontmatter)
            ├── Session Tools (1) ── Adaptive context assembly
            └── Worker Tools (2) ── Ollama (free) → OpenRouter free → paid ($1/mo cap) → reject
```

## Documentation

Full documentation at **[mlorentedev.github.io/hive](https://mlorentedev.github.io/hive/)**:

- [Getting Started](https://mlorentedev.github.io/hive/getting-started/) — install for all MCP clients
- [Configuration](https://mlorentedev.github.io/hive/configuration/) — all 19 environment variables
- [Vault Structure](https://mlorentedev.github.io/hive/guides/vault-structure/) — how to organize your vault
- [Use Cases](https://mlorentedev.github.io/hive/guides/use-cases/) — real-world workflows
- [Architecture](https://mlorentedev.github.io/hive/reference/architecture/) — module map and design decisions
- [Troubleshooting](https://mlorentedev.github.io/hive/guides/troubleshooting/) — common issues and fixes

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and PR workflow.

```bash
git clone https://github.com/mlorentedev/hive.git && cd hive
make install   # create venv + install deps
make check     # lint + typecheck + test (478 tests, 90% coverage)
```

## License

[MIT](LICENSE)
