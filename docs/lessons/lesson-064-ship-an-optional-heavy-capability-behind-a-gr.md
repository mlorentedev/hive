---
id: lesson-064-ship-an-optional-heavy-capability-behind-a-gr
type: lesson
status: active
created: "2026-06-05"
owner: manu
tags: [hive, lesson, mcp, optional-dependency, graceful-degradation, semantic-search, config, HIVE-211]
---

# Ship an optional heavy capability behind a graceful-degradation gate, never a hard dependency

**Context:** HIVE-211 added `vault_ask` — semantic Q&A over the vault (embeddings + retrieval + LLM synthesis). It needs numpy + an embeddings backend, neither of which the base MCP server should require. Shipped across 5 PRs (v1.37.0–v1.41.0): OpenAI-compatible client (chat+embeddings), disabled-by-default tool skeleton, retrieval engine (`_semantic.py`), cited synthesis, incremental re-embed hook.
**Problem:** Bolting a heavy, optional subsystem onto a tool that every client loads risks (a) forcing numpy/model deps on users who never ask a semantic question, (b) crashing or erroring the whole tool registration when the backend or extra is absent, and (c) silently paying indexing cost even when the feature is off. A naive "import numpy at module load + register the tool unconditionally" would regress install size and startup for 100% of users to serve a feature used by few.
**Solution:** Gate the capability at three layers. (1) **Optional dependency** — heavy deps live in a `[semantic]` extra (`numpy>=1.26`), no top-level import; the base install pulls nothing new. (2) **Disabled by default** — the tool registers always but returns a clear *how-to-enable* message (never raises) until `HIVE_EMBED_BASE_URL` is set AND the extra is installed; both "backend missing" and "extra missing" are distinct, tested branches. (3) **Zero overhead when off** — the index is lazy-built on first real use, and the incremental re-embed hook on `vault_write`/`vault_patch` is a no-op when no index exists (AC4). Schema stays `anyOf`-free (`question: str = ""`). Two design corollaries worth remembering: persist vectors as `numpy.save/load(allow_pickle=False)` + JSON, **not** pickle (arbitrary-code-execution risk a security hook flagged); and let config select **one** embed backend with **no cross-provider runtime fallback** — a 4096-dim vs 768-dim mismatch makes silent fallback unsafe, so switching providers is a config change that forces an index rebuild.
**Why:** "Optional" must mean optional along every axis — install weight, import time, registration safety, and runtime cost — or it is a hard dependency wearing a flag. Making "disabled" the graceful default (clear message, never raise) means the tool is always present for discovery yet costs nothing until explicitly turned on. Candidate cross-project pattern: *optional heavy capability behind a graceful-degradation gate*.
**Tags:** `#mcp` `#optional-dependency` `#graceful-degradation` `#semantic-search` `#config` `#HIVE-211`
