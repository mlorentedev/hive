---
title: Herramientas de Vault
description: 7 herramientas para consultar, buscar y gestionar tu vault de Obsidian.
---

## vault_list

Lista proyectos o navega archivos dentro de un proyecto.

```python
# Listar todos los proyectos
vault_list()

# Navegar archivos de un proyecto
vault_list(project="mi-proyecto")

# Navegar un subdirectorio
vault_list(project="mi-proyecto", path="30-architecture")

# Filtrado con patrón glob
vault_list(project="mi-proyecto", pattern="adr-*")
```

Sin `project`, lista todos los proyectos del vault con recuento de archivos y atajos. Con `project`, lista archivos y directorios (directorios primero, luego archivos). Con `pattern`, busca recursivamente todos los archivos que coincidan.

### Resolución de scopes

Los proyectos se organizan en **scopes** — directorios de nivel superior del vault. Por defecto, Hive incluye tres scopes:

| Scope | Directorio | Propósito |
|---|---|---|
| `projects` | `10_projects/` | Proyectos personales |
| `work` | `50_work/` | Trabajo: productos, clientes, tickets, desarrollo |
| `meta` | `00_meta/` | Patrones y plantillas cross-proyecto |

Usa la sintaxis `scope:slug` para apuntar a un scope específico: `vault_list(project="work:hydra3d-plus")`.

**Scopes jerárquicos:** El scope `work` (y cualquier scope) soporta directorios anidados. Si un slug no se encuentra como hijo directo del directorio del scope, Hive busca recursivamente usando búsqueda en amplitud (BFS). Por ejemplo, `work:hydra3d-plus` resuelve a `50_work/20-products/hydra3d-plus/` automáticamente.

Usa rutas explícitas con `/` para saltar BFS: `work:20-products/hydra3d-plus`.

Sin prefijo de scope, Hive escanea todos los scopes en orden — la primera coincidencia gana.

## vault_query

Lee secciones o archivos bajo demanda.

```python
vault_query(
    project="mi-proyecto",
    section="context",       # context | tasks | roadmap | lessons
    path="",                 # ruta arbitraria (sobreescribe section)
    max_lines=200,           # 0 = sin límite
    include_metadata=False   # anteponer resumen de frontmatter
)
```

Los atajos de sección mapean a archivos:
- `context` -> `00-context.md`
- `tasks` -> `11-tasks.md`
- `roadmap` -> `10-roadmap.md`
- `lessons` -> `90-lessons.md`

Usa `path` para archivos arbitrarios: `vault_query(project="mi-proyecto", path="30-architecture/adr-001.md")`

Usa `project="_meta"` para acceder a `00_meta/` (patrones cross-proyecto).

## vault_search

Búsqueda full-text en el vault con ranking opcional y modo de cambios recientes.

```python
# Búsqueda estándar por palabra clave
vault_search(
    query="autenticación",
    max_lines=100,
    type_filter="",     # filtrar por tipo en frontmatter
    status_filter="",   # filtrar por estado en frontmatter
    tag_filter="",      # filtrar por tag en frontmatter
    use_regex=False     # tratar query como expresión regular
)

# Búsqueda con ranking (puntuación de relevancia)
vault_search(query="deployment", ranked=True, max_results=5)

# Cambios recientes (últimos N días)
vault_search(since_days=7, project="mi-proyecto")

# Restringir búsqueda a un scope
vault_search(query="LVDS", scope="work")

# Ranking por refuerzo de lecciones (HIVE-97)
vault_search(query="timeout", rank_by="reinforcements")  # más leídas primero
vault_search(query="timeout", rank_by="confidence")      # mayor confianza decaída
vault_search(query="timeout", rank_by="hybrid")          # α=0.7 BM25 + 0.3 confianza
```

**Modo estándar:** Devuelve líneas coincidentes agrupadas por archivo, con cabeceras de metadatos. Cuando `use_regex=True`, la query se compila como expresión regular de Python (case-insensitive).

**Modo ranking** (`ranked=True`): Puntúa resultados por peso de estado (active > draft > archived), recencia y densidad de coincidencias. Devuelve los mejores resultados con metadatos y líneas coincidentes.

**Modo reciente** (`since_days > 0`): Combina historial de git con fechas `created` del frontmatter para encontrar archivos modificados en los últimos N días. Filtro opcional por `project`.

**Filtro de scope** (`scope`): Restringe la búsqueda a un solo scope (ej. `"work"`, `"projects"`). Funciona en los tres modos. Sin `scope`, busca en todo el vault.

**Modo rank-de-lecciones** (`rank_by != "bm25"`): Filtra coincidencias a `90-lessons.md` únicamente y rankea por la señal de uso elegida — cada lección surfaceada se incrementa una vez por llamada. `hybrid` mezcla BM25 (α=0.7) con confianza (1−α=0.3). Valores `rank_by` desconocidos devuelven un error claro en lugar de caer silenciosamente a BM25.

## vault_health

Identidad del servidor, métricas de salud, detección de drift y estadísticas de uso.

```python
# Reporte de salud de todos los proyectos (siempre incluye el bloque ## server)
vault_health()

# Validar un proyecto específico
vault_health(project="mi-proyecto", checks=["frontmatter", "stale", "links"], max_issues=50)

# Incluir estadísticas de uso
vault_health(include_usage=True, usage_days=30)

# Incluir metadatos de runtime (uptime, tools registrados, presupuesto OpenRouter)
vault_health(include_runtime=True)
```

**Bloque de identidad** (siempre activo, ~5 líneas): Prepended a cada respuesta correcta para que los hosts MCP y operadores puedan responder *"¿qué versión de hive-vault sirve esta sesión?"* sin salir de la conversación.

```text
## server
- version: 1.15.0
- python: 3.12.10
- vault_path: /home/me/Projects/knowledge
- backends: {"ollama": true, "openrouter": false}
- started_at: 2026-05-21T20:33:20+00:00
```

Los backends se reportan como booleanos de presencia únicamente — **las API keys nunca se embeben**. `ollama` refleja la probe de disponibilidad cacheada; `openrouter` es true cuando se configuró una API key al arrancar.

**Reporte de salud** (por defecto): Recuento de archivos por proyecto, total de líneas, archivos obsoletos (>180 días, configurable vía `HIVE_STALE_THRESHOLD_DAYS`), cobertura de secciones. También detecta **nombres de directorio duplicados** en scopes jerárquicos y advierte qué ruta resolverá BFS.

**Validación** (parámetro `checks`): Detector de drift para problemas comunes:
- **frontmatter**: Frontmatter YAML faltante o malformado, campos requeridos ausentes (id, type, status), fechas no parseables
- **stale**: Archivos activos no modificados en `HIVE_STALE_THRESHOLD_DAYS` (por defecto 180)
- **links**: `[[wikilinks]]` rotos apuntando a archivos inexistentes

Los problemas se categorizan como `[error]` o `[warning]` con ruta del archivo y descripción.

**Estadísticas de uso** (`include_usage=True`): Recuento de llamadas a herramientas por herramienta y proyecto, con ahorro estimado de tokens.

**Bloque de runtime** (`include_runtime=True`, opt-in): Diagnósticos dinámicos al final del reporte — uptime en segundos, número + nombres de tools MCP registrados (sanity check de que ningún módulo falló al registrarse), y snapshot del presupuesto OpenRouter (`spent_usd`, `cap_usd`, `period`). Independiente de `include_usage`; ambos pueden combinarse en una sola llamada.

## vault_write

Crea, agrega o reemplaza archivos del vault con validación de frontmatter. Hace auto-commit a git por defecto; pasa `commit=False` para diferir el commit y hacer flush de un lote más tarde vía [`vault_commit`](#vault_commit).

```python
# Agregar a un archivo existente
vault_write(
    project="mi-proyecto",
    section="lessons",
    content="Nueva lección aprendida...",
    operation="append"      # por defecto
)

# Reemplazar un archivo completo (requiere frontmatter válido)
vault_write(
    project="mi-proyecto",
    section="context",
    content="---\nid: mi-proyecto\ntype: project\nstatus: active\n---\n\nContenido nuevo.",
    operation="replace"
)

# Crear un nuevo archivo con frontmatter auto-generado
vault_write(
    project="mi-proyecto",
    path="30-architecture/adr-005.md",
    content="# ADR-005: PostgreSQL\n\n## Contexto\n...",
    operation="create",
    doc_type="adr"          # usado en frontmatter generado
)
```

- **append**: Agrega contenido al final de un archivo existente
- **replace**: Reemplaza el archivo completo. Requiere frontmatter YAML válido con `id`, `type`, `status`
- **create**: Crea un nuevo archivo. Auto-genera frontmatter con `id`, `type`, `status: draft`, `created: hoy`

Todas las operaciones hacen auto-commit a git por defecto. Pasa `commit=False` para diferir el commit — el archivo se escribe en disco pero `git status` lo seguirá mostrando como modificado. Haz flush de un lote más tarde con [`vault_commit`](#vault_commit), o deja que un committer externo (p. ej. el auto-commit de obsidian-git) lo recoja. Ver [Configuración → Configuración recomendada](/es/configuration/#configuración-recomendada) para el contrato de durabilidad.

## vault_patch

Buscar y reemplazar quirúrgico en un archivo del vault.

```python
# Reemplazo simple
vault_patch(
    project="mi-proyecto",
    path="30-architecture/adr-001.md",
    find="status: draft",
    replace="status: accepted"
)

# Múltiples reemplazos (aplicados en secuencia)
vault_patch(
    project="mi-proyecto",
    path="11-tasks.md",
    patches=[
        {"find": "- [ ] Tarea uno", "replace": "- [x] Tarea uno"},
        {"find": "- [ ] Tarea dos", "replace": "- [x] Tarea dos"},
    ]
)
```

Reemplaza exactamente una ocurrencia de `find` con `replace`. Rechaza coincidencias ambiguas — si `find` aparece más de una vez, la operación falla con un error pidiendo más contexto. Usa coincidencia en cascada de 3 pasadas: exacta → solo cuerpo → normalizada por espacios. Auto-commit a git por defecto; pasa `commit=False` para diferir el commit (mismo contrato que `vault_write`).

## vault_commit

Hace flush de todas las escrituras pendientes en un único commit de git. Compañera de `vault_write(commit=False)` y `vault_patch(commit=False)`.

```python
vault_commit(message="vault: checkpoint fin de sesión")
```

Stage todo lo modificado en el vault (`git add -A`) y crea un único commit. Devuelve el nuevo SHA, un aviso de árbol limpio si no hay nada modificado, o un error legible. Idealmente combinado con el plugin obsidian-git (ver [Configuración → Configuración recomendada](/es/configuration/#configuración-recomendada)).

## capture_lesson

Captura lecciones inline, extracción por lotes desde texto, o look-up de lecciones existentes por palabra clave.

```python
# Captura inline (estructurada, una lección)
capture_lesson(
    project="mi-proyecto",
    title="La causa raíz era caché obsoleta",
    context="Depurando fallo de deploy",
    problem="El servicio devolvió 500 después del deploy",
    solution="Limpiar caché de Redis después de cambios de configuración",
    tags=["deploy", "cache"]    # opcional
)

# Extracción por lotes (con worker, múltiples lecciones)
capture_lesson(
    project="mi-proyecto",
    text="Descubrimos que la caché estaba obsoleta después del deploy...",
    min_confidence=0.7,
    max_lessons=5
)

# Modo look-up — recuperar lecciones existentes por palabra clave (HIVE-97)
capture_lesson(
    project="mi-proyecto",
    find="cache",
    rank_by="reinforcements",   # o "confidence", "hybrid"
    max_lessons=5
)
```

**Modo inline** (sin `text`, sin `find`): Agrega una entrada estructurada a `90-lessons.md` con fecha, contexto, problema y solución. Crea el archivo con frontmatter si no existe. Deduplicación por título. Auto-commit a git. Cada inline write también siembra una fila baseline en la tabla de refuerzo a `confidence=0.7`.

**Modo por lotes** (`text` proporcionado): Envía el texto a un modelo worker (Ollama/OpenRouter) que extrae lecciones estructuradas (título, contexto, problema, solución, tags, confianza). Las lecciones por encima del umbral de confianza se escriben en `90-lessons.md` con deduplicación y se siembran en la tabla de refuerzo.

**Modo look-up** (`find` proporcionado): Grepea headings de lecciones (consciente de bloques de código) por la palabra clave, rankea las coincidencias por `rank_by` (default `reinforcements`), incrementa cada lección surfaceada una vez, y devuelve las top `max_lessons`. Simetría tool-única: `capture_lesson` ESCRIBE lecciones Y las consulta.

**Cuándo usar:** Inmediatamente después de descubrir la causa raíz de un bug, un insight arquitectónico o un truco de depuración — no esperes al final de la sesión. Usa `find=` para recuperar lecciones pasadas por tema.
