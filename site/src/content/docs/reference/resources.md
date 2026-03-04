---
title: Resources
description: MCP resources exposed by Hive.
---

Hive exposes 5 MCP resources that can be consumed directly by AI clients.

## Static Resources

### hive://projects

Lists all vault projects with file counts and available section shortcuts.

```
URI: hive://projects
```

### hive://health

Vault health metrics for all projects — file counts, line counts, stale file detection, and section coverage.

```
URI: hive://health
```

## Resource Templates

These resources accept a `{project}` parameter:

### hive://projects/{project}/context

Returns the project's context document (`00-context.md`).

```
URI: hive://projects/my-project/context
```

### hive://projects/{project}/tasks

Returns the project's task backlog (`11-tasks.md`).

```
URI: hive://projects/my-project/tasks
```

### hive://projects/{project}/lessons

Returns the project's lessons learned (`90-lessons.md`).

```
URI: hive://projects/my-project/lessons
```

## Resources vs Tools

Resources are **read-only data endpoints** — they return content but don't accept complex parameters. Use them when you need to load a known document.

Tools are **actions** — they accept parameters, support filtering, and can write data. Use them for search, updates, and complex queries.

| Need | Use |
|---|---|
| Load project context | `hive://projects/{project}/context` resource |
| Search across vault | `vault_search` tool |
| Update a file | `vault_update` tool |
| List projects | `hive://projects` resource OR `vault_list_projects` tool |
