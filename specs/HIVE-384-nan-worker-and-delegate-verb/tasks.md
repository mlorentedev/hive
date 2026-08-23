---
tags: [spec, tasks, templates]
created: "2026-08-23"
---

# Tasks - HIVE-384-nan-worker-and-delegate-verb

> TDD order. One task = one focused commit. Tick as you go. Reorder freely while spec is in `draft` state; freeze once you start `implementing`.
>
> **Inline markers**: `[P]` — no dependency on another unchecked task, safe to run in parallel. `[AC<n>]` — helps satisfy acceptance criterion #`<n>` from `proposal.md`.

## Setup

- [ ] Worktree + branch off `master`: `feat/nan-worker-and-delegate-verb`
- [ ] `proposal.md` complete; acceptance criteria testable
- [ ] Two open questions in `proposal.md` answered: where the verb's smoke test runs, and what
      `worker_status` reports once one provider remains
- [ ] `make check` green on a clean checkout before the first change, so a later failure is
      attributable

## Implementation

### The worker's provider layer

- [ ] [P] [AC5] Failing test: the worker resolves its endpoint from `HIVE_WORKER_BASE_URL` and falls
      back to `HIVE_EMBED_BASE_URL` when unset (same for the key and the model)
- [ ] [AC5] Add the `HIVE_WORKER_*` settings to `HiveSettings` with that fallback
- [ ] [AC5] Route the worker through the OpenAI-compatible transport already used for embeddings and
      synthesis
- [ ] [AC6] Remove the Ollama and OpenRouter providers, `ollama_endpoint`, `ollama_model`,
      `openrouter_api_key` (**and its unprefixed alias**), `openrouter_budget`, `openrouter_model`,
      `openrouter_paid_model`
- [ ] [AC6] Remove `max_cost_per_request` from `delegate_task`'s MCP parameters — the declared
      amendment to ADR-011 §5, stated in the commit body, not slipped in
- [ ] [AC6] **Migrate `delegate_task`'s `model` parameter**, which today is `model: str = "auto"`
      carrying the legacy provider vocabulary. Removing only `max_cost_per_request` would leave the
      old model contract standing after every other task is done. Three parts, and the third is the
      one that makes it a migration rather than a rename:
      - redefine the parameter as a concrete model id with no provider-alias vocabulary and no
        `"auto"` default — the caller states the model, as the CLI contract requires;
      - update every in-repo caller and the tool's docstring and documentation;
      - **reject the legacy values explicitly** (`auto`, `ollama`, `openrouter-free`, `openrouter`)
        with an error naming the replacement, rather than passing them through to a provider that no
        longer exists. A silently-accepted dead alias is how a caller discovers the break as a
        confusing inference failure instead of a clear validation error.
- [ ] [AC6] Assertion that a legacy `model` value is rejected, so the migration cannot regress
- [ ] [AC6] Assertion that keeps them gone: no settings field, MCP parameter, or documentation line
      names Ollama or OpenRouter for the worker

### The verb

- [ ] [P] [AC1] Failing test: `hive delegate` requires `--model` and `--timeout`, and rejects the
      call rather than defaulting either
- [ ] [AC1] Failing test: it writes exactly one JSON object to stdout and every log line to stderr
- [ ] [AC1] Implement the verb, dispatching through the client → daemon path
- [ ] [P] [AC2] Failing test: an unreachable or 429-ing provider exits `3`; a worker that answers
      with a failure exits `1`
- [ ] [AC2] Implement the error classification. **These two must not share a code path** — the
      dispatcher advances its chain on `3` and must not on `1`
- [ ] [AC3] Failing test: a worker that outlives its deadline is killed and the verb returns exit `4`
      without waiting for it
- [ ] [AC3] Wire the call through ADR-008's `bounded_call` with the process registered; do not add a
      second timeout mechanism
- [ ] [AC4] Failing test: with no reachable daemon the verb falls back to the in-process stdio path
      and marks degraded mode in its output (ADR-011 §3)
- [ ] [AC4] Implement the fallback, reusing the existing client shim's detection rather than a second
      probe

### Credential handling

- [ ] [P] [AC7] Failing test: with a planted key value in the environment, it appears in no log line,
      no `worker_status` output, and no crash artifact (ADR-011 §4 already requires the last)
- [ ] [AC7] Make it pass; verify the daemon works **by consequence** — it answers — never by printing
      the value

### Surfaces that go stale on the same commit

- [ ] [AC6] `AGENTS.md`: replace the "Ollama … free, primary" provider list with the NaN-only reality
- [ ] [AC6] `Makefile`: the `smoke` target's help text reads *"needs Ollama + API key"* — it becomes
      NaN and `HIVE_WORKER_API_KEY`. A help string is documentation a human acts on
- [ ] [AC6] `tests/test_budget.py` covers the spend cap being removed; delete it with its subject
      rather than leaving a test asserting a contract that no longer exists
- [ ] [AC5] Re-point the `@pytest.mark.smoke` tests at NaN and `HIVE_WORKER_API_KEY`; they stay
      excluded from `make check` by the existing `-m 'not smoke'`
- [ ] [AC5] Reshape `worker_status` to report reachability and the resolved model — the shape the
      consuming `dotf doctor` check needs — once the open question above is answered
- [ ] `docs/`: record the provider change where hive keeps build/operate knowledge

## Closing

- [ ] Every acceptance criterion covered by at least one test
- [ ] Every criterion has a `features.json` entry with a non-vacuous verification command
- [ ] `make check` green (lint + typecheck + test)
- [ ] Smoke run performed once by hand against a live NaN endpoint, with the output in
      `verification.md` — the criteria that cannot be proved by `make check` alone
- [ ] Commit is `feat!` so release-please cuts **4.0.0**; the breaking change is named in the body
- [ ] `verification.md` filled in
- [ ] PR opened referencing this spec folder
- [ ] **After the release publishes**: `uv tool upgrade hive-vault` on the consuming machine, then
      re-run `dotfiles` CLI-042 AC6. A PR merged upstream is not a version this machine runs.

## Machine-readable features

Each acceptance criterion maps to ≥1 entry in the sibling `features.json` with `id`, `behavior`,
`verification` (executable command), `state` and `evidence`.

**Pass-state gating:** the agent CANNOT write `"state": "passing"` — only the harness, after running
`verification` and capturing exit code 0, may set that terminal state.
