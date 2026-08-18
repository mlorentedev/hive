---
id: lesson-084-a-benchmark-assertion-that-cannot-flake-bound
type: lesson
status: active
created: "2026-08-08"
owner: manu
tags: [hive, lesson, testing, benchmarks, flaky-tests, ci, performance, HIVE-322]
---

# A benchmark assertion that cannot flake: bound a count, derive the bound from measured time

**Context:** ADR-018's whole claim is that commit rate stops tracking write volume. AC3 had to assert that in CI, in two regimes — threads in one process, and separate processes sharing the vault filelock — without becoming the flaky test everyone learns to re-run.
**Problem:** The instinct is to assert throughput: "10 writers finish in under X seconds", or "at least N writes/s". Every such threshold is a bet on the slowest machine that will ever run it, and loses on a loaded CI runner. The repo's existing benchmarks had already conceded this and assert only `> 0` — real measurement harnesses that verify nothing. Meanwhile the property actually worth guarding is not a speed at all: it is that commit *count* is decoupled from write count.
**Solution:** Assert `commits <= ceil(elapsed / tick) + 2`, where `elapsed` is measured by the test itself. That inverts the usual failure direction — a slow machine inflates `elapsed` and therefore *loosens* the bound, and contention can only delay commits, never manufacture them, so the assertion is one-sided against the thing that causes flakes. Two details carry weight. `elapsed` must span the shutdown drain, or that final commit lands outside the bound being asserted. And each test asserts `bound < total_writes` as a **precondition**: if someone later shrinks the load until the bound exceeds the write count, the test fails loudly instead of passing while proving nothing. Results: 200 writes → 1 commit, and 180 writes across 3 processes → 3 commits, stable over five consecutive runs each; the neuter (reverting to synchronous commits) gives 200 against a bound of 13.
**Why:** When a performance claim needs a regression guard, look for the *structural* quantity behind the speed and bound that instead. Here the reconciler cannot physically commit more than once per tick, so the count is bounded by construction and the test just pins it — no timing budget to tune. Deriving the bound from measured elapsed rather than a constant is what makes it portable across machines. And a benchmark whose load can drift below its own discrimination threshold should say so in an assertion; "it passed" and "it could still fail" are different properties, and only the second is worth CI time.
**Tags:** `#testing` `#benchmarks` `#flaky-tests` `#ci` `#performance` `#HIVE-322`
