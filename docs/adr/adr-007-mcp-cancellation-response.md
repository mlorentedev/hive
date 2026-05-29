---
id: adr-007-mcp-cancellation-response
type: adr
status: active
created: "2026-05-20"
---

## Status
Accepted (2026-05-20) — written after diagnosing a "ghost response" failure mode reported by the user on 2026-05-20 during write-heavy MCP flows.

**Amended 2026-05-20** (same day, post-investigation during HIVE-104 spec drafting): a Sonnet subagent located the upstream framing utility `BaseSession._send_response` at `mcp/shared/session.py:337-349` AND surfaced that `RequestResponder.cancel()` already calls `_send_response` with an `ErrorData` payload at `session.py:148-150` BEFORE `_patched_respond` fires. This means the "best-effort raw send" decision in §1 below is **duplicate-unsafe** under the scenario where the cancellation ErrorData reaches the wire — sending our success after it would create two responses for the same `request_id`, which some MCP clients treat as a protocol error (worse than the current silent-suppress state). An empirical integration test in HIVE-104 (AC-3) will deterministically classify the actual cancellation behavior; if the ErrorData-wins scenario is observed, this ADR's §1 needs a follow-up amendment (best-effort send → conditional suppression with observability counter, possibly via out-of-band notification). §2 (observability), §3 (was emergency opt-out, now removed), and §4 (upstream tracking) stand unchanged.

**Amended 2026-05-20 (#2)** — empirical test resolved: `tests/test_compat_shim.py::test_classify_cancellation_race` ran 20 iterations against a real hive subprocess on Linux. **All 20 iterations showed scenario (a)** — `RequestResponder.cancel()`'s `_send_response(ErrorData)` always wins the race; the wire response is invariably `{"id": N, "error": {"code": 0, "message": "Request cancelled"}}`. **Decision §1 is therefore retracted**: "best-effort raw send" would produce a duplicate response in 100% of cases (protocol violation worse than the status quo). The revised Fase C plan is **observability-only**: keep silent suppression as the delivery behavior, add WARNING `mcp.ghost_response.suppressed_after_cancel_ack` with tool name and request id, increment counter exposed in `vault_health` as `ghost_responses: {total, last_seen, last_tool}`, and document the semantic mismatch in README + docs site (the cancellation ack reaches the wire but the operation may have completed on disk; correct client behavior is `vault_query` to verify state, not retry). §2 (observability) and §4 (upstream tracking) stand unchanged and now carry the full Fase C decision. The `pyproject.toml` minimum-`mcp` pin recommended in Amendment #1 is no longer necessary — Hive does not call `_send_response` directly.

## Context

[adr-005-transport-and-scale.md](adr-005-transport-and-scale.md) documented the original fix in `src/hive/_compat.py`: a monkey-patch on `mcp.shared.session.RequestResponder.respond` that short-circuits when `_completed=True`, preventing the upstream SDK's `AssertionError: Request already responded to` from killing the stdio receive loop after a client cancellation. ADR-005 marked this fix as "Residual risk: None; self-gated, removable when upstream fixes."

After 2 days of operation (and previously 2 months in earlier form), a **new user-visible failure mode** has emerged that ADR-005 did not anticipate.

### The ghost response

User-reported behavior (2026-05-20):

1. Client (Claude Code) sends `vault_write` tool call.
2. Hive handler completes successfully: file is written, git commit succeeds.
3. **No JSON-RPC response reaches the client.**
4. Client tool-call timeout fires (~30–60 s depending on transport config).
5. Client retries the write via a different mechanism (e.g. filesystem MCP) and discovers the file is already at the desired state.
6. User experiences: long inexplicable wait + redundant retry + confusion.

### Root cause

The path from handler return to stdio write (`src/hive/_compat.py:75-90`, `_patched_respond`):

```python
async def _patched_respond(self, response):
    if not self._entered:
        raise RuntimeError("RequestResponder must be used as a context manager")
    if self._completed:                              # ← here
        _log.debug("Suppressed respond() on already-completed responder %s ...")
        return                                       # ← bytes never written
    await original_respond(self, response)
```

`self._completed` is set by the upstream `RequestResponder` when the client sends `notifications/cancelled`. The sequence that produces a ghost response:

1. Client issues `tools/call`. Handler starts. Write window opens (50–500 ms typical).
2. Client-side tool-call timeout fires (or user cancels). Client emits `notifications/cancelled`.
3. Upstream `_received_notification` sets `_completed = True` on the responder.
4. Handler returns successfully. FastMCP awaits `responder.respond(result)`.
5. Patched `respond` sees `_completed=True`, logs a DEBUG line, and **returns silently**.
6. JSON-RPC response is never framed and never written to stdio.
7. Client never sees a response → its retry path activates.

The DEBUG log is below the default INFO level (`src/hive/server.py:435`), so the suppression is **invisible to operators**.

### Why this matters now

The original silent-suppress decision in ADR-005 traded a hard crash (AssertionError kills the receive loop, all subsequent calls hang) for a soft failure (one response is silently dropped). At the time, this was correct — a single dropped response is recoverable; a dead server is not.

But the soft failure has compounded into a real UX problem:

- It is invisible in logs (DEBUG-level log; default level INFO).
- The "phantom retries" by the client are not free — each retry costs another round-trip and another write window.
- It interacts badly with the per-write commit cost addressed in [adr-006-commit-policy.md](adr-006-commit-policy.md): longer writes widen the race window, more cancellations fire, more responses are dropped.
- Multi-process load (per ADR-005's table at >5 sessions) makes it worse.

### The trade-off space

Three options for `_patched_respond` when `_completed=True`:

| Option | Behavior | Failure mode |
|---|---|---|
| **A — Silent suppress** (status quo) | Drop the response, log DEBUG. | Ghost responses; invisible to operators. |
| **B — Fail-loud** | Re-raise the `CancelledError` so FastMCP surfaces an error. | Risks regressing to the original AssertionError if upstream SDK still strict in some code path. |
| **C — Best-effort raw send** | Attempt a direct stdio write of the framed JSON-RPC response, bypassing the upstream `respond()` machinery; on failure, fall back to A. | Best-of-both: client gets the response when possible, falls back safely otherwise. |

## Decision

### 1. Switch to best-effort raw send (Option C)

`_patched_respond` is modified to attempt a direct stdio write of the JSON-RPC response when `self._completed` is `True`, **before** silently returning. Specifically:

- Serialize the response to the wire format (JSON-RPC + framing) using the same encoder upstream uses.
- Write to the underlying stdio writer with a short timeout (e.g. 1 s) to avoid hanging if the writer is itself broken.
- On success: log **INFO** with `tool_name`, `request_id`, write duration. Increment a counter in `ServerContext`.
- On any exception: log **WARNING** with full context (`tool_name`, `request_id`, exception type), then return silently — preserving the ADR-005 crash-prevention guarantee.

### 2. Make suppressions observable

Two visibility changes regardless of whether the raw send succeeds:

- A WARNING-level log line with the prefix `mcp.ghost_response` so users can grep for it.
- `vault_health` surfaces a counter: `ghost_responses: { total: N, last_seen: <timestamp>, last_tool: <name> }`.

Without observability, this failure mode regresses to invisible.

### 3. Document the upstream dependency

Until [modelcontextprotocol/python-sdk#2610](https://github.com/modelcontextprotocol/python-sdk/issues/2610) ships, this shim is load-bearing. The check-in routine `trig_01WQwBTKu9zUDfiobrJn2pyk` (scheduled 2026-05-24, per current operational memory) continues. When the upstream fix lands, **both** `_patched_respond` and `_patched_exit` are candidates for removal — the existing routine covers this.

## Alternatives considered

### A) Status quo (silent suppress)

Rejected. The failure mode is real, user-reported, and invisible. Even if we could not recover the response, making the suppression observable (decision §2 above) is non-negotiable.

### B) Fail-loud — re-raise `CancelledError`

Rejected. The original AssertionError that ADR-005 fixed was load-bearing: it killed the receive loop and froze the server. Re-raising any exception from `respond()` risks the same outcome if the upstream session handling has not changed since ADR-005 was written. The cost of a regression here (server hang) is much higher than the cost of a ghost response (client retry).

### C) **Chosen: Best-effort raw send + observable fallback**

Rejected nothing here. See decision §1.

### D) Wait for upstream SDK fix

Rejected as a sole strategy. We have no control over the upstream timeline. The user pain is real now. This ADR is a bridge until upstream lands, after which both shims can be removed (per ADR-005's "removable when upstream fixes" note).

### E) Buffer responses in a queue, send on next idle tick

Rejected. Adds asyncio lifecycle complexity that is exactly the kind of thing ADR-005 and [adr-006-commit-policy.md](adr-006-commit-policy.md) explicitly avoid. The raw-send path is simpler and addresses the same problem.

## Consequences

- **Most ghost responses recovered**: when the client cancellation arrives after the handler completed but before the response was framed, the raw send should succeed and the client receives a normal response. No retry needed, no user-visible hang.
- **Residual ghost responses become observable**: any case the raw send cannot handle is logged at WARNING with a recognizable prefix, and `vault_health` exposes the count. Future regressions are visible without DEBUG-level logging.
- **Crash-prevention guarantee preserved**: the silent-suppress fallback for raw-send failures keeps the original ADR-005 contract — no upstream AssertionError can kill the receive loop.
- **Marginal new failure mode**: if the raw stdio writer itself is in a bad state (e.g. partially closed, broken pipe), the raw-send attempt could log a WARNING + fall back. This is strictly more observable than the status quo, not less reliable.
- **No new lifecycle**: zero background tasks, zero SQLite changes. Touches `_compat.py` and `vault_health` only.
- **Pairs well with [adr-006-commit-policy.md](adr-006-commit-policy.md)**: once writes are fast (5–15 ms via `commit=False`), the race window shrinks dramatically and ghost responses become much rarer in practice. ADR-007 is the safety net; ADR-006 is the prevention. They are complementary, not redundant.
- **Upstream removal path stays clean**: when `python-sdk#2610` ships, both `_patched_respond` and `_patched_exit` can be deleted together. This ADR does not add anything that prevents that.

## References

- [adr-005-transport-and-scale.md](adr-005-transport-and-scale.md) — documented the original shim; this ADR refines its behavior based on observed failure mode
- [adr-006-commit-policy.md](adr-006-commit-policy.md) — interrelated; ADR-006 shrinks the race window, ADR-007 handles whatever race remains
- Upstream issue: https://github.com/modelcontextprotocol/python-sdk/issues/2610
- Current shim: `src/hive/_compat.py:75-90` (`_patched_respond`)
- Default log level: `src/hive/server.py:435`
- Per-PID logs (introduced PR #92): `~/.local/share/hive/hive-{pid}.log`
- HIVE-104 spec (forthcoming): `specs/HIVE-104-write-throughput/`
