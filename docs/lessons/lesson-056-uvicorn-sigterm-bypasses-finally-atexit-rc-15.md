---
id: lesson-056-uvicorn-sigterm-bypasses-finally-atexit-rc-15
type: lesson
status: active
created: "2026-05-31"
owner: manu
tags: [hive, lesson, python, uvicorn, signals, daemon, fastmcp]
---

# uvicorn SIGTERM bypasses finally/atexit (rc -15)

**Context:** HIVE-118 slice 2 hardening: tried to make the `hive serve` daemon clean up its published token/port state files on graceful shutdown by wrapping `server.run()` (FastMCP → `await uvicorn.Server.serve()`) in a try/finally and also registering an atexit handler.
**Problem:** Neither the `finally` block nor the `atexit` handler ran on SIGTERM, even though uvicorn logged a clean "Application shutdown complete". A direct probe showed the daemon's `Popen.returncode` was -15: the process exits via the SIGTERM signal itself after uvicorn's asyncio signal handler runs its graceful ASGI shutdown — control never unwinds back through our frame, and atexit is skipped on signal death. SIGTERM is exactly how systemd `stop`, `Popen.terminate()`, and `kill` stop the process, so the cleanup was effectively dead code.
**Solution:** Do not rely on finally/atexit for cleanup in a uvicorn-hosted daemon stopped by SIGTERM. Either (a) treat leftover state as benign and make the readers robust (here: the client TCP-probes the port and falls back; a daemon restart overwrites the state files), or (b) if cleanup on stop is truly required, install your own SIGTERM handler — but that means cooperating with / replacing uvicorn's handler, which risks breaking its graceful shutdown. We chose (a): document it as by-design and drop the finally/atexit attempt.
**Tags:** `#python` `#uvicorn` `#signals` `#daemon` `#fastmcp`
