---
id: lesson-045-telemetry-is-the-design-not-an-afterthought
type: lesson
status: active
created: "2026-05-21"
owner: manu
tags: [hive, lesson, observability, telemetry, design, instrumentation, HIVE-115, phase-a]
---

# Telemetry IS the design, not an afterthought

**Context:** Investigating root causes of HIVE-115. The 838s `capture_lesson` event from issue #111 was invisible until manual log archaeology surfaced one INFO line buried in a per-PID log file: `mcp ok ... tool=capture_lesson id=11 elapsed_ms=838360`. The configured `tool_timeout=60` value lived in code, but the actual elapsed time was only logged at INFO with no structured fields. WAL bloat (4.1 MB `relevance.db-wal`) was invisible until manual `ls` showed the size — no metric exposed it. Lock contention with obsidian-git was suspected only by correlating obsidian-git's `autoSaveInterval=10` config with hive's 30s freezes. None of these were observable from inside the system; all required external archaeology.
**Problem:** A tool with a documented contract (e.g. `HIVE_TOOL_TIMEOUT=60`) but no structured telemetry for the actual elapsed time, the wait breakdown, or the contract violations is operating blind. When something goes wrong, debugging requires log spelunking instead of metric query. By the time someone notices a 14-minute hang in a per-PID log file, the user has already abandoned the session and lost confidence. Worse, decisions about whether to re-architect become subjective ("seems slow lately") instead of measured. The prior plan for HIVE-115 included a Phase B (Outbox+Reconciler) that depends on sizing the bounded_call grace period correctly — without distribution data for `last_git_lock_wait_ms`, the grace period would be guessed, not measured.
**Solution:** Any tool/api with a configured deadline needs structured logging of `{deadline, elapsed, wait_breakdown}` from day one. For HIVE-115 Phase A: emit one structured `mcp.lock_contention` log per `_GIT_LOCK.acquire` attempt with `{tool, lock, waited_ms, abandoned}`; surface `wal_size_bytes`, `competing_pid_count`, `last_git_lock_wait_ms` (rolling N=100), `obsidian_git_present` via `vault_health(include_runtime=True)`. Treat instrumentation as a SHIPPING REQUIREMENT alongside the fix, not as follow-up work. Without these metrics, the gate condition for Phase B advancement ("≥10 events of waited_ms>5000, or p99 wal_size > 5 MB, or ≥1 tool_timeout_exceeded") cannot be evaluated objectively. Generalization: when a design choice introduces a contract (deadline, capacity, freshness), the same PR must introduce its observability — they are inseparable. Cross-ref: the maintainer's cross-project phased-redesign-with-telemetry-gates pattern documents the gating discipline; the lesson "Three timeouts in a chain aren't a deadline" (above) is the canonical "broken contract due to lack of enforcement and observability" pair.
**Tags:** `#observability` `#telemetry` `#design` `#instrumentation` `#HIVE-115` `#phase-a`
