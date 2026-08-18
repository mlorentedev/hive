---
id: lesson-042-three-timeouts-in-a-chain-aren-t-a-deadline
type: lesson
status: active
created: "2026-05-21"
owner: manu
tags: [hive, lesson, python, asyncio, deadline, timeout, subprocess, concurrency, HIVE-115, ADR-008]
---

# Three timeouts in a chain aren't a deadline

**Context:** Designing HIVE-115 latency-tail re-architecture. Hive's `tool_span` wraps async tool handlers with `asyncio.timeout(60)`. Inside, `Lock.acquire(timeout=30)` enforces lock-wait. Inside that, `subprocess.run(timeout=30)` enforces git subprocess wall-time. Three nested timeouts, each correct at its layer. Live evidence in issue #111: `capture_lesson` elapsed 838s while `ctx.tool_timeout` was 60s — 14× the documented contract. Three failure modes observed in production: invisible hang, client interprets silence as user-rejection, "Server busy" canned string returned while operation still running. See the lesson "SQLite WAL doesn't auto-checkpoint when N processes hold readers" (below) for the SQLite half of the same systemic issue.
**Problem:** `asyncio.timeout` only cancels at `await` points. Once execution enters `asyncio.to_thread(...)`, asyncio cancels the future but cannot interrupt the thread itself — Python has no portable way to inject an exception into a running thread. Inside that thread, `Lock.acquire(timeout=30)` and `subprocess.run(timeout=30)` enforce only their own deadlines. The composition is unsafe: 30s lock wait + 30s subprocess wait + repeated retries can chain into 60+ seconds outside the 60s asyncio envelope. None of the layers act as a true deadline over the composed chain. The deadline is advisory, not enforced. Logs show "ok elapsed_ms=838360" with tool_timeout=60 — the call returned eventually, but 14× over contract.
**Solution:** A real deadline requires ONE supervisor with termination authority over all sub-operations. Pattern: introduce `bounded_call(fn, deadline_s)` helper that holds a context-local registry of `subprocess.Popen` handles + `ThreadPoolExecutor` futures. On deadline expiry: cancel the future (best-effort), then `Popen.terminate()` on each registered child (SIGTERM with 2s grace → SIGKILL on POSIX; `TerminateProcess` on Windows with `CREATE_NEW_PROCESS_GROUP` so child trees go down), surface a real `mcp.protocol.TimeoutError` to the client. Migration cost: `subprocess.run → Popen` in all 5 git callsites (`_helpers._git_commit`, etc.). Tracked as ADR-008 hard-deadline-enforcement, lands in Phase B of HIVE-115. Generalization: ANY tool or API with a documented timeout must enforce it at one layer with kill authority. Per-step timeouts that compose do not compose into a global deadline. This refines the four-layer model in the maintainer's cross-project async-threading pattern §1 — defense-in-depth is correct, but ONE layer must own preemption.
**Tags:** `#python` `#asyncio` `#deadline` `#timeout` `#subprocess` `#concurrency` `#HIVE-115` `#ADR-008`
