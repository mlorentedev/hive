---
id: adr-003-rag-evaluation
type: adr
status: active
created: "2026-03-03"
owner: manu
---

# ADR-003: RAG Evaluation — Embeddings Not Yet Needed

## Status

Accepted

## Date

2026-03-03

## Context

Phase 3.2 required an evaluation of whether the vault needs Retrieval Augmented Generation (embeddings + vector store) to maintain search quality as the vault grows.

Current vault state:
- ~50-80 markdown files across ~10 projects
- Full-text search (`vault_search`) scans all files in <100ms
- Smart search (`vault_smart_search`) adds ranking with O(N) file scan
- Session profiling (`vault_usage`) now tracks which queries are useful

The question: when does linear scan become too slow, and what should replace it?

## Analysis

### Current Performance (N ≈ 80 files)

| Operation | Approach | Latency |
|---|---|---|
| `vault_search` | `rglob("*.md")` + string match | <100ms |
| `vault_smart_search` | Same scan + scoring | <100ms |
| `vault_query` | Direct file read | <5ms |

At 80 files, there is no performance problem. The bottleneck is context window tokens, not search speed — and that's already solved by `_truncate` and `max_lines`.

### Projected Growth

| Timeframe | Estimated files | Search latency (projected) |
|---|---|---|
| Current | ~80 | <100ms |
| 6 months | ~150 | <200ms |
| 1 year | ~300 | <400ms |
| 2 years | ~500+ | ~800ms (threshold) |

The vault grows ~5-10 files/month. At this rate, 500 files is 2+ years away.

### When Embeddings Become Necessary

1. **Search latency >500ms** — user-perceptible delay on every query
2. **Semantic search needed** — "find architecture decisions about caching" when no file contains the word "caching" but discusses TTL, Redis, memoization
3. **Cross-reference discovery** — "what's related to this file?" without explicit links

Currently none of these are pain points. Frontmatter metadata (`type`, `status`, `tags`) already provides structured filtering that embeddings would not improve.

### Technology Options (for future reference)

| Option | Pros | Cons |
|---|---|---|
| **sqlite-vec** | Zero new deps (SQLite extension), local, fast | Requires compilation, Python bindings limited |
| **chromadb** | Pure Python, well-documented, local | Heavy dependency (~200MB), overkill for <500 files |
| **sentence-transformers + FAISS** | Best quality, battle-tested | GPU preferred, large models, complex setup |
| **Ollama embeddings** | Already have Ollama infra | 8GB mini PC may struggle with embedding model + LLM |

## Decision

**Do not implement RAG/embeddings now.** Revisit when:
1. Vault exceeds 500 files, OR
2. `vault_usage` data shows frequent "no matches" results (semantic gap), OR
3. Search latency consistently exceeds 500ms

The `vault_usage` tool (Phase 3.2) provides the instrumentation to detect condition #2 automatically.

## Consequences

### Positive

- No new dependencies or infrastructure complexity
- No embedding model resource contention on 8GB mini PC
- Full-text search remains deterministic and debuggable
- Decision is data-driven (usage tracking in place to trigger revisit)

### Negative

- No semantic search capability (must match exact keywords)
- No "related files" discovery beyond manual tags

### Neutral

- Technology evaluation is documented for when the threshold is reached
- `sqlite-vec` is the most likely choice given existing SQLite usage

## References

- [adr-001-orchestration-model.md](adr-001-orchestration-model.md): Infrastructure constraints (8GB mini PC)
- [adr-002-system-architecture-phase5.md](adr-002-system-architecture-phase5.md): Current search implementation details
- [sqlite-vec](https://github.com/asg017/sqlite-vec): SQLite vector extension
- [chromadb](https://www.trychroma.com/): Embedding database
