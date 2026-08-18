---
id: lesson-028-three-pass-cascading-match-for-vault-patch-to
type: lesson
status: active
created: "2026-03-11"
owner: manu
tags: [hive, lesson, vault-patch, tolerant-matching, llm-ergonomics, mcp]
---

# Three-pass cascading match for vault_patch tolerant text replacement

**Context:** `vault_patch` originally required `old_text` to match the file content byte-for-byte, including YAML frontmatter. LLMs typically copy snippets from `vault_query` output that has the frontmatter stripped or whitespace normalized, so the patch call would fail every time the body was sourced from a prior read (Issue #52).
**Problem:** Strict matching is brittle to two realistic LLM-induced drifts: (a) the frontmatter is missing because the LLM only copied the body, (b) trailing/leading whitespace in tables or code blocks got normalized during quoting. Either drift produced a hard error with no hint of how close the match was, breaking the read→patch workflow entirely.
**Solution:** `_match_and_replace()` in `_helpers.py` performs three cascading passes — (1) exact match on full file, (2) exact match on body only (strip frontmatter, then re-attach after replacement), (3) whitespace-normalized match on body (collapse runs of whitespace to a single space for comparison only). First successful pass wins. If all three miss, `difflib.SequenceMatcher` computes a similarity percentage and the error message includes the closest near-match excerpt — turning a dead-end "not found" into an actionable diagnostic.
**Why:** Cascading from strict→loose preserves correctness when the LLM gets the text right, while tolerating the common failure modes. Returning similarity diagnostics on total miss converts a UX dead end into a debugging signal: the LLM can see how close it got and adjust. `replace_body` mode was evaluated as an alternative and discarded — tolerant matching covers the same use cases without adding a second tool surface.
**Tags:** `#vault-patch` `#tolerant-matching` `#llm-ergonomics` `#mcp`
