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
│  │ _vault_write │  │ (3 tools)  │  └──────────┘  │
│  │ _vault_health│  └─────┬──────┘                │
│  │ (10 tools)   │        │                       │
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
    │ Obsidian │    │  OpenAI-compatible  │
    │  Vault   │    │  endpoint (/v1)     │
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
| `_vault_ask.py` | Herramienta de Q&A semántico — `vault_ask` (opcional; deshabilitada hasta que se configura un endpoint de embeddings) |
| `_semantic.py` | Chunking e índice de embeddings persistido detrás de `vault_ask` (requiere el extra `[semantic]`) |
| `_vault_health.py` | Herramientas de salud — `vault_health`, constructor de reportes de salud |
| `_workers.py` | Herramientas de worker — `capture_lesson`, `delegate_task`, `worker_status` |
| `config.py` | Configuración pydantic-settings con prefijo `HIVE_` |
| `frontmatter.py` | Parsing, validación y generación de frontmatter YAML |
| `clients.py` | Cliente HTTP async para endpoints compatibles con OpenAI (worker, embeddings, síntesis) |
| `budget.py` | Telemetría de uso SQLite para las peticiones del worker (peticiones, tokens, latencia) con modo WAL. El nombre es histórico: el tope de gasto que aplicaba se eliminó en 4.0.0 |
| `relevance.py` | Puntuación de relevancia basada en EMA para contexto adaptativo |
| `usage.py` | Analíticas de llamadas a herramientas y estimación de tokens |

## Decisiones de Diseño Clave

### Servidor Único, Internos Modulares

La funcionalidad de vault y worker se sirve desde una única instancia FastMCP. Internamente, las herramientas están organizadas en módulos de dominio (`_vault_read`, `_vault_write`, `_vault_health`, `_workers`) que se registran vía funciones `register_*(mcp, ctx)`. El estado compartido vive en un dataclass `ServerContext` (`_context.py`), y las funciones utilitarias puras viven en `_helpers.py`.

### Inyección de Dependencias

`create_server()` acepta overrides opcionales para la ruta del vault, clientes y trackers. Esto permite testear sin infraestructura real.

### Auto-Commit a Git

Todas las escrituras al vault llegan a git, que es lo que da historial completo y permite que `vault_search(since_days=N)` detecte cambios vía `git log`. Desde que existe la cola de commits asíncrona llegan de forma *asíncrona*: una escritura devuelve en cuanto el fichero está en disco, y un hilo reconciler commitea las rutas encoladas en el siguiente tick (`HIVE_COMMIT_TICK_S`). La tasa de commits sigue al tick, no al volumen de escrituras.

Los flujos masivos ya no necesitan tratamiento especial — agrupar es lo que la cola hace por defecto. `commit=True` fuerza un commit síncrono para quien necesite confirmarlo, y `vault_commit(message=...)` sigue haciendo flush del working tree en una sola operación. `vault_delete` se mantiene síncrono y fuera de la cola, así que un borrado y una recreación no pueden colapsar en un mismo tick. Cuando se detecta el plugin obsidian-git en el vault (vía `.obsidian/plugins/obsidian-git/data.json`), `vault_health` expone un bloque `external_committer` y el reconciler le cede el commit en el momento del drain en vez de commitear él.

### Un Worker, Fallos Clasificados

La delegación de workers hace un intento contra un único endpoint compatible con OpenAI; no hay cadena de fallback interna, porque un backend que hace fallback por su cuenta es una segunda autoridad de enrutamiento. Los fallos se clasifican como `pool_unavailable` (el endpoint no atendió la petición) o `task_failed` (respondió y la respuesta es inutilizable), de modo que el llamador que mantiene un mapa de enrutamiento puede decidir si reintentar en otro sitio. La telemetría de uso — peticiones, tokens y latencia por modelo — se registra en una base de datos SQLite con modo WAL y un `threading.Lock` explícito para acceso concurrente thread-safe. No hay tope de gasto.
