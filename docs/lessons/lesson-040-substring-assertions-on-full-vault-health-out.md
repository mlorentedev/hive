---
id: lesson-040-substring-assertions-on-full-vault-health-out
type: lesson
status: active
created: "2026-05-21"
owner: manu
tags: [hive, lesson, testing, regression, vault-health]
---

# Substring assertions on full vault_health output break when vault_path is included

**Context:** Implementing the `## server` identity block in `vault_health` (issue #109) — the block embeds `vault_path: <abs path>`. Four pre-existing tests in `test_server.py` used substring assertions like `assert "stale" not in result.lower()`, `assert "error" not in result.lower()`, `assert "ghost_responses" not in result`. They started failing because pytest's `tmp_path` includes the test name (e.g. `test_terminal_status_not_stale0`), which is now legitimately printed verbatim inside the identity block.
**Problem:** Bare substring negation on a multi-line markdown report is fragile against any future field that interpolates user-controlled / path-like data. Once vault_path was added, four tests false-positive on substrings that happen to be inside the path. Adding the field exposed brittleness that had been latent.
**Solution:** Anchor negative assertions on the structural marker the producer code actually emits — `## ghost_responses` (the section header), `[error]` (the issue marker), `Stale files` (the label). Positive assertions can stay loose. Rule of thumb: if you're asserting that section X is absent, assert the section *header* is absent, not the topic word.
**Tags:** `#testing` `#regression` `#vault-health`
