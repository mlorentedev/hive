---
id: lesson-060-enforce-a-single-owner-invariant-at-the-routi
type: lesson
status: active
created: "2026-06-01"
owner: manu
tags: [hive, lesson, architecture, resilience, single-owner, daemon, failover, concurrency]
---

# Enforce a single-owner invariant at the routing layer, not by making the fallback contend for the owner's lock

**Context:** HIVE-118 Phase C slice 3: adding client auto-reconnect to the `hive client` stdio shim. The shim proxies to a single-owner `hive serve` daemon (which owns git + SQLite under an exclusive singleton `daemon.lock` flock) and falls back to an in-process server when no daemon is reachable. Auto-reconnect makes the backend decision per-call instead of one-shot at startup, which introduces a dual-owner window: the shim can fall back to a write-capable in-process owner and a daemon can (re)appear concurrently.
**Problem:** How to stop the in-process fallback and the daemon from both owning git/SQLite at once. Three options: (1) prefer-daemon per-call routing; (2) prefer-daemon + async teardown of the cached in-process standby when the daemon returns; (3) flock-gate the fallback so it must take the same singleton lock before owning state. Option 3 looks like the "purest" single-owner design but has a fatal inversion: the singleton flock is exclusive AND the daemon declines (exit 0) if it cannot acquire it (the supervised-restart design), so a degraded fallback holding the flock would BLOCK the canonical daemon from ever starting — the opposite of the desired priority. Option 2 adds a genuine mid-call-close race to reclaim resources (idle SQLite connections + reconciler/checkpoint threads) that are already cross-process safe.
**Solution:** Enforce single-ownership at the WRITE-ROUTING layer (option 1): the per-call factory prefers the canonical owner — while the daemon is reachable every call is forwarded to it, so the fallback owner performs zero writes even if it exists. Build the fallback lazily (only on first unreachability) and cache it, so the happy path never creates a second owner at all. Do NOT make the fallback contend for the owner's exclusive lock: when the lock is also the owner's liveness gate (acquire-or-decline), a fallback that grabs it starves the canonical owner. Leave residual sub-second races to the defenses already designed for multi-owner contention (here: idempotency key + .git/index.lock self-heal). General rule: when adding transparent failover to a system with a single-owner write invariant, make the canonical owner win at the routing decision; reserve exclusive locks for the owner's own liveness, and never let a degraded fallback hold a lock that gates the thing it is supposed to defer to.
**Tags:** `#architecture` `#resilience` `#single-owner` `#daemon` `#failover` `#concurrency` `#hive`
