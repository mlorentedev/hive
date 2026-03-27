# Hierarchical Scope Support for 50_work

**Goal:** Enable Hive MCP to navigate and operate on hierarchical vault scopes (like `50_work/`) where entities live at arbitrary depth, not just the first level.

**Architecture:** Add `"work": "50_work"` to default scopes. Change `_resolve_project_dir` to use breadth-first recursive search within any scope (not just flat `scope_dir/slug`). Add scope filter to `vault_search`. Add duplicate-name detection to `vault_health`. Guard `vault_write`/`vault_patch` against creating entities in ambiguous locations within hierarchical scopes.

**Tech Stack:** Python 3.12+, pytest, FastMCP

---

### Task 1: Add `work` to default scopes in config

**Files:**
- Modify: `src/hive/config.py`
- Modify: `src/hive/_helpers.py`

**Step 1: Update default scopes**

In `src/hive/config.py` line 22-24, change:

```python
vault_scopes: dict[str, str] = Field(
    default={"projects": "10_projects", "meta": "00_meta"},
)
```

to:

```python
vault_scopes: dict[str, str] = Field(
    default={"projects": "10_projects", "meta": "00_meta", "work": "50_work"},
)
```

In `src/hive/_helpers.py` line 42, change:

```python
_DEFAULT_SCOPES: dict[str, str] = {"projects": "10_projects", "meta": "00_meta"}
```

to:

```python
_DEFAULT_SCOPES: dict[str, str] = {"projects": "10_projects", "meta": "00_meta", "work": "50_work"}
```

**Step 2: Run tests to check nothing breaks**

Run: `make test`
Expected: All existing tests pass (50_work dir doesn't exist in mock_vault, so it's silently skipped).

**Step 3: Commit**

```bash
git add src/hive/config.py src/hive/_helpers.py
git commit -m "feat: add work scope (50_work) to default vault_scopes"
```

---

### Task 2: Recursive BFS resolution in `_resolve_project_dir`

**Files:**
- Modify: `src/hive/_helpers.py`
- Test: `tests/test_helpers.py`

**Step 1: Write failing tests**

Add to `tests/test_helpers.py`:

```python
from hive._helpers import _resolve_project_dir


class TestResolveProjectDir:
    """Tests for _resolve_project_dir with hierarchical scopes."""

    def test_flat_scope_resolves(self, mock_vault: Path) -> None:
        """Standard flat scope: 10_projects/testproject resolves."""
        result = _resolve_project_dir(mock_vault, "testproject")
        assert result is not None
        assert result[0] == mock_vault / "10_projects" / "testproject"
        assert result[1] == "projects"

    def test_explicit_scope_flat(self, mock_vault: Path) -> None:
        """Explicit scope:slug resolves for flat scopes."""
        result = _resolve_project_dir(mock_vault, "projects:testproject")
        assert result is not None
        assert result[0] == mock_vault / "10_projects" / "testproject"

    def test_hierarchical_scope_resolves_nested(self, tmp_path: Path) -> None:
        """BFS finds entity nested under a category in a hierarchical scope."""
        # Setup: 50_work/20-products/hydra3d-plus/
        (tmp_path / "50_work" / "20-products" / "hydra3d-plus").mkdir(parents=True)
        scopes = {"work": "50_work"}
        result = _resolve_project_dir(tmp_path, "work:hydra3d-plus", scopes)
        assert result is not None
        assert result[0] == tmp_path / "50_work" / "20-products" / "hydra3d-plus"
        assert result[1] == "work"

    def test_hierarchical_bfs_shallowest_wins(self, tmp_path: Path) -> None:
        """When same name exists at multiple depths, shallowest wins (BFS)."""
        (tmp_path / "50_work" / "agents").mkdir(parents=True)
        (tmp_path / "50_work" / "30-clients" / "acme" / "agents").mkdir(parents=True)
        scopes = {"work": "50_work"}
        result = _resolve_project_dir(tmp_path, "work:agents", scopes)
        assert result is not None
        assert result[0] == tmp_path / "50_work" / "agents"

    def test_hierarchical_category_itself_resolves(self, tmp_path: Path) -> None:
        """Category directories (e.g. 12-tickets) are valid targets."""
        (tmp_path / "50_work" / "12-tickets" / "active").mkdir(parents=True)
        scopes = {"work": "50_work"}
        result = _resolve_project_dir(tmp_path, "work:12-tickets", scopes)
        assert result is not None
        assert result[0] == tmp_path / "50_work" / "12-tickets"

    def test_hierarchical_explicit_path_with_slash(self, tmp_path: Path) -> None:
        """Explicit category/entity path resolves directly."""
        (tmp_path / "50_work" / "20-products" / "hydra3d-plus").mkdir(parents=True)
        scopes = {"work": "50_work"}
        result = _resolve_project_dir(tmp_path, "work:20-products/hydra3d-plus", scopes)
        assert result is not None
        assert result[0] == tmp_path / "50_work" / "20-products" / "hydra3d-plus"

    def test_auto_scan_finds_in_hierarchical_scope(self, tmp_path: Path) -> None:
        """Auto-scan (no explicit scope) searches hierarchical scopes too."""
        (tmp_path / "10_projects").mkdir()
        (tmp_path / "50_work" / "20-products" / "hydra3d-plus").mkdir(parents=True)
        scopes = {"projects": "10_projects", "work": "50_work"}
        result = _resolve_project_dir(tmp_path, "hydra3d-plus", scopes)
        assert result is not None
        assert result[0] == tmp_path / "50_work" / "20-products" / "hydra3d-plus"
        assert result[1] == "work"

    def test_not_found_returns_none(self, tmp_path: Path) -> None:
        """Non-existent slug returns None."""
        (tmp_path / "50_work").mkdir()
        scopes = {"work": "50_work"}
        result = _resolve_project_dir(tmp_path, "work:nonexistent", scopes)
        assert result is None

    def test_boundary_escape_blocked(self, tmp_path: Path) -> None:
        """Path traversal in slug is blocked."""
        (tmp_path / "50_work").mkdir()
        scopes = {"work": "50_work"}
        result = _resolve_project_dir(tmp_path, "work:../../etc", scopes)
        assert result is None

    def test_meta_unchanged(self, mock_vault: Path) -> None:
        """_meta still resolves to 00_meta scope root."""
        result = _resolve_project_dir(mock_vault, "_meta")
        assert result is not None
        assert result[0] == mock_vault / "00_meta"
        assert result[1] == "meta"
```

**Step 2: Run tests to verify failure**

Run: `pytest tests/test_helpers.py::TestResolveProjectDir -v`
Expected: `test_hierarchical_scope_resolves_nested`, `test_hierarchical_bfs_shallowest_wins`, `test_auto_scan_finds_in_hierarchical_scope` FAIL (current code only checks one level).

**Step 3: Implement BFS resolution**

Replace the `_resolve_project_dir` function in `src/hive/_helpers.py` (lines 87-134):

```python
def _resolve_project_dir(
    vault: Path, project: str, scopes: dict[str, str] | None = None,
) -> tuple[Path, str] | None:
    """Resolve a project slug to (directory, scope_name).

    - ``_meta`` maps to the meta scope root (backward compat).
    - ``scope:project`` targets a specific scope.
    - Plain ``project`` auto-scans all scopes, first match wins.

    Supports hierarchical scopes: if the slug is not found at the first
    level of a scope directory, a breadth-first search finds it at any
    depth.  Slugs containing ``/`` are resolved as literal relative paths
    within the scope directory (no BFS).

    Returns None if the project is not found or escapes the vault boundary.
    """
    scopes = scopes or _DEFAULT_SCOPES

    # _meta special case → meta scope root
    if project == "_meta":
        meta_dir_name = scopes.get("meta", "00_meta")
        d = vault / meta_dir_name
        if not d.is_dir():
            return None
        if _check_path_boundary(d, vault) is not None:
            return None
        return (d, "meta")

    explicit_scope, slug = _parse_project_ref(project)

    if explicit_scope is not None:
        dir_name = scopes.get(explicit_scope)
        if dir_name is None:
            return None
        scope_dir = vault / dir_name
        return _search_scope(scope_dir, slug, explicit_scope, vault)

    # Auto-scan: iterate scopes, first match wins, skip missing dirs
    for scope_name, dir_name in scopes.items():
        if scope_name == "meta":
            continue  # meta is not a project container
        scope_dir = vault / dir_name
        result = _search_scope(scope_dir, slug, scope_name, vault)
        if result is not None:
            return result

    return None


def _search_scope(
    scope_dir: Path, slug: str, scope_name: str, vault: Path,
) -> tuple[Path, str] | None:
    """Search for a slug within a scope directory.

    If *slug* contains ``/``, treat it as a literal relative path.
    Otherwise, try a direct child first, then breadth-first search.
    """
    if not scope_dir.is_dir():
        return None

    # Literal relative path (e.g. "20-products/hydra3d-plus")
    if "/" in slug:
        d = scope_dir / slug
        if d.is_dir() and _check_path_boundary(d, vault) is None:
            return (d, scope_name)
        return None

    # Fast path: direct child
    d = scope_dir / slug
    if d.is_dir() and _check_path_boundary(d, vault) is None:
        return (d, scope_name)

    # BFS: breadth-first search through subdirectories
    from collections import deque

    queue: deque[Path] = deque()
    try:
        queue.extend(sorted(c for c in scope_dir.iterdir() if c.is_dir()))
    except OSError:
        return None

    while queue:
        candidate = queue.popleft()
        # Check children of this candidate
        try:
            children = sorted(c for c in candidate.iterdir() if c.is_dir())
        except OSError:
            continue
        for child in children:
            if child.name == slug and _check_path_boundary(child, vault) is None:
                return (child, scope_name)
            queue.append(child)

    return None
```

**Step 4: Run tests to verify pass**

Run: `pytest tests/test_helpers.py::TestResolveProjectDir -v`
Expected: All PASS.

**Step 5: Run full test suite**

Run: `make test`
Expected: All tests pass.

**Step 6: Commit**

```bash
git add src/hive/_helpers.py tests/test_helpers.py
git commit -m "feat: recursive BFS resolution for hierarchical vault scopes"
```

---

### Task 3: Guard `vault_write` against ambiguous creation in hierarchical scopes

**Files:**
- Modify: `src/hive/_vault_write.py`
- Test: `tests/test_server.py`

**Step 1: Write failing test**

Add to `tests/test_server.py` in a new class after the multi-scope tests:

```python
class TestHierarchicalScopeWriteGuard:
    """vault_write refuses to create entities at ambiguous locations in hierarchical scopes."""

    async def test_write_to_resolved_entity_works(self, git_multi_scope_vault: Path) -> None:
        """Writing to an existing resolved entity in hierarchical scope works."""
        # Create a nested entity
        products = git_multi_scope_vault / "50_work" / "20-products" / "hydra3d"
        products.mkdir(parents=True)
        (products / "00-context.md").write_text(
            "---\nid: hydra3d\ntype: project\nstatus: active\n---\n\n# Hydra3D\n"
        )
        # Stage and commit new files
        import subprocess
        subprocess.run(["git", "add", "."], cwd=git_multi_scope_vault, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "add hydra3d"], cwd=git_multi_scope_vault, capture_output=True, check=True)

        mcp = create_server(vault_path=git_multi_scope_vault, vault_scopes=MULTI_SCOPES)
        result = _text(await mcp.call_tool("vault_write", {
            "project": "work:hydra3d",
            "path": "notes.md",
            "content": "# Notes\n",
            "doc_type": "note",
            "operation": "create",
        }))
        assert "created" in result.lower()

    async def test_write_to_nonexistent_slug_without_path_fails(self, git_multi_scope_vault: Path) -> None:
        """Creating a file in a non-existent slug without category path returns error."""
        mcp = create_server(vault_path=git_multi_scope_vault, vault_scopes=MULTI_SCOPES)
        result = _text(await mcp.call_tool("vault_write", {
            "project": "work:new-thing",
            "path": "notes.md",
            "content": "# Notes\n",
            "doc_type": "note",
            "operation": "create",
        }))
        assert "not found" in result.lower()

    async def test_write_with_explicit_category_path_works(self, git_multi_scope_vault: Path) -> None:
        """Creating with explicit category/entity path works (vault_write resolves it)."""
        # Ensure category exists
        (git_multi_scope_vault / "50_work" / "20-products").mkdir(parents=True, exist_ok=True)
        import subprocess
        subprocess.run(["git", "add", "."], cwd=git_multi_scope_vault, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "add products dir"], cwd=git_multi_scope_vault, capture_output=True, check=True)

        mcp = create_server(vault_path=git_multi_scope_vault, vault_scopes=MULTI_SCOPES)
        # This resolves because the slug "20-products" is a direct child of 50_work
        result = _text(await mcp.call_tool("vault_write", {
            "project": "work:20-products",
            "path": "new-camera/00-context.md",
            "content": "# New Camera\n",
            "doc_type": "project",
            "operation": "create",
        }))
        assert "created" in result.lower()
```

**Step 2: Run tests to verify behavior**

Run: `pytest tests/test_server.py::TestHierarchicalScopeWriteGuard -v`
Expected: Tests should pass because `_resolve_project_dir` returning None already causes "not found" in vault_write. The existing guard is sufficient — no code change needed in vault_write.

**Step 3: Commit (tests only)**

```bash
git add tests/test_server.py
git commit -m "test: add hierarchical scope write guard tests"
```

---

### Task 4: Add `scope` filter to `vault_search`

**Files:**
- Modify: `src/hive/_vault_read.py`
- Test: `tests/test_server.py`

**Step 1: Write failing tests**

Add to `tests/test_server.py`:

```python
class TestVaultSearchScopeFilter:
    """vault_search scope parameter restricts search to a specific scope."""

    async def test_scope_filter_limits_to_work(self, multi_scope_vault: Path) -> None:
        """Search with scope='work' only finds files in 50_work."""
        mcp = create_server(vault_path=multi_scope_vault, vault_scopes=MULTI_SCOPES)
        result = _text(await mcp.call_tool("vault_search", {
            "query": "Project",
            "scope": "work",
        }))
        # Should find "My Company" (in 50_work) but not "Test Project" (in 10_projects)
        assert "50_work" in result or "my-company" in result
        assert "10_projects" not in result

    async def test_scope_filter_limits_to_projects(self, multi_scope_vault: Path) -> None:
        """Search with scope='projects' only finds files in 10_projects."""
        mcp = create_server(vault_path=multi_scope_vault, vault_scopes=MULTI_SCOPES)
        result = _text(await mcp.call_tool("vault_search", {
            "query": "Project",
            "scope": "projects",
        }))
        assert "10_projects" in result or "testproject" in result
        assert "50_work" not in result

    async def test_scope_filter_invalid_scope(self, multi_scope_vault: Path) -> None:
        """Search with an invalid scope name returns error."""
        mcp = create_server(vault_path=multi_scope_vault, vault_scopes=MULTI_SCOPES)
        result = _text(await mcp.call_tool("vault_search", {
            "query": "anything",
            "scope": "nonexistent",
        }))
        assert "unknown scope" in result.lower()

    async def test_no_scope_searches_everything(self, multi_scope_vault: Path) -> None:
        """Without scope, searches the entire vault (backward compat)."""
        mcp = create_server(vault_path=multi_scope_vault, vault_scopes=MULTI_SCOPES)
        result = _text(await mcp.call_tool("vault_search", {
            "query": "Project",
        }))
        # Should contain results from both scopes
        assert "testproject" in result.lower() or "10_projects" in result
        assert "my-company" in result.lower() or "50_work" in result

    async def test_scope_filter_ranked_mode(self, multi_scope_vault: Path) -> None:
        """Scope filter works in ranked mode too."""
        mcp = create_server(vault_path=multi_scope_vault, vault_scopes=MULTI_SCOPES)
        result = _text(await mcp.call_tool("vault_search", {
            "query": "Project",
            "scope": "work",
            "ranked": True,
        }))
        assert "10_projects" not in result

    async def test_scope_filter_recent_mode(self, git_multi_scope_vault: Path) -> None:
        """Scope filter works in recent mode too."""
        mcp = create_server(vault_path=git_multi_scope_vault, vault_scopes=MULTI_SCOPES)
        result = _text(await mcp.call_tool("vault_search", {
            "scope": "work",
            "since_days": 30,
        }))
        # Should only show 50_work changes
        assert "10_projects" not in result
```

**Step 2: Run tests to verify failure**

Run: `pytest tests/test_server.py::TestVaultSearchScopeFilter -v`
Expected: FAIL — `vault_search` doesn't accept a `scope` parameter yet.

**Step 3: Implement scope filter**

In `src/hive/_vault_read.py`, modify the `vault_search` function:

1. Add `scope` parameter:

```python
def vault_search(
    query: str = "",
    max_lines: int = 500,
    type_filter: str = "",
    status_filter: str = "",
    tag_filter: str = "",
    use_regex: bool = False,
    ranked: bool = False,
    max_results: int = 10,
    since_days: int = 0,
    project: str = "",
    scope: str = "",
) -> str:
```

2. Update docstring to document `scope`:

```python
"""Search the vault: full-text, ranked, or recent changes.

Default mode: flat full-text search across all vault files.
Ranked mode (ranked=True): results scored by relevance.
Recent mode (since_days>0): files changed in the last N days.

Args:
    query: Text to search for (case-insensitive).
    max_lines: Maximum output lines. Default 500.
    type_filter: Only files whose frontmatter type matches.
    status_filter: Only files whose frontmatter status matches.
    tag_filter: Only files that have this frontmatter tag.
    use_regex: Treat query as regex. Default False.
    ranked: Score results by relevance. Default False.
    max_results: Max files when ranked. Default 10.
    since_days: Show recent changes (0 = disabled). Default 0.
    project: Filter to this project (recent mode only).
    scope: Restrict search to a scope (e.g. 'work', 'projects'). Empty = all.
"""
```

3. Add scope validation and search root resolution after the guard check:

```python
# ── Scope filter ──
search_root = ctx.vault
if scope:
    scope_dir_name = ctx.scopes.get(scope)
    if scope_dir_name is None:
        available = ", ".join(sorted(ctx.scopes.keys()))
        return track(
            ctx, "vault_search",
            f"Unknown scope '{scope}'. Available: {available}",
        )
    search_root = ctx.vault / scope_dir_name
    if not search_root.is_dir():
        return track(
            ctx, "vault_search",
            f"Scope directory '{scope_dir_name}' not found in vault.",
        )
```

4. Replace all `ctx.vault.rglob("*.md")` calls in the function with `search_root.rglob("*.md")`. There are 3 occurrences:
   - Line 246 (recent mode): `for md_file in ctx.vault.rglob("*.md"):`
   - Line 316 (ranked mode): `for md_file in sorted(ctx.vault.rglob("*.md")):`
   - Line 383 (standard search): `for md_file in sorted(ctx.vault.rglob("*.md")):`

   Also update `md_file.relative_to(ctx.vault)` calls — these remain unchanged since `search_root` is a subdirectory of `ctx.vault` and `relative_to(ctx.vault)` still works.

5. In recent mode, also filter `git_paths` by scope prefix:

After computing `git_paths` and before the existing `project` filter, add:

```python
if scope and search_root != ctx.vault:
    scope_prefix = search_root.relative_to(ctx.vault).as_posix() + "/"
    git_paths = {p for p in git_paths if p.startswith(scope_prefix)}
```

**Step 4: Run tests to verify pass**

Run: `pytest tests/test_server.py::TestVaultSearchScopeFilter -v`
Expected: All PASS.

**Step 5: Run full suite**

Run: `make test`
Expected: All tests pass.

**Step 6: Commit**

```bash
git add src/hive/_vault_read.py tests/test_server.py
git commit -m "feat: add scope filter to vault_search"
```

---

### Task 5: Duplicate name detection in `vault_health`

**Files:**
- Modify: `src/hive/_vault_health.py`
- Test: `tests/test_server.py`

**Step 1: Write failing test**

Add to `tests/test_server.py`:

```python
class TestDuplicateNameDetection:
    """vault_health warns about duplicate directory names within a scope."""

    async def test_detects_duplicate_names(self, multi_scope_vault: Path) -> None:
        """Health report warns when same name exists at different depths."""
        # Create duplicate: 50_work/agents/ and 50_work/30-clients/acme/agents/
        (multi_scope_vault / "50_work" / "agents").mkdir(parents=True, exist_ok=True)
        (multi_scope_vault / "50_work" / "30-clients" / "acme" / "agents").mkdir(parents=True)
        mcp = create_server(vault_path=multi_scope_vault, vault_scopes=MULTI_SCOPES)
        result = _text(await mcp.call_tool("vault_health", {}))
        assert "duplicate" in result.lower() or "agents" in result

    async def test_no_false_positive_without_duplicates(self, multi_scope_vault: Path) -> None:
        """No duplicate warning when all names are unique."""
        mcp = create_server(vault_path=multi_scope_vault, vault_scopes=MULTI_SCOPES)
        result = _text(await mcp.call_tool("vault_health", {}))
        assert "duplicate" not in result.lower()
```

**Step 2: Run test to verify failure**

Run: `pytest tests/test_server.py::TestDuplicateNameDetection -v`
Expected: `test_detects_duplicate_names` FAILS (no duplicate detection in current code).

**Step 3: Implement duplicate detection**

Add a helper function in `src/hive/_vault_health.py` and call it from `health_report_text`:

Add this function before `health_report_text`:

```python
def _find_duplicate_names(scope_dir: Path) -> list[tuple[str, list[str]]]:
    """Find directory names that appear at multiple depths within a scope.

    Returns a list of (name, [relative_paths]) for duplicated names.
    """
    from collections import defaultdict

    name_paths: dict[str, list[str]] = defaultdict(list)
    try:
        for d in scope_dir.rglob("*"):
            if d.is_dir():
                rel = d.relative_to(scope_dir).as_posix()
                name_paths[d.name].append(rel)
    except OSError:
        return []
    return [
        (name, paths) for name, paths in sorted(name_paths.items())
        if len(paths) > 1
    ]
```

At the end of `health_report_text`, before the `if not found_any` check, add:

```python
    # ── Duplicate name warnings ──
    dup_lines: list[str] = []
    for scope_name, dir_name in ctx.scopes.items():
        if scope_name == "meta":
            continue
        scope_dir = ctx.vault / dir_name
        if not scope_dir.is_dir():
            continue
        duplicates = _find_duplicate_names(scope_dir)
        for name, paths in duplicates:
            dup_lines.append(
                f"- **{scope_name}**: '{name}' exists at: "
                + ", ".join(paths)
                + f" (resolved to: {paths[0]})"
            )
    if dup_lines:
        found_any = True
        lines.append("## Duplicate Names (BFS resolution warning)")
        lines.extend(dup_lines)
        lines.append("")
```

**Step 4: Run tests to verify pass**

Run: `pytest tests/test_server.py::TestDuplicateNameDetection -v`
Expected: All PASS.

**Step 5: Run full suite**

Run: `make test`
Expected: All tests pass.

**Step 6: Commit**

```bash
git add src/hive/_vault_health.py tests/test_server.py
git commit -m "feat: detect duplicate directory names in vault_health"
```

---

### Task 6: Update conftest with hierarchical vault fixture

**Files:**
- Modify: `tests/conftest.py`

**Step 1: Extend `multi_scope_vault` fixture**

Replace the `multi_scope_vault` fixture in `tests/conftest.py` to include hierarchical structure:

```python
@pytest.fixture
def multi_scope_vault(mock_vault: Path) -> Path:
    """Extend mock_vault with a 50_work scope containing hierarchical structure."""
    # Direct child (flat entity, like 10_projects)
    company = mock_vault / "50_work" / "my-company"
    company.mkdir(parents=True)
    (company / "00-context.md").write_text(
        "---\nid: my-company\ntype: project\nstatus: active\n---\n\n"
        "# My Company\n\nProfessional project.\n"
    )
    (company / "11-tasks.md").write_text(
        "---\nid: my-company-tasks\ntype: project-tasks\nstatus: active\n---\n\n"
        "# My Company: Tasks\n\n- [ ] Ship feature\n"
    )
    (company / "90-lessons.md").write_text(
        "---\nid: my-company-lessons\ntype: lesson\nstatus: active\n---\n\n"
        "# My Company: Lessons\n\n## Entry 1\nDeploy on Fridays.\n"
    )

    # Nested entity under a category (hierarchical)
    hydra = mock_vault / "50_work" / "20-products" / "hydra3d-plus"
    hydra.mkdir(parents=True)
    (hydra / "00-context.md").write_text(
        "---\nid: hydra3d-plus\ntype: product\nstatus: active\n---\n\n"
        "# Hydra3D Plus\n\n3D camera product.\n"
    )

    # Another nested entity (different category)
    client = mock_vault / "50_work" / "30-clients" / "appliedmaterials"
    client.mkdir(parents=True)
    (client / "00-context.md").write_text(
        "---\nid: appliedmaterials\ntype: client\nstatus: active\n"
        "tags: [hydra3d-plus]\n---\n\n"
        "# Applied Materials\n\nKey client using Hydra3D Plus.\n"
    )

    return mock_vault
```

**Step 2: Run tests**

Run: `make test`
Expected: All existing multi-scope tests still pass with the extended fixture.

**Step 3: Commit**

```bash
git add tests/conftest.py
git commit -m "test: extend multi_scope_vault fixture with hierarchical structure"
```

---

### Task 7: Integration tests for hierarchical scope workflows

**Files:**
- Modify: `tests/test_integration.py`

**Step 1: Write integration tests**

Add to `tests/test_integration.py`:

```python
class TestHierarchicalScopeDiscovery:
    """Navigate hierarchical 50_work scope: list → drill → query."""

    async def test_list_then_drill_into_category(self, multi_scope_vault: Path) -> None:
        mcp = create_server(
            vault_path=multi_scope_vault,
            vault_scopes={"projects": "10_projects", "meta": "00_meta", "work": "50_work"},
        )

        # Step 1: list all projects — should show work scope entries
        projects = _text(await mcp.call_tool("vault_list", {}))
        assert "work/" in projects

        # Step 2: drill into a category
        products = _text(await mcp.call_tool("vault_list", {"project": "work:20-products"}))
        assert "hydra3d-plus" in products

        # Step 3: query the nested entity directly
        ctx = _text(await mcp.call_tool(
            "vault_query", {"project": "work:hydra3d-plus", "section": "context"},
        ))
        assert "Hydra3D Plus" in ctx

    async def test_auto_scan_resolves_nested_entity(self, multi_scope_vault: Path) -> None:
        mcp = create_server(
            vault_path=multi_scope_vault,
            vault_scopes={"projects": "10_projects", "meta": "00_meta", "work": "50_work"},
        )

        # Auto-scan should find hydra3d-plus without explicit scope
        ctx = _text(await mcp.call_tool(
            "vault_query", {"project": "hydra3d-plus", "section": "context"},
        ))
        assert "Hydra3D Plus" in ctx

    async def test_search_with_scope_filter(self, multi_scope_vault: Path) -> None:
        mcp = create_server(
            vault_path=multi_scope_vault,
            vault_scopes={"projects": "10_projects", "meta": "00_meta", "work": "50_work"},
        )

        # Search only within work scope
        result = _text(await mcp.call_tool("vault_search", {
            "query": "Hydra3D",
            "scope": "work",
        }))
        assert "hydra3d" in result.lower()

        # Same search in projects scope should find nothing
        result = _text(await mcp.call_tool("vault_search", {
            "query": "Hydra3D",
            "scope": "projects",
        }))
        assert "no matches" in result.lower()
```

**Step 2: Run integration tests**

Run: `pytest tests/test_integration.py -v`
Expected: All PASS.

**Step 3: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: integration tests for hierarchical scope workflows"
```

---

### Task 8: Update site docs (EN + ES)

**Files:**
- Modify: `site/src/content/docs/tools/vault-tools.mdx` (or equivalent)
- Modify: `site/src/content/docs/es/tools/vault-tools.mdx` (or equivalent)

> Note: Exact file paths TBD — check `site/src/content/docs/` for the vault tools documentation page.

Document:
1. The new `work` scope in the default configuration
2. How hierarchical scopes work (BFS resolution)
3. The `scope:category/entity` syntax for explicit paths
4. The new `scope` parameter in `vault_search`
5. Duplicate name detection in `vault_health`

**Step 1: Update English docs**

Add a "Hierarchical Scopes" section explaining the behavior.

**Step 2: Update Spanish docs**

Mirror the English changes in the `es/` version.

**Step 3: Build site**

Run: `cd site && npm run build`
Expected: Build succeeds.

**Step 4: Commit**

```bash
git add site/
git commit -m "docs: document hierarchical scope support (EN + ES)"
```

---

### Task 9: Lint, typecheck, full verification

**Step 1: Full check**

Run: `make check`
Expected: lint + typecheck + test all pass.

**Step 2: Final commit (if any fixups needed)**

```bash
git add -u
git commit -m "fix: address lint/type issues from hierarchical scope changes"
```
