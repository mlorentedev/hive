---
id: lesson-083-the-read-only-git-command-that-takes-a-write-
type: lesson
status: active
created: "2026-08-08"
owner: manu
tags: [hive, lesson, git, locking, telemetry, observability, daemon, HIVE-322]
---

# The read-only git command that takes a write lock

**Context:** ADR-018 §3 replaced startup self-healing with a report, so two callers needed to enumerate uncommitted vault paths: `_startup_self_heal` at daemon boot, and the `vault_health` runtime block. Neither writes anything — both just count what is dirty.
**Problem:** `git status` is not a read. It refreshes the index as a side effect and takes `.git/index.lock` to do it, so a "read-only" reporter contends with any concurrent committer. Two consequences, and the second is the bad one. In `vault_health` the reporter would race the reconciler's own commit for the same lock. At daemon boot it is worse than a race: `_startup_self_heal` exists to *clear* a stale `index.lock` left by a dead prior run, and a reporter appended after that step could recreate the very lock the function had just removed — a self-inflicted stale lock, created by the recovery path, on every start. Nothing about the call site suggests a write is happening, so this survives review comfortably.
**Solution:** `git --no-optional-locks status --porcelain -z --untracked-files=all`. The flag exists for precisely this case (git's own docs cite status-in-a-shell-prompt) and makes the invocation genuinely lock-free. `-z` came along for a second reason worth stating: porcelain quotes paths containing special characters under `core.quotePath`, and NUL separation sidesteps unquoting entirely — but renames then emit their source path as a *second* NUL field, which must be consumed explicitly or every entry after a rename is misparsed as an `XY PATH` record. That failure skews silently instead of erroring, so it got its own test.
**Why:** "This command only reads" is a claim about intent, not about syscalls. Before putting a subprocess on a monitoring or telemetry path, check what locks it takes — the tools most likely to be added "just to observe" are the ones nobody reviews for contention. The sharper version of the lesson: a reporter added to a *recovery* routine can undo the recovery, because recovery paths are exactly where the resource being reported on is in a fragile state. Order matters less than whether the observation is inert.
**Tags:** `#git` `#locking` `#telemetry` `#observability` `#daemon` `#HIVE-322`
