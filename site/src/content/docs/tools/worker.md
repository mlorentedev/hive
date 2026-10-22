---
title: Worker Tools
description: 2 tools for delegating tasks to a single OpenAI-compatible worker and checking its health.
---

The worker is one OpenAI-compatible endpoint, configured with `HIVE_WORKER_BASE_URL`, `HIVE_WORKER_API_KEY` and `HIVE_WORKER_MODEL` (see [Configuration](/hive/configuration/#setting-up-a-worker)). Hive names no provider: a hosted service and a local runtime are configured the same way. There is no fallback chain — one call is one attempt against that one endpoint. See [Worker Routing](/hive/guides/worker-routing/) for why.

## delegate_task

Hand a task to the worker model, or summarize vault files.

```python
# Task delegation
delegate_task(
    prompt="Explain this regex: ^(?:[a-z0-9]+\.)*[a-z0-9]+$",
    context="",          # optional system context
    model="",            # empty = the configured HIVE_WORKER_MODEL
    max_tokens=2000,
    timeout_s=0.0,       # 0 = the ambient HIVE_TOOL_TIMEOUT
    structured=False     # True = JSON record instead of prose
)

# Vault file summarization
delegate_task(
    project="my-project",
    section="context",       # or use path="..."
    max_summary_lines=20     # target summary length
)
```

### Task Delegation

When `prompt` is provided, Hive makes **one attempt** against the configured worker:

- An empty `model` uses `HIVE_WORKER_MODEL`. A concrete model id is sent as given; Hive does not validate it against a catalog, so the id is whatever your endpoint calls it.
- The model aliases `auto`, `ollama`, `openrouter-free` and `openrouter` were retired in 4.0.0. Passing one is rejected with a message that names the replacement (a concrete model id, or omit `model`) rather than being silently ignored.
- `timeout_s` sets a per-call deadline. A value above `HIVE_TOOL_TIMEOUT` raises the ceiling instead of being clamped by it.
- If the worker is not configured, is unreachable, or returns an unusable answer, the call returns an error and, in prose mode, ends with *"The host should handle this task directly."* — the host model takes the task itself.

### Vault Summarization

When `project` is provided, reads a vault file:
- **Small files** (≤50 lines): returned directly with metadata
- **Large files** (>50 lines): sent to the worker for summarization. If the worker is unavailable, the raw content is returned with a note saying the summary failed.

### Structured Mode

`structured=True` returns a one-line JSON record instead of prose, for callers that need to branch on the outcome rather than parse text:

```json
{"status": "ok", "model": "<model-id>", "output": "...", "tokens": 412, "duration_ms": 2100, "detail": ""}
```

Every field is always present, including on failure. `status` is one of:

| `status` | Meaning | Retry on another pool? |
|---|---|---|
| `ok` | The worker answered | — |
| `pool_unavailable` | The endpoint did not serve the request: unreachable, HTTP request timed out, rate-limited (429), credential rejected (401/403), or worker not configured | Yes, if the caller has another pool |
| `task_failed` | The endpoint answered and the answer is unusable (a 5xx, or a 4xx about the request itself such as an unknown model id), or the request was wrong (for example a retired alias) | No |
| `timeout` | The per-call deadline (`timeout_s` / `HIVE_TOOL_TIMEOUT`) expired | The caller decides |

`structured=True` combined with `project=` is refused as `task_failed`: summarization returns a document, not a record.

### Response Format

Prose responses start with a header naming the model, token count, cost and latency:

```
## Worker Response (model: <model-id>, 412 tokens, $0.00, 2.1s)

<the worker's answer>
```

The cost reads `$0.00` unless the endpoint reports a cost in its usage data. Hive itself does not track or cap spend (see [Worker Routing](/hive/guides/worker-routing/#no-spend-cap)).

## worker_status

Show worker configuration, reachability, and usage.

```python
# Full status, probing the endpoint for its model list
worker_status()

# Configuration and usage only, no network call
worker_status(include_models=False)
```

Returns:

- `## Provider` — whether a worker is configured (`Configured: no — set HIVE_WORKER_BASE_URL` when it is not), the model, and reachability
- `## Usage this month` — request count and token total
- `## By model` — per-model request count, tokens and average latency
- `## Available Models` — the models the endpoint lists (when `include_models=True`)

Reachability is **probed**, not inferred from configuration: `Reachable: yes (N models)`, `Reachable: NO — <reason>`, or `Reachable: unprobed` when `include_models=False`. "Configured" and "answers" are reported separately on purpose — a worker can be configured and still down.
