---
id: lesson-055-cross-os-spikes-catch-bugs-linux-only-testing
type: lesson
status: active
created: "2026-05-31"
owner: manu
tags: [hive, lesson, testing, cross-platform, windows, spikes, dependencies, mcp, asyncio]
---

# Cross-OS spikes catch bugs Linux-only testing misses

**Context:** HIVE-118 Phase C daemon de-risking: built 5 runnable spikes (transport, load, idempotency, resilience, robustness) for the loopback-HTTP + bearer-token daemon and ran them on Linux AND Windows before writing the real `hive serve`.
**Problem:** Four real bugs surfaced only on Windows or were latent on Linux: (1) WinError 32 — deleting the daemon log while the child still held the handle (POSIX allows unlinking an open file; Windows does not); (2) an icacls owner-only ACL check false-matched the `Users` group inside the path `C:\Users\Manu\...`; (3) an unfaithful load model — a sync `time.sleep` tool blocked the event loop and a per-call SQLite connect tanked Windows throughput; (4) `asyncio.CancelledError` subclasses `BaseException`, not `Exception`, so `suppress(Exception)` let a cancelled call escape teardown. Separately, a blanket `uv lock --upgrade` pulled starlette 0.52->1.x (a MAJOR, sitting directly under the daemon's HTTP transport).
**Solution:** De-risk the platform-sensitive layer (transport, file handles, process kill) cross-OS BEFORE building on it — the spike converts "will it work on Windows?" from a project risk into a CI check. Concrete fixes: print the result BEFORE best-effort temp cleanup and kill+wait the child before unlinking (Windows handle lock); abstract owner-only behind explicit POSIX-mode vs Windows-icacls branches and strip the path before scanning ACL principals; model hive's real pattern (async tools + asyncio.to_thread offload + single owning connection) instead of a loop-blocking sync sleep; catch CancelledError explicitly. For deps: scope a relock to the dependency you have a concrete reason + audit for (mcp, capped <2.0 because _compat.py patches private internals) and leave transitive majors (starlette, cryptography) to Dependabot's individually-reviewable PRs — never bury a risky major in a 90-package blanket relock.
**Tags:** `#testing` `#cross-platform` `#windows` `#spikes` `#dependencies` `#mcp` `#asyncio`
