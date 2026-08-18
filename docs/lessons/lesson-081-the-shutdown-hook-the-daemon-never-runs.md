---
id: lesson-081-the-shutdown-hook-the-daemon-never-runs
type: lesson
status: active
created: "2026-08-07"
owner: manu
tags: [hive, lesson, shutdown, daemon, uvicorn, asgi, lifespan, signals, HIVE-322]
---

# The shutdown hook the daemon never runs

**Context:** ADR-018 AC6 requires the commit queue to drain on clean shutdown, and named two triggers to wire: the daemon's signal handling, and the stdio server's lifespan teardown.
**Problem:** The obvious implementation — a `finally` block around the serve call, or an `atexit` handler — is silently broken on the trigger most likely to fire. `_daemon.py` already documented why, in a comment written to explain something else entirely: it deliberately does *not* clean up its token and port files on stop, because "uvicorn's SIGTERM handling exits the process via the signal (rc -15), bypassing `finally`/`atexit`, and SIGTERM is exactly how systemd / kill stop the daemon". A drain hung off either hook would have passed every local test, passed CI, and then quietly discarded queued work on every `systemctl stop` in production — the one path that matters most, and the one hardest to notice failing, since a missed commit looks identical to a write that simply had not ticked yet.
**Solution:** Hung the drain off a FastMCP `lifespan` instead. That is a single seam covering *both* triggers the AC listed separately: the stdio run fires it, and the daemon's `http_app(lifespan="on")` fires it during uvicorn's graceful shutdown, before any signal-driven exit. The test drives the real ASGI lifespan the way uvicorn does rather than reaching for a private hook, so it exercises the production path. The drain is best-effort — a shutdown must not fail because a commit failed, and anything uncommitted stays on disk where the uncommitted-path report picks it up.
**Why:** Before wiring anything to process teardown, find out which teardown hooks the process actually reaches — `finally` and `atexit` are not guaranteed, and an embedded server's signal handling is exactly the kind of thing that skips them. The specific tell here is worth generalising: **a codebase that deliberately avoids cleanup on a path has already discovered the constraint**, and that knowledge is usually parked in a comment explaining an unrelated decision. Grep for what the code refuses to do before designing something that depends on it doing that. Also note the AC asked for two triggers and the right answer was one — the ASGI lifespan is the shared seam under both transports, so implementing the requirement literally would have produced two mechanisms where one composes.
**Tags:** `#shutdown` `#daemon` `#uvicorn` `#asgi` `#lifespan` `#signals` `#HIVE-322`
