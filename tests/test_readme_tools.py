"""The README's tool table must match the tools the server registers (#439)."""

from __future__ import annotations

import re
from pathlib import Path

from hive.server import create_server

_README = Path(__file__).resolve().parents[1] / "README.md"
_ROW = re.compile(r"^\| `(?P<name>[a-z_]+)` \|", re.MULTILINE)


def _readme_tools() -> set[str]:
    text = _README.read_text(encoding="utf-8")
    section = text.split("\n## Tools\n", 1)[1].split("\n## ", 1)[0]
    return {m["name"] for m in _ROW.finditer(section)}


async def test_readme_lists_exactly_the_registered_tools(tmp_path: Path) -> None:
    mcp = create_server(vault_path=tmp_path)
    try:
        registered = {tool.name for tool in await mcp.list_tools()}
    finally:
        ctx = getattr(mcp, "_hive_ctx", None)
        if ctx is not None:
            ctx.close()

    documented = _readme_tools()
    assert registered - documented == set(), "registered but missing from the README tool table"
    assert documented - registered == set(), "in the README tool table but not registered"
