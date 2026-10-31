---
id: lesson-114-a-stub-that-does-not-model-the-owner-hides-what-the-terminator-kills
type: lesson
status: active
created: "2026-10-30"
owner: manu
tags: [hive, lesson, testing, subprocess, deadline, process-group, HIVE-524]
---

# A stub that does not model the owner hides what the terminator kills

**Context:** `test_subprocess_terminated` checks that `bounded_call` kills a
registered `sleep 60` on its deadline. It passed for months, and its teardown
took ~59 s (#524).

**Problem:** The stub differed from `_run_git`, the only real registrant, in
two ways, and each one hid the other.

1. It removed the `Popen` from the registry in a `finally` of the
   *coroutine*. On deadline the coroutine is cancelled and that `finally`
   runs before the terminator reads the registry, so the terminator saw an
   empty list and signalled nothing. `sleep` ran its full 60 s, and the
   executor thread blocked in `wait()` stalled loop shutdown. The final
   assertion iterated the empty registry, so it checked nothing.
2. It spawned without `start_new_session`. The terminator signals the
   child's whole process group (`os.killpg(os.getpgid(pid))`), so once (1)
   was fixed the SIGTERM landed on pytest's own group and killed the test
   runner and the shell that launched it.

Point 2 is a latent production defect, not only a test one: any future
registrant that forgets `popen_creation_kwargs()` would make a deadline kill
the hive server itself.

**Solution:** The stub now does what `_run_git` does: it spawns with
`popen_creation_kwargs()` and registers and unregisters from its own thread.
The assertion checks the spawned process, not the registry. The terminator
compares the child's group with `os.getpgrp()` and, if they match, signals the
pid alone and logs a warning (`_signal_group` in `_deadline.py`).

**Testing notes:** Assert on the effect (the process exited), never on a
collection the code under test is allowed to empty. A test that sends real
group signals must run its scenario in a child started with
`start_new_session=True`: if the guard is wrong, the stray signal takes the
child and the test reports it, instead of killing pytest (and, locally, the
shell that launched it, which is how this was found: exit 143). The group
logic itself is also covered with `os.killpg`/`os.kill` monkeypatched.
