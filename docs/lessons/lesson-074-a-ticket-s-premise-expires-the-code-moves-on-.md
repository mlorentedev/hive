---
id: lesson-074-a-ticket-s-premise-expires-the-code-moves-on-
type: lesson
status: active
created: "2026-08-07"
owner: manu
tags: [hive, lesson, diagnosis, tickets, documentation-drift, logging, mcp, HIVE-127, HIVE-329]
---

# A ticket's premise expires; the code moves on and the instruction does not

**Context:** Two chores picked up in one session — [#127](https://github.com/mlorentedev/hive/issues/127) ("drop `_compat.py`: if upstream python-sdk#2610 is still silent by 2026-06-12, port the fix as our own upstream PR") and [#329](https://github.com/mlorentedev/hive/issues/329) ("debug logs are never rotated or GC'd").
**Problem:** Both tickets described a state that had stopped being true, and in each case executing the instruction as written would have produced wrong work. #127: the `__exit__` patch for #2610 had **already been removed** from `_compat.py` — its own docstring records that the symptom stopped reproducing on `mcp >= 1.27`, because `Server._handle_request` now catches the in-flight cancellation first — and #2610 already has an open upstream fix PR (#2624). What the shim still patches is `respond`, tracking a *different* issue (#2416), itself informally claimed by a contributor. So "port the #2610 fix" meant duplicating existing upstream work on a bug we no longer depend on. #329: the logs **are** rotated — `RotatingFileHandler`, `maxBytes=1_000_000`, `backupCount=1`. The unbounded axis is the *number* of files, one `hive-<pid>.log` per process start, and the per-PID filename is deliberate (its docstring says it exists to dodge the multi-writer rotation race). Consolidating into one file, which the title invites, would have reintroduced the race the design was chosen to avoid. `AGENTS.md` had propagated #127's stale version to every agent that reads it first: wrong patch target, wrong tracker, and a stale `mcp>=1.26,<2.0` quoted where the real constraint is now `>=1.27,<3.0`.
**Solution:** #329 fixed as a *garbage collector* rather than a rotator, with two guards — a file goes only when it is both older than `HIVE_LOG_RETENTION_DAYS` and its owning PID is gone, because age alone would unlink an idle-but-running daemon's open file and detach its inode on POSIX. #127 rescoped on-ticket with the corrected premise instead of being worked as written; `AGENTS.md` corrected, which also surfaced that widening the pin to `<3.0` silently lost the guard the narrow cap existed to provide.
**Why:** Read the mechanism before accepting the ticket's diagnosis — a title is a hypothesis written at a past moment, and the code is the only current statement of what is true. This matters most for tickets carrying a *dated instruction* ("if X by DATE, do Y"): the instruction keeps its authority long after its premise lapses, and following it feels like diligence. Cheapest check is to read the relevant docstring and the current pin, both of which contradicted the tickets here in under a minute. And when a stale fact has been copied into `AGENTS.md`, correcting the ticket is not enough — the copy is what every agent reads first.
**Tags:** `#diagnosis` `#tickets` `#documentation-drift` `#logging` `#mcp` `#HIVE-127` `#HIVE-329`
