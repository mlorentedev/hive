---
id: lesson-117-porcelain-v1-shows-a-dirty-submodule-as-a-change-git-add-cannot-stage
type: lesson
status: active
created: "2026-11-01"
owner: manu
tags: [hive, lesson, git, submodule, vault_commit]
---

# Porcelain v1 shows a dirty submodule as a change `git add` cannot stage

**Context:** `vault_commit` sweeps what `git status --porcelain -z` reports as unstaged
or untracked, runs `git add` on those paths, then `git commit --only` on them (#528).
A vault can contain a git submodule.

**Problem:** When a submodule has edits or untracked files inside it, porcelain v1
reports it as ` M sub`, the same as a modified file. But the parent repository cannot
stage that content: `git add sub` exits 0 and stages nothing. When the submodule was
the only path in the sweep, `git commit --only sub` exited 1, wrote "no changes added
to commit" to stdout and nothing to stderr, and hive reported
`Commit failed: git commit rc=1: <empty>`. The error named nothing (#543).

**Solution:** The sweep's status runs with `--ignore-submodules=dirty`. That flag still
reports a submodule whose checked-out commit moved, because its gitlink can be
committed. A second probe, `git status --porcelain=v2 -z --untracked-files=normal`,
finds the dirty submodules from the state field of each ordinary entry (`S` followed by
the commit, tracked and untracked flags), and `vault_commit` names them with the advice
to commit inside the submodule first. The probe cannot use `--untracked-files=no`,
because that also hides untracked files inside submodules.

**Rule:** In porcelain v1, ` M` on a path does not mean `git add` can stage it. A
submodule's inner state appears only in porcelain v2. A sweep that stages what v1
reports must exclude dirty submodules, and must name them so the change is not lost.
