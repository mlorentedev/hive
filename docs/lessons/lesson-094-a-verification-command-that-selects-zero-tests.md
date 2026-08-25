---
id: lesson-094-a-verification-command-that-selects-zero-tests
type: lesson
status: active
created: "2026-08-23"
owner: manu
tags: [hive, lesson, sdd, verification, testing, pytest, false-positive, definition-of-done, HIVE-384]
---

# A verification command that selects zero tests marks the criterion complete

**Context:** Spec-driven development records each acceptance criterion in `features.json` with a `verification` command — the pytest invocation that proves it. The command is the evidence. It is written when the criterion is written, and read back when the criterion is closed.
**Problem:** **Four of the eight commands in `HIVE-384`'s `features.json` matched nothing when run.** A `pytest` invocation naming a class that does not exist, or a path that moved, exits **0** and reports "no tests ran". Read as a gate, that is indistinguishable from a pass — and 4.0.0 shipped with those four criteria marked complete on exactly that basis. The worst of them, AC7, turned out to have no test at all rather than a broken selector: the criterion was never verified by anything, in any form. Writing it in PR 2 found two live credential leaks ([[docs/lessons/lesson-093-the-credential-leaks-through-the-surfaces-nobody-calls|lesson-093]]).
**Solution:** Re-run every command in `features.json` under `--collect-only` and confirm it selects a non-zero number of tests. Cheap, mechanical, and it is the only check that distinguishes "proved" from "never executed". Record the *count* alongside the criterion — "10 tests", "15 tests" — so a later reader can see the proof had substance rather than a green tick. `verification.md` carries the finding about its own machinery, because a gate that failed silently once is worth writing down inside the artifact that failed.
**Why:** Same family as [[docs/lessons/lesson-087-a-green-review-check-can-mean-did-not-review|lesson-087]] — **a signal that reports on its own execution rather than on its subject**. The list in this repo alone: `shutil.which` proving a name resolves rather than that the thing behind it runs (077); a config default equal to the real value, so the test passes without the config (086); a review bot reporting SUCCESS when it declined to review (087); publish jobs reported `skipped` on a rate limit (089); and now a verification command that selects nothing. Every one exits zero, and every one asserts something true about itself while asserting nothing about what it gates. **When a check is a gate, ask what it returns when the thing it gates did not happen** — if the answer is "the same thing as success", it is not a gate. The general remedy is to make absence loud: `--collect-only` counts, `-p no:randomly --strict-markers`, `pytest --co -q | wc -l` in CI, or a non-zero-selection assertion in the harness that reads `features.json`.
**Tags:** `#sdd` `#verification` `#testing` `#pytest` `#false-positive` `#definition-of-done` `#HIVE-384`
