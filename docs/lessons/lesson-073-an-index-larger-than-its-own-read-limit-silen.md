---
id: lesson-073-an-index-larger-than-its-own-read-limit-silen
type: lesson
status: active
created: "2026-08-07"
owner: manu
tags: [hive, lesson, memory, context, truncation, silent-failure, tooling]
---

# An index larger than its own read limit silently drops the part that matters

**Context:** Adding one line to the project's auto-memory index, `MEMORY.md`, which is loaded into every session's context.
**Problem:** The write succeeded and a hook reported the file was 24.6 KB against a 24.4 KB read limit — "everything past the limit is silently dropped each time the index is loaded". It had been over the line for a while: session start had also printed the warning, in a wall of other startup output. The truncation is at the **tail**, and by design the volatile `## Session Handoff` continuity block is the *last* section (kept out of the cached prefix so it does not bust the KV cache each session). So the one section written specifically to carry state across sessions was the section being discarded — and nothing downstream errors, because a short read is a valid read. The cause was ordinary drift: ~14 KB of architecture notes and shipped-feature history had accumulated in a file whose contract is one line per entry.
**Solution:** Moved the detail into linked topic files (`arch-notes.md`, `open-threads.md`, `shipped-history.md`), leaving the index at 4.4 KB — later 7.7 KB with a full handoff. Restored the invariant rather than trimming the handoff, since the handoff is the payload and the index is the envelope.
**Why:** A size cap enforced by truncation rather than by failure produces silent, position-dependent data loss — and anything appended last is what disappears first. When a file has both a "keep it short" contract and a section that must survive, the cap needs to be checked as a *gate*, not reported as a warning among many. Also worth noticing: a warning that appears identically every session stops being read, so a recurring soft warning is a weaker signal than a one-off hard failure.
**Tags:** `#memory` `#context` `#truncation` `#silent-failure` `#tooling`
