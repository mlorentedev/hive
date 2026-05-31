"""Guard: documented scope defaults must match ``HiveSettings().vault_scopes``.

Issue #158 — the default scope set (``projects`` / ``meta`` / ``work`` /
``agents`` → directory names) is hand-mirrored across many documentation
surfaces. Adding the ``agents`` scope in #154 required editing seven files, and
it is easy to miss one (the ``tools/vault.md`` table still said "three scopes"
until caught). This test turns that drift into a CI failure instead of a
review-by-eye catch.

SSOT is ``HiveSettings().vault_scopes`` — the exact literal the server ships.
We never re-declare the expected scopes here; we read them from config so the
guard itself cannot drift.

Scope of the guard (and its limit): we assert every *current* default scope is
documented on every surface. We deliberately do not try to detect a *stale*
scope left behind after a removal — doc prose mentions many directory-like
tokens (``20-products``, ``30-architecture``) as examples, so reverse-matching
is brittle. The common failure mode (#154) is "added a scope, forgot a
surface", which this forward check covers.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hive.config import HiveSettings

REPO_ROOT = Path(__file__).resolve().parents[1]

# Every doc surface that hand-lists the default scope set. A new surface that
# documents scopes should be added here so the guard covers it too.
SCOPE_DOC_SURFACES = [
    "README.md",
    "site/src/content/docs/configuration.mdx",
    "site/src/content/docs/es/configuration.mdx",
    "site/src/content/docs/guides/vault-structure.md",
    "site/src/content/docs/es/guides/vault-structure.md",
    "site/src/content/docs/tools/vault.md",
    "site/src/content/docs/es/tools/vault.md",
]

DEFAULT_SCOPES = HiveSettings().vault_scopes


def _surface_documents_scope(text: str, scope_key: str, dir_name: str) -> bool:
    """Return True if *text* documents the ``scope_key → dir_name`` mapping.

    Matching policy (#158): anchor on the *directory name*. The numeric-prefixed
    dir names (``10_projects``, ``00_meta``, ``80_agents``) are distinctive — a
    surface contains one only when it documents that scope — so a substring
    check has near-zero false positives and reliably catches the #154 failure
    mode (added a scope, forgot a surface). The scope *key* is a plain word
    (``work``, ``meta``) that appears in unrelated prose, so it is not used as
    the anchor; reverse-matching for stale scopes is out of scope (see module
    docstring).
    """
    return dir_name in text


@pytest.mark.parametrize("surface", SCOPE_DOC_SURFACES)
def test_doc_surface_lists_all_default_scopes(surface: str) -> None:
    """Each documentation surface must list every default scope mapping."""
    path = REPO_ROOT / surface
    assert path.is_file(), f"documented scope surface is missing: {surface}"
    text = path.read_text(encoding="utf-8")
    missing = [
        f"{key} -> {dir_name}"
        for key, dir_name in DEFAULT_SCOPES.items()
        if not _surface_documents_scope(text, key, dir_name)
    ]
    assert not missing, (
        f"{surface} does not document scope(s): {missing}. "
        "Update it to match HiveSettings().vault_scopes."
    )
