---
title: Integración con Obsidian-Git
description: Coopera con el plugin obsidian-git de Obsidian para evitar contención de locks y amortizar commits.
---

Si tu vault también se abre en Obsidian con el plugin [obsidian-git](https://github.com/Vinzent03/obsidian-git) auto-committing cada N minutos, el `git commit` por escritura de hive y el commit por intervalo de obsidian-git pueden competir por `.git/index.lock`. Desde el punto de vista de un operador de hive esto se ve como congelamientos silenciosos de 30 segundos coincidentes con el tick de auto-save de obsidian-git.

HIVE-115 PR-4 introduce un **modo de cooperación opt-in**: cuando se habilita, hive detecta la salud de obsidian-git y le deja manejar el commit, mientras sigue escribiendo los archivos a disco síncronamente. Hace fallback al commit propio de hive cuando obsidian-git está roto o ausente.

## Activación

Configura la variable de entorno al registrar hive con tu cliente MCP:

```bash
# Claude Code
claude mcp add -s user hive \
  -e VAULT_PATH=$HOME/tu-vault \
  -e HIVE_AUTO_DEFER_TO_EXTERNAL_COMMITTER=true \
  -- uvx --upgrade hive-vault
```

Por defecto está en `false`. Cualquier valor de `true`, `yes`, `1`, `on` (case-insensitive) activa la cooperación; cualquier otro valor (incluso no estar definida) mantiene el comportamiento pre-PR-4 de commit inline.

## Cuándo hive difiere

El predicado compuesto (según ADR-010 + el audit pre-PR-3 del 2026-05-22 M4):

```
defer ⇔
    env "HIVE_AUTO_DEFER_TO_EXTERNAL_COMMITTER" == "true"
    AND el plugin obsidian-git está instalado (data.json presente, commitInterval > 0)
    AND (
        (edad del último commit < 2 * autoSaveInterval)
        OR
        ( git status --porcelain produce salida vacía )   # vault inactivo
    )
```

- **Ventana de commit reciente** es `2 × commitInterval` (minutos de la configuración propia de obsidian-git). Con el default de 10 minutos de auto-save, hive considera "recientes" los commits dentro de los últimos 20 minutos.
- **Vault inactivo** se considera deferible. Si `git status --porcelain` está vacío, no hay nada que commitear — el committer externo no se ha colgado, no tiene nada que hacer. Seguro.
- **Sucio + obsoleto** es el único caso que cae al fallback. obsidian-git está configurado pero ni commitea a tiempo ni deja el vault limpio ⇒ probablemente pausado / roto ⇒ hive commitea la escritura él mismo.

## Superficie de respuesta

| Escenario | Sufijo de respuesta de `vault_write` / `vault_patch` |
|-----------|-----------------------------------------------------|
| `commit=True`, sin defer | `""` (commiteado inline por hive — sin cambios respecto a pre-PR-4) |
| `commit=True`, diferido | `" (deferred to obsidian-git; will be picked up on its next tick)"` |
| por defecto (diferido) | `" (queued — commits on the next reconciler tick)"` |
| por defecto, sin reconciler corriendo | `" (uncommitted — call vault_commit to flush)"` |

El sufijo de "deferred" hace visible el cambio de comportamiento a los operadores que inspeccionan los logs de las tools — sin flip silencioso de semánticas.

## Internals del health probe

El probe lanza dos invocaciones git baratas:

- `git log -1 --format=%ct` — timestamp Unix del commit de HEAD.
- `git status --porcelain` — vacío cuando el working tree coincide con HEAD.

Ambas corren vía `subprocess.run` (read paths, `tool_timeout` advisory; sin autoridad de terminación de `bounded_call` necesaria). Cada una está acotada a 10 segundos por `subprocess.run(timeout=10)`. Cualquier código de retorno no-cero o excepción se trata como "no saludable ⇒ no diferir".

## Outbox de refuerzo (relacionado)

Los contadores de refuerzo de lecciones se enrutan a través de un **outbox** in-process en memoria, drenado por un thread daemon reconciliador cada `HIVE_OUTBOX_TICK_S` (default 5 segundos). Esto amortiza ~5 transacciones de escritura SQLite por segundo a un UPSERT en lote por tick, manteniendo `same-process read after write` inmediato (los read paths drenan el outbox síncronamente antes de consultar).

La consistencia cross-process es eventual dentro de `~2 × HIVE_OUTBOX_TICK_S`. Aceptable para señales de ranking; **nunca uses este patrón para estado durable**. El docstring del outbox especifica el contrato de pérdida-en-crash explícitamente.

## Recomendaciones de tuning

| Carga de trabajo | `HIVE_AUTO_DEFER_TO_EXTERNAL_COMMITTER` | `HIVE_OUTBOX_TICK_S` | Notas |
|------------------|------------------------------------------|----------------------|-------|
| Dev en solitario con Obsidian abierto | `true` | `5.0` (default) | Elimina el patrón de freeze 30s bajo contención de obsidian-git. |
| CI / agente headless (sin Obsidian) | `false` (default) | `5.0` | obsidian-git no presente → el predicado defer retorna False de todos modos. `false` explícito documenta la intención. |
| Flota de agentes, sin Obsidian, sensible a latencia | `false` | `2.0` | Visibilidad cross-process más rápida para contadores de refuerzo a costa de ligeramente más carga de escritura SQLite. |
| Air-gapped, sync solo por archivos (Syncthing / rsync) | `false` | `30.0` | Sin committer externo con el que cooperar; menos ticks del reconciliador reducen wakeups. |

## Troubleshooting

**Síntoma:** `vault_write` siempre retorna el sufijo `"(deferred ...)"` incluso tras cerrar Obsidian.

- El `data.json` de obsidian-git persiste en `<vault>/.obsidian/plugins/obsidian-git/data.json` incluso cuando Obsidian está cerrado. El probe revisa el archivo, no si Obsidian está corriendo. Desactiva el plugin (o elimina el directorio) para volver a que hive haga commit.

**Síntoma:** Las escrituras diferidas nunca aparecen en `git log`.

- El `autoSaveInterval` del plugin debe ser > 0. Inspecciona `data.json`: campo `commitInterval`. Si es `0`, el plugin no está auto-commitiendo y el predicado de defer de hive retornará False (la cláusula `commit_interval > 0` en `detect_obsidian_git`).
- En Obsidian, abre el panel de configuración de obsidian-git y verifica que "Backup interval" sea > 0 minutos.

**Síntoma:** hive commitea inline a pesar de `HIVE_AUTO_DEFER_TO_EXTERNAL_COMMITTER=true`.

- El probe encontró una de: env no parseado como truthy (¿typo?), obsidian-git no instalado en este vault, o `recent_commit ∨ empty_porcelain` fue False (vault sucio + último commit de obsidian-git > `2 * interval` atrás). El commit inline ES el fallback de seguridad.

## Ver también

- [Variables de entorno de Configuration](/es/configuration/) — referencia `HIVE_*` completa.
- [Troubleshooting — Contención multi-sesión](/es/guides/troubleshooting/#contención-multi-sesión) — técnicas hermanas para N=3-5 procesos hive contra el mismo vault.
- ADR-010 (en el vault del maintainer) — racional de diseño del patrón cooperate-don't-compete.
