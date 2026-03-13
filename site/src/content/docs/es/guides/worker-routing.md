---
title: Enrutamiento de Workers
description: Cómo Hive enruta tareas al modelo más barato capaz.
---

El sistema de workers de Hive delega tareas a modelos más baratos, reservando tu modelo primario para razonamiento de alto valor.

## Flujo de Enrutamiento

```
delegate_task(prompt, context, max_cost_per_request)
    │
    ├─ Modelo explicito? ─── Si ──→ Enrutar directamente a ese proveedor
    │
    └─ Auto-enrutar:
        │
        ├─ 1. Ollama disponible? ─── Si ──→ Inferencia local (gratuito)
        │
        ├─ 2. OpenRouter configurado? ── Si ──→ Modelo tier gratuito
        │
        ├─ 3. max_cost > 0 Y presupuesto permite? ── Si ──→ Modelo de pago
        │
        └─ 4. Rechazar ──→ Error devuelto, el host lo gestiona
```

## Tabla de Costes

| Nivel | Proveedor | Modelo | Coste |
|---|---|---|---|
| 1 | Ollama (local) | qwen2.5-coder:7b | Gratuito |
| 2 | OpenRouter | qwen/qwen3-coder:free | Gratuito |
| 3 | OpenRouter | qwen/qwen3-coder | $0.22/1M entrada, $1.00/1M salida |
| 4 | Rechazar | — | — |

## Controles de Presupuesto

- **Tope mensual**: `HIVE_OPENROUTER_BUDGET` (por defecto: $1.00)
- **Tope por petición**: parámetro `max_cost_per_request` en `delegate_task`
- **Modelo de pago**: `HIVE_OPENROUTER_PAID_MODEL` (por defecto: `qwen/qwen3-coder`)
- El tracking de presupuesto usa SQLite con modo WAL y locking thread-safe para acceso concurrente

## Cuándo Delegar

Buenos candidatos para delegación:

- Explicaciones de regex
- Formateo de código y refactoring simple
- Generación de boilerplate
- Redacción de documentación
- Q&A simple sobre temas conocidos

Mantener en tu modelo primario:

- Decisiones complejas de arquitectura
- Refactoring multi-archivo con dependencias
- Revisión de código sensible a seguridad
- Tareas que requieren comprensión profunda del codebase

## ¿Por Qué Estos Modelos?

Los modelos por defecto de Hive fueron seleccionados en base a coste, calidad de código y disponibilidad:

### Ollama: qwen2.5-coder:7b

- **¿Por qué 7B?** Funciona en hardware mínimo (8GB RAM, solo CPU). Mini PCs Intel N95, portátiles viejos y dispositivos NAS pueden ejecutarlo. Modelos más grandes (14B, 32B) necesitan GPUs o 32GB+ RAM.
- **¿Por qué Qwen?** Mejores benchmarks de código en la clase 7B. Supera a CodeLlama 7B, DeepSeek Coder 6.7B y StarCoder2 7B en HumanEval y MBPP.
- **Ideal para:** Explicaciones de regex, boilerplate, Q&A simple. No apto para razonamiento complejo multi-archivo.
- **Override:** Configura `HIVE_OLLAMA_MODEL` con cualquier modelo que hayas descargado con `ollama pull`.

### OpenRouter gratuito: qwen/qwen3-coder:free

- **¿Por qué Qwen3 Coder?** Modelo MoE de 480B (solo 30B parámetros activos). Mejor modelo de código gratuito disponible en OpenRouter. Competitivo con GPT-4 en tareas de código.
- **¿Por qué tier gratuito?** Coste cero para el 80% de tareas delegadas que no necesitan calidad de tier de pago. Con rate limit pero suficiente para la mayoría de flujos de trabajo.
- **Override:** Configura `HIVE_OPENROUTER_MODEL` con cualquier modelo gratuito en OpenRouter (ej. `deepseek/deepseek-coder-v2:free`).

### OpenRouter de pago: qwen/qwen3-coder

- **¿Por qué de pago?** Mismo modelo, sin rate limits, mayor prioridad. Solo se usa cuando lo permites explícitamente vía `max_cost_per_request > 0`.
- **Coste:** ~$0.22/M tokens de entrada, ~$1.00/M tokens de salida. Con el presupuesto por defecto de $1/mes, eso son aproximadamente 1M tokens de salida o ~500 llamadas a delegate_task.
- **Override:** Configura `HIVE_OPENROUTER_PAID_MODEL` con cualquier modelo en OpenRouter.

### Cómo funciona la decisión de enrutamiento

```
delegate_task(prompt, max_cost_per_request=0)
    │
    │  Modelo explicito especificado? (ej. "ollama:llama3")
    │  └─ Si → Enviar directamente a ese proveedor. Saltar enrutamiento.
    │
    │  Intentar Ollama (ping HTTP al endpoint):
    │  └─ Alcanzable → Enviar a HIVE_OLLAMA_MODEL. Hecho.
    │
    │  Intentar OpenRouter tier gratuito:
    │  └─ API key configurada → Enviar a HIVE_OPENROUTER_MODEL. Hecho.
    │
    │  Intentar OpenRouter tier de pago:
    │  └─ max_cost_per_request > 0 Y presupuesto mensual permite?
    │     └─ Si → Enviar a HIVE_OPENROUTER_PAID_MODEL. Hecho.
    │
    └─ Todos los niveles agotados → Devolver error. El modelo host lo gestiona.
```

El enrutamiento es fail-fast: si Ollama no es alcanzable (timeout HTTP), inmediatamente pasa a OpenRouter. Sin reintentos, sin esperas. Un fallthrough típico toma <100ms.
