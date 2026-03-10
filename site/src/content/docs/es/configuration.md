---
title: Configuración
description: Variables de entorno del servidor MCP Hive.
---

Toda la configuración se realiza mediante variables de entorno, pasadas al registrar el servidor MCP.

## Variables de Entorno

| Variable | Valor por defecto | Descripción |
|---|---|---|
| `VAULT_PATH` | `~/Projects/knowledge` | Ruta a tu vault de Obsidian |
| `HIVE_OLLAMA_ENDPOINT` | `http://localhost:11434` | Endpoint de la API de Ollama |
| `HIVE_OLLAMA_MODEL` | `qwen2.5-coder:7b` | Modelo de Ollama por defecto |
| `HIVE_OPENROUTER_API_KEY` | — | API key de OpenRouter |
| `HIVE_OPENROUTER_MODEL` | `qwen/qwen3-coder:free` | Modelo de OpenRouter por defecto (tier gratuito) |
| `HIVE_OPENROUTER_PAID_MODEL` | `qwen/qwen3-coder` | Modelo de pago para delegate_task |
| `HIVE_OPENROUTER_BUDGET` | `1.0` | Presupuesto mensual máximo en USD |
| `HIVE_DB_PATH` | `~/.local/share/hive/worker.db` | Base de datos SQLite para tracking de presupuesto/uso |
| `HIVE_RELEVANCE_DB_PATH` | `~/.local/share/hive/relevance.db` | Base de datos SQLite para puntuación adaptativa de contexto |
| `HIVE_STALE_THRESHOLD_DAYS` | `180` | Días antes de que un archivo se marque como obsoleto |
| `HIVE_HTTP_TIMEOUT` | `120.0` | Timeout HTTP (segundos) para Ollama y OpenRouter |
| `HIVE_RELEVANCE_ALPHA` | `0.3` | Tasa de aprendizaje EMA para puntuación adaptativa |
| `HIVE_RELEVANCE_DECAY` | `0.9` | Factor de decaimiento por sesión para puntuaciones de relevancia |
| `HIVE_RELEVANCE_EPSILON` | `0.15` | Ratio de exploración para session_briefing (epsilon-greedy) |
| `HIVE_VAULT_SCOPES` | `{"projects": "10_projects", "meta": "00_meta"}` | Mapeo JSON de nombres de scope a subdirectorios del vault |
| `HIVE_LOG_PATH` | `~/.local/share/hive/hive.log` | Ruta del log persistente de depuración (rotación 1MB) |

## Resolución de API Key

La API key de OpenRouter soporta dos nombres por conveniencia:

- `HIVE_OPENROUTER_API_KEY` (con prefijo, estándar)
- `OPENROUTER_API_KEY` (sin prefijo, para entornos que ya la exportan)

Si ambas están definidas, la versión con prefijo `HIVE_` tiene prioridad.

## Ejemplo: Configuración Completa

**Claude Code:**
```bash
claude mcp add -s user hive \
  -e VAULT_PATH=$HOME/mi-vault \
  -e HIVE_OLLAMA_ENDPOINT=http://minipc.local:11434 \
  -e OPENROUTER_API_KEY=sk-or-v1-abc123 \
  -e HIVE_OPENROUTER_BUDGET=10.0 \
  -- uvx --upgrade hive-vault
```

**Gemini CLI:**
```bash
gemini mcp add -s user \
  -e VAULT_PATH=$HOME/mi-vault \
  -e HIVE_OLLAMA_ENDPOINT=http://minipc.local:11434 \
  -e OPENROUTER_API_KEY=sk-or-v1-abc123 \
  -e HIVE_OPENROUTER_BUDGET=10.0 \
  hive-vault uvx -- --upgrade hive-vault
```

**Codex CLI** — agrega a `~/.codex/config.toml`:
```toml
[mcp_servers.hive-vault]
command = "uvx"
args = ["--upgrade", "hive-vault"]

[mcp_servers.hive-vault.env]
VAULT_PATH = "~/mi-vault"
HIVE_OLLAMA_ENDPOINT = "http://minipc.local:11434"
OPENROUTER_API_KEY = "sk-or-v1-abc123"
HIVE_OPENROUTER_BUDGET = "10.0"
```

El flag `--upgrade` asegura que siempre obtengas la última versión de PyPI al iniciar cada sesión.

## Ejemplo: Solo Vault (Sin Worker)

Si solo necesitas acceso al vault sin delegación de tareas:

```bash
# Claude Code
claude mcp add -s user hive -e VAULT_PATH=$HOME/mi-vault -- uvx --upgrade hive-vault

# Gemini CLI
gemini mcp add -s user -e VAULT_PATH=$HOME/mi-vault hive-vault uvx -- --upgrade hive-vault
```

Para otros clientes MCP, pasa las mismas variables de entorno a través de la configuración de servidor MCP de tu cliente.

Las herramientas de worker seguirán disponibles pero devolverán "no hay proveedores disponibles" cuando se invoquen.

## Configurar Ollama

[Ollama](https://ollama.com/download) te permite ejecutar LLMs localmente de forma gratuita. Después de instalar:

```bash
# Descargar el modelo por defecto
ollama pull qwen2.5-coder:7b

# Verificar que está ejecutándose
curl http://localhost:11434/api/tags
```

Si Ollama se ejecuta en otra máquina (ej. un homelab), configura `HIVE_OLLAMA_ENDPOINT` con su dirección.

## Configurar OpenRouter

[OpenRouter](https://openrouter.ai/) proporciona acceso a muchos modelos a través de una única API. Hay modelos de tier gratuito disponibles.

1. Crea una cuenta en [openrouter.ai](https://openrouter.ai/)
2. Genera una API key en [openrouter.ai/keys](https://openrouter.ai/keys)
3. Pásala como `OPENROUTER_API_KEY` al registrar el servidor MCP

El modelo por defecto (`qwen/qwen3-coder:free`) es gratuito. Los modelos de pago solo se usan cuando estableces explícitamente `max_cost_per_request > 0` en las llamadas a `delegate_task`, y están limitados por `HIVE_OPENROUTER_BUDGET`.

## Activar Hive en tu Flujo de Trabajo

Instalar y registrar Hive hace que las herramientas estén *disponibles*, pero tu asistente de IA necesita guía sobre **cuándo** usarlas. Tu archivo de instrucciones de proyecto (`CLAUDE.md`, `GEMINI.md`, o equivalente en tu cliente MCP) es la clave.

Sin instrucciones explícitas, tu asistente *podría* usar las herramientas de Hive, pero de forma inconsistente. Con instrucciones claras, las usa **de forma predecible** para cada consulta relevante.

### Fragmento de Instrucciones Recomendado

Agrega esto a tu archivo de instrucciones de proyecto (`CLAUDE.md`, `GEMINI.md`, o equivalente):

```markdown
## Vault y Conocimiento (Hive MCP)

Cuando hive-vault MCP esté disponible, úsalo para contexto on-demand:
- `vault_query(project="miproyecto", section="context")` — resumen del proyecto
- `vault_query(project="miproyecto", section="tasks")` — backlog activo
- `vault_search(query="...")` — búsqueda cross-vault
- `session_briefing(project="miproyecto")` — contexto completo en una llamada

Al escribir en el vault: lecciones -> `90-lessons.md`, decisiones -> `30-architecture/`.
```

Reemplaza `miproyecto` con el slug real de tu proyecto (el nombre del directorio bajo `10_projects/`).

### Cómo Funciona la Selección de Herramientas MCP

1. **Registro** — Los servidores MCP se cargan al inicio de sesión. Tu cliente ve todas las herramientas de todos los servidores.
2. **Descubrimiento** — El asistente lee los nombres y descripciones de las herramientas para entender las capacidades.
3. **Enrutamiento** — Tus instrucciones en `CLAUDE.md` le dicen al asistente qué herramientas preferir en cada situación. Múltiples servidores MCP coexisten sin conflicto — cada uno sirve su dominio.
4. **Adaptación** — El `session_briefing` de Hive aprende de tus patrones de uso. Las secciones que consultas frecuentemente se priorizan automáticamente en futuros briefings.

## Automatizar Session Briefing (Claude Code Hooks)

Claude Code soporta [hooks](https://docs.anthropic.com/en/docs/claude-code/hooks) — comandos shell que se ejecutan en respuesta a eventos del ciclo de vida. Puedes usar un hook `SessionStart` para recordar automáticamente a tu asistente que cargue contexto de Hive al inicio de cada sesión.

### Opción 1: Inyección de Contexto (Recomendado)

Agrega un hook `SessionStart` que emite un recordatorio del sistema. El texto se inyecta en la conversación automáticamente:

En `~/.claude/settings.json` (o `.claude/settings.json` por proyecto):

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "echo 'Llama a session_briefing(project=\"miproyecto\") para cargar contexto antes de empezar.'"
          }
        ]
      }
    ]
  }
}
```

Reemplaza `miproyecto` con el slug de tu proyecto en el vault. El asistente verá el recordatorio y llamará a `session_briefing` al inicio de cada sesión.

### Opción 2: Hook de Agente (Autónomo)

Para carga de contexto completamente automática sin invocación manual, usa un hook de agente. Esto lanza un subagente ligero que llama a la herramienta directamente:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "agent",
            "prompt": "Llama a session_briefing(project=\"miproyecto\") y presenta los resultados al usuario.",
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

Esto es más autónomo pero lanza un subagente (consumiendo tokens extra). La Opción 1 suele ser suficiente ya que el asistente sigue el recordatorio inyectado de forma fiable.

### Notas

- Los hooks son una funcionalidad de Claude Code, no de Hive. Otros clientes MCP pueden tener sus propios mecanismos de automatización.
- También puedes usar hooks `UserPromptSubmit` para inyección de contexto por mensaje, pero `SessionStart` es el ajuste natural para briefings.
- Consulta `claude /hooks` dentro de Claude Code para gestionar hooks interactivamente.
