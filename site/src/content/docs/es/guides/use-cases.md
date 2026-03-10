---
title: Casos de Uso
description: Flujos de trabajo y ejemplos reales con Hive.
---

## Iniciar una Nueva Sesión

Carga todo el contexto que necesitas con una sola llamada:

> "Ejecuta un session briefing para mi-proyecto"

Tu asistente de IA llama a `session_briefing(project="mi-proyecto")` y obtiene: tareas activas, lecciones recientes, historial git y métricas de salud — todo lo necesario para retomar donde lo dejaste.

## Consultar Conocimiento del Proyecto

En lugar de pegar estándares en CLAUDE.md, consúltalos bajo demanda:

> "Carga los patrones de arquitectura del vault"

```python
vault_query(project="_meta", path="patterns/pattern-architecture.md")
```

Solo se carga el documento relevante. Tu CLAUDE.md de 800 líneas se queda en 100.

## Buscar Información Entre Proyectos

Busca en toda tu base de conocimiento:

> "Busca en el vault cómo gestionamos la autenticación"

```python
vault_search(query="autenticacion", type_filter="adr")
```

Devuelve líneas coincidentes de todos los archivos, filtradas solo a documentos ADR, con cabeceras de metadatos.

## Búsqueda Inteligente con Ranking

Cuando necesitas los resultados más relevantes, no solo coincidencias de palabras clave:

> "Encuentra los docs más relevantes sobre deployment"

```python
vault_search(query="deployment", ranked=True, max_results=5)
```

Los resultados se rankean por: active > draft > archived, reciente > antiguo, y densidad de coincidencias. Los documentos más útiles aparecen primero.

## Registrar Lecciones Aprendidas

Después de resolver un bug complicado o tomar una decisión de arquitectura:

> "Agrega esta lección al vault: Siempre usar modo WAL para SQLite en contextos async"

```python
vault_write(
    project="mi-proyecto",
    section="lessons",
    operation="append",
    content="\n## Modo WAL en SQLite\nSiempre usar modo WAL..."
)
```

Auto-commit a git. La lección estará disponible en futuras sesiones.

## Crear Nuevos Documentos

Inicia un nuevo ADR, runbook o cualquier documento:

> "Crea un ADR para elegir PostgreSQL sobre MySQL"

```python
vault_write(
    project="mi-proyecto",
    path="30-architecture/adr-005-postgresql.md",
    content="# ADR-005: PostgreSQL sobre MySQL\n\n## Contexto\n...",
    operation="create",
    doc_type="adr"
)
```

Hive auto-genera frontmatter YAML y hace commit a git.

## Capturar Lecciones Inline

Cuando descubres algo importante a mitad de tarea, captúralo inmediatamente sin romper tu flujo:

> "Captura una lección: siempre validar frontmatter YAML antes de escribir al vault"

```python
capture_lesson(
    project="mi-proyecto",
    title="Validacion de frontmatter YAML",
    context="Escribiendo herramienta vault_write que modifica archivos de proyecto",
    problem="Campos requeridos faltantes (id, type, status) causan fallos silenciosos en herramientas downstream como vault_search y session_briefing",
    solution="Siempre validar frontmatter antes de escrituras al vault — rechazar si faltan campos requeridos",
    tags=["yaml", "vault", "validacion"]
)
```

La lección se agrega al `90-lessons.md` del proyecto con frontmatter auto-generado. Si ya existe una lección con el mismo título, se omite (deduplicación).

Esto es mejor que esperar a la retrospectiva de fin de sesión — los insights capturados en el momento son más precisos y es menos probable que se olviden.

## Extracción de Lecciones por Lotes

Después de una sesión larga de depuración o discusión de arquitectura, extrae múltiples lecciones de una vez:

> "Extrae lecciones de estas notas de sesión sobre la migración de base de datos"

```python
capture_lesson(
    project="mi-proyecto",
    text="Descubrimos que la migracion fallo porque...[pegar notas de sesion]...",
    min_confidence=0.7,
    max_lessons=5
)
```

Esto envía el texto a un modelo worker (Ollama u OpenRouter) que identifica decisiones, causas raíz de bugs y elecciones de patrones — y luego los escribe en `90-lessons.md`. Tu modelo primario ahorra tokens al no procesar el texto en bruto.

**Cuándo usar inline vs lotes:**
- **Inline** (`capture_lesson` con title/problem/solution): Conoces la lección exacta — entrada estructurada, una lección
- **Lotes** (`capture_lesson` con `text=...`): Tienes texto en bruto y quieres que el worker encuentre lecciones — extracción por lotes

## Delegar Tareas Triviales

Ahorra tokens enrutando tareas simples a modelos más baratos:

> "Delega: explica esta regex ^(?:[a-z0-9]+\.)*[a-z0-9]+$"

```python
delegate_task(
    prompt="Explica esta regex: ^(?:[a-z0-9]+\\.)*[a-z0-9]+$",
    context="Usada para validacion de nombres de dominio"
)
```

Enruta a Ollama (gratuito, local) primero. Si no está disponible, usa OpenRouter como fallback.

## Retrospectiva de Fin de Sesión

Antes de terminar una sesión de trabajo, captura lo que aprendiste:

> "Ejecuta una retrospectiva para mi-proyecto"

El prompt `retrospective` guía a tu asistente a través de:
1. Revisar el trabajo completado
2. Identificar patrones e insights
3. Formatear lecciones estructuradas
4. Agregar al `90-lessons.md` del proyecto

## Sincronización Post-Sprint del Vault

Después de lanzar una feature, reconcilia la documentación con el código:

> "Sincroniza el vault para mi-proyecto"

El prompt `vault_sync` recorre:
1. Comparar docs del vault con historial git reciente
2. Marcar tareas completadas como hechas
3. Actualizar documentación obsoleta
4. Señalar gaps que necesitan nuevos docs

## Detectar Drift del Vault

Encuentra frontmatter roto, docs obsoletos y links muertos antes de que causen problemas:

> "Valida mi vault para encontrar problemas"

```python
vault_health(project="mi-proyecto", checks=["frontmatter", "stale", "links"])
```

Devuelve problemas categorizados: campos de frontmatter faltantes, archivos sin actualizar en 180+ días, y `[[wikilinks]]` apuntando a archivos inexistentes. Ejecútalo después de lanzar una feature para detectar documentación que divergió de la realidad.

También puedes apuntar a checks específicos:

```python
vault_health(checks=["stale"])  # Solo marcar archivos obsoletos en todos los proyectos
```

## Monitorizar Ahorro de Tokens

¿Curiosidad por cuánto te está ahorrando Hive?

> "Ejecuta un benchmark"

El prompt `benchmark` consulta `vault_health(include_usage=True)` y estima cuántos tokens habría consumido la carga estática de contexto vs. las consultas bajo demanda.

## Comprobar Cambios Recientes del Vault

Mira qué se ha actualizado en tu vault recientemente:

> "Muestra cambios del vault de los últimos 7 días"

```python
vault_search(since_days=7, project="mi-proyecto")
```

Combina historial git con fechas de frontmatter para mostrar toda la actividad reciente.

## Comprobar Presupuesto de Workers

Monitoriza tu gasto en OpenRouter:

> "Muestra estado de workers"

```python
worker_status()
```

Devuelve: presupuesto mensual restante, conectividad de proveedores, recuento de peticiones y desglose de costes.
