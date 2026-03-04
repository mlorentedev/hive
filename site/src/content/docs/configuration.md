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
| `HIVE_OPENROUTER_MODEL` | `qwen/qwen3-coder:free` | Default OpenRouter model |
| `HIVE_OPENROUTER_BUDGET` | `5.0` | Monthly budget cap in USD |
| `HIVE_DB_PATH` | `~/.local/share/hive/worker.db` | SQLite database for budget/usage tracking |

## API Key Resolution

The OpenRouter API key supports two names for convenience:

- `HIVE_OPENROUTER_API_KEY` (prefixed, standard)
- `OPENROUTER_API_KEY` (bare, for environments that already export it)

If both are set, the `HIVE_` prefixed version takes precedence.

## Example: Full Configuration

```bash
claude mcp add hive \
  -e VAULT_PATH=$HOME/my-vault \
  -e HIVE_OLLAMA_ENDPOINT=http://minipc.local:11434 \
  -e HIVE_OLLAMA_MODEL=qwen2.5-coder:7b \
  -e OPENROUTER_API_KEY=sk-or-v1-abc123 \
  -e HIVE_OPENROUTER_BUDGET=10.0 \
  -- uvx hive-vault
```

## Example: Vault Only (No Worker)

If you only need vault access and don't want worker delegation:

```bash
claude mcp add hive -e VAULT_PATH=$HOME/my-vault -- uvx hive-vault
```

Worker tools will still be available but will return "no providers available" when called.
