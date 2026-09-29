---
tags: [spec, tasks, templates]
created: "2026-09-28"
---

# Tasks - HIVE-437-stable-local-mcp-endpoint

> TDD order. One task = one focused commit. Tick as you go. Reorder freely while spec is in `draft` state; freeze once you start `implementing`.
>
> **FROZEN (2026-09-28).** `/spec check` verdict: **PASS**. AC1 maps to
> endpoint resolution; AC2 to stable binding and restart continuity; AC3 to the
> lightweight entrypoint and latency gate; AC4 to explicit failure; AC5 to relay
> protocol coverage; AC6 to credential publication; AC7 to the restart smoke.
> The refactor task is declared housekeeping and all other implementation tasks
> carry explicit acceptance-criterion markers.
>
> **Inline markers** (optional, additive — borrowed from `github/spec-kit`, adapt-not-adopt per #141):
> - `[P]` — this task has **no dependency on another unchecked task**, so it is safe to run in parallel (fan out to a `Workflow`, or just batch). TDD chains (test → implement → refactor of the *same* behavior) are sequential and must NOT carry `[P]`; independent behaviors can.
> - `[AC<n>]` — this task helps satisfy **acceptance criterion #`<n>`** from `proposal.md`. Lets `/spec check` map coverage deterministically; omit it and the check falls back to semantic judgment.

## Setup

- [x] Branch created from `master`: `feat/stable-local-mcp-endpoint`
- [x] `proposal.md` is complete and acceptance criteria are testable
- [x] No open questions left in `proposal.md` "Risks / open questions"

## Implementation

- [x] [AC1] Add failing table-driven tests in `tests/test_endpoint.py` for the
  v1 UID/SID port vectors, private-range invariant, environment override, and
  invalid override rejection.
- [x] [AC1] Add `src/hive/_endpoint.py` with stdlib-only identity and endpoint
  resolution; run `uv run python -m pytest tests/test_endpoint.py -q`.
- [ ] [AC2] [AC6] Add failing daemon unit tests for persistent token reuse,
  atomic publication, permission verification failure, and fixed-port conflict.
- [ ] [AC2] [AC6] Implement credential loading/creation and deterministic default
  binding in `src/hive/_daemon.py`; keep `daemon.port` diagnostic-only.
- [ ] [AC3] [AC4] Add failing tests proving the console entrypoint selects the
  client path without importing `hive.server`/FastMCP and refuses unavailable or
  invalid daemon state without fallback.
- [ ] [AC3] Add `src/hive/cli.py`, repoint both console scripts in
  `pyproject.toml`, and replace the FastMCP proxy in `src/hive/_client.py` with a
  stdlib-only relay.
- [ ] [AC5] Add failing relay tests for JSON, notifications, SSE frames,
  `Mcp-Session-Id`, and negotiated `MCP-Protocol-Version`; implement the minimum
  HTTP bridge to pass them.
- [ ] [AC2] [AC7] Add a black-box restart test in `tests/test_daemon.py` that
  reuses one URL and token across two daemon processes and calls both
  `tools/list` and `vault_health`.
- [ ] [AC3] Add a cold subprocess benchmark test with a one-second initialize
  deadline, isolated from the full server import graph.
- [ ] [AC1] [AC4] [AC6] Update `README.md` and CLI help for
  `HIVE_DAEMON_PORT`, persistent token handling, explicit no-fallback behavior,
  and the temporary diagnostic role of `daemon.port`.
- [ ] Refactor only after all targeted tests are green; keep production
  functions below 40 lines and avoid new dependencies.

## Closing

- [ ] Every acceptance criterion from `proposal.md` is covered by at least one test
- [ ] Every acceptance criterion has a matching entry in `features.json` (see below) with a non-vacuous verification command
- [ ] Type checks pass
- [ ] Lint passes
- [ ] No unrelated changes in the diff (no scope creep)
- [ ] `verification.md` filled in
- [ ] PR opened referencing this spec folder

## Machine-readable features

This spec emits a sibling `features.json` (alongside this file) following [[pattern-feature-list-as-primitive]]. The JSON is the harness-facing contract: each acceptance criterion maps to ≥1 feature with `id`, `behavior`, `verification` (executable command), `state` (lifecycle), and `evidence` (harness-captured output).

**Pass-state gating:** the agent CANNOT write `"state": "passing"` — only the harness, after running `verification` and capturing exit code 0, may set that terminal state. Reviewers must reject PRs where features.json contains `passing` entries with empty `evidence`.

Minimal `features.json` skeleton (drop into `<repo>/specs/HIVE-437-stable-local-mcp-endpoint/features.json`):

```json
[
  {
    "id": "HIVE-437-stable-local-mcp-endpoint-f1",
    "behavior": "<one-line copy of an acceptance criterion>",
    "verification": "<single shell command; exit 0 means pass>",
    "state": "pending",
    "evidence": ""
  }
]
```
