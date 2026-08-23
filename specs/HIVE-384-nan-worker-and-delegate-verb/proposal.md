---
id: "HIVE-384-nan-worker-and-delegate-verb"
type: spec
status: draft # draft | implementing | verifying | archived
created: "2026-08-23"
issue: "mlorentedev/hive#384"   # repo#NNN — GitHub issue / Project item that tracks this spec
tags: [spec, proposal]
template_version: "1.0"
---

# HIVE-384-nan-worker-and-delegate-verb

> **Naming**: file lives at `<repo>/specs/HIVE-384-nan-worker-and-delegate-verb/proposal.md`.

## Why

<!-- from issue #384: The delegate worker reaches zero models — make it NaN-only and give it a dispatch verb -->

**The delegate worker cannot serve a single request, and nothing noticed.** Measured 2026-08-22 via
`worker_status`: *Ollama offline / unavailable · OpenRouter no API key · Available Models: none.*
Both providers are dead — Ollama is not running locally, and OpenRouter was retired upstream in
August 2026 — so `delegate_task` has been a declared capability with no reachable backend for an
unknown length of time.

It became load-bearing on 2026-08-22: `mlorentedev/dotfiles#1190` makes hive one of two backends of
the `dotf agent run` executor seam, and a backend that cannot answer fails that spec's AC6 by
construction. Two things block it. The providers do not resolve, and **the worker is unreachable from
a shell at all** — `hive` exposes only the stdio MCP server, the daemon, the client shim, `service`
and `self-upgrade`, so `delegate_task` exists solely over MCP.

If this does not ship, the executor seam has one real implementation, which makes it an interface
rather than a seam, and hive keeps advertising an offload capability it cannot perform.

## What

Three changes, and one contract.

**1. The worker becomes NaN-only.** Ollama and OpenRouter are removed outright rather than
deprecated. The transport already exists on the other side of the same package: `HiveSettings` carries
`embed_base_url` / `embed_api_key` / `synth_model`, OpenAI-compatible, whose own comment reads
*"default config points at NaN"*. So this is a **removal plus a re-route**, not a new integration.

Configuration takes new `HIVE_WORKER_BASE_URL` / `HIVE_WORKER_API_KEY` / `HIVE_WORKER_MODEL`
settings that **fall back to the `HIVE_EMBED_*` values when unset**. Honest names — a worker is not
an embedder — with nothing new to deploy on day one, and the freedom to point inference and
embeddings at different models, which is the normal case. Provider-named settings (`HIVE_NAN_*`) were
rejected: putting a provider's name in the configuration schema makes a provider swap a rename rather
than a value edit.

**2. `hive delegate` — a dispatch verb, single-shot.**

```
$ hive delegate --model deepseek-v4-flash --timeout 60 --prompt "summarise this diff"
{"status":"ok","model":"deepseek-v4-flash","tokens":812,"duration_ms":4310,"output":"…"}
```

The name mirrors the MCP tool that already exists (`delegate_task`), so the CLI and the MCP surface
name the same thing with the same word.

**It routes through the client → daemon path, and this is not a style preference.** ADR-011 makes the
daemon the sole owner of `worker.db`, where usage accounting lives; a verb that spawned its own
process would be a second writer to that database — precisely the contention class the daemon model
exists to eliminate. ADR-011 §3's fallback contract applies unchanged: with no reachable daemon the
verb degrades to the in-process stdio path and **flags degraded mode in its output** rather than
failing or pretending.

**3. The contract the caller depends on.** Fixed here because the consumer is a dispatcher:

| Element | Decision |
|---|---|
| Model | **One per invocation, taken as a parameter.** No internal fallback list — the dispatcher owns chain-walking, and a second fallback list here would be a second routing authority. |
| Timeout | **Required**, no silent default. |
| Cancellation | Kill the worker and return without waiting for it. |
| Output | JSON on **stdout**, logs on **stderr**. The record names the model that actually answered. |
| Exit codes | `0` task succeeded · `1` task failed (the worker answered and the answer is a failure) · `2` usage or validation error · `3` **pool unavailable** (unreachable, 429, auth rejected) · `4` timeout |

**Exit code 3 is the one that carries weight.** *Pool unavailable* and *task failed* must be
distinguishable, because the dispatcher advances its chain on the first and must not on the second —
collapsing them turns a bad answer into a silent retry against a different model.

**Deadline enforcement is wiring, not construction.** ADR-008 already built `bounded_call(fn,
deadline_s, process_registry)`: cancel the future, `terminate()`, two-second grace, `kill()`, drain,
then raise with `{tool, deadline_s, elapsed_s, subprocess_killed}`. The verb registers its work with
that supervisor rather than growing a second timeout mechanism.

## Out of scope

Things this PR explicitly does NOT include. Forces a sharp boundary and prevents scope creep.

- **Any fallback or retry inside hive** — single-shot is a contract decision, not an omission.
- **`hive delegate status` / a detached mode.** The verb blocks and returns a result; the consuming
  seam is synchronous by decision (`dotfiles#1190`), so an async lifecycle has no caller.
- **Reintroducing a spend guard.** Removing `openrouter_budget` and `max_cost_per_request` removes
  the only spend cap in the package. Correct here — NaN is a flat subscription, so there is no
  marginal cost to cap and the binding constraint is concurrency — but recorded so a future paid
  provider knows it must bring its own cap rather than inherit one.
- **Concurrency accounting.** The NaN reserve is enforced by the dispatcher that launches the verb,
  not by hive; hive does not know what else is drawing on the pool.
- **Changing any other MCP tool.**

## Risks / open questions

- **[DECLARED AMENDMENT] This changes the MCP tool surface, which ADR-011 §5 places out of scope for
  the daemon work.** `delegate_task` loses `max_cost_per_request` and its `model` vocabulary
  (`auto` / `ollama` / `openrouter-free` / `openrouter`). That boundary was drawn to stop the daemon
  migration from absorbing tool redesign; this change is not the daemon migration, and it is declared
  here rather than slipped past. Recorded as an amendment, not an edit to an accepted ADR.
- **This is a breaking change and ships as one.** `feat!` → major, 3.3.0 → **4.0.0**, driven by
  release-please from the commit. That no provider currently answers makes the change harmless in
  practice — nothing functional can depend on a provider serving zero models — but the *schema* does
  change, and an MCP client still passing those parameters gets a validation error. A major is what
  announces that; a minor would hide it.
- **Three surfaces outside the worker module go stale on the same commit**, and each is authority
  that something reads:
  - `AGENTS.md` names Ollama as the primary (*"1. Ollama `qwen2.5-coder:7b` (local) — free,
    primary"*). Agents read that file as truth, so a stale line there is worse than a stale comment.
  - The `@pytest.mark.smoke` tests *require a live Ollama and an `OPENROUTER_API_KEY`*. Re-pointed at
    NaN, or they become tests that cannot pass on any machine — the same declared-but-unrunnable
    shape this spec exists to fix.
  - `HiveSettings` carries an unprefixed `OPENROUTER_API_KEY` alias for ergonomic deploy. It goes
    with the provider.
- **The credential must not land in a file.** `HIVE_WORKER_API_KEY` is read from the process
  environment the launcher injects (`dotf secrets run -- hive serve` on the consuming side), never
  from `environment.d` or a config file. hive's side of this is narrow but real: it must not log the
  value, must not echo it into the crash artifact — ADR-011 §4 already requires *"no secrets/API
  keys"* there — and must be verifiable by consequence (the daemon answers) rather than by printing.
- **[OPEN] Where the verb's own smoke test can run.** It needs `NAN_API_KEY` present, so it belongs
  behind the existing `smoke` marker (excluded by default) rather than in `make check`. What is not
  yet decided is whether CI gets a credential or the smoke stays developer-only; the second is the
  status quo and the cheaper answer.
- **[OPEN] What `worker_status` reports once one provider remains.** Its current output is shaped
  around two providers and a dollar budget, both of which disappear. It should report reachability
  and the resolved model, because that is what the consuming `dotf doctor` check needs — but the
  exact shape is a contract other things read, so it is named rather than assumed.

## Acceptance criteria

Observable outcomes. Each must be testable.

- [ ] **AC1 — `hive delegate` exists and honours the wire contract.** It writes one JSON object to
      stdout carrying `status`, `model`, `duration_ms` and `output`, with all logging on stderr, and
      requires `--model` and `--timeout` rather than defaulting them.
- [ ] **AC2 — the exit codes separate the two failure classes.** An unreachable or rate-limited
      provider exits `3`; a worker that answers with a failure exits `1`. Tested with both simulated.
- [ ] **AC3 — the timeout kills the worker and returns without waiting**, via ADR-008's
      `bounded_call`, exiting `4`. Tested against a worker that outlives its deadline.
- [ ] **AC4 — the verb routes through the daemon and degrades honestly.** With a daemon running it
      goes through the client path; with none it falls back to in-process stdio and marks degraded
      mode in its output (ADR-011 §3).
- [ ] **AC5 — the worker reaches NaN.** `worker_status` reports a reachable provider and the resolved
      model instead of two dead ones, and a `smoke`-marked test performs a real inference.
- [ ] **AC6 — Ollama and OpenRouter are gone from every surface, not just the provider module.** No
      settings, no `OPENROUTER_API_KEY` alias, no `AGENTS.md` claim, and no smoke test still
      requiring them. Asserted rather than reviewed by eye.
- [ ] **AC7 — the credential never appears in output.** Not in logs, not in the crash artifact, not
      in `worker_status`. Tested by asserting absence with a planted value.

## References

- Bitácora board: `mlorentedev/hive#384` (see the `issue:` frontmatter field)
- `docs/adr/adr-011-phase-c-daemon-model.md` — §3 fallback contract, §4 crash artifact carries no
  secrets, §5 the scope boundary this spec amends
- `docs/adr/adr-008-hard-deadline-enforcement.md` — `bounded_call`, the supervisor this verb reuses
- `docs/adr/adr-001-orchestration-model.md`, `docs/adr/adr-007-mcp-cancellation-response.md`
- Consumer: `mlorentedev/dotfiles#1190` (CLI-042 — the executor seam) and its
  `specs/CLI-042-dotf-agent-run/`, whose AC6 this unblocks
