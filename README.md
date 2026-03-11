# hive-vault

[![CI](https://github.com/mlorentedev/hive/actions/workflows/ci.yml/badge.svg)](https://github.com/mlorentedev/hive/actions/workflows/ci.yml)
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

```bash
# Claude Code
claude mcp add -s user hive -- uvx --upgrade hive-vault

# Gemini CLI
gemini mcp add -s user hive-vault uvx -- --upgrade hive-vault
```

For Codex CLI, GitHub Copilot, Cursor, Windsurf, and other clients, see [Getting Started](https://mlorentedev.github.io/hive/getting-started/).

Then ask your assistant: *"Use vault_list to see my vault"*

## Tools

| Tool | What it does |
|---|---|
| `vault_query` | Load project context, tasks, roadmap, lessons — or any file by path |
| `vault_search` | Full-text search with metadata filters, regex, ranked results, recent changes |
| `vault_list` | Browse projects and files with glob filtering |
| `vault_health` | Health metrics, drift detection, usage stats |
| `vault_write` | Create, append, or replace vault files with auto git commit |
| `vault_patch` | Surgical find-and-replace with auto git commit |
| `capture_lesson` | Capture lessons inline or batch-extract from text via worker |
| `session_briefing` | Tasks + lessons + git log + health in one call |
| `delegate_task` | Route tasks to cheaper models or summarize vault files |
| `worker_status` | Budget, connectivity, available models |

Plus 5 [resources](https://mlorentedev.github.io/hive/reference/resources/) and 4 [prompts](https://mlorentedev.github.io/hive/guides/prompts/) for guided workflows.

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
- [Configuration](https://mlorentedev.github.io/hive/configuration/) — all 16 environment variables
- [Vault Structure](https://mlorentedev.github.io/hive/guides/vault-structure/) — how to organize your vault
- [Use Cases](https://mlorentedev.github.io/hive/guides/use-cases/) — real-world workflows
- [Architecture](https://mlorentedev.github.io/hive/reference/architecture/) — module map and design decisions
- [Troubleshooting](https://mlorentedev.github.io/hive/guides/troubleshooting/) — common issues and fixes

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and PR workflow.

```bash
git clone https://github.com/mlorentedev/hive.git && cd hive
make install   # create venv + install deps
make check     # lint + typecheck + test (337 tests, 92% coverage)
```

## License

[MIT](LICENSE)
