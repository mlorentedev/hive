---
title: Solución de Problemas
description: Problemas comunes y soluciones para el servidor MCP Hive.
---

## Vault No Encontrado

**Síntoma:** Las herramientas de vault (vault_list, vault_query, etc.) devuelven "Vault not found at /home/user/Projects/knowledge" con instrucciones de configuración.

**Causa:** La ruta del vault no se configuró al registrar el servidor MCP. Hive usa `~/Projects/knowledge` por defecto, que puede no existir en tu máquina.

**Solución:** Re-registra el servidor MCP con `VAULT_PATH` apuntando a tu vault de Obsidian:

```bash
# Claude Code
claude mcp add -s user hive -e VAULT_PATH=$HOME/mi-vault -- uvx --upgrade hive-vault

# Gemini CLI
gemini mcp add -s user -e VAULT_PATH=$HOME/mi-vault hive-vault uvx -- --upgrade hive-vault
```

Se aceptan tanto `VAULT_PATH` como `HIVE_VAULT_PATH`. Si ambas están definidas, `HIVE_VAULT_PATH` tiene prioridad.

**Nota:** El servidor arranca incluso sin una ruta de vault válida — las herramientas de worker (`worker_status`, `delegate_task`) siguen funcionando. Solo las herramientas específicas de vault requieren una ruta válida.

## Hive No Disponible en Otros Proyectos

**Síntoma:** Instalaste Hive en un proyecto pero no aparece al iniciar sesión en otro proyecto.

**Causa:** Registraste Hive a nivel de **proyecto** (por defecto) en lugar de **usuario**. Los servidores MCP con scope de proyecto solo funcionan en el directorio del proyecto donde fueron registrados.

**Solución:** Re-registra a scope de usuario — esta es la configuración recomendada ya que el vault de Hive se comparte entre todos los proyectos:

```bash
# Claude Code — nota el flag -s user
claude mcp add -s user hive -- uvx --upgrade hive-vault

# Gemini CLI — scope de usuario con -s user
gemini mcp add -s user hive-vault uvx -- --upgrade hive-vault
```

Después de re-registrar, reinicia la sesión de tu asistente de IA. Hive aparecerá ahora en todos los proyectos.

**Por qué importa el scope de usuario:** Hive se conecta a un único vault de conocimiento que almacena contexto para *todos* tus proyectos. El registro a nivel de proyecto anula esto — tendrías que registrar Hive por separado en cada proyecto, y todos apuntarían al mismo vault de todas formas.

## Ollama Muestra "offline" en worker_status

**Síntoma:** `worker_status` reporta Ollama como offline, pero `curl http://tu-ollama:11434/api/tags` funciona.

**Causa:** La variable de entorno `HIVE_OLLAMA_ENDPOINT` no está configurada en tu registro del servidor MCP.

**Solución:** Re-registra el servidor MCP con el endpoint explícitamente configurado:

```bash
# Claude Code
claude mcp add -s user hive \
  -e HIVE_OLLAMA_ENDPOINT=http://tu-ollama:11434 \
  -- uvx --upgrade hive-vault

# Gemini CLI
gemini mcp add -s user \
  -e HIVE_OLLAMA_ENDPOINT=http://tu-ollama:11434 \
  hive-vault uvx -- --upgrade hive-vault
```

Los servidores MCP **no** heredan las variables de entorno de tu shell. Cada variable de entorno debe pasarse explícitamente en el momento del registro.

## OpenRouter Devuelve 429 (Rate Limit)

**Síntoma:** `delegate_task` falla con error de rate limit en el tier gratuito.

**Causa:** Los modelos de tier gratuito de OpenRouter tienen rate limits por minuto. Esto es normal bajo uso intensivo.

**Solución:** Espera 60 segundos y reintenta. Para cargas de trabajo sostenidas, configura `max_cost_per_request=0.01` para usar el tier de pago (limitado por `HIVE_OPENROUTER_BUDGET`).

## Los Cambios en la Config MCP No Surten Efecto

**Síntoma:** Actualizaste una variable de entorno (ej. `VAULT_PATH`) pero Hive sigue usando el valor anterior.

**Causa:** Los servidores MCP se cargan al inicio de sesión. Los cambios de configuración requieren una nueva sesión.

**Solución:** Sal y reinicia la sesión de tu asistente de IA (ej. reinicia Claude Code, inicia una nueva sesión de Gemini CLI).

## vault_list Devuelve Vacío

**Síntoma:** `vault_list` no muestra proyectos.

**Causa:** `VAULT_PATH` no apunta al directorio correcto, o el layout de tu vault no coincide con los scopes configurados.

**Solución:**
1. Verifica tu ruta del vault: `ls $VAULT_PATH`
2. Comprueba que los directorios de proyecto existan bajo el directorio de scope esperado (por defecto: `10_projects/`)
3. Si tu vault usa un layout diferente, configura `HIVE_VAULT_SCOPES`:

```bash
HIVE_VAULT_SCOPES='{"projects": "Projects", "meta": "Templates"}'
```

Consulta [Estructura del Vault](/hive/es/guides/vault-structure/) para detalles del layout.

## Errores "Project not found"

**Síntoma:** `vault_query(project="mi-app")` devuelve "Project not found" pero el directorio existe.

**Posibles causas:**
- El directorio del proyecto no está dentro de un directorio de scope configurado
- Error tipográfico en el nombre del proyecto (debe coincidir exactamente con el nombre del directorio)
- El directorio de scope en sí no existe

**Solución:** Ejecuta `vault_list` para ver qué puede encontrar Hive. Si tu proyecto no aparece, revisa tu configuración de `HIVE_VAULT_SCOPES`.

## Gemini CLI: Sintaxis de Registro MCP

**Síntoma:** `gemini mcp add` falla con errores de parsing de argumentos.

**Causa:** Gemini CLI tiene requisitos específicos de orden de argumentos. El separador `--` es necesario para evitar que Gemini consuma los argumentos del servidor.

**Sintaxis correcta:**

```bash
# Registro basico
gemini mcp add -s user hive-vault uvx -- --upgrade hive-vault

# Con variables de entorno
gemini mcp add -s user \
  -e VAULT_PATH=$HOME/mi-vault \
  hive-vault uvx -- --upgrade hive-vault
```

**Detalles clave:**
- El nombre del servidor va antes del comando (`hive-vault uvx`)
- `--` separa los flags de Gemini de los argumentos del servidor
- `-s user` instala a scope de usuario (persiste entre proyectos)
- Los valores de variables de entorno se expanden inmediatamente (no se almacenan como referencias)

## vault_write Rechaza Mi Contenido

**Síntoma:** `vault_write` con `operation="replace"` devuelve un error de validación.

**Causa:** Al reemplazar un archivo completo, Hive valida que el frontmatter YAML incluya campos requeridos: `id`, `type` y `status`.

**Solución:** Incluye frontmatter válido en tu contenido:

```markdown
---
id: mi-doc
type: context
status: active
---

Tu contenido aqui.
```

O usa `operation="append"` para agregar contenido sin reemplazar el frontmatter.

## Archivos de Base de Datos Creciendo

**Síntoma:** Los archivos SQLite en `~/.local/share/hive/` están creciendo.

**Tamaños esperados:**
- `worker.db` — Tracking de presupuesto/uso. Crece ~1KB por llamada a delegate_task. Típico: 10-50KB.
- `relevance.db` — Puntuación adaptativa de contexto. Crece ~0.5KB por llamada a session_briefing. Típico: 5-20KB.

Ambos usan modo WAL para rendimiento. Si los tamaños parecen excesivos, puedes borrarlos sin problema — Hive los recrea automáticamente. El tracking de presupuesto se reinicia al borrarlos.

## Consultar el Log de Depuración

Hive escribe warnings y errores en un archivo de log persistente para depuración post-mortem:

```
~/.local/share/hive/hive.log
```

Consulta este archivo cuando las herramientas devuelvan resultados inesperados o el servidor falle silenciosamente. El log rota a 1MB con un archivo de backup (`hive.log.1`).

Para cambiar la ubicación del log:

```bash
claude mcp add -s user hive \
  -e HIVE_LOG_PATH=/ruta/a/custom.log \
  -- uvx --upgrade hive-vault
```

## Llamadas a Herramientas Colgadas o con Timeout

**Sintoma:** Las llamadas MCP a herramientas se congelan sin respuesta, o devuelven "Tool timed out after 60s".

**Causa:** Las herramientas de worker (`capture_lesson`, `delegate_task`) llaman a APIs externas (Ollama/OpenRouter) que pueden ser lentas o no responder. Las herramientas de escritura (`vault_write`, `vault_patch`) pueden bloquearse si otra operacion mantiene el lock de escritura.

**Solucion:**
1. Verifica conectividad de workers: `worker_status` — si Ollama esta offline, arregla la conexion primero
2. Si los timeouts son muy agresivos, aumentalos:
   - `HIVE_TOOL_TIMEOUT=120` — timeout por herramienta para tools async de worker (por defecto: 60s)
   - `HIVE_HTTP_TIMEOUT=120` — timeout HTTP para llamadas a Ollama/OpenRouter (por defecto: 60s)
3. Revisa `~/.local/share/hive/hive.log` para warnings de timeout con nombres de herramientas y tiempos transcurridos
4. Si las herramientas de escritura devuelven "Server busy", reintenta en breve — una operacion de escritura previa esta terminando

**Nota:** Los timeouts son una red de seguridad. Una herramienta con timeout devuelve un mensaje de error claro en lugar de colgar tu sesion indefinidamente. La operacion subyacente (llamada HTTP, git commit) se limpia automaticamente.

## Desconexión del Transporte MCP Tras Rechazar la Primera Llamada

**Síntoma:** En Claude Code (y probablemente otros hosts MCP), rechazar el primer prompt de permisos de `mcp__hive__*` envenena el transporte durante el resto de la conversación. Las llamadas siguientes a cualquier herramienta de Hive devuelven `MCP error -32000: Connection closed` y después `No such tool available`. Reiniciar la conversación lo soluciona, y `claude mcp list` sigue reportando el servidor como conectado a nivel de proceso.

**Causa:** Una condición de carrera en el SDK Python `mcp` upstream alrededor de `mcp.shared.session.RequestResponder`. Cuando el cliente envía `notifications/cancelled` para una petición en vuelo, pueden dispararse dos modos de fallo:

1. El `CancelScope` de anyio del responder vuelve a lanzar un `CancelledError` después de que la respuesta de cancelación ya se haya enviado. Esa excepción espuria se propaga al `task_group` del receive loop del servidor y lo mata — el proceso sigue vivo pero deja de leer stdin.
2. El handler termina *después* de que el cliente ya haya enviado la cancelación, y la llamada tardía a `RequestResponder.respond()` falla con `AssertionError("Request already responded to")`. La excepción escapa del receive loop y mata el mismo `task_group`.

**Solución:** Hive aplica dos monkey-patches quirúrgicos al arrancar en [`src/hive/_compat.py`](https://github.com/mlorentedev/hive/blob/master/src/hive/_compat.py):

- `RequestResponder.__exit__` — ignora la `CancelledError` espuria una vez el responder está marcado como completado.
- `RequestResponder.respond` — corta en seco la llamada tardía con un log WARNING (`mcp.ghost_response.suppressed_after_cancel_ack`) e incrementa un contador expuesto en `vault_health`.

Ambos patches están auto-acotados al modo de fallo exacto (`_completed=True`), por lo que quedan inertes cuando upstream corrija el bug.

Seguimiento en [issue #75](https://github.com/mlorentedev/hive/issues/75) y [upstream python-sdk#2610](https://github.com/modelcontextprotocol/python-sdk/issues/2610). Tests de regresión: [`tests/test_transport_recovery.py`](https://github.com/mlorentedev/hive/blob/master/tests/test_transport_recovery.py) + [`tests/test_compat_shim.py`](https://github.com/mlorentedev/hive/blob/master/tests/test_compat_shim.py).

**Si la desconexión persiste:**

1. Confirma que estás en `hive-vault >= 1.14.0` — versiones anteriores no incluían el segundo patch (respond-after-cancel).
2. Revisa `~/.local/share/hive/hive.log` buscando líneas WARNING `mcp.ghost_response.suppressed_after_cancel_ack` (siempre logueadas) o líneas debug `Swallowed spurious cancellation on completed responder` (activa con `HIVE_LOG_LEVEL=DEBUG`).
3. Como solución temporal, acepta siempre la primera llamada de Hive en una conversación nueva. Los rechazos posteriores no rompen el transporte.

## Cancelé una Llamada Pero el Vault Cambió de Todas Formas

**Síntoma:** Tú (o tu cliente) cancelaste a mitad de ejecución una llamada `vault_write` / `vault_patch` / `capture_lesson` — recibiste un `ErrorData` diciendo *"Request cancelled"* — pero en el siguiente `vault_query` el archivo muestra el contenido nuevo como si la operación hubiera tenido éxito.

**Causa:** No es un bug, es un desajuste semántico documentado ([ADR-007](https://github.com/mlorentedev/hive/blob/master/docs/architecture/adr-007-mcp-cancellation-response.md), enmendado dos veces). Cuando llega la cancelación, el `RequestResponder.cancel()` upstream escribe el frame `ErrorData` al wire de inmediato — empíricamente 20/20 veces en Linux, ver [`tests/test_compat_shim.py::test_classify_cancellation_race`](https://github.com/mlorentedev/hive/blob/master/tests/test_compat_shim.py). Pero el hilo del handler sigue ejecutándose hasta acabar; Hive no puede interrumpir con seguridad una escritura parcial (Python `asyncio.timeout` cancela la corutina que espera pero no puede interrumpir el hilo bloqueante). Por eso el disco se muta **después** de que el ack de cancelación llegue al cliente.

**El ack ErrorData NO implica rollback.** La regla de corrección del cliente es: *verifica el estado vía `vault_query` en lugar de reintentar.* Reintentar un `vault_write(operation="append", ...)` después de un ghost-response duplica contenido; reintentar `vault_patch` puede producir errores de coincidencia ambigua contra el resultado ya aplicado.

**Cómo detectarlo en tus sesiones:** llama a `vault_health` — cuando han ocurrido ghost responses, la salida incluye un bloque tipo:

```
## ghost_responses
- total: 3
- last_seen: 2026-05-20T22:14:07+00:00
- last_tool: vault_patch
- note: ErrorData ack does NOT imply rollback — verify state via `vault_query`, do not retry.
```

El contador se reinicia cuando el servidor reinicia. Cada evento también se loguea a nivel WARNING con el prefijo `mcp.ghost_response.suppressed_after_cancel_ack` más el nombre de la herramienta y el id de la petición.

**Mitigaciones:**

- Aumenta `HIVE_TOOL_TIMEOUT` (default 60s) para que las llamadas lentas de worker terminen antes de que el cliente cancele.
- Para escrituras en lote, prefiere `vault_write(commit=False)` + `vault_commit` — el coste por escritura baja de ~150ms a ~5–15ms, reduciendo la ventana de cancelación.
- Tras cualquier cancelación: `vault_query(project=..., path=...)` el archivo afectado para inspeccionar el estado real en disco antes de emitir otra escritura.

Disponible desde `hive-vault >= 1.14.0`. El clasificador empírico de comportamiento del wire corrió 20 iteraciones en Linux y confirmó el escenario (a) — gana el ErrorData — en 20/20 casos; ver ADR-007 Amendment #2 para la retractación completa del plan "raw send" anterior.

## Contención multi-sesión (3-5 sesiones de Claude Code en paralelo)

**Baseline:** Hive se usa habitualmente con 3-5 sesiones de Claude Code abiertas en paralelo contra el mismo vault. Este es el uso diario base, no un caso extremo. El modelo MCP stdio lanza un subproceso `hive-vault` por sesión, así que 4 ventanas = 4 procesos hermanos compartiendo las mismas DBs SQLite (`~/.local/share/hive/*.db`) y el mismo repo git del vault.

Si esos procesos se acumulan, o si además usas [obsidian-git](https://github.com/Vinzent03/obsidian-git) para auto-backups, pueden aparecer tres síntomas:

- **Bloat del fichero WAL.** `~/.local/share/hive/relevance.db-wal` crece 10-100× el tamaño de la DB en reposo porque los lectores concurrentes impiden que SQLite haga checkpoint del WAL.
- **Congelaciones silenciosas durante escrituras.** Un `vault_write` o `capture_lesson` tarda 10-30 segundos cuando obsidian-git está auto-comprometiendo en segundo plano (su intervalo de 10 minutos retiene `.git/index.lock` durante pull + commit + push).
- **Procesos hive zombi.** Una ventana de Claude Code crasheó o se forzó a cerrar, pero su hijo `uvx hive-vault` siguió vivo manteniendo file handles abiertos.

### Inspeccionar el estado actual

Ejecuta `vault_health(include_runtime=True)` desde cualquier sesión. El bloque `## runtime` reporta:

```yaml
- wal_size_bytes: 4137984          # >5 MB sostenido = contención
- competing_pid_count: 3            # otros PIDs hive-vault (mismo usuario)
- last_git_lock_wait_ms:
  - mean: 12.5
  - p99: 8234.0                     # p99 > 5000ms = contención
  - samples: 47
- obsidian_git_present: true        # committer externo detectado
```

Sano: `wal_size_bytes` bajo unos pocos MB, `last_git_lock_wait_ms.p99` por debajo de 100ms.

### Detectar un proceso hive zombi

**POSIX (Linux, macOS)** — listar todos los procesos hive y su antigüedad:

```bash
ps -eo pid,etime,cmd | grep hive-vault | grep -v grep
```

Inspeccionar qué file handles mantiene un PID específico:

```bash
lsof -p <PID> | grep hive
```

Matar un zombi:

```bash
kill <PID>          # grácil
kill -9 <PID>       # forzado
```

**Windows (PowerShell)** — listar procesos hive:

```powershell
Get-Process | Where-Object { $_.ProcessName -match "hive-vault|python" } |
  Select-Object Id, StartTime, ProcessName, Path
```

Matar por PID:

```powershell
Stop-Process -Id <PID>           # grácil
Stop-Process -Id <PID> -Force    # forzado
```

### Ajustar `HIVE_LOCK_TIMEOUT_S`

Valor por defecto `30` segundos. Es el timeout de adquisición del lock usado cuando hive compite por el filelock de git. Limitado a 600 para evitar pies de plomo.

| Escenario | Recomendado | Por qué |
|---|---|---|
| Por defecto | `30` | Coincide con el timeout de subprocess; absorbe ticks típicos de obsidian-git |
| Vault grande + disco lento | `60-90` | El pull + commit + push de obsidian-git puede retener el lock 15-30s en un vault de 50 MB |
| Red lenta + autoPull=true | `90-120` | El pull de red domina; subir para evitar abandonos |
| Vault rápido, preferencia fail-fast | `10` | Si prefieres ver errores antes que esperar |

Configurar vía variable de entorno `HIVE_LOCK_TIMEOUT_S=60`.

### Ajustar `HIVE_WAL_CHECKPOINT_INTERVAL_S`

Por defecto `30.0` segundos. Frecuencia con la que cada proceso hive ejecuta `PRAGMA wal_checkpoint(PASSIVE)` para drenar su WAL de SQLite. Menor = drenaje más agresivo; mayor = menos CPU en ticks ociosos.

El default es apropiado para la mayoría. Sube a `120` si observas CPU excesiva en procesos hive ociosos (p. ej. en máquinas con recursos limitados); el trade-off es drenaje del WAL más lento.

### Patrón de cooperación con obsidian-git

Si tu vault usa obsidian-git para auto-backups, las dos herramientas cooperan limpiamente cuando se configuran correctamente:

- Configura el `autoSaveInterval` de obsidian-git a 5-10 minutos (el default está bien).
- Para flujos con muchas escrituras, prefiere `vault_write(commit=False)` / `vault_patch(commit=False)`. Hive escribe el fichero; obsidian-git lo commitea en su siguiente tick. El coste por escritura baja de ~150ms a ~5-15ms.
- Usa `vault_commit` solo cuando necesites un flush explícito antes del siguiente tick de obsidian-git (p. ej. antes de cerrar Obsidian).
- Vigila `vault_health.runtime.last_git_lock_wait_ms.p99`. Si se mantiene por encima de 5000ms, sube `HIVE_LOCK_TIMEOUT_S` para absorber las ventanas más largas.

Una release futura hará la cooperación automática (auto-defer cuando el committer externo esté sano); por ahora es opt-in vía `commit=False`.

## Obtener ayuda

Si tu problema no está listado aquí:

1. Ejecuta `vault_health` para comprobar conectividad del vault y recuentos de archivos — el bloque `## server` al principio reporta la versión en ejecución, python, ruta del vault y presencia de backends, así puedes incluirlo literal en un bug report (no se expone ninguna API key). Añade `include_runtime=True` para uptime, nombres de tools registrados, métricas de contención multi-sesión y snapshot del presupuesto OpenRouter.
2. Ejecuta `worker_status` para comprobar conectividad de proveedores y presupuesto
3. Revisa `~/.local/share/hive/hive.log` para detalles de errores
4. Consulta la página de [Configuración](/hive/es/configuration/) para todas las variables de entorno
5. Abre un issue en [github.com/mlorentedev/hive](https://github.com/mlorentedev/hive/issues)
