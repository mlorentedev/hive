---
id: lesson-086-a-default-equal-to-the-real-value-makes-a-con
type: lesson
status: active
created: "2026-08-09"
owner: manu
tags: [hive, lesson, testing, configuration, pydantic-settings, debugging, false-positive]
---

# A default equal to the real value makes a config test prove nothing

**Context:** Probing the published 2.0.0 for robustness, a scripted vault had to point hive at a temp directory. `HiveSettings.vault_path` accepts an unprefixed `VAULT_PATH` alias alongside `HIVE_VAULT_PATH`, and the probe set the unprefixed one.
**Problem:** Setting `VAULT_PATH=/tmp/...` appeared to be ignored — `settings.vault_path` came back as the real vault. That looked exactly like a broken alias, and the alias is documented in `AGENTS.md`, so it read as a shipped defect worth reporting. It was not. Two things conspired. The field's default is `Path.home() / "Projects" / "knowledge"`, which on this machine *is* the real vault, so "the variable was ignored" and "the variable was read" produce byte-identical output. And the shell exported **both** `HIVE_VAULT_PATH` and `VAULT_PATH` with that same value, so the prefixed one won the `AliasChoices` order — correct behaviour, invisible result.
**Solution:** Probe with a value the default cannot produce (`/tmp/probe-limpio`) and clear the competing variable (`env -u HIVE_VAULT_PATH`). The alias then resolves correctly, and the "defect" evaporates.
**Why:** This is the neuter discipline applied to a manual check rather than a test. A verification that cannot distinguish success from failure is not weak evidence — it is no evidence, and it points wherever your prior already pointed. The tell is cheap to look for: if the expected-pass and expected-fail branches would print the same thing, the probe is decorative. Defaults that coincide with production values are the common way this happens, and they are common precisely because good defaults are chosen to match real setups.
**Tags:** `#testing` `#configuration` `#pydantic-settings` `#debugging` `#false-positive`
