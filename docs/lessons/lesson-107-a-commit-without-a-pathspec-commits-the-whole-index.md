---
id: lesson-107-a-commit-without-a-pathspec-commits-the-whole-index
type: lesson
status: active
created: "2026-10-20"
owner: manu
tags: [hive, lesson, git, concurrency, BUG-008]
---

# A commit without a pathspec commits the whole index

**Context:** Every Hive write auto-commits through `_git_commit`, which ran
`git add <paths>` and then `git commit -m <msg>`. Several agent sessions share
one vault, each in its own hive process, and the cross-process filelock
serializes their git calls (#470).

**Problem:** The lock serializes the *calls*, not the *index*. `git add` scoped
the staging, but a bare `git commit` records everything staged, by anyone. A
`vault_patch` from one session committed another session's staged rename and
edits under its own message. A second, quieter case: `git add a[1].md` is a
glob, so it also stages `a1.md`.

**Solution:** `git add -- :(literal)<path>…` and then
`git commit --only -m <msg> -- :(literal)<path>…`. The rest of the index stays
staged and untouched. `as_posix()` keeps the pathspec the same on Windows.
`tests/test_git_commit_scope.py` runs real git, because the bug lives in what
git puts in the commit, which an argv recorder cannot see.

**Rule:** A lock around shared mutable state only orders the operations. Each
operation must still name exactly what it touches. With git, that means a
literal pathspec on both `add` and `commit`.
