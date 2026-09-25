"""Tool annotations reach clients as declared (#434).

mcp 2.x types ``ToolAnnotations`` with snake_case fields and keeps the
camelCase names as wire aliases. Clients decide on these hints whether a call
needs confirmation, so a read tool must arrive read-only and a write tool must
not arrive destructive.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastmcp import Client

from hive.server import create_server

if TYPE_CHECKING:
    from pathlib import Path


async def test_read_and_write_tools_carry_their_hints(mock_vault: Path) -> None:
    async with Client(create_server(vault_path=mock_vault)) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}

    read = tools["vault_query"].model_dump(by_alias=True)["annotations"]
    write = tools["vault_write"].model_dump(by_alias=True)["annotations"]
    assert read["readOnlyHint"] is True
    assert read["idempotentHint"] is True
    assert write["readOnlyHint"] is False
    assert write["destructiveHint"] is False
