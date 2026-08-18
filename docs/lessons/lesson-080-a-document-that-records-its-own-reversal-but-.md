---
id: lesson-080-a-document-that-records-its-own-reversal-but-
type: lesson
status: active
created: "2026-08-07"
owner: manu
tags: [hive, lesson, specs, documentation-drift, sdd, review, HIVE-322]
---

# A document that records its own reversal, but leaves the reversed text reading as current

**Context:** `specs/HIVE-322-commit-outbox/proposal.md` was about to be frozen so implementation could start against it. Reconciling the documents that mirrored ADR-018's `proposed` status meant reading it end to end.
**Problem:** The spec stated two incompatible designs for the same acceptance criterion. Its "Recovery of a dropped queue entry" bullet described daemon-side startup recovery issuing a commit while holding `daemon.lock`, written in the present tense as a resolved decision. Further down, under a "second pass" heading, the same file recorded that review had **overturned** exactly that design — `daemon.lock` excludes sibling hives but not a human editing in Obsidian — and that startup now reports and never commits, in either regime. AC9 and ADR-018 §3 agreed with the second version. Nothing marked the first as dead. The file was honest about its history and misleading about its conclusion, and an implementer reading top-down meets the withdrawn design first. Two milder instances of the same drift sat alongside it: an out-of-scope entry still scoping the refusal to "outside the daemon", and a consequence still calling `vault_health` the recovery signal "in the non-daemon regime".
**Solution:** Marked the superseded bullet explicitly — "no part of this bullet is current" — and pointed it at the entry that overturned it, rather than deleting it. The reversal is load-bearing history and deserves something to point at; what it does not deserve is to read as the answer. Corrected the two derived statements in the same pass, and recorded the whole thing in the PR body rather than folding it in silently.
**Why:** Appending a correction is not the same as retracting the thing corrected, and a document that grows by accretion will state both. The failure mode is specific to *resolved-question* sections: a bullet marked `~~struck~~ **Resolved**` reads as settled, so a later reversal recorded elsewhere does not visually compete with it. When a decision reverses, edit at the original site — a pointer forward costs one line and is the only thing a top-down reader will see in time. Worth checking whenever a spec is about to freeze: the freeze is what converts "documentation drift" into "the implementer built the wrong thing".
**Tags:** `#specs` `#documentation-drift` `#sdd` `#review` `#HIVE-322`
