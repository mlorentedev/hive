---
title: Recursos
description: Recursos MCP expuestos por Hive.
---

Hive expone 5 recursos MCP que pueden ser consumidos directamente por clientes de IA.

## Recursos Estáticos

### hive://projects

Lista todos los proyectos del vault con recuentos de archivos y atajos de sección disponibles.

```
URI: hive://projects
```

### hive://health

Métricas de salud del vault para todos los proyectos — recuentos de archivos, recuentos de líneas, detección de archivos obsoletos y cobertura de secciones.

```
URI: hive://health
```

## Plantillas de Recursos

Estos recursos aceptan un parámetro `{project}`:

### hive://projects/{project}/context

Devuelve el documento de contexto del proyecto (`00-context.md`).

```
URI: hive://projects/mi-proyecto/context
```

### hive://projects/{project}/tasks

Devuelve el backlog de tareas del proyecto (`11-tasks.md`).

```
URI: hive://projects/mi-proyecto/tasks
```

### hive://projects/{project}/lessons

Devuelve las lecciones aprendidas del proyecto (`90-lessons.md`).

```
URI: hive://projects/mi-proyecto/lessons
```

## Recursos vs Herramientas

Los recursos son **endpoints de datos de solo lectura** — devuelven contenido pero no aceptan parámetros complejos. Úsalos cuando necesites cargar un documento conocido.

Las herramientas son **acciones** — aceptan parámetros, soportan filtrado y pueden escribir datos. Úsalas para búsqueda, actualizaciones y consultas complejas.

| Necesidad | Usar |
|---|---|
| Cargar contexto de proyecto | recurso `hive://projects/{project}/context` |
| Buscar en el vault | herramienta `vault_search` |
| Actualizar un archivo | herramienta `vault_write` |
| Listar proyectos | recurso `hive://projects` O herramienta `vault_list` |
