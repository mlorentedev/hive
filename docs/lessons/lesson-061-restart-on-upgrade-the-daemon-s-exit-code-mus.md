---
id: lesson-061-restart-on-upgrade-the-daemon-s-exit-code-mus
type: lesson
status: active
created: "2026-06-02"
owner: manu
tags: [hive, lesson, daemon, systemd, uvicorn, process-supervision, fastmcp, exit-codes]
---

# Restart-on-upgrade: the daemon's exit code must match the supervisor's restart policy

**Context:** Implementing HIVE-118 slice 1.3: a long-lived daemon that should adopt a newly installed package version (after `uv tool upgrade`) by restarting into the new code under a process supervisor.
**Problem:** The intuitive design is "detect drift, stop cleanly, exit 0, let the supervisor restart". But under a systemd `Restart=on-failure` unit, exit 0 is a SUCCESS and the supervisor does NOT restart — so a clean exit(0) silently fails to pick up the upgrade. Forcing a restart by switching the unit to `Restart=always` + `RestartPreventExitStatus=` to suppress the no-op-decline case couples the code's exit-code contract to the unit file (fragile, two places to keep in sync). Separately, owning the server matters: FastMCP's `mcp.run(transport="http")` can only be stopped by a signal, which exits via the signal (rc -15) and cuts the in-flight handler.
**Solution:** Make the exit code carry intent and let one restart policy serve all cases. Under `Restart=on-failure`: a drift-triggered clean stop exits NON-ZERO (75 / EX_TEMPFAIL) so the supervisor relaunches into the new code; a graceful signal stop (`systemctl stop`) and a singleton-decline no-op exit 0 (no restart, no loop). No `RestartPreventExitStatus=` coupling. For the cooperative stop itself, OWN the server: build the app from the public `mcp.http_app()`, create your own `uvicorn.Server`, and set `should_exit=True` from a background drift poll — uvicorn drains (bounded by `timeout_graceful_shutdown`) and `serve()` returns, vs a signal that cuts the call. A true in-flight drain is unreachable over streamable-http (the handler is cancelled on lifespan shutdown), so at-most-once idempotency + client auto-reconnect must already cover the cut call. Also: re-raise SystemExit before any catch-all CRITICAL crash log so a clean restart code is not mislabelled a crash.
**Tags:** `#daemon` `#systemd` `#uvicorn` `#process-supervision` `#fastmcp` `#exit-codes`
