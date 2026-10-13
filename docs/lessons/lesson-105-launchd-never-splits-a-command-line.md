---
id: lesson-105-launchd-never-splits-a-command-line
type: lesson
status: active
created: "2026-10-13"
owner: manu
tags: [hive, lesson, macos, launchd, service, HIVE-488]
---

# launchd never splits a command line

**Context:** `hive service install` had real backends for systemd and Task
Scheduler and a stub for macOS (#488). The launchd backend renders a plist from
the same resolver the other two use.

**Problem:** `_resolve_exec()` returned one string, and its last fallback is
`<python> -m hive.server`. systemd splits `ExecStart=` into words, and the
PowerShell loop is a command line, so a string with spaces works on both.
launchd's `ProgramArguments` is an argv vector that no shell ever splits: the
fallback copied into one element names a file called `python -m hive.server`,
which does not exist, and the agent never starts. An interpreter path that
holds a space breaks any split that does try.

**Solution:** The resolver returns a vector (`_resolve_argv()`), and the string
form is that vector joined, used only where a supervisor takes a command line.
Two related traps showed up on the real Mac. Under `uv run`, the worktree's
`.venv/bin` is first on PATH, so the resolver picks a launcher that disappears
with the worktree; run `hive service install` with the PATH a user has. And a
plist is plaintext in `~/Library/LaunchAgents`, so secrets stay in the
owner-only `hive.env`, which the agent sources at start, as the systemd unit
does through `EnvironmentFile=`.

A third only the real Mac showed: `launchctl bootout` returns before launchd
has removed the job, and a `bootstrap` issued right after fails with
`5: Input/output error`, so a reload left the daemon down while the mocked
test passed. Install now waits, bounded, until `launchctl print` stops finding
the job.

**Why:** A supervisor's command field looks like the same thing across OSes,
but it is a shell line on one and an exec vector on another. Resolve to the
vector, which loses nothing, and render the string from it.
