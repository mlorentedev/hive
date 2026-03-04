---
title: Vault Structure
description: How to organize your Obsidian vault for Hive.
---

Hive expects an Obsidian vault with a specific directory layout. This structure enables section shortcuts, project discovery, and cross-project pattern access.

## Directory Layout

```
~/Projects/knowledge/              # vault root (VAULT_PATH)
├── 00_meta/
│   └── patterns/                  # cross-project patterns
│       ├── pattern-language-standards.md
│       └── pattern-architecture.md
├── 10_projects/
│   ├── my-project/
│   │   ├── 00-context.md          # "context" shortcut
│   │   ├── 10-roadmap.md          # "roadmap" shortcut
│   │   ├── 11-tasks.md            # "tasks" shortcut
│   │   ├── 90-lessons.md          # "lessons" shortcut
│   │   └── 30-architecture/       # arbitrary subdirectories
│   │       ├── adr-001.md
│   │       └── adr-002.md
│   └── another-project/
│       ├── 00-context.md
│       └── 11-tasks.md
└── ...
```

## Section Shortcuts

These filenames have special meaning and can be accessed via the `section` parameter:

| Shortcut | File | Purpose |
|---|---|---|
| `context` | `00-context.md` | Project overview, tech stack, key decisions |
| `roadmap` | `10-roadmap.md` | Strategic direction and milestones |
| `tasks` | `11-tasks.md` | Active backlog and current sprint items |
| `lessons` | `90-lessons.md` | Accumulated lessons learned |

## Cross-Project Access

Use `project="_meta"` to access files in `00_meta/`:

```python
vault_query(project="_meta", path="patterns/pattern-language-standards.md")
```

## Frontmatter

Hive uses YAML frontmatter for metadata. Required fields for `vault_update` with `operation="replace"`:

```yaml
---
id: unique-identifier
type: adr | task | lesson | context | runbook
status: draft | active | done | archived
created: 2026-03-01
tags: [python, architecture]
---
```

`vault_create` auto-generates frontmatter — you only need to provide the body content.

## Git Integration

All write operations (`vault_update`, `vault_create`) auto-commit to git. This ensures:

- Full history of changes
- `vault_recent` can find recently modified files
- No manual git management needed
