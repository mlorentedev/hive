---
id: lesson-027-bfs-hierarchical-scope-resolution-for-nested-
type: lesson
status: active
created: "2026-03-26"
owner: manu
tags: [hive, lesson, vault, scope-resolution, bfs, mcp]
---

# BFS hierarchical scope resolution for nested vault directories

**Context:** Hive vault uses a flat `10_projects/<slug>/` layout, but the new `50_work/` scope is multi-level (e.g. `50_work/45-development/<family>/<component>/`). The existing flat resolver only saw direct children of the scope root, so deep projects were unreachable via short slug — users had to spell out the full path.
**Problem:** `_resolve_project_dir` could not find a slug like `hydra3d-plus` under a nested work tree. Adding a `work` scope without changing resolution would have forced verbose literal paths for every work query, breaking the ergonomic short-slug API users already had for `10_projects`.
**Solution:** Switched `_resolve_project_dir` to breadth-first traversal: try direct child first, then BFS through subdirectories — shallowest match wins. Slugs containing `/` bypass BFS and resolve as literal relative paths inside the scope (escape hatch for collisions or explicit targeting). Added `scope` filter to `vault_search` (restricts all 3 modes to a single scope) and `_find_duplicate_names` in `vault_health` to surface BFS collisions before they cause silent mis-resolution.
**Why:** BFS preserves the short-slug API across heterogeneous vault layouts (flat for projects, nested for work). Shallowest-wins keeps resolution deterministic; the explicit-path escape hatch covers the duplicate-name edge case without coupling tools to a single layout. Duplicate detection in `vault_health` makes the previously silent collision surface visible at audit time.
**Tags:** `#vault` `#scope-resolution` `#bfs` `#mcp`
