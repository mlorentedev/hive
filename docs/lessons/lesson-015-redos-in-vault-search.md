---
id: lesson-015-redos-in-vault-search
type: lesson
status: active
created: "2026-03-06"
owner: manu
tags: [hive, lesson]
---

# ReDoS in vault_search

- **Context:** `vault_search(use_regex=True)` compiles user-supplied regex with `re.compile()` and applies it to every line of every vault file
- **Root cause:** Python's `re` module has no backtracking limit. Pathological patterns like `(a+)+$` cause exponential time on non-matching lines
- **Fix:** Cap regex pattern length at 200 chars. Practical constraint: vault files are small markdown, per-line matching limits blast radius
- **Lesson:** Any tool that accepts regex from untrusted input needs a complexity gate — length limit at minimum, `re2` library for production systems
