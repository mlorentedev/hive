"""The docs site must describe the tool that ships (#498).

The provider-neutrality guard scans source modules, not ``site/``. So after
4.0.0 retired the per-provider worker settings, the site went on documenting
them in both languages for every release that followed, and it never gained a
page entry for ``vault_ask``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from hive.server import create_server

_DOCS = Path(__file__).resolve().parents[1] / "site" / "src" / "content" / "docs"
# The settings 4.0.0 retired (#391): HIVE_OLLAMA_*, HIVE_OPENROUTER_*, and the
# bare OPENROUTER_API_KEY alias.
_RETIRED_SETTING = re.compile(r"\b(?:HIVE_)?(?:OLLAMA|OPENROUTER)_[A-Z_]+\b")
_LOCALES = ("", "es")


def _pages() -> list[Path]:
    return sorted(p for p in _DOCS.rglob("*") if p.suffix in (".md", ".mdx"))


def test_no_page_documents_a_retired_setting() -> None:
    pages = _pages()
    assert pages, f"no docs pages under {_DOCS}"
    offenders = [
        f"{page.relative_to(_DOCS)}:{n}: {match.group(0)}"
        for page in pages
        for n, line in enumerate(page.read_text(encoding="utf-8").splitlines(), 1)
        if (match := _RETIRED_SETTING.search(line))
    ]
    assert not offenders, "settings hive no longer reads; use HIVE_WORKER_*:\n" + "\n".join(
        offenders
    )


@pytest.mark.parametrize("locale", _LOCALES, ids=["en", "es"])
async def test_every_registered_tool_has_a_tools_page_entry(tmp_path: Path, locale: str) -> None:
    mcp = create_server(vault_path=tmp_path)
    try:
        registered = {tool.name for tool in await mcp.list_tools()}
    finally:
        ctx = getattr(mcp, "_hive_ctx", None)
        assert ctx is not None, "create_server no longer attaches _hive_ctx: close it another way"
        ctx.close()

    tools_dir = _DOCS / locale / "tools"
    headings = {
        m.group(1)
        for page in tools_dir.glob("*.md")
        for m in re.finditer(r"^##+ `?([a-z_]+)`?\s*$", page.read_text(encoding="utf-8"), re.M)
    }
    assert registered - headings == set(), f"registered tools with no heading under {tools_dir}"
