---
id: lesson-071-benchmarking-a-function-that-swallows-its-own
type: lesson
status: active
created: "2026-08-07"
owner: manu
tags: [hive, lesson, benchmarking, measurement, concurrency, git, methodology, HIVE-322]
---

# Benchmarking a function that swallows its own failures, and reading one run as a trend

**Context:** A field report of latency under concurrent use ("4-5 agents writing at once, sometimes 10") drove a measurement of hive's write path for [#322](https://github.com/mlorentedev/hive/issues/322) / ADR-018.
**Problem:** Two distinct ways to produce confident, wrong numbers. First, `_git_commit` is documented as best-effort — "failures are logged but never propagated" — so a commit that silently did not happen is recorded by a naive benchmark as a **very fast** latency sample. A run where contention caused skipped commits would report *better* results than a run where everything worked. Second, the first hypothesis (the untimed `threading.Lock` in the SQLite trackers, issues #288/#289) was measured and **falsified** — p99 of 1.4 ms at 16 processes, because both trackers buffer in memory — which is only visible because it was measured rather than reasoned about. Third, a single run showed coalesced throughput as 101 → 87 → 69 writes/s and that was written into a public issue comment as "degrades with N"; repeated runs showed it flat at ~85-100. Noise read as signal.
**Solution:** The landed spike (`specs/HIVE-322-commit-outbox/spike/commit_contention.py`) asserts the exact commit count and a clean tree after every rung, so numbers cannot be flattered by work that never happened. Its scaling check asserts **sublinearity** (~1.5x throughput for 12x the writers, against a 25%-of-linear budget) rather than flatness, because an equality assertion over noisy subprocess timings is flaky — the first version used "flat within 50%" and failed on its first honest run, partly because the `n=1` baseline was depressed by cold start (now warmed with a discarded run). The incorrect "degrades with N" claim was corrected in the issue rather than quietly dropped.
**Why:** Before benchmarking a function, read its error contract: if it is best-effort by design, the benchmark must assert the work happened, or it measures the failure path as a speedup. Prefer asserting the *shape* of a claim (sublinear, bounded) over its *magnitude* when the measurement is noisy — the shape is what the argument rests on and it survives variance. And a trend needs more than one run: three descending numbers from a single sweep are indistinguishable from scheduler noise.
**Tags:** `#benchmarking` `#measurement` `#concurrency` `#git` `#methodology` `#HIVE-322`
