---
id: lesson-097-a-diagnostic-instrument-must-not-gate-merges
type: lesson
status: active
created: "2026-08-22"
owner: manu
tags: [hive, lesson, testing, ci, flaky-tests, markers, gating, HIVE-388]
---

# A diagnostic instrument must not gate merges

**Context:** Release PR #386 was `master` plus a version bump, and it went red on one job: `tests/test_compat_shim.py::test_classify_cancellation_race` failed with `ConnectionResetError: Connection lost` on Python 3.13 while the 3.12 job on the same commit was green. The test spawns a real hive subprocess and drives twenty cancellation races over JSON-RPC stdio, taking about 41 s of a 228 s suite.
**Problem:** The test's own docstring says it is *not* a pass/fail correctness test but an empirical classifier whose output informed the Phase C design (HIVE-104); its only assertion is that every iteration was accounted for. The connection reset is the child closing the pipe under load, not a shim regression, and if `_compat.py` were broken the test would still pass. A diagnostic instrument was deciding merges, and it could only ever fail for reasons unrelated to what it nominally covers. Retrying the job would have produced a green, which proves the run fitted in the time budget and nothing else. See [[docs/lessons/lesson-076-a-gating-test-that-throws-away-the-only-evide|lesson-076]] for the same test's other defect: when it does fail, it discards the evidence needed to triage it.
**Solution:** A `diagnostic` pytest marker, excluded from the default run alongside `smoke` and `cross_worker`, plus a `make diagnostic` target so the instrument stays runnable on demand (#389). Neither existing marker fit: `cross_worker` still runs and gates in CI, so it would have de-gated nothing, and `smoke` means "against a real provider", which this test does not need. Reusing either would have made one marker mean two things. The change was verified in both directions: the default run deselects one more test than before (64 against 63), and `-m diagnostic` selects exactly one of 969.
**Why:** **A test earns a place in the merge gate by asserting a contract, not by being valuable.** Measurements, classifiers and benchmarks are worth keeping and worth running, but their failure modes are about the environment (load, timing, a pipe closing) rather than about the code under change, so a red from them is noise to the gate and a green from them is not evidence. Give such instruments their own marker with a name that says what they are, keep them one command away, and check a marker change in both directions: that the gate lost exactly the tests you meant, and that the marker selects exactly them.
**Tags:** `#testing` `#ci` `#flaky-tests` `#markers` `#gating` `#HIVE-388`
