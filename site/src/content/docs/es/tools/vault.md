---
title: Herramientas de Vault
description: Herramientas para consultar, buscar y gestionar tu vault de Obsidian.
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

Los proyectos se organizan en **scopes** — directorios de nivel superior del vault. Por defecto, Hive incluye cuatro scopes:

| Scope | Directorio | Propósito |
|---|---|---|
| `projects` | `10_projects/` | Proyectos personales |
| `work` | `50_work/` | Trabajo: productos, clientes, tickets, desarrollo |
| `agents` | `80_agents/` | Buzones de agentes de IA — cada subdirectorio es un proyecto de primera clase |
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

## vault_ask

Haz una pregunta en lenguaje natural y obtén una respuesta basada en tu vault, con citas de las fuentes. **Opcional — deshabilitada hasta que se configura un backend semántico.** Para búsquedas por palabra clave o regex, usa [`vault_search`](#vault_search).

```python
vault_ask(question="¿Por qué elegimos PostgreSQL en lugar de MySQL?")
```

`question` es el único parámetro. Usa `question`, no `query` ni `prompt`.

**Cómo habilitarla:** instala el extra `[semantic]` (`pip install 'hive-vault[semantic]'`) y define `HIVE_EMBED_BASE_URL` y `HIVE_EMBED_MODEL` (más `HIVE_EMBED_API_KEY` si el endpoint la necesita) apuntando a un endpoint de embeddings compatible con OpenAI. Un servicio alojado y un runtime local se configuran igual; Hive no nombra ningún proveedor. Consulta [Configuración → Configurar Q&A Semántico](/hive/es/configuration/#configurar-qa-semántico-vault_ask).

**Comportamiento según el estado:**

- **Sin `HIVE_EMBED_BASE_URL`:** devuelve un mensaje breve con cómo habilitarla y nunca falla. Las instalaciones base no se ven afectadas ni pagan coste alguno — no se construye ningún índice ni se generan embeddings al escribir.
- **Endpoint definido pero falta el extra `[semantic]`:** devuelve la instrucción de instalación.
- **Habilitada, `HIVE_SYNTH_MODEL` vacío:** genera el embedding de la pregunta, busca en el índice y devuelve las 5 secciones del vault más relevantes con su archivo de origen, encabezado y puntuación de similitud. No se hace ninguna llamada a un LLM.
- **Habilitada, `HIVE_SYNTH_MODEL` definido:** envía esas secciones al modelo de chat (mismo endpoint y clave que los embeddings) y devuelve una respuesta sintetizada que cita `[source: path/to/file.md]` para cada afirmación. Al modelo se le indica que responda solo a partir de las secciones recuperadas y que lo diga cuando la respuesta no esté ahí. Si la llamada de síntesis falla por cualquier motivo, `vault_ask` recurre a las secciones recuperadas en lugar de fallar.

```text
# vault_ask: '¿Por qué elegimos PostgreSQL en lugar de MySQL?'

### 1. 30-architecture/adr-005.md — 'Context'  (score: 0.82)
> Evaluamos PostgreSQL y MySQL para el almacén principal...
```

**El índice:** se construye de forma perezosa en la primera llamada, se persiste en un directorio `index/` junto a `HIVE_DB_PATH` y se actualiza de forma incremental cuando `vault_write` o `vault_patch` modifican un archivo. Cambiar `HIVE_EMBED_MODEL` provoca una reconstrucción. Una `question` vacía devuelve `vault_ask: provide a non-empty question.`

## session_briefing

Carga el contexto de trabajo de un proyecto en una sola llamada — la herramienta que se ejecuta al inicio de una sesión.

```python
# Listar los proyectos disponibles
session_briefing()

# Cargar un proyecto
session_briefing(project="my-project")
```

`project` es el único parámetro; no existe un argumento `days` — la ventana del briefing es fija.

Sin `project`, devuelve la lista de proyectos disponibles con una pista para pasar `project=<slug>`. Con `project`, ensambla una sola respuesta (sustituye a tres o cuatro llamadas manuales):

- `## Active Tasks` — el archivo de tareas del proyecto, truncado a 50 líneas
- `## Recent Lessons` — las últimas 30 líneas de su archivo de lecciones
- `## Recent Vault Activity` — los últimos 5 commits de git del vault
- `## Project Health` — número de archivos y, si los hay, el número de archivos obsoletos (anteriores a `HIVE_STALE_THRESHOLD_DAYS`)

**Orden adaptativo:** `Active Tasks` y `Recent Lessons` se ordenan según la frecuencia con la que has leído cada sección en briefings anteriores, ajustada con `HIVE_RELEVANCE_ALPHA`, `HIVE_RELEVANCE_DECAY` y `HIVE_RELEVANCE_EPSILON`; sin historial, las tareas van primero. La actividad y la salud van siempre al final. Si el seguimiento de relevancia agota su timeout, el briefing se devuelve igualmente con una nota indicando que muestra el orden de secciones por defecto.

Un proyecto desconocido devuelve un mensaje de no encontrado en lugar de un briefing vacío.

## vault_health

Identidad del servidor, métricas de salud, detección de drift y estadísticas de uso.

```python
# Reporte de salud de todos los proyectos (siempre incluye el bloque ## server)
vault_health()

# Validar un proyecto específico
vault_health(project="mi-proyecto", checks=["frontmatter", "stale", "links"], max_issues=50)

# Incluir estadísticas de uso
vault_health(include_usage=True, usage_days=30)

# Incluir metadatos de runtime (uptime, tools registrados, uso del worker)
vault_health(include_runtime=True)
```

**Bloque de identidad** (siempre activo, ~5 líneas): Prepended a cada respuesta correcta para que los hosts MCP y operadores puedan responder *"¿qué versión de hive-vault sirve esta sesión?"* sin salir de la conversación.

```text
## server
- version: 1.15.0
- python: 3.12.10
- vault_path: /home/me/Projects/knowledge
- backends: {"worker": true, "worker_reachable": "unprobed"}
- started_at: 2026-05-21T20:33:20+00:00
```

Los backends se reportan como booleanos de presencia únicamente — **las API keys nunca se embeben**. `worker` es true cuando hay un endpoint de worker configurado (hay una URL base definida). Eso no dice nada sobre si responde: aquí `worker_reachable` es siempre `"unprobed"`, así que llama a [`worker_status`](/hive/es/tools/worker/#worker_status) para sondearlo.

**Reporte de salud** (por defecto): Recuento de archivos por proyecto, total de líneas, archivos obsoletos (>180 días, configurable vía `HIVE_STALE_THRESHOLD_DAYS`), cobertura de secciones. También detecta **nombres de directorio duplicados** en scopes jerárquicos y advierte qué ruta resolverá BFS.

**Validación** (parámetro `checks`): Detector de drift para problemas comunes:
- **frontmatter**: Frontmatter YAML faltante o malformado, campos requeridos ausentes (id, type, status), fechas no parseables
- **stale**: Archivos activos no modificados en `HIVE_STALE_THRESHOLD_DAYS` (por defecto 180)
- **links**: `[[wikilinks]]` rotos apuntando a archivos inexistentes

Los problemas se categorizan como `[error]` o `[warning]` con ruta del archivo y descripción.

**Estadísticas de uso** (`include_usage=True`): Recuento de llamadas a herramientas por herramienta y proyecto, con ahorro estimado de tokens.

**Bloque de runtime** (`include_runtime=True`, opt-in): Diagnósticos dinámicos al final del reporte — uptime en segundos, número + nombres de tools MCP registrados (sanity check de que ningún módulo falló al registrarse), y snapshot del uso del worker (`request_count`, `total_tokens`, `period`). Independiente de `include_usage`; ambos pueden combinarse en una sola llamada.

`git_repo` informa de si el vault es siquiera un repositorio git. Es lo que hace legibles los dos bloques siguientes: sin repositorio, `uncommitted` es permanentemente `null` y una profundidad de `commit_queue` que nunca baja es el estado esperado, no un reconciler atascado. El servidor también avisa de esto al arrancar.

Dos bloques ahí informan sobre los [commits diferidos](#commits-diferidos-semántica-de-ack). `commit_queue` da `depth`, `last_flush_age_s` y `tick_s` — la profundidad solo es interpretable frente al tick que se supone que la está vaciando, así que un reconciler atascado se ve como una profundidad que sobrevive a varios ticks. `uncommitted` da `count` y `oldest_age_s` de las rutas del vault que siguen esperando commit; `null` ahí significa que git no pudo responder, que no es la misma respuesta que `0`. Los dos se solapan a mitad de tick, y eso es cierto — una ruta encolada está realmente sin commitear en disco.

## Commits diferidos (semántica de ACK)

**Cambio incompatible — sale en una release mayor.** Que `vault_write` o `vault_patch` devuelvan con éxito ya no significa que exista un commit.

El commit salió del camino de escritura. Una escritura aterriza en disco, su ruta se encola, y un hilo reconciler vacía la cola en **un commit por tick** (`HIVE_COMMIT_TICK_S`, 5s por defecto) en vez de un commit por escritura. La tasa de commits pasa a ser función del tick y no del volumen de escrituras, que es lo que levanta el techo de rendimiento: los commits de git contra un mismo repositorio se serializan, así que la única forma de ir más rápido es commitear menos veces.

:::caution[El tick tiene un techo de 300s]
Poner `HIVE_COMMIT_TICK_S` por encima de **300** no ralentiza el reconciler: impide que arranque. Lo que se detiene es la reconciliación *automática*: las rutas encoladas siguen llegando a git mediante un apagado limpio, un `vault_commit` explícito o un [committer externo](/es/guides/obsidian-git-integration/), pero ya nada ocurre por temporizador, y `vault_health` es lo que muestra crecer el backlog. El servidor registra `mcp.reconciler.auto_flush_disabled` al arrancar cuando ocurre. Si quieres commits poco frecuentes, usa un tick de **hasta 300 segundos inclusive** — exactamente 300 sigue arrancando.
:::

| Si quieres | Pasa |
|---|---|
| El comportamiento síncrono anterior | `commit=True` — commitea antes de devolver |
| El comportamiento por defecto | nada — se encola y se commitea en el siguiente tick |
| Hacer flush ya | [`vault_commit`](#vault_commit) |

Lo que garantiza el comportamiento por defecto es estrecho, y a propósito: **la escritura llegó a disco antes de que su ruta se encolara.** Todo lo posterior es best-effort. Un tick puede legítimamente no producir ningún commit (ver [integración con obsidian-git](/es/guides/obsidian-git-integration/)), y un proceso muerto antes de su tick deja la ruta en disco sin commitear.

`commit=False` ya no significa "que siga sin commitear hasta que yo haga flush" — ahora es un alias del comportamiento por defecto. El modo de diferido indefinido está **eliminado**, no conservado. Para lo que existía — agrupar muchas escrituras y pagar un solo commit — es justo lo que la cola hace ahora de forma automática y sin configuración.

**Nada recupera automáticamente un commit perdido.** Una ruta huérfana espera a la siguiente escritura sobre ese vault, a un `vault_commit` explícito, o a un committer externo. Mientras tanto sigue visible bajo `uncommitted` en [`vault_health(include_runtime=True)`](#vault_health) — el número y la edad de la más antigua. Ese reporte es la señal de recuperación, no un adorno, así que ojo: `count: null` significa que git no pudo responder, que no es la misma respuesta que `count: 0`.

## vault_write

Crea, agrega o reemplaza archivos del vault con validación de frontmatter. El commit se [difiere al siguiente tick](#commits-diferidos-semántica-de-ack); pasa `commit=True` para commitear de forma síncrona antes de devolver.

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

Las tres operaciones difieren su commit al siguiente tick del reconciler. El archivo está en disco cuando la llamada devuelve; `git status` lo muestra como modificado hasta que salta el tick. Pasa `commit=True` para commitear antes de devolver, haz flush ya con [`vault_commit`](#vault_commit), o deja que un committer externo (p. ej. el auto-commit de obsidian-git) lo recoja — ver [Commits diferidos](#commits-diferidos-semántica-de-ack) para el contrato completo y [Configuración → Configuración recomendada](/es/configuration/#configuración-recomendada) para la durabilidad.

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

Reemplaza exactamente una ocurrencia de `find` con `replace`. Rechaza coincidencias ambiguas — si `find` aparece más de una vez, la operación falla con un error pidiendo más contexto. Usa coincidencia en cascada de 3 pasadas: exacta → solo cuerpo → normalizada por espacios. El commit se [difiere al siguiente tick](#commits-diferidos-semántica-de-ack), mismo contrato que `vault_write`; pasa `commit=True` para commitear de forma síncrona.

## vault_commit

Hace flush del árbol de trabajo en un único commit de git, sin esperar a un tick.

```python
vault_commit(message="vault: checkpoint fin de sesión")
```

Hace stage de todo cambio sin stage y de todo fichero no rastreado del vault, y crea un único commit. Devuelve el nuevo SHA, un aviso de árbol limpio si no hay nada modificado, o un error legible. Idealmente combinado con el plugin obsidian-git (ver [Configuración → Configuración recomendada](/es/configuration/#configuración-recomendada)).

Este es el **único** camino que barre el árbol de trabajo entero, y es deliberado: hace stage de ediciones que hive nunca hizo, incluido tu propio trabajo a medias. Una persona que pide un flush ha consentido eso; un temporizador no, y por eso el reconciler solo commitea rutas que él mismo encoló. Es además el remedio para todo lo que el reporte de [`vault_health`](#vault_health) muestre como sin commitear, ya que nada de eso se resuelve solo.

Lo que **no** recoge es una ruta que ya está en stage. Varias sesiones comparten un vault, y el trabajo en stage pertenece a quien lo puso ahí, así que el commit lo deja en stage y el resultado lo nombra. Una de esas rutas puede ser de hive: una escritura matada entre `git add` y `git commit` se queda en stage, y git no la distingue de la de otra sesión. Si una ruta nombrada es tuya, commitéala a propósito con `git commit -- <ruta>`.

## vault_delete

Borra un único fichero del vault. **Destructivo — solo bajo petición explícita del usuario.** Recuperable desde el historial de git.

```python
vault_delete(
    project="mi-proyecto",
    path="30-architecture/adr-005.md",
    commit=True,            # por defecto: stage + commit del borrado
    idempotency_key=""      # token opcional de a-lo-sumo-una-vez
)
```

Elimina un fichero y commitea el borrado, de modo que sigue siendo recuperable vía `git revert` / `git show`. **Solo ficheros** — los directorios se rechazan. Una ruta inexistente es un error, salvo que se pase `idempotency_key`, en cuyo caso un reintento sobre un fichero ya eliminado es un no-op con éxito.

`vault_delete` es la única tool de escritura que **no** pasó a commits diferidos. Se queda fuera de la cola por completo y commitea **siempre** de forma síncrona, porque una granularidad de commit más gruesa es justo lo que debilitaría su garantía de recuperabilidad: un borrado y una recreación dentro del mismo tick colapsan en un único estado, y no queda nada a lo que hacer `git revert`.

:::caution[Aquí `commit=False` se rechaza]
Delete no tiene modo diferido en absoluto. Dejar un borrado sin commitear es el modo de diferimiento indefinido que ADR-018 §4 eliminó, y tampoco puede usar la cola — así que `commit=False` devuelve un error explicando por qué, en vez de aceptarse o corregirse en silencio. Omite `commit`, o pasa `commit=True`. Ver la [enmienda del 2026-08-09](https://github.com/mlorentedev/hive/blob/master/docs/adr/adr-018-asynchronous-commit-queue.md) del ADR.
:::

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

**Modo inline** (sin `text`, sin `find`): Agrega una entrada estructurada a `90-lessons.md` con fecha, contexto, problema y solución. Nunca crea el archivo: si el proyecto no tiene `90-lessons.md` no escribe nada y devuelve un error que indica dónde va la lección — el `docs/lessons/` del repositorio para un proyecto que movió allí sus lecciones, `00_meta/patterns/` para una lección entre proyectos. Deduplicación por título. Auto-commit a git. Cada inline write también siembra una fila baseline en la tabla de refuerzo a `confidence=0.7`.

**Modo por lotes** (`text` proporcionado): Envía el texto al modelo worker configurado, que extrae lecciones estructuradas (título, contexto, problema, solución, tags, confianza). Las lecciones por encima del umbral de confianza se escriben en `90-lessons.md` con deduplicación y se siembran en la tabla de refuerzo. La comprobación del archivo ausente se hace antes de llamar al worker, así que una captura rechazada no consume inferencia.

**Modo look-up** (`find` proporcionado): Grepea headings de lecciones (consciente de bloques de código) por la palabra clave, rankea las coincidencias por `rank_by` (default `reinforcements`), incrementa cada lección surfaceada una vez, y devuelve las top `max_lessons`. Simetría tool-única: `capture_lesson` ESCRIBE lecciones Y las consulta.

**Cuándo usar:** Inmediatamente después de descubrir la causa raíz de un bug, un insight arquitectónico o un truco de depuración — no esperes al final de la sesión. Usa `find=` para recuperar lecciones pasadas por tema.
