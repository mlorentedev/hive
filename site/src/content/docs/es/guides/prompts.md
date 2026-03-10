---
title: Prompts
description: Prompts MCP integrados para flujos de trabajo estructurados.
---

Hive incluye 4 prompts MCP — protocolos estructurados que cualquier cliente MCP puede invocar para seguir flujos de trabajo multi-paso.

## retrospective

Revisión de fin de sesión que extrae lecciones y las agrega al vault.

**Parámetros**: `project` (string)

**Protocolo**:
1. Revisar el trabajo completado en la sesión actual
2. Identificar patrones, errores e insights
3. Formatear como lecciones estructuradas
4. Usar `vault_write` para agregar al `90-lessons.md` del proyecto

**Uso**: Pide a tu asistente que "ejecute una retrospectiva para mi-proyecto" al final de una sesión de trabajo.

## delegate

Protocolo estructurado para delegar tareas a modelos más baratos vía hive-worker.

**Parámetros**: `task` (string)

**Protocolo**:
1. Evaluar complejidad de la tarea contra una matriz de idoneidad
2. Elegir el tier de modelo apropiado
3. Construir prompt rico en contexto
4. Llamar a `delegate_task` con el prompt preparado
5. Validar la respuesta antes de usarla

**Uso**: Pide a tu asistente que "delegue esta tarea: explica esta regex"

## vault_sync

Sincronización post-sprint del vault — reconciliar documentación con código enviado.

**Parámetros**: `project` (string)

**Protocolo**:
1. Cargar contexto actual del proyecto y tareas
2. Comparar con historial git reciente
3. Identificar docs obsoletos, tareas completadas, documentación faltante
4. Actualizar archivos del vault para reflejar el estado actual

**Uso**: Pide a tu asistente que "sincronice el vault para mi-proyecto después de este sprint"

## benchmark

Estimar ahorro de tokens de las herramientas MCP de Hive en la sesión actual.

**Parámetros**: ninguno

**Protocolo**:
1. Llamar a `vault_health(include_usage=True)` para obtener estadísticas de uso de herramientas
2. Estimar tokens que habría consumido la carga estática
3. Calcular porcentaje de ahorro
4. Reportar resultados

**Uso**: Pide a tu asistente que "haga benchmark del ahorro de tokens de hive"
