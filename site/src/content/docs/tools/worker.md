---
title: Worker Tools
description: 3 tools for delegating tasks to cheaper models with automatic routing.
---

## delegate_task

Route a task to a cheaper model with automatic tier selection.

```python
delegate_task(
    prompt="Explain this regex: ^(?:[a-z0-9]+\.)*[a-z0-9]+$",
    context="",              # optional context to include
    max_cost_per_request=0,  # 0 = free models only
    model=""                 # explicit model override
)
```

### Routing Tiers

When no explicit model is specified, tasks are routed through tiers in order:

1. **Ollama** (local) — Free. Best for trivial tasks. Falls through if unavailable.
2. **OpenRouter free** — Free tier models (e.g., Qwen3 Coder 480B). Real code work.
3. **OpenRouter paid** — Only when `max_cost_per_request > 0` and monthly budget allows.
4. **Reject** — Returns error so the host handles the task directly.

### Explicit Model Selection

Skip routing and target a specific provider:

```python
# Force local Ollama
delegate_task(prompt="...", model="ollama:qwen2.5-coder:7b")

# Force OpenRouter free
delegate_task(prompt="...", model="openrouter:qwen/qwen3-coder:free")

# Force OpenRouter paid
delegate_task(prompt="...", model="openrouter:deepseek/deepseek-chat-v3-0324:free", max_cost_per_request=0.01)
```

### Response Format

Each response includes a metadata footer:

```
[model: qwen2.5-coder:7b | provider: ollama | cost: $0.00 | latency: 2.1s]
```

## list_models

List available models across all providers.

```python
list_models()
```

Shows configured models for Ollama and OpenRouter, with connectivity status.

## worker_status

Show worker health and budget information.

```python
worker_status()
```

Returns:
- Monthly budget remaining (spent / cap)
- Ollama connectivity status
- OpenRouter connectivity status
- Request counts and cost breakdown
