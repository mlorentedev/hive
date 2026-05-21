---
title: Arquitectura
description: Arquitectura del sistema y mapa de módulos.
---

## Visión General del Sistema

```
┌─────────────────────────────────────────────────┐
│              Host MCP (cualquier cliente)        │
│         Claude Code, Codex CLI, Cursor, ...     │
└──────────────────────┬──────────────────────────┘
                       │ MCP (stdio)
┌──────────────────────▼──────────────────────────┐
│              Servidor MCP Hive                   │
│                                                  │
│  server.py (registro + recursos + prompts)       │
│  _context.py (estado compartido ServerContext)   │
│                                                  │
│  ┌─────────────┐  ┌────────────┐  ┌──────────┐  │
│  │ Vault+Session│  │Worker Tools│  │Recursos  │  │
│  │ _vault_read  │  │ _workers   │  │(5 URIs)  │  │
│  │ _vault_write │  │ (2 tools)  │  └──────────┘  │
│  │ _vault_health│  └─────┬──────┘                │
│  │ (8 tools)    │        │                       │
│  └──────┬──────┘  ┌─────▼──────┐                 │
│         │         │  clients   │                 │
│  ┌──────▼──────┐  │  budget    │                 │
│  │ _helpers    │  │  config    │                 │
│  │ frontmatter │  └────────────┘                 │
│  │ usage       │                                 │
│  └─────────────┘                                 │
└──────────────────────────────────────────────────┘
         │                    │
    ┌────▼────┐    ┌─────────▼──────────┐
    │ Obsidian │    │   Ollama (local)    │
    │  Vault   │    │   OpenRouter (cloud)│
    └─────────┘    └────────────────────┘
```

## Mapa de Módulos

| Módulo | Rol |
|---|---|
| `server.py` | Capa fina de registro — recursos, prompts, `create_server()` |
| `_context.py` | Dataclass `ServerContext` — estado compartido para todos los handlers |
| `_helpers.py` | Funciones puras — resolución de rutas, formateo, ops git, tracking |
| `_vault_read.py` | Herramientas de lectura — `vault_list`, `vault_query`, `vault_search`, `session_briefing` |
| `_vault_write.py` | Herramientas de escritura — `vault_write`, `vault_patch` |
| `_vault_health.py` | Herramientas de salud — `vault_health`, constructor de reportes de salud |
| `_workers.py` | Herramientas de worker — `capture_lesson`, `delegate_task`, `worker_status` |
| `config.py` | Configuración pydantic-settings con prefijo `HIVE_` |
| `frontmatter.py` | Parsing, validación y generación de frontmatter YAML |
| `clients.py` | Clientes HTTP async para Ollama y OpenRouter |
| `budget.py` | Tracker de presupuesto SQLite con modo WAL (tope $1/mes por defecto) |
| `relevance.py` | Puntuación de relevancia basada en EMA para contexto adaptativo |
| `usage.py` | Analíticas de llamadas a herramientas y estimación de tokens |

## Decisiones de Diseño Clave

### Servidor Único, Internos Modulares

La funcionalidad de vault y worker se sirve desde una única instancia FastMCP. Internamente, las herramientas están organizadas en módulos de dominio (`_vault_read`, `_vault_write`, `_vault_health`, `_workers`) que se registran vía funciones `register_*(mcp, ctx)`. El estado compartido vive en un dataclass `ServerContext` (`_context.py`), y las funciones utilitarias puras viven en `_helpers.py`.

### Inyección de Dependencias

`create_server()` acepta overrides opcionales para la ruta del vault, clientes y trackers. Esto permite testear sin infraestructura real.

### Auto-Commit a Git

Por defecto, todas las escrituras al vault hacen auto-commit a git. Esto proporciona historial completo y permite que `vault_search(since_days=N)` detecte cambios vía `git log`.

Para flujos masivos, `vault_write(commit=False)` y `vault_patch(commit=False)` escriben a disco pero difieren el commit; una llamada posterior a `vault_commit(message=...)` hace flush del working tree en una sola operación. Cuando se detecta el plugin obsidian-git en el vault (vía `.obsidian/plugins/obsidian-git/data.json`), `vault_health` expone un bloque `external_committer` para que las personas usuarias sepan que el intervalo de auto-commit ya está cubierto por el plugin.

### Controles de Presupuesto

La delegación de workers usa una base de datos SQLite con modo WAL y `threading.Lock` explícito para acceso concurrente thread-safe. Los topes mensuales y límites por petición previenen sobrecostes.
