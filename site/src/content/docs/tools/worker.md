---
title: Worker Tools
description: 2 tools for delegating tasks to cheaper models with automatic routing.
---

## delegate_task

Route a task to a cheaper model, or summarize vault files automatically.

```python
# Task delegation
delegate_task(
    prompt="Explain this regex: ^(?:[a-z0-9]+\.)*[a-z0-9]+$",
    context="",              # optional context to include
    max_cost_per_request=0,  # 0 = free models only
    model=""                 # explicit model override
)

# Vault file summarization
delegate_task(
    project="my-project",
    section="context",       # or use path="..."
    max_summary_lines=20     # target summary length
)
```

### Task Delegation

When `prompt` is provided, tasks are routed through tiers in order:

1. **Ollama** (local) — Free. Best for trivial tasks. Falls through if unavailable.
2. **OpenRouter free** — Free tier models (e.g., Qwen3 Coder 480B). Real code work.
3. **OpenRouter paid** — Only when `max_cost_per_request > 0` and monthly budget allows. Model configurable via `HIVE_OPENROUTER_PAID_MODEL`.
4. **Reject** — Returns error so the host handles the task directly.

### Vault Summarization

When `project` is provided, reads a vault file:
- **Small files** (≤50 lines): returned directly with metadata
- **Large files** (>50 lines): auto-delegated to a worker for summarization. Falls back to raw content if workers are unavailable.

### Explicit Model Selection

Skip routing and target a specific provider:

```python
# Force local Ollama
delegate_task(prompt="...", model="ollama:qwen2.5-coder:7b")

# Force OpenRouter free
delegate_task(prompt="...", model="openrouter:qwen/qwen3-coder:free")

# Force OpenRouter paid
delegate_task(prompt="...", model="openrouter:qwen/qwen3-coder", max_cost_per_request=0.01)
```

### Response Format

Each response includes a metadata footer:

```
[model: qwen2.5-coder:7b | provider: ollama | cost: $0.00 | latency: 2.1s]
```

## worker_status

Show worker health, budget, and available models.

```python
# Full status with model listing
worker_status()

# Status without model listing
worker_status(include_models=False)
```

Returns:
- Monthly budget remaining (spent / cap)
- Ollama connectivity status
- OpenRouter connectivity status
- Request counts and cost breakdown
- Available models across all providers (when `include_models=True`)
