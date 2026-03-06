# hive-vault

[![CI](https://github.com/mlorentedev/hive/actions/workflows/ci.yml/badge.svg)](https://github.com/mlorentedev/hive/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/hive-vault)](https://pypi.org/project/hive-vault/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Unified MCP server for AI-assisted development — on-demand Obsidian vault access + worker delegation to local/cloud models.

## The Problem

AI coding assistants load context statically. A typical `CLAUDE.md` grows to 800+ lines of standards, patterns, and project knowledge. Most of it is irrelevant to the current task. Every session pays the full token cost.

## The Solution

Hive replaces static context with **on-demand vault queries** via the [Model Context Protocol](https://modelcontextprotocol.io/). Your knowledge stays in an Obsidian vault. Your AI assistant loads only what it needs, when it needs it.

For tasks that don't need your primary model's full reasoning, Hive **delegates to cheaper models** (local Ollama or cloud OpenRouter) with automatic routing and budget controls.

**Works with any MCP client:** Claude Code, Codex CLI, Gemini CLI, Cursor, Windsurf, and others.

**Measured result:** 67–82% token reduction on targeted queries vs static context loading.

## Quick Start

**Claude Code:**
```bash
claude mcp add hive -- uvx --upgrade hive-vault
```

**Gemini CLI:**
```bash
gemini mcp add -s user hive-vault uvx -- --upgrade hive-vault
```

**OpenAI Codex CLI** — add to `~/.codex/config.toml`:
```toml
[mcp_servers.hive-vault]
command = "uvx"
args = ["--upgrade", "hive-vault"]
```

The `--upgrade` flag ensures you always get the latest version from PyPI on each session start.

**Other MCP clients:** point your client's MCP config at `uvx --upgrade hive-vault` via stdio transport.

To configure the vault path (defaults to `~/Projects/knowledge`):

```bash
# Claude Code
claude mcp add hive -e VAULT_PATH=/path/to/your/vault -- uvx --upgrade hive-vault

# Gemini CLI
gemini mcp add -s user -e VAULT_PATH=/path/to/your/vault hive-vault uvx -- --upgrade hive-vault
```

**Manual update** (if you prefer not to auto-upgrade on every launch):
```bash
uv tool upgrade hive-vault
```

## Tools

### Vault Tools (14)

| Tool | Description |
|---|---|
| `vault_list_projects` | List all projects in the Obsidian vault |
| `vault_query` | Read sections or files on demand (supports shortcuts: context, tasks, roadmap, lessons) |
| `vault_search` | Full-text search across the vault with metadata filters + optional regex |
| `vault_health` | Health metrics for all vault projects (file counts, staleness, coverage) |
| `vault_update` | Write to vault with YAML frontmatter validation + auto git commit |
| `vault_create` | Create new files with auto-generated frontmatter + auto git commit |
| `vault_list_files` | List files and directories with optional glob pattern filtering |
| `vault_patch` | Surgical text replacement in a vault file with auto git commit |
| `capture_lesson` | Capture a lesson learned inline during a session — appends to 90-lessons.md |
| `vault_summarize` | Smart summarization — returns small files directly, delegates large ones |
| `vault_smart_search` | Ranked search with relevance scoring (status weight + recency + match density) |
| `session_briefing` | One-call context briefing: tasks + lessons + git log + health |
| `vault_recent` | Files changed in the vault in the last N days (git + frontmatter) |
| `vault_usage` | Tool usage analytics — call counts, token estimates, breakdowns |

### Worker Tools (3)

| Tool | Description |
|---|---|
| `delegate_task` | Route tasks to cheaper models with automatic tier selection |
| `list_models` | List available models across all providers |
| `worker_status` | Worker health: budget remaining, connectivity, usage stats |

## Resources

| URI | Description |
|---|---|
| `hive://projects` | All vault projects with file counts and available shortcuts |
| `hive://health` | Vault health metrics for all projects |
| `hive://projects/{project}/context` | Project context document (00-context.md) |
| `hive://projects/{project}/tasks` | Project task backlog (11-tasks.md) |
| `hive://projects/{project}/lessons` | Project lessons learned (90-lessons.md) |

## Prompts

| Prompt | Description |
|---|---|
| `retrospective` | End-of-session review — extracts lessons and appends to vault |
| `delegate` | Structured protocol for delegating tasks to cheaper models |
| `vault_sync` | Post-sprint vault synchronization — reconcile docs with shipped code |
| `benchmark` | Estimate token savings from hive MCP tools in the current session |

## Configuration

| Variable | Default | Description |
|---|---|---|
| `VAULT_PATH` | `~/Projects/knowledge` | Path to your Obsidian vault |
| `HIVE_OLLAMA_ENDPOINT` | `http://localhost:11434` | Ollama API endpoint |
| `HIVE_OLLAMA_MODEL` | `qwen2.5-coder:7b` | Default Ollama model |
| `HIVE_OPENROUTER_API_KEY` | — | OpenRouter API key (also reads `OPENROUTER_API_KEY`) |
| `HIVE_OPENROUTER_MODEL` | `qwen/qwen3-coder:free` | Default OpenRouter model (free tier) |
| `HIVE_OPENROUTER_PAID_MODEL` | `qwen/qwen3-coder` | Paid tier model for delegate_task |
| `HIVE_OPENROUTER_BUDGET` | `1.0` | Monthly budget cap in USD |
| `HIVE_DB_PATH` | `~/.local/share/hive/worker.db` | SQLite database for budget tracking |
| `HIVE_RELEVANCE_DB_PATH` | `~/.local/share/hive/relevance.db` | SQLite database for adaptive context scoring |
| `HIVE_STALE_THRESHOLD_DAYS` | `180` | Days before a vault file is flagged as stale |
| `HIVE_HTTP_TIMEOUT` | `120.0` | HTTP timeout (seconds) for Ollama and OpenRouter |
| `HIVE_RELEVANCE_ALPHA` | `0.3` | EMA learning rate for adaptive context scoring |
| `HIVE_RELEVANCE_DECAY` | `0.9` | Session decay factor for relevance scores |
| `HIVE_RELEVANCE_EPSILON` | `0.15` | Exploration ratio for session_briefing |

## Architecture

```
MCP Host (Claude Code, Codex CLI, Cursor, ...)
    └── hive (MCP server, stdio)
            ├── Vault Tools ──── Obsidian vault (~/Projects/knowledge/)
            │     query, search, update, create, capture_lesson,
            │     summarize, smart_search, briefing, recent, usage, health
            │
            └── Worker Tools ─── delegate_task → routing:
                  list_models        1. Ollama (local, free)
                  worker_status      2. OpenRouter free tier
                                     3. OpenRouter paid ($1/mo cap)
                                     4. Reject → host handles it
```

## Maximizing Hive with CLAUDE.md

MCP servers don't activate themselves — your AI assistant needs guidance on **when** and **how** to use each tool. The `CLAUDE.md` file (or equivalent in your MCP client) is the key lever.

Add instructions like these to your project's `CLAUDE.md`:

```markdown
## Vault & Knowledge (Hive MCP)

When hive-vault MCP is available, use it for on-demand context:
- `vault_query(project="myproject", section="context")` — project overview
- `vault_query(project="myproject", section="tasks")` — active backlog
- `vault_search(query="...")` — cross-vault search
- `session_briefing(project="myproject")` — full context in one call

When writing to the vault: lessons → `90-lessons.md`, decisions → `30-architecture/`.

When you discover a lesson (bug root cause, architectural insight, debugging trick):
- `capture_lesson(project="myproject", title="...", context="...", problem="...", solution="...")`
- Don't wait until session end — capture inline when the insight is fresh.
```

Without these instructions, your assistant *might* use Hive, but inconsistently. With them, it uses Hive **predictably** for every relevant query.

**How it works:** Your MCP client loads all available tools at session start. The assistant sees tool names and descriptions, but `CLAUDE.md` instructions tell it which tools to prefer for which situations. Multiple MCP servers coexist — they don't compete. Each serves its domain, and your instructions guide the routing.

## Worker Routing

Tasks are routed through a tiered system that minimizes cost:

1. **Ollama** (local) — Free. Runs on homelab hardware. Best for trivial tasks.
2. **OpenRouter free** — Free tier models (Qwen3 Coder 480B). Real code work.
3. **OpenRouter paid** — Qwen3 Coder ($0.22/M input, $1.00/M output). Only when `max_cost_per_request > 0` and monthly budget allows.
4. **Reject** — Returns error so the host handles the task directly.

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

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, code standards, and PR workflow.

```bash
git clone https://github.com/mlorentedev/hive.git
cd hive
make install   # create venv + install deps
make check     # lint + typecheck + test
```

## License

MIT
