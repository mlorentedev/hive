---
title: Enrutamiento de Workers
description: Cómo gestiona Hive su único worker las tareas delegadas, y dónde viven las decisiones de enrutamiento.
---

El worker de Hive se encarga de las tareas delegadas, reservando tu modelo principal para el razonamiento de mayor valor. Desde 4.0.0 hay exactamente **un** worker: un único endpoint compatible con OpenAI. Hive hace un intento contra él e informa de lo ocurrido. Elegir *a qué* pool va una tarea es trabajo del llamador, no de Hive.

## Un Worker, Un Intento

El worker se configura con tres variables:

| Variable | Si no está definida, usa |
|---|---|
| `HIVE_WORKER_BASE_URL` | `HIVE_EMBED_BASE_URL` |
| `HIVE_WORKER_API_KEY` | `HIVE_EMBED_API_KEY` |
| `HIVE_WORKER_MODEL` | `HIVE_EMBED_MODEL` |

El fallback es por campo, así que un despliegue puede sobrescribir solo el modelo y heredar el endpoint. Sin URL base definida (y sin `HIVE_EMBED_BASE_URL` que heredar), el worker queda **deshabilitado**: Hive nunca adivina un endpoint por defecto, porque un worker que apunta en silencio a algún sitio es peor que uno que dice que no está configurado.

Hive no nombra ningún proveedor. Cualquier servicio o runtime local que sirva un `/v1` compatible con OpenAI es un worker válido, y estas tres variables son la única forma de elegir uno. Los ajustes por proveedor y los alias de modelo de versiones anteriores se eliminaron en 4.0.0; define `HIVE_WORKER_*` en su lugar.

## Flujo

```
delegate_task(prompt, context, model)
    │
    ├─ ¿Alias retirado en `model`? ──→ Rechazado, nombra el reemplazo
    │
    ├─ ¿Worker no configurado? ──→ pool_unavailable ──→ el host gestiona la tarea
    │
    └─ Una petición al endpoint configurado
        │
        ├─ Respondió ──→ ok
        ├─ Inalcanzable / timeout HTTP / 429 / credencial rechazada ──→ pool_unavailable
        ├─ Respondió pero inutilizable: 5xx, otros 4xx, fallo desconocido ──→ task_failed
        └─ Venció el deadline ──→ timeout
```

No hay ningún paso que pruebe un segundo modelo. El argumento `model` elige un modelo en el *mismo* endpoint; un valor vacío usa `HIVE_WORKER_MODEL`.

## Clases de Fallo

Los fallos se clasifican, no se colapsan en un único error:

- **`pool_unavailable`** — el endpoint no atendió la petición: era inalcanzable o su petición HTTP agotó el tiempo, aplicó un límite de tasa (429), rechazó la credencial (401/403), o no hay worker configurado. Un llamador que tenga otro pool puede reintentar la tarea allí.
- **`task_failed`** — el endpoint respondió y la respuesta es inutilizable (un 5xx, o un 4xx sobre la propia petición, como un id de modelo desconocido), o la petición en sí era incorrecta. Reintentar en otro sitio ocultaría un fallo real, así que un llamador no debe hacerlo. Los fallos desconocidos también acaban aquí: la dirección fail-closed es la que no reintenta.

`delegate_task(structured=True)` devuelve la clase como valor de `status` en un registro JSON (consulta [Herramientas de Worker](/hive/es/tools/worker/#modo-estructurado)). La clase viaja como dato y no como tipo de excepción porque los tipos de excepción no sobreviven a la frontera JSON-RPC entre un daemon y sus clientes.

## El Enrutamiento Pertenece al Llamador

Un backend que hace fallback por su cuenta se convierte en una segunda autoridad de enrutamiento, y su respuesta ya no puede atribuirse a un modelo. Por eso Hive deliberadamente no tiene cadena de fallback interna. Un dispatcher que mantiene un mapa de enrutamiento lo recorre por sí mismo — avanzando con `pool_unavailable`, deteniéndose con `task_failed` — y siempre sabe qué modelo produjo cada respuesta.

`hive delegate` es el verbo de línea de comandos pensado para ese dispatcher. Ejecuta una tarea contra el worker configurado e imprime un registro JSON por stdout. `--model`, `--timeout` y `--prompt` son obligatorios, de modo que un dispatcher nunca cree haber elegido un modelo o fijado un deadline por defecto. El código de salida lleva la clase:

| Código de salida | Significado |
|---|---|
| `0` | `ok` |
| `1` | `task_failed` — no reintentar |
| `2` | Error de uso |
| `3` | `pool_unavailable` — probar la siguiente entrada |
| `4` | `timeout` |

## Sin Tope de Gasto

Hive no registra ni limita el gasto. En un endpoint de tarifa plana o local la restricción que importa es la concurrencia, no el coste, así que un tope en dólares mediría la magnitud equivocada. Lo que Hive registra es telemetría de uso — número de peticiones, tokens y latencia por modelo — que informan [`worker_status`](/hive/es/tools/worker/#worker_status) y `vault_health(include_runtime=True)`. Si tu endpoint factura por token, aplica los límites en el lado del proveedor.

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


## Elegir un Modelo

Hive no incluye un modelo por defecto. Define `HIVE_WORKER_MODEL` con el id que tu endpoint asigna al modelo que quieres, o pasa `model=` en cada llamada. Un modelo pequeño y rápido encaja con las tareas anteriores. Un modelo lento se nota directamente como latencia, y un endpoint con límite de tasa se nota como `pool_unavailable`. Sube `HIVE_HTTP_TIMEOUT` y `HIVE_TOOL_TIMEOUT` (o pasa `timeout_s`) para modelos lentos.
