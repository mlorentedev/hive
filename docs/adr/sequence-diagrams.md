---
id: sequence-diagrams
type: architecture
status: active
created: "2026-03-03"
owner: manu
---

# Hive: Sequence Diagrams

> End-to-end flows for the three primary interaction patterns.
> Rendered by Obsidian or any Mermaid-compatible viewer.

## 1. Vault Query Flow

Standard read path — covers `vault_query`, `vault_search`, `vault_smart_search`.

```mermaid
sequenceDiagram
    participant C as Claude Code
    participant V as Vault MCP Server
    participant FS as Filesystem
    participant G as Git

    C->>V: vault_query(project, section)
    V->>V: _resolve_file(vault_path, project, section, path)
    alt project not found
        V-->>C: "Project 'X' not found."
    else file not found
        V-->>C: "'section' not found in project 'X'."
    else file exists
        V->>FS: Path.read_text()
        FS-->>V: raw content
        V->>V: _truncate(content, max_lines)
        opt include_metadata=True
            V->>V: parse_frontmatter(content)
            V->>V: prepend metadata line
        end
        V-->>C: truncated content
    end
```

## 2. Worker Delegation Flow

Task delegation with 3-tier routing and budget control.

```mermaid
sequenceDiagram
    participant C as Claude Code
    participant W as Worker MCP Server
    participant O as Ollama
    participant OR as OpenRouter
    participant B as Budget DB

    C->>W: delegate_task(prompt, task_type)
    W->>O: POST /api/generate
    alt Ollama available
        O-->>W: response
        W-->>C: result (model: qwen2.5-coder:7b)
    else Ollama unreachable
        W->>OR: POST /chat/completions (free model)
        alt free tier succeeds
            OR-->>W: response
            W-->>C: result (model: qwen3-coder:free)
        else free tier fails & max_cost > 0
            W->>B: can_spend(cost)?
            alt budget allows
                B-->>W: true
                W->>OR: POST /chat/completions (paid model)
                OR-->>W: response + cost
                W->>B: record(cost)
                W-->>C: result (model: deepseek)
            else budget exhausted
                B-->>W: false
                W-->>C: "Budget exhausted."
            end
        else no fallback
            W-->>C: "All models unavailable."
        end
    end
```

## 3. Session Briefing Flow

One-call cold-start that replaces 3–4 manual tool calls.

```mermaid
sequenceDiagram
    participant C as Claude Code
    participant V as Vault MCP Server
    participant FS as Filesystem
    participant G as Git

    C->>V: session_briefing(project)
    V->>V: _resolve_project_dir(project)
    alt project not found
        V-->>C: "Project 'X' not found."
    else project exists
        V->>FS: read tasks (11-tasks.md)
        FS-->>V: tasks content
        V->>V: _truncate(tasks, 50 lines)

        V->>FS: read lessons (90-lessons.md)
        FS-->>V: lessons content
        V->>V: last 30 lines

        V->>G: git log --oneline -5
        G-->>V: recent commits

        V->>FS: rglob("*.md") in project dir
        FS-->>V: file list
        V->>V: count files + detect stale

        V->>V: assemble markdown sections
        V-->>C: "# Session Briefing — {project}\n## Active Tasks\n..."
    end
```

## 4. Resource Discovery Flow

MCP clients discovering vault content without knowing tool names.

```mermaid
sequenceDiagram
    participant MC as MCP Client
    participant V as Vault MCP Server
    participant FS as Filesystem

    MC->>V: list_resources()
    V-->>MC: [hive://projects, hive://health]

    MC->>V: list_resource_templates()
    V-->>MC: [hive://projects/{project}/context, .../tasks, .../lessons]

    MC->>V: read_resource("hive://projects/hive/context")
    V->>V: _resolve_file(vault_path, "hive", "context", "")
    V->>FS: Path.read_text()
    FS-->>V: content
    V->>V: _truncate(content, 200)
    V-->>MC: ResourceResult(content)
```

## 5. Vault Write Flow

Write path with frontmatter validation and atomic git commit.

```mermaid
sequenceDiagram
    participant C as Claude Code
    participant V as Vault MCP Server
    participant FS as Filesystem
    participant G as Git

    C->>V: vault_update(project, section, "replace", content)
    V->>V: validate_frontmatter(content)
    alt invalid frontmatter
        V-->>C: "Frontmatter validation failed: ..."
    else valid
        V->>FS: Path.write_text(content)
        V->>G: git add <file>
        V->>G: git commit -m "vault: update project/section"
        G-->>V: committed
        V-->>C: "Updated project/section (replace)."
    end
```

## 6. Restart-on-Upgrade Flow (Daemon Mode)

How a supervised `hive serve` adopts a newer published version without a manual
restart — the exit-75 / `Restart=on-failure` contract behind daemon auto-update
(ADR-011). See the [daemon mode guide](../../site/src/content/docs/guides/daemon-mode.md).

```mermaid
sequenceDiagram
    participant Op as Operator / timer
    participant Disk as Installed package
    participant Sup as Supervisor (systemd --user / Task Scheduler)
    participant D as hive serve (daemon)
    participant Cl as hive client (session)

    Op->>Disk: uv tool upgrade hive-vault
    Note over D: booted at version v_boot
    loop every HIVE_UPGRADE_POLL_S
        D->>Disk: importlib.metadata.version("hive-vault")
        Disk-->>D: v_current
        alt v_current == v_boot (or version not-found, swap window)
            Note over D: keep serving — no restart
        else version drift detected
            D->>D: should_exit = True (cooperative stop)
            D-->>Sup: exit 75 (EX_TEMPFAIL)
            Note over Cl: daemon briefly down — hive client<br/>degrades to in-process server (never breaks)
            Sup->>D: Restart=on-failure relaunch
            Note over D: re-booted at version v_current
        end
    end
    Note over Sup,D: graceful signal stop / declined install exits 0 — NO restart
```
