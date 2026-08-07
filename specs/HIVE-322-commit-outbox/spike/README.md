# HIVE-322 spike

One runnable measurement behind ADR-018. Unlike the HIVE-118 spikes (which
de-risked a design before building it), this one establishes the
**pre-implementation baseline** the commit queue has to move, and is the harness
the eventual AC3 regression test grows out of.

```bash
uv run python specs/HIVE-322-commit-outbox/spike/commit_contention.py
```

Exit `0` = the integrity and sublinearity assertions held. Takes a few minutes:
each ladder rung builds a fresh 1300-file git vault.

## commit_contention.py

Measures write latency and throughput across three regimes, on a vault sized to
the real one's order of magnitude (commit cost tracks index size):

| regime | stands for |
|---|---|
| **processes** | pre-daemon deployment — one hive process per MCP session, serialized across the filelock at `vault/.git/hive.lock` |
| **threads** | the single-owner daemon (ADR-011) — cross-process filelock uncontended, only `_GIT_LOCK` serializes |
| **coalesced** | HIVE-104's `commit=False` + `vault_commit` — bounds what batching alone buys |

### What it asserts

1. **Every expected commit landed.** `_git_commit` swallows failures by design
   ("logged but never propagated"), so a silently skipped commit would be
   recorded as a very *fast* latency sample and flatter the results. Each rung
   asserts the exact commit count and a clean tree; a mismatch raises rather
   than reporting a pretty number.
2. **Throughput is sublinear in writer count.** If concurrency helped, N times
   the writers would approach N times the throughput. The check allows 25% of
   linear and observes ~1.5x for 12x the writers.

The sublinearity check is deliberately *not* "flat within X%". Absolute
throughput is noisy — a handful of writes per worker, real `git` subprocesses,
page-cache effects — so an equality assertion would be flaky. Sublinearity is
the claim that actually matters and it survives the noise. A warmup run is
discarded before measuring so the `n=1` baseline is not depressed by cold start.

### Reading the output

The finding is the *shape*, not the absolute numbers, which move run to run:

- **Per-write p50 stays flat while p95/max grow with N.** Median writers sail
  through; the tail queues. Throughput does not improve.
- **Threads narrow the spread.** The daemon converts a lottery (fast median,
  ~1.5 s worst case) into a fair queue — better worst case, same ceiling.
- **Coalescing raises the ceiling ~3x and holds it** across the ladder. It hits
  the serialized commit less often; it does not remove the serialization.

One observed run is recorded in `../verification.md` as the documented baseline.
Expect run-to-run variance of tens of percent in absolute throughput — compare
shapes and ratios, not individual cells.
