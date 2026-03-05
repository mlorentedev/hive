---
title: Worker Routing
description: How Hive routes tasks to the cheapest capable model.
---

Hive's worker system delegates tasks to cheaper models, reserving your primary model for high-value reasoning.

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
        └─ 4. Reject ──→ Error returned, host handles it
```

## Cost Table

| Tier | Provider | Model | Cost |
|---|---|---|---|
| 1 | Ollama (local) | qwen2.5-coder:7b | Free |
| 2 | OpenRouter | qwen/qwen3-coder:free | Free |
| 3 | OpenRouter | qwen/qwen3-coder | $0.22/1M input, $1.00/1M output |
| 4 | Reject | — | — |

## Budget Controls

- **Monthly cap**: `HIVE_OPENROUTER_BUDGET` (default: $1.00)
- **Per-request cap**: `max_cost_per_request` parameter on `delegate_task`
- **Paid model**: `HIVE_OPENROUTER_PAID_MODEL` (default: `qwen/qwen3-coder`)
- Budget tracking uses SQLite with WAL mode for concurrent access

## When to Delegate

Good candidates for delegation:

- Regex explanations
- Code formatting and simple refactoring
- Boilerplate generation
- Documentation drafting
- Simple Q&A about well-known topics

Keep on your primary model:

- Complex architecture decisions
- Multi-file refactoring with dependencies
- Security-sensitive code review
- Tasks requiring deep codebase understanding
