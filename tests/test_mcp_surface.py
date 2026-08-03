"""What an AI client actually sees when it connects.

Seam under test: the MCP protocol surface, exercised through FastMCP's
in-memory `Client` — the same boundary Claude or Cursor talks to.
"""

from fastmcp import Client

from emqx_mcp_server.gating import ToolGate
from emqx_mcp_server.registry import TOOL_REGISTRY
from emqx_mcp_server.server import build_server


async def test_registry_and_server_advertise_the_same_tools():
    """The GUI switches are driven by the registry, so it must not drift.

    A registry entry with no implementation renders a dead switch; an
    implemented tool missing from the registry can never be switched off.
    """
    mcp = build_server(gate=ToolGate())

    async with Client(mcp) as client:
        advertised = {tool.name for tool in await client.list_tools()}

    declared = {spec.name for spec in TOOL_REGISTRY}

    assert declared - advertised == set(), "declared but not implemented"
    assert advertised - declared == set(), "implemented but not declared"


async def test_disabled_tool_is_absent_from_the_advertised_tool_list():
    """Switching a tool off in the GUI must hide it from the model."""
    mcp = build_server(gate=ToolGate(disabled_tools=["emqx_kick_client"]))

    async with Client(mcp) as client:
        names = {tool.name for tool in await client.list_tools()}

    assert "emqx_kick_client" not in names
    assert "emqx_list_clients" in names


async def test_parameters_are_described_not_an_opaque_object():
    """The model must see real fields, not a bare `dict` it has to guess at."""
    mcp = build_server(gate=ToolGate())

    async with Client(mcp) as client:
        tool = next(
            t for t in await client.list_tools() if t.name == "emqx_list_clients"
        )

    properties = tool.inputSchema["properties"]
    assert {"username", "clientid", "page", "limit"} <= set(properties)
    assert properties["username"]["description"]
    # The injected HTTP client must never leak into the model-facing schema.
    assert "emqx" not in properties
