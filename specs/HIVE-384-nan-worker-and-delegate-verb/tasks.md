---
tags: [spec, tasks, templates]
created: "2026-08-23"
---

# Tasks - HIVE-384-nan-worker-and-delegate-verb

> TDD order. One task = one focused commit. Tick as you go. Reorder freely while spec is in `draft` state; freeze once you start `implementing`.
>
> **Inline markers**: `[P]` — no dependency on another unchecked task, safe to run in parallel. `[AC<n>]` — helps satisfy acceptance criterion #`<n>` from `proposal.md`.

## PR sequence

The work does not fit one atomic PR and splits on the seam this file already organises around.
Declared here so no PR silently absorbs the next:

| PR | Lands | Criteria |
|---|---|---|
| **1** | the provider layer: `HIVE_WORKER_*` settings with the `HIVE_EMBED_*` fallback and the `NAN_API_KEY` alias, Ollama and OpenRouter removed, the three `_try_worker` call sites re-routed, `budget.py` trimmed, `worker_status` reshaped, and the stale surfaces (`AGENTS.md`, `Makefile`, tests) | AC5, AC6, AC7 |
| **2** | the verb: `hive delegate`, the result-carried status classification, `delegate_task`'s `timeout_s` and `model` migration, exit-code mapping, daemon routing and the degraded flag | AC1, AC2, AC3, AC4 |

**PR 1 carries the `feat!`** — it is where the providers and the MCP parameters actually disappear.

**Corrected 2026-08-23, after the fact.** This planned for both PRs to land before one release, with
release-please accumulating them into a single `4.0.0`. That is not what happened: PR 1 merged
(`#390`) and `4.0.0` published carrying it alone, so **PR 2 ships in `4.1.0`** as a `feat`, and the
"after the release publishes, `uv tool upgrade`" step in Closing runs a second time before CLI-042's
AC6 can be re-checked. Two follow-ups also landed between them, both `fix` on the same release line:
`#392` (no provider named in shipped surfaces) and `#394` (its review findings).

## Setup

- [ ] Worktree + branch off `master`: `feat/nan-worker-and-delegate-verb`
- [ ] `proposal.md` complete; acceptance criteria testable
- [ ] Two open questions in `proposal.md` answered: where the verb's smoke test runs, and what
      `worker_status` reports once one provider remains
- [ ] `make check` green on a clean checkout before the first change, so a later failure is
      attributable

## Implementation

### The worker's provider layer

- [x] [P] [AC5] Failing test: the worker resolves its endpoint from `HIVE_WORKER_BASE_URL` and falls
      back to `HIVE_EMBED_BASE_URL` when unset (same for the key and the model)
- [x] [AC5] Add the `HIVE_WORKER_*` settings to `HiveSettings` with that fallback
- [x] [AC5] Route the worker through the OpenAI-compatible transport already used for embeddings and
      synthesis
- [x] [AC6] Remove the Ollama and OpenRouter providers, `ollama_endpoint`, `ollama_model`,
      `openrouter_api_key` (**and its unprefixed alias**), `openrouter_budget`, `openrouter_model`,
      `openrouter_paid_model`
- [x] [AC6] Remove `max_cost_per_request` from `delegate_task`'s MCP parameters — the declared
      amendment to ADR-011 §5, stated in the commit body, not slipped in
- [x] [AC6] **Migrate `delegate_task`'s `model` parameter**, which today is `model: str = "auto"`
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
- [x] [AC6] Assertion that a legacy `model` value is rejected, so the migration cannot regress
- [x] [AC6] Assertion that keeps them gone: no settings field, MCP parameter, or documentation line
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

- [x] [P] [AC7] Failing test: with a planted key value in the environment, it appears in no log line,
      no `worker_status` output, and no crash artifact (ADR-011 §4 already requires the last)
      — **NOT done in PR 1.** Ticked here by inference during reconciliation and corrected on
      measurement: no such test existed, and `features.json`'s AC7 command selected zero tests.
      Written in PR 2 (`tests/test_credential_never_emitted.py`), which found two real leaks.
- [x] [AC7] Make it pass; verify the daemon works **by consequence** — it answers — never by printing
      the value

### Surfaces that go stale on the same commit

- [x] [AC6] `AGENTS.md`: replace the "Ollama … free, primary" provider list with the NaN-only reality
- [x] [AC6] `Makefile`: the `smoke` target's help text reads *"needs Ollama + API key"* — it becomes
      NaN and `HIVE_WORKER_API_KEY`. A help string is documentation a human acts on
- [x] [AC6] Trim `budget.py` to its surviving half rather than deleting it: `record_request` stays
      (usage telemetry — tokens and latency, which the reshaped `worker_status` and `usage.db` both
      read), while `can_spend` / `month_spent` / `month_remaining` / `month_stats` go with the spend
      cap. `cost_usd` becomes moot on a flat subscription
- [x] [AC6] Trim `tests/test_budget.py` to the surviving behaviour — not a wholesale delete, which
      would drop coverage of telemetry that is staying
- [x] [AC5] Re-point the `@pytest.mark.smoke` tests at NaN and `HIVE_WORKER_API_KEY`; they stay
      excluded from `make check` by the existing `-m 'not smoke'`
- [x] [AC5] Reshape `worker_status` to report reachability and the resolved model — the shape the
      consuming `dotf doctor` check needs — once the open question above is answered
- [x] `docs/`: record the provider change where hive keeps build/operate knowledge

## Closing

- [ ] Every acceptance criterion covered by at least one test
- [ ] Every criterion has a `features.json` entry with a non-vacuous verification command
- [ ] `make check` green (lint + typecheck + test)
- [ ] Smoke run performed once by hand against a live NaN endpoint, with the output in
      `verification.md` — the criteria that cannot be proved by `make check` alone
- [x] PR 1's commit was `feat!` and release-please cut **4.0.0**; the breaking change is named in
      the body. PR 2 is a `feat` on top and cuts **4.1.0** — see the corrected note in PR sequence
- [ ] `verification.md` filled in
- [ ] PR opened referencing this spec folder
- [ ] **After the release publishes**: `uv tool upgrade hive-vault` on the consuming machine, then
      re-run `dotfiles` CLI-042 AC6. A PR merged upstream is not a version this machine runs.

## Machine-readable features

Each acceptance criterion maps to ≥1 entry in the sibling `features.json` with `id`, `behavior`,
`verification` (executable command), `state` and `evidence`.

**Pass-state gating:** the agent CANNOT write `"state": "passing"` — only the harness, after running
`verification` and capturing exit code 0, may set that terminal state.
