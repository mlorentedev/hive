---
title: Herramientas de Worker
description: 2 herramientas para delegar tareas a un único worker compatible con OpenAI y comprobar su estado.
---

El worker es un único endpoint compatible con OpenAI, configurado con `HIVE_WORKER_BASE_URL`, `HIVE_WORKER_API_KEY` y `HIVE_WORKER_MODEL` (consulta [Configuración](/hive/es/configuration/#configurar-un-worker)). Hive no nombra ningún proveedor: un servicio alojado y un runtime local se configuran igual. No hay cadena de fallback — una llamada es un intento contra ese único endpoint. Consulta [Enrutamiento de Workers](/hive/es/guides/worker-routing/) para ver por qué.

## delegate_task

Entrega una tarea al modelo worker, o resume archivos del vault.

```python
# Delegación de tarea
delegate_task(
    prompt="Explica esta regex: ^(?:[a-z0-9]+\.)*[a-z0-9]+$",
    context="",          # contexto de sistema opcional
    model="",            # vacío = el HIVE_WORKER_MODEL configurado
    max_tokens=2000,
    timeout_s=0.0,       # 0 = el HIVE_TOOL_TIMEOUT ambiental
    structured=False     # True = registro JSON en lugar de prosa
)

# Resumen de archivo del vault
delegate_task(
    project="mi-proyecto",
    section="context",       # o usa path="..."
    max_summary_lines=20     # longitud objetivo del resumen
)
```

### Delegación de Tareas

Cuando se proporciona `prompt`, Hive hace **un intento** contra el worker configurado:

- Un `model` vacío usa `HIVE_WORKER_MODEL`. Un id de modelo concreto se envía tal cual; Hive no lo valida contra ningún catálogo, así que el id es el que tu endpoint le asigne.
- Los alias de modelo `auto`, `ollama`, `openrouter-free` y `openrouter` se retiraron en 4.0.0. Pasar uno se rechaza con un mensaje que nombra el reemplazo (un id de modelo concreto, u omitir `model`) en lugar de ignorarlo en silencio.
- `timeout_s` fija un deadline por llamada. Un valor por encima de `HIVE_TOOL_TIMEOUT` eleva el techo en lugar de quedar limitado por él.
- Si el worker no está configurado, no es alcanzable o devuelve una respuesta inutilizable, la llamada devuelve un error y, en modo prosa, termina con *"The host should handle this task directly."* — el modelo host se encarga de la tarea.

### Resumen del Vault

Cuando se proporciona `project`, lee un archivo del vault:
- **Archivos pequeños** (<=50 líneas): se devuelven directamente con metadatos
- **Archivos grandes** (>50 líneas): se envían al worker para resumirlos. Si el worker no está disponible, devuelve el contenido en bruto con una nota indicando que el resumen falló.

### Modo Estructurado

`structured=True` devuelve un registro JSON de una línea en lugar de prosa, para llamadores que necesitan ramificar según el resultado en vez de analizar texto:

```json
{"status": "ok", "model": "<model-id>", "output": "...", "tokens": 412, "duration_ms": 2100, "detail": ""}
```

Todos los campos están siempre presentes, también en caso de fallo. `status` es uno de:

| `status` | Significado | ¿Reintentar en otro pool? |
|---|---|---|
| `ok` | El worker respondió | — |
| `pool_unavailable` | El endpoint no atendió la petición: inalcanzable, la petición HTTP agotó su tiempo, límite de tasa (429), credencial rechazada (401/403), o worker no configurado | Sí, si el llamador tiene otro pool |
| `task_failed` | El endpoint respondió y la respuesta es inutilizable (un 5xx, o un 4xx sobre la propia petición, como un id de modelo desconocido), o la petición era incorrecta (por ejemplo, un alias retirado) | No |
| `timeout` | Venció el deadline por llamada (`timeout_s` / `HIVE_TOOL_TIMEOUT`) | Lo decide el llamador |

`structured=True` combinado con `project=` se rechaza como `task_failed`: el resumen devuelve un documento, no un registro.

### Formato de Respuesta

Las respuestas en prosa empiezan con una cabecera que indica el modelo, el número de tokens, el coste y la latencia:

```
## Worker Response (model: <model-id>, 412 tokens, $0.00, 2.1s)

<la respuesta del worker>
```

El coste aparece como `$0.00` salvo que el endpoint informe un coste en sus datos de uso. Hive en sí no registra ni limita el gasto (consulta [Enrutamiento de Workers](/hive/es/guides/worker-routing/#sin-tope-de-gasto)).

## worker_status

Muestra la configuración, la alcanzabilidad y el uso del worker.

```python
# Estado completo, sondeando el endpoint para obtener su lista de modelos
worker_status()

# Solo configuración y uso, sin llamada de red
worker_status(include_models=False)
```

Devuelve:

- `## Provider` — si hay un worker configurado (`Configured: no — set HIVE_WORKER_BASE_URL` cuando no lo hay), el modelo y la alcanzabilidad
- `## Usage this month` — número de peticiones y total de tokens
- `## By model` — peticiones, tokens y latencia media por modelo
- `## Available Models` — los modelos que lista el endpoint (cuando `include_models=True`)

La alcanzabilidad se **sondea**, no se infiere de la configuración: `Reachable: yes (N models)`, `Reachable: NO — <motivo>`, o `Reachable: unprobed` cuando `include_models=False`. "Configured" y "responde" se informan por separado a propósito — un worker puede estar configurado y aun así caído.
