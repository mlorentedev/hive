---
title: Vault Structure
description: How to organize your Obsidian vault for Hive.
---

Hive works with **any directory of Markdown files**. You don't need to restructure your existing vault. The recommended layout below enables section shortcuts and project discovery, but everything is configurable.

## Recommended Layout

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

## Using Your Existing Vault

If you already have an Obsidian vault with a different structure, configure `HIVE_VAULT_SCOPES` to match your layout.

### Example: PARA method vault

```
~/my-vault/
├── Projects/        # active projects
├── Areas/           # ongoing responsibilities
├── Resources/       # reference material
└── Archive/         # completed items
```

Configure with:

```bash
# Claude Code
claude mcp add hive \
  -e VAULT_PATH=$HOME/my-vault \
  -e HIVE_VAULT_SCOPES='{"projects": "Projects", "meta": "Resources", "areas": "Areas"}' \
  -- uvx --upgrade hive-vault
```

Now `vault_query(project="my-app")` finds `Projects/my-app/`, and `vault_query(project="areas:health")` finds `Areas/health/`.

### Example: Flat vault (no nested folders)

```
~/notes/
├── projects/
│   ├── webapp/
│   └── api/
└── shared/
```

```bash
HIVE_VAULT_SCOPES='{"projects": "projects", "meta": "shared"}'
```

### How scope resolution works

1. **Explicit scope** — `vault_query(project="areas:health")` looks directly in the `areas` scope directory
2. **Auto-scan** — `vault_query(project="my-app")` scans all scopes (except `meta`) and returns the first match
3. **Meta shortcut** — `vault_query(project="_meta", path="patterns/...")` always targets the `meta` scope

Any scope can contain any number of project subdirectories. Hive just needs to know where to look.

## Section Shortcuts

These filenames have special meaning and can be accessed via the `section` parameter:

| Shortcut | File | Purpose |
|---|---|---|
| `context` | `00-context.md` | Project overview, tech stack, key decisions |
| `roadmap` | `10-roadmap.md` | Strategic direction and milestones |
| `tasks` | `11-tasks.md` | Active backlog and current sprint items |
| `lessons` | `90-lessons.md` | Accumulated lessons learned |

Hive tries bare names first (`context.md`) before the numbered convention (`00-context.md`). If you don't use numbered prefixes, your files still work — just name them `context.md`, `tasks.md`, etc.

**Don't want to use shortcuts?** The `path` parameter always works as a direct relative path from the project directory:

```python
# These all work, regardless of your naming convention:
vault_query(project="my-app", path="overview.md")
vault_query(project="my-app", path="docs/architecture.md")
vault_query(project="my-app", path="notes/2026-03-01.md")
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

All write operations (`vault_update`, `vault_create`, `vault_patch`, `capture_lesson`) auto-commit to git. This ensures:

- Full history of changes
- `vault_recent` can find recently modified files
- No manual git management needed

Your vault directory must be a git repository. If it isn't, run `git init` in your vault root.
