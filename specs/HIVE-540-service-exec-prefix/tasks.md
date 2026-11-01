---
tags: [spec, tasks, templates]
created: "2026-10-31"
---

# Tasks - HIVE-540-service-exec-prefix

> TDD order. One task = one focused commit. Tick as you go. Reorder freely while spec is in `draft` state; freeze once you start `implementing`.
>
> **Inline markers** (optional, additive — borrowed from `github/spec-kit`, adapt-not-adopt per #141):
> - `[P]` — this task has **no dependency on another unchecked task**, so it is safe to run in parallel (fan out to a `Workflow`, or just batch). TDD chains (test → implement → refactor of the *same* behavior) are sequential and must NOT carry `[P]`; independent behaviors can.
> - `[AC<n>]` — this task helps satisfy **acceptance criterion #`<n>`** from `proposal.md`. Lets `/spec check` map coverage deterministically; omit it and the check falls back to semantic judgment.

## Setup

- [x] Branch created from master: `feat/service-exec-prefix`
- [x] `proposal.md` is complete and acceptance criteria are testable
- [x] No open questions left in `proposal.md` "Risks / open questions" that block this PR (Windows termination is measured before the dotfiles follow-up switches a Windows host)

## Implementation

- [x] [AC1] [AC2] [AC3] [AC4] [AC5] Failing tests in `tests/test_service.py` (exec prefix section), red on `AttributeError: service_config_path`
- [x] [AC2] Record, load and clear the prefix (`service_config_path`, `load_exec_prefix`, `save_exec_prefix`), owner-only
- [x] [AC3] Resolve the launcher and refuse an unresolvable one, or on Windows an element with `"` or a line break, before anything is written (`_resolve_exec_prefix`)
- [x] [AC1] Render the prefix: `_systemd_command_line` for systemd, argv elements for launchd, quoted arguments in `_supervisor_ps_command` for the Scheduled Task and the Startup fallback
- [x] [AC4] Unprefixed output unchanged (the existing renderer tests pass untouched)
- [x] [AC5] `hive service status` prints the recorded prefix
- [x] CLI: `--exec-prefix -- ARGV` taken verbatim, `--no-exec-prefix`, misuse refused with exit 2
- [x] Docs: daemon-mode guide (en, es)

## Closing

- [x] Every acceptance criterion from `proposal.md` is covered by at least one test
- [x] Every acceptance criterion has a matching entry in `features.json` with a non-vacuous verification command
- [x] Type checks pass
- [x] Lint passes
- [x] No unrelated changes in the diff (no scope creep)
- [x] `verification.md` filled in
- [ ] PR opened referencing this spec folder

## Machine-readable features

See the sibling `features.json`.
