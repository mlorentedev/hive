---
id: lesson-114-a-consumer-wraps-the-daemon-by-declaring-not-by-patching
type: lesson
status: active
created: "2026-10-31"
owner: manu
tags: [hive, lesson, service, daemon, cross-os, secrets, HIVE-540]
---

# A consumer wraps the daemon by declaring, not by patching

**Context:** dotfiles keeps the worker API key out of every file, so it starts
`hive serve` through `dotf secrets run`. `hive service install` owns the
supervisor definition, so dotfiles patched it from outside with a systemd
drop-in that overrides `ExecStart`.

**Problem:** A patch from outside only works where the supervisor offers a
hook. systemd merges drop-ins. launchd has none, and the Windows task action is
regenerated on every install, including the upgrade timer's. On macOS and
Windows the daemon ran with no worker key at all (dotfiles#2351,
dotfiles#1297), and the Linux fix could not be carried over.

**Solution:** hive renders the wrapper itself. `hive service install
--exec-prefix -- <argv>` records the launcher in `~/.config/hive/service.json`,
every renderer runs `<argv> <hive> serve`, and a later install without the flag
reuses the record (#540). The prefix is an argv vector quoted per supervisor:
escaped for systemd's `%` and `$`, one `ProgramArguments` element each for
launchd, and single-quoted for PowerShell, which refuses `"` it cannot quote.

**Rule:** When a consumer needs to change how a tool's generated artifact
behaves, the tool should take that change as declared input that it keeps
across regenerations. A per-supervisor patch works only on supervisors that
accept patches.
