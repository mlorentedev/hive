---
id: lesson-059-fastmcp-streamable-http-cancels-in-flight-han
type: lesson
status: active
created: "2026-06-01"
owner: manu
tags: [hive, lesson, daemon, fastmcp, uvicorn, restart-on-upgrade, idempotency, spikes, crash-only]
---

# FastMCP streamable-http cancels in-flight handlers on shutdown — no true drain

**Context:** HIVE-118 slice 1.3 restart-on-upgrade spike (spike/upgrade_spike.py): wanted the daemon to "drain/swap" — finish in-flight tool calls before restarting into an upgraded version. Spiked owning the uvicorn Server via the PUBLIC mcp.http_app() and setting server.should_exit (not signals).
**Problem:** should_exit gives a CLEAN process exit (serve() returns, exit 0 — strictly better than SIGTERM's rc -15), but the MCP session manager CANCELS the active tool handler on lifespan shutdown: measured client_got_response=False AND handler_completed_serverside=False. So neither the client ack nor the server-side write completes — a true in-flight drain is NOT deliverable over the streamable-http transport. (Separately the spike proved importlib.metadata.version() DOES reflect an in-place dist-info swap mid-process with no invalidate_caches, so version-drift detection is viable stdlib-only.)
**Solution:** Restart-on-upgrade must be CLEAN-STOP-ONLY, not drain/swap: drift-poll via importlib.metadata → own the uvicorn Server through the public http_app() → should_exit → exit 0 → supervisor restarts into the new code. In-flight safety does NOT come from draining (the transport can't); it comes from idempotency (at-most-once key) + auto-reconnect (the client safely retries the cut call). This is exactly why idempotency (slice 2, ADR-013) was sequenced before restart-on-upgrade. General rule: don't fight a transport for a graceful drain it structurally cannot deliver — make the cut safe instead.
**Tags:** `#hive` `#daemon` `#fastmcp` `#uvicorn` `#restart-on-upgrade` `#idempotency` `#spikes` `#crash-only`
