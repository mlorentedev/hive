---
id: lesson-046-contextvar-propagates-across-asyncio-to-threa
type: lesson
status: active
created: "2026-05-22"
owner: manu
tags: [hive, lesson, python, concurrency, async, contextvars, design]
---

# ContextVar propagates across asyncio.to_thread — use over explicit parameter passing for per-call state

**Context:** HIVE-115 PR-3: designing `bounded_call`'s process_registry parameter (a list[Popen] mutated by sync code in a worker thread, iterated by async code on deadline expiry). ADR-008 §1 originally specified explicit parameter passing because "asyncio.to_thread boundary makes contextvar propagation fragile across the async/sync layer".
**Problem:** Threading the registry through every git helper signature (`_git_commit(vault, paths, message, registry=...)`, `_git_commit_all(vault, message, registry=...)`, plus every wrapper that calls them) would be invasive across 8 callsites and break every existing test that passes positional args. ADR claim went unverified against current CPython docs.
**Solution:** CPython 3.9+ `asyncio.to_thread` uses `contextvars.copy_context()` internally — the same `ContextVar`-bound list reference is visible from both async land and the worker thread, and mutations land in the same object (not a copy). Adopted `_GIT_REGISTRY_CV: ContextVar[list[Popen] | None]` with default `None`. `tool_span` (async wrapper) sets the CV at entry, `_run_git` (sync, runs inside `asyncio.to_thread`) reads `_GIT_REGISTRY_CV.get()` and appends/removes. Zero signature changes at callsites. Verified by `tests/test_bounded_call.py::test_subprocess_terminated` killing a registered Popen from async land. Lesson: re-evaluate ADR claims about Python concurrency against current docs before designing workarounds; contextvar propagation across `to_thread` is documented and robust.
**Tags:** `#python` `#concurrency` `#async` `#contextvars` `#design`
