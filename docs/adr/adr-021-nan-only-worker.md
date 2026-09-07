---
id: "ADR-021-nan-only-worker"
type: adr
status: accepted
owner: manu
date: "2026-08-23"
issue: "hive#384"   # repo#NNN — GitHub issue / Project item that triggered this decision
tags: [architecture, decision, worker, delegate, provider, mcp]
created: "2026-09-06"
---

# ADR-021: One OpenAI-Compatible Worker, Pointed at NaN (amends ADR-011 §5)

## Status

Accepted (2026-08-23). Records the decision that shipped in **4.0.0** (#386) through
[#390](https://github.com/mlorentedev/hive/pull/390), following
`specs/HIVE-384-nan-worker-and-delegate-verb/`. Written down on 2026-09-06, after the fact: the
spec declared the amendment at proposal time, but no ADR carried it. **Amends
[adr-011-phase-c-daemon-model.md](adr-011-phase-c-daemon-model.md) §5** ("Scope boundary"), whose
two claims this decision makes false: that *Ollama stays remote*, and that *changing the MCP tool
surface* is out of scope. ADR-011 is left as written, the same convention
[ADR-015](adr-015-windows-daemon-supervision-upgrade.md) used when it amended §4.

## Date

2026-08-23

## Context

**The delegate worker could not serve a single request, and nothing noticed.** Measured on
2026-08-22 through `worker_status`:

```
- Ollama: offline / unavailable
- OpenRouter: no API key
### Available Models — none
```

Ollama was not running on the machine and OpenRouter had been retired upstream in August 2026.
`delegate_task` had been a declared capability with no reachable backend for an unknown length of
time. The old shape made that invisible by construction: a non-optional Ollama client that always
existed and never answered, behind a tiered ladder (Ollama → OpenRouter free → OpenRouter paid →
reject) whose every rung was dead.

It became load-bearing the same day. `mlorentedev/dotfiles#1190` (CLI-042) made hive one of two
backends of the `dotf agent run` executor seam, and a backend that cannot answer fails that spec's
AC6 by construction. A seam with one real implementation is an interface, not a seam.

The transport that *did* work was already in the package. `HiveSettings` carried
`embed_base_url` / `embed_api_key` / `embed_model` for the semantic backend (HIVE-211, #220):
OpenAI-compatible, with a comment reading *"default config points at NaN"*. So the question was
never whether to build a new integration. It was whether to keep two dead providers alongside a
live transport, or to remove them and route the worker through the transport that answers.

Three constraints shaped the answer:

- [ADR-011](adr-011-phase-c-daemon-model.md) makes the daemon the sole owner of `worker.db`, where
  usage accounting lives. Whatever the worker becomes, it must not grow a second writer.
- [ADR-008](adr-008-hard-deadline-enforcement.md)'s `bounded_call` is the one deadline supervisor.
  The worker must not grow a second timeout mechanism.
- The consumer is a dispatcher walking a fallback chain, so every failure the worker reports is an
  instruction about what the caller does next. That constraint turned out to be the one the old
  code got most wrong.

## Decision

**The delegate worker is a single OpenAI-compatible provider. Ollama and OpenRouter are removed,
not deprecated.** Hive's own deployment points that provider at NaN, which is why the change is
known as the NaN-only worker; hive as a published package names no provider (see #392 below).
Concretely:

1. **One transport, honestly named.** New settings `HIVE_WORKER_BASE_URL`, `HIVE_WORKER_API_KEY`
   and `HIVE_WORKER_MODEL`, each falling back **per field** to its `HIVE_EMBED_*` counterpart when
   unset. A machine that works today keeps working; a deployment may override the model alone and
   inherit the endpoint. A worker is not an embedder, so it does not read the embedder's names.
   Empty `base_url` means *worker disabled*, and the client is constructed only when configured.
   `None` means unconfigured, and nothing pretends otherwise.
2. **No fallback inside hive.** One model per invocation, taken as a parameter. The tiered ladder
   went with its providers, and so did its shape: choosing among pools belongs to the caller that
   owns a routing map. A backend that falls back on its own is a second routing authority, and its
   answer can no longer be attributed to a model.
3. **Failures are classified as data, not as exception types.** `_try_worker` returns
   `(response, status, detail)` with `status` in `ok | pool_unavailable | task_failed`. *The
   provider did not serve the request* (unreachable, timeout, 429, 401, 403) and *the provider
   served it and the answer is unusable* are different instructions to the caller: retry the first
   elsewhere, never retry the second. Unknown failures classify as `task_failed`, because the
   fail-closed direction is the one that does not spend another pool's quota on an outcome nobody
   can classify. Exception types were rejected as the carrier because they do not survive the
   JSON-RPC boundary the `hive delegate` verb crosses to reach the daemon.
4. **The spend cap goes; usage telemetry stays.** `budget.py` shrinks rather than disappears.
   `record_request` and `month_usage` remain because tokens and latency are what `worker_status`,
   `/status` and `usage.db` read. `can_spend`, `month_spent` and `month_remaining` are removed: on
   a flat subscription a dollar cap measures the wrong quantity, and the binding constraint is
   concurrency, which the dispatcher that launches the verb enforces, not hive.
5. **The MCP tool surface changes, and this is the amendment.** `delegate_task` loses
   `max_cost_per_request`, and its `model` parameter no longer accepts `auto`, `ollama`,
   `openrouter-free` or `openrouter`. The retired aliases are rejected before dispatch with a
   message naming the replacement, because a silently accepted dead alias reaches the caller as a
   confusing inference failure instead of a validation error. ADR-011 §5 drew its boundary to stop
   the daemon migration from absorbing tool redesign. This change is not the daemon migration, and
   it is declared here rather than slipped past.

### What shipped when

- **4.0.0** (#386, 2026-08-23): the provider layer, #390. `feat!`, a major, because the schema
  changed even though no client could have depended on a provider serving zero models.
- **4.1.0** (#393, 2026-08-23): the `hive delegate` verb (#395, #396), the dispatch seam a router
  can call, with `delegate_task` gaining additive `structured` and `timeout_s` parameters. #390's
  body expected both halves to accumulate into one 4.0.0; release-please cut the major between
  them. Also in 4.1.0, #392 removed the last provider names from the shipped surfaces: the
  `NAN_API_KEY` alias that #390 had accepted, the literal `provider_name="NaN"`, and the dead
  `X-OpenRouter-Title` header. The label is now derived from the configured host, and mapping a
  launcher's own credential name onto `HIVE_WORKER_API_KEY` is the launcher's job at injection
  time. `tests/test_provider_neutrality.py` keeps it that way.

## Rejected alternatives

### Keep Ollama and OpenRouter, deprecate rather than remove

Rejected because neither could answer. Ollama was not running and had no operator willing to run
it; OpenRouter no longer existed upstream. A deprecation period protects callers who depend on the
old behaviour, and nothing functional can depend on a provider that serves zero models. Keeping the
code would have kept the shape that hid the outage: a client that always exists and never answers.

### Provider-named settings (`HIVE_NAN_*`)

Rejected because putting a provider's name in the configuration schema makes a provider swap a
rename rather than a value edit. The transport is OpenAI-compatible; which host serves it is a
deployment choice hive does not make. #392 later applied the same reasoning to the `NAN_API_KEY`
alias #390 had carried for the launcher's convenience.

### An internal fallback chain over several models

Rejected as a second routing authority. The dispatcher that calls hive owns the chain and the
routing map; if hive also walked one, two components would decide where a task ran and the result
record could not say which model answered. The consumer's contract (one model per invocation, exit
`3` advances the chain, exit `1` stops it) only works if hive does exactly one thing per call.

### A dollar budget on the new provider

Rejected because NaN is a flat subscription: there is no marginal cost to cap, and a gauge that
always reads zero looks like a working gauge. Recorded so that a future paid provider knows it must
bring its own cap rather than inherit one that was removed on purpose.

### Carry the failure class as an exception type

Rejected because typed exceptions do not cross the JSON-RPC boundary between the verb and the
daemon, and the pre-existing `_try_worker` had already collapsed `ConnectionError`, `RuntimeError`
and bare `Exception` into one `(None, "some string")` shape. The classification travels as a value
in the result, and the verb maps it to an exit code client-side.

## Consequences

### Positive

- `worker_status` reports configuration and probed reachability separately, in both directions,
  instead of two dead providers and a budget that always read zero. A dead backend is now visible.
- The executor seam in dotfiles has two real backends; hive stops advertising an offload
  capability it cannot perform.
- The error taxonomy tells the caller what to do next. [lesson-091](../lessons/lesson-091-a-refusal-is-not-a-failed-task.md)
  records the defect this exposed: `clients.py` raised `RuntimeError` for every non-2xx, so a
  `429` reached the verb as *task failed* and a dispatcher would have stopped its chain exactly
  where it should have advanced. `PoolUnavailableError` now separates *did not serve* from
  *served, unusable*, and `tests/test_pool_classification.py` pins each status to its class.
- 23 unit tests and 9 smoke tests were removed with their subject; coverage held at 85%.
- Nothing about the daemon model moves. The verb routes client → daemon so `worker.db` keeps one
  writer, and ADR-011 §3's fallback contract applies unchanged: with no daemon it degrades to the
  in-process stdio path and says so in an explicit `degraded` field.

### Negative

- **Breaking for any MCP client still passing `max_cost_per_request` or a retired model alias.**
  They receive a validation error naming the replacement. A major announced this; a minor would
  have hidden it.
- `BudgetTracker` no longer tracks a budget. The class name is a declared misnomer, tracked as
  [#387](https://github.com/mlorentedev/hive/issues/387), because the rename touches 83 call sites
  across ten files and would have buried #390's substance in churn.
- **AC5's live smoke has never run in CI.** The smoke tests are re-pointed and collect, but a real
  inference against the configured endpoint needs a credential CI does not hold. #408 recorded the
  measured evidence per acceptance criterion and left f6 at 0 passed, 2 skipped, rather than
  letting it read green. The decision is accepted with that gap stated, not implied.
- Hive's own deployment now depends on one external pool whose concurrency limit is shared with
  every other consumer of the same key. Hive does not know what else is drawing on it; the reserve
  is the launcher's problem by design.
- A consumer that wants a fallback must bring its own. That is the intent, but it moves work to
  every caller.

### Neutral

- `budget.py` keeps its `cost_usd` column so an existing `worker.db` opens unchanged; the column
  has no writer.
- `capture_lesson` is the worker's second consumer. Its MCP surface is unchanged, so it stays
  outside the declared amendment, but its provider re-routes with everything else and its own
  two-tier ladder collapses to the same single shot.
- ADR-011 §5's other boundaries (no team edition, no reconciliation of other sessions' vault
  branches) stand. Only the two claims named in the Status section are amended.

## References

- Amends: [adr-011-phase-c-daemon-model.md](adr-011-phase-c-daemon-model.md) §5; relies on its §3
  (fallback contract) and §4 (crash artifact carries no secrets) unchanged
- [adr-008-hard-deadline-enforcement.md](adr-008-hard-deadline-enforcement.md) — `bounded_call`,
  the supervisor the verb reuses
- [adr-015-windows-daemon-supervision-upgrade.md](adr-015-windows-daemon-supervision-upgrade.md) —
  the earlier amendment of ADR-011, whose convention this ADR follows
- `specs/HIVE-384-nan-worker-and-delegate-verb/` — proposal (the `[DECLARED AMENDMENT]` entry),
  tasks, verification and `features.json`
- [#384](https://github.com/mlorentedev/hive/issues/384) — the ticket
- [#390](https://github.com/mlorentedev/hive/pull/390) — the provider layer (4.0.0, #386)
- [#392](https://github.com/mlorentedev/hive/pull/392) — no provider named in shipped surfaces
- [#395](https://github.com/mlorentedev/hive/pull/395), [#396](https://github.com/mlorentedev/hive/pull/396)
  — the `hive delegate` verb and its review follow-up (4.1.0, #393)
- [#408](https://github.com/mlorentedev/hive/pull/408) — measured evidence per AC; AC5 not green
- [#387](https://github.com/mlorentedev/hive/issues/387) — the `BudgetTracker` rename
- [lesson-091](../lessons/lesson-091-a-refusal-is-not-a-failed-task.md) — a refusal is not a
  failed task
- `mlorentedev/dotfiles#1190` (CLI-042) — the executor seam this unblocks
- `src/hive/config.py` (`worker_*` settings and the embed fallback), `src/hive/_workers.py`
  (`_try_worker`'s result-carried status), `src/hive/clients.py` (`PoolUnavailableError`),
  `src/hive/budget.py`, `src/hive/_delegate.py`
