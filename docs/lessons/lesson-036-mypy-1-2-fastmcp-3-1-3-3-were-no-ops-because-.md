---
id: lesson-036-mypy-1-2-fastmcp-3-1-3-3-were-no-ops-because-
type: lesson
status: active
created: "2026-05-19"
owner: manu
tags: [hive, lesson, deps, tooling, process]
---

# mypy 1→2 + fastmcp 3.1→3.3 were no-ops because --strict already covered the surface

**Context:** Two "risky major" dep bumps in v1.13.0 stabilization cycle: mypy 1.19.1 → 2.1.0 (tightened defaults) and fastmcp 3.1.0 → 3.3.1 (MCP framework). Both initially classified as needing dedicated smoke + risk assessment.
**Problem:** Risk classification of major bumps tends to over-estimate effort for codebases that already use strict configs. mypy 2's "tightened defaults" are a subset of what --strict already enforces. fastmcp 3.3's API surface for @mcp.tool / call_tool / FastMCP() was stable across 3.1 → 3.3. Spending separate PR cycles on each was lower ROI than expected.</problem>
<parameter name="solution">For major bumps in projects already on strict configs (mypy --strict, ruff with aggressive ruleset, full async typing), run `make check` once on the bumped lockfile BEFORE designing a multi-step smoke plan. If green, ship as a small lockfile-only PR. The integration tests already exercise the framework wire; the type checker already enforces the strictest contracts. Save the dedicated-smoke effort for bumps where the project's own strictness DOESN'T cover the change vector (e.g. API rename, behavior flag flip).
**Solution:** 
**Tags:** `#deps` `#tooling` `#process`
