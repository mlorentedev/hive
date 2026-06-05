---
id: adr-015-windows-daemon-supervision-upgrade
type: adr
status: proposed
created: "2026-06-04"
owner: manu
tags: [architecture, daemon, windows, supervision, auto-update, cross-os, mcp]
---

# ADR-015: Windows Daemon Supervision & Auto-Upgrade (amends ADR-011 §4)

## Status

**Proposed (2026-06-04).** Amends [adr-011-phase-c-daemon-model.md](adr-011-phase-c-daemon-model.md) §4 ("Resilience…"), whose cross-OS restart-on-upgrade contract holds on Linux (systemd) but is **broken on Windows** as shipped. Drives `specs/HIVE-176-windows-daemon-supervision/` (to be scaffolded).

Two of the three Windows mechanisms are **already validated on real hardware** this session (see Context): the wrapper-loop supervisor (restart) and the S4U windowless task (no console window), both non-elevated. One residual `[MUST RESOLVE]` blocks acceptance: the **upgrade-swap mechanism (A)** must be validated by a spike — Windows cannot replace a running executable or a loaded native module in place, which `uv tool upgrade` assumes. `tasks.md` stays unfrozen until that spike passes and this ADR is accepted. This mirrors the discipline ADR-011 used for its own Windows transport `[MUST RESOLVE]`.

## Context

ADR-011 §4 specified supervised auto-restart as "systemd `--user` `Restart=on-failure` (launchd `KeepAlive` / **Windows service recovery**)" and auto-update as "the running daemon adopts [the upgrade] via an **atomic restart-on-upgrade** (drain → stop → swap → start)." The Windows transport was spiked and cleared before ADR-011 was accepted; the **Windows supervision + upgrade path was not** — the `[MUST RESOLVE]` was scoped to transport/token/ACLs only.

The **first real Windows rollout validation** (issue #176, 2026-06-04, on the maintainer's primary Windows box) exercised that unspiked path and found it broken in three ways:

- **(A) `uv tool upgrade` cannot swap a running install on Windows.** The package site-packages updated in place (the daemon's `importlib.metadata` read the new version live), but copying the on-PATH `hive.exe` launcher failed: `os error 32` (sharing violation) — the running daemon (and every `hive client` session Claude Code spawns from the same `uv tool` install) holds the executable open. POSIX replaces an in-use binary by inode swap; Windows refuses. This is **structural**: with the daemon model, hive is *always* running, so the swap can *never* complete cleanly. A naïve `--reinstall` is worse — it tries to remove the locked venv directory (`os error 5`) and corrupts the install.
- **(B) Task Scheduler `<RestartOnFailure>` does not restart on the daemon's exit 75.** Drift detection works (log `hive.daemon.upgrade.detected boot=1.32.3 installed=1.32.4`, task `LastTaskResult=75`), but 6 minutes after the clean exit the task was `Ready`, no process, `/health` refused. `RestartOnFailure` reacts to the task *engine* failing to launch the action, **not** to an application's non-zero exit code — it is **not** a 1:1 mapping of systemd `Restart=on-failure`.
- **(C) The daemon shows a console window at every start/logon.** `render_windows_task_xml` registers a console-app `<Exec>` under an interactive-token `LogonTrigger`, so Windows allocates a visible console window — no parity with the silent `systemd --user` unit.

What works on Windows (validated this session): loopback Streamable-HTTP + bearer token (`/health` `{status:ok, ready:true, version}`, MCP `initialize` HTTP 200), drift detection, clean stop, exit 75.

Root cause beyond the bugs: ADR-011 generalized the supervision + upgrade *mechanism* across OSes from a **single** reference (Linux/systemd) without auditing a second OS instance — the failure mode Regla del 3 (ADR-015-of-the-vault / the audit-is-the-evidence rule) exists to prevent.

### Reference audit (Regla del 3)

| Ref | Instance | Restart on exit≠0 | Replace in-use binary | Windowless |
|---|---|---|---|---|
| R0 (gold) | `systemd --user Restart=on-failure` (Linux) | yes, reliable | yes (inode replace) | yes (no TTY) |
| R1 (audited 2026-06-04) | Task Scheduler `<RestartOnFailure>` (Windows, as shipped) | **no** (ignores app exit code) | **no** (copy fails, locked) | **no** (console window) |
| R2 | NSSM / WinSW (Windows service supervisors) | yes (`AppExit=Restart` default, per-code configurable) | (still needs a swap step) | yes (runs in session 0) |
| R3 | Self-update idiom (Chrome/VS Code) | n/a | yes — **rename-then-replace** (rename the in-use file aside, write the new one) | n/a |

**Divergence log.** *Shared contract (generalizable, cross-OS — keep as-is):* single-owner daemon, thin client, drift detection, clean stop with a distinct exit code, transparent stdio fallback. *OS-specific mechanism (NOT generalizable — ADR-011 over-generalized here):* (1) restart trigger — systemd `Restart=on-failure` ≠ Task Scheduler `RestartOnFailure`; (2) binary-swap — inode replace (POSIX) ≠ direct copy (Windows, fails on lock); (3) windowless execution — implicit (systemd) ≠ requires S4U / service (Windows).

## Decision

Keep ADR-011's **shared daemon contract** unchanged; make the **Windows supervision + upgrade mechanism explicitly divergent**, no admin rights required (preserving the `setup-windows.ps1` "no admin" constraint). Three mechanisms:

### (B) Restart — in-task wrapper-loop supervisor — VALIDATED

The Task Scheduler action runs a thin supervising loop (a hive-owned `hive serve --supervised` subcommand, or an equivalent wrapper) that relaunches `hive serve` while it exits non-zero and stops on exit 0 — reproducing systemd `Restart=on-failure` semantics that `<RestartOnFailure>` does not provide. **Validated 2026-06-04:** killing the inner `hive serve` (exit −1) made the wrapper relaunch it (`/health` recovered on a new port + PID); `Stop-ScheduledTask` terminates the whole tree → a clean stop does not relaunch.

### (C) Windowless — S4U principal — VALIDATED

Register the task with `<Principal><LogonType>S4U</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal>` so it runs in session 0 (no console window), at logon, with no stored password and **no admin**. **Validated 2026-06-04:** `schtasks /Create /XML` with an S4U principal succeeded non-elevated; the daemon served `/health` with **no console window** (operator-confirmed).

### (A) Upgrade-swap — `[MUST RESOLVE]` (spike-gated)

The load-bearing unknown. `uv tool upgrade` does a direct copy that fails on the in-use `hive.exe` / loaded `.pyd`. Options considered (the spike picks one):

- **A1 — stop-before-upgrade.** The upgrade task stops the daemon, upgrades, restarts. *Rejected as sole mechanism:* fails when any `hive client` session holds the install (constraint C7) — cannot require "close all agent sessions to update."
- **A2 — tolerate-in-place + post-exit refresh.** `uv tool upgrade` already updates pure-python site-packages while running; treat the entrypoint-copy failure as non-fatal (the launcher is a version-agnostic trampoline) and the wrapper refreshes the launcher in the brief unlocked window after exit 75. *Risk:* a release that changes a loaded **native** module (`.pyd`: pydantic-core, cryptography) cannot be replaced while any hive process runs → mixed-version venv. Cheap, but not bullet-proof.
- **A3 — versioned-dir + atomic junction swap (recommended target).** Install each version in its own directory; a `current` junction points to the active one; upgrade writes a fresh dir (never touching in-use files) and atomically repoints the junction; the wrapper relaunches from `current`; the old dir is GC'd once unreferenced. Bullet-proof against native-dep upgrades and C7-safe. *Cost:* hive owns a Windows-specific install/upgrade layout, decoupled from `uv tool upgrade` — a real maintenance surface.
- **A4 — rename-then-replace (R3 idiom).** Wrap the upgrade to rename each locked target aside (`MoveFileEx`) before writing the new file. C7-safe and less invasive than A3, but must enumerate exactly which files uv will touch — fragile coupling to uv internals.

The spike validates A3 (preferred) feasibility with uv on Windows; if too invasive, fall back to A4, then A2. An upstream `uv` issue (replace-while-running on Windows) is filed regardless.

## Consequences

### Positive

- The shipped ADR-011 daemon contract becomes actually deliverable on Windows: restart-on-upgrade works (B), no console window (C), no admin.
- The cross-OS abstraction is honest: shared contract, per-OS mechanism — auditable against R0–R3, not speculated from one OS.

### Negative

- Windows-specific supervision (wrapper-loop) and upgrade (A3/A4) code — a maintenance surface that Linux/systemd gets for free. The upgrade path may diverge from `uv tool upgrade` (A3 = hive owns the Windows install layout).
- A multi-PR, cross-repo change: hive (`_service.py` `render_windows_task_xml`, a supervised-serve subcommand, the upgrade mechanism) + dotfiles (`setup-windows.ps1` `DotfilesHiveUpgrade`, `tests/hive-upgrade-timer.bats`).

### Neutral

- The stdio fallback (ADR-011 §3) remains the safety net throughout — a Windows daemon outage still degrades to in-process stdio, never breaks a session.
- The Linux/macOS mechanism is untouched; this ADR only adds the Windows branch.

## References

- Amends: [adr-011-phase-c-daemon-model.md](adr-011-phase-c-daemon-model.md) §4.
- Issue: #176 (Phase C activation / rollout — Windows leg). Validation evidence captured 2026-06-04.
- Code: `src/hive/_service.py` (`render_windows_task_xml`), `src/hive/_daemon.py` (`run_serve`, `EXIT_RESTART_ON_UPGRADE`, `_watch_for_upgrade`).
- Rollout: `mlorentedev/dotfiles` `setup-windows.ps1` (daemon-supervision block), `tests/hive-upgrade-timer.bats`.
- External (reference audit): Windows self-update rename-then-replace idiom; NSSM `AppExit=Restart` supervisor semantics; Task Scheduler `RestartOnFailure` schema.
- Spike (to land in spec): S4U windowless + wrapper-loop restart validated 2026-06-04; upgrade-swap (A) pending.
