---
id: lesson-053-uv-tool-upgrade-footguns-on-windows-in-use-lo
type: lesson
status: active
created: "2026-06-04"
owner: manu
tags: [hive, lesson, windows, uv, upgrade, footgun, ADR-015]
---

# `uv tool upgrade` footguns on Windows: in-use lock, exact pins, orphaned children

**Context:** Building + validating the Windows-safe upgrade orchestration (ADR-015 / dotfiles#229).
**Problem:** Three traps hit while making `uv tool upgrade hive-vault` work around a live daemon: (1) **`uv tool install --reinstall` is destructive on Windows** — it removes the venv `Scripts` dir, which fails (`os error 5`) when a hive process holds it, leaving the tool env corrupted (`uv tool list` then could not find the package). Plain `uv tool upgrade` only fails the *entrypoint copy* (cosmetic — the launcher is a version-agnostic trampoline) while still updating pure-python site-packages. (2) **`uv tool upgrade` respects the install-time version constraint** — `uv tool install hive-vault==1.32.4` pins it, after which `uv tool upgrade` reports "Nothing to upgrade". The production install must be unpinned (`uv tool install --upgrade hive-vault`, which the rollout's mcp prerequisite already uses). (3) **`Stop-Process -Name hive` orphans the python child** — `.local\bin\hive.exe` is a trampoline that spawns the real server as `python.exe` under the uv-tools dir; killing the trampoline leaves the child alive, still holding the install, so the next upgrade *defers* (a leftover daemon child is indistinguishable from a live client session).
**Solution:** The orchestration uses plain `uv tool upgrade` (never `--reinstall`), acts only when a *newer* version is published, and defers if any non-daemon process holds the install (conservative — never a partial upgrade). Process cleanups must kill the `python.exe` children under the uv-tools path, not just `hive`.
**Lesson:** On Windows, "upgrade a running tool" is neither atomic nor pin-agnostic. Stop the holder, keep the install unpinned, and treat any in-use process as a hard blocker (defer, don't force). The residual OS limitation is tracked upstream in uv (#8528, #11930, #11134), not reimplemented in hive.
**Tags:** `#windows` `#uv` `#upgrade` `#footgun` `#ADR-015`
