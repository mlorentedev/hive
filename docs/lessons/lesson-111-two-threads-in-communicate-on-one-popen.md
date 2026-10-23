---
id: lesson-111-two-threads-in-communicate-on-one-popen
type: lesson
status: active
created: "2026-10-22"
owner: manu
tags: [hive, lesson, subprocess, concurrency, macos, deadline, HIVE-517]
---

# Two threads in communicate() on one Popen

**Context:** The deadline registry (ADR-008) tracks every git `Popen` that
`_run_git` starts. On a deadline, `_terminate_registry` and its sync twin
`terminate_registry_sync` (used by the ADR-018 reconciler thread) kill the
process and then drained its pipes with `proc.communicate()`. The reconciler
flush test hung intermittently on macOS (#517).

**Problem:** Every registered `Popen` already has an owner: the thread inside
`_run_git`, blocked in its own `communicate()`. A second `communicate()` on the
same object races the owner for the pipes. Whichever reads EOF first closes
the descriptors; the other gets `EBADF`, or, on macOS, sleeps forever in a
`poll()` on a descriptor that was closed under it. Catching `ValueError`
("communicate called twice") did not help: that guard only fires once the
first call has finished, not while both are inside it. A pure-Python probe of
two threads calling `communicate()` on one `Popen` hung within 1-353 rounds.
This was a production bug, not only a test bug: the reconciler and the
`tool_span` workers both reach the terminator while an owner is reading.

**Solution:** A terminator reaps; it never reads pipes it does not own. After
SIGTERM/SIGKILL the owner gets EOF and drains on its own. The terminators now
call `proc.wait(_REAP_TIMEOUT_S)`, which takes the `Popen`'s waitpid lock and
is safe beside the owner. With `wait()` the same probe ran 2000/2000 rounds
clean. `wait()`'s own timeout replaces `asyncio.wait_for(to_thread(...))`, so a
process that outlives it leaves no thread behind.

**Guard:** `faulthandler_timeout = 90` in the pytest config dumps every
thread's stack for a test that runs past 90 seconds, so the next hang names
the frame it is stuck in instead of timing out the CI job silently.

**Testing notes:** Red-first tests in `tests/test_bounded_call.py`
(`*_never_strands_the_owning_thread`) run the race 200 rounds each with a real
owner thread inside `communicate()`; before the fix they failed between rounds
0 and 92. A single round proves nothing: the race needs repetition to show.
