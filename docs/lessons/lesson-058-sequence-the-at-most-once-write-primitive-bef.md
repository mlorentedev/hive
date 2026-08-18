---
id: lesson-058-sequence-the-at-most-once-write-primitive-bef
type: lesson
status: active
created: "2026-05-31"
owner: manu
tags: [hive, lesson, architecture, resilience, idempotency, crash-only, sequencing, daemon]
---

# Sequence the at-most-once write primitive before transparent retry/reconnect

**Context:** HIVE-118 Phase C daemon: deciding the order of two remaining slices — mid-session auto-reconnect in `hive client`, and the idempotency key (ADR-011 §6.2). The proposed order had reconnect first.
**Problem:** Auto-reconnect's value is transparently retrying a forwarded call that failed because the daemon died mid-flight. For a side-effecting write (vault_write/patch), a transparent retry duplicates an already-applied write whenever the original was committed-but-unacked — a case the real-daemon load harness empirically confirmed under SIGKILL. Building reconnect first forces a bad choice: ship it retrying writes (a latent-corruption window masked only by "the daemon isn't activated yet" — a fragile cross-slice coupling), or ship it retrying reads only and then revisit/expand it once idempotency lands (rework).
**Solution:** Build the at-most-once / idempotency primitive BEFORE the retry mechanism that relies on it. It is self-contained (key in the tool envelope + applied-key store + TTL), has zero dependency on reconnect, and was already spike-proven 3/3 cross-OS — so it can be built and tested in isolation. Doing it first lets auto-reconnect be built once, correctly, retrying writes safely, and lets the reconnect test assert the real invariant: a write whose daemon dies mid-flight and reconnects produces exactly one applied write. General rule: when adding transparent retry/failover to a system with side-effecting operations, the correctness invariant (at-most-once) must exist before the mechanism that can violate it — never ship the violator first guarded only by "currently unreachable".
**Tags:** `#architecture` `#resilience` `#idempotency` `#crash-only` `#sequencing` `#daemon`
