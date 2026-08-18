---
id: lesson-069-mypy-narrows-platform-on-a-literal-sys-platfo
type: lesson
status: active
created: "2026-07-09"
owner: manu
tags: [hive, lesson, mypy, typing, cross-platform, windows, dev-workflow, HIVE-293]
---

# mypy narrows platform on a literal `sys.platform`, not on a boolean constant

**Context:** `make typecheck` (`mypy --strict src/`) failed on the Windows dev box with 5 errors in `_deadline.py` — `os.getpgid` / `os.killpg` / `signal.SIGKILL` flagged as non-existent — while CI (Linux) stayed green.
**Problem:** The POSIX-only calls sat in the `else` of `if IS_WINDOWS:`, where `IS_WINDOWS = sys.platform == "win32"` is a module-level constant. mypy performs platform narrowing ONLY on literal `sys.platform == "…"` (and `os.name`, `sys.version_info`) comparisons — NOT on an arbitrary boolean derived from them. So on a Windows analysis mypy treated the POSIX branch as reachable and checked calls that don't exist on `os`/`signal` for win32. Net effect: a green CI (Linux, where the symbols exist) hiding a `make check` that could never pass on the primary dev platform.
**Solution:** Guard the branches with `sys.platform == "win32"` directly and drop the mypy-opaque constant. mypy then treats the non-taken branch as platform-specific dead code and skips it — POSIX branch skipped on Windows, Windows branch skipped on Linux — so `mypy --strict` is clean on both, with zero runtime change (the constant WAS that exact comparison). If a constant must stay for readability, `# type: ignore[attr-defined]` on the flagged lines is the fallback, but removing the indirection is cleaner.
**Why:** A platform-specific type error that surfaces on only one OS is invisible in a single-OS CI matrix. Prefer the narrowable idiom (`sys.platform ==`) over a readable-but-opaque alias so the checker can reason per-platform — "it's the same value" is true at runtime but not to mypy's control-flow analysis. Fixed #293 (found while shipping the unrelated #294, ticketed not inlined per Standing Order #4).
**Tags:** `#mypy` `#typing` `#cross-platform` `#windows` `#dev-workflow` `#HIVE-293`
