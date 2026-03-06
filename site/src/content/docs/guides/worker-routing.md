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

## Why These Models?

Hive's default models were selected based on cost, code quality, and availability:

### Ollama: qwen2.5-coder:7b

- **Why 7B?** Runs on minimal hardware (8GB RAM, CPU-only). Intel N95 mini PCs, old laptops, and NAS devices can all serve it. Larger models (14B, 32B) need GPUs or 32GB+ RAM.
- **Why Qwen?** Best coding benchmarks in the 7B class. Outperforms CodeLlama 7B, DeepSeek Coder 6.7B, and StarCoder2 7B on HumanEval and MBPP.
- **Best for:** Regex explanations, boilerplate, simple Q&A. Not suitable for complex multi-file reasoning.
- **Override:** Set `HIVE_OLLAMA_MODEL` to any model you've pulled with `ollama pull`.

### OpenRouter free: qwen/qwen3-coder:free

- **Why Qwen3 Coder?** 480B MoE model (only 30B active parameters). Best free coding model available on OpenRouter. Competitive with GPT-4 on code tasks.
- **Why free tier?** Zero cost for the 80% of delegated tasks that don't need paid-tier quality. Rate-limited but sufficient for most workflows.
- **Override:** Set `HIVE_OPENROUTER_MODEL` to any free model on OpenRouter (e.g., `deepseek/deepseek-coder-v2:free`).

### OpenRouter paid: qwen/qwen3-coder

- **Why paid?** Same model, no rate limits, higher priority. Used only when you explicitly allow it via `max_cost_per_request > 0`.
- **Cost:** ~$0.22/M input tokens, ~$1.00/M output tokens. At the default $1/month budget, that's roughly 1M output tokens or ~500 delegate_task calls.
- **Override:** Set `HIVE_OPENROUTER_PAID_MODEL` to any model on OpenRouter.

### How the routing decision works

```
delegate_task(prompt, max_cost_per_request=0)
    │
    │  Is an explicit model specified? (e.g., "ollama:llama3")
    │  └─ Yes → Send directly to that provider. Skip routing.
    │
    │  Try Ollama (HTTP ping to endpoint):
    │  └─ Reachable → Send to HIVE_OLLAMA_MODEL. Done.
    │
    │  Try OpenRouter free tier:
    │  └─ API key configured → Send to HIVE_OPENROUTER_MODEL. Done.
    │
    │  Try OpenRouter paid tier:
    │  └─ max_cost_per_request > 0 AND monthly budget allows?
    │     └─ Yes → Send to HIVE_OPENROUTER_PAID_MODEL. Done.
    │
    └─ All tiers exhausted → Return error. Host model handles it.
```

The routing is fail-fast: if Ollama is unreachable (HTTP timeout), it immediately falls through to OpenRouter. No retries, no waiting. A typical fallthrough takes <100ms.
