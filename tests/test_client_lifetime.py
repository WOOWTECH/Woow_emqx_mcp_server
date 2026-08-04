"""The shared EMQX client must survive more than one tool call.

Seam under test: the MCP tool surface across a single session.

Found in production: the first tool in a session worked and the second failed
with "Cannot reopen a client instance, once it has been closed". A `Depends`
provider that returns the client hands FastMCP an object that is itself an
async context manager, so the DI layer entered and exited it per call —
closing the pooled client after the first use.
"""

from __future__ import annotations

import httpx
import pytest
from fastmcp import Client

import emqx_mcp_server.lifespan as lifespan_module
from emqx_mcp_server.gating import ToolGate
from emqx_mcp_server.server import build_server

NODES = [{"node": "emqx@test", "version": "5.8.8", "role": "core",
          "node_status": "running", "connections": 1}]
STATS = [{"connections.count": 1, "sessions.count": 1,
          "subscriptions.count": 69, "topics.count": 69, "retained.count": 5}]


def _double() -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/nodes"):
            return httpx.Response(200, json=NODES)
        if request.url.path.endswith("/stats"):
            return httpx.Response(200, json=STATS)
        if request.url.path.endswith("/topics"):
            return httpx.Response(200, json={"data": [], "meta": {"count": 0}})
        return httpx.Response(404, json={"code": "NOT_FOUND"})

    return httpx.AsyncClient(
        base_url="http://emqx.test/api/v5", transport=httpx.MockTransport(handler)
    )


@pytest.fixture(autouse=True)
def _use_double(monkeypatch):
    monkeypatch.setattr(lifespan_module, "make_client", _double)


async def test_several_tools_in_one_session_all_succeed():
    """One session, three calls — the client must not be closed after the first."""
    async with Client(build_server(gate=ToolGate())) as client:
        first = await client.call_tool("emqx_broker_stats", {})
        second = await client.call_tool("emqx_cluster_status", {})
        third = await client.call_tool("emqx_list_topics", {})

    assert not first.is_error, first.content
    assert not second.is_error, f"second call in the session failed: {second.content}"
    assert not third.is_error, third.content
    assert second.structured_content["nodes"][0]["version"] == "5.8.8"


async def test_the_same_tool_can_be_called_twice():
    async with Client(build_server(gate=ToolGate())) as client:
        one = await client.call_tool("emqx_broker_stats", {})
        two = await client.call_tool("emqx_broker_stats", {})

    assert not one.is_error, one.content
    assert not two.is_error, f"repeat call failed: {two.content}"
    assert two.structured_content["subscriptions"] == 69
