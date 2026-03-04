---
title: Worker Routing
description: How Hive routes tasks to the cheapest capable model.
---

Hive's worker system delegates tasks to cheaper models, reserving Claude for high-value reasoning.

## Routing Flow

```
delegate_task(prompt, context, max_cost_per_request)
    │
    ├─ Explicit model? ─── Yes ──→ Route directly to that provider
    │
    └─ Auto-route:
        │
        ├─ 1. Ollama available? ─── Yes ──→ Local inference (free)
        │
        ├─ 2. OpenRouter configured? ── Yes ──→ Free tier model
        │
        ├─ 3. max_cost > 0 AND budget allows? ── Yes ──→ Paid model
        │
        └─ 4. Reject ──→ Error returned, Claude handles it
```

## Cost Table

| Tier | Provider | Model | Cost |
|---|---|---|---|
| 1 | Ollama (local) | qwen2.5-coder:7b | Free |
| 2 | OpenRouter | qwen/qwen3-coder:free | Free |
| 3 | OpenRouter | deepseek/deepseek-chat | ~$0.28/1M tokens |
| 4 | Reject | — | — |

## Budget Controls

- **Monthly cap**: `HIVE_OPENROUTER_BUDGET` (default: $5.00)
- **Per-request cap**: `max_cost_per_request` parameter on `delegate_task`
- Budget tracking uses SQLite with WAL mode for concurrent access

## When to Delegate

Good candidates for delegation:

- Regex explanations
- Code formatting and simple refactoring
- Boilerplate generation
- Documentation drafting
- Simple Q&A about well-known topics

Keep on Claude:

- Complex architecture decisions
- Multi-file refactoring with dependencies
- Security-sensitive code review
- Tasks requiring deep codebase understanding
