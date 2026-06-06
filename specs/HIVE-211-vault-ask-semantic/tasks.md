---
tags: [spec, tasks]
created: "2026-06-05"
---

# Tasks - HIVE-211-vault-ask-semantic (Stage 1: vector RAG)

> TDD order. One task = one focused commit. Tick as you go. Reorder freely while spec is in `draft`; freeze once you start `implementing`.
> **Status: draft / not started.** Scaffolded 2026-06-05 alongside issue #211. Implementation begins after #202 lands and the NaN-embeddings check (Risks) is resolved.

## Setup

- [x] Resolve [VERIFY] NaN `/embeddings` endpoint — **CONFIRMED viable** (`qwen3-embedding`, 4096-dim, OpenAI-shaped); see proposal Risks (2026-06-05)
- [x] Branch `feat/HIVE-211-vault-ask-semantic` off `master` (rebased onto master @ release 1.36.0)
- [x] Decide vector store: **numpy + pickle** (in-memory dot-product; sufficient for Stage 1 scale, no native wheel); `[semantic]` extra packaging lands in PR2. Default embed provider documented = **NaN** (`qwen3-embedding`), Ollama as local/private alt (2026-06-05)
- [ ] `proposal.md` complete; open questions in Risks resolved before freezing

## Implementation (TDD order)

> Keep `vault_ask` and its index in their own module(s); base install + other tools must be untouched. Lazy-import the optional deps. JSON schema stays `anyOf`-free.

- [ ] Write failing test: `vault_ask` with NO embed backend → clear "disabled / how to enable" message; no import error; other tools still register (AC2)
- [ ] Generalize the worker client into a parameterized OpenAI-compatible client (Ollama / NaN / OpenRouter) for chat + embeddings
- [ ] Implement config (`HIVE_EMBED_BASE_URL` / `HIVE_EMBED_MODEL` / `HIVE_EMBED_API_KEY`) + capability detection
- [ ] Write failing test: chunk + embed a small fixture vault; vector search returns the relevant chunk
- [ ] Implement chunker + embedding pass + vector store (lazy build / `vault_reindex`)
- [ ] Write failing test: `vault_ask(question)` returns a synthesized answer citing a real source file (AC1, AC6)
- [ ] Implement retrieval → anti-hallucination cite-your-sources synthesis prompt → answer
- [ ] Write failing test: incremental re-embed on `vault_write`/`vault_patch` only when index exists; disabled = zero overhead (AC4)
- [ ] Hook incremental re-embed into the write path (guarded by index presence)
- [ ] Write failing test: provider swap (Ollama ↔ NaN) by config only (AC3) — mock both endpoints
- [ ] Write failing test: `vault_ask` schema contains no `anyOf` (AC5; reuse HIVE-119 helper)
- [ ] Add retriever-attribution + recall instrumentation (the Stage-2 gate)
- [ ] Embedding-model-mismatch detection + rebuild

## Closing

- [ ] Every acceptance criterion covered by ≥1 test
- [ ] `features.json` entries non-vacuous
- [ ] Type checks (`mypy --strict src/`) + lint (`ruff`) pass
- [ ] Full suite green; base install (without `[semantic]`) imports + runs unaffected
- [ ] Docs: how to enable (Ollama and NaN), cost/privacy note, the disabled-default behaviour
- [ ] PR opened referencing this spec folder + #211

## Stage 2 (deferred — do NOT start without data)

- [ ] Structural graph edges from `[[wikilinks]]` + frontmatter (deterministic, no LLM) — only if Stage 1 instrumentation shows recurring multi-hop/entity recall failures

## Machine-readable features

Sibling `features.json` follows [[pattern-feature-list-as-primitive]]. **Pass-state gating:** the agent CANNOT write `"state": "passing"` — only the harness, after running `verification` and capturing exit code 0, may set that terminal state.
