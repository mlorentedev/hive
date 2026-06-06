---
tags: [spec, verification]
created: "2026-06-05"
---

# Verification - HIVE-211-vault-ask-semantic (Stage 1)

> Multi-PR feature. This file accumulates evidence per PR. Spec is `implementing`.

## PR1 — OpenAI-compatible client (chat + embeddings) [foundation]

PR1 delivers the provider-parameterized client that powers everything downstream; it
adds **no** MCP tool yet, so the feature ACs (AC1–AC6, all about `vault_ask`) are
**deferred to PR2+** and tracked below. PR1's own bar is: the client works for chat
**and** embeddings against any OpenAI-compatible `base_url`, and nothing regresses.

- [x] Provider-parameterized chat (`generate`) against arbitrary `base_url` -> `TestOpenAICompatibleChat`
- [x] Full-URL building (no httpx `base_url`-join footgun) -> `test_generate_posts_to_full_chat_completions_url`
- [x] New `embed()` + `EmbedResponse` -> `TestOpenAICompatibleEmbed::test_embed_success`
- [x] Vector<->text alignment under out-of-order provider responses -> `test_embed_preserves_input_order_when_provider_returns_out_of_order`
- [x] `embed()` error parity (ReadTimeout/Connect/HTTP -> ConnectionError/RuntimeError) -> `test_embed_read_timeout` / `test_embed_connection_error` / `test_embed_http_error`
- [x] No-auth-header for keyless local endpoints (Ollama) -> `test_no_auth_header_when_no_api_key`
- [x] `OpenRouterClient` is a drop-in subclass -> `TestOpenRouterBackwardCompat` (+ all 15 original OpenRouter tests untouched)
- [x] NaN `/embeddings` viability gate -> live probe: `qwen3-embedding`, 4096-dim, OpenAI-shaped, HTTP 200

### Feature ACs (deferred — not satisfied by PR1)

- [ ] AC1 (answer with cited sources) -> PR4 (retrieval -> synthesis)
- [x] AC2 (disabled-default never breaks) -> **PR2** `TestVaultAskDisabledByDefault`
- [ ] AC3 (Ollama <-> NaN by config) -> PR5 (needs the index/retrieval path)
- [ ] AC4 (lazy/incremental index; zero overhead when disabled) -> PR5
- [x] AC5 (no anyOf in schema) -> **PR2** `test_vault_ask_schema_has_no_anyof` + repo-wide `TestSchemaClean`
- [ ] AC6 (cites real, existing files) -> PR4

## Test status (PR1)

- Targeted: `uv run pytest tests/test_clients.py -q` -> **34 passed** (15 original + 19 new).
- ruff `src/ tests/`: clean. `mypy --strict src/hive/clients.py`: clean.
- Full suite (`uv run pytest -q`): **4 failed, 707 passed, 19 skipped, 62 deselected** (11m11s). All 4 failures are the documented pre-existing dev-box failures (#212): `test_bounded_call::test_windows_subprocess_terminated_reaches_descendants`, `test_daemon::test_client_degrades_in_process_when_daemon_dies_mid_session`, `test_lock_eviction::TestLockEvictionTracker::test_multiple_records_ordered`, `TestVaultHealthRuntime::test_runtime_block_includes_lock_eviction` — **none touch `clients.py`**. The visible `count_30d: 15 == 0` confirms #212's root cause (fixtures read real `~/.local/share/hive` state). **Zero new regressions.** CI (Linux + Windows) is green.

## PR2 — vault_ask tool (disabled-by-default skeleton)

PR2 registers the `vault_ask` MCP tool and the graceful-degradation gate. No
index/retrieval yet; the heavy deps stay behind the new `[semantic]` extra.

- [x] `vault_ask` registered; disabled by default (no `HIVE_EMBED_BASE_URL`) returns a clear how-to-enable message, never raises -> `test_disabled_default_is_graceful`
- [x] Other tools + import unaffected by registration -> `test_tool_registered_and_others_intact`
- [x] Backend set but `[semantic]` extra absent -> disabled + install hint (numpy genuinely absent in base env, real branch) -> `test_disabled_when_backend_set_but_extra_missing`
- [x] Enabled branch (backend + extra) returns pending-index placeholder -> `test_backend_ready_returns_pending_index`
- [x] AC5 schema has no `anyOf` (`question: str = ""`) -> `test_vault_ask_schema_has_no_anyof`
- [x] `[semantic]` optional extra declared (`numpy>=1.26`); base install pulls nothing new, no import at module load

### Test status (PR2)

- Targeted: `uv run pytest tests/test_vault_ask.py -q` -> **5 passed**. `tests/test_tool_param_aliases.py` (repo-wide schema/docstring) -> green.
- ruff `src/ tests/`: clean. `mypy --strict src/`: only the 5 pre-existing `_deadline.py` POSIX-on-Windows errors (Linux CI clean); numpy resolved via a `[[tool.mypy.overrides]]` ignore_missing_imports.
- Full suite (`uv run pytest -q`): **4 failed, 712 passed, 19 skipped, 62 deselected** (10m47s). The 4 failures are identical to PR1's — the documented #212 pre-existing dev-box failures (`test_bounded_call`, `test_daemon`, `test_lock_eviction`, `TestVaultHealthRuntime`); none touch the new code. Pass count rose 707 -> 712 (the 5 new `test_vault_ask.py` tests). **Zero new regressions.** CI (Linux + Windows) is green.

## PR3 — semantic retrieval engine

PR3 delivers the full retrieval pipeline: hybrid markdown chunker (`_semantic.py`), `VaultIndex`
(lazy-build, asyncio.Lock, numpy cosine similarity, JSON+npy persistence keyed by
`sha256(vault)[:12]+model_slug`, mismatch-rebuild), and `vault_ask` rewritten as native async
with seam functions for testability. 16 new tests.

- [x] Hybrid markdown chunker (structural-by-heading + size-cap with overlap) -> `test_semantic.py::TestChunkMarkdown`
- [x] `VaultIndex` build, persist, reload, mismatch-rebuild -> `test_semantic.py::TestVaultIndex`
- [x] `vault_ask` with backend + extra returns retrieved chunks -> `TestVaultAskRetrieval::test_retrieval_returns_chunks_from_vault`
- [x] Empty question rejected gracefully -> `TestVaultAskRetrieval::test_empty_question_rejected_gracefully`
- [x] Output cites .md sources -> `TestVaultAskRetrieval::test_retrieval_output_cites_sources`
- [x] numpy in dev extras; "extra absent" branch via monkeypatch -> `TestVaultAskDisabledByDefault::test_disabled_when_backend_set_but_extra_missing`

### Test status (PR3)

- Targeted: `uv run pytest tests/test_vault_ask.py tests/test_semantic.py -q` -> **16 new tests pass**.
- Full suite: **4 failed, 730 passed, 19 skipped, 62 deselected** (14m10s). Same 4 pre-existing dev-box failures. Zero new regressions. CI green.

## PR4 — LLM synthesis (AC1, AC6)

PR4 wires the synthesis LLM on top of retrieval. New seam `_build_synth_client`, synthesis prompt
builder `_build_synthesis_prompt` (anti-hallucination: cite-only-shown-sources), `_synthesize`
(async, falls back to formatted retrieval on any error), and `HIVE_SYNTH_MODEL` config. 5 new tests.

- [x] Synthesis returns LLM answer when `synth_model` is set (AC1) -> `TestVaultAskSynthesis::test_synthesis_returns_synthesized_answer`
- [x] Answer cites .md source (AC6) -> `TestVaultAskSynthesis::test_synthesis_cites_vault_source`
- [x] Synthesis prompt includes retrieved vault content -> `TestVaultAskSynthesis::test_synthesis_prompt_includes_chunk_context`
- [x] No `synth_model` → formatted retrieval only (no LLM call) -> `TestVaultAskSynthesis::test_no_synth_model_returns_retrieval_only`
- [x] generate() failure → graceful fallback, no crash -> `TestVaultAskSynthesis::test_synthesis_graceful_on_llm_error`

### Test status (PR4)

- Targeted: `uv run pytest tests/test_vault_ask.py -q` -> **12 passed** (7 old + 5 new). ruff + mypy clean.
- Full suite: pending CI.

## Decisions made during implementation

- NaN `/embeddings` available? -> **YES** — `qwen3-embedding`, 4096-dim, OpenAI-shaped (probed 2026-06-05). NaN-only viable; Ollama stays a config alternative.
- Vector store choice -> **numpy + JSON+npy** (`numpy.save/load(allow_pickle=False)`) — NOT pickle (security hook flagged arbitrary code execution). Settled for PR3.
- Provider fallback -> **config selects ONE embed backend; no cross-provider runtime fallback** (4096 vs 768 dim mismatch makes it unsafe — see proposal "Out of scope"). Switching = config change -> index rebuild.
- Client design -> build full URLs from a stored `base_url` (which includes the version prefix), NOT httpx's RFC-3986 `base_url` join (which silently drops a base path on absolute request paths). `OpenRouterClient` kept as a thin subclass for backward compat.
- Chunking strategy -> **hybrid** (structural-by-H1/H2/H3 heading split, then size-cap with overlap for long sections). Implemented in PR3 `_semantic.py::chunk_markdown`.
- Synthesis config -> **`HIVE_SYNTH_MODEL`** (empty = retrieval-only; same `base_url`/`api_key` as embed). `cast("OpenAICompatibleClient", ...)` for mypy; lazy import inside seam function avoids circular dep at module load.

## Promotion candidates

- [x] Lesson for `docs/lessons.md`? **Captured in-session (2026-06-05)** — "httpx `base_url` join silently drops the base path on absolute request paths".
- [ ] ADR-worthy (optional-dep + provider-agnostic semantic subsystem)? <likely yes — record the optional/graceful-degradation contract when PR2 lands>
- [ ] New pattern for `00_meta/patterns/`? <maybe — "optional heavy capability behind a graceful-degradation gate" if it recurs>

## Archive checklist

- [ ] `proposal.md` frontmatter set to `status: archived`
- [ ] Folder moved to `specs/archive/HIVE-211-vault-ask-semantic/`
- [ ] Backlog entry in vault `11-tasks.md` ticked with PR link
- [ ] Stage 2 decision recorded (escalate / defer / drop) based on instrumentation
