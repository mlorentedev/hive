---
id: lesson-026-mcp-tool-parameter-names-must-match-llm-menta
type: lesson
status: active
created: "2026-03-15"
owner: manu
tags: [hive, lesson, mcp, naming, llm-ergonomics, dx]
---

# MCP tool parameter names must match LLM mental models

**Context:** vault_patch tool had parameters named `old_text` and `new_text`. LLMs (Claude, Gemini) consistently hallucinated `find` and `replace` instead, causing Pydantic validation failures at runtime.
**Problem:** The parameter names were technically correct but didn't match the natural mental model that LLMs (and humans) have for text substitution operations. Every vault_patch call risked a validation error from the LLM guessing the "obvious" names.
**Solution:** Renamed `old_text`→`find` and `new_text`→`replace` across the entire codebase (7 files). Breaking API change, but eliminated an entire class of runtime failures. Evaluated adding aliases for backward compatibility — rejected as over-engineering since no external consumers exist yet.
**Why:** MCP tools are called by LLMs, not humans typing exact names. Parameter naming is a DX/UX decision that directly affects tool reliability. Shorter, more idiomatic names reduce schema misreads. Design rule: when naming MCP tool parameters, prefer the term an LLM would guess first.
**Tags:** `#mcp` `#naming` `#llm-ergonomics` `#dx`
