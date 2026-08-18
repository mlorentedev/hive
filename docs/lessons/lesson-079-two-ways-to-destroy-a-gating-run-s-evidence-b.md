---
id: lesson-079-two-ways-to-destroy-a-gating-run-s-evidence-b
type: lesson
status: active
created: "2026-08-08"
owner: manu
tags: [hive, lesson, ci, testing, debugging, evidence, git, editable-install, verification]
---

# Two ways to destroy a gating run's evidence, both self-inflicted

**Context:** Same session as the lesson above. Chasing that red `make check` took several 3.5-minute full-suite runs, and the first two data points were both worthless for different reasons.
**Problem:** Two distinct evidence-destroying mistakes. **First, the traceback was piped away:** the gating run was invoked as `make check 2>&1 | tail -25`, and pytest prints the `FAILURES` section *before* the coverage table and summary, so `tail` kept the part that says a test failed and discarded the part that says why. Four subsequent runs were spent theorising about which assertion broke — a question the discarded output had already answered. **Second, a later run was invalidated by a branch switch:** `make check` was launched, then `git checkout origin/master` to make an unrelated one-line docs fix, then back. Master does not merely differ from the feature branch, it **does not contain `_commit_queue.py` or `test_commit_queue.py` at all** — they are additions on that branch. So the editable-installed source tree lost the module under test mid-suite and regained it minutes later. Already-imported modules kept running from memory, but any test spawning a subprocess that imports `hive` got master's code, and coverage reads source off disk at report time. A green result there would have been luck, not proof.
**Solution:** Re-ran with `2>&1 | tee <log> | tail -3` — full output preserved on disk, short summary in the terminal — and re-ran the gate a final time with the tree parked, touching nothing until it reported. Read the result from the log tail, not from the harness's completion summary, which had already reported "exit code 0" for a run whose own output said `1 failed`.
**Why:** A gating run is the one command whose output you cannot reconstruct cheaply, so it is the one command that must never be truncated — `tee` costs nothing and `tail` on a 200-line failure section is a guaranteed second run. The branch-switch failure generalises further: an editable install means the working tree *is* the installed package, so a long-running test process and a `git checkout` are two writers to the same state. Treat a running gate as holding a lock on the worktree. Both mistakes share a shape worth naming — they produce results that still *look* like results, so the cost is not a missing answer but a confident wrong one built on top of it. This is the same failure #344 records for a test that discards its own subprocess stderr; here the discarding was done by the invocation rather than the test.
**Tags:** `#ci` `#testing` `#debugging` `#evidence` `#git` `#editable-install` `#verification`
