---
id: lesson-006-2026-03-01-squash-merge-for-feature-branches
type: lesson
status: active
created: "2026-05-01"
owner: manu
tags: [hive, lesson]
---

# 2026-03-01: Squash merge for feature branches

- **Context:** Feature branches accumulate WIP/fix commits that pollute master history.
- **Decision:** Always squash merge PRs into master.
- **Rationale:** Each commit on master = 1 complete feature/fix. Clean `git log`, easy `git revert`. Branch history preserved in GitHub PR for forensics if needed.
