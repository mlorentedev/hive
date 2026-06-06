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
- [ ] AC2 (disabled-default never breaks) -> PR2 (vault_ask skeleton)
- [ ] AC3 (Ollama <-> NaN by config) -> PR2/PR5
- [ ] AC4 (lazy/incremental index; zero overhead when disabled) -> PR5
- [ ] AC5 (no anyOf in schema) -> PR2 (when the tool is registered)
- [ ] AC6 (cites real, existing files) -> PR4

## Test status (PR1)

- Targeted: `uv run pytest tests/test_clients.py -q` -> **34 passed** (15 original + 19 new).
- ruff `src/ tests/`: clean. `mypy --strict src/hive/clients.py`: clean.
- Full suite (`uv run pytest -q`): **4 failed, 707 passed, 19 skipped, 62 deselected** (11m11s). All 4 failures are the documented pre-existing dev-box failures (#212): `test_bounded_call::test_windows_subprocess_terminated_reaches_descendants`, `test_daemon::test_client_degrades_in_process_when_daemon_dies_mid_session`, `test_lock_eviction::TestLockEvictionTracker::test_multiple_records_ordered`, `TestVaultHealthRuntime::test_runtime_block_includes_lock_eviction` — **none touch `clients.py`**. The visible `count_30d: 15 == 0` confirms #212's root cause (fixtures read real `~/.local/share/hive` state). **Zero new regressions.** CI (Linux + Windows) is green.

## Decisions made during implementation

- NaN `/embeddings` available? -> **YES** — `qwen3-embedding`, 4096-dim, OpenAI-shaped (probed 2026-06-05). NaN-only viable; Ollama stays a config alternative.
- Vector store choice -> **numpy + pickle** (in-memory dot-product; no native wheel behind `[semantic]`). Settled for PR3.
- Provider fallback -> **config selects ONE embed backend; no cross-provider runtime fallback** (4096 vs 768 dim mismatch makes it unsafe — see proposal "Out of scope"). Switching = config change -> index rebuild.
- Client design -> build full URLs from a stored `base_url` (which includes the version prefix), NOT httpx's RFC-3986 `base_url` join (which silently drops a base path on absolute request paths). `OpenRouterClient` kept as a thin subclass for backward compat.
- Chunking strategy -> <to fill: PR3>

## Promotion candidates

- [x] Lesson for `docs/lessons.md`? **Captured in-session (2026-06-05)** — "httpx `base_url` join silently drops the base path on absolute request paths".
- [ ] ADR-worthy (optional-dep + provider-agnostic semantic subsystem)? <likely yes — record the optional/graceful-degradation contract when PR2 lands>
- [ ] New pattern for `00_meta/patterns/`? <maybe — "optional heavy capability behind a graceful-degradation gate" if it recurs>

## Archive checklist

- [ ] `proposal.md` frontmatter set to `status: archived`
- [ ] Folder moved to `specs/archive/HIVE-211-vault-ask-semantic/`
- [ ] Backlog entry in vault `11-tasks.md` ticked with PR link
- [ ] Stage 2 decision recorded (escalate / defer / drop) based on instrumentation
