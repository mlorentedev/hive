---
id: lesson-019-evaluating-external-tools-separation-of-conce
type: lesson
status: active
created: "2026-03-07"
owner: manu
tags: [hive, lesson, architecture, yagni, competitive-analysis]
---

# Evaluating external tools — separation of concerns over feature envy

**Context:** Analyzed claude-qmd-sessions (hook-driven session transcript indexing via qmd). Evaluated 4 ideas: auto-briefing hooks, transcript indexing, CWD auto-detection, dual-cap context.
**Problem:** Temptation to absorb external tool patterns into hive (session transcript search, hook automation, CWD-based project detection).
**Solution:** None warranted inclusion. Hooks are user-config (not server feature) — documented instead. Transcript indexing adds noise (hive already has capture_lesson for curated extraction). CWD detection is impossible (MCP server doesn't receive client CWD). Dual-cap is YAGNI.
**Why:** An MCP server should do one thing well (vault access) rather than absorb tangential features. The right response to "interesting external pattern" is often documentation, not code.
**Tags:** `#architecture` `#yagni` `#competitive-analysis`
