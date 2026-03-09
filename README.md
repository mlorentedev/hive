# hive-vault

[![CI](https://github.com/mlorentedev/hive/actions/workflows/ci.yml/badge.svg)](https://github.com/mlorentedev/hive/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/hive-vault)](https://pypi.org/project/hive-vault/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)](https://python.org)
[![Docs](https://img.shields.io/badge/docs-hive-blue)](https://mlorentedev.github.io/hive/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

<!-- mcp-name: io.github.mlorentedev/hive-vault -->

**Your AI coding assistant forgets everything between sessions. Hive fixes that.**

Every session, your assistant loads 800+ lines of static context. Most of it is irrelevant. You pay the full token cost every time. And next session? It starts from zero again.

Hive is an MCP server that connects your AI assistant to an [Obsidian](https://obsidian.md/) vault. Instead of loading everything upfront, it queries only what's needed — architecture decisions, lessons learned, project context — all on demand via [MCP](https://modelcontextprotocol.io/).

**The numbers:**

| Metric | Without Hive | With Hive |
|---|---|---|
| Context loaded per session | ~800 lines (static) | ~50 lines (on demand) |
| Token cost for context | 100% every session | 6% average per query |
| Knowledge retained between sessions | 0% | 100% (in vault) |
| Time to find past decisions | Manual search | `vault_search` in seconds |

> Measured on a real vault with 19 projects, 200+ files. See [benchmarks](https://mlorentedev.github.io/hive/guides/benchmarks/).

## Install (30 seconds)

One command. No cloning, no venv, no config files. **Use user scope (`-s user`) so Hive works across all your projects** — that's where cross-project knowledge shines.

**Claude Code:**
```bash
claude mcp add -s user hive -- uvx --upgrade hive-vault
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

**GitHub Copilot (VS Code)** — add to `.vscode/mcp.json`:
```json
{
  "servers": {
    "hive-vault": {
      "command": "uvx",
      "args": ["--upgrade", "hive-vault"]
    }
  }
}
```

**Other MCP clients** (Cursor, Windsurf, etc.): point your client at `uvx --upgrade hive-vault` via stdio transport.

Then ask your assistant:

> "Use vault_list to see my vault"

That's it. You're running.

## What You Get

### 10 Tools — your knowledge, on demand

| Tool | What it does |
|---|---|
| `vault_query` | Load project context, tasks, roadmap, lessons — or any file by path |
| `vault_search` | Full-text search with metadata filters, regex, ranked results, and recent changes |
| `vault_list` | List projects (no args) or browse files within a project with glob filtering |
| `vault_health` | Health metrics, drift detection (frontmatter, stale, wikilinks), and usage stats |
| `vault_write` | Create, append, or replace vault files with YAML validation + auto git commit |
| `vault_patch` | Surgical find-and-replace with ambiguity rejection + auto git commit |
| `capture_lesson` | Capture lessons inline (structured) or batch-extract from text via worker |
| `session_briefing` | One call = tasks + lessons + git log + health. Start every session here |
| `delegate_task` | Route tasks to cheaper models, or summarize vault files automatically |
| `worker_status` | Budget remaining, connectivity, available models, usage stats |

**Worker routing:** Ollama first (free) → OpenRouter free tier → OpenRouter paid ($1/mo cap) → reject.

Your primary model handles architecture. Cheaper models handle boilerplate. `capture_lesson(text=...)` uses workers to batch-extract lessons from session notes — saving your primary model's tokens.

## Before / After

**Before Hive** — static CLAUDE.md:
```markdown
# My Project
## Architecture
[200 lines of decisions you made 3 months ago]
## Standards
[150 lines of coding patterns]
## Lessons
[100 lines of past bugs]
## Tasks
[50 lines of backlog]
# ...loaded every single session, whether relevant or not
```

**With Hive** — dynamic, on demand:
```python
# Only when the assistant needs architecture context:
vault_query(project="my-project", section="context")

# Only when searching for a past decision:
vault_search(query="database migration strategy")

# Start of session — just the essentials:
session_briefing(project="my-project")
```

## Configure Your Vault

Default vault path: `~/Projects/knowledge`. To change it:

```bash
# Claude Code
claude mcp add -s user hive -e VAULT_PATH=/path/to/vault -- uvx --upgrade hive-vault

# Gemini CLI
gemini mcp add -s user -e VAULT_PATH=/path/to/vault hive-vault uvx -- --upgrade hive-vault
```

### Enable Worker Delegation (optional)

```bash
claude mcp add -s user hive \
  -e VAULT_PATH=/path/to/vault \
  -e HIVE_OLLAMA_ENDPOINT=http://your-ollama:11434 \
  -e OPENROUTER_API_KEY=sk-or-... \
  -- uvx --upgrade hive-vault
```

### All Configuration

| Variable | Default | Description |
|---|---|---|
| `VAULT_PATH` | `~/Projects/knowledge` | Path to your Obsidian vault |
| `HIVE_OLLAMA_ENDPOINT` | `http://localhost:11434` | Ollama API endpoint |
| `HIVE_OLLAMA_MODEL` | `qwen2.5-coder:7b` | Default Ollama model |
| `HIVE_OPENROUTER_API_KEY` | — | OpenRouter API key (also reads `OPENROUTER_API_KEY`) |
| `HIVE_OPENROUTER_MODEL` | `qwen/qwen3-coder:free` | Default free tier model |
| `HIVE_OPENROUTER_PAID_MODEL` | `qwen/qwen3-coder` | Paid tier model |
| `HIVE_OPENROUTER_BUDGET` | `1.0` | Monthly budget cap (USD) |
| `HIVE_VAULT_SCOPES` | `{"projects": "10_projects", "meta": "00_meta"}` | JSON mapping of scope names to vault subdirectories |
| `HIVE_LOG_PATH` | `~/.local/share/hive/hive.log` | Persistent debug log (1MB rotating, 1 backup) |

See [full configuration reference](https://mlorentedev.github.io/hive/configuration/) for all 16 environment variables including advanced tuning.

### Debug Logging

Hive writes warnings and errors to a persistent log file for post-mortem debugging:

```
~/.local/share/hive/hive.log
```

Check this file when tools return unexpected results or the server fails silently. The log rotates at 1MB with one backup file. Override the path with `HIVE_LOG_PATH`.

## Recommended Workflow

The highest-value setup combines three tools:

1. **[Obsidian](https://obsidian.md/)** — local-first knowledge base with 1M+ community, Markdown native, no lock-in
2. **[Obsidian Git](https://github.com/Vinzent03/obsidian-git)** — auto-commits your vault changes on a schedule (version history for free)
3. **Hive** — bridges your vault to any AI coding assistant via MCP

Your assistant writes lessons and decisions to the vault → Obsidian Git auto-commits → next session, everything is there. No manual sync. No context lost.

> Hive works with any directory of Markdown files — Obsidian is recommended, not required.

## Vault Structure

For best results, follow this layout:

```
~/Projects/knowledge/
├── 00_meta/patterns/          # cross-project patterns
├── 10_projects/
│   ├── my-project/
│   │   ├── 00-context.md      # vault_query section="context"
│   │   ├── 10-roadmap.md      # vault_query section="roadmap"
│   │   ├── 11-tasks.md        # vault_query section="tasks"
│   │   ├── 90-lessons.md      # vault_query section="lessons"
│   │   └── 30-architecture/   # any path works with vault_query path="..."
│   └── another-project/
└── ...
```

## Make Your Assistant Use Hive Consistently

MCP tools don't activate on their own. Add this to your project's `CLAUDE.md` (or equivalent):

```markdown
## Vault & Knowledge (Hive MCP)

When hive-vault MCP is available:
- `session_briefing(project="myproject")` — start every session here
- `vault_query(project="myproject", section="context")` — project overview
- `vault_search(query="...")` — find past decisions
- `capture_lesson(...)` — capture insights inline, don't wait until session end
```

Without these instructions, your assistant uses Hive inconsistently. With them, it uses Hive **every session, predictably**.

## Resources & Prompts

**5 MCP Resources** for auto-discoverable data:

| URI | Description |
|---|---|
| `hive://projects` | All vault projects with file counts |
| `hive://health` | Vault health metrics |
| `hive://projects/{project}/context` | Project context |
| `hive://projects/{project}/tasks` | Project backlog |
| `hive://projects/{project}/lessons` | Lessons learned |

**4 MCP Prompts** for guided workflows:

| Prompt | Description |
|---|---|
| `retrospective` | End-of-session review → extract lessons to vault |
| `delegate` | Structured protocol for worker delegation |
| `vault_sync` | Post-sprint vault sync — reconcile docs with shipped code |
| `benchmark` | Estimate token savings from Hive in the current session |

## Architecture

```
MCP Host (Claude Code, Gemini CLI, Codex CLI, Cursor, ...)
    └── hive-vault (MCP server, stdio)
            ├── Vault Tools (7) ── Obsidian vault (Markdown + YAML frontmatter)
            │     query, search, list, health, write, patch,
            │     capture_lesson
            │
            ├── Session Tools (1) ── Adaptive context assembly
            │     session_briefing
            │
            └── Worker Tools (2) ── Task delegation + routing:
                  delegate_task        1. Ollama (local, free)
                  worker_status        2. OpenRouter free tier
                                       3. OpenRouter paid ($1/mo cap)
                                       4. Reject → host handles it
```

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, code standards, and PR workflow.

```bash
git clone https://github.com/mlorentedev/hive.git
cd hive
make install   # create venv + install deps
make check     # lint + typecheck + test (324 tests, 91% coverage)
```

## License

MIT
