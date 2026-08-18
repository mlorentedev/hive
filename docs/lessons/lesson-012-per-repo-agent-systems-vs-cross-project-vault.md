---
id: lesson-012-per-repo-agent-systems-vs-cross-project-vault
type: lesson
status: active
created: "2026-03-05"
owner: manu
tags: [hive, lesson, competitive-analysis, architecture, ci-cd, self-improvement]
---

# Per-repo agent systems vs cross-project vault — competitive analysis

**Context:** Analyzing Bruno Bertolini's per-repo .agent/ system (rules, skills, agents, specs) that runs 6 CI agents on every PR for quality gates, security, architecture, and dogfood enforcement.
**Problem:** Should Hive adopt CI-based quality gate agents (architecture check, security scan, dogfood enforcement) or a PRD→techspec→exec pipeline? Is the per-repo .agent/ pattern superior to a cross-project vault?
**Solution:** No features to copy. Per-repo agents excel at CI automation but are limited to single-repo scope, scale poorly with .md files (author admits "too big"), and lack cross-project context. Hive's strengths (session_briefing, smart_search, EMA relevance, cross-project vault, capture_lesson) are exactly what per-repo systems cannot do. The only actionable takeaway is P3 drift detector (vault_validate) which is already on roadmap — validates code changes against vault ADRs/patterns. PRD→exec pipeline already covered by Claude Code skills (/prd, /writing-plans). Self-improvement loop is our P2 (auto-capture). RAG not needed until 500+ files (ADR-003).
**Tags:** `#competitive-analysis` `#architecture` `#ci-cd` `#self-improvement`
