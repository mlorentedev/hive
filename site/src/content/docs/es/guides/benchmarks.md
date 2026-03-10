---
title: Benchmarks
description: Datos de ahorro de tokens y calibración de max_lines para las herramientas de vault de Hive.
---

## Por Qué Importa

Los asistentes de IA para código típicamente cargan contexto de forma estática: archivos CLAUDE.md, docs de proyecto y guías de convenciones se inyectan en la ventana de contexto al inicio de sesión, pagando el coste completo de tokens cada vez sin importar si el contenido es relevante. Una base de conocimiento de tamaño moderado (500+ archivos) puede consumir decenas de miles de tokens antes de hacer una sola pregunta.

Hive reemplaza la carga estática con consultas al vault bajo demanda. El contexto se obtiene solo cuando se necesita, con alcance al proyecto y sección relevantes. Los benchmarks a continuación cuantifican el ahorro de tokens en diferentes patrones de uso y calibran el parámetro `max_lines` para una relación señal/ruido óptima.

## Metodología

- **Vault sintético** con distribución real: P25=37 líneas, mediana=77 líneas, P90=262 líneas, max=878 líneas por archivo.
- **Vault Obsidian real**: 228 archivos, 493K tokens en total.
- **Estimación de tokens**: 1 token ~ 4 caracteres (aproximación estándar para texto en inglés y código).
- **Señal/ruido (S/N)**: porcentaje de líneas devueltas que contienen contenido útil vs. boilerplate (frontmatter YAML, headers vacíos, separadores, líneas en blanco).
- **Suite de tests**: `pytest tests/test_benchmark.py -v -s`

## Resultados

### Ahorro de Tokens por Tipo de Sesión

Vault sintético con 51K tokens como baseline estático (cargando todo al inicio de sesión):

| Tipo de sesión | Consultas | Tokens usados | Ahorro vs estático |
|---|---|---|---|
| Bug fix (enfocado) | 2 | 2,645 | **94.8%** |
| Desarrollo de feature (amplio) | 4 | 13,082 | **74.4%** |
| Exploración (intensiva) | 6 | 27,549 | **46.0%** |

Vault real (493K tokens): 5 consultas de contexto de proyecto consumieron 2,925 tokens en total, obteniendo **99.4% de ahorro** sobre carga estática.

### Calibración de max_lines

`vault_query` en un archivo real (878 líneas, 15K tokens):

| max_lines | Tokens | Contenido capturado | Señal/Ruido |
|---|---|---|---|
| 50 | 357 | 2.3% | 48% |
| 100 | 1,103 | 7.2% | 49% |
| 200 | 2,797 | 18.2% | 51% |
| 300 | 4,570 | 29.8% | 49% |
| **500** | **7,625** | **49.7%** | **52%** |
| 1000 | 15,355 | 100% | 52% |

`vault_search` en vault real (query="deploy"):

| max_lines | Tokens | Contenido capturado | S/N | Coincidencias |
|---|---|---|---|---|
| 100 | 2,214 | 16.7% | 97% | 47/312 |
| 300 | 6,494 | 49.0% | 99% | 159/312 |
| **500** | **13,039** | **98.5%** | **99%** | **304/312** |
| 750+ | 13,244 | 100% | 100% | 312/312 |

### Señal/Ruido por Herramienta

| Herramienta | Ratio S/N | Mejor para |
|---|---|---|
| vault_search | 98.8% | Consultas dirigidas — ruido mínimo |
| vault_search (ranked) | 98.4% | Resultados de búsqueda rankeados |
| vault_query | 87-90% | Lecturas completas de sección |
| session_briefing | 78.5% | Ensamblaje de contexto en cold start |

## Recomendaciones

Basado en estos resultados:

1. **max_lines = 500 por defecto** — captura 98.5% de resultados de búsqueda con 99% S/N. El valor anterior (100) perdía el 83% del contenido en archivos grandes.
2. **Usar vault_search para precisión** — mayor ratio S/N (98.8%). Preferir sobre vault_query cuando sabes lo que buscas.
3. **session_briefing para cold starts** — a pesar de menor S/N (78.5%), ensambla contexto, tareas y salud en una llamada (~1,300 tokens).
4. **Saturación en 500-1000 líneas** — valores por encima de 1000 no aportan beneficio con los tamaños actuales de vault. El archivo más grande del vault real tiene 878 líneas.
5. **Sobreescribir max_lines por consulta** — para consultas rápidas, usa `max_lines=200`. Para lecturas completas, usa `max_lines=0` (sin límite).

## Ejecutar los Benchmarks

```bash
# Benchmarks con vault sintetico (sin dependencias externas)
pytest tests/test_benchmark.py -v -s

# Benchmarks con vault real (requiere vault de Obsidian)
pytest tests/test_benchmark.py -m smoke -v -s
```
