---
title: Herramientas de Worker
description: 2 herramientas para delegar tareas a modelos más baratos con enrutamiento automático.
---

## delegate_task

Enruta una tarea a un modelo más barato, o resume archivos del vault automáticamente.

```python
# Delegación de tarea
delegate_task(
    prompt="Explica esta regex: ^(?:[a-z0-9]+\.)*[a-z0-9]+$",
    context="",              # contexto opcional a incluir
    max_cost_per_request=0,  # 0 = solo modelos gratuitos
    model=""                 # override explícito de modelo
)

# Resumen de archivo del vault
delegate_task(
    project="mi-proyecto",
    section="context",       # o usa path="..."
    max_summary_lines=20     # longitud objetivo del resumen
)
```

### Delegación de Tareas

Cuando se proporciona `prompt`, las tareas se enrutan por niveles en orden:

1. **Ollama** (local) — Gratuito. Mejor para tareas triviales. Pasa al siguiente si no está disponible.
2. **OpenRouter gratuito** — Modelos de tier gratuito (ej. Qwen3 Coder 480B). Trabajo real de código.
3. **OpenRouter de pago** — Solo cuando `max_cost_per_request > 0` y el presupuesto mensual lo permite. Modelo configurable vía `HIVE_OPENROUTER_PAID_MODEL`.
4. **Rechazar** — Devuelve error para que el host gestione la tarea directamente.

### Resumen del Vault

Cuando se proporciona `project`, lee un archivo del vault:
- **Archivos pequeños** (<=50 líneas): se devuelven directamente con metadatos
- **Archivos grandes** (>50 líneas): auto-delegados a un worker para resumen. Si los workers no están disponibles, devuelve el contenido en bruto.

### Selección Explícita de Modelo

Salta el enrutamiento y apunta a un proveedor específico:

```python
# Forzar Ollama local
delegate_task(prompt="...", model="ollama:qwen2.5-coder:7b")

# Forzar OpenRouter gratuito
delegate_task(prompt="...", model="openrouter:qwen/qwen3-coder:free")

# Forzar OpenRouter de pago
delegate_task(prompt="...", model="openrouter:qwen/qwen3-coder", max_cost_per_request=0.01)
```

### Formato de Respuesta

Cada respuesta incluye un pie de metadatos:

```
[model: qwen2.5-coder:7b | provider: ollama | cost: $0.00 | latency: 2.1s]
```

## worker_status

Muestra salud de workers, presupuesto y modelos disponibles.

```python
# Estado completo con listado de modelos
worker_status()

# Estado sin listado de modelos
worker_status(include_models=False)
```

Devuelve:
- Presupuesto mensual restante (gastado / tope)
- Estado de conectividad de Ollama
- Estado de conectividad de OpenRouter
- Recuento de peticiones y desglose de costes
- Modelos disponibles en todos los proveedores (cuando `include_models=True`)
