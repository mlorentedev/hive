---
id: lesson-100-replay-the-reported-version-against-the-reported-data
type: lesson
status: active
created: "2026-09-25"
owner: manu
tags: [hive, lesson, debugging, reproduction, vault-health, git-archive, HIVE-346]
---

# Replay the reported version against the reported data

**Context:** #346, a P1, reported that the whole-vault `vault_health(checks=["frontmatter"])` sweep flagged files that carried every required key, while the project-scoped run passed them. It named the server version (1.43.0), a file, and the belief that "the two code paths disagree". Its acceptance asked to make one path call the other.
**Problem:** On master the report did not reproduce: global and scoped runs agreed, and every global error was a true positive when re-parsed with PyYAML. A negative on master says little on its own. The bug could have been fixed in passing, the vault could have changed, or the reproduction could have been wrong. The acceptance also carried a design ("have one call the other") built on a premise nobody had checked, because both runs already shared one loop over `project_dirs`.
**Solution:** Rebuild both halves of the report as they stood: `git archive v1.43.0` of hive, and `git archive` of the vault at the last commit before the issue's timestamp and the day after, then run the reported calls against that pair. The flagged file was clean at the reported version on the reported data. That turns "cannot reproduce" from a shrug into evidence. It also located the likely source of the impact figures: the session-start banner is computed by dotfiles' `vault-health.sh` with a whole-file grep, not by hive (dotfiles#1752). The property the issue wanted was then pinned by a test that fails under two mutations, a positional key lookup and a skipped scope (#446), and the issue was closed on that evidence rather than on a fix.
**Why:** **A report is a claim about a version and a dataset. Test it against both before changing code.** Both are cheap to recover when they live in git: `git archive <tag>` and `git archive <commit-before-timestamp>` into a scratch directory never touch the live trees. Replaying on master alone confuses "fixed since" with "never broken". Fixing on the report's stated mechanism would have refactored a path that was already single. When the replay is negative, keep the acceptance criterion that states a property, drop the one that prescribes a mechanism, and make the property's test fail on purpose once to prove it is not vacuous.
**Tags:** `#debugging` `#reproduction` `#vault-health` `#git-archive` `#HIVE-346`
