---
title: Worker Routing
description: How Hive's single worker handles delegated tasks, and where routing decisions live.
---

Hive's worker takes delegated tasks off your primary model, reserving your primary model for high-value reasoning. Since 4.0.0 there is exactly **one** worker: a single OpenAI-compatible endpoint. Hive makes one attempt against it and reports what happened. Choosing *which* pool a task goes to is the caller's job, not Hive's.

## One Worker, One Attempt

The worker is configured with three variables:

| Variable | Falls back to when unset |
|---|---|
| `HIVE_WORKER_BASE_URL` | `HIVE_EMBED_BASE_URL` |
| `HIVE_WORKER_API_KEY` | `HIVE_EMBED_API_KEY` |
| `HIVE_WORKER_MODEL` | `HIVE_EMBED_MODEL` |

The fallback is per field, so a deployment can override the model alone and inherit the endpoint. With no base URL set (and no `HIVE_EMBED_BASE_URL` to inherit), the worker is **disabled**: Hive never guesses a default endpoint, because a worker that silently points somewhere is worse than one that says it is unconfigured.

Hive names no provider. Any service or local runtime that serves an OpenAI-compatible `/v1` is a valid worker, and these three variables are the only way to select one. The per-provider settings and model aliases of earlier releases were removed in 4.0.0; set `HIVE_WORKER_*` instead.

## Flow

```
delegate_task(prompt, context, model)
    │
    ├─ Retired alias in `model`? ──→ Rejected, names the replacement
    │
    ├─ Worker not configured? ──→ pool_unavailable ──→ host handles the task
    │
    └─ One request to the configured endpoint
        │
        ├─ Answered ──→ ok
        ├─ Unreachable / HTTP timeout / 429 / credential rejected ──→ pool_unavailable
        ├─ Answered but unusable: 5xx, other 4xx, unknown failure ──→ task_failed
        └─ Deadline expired ──→ timeout
```

There is no step that tries a second model. The `model` argument picks a model on the *same* endpoint; an empty value uses `HIVE_WORKER_MODEL`.

## Failure Classes

Failures are classified, not collapsed into one error:

- **`pool_unavailable`** — the endpoint did not serve the request: it was unreachable or its HTTP request timed out, it rate-limited the call (429), it rejected the credential (401/403), or no worker is configured. A caller that owns another pool may retry the task there.
- **`task_failed`** — the endpoint answered and the answer is unusable (a 5xx, or a 4xx about the request itself such as an unknown model id), or the request itself was wrong. Retrying elsewhere would hide a real failure, so a caller must not. Unknown failures also land here: the fail-closed direction is the one that does not retry.

`delegate_task(structured=True)` returns the class as the `status` value of a JSON record (see [Worker Tools](/hive/tools/worker/#structured-mode)). The class travels as data rather than as an exception type because exception types do not survive the JSON-RPC boundary between a daemon and its clients.

## Routing Belongs to the Caller

A backend that falls back on its own becomes a second routing authority, and its answer can no longer be attributed to a model. So Hive deliberately has no internal fallback chain. A dispatcher that keeps a routing map walks it itself — advancing on `pool_unavailable`, stopping on `task_failed` — and always knows which model produced which answer.

`hive delegate` is the command-line verb built for such a dispatcher. It runs one task against the configured worker and prints one JSON record on stdout. `--model`, `--timeout` and `--prompt` are required, so a dispatcher never believes it chose a model or set a deadline by default. The exit code carries the class:

| Exit code | Meaning |
|---|---|
| `0` | `ok` |
| `1` | `task_failed` — do not retry |
| `2` | Usage error |
| `3` | `pool_unavailable` — try the next entry |
| `4` | `timeout` |

## No Spend Cap

Hive does not track or cap spend. On a flat-rate or local endpoint the binding constraint is concurrency, not cost, so a dollar cap would measure the wrong quantity. What Hive records is usage telemetry — request count, tokens and latency per model — which [`worker_status`](/hive/tools/worker/#worker_status) and `vault_health(include_runtime=True)` report. If your endpoint bills per token, enforce limits on the provider side.

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

## Choosing a Model

Hive does not ship a default model. Set `HIVE_WORKER_MODEL` to the id your endpoint calls the model you want, or pass `model=` per call. A small, fast model suits the tasks above. A slow model shows up directly as latency, and a rate-limited endpoint shows up as `pool_unavailable`. Raise `HIVE_HTTP_TIMEOUT` and `HIVE_TOOL_TIMEOUT` (or pass `timeout_s`) for slow models.
