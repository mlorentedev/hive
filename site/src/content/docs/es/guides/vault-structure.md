---
title: Estructura del Vault
description: Cómo organizar tu vault de Obsidian para Hive.
---

Hive funciona con **cualquier directorio de archivos Markdown**. No necesitas reestructurar tu vault existente. El layout recomendado a continuación habilita atajos de sección y descubrimiento de proyectos, pero todo es configurable.

## Layout Recomendado

```
~/Projects/knowledge/              # raiz del vault (VAULT_PATH)
├── 00_meta/
│   └── patterns/                  # patrones cross-proyecto
│       ├── pattern-language-standards.md
│       └── pattern-architecture.md
├── 10_projects/
│   ├── mi-proyecto/
│   │   ├── 00-context.md          # atajo "context"
│   │   ├── 10-roadmap.md          # atajo "roadmap"
│   │   ├── 11-tasks.md            # atajo "tasks"
│   │   ├── 90-lessons.md          # atajo "lessons"
│   │   └── 30-architecture/       # subdirectorios arbitrarios
│   │       ├── adr-001.md
│   │       └── adr-002.md
│   └── otro-proyecto/
│       ├── 00-context.md
│       └── 11-tasks.md
└── ...
```

## Usar tu Vault Existente

Si ya tienes un vault de Obsidian con una estructura diferente, configura `HIVE_VAULT_SCOPES` para que coincida con tu layout.

### Ejemplo: Vault con método PARA

```
~/mi-vault/
├── Projects/        # proyectos activos
├── Areas/           # responsabilidades continuas
├── Resources/       # material de referencia
└── Archive/         # elementos completados
```

Configura con:

```bash
# Claude Code
claude mcp add hive \
  -e VAULT_PATH=$HOME/mi-vault \
  -e HIVE_VAULT_SCOPES='{"projects": "Projects", "meta": "Resources", "areas": "Areas"}' \
  -- uvx --upgrade hive-vault
```

Ahora `vault_query(project="mi-app")` encuentra `Projects/mi-app/`, y `vault_query(project="areas:salud")` encuentra `Areas/salud/`.

### Ejemplo: Vault plano (sin carpetas anidadas)

```
~/notas/
├── projects/
│   ├── webapp/
│   └── api/
└── shared/
```

```bash
HIVE_VAULT_SCOPES='{"projects": "projects", "meta": "shared"}'
```

### Cómo funciona la resolución de scopes

1. **Scope explícito** — `vault_query(project="areas:salud")` busca directamente en el directorio del scope `areas`
2. **Auto-scan** — `vault_query(project="mi-app")` escanea todos los scopes (excepto `meta`) y devuelve la primera coincidencia
3. **Atajo meta** — `vault_query(project="_meta", path="patterns/...")` siempre apunta al scope `meta`

Cualquier scope puede contener cualquier número de subdirectorios de proyecto. Hive solo necesita saber dónde buscar.

## Atajos de Sección

Estos nombres de archivo tienen significado especial y se pueden acceder vía el parámetro `section`:

| Atajo | Archivo | Propósito |
|---|---|---|
| `context` | `00-context.md` | Resumen del proyecto, tech stack, decisiones clave |
| `roadmap` | `10-roadmap.md` | Dirección estratégica y milestones |
| `tasks` | `11-tasks.md` | Backlog activo y items del sprint actual |
| `lessons` | `90-lessons.md` | Lecciones aprendidas acumuladas |

Hive prueba primero nombres sin prefijo (`context.md`) antes de la convención numerada (`00-context.md`). Si no usas prefijos numerados, tus archivos siguen funcionando — solo nómbralos `context.md`, `tasks.md`, etc.

**¿No quieres usar atajos?** El parámetro `path` siempre funciona como ruta relativa directa desde el directorio del proyecto:

```python
# Todos estos funcionan, independientemente de tu convencion de nombres:
vault_query(project="mi-app", path="overview.md")
vault_query(project="mi-app", path="docs/architecture.md")
vault_query(project="mi-app", path="notes/2026-03-01.md")
```

## Frontmatter

Hive usa frontmatter YAML para metadatos. Campos requeridos para `vault_write` con `operation="replace"`:

```yaml
---
id: identificador-unico
type: adr | task | lesson | context | runbook
status: draft | active | done | archived
created: 2026-03-01
tags: [python, architecture]
---
```

`vault_write(operation="create")` auto-genera frontmatter — solo necesitas proporcionar el contenido del cuerpo.

## Integración con Git

Todas las operaciones de escritura (`vault_write`, `vault_patch`, `capture_lesson`) hacen auto-commit a git. Esto asegura:

- Historial completo de cambios
- `vault_search(since_days=N)` puede encontrar archivos modificados recientemente
- No se necesita gestión manual de git

Tu directorio del vault debe ser un repositorio git. Si no lo es, ejecuta `git init` en la raíz de tu vault.
