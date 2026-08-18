---
id: lesson-037-empirical-wire-level-test-must-precede-adrs-a
type: lesson
status: active
created: "2026-05-20"
owner: manu
tags: [hive, lesson, adr, testing, mcp, cancellation, design-process]
---

# Empirical wire-level test must precede ADRs about MCP cancellation/race behavior

**Context:** Drafting ADR-006 (commit policy) and ADR-007 (MCP cancellation response) for HIVE-104. ADR-007 §1 originally decided that `_compat._patched_respond` would attempt a "best-effort raw stdio write" of the JSON-RPC response when `_completed=True` to recover user-visible ghost responses. Promoted to Accepted via vault_write after a multi-turn architectural design discussion. The decision rested on an unstated assumption: that no prior response had reached the wire by the time our patched `respond()` fires.
**Problem:** A 20-iteration empirical classifier (`tests/test_compat_shim.py::test_classify_cancellation_race`, spawns a real hive subprocess on Linux, drives `tools/call` + `notifications/cancelled`, inspects wire bytes) ran AFTER promotion and showed scenario (a) — "ErrorData wins" — in 20/20 cases. `RequestResponder.cancel()` at `mcp/shared/session.py:148-150` always succeeds in calling `_send_response(ErrorData)` before our handler completes; the wire response is invariably `{"id": N, "error": {"code": 0, "message": "Request cancelled"}}`. The "best-effort raw send" decision would have produced a duplicate response (same request_id, success after error) in 100% of cases — a protocol violation worse than the silent-suppress status quo. ADR-007 §1 had to be retracted in Amendment #2 (same day as promotion), and Fase C scope dropped from ~80 LOC raw-stdio-framing to ~30 LOC observability-only.
**Solution:** For any ADR whose decision depends on wire-level behavior under cancellation or race conditions, write the empirical classifier BEFORE the ADR's decision section is locked in. The classifier pattern is cheap (~50 LOC, mirrors the subprocess fixture in `tests/test_transport_recovery.py`): spawn the real server, drive the race over N iterations, classify outcomes into well-defined scenarios, count distribution. Do NOT rely on in-memory mocks of anyio streams for this — stdio framing and cancellation timing only behave faithfully end-to-end via subprocess. Also: ADRs MUST allow Status amendments without supersession (ADR-007 carries two amendments stacked under one Status block); the document a future reader sees is the FULL audit trail of how the decision evolved, not just the latest verdict.
**Tags:** `#adr` `#testing` `#mcp` `#cancellation` `#design-process`
