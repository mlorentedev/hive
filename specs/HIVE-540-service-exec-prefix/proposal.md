---
id: "HIVE-540-service-exec-prefix"
type: spec
status: implementing # draft | implementing | verifying | archived
created: "2026-10-31"
issue: "mlorentedev/hive#540"   # repo#NNN — GitHub issue / Project item that tracks this spec
tags: [spec, proposal, service, daemon, cross-os, secrets]
template_version: "1.0"
---

# HIVE-540-service-exec-prefix

> **Naming**: file lives at `<repo>/specs/HIVE-540-service-exec-prefix/proposal.md`. `HIVE-540-service-exec-prefix` is `AREA-NNN-slug` (e.g. `TOOL-001-secret-drift`).

## Why

<!-- from issue #540: hive service install cannot start the daemon through a launcher, so no supervisor but systemd can give it a credential without a file -->

`hive service install` writes the supervisor definition, and nothing in it
lets a consumer start the daemon through a launcher. The worker credential
therefore reaches the daemon only from the optional `hive.env` file, or, for a
deployment that keeps every secret out of files, through a systemd drop-in that
wraps `hive serve` in `dotf secrets run` (dotfiles CLI-042 AC7). launchd has no
drop-ins and the Windows task action is regenerated on every install, so on
macOS and Windows the daemon runs with no worker credential at all
(dotfiles#2351, dotfiles#1297), and the Linux fix stays a Linux-only shim.

## What

1. `hive service install --exec-prefix -- <argv...>` records a launch prefix,
   and every supervisor definition runs `<prefix> <hive> serve`: the systemd
   unit's `ExecStart`, the launchd agent's `ProgramArguments`, and the
   PowerShell supervisor loop of both the Scheduled Task and the
   Startup-folder fallback.
2. The prefix is an argv vector, never a string. It is stored in
   `<config root>/hive/service.json` (`{"exec_prefix": [...]}`), owner-only,
   as given. Each install resolves its first element to an absolute path
   before rendering, because neither systemd nor launchd searches the user's
   PATH.
3. A later `hive service install` without the flag reuses the recorded
   prefix, so a reinstall by the upgrade timer or a setup script keeps it.
   `--no-exec-prefix` clears it. `hive service uninstall` leaves the record,
   since it is the operator's configuration, not the service.
4. An install refuses, before writing anything, a prefix whose first element
   cannot be found, and on Windows any element carrying a character the
   PowerShell loop cannot quote safely (`"`, a newline).
5. `hive service status` names the recorded prefix, so the operator can see
   what the supervisor starts without reading the unit.

## Out of scope

- **What the prefix is.** hive takes an opaque argv. The dotfiles side
  (setting it to `dotf secrets run --only NAN_API_KEY --skip-locked --`,
  retiring its systemd drop-in behind a version gate, and making the
  locked-vault trace and restart work off systemd) is a follow-up spec in
  dotfiles, after a hive release carries this one.
- **Removing `hive.env`.** It stays as the optional, file-based path for
  deployments that do not use a launcher. On launchd the prefix runs inside
  the same `/bin/sh` wrapper that sources it, so both work together.
- **The module-invocation fallback on Windows** (`python -m hive.server`
  quoted as one command in the PowerShell loop). The prefix path quotes each
  element on its own; the unprefixed path is left exactly as it is.

## Risks / open questions

- **Resolved — exit code and termination through the prefix.** The restart
  contract (0 stops, 75 and any other failure relaunch) holds only if the
  prefix passes the child's exit code back. `dotf secrets run` does on every
  OS (`runChild` returns the child's `ExitCode()`), and forwards SIGTERM and
  SIGINT on POSIX. hive documents the requirement on the flag; a prefix that
  swallows the code breaks restart, which is the consumer's contract to keep.
- **Resolved — launchd wrapper.** The prefix goes after the env-file
  argument, inside `exec "$@"`, so the process launchd supervises is the
  prefix, which owns hive as its child. The env file keeps working.
- **Open — Windows termination.** Stopping the task or killing the process
  tree reaches hive through the prefix's process, and Windows has no SIGTERM
  to forward. The upgrade orchestrator (ADR-015 A) stops the daemon; its
  behaviour with a prefix in between is measured on Windows before the
  dotfiles follow-up switches any Windows host to a prefix.
- **Resolved — a launcher that moves.** The record keeps the element as
  given, and every install resolves it again: an absolute path that exists is
  used as is, anything else is looked up on PATH by its name. When neither
  finds it the install refuses, rather than writing a definition that never
  starts.

## Acceptance criteria

- [ ] AC1: with a recorded prefix, the systemd unit's `ExecStart`, the launchd
  `ProgramArguments`, and the PowerShell loop of the Scheduled Task and the
  Startup fallback each run the prefix, then the hive launcher, then `serve`,
  with every prefix element kept as its own argument.
- [ ] AC2: `--exec-prefix -- <argv>` writes `service.json` owner-only; the
  rendered first element is absolute; a following install without the flag
  renders the same prefix; `--no-exec-prefix` removes it and renders the
  unprefixed definition.
- [ ] AC3: a prefix whose first element cannot be found, or (Windows) carries
  `"` or a newline, is refused with a message naming the element, and no
  supervisor definition is written or changed.
- [ ] AC4: with no prefix ever recorded, every renderer's output is
  byte-identical to today's.
- [ ] AC5: `hive service status` prints the recorded prefix, or says none is
  recorded.

## References

- Bitácora board: the GitHub issue / Project item tracking this spec (see the `issue:` frontmatter field)
- Related ADR: `docs/adr/adr-011-phase-c-daemon-model.md`, `docs/adr/adr-015-windows-daemon-supervision-upgrade.md`
- Consumers: dotfiles#3005 / dotfiles#3006 (`--skip-locked`), dotfiles#2351 (macOS), dotfiles#1297 (Windows)
