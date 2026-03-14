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

## Obtener Ayuda

Si tu problema no está listado aquí:

1. Ejecuta `vault_health` para comprobar conectividad del vault y recuentos de archivos
2. Ejecuta `worker_status` para comprobar conectividad de proveedores y presupuesto
3. Revisa `~/.local/share/hive/hive.log` para detalles de errores
4. Consulta la página de [Configuración](/hive/es/configuration/) para todas las variables de entorno
5. Abre un issue en [github.com/mlorentedev/hive](https://github.com/mlorentedev/hive/issues)
