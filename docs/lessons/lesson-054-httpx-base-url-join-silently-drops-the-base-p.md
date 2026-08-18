---
id: lesson-054-httpx-base-url-join-silently-drops-the-base-p
type: lesson
status: active
created: "2026-06-05"
owner: manu
tags: [hive, lesson, httpx, footgun, base-url, openai-compatible, HIVE-211]
---

# httpx `base_url` join silently drops the base path on absolute request paths

**Context:** HIVE-211 PR1 — generalizing `OpenRouterClient` into a provider-parameterized `OpenAICompatibleClient` (Ollama / NaN / OpenRouter; chat + embeddings). The original client set httpx `base_url="https://openrouter.ai"` (no path) and POSTed to the absolute path `/api/v1/chat/completions`.
**Problem:** httpx merges `base_url` + request URL with RFC-3986 join semantics (`httpx.URL(base).join(req)`), NOT string concatenation. An **absolute** request path (leading `/`) **replaces** any path of `base_url`: `base="https://host/api/v1"` + `.post("/chat/completions")` → `https://host/chat/completions` — the `/api/v1` is silently dropped. The old code only worked by accident because its `base_url` had no path. Naively reusing that shape with `base_url="https://api.nan.builders/v1"` would have hit `/chat/completions` (404) instead of `/v1/chat/completions` — and mocked unit tests would NOT catch it (they patch `_http.post` and never exercise URL joining).
**Solution:** Don't rely on httpx `base_url` join at all. Store `self._base_url = base_url.rstrip("/")` (the full prefix incl. version: NaN `…/v1`, OpenRouter `…/api/v1`, Ollama `…/v1`) and build full URLs explicitly: `self._http.post(f"{self._base_url}/chat/completions", …)` — the same convention the OpenAI SDK uses for `base_url`. Added `test_generate_posts_to_full_chat_completions_url` asserting the exact composed URL so the join behavior is pinned, not assumed.
**Tags:** `#httpx` `#footgun` `#base-url` `#openai-compatible` `#HIVE-211`
