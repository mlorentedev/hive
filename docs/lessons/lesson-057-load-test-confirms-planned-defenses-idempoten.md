---
id: lesson-057-load-test-confirms-planned-defenses-idempoten
type: lesson
status: active
created: "2026-05-31"
owner: manu
tags: [hive, lesson, load-testing, daemon, idempotency, crash-safety, fastmcp]
---

# Load test confirms planned defenses (idempotency, init_timeout) hit real cases

**Context:** HIVE-118 Phase C: ran a real-daemon load/bug-hunt harness against `hive serve` — N concurrent sessions doing mixed vault_query reads + vault_write (auto-commit) writes + /status polls, up to 64 sessions x 40 calls, plus a proxy-FD probe and a mid-write SIGKILL durability probe.
**Problem:** Wanted to find bugs in the new single-owner daemon design under load. The core stress found NONE: zero lost writes, git fsck clean with exact commit counts, metrics accurate under 64-way concurrency, no fd leak (direct AND proxy paths — the proxy tears down its per-request backend session cleanly), latency backpressure-bounded. But two probes surfaced behaviors that map to ALREADY-PLANNED defenses, confirming they target real problems: (1) a raw fastmcp Client with no init_timeout hangs when its server dies mid-session; (2) a mid-write SIGKILL leaves git uncorrupted but can produce a committed-but-unacked write (commits=22 vs client-acked=21), so a naive retry would duplicate-append.
**Solution:** Treat these as validation, not new bugs. (1) confirms why the client shim sets init_timeout (H1 hardening) — without it the shim would hang on a wedged/dead daemon; it also argues for revisiting a per-request timeout for the wedge-after-initialize case. (2) confirms why ADR-011 §6.2 reserves a per-write idempotency key: under crash, vault_write is at-least-once (commit can outlive its ack), so the key is needed to make a retry a no-op (safe for append mode). A good load suite verifies that planned defenses attack real failure modes — both were demonstrated with real traffic. Crash-safety of git itself (fsck clean, no stale index.lock) holds under SIGKILL.
**Tags:** `#load-testing` `#hive` `#daemon` `#idempotency` `#crash-safety` `#fastmcp`
