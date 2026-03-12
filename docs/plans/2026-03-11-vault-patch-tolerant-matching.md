# vault_patch Tolerant Matching — Implementation Plan

**Goal:** Fix the broken read→patch workflow (Issue #52) by making `vault_patch` match text against the body (post-frontmatter) with whitespace-tolerant fallback, and provide diagnostic error messages on failure.

**Architecture:** Three-pass cascading match — exact on full file (current), exact on body-only, whitespace-normalized on body. A new `_match_and_replace()` helper in `_helpers.py` encapsulates the logic. On total failure, `difflib.SequenceMatcher` provides similarity diagnostics.

**Tech Stack:** Python 3.12+, difflib (stdlib), pytest, mypy --strict

---

### Task 1: Add `_match_and_replace` helper with exact body-only matching

**Files:**
- Modify: `src/hive/_helpers.py`
- Test: `tests/test_helpers.py`

**Step 1: Write failing tests**

In `tests/test_helpers.py` (create if needed), add:

```python
import pytest
from hive._helpers import _match_and_replace


_FM = "---\nid: test\ntype: note\nstatus: active\n---\n\n"


class TestMatchAndReplace:
    """Tests for _match_and_replace cascading logic."""

    def test_exact_match_full_file(self) -> None:
        """Pass 1: exact match on full content including frontmatter works."""
        content = _FM + "# Title\n\nHello world\n"
        result = _match_and_replace(content, "Hello world", "Goodbye world")
        assert result is not None
        ok, new_content = result
        assert ok
        assert "Goodbye world" in new_content
        assert new_content.startswith("---")

    def test_exact_match_body_only(self) -> None:
        """Pass 2: old_text matches body but NOT full file (frontmatter omitted by caller)."""
        content = _FM + "# Title\n\nHello world\n"
        # Simulate LLM copying just body text (no frontmatter)
        old_text = "# Title\n\nHello world\n"
        result = _match_and_replace(content, old_text, "# Title\n\nGoodbye world\n")
        assert result is not None
        ok, new_content = result
        assert ok
        assert new_content.startswith("---")
        assert "Goodbye world" in new_content

    def test_whitespace_normalized_match(self) -> None:
        """Pass 3: trailing whitespace differences tolerated."""
        content = _FM + "# Title\n\n| A | B |   \n|---|---|\n| 1 | 2 |  \n"
        # LLM stripped trailing spaces
        old_text = "| A | B |\n|---|---|\n| 1 | 2 |"
        result = _match_and_replace(content, old_text, "| A | B | C |\n|---|---|---|\n| 1 | 2 | 3 |")
        assert result is not None
        ok, new_content = result
        assert ok
        assert "| C |" in new_content

    def test_ambiguous_returns_error(self) -> None:
        """Ambiguous match (>1 occurrence) returns error tuple."""
        content = _FM + "word\nword\n"
        result = _match_and_replace(content, "word", "replacement")
        assert result is not None
        ok, msg = result
        assert not ok
        assert "ambiguous" in msg.lower()

    def test_not_found_returns_diagnostic(self) -> None:
        """Total miss returns diagnostic with similarity hint."""
        content = _FM + "# Title\n\nHello world\n"
        result = _match_and_replace(content, "completely different text", "replacement")
        assert result is not None
        ok, msg = result
        assert not ok
        assert "not found" in msg.lower()

    def test_no_frontmatter_file(self) -> None:
        """Files without frontmatter still work (pass 1 or pass 3)."""
        content = "# Plain file\n\nHello world\n"
        result = _match_and_replace(content, "Hello world", "Goodbye world")
        assert result is not None
        ok, new_content = result
        assert ok
        assert "Goodbye world" in new_content

    def test_similarity_hint_on_close_miss(self) -> None:
        """When old_text is close but not exact, error includes similarity %."""
        content = _FM + "Hello world\n"
        result = _match_and_replace(content, "Hello worlds", "replacement")
        assert result is not None
        ok, msg = result
        assert not ok
        assert "%" in msg  # similarity percentage shown
```

**Step 2: Run tests to verify failure**

Run: `make test` or `pytest tests/test_helpers.py::TestMatchAndReplace -v`
Expected: FAIL — `_match_and_replace` does not exist yet.

**Step 3: Implement `_match_and_replace`**

In `src/hive/_helpers.py`, add:

```python
import difflib

from hive.frontmatter import extract_body


def _match_and_replace(
    content: str,
    old_text: str,
    new_text: str,
) -> tuple[bool, str]:
    """Cascading match-and-replace: exact → body-only → whitespace-normalized.

    Returns (True, new_content) on success, (False, error_message) on failure.
    """
    # ── Pass 1: Exact match on full content ──
    count = content.count(old_text)
    if count == 1:
        return True, content.replace(old_text, new_text, 1)
    if count > 1:
        return False, f"Ambiguous: old_text appears {count} times."

    # ── Pass 2: Exact match on body (post-frontmatter) ──
    body = extract_body(content)
    frontmatter = content[: len(content) - len(body)] if body != content else ""

    count = body.count(old_text)
    if count == 1:
        return True, frontmatter + body.replace(old_text, new_text, 1)
    if count > 1:
        return False, f"Ambiguous: old_text appears {count} times."

    # ── Pass 3: Whitespace-normalized match on body ──
    def _normalize(text: str) -> str:
        return "\n".join(line.rstrip() for line in text.splitlines())

    norm_body = _normalize(body)
    norm_old = _normalize(old_text)

    if norm_old:
        count = norm_body.count(norm_old)
        if count == 1:
            return True, frontmatter + norm_body.replace(norm_old, new_text, 1)
        if count > 1:
            return False, f"Ambiguous: old_text appears {count} times (after whitespace normalization)."

    # ── Diagnostic: similarity hint ──
    best_ratio = 0.0
    search_in = norm_body if norm_body else body
    if norm_old and len(norm_old) <= len(search_in):
        matcher = difflib.SequenceMatcher(None, norm_old, "")
        # Slide a window roughly the size of old_text across the body
        window = len(norm_old)
        lines = search_in.splitlines()
        old_lines = norm_old.splitlines()
        n_old = len(old_lines)
        for i in range(max(1, len(lines) - n_old + 1)):
            chunk = "\n".join(lines[i : i + n_old])
            matcher.set_seq2(chunk)
            ratio = matcher.ratio()
            if ratio > best_ratio:
                best_ratio = ratio

    pct = int(best_ratio * 100)
    hint = f" Best match: {pct}% similar." if pct > 40 else ""
    return False, f"old_text not found.{hint}"
```

**Step 4: Run tests to verify pass**

Run: `pytest tests/test_helpers.py::TestMatchAndReplace -v`
Expected: all 8 tests PASS.

**Step 5: Run full suite + lint**

Run: `make check`
Expected: lint clean, mypy clean, all tests pass.

---

### Task 2: Wire `_match_and_replace` into `vault_patch`

**Files:**
- Modify: `src/hive/_vault_write.py`
- Test: `tests/test_server.py`

**Step 1: Write failing tests for new vault_patch behavior**

Add to `TestVaultPatch` in `tests/test_server.py`:

```python
async def test_patch_matches_body_without_frontmatter(self, git_vault: Path) -> None:
    """old_text from body (no frontmatter) matches correctly."""
    mcp = create_server(vault_path=git_vault)
    # old_text is body content only — no frontmatter prefix
    result = _text(await mcp.call_tool(
        "vault_patch",
        {
            "project": "testproject",
            "path": "11-tasks.md",
            "old_text": "- [ ] Task one",
            "new_text": "- [x] Task one (done)",
        },
    ))
    assert "1 patch" in result.lower()
    content = (git_vault / "10_projects" / "testproject" / "11-tasks.md").read_text()
    assert "- [x] Task one (done)" in content
    # Frontmatter preserved
    assert content.startswith("---")

async def test_patch_tolerates_trailing_whitespace(self, git_vault: Path) -> None:
    """Trailing whitespace differences between old_text and file are tolerated."""
    tasks = git_vault / "10_projects" / "testproject" / "11-tasks.md"
    # Add trailing spaces to the file
    raw = tasks.read_text()
    raw = raw.replace("- [ ] Task one", "- [ ] Task one   ")
    tasks.write_text(raw)
    # Commit so git is clean
    import subprocess
    subprocess.run(["git", "add", "."], cwd=git_vault, capture_output=True)
    subprocess.run(["git", "commit", "-m", "ws"], cwd=git_vault, capture_output=True)

    mcp = create_server(vault_path=git_vault)
    # old_text has NO trailing spaces (LLM stripped them)
    result = _text(await mcp.call_tool(
        "vault_patch",
        {
            "project": "testproject",
            "path": "11-tasks.md",
            "old_text": "- [ ] Task one",
            "new_text": "- [x] Task one",
        },
    ))
    assert "1 patch" in result.lower()

async def test_patch_not_found_shows_similarity(self, git_vault: Path) -> None:
    """When old_text is close but wrong, error includes similarity hint."""
    mcp = create_server(vault_path=git_vault)
    result = _text(await mcp.call_tool(
        "vault_patch",
        {
            "project": "testproject",
            "path": "11-tasks.md",
            "old_text": "- [ ] Task ones",  # typo
            "new_text": "- [x] Task one",
        },
    ))
    assert "not found" in result.lower()
    assert "%" in result  # similarity hint

async def test_patch_roundtrip_query_then_patch(self, git_vault: Path) -> None:
    """The real-world workflow: vault_query output → vault_patch old_text."""
    mcp = create_server(vault_path=git_vault)
    # Step 1: Read via vault_query
    query_result = _text(await mcp.call_tool(
        "vault_query",
        {"project": "testproject", "section": "tasks"},
    ))
    # Step 2: Extract a line from the query result, use as old_text
    # vault_query returns full content including frontmatter,
    # but the LLM might only copy body lines
    assert "- [ ] Task one" in query_result
    result = _text(await mcp.call_tool(
        "vault_patch",
        {
            "project": "testproject",
            "path": "11-tasks.md",
            "old_text": "- [ ] Task one",
            "new_text": "- [x] Task one (completed)",
        },
    ))
    assert "1 patch" in result.lower()
```

**Step 2: Run new tests to verify they fail (the ws one should fail, others may pass already)**

Run: `pytest tests/test_server.py::TestVaultPatch::test_patch_tolerates_trailing_whitespace -v`
Expected: FAIL — current code does byte-exact match.

**Step 3: Modify `vault_patch` to use `_match_and_replace`**

In `src/hive/_vault_write.py`, replace the match-and-replace loop (lines 252-281) with:

```python
from hive._helpers import _match_and_replace

# Replace the for-loop that does working.count(old)/working.replace(old, new, 1)
# with calls to _match_and_replace:

working = content
for i, patch in enumerate(patch_list, 1):
    if "old_text" not in patch or "new_text" not in patch:
        label = f"patch {i}: " if len(patch_list) > 1 else ""
        return track(
            ctx, "vault_patch",
            f"{label}Each patch must have 'old_text' and 'new_text' keys.",
            project,
        )
    ok, result = _match_and_replace(working, patch["old_text"], patch["new_text"])
    if not ok:
        label = f"patch {i}: " if len(patch_list) > 1 else ""
        return track(ctx, "vault_patch", f"{label}{result}", project)
    working = result
```

Note: For multi-patch, after the first patch modifies `working`, subsequent patches run against the already-modified content. The frontmatter extraction in `_match_and_replace` handles this correctly because frontmatter is preserved in the working copy.

**Step 4: Run all vault_patch tests**

Run: `pytest tests/test_server.py::TestVaultPatch -v`
Expected: ALL pass (existing + new).

**Step 5: Run full suite**

Run: `make check`
Expected: lint + mypy + all tests pass.

---

### Task 3: Verify and commit

**Step 1: Run `make check` to confirm everything is clean**

Run: `make check`
Expected: lint clean, mypy clean, all tests pass.

**Step 2: Verify the ambiguous-match path is not broken**

The existing tests cover this (`test_single_patch_ambiguous`, `test_multi_patch_ambiguous_aborts_all`). The new `_match_and_replace` preserves the ambiguity check at each pass level. Confirm these still pass in the full run.

**Step 3: Commit**

```bash
git add src/hive/_helpers.py src/hive/_vault_write.py tests/test_helpers.py tests/test_server.py
git commit -m "fix(vault_patch): tolerant matching for read→patch workflow (#52)

vault_patch now uses three-pass cascading match:
1. Exact match on full file (existing behavior)
2. Exact match on body only (post-frontmatter)
3. Whitespace-normalized match on body (rstrip per line)

On failure, error message includes similarity % diagnostic.

Closes #52"
```
