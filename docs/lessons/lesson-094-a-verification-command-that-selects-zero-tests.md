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
**Problem:** Zero evidence reaches a criterion two ways, and only one of them is silent. Both were measured on `HIVE-384`, by re-running its commands at the commits that carried them.

- **The selector resolves and every test skips — exit `0`, and this one really is invisible.** AC5's live half is `uv run pytest tests/test_smoke.py::TestWorkerDispatch -m smoke`. Today it collects two tests, runs neither (no worker endpoint is configured), and **exits 0**. Nothing in that outcome distinguishes "proved" from "never executed", and `verification.md` marks AC5 `[x]` — on the strength of the *other* half, the settings test, which does pass.
- **The selector matches nothing — pytest refuses, loudly.** At the 4.0.0 release commit (`dff8af4`), **all eight** of the spec's scaffolded commands selected zero tests. They did not sneak through: six exited **`4`** (unresolvable path or nodeid — `tests/test_provider_removal.py` never existed) and two exited **`5`** (`-k` deselected everything). pytest signalled every one.

So the second case is not pytest failing to report. **It is the observer dropping the report** — reading the human-readable tail, where "no tests ran in 0.14s" looks benign next to a red failure, or piping the command so the exit status becomes the pipe's last stage instead of pytest's. This was reproduced live while writing this lesson: `pytest ... | tail -3; echo $?` printed `0` for a command that had exited `4`.

What did *not* happen, and is worth stating because the first draft of this lesson asserted it: 4.0.0 did not ship criteria marked complete on broken commands. At `dff8af4` every `features.json` row read `pending` and every `verification.md` box was unchecked. The boxes and the working commands arrived together in `4092bf4`. The near miss is that the boxes were checked in the same commit that made the commands runnable — so nothing ever re-read them independently, and `features.json`'s states were left at `pending`, where they still are.

**Solution:** Treat pytest's exit status as the gate and never let it reach a pipe. Exit `4` and `5` are the command telling you it proved nothing — fail on them explicitly rather than eyeballing the summary line. Then record the **passed** count next to the criterion, not the collected count: AC5's smoke command collects 2 and proves 0, so a collection count would have called it evidence. `verification.md` carries this finding about its own machinery, because a gate this narrowly missed is worth writing down inside the artifact it gates.

**Why:** Same family as [[docs/lessons/lesson-087-a-green-review-check-can-mean-did-not-review|lesson-087]] — **a signal that reports on its own execution rather than on its subject**. The list in this repo alone: `shutil.which` proving a name resolves rather than that the thing behind it runs (077); a config default equal to the real value, so the test passes without the config (086); a review bot reporting SUCCESS when it declined to review (087); publish jobs reported `skipped` on a rate limit (089); and now a skipped test reported as a passing criterion. **When a check is a gate, ask what it returns when the thing it gates did not happen** — if the answer is "the same thing as success", it is not a gate. Note where the boundary actually fell here: pytest is a *good* gate for a broken selector and a bad one for a universal skip, and the tooling built on top erased the difference. `pytest --co -q | wc -l` is the trap in miniature — it counts output lines, and a zero-selection run still prints two, so it reports `2` for nothing at all while `wc`'s exit status hides pytest's `4`.
**Tags:** `#sdd` `#verification` `#testing` `#pytest` `#false-positive` `#definition-of-done` `#HIVE-384`
