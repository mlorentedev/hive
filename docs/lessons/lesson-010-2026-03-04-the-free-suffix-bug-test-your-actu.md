---
id: lesson-010-2026-03-04-the-free-suffix-bug-test-your-actu
type: lesson
status: active
created: "2026-05-01"
owner: manu
tags: [hive, lesson]
---

# 2026-03-04: The `:free` suffix bug — test your actual infrastructure

- **Context:** OpenRouter paid tier smoke test was failing. The model ID being sent included the `:free` suffix (e.g., `qwen/qwen3-coder:free`), which routed to the free tier instead of the paid model.
- **Root cause:** Configuration assumed the model string was used as-is, but the `:free` suffix is a routing hint, not part of the model name.
- **Lesson:** Smoke tests must exercise the actual production path. Unit tests with mocked responses would not have caught this — only a real HTTP call to OpenRouter revealed the suffix behavior.
