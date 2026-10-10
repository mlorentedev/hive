---
id: lesson-103-an-install-that-only-registers-leaves-the-service-down
type: lesson
status: active
created: "2026-10-10"
owner: manu
tags: [hive, lesson, windows, service, parity, HIVE-479]
---

# An install that only registers leaves the service down

**Context:** `hive service install` supervises the daemon per OS. On Linux it
runs `systemctl --user enable --now`. On Windows it registers a Scheduled Task
whose only trigger is logon, or, when Task Scheduler is locked, writes a
Startup-folder launcher (#252).

**Problem:** On Windows nothing started the daemon until the next logon.
Install reported success, `hive service status` reported the daemon down, and
every `hive client` started by an MCP host failed in between. A machine that
installs and keeps working in the same session, like a CI runner, never gets a
daemon at all. It surfaced only when dotfiles#2255 added a doctor check that
asks whether the daemon answers, and its first Windows run went red (#479).

**Solution:**

- **Install starts what it registered.** After the task is created, install
  runs `schtasks /Run /TN HiveVaultDaemon`. The task's `IgnoreNew` policy makes
  that a no-op when the daemon already runs.
- **The fallback starts through its own launcher.** Install launches the same
  `.vbs` with `wscript.exe`, so the session and the next logon run one
  definition. The daemon's singleton lock refuses a second owner.
- **The verdict is the daemon answering.** On every OS, install waits up to
  30 s for the pinned `/health` probe and exits 1 with the daemon's state if it
  never reports healthy. So a launcher that starts and then dies fails install,
  and a `/Run` that reports an error for an already running task does not.

**Why:** "Registered" and "running" are different post-conditions, and only
one OS's supervisor joins them by default (`enable --now`). A cross-OS install
command has to state which one it promises and reach it on every OS. Probe the
post-condition the user relies on (the daemon answers), not the step the
installer took.
