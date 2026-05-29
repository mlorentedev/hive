---
id: "hive-issue-75-transport-closed-after-reject"
type: troubleshooting
status: resolved
severity: medium
tags: [bug, mcp-stability, race-condition, cancellation, upstream, issue-75]
created: "2026-05-15"
resolved: "2026-05-15"
owner: manu
---

# MCP transport disconnect after rejecting first tool call

## Summary

Rejecting the very first `mcp__hive__*` permission prompt in a fresh Claude Code conversation poisoned the transport for the rest of the conversation. Subsequent calls to any Hive tool returned `MCP error -32000: Connection closed` and then `No such tool available`. The server process stayed alive (`claude mcp list` reported it as connected), but the per-conversation handle was dead.

Tracked as [#75](https://github.com/mlorentedev/hive/issues/75). Fixed in `hive-vault 1.13.0`.

## Environment

- **OS:** Windows 11 Enterprise 10.0.22631
- **Hive version:** `hive-vault 1.12.0` and below
- **Python:** 3.12
- **Transport:** stdio
- **MCP client:** Claude Code (Opus 4.7)
- **Library versions affected:** `mcp` 1.26.0 and 1.27.1 confirmed; `fastmcp` 3.1.1 → 3.3.1.

## Reproduction

1. Fresh Claude Code session with hive registered via `uvx hive-vault`.
2. Invoke any Hive tool (e.g. `mcp__hive__session_briefing`).
3. Reject the permission prompt.
4. Invoke any other Hive tool — first call returns `Connection closed`, then `No such tool`.
5. `claude mcp list` still reports `hive: ✓ Connected`.

Automated regression test: `tests/test_transport_recovery.py::TestSubprocessTransportRecovery`.

## Root cause

In `mcp.shared.session.RequestResponder.__exit__` (mcp 1.26.0 — 1.27.1):

```python
def __exit__(self, exc_type, exc_val, exc_tb):
    try:
        if self._completed:
            self._on_complete(self)
    finally:
        self._entered = False
        ...
        self._cancel_scope.__exit__(exc_type, exc_val, exc_tb)  # ←
```

When the client sends `notifications/cancelled`, `RequestResponder.cancel()`:
1. Calls `self._cancel_scope.cancel()` — flags the scope.
2. Sends the `ErrorData(code=0, message="Request cancelled")` response.

The handler task catches the `CancelledError` in `_handle_request` (line 766-771 of `mcp/server/lowlevel/server.py`) and returns cleanly. But the `with responder:` block exits with `exc_type=None` while the cancel scope has a pending cancel — anyio's `CancelScope.__exit__` re-raises `CancelledError`. That exception propagates to the receive loop's `anyio.create_task_group()`, kills it, and the server stops reading stdin.

The flakiness comes from a timing window between when the cancellation arrives and when the handler completes. Reproduction on Windows: 2/5 — 4/5 runs of the subprocess test fail without the patch.

## Fix

`src/hive/_compat.py` monkey-patches `RequestResponder.__exit__` to swallow the spurious `CancelledError` *only* when the responder is already completed (response sent) AND the exception is anyio's cancelled class. Both gates make the patch self-limiting: when upstream fixes the bug, the patch never fires.

```python
try:
    self._cancel_scope.__exit__(exc_type, exc_val, exc_tb)
except BaseException as exc:
    cancelled_cls = anyio.get_cancelled_exc_class()
    if self._completed and isinstance(exc, cancelled_cls):
        return  # swallow — issue #75
    raise
```

Applied at import time from `hive/server.py`. The `apply()` function is idempotent and degrades to a warning if `RequestResponder` is renamed/removed upstream.

## Verification

- `tests/test_transport_recovery.py`: 4 tests, 5/5 runs green after the patch (without the patch, the subprocess tests fail 2-4 / 5 times).
- Lifecycle middleware in `src/hive/_diagnostics.py` logs each cancellation under DEBUG so the next incident is diagnosable from `~/.local/share/hive/hive.log`.

## Upstream report

Drafted for `modelcontextprotocol/python-sdk`. <!-- The full upstream bug-report draft (upstream-mcp-cancellation-race) lives in the maintainer's cross-project knowledge store; not linked here to preserve repo->store independence (knowledge-placement directionality invariant). -->

## Lessons

- **Race conditions in stdio MCP servers manifest only at the transport boundary.** In-memory FastMCP tests are inadequate to characterize cancellation behaviour; you need a subprocess driving real JSON-RPC.
- **`anyio.CancelScope.__exit__` re-raises on clean exit when the scope was cancelled.** Library code that responds to a cancellation and then exits the scope must either swallow the re-raised exception or propagate it deliberately.
- **A monkey-patch is acceptable technical debt** when the alternative is an unbounded wait for upstream and the patch is self-gated on the exact failure mode.
