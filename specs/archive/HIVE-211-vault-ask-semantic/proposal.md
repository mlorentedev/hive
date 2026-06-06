---
id: "HIVE-211-vault-ask-semantic"
type: spec
status: archived # draft | implementing | verifying | archived
created: "2026-06-05"
tags: [spec, proposal]
template_version: "1.0"
---

# HIVE-211: vault_ask — optional semantic retrieval

> **Naming**: file lives at `<repo>/specs/HIVE-211-vault-ask-semantic/proposal.md`.

## Why

<!-- from GitHub issue #211: vault_ask — optional semantic retrieval (vector RAG), provider-agnostic (Ollama/NaN). Inspired by benmaster82/Kwipu; take the lean subset (no graph). -->

Hive's only retrieval surface, `vault_search`, is **purely lexical** — substring/regex matching plus a heuristic `ranked` score. It cannot do semantic recall (synonyms, paraphrase) or cross-file synthesis: a question like *"what did I decide about X across all my notes?"* requires the agent to read many files itself. The competitive scan of [benmaster82/Kwipu](https://github.com/benmaster82/Kwipu) (a local Graph RAG engine over Obsidian-style markdown) confirmed this is Hive's biggest gap and that a **lean** subset of its approach is worth adopting. This spec adds an **optional** `vault_ask` tool — ask in natural language, get a synthesized, **source-cited** answer — without importing Kwipu's expensive property-graph machinery. GitHub issue: [#211](https://github.com/mlorentedev/hive/issues/211).

## What

After **Stage 1**, an MCP client can call `vault_ask(question)` and receive an LLM-synthesized answer grounded in vault content, **with cited source files** — when (and only when) a semantic backend is configured.

- **Vector RAG, no graph.** Retrieval = embeddings + vector similarity over vault chunks; synthesis = an LLM with an anti-hallucination, cite-your-sources prompt. The property graph (LLM-per-chunk triple extraction = hours-long builds + fragile storage) is **out of scope** — embeddings are the cheap part, the graph is the expensive part.
- **Provider-agnostic.** One OpenAI-compatible client with a configurable `base_url` serves both embeddings and synthesis across Ollama (`localhost:11434/v1`), NaN (`api.nan.builders/v1`), and OpenRouter. Reuses the planned generalization of `OpenRouterClient` into a parameterized OpenAI-compatible client. Config: `HIVE_EMBED_BASE_URL` / `HIVE_EMBED_MODEL` / `HIVE_EMBED_API_KEY`.
- **Optional / non-breaking (load-bearing).** Heavy deps ship as an optional extra (`pip install hive[semantic]`), lazy-imported. With no backend configured or the extra missing, `vault_ask` returns a clear "disabled — how to enable" message and **never breaks minimal installs or other tools**. Zero cost when disabled (no index, no embed-on-write).
- **Index lifecycle.** Lazy build on first use (or explicit `vault_reindex`); incremental re-embed hooked into `vault_write`/`vault_patch` **only if the index exists**. Persisted server-side in the daemon (sqlite-vec / numpy).

## Out of scope

- **Property graph / LLM triple extraction** — deferred to Stage 2, gated on observed recall gaps (see Risks).
- **Any mandatory heavy dependency** — `hive[semantic]` is an opt-in extra; base install must be unaffected.
- **Re-ranking or replacing the existing `vault_search` modes** — `vault_ask` is additive.
- **Structural wikilink/frontmatter graph edges** — Stage 2.
- **Automatic runtime fallback between embed providers.** Config selects **one** embed backend (`HIVE_EMBED_BASE_URL`). Unlike `delegate_task`'s stateless chat cost-ladder, embeddings persist an index keyed to a single model's dimensionality (NaN `qwen3-embedding` = 4096-dim vs Ollama `nomic-embed-text` = 768-dim) — a transparent cross-provider fallback would query a 4096-dim index with a 768-dim vector (mathematically invalid). Switching providers is a config change that triggers an index **rebuild** (model-id mismatch guard), never a silent runtime fallback. Default config = NaN; Ollama is the local/private alternative.

## Risks / open questions

- **[RESOLVED 2026-06-05] NaN exposes `/embeddings`.** Confirmed live against `api.nan.builders/v1`: model `qwen3-embedding`, **4096-dim** vectors, standard OpenAI-shaped `{data:[{embedding}], usage}` response (HTTP 200). **NaN-only is viable** for both embeddings and synthesis (`deepseek-v4-flash` / `qwen3.6` for chat). The provider-agnostic design is retained so Ollama (`nomic-embed-text`, 768-dim, local / free / private) stays a config-only alternative. **Dimensionality differs per provider (4096 vs 768)** → the index MUST record its embed-model id and rebuild on mismatch (binary-incompatible vectors otherwise).
- **Index cost & privacy.** First build embeds every chunk (N calls; cost depends on provider pricing + vault size — estimate before enabling on a large vault). Chunks + embeddings leave the machine for a remote provider (NaN) — acceptable only if the user already trusts that provider with vault content; document the trade-off vs. Kwipu's "no cloud".
- **Dependency weight.** The vector-store/embedding libs must stay behind the `[semantic]` extra and be lazy-imported so a base `pip install hive` pulls nothing new and no import fails when the extra is absent.
- **Index staleness / corruption.** Embedding-model mismatch must be detected (store the model id with the index; rebuild on mismatch). Incremental re-embed must stay consistent with the git-committed file state.
- **Stage-2 gate (instrumentation).** Log retriever attribution + recall (which chunks/files retrieved, did the cited source answer, multi-hop misses). Observe for a few weeks; only escalate to the structural graph if vector recall demonstrably fails on recurring multi-hop/entity questions.

## Acceptance criteria

Observable outcomes. Each must be testable.

- [ ] AC1 — `vault_ask(question)` returns a synthesized answer with cited source file(s) when an embed backend is configured.
- [ ] AC2 — with NO embed backend configured (or the `[semantic]` extra absent), `vault_ask` returns a clear "disabled / how to enable" message and **no other tool or import breaks**.
- [ ] AC3 — works against both a local Ollama endpoint and a remote OpenAI-compatible endpoint (NaN) by config alone (no code change).
- [ ] AC4 — the index builds lazily and updates incrementally on `vault_write`/`vault_patch`; a disabled `vault_ask` adds zero overhead to existing tools (no embed-on-write, no startup build).
- [ ] AC5 — `vault_ask`'s JSON schema contains no `anyOf` (no `| None`); MCP tool-schema rules honored.
- [ ] AC6 — answers cite real files that exist in the vault (anti-hallucination: no invented sources).

## References

- Vault: `10_projects/hive/11-tasks.md` (backlog entry **HIVE-211**)
- GitHub issue: [#211](https://github.com/mlorentedev/hive/issues/211)
- Competitive scan: [benmaster82/Kwipu](https://github.com/benmaster82/Kwipu) (Graph RAG over Obsidian; we take the lean vector-only subset)
- Related: worker cost-ladder + `BudgetTracker` (resilience parity), planned OpenAI-compatible client generalization (NaN tier)
- Related patterns: `00_meta/patterns/pattern-mcp-tool-design.md` (the `| None` ban), `00_meta/patterns/config-defaults.md`
