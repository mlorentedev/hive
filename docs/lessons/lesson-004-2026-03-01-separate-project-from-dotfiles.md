---
id: lesson-004-2026-03-01-separate-project-from-dotfiles
type: lesson
status: active
created: "2026-05-01"
owner: manu
tags: [hive, lesson]
---

# 2026-03-01: Separate project from dotfiles

- **Context:** Could live inside dotfiles or as standalone project.
- **Decision:** Standalone `~/Projects/hive`, deployed by dotfiles setup scripts.
- **Rationale:** Own lifecycle, own dependencies (pyproject.toml), own tests. Shareable with team members who don't need personal dotfiles. Dotfiles references hive, doesn't contain it.
