---
id: lesson-052-the-daemon-s-restart-on-upgrade-is-posix-only
type: lesson
status: active
created: "2026-06-04"
owner: manu
tags: [hive, lesson, windows, daemon, cross-os, task-scheduler, s4u, ADR-015, hive-176]
---

# The daemon's restart-on-upgrade is POSIX-only — Windows breaks it three ways

**Context:** First real Windows validation of the Phase C daemon rollout (hive#176). ADR-011 §4 specified supervised restart-on-upgrade cross-OS, but only the Linux/systemd path was ever exercised — the Windows path was unspiked (the ADR-011 `[MUST RESOLVE]` covered transport/token, not supervision/upgrade).
**Problem:** Three independent breakages, all rooted in Windows OS semantics that differ from systemd: (1) **Task Scheduler `<RestartOnFailure>` does NOT restart on the daemon's exit 75** — drift was detected and the process exited 75 (`LastTaskResult=75` confirmed), but 6 min later the task was `Ready`, no process. `RestartOnFailure` reacts to the task *engine* failing to launch, not an application's non-zero exit code; it is not a 1:1 map of systemd `Restart=on-failure`. (2) **`uv tool upgrade` cannot replace the in-use `hive.exe`** (`os error 32`) because the daemon — and every `hive client` session — always holds it; POSIX swaps an in-use binary by inode, Windows refuses. (3) **A console-app Task action under an interactive-token LogonTrigger shows a console window every logon** — no parity with the silent `systemd --user` unit.
**Solution (ADR-015):** Keep ADR-011's shared daemon *contract*; make the Windows *mechanism* diverge. (B) An in-task PowerShell **wrapper-loop** relaunches `hive serve` while it exits non-zero, stops on exit 0 — the systemd semantics Task Scheduler lacks. (C) An **S4U `<Principal>`** runs the task in session 0 (no window, non-elevated, no stored password). (A) An **orchestrated stop-before-upgrade** (PowerShell, holds no lock): only-if-newer -> defer-if-locked -> stop daemon -> `uv tool upgrade` -> start. Shipped in hive#207 (B+C) + dotfiles#229 (A); all three validated on real hardware.
**Lesson:** A cross-OS service abstraction's *contract* generalizes; its *mechanism* (restart trigger, binary swap, windowless execution) does NOT. Audit a second OS empirically before generalizing supervision — the Regla-del-3 failure mode. ADR-011 generalized from one OS and the gaps surfaced only at the first real Windows rollout.
**Tags:** `#windows` `#daemon` `#cross-os` `#task-scheduler` `#s4u` `#ADR-015` `#hive-176`
