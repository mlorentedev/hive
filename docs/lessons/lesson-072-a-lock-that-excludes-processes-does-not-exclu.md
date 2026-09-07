---
id: lesson-072-a-lock-that-excludes-processes-does-not-exclu
type: lesson
status: active
created: "2026-08-07"
owner: manu
tags: [hive, lesson, architecture, concurrency, git, recovery, adr-018, HIVE-322]
---

# A lock that excludes processes does not exclude people

**Context:** Rescoping ADR-018 so the commit reconciler runs in every hive process, not only the daemon. That forced a redesign of crash recovery, and the first draft split it by regime: daemon-side startup recovery would enumerate uncommitted vault paths and commit them, justified as safe because it holds the singleton `daemon.lock`; the non-daemon path would refuse for lack of that lock.
**Problem:** The justification does not hold, and it is the kind that reads as rigorous. `daemon.lock` establishes that no *sibling hive* owns the working tree. It says nothing about a **human** — a maintainer with a half-edited note open in Obsidian while the daemon restarts produces exactly the dirty state recovery cannot distinguish from its own orphaned write. That is [ADR-014](../adr/adr-014-vault-commit-coordination.md)'s original objection (a committer sweeping in-progress work unasked) surviving *inside* the regime the draft called safe. The draft had also introduced a second smell that should have prompted the re-check: recovery behaving differently in two regimes, flagged in its own Consequences as "an asymmetry a reader could mistake for a bug".
**Solution:** Recovery reports and never commits, in either regime — startup surfaces the count and oldest age of uncommitted paths in `vault_health` and stages nothing. What the safe version actually needed was **provenance** (commit only paths hive wrote), and after a crash the in-memory queue that knew them is gone; reconstructing it means persisting on the write path, the exact cost the ADR exists to remove. Report-only is the one resolution needing no provenance at all, because *reporting* a path is safe whoever wrote it. Sweeping stays legal solely as an explicit user act (`vault_commit`, which already uses `git add -A`) — which turns out to be the distinction ADR-014 was drawing all along: it objected to a *timer* sweeping unasked, never to a human asking. Both the asymmetry and the lock reasoning disappeared with it.
**Why:** When a safety argument rests on a lock, name every actor the lock excludes and check that the set is complete — mutual exclusion between *your* processes is not exclusion of everything that writes the same files, and a shared working tree has a human in it. Where the honest fix needs information you cannot reconstruct (here: who wrote this path), prefer the weaker operation that needs no such information over inventing a proxy for it; "report it" is safe under ignorance in a way "commit it" is not. And treat a design that behaves differently in two regimes as a prompt to re-derive both, not as a documentation problem — the asymmetry was the symptom, and the ADR named it one paragraph before the flaw it was pointing at.
**Tags:** `#architecture` `#concurrency` `#git` `#recovery` `#adr-018` `#HIVE-322`
