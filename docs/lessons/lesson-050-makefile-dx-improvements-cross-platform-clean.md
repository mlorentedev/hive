---
id: lesson-050-makefile-dx-improvements-cross-platform-clean
type: lesson
status: active
created: "2026-05-27"
owner: manu
tags: [hive, lesson, dx, makefile, cross-platform]
---

# Makefile DX improvements: cross-platform clean, test-one, logs target

**Context:** DX bundle improvements during 2026-05-27 debt triage session.
**Problem:** make clean used rm -rf (POSIX-only), no make test-one target existed for quick single-test runs, log path was only documented in troubleshooting docs.
**Solution:** Replaced rm -rf in make clean with uv run python -c (cross-platform), added make test-one ARGS=... target, added make logs target that shows path + tail -f tip. Updated .claude/CLAUDE.md with upstream _compat.py tracker + issue #127 reference.
**Tags:** `#dx` `#makefile` `#cross-platform`
