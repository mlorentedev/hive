---
title: Configuration
description: Environment variables for Hive MCP server.
---

All configuration is done through environment variables, passed when registering the MCP server.

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `VAULT_PATH` | `~/Projects/knowledge` | Path to your Obsidian vault |
| `HIVE_OLLAMA_ENDPOINT` | `http://localhost:11434` | Ollama API endpoint |
| `HIVE_OLLAMA_MODEL` | `qwen2.5-coder:7b` | Default Ollama model |
| `HIVE_OPENROUTER_API_KEY` | — | OpenRouter API key |
| `HIVE_OPENROUTER_MODEL` | `qwen/qwen3-coder:free` | Default OpenRouter model (free tier) |
| `HIVE_OPENROUTER_PAID_MODEL` | `qwen/qwen3-coder` | Paid tier model for delegate_task |
| `HIVE_OPENROUTER_BUDGET` | `1.0` | Monthly budget cap in USD |
| `HIVE_DB_PATH` | `~/.local/share/hive/worker.db` | SQLite database for budget/usage tracking |
| `HIVE_RELEVANCE_DB_PATH` | `~/.local/share/hive/relevance.db` | SQLite database for adaptive context scoring |
| `HIVE_STALE_THRESHOLD_DAYS` | `180` | Days before a vault file is flagged as stale |
| `HIVE_HTTP_TIMEOUT` | `120.0` | HTTP timeout (seconds) for Ollama and OpenRouter |
| `HIVE_RELEVANCE_ALPHA` | `0.3` | EMA learning rate for adaptive context scoring |
| `HIVE_RELEVANCE_DECAY` | `0.9` | Session decay factor for relevance scores |
| `HIVE_RELEVANCE_EPSILON` | `0.15` | Exploration ratio for session_briefing (epsilon-greedy) |
| `HIVE_VAULT_SCOPES` | `{"projects": "10_projects", "meta": "00_meta"}` | JSON mapping of scope names to vault subdirectories |

## API Key Resolution

The OpenRouter API key supports two names for convenience:

- `HIVE_OPENROUTER_API_KEY` (prefixed, standard)
- `OPENROUTER_API_KEY` (bare, for environments that already export it)

If both are set, the `HIVE_` prefixed version takes precedence.

## Example: Full Configuration

**Claude Code:**
```bash
claude mcp add -s user hive \
  -e VAULT_PATH=$HOME/my-vault \
  -e HIVE_OLLAMA_ENDPOINT=http://minipc.local:11434 \
  -e OPENROUTER_API_KEY=sk-or-v1-abc123 \
  -e HIVE_OPENROUTER_BUDGET=10.0 \
  -- uvx --upgrade hive-vault
```

**Gemini CLI:**
```bash
gemini mcp add -s user \
  -e VAULT_PATH=$HOME/my-vault \
  -e HIVE_OLLAMA_ENDPOINT=http://minipc.local:11434 \
  -e OPENROUTER_API_KEY=sk-or-v1-abc123 \
  -e HIVE_OPENROUTER_BUDGET=10.0 \
  hive-vault uvx -- --upgrade hive-vault
```

**Codex CLI** — add to `~/.codex/config.toml`:
```toml
[mcp_servers.hive-vault]
command = "uvx"
args = ["--upgrade", "hive-vault"]

[mcp_servers.hive-vault.env]
VAULT_PATH = "~/my-vault"
HIVE_OLLAMA_ENDPOINT = "http://minipc.local:11434"
OPENROUTER_API_KEY = "sk-or-v1-abc123"
HIVE_OPENROUTER_BUDGET = "10.0"
```

The `--upgrade` flag ensures you always get the latest version from PyPI on each session start.

## Example: Vault Only (No Worker)

If you only need vault access and don't want worker delegation:

```bash
# Claude Code
claude mcp add -s user hive -e VAULT_PATH=$HOME/my-vault -- uvx --upgrade hive-vault

# Gemini CLI
gemini mcp add -s user -e VAULT_PATH=$HOME/my-vault hive-vault uvx -- --upgrade hive-vault
```

For other MCP clients, pass the same environment variables through your client's MCP server configuration.

Worker tools will still be available but will return "no providers available" when called.

## Setting Up Ollama

[Ollama](https://ollama.com/download) lets you run LLMs locally for free. After installing:

```bash
# Pull the default model
ollama pull qwen2.5-coder:7b

# Verify it's running
curl http://localhost:11434/api/tags
```

If Ollama runs on a different machine (e.g., a homelab), set `HIVE_OLLAMA_ENDPOINT` to its address.

## Setting Up OpenRouter

[OpenRouter](https://openrouter.ai/) provides access to many models through a single API. Free tier models are available.

1. Create an account at [openrouter.ai](https://openrouter.ai/)
2. Generate an API key at [openrouter.ai/keys](https://openrouter.ai/keys)
3. Pass it as `OPENROUTER_API_KEY` when registering the MCP server

The default model (`qwen/qwen3-coder:free`) is free. Paid models are only used when you explicitly set `max_cost_per_request > 0` on `delegate_task` calls, and are capped by `HIVE_OPENROUTER_BUDGET`.

## Activating Hive in Your Workflow

Installing and registering Hive makes the tools *available*, but your AI assistant needs guidance on **when** to use them. Your project instructions file (`CLAUDE.md`, `GEMINI.md`, or equivalent in your MCP client) is the key.

Without explicit instructions, your assistant *might* use Hive tools, but inconsistently. With clear instructions, it uses them **predictably** for every relevant query.

### Recommended Instructions Snippet

Add this to your project instructions file (`CLAUDE.md`, `GEMINI.md`, or equivalent):

```markdown
## Vault & Knowledge (Hive MCP)

When hive-vault MCP is available, use it for on-demand context:
- `vault_query(project="myproject", section="context")` — project overview
- `vault_query(project="myproject", section="tasks")` — active backlog
- `vault_search(query="...")` — cross-vault search
- `session_briefing(project="myproject")` — full context in one call

When writing to the vault: lessons → `90-lessons.md`, decisions → `30-architecture/`.
```

Replace `myproject` with your actual project slug (the directory name under `10_projects/`).

### How MCP Tool Selection Works

1. **Registration** — MCP servers are loaded at session start. Your client sees all tools from all servers.
2. **Discovery** — The assistant reads tool names and descriptions to understand capabilities.
3. **Routing** — Your `CLAUDE.md` instructions tell the assistant which tools to prefer for which situations. Multiple MCP servers coexist without conflict — each serves its domain.
4. **Adaptation** — Hive's `session_briefing` learns from your usage patterns. Sections you query often get prioritized automatically in future briefings.
