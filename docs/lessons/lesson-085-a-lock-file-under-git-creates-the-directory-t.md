---
id: lesson-085-a-lock-file-under-git-creates-the-directory-t
type: lesson
status: active
created: "2026-08-09"
owner: manu
tags: [hive, lesson, git, locking, filelock, vault, HIVE-353]
---

# A lock file under `.git/` creates the directory that proves a repo exists

**Context:** Hive's inter-process write lock lived at `vault/.git/hive.lock`. The path is sensible for a vault that is a git repository: the lock travels with the repo and sits in the one directory git already ignores. The lock is not git-specific, though — `vault_write_lock` takes it around every read-modify-write, so it must also work in a vault with no repository at all.
**Problem:** `filelock` builds missing parent directories on acquire. So the first write against a non-repo vault *created* `.git/`, leaving a directory containing one lock file and nothing else. Everything downstream that answers "is this a repository?" by looking then answers yes. That includes hive's own `uncommitted_summary`, whose `if not (vault_path / ".git").exists()` short-circuit exists specifically so a vault nested inside an unrelated repo does not report that repo's state — after one write, the short-circuit stopped applying and every health call spawned a `git` that could only fail. The artifact is cosmetic; what it breaks is every cheap check written against it.
**Solution:** A single `_filelock_path()` deriving the location once — `.git/hive.lock` when `.git` exists, `.hive.lock` at the vault root when it does not — used by both `_git_filelock` and `evict_filelock`. The two previously computed the same string independently, and a divergence would have been silent in the worst way: eviction popping nothing, returning `False` as though it had worked, while the deadline supervisor quietly stopped unblocking siblings (ADR-012). The check is a `stat`, not `git rev-parse`: it runs on every write, and keying off mere presence means a `.git` left by an older hive keeps the lock where that hive expects it, so the two stay mutually exclusive across an upgrade.
**Why:** A library that creates what it needs will happily create something that means more than you intended. `filelock`'s parent-building is convenient and documented; the surprise is that the parent here was a *load-bearing signal*. Before putting a file under a directory whose existence is evidence of something, ask what happens when the library makes that directory for you. The corollary bit in the same session: the fix for the sibling defect had to ask git rather than check for `.git/` *because* of this bug, so fixing them in the other order would have left a check that is correct only by accident.
**Tags:** `#git` `#locking` `#filelock` `#vault` `#HIVE-353`
