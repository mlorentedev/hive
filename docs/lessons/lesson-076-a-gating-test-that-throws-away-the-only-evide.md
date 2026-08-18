---
id: lesson-076-a-gating-test-that-throws-away-the-only-evide
type: lesson
status: active
created: "2026-08-07"
owner: manu
tags: [hive, lesson, testing, ci, flakiness, diagnosis, subprocess, observability, compat-shim, HIVE-344]
---

# A gating test that throws away the only evidence that could triage it

**Context:** `#339`, a PR touching three spec `.md` files and no code at all, went red on `check (3.13)` — `tests/test_compat_shim.py::test_classify_cancellation_race`, with `check (3.12)` cancelled by `fail-fast`. A re-run of the same commit went green, so the immediate answer was "flake, re-run".
**Problem:** The re-run resolves the PR and settles nothing. The failure surfaced as `ConnectionResetError: Connection lost` on `proc.stdin.drain()` — the hive subprocess had **died**, which is precisely the symptom `_compat.py` exists to prevent. A genuine intermittent shim regression and a CI-load timing flake are therefore indistinguishable from outside the process, and this test is the one gating every merge. The evidence that separates them existed and was thrown away: the traceback itself reports `stderr=<StreamReader 6526 bytes eof ...>`, so the dying server wrote 6.4 KB of diagnostics that the test never dumps on failure. The one run where the bytes mattered is the one run that discarded them, and a re-run cannot recover them because the green pass produces no stderr worth reading.
**Solution:** Filed `#344` rather than closing the loop on the re-run, because "confirmed flaky" was itself an inference the test made unfalsifiable. The fix is to drain and attach the subprocess `stderr` to the assertion message on failure — the pipe is already open and already buffered, so this costs nothing on the passing path. Until then the flake/regression question stays formally open, which is the honest state.
**Why:** A test that spawns a subprocess owns two failure reports: its own assertion, and whatever the child said before it died. Discarding the second is invisible while the test passes and total when it fails. Two generalisations worth carrying: **a "flaky" verdict is a claim about a distribution, and re-running until green is not evidence for it** — it is the one experiment guaranteed not to reproduce the thing you want to explain. And when a test's failure mode is identical to the production bug the code under test defends against, the test cannot be allowed to be low-fidelity: its whole value is telling those two apart. Audit any subprocess-driving test for what it does with the child's stderr *before* trusting it as a merge gate.
**Tags:** `#testing` `#ci` `#flakiness` `#diagnosis` `#subprocess` `#observability` `#compat-shim` `#HIVE-344`
