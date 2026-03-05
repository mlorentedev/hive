---
title: Benchmarks
description: Token savings and max_lines calibration data for Hive vault tools.
---

## Why This Matters

AI coding assistants typically load context statically: CLAUDE.md files, project docs, and convention guides are injected into the context window at session start, paying the full token cost every time regardless of whether the content is relevant. A moderately sized knowledge base (500+ files) can consume tens of thousands of tokens before a single question is asked.

Hive replaces static loading with on-demand vault queries. Context is fetched only when needed, scoped to the relevant project and section. The benchmarks below quantify the token savings across different usage patterns and calibrate the `max_lines` parameter for optimal signal-to-noise ratio.

## Methodology

- **Synthetic vault** matching real-world distribution: P25=37 lines, median=77 lines, P90=262 lines, max=878 lines per file.
- **Real Obsidian vault**: 228 files, 493K tokens total.
- **Token estimation**: 1 token ~ 4 characters (standard approximation for English text and code).
- **Signal-to-noise (S/N)**: percentage of returned lines containing useful content vs. boilerplate (YAML frontmatter, empty headers, separators, blank lines).
- **Test suite**: `pytest tests/test_benchmark.py -v -s`

## Results

### Token Savings by Session Type

Synthetic vault with 51K tokens as the static baseline (loading everything at session start):

| Session type | Queries | Tokens used | Savings vs static |
|---|---|---|---|
| Bug fix (focused) | 2 | 2,645 | **94.8%** |
| Feature dev (broad) | 4 | 13,082 | **74.4%** |
| Exploration (heavy) | 6 | 27,549 | **46.0%** |

Real vault (493K tokens): 5 project context queries consumed 2,925 tokens total, yielding **99.4% savings** over static loading.

### max_lines Calibration

`vault_query` on a real file (878 lines, 15K tokens):

| max_lines | Tokens | Content captured | Signal/Noise |
|---|---|---|---|
| 50 | 357 | 2.3% | 48% |
| 100 | 1,103 | 7.2% | 49% |
| 200 | 2,797 | 18.2% | 51% |
| 300 | 4,570 | 29.8% | 49% |
| **500** | **7,625** | **49.7%** | **52%** |
| 1000 | 15,355 | 100% | 52% |

`vault_search` on real vault (query="deploy"):

| max_lines | Tokens | Content captured | S/N | Matches found |
|---|---|---|---|---|
| 100 | 2,214 | 16.7% | 97% | 47/312 |
| 300 | 6,494 | 49.0% | 99% | 159/312 |
| **500** | **13,039** | **98.5%** | **99%** | **304/312** |
| 750+ | 13,244 | 100% | 100% | 312/312 |

### Signal-to-Noise by Tool

| Tool | S/N ratio | Best for |
|---|---|---|
| vault_search | 98.8% | Targeted queries -- minimal noise |
| vault_smart_search | 98.4% | Ranked search results |
| vault_query | 87-90% | Full section reads |
| session_briefing | 78.5% | Cold start context assembly |

## Recommendations

Based on these results:

1. **Default max_lines = 500** -- captures 98.5% of search results with 99% S/N. The previous default (100) missed 83% of content in large files.
2. **Use vault_search for precision** -- highest S/N ratio (98.8%). Prefer over vault_query when you know what you are looking for.
3. **session_briefing for cold starts** -- despite lower S/N (78.5%), it assembles context, tasks, and health in one call (~1,300 tokens).
4. **Saturation at 500-1000 lines** -- values above 1000 add zero benefit with current vault sizes. The largest real vault file is 878 lines.
5. **Override max_lines per query** -- for quick lookups, pass `max_lines=200`. For comprehensive reads, use `max_lines=0` (unlimited).

## Running the Benchmarks

```bash
# Synthetic vault benchmarks (no external deps)
pytest tests/test_benchmark.py -v -s

# Real vault benchmarks (requires Obsidian vault)
pytest tests/test_benchmark.py -m smoke -v -s
```
